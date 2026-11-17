from datetime import datetime, timedelta

import pytest

from barber_bot import db
from barber_bot.schedule import TZ


@pytest.fixture
def conversation_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "conversation.db")
    db.initialize_database()


def test_private_data_is_removed_before_model_and_history():
    from barber_bot.conversation import sanitize_chat_messages
    code = "BI-" + "A" * 24
    original = [{"role": "user", "content": f"{code} (16) 99999-0000 cliente@example.com"}]
    sanitized = sanitize_chat_messages(original)
    assert code not in sanitized[0]["content"]
    assert "99999" not in sanitized[0]["content"]
    assert "cliente@example.com" not in sanitized[0]["content"]
    assert code in original[0]["content"]


def test_guided_search_uses_context_and_afternoon(conversation_db):
    from barber_bot.availability_conversation import availability_answer
    day = datetime.now(TZ).date() + timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    answer = availability_answer([
        {"role": "user", "content": "Quero agendar corte"},
        {"role": "assistant", "content": "Qual dia?"},
        {"role": "user", "content": f"{day.isoformat()} à tarde"},
    ])
    assert 1 <= len(answer["slots"]) <= 3
    assert all(datetime.fromisoformat(slot["starts_at"]).hour >= 12 for slot in answer["slots"])
    assert all(slot["starts_at"].startswith(day.isoformat()) for slot in answer["slots"])
    assert "Qual serviço" not in answer["content"]


def test_unavailable_professional_offers_real_alternative(conversation_db):
    from barber_bot.availability_conversation import availability_answer
    day = datetime.now(TZ).date() + timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    with db.connect() as conn:
        conn.execute("UPDATE barbers SET working_days='6' WHERE name='Rafael'")
    answer = availability_answer([{"role": "user", "content": f"Quero corte com Rafael em {day.isoformat()}"}])
    assert answer["slots"]
    assert all(slot["barber"] != "Rafael" for slot in answer["slots"])
    assert "alternativ" in answer["content"].lower()


def test_no_api_key_still_answers_faq_and_availability(conversation_db, monkeypatch):
    from barber_bot import assistant
    monkeypatch.setattr(assistant, "OPENAI_API_KEY", "")
    result = assistant.run_agent([{"role": "user", "content": "Quais os serviços e preços?"}], session_id="offline-test")
    assert "R$ 50,00" in result["content"]
    assert result["mode"] == "guided"


