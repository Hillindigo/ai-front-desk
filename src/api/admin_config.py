"""Phase G G3：商家门店配置 API。"""

from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from api.admin_auth import get_current_admin, require_csrf, require_permission
from services.store_config import StoreConfigError, StoreConfigService

router = APIRouter(prefix="/api/v1/admin/config", tags=["门店配置"])


class StoreProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    address: Optional[str] = Field(default=None, max_length=256)
    phone: Optional[str] = Field(default=None, max_length=64)
    timezone: Optional[str] = Field(default=None, max_length=64)
    is_open: Optional[bool] = None


class ServiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    price_cents: int
    duration_minutes: int
    description: Optional[str] = Field(default=None, max_length=512)
    is_bookable: bool = True


class BusinessHoursUpdate(BaseModel):
    weekday: int = Field(ge=0, le=6)
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    is_closed: bool = False


class PolicyUpdate(BaseModel):
    min_notice_minutes: int = Field(ge=0)
    cancel_window_minutes: int = Field(ge=0)
    late_rule: Optional[str] = Field(default=None, max_length=256)


@lru_cache(maxsize=1)
def get_store_config_service() -> StoreConfigService:
    return StoreConfigService()


def _config_error(exc: StoreConfigError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "INVALID_CONFIG", "message": str(exc)},
    )


def _request_id(request: Request) -> Optional[str]:
    return request.headers.get("X-Request-ID")


def _store_id(identity: dict) -> int:
    active = identity.get("active_store") or {}
    if not active.get("store_id"):
        raise HTTPException(status_code=403, detail={"code": "STORE_FORBIDDEN", "message": "没有当前门店"})
    return int(active["store_id"])


@router.get("/store")
def get_store_profile(
    identity=Depends(require_permission("manage_store")),
    service: StoreConfigService = Depends(get_store_config_service),
):
    try:
        return service.get_profile(_store_id(identity))
    except StoreConfigError as exc:
        raise _config_error(exc)


@router.put("/store")
def update_store_profile(
    body: StoreProfileUpdate,
    request: Request,
    identity=Depends(require_permission("manage_store")),
    _csrf=Depends(require_csrf),
    service: StoreConfigService = Depends(get_store_config_service),
):
    try:
        values = body.model_dump(exclude_none=True)
        return service.update_profile(
            _store_id(identity), identity["actor"]["actor_id"], values, _request_id(request)
        )
    except StoreConfigError as exc:
        raise _config_error(exc)


@router.get("/services")
def list_services(
    identity=Depends(get_current_admin),
    service: StoreConfigService = Depends(get_store_config_service),
):
    return {"items": service.list_services(_store_id(identity))}


@router.post("/services", status_code=201)
def create_service(
    body: ServiceCreate,
    request: Request,
    identity=Depends(require_permission("manage_store")),
    _csrf=Depends(require_csrf),
    service: StoreConfigService = Depends(get_store_config_service),
):
    try:
        return service.create_service(
            _store_id(identity), identity["actor"]["actor_id"], body.model_dump(), _request_id(request)
        )
    except StoreConfigError as exc:
        raise _config_error(exc)


@router.post("/business-hours")
def update_business_hours(
    body: BusinessHoursUpdate,
    request: Request,
    identity=Depends(require_permission("manage_store")),
    _csrf=Depends(require_csrf),
    service: StoreConfigService = Depends(get_store_config_service),
):
    try:
        return service.set_business_hours(
            _store_id(identity), identity["actor"]["actor_id"], body.model_dump(), _request_id(request)
        )
    except StoreConfigError as exc:
        raise _config_error(exc)


@router.put("/policy")
def update_policy(
    body: PolicyUpdate,
    request: Request,
    identity=Depends(require_permission("manage_store")),
    _csrf=Depends(require_csrf),
    service: StoreConfigService = Depends(get_store_config_service),
):
    try:
        return service.set_policy(
            _store_id(identity), identity["actor"]["actor_id"], body.model_dump(), _request_id(request)
        )
    except StoreConfigError as exc:
        raise _config_error(exc)
