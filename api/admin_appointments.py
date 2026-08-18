"""Phase G G5：商家预约管理 API。"""

from functools import lru_cache
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.admin_auth import get_current_admin, require_csrf, require_permission
from services.admin_appointments import AdminAppointmentService
from services.appointment_domain import AppointmentDomainError

router = APIRouter(prefix="/api/v1/admin/appointments", tags=["商家预约"])


class CancelBody(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=256)


class ConfirmBody(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


class RescheduleBody(BaseModel):
    start_time: datetime
    end_time: datetime


@lru_cache(maxsize=1)
def get_admin_appointment_service():
    return AdminAppointmentService()


def _store_id(identity):
    active = identity.get("active_store") or {}
    if not active.get("store_id"):
        raise HTTPException(status_code=403, detail={"code": "STORE_FORBIDDEN", "message": "没有当前门店"})
    return int(active["store_id"])


def _error(exc):
    return HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message})


@router.get("")
def list_appointments(
    status: Optional[str] = None,
    identity=Depends(get_current_admin),
    service: AdminAppointmentService = Depends(get_admin_appointment_service),
):
    return {"items": service.list(_store_id(identity), status=status)}


@router.get("/{appointment_id}")
def get_appointment(
    appointment_id: str,
    identity=Depends(get_current_admin),
    service: AdminAppointmentService = Depends(get_admin_appointment_service),
):
    result = service.get(_store_id(identity), appointment_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "APPOINTMENT_NOT_FOUND", "message": "预约不存在"})
    return result


@router.post("/{appointment_id}/confirm")
def confirm_appointment(
    appointment_id: str,
    body: ConfirmBody,
    identity=Depends(require_permission("write_appointments")),
    _csrf=Depends(require_csrf),
    service: AdminAppointmentService = Depends(get_admin_appointment_service),
):
    try:
        result = service.confirm(_store_id(identity), appointment_id, body.idempotency_key)
    except AppointmentDomainError as exc:
        raise _error(exc)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "APPOINTMENT_NOT_FOUND", "message": "预约不存在"})
    return result


@router.post("/{appointment_id}/cancel")
def cancel_appointment(
    appointment_id: str,
    body: CancelBody,
    request: Request,
    identity=Depends(require_permission("write_appointments")),
    _csrf=Depends(require_csrf),
    service: AdminAppointmentService = Depends(get_admin_appointment_service),
):
    try:
        result = service.cancel(
            _store_id(identity), appointment_id, body.reason,
            request.headers.get("X-Request-ID"),
        )
    except AppointmentDomainError as exc:
        raise _error(exc)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "APPOINTMENT_NOT_FOUND", "message": "预约不存在"})
    return result


@router.post("/{appointment_id}/reschedule")
def reschedule_appointment(
    appointment_id: str,
    body: RescheduleBody,
    request: Request,
    identity=Depends(require_permission("write_appointments")),
    _csrf=Depends(require_csrf),
    service: AdminAppointmentService = Depends(get_admin_appointment_service),
):
    try:
        result = service.reschedule(
            _store_id(identity), appointment_id, body.start_time, body.end_time,
            request.headers.get("X-Request-ID"),
        )
    except AppointmentDomainError as exc:
        raise _error(exc)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "APPOINTMENT_NOT_FOUND", "message": "预约不存在"})
    return result
