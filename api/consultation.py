"""
咨询 API（Phase D D6：A-R3 修复）

旧实现调用不存在的 process_consultation 恒 400；现走统一咨询路径
（ConsultantAgent.consult），与规范编排共享同一咨询能力。
"""

import logging

from fastapi import APIRouter, HTTPException

from .core.response_models import ConsultationRequest, DataResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/consultation", tags=["咨询服务"])


@router.post("/ask", response_model=DataResponse)
async def ask_consultation(request: ConsultationRequest):
    """提交咨询问题（统一咨询路径）"""
    try:
        from api.chat_handler import get_container
        from application.contracts import EventType

        container = get_container()
        session = container.session_manager.get_or_create_default(request.user_id)
        chunks = []
        async for event in container.orchestrator.handle_turn(
            session.conversation_id,
            request.user_id,
            request.question,
            request_id=None,
        ):
            if event.type == EventType.ASSISTANT_DELTA:
                chunks.append(event.data.get("text", ""))
            elif event.type == EventType.RUN_FAILED:
                raise HTTPException(
                    status_code=500,
                    detail={"code": event.data.get("error", "INTERNAL_ERROR"),
                            "message": event.data.get("message", "咨询服务异常")},
                )
        result = "".join(chunks)
        return DataResponse(
            message="咨询处理成功",
            data={"answer": result, "question": request.question},
        )
    except Exception as exc:
        logger.error("咨询失败", exc_info=True)
        raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": "咨询服务异常"})
