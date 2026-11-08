from datetime import date, datetime, time, timedelta
from sqlite3 import IntegrityError
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from barber_bot.db import connect
from barber_bot.operations import create_block, list_blocks, remove_block, update_shop, update_working_days
from barber_bot.schedule import (
    TZ, BookingError, _parse_start, availability, cancel_appointment, create_appointment,
    replace_access_code, reschedule_appointment, reschedule_options,
)
from barber_bot.staff import StaffMember, authenticate, get_staff_member, revoke_session


router = APIRouter(prefix="/staff", tags=["Equipe"])
Bearer = Annotated[HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))]


def require_staff(credentials: Bearer, response: Response) -> StaffMember:
    member = get_staff_member(credentials.credentials) if credentials else None
    if not member:
        raise HTTPException(401, "Entre na área da equipe.", headers={"WWW-Authenticate": "Bearer"})
    response.headers["Cache-Control"] = "no-store"
    return member


Staff = Annotated[StaffMember, Depends(require_staff)]
protected = APIRouter(dependencies=[Depends(require_staff)])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=128)


class ConfirmationIn(BaseModel):
    confirmed: bool = False


class RescheduleStaffIn(ConfirmationIn):
    new_starts_at: str


class ServiceUpdate(BaseModel):
    price: float = Field(ge=0, le=1_000_000, allow_inf_nan=False)
    duration_minutes: int = Field(ge=5, le=390)


class ServiceCreate(ServiceUpdate):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=90)


class CounterBookingIn(ConfirmationIn):
    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str = Field(min_length=8, max_length=30)
    service_id: int
    barber_id: int
    starts_at: str = Field(max_length=50)
    expected_price: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    expected_duration: int | None = Field(default=None, gt=0)


class BlockIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    barber_id: int
    starts_at: str = Field(max_length=50)
    ends_at: str = Field(max_length=50)
    reason: str = Field(min_length=1, max_length=160)


class ShopUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=250)
    phone: str = Field(min_length=8, max_length=30)
    payment_methods: str = Field(min_length=1, max_length=160)
    open_1: time
    close_1: time
    open_2: time | None = None
    close_2: time | None = None


class WorkingDaysUpdate(BaseModel):
    working_days: list[Annotated[int, Field(ge=0, le=6)]] = Field(min_length=1, max_length=7)


def _scope(member, barber_id):
    if member.role == "barber":
        if barber_id not in (None, member.barber_id):
            raise HTTPException(403, "Acesso restrito à sua agenda.")
        return member.barber_id
    return barber_id


def require_owner(member: Staff):
    if member.role != "owner":
        raise HTTPException(403, "Somente o dono pode alterar as configurações.")


Owner = Annotated[None, Depends(require_owner)]


@router.post("/login")
def login(payload: LoginIn, response: Response):
    token = authenticate(payload.username, payload.password)
    if token is None:
        raise HTTPException(401, "Usuário ou senha inválidos.")
    response.headers["Cache-Control"] = "no-store"
    return {"token": token}


@protected.get("/me")
def staff_profile(member: Staff):
    return member


@protected.post("/logout", status_code=204)
def logout(member: Staff, credentials: Bearer):
    revoke_session(credentials.credentials)


@protected.get("/appointments")
def staff_appointments(day: date, member: Staff, barber_id: int | None = None):
    barber_id = _scope(member, barber_id)
    with connect() as conn:
        rows = conn.execute("""
            SELECT a.*, b.name AS barber, s.name AS service, COALESCE(a.price_at_booking,s.price) AS price
            FROM appointments a JOIN barbers b ON b.id=a.barber_id
            JOIN services s ON s.id=a.service_id
            WHERE substr(a.starts_at, 1, 10)=? AND (? IS NULL OR a.barber_id=?)
            ORDER BY a.starts_at, b.name
        """, (day.isoformat(), barber_id, barber_id)).fetchall()
    return [dict(row) for row in rows]


def _booking_phone(member: StaffMember, appointment_id: int) -> str:
    with connect() as conn:
        row = conn.execute("""
            SELECT customer_phone FROM appointments
            WHERE id=? AND (? IS NULL OR barber_id=?)
        """, (appointment_id, member.barber_id, member.barber_id)).fetchone()
    if not row:
        raise HTTPException(404, "Agendamento não encontrado.")
    return row["customer_phone"]


def _check_schedule_day(day: date):
    today = datetime.now(TZ).date()
    if not today <= day <= today + timedelta(days=30):
        raise BookingError("Escolha uma data entre hoje e os próximos 30 dias.")


@protected.get("/availability")
def staff_availability(service_id: int, day: date, member: Staff, barber_id: int | None = None):
    barber_id = _scope(member, barber_id)
    try:
        _check_schedule_day(day)
        return availability(service_id, day, barber_id, days_range=False)
    except BookingError as exc:
        raise HTTPException(409, str(exc)) from exc


