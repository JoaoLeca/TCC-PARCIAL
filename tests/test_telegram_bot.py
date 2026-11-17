"""Offline Telegram regressions using the real booking database."""

from datetime import datetime, timedelta
import json

import pytest

from barber_bot import db, telegram_bot as bot
from barber_bot.schedule import availability, cancel_appointment, create_appointment, get_customer_appointment


@pytest.fixture
def telegram(tmp_path, monkeypatch):
    path = tmp_path / "telegram.db"
    monkeypatch.setattr(db, "DATABASE_PATH", path)
    db.initialize_database()
    sent = []
    fail = {"receipt": False}

    def api(method, payload=None, **kwargs):
        if method == "sendMessage":
            if fail["receipt"] and "Agendamento confirmado" in payload["text"]:
                raise bot.TelegramError("offline")
            sent.append(payload)
        return True

    monkeypatch.setattr(bot, "telegram_call", api)
    bot._recent_messages.clear()
    return path, sent, fail


def callback(data):
    bot.handle_update({"callback_query": {"id": "q", "data": data,
                       "message": {"chat": {"id": 123, "type": "private"}}}})


def text(value):
    bot.handle_update({"message": {"chat": {"id": 123, "type": "private"},
                                  "from": {"id": 123}, "text": value}})


def button(sent, action):
    for message in reversed(sent):
        for row in message.get("reply_markup", {}).get("inline_keyboard", []):
            for item in row:
                data = item["callback_data"]
                if data == action or data.endswith(":" + action):
                    return data
    raise AssertionError(f"Button {action!r} was not offered")


def available_day():
    for offset in range(1, bot.MAX_BOOKING_DAYS + 1):
        day = datetime.now(bot.TZ).date() + timedelta(days=offset)
        slots = availability(1, day)
        if slots:
            return day, slots
    raise AssertionError("No sample availability")


def show_slots(sent):
    day, slots = available_day()
    text("/agendar")
    callback(button(sent, "service:1"))
    callback(button(sent, f"day:{day.isoformat()}"))
    callback(button(sent, "barber:0"))
    return day, slots


def confirm_button(sent):
    show_slots(sent)
    callback(button(sent, "slot:0"))
    text("Ana")
    text("16999990000")
    return button(sent, "confirm")


def test_disappearing_slot_never_selects_the_next_available_slot(telegram):
    _, sent, _ = telegram
    _, slots = show_slots(sent)
    selected = button(sent, "slot:0")
    slot = slots[0]
    create_appointment("Outra pessoa", "16999990001", 1, slot["barber_id"], slot["starts_at"], True)
    callback(selected)
    state = bot._load_state("123")
    assert state["step"] == "slot"
    assert "starts_at" not in state
    assert any("ocupado" in item["text"] for item in sent)


def test_slot_keeps_its_identity_when_an_earlier_slot_disappears(telegram):
    _, sent, _ = telegram
    _, slots = show_slots(sent)
    selected = button(sent, "slot:3")
    first = slots[0]
    create_appointment("Outra pessoa", "16999990001", 1, first["barber_id"], first["starts_at"], True)
    callback(selected)
    state = bot._load_state("123")
    assert state["step"] == "name"
    assert state["starts_at"] == slots[3]["starts_at"]
    assert state["barber_id"] == slots[3]["barber_id"]


def test_old_flow_and_old_page_buttons_cannot_mutate_current_flow(telegram):
    _, sent, _ = telegram
    text("/agendar")
    stale_service = button(sent, "service:1")
    text("/agendar")
    callback(stale_service)
    assert bot._load_state("123")["step"] == "service"
    show_slots(sent)
    stale_slot = button(sent, "slot:0")
    callback(button(sent, "more"))
    callback(stale_slot)
    assert bot._load_state("123")["step"] == "slot"


def test_repeated_confirmation_creates_one_booking_and_stores_no_private_code(telegram):
    _, sent, _ = telegram
    confirm = confirm_button(sent)
    callback(confirm)
    callback(confirm)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 1
        state_json = conn.execute("SELECT state_json FROM telegram_sessions").fetchone()[0]
    assert "BI-" not in state_json
    assert "customer_phone" not in state_json
    assert bot._load_state("123")["step"] == "completed"


