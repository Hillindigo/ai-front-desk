"""D4：SSE 事件流测试（事件顺序 / terminal 唯一 / 标记清洗 / SSE framing）"""

import json

import pytest

from api.chat_handler import get_session_manager, get_task_agent_for, reset_session_manager
from application.contracts import EventType
from application.events import EventStream, clean_token, sse_frame
from application.orchestrator import ConversationOrchestrator, IntentRouter
from services.technician_service import TechnicianService


@pytest.fixture(autouse=True)
def fresh():
    reset_session_manager()
    TechnicianService().initialize_default_technicians()
    yield
    reset_session_manager()


def make_orchestrator():
    return ConversationOrchestrator(
        session_manager=get_session_manager(),
        router=IntentRouter(),
        agent_factory=get_task_agent_for,
    )


async def collect_events(orchestrator, conversation_id, user_id, text):
    events = []
    async for envelope in orchestrator.handle_turn(conversation_id, user_id, text):
        events.append(envelope)
    return events


class TestCleanToken:
    def test_thought_signal_dropped(self):
        assert clean_token("[THOUGHT]正在分析用户意图") is None
        assert clean_token("[SIGNAL]recommendation_pending") is None

    def test_reply_stripped(self):
        assert clean_token("[REPLY]您好，请问有什么可以帮您？") == "您好，请问有什么可以帮您？"

    def test_plain_text_passthrough(self):
        assert clean_token("普通文本") == "普通文本"
        assert clean_token("") is None
        assert clean_token("   ") is None


class TestEventStreamOrder:
    @pytest.mark.asyncio
    async def test_event_order_and_sequence(self):
        orch = make_orchestrator()
        s = get_session_manager().create_conversation(user_id="u1")
        events = await collect_events(orch, s.conversation_id, "u1", "我想预约肩颈放松")

        # 首发 run_started，唯一终止事件收尾
        assert events[0].type == EventType.RUN_STARTED
        assert events[-1].type == EventType.RUN_COMPLETED
        # sequence 单调递增
        seqs = [e.sequence for e in events]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        # 终止事件唯一
        terminals = [e for e in events if e.type in (EventType.RUN_COMPLETED, EventType.RUN_FAILED)]
        assert len(terminals) == 1
        # 事件包字段完整
        for e in events:
            d = e.to_dict()
            for key in ("protocol_version", "event_id", "run_id", "conversation_id",
                        "sequence", "type", "timestamp", "data"):
                assert key in d

    @pytest.mark.asyncio
    async def test_contains_intent_and_delta_events(self):
        orch = make_orchestrator()
        s = get_session_manager().create_conversation(user_id="u1")
        events = await collect_events(orch, s.conversation_id, "u1", "我想预约肩颈放松")
        types = [e.type for e in events]
        assert EventType.INTENT_DETECTED in types
        assert EventType.WORKFLOW_STARTED in types
        assert EventType.ASSISTANT_DELTA in types

    @pytest.mark.asyncio
    async def test_no_internal_markers_in_deltas(self):
        """[THOUGHT]/[REPLY]/[SIGNAL] 不进入事件流（D4 清理）。"""
        orch = make_orchestrator()
        s = get_session_manager().create_conversation(user_id="u1")
        events = await collect_events(orch, s.conversation_id, "u1", "我想预约明天下午2点的肩颈放松，女服务人员")
        for e in events:
            if e.type == EventType.ASSISTANT_DELTA:
                text = e.data.get("text", "")
                assert "[THOUGHT]" not in text
                assert "[SIGNAL]" not in text
                assert "[REPLY]" not in text

    @pytest.mark.asyncio
    async def test_persisted_message_is_clean(self):
        """落库的 assistant 消息为清洗后文本（不含旧标记）。"""
        orch = make_orchestrator()
        s = get_session_manager().create_conversation(user_id="u1")
        await collect_events(orch, s.conversation_id, "u1", "我想预约肩颈放松")
        msgs = get_session_manager().repository.get_recent_messages(s.conversation_id)
        assistant_text = "".join(m["content"] for m in msgs if m["role"] == "assistant")
        assert "[THOUGHT]" not in assistant_text
        assert "[SIGNAL]" not in assistant_text

    @pytest.mark.asyncio
    async def test_run_failed_single_terminal_on_error(self):
        """工作流异常 -> 唯一 run_failed 终止事件。"""
        orch = make_orchestrator()

        class BoomWorkflow:
            async def run(self, *a, **k):
                raise RuntimeError("boom")
                yield  # pragma: no cover

        # 用异常工作流替换
        from application.contracts import IntentType
        orch.workflows = {IntentType.APPOINTMENT: BoomWorkflow()}
        s = get_session_manager().create_conversation(user_id="u1")
        events = await collect_events(orch, s.conversation_id, "u1", "我想预约肩颈放松")
        assert events[-1].type == EventType.RUN_FAILED
        assert events[-1].data.get("error") == "INTERNAL_ERROR"
        terminals = [e for e in events if e.type in (EventType.RUN_COMPLETED, EventType.RUN_FAILED)]
        assert len(terminals) == 1


class TestSseFraming:
    def test_frame_format(self):
        stream = EventStream(run_id="r1", conversation_id="c1")
        env = stream.next(EventType.RUN_STARTED, {"request_id": "x1"})
        frame = sse_frame(env)
        lines = frame.strip().split("\n")
        assert lines[0] == f"event: {EventType.RUN_STARTED.value}"
        assert lines[1].startswith("data: ")
        payload = json.loads(lines[1][len("data: "):])
        assert payload["run_id"] == "r1"
        assert payload["conversation_id"] == "c1"
        assert payload["sequence"] == 1
        assert payload["protocol_version"] == "v1"

    def test_sequence_increment(self):
        stream = EventStream(run_id="r", conversation_id="c")
        assert stream.next(EventType.RUN_STARTED).sequence == 1
        assert stream.next(EventType.INTENT_DETECTED).sequence == 2
        assert stream.next(EventType.RUN_COMPLETED).sequence == 3