@protected.post("/appointments/{appointment_id}/access-code")
def staff_replace_access_code(appointment_id: int, payload: ConfirmationIn, member: Staff):
    if not payload.confirmed:
        raise HTTPException(409, "Confirme a identidade do cliente antes de emitir um código.")
    code = replace_access_code(appointment_id, member.barber_id)
    if code is None:
        raise HTTPException(404, "Agendamento não encontrado.")
    return {"access_code": code}


@protected.post("/appointments", status_code=201)
def staff_book(payload: CounterBookingIn, member: Staff):
    _scope(member, payload.barber_id)
    try:
        _check_schedule_day(_parse_start(payload.starts_at).date())
        return create_appointment(payload.customer_name, payload.customer_phone, payload.service_id,
                                  payload.barber_id, payload.starts_at, payload.confirmed, days_range=False,
                                  expected_price=payload.expected_price, expected_duration=payload.expected_duration)
    except BookingError as exc:
        raise HTTPException(409, str(exc)) from exc


@protected.get("/blocks")
def staff_blocks(day: date, member: Staff, barber_id: int | None = None):
    return list_blocks(day, _scope(member, barber_id))


@protected.post("/blocks", status_code=201)
def staff_block(payload: BlockIn, member: Staff):
    _scope(member, payload.barber_id)
    try:
        record = create_block(payload.barber_id, payload.starts_at, payload.ends_at, payload.reason)
    except BookingError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not record:
        raise HTTPException(404, "Barbeiro não encontrado.")
    return record


@protected.delete("/blocks/{block_id}", status_code=204)
def staff_unblock(block_id: int, member: Staff):
    if not remove_block(block_id, member.barber_id):
        raise HTTPException(404, "Bloqueio não encontrado.")


@protected.post("/appointments/{appointment_id}/cancel")
def staff_cancel(appointment_id: int, payload: ConfirmationIn, member: Staff):
    phone = _booking_phone(member, appointment_id)
    try:
        return cancel_appointment(appointment_id, phone, payload.confirmed, min_notice_hours=0)
    except BookingError as exc:
        raise HTTPException(409, str(exc)) from exc


@protected.get("/appointments/{appointment_id}/reschedule-options")
def staff_reschedule_options(appointment_id: int, day: date, member: Staff):
    phone = _booking_phone(member, appointment_id)
    try:
        _check_schedule_day(day)
        return reschedule_options(appointment_id, phone, day, min_notice_hours=0, days_range=False)
    except BookingError as exc:
        raise HTTPException(409, str(exc)) from exc


@protected.post("/appointments/{appointment_id}/reschedule")
def staff_reschedule(appointment_id: int, payload: RescheduleStaffIn, member: Staff):
    phone = _booking_phone(member, appointment_id)
    try:
        _check_schedule_day(_parse_start(payload.new_starts_at).date())
        return reschedule_appointment(appointment_id, phone, payload.new_starts_at, payload.confirmed,
                                      min_notice_hours=0, days_range=False)
    except BookingError as exc:
        raise HTTPException(409, str(exc)) from exc


@protected.patch("/services/{service_id}")
def update_service(service_id: int, payload: ServiceUpdate, owner: Owner):
    with connect() as conn:
        row = conn.execute("""
            UPDATE services SET price=?, duration_minutes=? WHERE id=?
            RETURNING id, name, price, duration_minutes
        """, (round(payload.price, 2), payload.duration_minutes, service_id)).fetchone()
    if not row:
        raise HTTPException(404, "Serviço não encontrado.")
    return dict(row)


@protected.post("/services", status_code=201)
def add_service(payload: ServiceCreate, owner: Owner):
    try:
        with connect() as conn:
            row = conn.execute("""
                INSERT INTO services (name, price, duration_minutes) VALUES (?, ?, ?)
                RETURNING id, name, price, duration_minutes
            """, (payload.name, round(payload.price, 2), payload.duration_minutes)).fetchone()
    except IntegrityError as exc:
        raise HTTPException(409, "Já existe um serviço com esse nome.") from exc
    return dict(row)


@protected.put("/shop")
def save_shop(payload: ShopUpdate, owner: Owner):
    try:
        return update_shop(payload.model_dump())
    except BookingError as exc:
        raise HTTPException(409, str(exc)) from exc


@protected.put("/barbers/{barber_id}/working-days")
def save_working_days(barber_id: int, payload: WorkingDaysUpdate, owner: Owner):
    try:
        barber = update_working_days(barber_id, payload.working_days)
    except BookingError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not barber:
        raise HTTPException(404, "Barbeiro não encontrado.")
    return barber


router.include_router(protected)
