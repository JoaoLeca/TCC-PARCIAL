WEEKDAYS = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo")


def format_price(price):
    return "R$ " + f"{price:,.2f}".translate(str.maketrans(",.", ".,"))
