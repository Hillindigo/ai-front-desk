"""预约领域 API（Phase C C6）。

- POST   /api/v1/appointments                创建草稿（mode=draft）或创建并确认（mode=confirm）
- GET    /api/v1/appointments/{id}           查询预约详情（归属校验）
- POST   /api/v1/appointments/{id}/confirm   确认待确认预约（支持幂等键）
- POST   /api/v1/appointments/{id}/cancel    取消预约
- POST   /api/v1/appointments/{id}/reschedule 原子改约
- GET    /api/v1/availability                查询可用性

请求/响应均为显式模型，不接收任意字典直接写库；
领域错误返回稳定 code + 可读 message。
"""

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.appointment_domain import AppointmentCommandService, AppointmentDomainError

router = APIRouter(prefix="/api/v1/appointments", tags=["预约"])


class AppointmentCreateRequest(BaseModel):
    user_id: str = "default_user"
    conversation_id: Optional[str] = None
    service_type: str
    mode: str = "draft"  # draft | confirm
    project: Optional[str] = None
    technician_id: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    idempotency_key: Optional[str] = None


class AppointmentActionRequest(BaseModel):
    user_id: str = "default_user"
    idempotency_key: Optional[str] = None
    reason: Optional[str] = None                       # cancel 用
    new_start_time: Optional[datetime] = None          # reschedule 用
    new_end_time: Optional[datetime] = None            # reschedule 用


_STATUS_CODE = {
    "APPOINTMENT_NOT_FOUND": 404,
    "TECHNICIAN_NOT_FOUND": 404,
    "APPOINTMENT_INVALID_STATE": 409,
    "APPOINTMENT_CONFLICT": 409,
    "TECHNICIAN_UNAVAILABLE": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "APPOINTMENT_TIME_INVALID": 422,
    "APPOINTMENT_REQUIRED_FIELD": 422,
    "APPOINTMENT_PERSISTENCE_FAILED": 500,
}


def _raise_domain(e: AppointmentDomainError) -> None:
    raise HTTPException(
        status_code=_STATUS_CODE.get(e.code, 400),
        detail={"code": e.code, "message": e.message},
    )


def _fields_of(req: AppointmentCreateRequest) -> Dict[str, Any]:
    return {
        "project": req.project,
        "technician_id": req.technician_id,
        "start_time": req.start_time,
        "end_time": req.end_time,
        "duration_minutes": req.duration_minutes,
    }


@router.post("")
def create_appointment(request: AppointmentCreateRequest):
    """创建草稿（mode=draft）或一步创建并确认（mode=confirm）。"""
    svc = AppointmentCommandService()
    try:
        if request.mode == "confirm":
            # 幂等优先：同 (user_id, idempotency_key) 已存在直接返回原预约
            if request.idempotency_key:
                existing = svc.repo.get_by_idempotency(request.user_id, request.idempotency_key)
                if existing is not None:
                    return existing
            # 优先复用会话现有活跃草稿，否则新建
            draft = None
            if request.conversation_id:
                draft = svc.get_active_draft(request.conversation_id)
            if draft:
                draft = svc.update_draft(draft["id"], request.user_id, _fields_of(request))
            else:
                draft = svc.create_draft(
                    user_id=request.user_id,
                    conversation_id=request.conversation_id,
                    service_type=request.service_type,
                    fields=_fields_of(request),
                )
            pending = svc.request_confirmation(draft["id"], request.user_id)
            return svc.confirm(pending["id"], request.user_id, idempotency_key=request.idempotency_key)

        # mode=draft
        return svc.create_draft(
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            service_type=request.service_type,
            fields=_fields_of(request),
        )
    except AppointmentDomainError as e:
        _raise_domain(e)
    finally:
        svc.close()


@router.get("/availability")
def check_availability(
    technician_id: int = Query(...),
    start_time: datetime = Query(...),
    end_time: datetime = Query(...),
):
    """查询服务人员在 [start_time, end_time) 的可用性。

    注意：本路由必须注册在 /{appointment_id} 之前（FastAPI 按注册顺序匹配）。
    """
    from services.appointment_service import AppointmentService

    svc = AppointmentService()
    try:
        return svc.check_technician_availability(technician_id, start_time, end_time)
    finally:
        svc.db_router.close()


@router.get("/{appointment_id}")
def get_appointment(appointment_id: str, user_id: str = "default_user"):
    """查询预约详情（归属校验）。"""
    svc = AppointmentCommandService()
    try:
        appt = svc.repo.get(appointment_id, user_id)
        if appt is None:
            raise HTTPException(status_code=404, detail={"code": "APPOINTMENT_NOT_FOUND", "message": "预约不存在"})
        return appt
    finally:
        svc.close()


@router.post("/{appointment_id}/confirm")
def confirm_appointment(appointment_id: str, request: AppointmentActionRequest):
    svc = AppointmentCommandService()
    try:
        return svc.confirm(appointment_id, request.user_id, idempotency_key=request.idempotency_key)
    except AppointmentDomainError as e:
        _raise_domain(e)
    finally:
        svc.close()


@router.post("/{appointment_id}/cancel")
def cancel_appointment(appointment_id: str, request: AppointmentActionRequest):
    svc = AppointmentCommandService()
    try:
        return svc.cancel(appointment_id, request.user_id, reason=request.reason,
                          request_id=request.idempotency_key)
    except AppointmentDomainError as e:
        _raise_domain(e)
    finally:
        svc.close()


@router.post("/{appointment_id}/reschedule")
def reschedule_appointment(appointment_id: str, request: AppointmentActionRequest):
    if request.new_start_time is None or request.new_end_time is None:
        raise HTTPException(status_code=422, detail={"code": "APPOINTMENT_TIME_INVALID", "message": "缺少新时间"})
    svc = AppointmentCommandService()
    try:
        return svc.reschedule(appointment_id, request.user_id,
                              request.new_start_time, request.new_end_time,
                              request_id=request.idempotency_key)
    except AppointmentDomainError as e:
        _raise_domain(e)
    finally:
        svc.close()