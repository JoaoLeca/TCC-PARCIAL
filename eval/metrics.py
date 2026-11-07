"""Calcula métricas a partir de CSVs preenchidos após a avaliação."""
import argparse
import csv
from collections import Counter
from datetime import datetime
from pathlib import Path


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
    eligible = [r for r in rows if r.get("expected_task_achieved") in ("0", "1")]
    solved = [r for r in eligible if r["expected_task_achieved"] == "1" and r.get("escalated", "0") == "0" and r.get("repeated_question", "0") == "0"]
    return {"scenarios": len(eligible), "resolved_first_contact": len(solved), "fcr": len(solved) / len(eligible) if eligible else None}


def response_metrics(rows: list[dict]) -> dict:
    values = [float(r["response_ms"]) for r in rows if r.get("response_ms", "").strip()]
    return {"n": len(values), "mean_ms": sum(values) / len(values) if values else None}


def session_duration_metrics(rows: list[dict]) -> dict:
    sessions: dict[str, list[datetime]] = {}
    for row in rows:
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


def main():
    parser = argparse.ArgumentParser(description="Calcula as métricas de avaliação do protótipo.")
    parser.add_argument("--intents", help="CSV com true_intent e predicted_intent")
    parser.add_argument("--fcr", help="CSV de cenários FCR preenchido por avaliadores")
    parser.add_argument("--latency", help="CSV com response_ms")
    parser.add_argument("--likert", help="CSV anônimo com rating de 1 a 5")
    args = parser.parse_args()
    if args.intents: print("Intenções:", intent_metrics(read_csv(args.intents)))
    if args.fcr: print("FCR:", fcr_metrics(read_csv(args.fcr)))
    if args.latency:
        rows = read_csv(args.latency)
        print("Latência por resposta:", response_metrics(rows))
        print("Duração de sessão:", session_duration_metrics(rows))
    if args.likert: print("Likert:", likert_metrics(read_csv(args.likert)))


if __name__ == "__main__":
    main()
