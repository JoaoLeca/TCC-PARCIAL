import os
import uuid
from calendar import Calendar, SUNDAY
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import streamlit as st

from barber_bot.config import MAX_BOOKING_DAYS, MIN_BOOKING_DAYS, SHOP_OPEN_DAYS, TIMEZONE
from barber_bot.calendar_dates import ribeirao_preto_holidays
from barber_bot.conversation import sanitize_chat_messages
from barber_bot.presentation import WEEKDAYS, format_price

API = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
MONTHS = ("Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto",
          "Setembro", "Outubro", "Novembro", "Dezembro")


def booking_detail_rows(booking):
    start = datetime.fromisoformat(booking["starts_at"])
    end = datetime.fromisoformat(booking["ends_at"])
    duration = (end - start) // timedelta(minutes=1)
    return (
        ("Serviço", booking["service"]),
        ("Barbeiro", booking["barber"]),
        ("Data e horário", f"{WEEKDAYS[start.weekday()]}, {start:%d/%m/%Y} às {start:%Hh%M}"),
        ("Duração", f"{duration} min"),
        ("Valor", format_price(booking["price"])),
    )


def show_booking_details(booking):
    details = dict(booking_detail_rows(booking))
    st.write(f"{details['Serviço']} com {details['Barbeiro']}")
    st.write(f"{details['Data e horário']} · {details['Duração']} · {details['Valor']}")


def receipt_text(booking, shop_name):
    details = "\n".join(f"{label} — {value}" for label, value in booking_detail_rows(booking))
    return (
        f"{shop_name}\nComprovante da reserva #{booking['id']}\n\n{details}\n\n"
        f"Código privado de acesso\n{booking['access_code']}\n\n"
        "Em Meus horários, use o telefone informado na reserva e este código privado.\n"
        "Guarde o comprovante em um local seguro.\n"
    )


def get_booking_data(endpoint, params=None, *, api_url=API):
    response = requests.get(f"{api_url}/{endpoint}", params=params, timeout=10)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=60, show_spinner=False)
def get_booking_catalog(api_url):
    return get_booking_data("catalog", api_url=api_url)


def render_booking_calendar(dates_and_slots, first_day, last_day, working_days):
    state = st.session_state
    chosen_date = state.get("booking_date")
    if not dates_and_slots.get(chosen_date):
        chosen_date = next((day for day, slots in dates_and_slots.items() if slots), None)
        if chosen_date != state.get("booking_date"):
            state.pop("booking_calendar_month", None)
        state.booking_date = chosen_date

    first_month, last_month = first_day.replace(day=1), last_day.replace(day=1)
    month = state.get("booking_calendar_month")
    if month is None or not first_month <= month <= last_month:
        month = (chosen_date or first_day).replace(day=1)
    previous_month = (month - timedelta(days=1)).replace(day=1)
    next_month = (month + timedelta(days=32)).replace(day=1)
    holidays = ribeirao_preto_holidays(month.year)

    with st.container(key="booking_calendar", width=440, border=True):
        st.markdown("#### Escolha uma data")
        if first_month == last_month:
            st.markdown(f"**{MONTHS[month.month - 1]} de {month.year}**")
        else:
            prev, title, following = st.columns([1, 4, 1], vertical_alignment="center", wrap=False)
            prev.button("‹", key="booking_previous_month", help="Mês anterior", disabled=month <= first_month,
                        on_click=state.__setitem__, args=("booking_calendar_month", previous_month))
            title.markdown(f"**{MONTHS[month.month - 1]} de {month.year}**")
            following.button("›", key="booking_next_month", help="Próximo mês", disabled=month >= last_month,
                             on_click=state.__setitem__, args=("booking_calendar_month", next_month))

        with st.container(key="booking_calendar_days", gap=None):
            for column, weekday in zip(st.columns(7, gap=None, wrap=False), ("Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb")):
                column.html(f'<div class="booking-weekday">{weekday}</div>')
            for week in Calendar(firstweekday=SUNDAY).monthdatescalendar(month.year, month.month):
                for column, day in zip(st.columns(7, gap=None, wrap=False), week):
                    if day.month == month.month:
                        count = len({slot["starts_at"] for slot in dates_and_slots.get(day, [])})
                        if not first_day <= day <= last_day:
                            status, reason = "outside", "Fora do período de reservas online"
                        elif day.weekday() not in SHOP_OPEN_DAYS:
                            status, reason = "closed", "Barbearia fechada neste dia da semana"
                        elif day.weekday() not in working_days:
                            status, reason = "closed", "Sem atendimento para o profissional escolhido"
                        elif not count:
                            status, reason = "unavailable", "Sem horários disponíveis para este serviço"
                        else:
                            status, reason = "available", f"{count} horários disponíveis"
                        if holiday := holidays.get(day):
                            status = "holiday"
                            reason = f"Feriado · {holiday}. {reason}"
                        with column.container(key=f"calendar_{status}_{day.isoformat()}"):
                            st.button(
                                str(day.day), key=f"booking_day_{day.isoformat()}", width="stretch",
                                type="primary" if day == chosen_date else "secondary",
                                disabled=not count, help=f"{day:%d/%m} · {reason}",
                                on_click=state.__setitem__, args=("booking_date", day),
                            )

        st.html('''<div class="booking-legend" aria-label="Legenda do calendário">
          <span><i class="available"></i>Com vagas</span>
          <span><i class="holiday"></i>Feriado</span>
          <span><i class="unavailable"></i>Sem vagas</span>
          <span><i class="closed"></i>Sem atendimento / fora do período</span>
        </div>''')
    return chosen_date


