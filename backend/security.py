"""
security.py — Хешування паролів PBKDF2-HMAC-SHA256, токени HMAC та залежності авторизації.
"""

import base64
import hashlib
import hmac
import os
import secrets
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import SessionLocal, House, User

AUTH_SECRET = os.getenv("AUTH_SECRET", "energydim-dev-secret-change-in-prod-2026").encode("utf-8")
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 днів


def get_db():
    """Створює нову сесію БД для кожного запиту."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
