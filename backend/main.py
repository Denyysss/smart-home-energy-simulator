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

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from database import (
    SessionLocal, init_db, seed_catalog, seed_default_house,
    ApplianceCatalog, House, HouseAppliance
)
from simulation import simulate, optimize_schedule, ApplianceInput


# ---------- Lifespan (замість застарілого @app.on_event("startup")) ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        seed_catalog(db)
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


# ---------- Залежність: отримання сесії БД ----------
def get_db():
    """Створює нову сесію БД для кожного запиту."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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


class HouseUpdate(BaseModel):
    """Запит на оновлення будинку."""
    name: str | None = Field(None, min_length=1, max_length=100)
    address: str | None = Field(None, max_length=200)
    voltage: float | None = Field(None, gt=0, le=1000)
    amps: float | None = Field(None, gt=0, le=1000)
    max_power_watt: float | None = Field(None, gt=0, le=1000000)
    day_rate: float | None = Field(None, ge=0, le=1000)
    night_rate: float | None = Field(None, ge=0, le=1000)


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


def serialize_house(h: House) -> dict:
    voltage = h.voltage if h.voltage is not None else 230.0
    amps = h.amps if h.amps is not None else 16.0
    max_power = h.max_power_watt if h.max_power_watt else (voltage * amps)
    return {
        "id": h.id,
        "name": h.name,
        "address": h.address or "",
        "voltage": voltage,
        "amps": amps,
        "max_power_watt": max_power,
        "dayRate": h.day_rate if h.day_rate is not None else 4.32,
        "nightRate": h.night_rate if h.night_rate is not None else 2.16,
        "day_rate": h.day_rate if h.day_rate is not None else 4.32,
        "night_rate": h.night_rate if h.night_rate is not None else 2.16,
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
    )


@app.post("/api/houses/{house_id}/optimize")
def optimize_house_in_db(house_id: int, apply: bool = False, db: Session = Depends(get_db)):
    """
    Розраховує (або одразу застосовує при apply=true) оптимізований розклад
    для всіх увімкнених приладів будинку в БД.
    """
    house = db.query(House).filter(House.id == house_id).first()
    if not house:
        raise HTTPException(status_code=404, detail="Будинок не знайдено")

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

    opt = optimize_schedule(
        appliances_dicts,
        max_power_watt=max_power,
        day_rate=day_rate,
        night_rate=night_rate,
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


# --- 3. Будинки (CRUD у БД) ---
@app.get("/api/houses")
def list_houses(db: Session = Depends(get_db)):
    """Список усіх будинків разом із їхніми приладами."""
    houses = db.query(House).order_by(House.id).all()
    return [serialize_house(h) for h in houses]


@app.post("/api/houses")
def create_house(data: HouseCreate, db: Session = Depends(get_db)):
    """Створює новий будинок у БД."""
    max_power = data.max_power_watt if data.max_power_watt is not None else (data.voltage * data.amps)
    house = House(
        name=data.name.strip(),
        address=data.address.strip(),
        voltage=data.voltage,
        amps=data.amps,
        max_power_watt=max_power,
        day_rate=data.day_rate,
        night_rate=data.night_rate,
    )
    db.add(house)
    db.commit()
    db.refresh(house)
    return serialize_house(house)


@app.put("/api/houses/{house_id}")
def update_house(house_id: int, data: HouseUpdate, db: Session = Depends(get_db)):
    """Оновлює параметри будинку, тарифи або ліміт автомата."""
    house = db.query(House).filter(House.id == house_id).first()
    if not house:
        raise HTTPException(status_code=404, detail="Будинок не знайдено")

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

    if data.max_power_watt is not None:
        house.max_power_watt = data.max_power_watt
    else:
        house.max_power_watt = (house.voltage or 230.0) * (house.amps or 16.0)

    db.commit()
    db.refresh(house)
    return serialize_house(house)


@app.delete("/api/houses/{house_id}")
def delete_house(house_id: int, db: Session = Depends(get_db)):
    """Видаляє будинок та його прилади з БД."""
    house = db.query(House).filter(House.id == house_id).first()
    if not house:
        raise HTTPException(status_code=404, detail="Будинок не знайдено")
    db.delete(house)
    db.commit()
    return {"deleted": house_id}


# --- 4. Прилади будинку (CRUD у БД) ---
@app.post("/api/houses/{house_id}/appliances")
def add_appliance_to_house(
    house_id: int,
    data: AddApplianceRequest,
    db: Session = Depends(get_db),
):
    """Додає прилад з каталогу до будинку."""
    house = db.query(House).filter(House.id == house_id).first()
    if not house:
        raise HTTPException(status_code=404, detail="Будинок не знайдено")

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
):
    """Оновлює прилад будинку (розклад, кількість, увімкнено/вимкнено, потужність)."""
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
):
    """Видаляє прилад із будинку."""
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
def reset_database(db: Session = Depends(get_db)):
    """Скидає всі будинки та прилади у БД до початкового стану."""
    db.query(HouseAppliance).delete()
    db.query(House).delete()
    db.commit()
    seed_default_house(db)
    houses = db.query(House).order_by(House.id).all()
    return [serialize_house(h) for h in houses]


@app.get("/api/houses/{house_id}/simulate")
def simulate_house(house_id: int, db: Session = Depends(get_db)):
    """Симулює споживання для всіх увімкнених приладів конкретного будинку з БД."""
    house = db.query(House).filter(House.id == house_id).first()
    if not house:
        raise HTTPException(status_code=404, detail="Будинок не знайдено")

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

    result = simulate(
        appliance_inputs,
        max_power_watt=max_power,
        day_rate=day_rate,
        night_rate=night_rate,
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
    }


# ---------- Роздача статики фронтенду з цього ж сервера ----------
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