def save_booking_contact():
    st.session_state.booking_contact = {
        "name": st.session_state.get("booking_name", ""),
        "phone": st.session_state.get("booking_phone", ""),
    }


def select_chat_booking(service, slot):
    state = st.session_state
    if state.get("booking_pending"):
        state.chat_selection_error = "Recupere a confirmação pendente em Agendar antes de escolher outra reserva."
        return
    state.pop("chat_selection_error", None)
    state.pop("booking_slot", None)
    state.pop("booking_start", None)
    state.chat_booking_choice = {"service": service, "slot": slot}
    state.public_section = "Agendar"
    state.floating_chat_open = False
    state.support_contact_open = False
    get_booking_catalog.clear()


def prepare_chat_booking(catalog):
    state = st.session_state
    choice = state.get("chat_booking_choice")
    if not choice or not catalog:
        return None
    state.pop("chat_booking_choice")
    service = next((item for item in catalog["services"] if item["id"] == choice["service"]["id"]), None)
    if service is None or any(service[field] != choice["service"][field] for field in ("price", "duration_minutes")):
        state.booking_selection_notice = "O serviço foi atualizado. Consulte as opções novamente antes de escolher."
        return None
    slot = choice["slot"]
    if not any(barber["id"] == slot["barber_id"] for barber in catalog["barbers"]):
        state.booking_selection_notice = "O profissional não está mais disponível. Consulte as opções novamente."
        return None
    state.booking_service = service
    state.booking_barber = slot["barber_id"]
    state.booking_date = datetime.fromisoformat(slot["starts_at"]).date()
    state.booking_calendar_month = state.booking_date.replace(day=1)
    state.pop("booking_receipt", None)
    state.pop("booking_availability", None)
    return slot


def select_booking_time(starts_at):
    st.session_state.booking_start = starts_at
    st.session_state.pop("booking_slot", None)


def render_booking_times(slots):
    state = st.session_state
    by_start = {}
    for option in slots:
        by_start.setdefault(option["starts_at"], []).append(option)
    previous = state.get("booking_slot")
    if previous is not None and previous not in slots:
        state.pop("booking_slot", None)
        state.pop("booking_start", None)
    elif previous is not None and not state.get("booking_start"):
        state.booking_start = previous["starts_at"]
    if state.get("booking_start") not in by_start:
        state.pop("booking_start", None)
    with st.container(key="booking_times", border=False):
        for period, hours in (("Manhã", range(0, 12)), ("Tarde e noite", range(12, 24))):
            starts = [start for start in by_start if datetime.fromisoformat(start).hour in hours]
            if not starts:
                continue
            st.caption(period)
            for offset in range(0, len(starts), 4):
                for column, start in zip(st.columns(4, gap="small", wrap=False), starts[offset:offset + 4]):
                    column.button(
                        f"{datetime.fromisoformat(start):%H:%M}", key=f"booking_time_{start}",
                        type="primary" if start == state.get("booking_start") else "secondary",
                        width="stretch", on_click=select_booking_time, args=(start,),
                    )
    choices = by_start.get(state.get("booking_start"), [])
    if len(choices) == 1:
        state.booking_slot = choices[0]
    elif choices:
        st.markdown("#### Quem vai atender você?")
        with st.container(key="booking_professionals"):
            for column, option in zip(st.columns(len(choices), gap="small"), choices):
                column.button(
                    option["barber"], key=f"booking_professional_{option['barber_id']}", width="stretch",
                    type="primary" if option == state.get("booking_slot") else "secondary",
                    on_click=state.__setitem__, args=("booking_slot", option),
                )
    return state.get("booking_slot")


def submit_booking(payload):
    state = st.session_state
    try:
        response = requests.post(f"{API}/appointments", json=payload, timeout=10)
        if 400 <= response.status_code < 500 and response.status_code not in (408, 429):
            state.pop("booking_pending", None)
            detail = response.json().get("detail")
            state.booking_conflict = detail if isinstance(detail, str) else "Confira seus dados e escolha o horário novamente antes de confirmar."
            get_booking_catalog.clear()
            st.rerun()
        response.raise_for_status()
        state.booking_receipt = response.json()
    except requests.RequestException:
        state.booking_pending = payload
        st.warning("A confirmação não foi recebida. Tente novamente para recuperar o resultado desta mesma reserva.")
        return
    state.pop("booking_pending", None)
    state.pop("booking_availability", None)
    state.pop("booking_contact", None)
    st.rerun()


