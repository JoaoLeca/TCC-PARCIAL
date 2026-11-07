from typing import Literal
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from barber_bot.config import OPENAI_API_KEY, OPENAI_MODEL

IntentName = Literal[
    "saudacao", "servicos_precos", "consultar_disponibilidade", "agendar",
    "remarcar", "cancelar", "consultar_agendamento", "politicas",
    "atendimento_humano", "fora_escopo",
]


class IntentResult(BaseModel):
    intent: IntentName
    confidence: float = Field(ge=0, le=1)


def classify_intent(text: str) -> IntentResult:
    if not OPENAI_API_KEY:
        raise RuntimeError("A classificação de intenção com GPT requer OPENAI_API_KEY.")
    model = ChatOpenAI(model=OPENAI_MODEL, temperature=0, api_key=OPENAI_API_KEY)
    classifier = model.with_structured_output(IntentResult)
    return classifier.invoke([
        ("system", "Classifique a intenção principal da mensagem do cliente de uma barbearia. Use atendimento_humano para reclamação ou pedido explícito por uma pessoa. Use fora_escopo se não for atendimento da barbearia. A confiança deve refletir a clareza da mensagem."),
        ("human", text),
    ])
