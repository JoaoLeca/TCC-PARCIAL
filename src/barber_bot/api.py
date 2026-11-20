from datetime import date
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr
from time import perf_counter
from typing import Literal
from barber_bot.db import initialize_database, connect
from barber_bot.barber_telegram import notify_barber
from barber_bot.schedule import (
    BookingAccessError, BookingError, availability, cancel_appointment, create_appointment,
    get_customer_appointment, list_barbers, list_services, reschedule_appointment, reschedule_options,
)
from barber_bot.assistant import run_agent
from barber_bot.config import OPENAI_API_KEY
from barber_bot.operations import shop_profile
from barber_bot.staff_api import router as staff_router
from barber_bot.conversation import sanitize_chat_messages
from barber_bot.support import SupportError, create_support_request, get_customer_request, update_support_contact
from barber_bot.telemetry import record_booking, record_handoff, record_interaction
from barber_bot.customer_access import list_owned_bookings, require_owned_booking, public_appointment
from barber_bot.booking_conversation import handle_booking_message, has_pending_request
from barber_bot.customer_recovery import RecoveryError, start_recovery, verify_recovery, begin_chat_recovery, handle_chat_recovery

@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="API da Barbearia Imperial",
    description="Serviços de agendamento, atendimento e gestão da barbearia.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(staff_router)


class BookingIn(BaseModel):
    customer_token: SecretStr | None = Field(default=None, min_length=32, max_length=128)
    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str = Field(min_length=8, max_length=30)
    service_id: int
    barber_id: int
    starts_at: str
    confirmed: bool = False
    expected_price: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    expected_duration: int | None = Field(default=None, gt=0)
    request_key: str | None = Field(default=None, min_length=16, max_length=128)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatIn(BaseModel):
    recovery_input: SecretStr | None = Field(default=None, max_length=4000)
    customer_token: SecretStr | None = Field(default=None, min_length=32, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)


@app.get("/health")
def health():
    return {"status": "ok", "gpt_configured": bool(OPENAI_API_KEY)}


@app.get("/services")
def services():
    return list_services()


@app.get("/barbers")
def barbers():
    return list_barbers()


@app.get("/shop")
def shop():
    return shop_profile()


@app.get("/catalog")
def public_catalog():
    return {"services": list_services(), "barbers": list_barbers(), "shop": shop_profile()}


@app.get("/availability")
def get_availability(service_id: int, appointment_date: date, barber_id: int | None = None):
    try:
        return availability(service_id, appointment_date, barber_id)
    except BookingError as exc:
        raise HTTPException(400, str(exc))


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


@app.exception_handler(BookingError)
def booking_error(_request: Request, exc: BookingError):
    status = 404 if isinstance(exc, BookingAccessError) else 409
    return JSONResponse(status_code=status, content={"detail": str(exc)}, headers={"Cache-Control": "no-store"})


@app.post("/appointments", dependencies=[Depends(no_store)])
def book(payload: BookingIn):
    booking = create_appointment(payload.customer_name, payload.customer_phone, payload.service_id, payload.barber_id,
                                 payload.starts_at, payload.confirmed, expected_price=payload.expected_price,
                                 expected_duration=payload.expected_duration,
                                 request_key=f"web:{payload.request_key}" if payload.request_key else None,
                                 owner_channel="web" if payload.customer_token else None,
                                 owner_key=payload.customer_token.get_secret_value() if payload.customer_token else None)
    record_booking(payload.session_id or f"booking:{booking['id']}", booking["id"])
    notify_barber(booking)
    return public_appointment(booking)


class OwnedAccessIn(BaseModel):
    customer_token: SecretStr = Field(min_length=32, max_length=128)


class OwnedChangeIn(OwnedAccessIn):
    confirmed: bool = False
    expected_starts_at: str


class RecoveryIn(OwnedAccessIn):
    phone: str = Field(min_length=8, max_length=30)


class RecoveryVerifyIn(OwnedAccessIn):
    challenge_id: SecretStr = Field(min_length=32, max_length=64)
    code: SecretStr = Field(min_length=1, max_length=30)