def render_booking(catalog, shop_name):
    state = st.session_state
    suggested_slot = prepare_chat_booking(catalog)
    if notice := state.pop("booking_selection_notice", None):
        st.warning(notice)
    if receipt := state.get("booking_receipt"):
        st.success("Agendamento confirmado!")
        st.markdown(f"**Código da reserva #{receipt['id']}**")
        show_booking_details(receipt)
        with st.container(key="booking_receipt_code", border=True):
            st.markdown("**Código privado de acesso**")
            st.code(receipt["access_code"], language=None)
            st.caption("Use o telefone da reserva e este código em Meus horários. O comprovante inclui o código. Guarde-o em um local seguro.")
            st.download_button(
                "Salvar comprovante", receipt_text(receipt, shop_name),
                file_name=f"reserva-imperial-{receipt['id']}.txt", mime="text/plain; charset=utf-8",
                on_click="ignore", icon=":material/download:", width="stretch",
            )
        if st.button("Novo agendamento", type="tertiary"):
            del state.booking_receipt
            state.pop("booking_slot", None)
            state.pop("booking_start", None)
            st.rerun()
        return

    if pending := state.get("booking_pending"):
        st.warning("Aguardando confirmação da sua reserva. Repita a tentativa para recuperar o resultado.")
        if st.button("Tentar confirmar novamente", type="primary"):
            submit_booking(pending)
        return

    st.markdown("## 1. Encontre um horário")
    st.caption("Escolha o serviço e o barbeiro para ver os dias com vagas.")
    if catalog is None:
        st.error("Não foi possível carregar a agenda agora. Tente novamente em instantes.")
        st.button("Tentar novamente", key="retry_booking_catalog")
        return
    services, barbers = catalog["services"], catalog["barbers"]
    if not services or not barbers:
        st.info("A agenda está sem serviços ou profissionais disponíveis no momento.")
        return

    service = st.selectbox(
        "Serviço", services, key="booking_service",
        format_func=lambda item: f"{item['name']} · {format_price(item['price'])} · {item['duration_minutes']} min",
    )
    professionals = {barber["id"]: barber["name"] for barber in barbers}
    barber_id = st.selectbox(
        "Barbeiro", [None, *professionals], key="booking_barber",
        format_func=lambda item: professionals.get(item, "Sem preferência"),
    )
    today = datetime.now(ZoneInfo(TIMEZONE)).date()
    first_day = today + timedelta(days=MIN_BOOKING_DAYS)
    last_day = today + timedelta(days=MAX_BOOKING_DAYS)
    st.caption(
        f"Reservas online com {MIN_BOOKING_DAYS} a {MAX_BOOKING_DAYS} dias de antecedência. "
        f"Período aberto: {first_day:%d/%m} a {last_day:%d/%m}."
    )

    conflict = state.pop("booking_conflict", None)
    if conflict:
        st.warning(conflict)
    criteria = (service["id"], service["price"], service["duration_minutes"], barber_id, first_day, last_day)
    cached_criteria, dates_and_slots = state.get("booking_availability", (None, None))
    if criteria != cached_criteria or conflict:
        state.pop("booking_availability", None)
        state.pop("booking_slot", None)
        state.pop("booking_start", None)
        dates_and_slots = None
        params = {"service_id": service["id"]}
        if barber_id is not None:
            params["barber_id"] = barber_id
        try:
            with st.spinner("Consultando os dias com vagas…"):
                dates_and_slots = {}
                for offset in range((last_day - first_day).days + 1):
                    day = first_day + timedelta(days=offset)
                    dates_and_slots[day] = get_booking_data("availability", {**params, "appointment_date": day.isoformat()})
            state.booking_availability = (criteria, dates_and_slots)
        except requests.RequestException:
            st.error("Não foi possível consultar a disponibilidade. Tente novamente.")
            st.button("Tentar novamente", key="booking_refresh")
            return

    if suggested_slot is not None and suggested_slot not in dates_and_slots.get(state.booking_date, []):
        st.warning("Esse horário mudou ou acabou de ser ocupado. Escolha uma opção atualizada para continuar.")
        suggested_slot = None
    working_days = {
        day for barber in barbers if barber_id is None or barber["id"] == barber_id
        for day in barber["working_days"] if day in SHOP_OPEN_DAYS
    }
    chosen_date = render_booking_calendar(dates_and_slots, first_day, last_day, working_days)
    slot = None
    if chosen_date is None:
        message = "Não há horários para essa escolha no período exibido."
        if barber_id is not None:
            message += " Selecione Sem preferência para consultar toda a equipe."
        st.info(message)
    else:
        filters = (*criteria, chosen_date)
        if filters != state.get("booking_filters"):
            state.booking_filters = filters
            state.pop("booking_slot", None)
            state.pop("booking_start", None)
        if suggested_slot is not None:
            state.booking_slot = suggested_slot
            state.booking_start = suggested_slot["starts_at"]
        slots = sorted(dates_and_slots[chosen_date], key=lambda item: (item["starts_at"], item["barber"]))
        st.markdown(f"#### Horários disponíveis · {chosen_date:%d/%m}")
        st.caption("Escolha um horário para continuar. A vaga será verificada novamente na confirmação.")
        slot = render_booking_times(slots)
    st.button("Atualizar disponibilidade", key="booking_refresh", on_click=state.pop,
              args=("booking_availability", None))
    if slot is None:
        return

    with st.container(key="booking_selection_summary", border=True):
        st.markdown("**Sua escolha**")
        show_booking_details({**slot, "service": service["name"], "price": service["price"]})
    st.markdown("## 2. Informe seu contato")
    draft = state.get("booking_contact", {})
    name = st.text_input("Seu nome", value=draft.get("name", ""), max_chars=120, key="booking_name",
                         on_change=save_booking_contact).strip()
    phone = st.text_input(
        "Telefone com DDD", value=draft.get("phone", ""), max_chars=30,
        placeholder="(16) 99999-9999", key="booking_phone",
        on_change=save_booking_contact,
    ).strip()
    valid_phone = sum(character.isdecimal() for character in phone) >= 10
    if phone and not valid_phone:
        st.caption("Informe um telefone com DDD e pelo menos 10 dígitos.")

    with st.container(key="booking_review", border=True):
        st.markdown("### 3. Revise e confirme")
        show_booking_details({**slot, "service": service["name"], "price": service["price"]})
        if not (name and valid_phone):
            st.caption("Preencha seu nome e telefone para confirmar.")
        confirmed = st.button("Confirmar agendamento", type="primary", width="stretch",
                              disabled=not (name and valid_phone))
    if not confirmed:
        return

    payload = {
        "customer_name": name, "customer_phone": phone, "service_id": service["id"],
        "barber_id": slot["barber_id"], "starts_at": slot["starts_at"], "confirmed": True,
        "expected_price": service["price"], "expected_duration": service["duration_minutes"],
        "request_key": str(uuid.uuid4()), "session_id": state.session_id,
    }
    save_booking_contact()
    state.booking_pending = payload
    submit_booking(payload)


