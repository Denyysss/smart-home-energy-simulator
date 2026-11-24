"""
gpv_service.py — Сервіс адрес України (КАТОТТГ + вулиці OpenStreetMap) та живої синхронізації
фактичних графіків погодинних відключень (ГПВ) АТ «Вінницяобленерго» (e-svitlo.com.ua) і регіонів України.

Головний принцип:
  Використовується ВИКЛЮЧНО фактично затверджений оперативний графік (`fact`), який оновлюється
  кожні 10 хвилин. Якщо обленерго о 14:01 скасовує планове відключення 14:00–16:00, статус слота
  стає "yes" і система автоматично знімає відключення у будинку користувача.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import threading
import time
import urllib.parse
import urllib.request

try:
    from zoneinfo import ZoneInfo
    KYIV_TZ = ZoneInfo("Europe/Kyiv")
except Exception:
    # Надійний fallback на випадок відсутності tzdata у мінімальному середовищі Windows
    KYIV_TZ = timezone(timedelta(hours=2))


KATOTTG_PATH = Path(__file__).resolve().parent / "ua_katottg.json.gz"

VOE_LIVE_JSON_URL = (
    "https://raw.githubusercontent.com/vn-progr/gpv-voe-vinnytsia/main/data/Vinnytsiaoblenerho.json"
)
SVITLOBOT_BASE_URL = "https://raw.githubusercontent.com/Svitlobot-official/timetables/main"

# Відповідність областей КАТОТТГ до кодів живих графіків обленерго
OBLAST_TO_GPV_REGION: dict[str, tuple[str, str]] = {
    "Вінницька": ("vinnytsia", "АТ «Вінницяобленерго» (e-svitlo)"),
    "м. Київ": ("kyiv", "ДТЕК Київські електромережі"),
    "Київська": ("kyiv_oblast", "ДТЕК Київські регіональні електромережі"),
    "Львівська": ("lviv", "ПрАТ «Львівобленерго»"),
    "Хмельницька": ("khmelnytskyi", "АТ «Хмельницькобленерго»"),
    "Житомирська": ("zhytomyr", "АТ «Житомиробленерго»"),
    "Тернопільська": ("ternopil", "ВАТ «Тернопільобленерго»"),
    "Івано-Франківська": ("ivano_frankivsk", "АТ «Прикарпаттяобленерго»"),
    "Рівненська": ("rivne", "ПрАТ «Рівнеобленерго»"),
    "Черкаська": ("cherkasy", "ПАТ «Черкасиобленерго»"),
    "Чернігівська": ("chernihiv", "АТ «Чернігівобленерго»"),
    "Одеська": ("odesa", "ДТЕК Одеські електромережі"),
    "Дніпропетровська": ("dnipro_dtek", "ДТЕК Дніпровські електромережі"),
    "Полтавська": ("poltava", "АТ «Полтаваобленерго»"),
    "Закарпатська": ("zakarpattia", "ПрАТ «Закарпаттяобленерго»"),
    "Запорізька": ("zaporizhzhia", "ПАТ «Запоріжжяобленерго»"),
    "Харківська": ("kharkiv", "АТ «Харківобленерго»"),
}

STANDARD_12_QUEUES = [
    "1.1", "1.2", "2.1", "2.2", "3.1", "3.2",
    "4.1", "4.2", "5.1", "5.2", "6.1", "6.2",
]

# Орієнтовні підказки ліній для районів м. Вінниці (як додатковий орієнтир при першому виборі)
VINNYTSIA_STREET_HINTS: list[tuple[tuple[str, ...], str, list[str]]] = [
    (("келецьк", "вишеньк", "космонавт", "600-річчя", "пирогов", "юності", "політехніч", "стельмах"), "м. Вінниця (Вишенька)", ["1.1", "2.1", "3.1", "4.2"]),
    (("хмельницьк", "внту", "воїн", "магістрат", "першотравнев", "лісопарк", "маккаве"), "м. Вінниця (Хмельницьке ш. / ВНТУ)", ["2.2", "3.2", "5.1"]),
    (("замост", "коцюбинськ", "київськ", "стрілецьк", "перемог", "батозьк", "привокзальн", "немирівськ"), "м. Вінниця (Замостя / Тяжилів)", ["1.2", "4.1", "5.2", "6.1"]),
    (("соборн", "театральн", "оводова", "грушевськ", "артинов", "князів коріатовичів"), "м. Вінниця (Центр)", ["2.1", "4.1", "6.2"]),
    (("збаразьк", "поділля", "зодчих", "бортнянськ", "гончар"), "м. Вінниця (Поділля)", ["1.1", "3.1", "5.1"]),
    (("пирогово", "сабарів", "гніванськ", "аграрн"), "м. Вінниця (Аграрний / Сабарів)", ["3.2", "5.2", "6.2"]),
]

FALLBACK_STREETS = [
    "вул. Центральна",
    "вул. Соборна",
    "вул. Шевченка",
    "вул. Незалежності",
    "вул. Богдана Хмельницького",
    "вул. Лесі Українки",
    "вул. Миру",
    "вул. Молодіжна",
    "вул. Садова",
    "вул. Набережна",
    "вул. Захисників України",
    "вул. Вишнева",
]

_KATOTTG_TREE: dict[str, dict[str, list[str]]] | None = None
_KATOTTG_LOCK = threading.Lock()

_SCHEDULE_CACHE: dict[str, tuple[float, dict]] = {}
_SCHEDULE_LOCK = threading.Lock()
SCHEDULE_CACHE_TTL_SEC = 120  # 2 хв кеш у RAM для захисту від 100+ одночасних запитів

_STREET_CACHE: dict[str, tuple[float, list[str]]] = {}
_NOMINATIM_LOCK = threading.Lock()
_LAST_NOMINATIM_TS = 0.0


def kyiv_now() -> datetime:
    """Поточний час за Києвом."""
    return datetime.now(KYIV_TZ)


def load_katottg() -> dict[str, dict[str, list[str]]]:
    """Завантажує стиснуте дерево КАТОТТГ (Область -> Район -> Села/Міста) у пам'ять."""
    global _KATOTTG_TREE
    if _KATOTTG_TREE is not None:
        return _KATOTTG_TREE
    with _KATOTTG_LOCK:
        if _KATOTTG_TREE is None:
            if KATOTTG_PATH.exists():
                with gzip.open(KATOTTG_PATH, "rt", encoding="utf-8") as f:
                    _KATOTTG_TREE = json.load(f)
            else:
                _KATOTTG_TREE = {
                    "Вінницька": {
                        "Вінницький": ["м. Вінниця", "м. Гнівань", "м. Іллінці", "с. Агрономічне", "с. Якушинці", "с-ще Стрижавка"],
                        "Гайсинський": ["м. Гайсин", "м. Ладижин", "м. Бершадь"],
                        "Жмеринський": ["м. Жмеринка", "м. Бар", "м. Шаргород"],
                        "Могилів-Подільський": ["м. Могилів-Подільський", "м. Ямпіль"],
                        "Тульчинський": ["м. Тульчин", "с-ще Крижопіль", "с-ще Вапнярка"],
                        "Хмільницький": ["м. Хмільник", "м. Калинівка", "м. Козятин"],
                    }
                }
    return _KATOTTG_TREE


