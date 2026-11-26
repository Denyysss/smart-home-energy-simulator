"""
schemas.py — Pydantic-схеми валідації запитів та серіалізація моделей БД.
"""

import json
from pydantic import BaseModel, ConfigDict, Field, field_validator
from database import House, HouseAppliance, User


def validate_hours_list(schedule: list[int]) -> list[int]:
    """Спільний валідатор розкладу годин (0..23)."""
    if len(schedule) > 24:
        raise ValueError("Розклад не може містити більше 24 елементів")
    cleaned: set[int] = set()
    for h in schedule:
        if isinstance(h, bool) or not isinstance(h, int) or not (0 <= h <= 23):
            raise ValueError(f"Година {h!r} некоректна: дозволені цілі числа від 0 до 23")
        cleaned.add(h)
    return sorted(cleaned)


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
    """Тіло POST-запиту на /api/simulate та /api/optimize."""
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
                    }
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
    """Пошук та фільтрація черги ГПВ за областю, районом, селом, вулицею та наявністю світла."""
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


def serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "username": user.username,
        "is_guest": bool(user.is_guest),
    }


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
