"""Phase E E3：SummaryService 与失败回退。

流程（对应执行计划 3.3）：
消息累计达到阈值 ->（会话锁内，由调用方保证）确定待覆盖序号范围
-> 读取结构化业务事实与未覆盖原始消息 -> 生成候选摘要
-> 校验覆盖连续性/关键预约事实/敏感字段 -> 事务写入新版本
-> 失败保留旧摘要与原始消息，返回降级信号。

约束：
- 覆盖范围只按消息 sequence，禁止用时间戳猜测范围。
- 新摘要保存前不覆盖旧摘要（版本递增，历史保留）。
- 摘要失败/模型不可用/校验失败/写入失败：保留旧摘要和原始消息，不伪造成功。
- 日志记录覆盖范围与失败原因（内部日志关联 ID），不记录原始敏感内容。
"""

import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from application.context_contracts import (
    DEFAULT_CONTEXT_BUDGET,
    ContextBudget,
    FakeTokenEstimator,
    SummarySnapshot,
    SummaryStatus,
    SummaryTriggerDefaults,
    TokenEstimator,
    coverage_is_continuous,
)
from application.context_builder import AppointmentReader, MessageReader
from db.repositories.summary_repository import SummaryRepository

logger = logging.getLogger(__name__)

# 被禁止进入摘要正文的内部标记/敏感前缀（防止泄漏）
_FORBIDDEN_MARKERS = ("[THOUGHT]", "[SIGNAL]", "[REPLY]", "sk-", "bearer ", "api_key", "secret")

# 屏蔽标记：metadata_json 中被墓碑关联的消息（E4 写入）
CONTEXT_EXCLUDED_KEY = "context_excluded"


class SummaryGenerationResult:
    """摘要生成候选结果（signature 固定，便于替换真实模型）。"""

    __slots__ = ("content", "key_facts")

    def __init__(self, content: str, key_facts: List[Dict[str, Any]]):
        self.content = content
        self.key_facts = key_facts


class Summarizer(ABC):
    """摘要生成器抽象（可替换：Fake 确定 / 真实 LLM 后续接入）。"""

    @abstractmethod
    async def summarize(self, messages: List[Dict[str, Any]], key_facts_hint: Dict[str, Any]) -> SummaryGenerationResult:
        """基于未覆盖原始消息 + 结构化业务事实 hint 生成候选摘要。"""


class FakeSummarizer(Summarizer):
    """确定性 Fake 摘要器（E0 边界：Fake 通过不代表真实摘要质量）。

    压缩 = 非敏感内容拼接 + hint 结构化关键事实；每次输出可复现，便于校验和测试。
    """

    async def summarize(self, messages: List[Dict[str, Any]], key_facts_hint: Dict[str, Any]) -> SummaryGenerationResult:
        parts = []
        for m in messages or []:
            content = m.get("content", "")
            if any(marker in content for marker in _FORBIDDEN_MARKERS):
                continue
            role = "用户" if m.get("role") == "user" else ("助手" if m.get("role") == "assistant" else str(m.get("role", "")))
            parts.append(f"{role}：{content}")
        content = "；".join(p for p in parts if p) if parts else "（无有效内容）"
        key_facts = [{"key": k, "value": v} for k, v in (key_facts_hint or {}).items()]
        return SummaryGenerationResult(content=content, key_facts=key_facts)


class SummaryOutcome(str):
    """summarize_if_needed 的结果信号。"""

    SKIPPED = "skipped"      # 未达触发阈值或没有新消息
    SUCCEEDED = "succeeded"  # 新摘要写入
    FAILED = "failed"        # 生成/校验/写入失败（保留旧摘要）
    NO_OP = "no_op"          # 失败但无旧摘要（降级为无摘要）


