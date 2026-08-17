"""D6：行为记录旁路与失败隔离测试"""

import logging

import pytest

from api.chat_handler import get_session_manager, get_task_agent_for, reset_session_manager
from application.behavior import BehaviorRecorder
from application.contracts import EventType
from application.orchestrator import ConversationOrchestrator, IntentRouter
from services.technician_service import TechnicianService


@pytest.fixture(autouse=True)
def fresh():
    reset_session_manager()
    TechnicianService().initialize_default_technicians()
    yield
    reset_session_manager()


class TestBehaviorRecorder:
    def test_default_logs_structured(self, caplog):
        with caplog.at_level(logging.INFO, logger="application.behavior"):
            BehaviorRecorder().record("u1", "c1", "appointment", {"sub_action": "draft"})
        assert any("BEHAVIOR" in r.message for r in caplog.records)
        assert any('"action_type": "appointment"' in r.message for r in caplog.records)

    def test_record_fn_failure_is_silent(self, caplog):
        """注入的记录函数抛异常 -> 不向上传播（旁路原则）。"""

        def boom(entry):
            raise RuntimeError("db down")

        recorder = BehaviorRecorder(record_fn=boom)
        # 不抛异常
        recorder.record("u1", "c1", "appointment")
        with caplog.at_level(logging.WARNING, logger="application.behavior"):
            recorder.record("u1", "c1", "appointment")
        assert any("行为记录失败" in r.message for r in caplog.records)

    def test_record_fn_called_with_entry(self):
        captured = []

        def spy(entry):
            captured.append(entry)

        BehaviorRecorder(record_fn=spy).record("u1", "c1", "consultation", {"q": "价格"})
        assert len(captured) == 1
        assert captured[0]["action_type"] == "consultation"
        assert captured[0]["data"] == {"q": "价格"}


class TestOrchestratorBehaviorBypass:
    @pytest.mark.asyncio
    async def test_recorder_failure_does_not_break_turn(self):
        """旁路记录器抛异常 -> 主对话仍正常完成。"""

        class BoomRecorder:
            def record(self, *a, **k):
                raise RuntimeError("recorder down")

        orch = ConversationOrchestrator(
            session_manager=get_session_manager(),
            router=IntentRouter(),
            agent_factory=get_task_agent_for,
            behavior_recorder=BoomRecorder(),
        )
        s = get_session_manager().create_conversation(user_id="u1")
        events = []
        async for envelope in orch.handle_turn(s.conversation_id, "u1", "你好"):
            events.append(envelope)
        assert events[-1].type == EventType.RUN_COMPLETED, "记录器失败不应影响主流程"

    @pytest.mark.asyncio
    async def test_recorder_receives_intent(self):
        captured = []

        def spy(entry):
            captured.append(entry)

        orch = ConversationOrchestrator(
            session_manager=get_session_manager(),
            router=IntentRouter(),
            agent_factory=get_task_agent_for,
            behavior_recorder=BehaviorRecorder(record_fn=spy),
        )
        s = get_session_manager().create_conversation(user_id="u1")
        async for _ in orch.handle_turn(s.conversation_id, "u1", "我想预约肩颈放松"):
            pass
        assert len(captured) == 1
        assert captured[0]["action_type"] == "appointment"
        assert captured[0]["conversation_id"] == s.conversation_id
