"""Interpreta e valida datas e horários usados em reservas e remarcações."""
from datetime import date, datetime, timedelta
import re

from barber_bot.conversation import normalize_text
from barber_bot.presentation import WEEKDAYS


def _requested_day(text, today):
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    local = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", text)
    try:
        if iso:
            return date.fromisoformat(iso[1])
        if local:
            return date(int(local[3] or today.year), int(local[2]), int(local[1]))
    except ValueError:
        return "invalid"
    day_only = re.search(r"\bdia\s+(\d{1,2})\b", text)
    if day_only:
        wanted = int(day_only[1])
        if not 1 <= wanted <= 31:
            return "invalid"
        for offset in range(13):
            month_index = today.month - 1 + offset
            year, month = today.year + month_index // 12, month_index % 12 + 1
            try:
                candidate = date(year, month, wanted)
            except ValueError:
                continue
            if candidate >= today:
                return candidate
        return "invalid"
    if "depois de amanha" in text:
        return today + timedelta(days=2)
    if "amanha" in text:
        return today + timedelta(days=1)
    if re.search(r"\bhoje\b", text):
        return today
    for weekday, name in enumerate(WEEKDAYS):
        if re.search(r"\b" + normalize_text(name) + r"(?:-feira)?\b", text):
            distance = (weekday - today.weekday()) % 7 or 7
            return today + timedelta(days=distance)
    return None


_CLOCK_VALUE = r"\d{1,3}(?:\s*h(?:\s*\d{1,3})?|:\s*\d{1,3})?(?![\w:])"
_CLOCK_MARKED = r"\b\d{1,3}(?:\s*h(?:\s*\d{1,3})?|:\s*\d{1,3})(?![\w:])"


def _clock_minutes(value):
    match = re.fullmatch(r"(\d{1,2})(?:\s*h(?:\s*(\d{1,2}))?|:\s*(\d{2}))?", value)
    if not match:
        return None
    hour, minute = int(match[1]), int(match[2] or match[3] or 0)
    return hour * 60 + minute if hour < 24 and minute < 60 else None


def _time_preference(text):
    """Interpreta horário sem confundir a data com a hora."""
    text = re.sub(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{4})?)\b", " ", text)
    text = re.sub(r"\bdepois de amanha\b", " ", text)
    periods = re.findall(r"\b(manha|tarde|noite)\b", text)
    orders = re.findall(r"\b(primeiro|ultimo)\s+horarios?\b", text)
    preference = {"period": periods[0] if periods else None,
                  "latest": bool(orders and orders[0] == "ultimo"), "constraint": None, "invalid": False}
    if len(set(periods)) > 1 or len(set(orders)) > 1:
        preference["invalid"] = True
    # Não transforme pedidos ambíguos em um horário inventado.
    if re.search(r"\b(por volta|mais ou menos|mais tarde|mais cedo|aproximadamente|umas|meio[ -]dia|meia[ -]noite)\b", text):
        preference["invalid"] = True
    patterns = [
        ("between", rf"\bentre\s*({_CLOCK_VALUE})\s+e\s*({_CLOCK_VALUE})"),
        ("after", rf"\b(?:depois|apos)\s+(?:das?|as?)\s*({_CLOCK_VALUE})"),
        ("from", rf"\ba partir\s+(?:das?|as?)\s*({_CLOCK_VALUE})"),
        ("before", rf"\bantes\s+(?:das?|as?)\s*({_CLOCK_VALUE})"),
        ("before", rf"\bate\s+(?:as?\s*)?({_CLOCK_VALUE})"),
        ("exact", rf"\bas\s*({_CLOCK_VALUE})"),
    ]
    remaining, constraints = text, []
    for mode, pattern in patterns:
        matches = list(re.finditer(pattern, remaining))
        for match in matches:
            values = [_clock_minutes(value) for value in match.groups()]
            if None in values or (mode == "between" and values[0] >= values[1]):
                preference["invalid"] = True
            else:
                constraints.append((mode, *values))
        remaining = re.sub(pattern, " ", remaining)
    for match in re.finditer(_CLOCK_MARKED, remaining):
        value = _clock_minutes(match[0])
        if value is None:
            preference["invalid"] = True
        else:
            constraints.append(("exact", value))
    remaining = re.sub(_CLOCK_MARKED, " ", remaining)
    if re.search(r"\b(?:antes|depois|apos|a partir|entre|ate)\b|\bas\s+(?:\d|\w+\s+horas?\b)|\b\d+\s*[:h]", remaining):
        preference["invalid"] = True
    if re.search(r"\bas\s+(?:uma?|duas?|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|catorze|quatorze|quinze|dezesseis|dezessete|dezoito|dezenove|vinte)\b", remaining):
        preference["invalid"] = True
    if constraints and re.search(r"[.,]\d|\be\s+(?:meia|um quarto|\d)|\bmenos\b", remaining):
        preference["invalid"] = True
    if len(constraints) > 1:
        preference["invalid"] = True
    if constraints:
        preference["constraint"] = constraints[0]
    # Se houver uma negação, peça esclarecimento sobre o horário.
    if (periods or orders or constraints or re.search(r"\bhorarios?\b", text)) and re.search(
        r"\b(?:nao|nem|exceto|menos|sem ser)\b", text
    ):
        preference["invalid"] = True
    # Não trate um horário negado como uma preferência válida.
    if re.search(r"(?<!\w)[-−]\s*\d", text):
        preference["invalid"] = True
    return preference if periods or orders or constraints or preference["invalid"] else None


def _preference_slots(slots, preference):
    if not preference:
        return slots
    period, constraint = preference["period"], preference["constraint"]
    result = []
    for slot in slots:
        start, end = datetime.fromisoformat(slot["starts_at"]), datetime.fromisoformat(slot["ends_at"])
        first, last = start.hour * 60 + start.minute, end.hour * 60 + end.minute
        if period:
            lower, upper = {"manha": (0, 720), "tarde": (720, 1080), "noite": (1080, 1440)}[period]
            if not lower <= first < upper:
                continue
        if constraint:
            mode, bound, *upper = constraint
            if not {"exact": first == bound, "after": first > bound, "from": first >= bound,
                    "before": last <= bound, "between": first >= bound and last <= (upper[0] if upper else 1440)}[mode]:
                continue
        result.append(slot)
    return result


def _three_options(slots, latest=False):
    choices = {}
    for slot in sorted(slots, key=lambda item: (item["starts_at"], item["barber"]), reverse=latest):
        choices.setdefault(slot["starts_at"], slot)
        if len(choices) == 3:
            break
    return list(choices.values())
