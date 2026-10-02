from typing import List, Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Column, Float, Integer, String, JSON
from sqlalchemy.orm import DeclarativeBase


# ---------------------------------------------------------------------------
# SQLAlchemy: таблиці бази даних
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


class Appliance(Base):
    """Побутовий прилад."""
    __tablename__ = "appliances"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    power_w = Column(Float, nullable=False)          # потужність, Вт
    hours = Column(JSON, nullable=False, default=list)  # години роботи, напр. [7, 8, 19, 20]


class SolarStation(Base):
    """Сонячні панелі."""
    __tablename__ = "solar_stations"

    id = Column(Integer, primary_key=True, index=True)
    peak_power_w = Column(Float, nullable=False)     # пікова потужність, Вт


class Battery(Base):
    """Акумулятор / EcoFlow."""
    __tablename__ = "batteries"

    id = Column(Integer, primary_key=True, index=True)
    capacity_wh = Column(Float, nullable=False)      # ємність, Вт·год


# ---------------------------------------------------------------------------
# Pydantic: схеми для даних від фронтенду
# ---------------------------------------------------------------------------
class ApplianceSchema(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    power_w: float = Field(..., ge=0, description="Потужність приладу, Вт")
    hours: List[int] = Field(default_factory=list, description="Години роботи (0-23)")

    @field_validator("hours")
    @classmethod
    def validate_hours(cls, value: List[int]) -> List[int]:
        for h in value:
            if h < 0 or h > 23:
                raise ValueError("Година має бути в діапазоні 0-23")
        return sorted(set(value))

    model_config = {"from_attributes": True}


class SolarSchema(BaseModel):
    peak_power_w: float = Field(0, ge=0, description="Пікова потужність панелей, Вт")

    model_config = {"from_attributes": True}


class BatterySchema(BaseModel):
    capacity_wh: float = Field(0, ge=0, description="Ємність акумулятора, Вт·год")
    initial_charge_wh: Optional[float] = Field(
        None, ge=0, description="Початковий заряд, Вт·год (за замовчуванням 0)"
    )

    model_config = {"from_attributes": True}


class SimulationRequest(BaseModel):
    """Повна конфігурація будинку, яку надсилає фронтенд."""
    appliances: List[ApplianceSchema] = Field(default_factory=list)
    solar: SolarSchema = Field(default_factory=SolarSchema)
    battery: BatterySchema = Field(default_factory=BatterySchema)
    tariff_day: float = Field(4.32, ge=0, description="Денний тариф, грн/кВт·год")


class SimulationResponse(BaseModel):
    consumption: List[float]
    solar_generation: List[float]
    battery_level: List[float]
    grid_usage: List[float]
    total_cost: float