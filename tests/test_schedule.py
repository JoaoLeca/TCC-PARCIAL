from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from barber_bot.db import connect, initialize_database
from barber_bot.schedule import BookingError, create_appointment, availability, cancel_appointment, find_appointments, reschedule_appointment
from barber_bot.schedule import list_barbers

TZ = ZoneInfo("America/Sao_Paulo")


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.db"
    initialize_database(path)
    return path


def valid_start(day_offset=1, hour=10, minute=0):
    day = (datetime.now(TZ).date() + timedelta(days=day_offset))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ).isoformat()


def get_working_barber_date(path):
    for offset in (1, 2, 3):
        day = datetime.now(TZ).date() + timedelta(days=offset)
        slots = availability(1, day, path=path)
        if slots:
            return day, slots[0]
    raise AssertionError("No working sample date found")


def test_booking_succeeds_and_conflict_is_rejected(db):
    day, slot = get_working_barber_date(db)
    first = create_appointment("Ana", "16999990000", 1, slot["barber_id"], slot["starts_at"], True, db)
    assert first["status"] == "confirmed"
    with pytest.raises(BookingError, match="ocupado"):
        create_appointment("Bia", "16999990001", 1, slot["barber_id"], slot["starts_at"], True, db)


def test_overlapping_different_start_time_is_rejected(db):
    _, slot = get_working_barber_date(db)
    start = datetime.fromisoformat(slot["starts_at"])
    create_appointment("Ana", "16999990000", 2, slot["barber_id"], slot["starts_at"], True, db)
    overlapping = (start + timedelta(minutes=30)).isoformat()
    with pytest.raises(BookingError, match="ocupado"):
        create_appointment("Bia", "16999990001", 1, slot["barber_id"], overlapping, True, db)


def test_requires_explicit_confirmation(db):
    _, slot = get_working_barber_date(db)
    with pytest.raises(BookingError, match="confirmação explícita"):
        create_appointment("Ana", "16999990000", 1, slot["barber_id"], slot["starts_at"], False, db)


def test_service_cannot_cross_lunch_or_closing(db):
    _, slot = get_working_barber_date(db)
    day = datetime.fromisoformat(slot["starts_at"]).date()
    with pytest.raises(BookingError, match="caber no expediente"):
        create_appointment("Ana", "16999990000", 4, slot["barber_id"], datetime(day.year, day.month, day.day, 19, 0, tzinfo=TZ).isoformat(), True, db)


def test_cancel_unknown_booking_is_rejected(db):
    with pytest.raises(BookingError, match="não encontrado"):
        cancel_appointment(999, "16999990000", True, db)


def test_cancel_requires_explicit_confirmation(db):
    with pytest.raises(BookingError, match="confirmação explícita"):
        cancel_appointment(999, "16999990000", False, db)


def test_cancel_confirmed_booking(db):
    _, slot = get_working_barber_date(db)
    appointment = create_appointment("Ana", "16999990000", 1, slot["barber_id"], slot["starts_at"], True, db)
    result = cancel_appointment(appointment["id"], "16999990000", True, db)
    assert result["status"] == "cancelled"


def test_barber_weekday_schedule_is_enforced(db):
    assert {barber["name"]: barber["working_days"] for barber in list_barbers(db)} == {
        "Rafael": [1, 2, 3, 4, 5],
        "Diego": [0, 1, 3, 4, 5],
        "Alecio": [0, 1, 2, 3, 4],
    }


def test_sunday_is_closed_even_if_a_legacy_barber_schedule_includes_it(db):
    with connect(db) as conn:
        conn.execute("UPDATE barbers SET working_days='6' WHERE id=1")
    today = datetime.now(TZ).date()
    days_until_sunday = (6 - today.weekday()) % 7 or 7
    start = valid_start(day_offset=days_until_sunday)
    assert availability(1, datetime.fromisoformat(start).date(), path=db, days_range=False) == []
    with pytest.raises(BookingError, match="fechada aos domingos"):
        create_appointment("Ana", "16999990000", 1, 1, start, True, db, days_range=False)


def test_reschedule_keeps_old_booking_when_new_time_is_occupied(db):
    day, first_slot = get_working_barber_date(db)
    choices = [slot for slot in availability(1, day, first_slot["barber_id"], db) if slot["barber_id"] == first_slot["barber_id"]]
    original = create_appointment("Ana", "16999990000", 1, first_slot["barber_id"], choices[0]["starts_at"], True, db)
    occupied = create_appointment("Bia", "16999990001", 1, first_slot["barber_id"], choices[2]["starts_at"], True, db)
    with pytest.raises(BookingError, match="ocupado"):
        reschedule_appointment(original["id"], "16999990000", occupied["starts_at"], True, db)
    records = find_appointments("16999990000", db)
    assert records[0]["starts_at"] == original["starts_at"]
    assert records[0]["status"] == "confirmed"


def test_reschedule_moves_booking_to_free_time(db):
    day, first_slot = get_working_barber_date(db)
    choices = [slot for slot in availability(1, day, first_slot["barber_id"], db) if slot["barber_id"] == first_slot["barber_id"]]
    original = create_appointment("Ana", "16999990000", 1, first_slot["barber_id"], choices[0]["starts_at"], True, db)
    result = reschedule_appointment(original["id"], "16999990000", choices[2]["starts_at"], True, db)
    assert result["id"] == original["id"]
    assert result["starts_at"] == choices[2]["starts_at"]
    assert result["status"] == "confirmed"
