from datetime import date, datetime, time, timedelta
from pathlib import Path
from barber_bot.config import MIN_BOOKING_DAYS, MAX_BOOKING_DAYS, TIMEZONE
from barber_bot.db import connect
from zoneinfo import ZoneInfo

TZ = ZoneInfo(TIMEZONE)
WINDOWS = ((time(8, 0), time(12, 0)), (time(13, 30), time(20, 0)))


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
    return [{"id": r["id"], "name": r["name"], "working_days": [int(x) for x in r["working_days"].split(",")]} for r in rows]


def _validate_window(start: datetime, duration: int, days_range: bool = True) -> datetime:
    now = datetime.now(TZ)
    if start <= now:
        raise BookingError("O horário escolhido precisa estar no futuro.")
    if days_range:
        days_ahead = (start.date() - now.date()).days
        if days_ahead < MIN_BOOKING_DAYS or days_ahead > MAX_BOOKING_DAYS:
            raise BookingError(f"Os agendamentos devem ser feitos com {MIN_BOOKING_DAYS} a {MAX_BOOKING_DAYS} dias de antecedência.")
    start_minute = _minutes(start.time())
    end_minute = start_minute + duration
    if not any(start_minute >= _minutes(opening) and end_minute <= _minutes(closing) for opening, closing in WINDOWS):
        raise BookingError("O serviço precisa caber no expediente: 08:00–12:00 ou 13:30–20:00.")
    return start + timedelta(minutes=duration)


