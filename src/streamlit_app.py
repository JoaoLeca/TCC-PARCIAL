import os
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import streamlit as st

from barber_bot.config import MAX_BOOKING_DAYS, MIN_BOOKING_DAYS, TIMEZONE

API = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def format_price(price):
    return "R$ " + f"{price:,.2f}".translate(str.maketrans(",.", ".,"))


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
    return (
        get_booking_data("services", api_url=api_url),
        get_booking_data("barbers", api_url=api_url),
    )


def render_booking(catalog):
    state = st.session_state
    if receipt := state.get("booking_receipt"):
        st.success("Agendamento confirmado!")
        st.markdown(f"**Código da reserva #{receipt['id']}**")
        show_booking_details(receipt)
        st.markdown("Use a aba **Meus horários** para consultar sua reserva.")
        if st.button("Novo agendamento"):
            del state.booking_receipt
            st.rerun()
        return

    st.markdown("## 1. Encontre um horário")
    st.caption("Escolha o serviço, o barbeiro e a data para ver os horários disponíveis.")
    if catalog is None:
        st.error("Não foi possível carregar a agenda agora. Tente novamente em instantes.")
        st.button("Tentar novamente", key="retry_booking_catalog")
        return
    services, barbers = catalog
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
    chosen_date = st.date_input(
        "Data", value=first_day, min_value=first_day,
        max_value=today + timedelta(days=MAX_BOOKING_DAYS), format="DD/MM/YYYY", key="booking_date",
    )
    filters = (service["id"], barber_id, chosen_date)
    if filters != state.get("booking_filters"):
        state.booking_filters = filters
        state.pop("booking_slots", None)
        state.pop("booking_slot", None)

    conflict = state.pop("booking_conflict", None)
    if conflict:
        st.warning(conflict)
    if st.button("Ver horários", key="booking_search", type="primary", disabled=chosen_date is None) or conflict:
        state.pop("booking_slots", None)
        state.pop("booking_slot", None)
        params = {"service_id": service["id"], "appointment_date": chosen_date.isoformat()}
        if barber_id is not None:
            params["barber_id"] = barber_id
        try:
            with st.spinner("Buscando horários disponíveis…"):
                slots = get_booking_data("availability", params)
            state.booking_slots = sorted(slots, key=lambda slot: (slot["starts_at"], slot["barber"]))
        except requests.RequestException:
            st.error("Não foi possível consultar os horários. Tente novamente.")
            return

    slots = state.get("booking_slots")
    if slots is None:
        return
    if not slots:
        st.info("Não há horários para essa escolha. Tente outra data ou selecione Sem preferência de barbeiro.")
        return
    slot = st.selectbox(
        "Horário disponível", slots, index=None, key="booking_slot", placeholder="Escolha um horário",
        format_func=lambda item: f"{datetime.fromisoformat(item['starts_at']):%H:%M} · {item['barber']}",
    )
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
    }
    try:
        response = requests.post(f"{API}/appointments", json=payload, timeout=10)
        if response.status_code == 409:
            state.booking_contact = {"name": name, "phone": phone}
            state.booking_conflict = response.json()["detail"]
            st.rerun()
        response.raise_for_status()
        state.booking_receipt = response.json()
    except requests.RequestException:
        st.error("Não foi possível verificar a confirmação. Consulte Meus horários antes de tentar novamente.")
        return
    state.pop("booking_slots", None)
    state.pop("booking_contact", None)
    st.rerun()


