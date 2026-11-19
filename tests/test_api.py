from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
import barber_bot.db as db
from barber_bot.api import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "api-test.db")
    with TestClient(app) as test_client:
        yield test_client


def test_services_and_barbers_are_available(client):
    services = client.get("/services").json()
    barbers = client.get("/barbers").json()
    assert len(services) == 4
    assert {barber["name"] for barber in barbers} == {"Rafael", "Diego", "Alecio"}


def test_api_requires_confirmation_and_prevents_duplicate_booking(client):
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    slots = []
    for offset in (1, 2, 3):
        response = client.get("/availability", params={"service_id": 1, "appointment_date": (today + timedelta(days=offset)).isoformat()})
        slots = response.json()
        if slots:
            break
    assert slots
    slot = slots[0]
    payload = {"customer_name": "Teste", "customer_phone": "16999990000", "service_id": 1, "barber_id": slot["barber_id"], "starts_at": slot["starts_at"]}
    assert client.post("/appointments", json={**payload, "confirmed": False}).status_code == 409
    created = client.post("/appointments", json={**payload, "confirmed": True})
    assert created.status_code == 200
    assert client.post("/appointments", json={**payload, "customer_phone": "16999990001", "confirmed": True}).status_code == 409


def test_chat_has_guided_help_without_api_key(client, monkeypatch):
    import barber_bot.assistant as assistant
    monkeypatch.setattr(assistant, "OPENAI_API_KEY", "")
    response = client.post("/chat", json={"session_id": "test", "messages": [{"role": "user", "content": "Oi"}]})
    assert response.status_code == 200
    assert response.json()["mode"] == "guided"


def test_chat_returns_real_clickable_booking_options(client):
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    day = next(today + timedelta(days=i) for i in range(1, 4) if (today + timedelta(days=i)).weekday() != 6)
    result = client.post("/chat", json={"session_id": "clickable-test", "messages": [
        {"role": "user", "content": f"Quero agendar corte em {day.isoformat()} depois das 15h"},
    ]}).json()
    options = result["booking_options"]
    assert 1 <= len(options["slots"]) <= 3
    assert options["service"]["id"] == 1
    available = client.get("/availability", params={"service_id": 1, "appointment_date": day.isoformat()}).json()
    assert all(slot in available for slot in options["slots"])
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM appointments").fetchone()[0] == 0


def test_customer_access_code_is_required_and_bound_to_one_booking(client):
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    day = next(today + timedelta(days=i) for i in range(1, 4) if (today + timedelta(days=i)).weekday() != 6)
    slots = client.get("/availability", params={"service_id": 1, "appointment_date": day.isoformat()}).json()
    first, second = [
        client.post("/appointments", json={
            "customer_name": "Teste", "customer_phone": "16999990000", "service_id": 1,
            "barber_id": slot["barber_id"], "starts_at": slot["starts_at"], "confirmed": True,
        }).json()
        for slot in (slots[0], next(slot for slot in slots if slot["starts_at"][11:16] == "10:00"))
    ]
    assert client.get("/appointments", params={"phone": "16999990000"}).status_code == 405
    # Only old reservations retain the legacy API. New booking responses never mint it.
    from barber_bot.schedule import _issue_access_code
    with db.connect() as conn:
        for booking in (first, second):
            assert "access_code" not in booking
            booking["access_code"] = _issue_access_code(conn, booking["id"])
    assert first["access_code"] != second["access_code"]
    credentials = {"phone": "(16) 99999-0000", "access_code": first["access_code"]}
    assert client.post("/appointments/lookup", json={"phone": credentials["phone"]}).status_code == 422
    assert client.post("/appointments/lookup", json={**credentials, "phone": "16999990001"}).status_code == 404
    lookup = client.post("/appointments/lookup", json=credentials)
    assert lookup.status_code == 200 and lookup.json()["id"] == first["id"]
    assert lookup.headers["Cache-Control"] == "no-store"
    assert "access_code" not in lookup.json()
    other = f"/appointments/{second['id']}"
    assert client.post(f"{other}/reschedule-options", json={**credentials, "appointment_date": day.isoformat()}).status_code == 404
    assert client.post(f"{other}/reschedule", json={**credentials, "confirmed": True, "new_starts_at": slots[-1]["starts_at"]}).status_code == 404
    assert client.post(f"{other}/cancel", json={**credentials, "confirmed": True}).status_code == 404


