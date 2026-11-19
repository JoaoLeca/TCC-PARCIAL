"""Reservation changes require ownership and a separate final confirmation."""
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest

from barber_bot import db
from barber_bot.schedule import TZ, availability, create_appointment


OWNER = "web-browser-secret-" + "a" * 32
OTHER = "web-browser-secret-" + "b" * 32


@pytest.fixture
def booking_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "changes.db")
    db.initialize_database()
    for offset in range(1, 7):
        day = datetime.now(TZ).date() + timedelta(days=offset)
        slots = availability(1, day)
        if slots:
            slot = slots[0]
            break
    booking = create_appointment("Ana", "16999991234", 1, slot["barber_id"], slot["starts_at"], True,
                                 owner_channel="web", owner_key=OWNER)
    return booking


def ask(text, owner=OWNER, channel="web"):
    from barber_bot.booking_conversation import handle_booking_message
    return handle_booking_message([{"role": "user", "content": text}], channel, owner)


def stored(booking):
    with db.connect() as conn:
        return dict(conn.execute("SELECT * FROM appointments WHERE id=?", (booking["id"],)).fetchone())


def begin_reschedule(booking):
    day = datetime.fromisoformat(booking["starts_at"]).date()
    answer = ask(f"quero remarcar reserva #{booking['id']} para {day.isoformat()} à tarde")
    assert "opções" in answer["content"]
    assert "slots" not in answer
    return ask("1")


def test_booking_conversation_has_a_trusted_entrypoint():
    assert importlib.util.find_spec("barber_bot.booking_conversation") is not None


def test_cancel_only_after_a_separate_final_yes(booking_db):
    booking = booking_db
    answer = ask("quero cancelar meu horário")
    assert "Confirma o cancelamento?" in answer["content"]
    assert f"#{booking['id']}" in answer["content"]
    assert "slots" not in answer and "service" not in answer
    assert stored(booking)["status"] == "confirmed"
    answer = ask("sim")
    assert answer["outcome"] == "completed"
    assert stored(booking)["status"] == "cancelled"
    assert ask("sim") is None


def test_cancel_no_and_wrong_session_cannot_mutate(booking_db):
    ask("quero cancelar")
    assert ask("sim", owner=OTHER) is None
    assert stored(booking_db)["status"] == "confirmed"
    answer = ask("não")
    assert answer["outcome"] == "aborted"
    assert ask("sim") is None
    assert stored(booking_db)["status"] == "confirmed"


def test_consultation_is_owned_and_never_uses_shared_phone_suffix(booking_db):
    day = datetime.fromisoformat(booking_db["starts_at"]).date()
    slot = next(slot for slot in availability(1, day) if slot["starts_at"] != booking_db["starts_at"])
    other = create_appointment("Bia", "11988881234", 1, slot["barber_id"], slot["starts_at"], True,
                               owner_channel="web", owner_key=OTHER)
    answer = ask("consultar minhas reservas 1234")
    assert f"#{booking_db['id']}" in answer["content"]
    assert f"#{other['id']}" not in answer["content"]
    assert "16999991234" not in answer["content"]
    answer = ask(f"quero cancelar reserva #{other['id']}")
    assert "Qual reserva" in answer["content"]
    assert ask("sim")["outcome"] != "completed"
    assert stored(other)["status"] == "confirmed"


def test_multiple_owned_reservations_require_explicit_choice(booking_db):
    day = datetime.fromisoformat(booking_db["starts_at"]).date()
    slot = next(slot for slot in availability(1, day, booking_db["barber_id"]) if datetime.fromisoformat(slot["starts_at"]).hour >= 14)
    second = create_appointment("Ana", "16999991234", 1, slot["barber_id"], slot["starts_at"], True,
                                owner_channel="web", owner_key=OWNER)
    assert "Qual reserva" in ask("quero cancelar, identificador 1234")["content"]
    assert "Confirma" not in ask("1234")["content"]
    answer = ask("2")
    assert f"#{second['id']}" in answer["content"]
    ask("sim")
    assert stored(second)["status"] == "cancelled"
    assert stored(booking_db)["status"] == "confirmed"


def test_reschedule_options_and_selection_do_not_change_reservation(booking_db):
    answer = begin_reschedule(booking_db)
    assert "Reserva atual" in answer["content"]
    assert "Novo horário" in answer["content"]
    assert "Confirma a remarcação?" in answer["content"]
    assert stored(booking_db)["starts_at"] == booking_db["starts_at"]
    answer = ask("sim")
    assert answer["outcome"] == "completed"
    assert stored(booking_db)["starts_at"] != booking_db["starts_at"]
    assert stored(booking_db)["price_at_booking"] == booking_db["price_at_booking"]


def test_written_time_selects_only_real_offered_options(booking_db):
    day = datetime.fromisoformat(booking_db["starts_at"]).date()
    ask(f"quero remarcar para {day.isoformat()} à tarde")
    with db.connect() as conn:
        state = json.loads(conn.execute("SELECT state_json FROM booking_conversations").fetchone()[0])
    chosen = state["options"][1]
    answer = ask(datetime.fromisoformat(chosen["starts_at"]).strftime("%H:%M"))
    assert "Confirma a remarcação?" in answer["content"]
    ask("sim")
    assert stored(booking_db)["starts_at"] == chosen["starts_at"]


