"""
test_simulation.py — Автотести (pytest) для математичного ядра та REST API.

Запуск:
    python -m pytest -v
"""

import pytest
from fastapi.testclient import TestClient

from simulation import simulate, ApplianceInput
from main import app


# ============================================================
# 1. ЮНІТ-ТЕСТИ МАТЕМАТИЧНОГО ЯДРА (simulate)
# ============================================================

def test_empty_appliances():
    """Порожній список приладів повертає нульове споживання без перевантажень."""
    res = simulate([], max_power_watt=3680)
    assert res.hourly_watt == [0.0] * 24
    assert res.hourly_kwh == [0.0] * 24
    assert res.hourly_cost == [0.0] * 24
    assert res.total_kwh == 0.0
    assert res.total_cost == 0.0
    assert res.peak_watt == 0.0
    assert res.overload_hours == []
    assert res.per_appliance == []


def test_day_vs_night_tariffs():
    """Перевірка двозонного тарифу: ніч (23..6) та день (7..22)."""
    appliances = [
        ApplianceInput(
            name="Обігрівач",
            power_watt=1000,
            schedule=[2, 12],  # 02:00 — ніч, 12:00 — день
            quantity=1,
            is_cyclic=False,
            duty_cycle=1.0,
        )
    ]
    res = simulate(appliances, max_power_watt=5000, day_rate=4.32, night_rate=2.16)
    assert res.hourly_kwh[2] == 1.0
    assert res.hourly_kwh[12] == 1.0
    assert res.hourly_cost[2] == pytest.approx(2.16)
    assert res.hourly_cost[12] == pytest.approx(4.32)
    assert res.total_kwh == pytest.approx(2.0)
    assert res.total_cost == pytest.approx(6.48)


def test_cyclic_appliance_duty_cycle():
    """Циклічний прилад (холодильник) враховує коефіцієнт duty_cycle."""
    fridge = ApplianceInput(
        name="Холодильник",
        power_watt=150,
        schedule=list(range(24)),
        quantity=1,
        is_cyclic=True,
        duty_cycle=0.35,
    )
    res = simulate([fridge], max_power_watt=3680, day_rate=4.0, night_rate=2.0)
    # 150 * 0.35 = 52.5 Вт щогодини
    assert all(w == pytest.approx(52.5) for w in res.hourly_watt)
    # За 24 години: 52.5 * 24 / 1000 = 1.26 кВт·год
    assert res.total_kwh == pytest.approx(1.26)


def test_quantity_multiplier():
    """Кількість приладів (quantity > 1) пропорційно масштабує споживання."""
    lamps = ApplianceInput(
        name="Освітлення",
        power_watt=100,
        schedule=[18, 19, 20],
        quantity=5,
    )
    res = simulate([lamps], max_power_watt=3680)
    assert res.hourly_watt[18] == pytest.approx(500.0)
    assert res.hourly_watt[19] == pytest.approx(500.0)
    assert res.hourly_watt[20] == pytest.approx(500.0)
    assert res.hourly_watt[17] == 0.0
    assert res.total_kwh == pytest.approx(1.5)


def test_overload_detection():
    """Перевірка фіксації перевантаження мережі, коли сумарна потужність > ліміту."""
    appliances = [
        ApplianceInput(name="Бойлер", power_watt=2000, schedule=[18, 19]),
        ApplianceInput(name="Пральна машина", power_watt=2200, schedule=[19, 20]),
    ]
    res = simulate(appliances, max_power_watt=3680)
    assert res.peak_watt == pytest.approx(4200.0)
    assert res.peak_hour == 19
    assert len(res.overload_hours) == 1
    assert res.overload_hours[0]["hour"] == 19
    assert res.overload_hours[0]["total_watt"] == pytest.approx(4200.0)
    assert res.overload_hours[0]["excess_watt"] == pytest.approx(520.0)


def test_duplicate_hours_deduplicated():
    """Дублікати годин у розкладі не повинні подвоювати споживання."""
    app_in = ApplianceInput(name="Бойлер", power_watt=2000, schedule=[6, 6, 6])
    res = simulate([app_in], max_power_watt=5000)
    assert res.hourly_watt[6] == pytest.approx(2000.0)
    assert res.total_kwh == pytest.approx(2.0)


