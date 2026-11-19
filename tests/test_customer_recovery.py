from datetime import datetime, timedelta
import importlib.util

import pytest

from barber_bot import db
from barber_bot.schedule import TZ, availability, create_appointment

OWNER = "browser-original-" + "x" * 32
RECOVERED = "browser-recovered-" + "y" * 32


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "recovery.db")
    db.initialize_database()
    if importlib.util.find_spec("barber_bot.customer_recovery") is not None:
        import barber_bot.customer_recovery as module
        monkeypatch.setattr(module, "get_bot_username", lambda: "ImperialTesteBot")


def booking(phone="16999990042", owner=None):
    for offset in range(1, 8):
        slots = availability(1, datetime.now(TZ).date() + timedelta(days=offset))
        if slots:
            slot = slots[0]
            return create_appointment("Teste", phone, 1, slot["barber_id"], slot["starts_at"], True,
                                      owner_channel="web" if owner else None, owner_key=owner)


def test_recovery_module_exists():
    assert importlib.util.find_spec("barber_bot.customer_recovery") is not None


def test_verified_telegram_contact_recovers_legacy_and_owned_without_overwriting_owner(recovery):
    from barber_bot.customer_recovery import start_recovery, issue_telegram_code, verify_recovery
    from barber_bot.customer_access import list_owned_bookings
    first, second = booking(), booking("+55 (16) 99999-0042", OWNER)
    foreign = booking("11988880042")
    challenge = start_recovery("(16) 99999-0042", RECOVERED)
    assert challenge["telegram_url"].startswith("https://t.me/ImperialTesteBot?start=recover_")
    assert "code" not in challenge
    code = issue_telegram_code(challenge["challenge_id"], "123", "123", {"user_id": 123, "phone_number": "+5516999990042"})
    assert len(code) == 6 and code.isascii() and code.isdigit()
    assert not list_owned_bookings("web", RECOVERED)
    verify_recovery(challenge["challenge_id"], code, RECOVERED)
    assert {row["id"] for row in list_owned_bookings("web", RECOVERED)} == {first["id"], second["id"]}
    assert [row["id"] for row in list_owned_bookings("web", OWNER)] == [second["id"]]
    assert foreign["id"] not in {row["id"] for row in list_owned_bookings("web", RECOVERED)}
    with db.connect() as conn:
        dump = " ".join(str(tuple(row)) for row in conn.execute("SELECT * FROM customer_recovery"))
        assert code not in dump and challenge["challenge_id"] not in dump


def test_wrong_contacts_and_phone_suffix_never_authorize(recovery):
    from barber_bot.customer_recovery import RecoveryError, start_recovery, issue_telegram_code
    challenge = start_recovery("16999990042", RECOVERED)
    for contact in ({"user_id": 999, "phone_number": "+5516999990042"},
                    {"user_id": 123, "phone_number": "+5511988880042"},
                    {"phone_number": "+5516999990042"}):
        with pytest.raises(RecoveryError):
            issue_telegram_code(challenge["challenge_id"], "123", "123", contact)
    with pytest.raises(RecoveryError):
        start_recovery("0042", RECOVERED)


def test_code_is_bound_to_browser_expires_and_is_consumed_once(recovery):
    from barber_bot.customer_recovery import RecoveryError, start_recovery, issue_telegram_code, verify_recovery
    challenge = start_recovery("16999990042", RECOVERED)
    code = issue_telegram_code(challenge["challenge_id"], "123", "123", {"user_id": 123, "phone_number": "5516999990042"})
    with pytest.raises(RecoveryError):
        verify_recovery(challenge["challenge_id"], code, OWNER)
    verify_recovery(challenge["challenge_id"], code, RECOVERED)
    with pytest.raises(RecoveryError):
        verify_recovery(challenge["challenge_id"], code, RECOVERED)
    second = start_recovery("16999990042", OWNER)
    other_code = issue_telegram_code(second["challenge_id"], "123", "123", {"user_id": 123, "phone_number": "16999990042"})
    with db.connect() as conn:
        conn.execute("UPDATE customer_recovery SET code_expires_at=0")
    with pytest.raises(RecoveryError):
        verify_recovery(second["challenge_id"], other_code, OWNER)


