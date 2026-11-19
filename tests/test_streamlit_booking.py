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
        yield AppTest.from_file(str(APP_SCRIPT), default_timeout=15).run(), api


def button(ui, label):
    return next(widget for widget in ui.button if widget.label == label)


def has_contact_fields(ui):
    return any(widget.label == "Seu nome" for widget in ui.text_input)


def test_floating_chat_preserves_booking_and_shares_conversation(booking_app, monkeypatch):
    ui, _ = booking_app
    selected_slot = choose_slot(ui)
    messages_sent = []

    def answer_chat(url, *, json, timeout):
        assert url.endswith("/chat")
        messages_sent.append(json["messages"][-1]["content"])
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"answer":"Temos corte de cabelo."}'
        return response

    monkeypatch.setattr(requests, "post", answer_chat)
    button(ui, "Conversar").click().run()
    ui.chat_input(key="floating_chat_input").set_value("Quais os serviços?").run()
    button(ui, "Fechar conversa").click().run()
    assert all(widget.key != "floating_chat_input" for widget in ui.chat_input)
    assert any(item.value == "Temos corte de cabelo." for item in ui.markdown)
    button(ui, "Conversar").click().run()
    assert ui.chat_input(key="floating_chat_input")
    assert ui.session_state["booking_slot"] == selected_slot
    assert messages_sent == ["Quais os serviços?"]
    assert not ui.exception


def mock_chat_options(monkeypatch):
    import barber_bot.api as api_module
    from barber_bot.schedule import availability, list_services

    service = list_services()[0]
    today = datetime.now(TZ).date()
    slots = []
    for offset in range(MIN_BOOKING_DAYS, MAX_BOOKING_DAYS + 1):
        slots = availability(service["id"], today + timedelta(days=offset))
        if slots:
            break

    def fake_agent(messages, **kwargs):
        return {
            "content": "Encontrei estas opções disponíveis.", "intent": "consultar_disponibilidade",
            "confidence": 1.0, "tools_called": ["buscar_disponibilidade"], "mode": "ai",
            "outcome": "answered", "booking_options": {"service": service, "slots": slots[:3]},
        }

    monkeypatch.setattr(api_module, "run_agent", fake_agent)


def request_chat_suggestions(ui, monkeypatch):
    mock_chat_options(monkeypatch)
    day = next_open_days()[0]
    button(ui, "Conversar").click().run()
    ui.chat_input(key="floating_chat_input").set_value(
        f"Quero agendar Cabelo + Barba em {day.isoformat()} depois das 15h"
    ).run()
    assert not ui.exception
    return ui.session_state["messages"][-1]["booking_options"]


def suggestion_button(ui):
    return next(widget for widget in ui.button if widget.key and widget.key.startswith("floating_") and "_suggestion_" in widget.key)


def test_chat_suggestion_prefills_booking_and_keeps_contact_without_creating_reservation(booking_app, monkeypatch):
    ui, _ = booking_app
    choose_slot(ui)
    ui.text_input(key="booking_name").input("Cliente Teste")
    ui.text_input(key="booking_phone").input("16999990000").run()
    options = request_chat_suggestions(ui, monkeypatch)
    slot = options["slots"][0]
    suggestion_button(ui).click().run()
    assert not ui.exception
    assert ui.session_state["public_section"] == "Agendar"
    assert ui.selectbox(key="booking_service").value["id"] == options["service"]["id"]
    assert ui.selectbox(key="booking_barber").value == slot["barber_id"]
    assert ui.session_state["booking_slot"] == slot
    assert ui.text_input(key="booking_name").value == "Cliente Teste"
    assert ui.text_input(key="booking_phone").value == "16999990000"
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM appointments").fetchone()[0] == 0


