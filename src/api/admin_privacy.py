"""Phase I I2：客户数据导出与删除/匿名化 API（D9 商家侧 / D10 幂等删除登记 / D11 短时令牌）。

- 全部写操作需 manage_customer_data 权限（owner/manager）+ CSRF + 当前门店 scope。
- 导出：创建短时一次性导出记录，下载需携带一次性令牌。
- 匿名化：默认 dry-run；真实执行带幂等 request_id，均写审计与删除登记。
"""

from functools import lru_cache
import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.admin_auth import get_current_admin, require_csrf, require_permission
from services.customer_privacy import CustomerPrivacyService, CustomerPrivacyError

router = APIRouter(prefix="/api/v1/admin", tags=["客户数据隐私"])


class AnonymizeRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    dry_run: bool = True


@lru_cache(maxsize=1)
def get_privacy_service() -> CustomerPrivacyService:
    return CustomerPrivacyService()


def _store_id(identity):
    active = identity.get("active_store") or {}
    if not active.get("store_id"):
        raise HTTPException(status_code=403, detail={"code": "STORE_FORBIDDEN", "message": "没有当前门店"})
    return int(active["store_id"])


def _actor_id(identity):
    return identity.get("actor", {}).get("actor_id")


def _write_audit(store_id, actor_id, action, resource_type, resource_id,
                 outcome, request_id=None, summary=None):
    from db.base.session_manager import SessionManager
    from db.models import AuditEvent

    with SessionManager().session_scope() as session:
        session.add(AuditEvent(
            id=uuid.uuid4().hex,
            actor_id=actor_id,
            store_id=store_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            outcome=outcome,
            summary_json=summary,
            created_at=datetime.utcnow(),
        ))


def _privacy_error(exc: CustomerPrivacyError):
    return HTTPException(status_code=422, detail={"code": exc.code, "message": exc.message})


# ---------------- 导出 ----------------

@router.post("/customers/{customer_user_id}/export")
def export_customer(
    customer_user_id: str,
    request: Request,
    identity=Depends(require_permission("manage_customer_data")),
    _csrf=Depends(require_csrf),
    service: CustomerPrivacyService = Depends(get_privacy_service),
):
    store_id = _store_id(identity)
    result = service.create_export(store_id, customer_user_id)
    _write_audit(store_id, _actor_id(identity), "customer.data.export",
                 "customer", customer_user_id, "succeeded",
                 request_id=request.headers.get("X-Request-ID"),
                 summary=json.dumps({"counts": result.get("counts")}, ensure_ascii=False))
    return {
        "export_id": result["export_id"],
        "expires_in_seconds": result["expires_in_seconds"],
        "download_token": result["download_token"],
        "counts": result["counts"],
    }


@router.get("/customer-exports/{export_id}")
def download_export(
    export_id: str,
    token: str = Query(...),
    service: CustomerPrivacyService = Depends(get_privacy_service),
):
    try:
        return service.consume_export(export_id, token)
    except CustomerPrivacyError as exc:
        raise _privacy_error(exc)


# ---------------- 删除 / 匿名化 ----------------

@router.post("/customers/{customer_user_id}/anonymize")
def anonymize_customer(
    customer_user_id: str,
    body: AnonymizeRequest,
    request: Request,
    identity=Depends(require_permission("manage_customer_data")),
    _csrf=Depends(require_csrf),
    service: CustomerPrivacyService = Depends(get_privacy_service),
):
    store_id = _store_id(identity)
    result = service.anonymize_customer(
        store_id, customer_user_id,
        actor_id=_actor_id(identity),
        request_id=body.request_id,
        dry_run=body.dry_run,
    )
    _write_audit(
        store_id, _actor_id(identity),
        "customer.data.anonymize", "customer", customer_user_id,
        "succeeded" if not result.get("dry_run") else "dry_run",
        request_id=body.request_id,
        summary=json.dumps({"dry_run": result.get("dry_run", True), "counts": result.get("counts")},
                            ensure_ascii=False),
    )
    return result