def test_failed_receipt_can_be_retried_without_creating_a_second_booking(telegram):
    _, sent, fail = telegram
    confirm = confirm_button(sent)
    fail["receipt"] = True
    with pytest.raises(bot.TelegramError):
        callback(confirm)
    assert bot._load_state("123")["receipt_pending"] is True
    fail["receipt"] = False
    callback(confirm)
    receipt = next(item["text"] for item in reversed(sent) if "Agendamento confirmado" in item["text"])
    code = receipt.split("pelo site: ")[1].splitlines()[0]
    assert get_customer_appointment("16999990000", code)["status"] == "confirmed"
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 1
        assert code not in conn.execute("SELECT state_json FROM telegram_sessions").fetchone()[0]
    assert bot._load_state("123")["receipt_pending"] is False


def test_private_codes_are_masked_before_model_and_history_storage(telegram, monkeypatch):
    _, sent, _ = telegram
    private_code = "BI-abcdefghijklmnopqrstuvwx"
    captured = []

    def model(messages, **kwargs):
        captured.append((json.dumps(messages), kwargs))
        return {"content": "Seu código: " + private_code}

    monkeypatch.setattr(bot, "run_agent", model)
    bot._save_state("123", {"step": "chat", "history": [{"role": "user", "content": private_code}]})
    text("Consulte " + private_code)
    assert private_code not in captured[0][0]
    assert captured[0][1]["session_id"] == "telegram:123"
    assert private_code not in json.dumps(bot._load_state("123"))
    assert private_code not in sent[-1]["text"]
    with db.connect() as conn:
        event = conn.execute("SELECT session_id,user_message,channel FROM interaction_logs").fetchone()
    assert event["session_id"] == "telegram:123"
    assert event["channel"] == "telegram"
    assert private_code not in event["user_message"]


def test_cancelar_interrupts_flow_without_cancelling_existing_reservation(telegram):
    _, sent, _ = telegram
    callback(confirm_button(sent))
    text("/cancelar")
    with db.connect() as conn:
        assert conn.execute("SELECT status FROM appointments").fetchone()[0] == "confirmed"
    assert "interrompi" in sent[-1]["text"]


def test_no_slots_keeps_choices_and_offers_another_day_or_barber(telegram):
    _, sent, _ = telegram
    day, _ = available_day()
    start = datetime.combine(day, datetime.min.time(), tzinfo=bot.TZ)
    with db.connect() as conn:
        conn.executemany("INSERT INTO schedule_blocks(barber_id,starts_at,ends_at,reason) VALUES (?,?,?,?)",
                         [(i, start.isoformat(), (start + timedelta(days=1)).isoformat(), "Indisponível")
                          for i in (1, 2, 3)])
    text("/agendar")
    callback(button(sent, "service:1"))
    callback(button(sent, f"day:{day.isoformat()}"))
    callback(button(sent, "barber:1"))
    state = bot._load_state("123")
    assert state["service_id"] == 1
    assert state["date"] == day.isoformat()
    callback(button(sent, "change_barber"))
    assert bot._load_state("123")["step"] == "barber"
    callback(button(sent, "barber:0"))
    callback(button(sent, "change_day"))
    assert bot._load_state("123")["step"] == "date"


def test_invalid_callbacks_do_not_crash_or_change_selection(telegram):
    _, sent, _ = telegram
    text("/agendar")
    original = button(sent, "service:1")
    callback(original.rsplit("service:", 1)[0] + "service:not-an-integer")
    assert bot._load_state("123")["step"] == "service"


def test_human_support_collects_contact_outside_model_history(telegram, monkeypatch):
    _, sent, _ = telegram

    def unexpected_model(*args, **kwargs):
        raise AssertionError("Support contact must never reach the model")

    monkeypatch.setattr(bot, "run_agent", unexpected_model)
    text("/atendente")
    text("Ana Silva")
    text("16999990000")
    with db.connect() as conn:
        row = conn.execute("SELECT session_id,channel,customer_name,customer_phone FROM support_tickets").fetchone()
    assert dict(row) == {"session_id": "telegram:123", "channel": "telegram",
                         "customer_name": "Ana Silva", "customer_phone": "16999990000"}
    assert "16999990000" not in json.dumps(bot._load_state("123"))
    assert "protocolo" in sent[-1]["text"].lower()


