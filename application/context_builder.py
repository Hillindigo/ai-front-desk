"""Phase E E2：ContextBuilder 与预算裁剪。

设计约束（对应 Phase E 执行计划 1.1/3.2/0.4 决策一）：
- 只读、无副作用：不创建长期数据库 Session、不写库、不调用其他 Agent。
- 数据来自注入的只读读取器（E5 在 application 层接入真实 Repository / 领域服务）。
- 来源优先级固定；任何裁剪都不能删除当前轮输入、活跃预约状态或待确认动作。
- Token 估算器可替换；裁剪策略确定性：相同输入+数据库状态 → 相同 ContextPackage。
"""

from abc import ABC, abstractmethod
import logging
from typing import Any, Dict, List, Optional

from application.context_contracts import (
    DEFAULT_CONTEXT_BUDGET,
    ContextBudget,
    ContextPackage,
    FakeTokenEstimator,
    OmissionReason,
    PreferenceRecord,
    RetrievedEvidence,
    SummarySnapshot,
    TokenEstimator,
)
from application.events import clean_token

logger = logging.getLogger(__name__)


# ---------------- 只读数据读取器接口 ----------------


class MessageReader(ABC):
    """读取持久化消息（只读，不含写入）。"""

    @abstractmethod
    def recent_messages(
        self,
        conversation_id: str,
        after_sequence: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """按 sequence 顺序返回消息（role/content/metadata_json）。"""


class AppointmentReader(ABC):
    """读取预约/草稿业务事实（以 Phase C 领域数据为事实来源）。"""

    @abstractmethod
    def active_facts(self, conversation_id: str) -> Dict[str, Any]:
        """返回当前会话的预约草稿/待确认/已确认结构化事实。空则返回 {}。"""


class PreferenceReader(ABC):
    """读取长期偏好（只读；删除/墓碑过滤由实现保证）。"""

    @abstractmethod
    def confirmed_preferences(self, user_id: str) -> List[PreferenceRecord]:
        """返回未删除、未过期、来源可信的 active 偏好。"""


class SummaryReader(ABC):
    """读取最新有效摘要。"""

    @abstractmethod
    def latest_usable(self, conversation_id: str) -> Optional[SummarySnapshot]:
        """返回最新 ACTIVE 摘要；无则 None（失败的摘要由调用方降级）。"""


class EvidenceReader(ABC):
    """知识检索读取器（通过阈值的结果才能返回；实现可为异步）。"""

    @abstractmethod
    async def retrieve(self, query: str, limit: int) -> List[RetrievedEvidence]:
        """返回已过阈值的证据列表，无依据返回空列表。"""


# ---------------- 裁剪顺序 ----------------


# 来源优先级：0 为最高（不可裁剪），数值越大越先被裁剪
PRIORITY_ORDER = [
    "current_input",        # 0 强制保留
    "appointment_facts",    # 1 强制保留（活跃预约/待确认动作）
    "workflow_state",       # 2 强制保留（待确认动作）
    "confirmed_preferences",  # 3
    "summary",              # 4
    "recent_messages",      # 5
    "retrieved_evidence",   # 6 最先裁剪
]


class ContextBuilder:
    """统一上下文装配器（E2）。"""

    def __init__(
        self,
        message_reader: MessageReader,
        appointment_reader: AppointmentReader,
        preference_reader: PreferenceReader,
        summary_reader: SummaryReader,
        evidence_reader: EvidenceReader,
        token_estimator: Optional[TokenEstimator] = None,
        budget: Optional[ContextBudget] = None,
    ):
        self._messages = message_reader
        self._appointments = appointment_reader
        self._preferences = preference_reader
        self._summaries = summary_reader
        self._evidence = evidence_reader
        self._estimator = token_estimator or FakeTokenEstimator()
        self._budget = budget or DEFAULT_CONTEXT_BUDGET

    # ---------------- 构建 ----------------

    async def build(
        self,
        conversation_id: str,
        user_id: str,
        current_input: str,
        workflow_state: Optional[Dict[str, Any]] = None,
    ) -> ContextPackage:
        """按固定顺序装配并裁剪，输出可审计的 ContextPackage。"""
        package = await self._collect(conversation_id, user_id, current_input, workflow_state or {})
        return self._apply_budget(package)

    async def _collect(
        self,
        conversation_id: str,
        user_id: str,
        current_input: str,
        workflow_state: Dict[str, Any],
    ) -> ContextPackage:
        included: List[str] = []
        omitted: List[Dict[str, str]] = []

        # 1. 预约/草稿事实（强制保留）
        appointment_facts = self._as_list(self._appointments.active_facts(conversation_id))
        if appointment_facts:
            included.append("appointment_facts")
        else:
            omitted.append({"source": "appointment_facts", "reason": OmissionReason.SOURCE_UNAVAILABLE.value})

        # 2. 长期偏好（未删除/未过期/来源可信）
        confirmed_preferences: List[PreferenceRecord] = []
        for pref in self._preferences.confirmed_preferences(user_id):
            if pref.is_active_now():
                confirmed_preferences.append(pref)
        if confirmed_preferences:
            included.append("confirmed_preferences")
        else:
            omitted.append({"source": "confirmed_preferences", "reason": OmissionReason.SOURCE_UNAVAILABLE.value})

        # 3. 最新有效摘要（失败/失效则跳过，不阻断）
        summary = self._summaries.latest_usable(conversation_id)
        if summary is not None:
            included.append("summary")
        else:
            omitted.append({"source": "summary", "reason": OmissionReason.SOURCE_UNAVAILABLE.value})

        # 4. 最近原始消息（默认窗口；含旧协议标记清洗，防止历史残留泄漏）
        recent_messages = self._sanitize_messages(
            self._messages.recent_messages(
                conversation_id,
                after_sequence=summary.to_sequence if summary else None,
                limit=self._budget.max_recent_messages,
            )
        )
        if recent_messages:
            included.append("recent_messages")
        else:
            omitted.append({"source": "recent_messages", "reason": OmissionReason.SOURCE_UNAVAILABLE.value})

        # 5. 知识证据（读取器已保证通过阈值；数量上限由预算约束）
        retrieved_evidence = await self._evidence.retrieve(current_input, self._budget.max_evidence_items)
        if retrieved_evidence:
            included.append("retrieved_evidence")
        else:
            omitted.append({"source": "retrieved_evidence", "reason": OmissionReason.BELOW_THRESHOLD.value})

        return ContextPackage(
            conversation_id=conversation_id,
            user_id=user_id,
            current_input=current_input,
            workflow_state=workflow_state,
            appointment_facts=appointment_facts,
            confirmed_preferences=confirmed_preferences,
            summary=summary,
            recent_messages=recent_messages,
            retrieved_evidence=retrieved_evidence,
            budget=self._budget,
            included_sources=included,
            omitted_sources=omitted,
        )

    def _apply_budget(self, package: ContextPackage) -> ContextPackage:
        """确定性裁剪：逆优先级裁减，强制保留项永不删除。"""
        estimate = self._pack_tokens(package)
        if estimate <= self._budget.max_input_tokens:
            return package

        # 裁剪顺序：evidence -> recent_messages -> summary -> preferences
        # （appointment_facts / workflow_state / current_input 为强制保留项）
        omitted = list(package.omitted_sources)
        included = list(package.included_sources)

        # 1) 知识证据（可整体裁掉，数量本身受 max_evidence_items 限制）
        if package.retrieved_evidence and estimate > self._budget.max_input_tokens:
            estimate -= self._estimate_evidence(package.retrieved_evidence)
            omitted.append({"source": "retrieved_evidence", "reason": OmissionReason.BUDGET_TRUNCATED.value})
            package = _replace(package, retrieved_evidence=[], included_sources=_drop(included, "retrieved_evidence"))

        # 2) 最近消息（从最旧开始裁，保留当前轮相关的最新消息）
        if package.recent_messages and estimate > self._budget.max_input_tokens:
            kept, dropped = self._trim_recent_messages(package.recent_messages, package, estimate)
            if dropped:
                estimate = self._pack_tokens(_replace(package, recent_messages=kept))
                omitted.append({"source": "recent_messages", "reason": OmissionReason.BUDGET_TRUNCATED.value})
            package = _replace(package, recent_messages=kept)

        # 3) 摘要（整体降级为不可用：宁可丢摘要也不丢强制事实）
        if package.summary is not None and estimate > self._budget.max_input_tokens:
            estimate -= self._estimator.estimate_text(package.summary.content)
            omitted.append({"source": "summary", "reason": OmissionReason.BUDGET_TRUNCATED.value})
            package = _replace(package, summary=None, included_sources=_drop(included, "summary"))

        # 4) 偏好（最后裁）
        if package.confirmed_preferences and estimate > self._budget.max_input_tokens:
            omitted.append({"source": "confirmed_preferences", "reason": OmissionReason.BUDGET_TRUNCATED.value})
            package = _replace(
                package,
                confirmed_preferences=[],
                included_sources=_drop(included, "confirmed_preferences"),
            )

        if estimate > self._budget.max_input_tokens and not omitted:
            omitted.append({"source": "context", "reason": OmissionReason.BUDGET_TRUNCATED.value})

        return _replace(package, omitted_sources=omitted)

    # ---------------- 工具 ----------------

    def _pack_tokens(self, package: ContextPackage) -> int:
        total = self._estimator.estimate_text(package.current_input)
        for fact in package.appointment_facts:
            total += self._estimator.estimate_text(str(fact))
        total += self._estimator.estimate_messages(package.recent_messages)
        if package.summary is not None:
            total += self._estimator.estimate_text(package.summary.content)
        total += self._estimate_evidence(package.retrieved_evidence)
        for pref in package.confirmed_preferences:
            total += self._estimator.estimate_text(pref.preference_value)
        return total

    def _estimate_evidence(self, evidence: List[RetrievedEvidence]) -> int:
        return sum(self._estimator.estimate_text(e.snippet) for e in evidence)

    def _trim_recent_messages(self, messages, package, estimate) -> tuple:
        """从最旧开始逐条裁减，直到不超预算；至少保留最近 1 条。"""
        kept = list(messages)
        while len(kept) > 1 and estimate > self._budget.max_input_tokens:
            dropped_msg = kept.pop(0)
            estimate -= self._estimator.estimate_text(f"{dropped_msg.get('role', '')}:{dropped_msg.get('content', '')}")
        return kept, (len(messages) - len(kept))

    @staticmethod
    def _sanitize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """清洗旧协议标记（[THOUGHT]/[SIGNAL] 等），防止历史残留泄漏到模型输入。"""
        cleaned: List[Dict[str, str]] = []
        for m in messages or []:
            text = clean_token(m.get("content", ""))
            if text is None:
                text = ""
            cleaned.append({"role": m.get("role", "user"), "content": text})
        return cleaned

    @staticmethod
    def _as_list(value: Any) -> List[Dict[str, Any]]:
        if not value:
            return []
        if isinstance(value, list):
            return value
        return [value]


def _replace(package: ContextPackage, **changes) -> ContextPackage:
    """frozen dataclass 的字段替换构造。"""
    fields = {
        "conversation_id": package.conversation_id,
        "user_id": package.user_id,
        "current_input": package.current_input,
        "workflow_state": package.workflow_state,
        "appointment_facts": package.appointment_facts,
        "confirmed_preferences": package.confirmed_preferences,
        "summary": package.summary,
        "recent_messages": package.recent_messages,
        "retrieved_evidence": package.retrieved_evidence,
        "budget": package.budget,
        "included_sources": package.included_sources,
        "omitted_sources": package.omitted_sources,
        "suppression_metadata": package.suppression_metadata,
    }
    fields.update(changes)
    return ContextPackage(**fields)


def _drop(items: List[str], name: str) -> List[str]:
    return [i for i in items if i != name]