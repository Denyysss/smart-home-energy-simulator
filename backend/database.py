"""
database.py — Ініціалізація БД та моделі таблиць (SQLite через SQLAlchemy).

Таблиці:
  1. appliance_catalog — Довідник приладів (бойлер, холодильник, тощо)
  2. houses            — Будинки / квартири користувачів
  3. house_appliances  — Прилади, які додані до конкретного будинку
"""

import json
import os
import time
from sqlalchemy import (
    create_engine, Column, Integer, Float, String,
    Boolean, ForeignKey, Text, event, inspect, text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

# ---------- Підключення до SQLite / PostgreSQL ----------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./energy.db")
SQL_ECHO = os.getenv("SQL_ECHO", "false").lower() == "true"
IS_SQLITE = DATABASE_URL.startswith("sqlite")

connect_args = {"check_same_thread": False, "timeout": 30} if IS_SQLITE else {}
pool_kwargs = (
    {}
    if ":memory:" in DATABASE_URL
    else {"pool_size": 25, "max_overflow": 50, "pool_timeout": 30, "pool_pre_ping": True}
)

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=SQL_ECHO,  # Вимкнено за замовчуванням, щоб не засмічувати логи
    **pool_kwargs,
)

if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ============================================================
# 0. КОРИСТУВАЧІ (особисті та одноразові гостьові акаунти)
# ============================================================
class User(Base):
    """Користувач системи з власним набором будинків та приладів."""
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String(100), nullable=False, unique=True, index=True)
    name          = Column(String(100), nullable=False)
    password_hash = Column(String(200), nullable=False)
    salt          = Column(String(64),  nullable=False)
    is_guest      = Column(Boolean, default=False, nullable=False, index=True)
    created_at    = Column(Integer, default=lambda: int(time.time()), nullable=False)
    last_seen_at  = Column(Integer, default=lambda: int(time.time()), nullable=False)

    houses = relationship("House", back_populates="owner", cascade="all, delete-orphan")


# ============================================================
# 1. ДОВІДНИК ПРИЛАДІВ (каталог)
# ============================================================
class ApplianceCatalog(Base):
    """
    Містить типові побутові прилади з їхніми характеристиками.
    Заповнюється один раз (seed-дані).
    """
    __tablename__ = "appliance_catalog"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(100), nullable=False, unique=True)
    name_en     = Column(String(100), nullable=True)
    category    = Column(String(50),  nullable=False)
    power_watt  = Column(Float,       nullable=False)
    is_cyclic   = Column(Boolean,     default=False)
    duty_cycle  = Column(Float,       default=1.0)
    description = Column(Text,        nullable=True)


# ============================================================
# 2. БУДИНКИ
# ============================================================
class House(Base):
    """
    Представляє окрему квартиру / будинок з параметрами мережі та тарифами.
    """
    __tablename__ = "houses"

    id              = Column(Integer, primary_key=True, index=True)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    name            = Column(String(100), nullable=False)
    address         = Column(String(200), nullable=True, default="")
    voltage         = Column(Float, nullable=False, default=230.0)
    amps            = Column(Float, nullable=False, default=16.0)
    max_power_watt  = Column(Float, nullable=False, default=3680.0)
    day_rate        = Column(Float, nullable=False, default=4.32)
    night_rate      = Column(Float, nullable=False, default=2.16)
    outage_hours    = Column(Text,  nullable=False, default="[]")
    oblast          = Column(String(80),  nullable=True, default="")
    raion           = Column(String(100), nullable=True, default="")
    settlement      = Column(String(120), nullable=True, default="")
    street          = Column(String(150), nullable=True, default="")
    house_num       = Column(String(30),  nullable=True, default="")
    gpv_region      = Column(String(40),  nullable=True, default="vinnytsia")
    gpv_queue       = Column(String(16),  nullable=True, default="")
    gpv_auto_sync   = Column(Boolean,     nullable=False, default=False)

    owner = relationship("User", back_populates="houses")
    appliances = relationship(
        "HouseAppliance",
        back_populates="house",
        cascade="all, delete-orphan",
        order_by="HouseAppliance.id",
    )


# ============================================================
# 3. ПРИЛАДИ БУДИНКУ (конкретні прилади, додані користувачем)
# ============================================================
class HouseAppliance(Base):
    """
    Конкретний прилад у конкретному будинку.
    Користувач вказує, в які години він працює.
    """
    __tablename__ = "house_appliances"

    id             = Column(Integer, primary_key=True, index=True)
    house_id       = Column(Integer, ForeignKey("houses.id"), nullable=False)
    catalog_id     = Column(Integer, ForeignKey("appliance_catalog.id"), nullable=False)
    custom_name    = Column(String(100), nullable=True)
    custom_power   = Column(Float, nullable=True)
    quantity       = Column(Integer, default=1, nullable=False)
    schedule       = Column(Text, nullable=False)  # JSON-рядок: [6,7,8,18,19,20]
    enabled        = Column(Boolean, default=True, nullable=False)

    house   = relationship("House", back_populates="appliances")
    catalog = relationship("ApplianceCatalog")