def test_chat_does_not_send_or_log_private_codes(client, monkeypatch):
    import barber_bot.api as api

    code = "BI-" + "a" * 24

    def fake_agent(messages, **kwargs):
        assert messages == [{"role": "user", "content": "Meu código é [código privado]"}]
        return {"content": "Use Meus horários.", "intent": "consultar_agendamento", "confidence": 1, "tools_called": []}

    monkeypatch.setattr(api, "OPENAI_API_KEY", "test")
    monkeypatch.setattr(api, "run_agent", fake_agent)
    response = client.post("/chat", json={
        "session_id": "test", "messages": [{"role": "user", "content": f"Meu código é {code}"}],
    })
    assert response.status_code == 200
    with db.connect() as conn:
        assert conn.execute("SELECT user_message FROM interaction_logs").fetchone()[0] == "Meu código é [código privado]"


def owned_booking(client, token="owner-" + "x" * 40, phone="16999990042"):
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    for offset in range(1, 8):
        slots = client.get("/availability", params={"service_id": 1, "appointment_date": (today + timedelta(days=offset)).isoformat()}).json()
        if slots:
            break
    response = client.post("/appointments", json={
        "customer_name": "Cliente", "customer_phone": phone, "service_id": 1,
        "barber_id": slots[0]["barber_id"], "starts_at": slots[0]["starts_at"],
        "confirmed": True, "customer_token": token,
    })
    assert response.status_code == 200
    return response.json()


def test_owned_api_separates_identification_from_authorization(client):
    token, other = "owner-" + "x" * 40, "other-" + "y" * 40
    first = owned_booking(client, token)
    second = owned_booking(client, other, "11988880042")
    assert first["identification_code"] == second["identification_code"] == "0042"
    assert not {"customer_phone", "customer_name", "access_code", "customer_token"} & first.keys()
    credentials = {"customer_token": token}
    response = client.post("/customer/appointments", json=credentials)
    assert [row["id"] for row in response.json()] == [first["id"]]
    assert response.headers["Cache-Control"] == "no-store"
    foreign = f"/customer/appointments/{second['id']}"
    assert client.post(foreign, json=credentials).status_code == 404
    assert client.post(foreign + "/cancel", json={**credentials, "confirmed": True, "expected_starts_at": second["starts_at"]}).status_code == 404
    assert client.post(foreign + "/reschedule-options", json={**credentials, "appointment_date": second["starts_at"][:10]}).status_code == 404
    assert client.post(foreign + "/reschedule", json={**credentials, "confirmed": True, "expected_starts_at": second["starts_at"], "new_starts_at": first["starts_at"]}).status_code == 404
    assert client.post("/customer/appointments", json={"customer_token": "0042"}).status_code == 422
    assert client.post("/appointments/lookup", json={"phone": "16999990042", "access_code": "0042"}).status_code == 404
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM appointment_access").fetchone()[0] == 0


def test_owned_changes_require_confirmation_and_current_snapshot(client):
    token = "owner-" + "x" * 40
    booking = owned_booking(client, token)
    endpoint = f"/customer/appointments/{booking['id']}"
    payload = {"customer_token": token, "expected_starts_at": booking["starts_at"]}
    assert client.post(endpoint + "/cancel", json=payload).status_code == 409
    assert client.post(endpoint + "/cancel", json={**payload, "confirmed": True, "expected_starts_at": "stale"}).status_code == 409
    assert client.post(endpoint, json={"customer_token": token}).json()["status"] == "confirmed"
    assert client.post(endpoint + "/cancel", json={**payload, "confirmed": True}).json()["status"] == "cancelled"


def test_chat_management_uses_owner_and_separate_written_confirmation(client, monkeypatch):
    token = "owner-" + "x" * 40
    booking = owned_booking(client, token)

    def chat(text, owner=token):
        return client.post("/chat", json={"session_id": "analytics-only", "customer_token": owner,
                                          "messages": [{"role": "user", "content": text}]}).json()

    reply = chat("Quero cancelar minha reserva")
    assert "Confirma o cancelamento" in reply["answer"] and not reply["booking_options"]
    foreign = chat("Quero cancelar minha reserva", "other-" + "y" * 40)
    assert foreign["recovery_stage"] == "phone" and "telefone completo" in foreign["answer"]
    endpoint = f"/customer/appointments/{booking['id']}"
    assert client.post(endpoint, json={"customer_token": token}).json()["status"] == "confirmed"
    assert "Cancelamento confirmado" in chat("sim")["answer"]
    assert client.post(endpoint, json={"customer_token": token}).json()["status"] == "cancelled"
    with db.connect() as conn:
        assert token not in str([tuple(row) for row in conn.execute("SELECT * FROM interaction_logs")])