def request_managed_booking(endpoint, payload):
    response = requests.post(f"{API}/{endpoint}", json=payload, timeout=10)
    response.raise_for_status()
    return response.json()


def clear_managed_booking():
    for key in ("manage_credentials", "manage_records", "manage_action", "manage_slots", "manage_notice"):
        st.session_state.pop(key, None)


def handle_management_error(exc, message):
    if exc.response is not None and exc.response.status_code == 404:
        clear_managed_booking()
        st.session_state.manage_notice = ("warning", "Telefone ou código privado inválido. Confira os dados ou fale com a equipe.")
        st.rerun()
    st.error(message)


def update_managed_booking(record, credentials, action, new_start=None):
    state = st.session_state
    payload = {**credentials, "confirmed": True}
    if new_start is not None:
        payload["new_starts_at"] = new_start
    try:
        result = requests.post(f"{API}/appointments/{record['id']}/{action}", json=payload, timeout=10)
        if result.status_code == 409:
            state.manage_notice = ("warning", result.json()["detail"])
            state.pop("manage_slots", None)
            state.manage_records = [request_managed_booking("appointments/lookup", credentials)]
            st.rerun()
        result.raise_for_status()
        record.update(result.json())
    except requests.RequestException as exc:
        handle_management_error(exc, "Não foi possível verificar a alteração. Consulte a reserva novamente antes de tentar outra vez.")
        return

    state.manage_records.sort(key=lambda item: item["starts_at"])
    state.manage_notice = ("success", "Reserva cancelada." if action == "cancel" else "Reserva remarcada.")
    state.pop("manage_action", None)
    state.pop("manage_slots", None)
    st.rerun()


def render_reschedule(record, credentials, today):
    state = st.session_state
    appointment_id = record["id"]
    first_day = today + timedelta(days=MIN_BOOKING_DAYS)
    new_date = st.date_input(
        "Nova data", value=first_day, min_value=first_day,
        max_value=today + timedelta(days=MAX_BOOKING_DAYS), format="DD/MM/YYYY",
        key=f"reschedule_date_{appointment_id}",
    )
    search = (appointment_id, new_date)
    slot_key = f"reschedule_slot_{appointment_id}"
    cached_search, slots = state.get("manage_slots", (None, None))
    if cached_search != search:
        state.pop("manage_slots", None)
        state.pop(slot_key, None)
        slots = None

    if st.button("Buscar novos horários", key=f"reschedule_search_{appointment_id}", disabled=new_date is None):
        state.pop("manage_slots", None)
        state.pop(slot_key, None)
        try:
            slots = request_managed_booking(f"appointments/{appointment_id}/reschedule-options", {
                **credentials, "appointment_date": new_date.isoformat(),
            })
        except requests.RequestException as exc:
            handle_management_error(exc, "Não foi possível buscar novos horários. Tente novamente.")
            return
        state.manage_slots = (search, slots)

    if slots is None:
        return
    if not slots:
        st.info("Sem horários disponíveis com esse barbeiro nessa data. Escolha outro dia.")
        return
    slot = st.selectbox(
        "Novo horário", slots, index=None, key=slot_key, placeholder="Escolha um horário",
        format_func=lambda item: f"{datetime.fromisoformat(item['starts_at']):%Hh%M}",
    )
    if st.button("Confirmar remarcação", key=f"confirm_reschedule_{appointment_id}",
                 type="primary", disabled=slot is None):
        update_managed_booking(record, credentials, "reschedule", slot["starts_at"])