def list_oblasts() -> list[dict]:
    tree = load_katottg()
    result = []
    for ob in tree.keys():
        reg_info = OBLAST_TO_GPV_REGION.get(ob)
        result.append({
            "name": ob,
            "gpv_region": reg_info[0] if reg_info else "vinnytsia",
            "provider": reg_info[1] if reg_info else "Обленерго регіону",
            "has_live_gpv": reg_info is not None,
            "raions_count": len(tree[ob]),
        })
    return result


def list_raions(oblast: str) -> list[str]:
    tree = load_katottg()
    return list(tree.get(oblast, {}).keys())


def list_settlements(oblast: str, raion: str, q: str = "", limit: int = 500) -> list[str]:
    tree = load_katottg()
    items = tree.get(oblast, {}).get(raion, [])
    query = q.strip().lower()
    if query:
        items = [s for s in items if query in s.lower()]
    return items[:limit]


def clean_settlement_name(settlement: str) -> str:
    """Прибирає префікси 'с. ', 'м. ', 'с-ще ' та уточнення громади в дужках для геокодера."""
    s = settlement.strip()
    if "(" in s:
        s = s.split("(", 1)[0].strip()
    for pref in ("м. ", "с-ще ", "с. ", "смт "):
        if s.startswith(pref):
            s = s[len(pref):].strip()
            break
    return s


