import os
from datetime import datetime, time, timedelta
from inspect import signature

import requests
import streamlit as st

from barber_bot.schedule import TZ
from barber_bot.presentation import WEEKDAYS, format_price


API = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
STATUS = {"confirmed": "Confirmado", "cancelled": "Cancelado"}


class StaffRequestError(Exception):
    pass


def clear_staff_session():
    for key in list(st.session_state):
        if key.startswith("staff_"):
            del st.session_state[key]


def call_api(method, endpoint, *, data=None, params=None):
    token = st.session_state.get("staff_token")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = requests.request(method, f"{API}/{endpoint}", json=data, params=params, headers=headers, timeout=10)
    except requests.RequestException as exc:
        raise StaffRequestError("Não foi possível conectar à API. Tente novamente em instantes.") from exc
    if response.status_code == 401 and token:
        clear_staff_session()
        st.session_state.staff_message = "Sua sessão terminou. Entre novamente."
        st.rerun()
    if not response.ok:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        raise StaffRequestError(detail if isinstance(detail, str) else "Não foi possível concluir a operação.")
    return response.json() if response.status_code != 204 else None


def login_form():
    st.title("Área da equipe")
    st.write("Acesso exclusivo para barbeiros e dono da barbearia. Entre com o usuário fornecido pela equipe.")
    with st.form("staff_login", clear_on_submit=True):
        username = st.text_input("Usuário", max_chars=60)
        password = st.text_input("Senha", type="password", max_chars=128)
        enter = st.form_submit_button("Entrar", type="primary")
    if enter:
        if not username.strip() or not password:
            st.warning("Informe o usuário e a senha.")
            return
        try:
            result = call_api("POST", "staff/login", data={"username": username.strip(), "password": password})
            st.session_state.staff_token = result["token"]
            st.rerun()
        except StaffRequestError as exc:
            st.error(str(exc))


def appointment_time(value):
    when = datetime.fromisoformat(value)
    return when.astimezone(TZ) if when.tzinfo else when.replace(tzinfo=TZ)


def confirm_change(appointment_id, action, data):
    try:
        call_api("POST", f"staff/appointments/{appointment_id}/{action}", data={"confirmed": True, **data})
    except StaffRequestError as exc:
        st.error(str(exc))
        st.caption("Atualize a agenda para conferir o estado da reserva antes de tentar novamente.")
        return
    st.session_state.staff_success = "Reserva cancelada." if action == "cancel" else "Reserva remarcada."
    st.session_state.pop("staff_new_slots", None)
    st.rerun()


def change_reservation(record, now):
    appointment_id = record["id"]
    st.subheader(f"Reserva #{appointment_id}")
    st.text(f"Cliente: {record['customer_name']}\nTelefone: {record['customer_phone']}")
    st.write(f"{record['service']} com {record['barber']} · {STATUS[record['status']]}")
    original = appointment_time(record["starts_at"])
    st.write(f"{original:%d/%m/%Y às %Hh%M} · {format_price(record['price'])}")
    with st.expander("Emitir código privado para o cliente"):
        st.caption("Use após confirmar a identidade do cliente. A emissão invalida o código anterior.")
        if st.button("Identidade confirmada · Emitir código", key=f"staff_code_{appointment_id}"):
            try:
                result = call_api("POST", f"staff/appointments/{appointment_id}/access-code", data={"confirmed": True})
            except StaffRequestError as exc:
                st.error(str(exc))
            else:
                st.code(result["access_code"], language=None)
                st.caption("Entregue o código somente ao cliente identificado.")
    if record["status"] != "confirmed" or original <= now:
        return

    action = st.radio("O que deseja fazer?", ["Ver detalhes", "Remarcar", "Cancelar"],
                      horizontal=True, key=f"staff_action_{appointment_id}")
    if action == "Cancelar":
        st.warning("O cancelamento libera o horário para outra reserva.")
        if st.button("Confirmar cancelamento", type="primary", key=f"staff_cancel_{appointment_id}"):
            confirm_change(appointment_id, "cancel", {})
    if action == "Remarcar":
        day = st.date_input("Nova data", value=min(original.date(), now.date() + timedelta(days=30)),
                            min_value=now.date(), max_value=now.date() + timedelta(days=30),
                            format="DD/MM/YYYY", key=f"staff_date_{appointment_id}")
        scope = (appointment_id, original, day)
        slot_key = f"staff_slot_{appointment_id}"
        if st.button("Buscar horários", disabled=day is None):
            st.session_state.pop("staff_new_slots", None)
            st.session_state.pop(slot_key, None)
            try:
                options = call_api("GET", f"staff/appointments/{appointment_id}/reschedule-options",
                                   params={"day": day.isoformat()})
                st.session_state.staff_new_slots = (scope, options)
            except StaffRequestError as exc:
                st.error(str(exc))
        previous_scope, slots = st.session_state.get("staff_new_slots", (None, None))
        if previous_scope != scope:
            st.session_state.pop("staff_new_slots", None)
            st.session_state.pop(slot_key, None)
            return
        if not slots:
            st.info("Sem horários disponíveis com esse profissional. Escolha outro dia.")
            return
        slot = st.selectbox("Novo horário", slots, index=None, key=slot_key,
                            format_func=lambda item: f"{appointment_time(item['starts_at']):%Hh%M}")
        if st.button("Confirmar remarcação", type="primary", disabled=slot is None):
            confirm_change(appointment_id, "reschedule", {"new_starts_at": slot["starts_at"]})


