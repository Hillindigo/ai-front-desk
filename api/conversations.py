"""会话 API（Phase B B4）

- POST /api/v1/conversations                  创建会话
- GET  /api/v1/conversations/{id}             获取会话元数据 + 最近消息
- POST /api/v1/conversations/{id}/turns       发送一轮消息（流式返回）

URL 中的 conversation_id 是会话主标识；服务端校验会话存在与归属。
"""

from typing import Optional
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.chat_handler import get_container, get_session_manager

router = APIRouter(prefix="/api/v1/conversations", tags=["会话"])


class CreateConversationRequest(BaseModel):
    user_id: str = "default_user"
    channel: str = "web"


class TurnRequest(BaseModel):
    message: str
    user_id: str = "default_user"
    client_request_id: Optional[str] = None  # D4：请求去重标识（不替代预约幂等键）


def _resolve_session(conversation_id: str, user_id: str):
    """解析会话并校验存在/归属，异常转为稳定 HTTP 错误。"""
    try:
        return get_session_manager().get_or_create_session(conversation_id, user_id=user_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail={"code": "CONVERSATION_NOT_FOUND", "message": "会话不存在"},
        )
    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail={"code": "CONVERSATION_ACCESS_DENIED", "message": "会话归属不符"},
        )


@router.post("")
def create_conversation(request: CreateConversationRequest):
    """创建会话，返回 conversation_id。"""
    session = get_session_manager().create_conversation(
        user_id=request.user_id, channel=request.channel
    )
    return {
        "conversation_id": session.conversation_id,
        "user_id": session.user_id,
        "channel": session.channel,
        "status": session.status,
    }


@router.get("/{conversation_id}/sources")
def conversation_sources(conversation_id: str, user_id: str = "default_user"):
    """最近一条 assistant 消息的回答依据（F6：来源卡片/无依据提示）。

    证据来自 assistant 消息 metadata（F5 写入）；无证据时 has_evidence=false，
    前端不得伪造来源。断线/刷新可据此从服务端恢复依据状态。
    """
    _resolve_session(conversation_id, user_id)
    repo = get_container().db_router.conversations
    messages = repo.get_recent_messages(conversation_id, limit=50)
    last_evidence = []
    message_id = None
    for m in reversed(messages or []):
        if m.get("role") != "assistant":
            continue
        meta = m.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (ValueError, TypeError):
                meta = {}
        last_evidence = meta.get("evidence") or []
        message_id = m.get("id")
        break
    return {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "has_evidence": bool(last_evidence),
        "evidence": [
            {
                "document_id": e.get("document_id"),
                "category": e.get("category", ""),
                "snippet": e.get("snippet", ""),
                "score": e.get("score", 0.0),
                "source_version": e.get("source_version", ""),
            }
            for e in last_evidence
        ],
    }


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str, user_id: str = "default_user"):
    """获取会话元数据与最近消息（恢复历史）。"""
    session = _resolve_session(conversation_id, user_id)
    return {
        "conversation_id": session.conversation_id,
        "user_id": session.user_id,
        "channel": session.channel,
        "status": session.status,
        "messages": session.recent_messages,
    }


@router.post("/{conversation_id}/turns")
async def send_turn(conversation_id: str, request: TurnRequest):
    """发送一轮消息（Phase D D4：SSE 事件流，事件只描述当前轮次）。

    失败以 run_failed 终止事件结束，不中途切换为未定义 JSON。
    """
    if not request.message or not request.message.strip():
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_INPUT", "message": "消息不能为空"},
        )

    _resolve_session(conversation_id, request.user_id)

    from application.events import sse_frame

    container = get_container()

    async def event_generator():
        async for envelope in container.orchestrator.handle_turn(
            conversation_id, request.user_id, request.message,
            request_id=request.client_request_id,
        ):
            yield sse_frame(envelope)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
