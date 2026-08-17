"""D8：编排故障注入与边界测试（客户端断开 / 请求 ID / 锁释放）"""

import asyncio

import pytest

from api.chat_handler import get_session_manager, get_task_agent_for, reset_session_manager
from application.contracts import EventType
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


class TestClientDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_releases_lock(self):
        """客户端提前关闭事件流 -> 会话锁释放，后续 turn 可正常进入。"""
        orch = make_orchestrator()
        s = get_session_manager().create_conversation(user_id="u1")

        # 第一轮：只读 2 个事件后提前关闭（模拟客户端断开）
        gen = orch.handle_turn(s.conversation_id, "u1", "我想预约肩颈放松")
        count = 0
        async for _envelope in gen:
            count += 1
            if count >= 2:
                break
        await gen.aclose()  # 客户端断开

        # 第二轮应正常完成（锁已释放、DB Session 无泄漏）
        events = []
        async for envelope in orch.handle_turn(s.conversation_id, "u1", "你好"):
            events.append(envelope)
        assert events[-1].type == EventType.RUN_COMPLETED

    @pytest.mark.asyncio
    async def test_concurrent_turn_after_disconnect(self):
        """断开后再发一轮不卡死（asyncio 锁无泄漏）。"""
        orch = make_orchestrator()
        s = get_session_manager().create_conversation(user_id="u1")

        gen = orch.handle_turn(s.conversation_id, "u1", "我想预约肩颈放松")
        async for _e in gen:
            break
        await gen.aclose()

        async def run_second():
            events = []
            async for envelope in orch.handle_turn(s.conversation_id, "u1", "谢谢"):
                events.append(envelope)
            return events[-1].type

        result = await asyncio.wait_for(run_second(), timeout=10)
        assert result == EventType.RUN_COMPLETED


class TestRequestId:
    @pytest.mark.asyncio
    async def test_request_id_becomes_run_id(self):
        """client_request_id 作为 run_id 透传（首事件携带）。"""
        orch = make_orchestrator()
        s = get_session_manager().create_conversation(user_id="u1")
        events = []
        async for envelope in orch.handle_turn(
            s.conversation_id, "u1", "你好", request_id="req-abc-123"
        ):
            events.append(envelope)
        assert events[0].type == EventType.RUN_STARTED
        assert events[0].data.get("request_id") == "req-abc-123"
        assert events[0].run_id == "req-abc-123"
        # 所有事件共享同一 run_id
        assert {e.run_id for e in events} == {"req-abc-123"}

    @pytest.mark.asyncio
    async def test_run_id_generated_when_absent(self):
        orch = make_orchestrator()
        s = get_session_manager().create_conversation(user_id="u1")
        events = []
        async for envelope in orch.handle_turn(s.conversation_id, "u1", "你好"):
            events.append(envelope)
        assert events[0].run_id and events[0].run_id != ""

    @pytest.mark.asyncio
    async def test_duplicate_request_id_replays_without_duplicate_messages(self):
        orch = make_orchestrator()
        s = get_session_manager().create_conversation(user_id="u1")

        first = []
        async for event in orch.handle_turn(s.conversation_id, "u1", "你好", request_id="same-request"):
            first.append(event)
        second = []
        async for event in orch.handle_turn(s.conversation_id, "u1", "你好", request_id="same-request"):
            second.append(event)

        messages = get_session_manager().repository.get_recent_messages(s.conversation_id)
        assert len(messages) == 2
        assert second[0].data["replayed"] is True
        assert second[-1].type == EventType.RUN_COMPLETED
        assert second[-1].data["replayed"] is True

    @pytest.mark.asyncio
    async def test_duplicate_request_id_with_different_content_is_conflict(self):
        orch = make_orchestrator()
        s = get_session_manager().create_conversation(user_id="u1")

        async for _ in orch.handle_turn(s.conversation_id, "u1", "你好", request_id="same-request"):
            pass
        events = []
        async for event in orch.handle_turn(s.conversation_id, "u1", "谢谢", request_id="same-request"):
            events.append(event)

        assert events[-1].type == EventType.RUN_FAILED
        assert events[-1].data["error"] == "IDEMPOTENCY_CONFLICT"


class TestFaultInjection:
    @pytest.mark.asyncio
    async def test_llm_classifier_failure_falls_back_to_rules(self):
        """LLM 分类器抛异常 -> 回退规则结果（决策一兜底链）。"""
        async def broken_llm(text, ctx):
            raise RuntimeError("llm down")

        orch = ConversationOrchestrator(
            session_manager=get_session_manager(),
            router=IntentRouter(llm_classifier=broken_llm),
            agent_factory=get_task_agent_for,
        )
        s = get_session_manager().create_conversation(user_id="u1")
        events = []
        async for envelope in orch.handle_turn(s.conversation_id, "u1", "我想预约肩颈放松"):
            events.append(envelope)
        # 规则命中（appointment），LLM 失败不影响
        assert events[-1].type == EventType.RUN_COMPLETED
        intent_event = [e for e in events if e.type == EventType.INTENT_DETECTED][0]
        assert intent_event.data["intent"] == "appointment"
