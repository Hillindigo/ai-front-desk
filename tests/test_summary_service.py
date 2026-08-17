"""Phase E E3 测试：摘要触发、版本递增、覆盖连续性、失败回退与重启恢复。

覆盖：较早者触发策略、sequence 覆盖范围、旧摘要保留、生成/校验/写入失败降级、
墓碑屏蔽消息不进入摘要、invalidate 联动、服务重启恢复。
"""

from datetime import datetime, timezone

import pytest

from application.context_builder import AppointmentReader, MessageReader
from application.context_contracts import (
    DEFAULT_CONTEXT_BUDGET,
    ContextBudget,
    PreferenceRecord,
    PreferenceSourceType,
    PreferenceTypeEnum,
    SummarySnapshot,
    SummaryStatus,
    SummaryTriggerDefaults,
)
from db.repositories.summary_repository import SummaryRepository
from services.summary_service import (
    CONTEXT_EXCLUDED_KEY,
    FakeSummarizer,
    Summarizer,
    SummaryGenerationResult,
    SummaryOutcome,
    SummaryService,
)


def make_message(role, content, sequence, metadata=None):
    return {"conversation_id": "c1", "role": role, "content": content,
            "sequence": sequence, "metadata": metadata}


class MemoryMessageReader(MessageReader):
    def __init__(self, messages):
        self.messages = list(messages)

    def recent_messages(self, conversation_id, after_sequence=None, limit=None):
        msgs = [m for m in self.messages if m.get("sequence", 0) > (after_sequence or 0)]
        if limit is not None:
            msgs = msgs[-limit:]
        return msgs


class MemoryAppointmentReader(AppointmentReader):
    def __init__(self, facts=None):
        self.facts = facts or {}

    def active_facts(self, conversation_id):
        return self.facts


@pytest.fixture
def summary_parts():
    """真实临时数据库 + 20 条消息（超过默认触发阈值 20 条中的 >= 判定）。"""
    from db.db_router import DatabaseRouter

    router = DatabaseRouter()
    conv = router.conversations.create_conversation(user_id="u1")
    conv_id = conv["id"]
    for i in range(1, 21):
        router.conversations.add_message(conv_id, "user", f"消息-{i}", metadata={"seq_hint": i})
    reader = MemoryMessageReader(
        [{"role": "user", "content": f"消息-{i}", "sequence": i} for i in range(1, 21)]
    )
    repo = SummaryRepository(router.session_manager)
    yield {"router": router, "conv_id": conv_id, "reader": reader, "repo": repo}
    router.close()


def make_service(repo, reader, facts=None, summarizer=None, budget=None, trigger=None):
    return SummaryService(
        repository=repo,
        message_reader=reader,
        appointment_reader=MemoryAppointmentReader(facts),
        summarizer=summarizer,
        budget=budget,
        trigger_defaults=trigger,
    )


class TestTrigger:
    @pytest.mark.asyncio
    async def test_未达阈值跳过(self, summary_parts):
        reader = MemoryMessageReader(
            [{"role": "user", "content": "只有一条", "sequence": 1}]
        )
        svc = make_service(summary_parts["repo"], reader)
        assert await svc.summarize_if_needed("c1") == SummaryOutcome.SKIPPED
        assert summary_parts["repo"].get_latest_active("c1") is None

    @pytest.mark.asyncio
    async def test_达到消息数阈值生成(self, summary_parts):
        svc = make_service(summary_parts["repo"], summary_parts["reader"])
        outcome = await svc.summarize_if_needed(summary_parts["conv_id"])
        assert outcome == SummaryOutcome.SUCCEEDED
        snap = summary_parts["repo"].get_latest_active(summary_parts["conv_id"])
        assert snap["from_sequence"] == 1
        assert snap["to_sequence"] == 20
        assert snap["version"] == 1
        assert snap["status"] == "active"

    @pytest.mark.asyncio
    async def test_预算比例触发(self, summary_parts):
        # 消息少但估算超 75%：max_input_tokens 极小
        budget = ContextBudget(max_input_tokens=8, reserved_output_tokens=8, max_recent_messages=5, max_evidence_items=1)
        reader = MemoryMessageReader([{"role": "user", "content": "很长" * 100, "sequence": 1}])
        svc = make_service(summary_parts["repo"], reader, budget=budget)
        assert await svc.summarize_if_needed("c1") == SummaryOutcome.SUCCEEDED


class TestVersioning:
    @pytest.mark.asyncio
    async def test_第二次生成版本递增且覆盖连续(self, summary_parts):
        # 小触发阈值（5 条），聚焦版本与连续性逻辑
        trigger = SummaryTriggerDefaults(max_messages=5)
        svc = make_service(summary_parts["repo"], summary_parts["reader"], trigger=trigger)
        await svc.summarize_if_needed(summary_parts["conv_id"])
        # 追加 5 条再触发
        for i in range(21, 26):
            summary_parts["router"].conversations.add_message(summary_parts["conv_id"], "assistant", f"回复-{i}")
        reader2 = MemoryMessageReader(
            [{"role": "user", "content": f"消息-{i}", "sequence": i} for i in range(1, 21)]
            + [{"role": "assistant", "content": f"回复-{i}", "sequence": i} for i in range(21, 26)]
        )
        svc2 = make_service(summary_parts["repo"], reader2, trigger=trigger)
        assert await svc2.summarize_if_needed(summary_parts["conv_id"]) == SummaryOutcome.SUCCEEDED
        snaps = summary_parts["repo"].get_active_history(summary_parts["conv_id"])
        assert len(snaps) == 2  # 旧版本保留
        assert snaps[0]["to_sequence"] == 20
        assert snaps[1]["from_sequence"] == 21
        assert snaps[1]["to_sequence"] == 25
        assert snaps[1]["version"] == 2

    @pytest.mark.asyncio
    async def test_无新消息时不重复压缩(self, summary_parts):
        svc = make_service(summary_parts["repo"], summary_parts["reader"])
        await svc.summarize_if_needed(summary_parts["conv_id"])
        assert await svc.summarize_if_needed(summary_parts["conv_id"]) == SummaryOutcome.SKIPPED


