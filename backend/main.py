"""
main.py — FastAPI сервер (REST API).

Запуск:  python -m uvicorn main:app --reload --port 8000

Ендпоінти:
  GET    /api/catalog                              — Список приладів з довідника
  POST   /api/simulate                             — Запуск симуляції
  GET    /api/houses                               — Список будинків з їхніми приладами
  POST   /api/houses                               — Створити будинок
  PUT    /api/houses/{house_id}                    — Оновити будинок (назва, тарифи, ліміт)
  DELETE /api/houses/{house_id}                    — Видалити будинок
  POST   /api/houses/{house_id}/appliances         — Додати прилад до будинку
  PUT    /api/houses/{house_id}/appliances/{id}    — Оновити прилад будинку
  DELETE /api/houses/{house_id}/appliances/{id}    — Видалити прилад будинку
  GET    /api/houses/{house_id}/simulate           — Симуляція будинку напряму з БД
  POST   /api/reset                                — Скинути будинки до початкових даних
"""

import base64
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from database import (
    SessionLocal, init_db, seed_catalog, seed_default_house, cleanup_expired_guests,
    ApplianceCatalog, House, HouseAppliance, User, AddressQueueMemory
)
from gpv_service import (
    load_katottg, list_oblasts, list_raions, list_settlements,
    search_streets_for_settlement, normalize_location_keys,
    get_live_region_schedule, compute_queue_live_status,
    match_queues_for_location, OBLAST_TO_GPV_REGION
)
from simulation import simulate, optimize_schedule, ApplianceInput


_LAST_GUEST_CLEANUP_TS = 0


# ---------- Lifespan (замість застарілого @app.on_event("startup")) ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _LAST_GUEST_CLEANUP_TS
    init_db()
    load_katottg()
    db = SessionLocal()
    try:
        seed_catalog(db)
        cleanup_expired_guests(db)
        _LAST_GUEST_CLEANUP_TS = int(time.time())
    finally:
        db.close()
    yield


# ---------- Ініціалізація ----------
app = FastAPI(
    title="⚡ Energy Simulator API",
    description="Симулятор споживання електроенергії побутовими приладами",
    version="1.1.0",
    lifespan=lifespan,
)

# ---------- Безпечний CORS (конкретні дозволені домени) ----------
DEFAULT_ORIGINS = (
    "http://localhost:8080,"
    "http://127.0.0.1:8080,"
    "http://localhost:8000,"
    "http://127.0.0.1:8000"
)
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",")
    if origin.strip()
]
if os.getenv("RENDER_EXTERNAL_URL"):
    ALLOWED_ORIGINS.append(os.getenv("RENDER_EXTERNAL_URL").rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def security_and_nocache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    path = request.url.path
    if path == "/" or path.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


# ---------- Залежність: отримання сесії БД ----------
def get_db():
    """Створює нову сесію БД для кожного запиту."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- Авторизація та ізоляція користувачів ----------
AUTH_SECRET = os.getenv("AUTH_SECRET", "energydim-dev-secret-change-in-prod-2026").encode("utf-8")
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 днів


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt_val = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_val.encode("utf-8"), 100_000)
    return dk.hex(), salt_val


def verify_password(password: str, expected_hash: str, salt: str) -> bool:
    actual_hash, _ = hash_password(password, salt)
    return hmac.compare_digest(actual_hash, expected_hash)


def create_token(user_id: int) -> str:
    exp = int(time.time()) + TOKEN_TTL_SECONDS
    payload = f"{user_id}:{exp}".encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    sig = hmac.new(AUTH_SECRET, b64_payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{b64_payload}.{sig}"


def verify_token(token: str) -> int | None:
    try:
        b64_payload, sig = token.split(".", 1)
        expected_sig = hmac.new(AUTH_SECRET, b64_payload.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        padded = b64_payload + "=" * (-len(b64_payload) % 4)
        uid_str, exp_str = base64.urlsafe_b64decode(padded).decode("utf-8").split(":", 1)
        if int(exp_str) < int(time.time()):
            return None
        return int(uid_str)
    except Exception:
        return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """
    Повертає авторизованого User, якщо передано валідний заголовок Authorization: Bearer <token>.
    Якщо токен не передано — повертає None (анонімний контекст House.user_id IS NULL).
    Якщо токен передано, але він недійсний — кидає 401.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Некоректний формат токена")
    token = auth_header[7:].strip()
    user_id = verify_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Сесія застаріла або недійсна. Увійдіть знову.")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Користувача не знайдено або гостьову сесію завершено")
    if user.is_guest:
        now = int(time.time())
        if now - (user.last_seen_at or 0) >= 60:
            user.last_seen_at = now
            db.commit()
    return user


def get_user_house(db: Session, house_id: int, user: User | None) -> House:
    """Шукає будинок виключно серед будинків поточного користувача."""
    cond = House.user_id == user.id if user is not None else House.user_id.is_(None)
    house = db.query(House).filter(House.id == house_id, cond).first()
    if not house:
        raise HTTPException(status_code=404, detail="Будинок не знайдено")
    return house


# ---------- Спільний валідатор розкладу годин (0..23) ----------
def validate_hours_list(schedule: list[int]) -> list[int]:
    if len(schedule) > 24:
        raise ValueError("Розклад не може містити більше 24 елементів")
    cleaned: set[int] = set()
    for h in schedule:
        if isinstance(h, bool) or not isinstance(h, int) or not (0 <= h <= 23):
            raise ValueError(f"Година {h!r} некоректна: дозволені цілі числа від 0 до 23")
        cleaned.add(h)
    return sorted(cleaned)


