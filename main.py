from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from calculator import calculate_day
from models import Appliance, Base, HouseSettings, SimulationRequest

engine = create_engine("sqlite:///./energy.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app = FastAPI(title="Симулятор споживання електроенергії")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

BASE_DIR = Path(__file__).resolve().parent


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(BASE_DIR / "index.html")


def save_config(db: Session, req: SimulationRequest, limit: float) -> None:
    """Зберігає останню конфігурацію (попередню замінює)."""
    db.query(Appliance).delete()
    db.query(HouseSettings).delete()

    seen: dict[str, int] = {}
    for a in req.appliances:
        seen[a.name] = seen.get(a.name, 0) + 1
        name = a.name if seen[a.name] == 1 else f"{a.name} ({seen[a.name]})"  # name унікальне
        db.add(
            Appliance(
                name=name,
                power_watts=a.power_watts,
                quantity=a.quantity,
                work_minutes=a.work_minutes,
                cycle_minutes=a.cycle_minutes,
                active_hours=a.active_hours,
            )
        )
    db.add(
        HouseSettings(
            grid_limit_watts=limit,
            voltage=req.voltage,
            breaker_amps=req.breaker_amps,
            price_day=req.price_day,
            price_night=req.price_night,
            night_start=req.night_start,
            night_end=req.night_end,
        )
    )
    db.commit()


@app.post("/api/simulate")
def simulate(req: SimulationRequest, db: Session = Depends(get_db)):
    limit = req.voltage * req.breaker_amps
    try:
        save_config(db, req, limit)
    except Exception:
        db.rollback()  # збій збереження не блокує розрахунок

    try:
        return calculate_day(
            req.appliances, limit, req.price_day, req.price_night,
            req.night_start, req.night_end,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Помилка розрахунку: {exc}")


@app.get("/api/config")
def get_config(db: Session = Depends(get_db)):
    """Остання збережена конфігурація (null, якщо ще нічого не збережено)."""
    s = db.query(HouseSettings).first()
    if s is None:
        return None
    return {
        "voltage": s.voltage,
        "breaker_amps": s.breaker_amps,
        "price_day": s.price_day,
        "price_night": s.price_night,
        "night_start": s.night_start,
        "night_end": s.night_end,
        "appliances": [
            {
                "name": a.name,
                "power_watts": a.power_watts,
                "quantity": a.quantity,
                "work_minutes": a.work_minutes,
                "cycle_minutes": a.cycle_minutes,
                "active_hours": a.active_hours,
            }
            for a in db.query(Appliance).order_by(Appliance.id).all()
        ],
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)