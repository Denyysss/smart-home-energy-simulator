"""
routers/auth.py — Реєстрація, вхід, одноразові пусті гостьові сесії та профіль користувача.
"""

import json
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import House, User, cleanup_expired_guests, seed_default_house
from schemas import LoginRequest, ProfileUpdateRequest, RegisterRequest, serialize_user
from security import create_token, get_current_user, get_db, hash_password, verify_password, verify_token

router = APIRouter(prefix="/api/auth", tags=["Авторизація та профіль"])

_LAST_GUEST_CLEANUP_TS = 0


def reset_cleanup_ts():
    global _LAST_GUEST_CLEANUP_TS
    _LAST_GUEST_CLEANUP_TS = int(time.time())


@router.post("/guest")
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
    return {"token": token, "user": serialize_user(user)}


@router.post("/logout")
async def logout_session(request: Request, db: Session = Depends(get_db)):
    """
    Завершує сесію. Якщо акаунт гостьовий — негайно видаляє користувача,
    усі його будинки та прилади з бази даних, роблячи подальший доступ неможливим.
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


@router.post("/register")
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
    return {"token": token, "user": serialize_user(user)}


@router.post("/login")
def login_user(data: LoginRequest, db: Session = Depends(get_db)):
    """Вхід у власний постійний акаунт."""
    uname = data.username.strip().lower()
    user = db.query(User).filter(User.username == uname).first()
    if not user or user.is_guest or not verify_password(data.password, user.password_hash, user.salt):
        raise HTTPException(status_code=401, detail="Невірний логін або пароль")
    token = create_token(user.id)
    return {"token": token, "user": serialize_user(user)}


@router.get("/me")
def get_me(current_user: User | None = Depends(get_current_user)):
    """Повертає дані поточного авторизованого користувача."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Потрібна авторизація")
    return serialize_user(current_user)


@router.put("/me")
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
    return serialize_user(current_user)