# ============================================================
# 4. ПАМ'ЯТЬ АДРЕС -> ЧЕРГА ГПВ (для сіл і вулиць області)
# ============================================================
class AddressQueueMemory(Base):
    """
    Зберігає прив'язку населеного пункту / вулиці до підчерги ГПВ обленерго,
    щоб при повторному виборі цього ж села чи вулиці черга підставлялась автоматично.
    """
    __tablename__ = "address_queue_memory"

    id           = Column(Integer, primary_key=True, index=True)
    location_key = Column(String(320), nullable=False, unique=True, index=True)
    gpv_region   = Column(String(40),  nullable=False, default="vinnytsia")
    gpv_queue    = Column(String(16),  nullable=False)
    updated_at   = Column(Integer,     default=lambda: int(time.time()), nullable=False)


# ---------- Створення та міграція таблиць ----------
def init_db():
    """Створює всі таблиці та додає відсутні колонки у випадку оновлення."""
    Base.metadata.create_all(bind=engine)
    _ensure_columns()


def _ensure_columns():
    """Безпечно додає нові колонки в існуючу SQLite базу даних."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    migrations = {
        "users": [
            ("is_guest", "BOOLEAN DEFAULT 0"),
            ("created_at", "INTEGER DEFAULT 0"),
            ("last_seen_at", "INTEGER DEFAULT 0"),
        ],
        "houses": [
            ("user_id", "INTEGER"),
            ("address", "VARCHAR(200) DEFAULT ''"),
            ("voltage", "FLOAT DEFAULT 230.0"),
            ("amps", "FLOAT DEFAULT 16.0"),
            ("day_rate", "FLOAT DEFAULT 4.32"),
            ("night_rate", "FLOAT DEFAULT 2.16"),
            ("outage_hours", "TEXT DEFAULT '[]'"),
            ("oblast", "VARCHAR(80) DEFAULT ''"),
            ("raion", "VARCHAR(100) DEFAULT ''"),
            ("settlement", "VARCHAR(120) DEFAULT ''"),
            ("street", "VARCHAR(150) DEFAULT ''"),
            ("house_num", "VARCHAR(30) DEFAULT ''"),
            ("gpv_region", "VARCHAR(40) DEFAULT 'vinnytsia'"),
            ("gpv_queue", "VARCHAR(16) DEFAULT ''"),
            ("gpv_auto_sync", "BOOLEAN DEFAULT 0"),
        ],
        "house_appliances": [
            ("custom_power", "FLOAT"),
            ("enabled", "BOOLEAN DEFAULT 1"),
        ],
    }

    with engine.begin() as conn:
        for table, cols in migrations.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for col_name, col_def in cols:
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))


# ---------- Seed-дані (довідник приладів + стартовий будинок) ----------
def seed_catalog(db):
    """
    Додає типові прилади в каталог, якщо таблиця порожня,
    та створює стартовий будинок із приладами, якщо будинків ще немає.
    """
    if db.query(ApplianceCatalog).count() == 0:
        defaults = [
            ApplianceCatalog(
                name="Бойлер 80л", name_en="boiler_80l", category="heating",
                power_watt=2000, is_cyclic=False, duty_cycle=1.0,
                description="Електричний водонагрівач на 80 літрів"
            ),
            ApplianceCatalog(
                name="Холодильник", name_en="fridge", category="cooling",
                power_watt=150, is_cyclic=True, duty_cycle=0.35,
                description="Компресор працює ~35% часу (циклічно)"
            ),
            ApplianceCatalog(
                name="Освітлення (кімната)", name_en="room_light", category="lighting",
                power_watt=100, is_cyclic=False, duty_cycle=1.0,
                description="Лампи в одній кімнаті (~100 Вт сумарно)"
            ),
            ApplianceCatalog(
                name="Пральна машина", name_en="washing_machine", category="heating",
                power_watt=2200, is_cyclic=True, duty_cycle=0.5,
                description="Середня потужність за цикл прання"
            ),
            ApplianceCatalog(
                name="Мікрохвильовка", name_en="microwave", category="heating",
                power_watt=1200, is_cyclic=False, duty_cycle=1.0,
                description="Мікрохвильова піч"
            ),
            ApplianceCatalog(
                name="Кондиціонер", name_en="air_conditioner", category="cooling",
                power_watt=1500, is_cyclic=True, duty_cycle=0.6,
                description="Спліт-система, компресор працює ~60% часу"
            ),
            ApplianceCatalog(
                name="Телевізор", name_en="tv", category="entertainment",
                power_watt=120, is_cyclic=False, duty_cycle=1.0,
                description="LED-телевізор 42 дюйми"
            ),
            ApplianceCatalog(
                name="Електрочайник", name_en="kettle", category="heating",
                power_watt=2000, is_cyclic=False, duty_cycle=0.1,
                description="Кип'ятить ~6 хв/год → duty_cycle ≈ 0.1"
            ),
            ApplianceCatalog(
                name="Комп'ютер (ПК)", name_en="desktop_pc", category="entertainment",
                power_watt=350, is_cyclic=False, duty_cycle=1.0,
                description="Десктоп з монітором"
            ),
            ApplianceCatalog(
                name="Електроплита (1 конфорка)", name_en="stove_1burner", category="heating",
                power_watt=1500, is_cyclic=True, duty_cycle=0.7,
                description="Одна конфорка, середнє навантаження"
            ),
            ApplianceCatalog(
                name="Посудомийна машина", name_en="dishwasher", category="heating",
                power_watt=1800, is_cyclic=True, duty_cycle=0.6,
                description="Цикл миття посуду з підігрівом води"
            ),
            ApplianceCatalog(
                name="Зарядка електромобіля", name_en="ev_charger", category="heating",
                power_watt=3500, is_cyclic=False, duty_cycle=1.0,
                description="Домашня зарядна станція 16А"
            ),
        ]
        db.add_all(defaults)
        db.commit()
    else:
        extra = [
            ApplianceCatalog(
                name="Посудомийна машина", name_en="dishwasher", category="heating",
                power_watt=1800, is_cyclic=True, duty_cycle=0.6,
                description="Цикл миття посуду з підігрівом води"
            ),
            ApplianceCatalog(
                name="Зарядка електромобіля", name_en="ev_charger", category="heating",
                power_watt=3500, is_cyclic=False, duty_cycle=1.0,
                description="Домашня зарядна станція 16А"
            ),
        ]
        existing_names = {c.name for c in db.query(ApplianceCatalog).all()}
        for item in extra:
            if item.name not in existing_names:
                db.add(item)
        db.commit()

    seed_default_house(db)


def seed_default_house(
    db,
    user_id: int | None = None,
    house_name: str = "Мій будинок",
    with_appliances: bool = True,
    address: str | None = None,
):
    """Створює стартовий будинок для користувача (з приладами або повністю пустий для гостя)."""
    q = db.query(House).filter(House.user_id.is_(None) if user_id is None else House.user_id == user_id)
    if q.count() > 0:
        return

    resolved_address = ("вул. Центральна, 8" if with_appliances else "") if address is None else address

    house = House(
        user_id=user_id,
        name=house_name,
        address=resolved_address,
        voltage=230.0,
        amps=16.0,
        max_power_watt=3680.0,
        day_rate=4.32,
        night_rate=2.16,
    )
    db.add(house)
    db.commit()
    db.refresh(house)

    if not with_appliances:
        return

    catalog_by_name = {c.name: c for c in db.query(ApplianceCatalog).all()}
    starter_appliances = [
        ("Бойлер 80л", [6, 7, 18, 19, 20], 1),
        ("Холодильник", list(range(24)), 1),
        ("Освітлення (кімната)", [18, 19, 20, 21, 22], 5),
        ("Пральна машина", [10, 11], 1),
    ]
    for name, sched, qty in starter_appliances:
        cat = catalog_by_name.get(name)
        if cat:
            db.add(HouseAppliance(
                house_id=house.id,
                catalog_id=cat.id,
                quantity=qty,
                schedule=json.dumps(sched),
                enabled=True,
            ))
    db.commit()


def cleanup_expired_guests(db, max_idle_seconds: int = 7200) -> int:
    """
    Видаляє застарілі гостьові акаунти (разом із їхніми будинками та приладами),
    якщо браузер користувача аварійно закрився без сигналу виходу.
    """
    cutoff = int(time.time()) - max_idle_seconds
    stale_guests = (
        db.query(User)
        .filter(
            User.is_guest.is_(True),
            ((User.last_seen_at > 0) & (User.last_seen_at < cutoff))
            | ((User.last_seen_at == 0) & (User.created_at > 0) & (User.created_at < cutoff)),
        )
        .all()
    )
    if not stale_guests:
        return 0
    for guest in stale_guests:
        db.delete(guest)
    db.commit()
    return len(stale_guests)
