import os
import uuid
from datetime import date, timedelta
import requests
import streamlit as st
from dotenv import load_dotenv
load_dotenv()
API = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
st.set_page_config(page_title="Barbearia Imperial", page_icon="💈", layout="centered")
st.title("💈 Barbearia Imperial")
st.caption("Atendimento e agendamentos · Ribeirão Preto, SP")

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = []

tab_chat, tab_book, tab_manage = st.tabs(["Conversar", "Agendar", "Meus horários"])
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
with tab_book:
    st.subheader("Novo agendamento")
    try:
        services = requests.get(f"{API}/services", timeout=10).json()
        barbers = requests.get(f"{API}/barbers", timeout=10).json()
        with st.form("booking"):
            customer_name = st.text_input("Seu nome")
            customer_phone = st.text_input("Telefone com DDD")
            service = st.selectbox("Serviço", services, format_func=lambda x: f"{x['name']} · R$ {x['price']:.2f} · {x['duration_minutes']} min")
            barber = st.selectbox("Barbeiro", barbers, format_func=lambda x: x["name"])
            chosen_date = st.date_input("Data", min_value=date.today() + timedelta(days=1), max_value=date.today() + timedelta(days=3))
            check = st.form_submit_button("Ver horários")
        if check:
            slots = requests.get(f"{API}/availability", params={"service_id": service["id"], "appointment_date": chosen_date.isoformat(), "barber_id": barber["id"]}, timeout=10).json()
            if not slots:
                st.info("Não há horários disponíveis nessa data. Tente outro barbeiro ou dia.")
            else:
                st.session_state.slots = slots
                st.session_state.booking_info = {"name": customer_name, "phone": customer_phone, "service": service, "barber": barber}
        if st.session_state.get("slots"):
            info = st.session_state.booking_info
            slot = st.selectbox("Horário disponível", st.session_state.slots, format_func=lambda x: x["starts_at"][11:16])
            st.info(f"Confirme: {info['name']} · {info['service']['name']} · {info['barber']['name']} · {slot['starts_at'][:10]} às {slot['starts_at'][11:16]} · R$ {info['service']['price']:.2f}")
            if st.button("Confirmar agendamento"):
                payload = {"customer_name": info["name"], "customer_phone": info["phone"], "service_id": info["service"]["id"], "barber_id": info["barber"]["id"], "starts_at": slot["starts_at"], "confirmed": True}
                response = requests.post(f"{API}/appointments", json=payload, timeout=10)
                if response.ok:
                    st.success(f"Agendado! Número do atendimento: {response.json()['id']}.")
                    st.session_state.slots = []
                else:
                    st.error(response.json().get("detail", "Não foi possível agendar."))
    except requests.RequestException as exc:
        st.error(f"API indisponível: {exc}")
with tab_manage:
    st.subheader("Consultar seus agendamentos")
    phone = st.text_input("Telefone usado no agendamento", key="lookup_phone")
    if st.button("Consultar", key="lookup") and phone:
        try:
            records = requests.get(f"{API}/appointments", params={"phone": phone}, timeout=10).json()
            if not records:
                st.info("Não encontramos agendamentos para esse telefone.")
            for record in records:
                st.write(f"#{record['id']} · {record['service']} com {record['barber']} · {record['starts_at'][:16].replace('T',' ')} · {record['status']}")
                if record["status"] == "confirmed" and st.button("Confirmar cancelamento", key=f"cancel-{record['id']}"):
                    result = requests.post(f"{API}/appointments/{record['id']}/cancel", json={"phone": phone, "confirmed": True}, timeout=10)
                    st.success("Cancelado." if result.ok else result.json().get("detail", "Não foi possível cancelar."))
        except requests.RequestException as exc:
            st.error(f"API indisponível: {exc}")