def test_context_classifier_receives_recent_conversation(conversation_db, monkeypatch):
    from barber_bot import assistant
    from barber_bot.intent import IntentResult
    seen = []
    monkeypatch.setattr(assistant, "OPENAI_API_KEY", "test")
    def classify(text):
        seen.append(text)
        return IntentResult(intent="politicas", confidence=0.9)
    monkeypatch.setattr(assistant, "classify_intent", classify)
    monkeypatch.setattr(assistant, "build_agent", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    result = assistant.run_agent([
        {"role": "user", "content": "O que acontece se eu atrasar?"},
        {"role": "assistant", "content": "Há tolerância."},
        {"role": "user", "content": "E por meia hora?"},
    ])
    assert "atrasar" in seen[0]
    assert "meia hora" in seen[0]
    assert result["outcome"] == "unavailable"


def test_chat_offers_human_support_without_opening_a_ticket(conversation_db):
    from barber_bot.assistant import run_agent
    messages = [{"role": "user", "content": "Quero falar com um atendente"}]
    first = run_agent(messages, session_id="session-a")
    second = run_agent(messages, session_id="session-a")
    assert first["support_offer"] and second["support_offer"]
    assert first["outcome"] == "offered"
    assert "support_request" not in first
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM support_tickets").fetchone()[0] == 0


@pytest.fixture
def search_day(conversation_db):
    day = datetime.now(TZ).date() + timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day


def _search(day, preference, earlier=None):
    from barber_bot.availability_conversation import availability_answer
    messages = [{"role": "user", "content": f"Quero agendar corte em {day.isoformat()} {earlier or ''}"}]
    if preference:
        messages.append({"role": "user", "content": preference})
    return availability_answer(messages)


def _times(answer):
    return [datetime.fromisoformat(slot["starts_at"]).strftime("%H:%M") for slot in answer["slots"]]


@pytest.mark.parametrize(("preference", "expected"), [
    ("depois das 15h", ["15:30", "16:00", "16:30"]),
    ("depois das15h", ["15:30", "16:00", "16:30"]),
    ("a partir das 15h", ["15:00", "15:30", "16:00"]),
    ("a partir das15h", ["15:00", "15:30", "16:00"]),
    ("último horário antes das 15h", ["14:00", "13:30", "11:00"]),
    ("último horário antes das15h", ["14:00", "13:30", "11:00"]),
    ("entre 10h e 12h", ["10:00", "10:30", "11:00"]),
    ("entre10h e12h", ["10:00", "10:30", "11:00"]),
    ("às 15h", ["15:00"]),
    ("15:30", ["15:30"]),
    ("às 15:30", ["15:30"]),
    ("primeiro horário", ["08:00", "08:30", "09:00"]),
    ("último horário", ["19:00", "18:30", "18:00"]),
    ("último horário depois das 15h", ["19:00", "18:30", "18:00"]),
])
def test_guided_search_honors_precise_time_constraints(search_day, preference, expected):
    assert _times(_search(search_day, preference)) == expected


@pytest.mark.parametrize(("earlier", "latest", "expected"), [
    ("pela manhã", "depois das 15h", ["15:30", "16:00", "16:30"]),
    ("depois das 15h", "pela manhã", ["08:00", "08:30", "09:00"]),
    ("às 15h", "à tarde", ["13:30", "14:00", "14:30"]),
    ("à noite", "à tarde depois das 15h", ["15:30", "16:00", "16:30"]),
    ("às 15h", "com Rafael", ["15:00"]),
])
def test_new_temporal_preference_replaces_old_preference(search_day, earlier, latest, expected):
    assert _times(_search(search_day, latest, earlier)) == expected


@pytest.mark.parametrize("preference", [
    "às 25h", "às 15:70", "às 24h", "entre 16h e 10h", "entre 10h e 10h",
    "entre 10h", "por volta das 15h", "depois do almoço", "às 15h ou às 16h",
    "antes das quinze", "depois das 15:300", "às 15h pela manhã",
    "às quinze", "às 15h e meia", "às 15.30", "15h30 ou 16h30",
    "mais tarde", "às 15:30pm", "às 15h e 20 minutos",
])
def test_invalid_or_unsupported_time_is_not_silently_ignored(search_day, preference):
    answer = _search(search_day, preference)
    assert answer["slots"] == []
    assert "horário" in answer["content"].lower()


@pytest.mark.parametrize("earlier", ["pela manhã", "às 15h"])
@pytest.mark.parametrize("preference", [
    "Não posso depois das15h", "Não às15h, outro horário", "Não posso pela manhã",
    "sem ser às15h", "exceto às15h", "nem às15h", "não esse horário",
    "-15h", "às -15h", "-1h", "às −15h",
])
def test_negated_or_negative_time_requests_clarification_without_reusing_old_preference(search_day, earlier, preference):
    answer = _search(search_day, preference, earlier)
    assert answer["slots"] == []
    assert "Informe" in answer["content"]


def test_local_date_is_not_interpreted_as_a_clock(search_day):
    from barber_bot.availability_conversation import availability_answer
    answer = availability_answer([{"role": "user", "content": f"Quero corte em {search_day:%d/%m}"}])
    assert _times(answer) == ["08:00", "08:30", "09:00"]
    assert all(slot["starts_at"].startswith(search_day.isoformat()) for slot in answer["slots"])


@pytest.mark.parametrize("day_text", ["31/02", "2026-02-30"])
def test_invalid_date_is_rejected_without_slots(conversation_db, day_text):
    from barber_bot.availability_conversation import availability_answer
    answer = availability_answer([{"role": "user", "content": f"Quero corte em {day_text} às 15h"}])
    assert answer["slots"] == []
    assert "data válida" in answer["content"]


def test_latest_saturday_options_are_real_distinct_starts(conversation_db):
    from barber_bot.availability_conversation import availability_answer
    answer = availability_answer([{"role": "user", "content": "Quero corte no último horário de sábado"}])
    assert _times(answer) == ["19:00", "18:30", "18:00"]
    assert all(datetime.fromisoformat(slot["starts_at"]).weekday() == 5 for slot in answer["slots"])


def test_blocked_latest_slot_and_duration_are_respected(search_day):
    with db.connect() as conn:
        conn.execute("UPDATE services SET duration_minutes=120 WHERE id=1")
        for barber in conn.execute("SELECT id FROM barbers").fetchall():
            conn.execute("INSERT INTO schedule_blocks(barber_id,starts_at,ends_at,reason) VALUES (?,?,?,?)",
                         (barber["id"], f"{search_day}T18:00:00-03:00", f"{search_day}T20:00:00-03:00", "teste"))
    answer = _search(search_day, "último horário")
    assert _times(answer) == ["16:00", "15:30", "15:00"]
    assert answer["service"] == {"id": 1, "name": "Corte de Cabelo", "price": 50.0, "duration_minutes": 120}
    assert all(datetime.fromisoformat(slot["ends_at"]) - datetime.fromisoformat(slot["starts_at"]) == timedelta(hours=2)
               for slot in answer["slots"])


def test_alternative_professional_retains_time_constraint(search_day):
    with db.connect() as conn:
        conn.execute("UPDATE barbers SET working_days='6' WHERE name='Rafael'")
    answer = _search(search_day, "com Rafael depois das 15h")
    assert _times(answer) == ["15:30", "16:00", "16:30"]
    assert all(slot["barber"] != "Rafael" for slot in answer["slots"])


def test_alternative_day_retains_exact_hour(search_day):
    with db.connect() as conn:
        conn.execute("INSERT INTO schedule_blocks(barber_id,starts_at,ends_at,reason) "
                     "SELECT id,?,?,? FROM barbers",
                     (f"{search_day}T00:00:00-03:00", f"{search_day}T23:59:00-03:00", "teste"))
    answer = _search(search_day, "às 15h")
    assert _times(answer) == ["15:00"]
    assert all(not slot["starts_at"].startswith(search_day.isoformat()) for slot in answer["slots"])


def test_exact_hour_outside_real_grid_does_not_fabricate_slots(search_day):
    answer = _search(search_day, "às 15:15")
    assert answer["slots"] == []
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM appointments").fetchone()[0] == 0
