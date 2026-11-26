"""
main.py — Головна точка входу FastAPI сервера ЕнергіяДім (модульна архітектура).

Запуск:
    python -m uvicorn main:app --reload --port 8000

Модулі бекенду:
    - database.py         : Моделі SQLAlchemy, ініціалізація та міграції БД
    - schemas.py          : Pydantic-схеми та серіалізатори
    - security.py         : Хешування PBKDF2, токени HMAC та авторизація
    - simulation.py       : Математичне ядро розрахунків та оптимізатор
    - gpv_service.py      : Довідник КАТОТТГ, вулиці та живий ГПВ обленерго
    - routers/auth.py     : /api/auth/* (реєстрація, вхід, одноразові гості)
    - routers/gpv.py      : /api/geo/* та /api/gpv/* (адреси і живий графік)
    - routers/houses.py   : /api/houses/*, /api/catalog, /api/simulate, /api/optimize
"""

from contextlib import asynccontextmanager
import mimetypes
import os
from pathlib import Path

# Гарантуємо коректний MIME-тип для ES-модулів на всіх ноутбуках Windows
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from database import SessionLocal, cleanup_expired_guests, init_db, seed_catalog
from gpv_service import load_katottg
from routers.auth import reset_cleanup_ts, router as auth_router
from routers.gpv import router as gpv_router
from routers.houses import router as houses_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    load_katottg()
    db = SessionLocal()
    try:
        seed_catalog(db)
        cleanup_expired_guests(db)
        reset_cleanup_ts()
    finally:
        db.close()
    yield


app = FastAPI(
    title="⚡ Energy Simulator API",
    description="Симулятор споживання електроенергії побутовими приладами (ЕнергіяДім)",
    version="1.2.0",
    lifespan=lifespan,
)

# Стиснення відповідей (JSON, КАТОТТГ, HTML/CSS/JS) у 4–5 разів для швидкого завантаження
app.add_middleware(GZipMiddleware, minimum_size=500)

DEFAULT_ORIGINS = (
    "http://localhost:8080,"
    "http://127.0.0.1:8080,"
    "http://localhost:8000,"
    "http://127.0.0.1:8000,"
    "http://localhost:5500,"
    "http://127.0.0.1:5500,"
    "http://localhost:5501,"
    "http://127.0.0.1:5501,"
    "null"
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
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def security_and_nocache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    path = request.url.path
    if path == "/" or path.endswith((".html", ".js", ".css")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


# ---------- Підключення модульних роутерів ----------
app.include_router(auth_router)
app.include_router(gpv_router)
app.include_router(houses_router)

# ---------- Роздача статики фронтенду ----------
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