def search_streets_for_settlement(oblast: str, raion: str, settlement: str, q: str = "") -> list[str]:
    """
    Повертає реальні вулиці вибраного села/міста з OpenStreetMap (з кешуванням та fallback-списком).
    """
    clean_place = clean_settlement_name(settlement)
    q_norm = q.strip()
    cache_key = f"{oblast}|{raion}|{clean_place}|{q_norm.lower()}"

    now = time.time()
    cached = _STREET_CACHE.get(cache_key)
    if cached and (now - cached[0] < 3600):
        return cached[1]

    streets: list[str] = []
    search_term = q_norm if len(q_norm) >= 2 else "вулиця"
    oblast_label = oblast if "Київ" in oblast else f"{oblast} область"
    full_query = f"{search_term}, {clean_place}, {oblast_label}, Україна"

    global _LAST_NOMINATIM_TS
    try:
        with _NOMINATIM_LOCK:
            wait = 1.0 - (time.time() - _LAST_NOMINATIM_TS)
            if 0 < wait <= 1.0:
                time.sleep(wait)
            url = (
                "https://nominatim.openstreetmap.org/search?"
                + urllib.parse.urlencode({
                    "q": full_query,
                    "format": "jsonv2",
                    "addressdetails": "1",
                    "countrycodes": "ua",
                    "limit": "15",
                    "accept-language": "uk",
                })
            )
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "EnergyDimVinnytsiaStudentProject/1.2"},
            )
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            _LAST_NOMINATIM_TS = time.time()

        seen: set[str] = set()
        for item in data:
            addr = item.get("address") or {}
            road = addr.get("road") or item.get("name") or ""
            road = road.strip()
            if not road or road.lower() == clean_place.lower():
                continue
            low = road.lower()
            if any(w in low for w in ("область", "район", "громада")):
                continue
            if low not in seen:
                seen.add(low)
                streets.append(road)
    except Exception:
        streets = []

    if not streets:
        if q_norm:
            streets = [s for s in FALLBACK_STREETS if q_norm.lower() in s.lower()]
        else:
            streets = list(FALLBACK_STREETS)

    if len(_STREET_CACHE) > 800:
        _STREET_CACHE.clear()
    _STREET_CACHE[cache_key] = (time.time(), streets)
    return streets


def normalize_location_keys(oblast: str, raion: str, settlement: str, street: str = "") -> tuple[str, str]:
    """Формує точний ключ (до вулиці) та загальний ключ населеного пункту для пам'яті черг у БД."""
    ob = oblast.strip().lower()
    ra = raion.strip().lower()
    se = settlement.strip().lower()
    st = street.strip().lower()
    for word in ("вулиця", "вул.", "вул ", "провулок", "пров.", "проспект", "просп."):
        st = st.replace(word, "")
    st = " ".join(st.split())
    settlement_key = f"{ob}|{ra}|{se}"
    street_key = f"{settlement_key}|{st}" if st else settlement_key
    return street_key, settlement_key


def _parse_voe_json(raw_data: dict) -> dict:
    """
    Розбирає живий JSON АТ «Вінницяобленерго» (e-svitlo.com.ua), беручи ВИКЛЮЧНО `fact`.
    Коди слотів:
      "yes"    -> світло 100% є
      "no"     -> 100% підтверджене відключення всю годину
      "second" -> відключення перші 30 хв (:00–:30)
      "first"  -> відключення другі 30 хв (:30–:00)
    """
    fact = raw_data.get("fact") or {}
    fact_data = fact.get("data") or {}
    if not fact_data:
        raise ValueError("Порожні дані fact Вінницяобленерго")

    now_kyiv = kyiv_now()
    today_midnight = now_kyiv.replace(hour=0, minute=0, second=0, microsecond=0)
    today_ts = str(int(today_midnight.timestamp()))

    day_keys = sorted(fact_data.keys(), key=lambda k: int(k))
    chosen_key = today_ts if today_ts in fact_data else (str(fact.get("today")) if str(fact.get("today")) in fact_data else day_keys[0])
    tomorrow_keys = [k for k in day_keys if int(k) > int(chosen_key)]
    tomorrow_key = tomorrow_keys[0] if tomorrow_keys else None

    def extract_day(d_map: dict) -> dict[str, list[str]]:
        queues_out: dict[str, list[str]] = {}
        for q_raw, hours_map in d_map.items():
            q_code = q_raw.replace("GPV", "").strip()
            slots_24: list[str] = []
            for hr_1based in range(1, 25):
                val = str(hours_map.get(str(hr_1based), "yes")).lower()
                if val in ("no", "first", "second"):
                    slots_24.append(val)
                else:
                    # Будь-які інші / непідтверджені стани трактуємо як "світло є" (без хибних тривог!)
                    slots_24.append("yes")
            queues_out[q_code] = slots_24
        return queues_out

    today_queues = extract_day(fact_data[chosen_key])
    tomorrow_queues = extract_day(fact_data[tomorrow_key]) if tomorrow_key else {}

    return {
        "region": "vinnytsia",
        "provider": "АТ «Вінницяобленерго» (e-svitlo.com.ua • fact)",
        "updated_at": fact.get("update") or str(raw_data.get("lastUpdated", "")),
        "queues": today_queues,
        "tomorrow_queues": tomorrow_queues,
    }


