import math
from typing import Any, Dict, List

HOURS_IN_DAY = 24
SOLAR_START_HOUR = 8    # сонце починає генерувати з 08:00
SOLAR_END_HOUR = 18     # і закінчує о 18:00
NIGHT_START = 23        # нічний тариф: 23:00 – 07:00
NIGHT_END = 7
NIGHT_MULTIPLIER = 0.5  # вночі вдвічі дешевше
DEFAULT_TARIFF = 4.32   # грн/кВт·год (денний)


def _solar_power_at(hour: int, peak_power_w: float) -> float:
    """
    Потужність сонячних панелей (Вт) у задану годину за умовною параболою.
    Генерація ненульова для годин 8..18 включно, максимум у центрі (13:00).
    """
    if hour < SOLAR_START_HOUR or hour > SOLAR_END_HOUR:
        return 0.0
    center = (SOLAR_START_HOUR + SOLAR_END_HOUR) / 2      # 13.0
    half_width = (SOLAR_END_HOUR - SOLAR_START_HOUR) / 2  # 5.0
    # парабола: 1 у центрі, 0 на краях
    factor = 1 - ((hour - center) / half_width) ** 2
    return max(0.0, peak_power_w * factor)


def _tariff_at(hour: int, tariff_day: float) -> float:
    """Тариф (грн/кВт·год) у задану годину з урахуванням двозонності."""
    if hour >= NIGHT_START or hour < NIGHT_END:
        return tariff_day * NIGHT_MULTIPLIER
    return tariff_day


def calculate_daily_balance(
    appliances: List[Dict[str, Any]],
    solar_peak_w: float,
    battery_capacity_wh: float,
    initial_charge_wh: float = 0.0,
    tariff_day: float = DEFAULT_TARIFF,
) -> Dict[str, Any]:
    battery_capacity_wh = max(0.0, float(battery_capacity_wh))
    solar_peak_w = max(0.0, float(solar_peak_w))
    charge = min(max(0.0, float(initial_charge_wh)), battery_capacity_wh)

    consumption: List[float] = []
    solar_generation: List[float] = []
    battery_level: List[float] = []
    grid_usage: List[float] = []
    total_cost = 0.0

    for hour in range(HOURS_IN_DAY):
        # 1. Споживання: сума потужностей приладів, що працюють у цю годину
        load = 0.0
        for item in appliances:
            if hour in item.get("hours", []):
                load += float(item.get("power_w", 0.0))

        # 2. Генерація сонця
        solar = _solar_power_at(hour, solar_peak_w)

        grid = 0.0
        balance = solar - load

        if balance >= 0:
            # 3. Надлишок сонця -> заряджаємо акумулятор (те, що не влізло, втрачається)
            charge = min(battery_capacity_wh, charge + balance)
        else:
            # 4. Дефіцит -> спершу акумулятор, потім мережа
            deficit = -balance
            from_battery = min(charge, deficit)
            charge -= from_battery
            grid = deficit - from_battery

        # 5. Вартість: кВт·год * тариф відповідної зони
        total_cost += (grid / 1000.0) * _tariff_at(hour, tariff_day)

        consumption.append(round(load, 2))
        solar_generation.append(round(solar, 2))
        battery_level.append(round(charge, 2))
        grid_usage.append(round(grid, 2))

    return {
        "consumption": consumption,
        "solar_generation": solar_generation,
        "battery_level": battery_level,
        "grid_usage": grid_usage,
        "total_cost": round(total_cost, 2),
    }


if __name__ == "__main__":
    # Швидка перевірка
    demo = calculate_daily_balance(
        appliances=[
            {"name": "Холодильник", "power_w": 150, "hours": list(range(24))},
            {"name": "Чайник", "power_w": 2000, "hours": [7, 19]},
            {"name": "Освітлення", "power_w": 100, "hours": [18, 19, 20, 21, 22]},
        ],
        solar_peak_w=3000,
        battery_capacity_wh=2048,
    )
    print(demo)