def render_managed_bookings():
    state = st.session_state
    st.subheader("Consultar uma reserva")
    st.caption("Use o telefone da reserva e o código privado do comprovante. Se perdeu o código ou reservou antes dessa mudança, fale com a equipe.")
    with st.form("lookup_appointments", clear_on_submit=True):
        phone = st.text_input("Telefone usado no agendamento", key="lookup_phone",
                              placeholder="(16) 99999-9999", max_chars=30).strip()
        code = st.text_input("Código privado de acesso", key="lookup_code", type="password",
                             max_chars=64).strip()
        lookup = st.form_submit_button("Consultar", type="primary")
    if lookup:
        clear_managed_booking()
        if not phone or not code:
            st.info("Informe o telefone com DDD e o código privado da reserva.")
            return
        credentials = {"phone": phone, "access_code": code}
        try:
            state.manage_records = [request_managed_booking("appointments/lookup", credentials)]
            state.manage_credentials = credentials
        except requests.RequestException as exc:
            handle_management_error(exc, "Não foi possível consultar a reserva. Confira os dados ou tente novamente.")
            return

    notice = state.pop("manage_notice", None)
    if notice:
        kind, message = notice
        (st.success if kind == "success" else st.warning)(message)
    records = state.get("manage_records")
    if records is None:
        return
    st.button("Encerrar consulta", on_click=clear_managed_booking)

    now = datetime.now(ZoneInfo(TIMEZONE))
    for record in records:
        appointment_id = record["id"]
        starts_at = datetime.fromisoformat(record["starts_at"])
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=now.tzinfo)
        with st.container(border=True):
            status = "Cancelado" if record["status"] == "cancelled" else "Confirmado"
            st.markdown(f"**Reserva #{appointment_id} · {status}**")
            show_booking_details(record)
            if record["status"] != "confirmed":
                continue
            if starts_at - now < timedelta(hours=3):
                if starts_at > now:
                    st.caption("Alterações online estão disponíveis até 3 horas antes do atendimento.")
                continue

            reschedule, cancel = st.columns(2)
            if reschedule.button("Remarcar", key=f"reschedule_{appointment_id}"):
                state.manage_action = (appointment_id, "reschedule")
                state.pop("manage_slots", None)
            if cancel.button("Cancelar reserva", key=f"cancel_{appointment_id}"):
                state.manage_action = (appointment_id, "cancel")
                state.pop("manage_slots", None)
            if state.get("manage_action") == (appointment_id, "reschedule"):
                render_reschedule(record, state.manage_credentials, now.date())
            elif state.get("manage_action") == (appointment_id, "cancel"):
                st.warning("Confirme apenas se quiser liberar esse horário.")
                if st.button("Confirmar cancelamento", key=f"confirm_cancel_{appointment_id}", type="primary"):
                    update_managed_booking(record, state.manage_credentials, "cancel")
            else:
                continue
            if st.button("Voltar", key=f"manage_back_{appointment_id}"):
                state.pop("manage_action", None)
                state.pop("manage_slots", None)
                st.rerun()


def show_chat_message(role, content, booking_options=None, key_prefix=None):
    with st.chat_message(role):
        st.caption("Você" if role == "user" else "Assistente virtual")
        if role == "user":
            st.text(content)
        else:
            st.markdown(content.split("\n\n", 1)[0] if booking_options else content)
            if booking_options:
                for index, slot in enumerate(booking_options["slots"]):
                    start = datetime.fromisoformat(slot["starts_at"])
                    st.button(
                        f"{start:%d/%m · %H:%M} · {slot['barber']}", key=f"{key_prefix}_suggestion_{index}",
                        width="stretch", on_click=select_chat_booking, args=(booking_options["service"], slot),
                    )
                st.caption("Escolha uma opção para revisar e confirmar a reserva.")


def render_chat(surface, shop_name, *, compact=False):
    state = st.session_state
    with st.container(key=f"{surface}_chat_heading"):
        st.markdown("### Atendimento")
        st.caption(f"Assistente virtual · {shop_name}")
    render_support_tracking(surface)
    request = state.get("support_request")
    contact_pending = bool(request and request["status"] != "resolved" and not request.get("contact_provided")
                           and state.get("support_contact_saved") != request["id"])
    if state.get("support_contact_open", False):
        render_support_contact(surface)
        return
    history = st.container(key=f"{surface}_chat_history", height=250 if compact else 320,
                           border=False, autoscroll=True)
    with history:
        if not state.messages:
            show_chat_message("assistant", "Olá! Posso ajudar com serviços, preços e horários da barbearia.")
        for index, message in enumerate(state.messages):
            show_chat_message(message["role"], message["content"], message.get("booking_options"), f"{surface}_{index}")
    if state.get("chat_selection_error"):
        st.warning(state.chat_selection_error)
    prompt = st.chat_input("Escreva sua mensagem", key=f"{surface}_chat_input", max_chars=4000)
    retry = False
    if state.get("chat_error"):
        st.warning(state.chat_error)
        retry = st.button("Tentar atendimento novamente", key=f"{surface}_chat_retry")
    if contact_pending:
        if st.button("Informar contato para a equipe", key=f"{surface}_human_request", type="tertiary"):
            state.support_contact_open = True
            st.rerun()
    elif not request or request["status"] == "resolved":
        if st.button("Falar com a equipe", key=f"{surface}_human_request", type="tertiary"):
            state.pop("support_request", None)
            state.support_contact_open = True
            st.rerun()
    if not retry and (not prompt or not prompt.strip()):
        return
    if not retry:
        state.messages.extend(sanitize_chat_messages([{"role": "user", "content": prompt}]))
    with history:
        if not retry:
            show_chat_message("user", state.messages[-1]["content"])
        with st.chat_message("assistant"):
            st.caption("Assistente virtual")
            with st.spinner("Consultando o atendimento…"):
                result = request_chat_answer(state.session_id, state.messages)
    if result.get("retryable"):
        state.chat_error = result["answer"]
    else:
        state.pop("chat_error", None)
        message = {"role": "assistant", "content": result["answer"]}
        if result.get("booking_options"):
            message["booking_options"] = result["booking_options"]
        state.messages.append(message)
        if result.get("support_request"):
            state.support_request = result["support_request"]
            state.support_contact_open = True
    st.rerun()


