import importlib.util
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