def agenda(member):
    now = datetime.now(TZ)
    today = now.date()
    st.header("Agenda de trabalho")
    today_action, tomorrow = st.columns(2)
    today_action.button("Hoje", key="staff_agenda_today", on_click=st.session_state.__setitem__,
                        args=("staff_agenda_day", today))
    tomorrow.button("Amanhã", key="staff_agenda_tomorrow", on_click=st.session_state.__setitem__,
                    args=("staff_agenda_day", today + timedelta(days=1)))
    date_filter, person_filter, status_filter = st.columns(3)
    st.session_state.setdefault("staff_agenda_day", today)
    day = date_filter.date_input("Data da agenda", value=None, format="DD/MM/YYYY", key="staff_agenda_day")
    if day is None:
        st.info("Escolha uma data para visualizar a agenda.")
        return
    params = {"day": day.isoformat()}
    try:
        if member["role"] == "owner":
            barbers = {item["id"]: item["name"] for item in call_api("GET", "barbers")}
            barber = person_filter.selectbox("Profissional", [None, *barbers],
                                              format_func=lambda item: barbers.get(item, "Todos"))
            if barber is not None:
                params["barber_id"] = barber
        else:
            person_filter.caption(f"Profissional · {member['display_name']}")
        status = status_filter.selectbox("Situação", ["Todos", *STATUS.values()])
        st.button("Atualizar agenda")
        records = call_api("GET", "staff/appointments", params=params)
        blocks = call_api("GET", "staff/blocks", params=params)
    except StaffRequestError as exc:
        st.error(str(exc))
        return

    scheduled, confirmed, cancelled = st.columns(3)
    scheduled.metric("Reservas no dia", len(records))
    active = sum(record["status"] == "confirmed" for record in records)
    confirmed.metric("Confirmadas", active)
    cancelled.metric("Canceladas", len(records) - active)
    if blocks:
        st.caption("Bloqueios neste dia")
        st.dataframe([{"Profissional": block["barber"], "Início": label_time(block["starts_at"]),
                       "Fim": label_time(block["ends_at"]), "Motivo": block["reason"]} for block in blocks], hide_index=True)
    records = [record for record in records if status == "Todos" or STATUS[record["status"]] == status]
    if not records:
        st.info("Nenhuma reserva para a data e os filtros selecionados.")
        return
    st.dataframe([{
        "Horário": appointment_time(record["starts_at"]).strftime("%Hh%M"),
        "Cliente": record["customer_name"], "Profissional": record["barber"],
        "Serviço": record["service"], "Situação": STATUS[record["status"]],
    } for record in records], hide_index=True)
    record = st.selectbox("Abra uma reserva para gerenciar", records, index=None,
                          format_func=lambda item: f"#{item['id']} · {appointment_time(item['starts_at']):%Hh%M} · {item['customer_name']}")
    if record is not None:
        change_reservation(record, now)


