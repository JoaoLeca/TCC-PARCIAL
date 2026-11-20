from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from barber_bot.availability_conversation import _preference_slots, _three_options, _time_preference
from barber_bot.config import MAX_BOOKING_DAYS, OPENAI_API_KEY, OPENAI_MODEL, TIMEZONE
from barber_bot.db import initialize_database
from barber_bot.knowledge import search_knowledge
from barber_bot.operations import shop_profile
from barber_bot.schedule import availability, list_services, list_barbers
from barber_bot.conversation import normalize_text, sanitize_chat_messages


def availability_suggestions(service_id: int, day: date, barber_id: int | None = None,
                             preference_text: str | None = None) -> dict:
    """Aplica a preferência de horário do cliente e escolhe as sugestões exibidas como botões.

    O modelo só repassa as palavras do cliente; a interpretação é a mesma das remarcações
    por mensagem, para que o texto e os botões partam da mesma lista de vagas reais.
    """
    preference = _time_preference(normalize_text(preference_text)) if preference_text and preference_text.strip() else None
    if preference and preference["invalid"]:
        return {"vagas": [], "sugestoes": [], "alternativa": None,
                "observacao": "A preferência de horário é ambígua ou inválida. Peça um esclarecimento ao cliente e não ofereça vagas."}
    latest = bool(preference and preference["latest"])

    def matching(candidate_day, candidate_barber):
        slots = _preference_slots(availability(service_id, candidate_day, candidate_barber), preference)
        return sorted(slots, key=lambda slot: (slot["starts_at"], slot["barber"]), reverse=latest)

    slots, alternative = matching(day, barber_id), None
    if not slots and barber_id is not None:
        slots = matching(day, None)
        alternative = "outro profissional na mesma data" if slots else None
    last_day = datetime.now(ZoneInfo(TIMEZONE)).date() + timedelta(days=MAX_BOOKING_DAYS)
    candidate = day + timedelta(days=1)
    while not slots and candidate <= last_day:
        slots = matching(candidate, barber_id) or (matching(candidate, None) if barber_id is not None else [])
        alternative = "outra data" if slots else None
        candidate += timedelta(days=1)
    suggestions = _three_options(slots, latest=latest)
    observation = None if suggestions else "Não há vagas compatíveis na janela de agendamento. Informe a indisponibilidade sem sugerir horários."
    return {"vagas": slots, "sugestoes": suggestions, "alternativa": alternative, "observacao": observation}


def build_agent(channel: str = "web", *, booking_handler=None, runtime: dict | None = None):
    if not OPENAI_API_KEY:
        raise RuntimeError("O modo GPT precisa da chave OPENAI_API_KEY. A consulta de serviços e a agenda continuam disponíveis sem ela.")
    initialize_database()
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
    def buscar_disponibilidade(servico_id: int, data: str, barbeiro_id: int | None = None,
                               preferencia_horario: str | None = None) -> dict:
        """Busca horários reais para um serviço em uma data AAAA-MM-DD, opcionalmente filtrando barbeiro.
        Em preferencia_horario, repita a restrição de horário do cliente com as palavras dele, por exemplo
        "depois das 15h", "à tarde", "entre 10h e 12h", "às 15h" ou "último horário"; o servidor aplica a
        restrição. Sem vaga na combinação pedida, a resposta traz alternativas de outro profissional ou de outra
        data. Ofereça somente os horários de "sugestoes", na mesma ordem."""
        result = availability_suggestions(servico_id, date.fromisoformat(data), barbeiro_id, preferencia_horario)
        service = next((item for item in list_services() if item["id"] == servico_id), None)
        if service and result["sugestoes"]:
            runtime["booking_options"] = {"service": service, "slots": result["sugestoes"]}
        else:
            runtime.pop("booking_options", None)
        return result

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
    system_prompt = f"""Você é o assistente virtual da barbearia. Produza as respostas em português brasileiro, com clareza e cordialidade. Use o histórico para entender respostas curtas como datas, números, horários e períodos do dia; se houver mais de uma interpretação possível, faça uma pergunta objetiva.

Use somente os dados retornados pelas ferramentas para informar serviços, preços, profissionais, expediente, contato, endereço, formas de pagamento, políticas e disponibilidade. Consulte os dados atuais antes de responder: consultar_servicos para serviços e preços, consultar_barbeiros para profissionais e escala, consultar_barbearia para informações da barbearia, buscar_disponibilidade para vagas reais e consultar_politicas para regras. Nunca invente nem deduza vagas, preços, horários ou políticas. Se a consulta não retornar a informação necessária, diga que não conseguiu confirmar e ofereça ajuda da equipe.

A data e hora atuais são informadas em cada conversa no fuso {TIMEZONE}. Ao consultar horários, use o serviço, a data e as preferências informados no histórico. Para agendamento novo, conduza a conversa, consulte vagas e apresente até três opções realmente retornadas; a pessoa ainda revisa e confirma em Agendar. Na barbearia, "corte" ou "cortar o cabelo" sem outro complemento significa o serviço Corte de Cabelo. Quando o cliente mudar só o horário, o período ou o profissional, mantenha o serviço e a data já informados no histórico e consulte novamente, passando o barbeiro_id do profissional pedido. Ao chamar buscar_disponibilidade, informe em preferencia_horario a restrição de horário que o cliente indicou e apresente somente os horários de sugestoes, na mesma ordem; se vierem como alternativa, diga que são alternativas ao pedido. Se a observação pedir esclarecimento ou informar que não há vagas, responda isso sem sugerir horários.

No início de cada turno no site, chame verificar_fluxo_de_reserva sem argumentos. Se a resposta for diferente de NOOP, repita-a fielmente e não invente, confirme nem altere dados. O servidor controla a propriedade da reserva e só conclui mudanças depois da confirmação explícita do cliente. Para políticas, use apenas trechos recuperados e cite o nome do arquivo-fonte; se não houver informação relevante, diga que não encontrou. {booking_instructions} Se houver reclamação, erro ou pedido fora do escopo, ofereça falar com a equipe. Não solicite dados além do necessário nem envie dados pessoais à ferramenta de IA."""
    tools = [consultar_servicos, consultar_barbeiros, consultar_barbearia, buscar_disponibilidade, consultar_politicas, solicitar_atendimento_humano]
    if channel == "web":
        tools.append(verificar_fluxo_de_reserva)
    return create_agent(ChatOpenAI(model=OPENAI_MODEL, api_key=OPENAI_API_KEY), tools=tools, system_prompt=system_prompt)


def run_agent(messages: list[dict], *, channel: str = "web", booking_handler=None) -> dict:
    messages = sanitize_chat_messages(messages)[-12:]
    if not OPENAI_API_KEY:
        return {"content": "O atendimento virtual não está habilitado.",
                "intent": "indisponivel", "confidence": 0.0, "tools_called": [], "outcome": "unavailable", "mode": "unavailable"}
    try:
        runtime = {"latest_user_message": next((message["content"] for message in reversed(messages) if message["role"] == "user"), "")}
        agent = build_agent(channel, booking_handler=booking_handler, runtime=runtime)
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
