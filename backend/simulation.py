"""
simulation.py — Математичне ядро симуляції та оптимізації.

ЧИСТІ ФУНКЦІЇ: не залежать від бази чи FastAPI.
  - simulate()          — розрахунок погодинного споживання, вартості, перевантажень та резерву на час блекауту
  - optimize_schedule() — автоматичне перенесення гнучких приладів на нічний тариф без перевантажень і блекаутів
"""

from dataclasses import dataclass, field


# ============================================================
# Тарифи за замовчуванням (двозонний лічильник)
# ============================================================
# Нічний тариф: 23:00–07:00 (години 23, 0, 1, 2, 3, 4, 5, 6)
# Денний тариф: 07:00–23:00 (години 7, 8, ..., 22)
DAY_RATE   = 4.32   # грн/кВт·год
NIGHT_RATE = 2.16   # грн/кВт·год (50% від денного)

NIGHT_HOURS = {23, 0, 1, 2, 3, 4, 5, 6}

# Тризонні коефіцієнти (відносно базового денного тарифу):
# Пік (08:00-11:00, 20:00-22:00) -> 1.5
# Напівпік (07:00-08:00, 11:00-20:00, 22:00-23:00) -> 1.0
# Ніч (23:00-07:00) -> 0.4
PEAK_HOURS = {8, 9, 10, 20, 21}


@dataclass
class ApplianceInput:
    """Один прилад для симуляції."""
    name: str
    power_watt: float
    schedule: list[int]       # Години [0..23]
    quantity: int = 1
    is_cyclic: bool = False
    duty_cycle: float = 1.0


@dataclass
class SimulationResult:
    """Результат симуляції на 24 години."""
    hourly_watt: list[float]
    hourly_kwh: list[float]
    hourly_cost: list[float]
    total_kwh: float
    total_cost: float
    peak_watt: float
    peak_hour: int
    overload_hours: list[dict]
    per_appliance: list[dict]
    outage_hours: list[int] = field(default_factory=list)
    outage_backup: dict = field(default_factory=dict)
    tariff_comparison: dict = field(default_factory=dict)


def _recommend_station_model(wh: float, inverter_w: float) -> str:
    """Підбирає рекомендований клас зарядної станції за ємністю (Вт·год) та потужністю (Вт)."""
    if wh <= 0:
        return "Резервне живлення не потрібне (0 Вт·год у години відключень)"
    if wh <= 500 and inverter_w <= 600:
        return "EcoFlow RIVER 2 Max (512 Вт·год / 500 Вт)"
    if wh <= 800 and inverter_w <= 800:
        return "EcoFlow RIVER 2 Pro (768 Вт·год / 800 Вт)"
    if wh <= 1100 and inverter_w <= 1800:
        return "EcoFlow DELTA 2 (1024 Вт·год / 1800 Вт)"
    if wh <= 2100 and inverter_w <= 2400:
        return "EcoFlow DELTA 2 Max (2048 Вт·год / 2400 Вт)"
    if wh <= 3600 and inverter_w <= 3600:
        return "EcoFlow DELTA Pro (3600 Вт·год / 3600 Вт)"
    return f"Гібридний інвертор 5 кВт + LiFePO4 акумулятор {max(5, round(wh / 1000, 1))} кВт·год"


