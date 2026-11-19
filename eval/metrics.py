"""Calcula métricas a partir de CSVs preenchidos após a avaliação."""
import argparse
import csv
import math
from collections import Counter
from datetime import datetime
from statistics import median


def read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def intent_metrics(rows: list[dict]) -> dict:
    rows = [row for row in rows if row.get("true_intent") and row.get("predicted_intent")]
    if not rows:
        return {"n": 0, "accuracy": None, "macro_f1": None, "confusion": {}}
    labels = sorted({row["true_intent"] for row in rows} | {row["predicted_intent"] for row in rows})
    matrix = {actual: Counter() for actual in labels}
    correct = 0
    for row in rows:
        actual, predicted = row["true_intent"], row["predicted_intent"]
        matrix[actual][predicted] += 1
        correct += actual == predicted
    scores = []
    for label in labels:
        tp = matrix[label][label]
        fp = sum(matrix[other][label] for other in labels if other != label)
        fn = sum(matrix[label][other] for other in labels if other != label)
        precision = tp / (tp + fp) if tp + fp else 0
        recall = tp / (tp + fn) if tp + fn else 0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0)
    return {"n": len(rows), "accuracy": correct / len(rows), "macro_f1": sum(scores) / len(scores), "labels": labels, "confusion": {key: dict(value) for key, value in matrix.items()}}


def fcr_metrics(rows: list[dict]) -> dict:
    # Só a avaliação final dos revisores conta como resolução.
    fields = ("expected_task_achieved", "escalated", "repeated_question")
    eligible = [r for r in rows if all(r.get(field) in ("0", "1") for field in fields)]
    solved = [r for r in eligible if r["expected_task_achieved"] == "1" and r["escalated"] == "0" and r["repeated_question"] == "0"]
    return {"scenarios": len(eligible), "resolved_first_contact": len(solved), "fcr": len(solved) / len(eligible) if eligible else None}


def response_metrics(rows: list[dict]) -> dict:
    values = []
    for row in rows:
        if row.get("outcome") == "booking_confirmed":
            continue
        try:
            value = float(row.get("response_ms", ""))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value >= 0:
            values.append(value)
    values.sort()
    return {
        "n": len(values),
        "mean_ms": sum(values) / len(values) if values else None,
        "median_ms": median(values) if values else None,
        "p95_ms": values[math.ceil(0.95 * len(values)) - 1] if values else None,
    }


def automation_metrics(rows: list[dict]) -> dict:
    """Operational counts; booking events must already be deduplicated by exporter."""
    interactions = [row for row in rows if row.get("outcome") != "booking_confirmed"]
    bookings = [row for row in rows if row.get("outcome") == "booking_confirmed"]
    outcomes = Counter(row.get("outcome") or "unknown" for row in interactions)
    channels = Counter(row.get("channel") or "unknown" for row in interactions)
    sessions = {row["session_id"] for row in interactions if row.get("session_id")}
    escalated_sessions = {row["session_id"] for row in interactions if row.get("outcome") == "escalated" and row.get("session_id")}
    booked_sessions = {row["session_id"] for row in bookings if row.get("session_id")}
    return {
        "interactions": len(interactions), "sessions": len(sessions),
        "escalations": outcomes["escalated"],
        "escalation_rate": outcomes["escalated"] / len(interactions) if interactions else None,
        "escalated_sessions": len(escalated_sessions),
        "session_escalation_rate": len(escalated_sessions) / len(sessions) if sessions else None,
        "completed_bookings": len(bookings), "booked_sessions": len(booked_sessions),
        "unavailable": outcomes["unavailable"], "errors": outcomes["error"],
        "outcomes": dict(outcomes), "channels": dict(channels),
    }


def session_duration_metrics(rows: list[dict]) -> dict:
    sessions: dict[str, list[datetime]] = {}
    for row in rows:
        if row.get("outcome") == "booking_confirmed":
            continue
        session_id, timestamp = row.get("session_id", ""), row.get("created_at", "")
        if session_id and timestamp:
            try:
                sessions.setdefault(session_id, []).append(datetime.fromisoformat(timestamp))
            except ValueError:
                continue
    durations = [(max(times) - min(times)).total_seconds() / 60 for times in sessions.values()]
    return {"n": len(durations), "mean_minutes": sum(durations) / len(durations) if durations else None}


def likert_metrics(rows: list[dict]) -> dict:
    values = [int(r["rating"]) for r in rows if r.get("rating", "").strip()]
    return {"n": len(values), "mean": sum(values) / len(values) if values else None, "distribution": dict(sorted(Counter(values).items()))}


TASK_RESULTS = ("independente", "com_ajuda", "nao_concluida")


def _clock_seconds(value: str) -> int | None:
    parts = (value or "").strip().split(":")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        return None
    hours, minutes, seconds = (int(part) for part in (parts + ["0"])[:3])
    return hours * 3600 + minutes * 60 + seconds if minutes < 60 and seconds < 60 else None


def task_metrics(rows: list[dict]) -> dict:
    """Resume a ficha de observação; linhas sem resultado válido não entram nos denominadores."""
    tasks: dict[str, dict] = {}
    pending = 0
    for row in rows:
        task, result = (row.get("tarefa_id") or "").strip(), (row.get("resultado") or "").strip()
        if not task:
            continue
        if result not in TASK_RESULTS:
            pending += 1
            continue
        item = tasks.setdefault(task, {"n": 0, **{key: 0 for key in TASK_RESULTS}, "durations": []})
        item["n"] += 1
        item[result] += 1
        start, end = _clock_seconds(row.get("inicio", "")), _clock_seconds(row.get("fim", ""))
        if start is not None and end is not None and end >= start:
            item["durations"].append(end - start)
    summary = {}
    for task, item in sorted(tasks.items()):
        durations = sorted(item.pop("durations"))
        summary[task] = {**item, "taxa_independente": item["independente"] / item["n"],
                         "mediana_segundos": median(durations) if durations else None, "n_tempos": len(durations)}
    participants = {row.get("participante_id") for row in rows
                    if (row.get("resultado") or "").strip() in TASK_RESULTS and row.get("participante_id")}
    return {"participantes": len(participants), "linhas_sem_resultado": pending, "tarefas": summary}


def main():
    parser = argparse.ArgumentParser(description="Calcula as métricas de avaliação do protótipo.")
    parser.add_argument("--intents", help="CSV com true_intent e predicted_intent")
    parser.add_argument("--fcr", help="CSV de cenários FCR preenchido por avaliadores")
    parser.add_argument("--latency", help="CSV com response_ms")
    parser.add_argument("--operations", help="CSV exportado com outcomes, canais e reservas concluídas")
    parser.add_argument("--likert", help="CSV anônimo com rating de 1 a 5")
    parser.add_argument("--tasks", help="Ficha de observação preenchida (eval/participantes/ficha_observacao.csv)")
    args = parser.parse_args()
    if args.tasks: print("Tarefas:", task_metrics(read_csv(args.tasks)))
    if args.intents: print("Intenções:", intent_metrics(read_csv(args.intents)))
    if args.fcr: print("FCR:", fcr_metrics(read_csv(args.fcr)))
    if args.latency:
        rows = read_csv(args.latency)
        print("Latência por resposta:", response_metrics(rows))
        print("Duração de sessão:", session_duration_metrics(rows))
    if args.likert: print("Likert:", likert_metrics(read_csv(args.likert)))
    if args.operations: print("Automação:", automation_metrics(read_csv(args.operations)))


if __name__ == "__main__":
    main()
