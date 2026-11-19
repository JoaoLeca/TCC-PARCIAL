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
from barber_bot.availability_conversation import availability_answer, basic_answer
from barber_bot.conversation import normalize_text, sanitize_chat_messages
import re
from uuid import uuid4


def build_agent(channel: str = "web", session_id: str | None = None, *, booking_handler=None, runtime: dict | None = None):
    if not OPENAI_API_KEY:
        raise RuntimeError("O modo GPT precisa da chave OPENAI_API_KEY. A consulta de serviços e a agenda continuam disponíveis sem ela.")
    initialize_database()
    session_id = session_id or f"adhoc:{uuid4()}"
    runtime = runtime if runtime is not None else {}

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
        slots = availability(servico_id, date.fromisoformat(data), barbeiro_id)
        service = next((item for item in list_services() if item["id"] == servico_id), None)
        if service and slots:
            existing = runtime.setdefault("availability_slots", {})
            for slot in slots:
                existing[(slot["starts_at"], slot["barber_id"])] = slot
            runtime["booking_options"] = {"service": service, "slots": list(existing.values())[:3]}
        return slots

    @tool
    def verificar_fluxo_de_reserva() -> str:
        """Verifica pedidos de consulta, cancelamento ou remarcação de reservas próprias e continua confirmações pendentes. Sempre chame no início de cada turno do site. O servidor valida a propriedade e exige confirmação antes de alterar uma reserva."""
        if channel != "web":
            return "NOOP"
        latest = runtime.get("latest_user_message", "")
        result = booking_handler(latest) if booking_handler else None
        runtime["booking_result"] = result
        return result["content"] if result else "NOOP"

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
        "Para consultar seus horários, remarcar ou cancelar, a conversa vinculada à reserva verifica o acesso e pede confirmação antes de alterar. Em outro canal, oriente a recuperar o acesso automaticamente pelo próprio contato confirmado do Telegram ou pela verificação em Meus horários. O protocolo da reserva não comprova a identidade do cliente."
        if channel == "telegram" else
        "Para fazer uma reserva, oriente a usar a aba Agendar. Para consultar, remarcar ou cancelar, use Meus horários na sessão que criou a reserva ou faça o pedido por escrito nesta conversa. Em outra sessão, oriente a confirmar o telefone pelo Telegram em Meus horários ou no próprio chat para recuperar o acesso automaticamente."
    )
    system_prompt = f"""Você é o assistente virtual da barbearia. Toda resposta de atendimento deve ser produzida por você, usando o contexto da conversa e as ferramentas quando necessário. No início de cada turno no site, chame verificar_fluxo_de_reserva com a chamada sem argumentos: se a resposta for diferente de NOOP, repita-a fielmente e não invente, confirme nem altere dados. O servidor controla a propriedade da reserva e só conclui mudanças depois da confirmação explícita do cliente. Use consultar_barbearia para obter dados atuais. A data e hora atuais são informadas em cada conversa no fuso {TIMEZONE}. Responda em português brasileiro, com clareza e cordialidade; entenda perguntas indiretas e mensagens curtas pelo contexto, fazendo uma pergunta objetiva se faltar informação. Use consultar_servicos para preços e duração, consultar_barbeiros para escala e buscar_disponibilidade para horários reais. Para agendamento novo, conduza a conversa, consulte vagas e apresente até três opções; a pessoa ainda revisa e confirma em Agendar. Nunca invente disponibilidade, preços ou regras. Para políticas, use apenas trechos recuperados e cite o nome do arquivo-fonte; se não houver informação relevante, diga que não encontrou. {booking_instructions} Se houver reclamação, erro ou pedido fora do escopo, ofereça falar com a equipe. Não solicite dados além do necessário nem envie dados pessoais à ferramenta de IA. Para endereço, contato, pagamento e expediente, use consultar_barbearia. Essas ferramentas têm os dados operacionais atuais."""
    tools = [consultar_servicos, consultar_barbeiros, consultar_barbearia, buscar_disponibilidade, consultar_politicas, solicitar_atendimento_humano]
    if channel == "web":
        tools.append(verificar_fluxo_de_reserva)
    return create_agent(ChatOpenAI(model=OPENAI_MODEL, api_key=OPENAI_API_KEY), tools=tools, system_prompt=system_prompt)


def _handoff(messages, session_id, channel):
    next_step = "Envie /atendente para confirmar." if channel == "telegram" else "Clique em Falar com a equipe e confirme o formulário para solicitar atendimento."
    return {"content": f"Posso orientar você a falar com a equipe. {next_step}",
            "intent": "atendimento_humano", "confidence": 1.0, "tools_called": [],
            "support_offer": True, "outcome": "offered", "mode": "guided"}


def run_agent(messages: list[dict], *, channel: str = "web", session_id: str | None = None, booking_handler=None) -> dict:
    session_id = session_id or f"adhoc:{uuid4()}"
    messages = sanitize_chat_messages(messages)[-12:]
    if not OPENAI_API_KEY:
        if channel != "web":
            latest = normalize_text(next((message["content"] for message in reversed(messages) if message["role"] == "user"), ""))
            if re.search(r"humano|atendente|falar com (?:uma? )?pessoa|reclamacao", latest):
                return _handoff(messages, session_id, channel)
            if guided := availability_answer(messages):
                guided["content"] += " Envie /agendar para escolher e confirmar." if guided["slots"] else ""
                return guided
            fallback = basic_answer(messages)
            if fallback:
                return {"content": fallback, "intent": "servicos_precos", "confidence": 1.0,
                        "tools_called": ["consulta_guiada"], "outcome": "answered", "mode": "guided"}
        return {"content": "O atendimento virtual não está habilitado.",
                "intent": "indisponivel", "confidence": 0.0, "tools_called": [], "outcome": "unavailable", "mode": "unavailable"}
    try:
        runtime = {"latest_user_message": next((message["content"] for message in reversed(messages) if message["role"] == "user"), "")}
        agent = build_agent(channel, session_id, booking_handler=booking_handler, runtime=runtime)
        current = datetime.now(ZoneInfo(TIMEZONE)).strftime("%d/%m/%Y %H:%M")
        result = agent.invoke({"messages": [{"role": "system", "content": f"Data e hora atuais: {current}. Considere o histórico recente. Se a pergunta sobre horários não especificar serviço ou data, pergunte o que falta; quando uma data for solicitada, consulte a agenda e proponha alternativas disponíveis se necessário."}, *messages]})
        last = result["messages"][-1]
        tools_called = [call.get("name", "") for message in result["messages"] for call in getattr(message, "tool_calls", [])]
        booking_result = runtime.get("booking_result")
        if booking_result is not None:
            response = {**booking_result,
                        "content": last.content if isinstance(last.content, str) else str(last.content),
                        "mode": "ai"}
        else:
            response = {"content": last.content if isinstance(last.content, str) else str(last.content),
                        "intent": "atendimento" if "solicitar_atendimento_humano" in tools_called else "conversa",
                        "confidence": 1.0, "tools_called": tools_called, "outcome": "answered", "mode": "ai"}
        if runtime.get("booking_options"):
            response["booking_options"] = runtime["booking_options"]
        response["tools_called"] = list(dict.fromkeys(response.get("tools_called", []) + tools_called))
        return response
    except Exception:
        return {"content": "Não consegui acessar o atendimento por IA agora. Tente novamente em instantes ou fale com a equipe.",
                "intent": "indisponivel", "confidence": 0.0, "tools_called": [], "outcome": "unavailable", "mode": "unavailable"}
