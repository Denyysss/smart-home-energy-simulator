from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from calculator import calculate_daily_balance
from models import (
    Appliance,
    Base,
    Battery,
    SimulationRequest,
    SimulationResponse,
    SolarStation,
)

# ---------------------------------------------------------------------------
# База даних (SQLite через SQLAlchemy)
# ---------------------------------------------------------------------------
DATABASE_URL = "sqlite:///./energy.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# FastAPI + CORS
# ---------------------------------------------------------------------------
app = FastAPI(title="Симулятор енергонезалежності будинку", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # для MVP; у продакшені вкажіть конкретні домени
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent


@app.get("/", include_in_schema=False)
def index():
    """Віддає фронтенд, щоб усе працювало з одного адреса."""
    return FileResponse(BASE_DIR / "index.html")


def save_configuration(db: Session, data: SimulationRequest) -> None:
    """Зберігає останню конфігурацію будинку в БД (попередню замінює)."""
    db.query(Appliance).delete()
    db.query(SolarStation).delete()
    db.query(Battery).delete()

    for item in data.appliances:
        db.add(Appliance(name=item.name, power_w=item.power_w, hours=item.hours))
    db.add(SolarStation(peak_power_w=data.solar.peak_power_w))
    db.add(Battery(capacity_wh=data.battery.capacity_wh))
    db.commit()


@app.post("/api/simulate", response_model=SimulationResponse)
def simulate(data: SimulationRequest, db: Session = Depends(get_db)):
    """Приймає конфігурацію будинку та повертає добовий енергобаланс."""
    try:
        save_configuration(db, data)
    except Exception:
        db.rollback()  # збій збереження не повинен блокувати розрахунок

    try:
        result = calculate_daily_balance(
            appliances=[a.model_dump() for a in data.appliances],
            solar_peak_w=data.solar.peak_power_w,
            battery_capacity_wh=data.battery.capacity_wh,
            initial_charge_wh=data.battery.initial_charge_wh or 0.0,
            tariff_day=data.tariff_day,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Помилка розрахунку: {exc}")

    return result