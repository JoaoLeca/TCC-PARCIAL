from datetime import date
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr
from time import perf_counter
from typing import Literal
from barber_bot.db import initialize_database, connect
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Barbearia Imperial API", version="0.1.0", lifespan=lifespan)
app.include_router(staff_router)


class BookingIn(BaseModel):
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
                                 request_key=f"web:{payload.request_key}" if payload.request_key else None)
    record_booking(payload.session_id or f"booking:{booking['id']}", booking["id"])
    return booking


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
        result = run_agent(messages, session_id=payload.session_id)
        elapsed = round((perf_counter() - started) * 1000)
        record_interaction(payload.session_id, messages, result, elapsed)
        options = None
        if result.get("mode") == "guided" and result.get("service") and result.get("slots"):
            options = {"service": result["service"], "slots": result["slots"][:3]}
        return {"answer": result["content"], "intent": result["intent"], "confidence": result["confidence"],
                "latency_ms": elapsed, "mode": result.get("mode", "ai"), "support_request": result.get("support_request"),
                "booking_options": options}
    except SupportError:
        raise
    except Exception:
        raise HTTPException(503, "Não foi possível acessar o atendimento agora. Tente novamente ou use Agendar.")