def availability(service_id: int, appointment_date: date, barber_id: int | None = None, path: str | Path | None = None) -> list[dict]:
    with connect(path) as conn:
        service = conn.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()
        if not service:
            raise BookingError("Serviço não encontrado.")
        query = "SELECT * FROM barbers" + (" WHERE id=?" if barber_id else "")
        barbers = conn.execute(query, (barber_id,) if barber_id else ()).fetchall()
        busy = conn.execute("SELECT barber_id,starts_at,ends_at FROM appointments WHERE status='confirmed' AND date(starts_at)=?", (appointment_date.isoformat(),)).fetchall()
    now = datetime.now(TZ)
    days_ahead = (appointment_date - now.date()).days
    if not (MIN_BOOKING_DAYS <= days_ahead <= MAX_BOOKING_DAYS):
        return []
    result = []
    busy_by_barber: dict[int, list[tuple[datetime, datetime]]] = {}
    for item in busy:
        busy_by_barber.setdefault(item["barber_id"], []).append((_parse_start(item["starts_at"]), _parse_start(item["ends_at"])))
    for barber in barbers:
        if appointment_date.weekday() not in [int(x) for x in barber["working_days"].split(",")]:
            continue
        for opening, closing in WINDOWS:
            minute = _minutes(opening)
            while minute + service["duration_minutes"] <= _minutes(closing):
                candidate = datetime.combine(appointment_date, time(minute // 60, minute % 60), tzinfo=TZ)
                end = candidate + timedelta(minutes=service["duration_minutes"])
                if candidate > now and all(end <= old_start or candidate >= old_end for old_start, old_end in busy_by_barber.get(barber["id"], [])):
                    result.append({"barber_id": barber["id"], "barber": barber["name"], "starts_at": candidate.isoformat(), "ends_at": end.isoformat()})
                minute += 30
    return result


def create_appointment(customer_name: str, customer_phone: str, service_id: int, barber_id: int, starts_at: str, confirmed: bool, path: str | Path | None = None) -> dict:
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
        end = _validate_window(start, service["duration_minutes"])
        if start.date().weekday() not in [int(x) for x in barber["working_days"].split(",")]:
            raise BookingError(f"{barber['name']} não atende nesse dia da semana.")
        overlap = conn.execute("SELECT 1 FROM appointments WHERE barber_id=? AND status='confirmed' AND starts_at < ? AND ends_at > ?", (barber_id, end.isoformat(), start.isoformat())).fetchone()
        if overlap:
            raise BookingError("Esse horário acabou de ser ocupado. Consulte a disponibilidade novamente.")
        cursor = conn.execute("INSERT INTO appointments(customer_name,customer_phone,barber_id,service_id,starts_at,ends_at) VALUES (?,?,?,?,?,?)", (name, phone, barber_id, service_id, start.isoformat(), end.isoformat()))
        row = conn.execute("SELECT a.*, b.name AS barber, s.name AS service, s.price FROM appointments a JOIN barbers b ON b.id=a.barber_id JOIN services s ON s.id=a.service_id WHERE a.id=?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def find_appointments(customer_phone: str, path: str | Path | None = None) -> list[dict]:
    with connect(path) as conn:
        rows = conn.execute("SELECT a.*,b.name AS barber,s.name AS service,s.price FROM appointments a JOIN barbers b ON b.id=a.barber_id JOIN services s ON s.id=a.service_id WHERE a.customer_phone=? ORDER BY a.starts_at", (customer_phone.strip(),)).fetchall()
    return [dict(r) for r in rows]


def cancel_appointment(appointment_id: int, customer_phone: str, confirmed: bool = False, path: str | Path | None = None) -> dict:
    if not confirmed:
        raise BookingError("Peça confirmação explícita antes de cancelar o agendamento.")
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM appointments WHERE id=? AND customer_phone=? AND status='confirmed'", (appointment_id, customer_phone.strip())).fetchone()
        if not row:
            raise BookingError("Agendamento ativo não encontrado para esse telefone.")
        start = _parse_start(row["starts_at"])
        if start - datetime.now(TZ) < timedelta(hours=3):
            raise BookingError("O cancelamento precisa ser feito com pelo menos 3 horas de antecedência. Vou encaminhar para atendimento humano.")
        conn.execute("UPDATE appointments SET status='cancelled' WHERE id=?", (appointment_id,))
        return {"id": appointment_id, "status": "cancelled"}


def reschedule_appointment(appointment_id: int, customer_phone: str, new_starts_at: str, confirmed: bool, path: str | Path | None = None) -> dict:
    if not confirmed:
        raise BookingError("Peça confirmação explícita da pessoa antes de remarcar.")
    start = _parse_start(new_starts_at)
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM appointments WHERE id=? AND customer_phone=? AND status='confirmed'", (appointment_id, customer_phone.strip())).fetchone()
        if not old:
            raise BookingError("Agendamento ativo não encontrado para esse telefone.")
        old_start = _parse_start(old["starts_at"])
        if old_start - datetime.now(TZ) < timedelta(hours=3):
            raise BookingError("A remarcação precisa ser feita com pelo menos 3 horas de antecedência.")
        service = conn.execute("SELECT * FROM services WHERE id=?", (old["service_id"],)).fetchone()
        barber = conn.execute("SELECT * FROM barbers WHERE id=?", (old["barber_id"],)).fetchone()
        end = _validate_window(start, service["duration_minutes"])
        if start.date().weekday() not in [int(x) for x in barber["working_days"].split(",")]:
            raise BookingError(f"{barber['name']} não atende nesse dia da semana.")
        overlap = conn.execute("SELECT 1 FROM appointments WHERE barber_id=? AND id<>? AND status='confirmed' AND starts_at < ? AND ends_at > ?", (old["barber_id"], appointment_id, end.isoformat(), start.isoformat())).fetchone()
        if overlap:
            raise BookingError("Esse horário acabou de ser ocupado. Consulte a disponibilidade novamente.")
        conn.execute("UPDATE appointments SET starts_at=?,ends_at=? WHERE id=?", (start.isoformat(), end.isoformat(), appointment_id))
        row = conn.execute("SELECT a.*,b.name AS barber,s.name AS service,s.price FROM appointments a JOIN barbers b ON b.id=a.barber_id JOIN services s ON s.id=a.service_id WHERE a.id=?", (appointment_id,)).fetchone()
        return dict(row)

