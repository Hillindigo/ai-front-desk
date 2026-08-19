"""偏好管理 API（Phase E E4，对应计划 4.2）。

- GET    /api/v1/preferences                       查看当前用户偏好
- PUT    /api/v1/preferences/{preference_type}     覆盖写入（单 active 值语义）
- DELETE /api/v1/preferences/{preference_type}     删除（幂等成功）

约束：
- 用户身份从身份上下文解析（IdentityResolver），请求体 user_id 只作兼容字段。
- 写入请求不能由客户端伪造高可信度（来源由服务端判定）。
- 未完成正式鉴权前，本接口为本地演示边界，不宣称生产可用。
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.chat_handler import get_container
from application.context_contracts import PreferenceDomainError
from application.identity import IdentityError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/preferences", tags=["偏好"])


class PreferenceWriteRequest(BaseModel):
    value: str
    user_id: str = "default_user"  # 兼容字段：必须与已解析身份一致
    source_message_id: Optional[str] = None


def _resolve_identity(request_user_id: str) -> str:
    try:
        return get_container().identity_resolver.resolve(request_user_id)
    except IdentityError:
        raise HTTPException(
            status_code=403,
            detail={"code": "CONVERSATION_ACCESS_DENIED", "message": "身份校验失败，会话归属不符"},
        )


@router.get("")
def list_preferences(user_id: str = "default_user"):
    """查看当前用户自己的偏好（含 legacy_unverified 标记，不提升可信度）。"""
    identity = _resolve_identity(user_id)
    preferences = get_container().preference_service.list_preferences(identity)
    return {"user_id": identity, "preferences": preferences}


@router.put("/{preference_type}")
def set_preference(preference_type: str, request: PreferenceWriteRequest):
    """覆盖写入（PUT 替换而非追加；同类型旧值原子停用）。"""
    identity = _resolve_identity(request.user_id)
    service = get_container().preference_service
    try:
        record = service.set_preference(
            user_id=identity,
            preference_type=preference_type,
            preference_value=request.value,
            source="explicit_memorize",
            source_message_id=request.source_message_id,
        )
    except PreferenceDomainError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_INPUT", "message": exc.message},
        )
    except Exception:
        logger.exception("偏好写入失败")
        raise HTTPException(
            status_code=500,
            detail={"code": "INTERNAL_ERROR", "message": "偏好保存失败，请稍后再试。"},
        )
    return {"status": "saved", "preference": record}


@router.delete("/{preference_type}")
def delete_preference(preference_type: str, user_id: str = "default_user"):
    """删除偏好；不存在也返回成功（幂等语义，不泄漏他用户数据）。"""
    identity = _resolve_identity(user_id)
    service = get_container().preference_service
    try:
        tombstone = service.delete_preference(identity, preference_type)
    except Exception:
        logger.exception("偏好删除失败")
        raise HTTPException(
            status_code=500,
            detail={"code": "INTERNAL_ERROR", "message": "删除失败，请稍后再试。"},
        )
    return {"status": "deleted", "tombstone": tombstone}