@st.fragment(run_every="10s")
def render_support_tracking(surface):
    state = st.session_state
    request = state.get("support_request")
    if not request or (surface == "tab" and not tab_chat.open):
        return
    try:
        response = requests.post(f"{API}/support/status", json={
            "session_id": state.session_id, "ticket_id": request["id"],
        }, timeout=10)
        response.raise_for_status()
        current = response.json()["request"]
        if current is None:
            st.info("Nenhum atendimento encontrado nesta conversa.")
            return
        state.support_request = current
        if current["status"] == "resolved" and request["status"] != "resolved" and state.get("support_contact_open"):
            state.support_contact_open = False
            st.rerun()
        if not current["contact_provided"]:
            return
        statuses = {"open": "🟡 Aguardando a equipe", "in_progress": "🔵 Em atendimento", "resolved": "🟢 Resolvido"}
        status, action = st.columns([3, 1], vertical_alignment="center", wrap=False)
        status.caption(f"#{current['id']} · {statuses[current['status']]}")
        action.button("Atualizar", key=f"{surface}_support_refresh", type="tertiary")
    except requests.RequestException:
        st.caption("Situação do atendimento indisponível.")
        st.button("Atualizar situação", key=f"{surface}_support_retry", type="tertiary")


def render_support_contact(surface):
    state = st.session_state
    draft = state.get("support_contact_draft", {})
    st.markdown("#### Retorno da equipe")
    st.caption("A equipe fará o retorno pelo telefone informado. Preencha e confirme para solicitar.")
    with st.form(f"{surface}_support_contact", border=False):
        name = st.text_input("Nome para retorno", value=draft.get("name", ""), max_chars=120, key=f"{surface}_support_name")
        phone = st.text_input("Telefone para retorno", value=draft.get("phone", ""), placeholder="(16) 99999-9999", max_chars=30, key=f"{surface}_support_phone")
        back, send = st.columns([1, 2], gap="small")
        returned = back.form_submit_button("Voltar", key=f"{surface}_support_back", width="stretch")
        submitted = send.form_submit_button("Solicitar atendimento", key=f"{surface}_support_send", type="primary", width="stretch")
    if returned or submitted:
        state.support_contact_draft = {"name": name, "phone": phone}
    if returned:
        state.support_contact_open = False
        st.rerun()
    if submitted:
        try:
            response = requests.post(f"{API}/support/requests", json={
                "session_id": state.session_id,
                "customer_name": name, "customer_phone": phone,
            }, timeout=10)
            response.raise_for_status()
            state.support_request = response.json()
            if state.get("support_contact_saved") != state.support_request["id"]:
                state.messages.append({"role": "assistant", "content":
                    f"Pedido #{state.support_request['id']} enviado à equipe. O retorno será pelo telefone informado. Posso continuar ajudando você por aqui como assistente virtual."})
            state.support_contact_saved = state.support_request["id"]
            state.support_contact_open = False
            st.rerun()
        except requests.RequestException as exc:
            message = "Não foi possível enviar seu contato. Tente novamente."
            if exc.response is not None and exc.response.status_code == 422:
                message = "Preencha seu nome e um telefone válido com DDD."
            st.error(message)


def request_chat_answer(session_id, messages):
    try:
        recent = [{"role": message["role"], "content": message["content"][:4000]} for message in messages[-16:]]
        response = requests.post(f"{API}/chat", json={"session_id": session_id, "messages": recent}, timeout=90)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return {"answer": "O atendimento está indisponível agora. Tente novamente ou use Agendar.", "retryable": True}


def render_floating_chat(shop_name):
    state = st.session_state
    opened = state.get("floating_chat_open", False)
    with st.container(key="imperial_chat_dock"):
        st.button(
            "Fechar conversa" if opened else "Conversar", key="floating_chat_toggle", type="primary",
            icon=":material/close:" if opened else ":material/chat:",
            on_click=state.__setitem__, args=("floating_chat_open", not opened),
        )
        if opened:
            with st.container(key="imperial_chat_panel", border=True):
                render_chat("floating", shop_name, compact=True)


catalog = None
try:
    catalog = get_booking_catalog(API)
except requests.RequestException:
    pass  # A aba de agendamento exibe o erro e oferece nova tentativa.

