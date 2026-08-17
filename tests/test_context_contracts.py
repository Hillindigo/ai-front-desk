"""Phase E E1 契约测试：上下文/摘要/偏好/证据结构化模型。

覆盖：空输入拒绝、预算校验、偏好来源与可信度边界、单 active 覆盖语义契约、
摘要覆盖连续性、摘要失效不进模型输入、证据阈值、墓碑契约、Token 估算器可替换性。
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.context_contracts import (
    DEFAULT_CONTEXT_BUDGET,
    KNOWLEDGE_MIN_SCORE_DEFAULT,
    ContextBudget,
    ContextPackage,
    ExclusionReason,
    FakeTokenEstimator,
    MessageExclusion,
    OmissionReason,
    PreferenceDomainError,
    PreferenceRecord,
    PreferenceSourceType,
    PreferenceStatus,
    PreferenceTombstone,
    PreferenceTypeEnum,
    RetrievedEvidence,
    SummaryDomainError,
    SummarySnapshot,
    SummaryStatus,
    SummaryTriggerDefaults,
    TokenEstimator,
    coverage_is_continuous,
    evidence_passes_threshold,
)


def make_pref(
    ptype=PreferenceTypeEnum.TECHNICIAN,
    value="张三",
    source=PreferenceSourceType.EXPLICIT_MEMORIZE,
    **kw,
) -> PreferenceRecord:
    defaults = dict(
        user_id="u1",
        preference_type=ptype,
        preference_value=value,
        source=source,
        last_confirmed_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )
    defaults.update(kw)
    return PreferenceRecord(**defaults)


# ---------------- ContextPackage 与预算 ----------------


class TestContextPackage:
    def test_空输入被拒绝(self):
        with pytest.raises(ValueError):
            ContextPackage(conversation_id="c1", user_id="u1", current_input="")

    def test_缺少会话或用户归属被拒绝(self):
        with pytest.raises(ValueError):
            ContextPackage(conversation_id="", user_id="u1", current_input="你好")
        with pytest.raises(ValueError):
            ContextPackage(conversation_id="c1", user_id="", current_input="你好")

    def test_model_input_不含审计字段(self):
        pkg = ContextPackage(
            conversation_id="c1", user_id="u1", current_input="请推荐技师",
            included_sources=["preference"],
            omitted_sources=[{"source": "summary", "reason": OmissionReason.BUDGET_TRUNCATED.value}],
            suppression_metadata=[{"user_id": "u1", "type": "exclusion"}],
        )
        mi = pkg.model_input()
        assert "included_sources" not in mi
        assert "omitted_sources" not in mi
        assert "suppression_metadata" not in mi
        assert "budget" not in mi
        # 审计信息仍在完整视图中
        full = pkg.to_dict()
        assert full["included_sources"] == ["preference"]

    def test_超预算时预算字段可审计(self):
        pkg = ContextPackage(
            conversation_id="c1", user_id="u1", current_input="你好",
            budget=DEFAULT_CONTEXT_BUDGET,
        )
        assert pkg.budget.max_input_tokens == 4000

    def test_偏好内容敏感字段防线(self):
        with pytest.raises(ValueError):
            ContextPackage(
                conversation_id="c1", user_id="u1", current_input="你好",
                confirmed_preferences=[make_pref(value="sk-abc123")],
            )
        with pytest.raises(ValueError):
            ContextPackage(
                conversation_id="c1", user_id="u1", current_input="你好",
                confirmed_preferences=[make_pref(value="Bearer token123")],
            )


class TestContextBudget:
    def test_默认预算为正(self):
        b = DEFAULT_CONTEXT_BUDGET
        assert b.max_input_tokens > 0
        assert b.reserved_output_tokens > 0
        assert b.max_recent_messages > 0
        assert b.max_evidence_items > 0

    def test_非法预算被拒绝(self):
        with pytest.raises(ValueError):
            ContextBudget(0, 1000, 20, 3)
        with pytest.raises(ValueError):
            ContextBudget(4000, -1, 20, 3)
        with pytest.raises(ValueError):
            ContextBudget(4000, 1000, 0, 3)

    def test_触发默认值较早者策略(self):
        t = SummaryTriggerDefaults()
        assert t.max_messages == 20
        assert t.budget_ratio == 0.75
        with pytest.raises(ValueError):
            SummaryTriggerDefaults(max_messages=0)
        with pytest.raises(ValueError):
            SummaryTriggerDefaults(budget_ratio=1.5)


# ---------------- 偏好契约 ----------------


class TestPreferenceRecord:
    def test_合法记录可序列化(self):
        rec = make_pref()
        d = rec.to_dict()
        assert d["user_id"] == "u1"
        assert d["preference_type"] == "technician"
        assert d["source"] == "explicit_memorize"

    def test_缺失类型或值被拒绝(self):
        with pytest.raises(ValueError):
            PreferenceRecord(user_id="u1", preference_type="technician", preference_value="x", source=PreferenceSourceType.EXPLICIT_MEMORIZE)
        with pytest.raises(ValueError):
            make_pref(value="")

    def test_legacy_unverified_不允许高可信(self):
        with pytest.raises(ValueError):
            make_pref(source=PreferenceSourceType.LEGACY_UNVERIFIED, confidence=0.9)

    def test_legacy_unverified_不允许携带确认时间(self):
        with pytest.raises(ValueError):
            make_pref(
                source=PreferenceSourceType.LEGACY_UNVERIFIED,
                confidence=0.3,
                last_confirmed_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
            )

    def test_过期偏好不可注入(self):
        rec = make_pref(
            expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_confirmed_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
        )
        assert rec.is_active_now() is False

    def test_删除偏好不可注入(self):
        rec = make_pref(is_active=False, deleted_at=datetime(2026, 8, 17, tzinfo=timezone.utc))
        assert rec.is_active_now() is False

    def test_active_偏好可注入(self):
        rec = make_pref()
        assert rec.is_active_now() is True

    def test_单active覆盖语义契约(self):
        # 决策四：同一 user_id+preference_type 只允许一个 active 值。
        # 覆盖为原子停用旧值+写入新值；并存偏好不在本阶段支持。
        rec1 = make_pref(value="张三")
        rec2 = make_pref(value="李四")
        rec2 = PreferenceRecord(
            user_id=rec2.user_id, preference_type=rec2.preference_type,
            preference_value=rec2.preference_value, source=rec2.source,
            last_confirmed_at=rec2.last_confirmed_at, is_active=rec2.is_active,
        )
        # 模拟覆盖：旧值停用、新值激活（E4 Service 保证原子性）
        rec1 = PreferenceRecord(
            user_id=rec1.user_id, preference_type=rec1.preference_type,
            preference_value=rec1.preference_value, source=rec1.source,
            last_confirmed_at=rec1.last_confirmed_at, is_active=False,
        )
        assert rec1.is_active is False
        assert rec2.is_active is True
        assert rec1.preference_type is rec2.preference_type
        assert rec1.preference_value != rec2.preference_value

    def test_无效置信度被拒绝(self):
        with pytest.raises(ValueError):
            make_pref(confidence=1.5)


class TestPreferenceTombstone:
    def test_墓碑必填规范化值(self):
        with pytest.raises(ValueError):
            PreferenceTombstone(user_id="u1", preference_type=PreferenceTypeEnum.TECHNICIAN, normalized_value="")
        with pytest.raises(ValueError):
            PreferenceTombstone(user_id="u1", preference_type=PreferenceTypeEnum.TECHNICIAN, normalized_value=None)  # type: ignore

    def test_墓碑自动记录删除时间(self):
        t = PreferenceTombstone(
            user_id="u1", preference_type=PreferenceTypeEnum.TECHNICIAN,
            normalized_value="张三", original_preference_id=7,
            source_message_id="m9",
        )
        assert t.deleted_at is not None
        d = t.to_dict()
        assert d["original_preference_id"] == 7
        assert d["source_message_id"] == "m9"

    def test_墓碑不可直接复用为active(self):
        t = PreferenceTombstone(user_id="u1", preference_type=PreferenceTypeEnum.TECHNICIAN, normalized_value="张三")
        # 墓碑不是 PreferenceRecord，防旧缓存重新激活：类型不同且无 is_active 语义
        assert not hasattr(t, "is_active")


class TestMessageExclusion:
    def test_屏蔽标记契约(self):
        ex = MessageExclusion(
            conversation_id="c1", message_id="m3",
            reason=ExclusionReason.PREFERENCE_TOMBSTONE, tombstone_ref="t-1",
        )
        d = ex.to_dict()
        assert d["reason"] == "preference_tombstone"
        assert d["tombstone_ref"] == "t-1"


# ---------------- 摘要契约 ----------------


class TestSummarySnapshot:
    def test_非法覆盖范围被拒绝(self):
        with pytest.raises(ValueError):
            SummarySnapshot(conversation_id="c1", from_sequence=0, to_sequence=5, content="x")
        with pytest.raises(ValueError):
            SummarySnapshot(conversation_id="c1", from_sequence=5, to_sequence=3, content="x")

    def test_active摘要正文不能为空(self):
        with pytest.raises(ValueError):
            SummarySnapshot(conversation_id="c1", from_sequence=1, to_sequence=3, content="")

    def test_active摘要不能带失败日志ID(self):
        with pytest.raises(ValueError):
            SummarySnapshot(conversation_id="c1", from_sequence=1, to_sequence=3, content="x", failure_log_id="log-1")

    def test_failed摘要可带失败日志ID(self):
        s = SummarySnapshot(
            conversation_id="c1", from_sequence=1, to_sequence=3, content="",
            status=SummaryStatus.FAILED, failure_log_id="log-1",
        )
        assert s.is_usable is False

    def test_invalidated摘要不可用(self):
        s = SummarySnapshot(
            conversation_id="c1", from_sequence=1, to_sequence=3, content="旧摘要",
            status=SummaryStatus.INVALIDATED,
        )
        assert s.is_usable is False

    def test_invalidated摘要不进model_input(self):
        s = SummarySnapshot(
            conversation_id="c1", from_sequence=1, to_sequence=3, content="旧摘要",
            status=SummaryStatus.INVALIDATED,
        )
        pkg = ContextPackage(
            conversation_id="c1", user_id="u1", current_input="你好", summary=s,
        )
        assert pkg.model_input()["summary"] is None  # 失效摘要永不进入模型输入

    def test_版本单调与历史保留(self):
        s1 = SummarySnapshot(conversation_id="c1", from_sequence=1, to_sequence=10, content="v1", version=1)
        s2 = SummarySnapshot(conversation_id="c1", from_sequence=11, to_sequence=20, content="v2", version=2)
        assert s2.version > s1.version
        assert coverage_is_continuous(s2, previous=s1)


class TestCoverageContinuity:
    def test_首份摘要必须从序号1开始(self):
        s = SummarySnapshot(conversation_id="c1", from_sequence=3, to_sequence=5, content="x")
        assert coverage_is_continuous(s) is False

    def test_首份摘要从1开始合法(self):
        s = SummarySnapshot(conversation_id="c1", from_sequence=1, to_sequence=5, content="x")
        assert coverage_is_continuous(s) is True

    def test_空洞被拒绝(self):
        s1 = SummarySnapshot(conversation_id="c1", from_sequence=1, to_sequence=5, content="v1")
        s2 = SummarySnapshot(conversation_id="c1", from_sequence=7, to_sequence=10, content="v2")
        assert coverage_is_continuous(s2, previous=s1) is False  # 6 被跳过

    def test_重复覆盖被拒绝(self):
        s1 = SummarySnapshot(conversation_id="c1", from_sequence=1, to_sequence=5, content="v1")
        s2 = SummarySnapshot(conversation_id="c1", from_sequence=5, to_sequence=9, content="v2")
        assert coverage_is_continuous(s2, previous=s1) is False  # 5 重复

    def test_不能覆盖未持久化消息(self):
        s = SummarySnapshot(conversation_id="c1", from_sequence=1, to_sequence=10, content="x")
        assert coverage_is_continuous(s, max_sequence=8) is False  # 最新消息只到 8


# ---------------- 知识证据契约 ----------------


class TestRetrievedEvidence:
    def test_字段完整可序列化(self):
        ev = RetrievedEvidence(
            document_id=12, category="预约政策", snippet="提前2小时通知",
            score=0.82, source_version="index-7", rank=1,
        )
        d = ev.to_dict()
        assert d["document_id"] == 12
        assert d["source_version"] == "index-7"

    def test_非法文档ID和空片段被拒绝(self):
        with pytest.raises(ValueError):
            RetrievedEvidence(document_id=0, category="x", snippet="y", score=0.5, source_version="v", rank=1)
        with pytest.raises(ValueError):
            RetrievedEvidence(document_id=1, category="x", snippet="", score=0.5, source_version="v", rank=1)
        with pytest.raises(ValueError):
            RetrievedEvidence(document_id=1, category="x", snippet="y", score=0.5, source_version="", rank=1)

    def test_低于阈值不能作为事实依据(self):
        low = RetrievedEvidence(document_id=1, category="x", snippet="y", score=0.2, source_version="v", rank=1)
        high = RetrievedEvidence(document_id=1, category="x", snippet="y", score=0.8, source_version="v", rank=1)
        assert evidence_passes_threshold(low) is False
        assert evidence_passes_threshold(high) is True
        assert KNOWLEDGE_MIN_SCORE_DEFAULT > 0


# ---------------- Token 估算器 ----------------


class TestTokenEstimator:
    def test_fake估算确定性且可替换(self):
        est = FakeTokenEstimator()
        assert est.estimate_text("你好世界") == est.estimate_text("你好世界")
        assert est.estimate_text("") >= 1
        msgs = [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "世界"}]
        assert est.estimate_messages(msgs) > 0

    def test_抽象接口可注入自定义实现(self):
        class FixedEstimator(TokenEstimator):
            def estimate_text(self, text: str) -> int:
                return 100

            def estimate_messages(self, messages) -> int:
                return 100 * len(messages)

        assert FixedEstimator().estimate_text("任意文本") == 100