def _fetch_svitlobot_region(region: str, queue: str | None = None) -> dict:
    """Резервне та міжобласне отримання фактичних графіків через відкриті дані Світлобота (CC BY 4.0)."""
    target_queues = [queue] if queue else STANDARD_12_QUEUES
    now_str = kyiv_now().strftime("%Y-%m-%d")
    tmr_str = (kyiv_now() + timedelta(days=1)).strftime("%Y-%m-%d")
    code_map = {0: "yes", 1: "no", 2: "second", 3: "first"}

    today_queues: dict[str, list[str]] = {}
    tomorrow_queues: dict[str, list[str]] = {}
    updated_label = ""

    for q in target_queues:
        url = f"{SVITLOBOT_BASE_URL}/{region}/{q}.json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "EnergyDim/1.2"})
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                d = json.loads(resp.read().decode("utf-8"))
            if not updated_label:
                updated_label = d.get("source_updated_at") or d.get("updated_at") or ""
            days_list = d.get("days") or []
            chosen_day = next((x for x in days_list if x.get("date") == now_str), days_list[-1] if days_list else None)
            if chosen_day and isinstance(chosen_day.get("slots"), list) and len(chosen_day["slots"]) == 24:
                today_queues[q] = [code_map.get(s, "yes") for s in chosen_day["slots"]]
            tmr_day = next((x for x in days_list if x.get("date") == tmr_str), None)
            if tmr_day and isinstance(tmr_day.get("slots"), list) and len(tmr_day["slots"]) == 24:
                tomorrow_queues[q] = [code_map.get(s, "yes") for s in tmr_day["slots"]]
        except Exception:
            continue

    return {
        "region": region,
        "provider": f"Оперативний графік ГПВ ({region})",
        "updated_at": updated_label,
        "queues": today_queues,
        "tomorrow_queues": tomorrow_queues,
    }


def get_live_region_schedule(region: str = "vinnytsia", force_refresh: bool = False) -> dict:
    """
    Повертає актуальний фактичний графік для регіону (за замовчуванням — Вінницяобленерго).
    Кешує відповідь на 120 секунд для миттєвої роботи при великому навантаженні.
    """
    reg = (region or "vinnytsia").strip().lower()
    now_ts = time.time()

    if not force_refresh:
        cached = _SCHEDULE_CACHE.get(reg)
        if cached and (now_ts - cached[0] < SCHEDULE_CACHE_TTL_SEC):
            return cached[1]

    with _SCHEDULE_LOCK:
        if not force_refresh:
            cached = _SCHEDULE_CACHE.get(reg)
            if cached and (time.time() - cached[0] < SCHEDULE_CACHE_TTL_SEC):
                return cached[1]

        parsed: dict | None = None
        if reg == "vinnytsia":
            try:
                req = urllib.request.Request(VOE_LIVE_JSON_URL, headers={"User-Agent": "EnergyDim/1.2"})
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                parsed = _parse_voe_json(raw)
            except Exception:
                parsed = None

        if not parsed or not parsed.get("queues"):
            parsed = _fetch_svitlobot_region(reg)

        # Якщо зовнішня мережа повністю недоступна (офлайн-режим), віддаємо попередній кеш або чисті слоти
        if not parsed.get("queues"):
            old = _SCHEDULE_CACHE.get(reg)
            if old:
                return old[1]
            parsed = {
                "region": reg,
                "provider": "АТ «Вінницяобленерго» (офлайн-режим)",
                "updated_at": "немає зв'язку з мережею",
                "queues": {q: ["yes"] * 24 for q in STANDARD_12_QUEUES},
                "tomorrow_queues": {},
                "offline": True,
            }

        _SCHEDULE_CACHE[reg] = (time.time(), parsed)
        return parsed