@app.exception_handler(RecoveryError)
def recovery_error(_request: Request, exc: RecoveryError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)}, headers={"Cache-Control": "no-store"})


@app.post("/customer/recovery", dependencies=[Depends(no_store)])
def request_recovery(payload: RecoveryIn):
    return start_recovery(payload.phone, payload.customer_token.get_secret_value())


@app.post("/customer/recovery/verify", dependencies=[Depends(no_store)])
def confirm_recovery(payload: RecoveryVerifyIn):
    verify_recovery(payload.challenge_id.get_secret_value(), payload.code.get_secret_value(), payload.customer_token.get_secret_value())
    return owned_appointments(payload)


class OwnedOptionsIn(OwnedAccessIn):
    appointment_date: date


class OwnedRescheduleIn(OwnedChangeIn):
    new_starts_at: str


@app.post("/customer/appointments", dependencies=[Depends(no_store)])
def owned_appointments(payload: OwnedAccessIn):
    return [public_appointment(row) for row in list_owned_bookings("web", payload.customer_token.get_secret_value())]


@app.post("/customer/appointments/{appointment_id}", dependencies=[Depends(no_store)])
def owned_appointment(appointment_id: int, payload: OwnedAccessIn):
    return public_appointment(require_owned_booking(appointment_id, "web", payload.customer_token.get_secret_value()))


@app.post("/customer/appointments/{appointment_id}/reschedule-options", dependencies=[Depends(no_store)])
def owned_options(appointment_id: int, payload: OwnedOptionsIn):
    record = require_owned_booking(appointment_id, "web", payload.customer_token.get_secret_value())
    return reschedule_options(appointment_id, record["customer_phone"], payload.appointment_date)


def change_owned_appointment(appointment_id, payload, *, new_start=None):
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        record = require_owned_booking(appointment_id, "web", payload.customer_token.get_secret_value(), conn=conn)
        if record["starts_at"] != payload.expected_starts_at:
            raise BookingError("A reserva foi alterada. Consulte os dados atualizados antes de confirmar novamente.")
        if new_start is None:
            cancel_appointment(appointment_id, record["customer_phone"], payload.confirmed, conn=conn)
        else:
            reschedule_appointment(appointment_id, record["customer_phone"], new_start, payload.confirmed, conn=conn)
        return public_appointment(require_owned_booking(appointment_id, "web", payload.customer_token.get_secret_value(), conn=conn))


@app.post("/customer/appointments/{appointment_id}/cancel", dependencies=[Depends(no_store)])
def owned_cancel(appointment_id: int, payload: OwnedChangeIn):
    return change_owned_appointment(appointment_id, payload)


@app.post("/customer/appointments/{appointment_id}/reschedule", dependencies=[Depends(no_store)])
def owned_reschedule(appointment_id: int, payload: OwnedRescheduleIn):
    return change_owned_appointment(appointment_id, payload, new_start=payload.new_starts_at)


class CustomerAccessIn(BaseModel):
    phone: str = Field(min_length=8, max_length=30)
    access_code: SecretStr


@app.post("/appointments/lookup", dependencies=[Depends(no_store)])
def appointments(payload: CustomerAccessIn):
    return get_customer_appointment(payload.phone, payload.access_code.get_secret_value())


class CancelIn(CustomerAccessIn):
    confirmed: bool = False


@app.post("/appointments/{appointment_id}/cancel", dependencies=[Depends(no_store)])
def cancel(appointment_id: int, payload: CancelIn):
    return cancel_appointment(appointment_id, payload.phone, payload.confirmed,
                              access_code=payload.access_code.get_secret_value())


class RescheduleOptionsIn(CustomerAccessIn):
    appointment_date: date


@app.post("/appointments/{appointment_id}/reschedule-options", dependencies=[Depends(no_store)])
def get_reschedule_options(appointment_id: int, payload: RescheduleOptionsIn):
    return reschedule_options(appointment_id, payload.phone, payload.appointment_date,
                              access_code=payload.access_code.get_secret_value())


class RescheduleIn(CustomerAccessIn):
    new_starts_at: str
    confirmed: bool = False


