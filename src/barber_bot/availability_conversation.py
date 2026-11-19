"""Consultas guiadas de agenda com contexto e dados atuais, disponíveis sem IA."""
from datetime import date, datetime, timedelta
import re

from barber_bot.config import MIN_BOOKING_DAYS, MAX_BOOKING_DAYS
from barber_bot.conversation import normalize_text
from barber_bot.operations import shop_profile
from barber_bot.presentation import WEEKDAYS, format_price
from barber_bot.schedule import TZ, availability, list_barbers, list_services


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


def _requested_service(text, services):
    for service in sorted(services, key=lambda item: len(item["name"]), reverse=True):
        if normalize_text(service["name"]) in text:
            return service
    terms = []
    if re.search(r"\b(pintar|pintura|tingir)\b", text):
        terms = ["pint"]
    elif "sobrancelha" in text:
        terms = ["sobrancelha"]
    elif "barba" in text:
        terms = ["barba"]
    elif re.search(r"\b(corte|cortar|cabelo)\b", text):
        terms = ["corte"]
    matches = [service for service in services if terms and all(term in normalize_text(service["name"]) for term in terms)]
    return min(matches, key=lambda item: len(item["name"])) if matches else None


_CLOCK_VALUE = r"\d{1,3}(?:\s*h(?:\s*\d{1,3})?|:\s*\d{1,3})?(?![\w:])"
_CLOCK_MARKED = r"\b\d{1,3}(?:\s*h(?:\s*\d{1,3})?|:\s*\d{1,3})(?![\w:])"


def _clock_minutes(value):
    match = re.fullmatch(r"(\d{1,2})(?:\s*h(?:\s*(\d{1,2}))?|:\s*(\d{2}))?", value)
    if not match:
        return None
    hour, minute = int(match[1]), int(match[2] or match[3] or 0)
    return hour * 60 + minute if hour < 24 and minute < 60 else None


def _time_preference(text):
    """Parse one utterance, without treating calendar dates as clock values.

    None means no new temporal preference; invalid preferences remain explicit
    so an older preference cannot silently replace a request we did not parse.
    """
    text = re.sub(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{4})?)\b", " ", text)
    text = re.sub(r"\bdepois de amanha\b", " ", text)
    periods = re.findall(r"\b(manha|tarde|noite)\b", text)
    orders = re.findall(r"\b(primeiro|ultimo)\s+horarios?\b", text)
    preference = {"period": periods[0] if periods else None,
                  "latest": bool(orders and orders[0] == "ultimo"), "constraint": None, "invalid": False}
    if len(set(periods)) > 1 or len(set(orders)) > 1:
        preference["invalid"] = True
    # Ambiguous qualifiers must not turn into an invented exact hour or range.
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
    # Negation changes the meaning of a temporal request. Ask rather than
    # treating its positive fragment as a preference or reviving an older one.
    if (periods or orders or constraints or re.search(r"\bhorarios?\b", text)) and re.search(
        r"\b(?:nao|nem|exceto|menos|sem ser)\b", text
    ):
        preference["invalid"] = True
    # Generic clock searches must never reinterpret a signed hour as positive.
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


_SCHEDULING_TERMS = re.compile(r"horario|disponib|vaga|agend|reserv|marcar|encaix")
_DAY_TERMS = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{4})?\b|\bdepois de amanha\b|\bamanha\b|\bhoje\b"
    r"|\b(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:-feira)?\b|\bfeira\b|\bdia\b|\d+"
)
# Palavras que acompanham uma resposta curta de data ("e na sexta?", "pode ser amanhã").
_FOLLOW_UP_WORDS = {
    "a", "as", "o", "os", "e", "ou", "mas", "no", "na", "nos", "nas", "em", "de", "do", "da", "para", "pra", "pro",
    "por", "favor", "pode", "ser", "entao", "sim", "ok", "certo", "beleza", "tambem", "que", "tal", "qual", "quais",
    "como", "fica", "seria", "talvez", "prefiro", "melhor", "quero", "queria", "gostaria", "mesmo", "isso", "esse",
    "essa", "um", "uma", "ai", "opa", "tem", "teria", "ha", "proxima", "proximo", "semana", "agora", "outro",
    "outra", "serve", "consegue", "consigo", "ja", "so", "se", "eu", "me",
}


def _continues_scheduling(text, services, barbers, today):
    """Decide se a mensagem atual continua a busca de horários iniciada antes.

    Serviço, profissional, horário, período ou termos de agenda continuam a busca.
    Uma data sozinha só continua quando vem em uma resposta curta; perguntas sobre
    outro assunto que apenas citam "amanhã" não devem virar agendamento.
    """
    if _SCHEDULING_TERMS.search(text) or _requested_service(text, services):
        return True
    if "sem preferencia" in text or "qualquer" in text:
        return True
    if any(normalize_text(barber["name"]) in text for barber in barbers):
        return True
    if _time_preference(text) is not None:
        return True
    if _requested_day(text, today) is None:
        return False
    residual = re.findall(r"[a-z]+", _DAY_TERMS.sub(" ", text))
    return all(word in _FOLLOW_UP_WORDS for word in residual)


