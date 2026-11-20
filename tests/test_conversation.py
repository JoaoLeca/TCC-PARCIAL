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


def test_no_api_key_disables_virtual_conversation(conversation_db, monkeypatch):
    from barber_bot import assistant
    monkeypatch.setattr(assistant, "OPENAI_API_KEY", "")
    result = assistant.run_agent([{"role": "user", "content": "Quais os serviços e preços?"}])
    assert result["content"] == "O atendimento virtual não está habilitado."
    assert result["mode"] == "unavailable"


def test_no_api_key_does_not_open_human_support_ticket(conversation_db, monkeypatch):
    from barber_bot import assistant
    monkeypatch.setattr(assistant, "OPENAI_API_KEY", "")
    messages = [{"role": "user", "content": "Quero falar com um atendente"}]
    result = assistant.run_agent(messages)
    assert result["mode"] == "unavailable"
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM support_tickets").fetchone()[0] == 0


@pytest.fixture
def search_day(conversation_db):
    day = datetime.now(TZ).date() + timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day


def _search(day, preference, earlier=None):
    from barber_bot.availability_conversation import _preference_slots, _three_options, _time_preference
    from barber_bot.conversation import normalize_text
    from barber_bot.schedule import availability, list_services
    current = _time_preference(normalize_text(preference))
    prior = _time_preference(normalize_text(earlier or ""))
    temporal_preference = current if current is not None else prior
    invalid = bool(temporal_preference and temporal_preference["invalid"])
    slots = [] if invalid else _preference_slots(availability(1, day), temporal_preference)
    choices = _three_options(slots, latest=bool(temporal_preference and temporal_preference["latest"]))
    return {
        "slots": choices,
        "service": list_services()[0],
        "content": "Informe um horário válido." if invalid else "Não encontrei horários para essa preferência." if not choices else "",
    }


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
def test_time_preference_filters_real_slots(search_day, preference, expected):
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
    answer = _search(search_day, "")
    assert _times(answer) == ["08:00", "08:30", "09:00"]
    assert all(slot["starts_at"].startswith(search_day.isoformat()) for slot in answer["slots"])


@pytest.mark.parametrize("day_text", ["31/02", "2026-02-30"])
def test_invalid_date_is_rejected_without_slots(conversation_db, day_text):
    from barber_bot.availability_conversation import _requested_day
    from barber_bot.conversation import normalize_text
    assert _requested_day(normalize_text(day_text), datetime.now(TZ).date()) == "invalid"


def test_latest_saturday_options_are_real_distinct_starts(conversation_db):
    from barber_bot.availability_conversation import _requested_day
    from barber_bot.conversation import normalize_text
    saturday = _requested_day(normalize_text("sábado"), datetime.now(TZ).date())
    answer = _search(saturday, "último horário")
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


def test_exact_hour_outside_real_grid_does_not_fabricate_slots(search_day):
    answer = _search(search_day, "às 15:15")
    assert answer["slots"] == []
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM appointments").fetchone()[0] == 0


def _block(barber_ids, first_day, last_day):
    with db.connect() as conn:
        for barber_id in barber_ids:
            conn.execute("INSERT INTO schedule_blocks(barber_id,starts_at,ends_at,reason) VALUES (?,?,?,?)",
                         (barber_id, f"{first_day}T00:00:00-03:00", f"{last_day + timedelta(days=1)}T00:00:00-03:00", "teste"))


def _working_barbers(day):
    from barber_bot.schedule import list_barbers
    return [barber["id"] for barber in list_barbers() if day.weekday() in barber["working_days"]]


def _minutes(slot):
    start = datetime.fromisoformat(slot["starts_at"])
    return start.hour * 60 + start.minute


@pytest.mark.parametrize(("preference", "expected"), [
    ("depois das 15h", ["15:30", "16:00", "16:30"]),
    ("a partir das 15h", ["15:00", "15:30", "16:00"]),
    ("à tarde", ["13:30", "14:00", "14:30"]),
    ("último horário", ["19:00", "18:30", "18:00"]),
    (None, ["08:00", "08:30", "09:00"]),
])
def test_agent_suggestions_follow_the_customer_time_preference(search_day, preference, expected):
    from barber_bot.assistant import availability_suggestions
    result = availability_suggestions(1, search_day, None, preference)
    assert _times({"slots": result["sugestoes"]}) == expected
    assert result["alternativa"] is None and result["observacao"] is None
    if preference == "depois das 15h":
        assert result["vagas"] and all(_minutes(slot) > 900 for slot in result["vagas"])


@pytest.mark.parametrize("preference", ["por volta das 15h", "às 25h", "entre 16h e 10h"])
def test_ambiguous_preference_returns_no_suggestions(search_day, preference):
    from barber_bot.assistant import availability_suggestions
    result = availability_suggestions(1, search_day, None, preference)
    assert result["vagas"] == [] and result["sugestoes"] == []
    assert "esclarecimento" in result["observacao"]


def test_unavailable_barber_gets_another_professional_on_the_same_day(search_day):
    from barber_bot.assistant import availability_suggestions
    barber_id = _working_barbers(search_day)[0]
    _block([barber_id], search_day, search_day)
    result = availability_suggestions(1, search_day, barber_id, "à tarde")
    assert result["alternativa"] == "outro profissional na mesma data"
    assert result["sugestoes"] and all(
        slot["barber_id"] != barber_id and slot["starts_at"].startswith(search_day.isoformat()) and 720 <= _minutes(slot) < 1080
        for slot in result["sugestoes"])


def test_full_day_moves_to_the_next_date_keeping_the_time_preference(search_day):
    from barber_bot.assistant import availability_suggestions
    _block(_working_barbers(search_day), search_day, search_day)
    result = availability_suggestions(1, search_day, None, "depois das 15h")
    assert result["alternativa"] == "outra data"
    assert result["sugestoes"] and all(
        slot["starts_at"][:10] > search_day.isoformat() and _minutes(slot) > 900 for slot in result["sugestoes"])


def test_full_window_returns_no_suggestions(search_day):
    from barber_bot.assistant import availability_suggestions
    from barber_bot.config import MAX_BOOKING_DAYS
    last_day = datetime.now(TZ).date() + timedelta(days=MAX_BOOKING_DAYS)
    with db.connect() as conn:
        barber_ids = [row["id"] for row in conn.execute("SELECT id FROM barbers")]
    _block(barber_ids, datetime.now(TZ).date(), last_day)
    result = availability_suggestions(1, search_day, None, None)
    assert result["vagas"] == [] and result["sugestoes"] == [] and result["alternativa"] is None
    assert "Não há vagas" in result["observacao"]


def test_agent_tool_buttons_use_the_filtered_suggestions(search_day, monkeypatch):
    import barber_bot.assistant as assistant
    captured = {}
    monkeypatch.setattr(assistant, "OPENAI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(assistant, "create_agent", lambda model, tools, system_prompt: captured.setdefault("tools", tools))
    runtime = {}
    assistant.build_agent("web", runtime=runtime)
    tool = next(item for item in captured["tools"] if item.name == "buscar_disponibilidade")
    tool.invoke({"servico_id": 1, "data": search_day.isoformat(), "preferencia_horario": "depois das 15h"})
    assert _times({"slots": runtime["booking_options"]["slots"]}) == ["15:30", "16:00", "16:30"]
    tool.invoke({"servico_id": 1, "data": search_day.isoformat(), "preferencia_horario": "por volta das 15h"})
    assert "booking_options" not in runtime
