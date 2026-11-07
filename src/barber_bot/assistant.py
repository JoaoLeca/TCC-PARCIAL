from datetime import datetime
from zoneinfo import ZoneInfo
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from barber_bot.config import OPENAI_API_KEY, OPENAI_MODEL, TIMEZONE
from barber_bot.db import initialize_database
from barber_bot.knowledge import search_knowledge
from barber_bot.schedule import availability, list_services, list_barbers, find_appointments, create_appointment, cancel_appointment, reschedule_appointment
from datetime import date
from barber_bot.intent import classify_intent


def build_agent():
    if not OPENAI_API_KEY:
        raise RuntimeError("O modo GPT precisa da chave OPENAI_API_KEY. A consulta de serviços e a agenda continuam disponíveis sem ela.")
    initialize_database()

    @tool
    def consultar_servicos() -> list[dict]:
        """Lista serviços, preços e duração."""
        return list_services()

    @tool
    def consultar_barbeiros() -> list[dict]:
        """Lista barbeiros e dias de atendimento; segunda=0 e domingo=6."""
        return list_barbers()

    @tool
    def buscar_disponibilidade(servico_id: int, data: str, barbeiro_id: int | None = None) -> list[dict]:
        """Busca horários reais para um serviço em uma data AAAA-MM-DD, opcionalmente filtrando barbeiro."""
        return availability(servico_id, date.fromisoformat(data), barbeiro_id)

    @tool
    def consultar_agendamentos(telefone: str) -> list[dict]:
        """Consulta agendamentos usando o telefone informado pelo cliente."""
        return find_appointments(telefone)

    @tool
    def consultar_politicas(pergunta: str) -> list[dict]:
        """Pesquisa políticas e informações da barbearia na base de conhecimento; use antes de responder dúvidas informativas."""
        return search_knowledge(pergunta)

    @tool
    def solicitar_atendimento_humano(motivo: str, nome: str = "", telefone: str = "") -> str:
        """Abre solicitação de atendimento humano para reclamações, baixa confiança ou pedidos fora do escopo."""
        from barber_bot.db import connect
        with connect() as conn:
            cur = conn.execute("INSERT INTO support_tickets(customer_name,customer_phone,reason) VALUES (?,?,?)", (nome, telefone, motivo))
        return f"Solicitação #{cur.lastrowid} registrada. A equipe entrará em contato durante o expediente."

    @tool
    def gravar_agendamento(nome: str, telefone: str, servico_id: int, barbeiro_id: int, inicio_iso: str, confirmado_pelo_cliente: bool) -> dict:
        """Grava um agendamento somente após o cliente confirmar nome, serviço, barbeiro, data e horário."""
        return create_appointment(nome, telefone, servico_id, barbeiro_id, inicio_iso, confirmado_pelo_cliente)

    @tool
    def cancelar_agendamento(agendamento_id: int, telefone: str, confirmado_pelo_cliente: bool) -> dict:
        """Cancela agendamento localizado pelo identificador e telefone apenas depois de confirmar explicitamente com o cliente; exige 3 horas de antecedência."""
        return cancel_appointment(agendamento_id, telefone, confirmado_pelo_cliente)

    @tool
    def remarcar_agendamento(agendamento_id: int, telefone: str, novo_inicio_iso: str, confirmado_pelo_cliente: bool) -> dict:
        """Remarca agendamento após confirmar os dados e o novo horário com o cliente."""
        return reschedule_appointment(agendamento_id, telefone, novo_inicio_iso, confirmado_pelo_cliente)

    system_prompt = f"""Você é o assistente virtual da Barbearia Imperial, em Ribeirão Preto-SP. A data e hora atuais são informadas em cada conversa no fuso {TIMEZONE}. Responda em português brasileiro, com clareza e cordialidade. Use ferramentas para preços, agenda e políticas. Nunca invente disponibilidade ou regras. Para políticas, use apenas trechos recuperados e cite o nome do arquivo-fonte; se nenhum trecho relevante aparecer, diga que não encontrou a informação e encaminhe à equipe. Antes de gravar ou remarcar, apresente nome, serviço, barbeiro, data e hora e aguarde confirmação explícita. Só então use a ferramenta com confirmado_pelo_cliente=true. Para cancelar, confirme o agendamento e telefone. Se não souber, houver reclamação, erro ou pedido fora do escopo, encaminhe para humano. Não solicite dados além do necessário. Horário: todos os dias 08:00–12:00 e 13:30–20:00; barbeiros têm escalas individuais."""
    return create_agent(ChatOpenAI(model=OPENAI_MODEL, temperature=0, api_key=OPENAI_API_KEY), tools=[consultar_servicos, consultar_barbeiros, buscar_disponibilidade, consultar_agendamentos, consultar_politicas, gravar_agendamento, cancelar_agendamento, remarcar_agendamento, solicitar_atendimento_humano], system_prompt=system_prompt)


def run_agent(messages: list[dict]) -> dict:
    classification = classify_intent(next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), ""))
    if classification.confidence < 0.55 or classification.intent == "atendimento_humano":
        from barber_bot.db import connect
        last_message = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        with connect() as conn:
            ticket = conn.execute("INSERT INTO support_tickets(reason) VALUES (?)", (last_message or "Solicitação com baixa confiança de intenção",))
        return {"content": f"Encaminhei sua solicitação para a equipe (protocolo {ticket.lastrowid}). Pode me informar seu nome e telefone para a equipe retornar?", "intent": classification.intent, "confidence": classification.confidence, "tools_called": ["solicitar_atendimento_humano"]}
    agent = build_agent()
    current = datetime.now(ZoneInfo(TIMEZONE)).strftime("%d/%m/%Y %H:%M")
    result = agent.invoke({"messages": [{"role": "system", "content": f"Data e hora atuais: {current}. A intenção classificada da última mensagem é {classification.intent} (confiança {classification.confidence:.2f})."}, *messages]})
    last = result["messages"][-1]
    tools_called = [call.get("name", "") for message in result["messages"] for call in getattr(message, "tool_calls", [])]
    return {"content": last.content if isinstance(last.content, str) else str(last.content), "intent": classification.intent, "confidence": classification.confidence, "tools_called": tools_called, "messages": result["messages"]}