def label_time(value):
    return f"{appointment_time(value):%d/%m/%Y às %Hh%M}"


def select_professional(member, key, allow_all=False):
    if member["role"] == "barber":
        st.caption(f"Profissional · {member['display_name']}")
        return member["barber_id"]
    barbers = {item["id"]: item["name"] for item in call_api("GET", "barbers")}
    options = [None, *barbers] if allow_all else list(barbers)
    return st.selectbox("Profissional", options, key=key, format_func=lambda item: barbers.get(item, "Todos"))


def counter_booking(member):
    st.header("Reserva de balcão")
    st.caption("Para reservas feitas pessoalmente ou por telefone. Escolha um horário de hoje até 30 dias à frente.")
    today = datetime.now(TZ).date()
    try:
        barber_id = select_professional(member, "counter_barber")
        services = call_api("GET", "services")
        if barber_id is None or not services:
            st.info("Cadastre um serviço e selecione um profissional.")
            return
        service = st.selectbox("Serviço", services, key="counter_service",
                               format_func=lambda item: f"{item['name']} · {format_price(item['price'])} · {item['duration_minutes']} min")
        day = st.date_input("Data da reserva", today, min_value=today, max_value=today + timedelta(days=30),
                            format="DD/MM/YYYY", key="counter_day")
        scope = (barber_id, service["id"], service["price"], service["duration_minutes"], day)
        state = st.session_state
        if state.get("staff_counter_scope") != scope:
            state.staff_counter_scope = scope
            state.pop("staff_counter_slots", None)
            state.pop("staff_counter_slot", None)
        if st.button("Consultar horários de balcão", disabled=day is None):
            state.pop("staff_counter_slots", None)
            state.pop("staff_counter_slot", None)
            state.staff_counter_slots = call_api("GET", "staff/availability", params={
                "day": day.isoformat(), "barber_id": barber_id, "service_id": service["id"],
            })
        slots = state.get("staff_counter_slots")
        if slots is None:
            return
        if not slots:
            st.info("Nenhum horário disponível para a seleção. Tente outro dia ou serviço.")
            return
        slot = st.selectbox("Horário da reserva", slots, index=None, key="staff_counter_slot",
                            format_func=lambda item: f"{appointment_time(item['starts_at']):%Hh%M}")
        if slot is None:
            return
        with st.form("staff_counter_details"):
            name = st.text_input("Nome do cliente", max_chars=120)
            phone = st.text_input("Telefone com DDD", max_chars=30)
            save = st.form_submit_button("Confirmar reserva de balcão", type="primary")
        if save:
            if not name.strip() or sum(character.isdecimal() for character in phone) < 10:
                st.warning("Informe o nome e um telefone com DDD.")
                return
            result = call_api("POST", "staff/appointments", data={
                "customer_name": name.strip(), "customer_phone": phone.strip(), "service_id": service["id"],
                "barber_id": barber_id, "starts_at": slot["starts_at"], "confirmed": True,
                "expected_price": service["price"], "expected_duration": service["duration_minutes"],
            })
            state.pop("staff_counter_slots", None)
            st.success(f"Reserva #{result['id']} confirmada.")
            st.caption("Entregue este código privado ao cliente para acessar Meus horários.")
            st.code(result["access_code"], language=None)
    except StaffRequestError as exc:
        st.error(str(exc))