def test_chat_suggestion_rejects_a_slot_occupied_after_the_answer(booking_app, monkeypatch):
    ui, api = booking_app
    options = request_chat_suggestions(ui, monkeypatch)
    slot = options["slots"][0]
    response = api.post("/appointments", json={
        "customer_name": "Outro Cliente", "customer_phone": "16999990001", "service_id": options["service"]["id"],
        "barber_id": slot["barber_id"], "starts_at": slot["starts_at"], "confirmed": True,
    })
    assert response.status_code == 200
    suggestion_button(ui).click().run()
    assert not ui.exception
    assert any("Esse horário mudou" in item.value for item in ui.warning)
    assert ui.session_state.get("booking_slot") is None
    assert not has_contact_fields(ui)


def test_chat_suggestion_rejects_changed_price(booking_app, monkeypatch):
    ui, _ = booking_app
    options = request_chat_suggestions(ui, monkeypatch)
    with db.connect() as conn:
        conn.execute("UPDATE services SET price=price+5 WHERE id=?", (options["service"]["id"],))
    suggestion_button(ui).click().run()
    assert not ui.exception
    assert any("serviço foi atualizado" in item.value for item in ui.warning)
    assert ui.session_state.get("booking_slot") is None


def test_chat_tab_suggestion_switches_to_booking_review(booking_app, monkeypatch):
    ui, _ = booking_app
    mock_chat_options(monkeypatch)
    ui.session_state["public_section"] = "Conversar"
    ui.run()
    day = next_open_days()[0]
    ui.chat_input(key="tab_chat_input").set_value(f"Quero agendar corte em {day.isoformat()} às 15h").run()
    options = ui.session_state["messages"][-1]["booking_options"]
    chosen = next(widget for widget in ui.button if widget.key and widget.key.startswith("tab_") and "_suggestion_" in widget.key)
    chosen.click().run()
    assert not ui.exception
    assert ui.session_state["public_section"] == "Agendar"
    assert ui.session_state["booking_slot"] == options["slots"][0]
    assert has_contact_fields(ui)


def test_chat_suggestion_preserves_pending_confirmation(booking_app, monkeypatch):
    ui, _ = booking_app
    request_chat_suggestions(ui, monkeypatch)
    pending = {"request_key": "confirmation-not-received"}
    ui.session_state["booking_pending"] = pending
    suggestion_button(ui).click().run()
    assert not ui.exception
    assert ui.session_state["booking_pending"] == pending
    assert not ui.session_state.get("chat_booking_choice")
    assert any("confirmação pendente" in item.value for item in ui.warning)


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
    assert time_buttons(ui)


def time_buttons(ui):
    return [widget for widget in ui.button if widget.key and widget.key.startswith("booking_time_")]


def choose_slot(ui):
    time_buttons(ui)[1].click().run()
    professionals = [widget for widget in ui.button if widget.key and widget.key.startswith("booking_professional_")]
    if professionals:
        professionals[0].click().run()
    assert not ui.exception
    return ui.session_state["booking_slot"]


def test_no_preference_groups_times_and_then_lists_matching_professionals(booking_app):
    ui, api = booking_app
    labels = [widget.label for widget in time_buttons(ui)]
    assert len(labels) == len(set(labels))
    time_buttons(ui)[1].click().run()
    selected_start = ui.session_state["booking_start"]
    slots = api.get("/availability", params={
        "service_id": 1, "appointment_date": ui.session_state["booking_date"].isoformat(),
    }).json()
    expected = {slot["barber"] for slot in slots if slot["starts_at"] == selected_start}
    options = [widget for widget in ui.button if widget.key and widget.key.startswith("booking_professional_")]
    assert {widget.label for widget in options} == expected
    assert len(expected) > 1
    assert not has_contact_fields(ui)
    options[-1].click().run()
    assert ui.session_state["booking_slot"]["barber"] == options[-1].label
    assert ui.session_state["booking_slot"]["starts_at"] == selected_start
    assert has_contact_fields(ui)
    del ui.session_state["booking_start"]
    ui.run()
    assert ui.session_state["booking_start"] == selected_start
    assert has_contact_fields(ui)
    time_buttons(ui)[2].click().run()
    assert ui.session_state.get("booking_slot") is None
    assert not has_contact_fields(ui)


