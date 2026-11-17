"""Telegram long-polling bot for FAQs, availability, and confirmed bookings."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
import json
import logging
import re
import sys
import time
from typing import Any

import requests

from barber_bot.assistant import run_agent
from barber_bot.config import (
    MAX_BOOKING_DAYS,
    MIN_BOOKING_DAYS,
    OPENAI_API_KEY,
    TELEGRAM_BOT_TOKEN,
)
from barber_bot.db import connect, initialize_database
from barber_bot.presentation import format_price
from barber_bot.schedule import (
    BookingError,
    TZ,
    availability,
    create_appointment,
    list_barbers,
    list_services,
)

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
            if datetime.now(timezone.utc) - updated > SESSION_TTL:
                conn.execute("DELETE FROM telegram_sessions WHERE chat_id=?", (chat_id,))
                return {}
            return json.loads(row["state_json"])
        except (ValueError, TypeError, json.JSONDecodeError):
            conn.execute("DELETE FROM telegram_sessions WHERE chat_id=?", (chat_id,))
            return {}


def _clear_state(chat_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM telegram_sessions WHERE chat_id=?", (chat_id,))


def _keyboard(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": text, "callback_data": data} for text, data in row] for row in rows]}


def send_message(chat_id: str, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    chunks = [text[i:i + MAX_REPLY_LENGTH] for i in range(0, max(1, len(text)), MAX_REPLY_LENGTH)]
    for index, chunk in enumerate(chunks):
        payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
        if index == len(chunks) - 1 and reply_markup:
            payload["reply_markup"] = reply_markup
        telegram_call("sendMessage", payload)


def _main_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [[{"text": "📅 Agendar"}, {"text": "🕒 Consultar horários"}]],
        "resize_keyboard": True,
        "is_persistent": True,
    }


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
    send_message(chat_id, "Para qual dia? Escolha uma das datas disponíveis:", _keyboard(rows))


def _start_flow(chat_id: str, mode: str) -> None:
    state = {"mode": mode, "step": "service", "history": []}
    _save_state(chat_id, state)
    services = list_services()
    rows = [[(f"{s['name']} · {format_price(s['price'])}", f"service:{s['id']}")] for s in services]
    if not rows:
        _clear_state(chat_id)
        send_message(chat_id, "Ainda não há serviços cadastrados.")
        return
    intro = "Vamos agendar. Primeiro, escolha o serviço:" if mode == "booking" else "Para consultar horários, escolha o serviço:"
    send_message(chat_id, intro, _keyboard(rows))


def _show_barbers(chat_id: str, state: dict[str, Any]) -> None:
    state["step"] = "barber"
    _save_state(chat_id, state)
    rows = [[(barber["name"], f"barber:{barber['id']}")] for barber in list_barbers()]
    rows.append([("Qualquer profissional", "barber:0")])
    send_message(chat_id, "Tem preferência por profissional?", _keyboard(rows))


def _slot_text(slot: dict[str, Any]) -> str:
    start = datetime.fromisoformat(slot["starts_at"]).astimezone(TZ)
    return f"{start:%H:%M} · {slot['barber']}"


def _show_slots(chat_id: str, state: dict[str, Any], offset: int = 0) -> None:
    day = date.fromisoformat(state["date"])
    slots = availability(state["service_id"], day, state.get("barber_id"))
    if not slots:
        _clear_state(chat_id)
        send_message(chat_id, "Não encontrei horários livres nessa data. Tente /horarios ou /agendar para escolher outra.")
        return
    state["step"] = "slot"
    state["slot_offset"] = offset
    _save_state(chat_id, state)
    page = slots[offset:offset + SLOTS_PER_PAGE]
    rows = []
    for index, slot in enumerate(page, start=offset):
        label = _slot_text(slot)
        action = "Ver horário" if state["mode"] == "availability" else "Escolher"
        rows.append([(f"{label} · {action}", f"slot:{index}")])
    if offset + SLOTS_PER_PAGE < len(slots):
        rows.append([("Mais horários", "more")])
    service = next((s for s in list_services() if s["id"] == state["service_id"]), None)
    state["slots_count"] = len(slots)
    _save_state(chat_id, state)
    date_label = datetime.combine(day, datetime.min.time()).strftime("%d/%m/%Y")
    if state["mode"] == "availability":
        intro = f"Horários livres para {service['name'] if service else 'o serviço'} em {date_label}:\nToque em um horário se quiser iniciar a reserva."
    else:
        intro = f"Escolha um horário para {service['name'] if service else 'o serviço'} em {date_label}:"
    send_message(chat_id, intro, _keyboard(rows))


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
    send_message(chat_id, summary, _keyboard([[ ("✅ Confirmar agendamento", "confirm"), ("Cancelar", "abort") ]]))


def _book(chat_id: str, state: dict[str, Any]) -> None:
    try:
        booking = create_appointment(
            state["customer_name"], state["customer_phone"], state["service_id"],
            state["barber_id"], state["starts_at"], True,
            expected_price=state["expected_price"], expected_duration=state["expected_duration"],
        )
    except BookingError as exc:
        _clear_state(chat_id)
        send_message(chat_id, f"Não consegui concluir: {exc}\nConsulte os horários novamente com /horarios.")
        return

    # Persist completion before replying so repeated confirmation clicks do not create a second booking.
    completed = {"step": "completed", "appointment_id": booking["id"], "access_code": booking["access_code"]}
    _save_state(chat_id, completed)
    start = datetime.fromisoformat(booking["starts_at"]).astimezone(TZ)
    send_message(
        chat_id,
        "✅ Agendamento confirmado!\n"
        f"Protocolo: #{booking['id']}\n"
        f"{booking['service']} com {booking['barber']}\n"
        f"{start:%d/%m/%Y às %H:%M} · {format_price(booking['price'])}\n\n"
        f"Código privado para consultar ou alterar pelo site: {booking['access_code']}\n"
        "Guarde esse código e não o compartilhe. Para iniciar outra reserva, envie /agendar.",
        _main_keyboard(),
    )


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
    data = str(callback.get("data", ""))
    if data == "abort":
        _clear_state(chat_id)
        send_message(chat_id, "Agendamento cancelado. Se precisar, envie /agendar.", _main_keyboard())
        return
    if state.get("step") == "completed" and data == "confirm":
        send_message(chat_id, "Esse agendamento já foi confirmado. Para fazer outro, envie /agendar.", _main_keyboard())
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
        state["barber_id"] = barber_id or None
        state["slot_offset"] = 0
        _show_slots(chat_id, state)
    elif data == "more" and state.get("step") == "slot":
        _show_slots(chat_id, state, state.get("slot_offset", 0) + SLOTS_PER_PAGE)
    elif data.startswith("slot:") and state.get("step") == "slot":
        index = int(data.split(":", 1)[1])
        slots = availability(state["service_id"], date.fromisoformat(state["date"]), state.get("barber_id"))
        if index < 0 or index >= len(slots):
            _show_slots(chat_id, state, 0)
            return
        slot = slots[index]
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

    if command in {"/start", "/help"}:
        _clear_state(chat_id)
        send_message(
            chat_id,
            "Olá! Sou o assistente da Barbearia Imperial. Posso responder dúvidas, consultar horários e fazer reservas.\n\n"
            "Use /horarios para consultar disponibilidade, /agendar para reservar ou /cancelar para interromper um fluxo. "
            "Para proteger seus dados, o agendamento deve ser feito nesta conversa privada.",
            _main_keyboard(),
        )
        return
    if command in {"/cancelar", "/cancel"} or raw_text.casefold() == "cancelar":
        _clear_state(chat_id)
        send_message(chat_id, "Tudo bem, interrompi o fluxo. Envie /agendar ou /horarios quando quiser.", _main_keyboard())
        return
    if command == "/agendar" or raw_text.casefold() in {"📅 agendar", "agendar", "quero agendar"}:
        _start_flow(chat_id, "booking")
        return
    if command in {"/horarios", "/disponibilidade"} or raw_text.casefold() in {"🕒 consultar horários", "consultar horários", "consultar disponibilidade"}:
        _start_flow(chat_id, "availability")
        return

    state = _load_state(chat_id)
    step = state.get("step")
    if step == "name":
        name = raw_text
        if not name and contact:
            name = str(contact.get("first_name", "")).strip()
        if not name or len(name) > 120:
            send_message(chat_id, "Envie um nome com até 120 caracteres. Para sair, use /cancelar.")
            return
        state["customer_name"] = name
        state["step"] = "phone"
        _save_state(chat_id, state)
        send_message(
            chat_id,
            "Agora envie seu telefone com DDD ou use o botão para compartilhá-lo.\n"
            "Ele será usado para identificar esta reserva no painel.",
            _phone_keyboard(),
        )
        return
    if step == "phone":
        if contact:
            if str(contact.get("user_id", "")) != sender_id:
                send_message(chat_id, "Compartilhe somente seu próprio contato ou digite seu telefone.")
                return
            phone = str(contact.get("phone_number", "")).strip()
        else:
            phone = raw_text
        digits = re.sub(r"\D", "", phone)
        if not 8 <= len(digits) <= 15:
            send_message(chat_id, "Não consegui validar o telefone. Envie o número com DDD ou compartilhe seu contato.", _phone_keyboard())
            return
        state["customer_phone"] = phone
        _show_confirmation(chat_id, state)
        return

    if step in {"service", "date", "barber", "slot", "confirm"}:
        prompts = {
            "service": "Escolha um serviço usando os botões ou envie /cancelar.",
            "date": "Escolha uma data usando os botões ou envie /cancelar.",
            "barber": "Escolha um profissional usando os botões ou envie /cancelar.",
            "slot": "Escolha um horário usando os botões ou envie /cancelar.",
            "confirm": "Use os botões para confirmar ou cancelar a reserva.",
        }
        send_message(chat_id, prompts[step])
        return

    if not raw_text:
        send_message(chat_id, "Envie uma pergunta ou use /agendar e /horarios.", _main_keyboard())
        return
    if len(raw_text) > MAX_CHAT_LENGTH:
        send_message(chat_id, "A mensagem ficou longa demais. Envie uma pergunta mais curta.")
        return
    if not OPENAI_API_KEY:
        send_message(chat_id, "O atendimento com IA está desativado no momento. Você ainda pode usar /horarios e /agendar.", _main_keyboard())
        return
    if _rate_limited(chat_id):
        send_message(chat_id, "Você enviou várias mensagens em pouco tempo. Aguarde um minuto e tente novamente.")
        return

    history = state.get("history", []) if state.get("step") in {"chat", "completed"} else []
    history = [*history, {"role": "user", "content": raw_text}][-10:]
    try:
        result = run_agent(history, channel="telegram")
        answer = str(result.get("content", "Não consegui formular uma resposta agora."))
    except Exception:
        log.warning("Falha na resposta de IA para uma conversa do Telegram.")
        answer = "Não consegui consultar a IA agora. Você pode tentar novamente ou usar /horarios e /agendar."
    history.append({"role": "assistant", "content": answer})
    _save_state(chat_id, {"step": "chat", "history": history[-10:]})
    send_message(chat_id, answer, _main_keyboard())


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
            {"command": "help", "description": "Ver ajuda"},
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
