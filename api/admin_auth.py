"""Phase G G1：商家认证、会话和门店上下文 API。"""

from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from services.admin_auth import AdminAuthService, SESSION_COOKIE_NAME
from application.admin_permissions import has_permission

router = APIRouter(prefix="/api/v1/admin/auth", tags=["商家认证"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class StoreSwitchRequest(BaseModel):
    store_id: int = Field(gt=0)


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@lru_cache(maxsize=1)
def get_admin_auth_service() -> AdminAuthService:
    """进程内共享认证服务；会话事实仍持久化在数据库。"""
    return AdminAuthService()


def _current_token(request: Request) -> Optional[str]:
    return request.cookies.get(SESSION_COOKIE_NAME)


def get_current_admin(
    request: Request,
    service: AdminAuthService = Depends(get_admin_auth_service),
):
    identity = service.resolve_session(_current_token(request))
    if identity is None:
        raise _error(401, "UNAUTHENTICATED", "请先登录商家后台")
    request.state.admin_identity = identity
    return identity


def require_csrf(
    request: Request,
    identity=Depends(get_current_admin),
    csrf_token: Optional[str] = Header(default=None, alias="X-CSRF-Token"),
    service: AdminAuthService = Depends(get_admin_auth_service),
):
    if not service.verify_csrf(_current_token(request), csrf_token):
        raise _error(403, "CSRF_INVALID", "缺少或无效的 CSRF token")
    return identity


def require_permission(permission: str):
    def dependency(identity=Depends(get_current_admin)):
        if not has_permission(identity.get("role"), permission):
            raise _error(403, "PERMISSION_DENIED", "当前角色无权执行该操作")
        return identity
    return dependency


def _public_identity(identity: dict) -> dict:
    return {
        "actor": identity["actor"],
        "stores": identity["stores"],
        "active_store": identity["active_store"],
        "role": identity["role"],
    }


@router.post("/login")
def login(
    body: LoginRequest,
    response: Response,
    service: AdminAuthService = Depends(get_admin_auth_service),
):
    identity = service.authenticate(body.username, body.password)
    if identity is None:
        raise _error(401, "AUTHENTICATION_FAILED", "账号或密码错误")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=identity["session_token"],
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=8 * 60 * 60,
        path="/",
    )
    return {
        **_public_identity(identity),
        "csrf_token": identity["csrf_token"],
    }


@router.get("/me")
def me(identity=Depends(get_current_admin)):
    return _public_identity(identity)


@router.get("/stores")
def stores(identity=Depends(get_current_admin)):
    return {"stores": identity["stores"], "active_store": identity["active_store"]}


@router.post("/stores/switch")
def switch_store(
    body: StoreSwitchRequest,
    request: Request,
    identity=Depends(require_csrf),
    service: AdminAuthService = Depends(get_admin_auth_service),
):
    updated = service.switch_store(_current_token(request), body.store_id)
    if updated is None:
        raise _error(403, "STORE_FORBIDDEN", "当前账号无权访问该门店")
    return _public_identity(updated)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    identity=Depends(require_csrf),
    service: AdminAuthService = Depends(get_admin_auth_service),
):
    service.revoke(_current_token(request))
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