def test_invalid_inputs_raise_value_error():
    """Некоректні години або параметри мають викликати ValueError."""
    with pytest.raises(ValueError):
        simulate([ApplianceInput(name="Тест", power_watt=100, schedule=[24])])
    with pytest.raises(ValueError):
        simulate([ApplianceInput(name="Тест", power_watt=100, schedule=[-1])])
    with pytest.raises(ValueError):
        simulate([ApplianceInput(name="Тест", power_watt=-50, schedule=[1])])
    with pytest.raises(ValueError):
        simulate([ApplianceInput(name="Тест", power_watt=100, schedule=[1], duty_cycle=1.5)])


# ============================================================
# 2. ІНТЕГРАЦІЙНІ ТЕСТИ REST API (FastAPI + SQLite)
# ============================================================

def test_api_catalog_and_simulation():
    with TestClient(app) as client:
        # 1. Каталог
        r_cat = client.get("/api/catalog")
        assert r_cat.status_code == 200
        catalog = r_cat.json()
        assert len(catalog) >= 10

        # 2. Коректна симуляція
        payload = {
            "appliances": [
                {"name": "Бойлер", "power_watt": 2000, "schedule": [6, 7], "quantity": 1},
            ],
            "max_power_watt": 3680,
            "day_rate": 4.32,
            "night_rate": 2.16,
        }
        r_sim = client.post("/api/simulate", json=payload)
        assert r_sim.status_code == 200
        data = r_sim.json()
        assert data["total_kwh"] == pytest.approx(4.0)
        # 06:00 (ніч 2.16 * 2) + 07:00 (день 4.32 * 2) = 4.32 + 8.64 = 12.96
        assert data["total_cost"] == pytest.approx(12.96)


def test_api_validation_rejects_invalid_hours_and_huge_schedule():
    with TestClient(app) as client:
        # Година 25 за межами 0..23
        bad_hour = {
            "appliances": [{"name": "Тест", "power_watt": 100, "schedule": [25]}],
            "max_power_watt": 3680,
        }
        assert client.post("/api/simulate", json=bad_hour).status_code == 422

        # Занадто великий масив schedule (> 24 елементів)
        huge_schedule = {
            "appliances": [{"name": "Тест", "power_watt": 100, "schedule": list(range(30))}],
            "max_power_watt": 3680,
        }
        assert client.post("/api/simulate", json=huge_schedule).status_code == 422


def test_api_houses_and_appliances_crud():
    with TestClient(app) as client:
        # Створення будинку
        r_create = client.post("/api/houses", json={
            "name": "Тестовий дім",
            "address": "вул. Наукова, 1",
            "voltage": 230,
            "amps": 20,
            "day_rate": 4.32,
            "night_rate": 2.16,
        })
        assert r_create.status_code == 200
        house = r_create.json()
        house_id = house["id"]
        assert house["max_power_watt"] == 4600.0

        # Додавання приладу до будинку
        r_app = client.post(f"/api/houses/{house_id}/appliances", json={
            "catalog_id": 1,
            "custom_name": "Бойлер кухня",
            "custom_power": 1500,
            "quantity": 1,
            "schedule": [6, 7],
            "enabled": True,
        })
        assert r_app.status_code == 200
        app_data = r_app.json()
        app_id = app_data["id"]
        assert app_data["name"] == "Бойлер кухня"
        assert app_data["power"] == 1500

        # Симуляція будинку з БД
        r_hsim = client.get(f"/api/houses/{house_id}/simulate")
        assert r_hsim.status_code == 200
        assert r_hsim.json()["total_kwh"] == pytest.approx(3.0)

        # Оновлення приладу (вимкнення)
        r_upd = client.put(f"/api/houses/{house_id}/appliances/{app_id}", json={"enabled": False})
        assert r_upd.status_code == 200
        assert r_upd.json()["enabled"] is False

        # Після вимкнення споживання = 0
        r_hsim2 = client.get(f"/api/houses/{house_id}/simulate")
        assert r_hsim2.json()["total_kwh"] == 0.0

        # Видалення приладу та будинку
        assert client.delete(f"/api/houses/{house_id}/appliances/{app_id}").status_code == 200
        assert client.delete(f"/api/houses/{house_id}").status_code == 200


