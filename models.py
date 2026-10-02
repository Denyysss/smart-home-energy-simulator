from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import JSON, Float, Integer, String
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

Base = declarative_base()


class Appliance(Base):
    __tablename__ = "appliances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    power_watts: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    work_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cycle_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    active_hours: Mapped[List[int]] = mapped_column(JSON, nullable=False, default=list)


class HouseSettings(Base):
    __tablename__ = "house_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grid_limit_watts: Mapped[float] = mapped_column(Float, nullable=False)
    battery_capacity_wh: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    voltage: Mapped[float] = mapped_column(Float, nullable=False, default=230)
    breaker_amps: Mapped[float] = mapped_column(Float, nullable=False, default=16)
    price_day: Mapped[float] = mapped_column(Float, nullable=False, default=4.32)
    price_night: Mapped[float] = mapped_column(Float, nullable=False, default=2.16)
    night_start: Mapped[int] = mapped_column(Integer, nullable=False, default=23)
    night_end: Mapped[int] = mapped_column(Integer, nullable=False, default=7)


class ApplianceBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    power_watts: float = Field(..., ge=0)
    quantity: int = Field(1, ge=1)
    work_minutes: Optional[float] = Field(None, ge=0)
    cycle_minutes: Optional[float] = Field(None, ge=0)
    active_hours: List[int] = Field(default_factory=list)

    @field_validator("active_hours", mode="before")
    @classmethod
    def validate_active_hours(cls, value: Any) -> List[int]:
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("active_hours має бути списком цілих чисел")
        for hour in value:
            if isinstance(hour, bool) or not isinstance(hour, int):
                raise ValueError("Кожна година має бути цілим числом")
            if not 0 <= hour <= 23:
                raise ValueError("Кожна година має бути в діапазоні від 0 до 23")
        return list(value)


class ApplianceCreate(ApplianceBase):
    pass


class ApplianceResponse(ApplianceBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


class HouseSettingsBase(BaseModel):
    grid_limit_watts: float = Field(..., ge=0)
    battery_capacity_wh: float = Field(0, ge=0)
    voltage: float = Field(230, gt=0)
    breaker_amps: float = Field(16, gt=0)
    price_day: float = Field(4.32, ge=0)
    price_night: float = Field(2.16, ge=0)
    night_start: int = Field(23, ge=0, le=23)
    night_end: int = Field(7, ge=0, le=23)


class HouseSettingsCreate(HouseSettingsBase):
    pass


class HouseSettingsResponse(HouseSettingsBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


class SimulationRequest(BaseModel):
    """Те, що надсилає фронтенд: налаштування будинку + прилади."""
    voltage: float = Field(230, gt=0)
    breaker_amps: float = Field(16, gt=0)
    price_day: float = Field(4.32, ge=0)
    price_night: float = Field(2.16, ge=0)
    night_start: int = Field(23, ge=0, le=23)
    night_end: int = Field(7, ge=0, le=23)
    appliances: List[ApplianceCreate] = Field(default_factory=list)