shop = catalog["shop"] if catalog else None
shop_name = shop["name"] if shop else "Barbearia Imperial"
st.set_page_config(page_title=shop_name, page_icon="💈", layout="centered", initial_sidebar_state="collapsed")
st.html("""
<style>
  .st-key-booking_calendar [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    min-width: 0;
  }
  .st-key-booking_calendar_days .booking-weekday {
    text-align: center;
    font-size: 0.8rem;
    font-weight: 600;
    padding-bottom: 0.25rem;
  }
  .st-key-booking_calendar_days button {
    min-width: 0;
    min-height: 42px;
    padding-inline: 0;
    margin: 2px auto;
    width: calc(100% - 4px);
    border-radius: 10px;
    font-weight: 600;
  }
  .st-key-booking_calendar_days [class*="st-key-calendar_available_"] button {
    color: light-dark(#17633b, #a7efc4);
    border-color: light-dark(#b9dec9, #3b7450);
    background: light-dark(#e9f7ee, #203c2b);
  }
  .st-key-booking_calendar_days [class*="st-key-calendar_holiday_"] button {
    color: light-dark(#805a0c, #f9dc8a);
    border-color: light-dark(#ead58f, #8a723b);
    background: light-dark(#fff5cf, #493e22);
  }
  .st-key-booking_calendar_days [class*="st-key-calendar_holiday_"] button:disabled {
    opacity: 0.6;
  }
  .st-key-booking_calendar_days [class*="st-key-calendar_holiday_"] button[data-testid="stBaseButton-primary"] {
    color: light-dark(#624300, #fff0b9);
    background: light-dark(#ffe6a0, #775a1e);
    border-color: #d5ae48;
    box-shadow: inset 0 0 0 1px #d5ae48;
  }
  .st-key-booking_calendar_days [class*="st-key-calendar_unavailable_"] button:disabled {
    color: light-dark(#9a424b, #e3a2aa);
    border-color: light-dark(#f0c5ca, #694047);
    background: light-dark(#fce8eb, #412b30);
    opacity: 1;
  }
  .st-key-booking_calendar_days button:hover:not(:disabled),
  .st-key-booking_times button:hover {
    filter: brightness(1.13);
  }
  .st-key-booking_calendar_days button[data-testid="stBaseButton-primary"] {
    color: white;
    background: #19834a;
    border-color: #56c78d;
    box-shadow: inset 0 0 0 1px #56c78d;
    font-weight: 700;
  }
  .st-key-booking_calendar_days [class*="st-key-calendar_outside_"] button:disabled,
  .st-key-booking_calendar_days [class*="st-key-calendar_closed_"] button:disabled {
    color: inherit;
    opacity: 0.43;
    border-color: transparent;
    background: transparent;
  }
  .st-key-booking_calendar_days button:focus-visible,
  .st-key-booking_times button:focus-visible {
    outline: 3px solid #56c78d;
    outline-offset: 2px;
  }
  .booking-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 0.9rem;
    font-size: 0.75rem;
    line-height: 1.5;
    padding-top: 0.65rem;
  }
  .booking-legend span { display: inline-flex; align-items: center; gap: 0.35rem; }
  .booking-legend i { width: 9px; height: 9px; border-radius: 3px; }
  .booking-legend .available { background: #52b77a; }
  .booking-legend .holiday { background: #d4b24d; }
  .booking-legend .unavailable { background: #c77481; }
  .booking-legend .closed { background: #858585; }
  .st-key-booking_times button {
    min-height: 40px;
    border-radius: 8px;
    color: light-dark(#393e3b, #e1e5e2);
    border-color: light-dark(#d4dbd6, #4b514c);
    background: light-dark(#f9fbfa, #242824);
    padding: 0.25rem;
  }
  .st-key-booking_times button:hover:not(:disabled) {
    border-color: #68b885;
    background: light-dark(#edf7f0, #2a392e);
  }
  .st-key-booking_times [data-testid="stColumn"] {
    min-width: 0;
  }
  .st-key-booking_times [data-testid="stVerticalBlock"] {
    gap: 0.5rem;
  }
  .st-key-booking_times button[data-testid="stBaseButton-primary"],
  .st-key-booking_times button[data-testid="stBaseButton-primary"]:hover:not(:disabled) {
    color: white;
    background: #19834a;
    border-color: #68d495;
    box-shadow: inset 0 0 0 1px #68d495;
  }
  @media (max-width: 480px) {
    .st-key-booking_times button p { font-size: 0.75rem; }
  }
  .st-key-imperial_chat_dock {
    --chat-edge: clamp(0.75rem, 2vw, 1.5rem);
    position: fixed;
    z-index: 100;
    right: calc(var(--chat-edge) + env(safe-area-inset-right, 0px));
    bottom: calc(var(--chat-edge) + env(safe-area-inset-bottom, 0px));
    width: max-content !important;
  }
  .st-key-floating_chat_toggle button {
    min-height: 44px;
    border-radius: 999px;
    padding-inline: 1rem;
    box-shadow: 0 4px 16px #0004;
  }
  .st-key-imperial_chat_panel {
    position: absolute;
    right: 0;
    bottom: calc(100% + 0.65rem);
    width: min(23rem, calc(100vw - 2 * var(--chat-edge) - env(safe-area-inset-right, 0px))) !important;
    max-width: none;
    max-height: calc(100vh - 5.5rem);
    max-height: calc(100dvh - 5.5rem - env(safe-area-inset-bottom, 0px));
    overflow-y: auto;
    overscroll-behavior: contain;
    background: #191715;
    background: light-dark(#fff, #191715);
    box-shadow: 0 12px 36px #0005;
  }
  :is(.st-key-floating_chat_heading, .st-key-tab_chat_heading) {
    gap: 0.85rem;
    padding-bottom: 0.65rem;
    border-bottom: 1px solid light-dark(#e5d9dc, #473c3e);
  }
  :is(.st-key-floating_chat_heading, .st-key-tab_chat_heading) h3 {
    padding: 0;
    font-size: 1.25rem;
    line-height: 1.25;
  }
  :is(.st-key-floating_chat_history, .st-key-tab_chat_history) {
    padding: 0.7rem;
    gap: 0.6rem;
    border-radius: 0.65rem;
    background: light-dark(#f8f5f5, #201c1c);
    overscroll-behavior: contain;
  }
  :is(.st-key-floating_chat_history, .st-key-tab_chat_history) [data-testid="stChatMessage"] {
    width: fit-content;
    max-width: min(90%, 30rem);
    margin-inline-end: auto;
    padding: 0.55rem 0.75rem;
    border-radius: 1rem 1rem 1rem 0.25rem;
    background: light-dark(#eee8e8, #312b2b);
  }
  :is(.st-key-floating_chat_history, .st-key-tab_chat_history)
    [data-testid="stChatMessage"]:has(> [aria-label="Chat message from user"]) {
    margin-inline: auto 0;
    border-radius: 1rem 1rem 0.25rem 1rem;
    background: light-dark(#f6dce3, #772c40);
  }
  :is(.st-key-floating_chat_history, .st-key-tab_chat_history) [data-testid="stChatMessage"] > :first-child:not([data-testid="stChatMessageContent"]) {
    display: none;
  }
  :is(.st-key-floating_chat_history, .st-key-tab_chat_history) [data-testid="stChatMessageContent"] {
    min-width: 0;
    overflow-wrap: anywhere;
  }
  :is(.st-key-floating_chat_history, .st-key-tab_chat_history) [data-testid="stChatMessageContent"] > [data-testid="stVerticalBlock"] {
    gap: 0.15rem;
  }
  :is(.st-key-floating_chat_history, .st-key-tab_chat_history) [data-testid="stChatMessageContent"] [data-testid="stCaptionContainer"] p {
    color: inherit;
    font-weight: 650;
    font-size: 0.72rem;
  }
  :is(.st-key-floating_chat_history, .st-key-tab_chat_history) [data-testid="stText"] {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .st-key-floating_chat_history {
    height: clamp(7rem, 36dvh, 15.625rem) !important;
  }
  [data-testid="stMainBlockContainer"]:has(.st-key-imperial_chat_dock) {
    padding-bottom: max(5rem, calc(4.5rem + env(safe-area-inset-bottom, 0px)));
  }
  @supports selector(:has(*)) {
    @keyframes imperial-reveal { to { visibility: visible; opacity: 1; pointer-events: auto; } }
    [data-testid="stMainBlockContainer"]:not(:has(#imperial-ready)):not(
      :has(.st-key-booking_service input):has(.st-key-booking_barber input):has(.st-key-booking_refresh button)
    ) {
      visibility: hidden;
      opacity: 0;
      pointer-events: none;
      animation: imperial-reveal 0s 2s forwards;
    }
    [data-testid="stElementContainer"]:has(#imperial-ready) { display: none; }
  }
</style>
""")
st.title(shop_name)
st.caption("Atendimento e agendamentos")

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = []

