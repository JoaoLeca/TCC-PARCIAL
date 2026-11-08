from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests
import streamlit as st
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from barber_bot import db
from barber_bot.api import app
from barber_bot.config import MAX_BOOKING_DAYS, MIN_BOOKING_DAYS
from barber_bot.schedule import TZ


APP_SCRIPT = Path(__file__).resolve().parents[1] / "src" / "streamlit_app.py"


@pytest.fixture
def booking_app(tmp_path, monkeypatch):
    monkeypatch.setenv("API_BASE_URL", f"http://{tmp_path.name}.test")
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "booking-ui.db")
    with TestClient(app) as api:
        def request(_session, method, url, *, params=None, data=None, json=None, timeout=None):
            result = api.request(method, urlsplit(url).path, params=params, data=data, json=json)
            response = requests.Response()
            response.url = url
            response.status_code = result.status_code
            response._content = result.content
            response.encoding = "utf-8"
            return response

        monkeypatch.setattr(requests.Session, "request", request)
        yield AppTest.from_file(str(APP_SCRIPT)).run(), api


def button(ui, label):
    return next(widget for widget in ui.button if widget.label == label)


def has_contact_fields(ui):
    return any(widget.label == "Seu nome" for widget in ui.text_input)


def next_open_days():
    today = datetime.now(TZ).date()
    days = [today + timedelta(days=offset) for offset in (1, 2, 3)]
    return [day for day in days if day.weekday() != 6]


def test_booking_dates_follow_the_selected_barber_availability(booking_app):
    ui, _ = booking_app
    first_day, next_day, *_ = next_open_days()
    with db.connect() as conn:
        conn.execute("UPDATE barbers SET working_days = ? WHERE id = 1", (str(next_day.weekday()),))

    ui.selectbox(key="booking_barber").select(1).run()

    assert not ui.exception
    assert ui.session_state["booking_date"] == next_day
    assert not ui.button(key=f"booking_day_{next_day.isoformat()}").disabled
    if first_day.month == next_day.month:
        assert ui.button(key=f"booking_day_{first_day.isoformat()}").disabled
    assert any("Sem vaga ou fora do período" in item.value for item in ui.caption)
    assert ui.selectbox(key="booking_slot").options


def choose_slot(ui):
    slot = ui.selectbox(key="booking_slot")
    slot.select_index(1).run()
    assert not ui.exception
    return ui.selectbox(key="booking_slot").value


def test_agenda_opens_first_and_only_asks_for_contact_after_slot_selection(booking_app):
    ui, _ = booking_app
    assert ui.tabs[0].label == "Agendar"
    assert not has_contact_fields(ui)
    ui.selectbox(key="booking_slot").select_index(1).run()
    assert has_contact_fields(ui)
    assert button(ui, "Confirmar agendamento").disabled


def test_booking_step_is_visible_even_when_catalog_is_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise requests.ConnectionError("Agenda indisponível")

    monkeypatch.setenv("API_BASE_URL", "http://unavailable-catalog.test")
    monkeypatch.setattr(requests.Session, "request", unavailable)
    ui = AppTest.from_file(str(APP_SCRIPT)).run()
    assert not ui.exception
    assert any("Encontre um horário" in element.value for element in ui.markdown)
    assert any("Não foi possível carregar a agenda" in element.value for element in ui.error)
    assert button(ui, "Tentar novamente")


def test_first_screen_starts_rendering_after_catalog_is_ready(monkeypatch):
    events = []
    original_title = st.title

    def title(*args, **kwargs):
        events.append("title")
        return original_title(*args, **kwargs)

    def request(_session, method, url, **kwargs):
        endpoint = urlsplit(url).path
        events.append(endpoint)
        response = requests.Response()
        response.status_code = 200
        if endpoint == "/availability":
            response._content = b"[]"
            return response
        assert endpoint == "/catalog"
        response._content = b'''{
            "services": [{"id": 1, "name": "Corte", "price": 50, "duration_minutes": 40}],
            "barbers": [{"id": 1, "name": "Rafael", "working_days": [1, 2, 3, 4, 5, 6]}],
            "shop": {"name": "Barbearia Imperial", "address": "Rua Teste", "phone": "16999990000",
                     "payment_methods": "Pix", "open_1": "08:00", "close_1": "12:00", "open_2": null}
        }'''
        return response

    monkeypatch.setenv("API_BASE_URL", "http://first-screen.test")
    monkeypatch.setattr(st, "title", title)
    monkeypatch.setattr(requests.Session, "request", request)
    ui = AppTest.from_file(str(APP_SCRIPT)).run()
    assert not ui.exception
    first_render = ["/catalog", "title"] + ["/availability"] * (MAX_BOOKING_DAYS - MIN_BOOKING_DAYS + 1)
    assert events == first_render
    assert ui.selectbox(key="booking_service").value["name"] == "Corte"
    ui.run()
    assert events == [*first_render, "title"]


def test_booking_shows_receipt_and_persists_across_reruns(booking_app):
    ui, api = booking_app
    slot = choose_slot(ui)
    ui.text_input(key="booking_name").input("Cliente Teste")
    ui.text_input(key="booking_phone").input("16999990000").run()
    button(ui, "Confirmar agendamento").click().run()
    assert not ui.exception
    records = api.get("/appointments", params={"phone": "16999990000"}).json()
    assert len(records) == 1
    assert records[0]["starts_at"] == slot["starts_at"]
    assert records[0]["barber_id"] == slot["barber_id"]
    ui.run()
    assert any(message.value == "Agendamento confirmado!" for message in ui.success)
    assert any(f"#{records[0]['id']}" in message.value for message in ui.markdown)