def test_only_available_professional_is_selected_automatically(booking_app):
    ui, _ = booking_app
    with db.connect() as conn:
        conn.execute("UPDATE barbers SET working_days='6' WHERE id IN (1,2)")
    button(ui, "Atualizar disponibilidade").click().run()
    time_buttons(ui)[0].click().run()
    assert ui.session_state["booking_slot"]["barber"] == "Alecio"
    assert not any(widget.key and widget.key.startswith("booking_professional_") for widget in ui.button)
    assert has_contact_fields(ui)


def test_specific_professional_needs_only_time_selection(booking_app):
    ui, _ = booking_app
    ui.selectbox(key="booking_barber").select(1).run()
    time_buttons(ui)[0].click().run()
    assert ui.session_state["booking_slot"]["barber_id"] == 1
    assert has_contact_fields(ui)
    assert not any(widget.key and widget.key.startswith("booking_professional_") for widget in ui.button)


def test_customer_sees_support_status_lifecycle_in_conversation(booking_app):
    from barber_bot.staff import StaffMember, save_staff_account
    from barber_bot.support import update_staff_request
    ui, _ = booking_app
    button(ui, "Conversar").click().run()
    ui.button(key="floating_human_request").click().run()
    ui.text_input(key="floating_support_name").input("Cliente Teste")
    ui.text_input(key="floating_support_phone").input("16999990000")
    ui.button(key="floating_support_send").click().run()
    ticket = ui.session_state["support_request"]
    assert any("Aguardando a equipe" in item.value for item in ui.caption)
    save_staff_account("test-owner", "Senha local da equipe", "owner")
    owner = StaffMember("test-owner", "owner", None, "Equipe")
    update_staff_request(ticket["id"], owner, claim=True)
    ui.button(key="floating_support_refresh").click().run()
    assert ui.session_state["support_request"]["status"] == "in_progress"
    assert any("Em atendimento" in item.value for item in ui.caption)
    update_staff_request(ticket["id"], owner, status="resolved", note="Observação interna")
    ui.button(key="floating_support_refresh").click().run()
    assert ui.session_state["support_request"]["status"] == "resolved"
    assert any("Resolvido" in item.value for item in ui.caption)
    assert not any("Observação interna" in item.value for item in ui.markdown)
    assert not ui.exception


def test_support_form_has_its_own_view_and_returns_to_conversation(booking_app):
    ui, _ = booking_app
    ui.session_state["messages"] = [{"role": "user", "content": "Minha conversa anterior"}]
    button(ui, "Conversar").click().run()
    ui.button(key="floating_human_request").click().run()
    assert not any(widget.key == "floating_chat_input" for widget in ui.chat_input)
    assert not any("Aguardando a equipe" in item.value for item in ui.caption)
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM support_tickets").fetchone()[0] == 0
    ui.text_input(key="floating_support_name").input("Cliente Teste")
    ui.text_input(key="floating_support_phone").input("16999990000")
    ui.button(key="floating_support_back").click().run()
    assert ui.chat_input(key="floating_chat_input")
    assert ui.session_state["messages"] == [{"role": "user", "content": "Minha conversa anterior"}]
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM support_tickets").fetchone()[0] == 0
    ui.button(key="floating_human_request").click().run()
    assert ui.text_input(key="floating_support_name").value == "Cliente Teste"
    assert ui.text_input(key="floating_support_phone").value == "16999990000"
    ui.button(key="floating_support_send").click().run()
    assert ui.chat_input(key="floating_chat_input")
    assert not any(widget.key == "floating_support_name" for widget in ui.text_input)
    assert not any(widget.key == "floating_human_request" for widget in ui.button)
    assert ui.session_state["support_request"]["contact_provided"] is True
    assert not ui.exception