def simulate(
    appliances: list[ApplianceInput],
    max_power_watt: float = 5000.0,
    day_rate: float = DAY_RATE,
    night_rate: float = NIGHT_RATE,
    outage_hours: list[int] | None = None,
) -> SimulationResult:
    """
    Головна функція симуляції на 24 години.
    Враховує циклічність приладів, двозонний/тризонний тарифи, ліміт автомата
    та графік погодинних відключень світла (ГПВ).
    """
    if max_power_watt <= 0:
        raise ValueError("max_power_watt must be > 0")
    if day_rate < 0 or night_rate < 0:
        raise ValueError("Tariff rates must be >= 0")

    outage_set: set[int] = set()
    if outage_hours:
        for oh in outage_hours:
            if isinstance(oh, bool) or not isinstance(oh, int) or not (0 <= oh <= 23):
                raise ValueError(f"Invalid outage hour {oh!r}; must be 0..23")
            outage_set.add(oh)

    hourly_watt = [0.0] * 24
    outage_hourly_watt = [0.0] * 24
    per_appliance = []
    affected_appliances = []

    for appliance in appliances:
        if appliance.power_watt <= 0:
            raise ValueError(f"power_watt must be > 0 for {appliance.name}")
        if appliance.quantity < 1:
            raise ValueError(f"quantity must be >= 1 for {appliance.name}")
        if not (0.0 <= appliance.duty_cycle <= 1.0):
            raise ValueError(f"duty_cycle must be in [0.0, 1.0] for {appliance.name}")

        appliance_hourly = [0.0] * 24
        effective_power = appliance.power_watt * appliance.duty_cycle * appliance.quantity
        blocked_hrs = []

        for hour in sorted(set(appliance.schedule)):
            if not isinstance(hour, int) or isinstance(hour, bool) or not (0 <= hour <= 23):
                raise ValueError(f"Invalid hour {hour!r} in schedule for {appliance.name}; must be 0..23")
            if hour in outage_set:
                blocked_hrs.append(hour)
                outage_hourly_watt[hour] += effective_power
                continue
            appliance_hourly[hour] = effective_power
            hourly_watt[hour] += effective_power

        if blocked_hrs:
            affected_appliances.append({
                "name": appliance.name,
                "blocked_hours": blocked_hrs,
                "needed_wh": round(effective_power * len(blocked_hrs), 1),
                "effective_power": round(effective_power, 1),
            })

        per_appliance.append({
            "name": appliance.name,
            "power_watt": appliance.power_watt,
            "effective_power": round(effective_power, 1),
            "hourly_watt": [round(x, 1) for x in appliance_hourly],
        })

    hourly_kwh = [w / 1000.0 for w in hourly_watt]

    # Вартість за поточним двозонним тарифом
    hourly_cost = []
    cost_1zone = 0.0
    cost_2zone = 0.0
    cost_3zone = 0.0

    for hour in range(24):
        kwh = hourly_kwh[hour]
        rate = night_rate if hour in NIGHT_HOURS else day_rate
        c = kwh * rate
        hourly_cost.append(round(c, 4))

        cost_1zone += kwh * day_rate
        cost_2zone += c
        if hour in NIGHT_HOURS:
            cost_3zone += kwh * (day_rate * 0.4)
        elif hour in PEAK_HOURS:
            cost_3zone += kwh * (day_rate * 1.5)
        else:
            cost_3zone += kwh * day_rate

    total_kwh = round(sum(hourly_kwh), 3)
    total_cost = round(sum(hourly_cost), 2)

    peak_watt = max(hourly_watt)
    peak_hour = hourly_watt.index(peak_watt)

    overload_hours = []
    for hour in range(24):
        if hourly_watt[hour] > max_power_watt:
            overload_hours.append({
                "hour": hour,
                "total_watt": round(hourly_watt[hour], 1),
                "excess_watt": round(hourly_watt[hour] - max_power_watt, 1),
            })

    # Розрахунок резервного живлення (EcoFlow / акумулятор) на час відключень
    unserved_wh = sum(outage_hourly_watt)
    peak_outage_w = max(outage_hourly_watt) if outage_hourly_watt else 0.0
    # Додаємо 20% запасу ємності (ККД інвертора + глибина розряду LiFePO4) та 25% запасу інвертора
    rec_wh = round(unserved_wh * 1.2)
    rec_inv_w = round(peak_outage_w * 1.25)

    outage_backup = {
        "outage_hours_count": len(outage_set),
        "unserved_kwh": round(unserved_wh / 1000.0, 3),
        "unserved_wh": round(unserved_wh, 1),
        "peak_outage_watt": round(peak_outage_w, 1),
        "recommended_wh": rec_wh,
        "recommended_inverter_watt": rec_inv_w,
        "recommended_model": _recommend_station_model(rec_wh, rec_inv_w),
        "affected_appliances": affected_appliances,
    }

    # Калькулятор окупності лічильників та СЕС
    meter_price_uah = 2000.0
    monthly_1zone = round(cost_1zone * 30, 2)
    monthly_2zone = round(cost_2zone * 30, 2)
    monthly_3zone = round(cost_3zone * 30, 2)
    monthly_save_2z = round(max(0.0, monthly_1zone - monthly_2zone), 2)
    monthly_save_3z = round(max(0.0, monthly_1zone - monthly_3zone), 2)
    payback_months_2z = round(meter_price_uah / monthly_save_2z, 1) if monthly_save_2z > 1 else None

    # Оцінка балконної/дахової сонячної панелі 1.5 кВт (генерація 08:00..17:00)
    solar_curve = {8: 0.25, 9: 0.55, 10: 0.85, 11: 1.1, 12: 1.25, 13: 1.25, 14: 1.1, 15: 0.8, 16: 0.45, 17: 0.15}
    solar_covered_kwh = sum(min(hourly_kwh[h], gen) for h, gen in solar_curve.items())
    solar_monthly_save_uah = round(solar_covered_kwh * day_rate * 30, 2)

    tariff_comparison = {
        "daily_1zone": round(cost_1zone, 2),
        "daily_2zone": round(cost_2zone, 2),
        "daily_3zone": round(cost_3zone, 2),
        "monthly_1zone": monthly_1zone,
        "monthly_2zone": monthly_2zone,
        "monthly_3zone": monthly_3zone,
        "monthly_save_2zone": monthly_save_2z,
        "monthly_save_3zone": monthly_save_3z,
        "meter_cost_uah": meter_price_uah,
        "payback_months_2zone": payback_months_2z,
        "solar_1_5kw_daily_covered_kwh": round(solar_covered_kwh, 2),
        "solar_1_5kw_monthly_save_uah": solar_monthly_save_uah,
    }

    hourly_watt = [round(w, 1) for w in hourly_watt]
    hourly_kwh = [round(k, 4) for k in hourly_kwh]

    return SimulationResult(
        hourly_watt=hourly_watt,
        hourly_kwh=hourly_kwh,
        hourly_cost=hourly_cost,
        total_kwh=total_kwh,
        total_cost=total_cost,
        peak_watt=round(peak_watt, 1),
        peak_hour=peak_hour,
        overload_hours=overload_hours,
        per_appliance=per_appliance,
        outage_hours=sorted(outage_set),
        outage_backup=outage_backup,
        tariff_comparison=tariff_comparison,
    )


