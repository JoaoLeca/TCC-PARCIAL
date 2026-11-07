"""Exporta latências e intenções registradas pela API para CSV."""
import argparse
import csv
from pathlib import Path
from barber_bot.db import connect

parser = argparse.ArgumentParser()
parser.add_argument("--out", default="eval/latencias.csv")
args = parser.parse_args()
with connect() as conn:
    rows = conn.execute("SELECT session_id,created_at,intent,latency_ms FROM interaction_logs ORDER BY id").fetchall()
output = Path(args.out)
output.parent.mkdir(parents=True, exist_ok=True)
with output.open("w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)
    writer.writerow(["session_id", "created_at", "intent", "response_ms"])
    writer.writerows([[r["session_id"], r["created_at"], r["intent"], r["latency_ms"]] for r in rows])
print(f"Exportadas {len(rows)} interações para {output}")
