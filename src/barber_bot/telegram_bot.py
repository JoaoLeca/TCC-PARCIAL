"""Telegram long-polling bot for FAQs, availability, and confirmed bookings."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
import json
import logging
import re
from secrets import token_hex
import sys
import time
from typing import Any

import requests

from barber_bot.assistant import run_agent
from barber_bot.barber_telegram import (
    barber_appointment, barber_appointments, link_barber_telegram, notify_barber,
)
from barber_bot.booking_conversation import handle_booking_message
from barber_bot.config import (
    MAX_BOOKING_DAYS,
    MIN_BOOKING_DAYS,
    TELEGRAM_BOT_TOKEN,
)
from barber_bot.db import connect, initialize_database
from barber_bot.conversation import sanitize_chat_messages
from barber_bot.customer_access import identification_code, public_appointment, require_owned_booking
from barber_bot.presentation import format_price
from barber_bot.schedule import (
    BookingError,
    TZ,
    availability,
    create_appointment,
    get_booking_request,
    list_barbers,
    list_services,
)
from barber_bot.telemetry import record_booking, record_handoff, record_interaction
from barber_bot.support import SupportError, create_support_request, get_customer_request, update_support_contact
from barber_bot.customer_recovery import RecoveryError, issue_telegram_code, verify_telegram_contact

log = logging.getLogger("barber_bot.telegram")
API_ROOT = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN
SESSION_TTL = timedelta(minutes=30)
SLOTS_PER_PAGE = 10
MAX_CHAT_LENGTH = 2000
MAX_REPLY_LENGTH = 3900
_recent_messages: dict[str, deque[float]] = defaultdict(deque)
_http = requests.Session()


class TelegramError(RuntimeError):
    pass


def telegram_call(method: str, payload: dict[str, Any] | None = None, *, timeout: int = 15) -> Any:
    try:
        response = _http.post(f"{API_ROOT}/{method}", json=payload or {}, timeout=timeout)
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise TelegramError(f"Falha de comunicação com o Telegram ({method}).") from exc
    if not response.ok or not result.get("ok"):
        description = result.get("description", "erro desconhecido")
        raise TelegramError(f"Telegram {method}: {description}")
    return result.get("result")


def _save_state(chat_id: str, state: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO telegram_sessions(chat_id,state_json,updated_at)
               VALUES (?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(chat_id) DO UPDATE SET state_json=excluded.state_json,
                 updated_at=CURRENT_TIMESTAMP""",
            (chat_id, json.dumps(state, ensure_ascii=False)),
        )


def _load_state(chat_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            "SELECT state_json,updated_at FROM telegram_sessions WHERE chat_id=?", (chat_id,)
        ).fetchone()
        if not row:
            return {}
        try:
            updated = datetime.fromisoformat(row["updated_at"])
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            state = json.loads(row["state_json"])
            if not isinstance(state, dict):
                raise ValueError("O estado da conversa é inválido.")
            if "access_code" in state:
                # Versões antigas salvavam o segredo em sessões concluídas.
                state.pop("access_code")
                _save_state(chat_id, state)
            if state.get("step") == "confirm" and state.get("request_key"):
                appointment_id = get_booking_request(f"telegram:{chat_id}:{state['request_key']}")
                if appointment_id is not None:
                    state = {"step": "completed", "appointment_id": appointment_id,
                             "receipt_pending": True, "callback_token": state.get("callback_token")}
                    _save_state(chat_id, state)
                    record_booking(f"telegram:{chat_id}", appointment_id, channel="telegram")
            if datetime.now(timezone.utc) - updated > SESSION_TTL:
                if state.get("appointment_id"):
                    state = {"step": "completed", **_receipt_fields(state)}
                    _save_state(chat_id, state)
                    return state
                conn.execute("DELETE FROM telegram_sessions WHERE chat_id=?", (chat_id,))
                return {}
            return state
        except (ValueError, TypeError, json.JSONDecodeError):
            conn.execute("DELETE FROM telegram_sessions WHERE chat_id=?", (chat_id,))
            return {}


def _clear_state(chat_id: str) -> None:
    state = _load_state(chat_id)
    if state.get("appointment_id"):
        _save_state(chat_id, {"step": "completed", **_receipt_fields(state)})
        return
    with connect() as conn:
        conn.execute("DELETE FROM telegram_sessions WHERE chat_id=?", (chat_id,))


def _receipt_fields(state: dict[str, Any]) -> dict[str, Any]:
    return {key: state[key] for key in ("appointment_id", "receipt_pending") if key in state}