# ============================================================
# PYDANTIC МОДЕЛІ (валідація вхідних/вихідних даних)
# ============================================================

class ApplianceSimInput(BaseModel):
    """Один прилад у запиті на симуляцію."""
    id: int | None = None
    name: str = Field(..., min_length=1, max_length=100, examples=["Бойлер 80л"])
    power_watt: float = Field(..., gt=0, le=100000, examples=[2000])
    schedule: list[int] = Field(..., max_length=24, examples=[[6, 7, 8, 18, 19, 20]])
    quantity: int = Field(1, ge=1, le=500, examples=[1])
    is_cyclic: bool = Field(False)
    duty_cycle: float = Field(1.0, ge=0.0, le=1.0)

    @field_validator("schedule")
    @classmethod
    def check_schedule(cls, v: list[int]) -> list[int]:
        return validate_hours_list(v)


class SimulationRequest(BaseModel):
    """Тіло POST-запиту на /api/simulate."""
    appliances: list[ApplianceSimInput] = Field(..., max_length=100)
    max_power_watt: float = Field(5000.0, gt=0, le=1000000, examples=[5000])
    day_rate: float = Field(4.32, ge=0, le=1000)
    night_rate: float = Field(2.16, ge=0, le=1000)
    outage_hours: list[int] = Field(default_factory=list, max_length=24)

    @field_validator("outage_hours")
    @classmethod
    def check_outage_hours(cls, v: list[int]) -> list[int]:
        return validate_hours_list(v)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "appliances": [
                    {
                        "name": "Бойлер 80л",
                        "power_watt": 2000,
                        "schedule": [6, 7, 8, 18, 19, 20],
                        "quantity": 1,
                        "is_cyclic": False,
                        "duty_cycle": 1.0,
                    },
                    {
                        "name": "Холодильник",
                        "power_watt": 150,
                        "schedule": list(range(24)),
                        "quantity": 1,
                        "is_cyclic": True,
                        "duty_cycle": 0.35,
                    },
                ],
                "max_power_watt": 3680,
                "day_rate": 4.32,
                "night_rate": 2.16,
                "outage_hours": [],
            }
        }
    )


class HouseCreate(BaseModel):
    """Запит на створення будинку."""
    name: str = Field(..., min_length=1, max_length=100, examples=["Квартира Петренка"])
    address: str = Field("", max_length=200)
    voltage: float = Field(230.0, gt=0, le=1000)
    amps: float = Field(16.0, gt=0, le=1000)
    max_power_watt: float | None = Field(None, gt=0, le=1000000)
    day_rate: float = Field(4.32, ge=0, le=1000)
    night_rate: float = Field(2.16, ge=0, le=1000)
    outage_hours: list[int] | None = Field(None, max_length=24)

    @field_validator("outage_hours")
    @classmethod
    def check_outage_hours(cls, v: list[int] | None) -> list[int] | None:
        return validate_hours_list(v) if v is not None else None


class HouseUpdate(BaseModel):
    """Запит на оновлення будинку."""
    name: str | None = Field(None, min_length=1, max_length=100)
    address: str | None = Field(None, max_length=200)
    voltage: float | None = Field(None, gt=0, le=1000)
    amps: float | None = Field(None, gt=0, le=1000)
    max_power_watt: float | None = Field(None, gt=0, le=1000000)
    day_rate: float | None = Field(None, ge=0, le=1000)
    night_rate: float | None = Field(None, ge=0, le=1000)
    outage_hours: list[int] | None = Field(None, max_length=24)
    gpv_auto_sync: bool | None = None

    @field_validator("outage_hours")
    @classmethod
    def check_outage_hours(cls, v: list[int] | None) -> list[int] | None:
        return validate_hours_list(v) if v is not None else None


class GpvLookupRequest(BaseModel):
    """Пошук та розумна фільтрація черги ГПВ за областю, районом, селом, вулицею та наявністю світла."""
    oblast: str = Field("Вінницька", max_length=80)
    raion: str = Field("", max_length=100)
    settlement: str = Field("", max_length=120)
    street: str = Field("", max_length=150)
    has_power_now: bool | None = None


class HouseGpvBindRequest(BaseModel):
    """Прив'язка адреси (Область -> Район -> Село/Місто -> Вулиця) та живої черги ГПВ до будинку."""
    oblast: str = Field("Вінницька", min_length=1, max_length=80)
    raion: str = Field("", max_length=100)
    settlement: str = Field("", max_length=120)
    street: str = Field("", max_length=150)
    house_num: str = Field("", max_length=30)
    gpv_queue: str = Field(..., min_length=1, max_length=16)
    gpv_auto_sync: bool = Field(True)
    remember_for_location: bool = Field(True)


class PresetRequest(BaseModel):
    """Запит на застосування швидкого сценарію для будинку."""
    preset: str = Field(..., pattern="^(workday|weekend|winter|summer|eco)$")


class RegisterRequest(BaseModel):
    """Реєстрація нового користувача."""
    name: str = Field(..., min_length=1, max_length=100)
    username: str = Field(..., min_length=2, max_length=60)
    password: str = Field(..., min_length=4, max_length=100)


class LoginRequest(BaseModel):
    """Вхід у власний акаунт."""
    username: str = Field(..., min_length=2, max_length=60)
    password: str = Field(..., min_length=1, max_length=100)


