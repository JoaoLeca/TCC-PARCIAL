"""Self-service access through the customer's own Telegram contact.

Only the trusted bot update handler calls contact verification. Public HTTP
never accepts contact objects or Telegram chat IDs as proof of identity.
"""
from functools import lru_cache
from hashlib import sha256
import hmac
import re
import secrets
import time

import requests

from barber_bot.config import TELEGRAM_BOT_TOKEN
from barber_bot.customer_access import owner_digest
from barber_bot.db import connect, phone_key

CHALLENGE_TTL = 600
CODE_TTL = 300
GRANT_TTL = 24 * 60 * 60
MAX_ATTEMPTS = 5


class RecoveryError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def _digest(value):
    return sha256(value.encode()).hexdigest()


def normalized_phone(value):
    digits = phone_key(str(value))
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    if not digits.isascii() or not digits.isdigit() or len(digits) not in (10, 11):
        raise RecoveryError("Informe o telefone completo com DDD usado na reserva.")
    return digits


@lru_cache(maxsize=1)
def get_bot_username():
    if not TELEGRAM_BOT_TOKEN:
        raise RecoveryError("A verificação pelo Telegram ainda não está configurada nesta instalação.", 503)
    try:
        response = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=8)
        data = response.json()
        username = data.get("result", {}).get("username", "")
        if not response.ok or not data.get("ok") or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
            raise ValueError()
        return username
    except (requests.RequestException, ValueError):
        raise RecoveryError("Não foi possível iniciar a verificação pelo Telegram. Tente novamente em instantes.", 503) from None


def start_recovery(phone, owner_key):
    digest, phone = owner_digest("web", owner_key), normalized_phone(phone)
    username = get_bot_username()
    now, nonce = int(time.time()), secrets.token_urlsafe(24)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        recent = conn.execute("SELECT count(*) FROM customer_recovery WHERE owner_hash=? AND created_at>?", (digest, now-900)).fetchone()[0]
        if recent >= 3:
            raise RecoveryError("Aguarde alguns minutos antes de solicitar outra verificação.", 429)
        # Supersede this browser's older challenges, never another customer's.
        conn.execute("UPDATE customer_recovery SET consumed_at=? WHERE owner_hash=? AND consumed_at IS NULL", (now, digest))
        conn.execute("INSERT INTO customer_recovery(challenge_hash,owner_hash,phone,created_at,expires_at) VALUES (?,?,?,?,?)",
                     (_digest(nonce), digest, phone, now, now+CHALLENGE_TTL))
    return {"challenge_id": nonce, "telegram_url": f"https://t.me/{username}?start=recover_{nonce}", "expires_in": CHALLENGE_TTL}


def _require_challenge(conn, nonce, now):
    row = conn.execute("SELECT * FROM customer_recovery WHERE challenge_hash=?", (_digest(nonce),)).fetchone()
    if row is None or row["consumed_at"] is not None or row["expires_at"] <= now or row["attempts"] >= MAX_ATTEMPTS:
        raise RecoveryError("Esta verificação expirou ou não está disponível. Solicite uma nova.")
    return row


def _own_contact(chat_id, sender_id, contact):
    if not sender_id or str(chat_id) != str(sender_id) or not isinstance(contact, dict) or str(contact.get("user_id", "")) != str(sender_id):
        raise RecoveryError("Use Compartilhar meu telefone no Telegram para confirmar seu próprio contato.")
    return normalized_phone(contact.get("phone_number", ""))


def _grant(conn, phone, channel, owner_key, now):
    digest = owner_digest(channel, owner_key)
    conn.execute("""INSERT INTO booking_access_grants(appointment_id,channel,owner_hash,expires_at)
        SELECT id,?,?,? FROM appointments WHERE phone_key(customer_phone) IN (?,?)
        ON CONFLICT(appointment_id,channel,owner_hash) DO UPDATE SET expires_at=excluded.expires_at""",
                 (channel, digest, now+GRANT_TTL, phone, "55"+phone))


def verify_telegram_contact(chat_id, sender_id, contact):
    phone, now = _own_contact(chat_id, sender_id, contact), int(time.time())
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _grant(conn, phone, "telegram", str(chat_id), now)


def issue_telegram_code(nonce, chat_id, sender_id, contact):
    phone, now = _own_contact(chat_id, sender_id, contact), int(time.time())
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _require_challenge(conn, nonce, now)
        if row["phone"] != phone:
            raise RecoveryError("O contato compartilhado não corresponde ao telefone informado no site. Confira o número e solicite uma nova verificação.")
        sent_for_phone = conn.execute("SELECT count(*) FROM customer_recovery WHERE phone=? AND code_hash IS NOT NULL AND created_at>?",
                                      (phone, now-900)).fetchone()[0]
        if not row["code_hash"] and sent_for_phone >= 5:
            raise RecoveryError("Aguarde alguns minutos antes de solicitar outro código para este telefone.", 429)
        if row["code_hash"] and row["code_expires_at"]-CODE_TTL > now-30:
            raise RecoveryError("Um código já foi enviado. Use o mais recente ou aguarde 30 segundos para pedir outro.", 429)
        if row["telegram_chat_id"] and row["telegram_chat_id"] != str(chat_id):
            raise RecoveryError("Esta verificação já está em andamento em outra conversa.")
        code = f"{secrets.randbelow(1000000):06d}"
        conn.execute("UPDATE customer_recovery SET code_hash=?,code_expires_at=?,telegram_chat_id=? WHERE challenge_hash=?",
                     (_digest(nonce+":"+code), min(now+CODE_TTL, row["expires_at"]), str(chat_id), _digest(nonce)))
        return code


