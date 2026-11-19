"""Booking ownership credentials, independent of visible phone identification.

Web callers supply an unguessable browser secret. Telegram callers pass the
trusted chat ID received from Telegram, never a value supplied by public HTTP.
"""

from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path

from barber_bot.db import connect, phone_key
from barber_bot.schedule import BookingAccessError


_ACCESS_ERROR = "Não foi possível acessar esse agendamento nesta conversa. Fale com a equipe."
_BOOKING_QUERY = """
    SELECT a.*, b.name AS barber, s.name AS service,
           COALESCE(a.price_at_booking,s.price) AS price
    FROM appointments a JOIN barbers b ON b.id=a.barber_id
    JOIN services s ON s.id=a.service_id
    WHERE (EXISTS (SELECT 1 FROM booking_owners o WHERE o.appointment_id=a.id AND o.channel=? AND o.owner_hash=?)
        OR EXISTS (SELECT 1 FROM booking_access_grants g WHERE g.appointment_id=a.id AND g.channel=?
                   AND g.owner_hash=? AND g.expires_at>CAST(strftime('%s','now') AS INTEGER)))
"""


def owner_digest(channel: str, owner_key: str) -> str:
    """Validate an ownership credential and return its non-reversible digest."""
    if channel not in ("web", "telegram") or not isinstance(owner_key, str) or not owner_key.strip():
        raise BookingAccessError(_ACCESS_ERROR)
    if channel == "web" and len(owner_key) < 32:
        raise BookingAccessError(_ACCESS_ERROR)
    return sha256(owner_key.encode("utf-8")).hexdigest()


def identification_code(customer_phone: str) -> str:
    """Return a display label only; this suffix never grants booking access."""
    return "".join(char for char in phone_key(customer_phone) if char in "0123456789")[-4:]


def _record(row) -> dict:
    record = dict(row)
    record["identification_code"] = identification_code(record["customer_phone"])
    return record


def list_owned_bookings(channel: str, owner_key: str, path: str | Path | None = None, *, conn=None) -> list[dict]:
    """Return trusted internal records for this owner, including contact details."""
    digest = owner_digest(channel, owner_key)
    with nullcontext(conn) if conn is not None else connect(path) as conn:
        return [_record(row) for row in conn.execute(_BOOKING_QUERY + " ORDER BY a.starts_at, a.id", (channel, digest, channel, digest))]


def require_owned_booking(appointment_id: int, channel: str, owner_key: str,
                          path: str | Path | None = None, *, conn=None) -> dict:
    """Authorize one booking without revealing whether another owner's ID exists."""
    digest = owner_digest(channel, owner_key)
    with nullcontext(conn) if conn is not None else connect(path) as conn:
        row = conn.execute(_BOOKING_QUERY + " AND a.id=?", (channel, digest, channel, digest, appointment_id)).fetchone()
    if row is None:
        raise BookingAccessError(_ACCESS_ERROR)
    return _record(row)


def public_appointment(record: dict) -> dict:
    """Strip contact data, ownership credentials, and any legacy private code."""
    fields = ("id", "service_id", "barber_id", "service", "barber", "starts_at", "ends_at", "price", "status")
    public = {field: record[field] for field in fields}
    public["identification_code"] = (identification_code(record["customer_phone"])
                                     if "customer_phone" in record else record["identification_code"])
    return public
