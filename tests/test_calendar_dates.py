from datetime import date

import pytest

from barber_bot.calendar_dates import ribeirao_preto_holidays


@pytest.mark.parametrize("day, name", [
    (date(2026, 1, 20), "São Sebastião"),
    (date(2026, 4, 3), "Sexta-feira Santa"),
    (date(2026, 6, 4), "Corpus Christi"),
    (date(2026, 6, 19), "Aniversário de Ribeirão Preto"),
    (date(2026, 7, 9), "Revolução Constitucionalista"),
    (date(2026, 11, 20), "Consciência Negra"),
    (date(2027, 3, 26), "Sexta-feira Santa"),
    (date(2027, 5, 27), "Corpus Christi"),
])
def test_holiday_dates_include_local_and_movable_dates(day, name):
    assert name in ribeirao_preto_holidays(day.year)[day]


def test_optional_public_service_days_are_not_marked_as_holidays():
    holidays = ribeirao_preto_holidays(2026)
    assert date(2026, 2, 17) not in holidays
    assert date(2026, 6, 5) not in holidays