def manage_blocks(member):
    st.header("Bloqueios de agenda")
    st.caption("Use para folgas, férias ou intervalos extras. Reservas já confirmadas aparecem como conflito.")
    today = datetime.now(TZ).date()
    try:
        barber_id = select_professional(member, "block_barber")
        if barber_id is None:
            st.info("Selecione um profissional para gerenciar os bloqueios.")
            return
        with st.expander("Criar bloqueio"):
            all_day = st.checkbox("Dias inteiros", key="block_all_day")
            with st.form("staff_block_create"):
                start_col, end_col = st.columns(2)
                start_day = start_col.date_input("Data de início", today, min_value=today, format="DD/MM/YYYY")
                end_day = end_col.date_input("Data de fim", today, min_value=today, format="DD/MM/YYYY")
                if not all_day:
                    start_time = start_col.time_input("Hora de início", time(12), step=60)
                    end_time = end_col.time_input("Hora de fim", time(13), step=60)
                reason = st.text_input("Motivo", max_chars=160, placeholder="Ex.: folga ou intervalo extra")
                create = st.form_submit_button("Salvar bloqueio", type="primary")
            if create:
                if not start_day or not end_day or not reason.strip():
                    st.warning("Preencha as datas e o motivo.")
                    return
                if not all_day and (start_time is None or end_time is None):
                    st.warning("Informe a hora de início e de fim.")
                    return
                if all_day:
                    end_day += timedelta(days=1)
                    start_time = end_time = time.min
                call_api("POST", "staff/blocks", data={
                    "barber_id": barber_id, "reason": reason.strip(),
                    "starts_at": datetime.combine(start_day, start_time, tzinfo=TZ).isoformat(),
                    "ends_at": datetime.combine(end_day, end_time, tzinfo=TZ).isoformat(),
                })
                st.session_state.staff_block_day = start_day
                st.session_state.staff_success = "Bloqueio registrado na agenda."
                st.rerun()

        st.session_state.setdefault("staff_block_day", today)
        day = st.date_input("Consultar bloqueios do dia", format="DD/MM/YYYY", key="staff_block_day")
        if day is None:
            return
        blocks = call_api("GET", "staff/blocks", params={"day": day.isoformat(), "barber_id": barber_id})
        if not blocks:
            st.info("Nenhum bloqueio para este profissional na data escolhida.")
            return
        block = st.selectbox("Selecione um bloqueio", blocks, index=None,
                             format_func=lambda item: f"{item['reason']} · {label_time(item['starts_at'])} a {label_time(item['ends_at'])}")
        if block is not None:
            confirm = st.checkbox("Confirmo a liberação deste período", key=f"staff_confirm_unblock_{block['id']}")
            if st.button("Liberar período", disabled=not confirm):
                call_api("DELETE", f"staff/blocks/{block['id']}")
                st.session_state.staff_success = "Bloqueio removido."
                st.rerun()
    except StaffRequestError as exc:
        st.error(str(exc))


def edit_services():
    st.header("Serviços e preços")
    try:
        services = call_api("GET", "services")
        if not services:
            st.info("Nenhum serviço cadastrado.")
            return
        service = st.selectbox("Selecione o serviço", [*services, None],
                               format_func=lambda item: item["name"] if item else "Adicionar serviço")
        with st.form(f"edit_service_{service['id'] if service else 'new'}"):
            if service is None:
                name = st.text_input("Nome do serviço", max_chars=90)
            price = st.number_input("Preço em reais", 0.0, 1_000_000.0, float(service["price"]) if service else 0.0, step=0.5, format="%.2f")
            duration = st.number_input("Duração em minutos", 5, 390, service["duration_minutes"] if service else 30)
            save = st.form_submit_button("Salvar serviço", type="primary")
        if save:
            data = {"price": price, "duration_minutes": duration}
            if service is None:
                call_api("POST", "staff/services", data={**data, "name": name.strip()})
            else:
                call_api("PATCH", f"staff/services/{service['id']}", data=data)
            st.cache_data.clear()
            st.session_state.staff_success = "Catálogo atualizado."
            st.rerun()
    except StaffRequestError as exc:
        st.error(str(exc))


