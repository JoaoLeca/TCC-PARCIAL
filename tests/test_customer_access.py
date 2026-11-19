from datetime import datetime, timedelta
from hashlib import sha256
import json
import sqlite3

import pytest

from barber_bot.db import connect, initialize_database
from barber_bot.schedule import BookingAccessError, BookingError, TZ, availability, create_appointment


WEB_OWNER = "a" * 43
OTHER_OWNER = "b" * 43


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "ownership.db"
    initialize_database(path)
    return path


def book(path, *, channel="web", key=WEB_OWNER, request_key=None, phone="16999990042"):
    for offset in range(1, 4):
        slots = availability(1, datetime.now(TZ).date() + timedelta(days=offset), path=path)
        if slots:
            slot = slots[0]
            return create_appointment("Ana", phone, 1, slot["barber_id"], slot["starts_at"], True,
                                      path, owner_channel=channel, owner_key=key, request_key=request_key)
    raise AssertionError("No available sample slot")


def test_owner_can_list_only_its_own_bookings_even_with_duplicate_identification(db):
    from barber_bot.customer_access import list_owned_bookings, require_owned_booking, public_appointment

    own = book(db)
    other = book(db, key=OTHER_OWNER, phone="11988880042")
    assert own["identification_code"] == other["identification_code"] == "0042"
    records = list_owned_bookings("web", WEB_OWNER, db)
    assert [record["id"] for record in records] == [own["id"]]
    assert records[0]["customer_phone"] == "16999990042"
    public = public_appointment(records[0])
    assert set(public) == {"id", "service_id", "barber_id", "service", "barber", "starts_at", "ends_at", "price", "status", "identification_code"}
    assert public["identification_code"] == "0042"
    with pytest.raises(BookingAccessError):
        require_owned_booking(other["id"], "web", WEB_OWNER, db)
    with pytest.raises(BookingAccessError):
        require_owned_booking(own["id"], "web", "0042", db)


def test_channels_are_isolated_even_for_identical_owner_key(db):
    from barber_bot.customer_access import list_owned_bookings, require_owned_booking

    own = book(db)
    telegram = book(db, channel="telegram", key=WEB_OWNER)
    assert [item["id"] for item in list_owned_bookings("telegram", WEB_OWNER, db)] == [telegram["id"]]
    with pytest.raises(BookingAccessError):
        require_owned_booking(own["id"], "telegram", WEB_OWNER, db)


@pytest.mark.parametrize("channel,key", [(None, None), ("web", None), ("web", ""), ("email", WEB_OWNER)])
def test_missing_or_invalid_owner_is_rejected_without_disclosing_booking(db, channel, key):
    from barber_bot.customer_access import list_owned_bookings, require_owned_booking

    own = book(db)
    with pytest.raises(BookingAccessError) as existing:
        require_owned_booking(own["id"], channel, key, db)
    with pytest.raises(BookingAccessError) as missing:
        require_owned_booking(9999, channel, key, db)
    assert str(existing.value) == str(missing.value)
    with pytest.raises(BookingAccessError):
        list_owned_bookings(channel, key, db)


def test_booking_owner_is_hashed_and_bound_atomically_with_idempotency(db):
    from barber_bot.customer_access import require_owned_booking

    own = book(db, request_key="confirmed-once")
    args = ("Ana", own["customer_phone"], own["service_id"], own["barber_id"], own["starts_at"], True, db)
    replay = create_appointment(*args, owner_channel="web", owner_key=WEB_OWNER, request_key="confirmed-once")
    assert replay["id"] == own["id"]
    for channel, key in [("web", OTHER_OWNER), ("telegram", WEB_OWNER), (None, None)]:
        with pytest.raises(BookingError):
            create_appointment(*args, owner_channel=channel, owner_key=key, request_key="confirmed-once")
    with connect(db) as conn:
        owner = conn.execute("SELECT * FROM booking_owners").fetchone()
        assert owner["owner_hash"] == sha256(WEB_OWNER.encode()).hexdigest()
        assert owner["channel"] == "web"
        assert conn.execute("SELECT count(*) FROM appointments").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM booking_requests").fetchone()[0] == 1
    assert require_owned_booking(own["id"], "web", WEB_OWNER, db)["id"] == own["id"]


