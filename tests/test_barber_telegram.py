from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from barber_bot import db, telegram_bot as bot
from barber_bot import barber_telegram
from barber_bot.operations import update_barber_telegram_phone
from barber_bot.schedule import availability, create_appointment

TZ = ZoneInfo("America/Sao_Paulo")


@pytest.fixture
def telegram(tmp_path, monkeypatch):
    path = tmp_path / "telegram-barbers.db"
    monkeypatch.setattr(db, "DATABASE_PATH", path)
    db.initialize_database()
    sent = []

    def api(method, payload=None, **kwargs):
        if method == "sendMessage":
            sent.append(payload)
        return True

    monkeypatch.setattr(bot, "telegram_call", api)
    bot._recent_messages.clear()
    return path, sent


def message(text, *, chat_id=123, sender_id=123, contact=None):
    update = {"message": {"chat": {"id": chat_id, "type": "private"},
                           "from": {"id": sender_id}, "text": text}}
    if contact:
        update["message"]["contact"] = contact
    bot.handle_update(update)


def test_barber_account_requires_own_registered_contact(telegram):
    path, sent = telegram
    update_barber_telegram_phone(2, "16 98196-7722")

    message("/vincular")
    message("", contact={"user_id": 999, "phone_number": "+5516981967722"})
    assert "próprio contato" in sent[-1]["text"]

    message("", contact={"user_id": 123, "phone_number": "+5516981967722"})
    assert "Caetano" in sent[-1]["text"]
    with db.connect(path) as conn:
        assert conn.execute("SELECT telegram_chat_id FROM barbers WHERE id=2").fetchone()[0] == "123"


def test_barber_agenda_buttons_only_show_owned_bookings(telegram):
    path, sent = telegram
    update_barber_telegram_phone(2, "16981967722")
    barber_telegram.link_barber_telegram("123", "123", {"user_id": 123, "phone_number": "+5516981967722"})
    booking = None
    for offset in range(1, 8):
        day = datetime.now(TZ).date() + timedelta(days=offset)
        slots = availability(1, day, barber_id=2, path=path)
        if slots:
            slot = slots[0]
            booking = create_appointment("Cliente de teste", "16999990001", 1, 2,
                                         slot["starts_at"], True, path)
            break
    assert booking is not None

    message("🗓 Minha agenda")
    menu = sent[-1]["reply_markup"]["inline_keyboard"]
    callback_data = menu[0][0]["callback_data"]
    bot.handle_update({"callback_query": {"id": "q", "data": callback_data,
                       "message": {"chat": {"id": 123, "type": "private"}}}})
    assert "Cliente de teste" in sent[-1]["text"]
    assert "16999990001" not in sent[-1]["text"]

    message("🗓 Minha agenda", chat_id=456, sender_id=456)
    assert "não está vinculada" in sent[-1]["text"]


def test_new_booking_notice_goes_only_to_linked_barber(telegram, monkeypatch):
    path, sent = telegram
    update_barber_telegram_phone(2, "16981967722")
    barber_telegram.link_barber_telegram("123", "123", {"user_id": 123, "phone_number": "+5516981967722"})
    monkeypatch.setattr(barber_telegram, "TELEGRAM_BOT_TOKEN", "teste")
    slot = next(
        slot
        for offset in range(1, 8)
        for slot in availability(1, datetime.now(TZ).date() + timedelta(days=offset), barber_id=2, path=path)
    )
    booking = create_appointment("Cliente de teste", "16999990001", 1, 2, slot["starts_at"], True, path)

    assert barber_telegram.notify_barber(booking) is True
    assert sent[-1]["chat_id"] == "123"
    assert "Novo agendamento confirmado" in sent[-1]["text"]
    assert "Cliente de teste" in sent[-1]["text"]
    assert "16999990001" not in sent[-1]["text"]


def test_notice_failure_does_not_change_saved_appointment(telegram, monkeypatch):
    path, sent = telegram
    update_barber_telegram_phone(2, "16981967722")
    barber_telegram.link_barber_telegram("123", "123", {"user_id": 123, "phone_number": "+5516981967722"})
    monkeypatch.setattr(barber_telegram, "TELEGRAM_BOT_TOKEN", "teste")

    def fail(method, payload=None, **kwargs):
        raise bot.TelegramError("offline")

    monkeypatch.setattr(bot, "telegram_call", fail)
    slot = next(
        slot
        for offset in range(1, 8)
        for slot in availability(1, datetime.now(TZ).date() + timedelta(days=offset), barber_id=2, path=path)
    )
    booking = create_appointment("Cliente de teste", "16999990001", 1, 2, slot["starts_at"], True, path)

    assert barber_telegram.notify_barber(booking) is False
    with db.connect(path) as conn:
        assert conn.execute("SELECT status FROM appointments WHERE id=?", (booking["id"],)).fetchone()[0] == "confirmed"