def test_agenda_opens_first_and_only_asks_for_contact_after_slot_selection(booking_app):
    ui, _ = booking_app
    assert ui.tabs[0].label == "Agendar"
    assert not has_contact_fields(ui)
    choose_slot(ui)
    assert has_contact_fields(ui)
    assert button(ui, "Confirmar agendamento").disabled


def test_booking_step_is_visible_even_when_catalog_is_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise requests.ConnectionError("Agenda indisponível")

    monkeypatch.setenv("API_BASE_URL", "http://unavailable-catalog.test")
    monkeypatch.setattr(requests.Session, "request", unavailable)
    ui = AppTest.from_file(str(APP_SCRIPT), default_timeout=15).run()
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
    ui = AppTest.from_file(str(APP_SCRIPT), default_timeout=15).run()
    assert not ui.exception
    first_render = ["/catalog", "title"] + ["/availability"] * (MAX_BOOKING_DAYS - MIN_BOOKING_DAYS + 1)
    assert events == first_render
    assert ui.selectbox(key="booking_service").value["name"] == "Corte"
    ui.run()
    assert events == [*first_render, "title"]


def test_booking_shows_receipt_and_persists_across_reruns(booking_app, monkeypatch):
    ui, api = booking_app
    downloads = []
    download_button = st.download_button

    def capture_download(label, data, **kwargs):
        downloads.append((label, data, kwargs["file_name"]))
        return download_button(label, data, **kwargs)

    monkeypatch.setattr(st, "download_button", capture_download)
    slot = choose_slot(ui)
    ui.text_input(key="booking_name").input("Cliente Teste")
    ui.text_input(key="booking_phone").input("16999990000").run()
    button(ui, "Confirmar agendamento").click().run()
    assert not ui.exception
    receipt = ui.session_state["booking_receipt"]
    assert "access_code" not in receipt
    assert not any("Identificação da reserva" in item.value for item in ui.markdown)
    record = customer_booking(api, {**receipt, "test_token": ui.session_state["customer_token"]})
    assert record["starts_at"] == slot["starts_at"]
    label, content, filename = downloads[-1]
    assert label == "Salvar comprovante"
    assert filename == f"reserva-imperial-{record['id']}.txt"
    for detail in (record["service"], record["barber"], "R$ 50,00", "40 min"):
        assert detail in content
    assert "Identificação da reserva" not in content and "0000" not in content
    assert "16999990000" not in content
    ui.run()
    assert any(message.value == "Agendamento confirmado!" for message in ui.success)
    assert any(f"#{record['id']}" in message.value for message in ui.markdown)


def test_new_search_filters_invalidate_previous_slot(booking_app):
    ui, _ = booking_app
    slot = choose_slot(ui)
    selected_day = datetime.fromisoformat(slot["starts_at"]).date()
    next_day = next_open_days()[1]
    if next_day.month != selected_day.month:
        ui.button(key="booking_next_month").click().run()
    ui.button(key=f"booking_day_{next_day.isoformat()}").click().run()
    assert ui.session_state["booking_date"] == next_day
    assert ui.session_state.get("booking_slot") is None
    assert all(widget.label != "Confirmar agendamento" for widget in ui.button)


def test_contact_survives_changing_day_and_choosing_again(booking_app):
    ui, _ = booking_app
    choose_slot(ui)
    ui.text_input(key="booking_name").input("Cliente Teste")
    ui.text_input(key="booking_phone").input("16999990000").run()
    next_day = next(day for day in next_open_days() if day != ui.session_state["booking_date"])
    if next_day.month != ui.session_state["booking_date"].month:
        ui.button(key="booking_next_month").click().run()
    ui.button(key=f"booking_day_{next_day.isoformat()}").click().run()
    choose_slot(ui)
    assert ui.text_input(key="booking_name").value == "Cliente Teste"
    assert ui.text_input(key="booking_phone").value == "16999990000"


