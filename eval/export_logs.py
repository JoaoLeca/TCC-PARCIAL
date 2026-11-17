"""Exporta telemetria sem mensagens ou contatos, com sessões pseudonimizadas."""
import argparse
import csv
import hashlib
import sqlite3
from pathlib import Path

from barber_bot.db import connect


def export_logs(conn: sqlite3.Connection, output: str | Path) -> int:
    """Export the same safe fields from both legacy and channel-aware databases."""
    has_channel = any(row["name"] == "channel" for row in conn.execute("PRAGMA table_info(interaction_logs)"))
    channel_column = ",channel" if has_channel else ""
    rows = conn.execute(
        "SELECT session_id,created_at,user_message,intent,latency_ms,outcome,tools_called"
        + channel_column + " FROM interaction_logs ORDER BY id"
    ).fetchall()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    seen_bookings = set()
    count = 0
    with output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["session_id", "created_at", "intent", "response_ms", "channel", "outcome", "tools_called"])
        for row in rows:
            session_id = row["session_id"] or ""
            if row["outcome"] == "booking_confirmed":
                key = (session_id, row["user_message"])
                if key in seen_bookings:
                    continue
                seen_bookings.add(key)
            channel = "telegram" if session_id.startswith("telegram:") else "web"
            if has_channel and row["channel"]:
                channel = row["channel"]
            pseudonym = "session_" + hashlib.sha256(session_id.encode("utf-8")).hexdigest() if session_id else ""
            writer.writerow([pseudonym, row["created_at"], row["intent"], row["latency_ms"], channel, row["outcome"], row["tools_called"]])
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="eval/latencias.csv")
    parser.add_argument("--database", help="Banco local opcional; padrão DATABASE_PATH")
    args = parser.parse_args()
    with connect(args.database) as conn:
        count = export_logs(conn, args.out)
    print(f"Exportados {count} registros (interações e eventos de reserva) para {args.out}")


if __name__ == "__main__":
    main()
