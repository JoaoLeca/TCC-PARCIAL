"""Deterministic, session-owned reservation consultation and changes.

State and the final mutation share a SQLite write transaction. A written yes
only applies to a still-current proposal made to this channel and owner.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re

from barber_bot import customer_access
from barber_bot.availability_conversation import _requested_day, _time_preference, _preference_slots, _three_options
from barber_bot.config import MIN_BOOKING_DAYS, MAX_BOOKING_DAYS
from barber_bot.conversation import normalize_text
from barber_bot.db import connect
from barber_bot.presentation import format_price
from barber_bot.schedule import TZ, BookingError, cancel_appointment, reschedule_appointment, reschedule_options


PENDING_TTL = timedelta(minutes=10)
_YES = re.compile(r"(?:sim|sim confirmo|confirmo|pode confirmar|confirmar|pode sim)[.! ]*")
_NO = re.compile(r"(?:nao|nao confirmo|cancelar|cancela|cancelar operacao|deixa pra la|deixe pra la|desisto|parar)[.! ]*")
_SNAPSHOT = ("id", "status", "starts_at", "ends_at", "barber_id", "service_id", "price")


def _reply(action, content, *, outcome="answered", tools=()):
    return {"intent": {"cancel": "cancelar", "reschedule": "remarcar"}.get(action, "consultar_agendamento"),
            "content": content, "confidence": 1.0, "tools_called": list(tools),
            "mode": "guided", "outcome": outcome}


def _intent(text):
    if re.search(r"\b(?:como|politica|regra|prazo|posso|pode)\b", text) and not re.search(r"\b(?:quero|preciso|gostaria)\b", text):
        return None
    if re.search(r"\b(?:nao|nem)\s+(?:quero\s+)?(?:cancel|desmarc|remarc|alter)", text):
        return None
    if re.search(r"\b(?:remarcar|remarca|reagendar|reagenda|alterar|mudar|trocar)\b", text):
        return "reschedule"
    if re.search(r"\b(?:cancelar|cancela|cancele|desmarcar|desmarca)\b", text):
        return "cancel"
    if re.search(r"\b(?:minhas?\s+(?:reservas?|consultas?)|meus?\s+(?:horarios?|agendamentos?))\b", text):
        return "consult"
    return None


def _describe(booking):
    start = datetime.fromisoformat(booking["starts_at"])
    end = datetime.fromisoformat(booking["ends_at"])
    return (f"reserva #{booking['id']}: {booking['service']} com {booking['barber']}, "
            f"{start:%d/%m/%Y} das {start:%H:%M} às {end:%H:%M}, {format_price(booking['price'])}")


def _snapshot(booking):
    return {key: booking.get(key) for key in _SNAPSHOT}


def _load(conn, channel, owner_hash):
    row = conn.execute("SELECT state_json,updated_at FROM booking_conversations WHERE channel=? AND owner_key_hash=?",
                       (channel, owner_hash)).fetchone()
    if not row:
        return None, False
    try:
        updated = datetime.fromisoformat(row["updated_at"])
        expired = datetime.now(timezone.utc) - updated >= PENDING_TTL
        state = json.loads(row["state_json"])
    except (ValueError, TypeError):
        state, expired = None, True
    if expired:
        _clear(conn, channel, owner_hash)
        return None, True
    return state, False


def _save(conn, channel, owner_hash, state):
    conn.execute("""INSERT INTO booking_conversations(channel,owner_key_hash,state_json,updated_at) VALUES (?,?,?,?)
        ON CONFLICT(channel,owner_key_hash) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at""",
                 (channel, owner_hash, json.dumps(state, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))


def _clear(conn, channel, owner_hash):
    conn.execute("DELETE FROM booking_conversations WHERE channel=? AND owner_key_hash=?", (channel, owner_hash))


def _select_booking(text, bookings):
    explicit = re.search(r"#\s*(\d+)\b|\b(?:reserva|agendamento)\s+(?:numero\s+|n[ºo.]?\s*|id\s*)?(\d+)\b", text)
    if explicit:
        wanted = int(explicit[1] or explicit[2])
        return next((booking for booking in bookings if booking["id"] == wanted), None)
    index = re.fullmatch(r"(?:opcao\s*)?(\d+)[.! ]*", text)
    if index and 1 <= int(index[1]) <= len(bookings):
        return bookings[int(index[1]) - 1]
    return None


def _list_text(bookings):
    return "\n".join(f"{index}. {_describe(booking)}" for index, booking in enumerate(bookings, 1))


def _trusted_booking(conn, channel, owner_key, appointment_id):
    return dict(customer_access.require_owned_booking(appointment_id, channel, owner_key, conn=conn))


def _confirm_text(action, booking, slot=None):
    if action == "cancel":
        return f"Você quer cancelar a {_describe(booking)}.\n\nConfirma o cancelamento? Responda sim ou não."
    start, end = datetime.fromisoformat(slot["starts_at"]), datetime.fromisoformat(slot["ends_at"])
    return (f"Reserva atual: {_describe(booking)}.\nNovo horário: {start:%d/%m/%Y} das {start:%H:%M} "
            f"às {end:%H:%M} com {booking['barber']}. O serviço e o valor permanecem {booking['service']}, "
            f"{format_price(booking['price'])}.\n\nConfirma a remarcação? Responda sim ou não.")


def _remember_preferences(state, text):
    today = datetime.now(TZ).date()
    day = _requested_day(text, today)
    if day is None and state.get("stage") in ("date", "options"):
        bare_day = re.fullmatch(r"\s*(\d{1,2})\s*", text)
        if bare_day and 1 <= int(bare_day[1]) <= 31:
            wanted = int(bare_day[1])
            for offset in range(13):
                month_index = today.month - 1 + offset
                year, month = today.year + month_index // 12, month_index % 12 + 1
                try:
                    candidate = datetime(year, month, wanted).date()
                except ValueError:
                    continue
                if candidate >= today:
                    day = candidate
                    break
            if day is None:
                day = "invalid"
    preference = _time_preference(text)
    if day is not None:
        state["day"] = day.isoformat() if day != "invalid" else "invalid"
    if preference is not None:
        state["preference"] = preference


def _options(conn, channel, owner_hash, state, booking, text):
    today = datetime.now(TZ).date()
    _remember_preferences(state, text)
    day_text = state.get("day")
    if day_text is None and re.search(r"\b(?:que|qual|quais|quando)\b.*\b(?:dia|data|horarios?|vagas?|disponiveis?)\b", text):
        first, last = today + timedelta(days=MIN_BOOKING_DAYS), today + timedelta(days=MAX_BOOKING_DAYS)
        preference = state.get("preference")
        choices = []
        for offset in range(MIN_BOOKING_DAYS, MAX_BOOKING_DAYS + 1):
            candidate_day = today + timedelta(days=offset)
            slots = reschedule_options(booking["id"], booking["customer_phone"], candidate_day, min_notice_hours=3)
            for slot in _three_options(_preference_slots(slots, preference)):
                if all(old["starts_at"] != slot["starts_at"] for old in choices):
                    choices.append(slot)
                if len(choices) == 3:
                    break
            if len(choices) == 3:
                break
        state["options"], state["stage"] = choices, "options"
        _save(conn, channel, owner_hash, state)
        if not choices:
            return _reply("reschedule", f"Não encontrei horários disponíveis para remarcar entre {first:%d/%m} e {last:%d/%m}. Prefere outro período ou quer falar com a equipe?")
        lines = [f"{index}. {datetime.fromisoformat(slot['starts_at']):%d/%m/%Y às %H:%M} com {slot['barber']}" for index, slot in enumerate(choices, 1)]
        return _reply("reschedule", "Consultei a agenda. Estas são as próximas opções disponíveis para remarcar:\n\n" + "\n".join(lines) + "\n\nEscolha pelo número ou escreva o horário. Depois vou pedir sua confirmação final.", tools=("buscar_opcoes_remarcacao",))
    if day_text is None:
        _save(conn, channel, owner_hash, state)
        return _reply("reschedule", "Para qual nova data você quer remarcar? Pode dizer amanhã ou informar dia/mês; se quiser, diga também o horário ou período do dia.")
    first, last = today + timedelta(days=MIN_BOOKING_DAYS), today + timedelta(days=MAX_BOOKING_DAYS)
    if day_text == "invalid" or not first <= datetime.fromisoformat(day_text).date() <= last:
        state.pop("day", None)
        state.pop("options", None)
        _save(conn, channel, owner_hash, state)
        if day_text == "invalid":
            return _reply("reschedule", f"Não consegui identificar uma data válida. Para remarcar, escolha uma data entre {first:%d/%m} e {last:%d/%m}. Qual prefere?")
        requested = datetime.fromisoformat(day_text).date()
        return _reply("reschedule", f"A data {requested:%d/%m} fica fora do período disponível para remarcação. No momento, posso oferecer datas entre {first:%d/%m} e {last:%d/%m}. Qual data prefere?")
    preference = state.get("preference")
    if preference and preference["invalid"]:
        _save(conn, channel, owner_hash, state)
        return _reply("reschedule", "Não consegui interpretar o horário com segurança. Diga, por exemplo, às 15h, depois das 15h ou à tarde.")
    slots = reschedule_options(booking["id"], booking["customer_phone"], datetime.fromisoformat(day_text).date(), min_notice_hours=3)
    choices = _three_options(_preference_slots(slots, preference), latest=bool(preference and preference["latest"]))
    state["options"], state["stage"] = choices, "options"
    _save(conn, channel, owner_hash, state)
    if not choices:
        requested = datetime.fromisoformat(day_text).date()
        if requested.weekday() == 6:
            return _reply("reschedule", f"{requested:%d/%m} cai em um domingo, quando a barbearia está fechada. Escolha outra data entre {first:%d/%m} e {last:%d/%m} e consulto os horários disponíveis.")
        return _reply("reschedule", "Não encontrei horários disponíveis para essa escolha. Qual outra data, horário ou período prefere?", tools=("buscar_opcoes_remarcacao",))
    lines = [f"{index}. {datetime.fromisoformat(slot['starts_at']):%d/%m/%Y às %H:%M} com {slot['barber']}" for index, slot in enumerate(choices, 1)]
    return _reply("reschedule", "Encontrei estas opções para remarcar:\n\n" + "\n".join(lines) + "\n\nEscolha pelo número ou escreva o horário. Depois vou pedir sua confirmação final.", tools=("buscar_opcoes_remarcacao",))


def _choose_slot(text, options):
    index = re.fullmatch(r"(?:opcao\s*)?([1-3])[.! ]*", text)
    if index:
        return options[int(index[1]) - 1] if int(index[1]) <= len(options) else None
    clock = text
    if re.fullmatch(r"\d{1,2}", clock):
        clock += "h"
    preference = _time_preference(clock)
    if not preference or preference["invalid"] or not preference["constraint"] or preference["constraint"][0] != "exact":
        return None
    day = _requested_day(text, datetime.now(TZ).date())
    matches = _preference_slots(options, preference)
    if day:
        matches = [slot for slot in matches if day != "invalid" and datetime.fromisoformat(slot["starts_at"]).date() == day]
    return matches[0] if len(matches) == 1 else None


def handle_booking_message(messages, channel, owner_key):
    """Handle only trusted ownership; message text can never establish identity."""
    latest = next((item.get("content", "") for item in reversed(messages) if item.get("role") == "user"), "")
    text = normalize_text(latest)
    action = _intent(text)
    if not owner_key or not isinstance(owner_key, str):
        return _reply(action, "Abra Meus horários no site e confirme seu telefone pelo Telegram para localizar sua reserva.") if action else None
    owner_hash = sha256(owner_key.encode("utf-8")).hexdigest()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        state, expired = _load(conn, channel, owner_hash)
        if expired and (_YES.fullmatch(text) or _NO.fullmatch(text)):
            return _reply("consult", "A confirmação expirou. Peça novamente a consulta, o cancelamento ou a remarcação para revisar os dados.")
        if state and (_NO.fullmatch(text) or (state["action"] == "reschedule" and re.search(r"\b(?:cancelar|cancela|desmarcar)\b", text))):
            _clear(conn, channel, owner_hash)
            return _reply(state["action"], "Tudo bem, encerrei este pedido. Sua reserva não foi alterada.", outcome="aborted")
        if action:
            # Um pedido novo substitui a proposta anterior.
            # "Sim" só vale para a proposta atual.
            state = None
        if state and state["action"] == "consult" and not action:
            _clear(conn, channel, owner_hash)
            return None
        if state is None and not action:
            return None
        try:
            bookings = [dict(item) for item in customer_access.list_owned_bookings(channel, owner_key, conn=conn)
                        if item["status"] == "confirmed" and datetime.fromisoformat(item["starts_at"]) > datetime.now(TZ)]
            if not bookings:
                _clear(conn, channel, owner_hash)
                return {**_reply(action or state["action"], "Não encontrei reservas ativas vinculadas a esta conversa. Confirme seu telefone pelo Telegram para localizar reservas feitas no site ou em outro aparelho, sem precisar da equipe."), "needs_verification": True}
            if state is None:
                state = {"action": action, "stage": "select", "booking_ids": [booking["id"] for booking in bookings]}
                if action == "reschedule":
                    _remember_preferences(state, text)
                if action == "consult":
                    _save(conn, channel, owner_hash, state)
                    return _reply(action, "Suas reservas vinculadas a esta conversa:\n\n" + _list_text(bookings) + "\n\nSe quiser, peça para cancelar ou remarcar uma reserva pelo número #.", tools=("consultar_reservas_proprias",))
                selected = _select_booking(text, bookings)
                explicit = bool(re.search(r"#\s*\d|\b(?:reserva|agendamento)\s+(?:id\s*)?\d", text))
                if not selected and len(bookings) == 1 and not explicit:
                    selected = bookings[0]
            elif state["stage"] == "select":
                choices = [booking for booking in bookings if booking["id"] in state["booking_ids"]]
                selected = _select_booking(text, choices)
                if state["action"] == "consult":
                    return _reply("consult", "Para mudar uma reserva, diga cancelar ou remarcar e informe seu número #.")
            else:
                selected = None
            if state["stage"] == "select":
                if selected is None:
                    _save(conn, channel, owner_hash, state)
                    return _reply(state["action"], "Qual reserva você quer " + ("cancelar" if state["action"] == "cancel" else "remarcar") + "? Escolha o número da lista ou escreva reserva #id:\n\n" + _list_text(bookings))
                booking = _trusted_booking(conn, channel, owner_key, selected["id"])
                state.update({"booking_id": booking["id"], "snapshot": _snapshot(booking), "stage": "confirm" if state["action"] == "cancel" else "date"})
                if state["action"] == "cancel":
                    _save(conn, channel, owner_hash, state)
                    return _reply("cancel", _confirm_text("cancel", booking))
                return _options(conn, channel, owner_hash, state, booking, "")
            booking = _trusted_booking(conn, channel, owner_key, state["booking_id"])
            if _snapshot(booking) != state["snapshot"]:
                _clear(conn, channel, owner_hash)
                return _reply(state["action"], "Esta reserva mudou desde a proposta. Consulte novamente seus horários para revisar os dados antes de confirmar.", outcome="stale")
            if state["stage"] == "confirm":
                if not _YES.fullmatch(text):
                    return _reply(state["action"], _confirm_text(state["action"], booking, state.get("slot")))
                if state["action"] == "cancel":
                    cancel_appointment(booking["id"], booking["customer_phone"], True, min_notice_hours=3, conn=conn)
                    content, tool = f"Cancelamento confirmado da {_describe(booking)}.", "cancelar_agendamento"
                else:
                    result = reschedule_appointment(booking["id"], booking["customer_phone"], state["slot"]["starts_at"], True, min_notice_hours=3, conn=conn)
                    content, tool = f"Remarcação confirmada: {_describe(result)}.", "remarcar_agendamento"
                _clear(conn, channel, owner_hash)
                return _reply(state["action"], content, outcome="completed", tools=(tool,))
            if state["stage"] == "options":
                slot = _choose_slot(text, state.get("options", []))
                if slot:
                    state.update({"stage": "confirm", "slot": slot})
                    _save(conn, channel, owner_hash, state)
                    return _reply("reschedule", _confirm_text("reschedule", booking, slot))
            return _options(conn, channel, owner_hash, state, booking, text)
        except BookingError as exc:
            _clear(conn, channel, owner_hash)
            return _reply(action or (state or {}).get("action"), f"Não concluí a alteração. {exc} A equipe pode ajudar.", outcome="rejected")