def test_cancel_response_aborts_rescheduling_without_cancelling_booking(booking_db):
    begin_reschedule(booking_db)
    answer = ask("cancelar")
    assert answer["outcome"] == "aborted"
    assert ask("sim") is None
    assert stored(booking_db)["status"] == "confirmed"
    assert stored(booking_db)["starts_at"] == booking_db["starts_at"]


def test_expired_final_confirmation_is_rejected(booking_db):
    ask("quero cancelar")
    with db.connect() as conn:
        conn.execute("UPDATE booking_conversations SET updated_at=?", ((datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(),))
    answer = ask("sim")
    assert "expirou" in answer["content"]
    assert stored(booking_db)["status"] == "confirmed"


def test_external_booking_change_invalidates_confirmation(booking_db):
    ask("quero cancelar")
    with db.connect() as conn:
        start = datetime.fromisoformat(booking_db["starts_at"]) + timedelta(minutes=30)
        conn.execute("UPDATE appointments SET starts_at=? WHERE id=?", (start.isoformat(), booking_db["id"]))
    answer = ask("sim")
    assert answer["outcome"] == "stale"
    assert stored(booking_db)["status"] == "confirmed"


def test_new_collision_is_checked_at_final_confirmation(booking_db):
    begin_reschedule(booking_db)
    with db.connect() as conn:
        state = json.loads(conn.execute("SELECT state_json FROM booking_conversations").fetchone()[0])
    choice = state["slot"]
    create_appointment("Bia", "11988885678", 1, choice["barber_id"], choice["starts_at"], True)
    answer = ask("sim")
    assert answer["outcome"] == "rejected"
    assert "ocupado" in answer["content"]
    assert stored(booking_db)["starts_at"] == booking_db["starts_at"]


def test_concurrent_yes_consumes_pending_proposal_once(booking_db):
    begin_reschedule(booking_db)
    with ThreadPoolExecutor(max_workers=2) as workers:
        answers = list(workers.map(lambda _: ask("sim"), range(2)))
    assert sum(bool(answer and answer["outcome"] == "completed") for answer in answers) == 1
    assert stored(booking_db)["starts_at"] != booking_db["starts_at"]


def test_rescheduling_asks_date_and_can_abort_before_offering(booking_db):
    answer = ask("quero alterar meu agendamento")
    assert "nova data" in answer["content"]
    assert stored(booking_db)["starts_at"] == booking_db["starts_at"]
    assert ask("deixa pra lá")["outcome"] == "aborted"


def test_unowned_and_informative_requests_are_safe(booking_db):
    answer = ask("meus horários", owner=OTHER)
    assert answer["needs_verification"] and "Confirme seu telefone pelo Telegram" in answer["content"]
    assert ask("qual a política de cancelamento?") is None
    assert ask("quero cancelar", owner=None)["outcome"] != "completed"
    assert stored(booking_db)["status"] == "confirmed"


def test_consultation_does_not_capture_an_unrelated_next_question(booking_db):
    ask("minhas reservas")
    assert ask("quanto custa corte?") is None


def test_temporal_request_survives_multiple_booking_selection(booking_db):
    day = datetime.fromisoformat(booking_db["starts_at"]).date()
    slot = next(slot for slot in availability(1, day, booking_db["barber_id"]) if datetime.fromisoformat(slot["starts_at"]).hour >= 14)
    create_appointment("Ana", "16999991234", 1, slot["barber_id"], slot["starts_at"], True,
                       owner_channel="web", owner_key=OWNER)
    assert "Qual reserva" in ask(f"quero remarcar para {day.isoformat()} à tarde")["content"]
    answer = ask("1")
    assert "opções" in answer["content"]
    with db.connect() as conn:
        state = json.loads(conn.execute("SELECT state_json FROM booking_conversations").fetchone()[0])
    assert state["options"]
    assert all(datetime.fromisoformat(slot["starts_at"]).hour >= 12 for slot in state["options"])


def test_initial_yes_in_request_is_not_a_final_confirmation(booking_db):
    answer = ask("sim, quero cancelar")
    assert "Confirma o cancelamento?" in answer["content"]
    assert stored(booking_db)["status"] == "confirmed"


def test_pending_confirmation_is_bound_to_channel(booking_db):
    ask("quero cancelar")
    assert ask("sim", channel="telegram") is None
    assert stored(booking_db)["status"] == "confirmed"


def test_minimum_notice_is_enforced_for_natural_language_cancel(booking_db):
    start = datetime.now(TZ) + timedelta(hours=2)
    with db.connect() as conn:
        conn.execute("UPDATE appointments SET starts_at=?,ends_at=? WHERE id=?",
                     (start.isoformat(), (start + timedelta(minutes=40)).isoformat(), booking_db["id"]))
    ask("quero cancelar")
    answer = ask("sim")
    assert answer["outcome"] == "rejected"
    assert "3 horas" in answer["content"]
    assert stored(booking_db)["status"] == "confirmed"


def test_new_schedule_block_is_checked_at_final_confirmation(booking_db):
    begin_reschedule(booking_db)
    with db.connect() as conn:
        state = json.loads(conn.execute("SELECT state_json FROM booking_conversations").fetchone()[0])
        choice = state["slot"]
        conn.execute("INSERT INTO schedule_blocks(barber_id,starts_at,ends_at,reason) VALUES (?,?,?,?)",
                     (choice["barber_id"], choice["starts_at"], choice["ends_at"], "Ausência"))
    answer = ask("sim")
    assert answer["outcome"] == "rejected"
    assert "bloqueado" in answer["content"]
    assert stored(booking_db)["starts_at"] == booking_db["starts_at"]
