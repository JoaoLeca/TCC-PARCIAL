"""Dois baselines simples para comparação acadêmica."""
from barber_bot.config import OPENAI_API_KEY, OPENAI_MODEL

FAQ = {
    "preco": "Corte: R$ 50; Cabelo + Barba: R$ 80; Cabelo + Barba + Sobrancelha: R$ 100; Pintar o Cabelo: R$ 200.",
    "horario": "Todos os dias, das 08:00 às 12:00 e das 13:30 às 20:00.",
    "pagamento": "Aceitamos dinheiro, Pix e cartão.",
    "cancelamento": "O cancelamento pode ser feito até 3 horas antes do horário marcado.",
    "atraso": "A tolerância para atraso é de 15 minutos.",
    "pacote": "Cabelo 2x no mês custa R$ 90. Cabelo + Barba 2x no mês custa R$ 145.",
}


def keyword_faq(question: str) -> str:
    text = question.casefold()
    triggers = {
        "preco": ("preço", "preco", "custa", "valor", "quanto"),
        "horario": ("horário", "horario", "abre", "fecha", "funciona"),
        "pagamento": ("pix", "cartão", "cartao", "dinheiro", "pagamento"),
        "cancelamento": ("cancel", "desmarcar"),
        "atraso": ("atras", "tolerância", "tolerancia"),
        "pacote": ("pacote", "mensal"),
    }
    for key, words in triggers.items():
        if any(word in text for word in words):
            return FAQ[key]
    return "Não encontrei essa informação. Entre em contato com a barbearia."


def llm_without_rag(question: str) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("Configure OPENAI_API_KEY para rodar o baseline de LLM sem RAG.")
    from langchain_openai import ChatOpenAI
    answer = ChatOpenAI(model=OPENAI_MODEL, temperature=0, api_key=OPENAI_API_KEY).invoke(question)
    return str(answer.content)