class ProfileUpdateRequest(BaseModel):
    """Оновлення імені користувача."""
    name: str = Field(..., min_length=1, max_length=100)


class AddApplianceRequest(BaseModel):
    """Запит на додавання приладу до будинку."""
    catalog_id: int = Field(..., ge=1)
    custom_name: str | None = Field(None, max_length=100)
    custom_power: float | None = Field(None, gt=0, le=100000)
    quantity: int = Field(1, ge=1, le=500)
    schedule: list[int] = Field(..., min_length=1, max_length=24)
    enabled: bool = Field(True)

    @field_validator("schedule")
    @classmethod
    def check_schedule(cls, v: list[int]) -> list[int]:
        res = validate_hours_list(v)
        if not res:
            raise ValueError("Оберіть хоча б одну годину роботи")
        return res


class UpdateApplianceRequest(BaseModel):
    """Запит на оновлення приладу будинку."""
    catalog_id: int | None = Field(None, ge=1)
    custom_name: str | None = Field(None, max_length=100)
    custom_power: float | None = Field(None, gt=0, le=100000)
    clear_custom_name: bool = Field(False)
    clear_custom_power: bool = Field(False)
    quantity: int | None = Field(None, ge=1, le=500)
    schedule: list[int] | None = Field(None, min_length=1, max_length=24)
    enabled: bool | None = None

    @field_validator("schedule")
    @classmethod
    def check_schedule(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return None
        res = validate_hours_list(v)
        if not res:
            raise ValueError("Оберіть хоча б одну годину роботи")
        return res


# ---------- Серіалізація моделей БД ----------
def serialize_house_appliance(ha: HouseAppliance) -> dict:
    cat = ha.catalog
    schedule = json.loads(ha.schedule) if isinstance(ha.schedule, str) else ha.schedule
    effective_power = ha.custom_power if (ha.custom_power and ha.custom_power > 0) else cat.power_watt
    return {
        "id": ha.id,
        "house_id": ha.house_id,
        "catalog_id": ha.catalog_id,
        "catalogId": ha.catalog_id,
        "name": ha.custom_name if ha.custom_name else cat.name,
        "custom": bool(ha.custom_name),
        "power": effective_power,
        "power_watt": effective_power,
        "customPower": bool(ha.custom_power and ha.custom_power > 0),
        "qty": ha.quantity,
        "quantity": ha.quantity,
        "schedule": schedule,
        "cyclic": bool(cat.is_cyclic),
        "is_cyclic": bool(cat.is_cyclic),
        "duty": cat.duty_cycle,
        "duty_cycle": cat.duty_cycle,
        "category": cat.category,
        "enabled": bool(ha.enabled if ha.enabled is not None else True),
    }


def sync_house_gpv_if_enabled(db: Session, h: House, force_refresh: bool = False) -> dict | None:
    """
    Якщо у будинку ввімкнено автосинхронізацію з обленерго (`gpv_auto_sync`),
    автоматично звіряє години відключень із фактичним оперативним графіком (`fact`).
    Якщо обленерго скасувало або змінило відключення (наприклад, о 14:01 дали світло),
    автоматично оновлює `outage_hours` у БД.
    """
    if not h.gpv_auto_sync or not h.gpv_queue:
        return None

    reg = h.gpv_region or "vinnytsia"
    sched = get_live_region_schedule(reg, force_refresh=force_refresh)
    live_st = compute_queue_live_status(reg, h.gpv_queue, sched_data=sched)

    old_outage = json.loads(h.outage_hours) if isinstance(h.outage_hours, str) and h.outage_hours else []
    new_outage = live_st["outage_hours"]
    changed = sorted(old_outage) != sorted(new_outage)
    if changed:
        h.outage_hours = json.dumps(new_outage)
        db.commit()
        db.refresh(h)
    live_st["schedule_changed"] = changed
    return live_st


def serialize_house(h: House, live_gpv: dict | None = None) -> dict:
    voltage = h.voltage if h.voltage is not None else 230.0
    amps = h.amps if h.amps is not None else 16.0
    max_power = h.max_power_watt if h.max_power_watt else (voltage * amps)
    outage = json.loads(h.outage_hours) if isinstance(h.outage_hours, str) and h.outage_hours else []
    return {
        "id": h.id,
        "name": h.name,
        "address": h.address or "",
        "oblast": h.oblast or "",
        "raion": h.raion or "",
        "settlement": h.settlement or "",
        "street": h.street or "",
        "house_num": h.house_num or "",
        "gpv_region": h.gpv_region or "vinnytsia",
        "gpv_queue": h.gpv_queue or "",
        "gpv_auto_sync": bool(h.gpv_auto_sync),
        "live_gpv": live_gpv,
        "voltage": voltage,
        "amps": amps,
        "max_power_watt": max_power,
        "dayRate": h.day_rate if h.day_rate is not None else 4.32,
        "nightRate": h.night_rate if h.night_rate is not None else 2.16,
        "day_rate": h.day_rate if h.day_rate is not None else 4.32,
        "night_rate": h.night_rate if h.night_rate is not None else 2.16,
        "outage_hours": outage,
        "outageHours": outage,
        "appliances": [serialize_house_appliance(a) for a in h.appliances],
    }


# ============================================================
# ЕНДПОІНТИ
# ============================================================

# --- 1. Каталог приладів ---
@app.get("/api/catalog")
def get_catalog(db: Session = Depends(get_db)):
    """Повертає список усіх приладів з довідника."""
    items = db.query(ApplianceCatalog).all()
    return [
        {
            "id": item.id,
            "name": item.name,
            "name_en": item.name_en,
            "category": item.category,
            "power_watt": item.power_watt,
            "is_cyclic": item.is_cyclic,
            "duty_cycle": item.duty_cycle,
            "description": item.description,
        }
        for item in items
    ]


# --- 2. СИМУЛЯЦІЯ (головний ендпоінт) ---
@app.post("/api/simulate")
def run_simulation(request: SimulationRequest):
    """Приймає список приладів і параметри → повертає результат симуляції."""
    appliance_inputs = [
        ApplianceInput(
            name=a.name,
            power_watt=a.power_watt,
            schedule=a.schedule,
            quantity=a.quantity,
            is_cyclic=a.is_cyclic,
            duty_cycle=a.duty_cycle,
        )
        for a in request.appliances
    ]

    result = simulate(
        appliance_inputs,
        max_power_watt=request.max_power_watt,
        day_rate=request.day_rate,
        night_rate=request.night_rate,
        outage_hours=request.outage_hours,
    )

    return {
        "hourly_watt": result.hourly_watt,
        "hourly_kwh": result.hourly_kwh,
        "hourly_cost": result.hourly_cost,
        "total_kwh": result.total_kwh,
        "total_cost": result.total_cost,
        "peak_watt": result.peak_watt,
        "peak_hour": result.peak_hour,
        "overload_hours": result.overload_hours,
        "per_appliance": result.per_appliance,
        "max_power_watt": request.max_power_watt,
        "outage_hours": result.outage_hours,
        "outage_backup": result.outage_backup,
        "tariff_comparison": result.tariff_comparison,
    }


# --- 2b. РОЗУМНИЙ ОПТИМІЗАТОР РОЗКЛАДУ ---
@app.post("/api/optimize")
def run_optimization(request: SimulationRequest):
    """
    Автоматично переносить гнучкі потужні прилади (бойлер, пральна машина,
    посудомийка, зарядка авто тощо) на нічну зону (23:00–07:00) та розводить піки,
    щоб уникнути перевантажень автомата і мінімізувати чек.
    """
    appliances_dicts = [
        {
            "id": a.id,
            "name": a.name,
            "power_watt": a.power_watt,
            "schedule": a.schedule,
            "quantity": a.quantity,
            "is_cyclic": a.is_cyclic,
            "duty_cycle": a.duty_cycle,
        }
        for a in request.appliances
    ]
    return optimize_schedule(
        appliances_dicts,
        max_power_watt=request.max_power_watt,
        day_rate=request.day_rate,
        night_rate=request.night_rate,
        outage_hours=request.outage_hours,
    )


@app.post("/api/houses/{house_id}/optimize")
def optimize_house_in_db(
    house_id: int,
    apply: bool = False,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """
    Розраховує (або одразу застосовує при apply=true) оптимізований розклад
    для всіх увімкнених приладів будинку в БД.
    """
    house = get_user_house(db, house_id, current_user)

    appliances_dicts = []
    for ha in house.appliances:
        if ha.enabled is False:
            continue
        cat = ha.catalog
        power = ha.custom_power if (ha.custom_power and ha.custom_power > 0) else cat.power_watt
        appliances_dicts.append({
            "id": ha.id,
            "name": ha.custom_name or cat.name,
            "power_watt": power,
            "schedule": json.loads(ha.schedule),
            "quantity": ha.quantity,
            "is_cyclic": cat.is_cyclic,
            "duty_cycle": cat.duty_cycle,
        })

    max_power = house.max_power_watt or ((house.voltage or 230.0) * (house.amps or 16.0))
    day_rate = house.day_rate if house.day_rate is not None else 4.32
    night_rate = house.night_rate if house.night_rate is not None else 2.16
    outage = json.loads(house.outage_hours) if isinstance(house.outage_hours, str) and house.outage_hours else []

    opt = optimize_schedule(
        appliances_dicts,
        max_power_watt=max_power,
        day_rate=day_rate,
        night_rate=night_rate,
        outage_hours=outage,
    )

    if apply:
        sched_by_id = {c["id"]: c["new_schedule"] for c in opt["changes"] if c.get("id") is not None}
        for ha in house.appliances:
            if ha.id in sched_by_id:
                ha.schedule = json.dumps(sched_by_id[ha.id])
        db.commit()
        db.refresh(house)
        opt["house"] = serialize_house(house)

    return opt


# --- 2c. АУТЕНТИФІКАЦІЯ, ОСОБИСТІ ПРОФІЛІ ТА ОДНОРАЗОВІ ГОСТІ ---
def _serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "username": user.username,
        "is_guest": bool(user.is_guest),
    }


@app.post("/api/auth/guest")
def create_guest_session(db: Session = Depends(get_db)):
    """
    Створює одноразовий повністю пустий гостьовий акаунт (0 приладів, 0 кВт·год).
    Оптимізовано для одночасного входу 50+ користувачів без колізій та блокувань БД.
    """
    global _LAST_GUEST_CLEANUP_TS
    now = int(time.time())
    if now - _LAST_GUEST_CLEANUP_TS >= 60:
        _LAST_GUEST_CLEANUP_TS = now
        try:
            cleanup_expired_guests(db)
        except Exception:
            db.rollback()

    guest_hex = secrets.token_hex(6)
    uname = f"guest_{guest_hex}"
    short_tag = guest_hex[:4].upper()

    user = User(
        username=uname,
        name=f"Гість #{short_tag}",
        password_hash="guest_ephemeral",
        salt=secrets.token_hex(8),
        is_guest=True,
        created_at=now,
        last_seen_at=now,
    )
    db.add(user)
    db.flush()

    empty_house = House(
        user_id=user.id,
        name="Мій будинок",
        address="",
        voltage=230.0,
        amps=16.0,
        max_power_watt=3680.0,
        day_rate=4.32,
        night_rate=2.16,
        outage_hours="[]",
    )
    db.add(empty_house)
    db.commit()
    db.refresh(user)

    token = create_token(user.id)
    return {"token": token, "user": _serialize_user(user)}


@app.post("/api/auth/logout")
async def logout_session(request: Request, db: Session = Depends(get_db)):
    """
    Завершує сесію. Якщо акаунт гостьовий — негайно видаляє користувача,
    усі його будинки та прилади з бази даних, роблячи подальший доступ неможливим.
    Підтримує як заголовок Authorization, так і navigator.sendBeacon при закритті вкладки.
    """
    token: str | None = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()

    if not token:
        try:
            raw = await request.body()
            if raw:
                payload = json.loads(raw.decode("utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("token"), str):
                    token = payload["token"].strip()
        except Exception:
            token = None

    if not token:
        return {"status": "ok", "deleted_guest": False}

    user_id = verify_token(token)
    if user_id is None:
        return {"status": "ok", "deleted_guest": False}

    user = db.query(User).filter(User.id == user_id).first()
    if user and user.is_guest:
        db.delete(user)
        db.commit()
        return {"status": "ok", "deleted_guest": True}

    return {"status": "ok", "deleted_guest": False}


@app.post("/api/auth/register")
def register_user(data: RegisterRequest, db: Session = Depends(get_db)):
    """Реєструє нового користувача та створює для нього особистий стартовий будинок."""
    uname = data.username.strip().lower()
    if not uname or uname.startswith("guest_"):
        raise HTTPException(status_code=400, detail="Введіть коректний логін (префікс guest_ зарезервовано)")
    if db.query(User).filter(User.username == uname).first():
        raise HTTPException(status_code=409, detail="Такий логін вже зайнятий. Оберіть інший або увійдіть.")

    now = int(time.time())
    pwd_hash, salt = hash_password(data.password)
    user = User(
        username=uname,
        name=data.name.strip(),
        password_hash=pwd_hash,
        salt=salt,
        is_guest=False,
        created_at=now,
        last_seen_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    seed_default_house(db, user_id=user.id, house_name=f"Будинок ({user.name})")
    token = create_token(user.id)
    return {"token": token, "user": _serialize_user(user)}


@app.post("/api/auth/login")
def login_user(data: LoginRequest, db: Session = Depends(get_db)):
    """Вхід у власний постійний акаунт (одноразові гостьові акаунти не підтримують повторний вхід)."""
    uname = data.username.strip().lower()
    user = db.query(User).filter(User.username == uname).first()
    if not user or user.is_guest or not verify_password(data.password, user.password_hash, user.salt):
        raise HTTPException(status_code=401, detail="Невірний логін або пароль")
    token = create_token(user.id)
    return {"token": token, "user": _serialize_user(user)}


@app.get("/api/auth/me")
def get_me(current_user: User | None = Depends(get_current_user)):
    """Повертає дані поточного авторизованого користувача."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Потрібна авторизація")
    return _serialize_user(current_user)


@app.put("/api/auth/me")
def update_me(
    data: ProfileUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """Оновлює ім'я поточного користувача в БД."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Потрібна авторизація")
    current_user.name = data.name.strip()
    db.commit()
    db.refresh(current_user)
    return _serialize_user(current_user)


# --- 3. Будинки (CRUD у БД, ізольовані по користувачах) ---
@app.get("/api/houses")
def list_houses(
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """Список будинків поточного користувача (із живою автосинхронізацією ГПВ, якщо вона увімкнена)."""
    cond = House.user_id == current_user.id if current_user is not None else House.user_id.is_(None)
    houses = db.query(House).filter(cond).order_by(House.id).all()
    out = []
    for h in houses:
        live_gpv = sync_house_gpv_if_enabled(db, h)
        out.append(serialize_house(h, live_gpv=live_gpv))
    return out


# --- 3a. Довідник адрес України (КАТОТТГ: Область -> Район -> Село/Місто -> Вулиця) та живий ГПВ ---
@app.get("/api/geo/oblasts")
def api_geo_oblasts():
    """Повертає список областей України (Вінницька область першою)."""
    return list_oblasts()


@app.get("/api/geo/raions")
def api_geo_raions(oblast: str = "Вінницька"):
    """Повертає всі райони вибраної області."""
    return list_raions(oblast)


@app.get("/api/geo/settlements")
def api_geo_settlements(oblast: str = "Вінницька", raion: str = "Вінницький", q: str = ""):
    """Повертає міста, селища та села вибраного району (усі ~1500 населених пунктів Вінниччини)."""
    return list_settlements(oblast, raion, q=q)


@app.get("/api/geo/streets")
def api_geo_streets(oblast: str = "Вінницька", raion: str = "", settlement: str = "", q: str = ""):
    """Повертає реальні вулиці вибраного села чи міста."""
    if not settlement.strip():
        return []
    return search_streets_for_settlement(oblast, raion, settlement, q=q)


@app.post("/api/gpv/lookup")
def api_gpv_lookup(data: GpvLookupRequest, db: Session = Depends(get_db)):
    """
    Перевіряє актуальний фактичний графік обленерго (`fact`), шукає збережену чергу для цього села/вулиці
    та відсіює черги за відповіддю користувача «Чи є зараз світло?».
    """
    remembered_queue: str | None = None
    if data.settlement.strip():
        street_key, settlement_key = normalize_location_keys(
            data.oblast, data.raion, data.settlement, data.street
        )
        mem = db.query(AddressQueueMemory).filter(AddressQueueMemory.location_key == street_key).first()
        if not mem and street_key != settlement_key:
            mem = db.query(AddressQueueMemory).filter(AddressQueueMemory.location_key == settlement_key).first()
        if mem:
            remembered_queue = mem.gpv_queue

    return match_queues_for_location(
        oblast=data.oblast,
        raion=data.raion,
        settlement=data.settlement,
        street=data.street,
        has_power_now=data.has_power_now,
        remembered_queue=remembered_queue,
    )


def _format_structured_address(oblast: str, raion: str, settlement: str, street: str, house_num: str) -> str:
    parts: list[str] = []
    if oblast and oblast != "м. Київ":
        parts.append(f"{oblast} обл.")
    if raion and raion != "м. Київ" and not settlement.startswith("м. Вінниця"):
        parts.append(f"{raion} р-н")
    if settlement:
        parts.append(settlement.split("(", 1)[0].strip())
    if street:
        st_clean = street.strip()
        if house_num.strip():
            parts.append(f"{st_clean}, {house_num.strip()}")
        else:
            parts.append(st_clean)
    elif house_num.strip():
        parts.append(f"буд. {house_num.strip()}")
    return ", ".join(parts)[:200]


@app.post("/api/houses/{house_id}/gpv-bind")
def bind_house_live_gpv(
    house_id: int,
    data: HouseGpvBindRequest,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """
    Прив'язує повну адресу (область, район, село/місто, вулицю) та чергу обленерго до будинку,
    запам'ятовує чергу для цієї локації в БД і відразу виставляє фактичні години відключень.
    """
    house = get_user_house(db, house_id, current_user)
    reg_code = OBLAST_TO_GPV_REGION.get(data.oblast, ("vinnytsia", ""))[0]
    q_clean = data.gpv_queue.replace("GPV", "").strip()

    house.oblast = data.oblast.strip()
    house.raion = data.raion.strip()
    house.settlement = data.settlement.strip()
    house.street = data.street.strip()
    house.house_num = data.house_num.strip()
    house.gpv_region = reg_code
    house.gpv_queue = q_clean
    house.gpv_auto_sync = data.gpv_auto_sync

    formatted_addr = _format_structured_address(
        house.oblast, house.raion, house.settlement, house.street, house.house_num
    )
    if formatted_addr:
        house.address = formatted_addr

    # Зберігаємо пам'ять черги для цього села/вулиці, щоб наступним мешканцям підставляло автоматично
    if data.remember_for_location and house.settlement:
        street_key, settlement_key = normalize_location_keys(
            house.oblast, house.raion, house.settlement, house.street
        )
        now_ts = int(time.time())
        for idx, key in enumerate([street_key, settlement_key]):
            if not key:
                continue
            existing = db.query(AddressQueueMemory).filter(AddressQueueMemory.location_key == key).first()
            if existing:
                # Вуличний ключ завжди оновлюємо; сільський — якщо це село (с. / с-ще) або запису ще не було
                if idx == 0 or not house.settlement.startswith("м. "):
                    existing.gpv_region = reg_code
                    existing.gpv_queue = q_clean
                    existing.updated_at = now_ts
            else:
                db.add(AddressQueueMemory(
                    location_key=key,
                    gpv_region=reg_code,
                    gpv_queue=q_clean,
                    updated_at=now_ts,
                ))

    live_st = compute_queue_live_status(reg_code, q_clean)
    if data.gpv_auto_sync:
        house.outage_hours = json.dumps(live_st["outage_hours"])
    live_st["schedule_changed"] = True

    db.commit()
    db.refresh(house)
    return serialize_house(house, live_gpv=live_st)


@app.get("/api/houses/{house_id}/gpv-sync")
def sync_house_live_gpv(
    house_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """Перевіряє актуальний стан на сайті обленерго та автоматично знімає/оновлює відключення."""
    house = get_user_house(db, house_id, current_user)
    live_gpv = sync_house_gpv_if_enabled(db, house, force_refresh=force)
    return serialize_house(house, live_gpv=live_gpv)


@app.post("/api/houses")
def create_house(
    data: HouseCreate,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """Створює новий будинок у БД для поточного користувача."""
    max_power = data.max_power_watt if data.max_power_watt is not None else (data.voltage * data.amps)
    house = House(
        user_id=current_user.id if current_user is not None else None,
        name=data.name.strip(),
        address=data.address.strip(),
        voltage=data.voltage,
        amps=data.amps,
        max_power_watt=max_power,
        day_rate=data.day_rate,
        night_rate=data.night_rate,
        outage_hours=json.dumps(data.outage_hours or []),
    )
    db.add(house)
    db.commit()
    db.refresh(house)
    return serialize_house(house)


@app.put("/api/houses/{house_id}")
def update_house(
    house_id: int,
    data: HouseUpdate,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """Оновлює параметри будинку, тарифи, графік відключень або ліміт автомата."""
    house = get_user_house(db, house_id, current_user)

    if data.name is not None:
        house.name = data.name.strip()
    if data.address is not None:
        house.address = data.address.strip()
    if data.voltage is not None:
        house.voltage = data.voltage
    if data.amps is not None:
        house.amps = data.amps
    if data.day_rate is not None:
        house.day_rate = data.day_rate
    if data.night_rate is not None:
        house.night_rate = data.night_rate
    if data.outage_hours is not None:
        house.outage_hours = json.dumps(data.outage_hours)
        # Якщо користувач вручну змінює клітинки годин — вимикаємо автоперезапис від обленерго, поки не увімкне знову
        if data.gpv_auto_sync is None:
            house.gpv_auto_sync = False
    if data.gpv_auto_sync is not None:
        house.gpv_auto_sync = data.gpv_auto_sync

    if data.max_power_watt is not None:
        house.max_power_watt = data.max_power_watt
    else:
        house.max_power_watt = (house.voltage or 230.0) * (house.amps or 16.0)

    db.commit()
    db.refresh(house)
    live_gpv = sync_house_gpv_if_enabled(db, house)
    return serialize_house(house, live_gpv=live_gpv)


@app.delete("/api/houses/{house_id}")
def delete_house(
    house_id: int,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """Видаляє будинок та його прилади з БД."""
    house = get_user_house(db, house_id, current_user)
    db.delete(house)
    db.commit()
    return {"deleted": house_id}


# --- 4. Прилади будинку (CRUD у БД) ---
@app.post("/api/houses/{house_id}/appliances")
def add_appliance_to_house(
    house_id: int,
    data: AddApplianceRequest,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """Додає прилад з каталогу до будинку."""
    get_user_house(db, house_id, current_user)

    catalog_item = db.query(ApplianceCatalog).filter(ApplianceCatalog.id == data.catalog_id).first()
    if not catalog_item:
        raise HTTPException(status_code=404, detail="Прилад не знайдено в каталозі")

    appliance = HouseAppliance(
        house_id=house_id,
        catalog_id=data.catalog_id,
        custom_name=data.custom_name.strip() if data.custom_name else None,
        custom_power=data.custom_power,
        quantity=data.quantity,
        schedule=json.dumps(data.schedule),
        enabled=data.enabled,
    )
    db.add(appliance)
    db.commit()
    db.refresh(appliance)
    return serialize_house_appliance(appliance)


@app.put("/api/houses/{house_id}/appliances/{appliance_id}")
def update_house_appliance(
    house_id: int,
    appliance_id: int,
    data: UpdateApplianceRequest,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """Оновлює прилад будинку (розклад, кількість, увімкнено/вимкнено, потужність)."""
    get_user_house(db, house_id, current_user)
    appliance = (
        db.query(HouseAppliance)
        .filter(HouseAppliance.id == appliance_id, HouseAppliance.house_id == house_id)
        .first()
    )
    if not appliance:
        raise HTTPException(status_code=404, detail="Прилад будинку не знайдено")

    if data.catalog_id is not None:
        cat = db.query(ApplianceCatalog).filter(ApplianceCatalog.id == data.catalog_id).first()
        if not cat:
            raise HTTPException(status_code=404, detail="Прилад не знайдено в каталозі")
        appliance.catalog_id = data.catalog_id

    if data.clear_custom_name:
        appliance.custom_name = None
    elif data.custom_name is not None:
        appliance.custom_name = data.custom_name.strip() or None

    if data.clear_custom_power:
        appliance.custom_power = None
    elif data.custom_power is not None:
        appliance.custom_power = data.custom_power

    if data.quantity is not None:
        appliance.quantity = data.quantity
    if data.schedule is not None:
        appliance.schedule = json.dumps(data.schedule)
    if data.enabled is not None:
        appliance.enabled = data.enabled

    db.commit()
    db.refresh(appliance)
    return serialize_house_appliance(appliance)


@app.delete("/api/houses/{house_id}/appliances/{appliance_id}")
def delete_house_appliance(
    house_id: int,
    appliance_id: int,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """Видаляє прилад із будинку."""
    get_user_house(db, house_id, current_user)
    appliance = (
        db.query(HouseAppliance)
        .filter(HouseAppliance.id == appliance_id, HouseAppliance.house_id == house_id)
        .first()
    )
    if not appliance:
        raise HTTPException(status_code=404, detail="Прилад будинку не знайдено")
    db.delete(appliance)
    db.commit()
    return {"deleted": appliance_id}


@app.post("/api/reset")
def reset_database(
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """Скидає будинки та прилади поточного користувача до початкового стану."""
    cond = House.user_id == current_user.id if current_user is not None else House.user_id.is_(None)
    user_houses = db.query(House).filter(cond).all()
    for uh in user_houses:
        db.delete(uh)
    db.commit()
    uid = current_user.id if current_user is not None else None
    is_guest = bool(current_user and current_user.is_guest)
    hname = "Мій будинок" if (current_user is None or is_guest) else f"Будинок ({current_user.name})"
    seed_default_house(db, user_id=uid, house_name=hname, with_appliances=not is_guest)
    houses = db.query(House).filter(cond).order_by(House.id).all()
    return [serialize_house(h) for h in houses]


HOUSE_PRESETS: dict[str, list[tuple[str, list[int], int, str | None, float | None]]] = {
    "workday": [
        ("Холодильник", list(range(24)), 1, None, None),
        ("Бойлер 80л", [6, 7, 19, 20], 1, None, None),
        ("Освітлення (кімната)", [7, 18, 19, 20, 21, 22], 4, None, None),
        ("Електрочайник", [7, 19], 1, None, None),
        ("Комп'ютер (ПК)", [19, 20, 21, 22], 1, None, None),
    ],
    "weekend": [
        ("Холодильник", list(range(24)), 1, None, None),
        ("Бойлер 80л", [8, 9, 13, 19, 20, 21], 1, None, None),
        ("Пральна машина", [11, 12], 1, None, None),
        ("Посудомийна машина", [15, 16], 1, None, None),
        ("Електроплита (1 конфорка)", [9, 13, 14, 19], 1, None, None),
        ("Телевізор", [10, 11, 16, 17, 18, 19, 20, 21, 22], 1, None, None),
        ("Освітлення (кімната)", [17, 18, 19, 20, 21, 22, 23], 5, None, None),
    ],
    "winter": [
        ("Холодильник", list(range(24)), 1, None, None),
        ("Бойлер 80л", [6, 7, 18, 19, 20, 21], 1, None, None),
        ("Бойлер 80л", [0, 1, 2, 3, 4, 5, 18, 19, 20, 21, 22, 23], 1, "Обігрівач (конвектор)", 1500.0),
        ("Освітлення (кімната)", [7, 8, 16, 17, 18, 19, 20, 21, 22], 5, None, None),
        ("Електрочайник", [7, 12, 18, 21], 1, None, None),
    ],
    "summer": [
        ("Холодильник", list(range(24)), 1, None, None),
        ("Кондиціонер", [12, 13, 14, 15, 16, 17, 18, 19, 20, 21], 1, None, None),
        ("Бойлер 80л", [6, 7, 20, 21], 1, None, None),
        ("Освітлення (кімната)", [20, 21, 22], 3, None, None),
        ("Посудомийна машина", [21, 22], 1, None, None),
    ],
    "eco": [
        ("Холодильник", list(range(24)), 1, None, None),
        ("Бойлер 80л", [0, 1, 2, 3, 4, 5], 1, None, None),
        ("Пральна машина", [23, 0], 1, None, None),
        ("Посудомийна машина", [1, 2], 1, None, None),
        ("Освітлення (кімната)", [19, 20, 21, 22], 2, None, None),
    ],
}


@app.post("/api/houses/{house_id}/preset")
def apply_house_preset(
    house_id: int,
    data: PresetRequest,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """Завантажує готовий сценарій набору приладів та розкладу для будинку."""
    house = get_user_house(db, house_id, current_user)

    catalog_by_name = {c.name: c for c in db.query(ApplianceCatalog).all()}
    db.query(HouseAppliance).filter(HouseAppliance.house_id == house_id).delete()

    for cat_name, sched, qty, custom_name, custom_power in HOUSE_PRESETS[data.preset]:
        cat = catalog_by_name.get(cat_name)
        if not cat:
            continue
        db.add(HouseAppliance(
            house_id=house_id,
            catalog_id=cat.id,
            custom_name=custom_name,
            custom_power=custom_power,
            quantity=qty,
            schedule=json.dumps(sched),
            enabled=True,
        ))

    db.commit()
    db.refresh(house)
    return serialize_house(house)


@app.get("/api/houses/{house_id}/simulate")
def simulate_house(
    house_id: int,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """Симулює споживання для всіх увімкнених приладів конкретного будинку з БД."""
    house = get_user_house(db, house_id, current_user)

    appliance_inputs = []
    for ha in house.appliances:
        if ha.enabled is False:
            continue
        catalog = ha.catalog
        power = ha.custom_power if (ha.custom_power and ha.custom_power > 0) else catalog.power_watt
        appliance_inputs.append(
            ApplianceInput(
                name=ha.custom_name or catalog.name,
                power_watt=power,
                schedule=json.loads(ha.schedule),
                quantity=ha.quantity,
                is_cyclic=catalog.is_cyclic,
                duty_cycle=catalog.duty_cycle,
            )
        )

    max_power = house.max_power_watt or ((house.voltage or 230.0) * (house.amps or 16.0))
    day_rate = house.day_rate if house.day_rate is not None else 4.32
    night_rate = house.night_rate if house.night_rate is not None else 2.16
    outage = json.loads(house.outage_hours) if isinstance(house.outage_hours, str) and house.outage_hours else []

    result = simulate(
        appliance_inputs,
        max_power_watt=max_power,
        day_rate=day_rate,
        night_rate=night_rate,
        outage_hours=outage,
    )

    return {
        "house": serialize_house(house),
        "hourly_watt": result.hourly_watt,
        "hourly_kwh": result.hourly_kwh,
        "hourly_cost": result.hourly_cost,
        "total_kwh": result.total_kwh,
        "total_cost": result.total_cost,
        "peak_watt": result.peak_watt,
        "peak_hour": result.peak_hour,
        "overload_hours": result.overload_hours,
        "per_appliance": result.per_appliance,
        "max_power_watt": max_power,
        "outage_hours": result.outage_hours,
        "outage_backup": result.outage_backup,
        "tariff_comparison": result.tariff_comparison,
    }


# ---------- Роздача статики фронтенду з цього ж сервера ----------
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