def test_confirmation_recovers_lost_response_without_duplicate(booking_app, monkeypatch):
    ui, _ = booking_app
    choose_slot(ui)
    ui.text_input(key="booking_name").input("Cliente Teste")
    ui.text_input(key="booking_phone").input("16999990000").run()
    original_post = requests.post
    attempts = []
    def lose_first_reply(url, **kwargs):
        response = original_post(url, **kwargs)
        if url.endswith("/appointments"):
            attempts.append(kwargs["json"]["request_key"])
            if len(attempts) == 1:
                raise requests.Timeout("Resposta perdida após gravar")
        return response
    monkeypatch.setattr(requests, "post", lose_first_reply)
    button(ui, "Confirmar agendamento").click().run()
    ui.run()
    button(ui, "Tentar confirmar novamente").click().run()
    assert attempts[0] == attempts[1]
    assert any(message.value == "Agendamento confirmado!" for message in ui.success)
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM appointments").fetchone()[0] == 1


def test_chat_sends_bounded_history_after_a_long_conversation(booking_app, monkeypatch):
    ui, _ = booking_app
    ui.session_state["messages"] = [{"role": "user", "content": "mensagem anterior"}] * 110
    sent = []
    def answer(url, *, json, timeout):
        sent.append(json["messages"])
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"answer":"Resposta local."}'
        return response
    monkeypatch.setattr(requests, "post", answer)
    button(ui, "Conversar").click().run()
    ui.chat_input(key="floating_chat_input").set_value("Nova pergunta").run()
    assert len(sent[0]) == 16
    assert sent[0][-1]["content"] == "Nova pergunta"
    assert not ui.exception


def test_invalid_confirmation_returns_to_editable_form(booking_app, monkeypatch):
    ui, _ = booking_app
    choose_slot(ui)
    ui.text_input(key="booking_name").input("Cliente Teste")
    ui.text_input(key="booking_phone").input("16999990000").run()
    def invalid(*args, **kwargs):
        response = requests.Response()
        response.status_code = 422
        response._content = b'{"detail":[{"msg":"invalid"}]}'
        return response
    monkeypatch.setattr(requests, "post", invalid)
    button(ui, "Confirmar agendamento").click().run()
    assert not ui.session_state.get("booking_pending")
    assert any("Confira seus dados" in message.value for message in ui.warning)
    choose_slot(ui)
    assert ui.text_input(key="booking_name").value == "Cliente Teste"


def test_empty_search_removes_previous_slot_even_with_same_filters(booking_app):
    ui, _ = booking_app
    choose_slot(ui)
    with db.connect() as conn:
        conn.execute("UPDATE barbers SET working_days = '6'")

    button(ui, "Atualizar disponibilidade").click().run()
    assert any("Não há horários" in message.value for message in ui.info)
    assert not time_buttons(ui)
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
    assert ui.session_state.get("booking_slot") is None
    refreshed = api.get("/availability", params={"service_id": 1,
                        "appointment_date": datetime.fromisoformat(slot["starts_at"]).date().isoformat()}).json()
    assert slot not in refreshed
    choose_slot(ui)
    assert ui.text_input(key="booking_name").value == "Cliente Teste"
    assert ui.text_input(key="booking_phone").value == "16999990000"


def create_manage_booking(api, phone="16999990000", token="test-browser-token-" + "x" * 32):
    day = next_open_days()[0]
    slots = api.get("/availability", params={"service_id": 1, "appointment_date": day.isoformat()}).json()
    slot = slots[0]
    result = api.post("/appointments", json={
        "customer_name": "Cliente Teste", "customer_phone": phone,
        "service_id": 1, "barber_id": slot["barber_id"],
        "starts_at": slot["starts_at"], "confirmed": True, "customer_token": token,
    })
    assert result.status_code == 200
    return {**result.json(), "test_token": token}