def is_slot_off_at_minute(slot_code: str, minute: int) -> bool:
    """Точна перевірка з урахуванням півгодинних перемикань о :30 хвилині."""
    if slot_code == "no":
        return True
    if slot_code == "second":  # немає перші 30 хв (:00..:29), о :30 вмикають!
        return minute < 30
    if slot_code == "first":   # перші 30 хв світло є (:00..:29), о :30 вимикають!
        return minute >= 30
    return False


def slots_to_outage_hours(slots_24: list[str], now_dt: datetime | None = None) -> list[int]:
    """
    Перетворює 24 фактичні слоти у список годин відключень для симулятора.
    Важливо: якщо зараз поточна година має півгодинний слот, де світло ВЖЕ дали
    (наприклад, "second" і зараз 14:35), поточна година більше НЕ вважається знеструмленою!
    """
    dt = now_dt or kyiv_now()
    cur_h = dt.hour
    cur_m = dt.minute
    hours: list[int] = []
    for h, code in enumerate(slots_24[:24]):
        if code == "no":
            hours.append(h)
        elif code in ("first", "second"):
            if h == cur_h:
                if is_slot_off_at_minute(code, cur_m):
                    hours.append(h)
            else:
                hours.append(h)
    return hours


def format_slots_intervals(slots_24: list[str]) -> list[str]:
    """Форматує фактичні інтервали з точністю до 30 хвилин (напр. '08:00–10:00', '14:30–16:00')."""
    # Розбиваємо добу на 48 півгодинних інтервалів
    half = [False] * 48
    for h, code in enumerate(slots_24[:24]):
        if code == "no":
            half[h * 2] = True
            half[h * 2 + 1] = True
        elif code == "second":
            half[h * 2] = True
        elif code == "first":
            half[h * 2 + 1] = True

    def fmt_idx(idx: int) -> str:
        hh = idx // 2
        mm = "30" if idx % 2 else "00"
        return f"{hh:02d}:{mm}"

    intervals: list[str] = []
    i = 0
    while i < 48:
        if not half[i]:
            i += 1
            continue
        start = i
        while i < 48 and half[i]:
            i += 1
        intervals.append(f"{fmt_idx(start)}–{fmt_idx(i)}")
    return intervals