def test_new_search_filters_invalidate_previous_slot(booking_app):
    ui, _ = booking_app
    slot = choose_slot(ui)
    selected_day = datetime.fromisoformat(slot["starts_at"]).date()
    next_day = next_open_days()[1]
    if next_day.month != selected_day.month:
        ui.button(key="booking_next_month").click().run()
    ui.button(key=f"booking_day_{next_day.isoformat()}").click().run()
    assert ui.session_state["booking_date"] == next_day
    assert ui.selectbox(key="booking_slot").value is None
    assert all(widget.label != "Confirmar agendamento" for widget in ui.button)


def test_empty_search_removes_previous_slot_even_with_same_filters(booking_app):
    ui, _ = booking_app
    choose_slot(ui)
    with db.connect() as conn:
        conn.execute("UPDATE barbers SET working_days = '6'")

    button(ui, "Atualizar disponibilidade").click().run()
    assert any("Não há horários" in message.value for message in ui.info)
    assert all(widget.key != "booking_slot" for widget in ui.selectbox)
    assert all(widget.label != "Confirmar agendamento" for widget in ui.button)


def test_conflict_refreshes_slots_and_preserves_contact(booking_app):
    ui, api = booking_app
    slot = choose_slot(ui)
    result = api.post("/appointments", json={
        "customer_name": "Outra Pessoa", "customer_phone": "16999990001",
        "service_id": 1, "barber_id": slot["barber_id"],
        "starts_at": slot["starts_at"], "confirmed": True,
    })
    assert result.status_code == 200
    ui.text_input(key="booking_name").input("Cliente Teste")
    ui.text_input(key="booking_phone").input("16999990000").run()
    button(ui, "Confirmar agendamento").click().run()
    assert not ui.exception
    assert any("ocupado" in message.value for message in ui.warning)
    options = ui.selectbox(key="booking_slot")
    assert options.value is None
    assert f"{datetime.fromisoformat(slot['starts_at']):%H:%M} · {slot['barber']}" not in options.options
    options.select_index(1).run()
    assert ui.text_input(key="booking_name").value == "Cliente Teste"
    assert ui.text_input(key="booking_phone").value == "16999990000"


def create_manage_booking(api, phone="16999990000"):
    day = next_open_days()[0]
    slots = api.get("/availability", params={"service_id": 1, "appointment_date": day.isoformat()}).json()
    slot = slots[0]
    result = api.post("/appointments", json={
        "customer_name": "Cliente Teste", "customer_phone": phone,
        "service_id": 1, "barber_id": slot["barber_id"],
        "starts_at": slot["starts_at"], "confirmed": True,
    })
    assert result.status_code == 200
    return result.json()


def lookup_booking(ui, phone="16999990000"):
    ui.text_input(key="lookup_phone").input(phone)
    button(ui, "Consultar").click().run()
    assert not ui.exception


def test_booking_can_be_cancelled_after_lookup_and_explicit_confirmation(booking_app):
    ui, api = booking_app
    record = create_manage_booking(api)
    lookup_booking(ui)
    ui.run()
    button(ui, "Cancelar reserva").click().run()
    assert api.get("/appointments", params={"phone": "16999990000"}).json()[0]["status"] == "confirmed"
    button(ui, "Confirmar cancelamento").click().run()
    assert not ui.exception
    assert any(element.value == "Reserva cancelada." for element in ui.success)
    assert any("Cancelado" in element.value for element in ui.markdown)
    result = api.get("/appointments", params={"phone": "16999990000"}).json()[0]
    assert (result["id"], result["status"]) == (record["id"], "cancelled")


def test_manage_refreshes_a_booking_cancelled_elsewhere(booking_app):
    ui, api = booking_app
    record = create_manage_booking(api)
    lookup_booking(ui)
    button(ui, "Cancelar reserva").click().run()
    result = api.post(f"/appointments/{record['id']}/cancel", json={"phone": "16999990000", "confirmed": True})
    assert result.status_code == 200

    button(ui, "Confirmar cancelamento").click().run()
    assert any("não encontrado" in element.value for element in ui.warning)
    assert any("Cancelado" in element.value for element in ui.markdown)
    assert all(widget.label != "Confirmar cancelamento" for widget in ui.button)


def test_reschedule_finds_formatted_phone_and_updates_reservation(booking_app):
    ui, api = booking_app
    record = create_manage_booking(api, "(16) 99999-0000")
    lookup_booking(ui)
    button(ui, "Remarcar").click().run()
    button(ui, "Buscar novos horários").click().run()
    selector = next(widget for widget in ui.selectbox if widget.label == "Novo horário")
    assert selector.options[0] == "08h30"
    selector.select_index(0).run()
    selected = next(widget for widget in ui.selectbox if widget.label == "Novo horário").value
    assert api.get("/appointments", params={"phone": "16999990000"}).json()[0]["starts_at"] == record["starts_at"]
    button(ui, "Confirmar remarcação").click().run()
    assert not ui.exception
    assert any(element.value == "Reserva remarcada." for element in ui.success)
    result = api.get("/appointments", params={"phone": "16999990000"}).json()[0]
    assert (result["id"], result["starts_at"]) == (record["id"], selected["starts_at"])
