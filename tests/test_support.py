import importlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from barber_bot import db
from barber_bot.api import app
from barber_bot.staff import StaffMember, save_staff_account


@pytest.fixture
def support_db(tmp_path, monkeypatch):
    path = tmp_path / "support.db"
    monkeypatch.setattr(db, "DATABASE_PATH", path)
    db.initialize_database()
    return path


def service():
    assert importlib.util.find_spec("barber_bot.support"), "Support queue service is missing"
    return importlib.import_module("barber_bot.support")


def test_support_migrates_legacy_tickets_without_losing_contact(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE support_tickets (id INTEGER PRIMARY KEY, customer_name TEXT, customer_phone TEXT, reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("INSERT INTO support_tickets VALUES (9, 'Ana', '16999990000', 'Ajuda', 'open', '2026-01-01 10:00:00')")
    db.initialize_database(path)
    db.initialize_database(path)
    with db.connect(path) as conn:
        row = dict(conn.execute("SELECT * FROM support_tickets WHERE id=9").fetchone())
    assert row["customer_name"] == "Ana"
    assert row["customer_phone"] == "16999990000"
    assert row.get("channel") == "legacy"
    assert row.get("updated_at") == "2026-01-01 10:00:00"
    assert row["session_id"] is None and row["assigned_to"] is None


def test_public_request_deduplicates_active_session_and_contact_is_session_bound(support_db):
    support = service()
    first = support.create_support_request("browser-a", "web", "Preciso de ajuda")
    assert support.create_support_request("browser-a", "web", "Outra tentativa")["id"] == first["id"]
    assert support.get_open_request("browser-a", "web")["id"] == first["id"]
    assert support.get_open_request("browser-a", "telegram") is None
    with pytest.raises(support.SupportError):
        support.update_support_contact("browser-b", "web", first["id"], "Outro", "16999990000")
    with pytest.raises(support.SupportError):
        support.update_support_contact("browser-a", "telegram", first["id"], "Outro", "16999990000")
    updated = support.update_support_contact("browser-a", "web", first["id"], "Ana", "(16) 99999-0000")
    assert updated["customer_name"] == "Ana" and updated["customer_phone"] == "16999990000"
    assert "history" not in updated and "session_id" not in updated


def test_support_endpoints_require_login(support_db):
    with TestClient(app) as api:
        assert api.get("/staff/support").status_code == 401
        assert api.patch("/staff/support/1", json={"claim": True}).status_code == 401


def test_support_staff_scope_lifecycle_and_owner_visibility(support_db):
    support = service()
    ticket = support.create_support_request("browser-a", "web", "Preciso de ajuda")
    with TestClient(app) as api:
        save_staff_account("dono", "Senha exclusiva do dono", "owner")
        save_staff_account("rafael", "Senha exclusiva Rafael", "barber", 1)
        save_staff_account("diego", "Senha exclusiva Diego", "barber", 2)
        def headers(username, password):
            token = api.post("/staff/login", json={"username": username, "password": password}).json()["token"]
            return {"Authorization": f"Bearer {token}"}
        owner = headers("dono", "Senha exclusiva do dono")
        rafael = headers("rafael", "Senha exclusiva Rafael")
        diego = headers("diego", "Senha exclusiva Diego")
        url = f"/staff/support/{ticket['id']}"
        assert api.get("/staff/support", headers=rafael).json()[0]["id"] == ticket["id"]
        assert api.patch(url, json={"status": "resolved"}, headers=rafael).status_code == 403
        claimed = api.patch(url, json={"claim": True}, headers=rafael)
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["assigned_to"] == "rafael" and claimed.json()["status"] == "in_progress"
        assert api.get("/staff/support", headers=diego).json() == []
        assert api.patch(url, json={"claim": True}, headers=diego).status_code == 409
        assert api.patch(url, json={"note": "Outro atendente"}, headers=diego).status_code == 403
        assert api.get("/staff/support", headers=owner).json()[0]["id"] == ticket["id"]
        resolved = api.patch(url, json={"status": "resolved", "note": "Cliente atendido"}, headers=rafael)
        assert resolved.status_code == 200
        assert resolved.json()["resolved_at"]
        assert resolved.json()["notes"][-1]["note"] == "Cliente atendido"
        assert api.get("/staff/support", params={"status": "open"}, headers=owner).json() == []
        assert support.get_open_request("browser-a", "web") is None
        assert api.patch(url, json={"status": "open"}, headers=owner).status_code == 409
        assert api.patch(url, json={"claim": True}, headers=owner).status_code == 409
        assert support.create_support_request("browser-a", "web", "Novo atendimento")["id"] != ticket["id"]


def test_concurrent_claim_has_one_winner_and_preserves_assignee(support_db):
    support = service()
    ticket = support.create_support_request("browser-a", "web", "Ajuda")
    barrier = Barrier(2)
    def claim(username, barber_id):
        member = StaffMember(username, "barber", barber_id, username)
        barrier.wait()
        try:
            return support.update_staff_request(ticket["id"], member, claim=True)["assigned_to"]
        except support.SupportError as exc:
            assert exc.status_code == 409
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim, "rafael", 1), pool.submit(claim, "diego", 2)]
        results = [future.result() for future in futures]
    assert sum(value is not None for value in results) == 1
    with db.connect() as conn:
        assert conn.execute("SELECT assigned_to FROM support_tickets WHERE id=?", (ticket["id"],)).fetchone()[0] == next(value for value in results if value)