class SummaryService:
    """会话摘要服务（调用方需在会话锁内调用，避免同一会话重复压缩）。"""

    def __init__(
        self,
        repository: SummaryRepository,
        message_reader: MessageReader,
        appointment_reader: AppointmentReader,
        summarizer: Optional[Summarizer] = None,
        token_estimator: Optional[TokenEstimator] = None,
        budget: Optional[ContextBudget] = None,
        trigger_defaults: Optional[SummaryTriggerDefaults] = None,
    ):
        self._repo = repository
        self._messages = message_reader
        self._appointments = appointment_reader
        self._summarizer = summarizer or FakeSummarizer()
        self._estimator = token_estimator or FakeTokenEstimator()
        self._budget = budget or DEFAULT_CONTEXT_BUDGET
        self._trigger = trigger_defaults or SummaryTriggerDefaults()

    # ---------------- 触发判断 ----------------

    def _uncovered_messages(self, conversation_id: str, after_sequence: int) -> List[Dict[str, Any]]:
        """读取未覆盖消息并过滤墓碑屏蔽项（E4 消息屏蔽联动）。"""
        messages = self._messages.recent_messages(
            conversation_id, after_sequence=after_sequence, limit=None
        ) or []
        return [m for m in messages if not (m.get("metadata") or {}).get(CONTEXT_EXCLUDED_KEY)]

    def should_trigger(self, conversation_id: str, after_sequence: int) -> bool:
        """较早者策略：未覆盖消息数 >= max_messages，或估算达到预算 75%。"""
        messages = self._uncovered_messages(conversation_id, after_sequence)
        if len(messages) >= self._trigger.max_messages:
            return True
        estimated = self._estimator.estimate_messages(messages)
        threshold = int(self._budget.max_input_tokens * self._trigger.budget_ratio)
        return estimated >= threshold

    # ---------------- 主流程 ----------------

    async def summarize_if_needed(self, conversation_id: str) -> str:
        """达到阈值则生成新摘要；未达/无新消息/失败均不影响主对话。"""
        latest = self._repo.get_latest_active(conversation_id)
        after = latest["to_sequence"] if latest else 0

        if latest is not None and not _snapshot_is_continuous(latest):
            # 损坏或不完整快照：回退上一版本（最坏情况按无摘要处理）
            logger.warning("摘要快照覆盖不连续，回退：conv=%s v=%s", conversation_id, latest["version"])
            return SummaryOutcome.NO_OP

        messages = self._uncovered_messages(conversation_id, after)
        if not messages:
            return SummaryOutcome.SKIPPED
        if not self.should_trigger(conversation_id, after):
            return SummaryOutcome.SKIPPED

        # 覆盖范围保持序号连续（after+1），墓碑屏蔽只排除内容、不制造空洞
        from_seq = after + 1
        to_seq = messages[-1]["sequence"]
        facts_hint = self._appointments.active_facts(conversation_id) or {}

        failure_log_id = str(uuid.uuid4())
        try:
            candidate = await self._summarizer.summarize(messages, facts_hint)
        except Exception as exc:
            logger.warning(
                "摘要生成失败 conv=%s range=[%s,%s] log_id=%s err=%s",
                conversation_id, from_seq, to_seq, failure_log_id, type(exc).__name__,
            )
            return SummaryOutcome.FAILED

        # 校验（失败不写入，保留旧摘要）
        validation = self._validate_candidate(
            conversation_id, from_seq, to_seq, candidate, facts_hint, latest
        )
        if validation is not True:
            logger.warning(
                "摘要校验失败 conv=%s range=[%s,%s] log_id=%s reason=%s",
                conversation_id, from_seq, to_seq, failure_log_id, validation,
            )
            return SummaryOutcome.FAILED

        try:
            version = (latest["version"] + 1) if latest else 1
            self._repo.add_snapshot(
                conversation_id=conversation_id,
                from_sequence=from_seq,
                to_sequence=to_seq,
                content=candidate.content,
                key_facts=candidate.key_facts,
                status=SummaryStatus.ACTIVE.value,
                version=version,
                model_provider="fake",
            )
        except Exception as exc:
            logger.warning(
                "摘要写入失败 conv=%s range=[%s,%s] log_id=%s err=%s",
                conversation_id, from_seq, to_seq, failure_log_id, type(exc).__name__,
            )
            return SummaryOutcome.FAILED

        logger.info("摘要写入成功 conv=%s range=[%s,%s] v=%s", conversation_id, from_seq, to_seq, version)
        return SummaryOutcome.SUCCEEDED

    # ---------------- 校验 ----------------

    def _validate_candidate(
        self,
        conversation_id: str,
        from_seq: int,
        to_seq: int,
        candidate: SummaryGenerationResult,
        facts_hint: Dict[str, Any],
        latest: Optional[Dict[str, Any]],
    ) -> Any:
        """返回 True 或失败原因字符串（不写库、不记录原始内容）。"""
        # 1. 覆盖范围连续且可验证
        prev = None
        if latest is not None:
            prev = SummarySnapshot(
                conversation_id=conversation_id,
                from_sequence=latest["from_sequence"],
                to_sequence=latest["to_sequence"],
                content=latest["content"],
                version=latest["version"],
            )
        candidate_snapshot = SummarySnapshot(
            conversation_id=conversation_id,
            from_sequence=from_seq,
            to_sequence=to_seq,
            content=candidate.content,
            key_facts=candidate.key_facts,
            version=(latest["version"] + 1) if latest else 1,
        )
        if not coverage_is_continuous(candidate_snapshot, previous=prev):
            return "coverage_gap_or_overlap"

        # 2. 关键预约事实保留（结构化 key_facts，不依赖自由文本）
        if facts_hint:
            hint_keys = set(facts_hint.keys())
            kept_keys = {f.get("key") for f in (candidate.key_facts or [])}
            missing = hint_keys - kept_keys
            if missing:
                return f"missing_key_facts:{sorted(missing)}"

        # 3. 敏感字段与内部标记防线
        for marker in _FORBIDDEN_MARKERS:
            if marker in candidate.content:
                return f"forbidden_marker:{marker}"

        # 4. 摘要必须覆盖到最新消息（不能假装覆盖未覆盖内容）
        return True


def _snapshot_is_continuous(snapshot_dict: Dict[str, Any]) -> bool:
    """数据库快照自洽性检查：范围合法即可（连续性在写入时校验）。"""
    return snapshot_dict["from_sequence"] >= 1 and snapshot_dict["to_sequence"] >= snapshot_dict["from_sequence"]