"""Session-bound support intake and authenticated staff queue operations."""

import re

from barber_bot.db import connect, phone_key
from barber_bot.staff import StaffMember


STATUSES = {"open", "in_progress", "resolved"}
CHANNELS = {"web", "telegram"}


class SupportError(ValueError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def _safe_text(value: str, limit: int) -> str:
    value = re.sub(r"BI-[A-Za-z0-9_-]{12,}", "[código privado]", str(value), flags=re.I)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[e-mail privado]", value)
    value = re.sub(r"(?<!\w)\+?\d[\d ().-]{7,}\d(?!\w)", "[telefone privado]", value)
    return " ".join(value.split())[:limit]


def _identity(session_id: str, channel: str):
    if not session_id or len(session_id) > 160 or channel not in CHANNELS:
        raise SupportError("Sessão ou canal inválido.", 422)


def _contact(name: str, phone: str) -> tuple[str, str]:
    name = " ".join(name.split())
    phone = phone_key(phone)
    if len(name) > 120 or (phone and (not phone.isascii() or not phone.isdecimal() or not 10 <= len(phone) <= 15)):
        raise SupportError("Informe nome e telefone com DDD válidos.", 422)
    return name, phone


def _public(row) -> dict:
    return {key: row[key] for key in (
        "id", "status", "channel", "reason", "customer_name", "customer_phone", "created_at", "updated_at", "resolved_at",
    )}


def get_open_request(session_id: str, channel: str) -> dict | None:
    _identity(session_id, channel)
    with connect() as conn:
        row = conn.execute("""
            SELECT * FROM support_tickets WHERE session_id=? AND channel=?
            AND status IN ('open','in_progress') ORDER BY id DESC LIMIT 1
        """, (session_id, channel)).fetchone()
    return _public(row) if row else None


def get_customer_request(session_id: str, channel: str, ticket_id: int | None = None) -> dict | None:
    """Return a minimal status, scoped to the originating conversation and channel."""
    _identity(session_id, channel)
    with connect() as conn:
        row = conn.execute("""
            SELECT id,status,updated_at,
                   (COALESCE(customer_name,'')<>'' AND COALESCE(customer_phone,'')<>'') AS contact_provided
            FROM support_tickets WHERE session_id=? AND channel=? AND (? IS NULL OR id=?)
            ORDER BY id DESC LIMIT 1
        """, (session_id, channel, ticket_id, ticket_id)).fetchone()
    if row is None:
        if ticket_id is not None:
            raise SupportError("Solicitação não encontrada nesta conversa.", 404)
        return None
    return {"id": row["id"], "status": row["status"], "updated_at": row["updated_at"],
            "contact_provided": bool(row["contact_provided"])}


def create_support_request(session_id: str, channel: str, reason: str,
                           customer_name: str = "", customer_phone: str = "") -> dict:
    _identity(session_id, channel)
    name, phone = _contact(customer_name, customer_phone)
    reason = _safe_text(reason, 500) or "Cliente solicitou atendimento humano."
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""
            SELECT * FROM support_tickets WHERE session_id=? AND channel=?
            AND status IN ('open','in_progress') ORDER BY id DESC LIMIT 1
        """, (session_id, channel)).fetchone()
        if not row:
            row = conn.execute("""
                INSERT INTO support_tickets(session_id,channel,reason,customer_name,customer_phone,updated_at)
                VALUES (?,?,?,?,?,CURRENT_TIMESTAMP) RETURNING *
            """, (session_id, channel, reason, name, phone)).fetchone()
        elif name and phone:
            row = conn.execute("""UPDATE support_tickets SET customer_name=?,customer_phone=?,
                updated_at=CURRENT_TIMESTAMP WHERE id=? RETURNING *""", (name, phone, row["id"])).fetchone()
    return _public(row)


def update_support_contact(session_id: str, channel: str, ticket_id: int,
                           customer_name: str, customer_phone: str) -> dict:
    _identity(session_id, channel)
    name, phone = _contact(customer_name, customer_phone)
    if not name or not phone:
        raise SupportError("Informe seu nome e telefone com DDD.", 422)
    with connect() as conn:
        row = conn.execute("""
            UPDATE support_tickets SET customer_name=?,customer_phone=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND session_id=? AND channel=? AND status IN ('open','in_progress') RETURNING *
        """, (name, phone, ticket_id, session_id, channel)).fetchone()
    if not row:
        raise SupportError("Solicitação ativa não encontrada nesta conversa.", 404)
    return _public(row)


def _staff_record(conn, row, member: StaffMember) -> dict:
    record = _public(row)
    record["reason"] = _safe_text(record["reason"], 500)
    record["assigned_to"] = row["assigned_to"]
    record["notes"] = []
    record["history"] = []
    if member.role != "owner" and row["assigned_to"] != member.username:
        record["customer_name"] = record["customer_phone"] = ""
        return record
    notes = conn.execute("""
        SELECT author,note,created_at FROM support_notes WHERE ticket_id=? ORDER BY id DESC LIMIT 20
    """, (row["id"],)).fetchall()
    record["notes"] = [{**dict(note), "note": _safe_text(note["note"], 1000)} for note in reversed(notes)]
    if row["session_id"]:
        history = conn.execute("""
            SELECT created_at,intent,tools_called,outcome FROM interaction_logs
            WHERE session_id=? AND channel=? AND created_at<=? ORDER BY id DESC LIMIT 10
        """, (row["session_id"], row["channel"], row["created_at"])).fetchall()
        record["history"] = [{key: (value if key == "created_at" else _safe_text(value, 250)) if value is not None else None
                              for key, value in dict(item).items()} for item in reversed(history)]
    return record


def list_staff_requests(member: StaffMember, status: str | None = None) -> list[dict]:
    if member.role not in {"owner", "barber"}:
        raise SupportError("Acesso restrito à equipe.", 403)
    if status is not None and status not in STATUSES:
        raise SupportError("Situação inválida.", 422)
    with connect() as conn:
        rows = conn.execute("""
            SELECT * FROM support_tickets WHERE (? IS NULL OR status=?)
            AND (?='owner' OR assigned_to=? OR (assigned_to IS NULL AND status='open'))
            ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END, created_at, id
            LIMIT 200
        """, (status, status, member.role, member.username)).fetchall()
        return [_staff_record(conn, row, member) for row in rows]


def update_staff_request(ticket_id: int, member: StaffMember, *, claim: bool = False,
                         status: str | None = None, note: str | None = None) -> dict:
    if member.role not in {"owner", "barber"}:
        raise SupportError("Acesso restrito à equipe.", 403)
    if status is not None and status not in STATUSES:
        raise SupportError("Situação inválida.", 422)
    if note is not None and (not note.strip() or len(note) > 1000):
        raise SupportError("Escreva uma observação de até 1000 caracteres.", 422)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)).fetchone()
        if not row:
            raise SupportError("Solicitação não encontrada.", 404)
        if claim:
            result = conn.execute("""
                UPDATE support_tickets SET assigned_to=?,status='in_progress',updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND assigned_to IS NULL AND status='open'
            """, (member.username, ticket_id))
            if not result.rowcount:
                raise SupportError("A solicitação já foi assumida ou encerrada. Atualize a fila.")
            row = conn.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)).fetchone()
        if member.role != "owner" and row["assigned_to"] != member.username:
            raise SupportError("Assuma a solicitação antes de alterá-la. Outros atendimentos são restritos.", 403)
        if row["status"] == "resolved" and status not in (None, "resolved"):
            raise SupportError("Solicitações resolvidas não podem ser reabertas. Abra um novo atendimento.")
        if status == "open" and row["assigned_to"] is not None:
            raise SupportError("Um atendimento assumido deve permanecer em andamento até ser resolvido.")
        if status == "in_progress" and row["assigned_to"] is None:
            raise SupportError("Assuma a solicitação para iniciar o atendimento.")
        if status is not None:
            conn.execute("""
                UPDATE support_tickets SET status=?,updated_at=CURRENT_TIMESTAMP,
                resolved_at=CASE WHEN ?='resolved' THEN COALESCE(resolved_at,CURRENT_TIMESTAMP) ELSE resolved_at END
                WHERE id=?
            """, (status, status, ticket_id))
        if note is not None:
            conn.execute("INSERT INTO support_notes(ticket_id,author,note) VALUES (?,?,?)",
                         (ticket_id, member.username, _safe_text(note, 1000)))
            conn.execute("UPDATE support_tickets SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (ticket_id,))
        row = conn.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)).fetchone()
        return _staff_record(conn, row, member)
