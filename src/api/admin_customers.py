"""Phase G G6：客户运营与回访任务 API。"""

from functools import lru_cache
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.admin_auth import get_current_admin, require_csrf, require_permission
from services.admin_customers import AdminCustomerService, CustomerOpsError

router = APIRouter(prefix="/api/v1/admin/customers", tags=["客户运营"])


class FollowUpCreate(BaseModel):
    reason: str = Field(min_length=1, max_length=512)
    due_at: Optional[datetime] = None
    assignee_id: Optional[int] = None
    source_type: Optional[str] = Field(default=None, max_length=32)
    source_id: Optional[str] = Field(default=None, max_length=128)


class FollowUpStatus(BaseModel):
    status: str


@lru_cache(maxsize=1)
def get_customer_service():
    return AdminCustomerService()


def _store_id(identity):
    active = identity.get("active_store") or {}
    if not active.get("store_id"):
        raise HTTPException(status_code=403, detail={"code": "STORE_FORBIDDEN", "message": "没有当前门店"})
    return int(active["store_id"])


def _error(exc):
    return HTTPException(status_code=422, detail={"code": "INVALID_CUSTOMER_OPERATION", "message": str(exc)})


@router.get("")
def list_customers(
    identity=Depends(get_current_admin),
    service: AdminCustomerService = Depends(get_customer_service),
):
    return {"items": service.list_customers(_store_id(identity))}


@router.get("/{customer_user_id}")
def get_customer(
    customer_user_id: str,
    identity=Depends(get_current_admin),
    service: AdminCustomerService = Depends(get_customer_service),
):
    result = service.get_customer(_store_id(identity), customer_user_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "CUSTOMER_NOT_FOUND", "message": "客户不存在"})
    return result


@router.get("/{customer_user_id}/follow-ups")
def list_follow_ups(
    customer_user_id: str,
    identity=Depends(get_current_admin),
    service: AdminCustomerService = Depends(get_customer_service),
):
    return {"items": service.list_follow_ups(_store_id(identity), customer_user_id)}


@router.post("/{customer_user_id}/follow-ups", status_code=201)
def create_follow_up(
    customer_user_id: str,
    body: FollowUpCreate,
    request: Request,
    identity=Depends(require_permission("manage_conversations")),
    _csrf=Depends(require_csrf),
    service: AdminCustomerService = Depends(get_customer_service),
):
    try:
        return service.create_follow_up(
            _store_id(identity), identity["actor"]["actor_id"], customer_user_id,
            body.model_dump(), request.headers.get("X-Request-ID"),
        )
    except CustomerOpsError as exc:
        raise _error(exc)


@router.post("/follow-ups/{task_id}/status")
def change_follow_up(
    task_id: str,
    body: FollowUpStatus,
    request: Request,
    identity=Depends(require_permission("manage_conversations")),
    _csrf=Depends(require_csrf),
    service: AdminCustomerService = Depends(get_customer_service),
):
    try:
        result = service.change_task(
            _store_id(identity), identity["actor"]["actor_id"], task_id,
            body.status, request.headers.get("X-Request-ID"),
        )
    except CustomerOpsError as exc:
        raise _error(exc)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "FOLLOW_UP_NOT_FOUND", "message": "回访任务不存在"})
    return result
