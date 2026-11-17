from datetime import date, timedelta

from dateutil.easter import easter


def ribeirao_preto_holidays(year: int) -> dict[date, str]:
    # Feriados locais conforme https://www.ribeiraopreto.sp.gov.br/portal/principal/feriados.
    fixed_dates = {
        (1, 1): "Confraternização Universal",
        (1, 20): "São Sebastião · Ribeirão Preto",
        (4, 21): "Tiradentes",
        (5, 1): "Dia do Trabalho",
        (6, 19): "Aniversário de Ribeirão Preto",
        (7, 9): "Revolução Constitucionalista · SP",
        (9, 7): "Independência do Brasil",
        (10, 12): "Nossa Senhora Aparecida",
        (11, 2): "Finados",
        (11, 15): "Proclamação da República",
        (12, 25): "Natal",
    }
    holidays = {date(year, month, day): name for (month, day), name in fixed_dates.items()}
    if year >= 2024:
        holidays[date(year, 11, 20)] = "Dia da Consciência Negra"
    easter_sunday = easter(year)
    holidays[easter_sunday - timedelta(days=2)] = "Sexta-feira Santa"
    holidays[easter_sunday + timedelta(days=60)] = "Corpus Christi"
    return holidays
