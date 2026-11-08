from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from barber_bot import db
from barber_bot.api import app
from barber_bot.schedule import TZ, create_appointment, find_appointments, list_barbers


@pytest.fixture
def staff_app(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "staff.db")
    with TestClient(app) as api:
        from barber_bot.staff import save_staff_account

        save_staff_account("dono", "Senha exclusiva do dono", "owner")
        save_staff_account("rafael", "Senha exclusiva do Rafael", "barber", 1)
        yield api


def login(api, username, password):
    result = api.post("/staff/login", json={"username": username, "password": password})
    assert result.status_code == 200, result.text
    return {"Authorization": f"Bearer {result.json()['token']}"}


def seed_reservations():
    today = datetime.now(TZ).date()
    common_days = set.intersection(*(set(barber["working_days"]) for barber in list_barbers() if barber["id"] in {1, 2}))
    for offset in range(1, 8):
        day = today + timedelta(days=offset)
        if day.weekday() in common_days:
            break
    start = datetime.combine(day, time(8), tzinfo=TZ).isoformat()
    one = create_appointment("Cliente A", "16999990001", 1, 1, start, True, days_range=False)
    two = create_appointment("Cliente B", "16999990002", 1, 2, start, True, days_range=False)
    return day, one, two


def test_staff_agenda_is_protected(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "protected.db")
    with TestClient(app) as api:
        for method, endpoint in (
            ("GET", "/me"), ("POST", "/logout"), ("GET", "/appointments"),
            ("POST", "/appointments"), ("GET", "/availability"), ("GET", "/blocks"),
            ("POST", "/blocks"), ("DELETE", "/blocks/1"),
            ("POST", "/appointments/1/cancel"), ("GET", "/appointments/1/reschedule-options"),
            ("POST", "/appointments/1/reschedule"), ("POST", "/services"),
            ("PATCH", "/services/1"), ("PUT", "/shop"), ("PUT", "/barbers/1/working-days"),
        ):
            assert api.request(method, f"/staff{endpoint}").status_code == 401


def test_staff_routes_require_a_session_and_logout_revokes_it(staff_app):
    api = staff_app
    assert api.get("/staff/me").status_code == 401
    assert api.post("/staff/login", json={"username": "dono", "password": "incorreta"}).status_code == 401
    headers = login(api, "dono", "Senha exclusiva do dono")
    profile = api.get("/staff/me", headers=headers)
    assert profile.json()["role"] == "owner"
    assert profile.headers.get("Cache-Control") == "no-store"
    assert api.post("/staff/logout", headers=headers).status_code == 204
    assert api.get("/staff/me", headers=headers).status_code == 401


def test_barber_can_only_see_and_change_own_reservations(staff_app):
    api = staff_app
    day, own, other = seed_reservations()
    barber = login(api, "rafael", "Senha exclusiva do Rafael")
    owner = login(api, "dono", "Senha exclusiva do dono")
    params = {"day": day.isoformat()}
    assert {item["id"] for item in api.get("/staff/appointments", params=params, headers=owner).json()} == {own["id"], other["id"]}
    assert [item["id"] for item in api.get("/staff/appointments", params=params, headers=barber).json()] == [own["id"]]
    assert api.get("/staff/appointments", params={**params, "barber_id": 2}, headers=barber).status_code == 403
    url = f"/staff/appointments/{other['id']}/cancel"
    assert api.post(url, json={"confirmed": True}, headers=barber).status_code == 404
    assert api.get(f"/staff/appointments/{other['id']}/reschedule-options", params=params, headers=barber).status_code == 404
    url = f"/staff/appointments/{own['id']}/cancel"
    assert api.post(url, json={"confirmed": False}, headers=barber).status_code == 409
    assert api.post(url, json={"confirmed": True}, headers=barber).status_code == 200
    assert find_appointments("16999990001")[0]["status"] == "cancelled"
    assert find_appointments("16999990002")[0]["status"] == "confirmed"


