from datetime import datetime
from zoneinfo import ZoneInfo
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from barber_bot.config import OPENAI_API_KEY, OPENAI_MODEL, TIMEZONE
from barber_bot.db import initialize_database
from barber_bot.knowledge import search_knowledge
from barber_bot.operations import shop_profile
from barber_bot.schedule import availability, list_services, list_barbers
from datetime import date
from barber_bot.intent import classify_intent
from barber_bot.availability_conversation import availability_answer, basic_answer
from barber_bot.conversation import normalize_text, sanitize_chat_messages
import re
from uuid import uuid4


def build_agent(channel: str = "web", session_id: str | None = None):
    if not OPENAI_API_KEY:
        raise RuntimeError("O modo GPT precisa da chave OPENAI_API_KEY. A consulta de serviços e a agenda continuam disponíveis sem ela.")
    initialize_database()
    session_id = session_id or f"adhoc:{uuid4()}"

    @tool
    def consultar_servicos() -> list[dict]:
        """Lista serviços, preços e duração."""
        return list_services()

    @tool
    def consultar_barbeiros() -> list[dict]:
        """Lista barbeiros e dias de atendimento; segunda=0 e domingo=6."""
        return list_barbers()

    @tool
    def consultar_barbearia() -> dict:
        """Consulta o nome, endereço, telefone, pagamentos e expediente atuais da barbearia."""
        return shop_profile()

    @tool
    def buscar_disponibilidade(servico_id: int, data: str, barbeiro_id: int | None = None) -> list[dict]:
        """Busca horários reais para um serviço em uma data AAAA-MM-DD, opcionalmente filtrando barbeiro."""
        return availability(servico_id, date.fromisoformat(data), barbeiro_id)

    @tool
    def consultar_politicas(pergunta: str) -> list[dict]:
        """Pesquisa políticas e informações da barbearia na base de conhecimento; use antes de responder dúvidas informativas."""
        return search_knowledge(pergunta)

    @tool
    def solicitar_atendimento_humano(motivo: str) -> str:
        """Orienta o cliente a confirmar atendimento humano pela interface ou pelo comando /atendente."""
        return "O encaminhamento depende da confirmação do cliente. Oriente a usar Falar com a equipe no site ou /atendente no Telegram."

    booking_instructions = (
        "Para iniciar uma reserva neste canal, oriente a pessoa a enviar /agendar. "
        "Para consultar seus horários, remarcar ou cancelar, oriente a usar o site com telefone e código privado; nunca peça o código nesta conversa."
        if channel == "telegram" else
        "Para fazer uma reserva, oriente a usar a aba Agendar. Para consultar, remarcar ou cancelar, oriente a usar a aba Meus horários com o telefone e o código privado do comprovante. Oriente a nunca enviar o código no chat."
    )
    system_prompt = f"""Você é o assistente virtual da barbearia. Use consultar_barbearia para obter o nome atual da barbearia. A data e hora atuais são informadas em cada conversa no fuso {TIMEZONE}. Responda em português brasileiro, com clareza e cordialidade. Use ferramentas para preços, agenda e políticas. Nunca invente disponibilidade ou regras. Para políticas, use apenas trechos recuperados e cite o nome do arquivo-fonte; se nenhum trecho relevante aparecer, diga que não encontrou a informação e encaminhe à equipe. {booking_instructions} Se não souber, houver reclamação, erro ou pedido fora do escopo, encaminhe para humano. Não solicite dados além do necessário. Para endereço, contato, pagamento e expediente, use consultar_barbearia. Para dias de atendimento individuais, use consultar_barbeiros. Essas ferramentas têm os dados operacionais atuais."""
    return create_agent(ChatOpenAI(model=OPENAI_MODEL, temperature=0, api_key=OPENAI_API_KEY), tools=[consultar_servicos, consultar_barbeiros, consultar_barbearia, buscar_disponibilidade, consultar_politicas, solicitar_atendimento_humano], system_prompt=system_prompt)


def _handoff(messages, session_id, channel):
    next_step = "Envie /atendente para confirmar." if channel == "telegram" else "Clique em Falar com a equipe e confirme o formulário para solicitar atendimento."
    return {"content": f"Posso orientar você a falar com a equipe. {next_step}",
            "intent": "atendimento_humano", "confidence": 1.0, "tools_called": [],
            "support_offer": True, "outcome": "offered", "mode": "guided"}


def run_agent(messages: list[dict], *, channel: str = "web", session_id: str | None = None) -> dict:
    session_id = session_id or f"adhoc:{uuid4()}"
    messages = sanitize_chat_messages(messages)[-16:]
    latest = normalize_text(next((message["content"] for message in reversed(messages) if message["role"] == "user"), ""))
    if re.search(r"humano|atendente|falar com (?:uma? )?pessoa|reclamacao", latest):
        return _handoff(messages, session_id, channel)
    if guided := availability_answer(messages):
        suffix = " Envie /agendar para escolher e confirmar." if channel == "telegram" else " Use Agendar para escolher e confirmar."
        guided["content"] += suffix if guided["slots"] else ""
        return guided
    fallback = basic_answer(messages)
    if not OPENAI_API_KEY:
        return {"content": fallback or "O atendimento com IA está indisponível. Posso consultar serviços, preços e vagas. Use Agendar para reservar ou peça atendimento da equipe.",
                "intent": "servicos_precos" if fallback else "fora_escopo", "confidence": 1.0 if fallback else 0.0,
                "tools_called": ["consulta_guiada"] if fallback else [], "outcome": "answered" if fallback else "unavailable", "mode": "guided"}
    try:
        context = "\n".join(f"{message['role']}: {message['content']}" for message in messages[-8:])
        classification = classify_intent(context)
        if classification.confidence < 0.55 or classification.intent == "atendimento_humano":
            return _handoff(messages, session_id, channel)
        agent = build_agent(channel, session_id)
        current = datetime.now(ZoneInfo(TIMEZONE)).strftime("%d/%m/%Y %H:%M")
        result = agent.invoke({"messages": [{"role": "system", "content": f"Data e hora atuais: {current}. A intenção classificada da última mensagem é {classification.intent}. Considere o contexto, pergunte apenas o que faltar e ofereça até três opções reais. Para falta de vagas, consulte alternativas de dia e profissional e explique o que mudou."}, *messages]})
        last = result["messages"][-1]
        tools_called = [call.get("name", "") for message in result["messages"] for call in getattr(message, "tool_calls", [])]
        if "solicitar_atendimento_humano" in tools_called:
            return _handoff(messages, session_id, channel)
        return {"content": last.content if isinstance(last.content, str) else str(last.content), "intent": classification.intent,
                "confidence": classification.confidence, "tools_called": tools_called, "outcome": "answered", "mode": "ai"}
    except Exception:
        return {"content": fallback or "Não consegui consultar a IA agora. Posso ajudar com serviços e vagas, ou encaminhar seu pedido à equipe.",
                "intent": "fora_escopo", "confidence": 0.0, "tools_called": [], "outcome": "unavailable", "mode": "fallback"}