tab_book, tab_manage, tab_info, tab_chat = st.tabs(
    ["Agendar", "Meus horários", "Informações", "Conversar"], key="public_section", on_change="rerun",
)
with tab_book:
    render_booking(catalog, shop_name)
with tab_chat:
    with st.container(key="imperial_chat_tab", border=True):
        render_chat("tab", shop_name)
with tab_manage:
    render_managed_bookings()

with tab_info:
    st.subheader("Informações da barbearia")
    if shop:
        st.markdown("**Endereço**")
        st.text(shop["address"])
        st.markdown("**Contato**")
        st.text(shop["phone"])
        hours = f"{shop['open_1']}–{shop['close_1']}"
        if shop["open_2"]:
            hours += f" e {shop['open_2']}–{shop['close_2']}"
        st.markdown("**Expediente**")
        st.write(f"Segunda a sábado · {hours}")
        st.caption("Domingo fechado.")
        st.caption("Os dias de atendimento variam conforme a escala do profissional.")
        st.markdown("**Pagamento**")
        st.text(shop["payment_methods"])
        with st.expander("Serviços e preços"):
            for service in catalog["services"]:
                st.write(f"{service['name']} · {format_price(service['price'])} · {service['duration_minutes']} min")
        with st.expander("Escala da equipe"):
            for barber in catalog["barbers"]:
                days = ", ".join(WEEKDAYS[day] for day in barber["working_days"])
                st.write(f"{barber['name']} · {days}")
    else:
        st.info("As informações estão temporariamente indisponíveis.")

if not tab_chat.open:
    render_floating_chat(shop_name)

# O formulário libera a primeira tela quando o serviço, o barbeiro e a consulta de vagas aparecem.
# Comprovante e estados sem catálogo usam o marcador porque não têm esses controles.
if st.session_state.get("booking_receipt") or not catalog or not (catalog["services"] and catalog["barbers"]):
    st.html('<span id="imperial-ready" hidden></span>')
