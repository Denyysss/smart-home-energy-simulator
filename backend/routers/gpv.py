"""
routers/gpv.py — Довідник адрес України (КАТОТТГ: Область -> Район -> Село/Місто -> Вулиця)
та жива синхронізація фактичних графіків ГПВ АТ «Вінницяобленерго» (e-svitlo) й обленерго регіонів.
"""

import json
import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import AddressQueueMemory, House, User
import gpv_service
from schemas import GpvLookupRequest, HouseGpvBindRequest, serialize_house
from security import get_current_user, get_db, get_user_house

router = APIRouter(tags=["Географія КАТОТТГ та живий ГПВ"])


def sync_house_gpv_if_enabled(db: Session, h: House, force_refresh: bool = False) -> dict | None:
    """
    Якщо у будинку ввімкнено автосинхронізацію з обленерго (`gpv_auto_sync`),
    автоматично звіряє години відключень із фактичним оперативним графіком (`fact`).
    """
    if not h.gpv_auto_sync or not h.gpv_queue:
        return None

    reg = h.gpv_region or "vinnytsia"
    sched = gpv_service.get_live_region_schedule(reg, force_refresh=force_refresh)
    live_st = gpv_service.compute_queue_live_status(reg, h.gpv_queue, sched_data=sched)

    old_outage = json.loads(h.outage_hours) if isinstance(h.outage_hours, str) and h.outage_hours else []
    new_outage = live_st["outage_hours"]
    changed = sorted(old_outage) != sorted(new_outage)
    if changed:
        h.outage_hours = json.dumps(new_outage)
        db.commit()
        db.refresh(h)
    live_st["schedule_changed"] = changed
    return live_st


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


@router.get("/api/geo/oblasts")
def api_geo_oblasts():
    return gpv_service.list_oblasts()


@router.get("/api/geo/raions")
def api_geo_raions(oblast: str = "Вінницька"):
    return gpv_service.list_raions(oblast)


@router.get("/api/geo/settlements")
def api_geo_settlements(oblast: str = "Вінницька", raion: str = "Вінницький", q: str = ""):
    return gpv_service.list_settlements(oblast, raion, q=q)


@router.get("/api/geo/streets")
def api_geo_streets(oblast: str = "Вінницька", raion: str = "", settlement: str = "", q: str = ""):
    if not settlement.strip():
        return []
    return gpv_service.search_streets_for_settlement(oblast, raion, settlement, q=q)


@router.post("/api/gpv/lookup")
def api_gpv_lookup(data: GpvLookupRequest, db: Session = Depends(get_db)):
    remembered_queue: str | None = None
    if data.settlement.strip():
        street_key, settlement_key = gpv_service.normalize_location_keys(
            data.oblast, data.raion, data.settlement, data.street
        )
        mem = db.query(AddressQueueMemory).filter(AddressQueueMemory.location_key == street_key).first()
        if not mem and street_key != settlement_key:
            mem = db.query(AddressQueueMemory).filter(AddressQueueMemory.location_key == settlement_key).first()
        if mem:
            remembered_queue = mem.gpv_queue

    return gpv_service.match_queues_for_location(
        oblast=data.oblast,
        raion=data.raion,
        settlement=data.settlement,
        street=data.street,
        has_power_now=data.has_power_now,
        remembered_queue=remembered_queue,
    )


@router.post("/api/houses/{house_id}/gpv-bind")
def bind_house_live_gpv(
    house_id: int,
    data: HouseGpvBindRequest,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    house = get_user_house(db, house_id, current_user)
    reg_code = gpv_service.OBLAST_TO_GPV_REGION.get(data.oblast, ("vinnytsia", ""))[0]
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

    if data.remember_for_location and house.settlement:
        street_key, settlement_key = gpv_service.normalize_location_keys(
            house.oblast, house.raion, house.settlement, house.street
        )
        now_ts = int(time.time())
        for idx, key in enumerate([street_key, settlement_key]):
            if not key:
                continue
            existing = db.query(AddressQueueMemory).filter(AddressQueueMemory.location_key == key).first()
            if existing:
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

    live_st = gpv_service.compute_queue_live_status(reg_code, q_clean)
    if data.gpv_auto_sync:
        house.outage_hours = json.dumps(live_st["outage_hours"])
    live_st["schedule_changed"] = True

    db.commit()
    db.refresh(house)
    return serialize_house(house, live_gpv=live_st)


@router.get("/api/houses/{house_id}/gpv-sync")
def sync_house_live_gpv(
    house_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    house = get_user_house(db, house_id, current_user)
    live_gpv = sync_house_gpv_if_enabled(db, house, force_refresh=force)
    return serialize_house(house, live_gpv=live_gpv)