SHIFTABLE_KEYWORDS = (
    "бойлер", "водонагрів", "пральн", "посудомий",
    "сушильн", "зарядк", "електромобіл", "авто", "обігрів", "акумулятор"
)


def is_shiftable(name: str) -> bool:
    """Чи можна переносити прилад на нічну зону (23:00–07:00) без втрати комфорту."""
    low = name.lower()
    return any(k in low for k in SHIFTABLE_KEYWORDS)


def optimize_schedule(
    appliances: list[dict],
    max_power_watt: float = 3680.0,
    day_rate: float = DAY_RATE,
    night_rate: float = NIGHT_RATE,
    outage_hours: list[int] | None = None,
) -> dict:
    """
    Розумний оптимізатор розкладу приладів (POST /api/optimize):
      1. Бере всі «гнучкі» потужні прилади (бойлер, пральна машина, посудомийка, зарядка авто тощо)
         і автоматично переносить їхні години роботи на нічну зону (23:00–07:00) так,
         щоб у жодну годину не перевищити ліміт автомата max_power_watt.
      2. Якщо серед денних негнучких приладів є перевантаження (наприклад, два потужні прилади
         увімкнені одночасно), розводить їх на сусідні вільні денні години без перевантаження.
    """
    outage_set = set(outage_hours or [])
    available_hours = [h for h in range(24) if h not in outage_set]
    if not available_hours:
        available_hours = list(range(24))

    before_inputs = [
        ApplianceInput(
            name=a["name"],
            power_watt=a["power_watt"],
            schedule=a["schedule"],
            quantity=a.get("quantity", 1),
            is_cyclic=a.get("is_cyclic", False),
            duty_cycle=a.get("duty_cycle", 1.0),
        )
        for a in appliances
    ]
    before_sim = simulate(before_inputs, max_power_watt, day_rate, night_rate, outage_hours=list(outage_set))

    hourly_load = [0.0] * 24
    optimized_list = []
    flexible_indices = []
    daytime_adjustable = []

    for idx, a in enumerate(appliances):
        sched = sorted(set(a["schedule"]))
        eff_power = a["power_watt"] * a.get("duty_cycle", 1.0) * a.get("quantity", 1)

        is_all_day = len(sched) >= 20
        if not is_all_day and is_shiftable(a["name"]):
            flexible_indices.append((idx, eff_power, len(sched)))
            optimized_list.append({**a, "new_schedule": []})
        else:
            valid_sched = [h for h in sched if h not in outage_set] or sched
            for h in valid_sched:
                hourly_load[h] += eff_power
            optimized_list.append({**a, "new_schedule": list(valid_sched)})
            if not is_all_day and eff_power >= 500:
                daytime_adjustable.append((idx, eff_power))

    # Якщо серед фіксованих денних приладів виникло перевантаження — розводимо пікові години
    daytime_adjustable.sort(key=lambda x: x[1], reverse=True)
    for idx, eff_power in daytime_adjustable:
        sched = optimized_list[idx]["new_schedule"]
        new_sched = []
        for h in sched:
            if hourly_load[h] > max_power_watt:
                # Шукаємо найближчу годину в тій самій тарифній зоні, де немає перевантаження
                candidates = [
                    ch for ch in available_hours
                    if ch not in sched and ch not in new_sched and (hourly_load[ch] + eff_power <= max_power_watt)
                ]
                if candidates:
                    best_h = min(candidates, key=lambda ch: (abs(ch - h), hourly_load[ch]))
                    hourly_load[h] -= eff_power
                    hourly_load[best_h] += eff_power
                    new_sched.append(best_h)
                    continue
            new_sched.append(h)
        optimized_list[idx]["new_schedule"] = sorted(set(new_sched))

    # Розподіляємо гнучкі прилади (бойлер, пральна машина, посудомийка, зарядка авто)
    # у нічну зону (23:00–07:00) так, щоб не перевищити ліміт автомата
    flexible_indices.sort(key=lambda x: x[1], reverse=True)

    for idx, eff_power, duration in flexible_indices:
        need = min(duration, len(available_hours))
        orig_set = set(appliances[idx]["schedule"])

        scored_hours = sorted(
            available_hours,
            key=lambda h: (
                0 if (hourly_load[h] + eff_power <= max_power_watt) else 1,
                0 if h in NIGHT_HOURS else 1,
                night_rate if h in NIGHT_HOURS else day_rate,
                hourly_load[h],
                0 if h in orig_set else 1,
                h,
            ),
        )
        chosen = sorted(scored_hours[:need])
        for h in chosen:
            hourly_load[h] += eff_power
        optimized_list[idx]["new_schedule"] = chosen

    after_inputs = [
        ApplianceInput(
            name=a["name"],
            power_watt=a["power_watt"],
            schedule=a["new_schedule"],
            quantity=a.get("quantity", 1),
            is_cyclic=a.get("is_cyclic", False),
            duty_cycle=a.get("duty_cycle", 1.0),
        )
        for a in optimized_list
    ]
    after_sim = simulate(after_inputs, max_power_watt, day_rate, night_rate, outage_hours=list(outage_set))

    changes = []
    for orig, opt in zip(appliances, optimized_list):
        old_s = sorted(set(orig["schedule"]))
        new_s = sorted(set(opt["new_schedule"]))
        changes.append({
            "id": orig.get("id"),
            "name": orig["name"],
            "old_schedule": old_s,
            "new_schedule": new_s,
            "changed": old_s != new_s,
            "shiftable": is_shiftable(orig["name"]),
        })

    saved_daily = round(max(0.0, before_sim.total_cost - after_sim.total_cost), 2)
    return {
        "before": {
            "total_kwh": before_sim.total_kwh,
            "total_cost": before_sim.total_cost,
            "peak_watt": before_sim.peak_watt,
            "overload_count": len(before_sim.overload_hours),
        },
        "after": {
            "total_kwh": after_sim.total_kwh,
            "total_cost": after_sim.total_cost,
            "peak_watt": after_sim.peak_watt,
            "overload_count": len(after_sim.overload_hours),
        },
        "saved_daily_uah": saved_daily,
        "saved_monthly_uah": round(saved_daily * 30, 2),
        "changes": changes,
    }
