"""Phase G G4：商家会话工作台 API。"""

from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.admin_auth import get_current_admin, require_csrf, require_permission
from services.admin_workbench import AdminWorkbenchService, WorkbenchError

router = APIRouter(prefix="/api/v1/admin/conversations", tags=["会话工作台"])


class ReasonBody(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=512)


class NoteBody(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


@lru_cache(maxsize=1)
def get_workbench_service() -> AdminWorkbenchService:
    return AdminWorkbenchService()


def _store_id(identity: dict) -> int:
    active = identity.get("active_store") or {}
    if not active.get("store_id"):
        raise HTTPException(status_code=403, detail={"code": "STORE_FORBIDDEN", "message": "没有当前门店"})
    return int(active["store_id"])


def _request_id(request: Request) -> Optional[str]:
    return request.headers.get("X-Request-ID")


def _error(exc: WorkbenchError) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": "INVALID_CONVERSATION_ACTION", "message": str(exc)})


@router.get("")
def list_conversations(
    identity=Depends(get_current_admin),
    service: AdminWorkbenchService = Depends(get_workbench_service),
):
    return {"items": service.list_conversations(_store_id(identity))}


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: str,
    identity=Depends(get_current_admin),
    service: AdminWorkbenchService = Depends(get_workbench_service),
):
    detail = service.get_detail(_store_id(identity), conversation_id)
    if detail is None:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND", "message": "会话不存在"})
    return detail


@router.post("/{conversation_id}/takeover")
def takeover(
    conversation_id: str,
    body: ReasonBody,
    request: Request,
    identity=Depends(require_permission("manage_conversations")),
    _csrf=Depends(require_csrf),
    service: AdminWorkbenchService = Depends(get_workbench_service),
):
    try:
        control = service.change_control(
            _store_id(identity), conversation_id, identity["actor"]["actor_id"],
            "human_active", body.reason, _request_id(request),
        )
    except WorkbenchError as exc:
        raise _error(exc)
    if control is None:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND", "message": "会话不存在"})
    return {"control": control}


@router.post("/{conversation_id}/resume-ai")
def resume_ai(
    conversation_id: str,
    body: ReasonBody,
    request: Request,
    identity=Depends(require_permission("manage_conversations")),
    _csrf=Depends(require_csrf),
    service: AdminWorkbenchService = Depends(get_workbench_service),
):
    try:
        control = service.change_control(
            _store_id(identity), conversation_id, identity["actor"]["actor_id"],
            "ai_active", body.reason, _request_id(request),
        )
    except WorkbenchError as exc:
        raise _error(exc)
    if control is None:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND", "message": "会话不存在"})
    return {"control": control}


@router.post("/{conversation_id}/notes", status_code=201)
def add_note(
    conversation_id: str,
    body: NoteBody,
    request: Request,
    identity=Depends(require_permission("manage_conversations")),
    _csrf=Depends(require_csrf),
    service: AdminWorkbenchService = Depends(get_workbench_service),
):
    try:
        event = service.add_event(
            _store_id(identity), conversation_id, identity["actor"]["actor_id"],
            "internal_note", body.content, _request_id(request),
        )
    except WorkbenchError as exc:
        raise _error(exc)
    if event is None:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND", "message": "会话不存在"})
    return event


@router.post("/{conversation_id}/reply", status_code=200)
def human_reply(
    conversation_id: str,
    body: NoteBody,
    request: Request,
    identity=Depends(require_permission("manage_conversations")),
    _csrf=Depends(require_csrf),
    service: AdminWorkbenchService = Depends(get_workbench_service),
):
    """人工回复：人工消息写入同一会话，自动置人工接管态并审计（H3）。"""
    try:
        result = service.human_reply(
            _store_id(identity), conversation_id, identity["actor"]["actor_id"],
            body.content, _request_id(request),
        )
    except WorkbenchError as exc:
        raise _error(exc)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND", "message": "会话不存在"})
    return result
