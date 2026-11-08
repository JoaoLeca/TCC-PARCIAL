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


def test_chat_explains_missing_api_key(client):
    response = client.post("/chat", json={"session_id": "test", "messages": [{"role": "user", "content": "Oi"}]})
    assert response.status_code == 503


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

    def fake_agent(messages):
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