def test_completion_save_failure_retries_the_same_booking_request(telegram, monkeypatch):
    _, sent, _ = telegram
    confirm = confirm_button(sent)
    save = bot._save_state
    fail_once = {"enabled": True}

    def interrupted_save(chat_id, state):
        if state.get("step") == "completed" and fail_once["enabled"]:
            fail_once["enabled"] = False
            raise RuntimeError("Interrupted after booking commit")
        save(chat_id, state)

    monkeypatch.setattr(bot, "_save_state", interrupted_save)
    with pytest.raises(RuntimeError):
        callback(confirm)
    callback(confirm)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 1
    assert any("Agendamento confirmado" in item["text"] for item in sent)


def test_pending_receipt_survives_session_expiry_and_new_command(telegram):
    _, sent, fail = telegram
    confirm = confirm_button(sent)
    fail["receipt"] = True
    with pytest.raises(bot.TelegramError):
        callback(confirm)
    with db.connect() as conn:
        conn.execute("UPDATE telegram_sessions SET updated_at=datetime('now','-2 hours')")
    fail["receipt"] = False
    text("/agendar")
    assert any("Agendamento confirmado" in item["text"] for item in sent)
    text("/comprovante")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 1


def test_interrupted_completion_is_recovered_before_starting_another_flow(telegram, monkeypatch):
    _, sent, _ = telegram
    confirm = confirm_button(sent)
    save = bot._save_state

    def interrupted_save(chat_id, state):
        if state.get("step") == "completed":
            raise RuntimeError("Interrupted after booking commit")
        save(chat_id, state)

    monkeypatch.setattr(bot, "_save_state", interrupted_save)
    with pytest.raises(RuntimeError):
        callback(confirm)
    monkeypatch.setattr(bot, "_save_state", save)
    text("/agendar")
    assert any("Agendamento confirmado" in item["text"] for item in sent)
    assert bot._load_state("123")["step"] == "service"
    assert bot._load_state("123")["appointment_id"] == 1


def test_loading_legacy_completion_removes_raw_private_code_from_database(telegram):
    private_code = "BI-abcdefghijklmnopqrstuvwx"
    with db.connect() as conn:
        conn.execute("INSERT INTO telegram_sessions(chat_id,state_json) VALUES (?,?)",
                     ("123", json.dumps({"step": "completed", "appointment_id": 1, "access_code": private_code})))
    assert "access_code" not in bot._load_state("123")
    with db.connect() as conn:
        assert private_code not in conn.execute("SELECT state_json FROM telegram_sessions").fetchone()[0]


def test_reissued_receipt_shows_reservation_cancelled_on_site(telegram):
    _, sent, _ = telegram
    callback(confirm_button(sent))
    cancel_appointment(1, "16999990000", True)
    text("/comprovante")
    assert "cancelado" in sent[-1]["text"].lower()
    assert "Agendamento confirmado" not in sent[-1]["text"]


def test_receipt_retry_reconciles_booking_event_after_telemetry_failure(telegram, monkeypatch):
    _, sent, _ = telegram
    confirm = confirm_button(sent)
    record = bot.record_booking

    def interrupted_record(*args, **kwargs):
        raise RuntimeError("Interrupted before event commit")

    monkeypatch.setattr(bot, "record_booking", interrupted_record)
    with pytest.raises(RuntimeError, match="event commit"):
        callback(confirm)
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM interaction_logs WHERE outcome='booking_confirmed'").fetchone()[0] == 0
    monkeypatch.setattr(bot, "record_booking", record)
    text("/comprovante")
    text("/comprovante")
    with db.connect() as conn:
        events = conn.execute("SELECT session_id,channel,user_message FROM interaction_logs WHERE outcome='booking_confirmed'").fetchall()
        assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 1
    assert [dict(event) for event in events] == [{"session_id": "telegram:123", "channel": "telegram", "user_message": "Reserva #1"}]
    assert "Agendamento confirmado" in sent[-1]["text"]