class TestFailureFallback:
    @pytest.mark.asyncio
    async def test_生成异常保留旧摘要(self, summary_parts):
        trigger = SummaryTriggerDefaults(max_messages=5)
        svc = make_service(summary_parts["repo"], summary_parts["reader"], trigger=trigger)
        await svc.summarize_if_needed(summary_parts["conv_id"])
        old = summary_parts["repo"].get_latest_active(summary_parts["conv_id"])

        class ExplodingSummarizer(Summarizer):
            async def summarize(self, messages, key_facts_hint):
                raise RuntimeError("模型不可用")

        reader2 = MemoryMessageReader(
            [{"role": "user", "content": f"消息-{i}", "sequence": i} for i in range(1, 26)]
        )
        svc2 = make_service(summary_parts["repo"], reader2, summarizer=ExplodingSummarizer(), trigger=trigger)
        assert await svc2.summarize_if_needed(summary_parts["conv_id"]) == SummaryOutcome.FAILED
        assert summary_parts["repo"].get_latest_active(summary_parts["conv_id"])["version"] == old["version"]

    @pytest.mark.asyncio
    async def test_关键事实缺失被校验拦截(self, summary_parts):
        class NoFactsSummarizer(Summarizer):
            async def summarize(self, messages, key_facts_hint):
                return SummaryGenerationResult(content="没有保留关键事实", key_facts=[])

        svc = make_service(
            summary_parts["repo"], summary_parts["reader"],
            facts={"status": "draft", "service_type": "基础护理"},
            summarizer=NoFactsSummarizer(),
        )
        outcome = await svc.summarize_if_needed(summary_parts["conv_id"])
        assert outcome == SummaryOutcome.FAILED
        assert summary_parts["repo"].get_latest_active(summary_parts["conv_id"]) is None

    @pytest.mark.asyncio
    async def test_敏感标记摘要被拦截(self, summary_parts):
        class LeakySummarizer(Summarizer):
            async def summarize(self, messages, key_facts_hint):
                return SummaryGenerationResult(content="x [THOUGHT]内部推理 y", key_facts=[])

        svc = make_service(summary_parts["repo"], summary_parts["reader"], summarizer=LeakySummarizer())
        assert await svc.summarize_if_needed(summary_parts["conv_id"]) == SummaryOutcome.FAILED

    @pytest.mark.asyncio
    async def test_失败不阻断且原始消息保留(self, summary_parts):
        class ExplodingSummarizer(Summarizer):
            async def summarize(self, messages, key_facts_hint):
                raise RuntimeError("模型不可用")

        svc = make_service(summary_parts["repo"], summary_parts["reader"], summarizer=ExplodingSummarizer())
        assert await svc.summarize_if_needed(summary_parts["conv_id"]) == SummaryOutcome.FAILED
        # 原始消息仍可读取（旧摘要/最近消息降级路径可用）
        recent = summary_parts["router"].conversations.get_recent_messages(summary_parts["conv_id"], limit=5)
        assert len(recent) == 5


class TestRestartAndMasking:
    @pytest.mark.asyncio
    async def test_重启后从数据库恢复(self, summary_parts):
        svc = make_service(summary_parts["repo"], summary_parts["reader"])
        await svc.summarize_if_needed(summary_parts["conv_id"])
        # 模拟重启：全新 Repository/Service（同一数据库文件）
        from db.db_router import DatabaseRouter

        router2 = DatabaseRouter()
        try:
            repo2 = SummaryRepository(router2.session_manager)
            svc2 = make_service(repo2, MemoryMessageReader([]))
            snap = repo2.get_latest_active(summary_parts["conv_id"])
            assert snap is not None
            assert snap["to_sequence"] == 20
        finally:
            router2.close()

    @pytest.mark.asyncio
    async def test_墓碑屏蔽消息不进入摘要(self, summary_parts):
        # 前 10 条被屏蔽（模拟 E4 墓碑关联）：覆盖范围连续（from=1），但内容排除屏蔽项
        masked = [make_message("user", f"消息-{i}", i, {CONTEXT_EXCLUDED_KEY: True}) for i in range(1, 11)]
        normal = [make_message("user", f"正常-{i}", i) for i in range(11, 31)]
        reader = MemoryMessageReader(masked + normal)
        svc = make_service(summary_parts["repo"], reader)
        assert await svc.summarize_if_needed(summary_parts["conv_id"]) == SummaryOutcome.SUCCEEDED
        snap = summary_parts["repo"].get_latest_active(summary_parts["conv_id"])
        assert snap["from_sequence"] == 1  # 序号连续，不制造空洞
        assert snap["to_sequence"] == 30
        assert "消息-1" not in snap["content"]  # 屏蔽消息内容不进入摘要
        assert "正常-11" in snap["content"]

    @pytest.mark.asyncio
    async def test_失效摘要不再进入活动查询(self, summary_parts):
        svc = make_service(summary_parts["repo"], summary_parts["reader"])
        await svc.summarize_if_needed(summary_parts["conv_id"])
        # E4 联动：偏好删除使该用户全部摘要失效
        affected = summary_parts["repo"].invalidate_all_for_user("u1")
        assert affected >= 1
        assert summary_parts["repo"].get_latest_active(summary_parts["conv_id"]) is None
        assert len(summary_parts["repo"].get_active_history(summary_parts["conv_id"])) == 0