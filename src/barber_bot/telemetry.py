from barber_bot.conversation import sanitize_chat_messages
from barber_bot.db import connect


def record_interaction(session_id, messages, result, latency_ms, channel="web"):
    if result.get("support_request"):
        record_handoff(session_id, result["support_request"]["id"], channel, latency_ms)
        return
    messages = sanitize_chat_messages(messages)
    last_message = next((message["content"] for message in reversed(messages) if message["role"] == "user"), "")
    with connect() as conn:
        conn.execute("""INSERT INTO interaction_logs
            (session_id,user_message,intent,tools_called,outcome,latency_ms,channel)
            VALUES (?,?,?,?,?,?,?)""", (
                session_id, last_message[:2000], result.get("intent"), ",".join(result.get("tools_called", [])),
                result.get("outcome", "answered"), latency_ms, channel,
            ))


def record_handoff(session_id, ticket_id, channel="web", latency_ms=None):
    marker = f"Atendimento #{ticket_id}"
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""INSERT INTO interaction_logs
            (session_id,user_message,intent,tools_called,outcome,latency_ms,channel)
            SELECT ?,?,'atendimento_humano','solicitar_atendimento_humano','escalated',?,?
            WHERE NOT EXISTS (SELECT 1 FROM interaction_logs WHERE outcome='escalated'
                              AND user_message=? AND session_id=? AND channel=?)""",
                     (session_id, marker, latency_ms, channel, marker, session_id, channel))


def record_booking(session_id, appointment_id, channel="web"):
    marker = f"Reserva #{appointment_id}"
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""INSERT INTO interaction_logs
            (session_id,user_message,intent,tools_called,outcome,latency_ms,channel)
            SELECT ?,?,'agendar','create_appointment','booking_confirmed',NULL,?
            WHERE NOT EXISTS (SELECT 1 FROM interaction_logs WHERE outcome='booking_confirmed'
                              AND user_message=?)""", (session_id, marker, channel, marker))