def test_customer_token_never_reaches_the_model(client, monkeypatch):
    import barber_bot.api as api
    token = "owner-" + "x" * 40
    def fake_agent(messages, **kwargs):
        assert token not in str(messages) + str(kwargs)
        return {"content": "Olá", "intent": "outro", "confidence": 1, "tools_called": []}
    monkeypatch.setattr(api, "run_agent", fake_agent)
    response = client.post("/chat", json={"session_id": "analytics-only", "customer_token": token,
                                         "messages": [{"role": "user", "content": "Oi"}]})
    assert response.status_code == 200


def test_recovery_api_and_chat_restore_access_to_another_session_without_staff(client, monkeypatch):
    import barber_bot.customer_recovery as recovery
    monkeypatch.setattr(recovery, "get_bot_username", lambda: "ImperialTesteBot")
    monkeypatch.setattr(recovery.secrets, "randbelow", lambda limit: 123456)
    record = owned_booking(client)
    token = "recovered-browser-" + "z" * 40
    response = client.post("/customer/recovery", json={"customer_token": token, "phone": "16999990042"})
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    challenge = response.json()
    assert "code" not in challenge
    verify = {"customer_token": token, "challenge_id": challenge["challenge_id"], "code": "123456"}
    assert client.post("/customer/recovery/verify", json=verify).status_code == 400
    recovery.issue_telegram_code(challenge["challenge_id"], "123", "123", {"user_id": 123, "phone_number": "+5516999990042"})
    verified = client.post("/customer/recovery/verify", json=verify)
    assert [item["id"] for item in verified.json()] == [record["id"]]
    assert client.post("/customer/recovery/verify", json=verify).status_code == 400
    assert "Confirma o cancelamento" in client.post("/chat", json={"customer_token": token, "session_id": "recovered",
             "messages": [{"role": "user", "content": "quero cancelar minha reserva"}]}).json()["answer"]


def test_chat_recovery_keeps_code_and_phone_out_of_model_and_logs(client, monkeypatch):
    import barber_bot.api as api
    import barber_bot.customer_recovery as recovery
    monkeypatch.setattr(recovery, "get_bot_username", lambda: "ImperialTesteBot")
    monkeypatch.setattr(recovery.secrets, "randbelow", lambda limit: 123456)
    monkeypatch.setattr(api, "run_agent", lambda *a, **k: pytest.fail("Recovery must not call the model"))
    owned_booking(client)
    token = "recovered-browser-" + "z" * 40
    def chat(text, private=None):
        body = {"customer_token": token, "session_id": "recover-chat", "messages": [{"role": "user", "content": text}]}
        if private:
            body["recovery_input"] = private
        return client.post("/chat", json=body).json()
    assert chat("quero cancelar minha reserva")["recovery_stage"] == "phone"
    response = chat("[telefone]", "16999990042")
    assert response["recovery_stage"] == "code" and "https://t.me/" in response["answer"]
    nonce = response["answer"].split("recover_", 1)[1].split(")", 1)[0]
    recovery.issue_telegram_code(nonce, "123", "123", {"user_id": 123, "phone_number": "5516999990042"})
    assert "Código inválido" in chat("000000")["answer"]
    result = chat("[código de verificação]", "123456")
    assert "Telefone confirmado" in result["answer"] and "Confirma o cancelamento" in result["answer"]
    with db.connect() as conn:
        assert conn.execute("SELECT status FROM appointments").fetchone()[0] == "confirmed"
        logs = str([tuple(row) for row in conn.execute("SELECT user_message FROM interaction_logs")])
        assert "123456" not in logs and "16999990042" not in logs and "000000" not in logs
    assert "Cancelamento confirmado" in chat("sim")["answer"]