@app.post("/appointments/{appointment_id}/reschedule", dependencies=[Depends(no_store)])
def reschedule(appointment_id: int, payload: RescheduleIn):
    return reschedule_appointment(appointment_id, payload.phone, payload.new_starts_at, payload.confirmed,
                                  access_code=payload.access_code.get_secret_value())


class SupportIn(BaseModel):
    session_id: str = Field(min_length=16, max_length=128)
    reason: str = Field(default="Cliente solicitou atendimento humano.", max_length=500)
    customer_name: str = Field(default="", max_length=120)
    customer_phone: str = Field(default="", max_length=30)


class SupportContactIn(SupportIn):
    ticket_id: int = Field(gt=0)
    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str = Field(min_length=10, max_length=30)


class SupportStatusIn(BaseModel):
    session_id: str = Field(min_length=16, max_length=128)
    ticket_id: int | None = Field(default=None, gt=0)


@app.middleware("http")
async def private_support_status(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/support/status":
        no_store(response)
    return response


@app.exception_handler(SupportError)
def support_error(_request: Request, exc: SupportError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)}, headers={"Cache-Control": "no-store"})


@app.post("/support/requests", dependencies=[Depends(no_store)])
def request_human(payload: SupportIn):
    if {"customer_name", "customer_phone"} & payload.model_fields_set:
        if not payload.customer_name.strip() or not payload.customer_phone.strip():
            raise SupportError("Informe seu nome e telefone com DDD para solicitar atendimento.", 422)
    request = create_support_request(payload.session_id, "web", payload.reason, payload.customer_name, payload.customer_phone)
    record_handoff(payload.session_id, request["id"])
    return {"id": request["id"], "status": request["status"]}


@app.post("/support/contact", dependencies=[Depends(no_store)])
def support_contact(payload: SupportContactIn):
    request = update_support_contact(payload.session_id, "web", payload.ticket_id, payload.customer_name, payload.customer_phone)
    return {"id": request["id"], "status": request["status"]}


@app.post("/support/status", dependencies=[Depends(no_store)])
def support_status(payload: SupportStatusIn):
    return {"request": get_customer_request(payload.session_id, "web", payload.ticket_id)}


@app.post("/chat", dependencies=[Depends(no_store)])
def chat(payload: ChatIn):
    started = perf_counter()
    messages = sanitize_chat_messages([message.model_dump() for message in payload.messages])
    try:
        owner_key = payload.customer_token.get_secret_value() if payload.customer_token else None
        latest = next((message.content for message in reversed(payload.messages) if message.role == "user"), "")
        value = payload.recovery_input.get_secret_value() if payload.recovery_input else latest
        result = handle_chat_recovery(value, owner_key)
        if result is None and has_pending_request("web", owner_key):
            # Respostas a uma remarcação ou cancelamento em andamento (número da opção, data, sim, não)
            # seguem direto para o fluxo determinístico, sem depender de o modelo chamar a ferramenta.
            pending_text = next((message["content"] for message in reversed(messages) if message["role"] == "user"), "")
            result = handle_booking_message([{"role": "user", "content": pending_text}], "web", owner_key)
        if result is None:
            def booking_handler(request_text):
                return handle_booking_message([{"role": "user", "content": request_text}], "web", owner_key)
            result = run_agent(messages, booking_handler=booking_handler)
            if result.get("needs_verification") and owner_key:
                result = begin_chat_recovery(owner_key, latest)
        elapsed = round((perf_counter() - started) * 1000)
        record_interaction(payload.session_id, messages, result, elapsed)
        options = result.get("booking_options")
        return {"answer": result["content"], "intent": result["intent"], "confidence": result["confidence"],
                "latency_ms": elapsed, "mode": result.get("mode", "ai"), "support_request": result.get("support_request"),
                "booking_options": options, "outcome": result.get("outcome"), "recovery_stage": result.get("recovery_stage")}
    except SupportError:
        raise
    except Exception:
        raise HTTPException(503, "Não foi possível acessar o atendimento agora. Tente novamente ou use Agendar.")