def _discard_management_proposal(chat_id: str) -> None:
    # Um "sim" não confirma um pedido que já foi substituído.
    handle_booking_message([{"role": "user", "content": "não"}], "telegram", chat_id)


def _keyboard(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": text, "callback_data": data} for text, data in row] for row in rows]}


def _flow_keyboard(chat_id: str, state: dict[str, Any], rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    # Cada menu tem seu token para impedir escolhas de menus antigos.
    state["callback_token"] = token_hex(6)
    _save_state(chat_id, state)
    return _keyboard([[(label, f"{state['callback_token']}:{action}") for label, action in row] for row in rows])


def send_message(chat_id: str, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    chunks = [text[i:i + MAX_REPLY_LENGTH] for i in range(0, max(1, len(text)), MAX_REPLY_LENGTH)]
    for index, chunk in enumerate(chunks):
        payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
        if index == len(chunks) - 1 and reply_markup:
            payload["reply_markup"] = reply_markup
        telegram_call("sendMessage", payload)


def _main_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [[{"text": "📅 Agendar"}, {"text": "🕒 Consultar horários"}],
                     [{"text": "🗓 Minha agenda"}]],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def _show_barber_agenda(chat_id: str) -> None:
    barber, appointments = barber_appointments(chat_id)
    if barber is None:
        send_message(chat_id, "Esta conta não está vinculada a um barbeiro. Use /vincular e compartilhe o contato cadastrado pela equipe.", _main_keyboard())
        return
    if not appointments:
        send_message(chat_id, f"Não há próximos agendamentos para {barber['name']}.", _main_keyboard())
        return
    rows = []
    for item in appointments:
        start = datetime.fromisoformat(item["starts_at"]).astimezone(TZ)
        rows.append([(f"{start:%d/%m %H:%M} · {item['service']}", f"agenda:view:{item['id']}")])
    state = {"step": "barber_agenda"}
    send_message(chat_id, f"Próximos agendamentos de {barber['name']}. Toque em um para ver os detalhes:",
                 _flow_keyboard(chat_id, state, rows))


def _private_chat(update: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    message = update.get("message")
    if not message:
        return None
    chat = message.get("chat", {})
    if chat.get("type") != "private":
        return None
    return str(chat.get("id")), message


def _send_dates(chat_id: str, state: dict[str, Any]) -> None:
    now = datetime.now(TZ).date()
    options = []
    for offset in range(max(1, MIN_BOOKING_DAYS), MAX_BOOKING_DAYS + 1):
        day = now + timedelta(days=offset)
        if day.weekday() < 6:
            weekdays = ("seg", "ter", "qua", "qui", "sex", "sáb")
            options.append((f"{weekdays[day.weekday()]} {day:%d/%m}", f"day:{day.isoformat()}"))
    if not options:
        _clear_state(chat_id)
        send_message(chat_id, "Não há datas abertas para reserva no momento. Fale com a equipe da barbearia.")
        return
    state["step"] = "date"
    _save_state(chat_id, state)
    rows = [[item] for item in options]
    send_message(chat_id, "Para qual dia? Escolha uma das datas disponíveis:", _flow_keyboard(chat_id, state, rows))


def _start_flow(chat_id: str, mode: str) -> None:
    _discard_management_proposal(chat_id)
    state = {"mode": mode, "step": "service", "history": [], "request_key": token_hex(16),
             **_receipt_fields(_load_state(chat_id))}
    _save_state(chat_id, state)
    services = list_services()
    rows = [[(f"{s['name']} · {format_price(s['price'])}", f"service:{s['id']}")] for s in services]
    if not rows:
        _clear_state(chat_id)
        send_message(chat_id, "Ainda não há serviços cadastrados.")
        return
    intro = "Vamos agendar. Primeiro, escolha o serviço:" if mode == "booking" else "Para consultar horários, escolha o serviço:"
    send_message(chat_id, intro, _flow_keyboard(chat_id, state, rows))


def _show_barbers(chat_id: str, state: dict[str, Any]) -> None:
    state["step"] = "barber"
    _save_state(chat_id, state)
    rows = [[(barber["name"], f"barber:{barber['id']}")] for barber in list_barbers()]
    rows.append([("Qualquer profissional", "barber:0")])
    send_message(chat_id, "Tem preferência por profissional?", _flow_keyboard(chat_id, state, rows))


def _slot_text(slot: dict[str, Any]) -> str:
    start = datetime.fromisoformat(slot["starts_at"]).astimezone(TZ)
    return f"{start:%H:%M} · {slot['barber']}"


def _show_slots(chat_id: str, state: dict[str, Any], offset: int = 0) -> None:
    day = date.fromisoformat(state["date"])
    slots = availability(state["service_id"], day, state.get("barber_id"))
    if not slots:
        state["step"] = "no_slots"
        state.pop("slot_choices", None)
        rows = [[("Outro profissional", "change_barber"), ("Outro dia", "change_day")]]
        send_message(chat_id, "Não encontrei horários livres nessa data para essa escolha. "
                     "Mantive o serviço selecionado. Podemos tentar outro profissional ou outro dia.",
                     _flow_keyboard(chat_id, state, rows))
        return
    if offset >= len(slots):
        offset = 0
    state["step"] = "slot"
    state["slot_offset"] = offset
    _save_state(chat_id, state)
    page = slots[offset:offset + SLOTS_PER_PAGE]
    state["slot_choices"] = {str(index): slot for index, slot in enumerate(page, start=offset)}
    rows = []
    for index, slot in enumerate(page, start=offset):
        label = _slot_text(slot)
        action = "Ver horário" if state["mode"] == "availability" else "Escolher"
        rows.append([(f"{label} · {action}", f"slot:{index}")])
    if offset + SLOTS_PER_PAGE < len(slots):
        rows.append([("Mais horários", "more")])
    rows.append([("Outro profissional", "change_barber"), ("Outro dia", "change_day")])
    service = next((s for s in list_services() if s["id"] == state["service_id"]), None)
    state["slots_count"] = len(slots)
    _save_state(chat_id, state)
    date_label = datetime.combine(day, datetime.min.time()).strftime("%d/%m/%Y")
    if state["mode"] == "availability":
        intro = f"Horários livres para {service['name'] if service else 'o serviço'} em {date_label}:\nToque em um horário se quiser iniciar a reserva."
    else:
        intro = f"Escolha um horário para {service['name'] if service else 'o serviço'} em {date_label}:"
    send_message(chat_id, intro, _flow_keyboard(chat_id, state, rows))


def _ask_name(chat_id: str, state: dict[str, Any], slot: dict[str, Any]) -> None:
    state.update({"step": "name", "starts_at": slot["starts_at"], "ends_at": slot["ends_at"],
                  "barber_id": slot["barber_id"], "barber_name": slot["barber"]})
    _save_state(chat_id, state)
    send_message(chat_id, "Qual nome devo colocar na reserva? Envie /cancelar para interromper.")


def _phone_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [[{"text": "Compartilhar meu telefone", "request_contact": True}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def _begin_support(chat_id: str, state: dict[str, Any], ticket: dict[str, Any] | None = None) -> None:
    _discard_management_proposal(chat_id)
    if ticket is None:
        ticket = create_support_request(f"telegram:{chat_id}", "telegram", "Cliente solicitou atendimento humano no Telegram.")
        record_handoff(f"telegram:{chat_id}", ticket["id"], channel="telegram")
    if ticket.get("customer_name") and ticket.get("customer_phone"):
        send_message(chat_id, f"Seu atendimento está na fila da equipe. Protocolo #{ticket['id']}.", _main_keyboard())
        return
    state = {"step": "support_name", "ticket_id": ticket["id"], **_receipt_fields(state)}
    _save_state(chat_id, state)
    send_message(chat_id, f"Solicitação #{ticket['id']} registrada para a equipe. "
                 "Qual é seu nome para o retorno? Use /cancelar para interromper o preenchimento.")


def _show_confirmation(chat_id: str, state: dict[str, Any]) -> None:
    service = next((s for s in list_services() if s["id"] == state.get("service_id")), None)
    if not service:
        _clear_state(chat_id)
        send_message(chat_id, "O serviço não está mais disponível. Inicie novamente com /agendar.")
        return
    start = datetime.fromisoformat(state["starts_at"]).astimezone(TZ)
    state["step"] = "confirm"
    state["expected_price"] = service["price"]
    state["expected_duration"] = service["duration_minutes"]
    _save_state(chat_id, state)
    phone_last_four = re.sub(r"\D", "", state["customer_phone"])[-4:]
    summary = (
        "Confira os dados antes de confirmar:\n"
        f"Serviço: {service['name']} ({service['duration_minutes']} min)\n"
        f"Profissional: {state['barber_name']}\n"
        f"Data: {start:%d/%m/%Y às %H:%M}\n"
        f"Valor: {format_price(service['price'])}\n"
        f"Nome: {state['customer_name']}\n"
        f"Telefone: final {phone_last_four}"
    )
    send_message(chat_id, "Contato registrado.", {"remove_keyboard": True})
    send_message(chat_id, summary, _flow_keyboard(chat_id, state, [[ ("✅ Confirmar agendamento", "confirm"), ("Interromper", "abort") ]]))


def _book(chat_id: str, state: dict[str, Any]) -> None:
    try:
        booking = create_appointment(
            state["customer_name"], state["customer_phone"], state["service_id"],
            state["barber_id"], state["starts_at"], True,
            expected_price=state["expected_price"], expected_duration=state["expected_duration"],
            request_key=f"telegram:{chat_id}:{state['request_key']}",
            owner_channel="telegram", owner_key=chat_id,
        )
    except BookingError as exc:
        send_message(chat_id, f"Não consegui concluir: {exc}\nVamos consultar os horários novamente.")
        _show_slots(chat_id, state)
        return

    notify_barber(booking)

    # Salva a reserva antes da resposta para evitar duplicidade.
    completed = {"step": "completed", "appointment_id": booking["id"],
                 "receipt_pending": True, "callback_token": state["callback_token"]}
    _save_state(chat_id, completed)
    _deliver_receipt(chat_id, completed)


def _deliver_receipt(chat_id: str, state: dict[str, Any]) -> None:
    # Confere a reserva salva e confirma que pertence a este chat.
    with connect() as conn:
        row = conn.execute("""
            SELECT a.*,b.name AS barber,s.name AS service,
                   COALESCE(a.price_at_booking,s.price) AS price
            FROM appointments a JOIN barbers b ON b.id=a.barber_id
            JOIN services s ON s.id=a.service_id WHERE a.id=?
        """, (state["appointment_id"],)).fetchone()
        if row is not None:
            owned = conn.execute("SELECT 1 FROM booking_owners WHERE appointment_id=?",
                                 (row["id"],)).fetchone()
            if owned:
                try:
                    booking = require_owned_booking(row["id"], "telegram", chat_id, conn=conn)
                except BookingError:
                    booking = None
            else:
                # Reservas antigas sem vínculo só podem ser recuperadas neste chat.
                booking = dict(row)
                booking["identification_code"] = identification_code(row["customer_phone"])
        else:
            booking = None
    if booking is None:
        send_message(chat_id, "Não encontrei o comprovante desta conversa. Fale com a equipe da barbearia.")
        return
    booking = public_appointment(booking)
    state["receipt_pending"] = True
    _save_state(chat_id, state)
    # Atualiza o evento em cada tentativa sem criar registros duplicados.
    record_booking(f"telegram:{chat_id}", booking["id"], channel="telegram")
    start = datetime.fromisoformat(booking["starts_at"]).astimezone(TZ)
    heading = "Agendamento cancelado." if booking.get("status") == "cancelled" else "✅ Agendamento confirmado!"
    send_message(
        chat_id,
        f"{heading}\n\n"
        f"Protocolo: #{booking['id']}\n"
        f"{booking['service']} com {booking['barber']}\n"
        f"{start:%d/%m/%Y às %H:%M} · {format_price(booking['price'])}\n\n"
        "Para remarcar ou cancelar, escreva seu pedido nesta conversa.\n"
        "Para reenviar o comprovante, use /comprovante. Para iniciar outra reserva, envie /agendar.",
        _main_keyboard(),
    )
    state["receipt_pending"] = False
    _save_state(chat_id, state)


def _handle_callback(callback: dict[str, Any]) -> None:
    query_id = callback.get("id", "")
    try:
        telegram_call("answerCallbackQuery", {"callback_query_id": query_id})
    except TelegramError:
        pass
    message = callback.get("message", {})
    chat = message.get("chat", {})
    if chat.get("type") != "private":
        return
    chat_id = str(chat.get("id"))
    state = _load_state(chat_id)
    token, _, data = str(callback.get("data", "")).partition(":")
    if not token or token != state.get("callback_token"):
        send_message(chat_id, "Essa opção expirou. Use os botões mais recentes ou envie /agendar ou /horarios.", _main_keyboard())
        return
    try:
        _dispatch_callback(chat_id, state, data)
    except (ValueError, KeyError):
        send_message(chat_id, "Essa opção não está disponível. Use os botões mais recentes.")


def _dispatch_callback(chat_id: str, state: dict[str, Any], data: str) -> None:
    if data == "agenda:list" and state.get("step") == "barber_agenda":
        _show_barber_agenda(chat_id)
        return
    if data.startswith("agenda:view:") and state.get("step") == "barber_agenda":
        appointment_id = int(data.rsplit(":", 1)[1])
        item = barber_appointment(chat_id, appointment_id)
        if not item:
            send_message(chat_id, "Este agendamento não pertence à sua agenda ou não está mais ativo.", _main_keyboard())
            return
        start = datetime.fromisoformat(item["starts_at"]).astimezone(TZ)
        end = datetime.fromisoformat(item["ends_at"]).astimezone(TZ)
        message = (
            f"Agendamento #{item['id']}\n"
            f"Cliente: {item['customer_name']}\n"
            f"Serviço: {item['service']}\n"
            f"Horário: {start:%d/%m/%Y às %H:%M}–{end:%H:%M}\n"
            f"Valor: {format_price(item['price'])}"
        )
        send_message(chat_id, message, _flow_keyboard(
            chat_id, {"step": "barber_agenda"}, [[("← Voltar à agenda", "agenda:list")]]
        ))
        return
    _discard_management_proposal(chat_id)
    if state.get("step") == "completed":
        if data == "confirm":
            if state.get("receipt_pending"):
                _deliver_receipt(chat_id, state)
            else:
                send_message(chat_id, "Esse agendamento já foi confirmado. Use /comprovante para reenviar os dados ou /agendar para fazer outro.", _main_keyboard())
        else:
            send_message(chat_id, "Esse fluxo já terminou. A reserva confirmada continua ativa.", _main_keyboard())
        return
    if data == "abort":
        _clear_state(chat_id)
        send_message(chat_id, "Interrompi o fluxo. Se precisar, envie /agendar.", _main_keyboard())
        return
    if data == "change_day" and state.get("step") in {"slot", "no_slots"}:
        _send_dates(chat_id, state)
        return
    if data == "change_barber" and state.get("step") in {"slot", "no_slots"}:
        _show_barbers(chat_id, state)
        return
    if data.startswith("service:") and state.get("step") == "service":
        service_id = int(data.split(":", 1)[1])
        service = next((item for item in list_services() if item["id"] == service_id), None)
        if not service:
            _clear_state(chat_id)
            send_message(chat_id, "Esse serviço não está mais disponível. Tente /agendar novamente.")
            return
        state.update({"service_id": service_id, "service_name": service["name"]})
        _send_dates(chat_id, state)
    elif data.startswith("day:") and state.get("step") == "date":
        selected = date.fromisoformat(data.split(":", 1)[1])
        days_ahead = (selected - datetime.now(TZ).date()).days
        if not MIN_BOOKING_DAYS <= days_ahead <= MAX_BOOKING_DAYS or selected.weekday() >= 6:
            send_message(chat_id, "Essa data não está dentro do período permitido. Escolha uma das opções exibidas.")
            return
        state["date"] = selected.isoformat()
        _show_barbers(chat_id, state)
    elif data.startswith("barber:") and state.get("step") == "barber":
        barber_id = int(data.split(":", 1)[1])
        if barber_id and not any(item["id"] == barber_id for item in list_barbers()):
            raise ValueError("O profissional selecionado não existe.")
        state["barber_id"] = barber_id or None
        state["slot_offset"] = 0
        _show_slots(chat_id, state)
    elif data == "more" and state.get("step") == "slot":
        _show_slots(chat_id, state, state.get("slot_offset", 0) + SLOTS_PER_PAGE)
    elif data.startswith("slot:") and state.get("step") == "slot":
        index = str(int(data.split(":", 1)[1]))
        selected = state.get("slot_choices", {}).get(index)
        slots = availability(state["service_id"], date.fromisoformat(state["date"]), state.get("barber_id"))
        slot = next((item for item in slots if selected and all(
            item[key] == selected[key] for key in ("barber_id", "starts_at", "ends_at")
        )), None)
        if slot is None:
            send_message(chat_id, "Esse horário acabou de ser ocupado ou mudou. Escolha outra opção atualizada.")
            _show_slots(chat_id, state, 0)
            return
        if state["mode"] == "availability":
            state["mode"] = "booking"
        _ask_name(chat_id, state, slot)
    elif data == "confirm" and state.get("step") == "confirm":
        _book(chat_id, state)
    else:
        send_message(chat_id, "Essa opção expirou. Inicie novamente com /agendar ou /horarios.", _main_keyboard())


def _rate_limited(chat_id: str) -> bool:
    now = time.monotonic()
    recent = _recent_messages[chat_id]
    while recent and now - recent[0] > 60:
        recent.popleft()
    if len(recent) >= 8:
        return True
    recent.append(now)
    return False


def _handle_text(chat_id: str, message: dict[str, Any]) -> None:
    raw_text = str(message.get("text", "")).strip()
    contact = message.get("contact")
    sender_id = str(message.get("from", {}).get("id", ""))
    command = raw_text.split()[0].split("@", 1)[0].lower() if raw_text.startswith("/") else ""
    if command == "/vincular":
        _save_state(chat_id, {"step": "barber_link"})
        send_message(chat_id, "Para vincular sua conta ao cadastro do barbeiro, compartilhe seu próprio contato usando o botão abaixo.", _phone_keyboard())
        return
    if command == "/atendimento":
        ticket = get_customer_request(f"telegram:{chat_id}", "telegram")
        if ticket is None:
            send_message(chat_id, "Nenhuma solicitação de atendimento nesta conversa. Use /atendente para falar com a equipe.")
        else:
            status = {"open": "Aberto", "in_progress": "Em andamento", "resolved": "Resolvido"}[ticket["status"]]
            updated = datetime.fromisoformat(ticket["updated_at"])
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            updated = updated.astimezone(TZ)
            send_message(chat_id, f"Atendimento #{ticket['id']}\nSituação: {status}\n"
                         f"Atualizado em {updated:%d/%m/%Y às %H:%M}.\n"
                         "Use /atendimento para consultar novamente.")
        return
    state = _load_state(chat_id)
    if state.get("step") == "barber_link":
        if contact:
            try:
                barber_name = link_barber_telegram(chat_id, sender_id, contact)
            except ValueError as exc:
                send_message(chat_id, str(exc), _phone_keyboard())
                return
            _save_state(chat_id, {"step": "chat"})
            send_message(chat_id, f"Telegram vinculado ao cadastro de {barber_name}. Use /minhaagenda para consultar seus horários.", _main_keyboard())
            return
        if command != "/cancelar":
            send_message(chat_id, "Use o botão para compartilhar o próprio contato. Não envie o telefone como texto.", _phone_keyboard())
            return
    if command == "/minhaagenda" or raw_text.casefold() == "🗓 minha agenda":
        _show_barber_agenda(chat_id)
        return
    if command == "/start" and re.fullmatch(r"/start(?:@\w+)? recover_[A-Za-z0-9_-]{32}", raw_text):
        _discard_management_proposal(chat_id)
        _save_state(chat_id, {"step": "verify_reservations", "recovery_nonce": raw_text.split("recover_", 1)[1], **_receipt_fields(state)})
        send_message(chat_id, "Para receber o código solicitado no site, confirme seu próprio telefone pelo botão Compartilhar meu telefone. Não digite o número. Depois volte ao site e informe o código.", _phone_keyboard())
        return
    if command == "/verificar":
        _discard_management_proposal(chat_id)
        _save_state(chat_id, {"step": "verify_reservations", "requested_action": "meus agendamentos", **_receipt_fields(state)})
        send_message(chat_id, "Vamos localizar suas reservas. Confirme seu próprio contato em Compartilhar meu telefone. Nenhuma reserva será alterada nesta etapa.", _phone_keyboard())
        return
    if state.get("step") == "verify_reservations" and not command:
        if not contact:
            send_message(chat_id, "Para proteger sua reserva, confirme pelo botão Compartilhar meu telefone do Telegram. Um número digitado não confirma sua identidade. Para sair, use /cancelar.", _phone_keyboard())
            return
        try:
            if state.get("recovery_nonce"):
                code = issue_telegram_code(state["recovery_nonce"], chat_id, sender_id, contact)
                send_message(chat_id, f"Seu código de verificação para o site:\n\n{code}\n\nVálido por 5 minutos. Digite somente no site da barbearia que abriu esta verificação. Se você não solicitou este acesso, não compartilhe o código.", {"remove_keyboard": True})
                _clear_state(chat_id)
            else:
                verify_telegram_contact(chat_id, sender_id, contact)
                result = handle_booking_message([{"role": "user", "content": state.get("requested_action", "meus agendamentos")}], "telegram", chat_id)
                _clear_state(chat_id)
                content = result["content"] if result and not result.get("needs_verification") else "Não encontrei reservas ativas para o telefone confirmado. Você pode fazer um novo agendamento."
                send_message(chat_id, "Telefone confirmado.\n\n"+content, {"remove_keyboard": True})
        except RecoveryError as exc:
            send_message(chat_id, str(exc), _phone_keyboard())
        return
    if command == "/comprovante":
        if state.get("appointment_id"):
            _deliver_receipt(chat_id, state)
        else:
            send_message(chat_id, "Não há comprovante desta conversa. Use /agendar ou fale com a equipe.")
        return
    if state.get("receipt_pending"):
        _deliver_receipt(chat_id, state)

    if command in {"/start", "/ajuda", "/help"}:
        _discard_management_proposal(chat_id)
        _clear_state(chat_id)
        send_message(
            chat_id,
            "Olá! Sou o assistente da Barbearia Imperial. Posso responder dúvidas, consultar horários e fazer reservas.\n\n"
            "Use /horarios para consultar disponibilidade, /agendar para reservar ou /cancelar para interromper um fluxo. "
            "Use /atendente para solicitar a equipe e /atendimento para acompanhar seu protocolo. "
            "Barbeiros: use /vincular para associar sua conta e /minhaagenda para ver seus agendamentos. "
            "Para proteger seus dados, o agendamento deve ser feito nesta conversa privada.",
            _main_keyboard(),
        )
        return
    if command in {"/cancelar", "/cancel"} or (
        raw_text.casefold() == "cancelar" and state.get("step") in {"name", "phone", "support_name", "support_phone"}
    ):
        _discard_management_proposal(chat_id)
        _clear_state(chat_id)
        send_message(chat_id, "Tudo bem, interrompi o fluxo. Envie /agendar ou /horarios quando quiser.", _main_keyboard())
        return
    if command == "/atendente":
        _begin_support(chat_id, state)
        return
    if command == "/agendar" or raw_text.casefold() in {"📅 agendar", "agendar", "quero agendar"}:
        _start_flow(chat_id, "booking")
        return
    if command in {"/horarios", "/disponibilidade"} or raw_text.casefold() in {"🕒 consultar horários", "consultar horários", "consultar disponibilidade"}:
        _start_flow(chat_id, "availability")
        return

    step = state.get("step")
    if step not in {"name", "phone", "support_name", "support_phone"} and raw_text:
        if len(raw_text) > MAX_CHAT_LENGTH:
            send_message(chat_id, "A mensagem ficou longa demais. Envie uma pergunta mais curta.")
            return
        management_messages = sanitize_chat_messages([{"role": "user", "content": raw_text}])
        started = time.monotonic()
        management = handle_booking_message(management_messages, "telegram", chat_id)
        if management is not None:
            if management.get("needs_verification"):
                _save_state(chat_id, {"step": "verify_reservations", "requested_action": management_messages[-1]["content"], **_receipt_fields(state)})
                send_message(chat_id, "Posso localizar sua reserva pelo telefone confirmado do Telegram, sem precisar da equipe. Toque em Compartilhar meu telefone para confirmar seu próprio contato. Depois continuo seu pedido, com confirmação antes de alterar a reserva.", _phone_keyboard())
                return
            answer = sanitize_chat_messages([{"role": "assistant", "content": str(management["content"])}])[0]["content"]
            record_interaction(f"telegram:{chat_id}", management_messages,
                               {**management, "content": answer},
                               int((time.monotonic() - started) * 1000), channel="telegram")
            send_message(chat_id, answer)
            return

    if step in {"name", "support_name"}:
        name = raw_text
        if not name and contact:
            name = str(contact.get("first_name", "")).strip()
        if not name or len(name) > 120:
            send_message(chat_id, "Envie um nome com até 120 caracteres. Para sair, use /cancelar.")
            return
        state["customer_name"] = name
        state["step"] = "support_phone" if step == "support_name" else "phone"
        _save_state(chat_id, state)
        send_message(
            chat_id,
            "Agora envie seu telefone com DDD ou use o botão para compartilhá-lo.\n"
            "A equipe usará esse contato para atender sua solicitação." if step == "support_name" else
            "Agora envie seu telefone com DDD ou use o botão para compartilhá-lo.\n"
            "Ele será usado para identificar esta reserva no painel.",
            _phone_keyboard(),
        )
        return
    if step in {"phone", "support_phone"}:
        if contact:
            if str(contact.get("user_id", "")) != sender_id:
                send_message(chat_id, "Compartilhe somente seu próprio contato ou digite seu telefone.")
                return
            phone = str(contact.get("phone_number", "")).strip()
        else:
            phone = raw_text
        digits = re.sub(r"\D", "", phone)
        if not 10 <= len(digits) <= 15:
            send_message(chat_id, "Não consegui validar o telefone. Envie o número com DDD ou compartilhe seu contato.", _phone_keyboard())
            return
        if step == "support_phone":
            try:
                ticket = update_support_contact(f"telegram:{chat_id}", "telegram", state["ticket_id"],
                                                state["customer_name"], phone)
            except SupportError as exc:
                send_message(chat_id, str(exc), _phone_keyboard())
                return
            _clear_state(chat_id)
            send_message(chat_id, f"Contato registrado para a equipe. Seu protocolo é #{ticket['id']}. "
                         "A equipe retornará pelo telefone informado. Use /atendimento para consultar a situação.", _main_keyboard())
            return
        state["customer_phone"] = phone
        _show_confirmation(chat_id, state)
        return

    if step in {"service", "date", "barber", "slot", "confirm", "no_slots"}:
        prompts = {
            "service": "Escolha um serviço usando os botões ou envie /cancelar.",
            "date": "Escolha uma data usando os botões ou envie /cancelar.",
            "barber": "Escolha um profissional usando os botões ou envie /cancelar.",
            "slot": "Escolha um horário usando os botões ou envie /cancelar.",
            "confirm": "Use os botões para confirmar ou cancelar a reserva.",
            "no_slots": "Use os botões para tentar outro profissional ou outro dia, ou envie /cancelar.",
        }
        send_message(chat_id, prompts[step])
        return

    if not raw_text:
        send_message(chat_id, "Envie uma pergunta ou use /agendar e /horarios.", _main_keyboard())
        return
    if len(raw_text) > MAX_CHAT_LENGTH:
        send_message(chat_id, "A mensagem ficou longa demais. Envie uma pergunta mais curta.")
        return
    if _rate_limited(chat_id):
        send_message(chat_id, "Você enviou várias mensagens em pouco tempo. Aguarde um minuto e tente novamente.")
        return

    history = state.get("history", []) if state.get("step") in {"chat", "completed"} else []
    history = sanitize_chat_messages([*history, {"role": "user", "content": raw_text}][-10:])
    result: dict[str, Any] = {}
    started = time.monotonic()
    try:
        result = run_agent(history, channel="telegram")
        answer = str(result.get("content", "Não consegui formular uma resposta agora."))
    except Exception:
        log.warning("Falha na resposta de IA para uma conversa do Telegram.")
        answer = "Não consegui consultar a IA agora. Você pode tentar novamente ou usar /horarios e /agendar."
        result = {"content": answer, "outcome": "unavailable"}
    record_interaction(f"telegram:{chat_id}", history, result,
                       int((time.monotonic() - started) * 1000), channel="telegram")
    history = sanitize_chat_messages([*history, {"role": "assistant", "content": answer}])
    answer = history[-1]["content"]
    _save_state(chat_id, {"step": "chat", "history": history[-10:], **_receipt_fields(state)})
    send_message(chat_id, answer, _main_keyboard())
    if result.get("support_request"):
        _begin_support(chat_id, state, result["support_request"])


def handle_update(update: dict[str, Any]) -> None:
    if "callback_query" in update:
        _handle_callback(update["callback_query"])
        return
    item = _private_chat(update)
    if not item:
        message = update.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        if chat_id is not None:
            send_message(str(chat_id), "Para proteger os dados das reservas, fale comigo em uma conversa privada.")
        return
    chat_id, message = item
    _handle_text(chat_id, message)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("Configure TELEGRAM_BOT_TOKEN no arquivo .env antes de iniciar o bot.", file=sys.stderr)
        raise SystemExit(2)
    initialize_database()
    me = telegram_call("getMe")
    try:
        telegram_call("setMyCommands", {"commands": [
            {"command": "start", "description": "Iniciar o atendimento"},
            {"command": "horarios", "description": "Consultar disponibilidade"},
            {"command": "agendar", "description": "Fazer um agendamento"},
            {"command": "cancelar", "description": "Interromper o fluxo atual"},
            {"command": "comprovante", "description": "Reenviar o último comprovante"},
            {"command": "atendente", "description": "Solicitar atendimento humano"},
            {"command": "atendimento", "description": "Consultar situação do atendimento"},
            {"command": "vincular", "description": "Vincular conta do barbeiro"},
            {"command": "minhaagenda", "description": "Ver os próprios agendamentos"},
            {"command": "ajuda", "description": "Ver ajuda"},
        ]})
    except TelegramError:
        log.info("Não foi possível registrar os comandos no menu do Telegram.")
    print(f"Bot @{me.get('username', 'telegram')} iniciado. Pressione Ctrl+C para encerrar.")
    offset: int | None = None
    while True:
        try:
            payload: dict[str, Any] = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
            if offset is not None:
                payload["offset"] = offset
            updates = telegram_call("getUpdates", payload, timeout=35)
            for update in updates:
                try:
                    handle_update(update)
                except Exception as exc:
                    log.error("Falha ao processar atualização Telegram %s (%s).", update.get("update_id"), type(exc).__name__)
                offset = update["update_id"] + 1
        except KeyboardInterrupt:
            print("Bot Telegram encerrado.")
            return
        except TelegramError as exc:
            log.warning("%s", exc)
            time.sleep(3)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