def test_smart_optimizer_shifts_to_night_and_eliminates_overload():
    """Перевірка POST /api/optimize: перенесення гнучких приладів на нічний тариф без перевантажень."""
    with TestClient(app) as client:
        payload = {
            "appliances": [
                {"id": 1, "name": "Бойлер 80л", "power_watt": 2000, "schedule": [6, 7, 18, 19, 20], "quantity": 1, "is_cyclic": False, "duty_cycle": 1.0},
                {"id": 2, "name": "Холодильник", "power_watt": 150, "schedule": list(range(24)), "quantity": 1, "is_cyclic": True, "duty_cycle": 0.35},
                {"id": 3, "name": "Освітлення (кімната)", "power_watt": 100, "schedule": [18, 19, 20, 21, 22], "quantity": 5, "is_cyclic": False, "duty_cycle": 1.0},
                {"id": 4, "name": "Пральна машина", "power_watt": 2200, "schedule": [18, 19], "quantity": 1, "is_cyclic": True, "duty_cycle": 0.65},
            ],
            "max_power_watt": 3680,
            "day_rate": 4.32,
            "night_rate": 2.16,
        }
        r = client.post("/api/optimize", json=payload)
        assert r.status_code == 200
        data = r.json()
        # До оптимізації о 18:00 та 19:00 було перевантаження (2000 + 52.5 + 500 + 1430 = 3982.5 Вт > 3680 Вт)
        assert data["before"]["overload_count"] == 2
        # Після оптимізації перевантажень 0 і вартість суттєво менша
        assert data["after"]["overload_count"] == 0
        assert data["after"]["total_cost"] < data["before"]["total_cost"]
        assert data["saved_monthly_uah"] > 500


def test_outage_hours_and_ecoflow_sizing():
    """Перевірка розрахунку відключень (ГПВ), підбору EcoFlow та окупності лічильника/СЕС."""
    appliances = [
        ApplianceInput(name="Холодильник", power_watt=150, schedule=list(range(24)), is_cyclic=True, duty_cycle=0.35),
        ApplianceInput(name="Комп'ютер (ПК)", power_watt=350, schedule=[10, 11, 12, 13]),
        ApplianceInput(name="Бойлер", power_watt=2000, schedule=[6, 7]),
    ]
    res = simulate(appliances, max_power_watt=3680, day_rate=4.32, night_rate=2.16, outage_hours=[10, 11, 12])
    assert res.outage_hours == [10, 11, 12]
    # За 3 години відключення: (52.5 + 350) * 3 = 1207.5 Вт·год (1.208 кВт·год)
    assert res.outage_backup["unserved_kwh"] == pytest.approx(1.208, abs=0.01)
    # Рекомендована ємність із +20% запасу = 1449 Вт·год, інвертор = 503 Вт -> DELTA Pro
    assert res.outage_backup["recommended_wh"] > 1200
    assert res.outage_backup["recommended_inverter_watt"] > 400
    assert "EcoFlow" in res.outage_backup["recommended_model"]
    assert res.hourly_watt[10] == 0.0  # Під час відключення мережеве споживання 0
    assert res.tariff_comparison["payback_months_2zone"] is not None
    assert res.tariff_comparison["solar_1_5kw_monthly_save_uah"] > 0


def test_house_preset_endpoint():
    """Перевірка POST /api/houses/{id}/preset для швидких сценаріїв."""
    with TestClient(app) as client:
        r_house = client.post("/api/houses", json={"name": "Пресет-тест", "address": "вул. Тестова"})
        assert r_house.status_code == 200
        hid = r_house.json()["id"]

        try:
            for preset_name, expected_sub in [("winter", "Обігрівач"), ("summer", "Кондиціонер"), ("eco", "Бойлер")]:
                r_p = client.post(f"/api/houses/{hid}/preset", json={"preset": preset_name})
                assert r_p.status_code == 200
                names = [a["name"] for a in r_p.json()["appliances"]]
                assert any(expected_sub in n for n in names)
        finally:
            assert client.delete(f"/api/houses/{hid}").status_code == 200

