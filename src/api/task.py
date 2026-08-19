"""
任务分类 API（Phase D D6：A-R3 修复）

旧实现调用不存在的字段/方法恒 400；现转统一确定性意图规则
（application/intent_rules.py），与规范编排共享同一分类逻辑。
"""

import logging

from fastapi import APIRouter, HTTPException

from .core.response_models import DataResponse, TaskClassificationRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/task", tags=["任务分类"])


@router.post("/classify", response_model=DataResponse)
async def classify_task(request: TaskClassificationRequest):
    """分类任务（确定性规则；模糊输入返回 unknown 而非伪造结果）"""
    try:
        from application.intent_rules import match_intent

        text = getattr(request, "text", "") or ""
        result = match_intent(text)
        return DataResponse(
            message=f"任务分类成功（规则: {result.matched_rule or 'llm/unknown'}）",
            data=result.to_dict(),
        )
    except Exception as exc:
        logger.error("任务分类失败", exc_info=True)
        raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": "分类服务异常"})
