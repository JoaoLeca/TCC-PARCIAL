import importlib.util
import csv
import sqlite3
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "eval" / "metrics.py"
SPEC = importlib.util.spec_from_file_location("evaluation_metrics", MODULE_PATH)
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


def test_intent_accuracy_and_macro_f1():
    result = metrics.intent_metrics([
        {"true_intent": "agendar", "predicted_intent": "agendar"},
        {"true_intent": "cancelar", "predicted_intent": "agendar"},
    ])
    assert result["n"] == 2
    assert result["accuracy"] == 0.5
    assert result["macro_f1"] == 1 / 3


def test_fcr_requires_success_without_escalation_or_repeat():
    result = metrics.fcr_metrics([
        {"expected_task_achieved": "1", "escalated": "0", "repeated_question": "0"},
        {"expected_task_achieved": "1", "escalated": "1", "repeated_question": "0"},
        {"expected_task_achieved": "0", "escalated": "0", "repeated_question": "0"},
    ])
    assert result["fcr"] == 1 / 3


def test_mean_latency_and_likert_distribution():
    assert metrics.response_metrics([{"response_ms": "100"}, {"response_ms": "300"}])["mean_ms"] == 200
    result = metrics.likert_metrics([{"rating": "4"}, {"rating": "5"}, {"rating": "4"}])
    assert result["mean"] == 13 / 3
    assert result["distribution"] == {4: 2, 5: 1}


def test_session_duration_uses_first_and_last_interaction():
    result = metrics.session_duration_metrics([
        {"session_id": "a", "created_at": "2026-11-01 10:00:00"},
        {"session_id": "a", "created_at": "2026-11-01 10:05:00"},
        {"session_id": "b", "created_at": "2026-11-01 11:00:00"},
    ])
    assert result == {"n": 2, "mean_minutes": 2.5}


def test_fcr_does_not_infer_resolution_from_telemetry_or_missing_human_labels():
    result = metrics.fcr_metrics([
        {"outcome": "answered"},
        {"outcome": "booking_confirmed"},
        {"expected_task_achieved": "1"},
        {"expected_task_achieved": "1", "escalated": "0", "repeated_question": ""},
        {"expected_task_achieved": "1", "escalated": "0", "repeated_question": "0"},
    ])
    assert result == {"scenarios": 1, "resolved_first_contact": 1, "fcr": 1.0}
    assert metrics.fcr_metrics([{"outcome": "answered"}])["fcr"] is None


def test_response_latency_excludes_booking_events_and_reports_tail():
    result = metrics.response_metrics([
        {"response_ms": "100", "outcome": "answered"},
        {"response_ms": "300", "outcome": "escalated"},
        {"response_ms": "0", "outcome": "booking_confirmed"},
        {"response_ms": "-3", "outcome": "error"},
        {"response_ms": "nan"},
        {"response_ms": "inf"},
        {"response_ms": "not measured"},
        {"response_ms": None},
    ])
    assert result == {"n": 2, "mean_ms": 200.0, "median_ms": 200.0, "p95_ms": 300.0}


def test_operational_metrics_distinguish_responses_sessions_and_saved_bookings():
    result = metrics.automation_metrics([
        {"session_id": "a", "outcome": "answered", "channel": "web"},
        {"session_id": "a", "outcome": "booking_confirmed", "channel": "web"},
        {"session_id": "a", "outcome": "booking_confirmed", "channel": "web"},
        {"session_id": "b", "outcome": "escalated", "channel": "telegram"},
        {"session_id": "b", "outcome": "escalated", "channel": "telegram"},
        {"session_id": "c", "outcome": "unavailable", "channel": "web"},
        {"session_id": "d", "outcome": "error", "channel": "web"},
    ])
    assert result["interactions"] == 5
    assert result["sessions"] == 4
    assert result["escalations"] == 2
    assert result["escalation_rate"] == 2 / 5
    assert result["escalated_sessions"] == 1
    assert result["session_escalation_rate"] == 1 / 4
    assert result["completed_bookings"] == 2
    assert result["booked_sessions"] == 1
    assert result["errors"] == 1
    assert result["unavailable"] == 1
    assert result["channels"] == {"web": 3, "telegram": 2}


