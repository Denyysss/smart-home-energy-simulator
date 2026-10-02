from typing import Any


def _get(item: Any, field: str, default: Any = None) -> Any:
    """Значення зі словника або атрибут Pydantic-схеми/ORM-об'єкта."""
    if isinstance(item, dict):
        return item.get(field, default)
    return getattr(item, field, default)


def _duty(work: Any, cycle: Any) -> float:
    """Частка часу роботи в циклі (холодильник: 15 із 40 хв = 0.375)."""
    if work and cycle and work > 0 and cycle > 0:
        return min(1.0, work / cycle)
    return 1.0


def is_night_hour(hour: int, night_start: int, night_end: int) -> bool:
    """Нічна зона; підтримує перехід через північ (23 -> 7)."""
    if night_start > night_end:
        return hour >= night_start or hour < night_end
    return night_start <= hour < night_end


def calculate_hourly_balance(appliances: list, grid_limit_watts: float) -> list[dict]:
    """
    Погодинний розрахунок за добу (години 0-23).

    total_consumption_watts - пікова потужність (прилади, що увімкнені в цю годину);
    energy_kwh - фактична енергія з урахуванням циклів роботи;
    is_overloaded - пікова потужність перевищує ліміт автомата.
    """
    result: list[dict] = []
    for hour in range(24):
        total_watts = 0.0
        energy_kwh = 0.0
        for a in appliances:
            if hour in (_get(a, "active_hours", []) or []):
                power = float(_get(a, "power_watts", 0)) * int(_get(a, "quantity", 1) or 1)
                total_watts += power
                energy_kwh += power * _duty(_get(a, "work_minutes"), _get(a, "cycle_minutes")) / 1000
        result.append(
            {
                "hour": hour,
                "total_consumption_watts": total_watts,
                "energy_kwh": round(energy_kwh, 3),
                "is_overloaded": total_watts > grid_limit_watts,
            }
        )
    return result


def calculate_day(
    appliances: list,
    grid_limit_watts: float,
    price_day: float,
    price_night: float,
    night_start: int,
    night_end: int,
) -> dict:
    """Добовий підсумок: години, перевантаження, вартість за двозонним тарифом."""
    hours = calculate_hourly_balance(appliances, grid_limit_watts)
    cost = kwh_day = kwh_night = 0.0

    for item in hours:
        night = is_night_hour(item["hour"], night_start, night_end)
        item["is_night"] = night
        if night:
            kwh_night += item["energy_kwh"]
            cost += item["energy_kwh"] * price_night
        else:
            kwh_day += item["energy_kwh"]
            cost += item["energy_kwh"] * price_day

    return {
        "limit_watts": grid_limit_watts,
        "hours": hours,
        "overloaded_hours": [h["hour"] for h in hours if h["is_overloaded"]],
        "cost": round(cost, 2),
        "kwh_day": round(kwh_day, 3),
        "kwh_night": round(kwh_night, 3),
    }