def customer_booking(api, record):
    result = api.post(f"/customer/appointments/{record['id']}", json={"customer_token": record["test_token"]})
    assert result.status_code == 200
    return result.json()


def lookup_booking(ui, record, phone="16999990000"):
    ui.session_state["customer_token"] = record["test_token"]
    ui.session_state["public_section"] = "Meus horários"
    ui.run()
    button(ui, "Atualizar reservas").click().run()
    assert not ui.exception


def test_booking_can_be_cancelled_after_lookup_and_explicit_confirmation(booking_app):
    ui, api = booking_app
    record = create_manage_booking(api)
    lookup_booking(ui, record)
    ui.run()
    button(ui, "Cancelar reserva").click().run()
    assert customer_booking(api, record)["status"] == "confirmed"
    button(ui, "Confirmar cancelamento").click().run()
    assert not ui.exception
    assert any(element.value == "Reserva cancelada." for element in ui.success)
    assert any("Cancelado" in element.value for element in ui.markdown)
    result = customer_booking(api, record)
    assert (result["id"], result["status"]) == (record["id"], "cancelled")


def test_manage_refreshes_a_booking_cancelled_elsewhere(booking_app):
    ui, api = booking_app
    record = create_manage_booking(api)
    lookup_booking(ui, record)
    button(ui, "Cancelar reserva").click().run()
    result = api.post(f"/customer/appointments/{record['id']}/cancel", json={"customer_token": record["test_token"], "expected_starts_at": record["starts_at"], "confirmed": True})
    assert result.status_code == 200

    button(ui, "Confirmar cancelamento").click().run()
    assert any("não encontrado" in element.value for element in ui.warning)
    assert any("Cancelado" in element.value for element in ui.markdown)
    assert all(widget.label != "Confirmar cancelamento" for widget in ui.button)


def test_reschedule_finds_formatted_phone_and_updates_reservation(booking_app):
    ui, api = booking_app
    record = create_manage_booking(api, "(16) 99999-0000")
    lookup_booking(ui, record)
    button(ui, "Remarcar").click().run()
    button(ui, "Buscar novos horários").click().run()
    selector = next(widget for widget in ui.selectbox if widget.label == "Novo horário")
    assert selector.options[0] == "08h30"
    selector.select_index(0).run()
    selected = next(widget for widget in ui.selectbox if widget.label == "Novo horário").value
    assert customer_booking(api, record)["starts_at"] == record["starts_at"]
    button(ui, "Confirmar remarcação").click().run()
    assert not ui.exception
    assert any(element.value == "Reserva remarcada." for element in ui.success)
    result = customer_booking(api, record)
    assert (result["id"], result["starts_at"]) == (record["id"], selected["starts_at"])


def test_legacy_receipt_session_does_not_crash_after_upgrade(booking_app):
    ui, api = booking_app
    record = create_manage_booking(api)
    ui.session_state["booking_receipt"] = {key: value for key, value in record.items() if key != "identification_code"}
    ui.session_state["booking_receipt"]["access_code"] = "BI-legacy-session-value"
    ui.run()
    assert not ui.exception
    assert any(item.value == "Agendamento confirmado!" for item in ui.success)
    assert not ui.code
    assert customer_booking(api, record)["status"] == "confirmed"


def test_site_chat_cancels_only_after_written_confirmation_and_clears_receipt(booking_app):
    from barber_bot.assistant import OPENAI_API_KEY
    if not OPENAI_API_KEY:
        pytest.skip("A conversa de gestão de reservas exige OPENAI_API_KEY.")
    ui, api = booking_app
    choose_slot(ui)
    ui.text_input(key="booking_name").input("Cliente Teste")
    ui.text_input(key="booking_phone").input("16999990042").run()
    button(ui, "Confirmar agendamento").click().run()
    receipt = ui.session_state["booking_receipt"]
    record = {**receipt, "test_token": ui.session_state["customer_token"]}
    button(ui, "Conversar").click().run()
    ui.chat_input(key="floating_chat_input").set_value("Quero cancelar minha reserva").run()
    assert "Confirma o cancelamento" in ui.session_state["messages"][-1]["content"]
    assert customer_booking(api, record)["status"] == "confirmed"
    assert not any(item.key and "_suggestion_" in item.key for item in ui.button)
    ui.chat_input(key="floating_chat_input").set_value("sim").run()
    assert not ui.exception
    assert "Cancelamento confirmado" in ui.session_state["messages"][-1]["content"]
    assert customer_booking(api, record)["status"] == "cancelled"
    assert ui.session_state.get("booking_receipt") is None


