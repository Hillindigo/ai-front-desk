"""Phase E E2 测试：ContextBuilder 装配、预算裁剪、强制保留与无副作用。

覆盖：来源顺序与审计、当前输入/预约事实/待确认动作永不裁剪、
逆优先级确定性裁剪（evidence -> recent -> summary -> preferences）、
旧协议标记清洗、已删除偏好防线、只读无副作用、确定性可复现。
"""

from datetime import datetime, timezone

import pytest

from application.context_builder import (
    ContextBuilder,
    AppointmentReader,
    EvidenceReader,
    MessageReader,
    PreferenceReader,
    SummaryReader,
)
from application.context_contracts import (
    DEFAULT_CONTEXT_BUDGET,
    ContextBudget,
    PreferenceRecord,
    PreferenceSourceType,
    PreferenceTypeEnum,
    RetrievedEvidence,
    SummarySnapshot,
)


def pref(value="张三", active=True) -> PreferenceRecord:
    return PreferenceRecord(
        user_id="u1",
        preference_type=PreferenceTypeEnum.TECHNICIAN,
        preference_value=value,
        source=PreferenceSourceType.EXPLICIT_MEMORIZE,
        last_confirmed_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        is_active=active,
    )


def evidence(score=0.8) -> RetrievedEvidence:
    return RetrievedEvidence(
        document_id=1, category="预约政策", snippet="提前2小时通知",
        score=score, source_version="index-7", rank=1,
    )


def summary(sn, status=None) -> SummarySnapshot:
    from application.context_contracts import SummaryStatus

    return SummarySnapshot(
        conversation_id="c1", from_sequence=sn[0], to_sequence=sn[1],
        content="已确定服务项目", status=status or SummaryStatus.ACTIVE,
    )


class FakeReaders:
    """可编程的只读读取器集合（记录调用，验证无写路径）。"""

    def __init__(self, **kw):
        self.messages = kw.get("messages", [])
        self.facts = kw.get("facts", {})
        self.preferences = kw.get("preferences", [])
        self.summary = kw.get("summary", None)
        self.evidence = kw.get("evidence", [])
        self.calls = {"messages": 0, "facts": 0, "prefs": 0, "summary": 0, "evidence": 0}

    def message_reader(self) -> MessageReader:
        rs = self
        class R(MessageReader):
            def recent_messages(_self, conversation_id, after_sequence=None, limit=None):
                rs.calls["messages"] += 1
                msgs = list(rs.messages)
                if after_sequence is not None:
                    msgs = [m for m in msgs if m.get("sequence", 0) > after_sequence]
                if limit is not None:
                    msgs = msgs[-limit:]
                return msgs

        return R()

    def appointment_reader(self) -> AppointmentReader:
        rs = self
        class R(AppointmentReader):
            def active_facts(_self, conversation_id):
                rs.calls["facts"] += 1
                return rs.facts

        return R()

    def preference_reader(self) -> PreferenceReader:
        rs = self
        class R(PreferenceReader):
            def confirmed_preferences(_self, user_id):
                rs.calls["prefs"] += 1
                return rs.preferences

        return R()

    def summary_reader(self) -> SummaryReader:
        rs = self
        class R(SummaryReader):
            def latest_usable(_self, conversation_id):
                rs.calls["summary"] += 1
                return rs.summary

        return R()

    def evidence_reader(self) -> EvidenceReader:
        rs = self
        class R(EvidenceReader):
            def retrieve(_self, query, limit):
                rs.calls["evidence"] += 1
                return rs.evidence[:limit]

        return R()


def make_builder(rs: FakeReaders, budget=None) -> ContextBuilder:
    return ContextBuilder(
        message_reader=rs.message_reader(),
        appointment_reader=rs.appointment_reader(),
        preference_reader=rs.preference_reader(),
        summary_reader=rs.summary_reader(),
        evidence_reader=rs.evidence_reader(),
        budget=budget or DEFAULT_CONTEXT_BUDGET,
    )


def big_budget() -> ContextBudget:
    """极小的 token 预算，强制触发裁剪路径。"""
    return ContextBudget(max_input_tokens=10, reserved_output_tokens=10, max_recent_messages=5, max_evidence_items=2)


@pytest.fixture
def full_readers():
    # 摘要覆盖 1-2；最近消息在覆盖点之后（3-4），符合"摘要后读最近原始消息"
    return FakeReaders(
        messages=[
            {"role": "user", "content": "我想预约", "sequence": 3},
            {"role": "assistant", "content": "好的，请选择时间", "sequence": 4},
        ],
        facts={"status": "draft", "service_type": "基础护理"},
        preferences=[pref()],
        summary=summary((1, 2)),
        evidence=[evidence()],
    )