def compute_queue_live_status(
    region: str,
    queue: str,
    sched_data: dict | None = None,
    now_dt: datetime | None = None,
) -> dict:
    """Формує повний статус живої черги на поточну хвилину (чи є світло, коли увімкнуть/вимкнуть, інтервали)."""
    data = sched_data or get_live_region_schedule(region)
    dt = now_dt or kyiv_now()
    q = queue.replace("GPV", "").strip()
    slots = (data.get("queues") or {}).get(q) or ["yes"] * 24
    tmr_slots = (data.get("tomorrow_queues") or {}).get(q) or ["yes"] * 24

    # 96 півгодинних кроків (сьогодні + завтра) для точного таймера до хвилини
    half_96: list[bool] = []
    for day_slots in (slots, tmr_slots):
        for code in day_slots[:24]:
            half_96.append(code in ("no", "second"))
            half_96.append(code in ("no", "first"))

    cur_half_idx = dt.hour * 2 + (1 if dt.minute >= 30 else 0)
    is_off_now = half_96[cur_half_idx]
    has_power_now = not is_off_now

    next_change_dt: datetime | None = None
    for idx in range(cur_half_idx + 1, len(half_96)):
        if half_96[idx] != is_off_now:
            day_offset = idx // 48
            rem = idx % 48
            hh = rem // 2
            mm = 30 if rem % 2 else 0
            base_day = dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day_offset)
            next_change_dt = base_day.replace(hour=hh, minute=mm)
            break

    if next_change_dt is not None:
        diff_min = max(1, int((next_change_dt - dt).total_seconds() // 60))
        hrs_left, mins_left = divmod(diff_min, 60)
        left_str = f"{hrs_left} год {mins_left} хв" if hrs_left else f"{mins_left} хв"
        time_hm = next_change_dt.strftime("%H:%M")
        day_pref = "завтра о " if next_change_dt.date() > dt.date() else "о "
        if has_power_now:
            next_text = f"Наступне відключення за графіком {day_pref}{time_hm} (через {left_str})"
        else:
            next_text = f"Світло за графіком увімкнуть {day_pref}{time_hm} (залишилось {left_str})"
    else:
        next_text = (
            "За оперативним графіком відключень сьогодні не заплановано"
            if has_power_now
            else "Очікується оновлення оперативного графіка"
        )

    outage_hours = slots_to_outage_hours(slots, dt)
    intervals = format_slots_intervals(slots)

    return {
        "region": data.get("region", region),
        "provider": data.get("provider", "АТ «Вінницяобленерго»"),
        "queue": q,
        "updated_at": data.get("updated_at", ""),
        "kyiv_time": dt.strftime("%H:%M"),
        "has_power_now": has_power_now,
        "current_slot": slots[dt.hour],
        "outage_hours": outage_hours,
        "slots": slots,
        "intervals": intervals,
        "tomorrow_intervals": format_slots_intervals(tmr_slots) if any(x != "yes" for x in tmr_slots) else [],
        "next_change_text": next_text,
    }


def match_queues_for_location(
    oblast: str,
    raion: str,
    settlement: str,
    street: str,
    has_power_now: bool | None,
    remembered_queue: str | None = None,
) -> dict:
    """
    Підбирає черги обленерго для вибраного села/міста/вулиці:
    1) Якщо адреса вже є в пам'яті БД — підставляє збережену чергу як основну.
    2) Враховує районні підказки м. Вінниці.
    3) Якщо користувач відповів «Чи є зараз світло? (Так/Ні)» — миттєво відсіює всі черги,
       які суперечать фактичному стану мережі в цю хвилину!
    """
    reg_info = OBLAST_TO_GPV_REGION.get(oblast, ("vinnytsia", "АТ «Вінницяобленерго»"))
    gpv_region = reg_info[0]
    sched = get_live_region_schedule(gpv_region)
    dt = kyiv_now()

    all_queues = sorted((sched.get("queues") or {}).keys()) or list(STANDARD_12_QUEUES)
    statuses = {q: compute_queue_live_status(gpv_region, q, sched, dt) for q in all_queues}

    hint_label = ""
    hint_queues: list[str] = []
    low_check = f"{settlement} {street}".lower()
    if oblast == "Вінницька" and "вінниц" in settlement.lower():
        for keys, label, qs in VINNYTSIA_STREET_HINTS:
            if any(k in low_check for k in keys):
                hint_label = label
                hint_queues = [q for q in qs if q in statuses]
                break

    if has_power_now is not None:
        matching_state = [q for q in all_queues if statuses[q]["has_power_now"] == has_power_now]
    else:
        matching_state = list(all_queues)

    recommended = None
    if remembered_queue and remembered_queue in statuses:
        if has_power_now is None or statuses[remembered_queue]["has_power_now"] == has_power_now:
            recommended = remembered_queue

    if not recommended and hint_queues:
        hint_and_state = [q for q in hint_queues if q in matching_state]
        if hint_and_state:
            recommended = hint_and_state[0]

    if not recommended:
        recommended = matching_state[0] if matching_state else all_queues[0]

    return {
        "gpv_region": gpv_region,
        "provider": sched.get("provider", reg_info[1]),
        "updated_at": sched.get("updated_at", ""),
        "kyiv_time": dt.strftime("%H:%M"),
        "remembered_queue": remembered_queue if remembered_queue in statuses else None,
        "hint_label": hint_label,
        "hint_queues": hint_queues,
        "matching_queues": matching_state,
        "all_queues": all_queues,
        "recommended_queue": recommended,
        "queues_detail": {
            q: {
                "has_power_now": st["has_power_now"],
                "intervals": st["intervals"],
                "outage_hours": st["outage_hours"],
                "next_change_text": st["next_change_text"],
            }
            for q, st in statuses.items()
        },
    }
