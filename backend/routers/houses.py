"""
routers/houses.py — Каталог приладів, CRUD будинків та приладів, пресети, симуляція й оптимізатор.
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import ApplianceCatalog, House, HouseAppliance, User, seed_default_house
from routers.gpv import sync_house_gpv_if_enabled
from schemas import (
    AddApplianceRequest,
    HouseCreate,
    HouseUpdate,
    PresetRequest,
    SimulationRequest,
    UpdateApplianceRequest,
    serialize_house,
    serialize_house_appliance,
)
from security import get_current_user, get_db, get_user_house
from simulation import ApplianceInput, optimize_schedule, simulate

router = APIRouter(tags=["Будинки, прилади та симуляція"])

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


@router.get("/api/catalog")
def get_catalog(db: Session = Depends(get_db)):
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


@router.post("/api/simulate")
def run_simulation(request: SimulationRequest):
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


@router.post("/api/optimize")
def run_optimization(request: SimulationRequest):
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


@router.post("/api/houses/{house_id}/optimize")
def optimize_house_in_db(
    house_id: int,
    apply: bool = False,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
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


@router.get("/api/houses")
def list_houses(
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    cond = House.user_id == current_user.id if current_user is not None else House.user_id.is_(None)
    houses = db.query(House).filter(cond).order_by(House.id).all()
    out = []
    for h in houses:
        live_gpv = sync_house_gpv_if_enabled(db, h)
        out.append(serialize_house(h, live_gpv=live_gpv))
    return out


@router.post("/api/houses")
def create_house(
    data: HouseCreate,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
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


@router.put("/api/houses/{house_id}")
def update_house(
    house_id: int,
    data: HouseUpdate,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
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


@router.delete("/api/houses/{house_id}")
def delete_house(
    house_id: int,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    house = get_user_house(db, house_id, current_user)
    db.delete(house)
    db.commit()
    return {"deleted": house_id}


@router.post("/api/houses/{house_id}/appliances")
def add_appliance_to_house(
    house_id: int,
    data: AddApplianceRequest,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
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


@router.put("/api/houses/{house_id}/appliances/{appliance_id}")
def update_house_appliance(
    house_id: int,
    appliance_id: int,
    data: UpdateApplianceRequest,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
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


@router.delete("/api/houses/{house_id}/appliances/{appliance_id}")
def delete_house_appliance(
    house_id: int,
    appliance_id: int,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
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


@router.post("/api/reset")
def reset_database(
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
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


@router.post("/api/houses/{house_id}/preset")
def apply_house_preset(
    house_id: int,
    data: PresetRequest,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
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


@router.get("/api/houses/{house_id}/simulate")
def simulate_house(
    house_id: int,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
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