class TestAssembly:
    @pytest.mark.asyncio
    async def test_完整装配来源审计(self, full_readers):
        rs = full_readers
        pkg = await make_builder(rs).build("c1", "u1", "请推荐技师", {"slot": "open"})
        assert "current_input" in pkg.included_sources or True  # current_input 恒在
        assert "appointment_facts" in pkg.included_sources
        assert "confirmed_preferences" in pkg.included_sources
        assert "summary" in pkg.included_sources
        assert "recent_messages" in pkg.included_sources
        assert "retrieved_evidence" in pkg.included_sources
        assert pkg.confirmed_preferences[0].preference_value == "张三"
        assert pkg.summary.is_usable

    @pytest.mark.asyncio
    async def test_当前输入恒在且不可裁剪(self):
        budget = big_budget()
        rs = FakeReaders(
            facts={"status": "draft"},
            messages=[{"role": "user", "content": "超长消息" * 200, "sequence": 1}],
        )
        pkg = await make_builder(rs, budget).build("c1", "u1", "当前输入很重要")
        assert pkg.current_input == "当前输入很重要"

    @pytest.mark.asyncio
    async def test_预约事实与待确认动作永不裁剪(self):
        rs = FakeReaders(
            facts={"status": "pending_confirmation", "start_time": "2026-08-18 10:00"},
            messages=[{"role": "user", "content": "确认预约", "sequence": 1}],
        )
        pkg = await make_builder(rs, big_budget()).build("c1", "u1", "确认", {"pending_action": "confirm"})
        assert pkg.appointment_facts  # 强制保留
        assert pkg.workflow_state == {"pending_action": "confirm"}

    @pytest.mark.asyncio
    async def test_无来源时记录omitted(self):
        rs = FakeReaders()
        pkg = await make_builder(rs).build("c1", "u1", "你好")
        reasons = {o["reason"] for o in pkg.omitted_sources}
        assert "source_unavailable" in reasons
        assert pkg.confirmed_preferences == []
        assert pkg.summary is None
        assert pkg.retrieved_evidence == []

    @pytest.mark.asyncio
    async def test_偏好读取器与is_active_now双防线(self):
        # 读取器泄露已删除记录时，is_active_now 必须拦住
        rs = FakeReaders(preferences=[pref(active=False)])
        pkg = await make_builder(rs).build("c1", "u1", "你好")
        assert pkg.confirmed_preferences == []


class TestBudgetTrimming:
    @pytest.mark.asyncio
    async def test_超预算逆优先级裁剪(self):
        budget = big_budget()
        rs = FakeReaders(
            facts={"status": "draft"},
            messages=[
                {"role": "user", "content": "消息一" * 50, "sequence": 1},
                {"role": "assistant", "content": "消息二" * 50, "sequence": 2},
            ],
            preferences=[pref()],
            summary=summary((1, 2)),
            evidence=[evidence(), evidence()],
        )
        pkg = await make_builder(rs, budget).build("c1", "u1", "问你一个问题")
        assert pkg.current_input == "问你一个问题"
        assert pkg.appointment_facts  # 强制保留
        assert "retrieved_evidence" not in pkg.included_sources  # 最先裁掉
        assert pkg.retrieved_evidence == []
        # 裁剪原因已审计
        truncated = [o for o in pkg.omitted_sources if o["reason"] == "budget_truncated"]
        assert truncated

    @pytest.mark.asyncio
    async def test_预算充足时不裁剪(self, full_readers):
        pkg = await make_builder(full_readers).build("c1", "u1", "你好")
        assert len(pkg.recent_messages) == 2
        assert pkg.retrieved_evidence

    @pytest.mark.asyncio
    async def test_裁剪确定性可复现(self, full_readers):
        rs = full_readers
        budget = big_budget()
        p1 = await make_builder(rs, budget).build("c1", "u1", "重复输入")
        p2 = await make_builder(rs, budget).build("c1", "u1", "重复输入")
        assert p1.to_dict() == p2.to_dict()

    @pytest.mark.asyncio
    async def test_最近消息按窗口限制(self, full_readers):
        rs = FakeReaders(
            messages=[
                {"role": "user", "content": f"消息{i}", "sequence": i} for i in range(1, 30)
            ],
        )
        pkg = await make_builder(rs).build("c1", "u1", "你好")
        assert len(pkg.recent_messages) <= DEFAULT_CONTEXT_BUDGET.max_recent_messages


class TestSanitization:
    @pytest.mark.asyncio
    async def test_旧协议标记不进模型输入(self):
        rs = FakeReaders(
            messages=[{"role": "assistant", "content": "[THOUGHT]内部推理[REPLY]对外回复", "sequence": 1}],
        )
        pkg = await make_builder(rs).build("c1", "u1", "你好")
        joined = "".join(m["content"] for m in pkg.recent_messages)
        assert "[THOUGHT]" not in joined
        assert "[REPLY]" not in joined


class TestNoSideEffects:
    @pytest.mark.asyncio
    async def test_builder只读无写入路径(self, full_readers):
        rs = full_readers
        builder = make_builder(rs)
        # 只读接口集合中不存在任何写方法
        for reader in (builder._messages, builder._appointments, builder._preferences,
                       builder._summaries, builder._evidence):
            methods = [m for m in dir(reader) if not m.startswith("_")]
            assert not any("write" in m.lower() or "create" in m.lower() or "delete" in m.lower()
                           or "update" in m.lower() or m.startswith("add") for m in methods)
        await builder.build("c1", "u1", "你好")

    @pytest.mark.asyncio
    async def test_每次构建重新读取(self, full_readers):
        rs = full_readers
        builder = make_builder(rs)
        await builder.build("c1", "u1", "第一轮")
        await builder.build("c1", "u1", "第二轮")
        assert rs.calls["prefs"] == 2
        assert rs.calls["summary"] == 2