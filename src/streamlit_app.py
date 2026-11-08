import os
import uuid
from calendar import Calendar, SUNDAY
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import streamlit as st

from barber_bot.config import MAX_BOOKING_DAYS, MIN_BOOKING_DAYS, TIMEZONE
from barber_bot.presentation import WEEKDAYS, format_price

API = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
MONTHS = ("Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto",
          "Setembro", "Outubro", "Novembro", "Dezembro")


def show_booking_details(booking):
    start = datetime.fromisoformat(booking["starts_at"])
    end = datetime.fromisoformat(booking["ends_at"])
    duration = (end - start) // timedelta(minutes=1)
    st.write(f"{booking['service']} com {booking['barber']}")
    st.write(f"{start:%d/%m/%Y} às {start:%Hh%M} · {duration} min · {format_price(booking['price'])}")


def get_booking_data(endpoint, params=None, *, api_url=API):
    response = requests.get(f"{api_url}/{endpoint}", params=params, timeout=10)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=60, show_spinner=False)
def get_booking_catalog(api_url):
    return get_booking_data("catalog", api_url=api_url)


def render_booking_calendar(dates_and_slots, first_day, last_day):
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

    with st.container(key="booking_calendar", width=440, border=True):
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
                        column.button(
                            str(day.day), key=f"booking_day_{day.isoformat()}", width="stretch",
                            type="primary" if day == chosen_date else "secondary",
                            disabled=not dates_and_slots.get(day),
                            on_click=state.__setitem__, args=("booking_date", day),
                        )

        st.caption("🟢 Com vagas · ⚪ Sem vaga ou fora do período")
        if chosen_date is not None:
            st.caption(f"Data selecionada: {WEEKDAYS[chosen_date.weekday()]}, {chosen_date:%d/%m/%Y}.")
    return chosen_date


def render_booking(catalog):
    state = st.session_state
    if receipt := state.get("booking_receipt"):
        st.success("Agendamento confirmado!")
        st.markdown(f"**Código da reserva #{receipt['id']}**")
        show_booking_details(receipt)
        st.markdown("**Código privado de acesso**")
        st.code(receipt["access_code"], language=None)
        st.caption("Guarde o código. Use-o com seu telefone em Meus horários. Se precisar de outro, fale com a equipe.")
        if st.button("Novo agendamento"):
            del state.booking_receipt
            st.rerun()
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

    chosen_date = render_booking_calendar(dates_and_slots, first_day, last_day)
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
        slots = sorted(dates_and_slots[chosen_date], key=lambda item: (item["starts_at"], item["barber"]))
        slot = st.selectbox(
            "Horário disponível", slots, index=None, key="booking_slot", placeholder="Escolha um horário",
            format_func=lambda item: f"{datetime.fromisoformat(item['starts_at']):%H:%M} · {item['barber']}",
        )
    st.button("Atualizar disponibilidade", key="booking_refresh", on_click=state.pop,
              args=("booking_availability", None))
    if slot is None:
        return

    st.markdown("## 2. Informe seu contato")
    draft = state.get("booking_contact", {})
    name = st.text_input("Seu nome", value=draft.get("name", ""), max_chars=120, key="booking_name").strip()
    phone = st.text_input(
        "Telefone com DDD", value=draft.get("phone", ""), max_chars=30,
        placeholder="(16) 99999-9999", key="booking_phone",
    ).strip()
    valid_phone = sum(character.isdecimal() for character in phone) >= 10
    if phone and not valid_phone:
        st.caption("Informe um telefone com DDD e pelo menos 10 dígitos.")

    st.markdown("## 3. Revise e confirme")
    show_booking_details({**slot, "service": service["name"], "price": service["price"]})
    if not st.button("Confirmar agendamento", type="primary", disabled=not (name and valid_phone)):
        return

    payload = {
        "customer_name": name, "customer_phone": phone, "service_id": service["id"],
        "barber_id": slot["barber_id"], "starts_at": slot["starts_at"], "confirmed": True,
        "expected_price": service["price"], "expected_duration": service["duration_minutes"],
    }
    try:
        response = requests.post(f"{API}/appointments", json=payload, timeout=10)
        if response.status_code == 409:
            state.booking_contact = {"name": name, "phone": phone}
            state.booking_conflict = response.json()["detail"]
            get_booking_catalog.clear()
            st.rerun()
        response.raise_for_status()
        state.booking_receipt = response.json()
    except requests.RequestException:
        st.error("Não foi possível verificar a confirmação. Consulte Meus horários antes de tentar novamente.")
        return
    state.pop("booking_availability", None)
    state.pop("booking_contact", None)
    st.rerun()


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
    padding-inline: 0;
    margin: 2px auto;
    width: calc(100% - 4px);
  }
  .st-key-booking_calendar_days button[data-testid="stBaseButton-secondary"]:not(:disabled) {
    border-color: #329365;
    background-color: color-mix(in srgb, #329365 20%, transparent);
  }
  .st-key-booking_calendar_days button[data-testid="stBaseButton-secondary"]:hover:not(:disabled) {
    background-color: color-mix(in srgb, #329365 36%, transparent);
  }
  .st-key-booking_calendar_days button[data-testid="stBaseButton-primary"] {
    color: white;
    background-color: #176643;
    border-color: #56c78d;
    box-shadow: inset 0 0 0 1px #56c78d;
    font-weight: 700;
  }
  .st-key-booking_calendar_days button:disabled {
    color: inherit;
    opacity: 0.43;
    border-color: transparent;
    background: transparent;
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
st.title(f"💈 {shop_name}")
st.caption("Atendimento e agendamentos")

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = []

tab_book, tab_manage, tab_info, tab_chat = st.tabs(["Agendar", "Meus horários", "Informações", "Conversar"])
with tab_book:
    render_booking(catalog)
with tab_chat:
    st.caption("Use Agendar para fazer uma reserva e Meus horários para consultar ou alterar. Digite seu código privado apenas em Meus horários.")
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    prompt = st.chat_input("Pergunte sobre serviços, horários ou políticas")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            try:
                response = requests.post(f"{API}/chat", json={"session_id": st.session_state.session_id, "messages": st.session_state.messages}, timeout=90)
                response.raise_for_status()
                answer = response.json()["answer"]
            except requests.RequestException as exc:
                answer = f"Não consegui acessar o atendimento agora: {exc}. Use a aba Agendar ou tente novamente."
            st.markdown(answer)
        st.session_state.messages.append({"role": "assistant", "content": answer})
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

# O formulário libera a primeira tela quando o serviço, o barbeiro e a consulta de vagas aparecem.
# Comprovante e estados sem catálogo usam o marcador porque não têm esses controles.
if st.session_state.get("booking_receipt") or not catalog or not (catalog["services"] and catalog["barbers"]):
    st.html('<span id="imperial-ready" hidden></span>')
