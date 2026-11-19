"""Vínculo dos barbeiros, avisos e consultas de agenda pelo Telegram."""

from __future__ import annotations

from datetime import datetime
import logging

from barber_bot.config import TELEGRAM_BOT_TOKEN
from barber_bot.db import connect, phone_key
from barber_bot.presentation import format_price
from barber_bot.schedule import TZ

log = logging.getLogger("barber_bot.telegram")


def link_barber_telegram(chat_id: str, sender_id: str, contact: dict) -> str:
    """Vincula somente quando o próprio usuário compartilha o telefone cadastrado."""
    if str(chat_id) != str(sender_id) or str(contact.get("user_id", "")) != str(sender_id):
        raise ValueError("Compartilhe somente o seu próprio contato em uma conversa privada.")
    phone = phone_key(str(contact.get("phone_number", "")))
    if not phone:
        raise ValueError("Não consegui ler o telefone compartilhado. Tente novamente.")
    if len(phone) in {10, 11}:
        phone = "55" + phone
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        barber = conn.execute(
            "SELECT id, name FROM barbers WHERE telegram_phone=?", (phone,)
        ).fetchone()
        if not barber:
            raise ValueError("Este telefone não está cadastrado para um barbeiro. Peça à equipe para conferir o cadastro.")
        other = conn.execute(
            "SELECT name FROM barbers WHERE telegram_chat_id=? AND id<>?", (str(chat_id), barber["id"])
        ).fetchone()
        if other:
            raise ValueError("Esta conta do Telegram já está vinculada a outro profissional.")
        conn.execute("UPDATE barbers SET telegram_chat_id=? WHERE id=?", (str(chat_id), barber["id"]))
    return barber["name"]


def barber_for_chat(chat_id: str):
    with connect() as conn:
        return conn.execute(
            "SELECT id, name FROM barbers WHERE telegram_chat_id=?", (str(chat_id),)
        ).fetchone()


def barber_appointments(chat_id: str, limit: int = 15):
    now = datetime.now(TZ).isoformat()
    with connect() as conn:
        barber = conn.execute(
            "SELECT id, name FROM barbers WHERE telegram_chat_id=?", (str(chat_id),)
        ).fetchone()
        if not barber:
            return None, []
        rows = conn.execute(
            """SELECT a.id, a.customer_name, a.starts_at, a.ends_at, a.status,
                      COALESCE(a.price_at_booking, s.price) AS price,
                      s.name AS service, b.name AS barber
               FROM appointments a
               JOIN services s ON s.id=a.service_id
               JOIN barbers b ON b.id=a.barber_id
               WHERE a.barber_id=? AND a.status='confirmed' AND a.starts_at>=?
               ORDER BY a.starts_at LIMIT ?""",
            (barber["id"], now, limit),
        ).fetchall()
    return barber, [dict(row) for row in rows]


def barber_appointment(chat_id: str, appointment_id: int):
    with connect() as conn:
        return conn.execute(
            """SELECT a.id, a.customer_name, a.starts_at, a.ends_at, a.status,
                      COALESCE(a.price_at_booking, s.price) AS price,
                      s.name AS service, b.name AS barber
               FROM appointments a
               JOIN services s ON s.id=a.service_id
               JOIN barbers b ON b.id=a.barber_id
               WHERE a.id=? AND a.barber_id=(
                   SELECT id FROM barbers WHERE telegram_chat_id=?
               )""",
            (appointment_id, str(chat_id)),
        ).fetchone()


def notify_barber(booking: dict) -> bool:
    """Envia o aviso sem interferir na gravação da reserva."""
    if not TELEGRAM_BOT_TOKEN:
        return False
    with connect() as conn:
        row = conn.execute(
            "SELECT name, telegram_chat_id FROM barbers WHERE id=?", (booking["barber_id"],)
        ).fetchone()
    if not row or not row["telegram_chat_id"]:
        return False

    start = datetime.fromisoformat(booking["starts_at"]).astimezone(TZ)
    text = (
        "📅 Novo agendamento confirmado\n\n"
        f"Protocolo: #{booking['id']}\n"
        f"Cliente: {booking['customer_name']}\n"
        f"Serviço: {booking['service']}\n"
        f"Data: {start:%d/%m/%Y às %H:%M}\n"
        f"Valor: {format_price(booking['price'])}"
    )
    try:
        from barber_bot.telegram_bot import send_message

        send_message(row["telegram_chat_id"], text)
    except Exception as exc:
        log.warning("Não foi possível avisar o barbeiro do agendamento #%s (%s).",
                    booking.get("id"), type(exc).__name__)
        return False
    return True
