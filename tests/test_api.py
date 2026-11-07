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
