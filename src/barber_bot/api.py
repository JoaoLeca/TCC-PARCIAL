from datetime import date
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr
from time import perf_counter
import re
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


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatIn(BaseModel):
    session_id: str
    messages: list[ChatMessage]


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
    return create_appointment(payload.customer_name, payload.customer_phone, payload.service_id, payload.barber_id,
                              payload.starts_at, payload.confirmed, expected_price=payload.expected_price,
                              expected_duration=payload.expected_duration)


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


@app.post("/chat")
def chat(payload: ChatIn):
    started = perf_counter()
    if not OPENAI_API_KEY:
        raise HTTPException(503, "O chat com GPT está desativado. Configure OPENAI_API_KEY no arquivo .env.")
    try:
        messages = [
            {"role": message.role, "content": re.sub(r"BI-[A-Za-z0-9_-]{24}", "[código privado]", message.content)}
            for message in payload.messages
        ]
        result = run_agent(messages)
        elapsed = round((perf_counter() - started) * 1000)
        user_message = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        with connect() as conn:
            conn.execute("INSERT INTO interaction_logs(session_id,user_message,intent,tools_called,outcome,latency_ms) VALUES (?,?,?,?,?,?)", (payload.session_id, user_message, result["intent"], ",".join(result["tools_called"]), "escalated" if result["intent"] == "atendimento_humano" else "answered", elapsed))
        return {"answer": result["content"], "intent": result["intent"], "confidence": result["confidence"], "latency_ms": elapsed}
    except Exception as exc:
        raise HTTPException(502, f"Falha ao consultar o assistente GPT: {exc}")