def edit_information():
    st.header("Informações e expediente")
    try:
        shop = call_api("GET", "shop")
        st.caption("Funcionamento de segunda a sábado. Domingo fechado.")
        second_period = st.checkbox("Atender em dois períodos", value=shop["open_2"] is not None)
        with st.form("shop_information"):
            name = st.text_input("Nome da barbearia", shop["name"], max_chars=100)
            address = st.text_input("Endereço", shop["address"], max_chars=250)
            phone = st.text_input("Telefone público", shop["phone"], max_chars=30)
            payments = st.text_input("Formas de pagamento", shop["payment_methods"], max_chars=160)
            left, right = st.columns(2)
            open_1 = left.time_input("Abertura", time.fromisoformat(shop["open_1"]), step=60)
            close_1 = right.time_input("Fechamento do primeiro período", time.fromisoformat(shop["close_1"]), step=60)
            open_2 = close_2 = None
            if second_period:
                open_2 = left.time_input("Reabertura", time.fromisoformat(shop["open_2"] or "13:30"), step=60)
                close_2 = right.time_input("Fechamento do segundo período", time.fromisoformat(shop["close_2"] or "20:00"), step=60)
            save = st.form_submit_button("Salvar informações", type="primary")
        if save:
            if open_1 is None or close_1 is None or (second_period and (open_2 is None or close_2 is None)):
                st.warning("Preencha os horários de abertura e fechamento.")
                return
            call_api("PUT", "staff/shop", data={
                "name": name, "address": address, "phone": phone, "payment_methods": payments,
                "open_1": open_1.strftime("%H:%M"), "close_1": close_1.strftime("%H:%M"),
                "open_2": open_2.strftime("%H:%M") if open_2 is not None else None,
                "close_2": close_2.strftime("%H:%M") if close_2 is not None else None,
            })
            st.cache_data.clear()
            st.session_state.staff_success = "Informações e expediente atualizados."
            st.rerun()

        st.subheader("Escala semanal")
        barbers = call_api("GET", "barbers")
        barber = st.selectbox("Profissional da escala", barbers, format_func=lambda item: item["name"])
        with st.form(f"working_days_{barber['id']}"):
            days = st.multiselect("Dias de atendimento", shop["open_days"], default=barber["working_days"],
                                  format_func=WEEKDAYS.__getitem__)
            save_days = st.form_submit_button("Salvar escala")
        if save_days:
            if not days:
                st.warning("Selecione pelo menos um dia. Para uma ausência temporária, crie um bloqueio.")
                return
            call_api("PUT", f"staff/barbers/{barber['id']}/working-days", data={"working_days": days})
            st.cache_data.clear()
            st.session_state.staff_success = "Escala atualizada."
            st.rerun()
    except StaffRequestError as exc:
        st.error(str(exc))


def main():
    st.set_page_config(page_title="Equipe · Barbearia Imperial", page_icon="💈", layout="wide", initial_sidebar_state="collapsed")
    if st.button("← Voltar para a barbearia"):
        st.switch_page("streamlit_app.py")
    if message := st.session_state.pop("staff_message", None):
        st.info(message)
    if not st.session_state.get("staff_token"):
        login_form()
        return
    try:
        member = call_api("GET", "staff/me")
        title, controls = st.columns([4, 1], vertical_alignment="center")
        title.title("Painel da equipe")
        role = "Dono da barbearia" if member["role"] == "owner" else "Barbeiro"
        title.caption(f"{member['display_name']} · {role}")
        if controls.button("Sair"):
            call_api("POST", "staff/logout")
            clear_staff_session()
            st.rerun()
    except StaffRequestError as exc:
        st.error(str(exc))
        return
    if success := st.session_state.pop("staff_success", None):
        st.success(success)
    sections = [("Agenda", lambda: agenda(member)), ("Balcão", lambda: counter_booking(member)),
                ("Bloqueios", lambda: manage_blocks(member))]
    if member["role"] == "owner":
        sections.extend([("Serviços", edit_services), ("Informações e escala", edit_information)])
    tab_options = {"on_change": "rerun", "key": "staff_section"} if "on_change" in signature(st.tabs).parameters else {}
    for tab, (_, render) in zip(st.tabs([label for label, _ in sections], **tab_options), sections):
        if not tab_options or tab.open:
            with tab:
                render()


main()