def test_owner_catalog_updates_are_visible_to_clients(staff_app):
    api = staff_app
    day, reservation, _ = seed_reservations()
    barber = login(api, "rafael", "Senha exclusiva do Rafael")
    owner = login(api, "dono", "Senha exclusiva do dono")
    update = {"price": 55.5, "duration_minutes": 45}
    assert api.patch("/staff/services/1", json=update, headers=barber).status_code == 403
    assert api.patch("/staff/services/1", json=update, headers=owner).status_code == 200
    assert api.get("/services").json()[0]["price"] == 55.5
    assert api.get("/services").json()[0]["duration_minutes"] == 45
    records = api.get("/staff/appointments", params={"day": day.isoformat()}, headers=owner).json()
    assert next(item for item in records if item["id"] == reservation["id"])["price"] == 50.0
    assert find_appointments("16999990001")[0]["price"] == 50.0


def test_staff_reschedule_checks_availability_and_preserves_original_on_conflict(staff_app):
    api = staff_app
    day, own, _ = seed_reservations()
    headers = login(api, "rafael", "Senha exclusiva do Rafael")
    options = api.get(f"/staff/appointments/{own['id']}/reschedule-options", params={"day": day.isoformat()}, headers=headers)
    assert options.status_code == 200
    slots = options.json()
    assert slots[0]["starts_at"][11:16] == "08:30"
    occupied = next(slot for slot in slots if slot["starts_at"][11:16] == "10:00")
    create_appointment("Cliente C", "16999990003", 1, 1, occupied["starts_at"], True, days_range=False)
    url = f"/staff/appointments/{own['id']}/reschedule"
    assert api.post(url, json={"confirmed": True, "new_starts_at": occupied["starts_at"]}, headers=headers).status_code == 409
    assert find_appointments("16999990001")[0]["starts_at"] == own["starts_at"]
    assert api.post(url, json={"confirmed": True, "new_starts_at": slots[0]["starts_at"]}, headers=headers).status_code == 200
    assert find_appointments("16999990001")[0]["starts_at"] == slots[0]["starts_at"]


@pytest.mark.parametrize(("username", "password", "owner"), [
    ("dono", "Senha exclusiva do dono", True),
    ("rafael", "Senha exclusiva do Rafael", False),
])
def test_staff_page_login_displays_the_correct_panel(staff_app, monkeypatch, username, password, owner):
    def request(_session, method, url, *, params=None, json=None, headers=None, timeout=None):
        result = staff_app.request(method, urlsplit(url).path, params=params, json=json, headers=headers)
        response = requests.Response()
        response.status_code = result.status_code
        response._content = result.content
        response.encoding = "utf-8"
        response.url = url
        return response

    monkeypatch.setenv("API_BASE_URL", f"http://test-staff-{username}.example")
    monkeypatch.setattr(requests.Session, "request", request)
    script = Path(__file__).resolve().parents[1] / "src" / "streamlit_app.py"
    ui = AppTest.from_file(str(script)).run().switch_page("pages/equipe.py").run()
    assert ui.title[0].value == "Área da equipe"
    assert not ui.tabs and not ui.dataframe
    ui.text_input[0].input(username)
    ui.text_input[1].input(password)
    next(widget for widget in ui.button if widget.label == "Entrar").click().run()
    assert not ui.exception
    assert ui.title[0].value == "Painel da equipe"
    assert any(header.value == "Agenda de trabalho" for header in ui.header)
    assert any(tab.label == "Serviços" for tab in ui.tabs) is owner
    next(widget for widget in ui.button if widget.label == "Amanhã").click().run()
    assert ui.date_input(key="staff_agenda_day").value == datetime.now(TZ).date() + timedelta(days=1)
    next(widget for widget in ui.button if widget.label == "Sair").click().run()
    assert ui.title[0].value == "Área da equipe"
    assert not ui.tabs and not ui.dataframe