def verify_recovery(nonce, code, owner_key):
    digest, now = owner_digest("web", owner_key), int(time.time())
    failure = None
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _require_challenge(conn, nonce, now)
        if not hmac.compare_digest(row["owner_hash"], digest):
            raise RecoveryError("Esta verificação não pertence a esta sessão.")
        if not row["code_hash"]:
            raise RecoveryError("Abra o Telegram e confirme seu próprio contato para receber o código.")
        if row["code_expires_at"] <= now:
            raise RecoveryError("O código expirou. Solicite uma nova verificação.")
        if not re.fullmatch(r"[0-9]{6}", code) or not hmac.compare_digest(row["code_hash"], _digest(nonce+":"+code)):
            conn.execute("UPDATE customer_recovery SET attempts=attempts+1 WHERE challenge_hash=?", (_digest(nonce),))
            failure = RecoveryError("Código inválido. Confira os seis dígitos recebidos no Telegram.")
        else:
            _grant(conn, row["phone"], "web", owner_key, now)
            conn.execute("UPDATE customer_recovery SET consumed_at=? WHERE challenge_hash=?", (now, _digest(nonce)))
            conn.execute("DELETE FROM customer_recovery_flows WHERE owner_hash=?", (digest,))
    # The failed-attempt counter must commit before raising.
    if failure:
        raise failure


def _chat_reply(content, stage=None):
    return {"content": content, "intent": "consultar_agendamento", "confidence": 1,
            "mode": "guided", "outcome": "verification_required", "tools_called": [], "recovery_stage": stage}


def begin_chat_recovery(owner_key, requested_action):
    digest = owner_digest("web", owner_key)
    with connect() as conn:
        conn.execute("""INSERT INTO customer_recovery_flows(owner_hash,stage,requested_action,expires_at) VALUES (?,'phone',?,?)
            ON CONFLICT(owner_hash) DO UPDATE SET stage='phone',challenge_id=NULL,
            requested_action=excluded.requested_action,expires_at=excluded.expires_at""",
                     (digest, requested_action, int(time.time())+CHALLENGE_TTL))
    return _chat_reply("Posso localizar sua reserva sem precisar da equipe. Qual é o telefone completo com DDD usado no agendamento? Vou confirmar seu acesso pelo Telegram.", "phone")


def handle_chat_recovery(value, owner_key):
    """Consume verification inputs before they reach any language model."""
    if not owner_key:
        return None
    digest, now = owner_digest("web", owner_key), int(time.time())
    with connect() as conn:
        flow = conn.execute("SELECT * FROM customer_recovery_flows WHERE owner_hash=?", (digest,)).fetchone()
    if flow is None:
        return None
    if value.strip().casefold() in {"novo código", "novo codigo", "reenviar", "trocar telefone", "solicitar novo código"}:
        return begin_chat_recovery(owner_key, flow["requested_action"])
    if flow["expires_at"] <= now or value.strip().casefold() in {"não", "nao", "voltar", "sair", "desisto", "deixa pra lá"}:
        with connect() as conn:
            conn.execute("DELETE FROM customer_recovery_flows WHERE owner_hash=?", (digest,))
        return _chat_reply("A verificação foi encerrada. Sua reserva não foi alterada. Para tentar de novo, peça para consultar seus agendamentos.")
    try:
        if flow["stage"] == "phone":
            challenge = start_recovery(value.strip(), owner_key)
            with connect() as conn:
                conn.execute("UPDATE customer_recovery_flows SET stage='code',challenge_id=?,expires_at=? WHERE owner_hash=?",
                             (challenge["challenge_id"], now+CHALLENGE_TTL, digest))
            return _chat_reply("[Abrir o Telegram para confirmar meu telefone](" + challenge["telegram_url"] + ").\n\nNo bot, toque em Iniciar e compartilhe seu próprio contato. Depois escreva aqui os seis dígitos que ele enviar. O código vale por cinco minutos. Não precisa falar com a equipe.", "code")
        verify_recovery(flow["challenge_id"], value.strip(), owner_key)
        with connect() as conn:
            conn.execute("DELETE FROM customer_recovery_flows WHERE owner_hash=?", (digest,))
        from barber_bot.booking_conversation import handle_booking_message
        result = handle_booking_message([{"role": "user", "content": flow["requested_action"]}], "web", owner_key)
        if result and not result.get("needs_verification"):
            return {**result, "content": "Telefone confirmado.\n\n"+result["content"], "recovery_stage": None}
        return _chat_reply("Telefone confirmado. Não encontrei reservas ativas para esse telefone. Confira em Meus horários ou faça um novo agendamento.")
    except RecoveryError as exc:
        return _chat_reply(str(exc), flow["stage"])
