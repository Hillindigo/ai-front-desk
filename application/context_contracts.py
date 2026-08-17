"""Phase E 上下文契约（E1）：上下文装配、摘要、偏好与知识证据的结构化模型。

设计约束（对应 Phase E 执行计划 1.x/3.x）：
- ContextBuilder / SummaryService / PreferenceService / 咨询工作流共享本文件契约，不再通过自由字典猜字段含义。
- 模型输入与内部审计信息分离：ContextPackage.model_input() 只输出允许公开给工作流的字段。
- Token 估算器必须可替换（决策一）：FakeTokenEstimator 是确定性估算，不冒充供应商精确 Token 数。
- 同一 user_id + preference_type 只保留一个 active 值（决策四：覆盖语义，PUT 替换而非追加）。
- 删除偏好 = 持久化墓碑 + 摘要失效 + 来源消息屏蔽（决策三），本文件提供对应契约与连续性校验。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------- 枚举 ----------------


class PreferenceTypeEnum(str, Enum):
    """统一偏好类型枚举（兼容旧 user_preferences 的 technician/time/service/duration）。"""

    TECHNICIAN = "technician"
    TIME = "time"
    SERVICE = "service"
    DURATION = "duration"


class PreferenceSourceType(str, Enum):
    """偏好来源枚举（决策二：只有明确表达或业务确认的稳定偏好才能进入长期上下文）。"""

    EXPLICIT_MEMORIZE = "explicit_memorize"        # 用户明确表达"以后/通常/请记住"并得到可见确认
    BUSINESS_CONFIRMATION = "business_confirmation"  # 预约/改约等业务流程中明确确认的稳定偏好
    LEGACY_UNVERIFIED = "legacy_unverified"        # 历史数据缺来源/确认时间，可展示但默认不注入


class PreferenceStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class SummaryStatus(str, Enum):
    ACTIVE = "active"          # 唯一有效快照，可进入 ContextPackage
    INVALIDATED = "invalidated"  # 因偏好删除等原因失效：保留用于审计，永不进入 ContextPackage
    FAILED = "failed"          # 生成/校验失败：保留原始消息并使用降级路径


class OmissionReason(str, Enum):
    """来源被裁剪/排除的原因（写入 ContextPackage.omitted_sources 用于测试与日志）。"""

    BUDGET_TRUNCATED = "budget_truncated"
    SOURCE_UNAVAILABLE = "source_unavailable"
    DELETED = "deleted"
    BELOW_THRESHOLD = "below_threshold"
    EXPIRED = "expired"
    UNVERIFIED = "unverified"
    SENSITIVE = "sensitive"
    PREFERENCE_DELETED = "preference_deleted"


class ContextSourceKind(str, Enum):
    """上下文内容分类（3.2：事实/摘要/证据/推断必须明确区分）。"""

    FACT = "fact"          # 数据库实体或用户明确确认
    SUMMARY = "summary"    # 对历史消息的压缩表达
    EVIDENCE = "evidence"  # 知识库且带文档 ID 与分数
    INFERENCE = "inference"  # 模型建议，不得回写为事实


class ExclusionReason(str, Enum):
    """消息 context-exclusion 标记原因（墓碑关联来源消息的屏蔽）。"""

    PREFERENCE_TOMBSTONE = "preference_tombstone"


# ---------------- 领域异常 ----------------


class PreferenceDomainError(Exception):
    """偏好领域错误（稳定错误码，E4 映射到公开 ErrorCode）。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class SummaryDomainError(Exception):
    """摘要领域错误（稳定错误码，E3 内部使用，映射为旁路日志/降级）。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


# ---------------- 预算与默认值 ----------------


@dataclass(frozen=True)
class ContextBudget:
    """配置化上下文预算（决策一）。所有字段必须为正整数。"""

    max_input_tokens: int
    reserved_output_tokens: int
    max_recent_messages: int
    max_evidence_items: int

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"ContextBudget.{name} 必须为正整数，收到 {value!r}")

    @property
    def total_token_cap(self) -> int:
        """本轮模型请求的输入上限（预算内可裁到只剩强制保留项）。"""
        return self.max_input_tokens


DEFAULT_CONTEXT_BUDGET = ContextBudget(
    max_input_tokens=4000,
    reserved_output_tokens=1000,
    max_recent_messages=20,
    max_evidence_items=3,
)


@dataclass(frozen=True)
class SummaryTriggerDefaults:
    """摘要触发默认值（E0 锁定：较早者策略，均可配置）。"""

    max_messages: int = 20
    budget_ratio: float = 0.75  # 下一轮估算输入达到 max_input_tokens 的 75% 时触发

    def __post_init__(self) -> None:
        if self.max_messages <= 0:
            raise ValueError("max_messages 必须为正数")
        if not 0.0 < self.budget_ratio <= 1.0:
            raise ValueError("budget_ratio 必须在 (0, 1] 区间")


# ---------------- Token 估算器 ----------------


class TokenEstimator(ABC):
    """Token 估算器抽象（决策一：必须可替换）。"""

    @abstractmethod
    def estimate_text(self, text: str) -> int:
        """估算单段文本的 Token 数。"""

    @abstractmethod
    def estimate_messages(self, messages: List[Dict[str, str]]) -> int:
        """估算消息列表（role/content dict）的总 Token 数。"""


class FakeTokenEstimator(TokenEstimator):
    """确定性估算（Fake 模式）：字符数 / 4 近似；真实供应商验证单独记录，不冒充精确值。"""

    def estimate_text(self, text: str) -> int:
        return max(1, (len(text) + 3) // 4)

    def estimate_messages(self, messages: List[Dict[str, str]]) -> int:
        return sum(self.estimate_text(f"{m.get('role', '')}:{m.get('content', '')}") for m in messages)


# ---------------- 偏好契约 ----------------


@dataclass(frozen=True)
class PreferenceRecord:
    """偏好记录（1.3：有来源、可管理；同一类型单 active 覆盖语义）。"""

    user_id: str
    preference_type: PreferenceTypeEnum
    preference_value: str
    source: PreferenceSourceType
    source_message_id: Optional[str] = None
    source_appointment_id: Optional[str] = None
    confidence: float = 1.0
    last_confirmed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    is_active: bool = True
    deleted_at: Optional[datetime] = None
    preference_id: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.user_id:
            raise ValueError("user_id 不能为空")
        if not isinstance(self.preference_type, PreferenceTypeEnum):
            raise ValueError(f"preference_type 必须为 PreferenceTypeEnum，收到 {self.preference_type!r}")
        if not self.preference_value:
            raise ValueError("preference_value 不能为空")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence 必须在 [0,1]，收到 {self.confidence!r}")
        if self.source is PreferenceSourceType.LEGACY_UNVERIFIED and self.confidence > 0.5:
            raise ValueError("legacy_unverified 来源不允许高可信度（不静默提升）")
        if self.source is PreferenceSourceType.LEGACY_UNVERIFIED and self.source_message_id is None and self.source_appointment_id is None:
            # 允许：历史数据本身无来源；但 last_confirmed_at 必须为空以示未确认
            if self.last_confirmed_at is not None:
                raise ValueError("legacy_unverified 不允许携带 last_confirmed_at")
        if self.expires_at is not None and self.last_confirmed_at is not None and self.expires_at <= self.last_confirmed_at:
            raise ValueError("expires_at 必须晚于 last_confirmed_at")

    def is_active_now(self, now: Optional[datetime] = None) -> bool:
        """是否可作为当前长期上下文注入（active + 未删除 + 未过期）。"""
        if not self.is_active or self.deleted_at is not None:
            return False
        if self.expires_at is not None:
            if now is None:
                now = _utcnow()
            if self.expires_at <= now:
                return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "preference_id": self.preference_id,
            "user_id": self.user_id,
            "preference_type": self.preference_type.value,
            "preference_value": self.preference_value,
            "source": self.source.value,
            "source_message_id": self.source_message_id,
            "source_appointment_id": self.source_appointment_id,
            "confidence": self.confidence,
            "last_confirmed_at": self.last_confirmed_at.isoformat() if self.last_confirmed_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_active": self.is_active,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }


@dataclass(frozen=True)
class PreferenceTombstone:
    """偏好删除墓碑（决策三第 1 步：不可复用，防止旧缓存/旧摘要/并发读取重新激活）。"""

    user_id: str
    preference_type: PreferenceTypeEnum
    normalized_value: str
    original_preference_id: Optional[int] = None
    source_message_id: Optional[str] = None
    source_appointment_id: Optional[str] = None
    deleted_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.user_id:
            raise ValueError("user_id 不能为空")
        if not isinstance(self.preference_type, PreferenceTypeEnum):
            raise ValueError("preference_type 必须为 PreferenceTypeEnum")
        if not self.normalized_value:
            raise ValueError("normalized_value 不能为空（规范化值防误用）")
        if self.deleted_at is None:
            object.__setattr__(self, "deleted_at", _utcnow())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "preference_type": self.preference_type.value,
            "normalized_value": self.normalized_value,
            "original_preference_id": self.original_preference_id,
            "source_message_id": self.source_message_id,
            "source_appointment_id": self.source_appointment_id,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }


@dataclass(frozen=True)
class MessageExclusion:
    """消息 context-exclusion 标记（决策三第 3 步：原始消息保留，但不再作为摘要/最近消息输入）。"""

    conversation_id: str
    message_id: str
    reason: ExclusionReason
    tombstone_ref: str  # 关联墓碑标识（审计用）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "reason": self.reason.value,
            "tombstone_ref": self.tombstone_ref,
        }


# ---------------- 摘要契约 ----------------


@dataclass(frozen=True)
class SummarySnapshot:
    """会话摘要快照（1.2：可恢复、可追溯；覆盖范围连续且可验证）。"""

    conversation_id: str
    from_sequence: int
    to_sequence: int
    content: str
    key_facts: List[Dict[str, Any]] = field(default_factory=list)
    status: SummaryStatus = SummaryStatus.ACTIVE
    version: int = 1
    model_provider: str = "fake"
    failure_log_id: Optional[str] = None
    summary_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.conversation_id:
            raise ValueError("conversation_id 不能为空")
        if self.from_sequence <= 0 or self.to_sequence < self.from_sequence:
            raise ValueError(
                f"覆盖范围非法: from_sequence={self.from_sequence}, to_sequence={self.to_sequence}"
            )
        if not self.content and self.status is SummaryStatus.ACTIVE:
            raise ValueError("ACTIVE 摘要正文不能为空")
        if self.failure_log_id is not None and self.status is SummaryStatus.ACTIVE:
            raise ValueError("ACTIVE 摘要不能携带 failure_log_id")
        if self.version <= 0:
            raise ValueError(f"version 必须为正数，收到 {self.version}")

    @property
    def is_usable(self) -> bool:
        """唯一有效快照规则：只有 ACTIVE 且覆盖范围完整的摘要可进入 ContextPackage。"""
        return self.status is SummaryStatus.ACTIVE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "conversation_id": self.conversation_id,
            "from_sequence": self.from_sequence,
            "to_sequence": self.to_sequence,
            "content": self.content,
            "key_facts": self.key_facts,
            "status": self.status.value,
            "version": self.version,
            "model_provider": self.model_provider,
            "failure_log_id": self.failure_log_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def coverage_is_continuous(
    snapshot: SummarySnapshot,
    previous: Optional[SummarySnapshot] = None,
    max_sequence: Optional[int] = None,
) -> bool:
    """覆盖范围连续性校验（禁止空洞/重复/跳过消息）。

    - 首份摘要：from_sequence 必须为 1（首个持久化消息序号）。
    - 后续摘要：from_sequence 必须等于上一份的 to_sequence + 1。
    - 存在 max_sequence 时 to_sequence 不得超过最新消息序号。
    """
    if previous is None:
        if snapshot.from_sequence != 1:
            return False
    else:
        if snapshot.from_sequence != previous.to_sequence + 1:
            return False
    if max_sequence is not None and snapshot.to_sequence > max_sequence:
        return False
    return True


# ---------------- 知识证据契约 ----------------


@dataclass(frozen=True)
class RetrievedEvidence:
    """知识检索证据（1.4：带文档 ID/片段/分数/索引版本；低于阈值不得作为事实依据）。"""

    document_id: int
    category: str
    snippet: str
    score: float
    source_version: str
    rank: int

    def __post_init__(self) -> None:
        if self.document_id <= 0:
            raise ValueError(f"document_id 必须为正数，收到 {self.document_id}")
        if not self.snippet:
            raise ValueError("snippet 不能为空")
        if not self.source_version:
            raise ValueError("source_version 不能为空")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "category": self.category,
            "snippet": self.snippet,
            "score": self.score,
            "source_version": self.source_version,
            "rank": self.rank,
        }


# E6 使用：低于该阈值的结果不进入上下文（可配置，E0 固定默认）
KNOWLEDGE_MIN_SCORE_DEFAULT = 0.5


def evidence_passes_threshold(evidence: RetrievedEvidence, min_score: float = KNOWLEDGE_MIN_SCORE_DEFAULT) -> bool:
    return evidence.score >= min_score


# ---------------- ContextPackage ----------------


@dataclass(frozen=True)
class ContextPackage:
    """装配后的结构化上下文（3.1）。模型输入与内部审计信息分离。"""

    conversation_id: str
    user_id: str
    current_input: str
    workflow_state: Dict[str, Any] = field(default_factory=dict)
    appointment_facts: List[Dict[str, Any]] = field(default_factory=list)
    confirmed_preferences: List[PreferenceRecord] = field(default_factory=list)
    summary: Optional[SummarySnapshot] = None
    recent_messages: List[Dict[str, str]] = field(default_factory=list)
    retrieved_evidence: List[RetrievedEvidence] = field(default_factory=list)
    budget: Optional[ContextBudget] = None
    included_sources: List[str] = field(default_factory=list)
    omitted_sources: List[Dict[str, str]] = field(default_factory=list)  # {"source": ..., "reason": ...}
    suppression_metadata: List[Dict[str, Any]] = field(default_factory=list)  # 仅内部规则使用
    model_provider_context: str = "default"  # 目标模型标识（估算器可替换的依据）

    def __post_init__(self) -> None:
        if not self.conversation_id:
            raise ValueError("conversation_id 不能为空")
        if not self.user_id:
            raise ValueError("user_id 不能为空")
        if not self.current_input:
            raise ValueError("current_input 不能为空（当前轮输入不可裁剪）")
        # 敏感内容防线：偏好内容不得包含密钥/内部日志（由写入路径保证，此处做防御性校验）
        for pref in self.confirmed_preferences:
            low = pref.preference_value.lower()
            for forbidden in ("sk-", "bearer ", "api_key", "secret"):
                if forbidden in low:
                    raise ValueError(f"偏好内容包含被禁止的敏感字段: {pref.preference_type}")

    def model_input(self) -> Dict[str, Any]:
        """只包含允许公开给该工作流的字段（审计字段不泄漏到用户文案/模型输入）。"""
        return {
            "current_input": self.current_input,
            "workflow_state": self.workflow_state,
            "appointment_facts": self.appointment_facts,
            "confirmed_preferences": [p.to_dict() for p in self.confirmed_preferences],
            "summary": self.summary.to_dict() if self.summary and self.summary.is_usable else None,
            "recent_messages": self.recent_messages,
            "retrieved_evidence": [e.to_dict() for e in self.retrieved_evidence],
        }

    def to_dict(self) -> Dict[str, Any]:
        """完整审计视图（测试与日志用，不进入用户文案）。"""
        return {
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "current_input": self.current_input,
            "workflow_state": self.workflow_state,
            "appointment_facts": self.appointment_facts,
            "confirmed_preferences": [p.to_dict() for p in self.confirmed_preferences],
            "summary": self.summary.to_dict() if self.summary else None,
            "recent_messages": self.recent_messages,
            "retrieved_evidence": [e.to_dict() for e in self.retrieved_evidence],
            "budget": self.budget.__dict__ if self.budget else None,
            "included_sources": self.included_sources,
            "omitted_sources": self.omitted_sources,
            "suppression_metadata": self.suppression_metadata,
        }