def test_operational_metrics_accept_old_exports_without_inventing_outcomes():
    result = metrics.automation_metrics([{"session_id": "a", "response_ms": "12"}])
    assert result["interactions"] == 1
    assert result["outcomes"] == {"unknown": 1}
    assert result["completed_bookings"] == 0
    assert metrics.automation_metrics([])["escalation_rate"] is None


def test_export_omits_messages_hashes_sessions_and_deduplicates_booking_events(tmp_path):
    spec = importlib.util.spec_from_file_location("evaluation_export", MODULE_PATH.with_name("export_logs.py"))
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE interaction_logs (id INTEGER PRIMARY KEY, session_id TEXT, created_at TEXT, user_message TEXT, intent TEXT, tools_called TEXT, outcome TEXT, latency_ms INTEGER)")
        conn.executemany("INSERT INTO interaction_logs VALUES (?,?,?,?,?,?,?,?)", [
            (1, "telegram:12345", "2026-11-17 12:00:00", "Meu telefone 16999990000", "agendar", "consultar_disponibilidade", "answered", 20),
            (2, "telegram:12345", "2026-11-17 12:00:01", "Reserva #7", "agendar", "", "booking_confirmed", 0),
            (3, "telegram:12345", "2026-11-17 12:00:02", "Reserva #7", "agendar", "", "booking_confirmed", 0),
            (4, "web-uuid", "2026-11-17 12:00:03", "Reserva #7", "agendar", "", "booking_confirmed", 0),
        ])
        first = tmp_path / "first.csv"
        second = tmp_path / "second.csv"
        assert exporter.export_logs(conn, first) == 3
        exporter.export_logs(conn, second)
    assert first.read_bytes() == second.read_bytes()
    content = first.read_text(encoding="utf-8")
    assert "16999990000" not in content
    assert "telegram:12345" not in content
    assert "Reserva #7" not in content
    with first.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    assert set(rows[0]) == {"session_id", "created_at", "intent", "response_ms", "channel", "outcome", "tools_called"}
    assert rows[0]["session_id"] == rows[1]["session_id"]
    assert rows[0]["session_id"] != rows[2]["session_id"]
    assert rows[0]["channel"] == "telegram"
    assert rows[2]["channel"] == "web"
    assert metrics.automation_metrics(rows)["completed_bookings"] == 2


def test_task_metrics_count_only_observed_results_and_valid_times():
    rows = [
        {"participante_id": "P01", "tarefa_id": "T01", "resultado": "independente", "inicio": "10:00:00", "fim": "10:01:30"},
        {"participante_id": "P02", "tarefa_id": "T01", "resultado": "com_ajuda", "inicio": "11:00", "fim": "11:03"},
        {"participante_id": "P03", "tarefa_id": "T01", "resultado": "nao_concluida", "inicio": "", "fim": ""},
        {"participante_id": "P01", "tarefa_id": "T02", "resultado": "independente", "inicio": "10:05:00", "fim": "10:04:00"},
        {"participante_id": "P04", "tarefa_id": "T02", "resultado": "", "inicio": "", "fim": ""},
    ]
    result = metrics.task_metrics(rows)
    assert result["participantes"] == 3
    assert result["linhas_sem_resultado"] == 1
    t01 = result["tarefas"]["T01"]
    assert (t01["n"], t01["independente"], t01["com_ajuda"], t01["nao_concluida"]) == (3, 1, 1, 1)
    assert t01["taxa_independente"] == 1 / 3
    assert t01["mediana_segundos"] == 135 and t01["n_tempos"] == 2
    assert result["tarefas"]["T02"]["mediana_segundos"] is None


def test_blank_observation_sheet_reports_no_results():
    import csv
    from pathlib import Path
    sheet = Path(__file__).resolve().parents[1] / "eval" / "participantes" / "ficha_observacao.csv"
    with sheet.open(encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    result = metrics.task_metrics(rows)
    assert result["tarefas"] == {} and result["participantes"] == 0
    assert result["linhas_sem_resultado"] == len(rows) == 10