def test_recovery_links_are_removed_from_model_history_after_abort(client, monkeypatch):
    import barber_bot.api as api
    nonce = "aB-cDeF_" * 4
    def model(messages, **kwargs):
        assert nonce not in str(messages) and "recover_" not in str(messages)
        return {"content": "Olá", "intent": "outro", "confidence": 1, "tools_called": []}
    monkeypatch.setattr(api, "run_agent", model)
    result = client.post("/chat", json={"session_id": "history-test", "customer_token": "owner-" + "x" * 40,
         "messages": [{"role": "assistant", "content": f"[Abrir Telegram](https://t.me/ImperialTesteBot?start=recover_{nonce})"},
                      {"role": "user", "content": "Oi"}]})
    assert result.status_code == 200


def test_support_contact_is_bound_to_original_session(client):
    session_id = "session-original-123456"
    request = client.post("/support/requests", json={"session_id": session_id}).json()
    contact = {"session_id": "session-unknown-123456", "ticket_id": request["id"],
               "customer_name": "Cliente", "customer_phone": "16999990000"}
    assert client.post("/support/contact", json=contact).status_code == 404
    response = client.post("/support/contact", json={**contact, "session_id": session_id})
    assert response.status_code == 200
    assert "customer_phone" not in response.json()
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/staff/support").status_code == 401
    client.post("/support/requests", json={"session_id": session_id})
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM interaction_logs WHERE outcome='escalated'").fetchone()[0] == 1


def test_support_confirmation_requires_contact_when_form_is_submitted(client):
    payload = {"session_id": "confirmed-contact-session", "customer_name": "", "customer_phone": ""}
    assert client.post("/support/requests", json=payload).status_code == 422
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM support_tickets").fetchone()[0] == 0
    payload.update(customer_name="Cliente", customer_phone="16999990000")
    first = client.post("/support/requests", json=payload)
    second = client.post("/support/requests", json=payload)
    assert first.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    status = client.post("/support/status", json={"session_id": payload["session_id"]}).json()["request"]
    assert status["contact_provided"] is True


def test_customer_support_status_reports_lifecycle_without_private_data(client):
    from barber_bot.staff import StaffMember
    from barber_bot.support import update_staff_request

    session_id = "session-support-status-123"
    assert client.post("/support/status", json={"session_id": session_id}).json() == {"request": None}
    ticket = client.post("/support/requests", json={"session_id": session_id}).json()
    payload = {"session_id": session_id, "ticket_id": ticket["id"]}
    response = client.post("/support/status", json=payload)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    record = response.json()["request"]
    assert set(record) == {"id", "status", "updated_at", "contact_provided"}
    assert record["status"] == "open" and record["contact_provided"] is False
    client.post("/support/contact", json={**payload, "customer_name": "Ana", "customer_phone": "16999990000"})
    owner = StaffMember("dono", "owner", None, "Dono")
    update_staff_request(ticket["id"], owner, claim=True, note="Nota privada")
    assert client.post("/support/status", json=payload).json()["request"]["status"] == "in_progress"
    update_staff_request(ticket["id"], owner, status="resolved")
    record = client.post("/support/status", json={"session_id": session_id}).json()["request"]
    assert record["status"] == "resolved" and record["contact_provided"] is True
    assert "Ana" not in str(record) and "Nota privada" not in str(record)
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM support_tickets").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM interaction_logs").fetchone()[0] == 1


def test_customer_support_status_hides_foreign_protocols_and_validates_input(client):
    from barber_bot.support import create_support_request

    session_id = "session-support-status-123"
    own = create_support_request(session_id, "web", "Ajuda")
    telegram = create_support_request(session_id, "telegram", "Outro canal")
    results = [client.post("/support/status", json=payload) for payload in (
        {"session_id": "session-other-customer-123", "ticket_id": own["id"]},
        {"session_id": session_id, "ticket_id": telegram["id"]},
        {"session_id": session_id, "ticket_id": 99999},
    )]
    assert all(response.status_code == 404 and response.headers["Cache-Control"] == "no-store" for response in results)
    assert results[0].json() == results[1].json() == results[2].json()
    for payload in ({"session_id": "short"}, {"session_id": "s" * 129},
                    {"session_id": session_id, "ticket_id": 0}, {"session_id": session_id, "ticket_id": -1}):
        response = client.post("/support/status", json=payload)
        assert response.status_code == 422
        assert response.headers["Cache-Control"] == "no-store"
