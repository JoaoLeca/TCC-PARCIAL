"""Classifica as 150 frases de avaliação e grava previsões em novo CSV."""
import csv
from pathlib import Path
from barber_bot.intent import classify_intent

source = Path(__file__).with_name("sentencas_intencao.csv")
destination = Path(__file__).with_name("resultados_intencao.csv")
with source.open(newline="", encoding="utf-8") as file:
    rows = list(csv.DictReader(file))
for index, row in enumerate(rows, 1):
    result = classify_intent(row["text"])
    row["predicted_intent"] = result.intent
    if index % 10 == 0:
        print(f"Classificadas {index}/{len(rows)}")
with destination.open("w", newline="", encoding="utf-8") as file:
    writer = csv.DictWriter(file, fieldnames=["sample_id", "text", "true_intent", "predicted_intent"])
    writer.writeheader()
    writer.writerows(rows)
print(f"Resultados em {destination}")
