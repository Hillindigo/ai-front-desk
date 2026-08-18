"""Phase G G7：审计和指标 API。"""

from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends

from api.admin_auth import get_current_admin, require_permission
from services.audit_metrics import AuditMetricsService

router = APIRouter(prefix="/api/v1/admin", tags=["审计与指标"])


@lru_cache(maxsize=1)
def get_audit_metrics_service():
    return AuditMetricsService()


def _store_id(identity):
    active = identity.get("active_store") or {}
    if not active.get("store_id"):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail={"code": "STORE_FORBIDDEN", "message": "没有当前门店"})
    return int(active["store_id"])


@router.get("/audit")
def list_audit(
    actor_id: Optional[int] = None,
    action: Optional[str] = None,
    outcome: Optional[str] = None,
    identity=Depends(require_permission("read_audit")),
    service: AuditMetricsService = Depends(get_audit_metrics_service),
):
    return {"items": service.list_audit(_store_id(identity), actor_id, action, outcome)}


@router.get("/metrics")
def metrics(
    identity=Depends(require_permission("read_audit")),
    service: AuditMetricsService = Depends(get_audit_metrics_service),
):
    return service.metrics(_store_id(identity))