def test_owner_binding_failure_rolls_back_booking_and_request(db):
    with connect(db) as conn:
        conn.execute("CREATE TRIGGER reject_owner BEFORE INSERT ON booking_owners BEGIN SELECT RAISE(ABORT, 'owner rejected'); END")
    with pytest.raises(sqlite3.IntegrityError, match="owner rejected"):
        book(db, request_key="atomic-request")
    with connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM appointments").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM booking_requests").fetchone()[0] == 0


def test_request_write_failure_rolls_back_booking_and_owner(db):
    with connect(db) as conn:
        conn.execute("CREATE TRIGGER reject_request BEFORE INSERT ON booking_requests BEGIN SELECT RAISE(ABORT, 'request rejected'); END")
    with pytest.raises(sqlite3.IntegrityError, match="request rejected"):
        book(db, request_key="atomic-request")
    with connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM appointments").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM booking_owners").fetchone()[0] == 0


@pytest.mark.parametrize("operation", ["cancel", "reschedule"])
def test_customer_mutations_share_callers_transaction_without_committing(db, operation):
    from barber_bot.customer_access import list_owned_bookings, require_owned_booking
    from barber_bot.schedule import cancel_appointment, reschedule_appointment

    own = book(db)
    with connect(db) as transaction:
        transaction.execute("BEGIN IMMEDIATE")
        if operation == "cancel":
            cancel_appointment(own["id"], own["customer_phone"], True, db, conn=transaction)
            assert require_owned_booking(own["id"], "web", WEB_OWNER, conn=transaction)["status"] == "cancelled"
            assert list_owned_bookings("web", WEB_OWNER, conn=transaction)[0]["status"] == "cancelled"
        else:
            new_start = (datetime.fromisoformat(own["starts_at"]) + timedelta(hours=2)).isoformat()
            reschedule_appointment(own["id"], own["customer_phone"], new_start, True, db, conn=transaction)
            assert require_owned_booking(own["id"], "web", WEB_OWNER, conn=transaction)["starts_at"] == new_start
        assert transaction.in_transaction
        transaction.rollback()
    restored = require_owned_booking(own["id"], "web", WEB_OWNER, db)
    assert restored["status"] == "confirmed"
    assert restored["starts_at"] == own["starts_at"]


def test_unowned_legacy_request_cannot_be_claimed_by_replay(db):
    from barber_bot.customer_access import require_owned_booking

    own = book(db, channel=None, key=None, request_key="old-request")
    args = ("Ana", own["customer_phone"], own["service_id"], own["barber_id"], own["starts_at"], True, db)
    # This is the fingerprint emitted by the previous application version.
    payload = ["Ana", "16999990042", 1, own["barber_id"], own["starts_at"], None, None]
    old_hash = sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
    with connect(db) as conn:
        conn.execute("UPDATE booking_requests SET payload_hash=?", (old_hash,))
    assert create_appointment(*args, request_key="old-request")["id"] == own["id"]
    with pytest.raises(BookingError):
        create_appointment(*args, request_key="old-request", owner_channel="web", owner_key=WEB_OWNER)
    with pytest.raises(BookingAccessError):
        require_owned_booking(own["id"], "web", WEB_OWNER, db)


def test_existing_private_codes_keep_working_but_new_bookings_cannot_issue_one(db, monkeypatch):
    import barber_bot.schedule as schedule

    monkeypatch.setattr(schedule, "connect", lambda path=None: connect(db))
    own = book(db, channel=None, key=None)
    assert schedule.replace_access_code(own["id"], None) is None
    code = "BI-manually-seeded-existing-secret"
    with connect(db) as conn:
        conn.execute("INSERT INTO appointment_access VALUES (?, ?)", (own["id"], sha256(code.encode()).hexdigest()))
    assert schedule.get_customer_appointment("(16) 99999-0042", code)["id"] == own["id"]
    replacement = schedule.replace_access_code(own["id"], None)
    assert replacement.startswith("BI-")
    with pytest.raises(BookingAccessError):
        schedule.get_customer_appointment("16999990042", code)
    assert schedule.get_customer_appointment("16999990042", replacement)["id"] == own["id"]
