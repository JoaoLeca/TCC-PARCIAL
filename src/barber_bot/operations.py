from datetime import date, datetime, time, timedelta

from barber_bot.config import SHOP_OPEN_DAYS
from barber_bot.db import connect
from barber_bot.schedule import BookingError, TZ, _parse_start, fits_hours


def shop_profile():
    with connect() as conn:
        profile = dict(conn.execute("""
            SELECT name, address, phone, payment_methods, open_1, close_1, open_2, close_2
            FROM shop_profile WHERE id=1
        """).fetchone())
    return {**profile, "open_days": list(SHOP_OPEN_DAYS)}


def _reject_affected(rows, message):
    if rows:
        ids = ", ".join(f"#{row['id']}" for row in rows[:5])
        suffix = f" e mais {len(rows) - 5}" if len(rows) > 5 else ""
        raise BookingError(f"{message} {ids}{suffix}. Remarque ou cancele essas reservas primeiro.")


def update_shop(values):
    windows = [(values["open_1"], values["close_1"])]
    second = (values["open_2"], values["close_2"])
    if (second[0] is None) != (second[1] is None):
        raise BookingError("Preencha a abertura e o fechamento do segundo período.")
    if second[0] is not None:
        windows.append(second)
    if any(point.tzinfo or point.second or point.microsecond for pair in windows for point in pair):
        raise BookingError("Use horários locais no formato HH:MM.")
    if any(start >= end for start, end in windows) or (len(windows) == 2 and windows[0][1] > windows[1][0]):
        raise BookingError("Informe períodos em ordem, sem sobreposição.")

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        upcoming = conn.execute("""
            SELECT id, starts_at, ends_at FROM appointments
            WHERE status='confirmed' AND ends_at > ? ORDER BY starts_at
        """, (datetime.now(TZ).isoformat(),)).fetchall()
        _reject_affected([row for row in upcoming if not fits_hours(
            _parse_start(row["starts_at"]), _parse_start(row["ends_at"]), windows,
        )], "O novo expediente afeta as reservas")
        conn.execute("""
            UPDATE shop_profile SET name=?, address=?, phone=?, payment_methods=?,
            open_1=?, close_1=?, open_2=?, close_2=? WHERE id=1
        """, (values["name"], values["address"], values["phone"], values["payment_methods"],
              *(values[key].strftime("%H:%M") if values[key] is not None else None
                for key in ("open_1", "close_1", "open_2", "close_2"))))
    return shop_profile()


def update_working_days(barber_id, days):
    days = sorted(set(days))
    if any(day not in SHOP_OPEN_DAYS for day in days):
        raise BookingError("A barbearia está fechada aos domingos.")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        barber = conn.execute("SELECT name FROM barbers WHERE id=?", (barber_id,)).fetchone()
        if not barber:
            return None
        upcoming = conn.execute("""
            SELECT id, starts_at FROM appointments
            WHERE barber_id=? AND status='confirmed' AND ends_at > ? ORDER BY starts_at
        """, (barber_id, datetime.now(TZ).isoformat())).fetchall()
        _reject_affected([row for row in upcoming if _parse_start(row["starts_at"]).weekday() not in days],
                         "A nova escala afeta as reservas")
        conn.execute("UPDATE barbers SET working_days=? WHERE id=?", (",".join(map(str, days)), barber_id))
    return {"id": barber_id, "name": barber["name"], "working_days": days}


def list_blocks(day, barber_id=None):
    start = datetime.combine(day, time.min, tzinfo=TZ)
    end = datetime.combine(day, time.max, tzinfo=TZ) if day == date.max else start + timedelta(days=1)
    with connect() as conn:
        rows = conn.execute("""
            SELECT x.*, b.name AS barber FROM schedule_blocks x JOIN barbers b ON b.id=x.barber_id
            WHERE x.released_at IS NULL AND x.starts_at < ? AND x.ends_at > ? AND (? IS NULL OR x.barber_id=?)
            ORDER BY x.starts_at, b.name
        """, (end.isoformat(), start.isoformat(), barber_id, barber_id)).fetchall()
    return [dict(row) for row in rows]


def create_block(barber_id, starts_at, ends_at, reason):
    start, end = _parse_start(starts_at), _parse_start(ends_at)
    if end <= start or end <= datetime.now(TZ):
        raise BookingError("O fim do bloqueio precisa ser posterior ao início e estar no futuro.")
    bounds = (barber_id, end.isoformat(), start.isoformat())
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM barbers WHERE id=?", (barber_id,)).fetchone():
            return None
        affected = conn.execute("""
            SELECT id FROM appointments
            WHERE barber_id=? AND status='confirmed' AND starts_at < ? AND ends_at > ? ORDER BY starts_at
        """, bounds).fetchall()
        _reject_affected(affected, "O período coincide com as reservas")
        if conn.execute("SELECT 1 FROM schedule_blocks WHERE released_at IS NULL AND barber_id=? AND starts_at < ? AND ends_at > ?",
                        bounds).fetchone():
            raise BookingError("Já existe um bloqueio para parte desse período.")
        return dict(conn.execute("""
            INSERT INTO schedule_blocks (barber_id, starts_at, ends_at, reason) VALUES (?, ?, ?, ?)
            RETURNING id, barber_id, starts_at, ends_at, reason
        """, (barber_id, start.isoformat(), end.isoformat(), reason)).fetchone())


def remove_block(block_id, barber_id):
    with connect() as conn:
        result = conn.execute("""
            UPDATE schedule_blocks SET released_at=?
            WHERE id=? AND released_at IS NULL AND (? IS NULL OR barber_id=?)
        """, (datetime.now(TZ).isoformat(), block_id, barber_id, barber_id))
    return result.rowcount > 0