def test_recovery_form_restores_legacy_booking_and_allows_confirmed_cancellation(booking_app, monkeypatch):
    from barber_bot import customer_recovery as recovery
    monkeypatch.setattr(recovery, "get_bot_username", lambda: "ImperialTesteBot")
    monkeypatch.setattr(recovery.secrets, "randbelow", lambda limit: 123456)
    ui, api = booking_app
    record = create_manage_booking(api, phone="16999990042")
    with db.connect() as conn:
        conn.execute("DELETE FROM booking_owners WHERE appointment_id=?", (record["id"],))
    ui.session_state["public_section"] = "Meus horários"
    ui.run()
    assert not any(item.label == "Cancelar reserva" for item in ui.button)
    next(item for item in ui.text_input if item.label == "Telefone usado na reserva").input("16999990042")
    button(ui, "Verificar pelo Telegram").click().run()
    challenge = ui.session_state["customer_recovery_challenge"]
    recovery.issue_telegram_code(challenge["challenge_id"], "123", "123", {"user_id": 123, "phone_number": "5516999990042"})
    next(item for item in ui.text_input if item.label == "Código recebido no Telegram").input("123456")
    button(ui, "Acessar minhas reservas").click().run()
    assert not ui.exception
    assert any("Telefone confirmado" in item.value for item in ui.success)
    button(ui, "Cancelar reserva").click().run()
    with db.connect() as conn:
        assert conn.execute("SELECT status FROM appointments").fetchone()[0] == "confirmed"
    button(ui, "Confirmar cancelamento").click().run()
    assert not ui.exception
    with db.connect() as conn:
        assert conn.execute("SELECT status FROM appointments").fetchone()[0] == "cancelled"


def test_chat_recovers_booking_and_masks_phone_and_one_time_code(booking_app, monkeypatch):
    from barber_bot.assistant import OPENAI_API_KEY
    if not OPENAI_API_KEY:
        pytest.skip("A recuperação pelo chat livre exige OPENAI_API_KEY.")
    from barber_bot import customer_recovery as recovery
    monkeypatch.setattr(recovery, "get_bot_username", lambda: "ImperialTesteBot")
    monkeypatch.setattr(recovery.secrets, "randbelow", lambda limit: 123456)
    ui, api = booking_app
    record = create_manage_booking(api, phone="16999990042")
    button(ui, "Conversar").click().run()
    ui.chat_input(key="floating_chat_input").set_value("quero ver meu agendamento").run()
    assert ui.session_state["chat_recovery_stage"] == "phone"
    ui.chat_input(key="floating_chat_input").set_value("16999990042").run()
    assert ui.session_state["chat_recovery_stage"] == "code"
    answer = ui.session_state["messages"][-1]["content"]
    nonce = answer.split("recover_", 1)[1].split(")", 1)[0]
    recovery.issue_telegram_code(nonce, "123", "123", {"user_id": 123, "phone_number": "5516999990042"})
    ui.chat_input(key="floating_chat_input").set_value("123456").run()
    assert not ui.exception
    assert "Telefone confirmado" in ui.session_state["messages"][-1]["content"]
    assert f"#{record['id']}" in ui.session_state["messages"][-1]["content"]
    assert "123456" not in str(ui.session_state["messages"])
    assert "16999990042" not in str(ui.session_state["messages"])