def test_explicit_handoff_records_one_escalation_per_active_ticket(telegram):
    text("/atendente")
    text("/atendente")
    text("Ana Silva")
    text("16999990000")
    text("/atendente")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM support_tickets").fetchone()[0] == 1
        events = conn.execute("SELECT session_id,channel,user_message,intent FROM interaction_logs WHERE outcome='escalated'").fetchall()
    assert [dict(event) for event in events] == [{"session_id": "telegram:123", "channel": "telegram",
                                                "user_message": "Atendimento #1", "intent": "atendimento_humano"}]


def test_support_status_survives_clear_and_expiry_and_tracks_staff_updates(telegram):
    from barber_bot.staff import StaffMember
    from barber_bot.support import update_staff_request

    _, sent, _ = telegram
    text("/atendente")
    text("Ana Silva")
    text("16999990000")
    bot._clear_state("123")
    text("/atendimento")
    assert "#1" in sent[-1]["text"] and "Aberto" in sent[-1]["text"]
    owner = StaffMember("dono", "owner", None, "Dono")
    update_staff_request(1, owner, claim=True, note="Nota privada")
    bot._save_state("123", {"step": "chat", "history": []})
    with db.connect() as conn:
        conn.execute("UPDATE telegram_sessions SET updated_at=datetime('now','-2 hours')")
    text("/atendimento")
    assert "Em andamento" in sent[-1]["text"]
    update_staff_request(1, owner, status="resolved")
    text("/atendimento")
    assert "Resolvido" in sent[-1]["text"]
    assert "Ana Silva" not in sent[-1]["text"] and "16999990000" not in sent[-1]["text"]
    assert "Nota privada" not in sent[-1]["text"] and "dono" not in sent[-1]["text"]
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM support_tickets").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM interaction_logs").fetchone()[0] == 1


@pytest.mark.parametrize("step", ["service", "name", "confirm", "support_name", "support_phone", "completed"])
def test_support_status_preserves_current_flow_and_does_not_dispatch_pending_receipt(telegram, monkeypatch, step):
    from barber_bot.support import create_support_request

    _, sent, _ = telegram
    create_support_request("telegram:123", "telegram", "Ajuda")
    state = {"step": step, "callback_token": "same-token", "receipt_pending": True,
             "customer_name": "Ana", "ticket_id": 1}
    bot._save_state("123", state)
    with db.connect() as conn:
        before = tuple(conn.execute("SELECT state_json,updated_at FROM telegram_sessions WHERE chat_id='123'").fetchone())
    def unexpected_receipt(*args, **kwargs):
        raise AssertionError("Status lookup must not send a booking receipt")
    monkeypatch.setattr(bot, "_deliver_receipt", unexpected_receipt)
    text("/atendimento@ImperialBot")
    assert "#1" in sent[-1]["text"]
    with db.connect() as conn:
        assert tuple(conn.execute("SELECT state_json,updated_at FROM telegram_sessions WHERE chat_id='123'").fetchone()) == before


def test_support_status_does_not_expose_other_chat_or_channel_or_create_ticket(telegram):
    from barber_bot.support import create_support_request

    _, sent, _ = telegram
    create_support_request("telegram:456", "telegram", "Outra pessoa")
    create_support_request("telegram:123", "web", "Outro canal")
    text("/atendimento")
    assert "Nenhuma solicitação" in sent[-1]["text"]
    assert "#1" not in sent[-1]["text"] and "#2" not in sent[-1]["text"]
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM support_tickets").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM interaction_logs").fetchone()[0] == 0


def test_support_status_command_is_registered_and_described_in_help(telegram, monkeypatch):
    _, sent, _ = telegram
    text("/help")
    assert "/atendimento" in sent[-1]["text"]
    registered = []
    def api(method, payload=None, **kwargs):
        if method == "getMe":
            return {"username": "offline-test"}
        if method == "setMyCommands":
            registered.extend(payload["commands"])
            return True
        if method == "getUpdates":
            raise KeyboardInterrupt
        raise AssertionError(f"Unexpected Telegram method: {method}")
    monkeypatch.setattr(bot, "TELEGRAM_BOT_TOKEN", "offline-token")
    monkeypatch.setattr(bot, "telegram_call", api)
    bot.main()
    assert any(command["command"] == "atendimento" for command in registered)
