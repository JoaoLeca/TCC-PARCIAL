from datetime import date, datetime, time, timedelta
from pathlib import Path
from barber_bot.config import MIN_BOOKING_DAYS, MAX_BOOKING_DAYS, SHOP_OPEN_DAYS, TIMEZONE
from barber_bot.db import connect, phone_key
from zoneinfo import ZoneInfo

TZ = ZoneInfo(TIMEZONE)


class BookingError(ValueError):
    pass


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _parse_start(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise BookingError("Informe a data e a hora no formato AAAA-MM-DDTHH:MM.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def list_services(path: str | Path | None = None) -> list[dict]:
    with connect(path) as conn:
        return [dict(row) for row in conn.execute("SELECT id,name,price,duration_minutes FROM services ORDER BY id")]


def list_barbers(path: str | Path | None = None) -> list[dict]:
    with connect(path) as conn:
        rows = conn.execute("SELECT id,name,working_days FROM barbers ORDER BY id").fetchall()
    return [{"id": r["id"], "name": r["name"],
             "working_days": [day for day in map(int, r["working_days"].split(",")) if day in SHOP_OPEN_DAYS]}
            for r in rows]


def read_windows(conn):
    row = conn.execute("SELECT open_1, close_1, open_2, close_2 FROM shop_profile WHERE id=1").fetchone()
    return tuple((time.fromisoformat(row[start]), time.fromisoformat(row[end]))
                 for start, end in (("open_1", "close_1"), ("open_2", "close_2")) if row[start] is not None)


def fits_hours(start: datetime, end: datetime, windows) -> bool:
    return start.date() == end.date() and any(opening <= start.time() < end.time() <= closing
                                              for opening, closing in windows)


def _validate_window(conn, start: datetime, duration: int, days_range: bool = True) -> datetime:
    now = datetime.now(TZ)
    if start <= now:
        raise BookingError("O horário escolhido precisa estar no futuro.")
    if start.weekday() not in SHOP_OPEN_DAYS:
        raise BookingError("A barbearia está fechada aos domingos.")
    if days_range:
        days_ahead = (start.date() - now.date()).days
        if days_ahead < MIN_BOOKING_DAYS or days_ahead > MAX_BOOKING_DAYS:
            raise BookingError(f"Os agendamentos devem ser feitos com {MIN_BOOKING_DAYS} a {MAX_BOOKING_DAYS} dias de antecedência.")
    end = start + timedelta(minutes=duration)
    if not fits_hours(start, end, read_windows(conn)):
        raise BookingError("O serviço precisa caber no expediente da barbearia.")
    return end


def _check_blocks(conn, barber_id, start, end):
    if conn.execute("SELECT 1 FROM schedule_blocks WHERE released_at IS NULL AND barber_id=? AND starts_at < ? AND ends_at > ?",
                    (barber_id, end.isoformat(), start.isoformat())).fetchone():
        raise BookingError("O período está bloqueado na agenda do barbeiro. Escolha outro horário.")


def availability(service_id: int, appointment_date: date, barber_id: int | None = None, path: str | Path | None = None, *, exclude_appointment_id: int | None = None, days_range: bool = True, duration_minutes: int | None = None) -> list[dict]:
    now = datetime.now(TZ)
    with connect(path) as conn:
        service = conn.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
        if not service:
            raise BookingError("Serviço não encontrado.")
        if appointment_date.weekday() not in SHOP_OPEN_DAYS:
            return []
        if days_range and not (MIN_BOOKING_DAYS <= (appointment_date - now.date()).days <= MAX_BOOKING_DAYS):
            return []
        duration = duration_minutes if duration_minutes is not None else service["duration_minutes"]
        query = "SELECT * FROM barbers" + (" WHERE id=?" if barber_id else "")
        barbers = conn.execute(query, (barber_id,) if barber_id else ()).fetchall()
        day_start = datetime.combine(appointment_date, time.min, tzinfo=TZ)
        bounds = ((day_start + timedelta(days=1)).isoformat(), day_start.isoformat())
        busy = conn.execute("""
            SELECT barber_id, starts_at, ends_at FROM appointments
            WHERE status='confirmed' AND starts_at < ? AND ends_at > ? AND id IS NOT ?
            UNION ALL
            SELECT barber_id, starts_at, ends_at FROM schedule_blocks
            WHERE released_at IS NULL AND starts_at < ? AND ends_at > ?
        """, (*bounds, exclude_appointment_id, *bounds)).fetchall()
        windows = read_windows(conn)
    result = []
    busy_by_barber: dict[int, list[tuple[datetime, datetime]]] = {}
    for item in busy:
        busy_by_barber.setdefault(item["barber_id"], []).append((_parse_start(item["starts_at"]), _parse_start(item["ends_at"])))
    for barber in barbers:
        if appointment_date.weekday() not in [int(x) for x in barber["working_days"].split(",")]:
            continue
        for opening, closing in windows:
            minute = _minutes(opening)
            while minute + duration <= _minutes(closing):
                candidate = datetime.combine(appointment_date, time(minute // 60, minute % 60), tzinfo=TZ)
                end = candidate + timedelta(minutes=duration)
                if candidate > now and all(end <= old_start or candidate >= old_end for old_start, old_end in busy_by_barber.get(barber["id"], [])):
                    result.append({"barber_id": barber["id"], "barber": barber["name"], "starts_at": candidate.isoformat(), "ends_at": end.isoformat()})
                minute += 30
    return result


def create_appointment(customer_name: str, customer_phone: str, service_id: int, barber_id: int, starts_at: str, confirmed: bool, path: str | Path | None = None, *, days_range: bool = True, expected_price: float | None = None, expected_duration: int | None = None) -> dict:
    if not confirmed:
        raise BookingError("Peça confirmação explícita da pessoa antes de gravar o agendamento.")
    name, phone = customer_name.strip(), customer_phone.strip()
    if not name or not phone:
        raise BookingError("Nome e telefone são necessários para concluir o agendamento.")
    start = _parse_start(starts_at)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        service = conn.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
        barber = conn.execute("SELECT * FROM barbers WHERE id=?", (barber_id,)).fetchone()
        if not service or not barber:
            raise BookingError("Serviço ou barbeiro não encontrado.")
        if (expected_price is not None and expected_price != service["price"]) or (
            expected_duration is not None and expected_duration != service["duration_minutes"]
        ):
            raise BookingError("O catálogo mudou. Revise o preço e a duração antes de confirmar novamente.")
        end = _validate_window(conn, start, service["duration_minutes"], days_range)
        if start.date().weekday() not in [int(x) for x in barber["working_days"].split(",")]:
            raise BookingError(f"{barber['name']} não atende nesse dia da semana.")
        overlap = conn.execute("SELECT 1 FROM appointments WHERE barber_id=? AND status='confirmed' AND starts_at < ? AND ends_at > ?", (barber_id, end.isoformat(), start.isoformat())).fetchone()
        if overlap:
            raise BookingError("Esse horário acabou de ser ocupado. Consulte a disponibilidade novamente.")
        _check_blocks(conn, barber_id, start, end)
        cursor = conn.execute("INSERT INTO appointments(customer_name,customer_phone,barber_id,service_id,starts_at,ends_at,price_at_booking) VALUES (?,?,?,?,?,?,?)", (name, phone, barber_id, service_id, start.isoformat(), end.isoformat(), service["price"]))
        row = conn.execute("SELECT a.*, b.name AS barber, s.name AS service, a.price_at_booking AS price FROM appointments a JOIN barbers b ON b.id=a.barber_id JOIN services s ON s.id=a.service_id WHERE a.id=?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def find_appointments(customer_phone: str, path: str | Path | None = None) -> list[dict]:
    with connect(path) as conn:
        rows = conn.execute("SELECT a.*,b.name AS barber,s.name AS service,COALESCE(a.price_at_booking,s.price) AS price FROM appointments a JOIN barbers b ON b.id=a.barber_id JOIN services s ON s.id=a.service_id WHERE phone_key(a.customer_phone)=? ORDER BY a.starts_at", (phone_key(customer_phone),)).fetchall()
    return [dict(r) for r in rows]


def cancel_appointment(appointment_id: int, customer_phone: str, confirmed: bool = False, path: str | Path | None = None, *, min_notice_hours: int = 3) -> dict:
    if not confirmed:
        raise BookingError("Peça confirmação explícita antes de cancelar o agendamento.")
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM appointments WHERE id=? AND phone_key(customer_phone)=? AND status='confirmed'", (appointment_id, phone_key(customer_phone))).fetchone()
        if not row:
            raise BookingError("Agendamento ativo não encontrado para esse telefone.")
        start = _parse_start(row["starts_at"])
        if start - datetime.now(TZ) < timedelta(hours=min_notice_hours):
            if not min_notice_hours:
                raise BookingError("Só é possível cancelar um horário futuro.")
            raise BookingError("O cancelamento precisa ser feito com pelo menos 3 horas de antecedência. Vou encaminhar para atendimento humano.")
        conn.execute("UPDATE appointments SET status='cancelled' WHERE id=?", (appointment_id,))
        return {"id": appointment_id, "status": "cancelled"}


def _reschedulable_booking(conn, appointment_id, customer_phone, min_notice_hours):
    booking = conn.execute(
        "SELECT * FROM appointments WHERE id=? AND phone_key(customer_phone)=? AND status='confirmed'",
        (appointment_id, phone_key(customer_phone)),
    ).fetchone()
    if not booking:
        raise BookingError("Agendamento ativo não encontrado para esse telefone.")
    if _parse_start(booking["starts_at"]) - datetime.now(TZ) < timedelta(hours=min_notice_hours):
        if not min_notice_hours:
            raise BookingError("Só é possível remarcar um horário futuro.")
        raise BookingError("A remarcação precisa ser feita com pelo menos 3 horas de antecedência.")
    return booking


def reschedule_options(appointment_id: int, customer_phone: str, appointment_date: date, path: str | Path | None = None, *, min_notice_hours: int = 3, days_range: bool = True) -> list[dict]:
    with connect(path) as conn:
        booking = _reschedulable_booking(conn, appointment_id, customer_phone, min_notice_hours)
    slots = availability(booking["service_id"], appointment_date, booking["barber_id"], path,
                         exclude_appointment_id=appointment_id, days_range=days_range,
                         duration_minutes=_booking_duration(booking))
    current_start = _parse_start(booking["starts_at"]).isoformat()
    return [slot for slot in slots if slot["starts_at"] != current_start]


def _booking_duration(booking) -> int:
    return (_parse_start(booking["ends_at"]) - _parse_start(booking["starts_at"])) // timedelta(minutes=1)


def reschedule_appointment(appointment_id: int, customer_phone: str, new_starts_at: str, confirmed: bool, path: str | Path | None = None, *, min_notice_hours: int = 3, days_range: bool = True) -> dict:
    if not confirmed:
        raise BookingError("Peça confirmação explícita da pessoa antes de remarcar.")
    start = _parse_start(new_starts_at)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = _reschedulable_booking(conn, appointment_id, customer_phone, min_notice_hours)
        barber = conn.execute("SELECT * FROM barbers WHERE id=?", (old["barber_id"],)).fetchone()
        end = _validate_window(conn, start, _booking_duration(old), days_range)
        if start.date().weekday() not in [int(x) for x in barber["working_days"].split(",")]:
            raise BookingError(f"{barber['name']} não atende nesse dia da semana.")
        overlap = conn.execute("SELECT 1 FROM appointments WHERE barber_id=? AND id<>? AND status='confirmed' AND starts_at < ? AND ends_at > ?", (old["barber_id"], appointment_id, end.isoformat(), start.isoformat())).fetchone()
        if overlap:
            raise BookingError("Esse horário acabou de ser ocupado. Consulte a disponibilidade novamente.")
        _check_blocks(conn, old["barber_id"], start, end)
        conn.execute("UPDATE appointments SET starts_at=?,ends_at=? WHERE id=?", (start.isoformat(), end.isoformat(), appointment_id))
        row = conn.execute("SELECT a.*,b.name AS barber,s.name AS service,COALESCE(a.price_at_booking,s.price) AS price FROM appointments a JOIN barbers b ON b.id=a.barber_id JOIN services s ON s.id=a.service_id WHERE a.id=?", (appointment_id,)).fetchone()
        return dict(row)