def update_managed_booking(record, phone, action, new_start=None):
    state = st.session_state
    payload = {"phone": phone, "confirmed": True}
    if new_start is not None:
        payload["new_starts_at"] = new_start
    try:
        result = requests.post(f"{API}/appointments/{record['id']}/{action}", json=payload, timeout=10)
        if result.status_code == 409:
            state.manage_notice = ("warning", result.json()["detail"])
            state.pop("manage_slots", None)
            state.manage_records = get_booking_data("appointments", {"phone": phone})
            st.rerun()
        result.raise_for_status()
        record.update(result.json())
    except requests.RequestException:
        st.error("Não foi possível verificar a alteração. Consulte suas reservas novamente antes de tentar outra vez.")
        return

    state.manage_records.sort(key=lambda item: item["starts_at"])
    state.manage_notice = ("success", "Reserva cancelada." if action == "cancel" else "Reserva remarcada.")
    state.pop("manage_action", None)
    state.pop("manage_slots", None)
    st.rerun()


def render_reschedule(record, phone, today):
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
            slots = get_booking_data(f"appointments/{appointment_id}/reschedule-options", {
                "phone": phone, "appointment_date": new_date.isoformat(),
            })
        except requests.RequestException:
            st.error("Não foi possível buscar novos horários. Tente novamente.")
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
        update_managed_booking(record, phone, "reschedule", slot["starts_at"])


def render_managed_bookings():
    state = st.session_state
    st.subheader("Consultar seus agendamentos")
    with st.form("lookup_appointments"):
        phone = st.text_input("Telefone usado no agendamento", key="lookup_phone",
                              placeholder="(16) 99999-9999").strip()
        lookup = st.form_submit_button("Consultar", type="primary")
    if lookup:
        for key in ("manage_phone", "manage_records", "manage_action", "manage_slots", "manage_notice"):
            state.pop(key, None)
        if not phone:
            st.info("Informe o telefone com DDD usado na reserva.")
            return
        try:
            state.manage_records = get_booking_data("appointments", {"phone": phone})
            state.manage_phone = phone
        except requests.RequestException:
            st.error("Não foi possível consultar suas reservas. Tente novamente.")
            return

    notice = state.pop("manage_notice", None)
    if notice:
        kind, message = notice
        (st.success if kind == "success" else st.warning)(message)
    records = state.get("manage_records")
    if records is None:
        return
    if not records:
        st.info("Não encontramos agendamentos para esse telefone.")
        return

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
                render_reschedule(record, state.manage_phone, now.date())
            elif state.get("manage_action") == (appointment_id, "cancel"):
                st.warning("Confirme apenas se quiser liberar esse horário.")
                if st.button("Confirmar cancelamento", key=f"confirm_cancel_{appointment_id}", type="primary"):
                    update_managed_booking(record, state.manage_phone, "cancel")
            else:
                continue
            if st.button("Voltar", key=f"manage_back_{appointment_id}"):
                state.pop("manage_action", None)
                state.pop("manage_slots", None)
                st.rerun()


st.set_page_config(page_title="Barbearia Imperial", page_icon="💈", layout="centered")
st.html("""
<style>
  @supports selector(:has(*)) {
    @keyframes imperial-reveal { to { visibility: visible; opacity: 1; pointer-events: auto; } }
    [data-testid="stMainBlockContainer"]:not(:has(#imperial-ready)):not(
      :has(.st-key-booking_service input):has(.st-key-booking_barber input):has(.st-key-booking_date :is(input, button, [role="spinbutton"])):has(.st-key-booking_search button)
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
catalog = None
if not st.session_state.get("booking_receipt"):
    try:
        catalog = get_booking_catalog(API)
    except requests.RequestException:
        pass  # A aba de agendamento exibe o erro e oferece nova tentativa.

st.title("💈 Barbearia Imperial")
st.caption("Atendimento e agendamentos · Ribeirão Preto, SP")

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = []

tab_book, tab_manage, tab_chat = st.tabs(["Agendar", "Meus horários", "Conversar"])
with tab_book:
    render_booking(catalog)
with tab_chat:
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

# O formulário libera a primeira tela quando os quatro controles montam no navegador.
# Comprovante e estados sem catálogo usam o marcador porque não têm esses controles.
if catalog is None or not all(catalog):
    st.html('<span id="imperial-ready" hidden></span>')