def test_barber_blocks_hide_slots_and_cannot_override_reservations(staff_app):
    api = staff_app
    day, own, _ = seed_reservations()
    headers = login(api, "rafael", "Senha exclusiva do Rafael")
    def at(hour, minute=0):
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ).isoformat()

    payload = {"barber_id": 1, "starts_at": at(10), "ends_at": at(11), "reason": "Intervalo extra"}
    assert api.post("/staff/blocks", json={**payload, "barber_id": 2}, headers=headers).status_code == 403
    clash = api.post("/staff/blocks", json={**payload, "starts_at": own["starts_at"]}, headers=headers)
    assert clash.status_code == 409
    assert f"#{own['id']}" in clash.json()["detail"]
    block = api.post("/staff/blocks", json=payload, headers=headers)
    assert block.status_code == 201, block.text
    params = {"day": day.isoformat(), "service_id": 1, "barber_id": 1}
    available = api.get("/staff/availability", params=params, headers=headers).json()
    assert at(10) not in {slot["starts_at"] for slot in available}
    booking = {"customer_name": "Cliente Balcão", "customer_phone": "16999990009", "service_id": 1,
               "barber_id": 1, "starts_at": at(10), "confirmed": True}
    assert api.post("/staff/appointments", json=booking, headers=headers).status_code == 409
    assert api.post("/staff/appointments", json={**booking, "barber_id": 2}, headers=headers).status_code == 403
    assert api.post(f"/staff/appointments/{own['id']}/reschedule", json={"new_starts_at": at(10), "confirmed": True}, headers=headers).status_code == 409
    assert api.delete(f"/staff/blocks/{block.json()['id']}", headers=headers).status_code == 204
    replacement = api.post("/staff/blocks", json={**payload, "starts_at": at(11), "ends_at": at(12)}, headers=headers)
    assert replacement.status_code == 201 and replacement.json()["id"] != block.json()["id"]
    assert api.delete(f"/staff/blocks/{block.json()['id']}", headers=headers).status_code == 404
    assert api.post("/staff/appointments", json=booking, headers=headers).status_code == 201
    assert find_appointments("16999990009")[0]["starts_at"] == at(10)


def test_public_profile_and_hours_use_owner_settings(staff_app):
    api = staff_app
    day, own, _ = seed_reservations()
    owner = login(api, "dono", "Senha exclusiva do dono")
    barber = login(api, "rafael", "Senha exclusiva do Rafael")
    profile = {"name": "Barbearia Imperial", "address": "Rua de Exemplo, 40", "phone": "(16) 91111-3322",
               "payment_methods": "Pix e cartão", "open_1": "08:00", "close_1": "12:00",
               "open_2": "14:00", "close_2": "20:00"}
    assert api.put("/staff/shop", json=profile, headers=barber).status_code == 403
    bad = api.put("/staff/shop", json={**profile, "open_1": "09:00"}, headers=owner)
    assert bad.status_code == 409
    assert f"#{own['id']}" in bad.json()["detail"]
    assert api.put("/staff/shop", json=profile, headers=owner).status_code == 200
    assert api.get("/shop").json()["address"] == profile["address"]
    available = api.get("/staff/availability", params={"day": day.isoformat(), "service_id": 1, "barber_id": 1}, headers=barber).json()
    assert min(slot["starts_at"][11:16] for slot in available if slot["starts_at"][11:16] >= "12:00") == "14:00"
    weekday_conflict = api.put("/staff/barbers/1/working-days", json={"working_days": [(day.weekday() + 1) % 7]}, headers=owner)
    assert weekday_conflict.status_code == 409


def test_multiday_block_applies_to_public_availability_and_respects_end_boundary(staff_app):
    api = staff_app
    owner = login(api, "dono", "Senha exclusiva do dono")
    barber = login(api, "rafael", "Senha exclusiva do Rafael")
    day = datetime.now(TZ).date() + timedelta(days=1)
    if day.weekday() == 6:
        day += timedelta(days=1)
    params = {"appointment_date": day.isoformat(), "service_id": 1}
    original = api.get("/availability", params=params).json()[0]
    start = datetime.combine(day - timedelta(days=1), time(20), tzinfo=TZ).isoformat()
    ends = datetime.combine(day, time(10), tzinfo=TZ).isoformat()
    response = api.post("/staff/blocks", json={
        "barber_id": original["barber_id"], "starts_at": start, "ends_at": ends, "reason": "Folga",
    }, headers=owner)
    assert response.status_code == 201, response.text
    block_id = response.json()["id"]
    visible = api.get("/availability", params={**params, "barber_id": original["barber_id"]}).json()
    assert visible[0]["starts_at"] == ends
    denied = api.post("/appointments", json={"customer_name": "Cliente Teste", "customer_phone": "16999990009",
        "service_id": 1, "barber_id": original["barber_id"], "starts_at": original["starts_at"], "confirmed": True})
    assert denied.status_code == 409 and "bloqueado" in denied.json()["detail"]
    if original["barber_id"] != 1:
        assert api.delete(f"/staff/blocks/{block_id}", headers=barber).status_code == 404
