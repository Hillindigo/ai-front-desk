"""应用层契约（Phase D D1）：结构化模型，替代字符串协议。

- IntentType / IntentClassification：意图识别结果（确定性规则 + LLM 兜底）。
- EventType / EventEnvelope：SSE 事件协议 v1 的最小模型。
- ErrorCode / 领域错误映射：对外稳定错误码。
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class IntentType(str, Enum):
    APPOINTMENT = "appointment"
    CONSULTATION = "consultation"
    UNRELATED = "unrelated"
    UNKNOWN = "unknown"


class AppointmentSubAction(str, Enum):
    """预约意图的子动作（由规则表识别，不代替领域状态机）。"""
    DRAFT = "draft"
    CONFIRM = "confirm"
    CANCEL = "cancel"
    RESCHEDULE = "reschedule"
    NONE = "none"


class IntentClassification:
    """意图分类结果（结构化，禁止自由文本路由）。"""

    __slots__ = ("intent", "confidence", "matched_rule", "sub_action", "requires_clarification")

    def __init__(
        self,
        intent: IntentType,
        confidence: float = 1.0,
        matched_rule: Optional[str] = None,
        sub_action: AppointmentSubAction = AppointmentSubAction.NONE,
        requires_clarification: bool = False,
    ):
        self.intent = intent
        self.confidence = confidence
        self.matched_rule = matched_rule
        self.sub_action = sub_action
        self.requires_clarification = requires_clarification

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "matched_rule": self.matched_rule,
            "sub_action": self.sub_action.value,
            "requires_clarification": self.requires_clarification,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"IntentClassification({self.to_dict()})"


# ---------------- 事件协议 v1 ----------------


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    INTENT_DETECTED = "intent_detected"
    WORKFLOW_STARTED = "workflow_started"
    TOOL_STARTED = "tool_started"
    TOOL_RESULT = "tool_result"
    ASSISTANT_DELTA = "assistant_delta"
    ASSISTANT_MESSAGE = "assistant_message"
    RUN_COMPLETED = "run_completed"  # 终止事件
    RUN_FAILED = "run_failed"        # 终止事件


TERMINAL_EVENTS = {EventType.RUN_COMPLETED, EventType.RUN_FAILED}


class EventEnvelope:
    """事件包：一次 turns 请求（一个 run_id）内 sequence 单调递增。"""

    __slots__ = (
        "protocol_version", "event_id", "run_id", "conversation_id",
        "sequence", "type", "timestamp", "data",
    )

    def __init__(
        self,
        run_id: str,
        conversation_id: str,
        sequence: int,
        type: EventType,
        data: Optional[Dict[str, Any]] = None,
    ):
        self.protocol_version = "v1"
        self.event_id = str(uuid.uuid4())
        self.run_id = run_id
        self.conversation_id = conversation_id
        self.sequence = sequence
        self.type = type
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.data = data or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "event_id": self.event_id,
            "run_id": self.run_id,
            "conversation_id": self.conversation_id,
            "sequence": self.sequence,
            "type": self.type.value,
            "timestamp": self.timestamp,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "EventEnvelope":
        """反序列化（契约测试用）：缺失字段/未知类型必须拒绝。"""
        required = ("protocol_version", "event_id", "run_id", "conversation_id",
                    "sequence", "type", "timestamp", "data")
        missing = [k for k in required if k not in raw]
        if missing:
            raise ValueError(f"事件包缺少字段: {missing}")
        if raw["protocol_version"] != "v1":
            raise ValueError(f"未知协议版本: {raw['protocol_version']}")
        if raw["type"] not in EventType.__members__.values():
            raise ValueError(f"未知事件类型: {raw['type']}")
        envelope = cls(
            run_id=raw["run_id"],
            conversation_id=raw["conversation_id"],
            sequence=raw["sequence"],
            type=EventType(raw["type"]),
            data=raw.get("data"),
        )
        return envelope


# ---------------- 统一错误码 ----------------


class ErrorCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
    CONVERSATION_ACCESS_DENIED = "CONVERSATION_ACCESS_DENIED"
    INTENT_UNSUPPORTED = "INTENT_UNSUPPORTED"
    APPOINTMENT_CONFLICT = "APPOINTMENT_CONFLICT"
    APPOINTMENT_STATE_INVALID = "APPOINTMENT_STATE_INVALID"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    TOOL_FAILED = "TOOL_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# Phase C 领域错误码 -> 公开错误码映射
APPOINTMENT_DOMAIN_ERROR_MAP = {
    "APPOINTMENT_NOT_FOUND": ErrorCode.CONVERSATION_ACCESS_DENIED,  # 归属校验统一为访问拒绝
    "APPOINTMENT_INVALID_STATE": ErrorCode.APPOINTMENT_STATE_INVALID,
    "APPOINTMENT_TIME_INVALID": ErrorCode.INVALID_INPUT,
    "APPOINTMENT_CONFLICT": ErrorCode.APPOINTMENT_CONFLICT,
    "TECHNICIAN_NOT_FOUND": ErrorCode.TOOL_FAILED,
    "TECHNICIAN_UNAVAILABLE": ErrorCode.APPOINTMENT_CONFLICT,
    "IDEMPOTENCY_CONFLICT": ErrorCode.IDEMPOTENCY_CONFLICT,
    "APPOINTMENT_REQUIRED_FIELD": ErrorCode.INVALID_INPUT,
    "APPOINTMENT_PERSISTENCE_FAILED": ErrorCode.INTERNAL_ERROR,
}


def map_appointment_error(domain_code: str) -> ErrorCode:
    return APPOINTMENT_DOMAIN_ERROR_MAP.get(domain_code, ErrorCode.INTERNAL_ERROR)