def test_five_bad_attempts_lock_code_even_if_later_correct(recovery):
    from barber_bot.customer_recovery import RecoveryError, start_recovery, issue_telegram_code, verify_recovery
    challenge = start_recovery("16999990042", RECOVERED)
    code = issue_telegram_code(challenge["challenge_id"], "123", "123", {"user_id": 123, "phone_number": "16999990042"})
    bad = "111111" if code != "111111" else "222222"
    for _ in range(5):
        with pytest.raises(RecoveryError):
            verify_recovery(challenge["challenge_id"], bad, RECOVERED)
    with pytest.raises(RecoveryError):
        verify_recovery(challenge["challenge_id"], code, RECOVERED)


def test_telegram_own_contact_grants_access_but_typed_or_foreign_contact_does_not(recovery):
    from barber_bot.customer_recovery import RecoveryError, verify_telegram_contact
    from barber_bot.customer_access import list_owned_bookings
    record = booking()
    with pytest.raises(RecoveryError):
        verify_telegram_contact("123", "123", {"user_id": 999, "phone_number": "16999990042"})
    verify_telegram_contact("123", "123", {"user_id": 123, "phone_number": "5516999990042"})
    assert [row["id"] for row in list_owned_bookings("telegram", "123")] == [record["id"]]
    with db.connect() as conn:
        conn.execute("UPDATE booking_access_grants SET expires_at=0")
    assert not list_owned_bookings("telegram", "123")


def test_requests_are_limited_and_new_challenge_invalidates_old_one(recovery):
    from barber_bot.customer_recovery import RecoveryError, start_recovery, issue_telegram_code
    first = start_recovery("16999990042", RECOVERED)
    start_recovery("16999990042", RECOVERED)
    with pytest.raises(RecoveryError):
        issue_telegram_code(first["challenge_id"], "123", "123", {"user_id": 123, "phone_number": "16999990042"})
    start_recovery("16999990042", RECOVERED)
    with pytest.raises(RecoveryError) as exc:
        start_recovery("16999990042", RECOVERED)
    assert exc.value.status_code == 429


def test_unverified_requests_cannot_exhaust_another_phones_code_quota(recovery):
    from barber_bot.customer_recovery import start_recovery, issue_telegram_code
    for index in range(6):
        start_recovery("16999990042", f"unverified-browser-{index}-" + "x" * 32)
    legitimate = start_recovery("16999990042", RECOVERED)
    code = issue_telegram_code(legitimate["challenge_id"], "123", "123", {"user_id": 123, "phone_number": "16999990042"})
    assert len(code) == 6


def test_concurrent_code_consumption_grants_access_once(recovery):
    from concurrent.futures import ThreadPoolExecutor
    from barber_bot.customer_recovery import RecoveryError, start_recovery, issue_telegram_code, verify_recovery
    booking()
    challenge = start_recovery("16999990042", RECOVERED)
    code = issue_telegram_code(challenge["challenge_id"], "123", "123", {"user_id": 123, "phone_number": "16999990042"})
    def consume(_):
        try:
            verify_recovery(challenge["challenge_id"], code, RECOVERED)
            return True
        except RecoveryError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(consume, range(2))) == [False, True]
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM booking_access_grants").fetchone()[0] == 1


def test_missing_credentials_never_claim_a_code_was_sent(recovery, monkeypatch):
    import barber_bot.customer_recovery as module
    monkeypatch.undo()
    module.get_bot_username.cache_clear()
    monkeypatch.setattr(module, "TELEGRAM_BOT_TOKEN", "")
    with pytest.raises(module.RecoveryError) as exc:
        module.get_bot_username()
    assert exc.value.status_code == 503 and "não está configurada" in str(exc.value)