def test_support_reason_notes_and_history_omit_private_data(support_db):
    support = service()
    ticket = support.create_support_request("browser-a", "web", "BI-" + "a" * 24 + " telefone 16999990000 email ana@example.com")
    owner = StaffMember("dono", "owner", None, "Dono")
    record = support.update_staff_request(ticket["id"], owner, note="BI-" + "b" * 24 + " ligar 16999990000")
    assert "16999990000" not in record["reason"] and "ana@example.com" not in record["reason"]
    assert "BI-" not in record["reason"] and "BI-" not in record["notes"][0]["note"]
    assert "16999990000" not in record["notes"][0]["note"]
    assert len(record["history"]) <= 10


def test_history_is_bounded_to_ticket_channel_and_excludes_raw_messages(support_db):
    support = service()
    with db.connect() as conn:
        for index in range(15):
            conn.execute("""INSERT INTO interaction_logs(session_id,channel,user_message,intent,tools_called,outcome,created_at)
                            VALUES ('shared','web','Contato privado Ana 16999990000',?,'[]','sucesso','2026-01-01 10:00:00')""", (f"intent-{index}",))
        conn.execute("""INSERT INTO interaction_logs(session_id,channel,user_message,intent,created_at)
                        VALUES ('shared','telegram','Outra pessoa','private-telegram','2026-01-01 10:00:00')""")
    support.create_support_request("shared", "web", "Ajuda")
    owner = StaffMember("dono", "owner", None, "Dono")
    history = support.list_staff_requests(owner)[0]["history"]
    assert len(history) == 10
    assert history[0]["intent"] == "intent-5" and history[-1]["intent"] == "intent-14"
    assert history[0]["created_at"] == "2026-01-01 10:00:00"
    assert all("user_message" not in row for row in history)


def test_concurrent_public_intake_creates_one_active_ticket(support_db):
    support = service()
    barrier = Barrier(2)
    def create():
        barrier.wait()
        return support.create_support_request("browser-a", "web", "Ajuda")["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create) for _ in range(2)]
        assert futures[0].result() == futures[1].result()


def test_customer_status_tracks_lifecycle_and_returns_only_safe_fields(support_db):
    support = service()
    assert support.get_customer_request("browser-a", "web") is None
    ticket = support.create_support_request("browser-a", "web", "Motivo reservado")
    owner = StaffMember("dono", "owner", None, "Dono")
    for expected, change in (("open", {}), ("in_progress", {"claim": True}),
                             ("resolved", {"status": "resolved", "note": "Observação interna"})):
        if change:
            support.update_staff_request(ticket["id"], owner, **change)
        record = support.get_customer_request("browser-a", "web")
        assert set(record) == {"id", "status", "updated_at", "contact_provided"}
        assert record["id"] == ticket["id"] and record["status"] == expected
        assert record["updated_at"] and record["contact_provided"] is False
    newer = support.create_support_request("browser-a", "web", "Outra solicitação", "Ana", "16999990000")
    assert support.get_customer_request("browser-a", "web")["id"] == newer["id"]
    assert support.get_customer_request("browser-a", "web")["contact_provided"] is True
    assert support.get_customer_request("browser-a", "web", ticket["id"])["status"] == "resolved"


def test_customer_status_is_bound_to_session_and_channel_without_existence_leak(support_db):
    support = service()
    ticket = support.create_support_request("browser-a", "web", "Ajuda")
    errors = []
    for session_id, channel, ticket_id in (("browser-b", "web", ticket["id"]),
                                           ("browser-a", "telegram", ticket["id"]),
                                           ("browser-a", "web", ticket["id"] + 1000)):
        with pytest.raises(support.SupportError) as error:
            support.get_customer_request(session_id, channel, ticket_id)
        errors.append((error.value.status_code, str(error.value)))
    assert len(set(errors)) == 1 and errors[0][0] == 404
    assert support.get_customer_request("browser-b", "web") is None
    assert support.get_customer_request("browser-a", "telegram") is None


def test_staff_support_ui_claims_then_resolves_request(support_db, monkeypatch):
    support = service()
    ticket = support.create_support_request("browser-a", "web", "Ajuda na reserva", "Ana", "16999990000")
    with db.connect() as conn:
        conn.execute("UPDATE support_tickets SET created_at='2026-01-01 12:00:00' WHERE id=?", (ticket["id"],))
    with TestClient(app) as api:
        save_staff_account("rafael", "Senha exclusiva Rafael", "barber", 1)
        token = api.post("/staff/login", json={"username": "rafael", "password": "Senha exclusiva Rafael"}).json()["token"]
        def request(_session, method, url, *, params=None, json=None, headers=None, timeout=None):
            result = api.request(method, urlsplit(url).path, params=params, json=json, headers=headers)
            response = requests.Response()
            response.status_code = result.status_code
            response._content = result.content
            response.encoding = "utf-8"
            return response
        monkeypatch.setattr(requests.Session, "request", request)
        monkeypatch.setenv("API_BASE_URL", "http://test-support-ui.example")
        script = Path(__file__).resolve().parents[1] / "src" / "pages" / "equipe.py"
        ui = AppTest.from_file(str(script), default_timeout=15)
        ui.session_state["staff_token"] = token
        ui.session_state["staff_section"] = "Atendimento"
        ui.run()
        assert not ui.exception
        assert any(header.value == "Atendimento humano" for header in ui.header)
        assert ui.dataframe[0].value.iloc[0]["Abertura"] == "01/01/2026 às 09h00"
        ui.selectbox(key="staff_support_ticket").select_index(0).run()
        assert not any("16999990000" in item.value for item in ui.text)
        next(widget for widget in ui.button if widget.label == "Assumir atendimento").click().run()
        assert not ui.exception
        ui.text_area(key=f"staff_support_note_{ticket['id']}").input("Contato realizado")
        next(widget for widget in ui.button if widget.label == "Resolver atendimento").click().run()
        assert not ui.exception
        assert support.get_open_request("browser-a", "web") is None