def availability_answer(messages):
    user_texts = [normalize_text(message["content"]) for message in messages if message["role"] == "user"][-8:]
    if not user_texts:
        return None
    context = " ".join(user_texts)
    latest = user_texts[-1]
    if not re.search(r"horario|disponib|vaga|agend|reserv|marcar|quero.*(?:cort|barba|cabelo)|tem.*corte", context):
        return None
    # Uma nova dúvida informativa interrompe a coleta de dados do agendamento.
    if re.search(r"preco|quanto|endereco|pagamento|politica|cancel|remarc|atras|telefone|funciona|expediente|pacote"
                 r"|\babre\b|\bfecha\b", latest):
        return None
    services, barbers = list_services(), list_barbers()
    today = datetime.now(TZ).date()
    # O contexto anterior só é reaproveitado quando a mensagem atual continua o agendamento.
    if len(user_texts) > 1 and not _continues_scheduling(latest, services, barbers, today):
        return None
    service = barber_id = chosen_day = preference = None
    for text in reversed(user_texts):
        service = service or _requested_service(text, services)
        chosen_day = chosen_day or _requested_day(text, today)
        if barber_id is None:
            if "sem preferencia" in text or "qualquer" in text:
                barber_id = 0
            else:
                barber_id = next((barber["id"] for barber in barbers if normalize_text(barber["name"]) in text), None)
        if preference is None:
            preference = _time_preference(text)
    result = {"intent": "consultar_disponibilidade", "confidence": 1.0,
              "tools_called": ["buscar_disponibilidade"], "mode": "guided", "outcome": "answered", "slots": []}
    if service is None:
        return {**result, "content": "Qual serviço você quer agendar? " + ", ".join(item["name"] for item in services) + "."}
    if chosen_day is None:
        return {**result, "content": f"Para qual dia você quer {service['name']}? Pode informar a data ou dizer amanhã."}
    first_day, last_day = today + timedelta(days=MIN_BOOKING_DAYS), today + timedelta(days=MAX_BOOKING_DAYS)
    if chosen_day == "invalid" or not first_day <= chosen_day <= last_day:
        return {**result, "content": f"Escolha uma data válida entre {first_day:%d/%m} e {last_day:%d/%m}. Qual dia prefere?"}
    if preference and preference["invalid"]:
        return {**result, "content": "Não consegui interpretar esse horário com segurança. Informe, por exemplo, às 15h, depois das 15h, a partir das 15h, antes das 15h ou entre 10h e 12h."}
    slots = _preference_slots(availability(service["id"], chosen_day, barber_id or None), preference)
    alternative = not slots
    if not slots and barber_id:
        slots = _preference_slots(availability(service["id"], chosen_day), preference)
    for offset in range(1, (last_day - chosen_day).days + 1):
        if slots:
            break
        slots = _preference_slots(availability(service["id"], chosen_day + timedelta(days=offset), barber_id or None), preference)
    choices = _three_options(slots, latest=bool(preference and preference["latest"]))
    if not choices:
        return {**result, "content": "Não encontrei vagas para esse horário e essas escolhas no período aberto. Prefere outro horário, período do dia ou profissional? A equipe também pode ajudar."}
    lines = [f"- {datetime.fromisoformat(slot['starts_at']):%d/%m às %H:%M} com {slot['barber']}" for slot in choices]
    intro = "Não encontrei a combinação pedida. Estas são alternativas disponíveis" if alternative else "Encontrei estas opções disponíveis"
    return {**result, "slots": choices, "service_id": service["id"], "service": service, "content":
            f"{intro} para {service['name']} ({format_price(service['price'])}, {service['duration_minutes']} min).\n\n" +
            "\n".join(lines) + "\n\nA reserva só será concluída após você escolher e confirmar."}


def basic_answer(messages):
    text = normalize_text(next((message["content"] for message in reversed(messages) if message["role"] == "user"), ""))
    if re.search(r"remarc|cancel|minha reserva|meu agendamento|meus horarios|codigo privado", text):
        return "Use Meus horários ou escreva aqui que quer consultar, remarcar ou cancelar. Se a reserva foi feita em outro aparelho ou canal, confirme seu telefone pelo Telegram para recuperar o acesso automaticamente. Antes de qualquer alteração, será pedida sua confirmação."
    if re.search(r"servico|preco|quanto|valor|duracao", text):
        return "Serviços disponíveis\n\n" + "\n".join(f"- {service['name']} · {format_price(service['price'])} · {service['duration_minutes']} min" for service in list_services())
    shop = shop_profile()
    if re.search(r"endereco|onde|localiza", text):
        return f"{shop['name']} fica em {shop['address']}."
    if re.search(r"pagamento|pix|cartao", text):
        return f"Formas de pagamento cadastradas pela equipe: {shop['payment_methods']}."
    if re.search(r"funciona|expediente|abre|fecha", text):
        hours = f"{shop['open_1']} às {shop['close_1']}"
        if shop["open_2"]:
            hours += f" e {shop['open_2']} às {shop['close_2']}"
        return f"Atendemos de segunda a sábado, das {hours}. A disponibilidade varia conforme a escala e os bloqueios de cada profissional."
    if re.fullmatch(r"(?:oi|ola|bom dia|boa tarde|boa noite)[!?. ]*", text):
        return "Olá! Posso consultar serviços, preços e vagas. Para começar, diga o serviço e o dia que prefere. Você também pode pedir atendimento da equipe."
    return None
