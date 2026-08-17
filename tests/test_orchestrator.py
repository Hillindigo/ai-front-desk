"""D2：IntentRouter 与 ConversationOrchestrator 测试"""

import pytest

from api.chat_handler import get_session_manager, get_task_agent_for, reset_session_manager
from application.contracts import IntentClassification, IntentType
from application.orchestrator import ConversationOrchestrator, IntentRouter
from application.intent_rules import match_intent
from services.appointment_domain import AppointmentCommandService
from services.technician_service import TechnicianService


@pytest.fixture(autouse=True)
def fresh():
    reset_session_manager()
    TechnicianService().initialize_default_technicians()
    yield
    reset_session_manager()


def make_orchestrator(llm_classifier=None):
    return ConversationOrchestrator(
        session_manager=get_session_manager(),
        router=IntentRouter(llm_classifier=llm_classifier),
        agent_factory=get_task_agent_for,
    )


async def run_turn(orchestrator, conversation_id, user_id, text):
    texts = []
    final = {}
    async for kind, payload in orchestrator.handle_turn(conversation_id, user_id, text):
        if kind == "text":
            texts.append(payload)
        else:
            final[kind] = payload
    return "".join(texts), final


class TestIntentRouter:
    @pytest.mark.asyncio
    async def test_rule_first_no_llm(self):
        """明确预约输入：规则命中，LLM 不被调用。"""
        called = []

        async def fake_llm(text, ctx):
            called.append(text)
            return IntentClassification(IntentType.CONSULTATION)

        router = IntentRouter(llm_classifier=fake_llm)
        result = await router.classify("我想预约肩颈放松")
        assert result.intent == IntentType.APPOINTMENT
        assert result.matched_rule == "appointment_request"
        assert called == []

    @pytest.mark.asyncio
    async def test_fuzzy_uses_llm(self):
        """模糊输入：LLM 兜底。"""
        async def fake_llm(text, ctx):
            return IntentClassification(IntentType.CONSULTATION, matched_rule="llm")

        router = IntentRouter(llm_classifier=fake_llm)
        result = await router.classify("帮我看看哪个套餐性价比高")
        assert result.matched_rule == "llm"


class TestOrchestratorRouting:
    @pytest.mark.asyncio
    async def test_appointment_routes_to_workflow(self):
        orch = make_orchestrator()
        mgr = get_session_manager()
        s = mgr.create_conversation(user_id="u1")
        reply, final = await run_turn(orch, s.conversation_id, "u1", "我想预约肩颈放松")
        assert final["completed"]["intent"]["intent"] == "appointment"
        assert "肩颈放松" in reply or len(reply) > 0

    @pytest.mark.asyncio
    async def test_consultation_routes_to_workflow(self):
        orch = make_orchestrator()
        s = get_session_manager().create_conversation(user_id="u1")
        reply, final = await run_turn(orch, s.conversation_id, "u1", "肩颈放松有什么好处")
        assert final["completed"]["intent"]["intent"] == "consultation"
        assert len(reply) > 0

    @pytest.mark.asyncio
    async def test_unrelated_deterministic(self):
        orch = make_orchestrator()
        s = get_session_manager().create_conversation(user_id="u1")
        reply, final = await run_turn(orch, s.conversation_id, "u1", "你好")
        assert final["completed"]["intent"]["intent"] == "unrelated"
        assert "暂不支持" in reply

    @pytest.mark.asyncio
    async def test_messages_persisted_pair(self):
        """user + assistant 消息成对落库。"""
        orch = make_orchestrator()
        s = get_session_manager().create_conversation(user_id="u1")
        await run_turn(orch, s.conversation_id, "u1", "我想预约肩颈放松")
        msgs = get_session_manager().repository.get_recent_messages(s.conversation_id)
        roles = [m["role"] for m in msgs]
        assert roles == ["user", "assistant"]


class TestDeterministicCommands:
    @pytest.mark.asyncio
    async def test_cancel_command_uses_domain(self):
        """明确取消命令：走领域服务，不调用 LLM。"""
        called = []

        async def fake_llm(text, ctx):
            called.append(text)
            return IntentClassification(IntentType.APPOINTMENT)

        orch = make_orchestrator(llm_classifier=fake_llm)
        s = get_session_manager().create_conversation(user_id="u1")
        # 先创建一个活跃草稿
        svc = AppointmentCommandService()
        try:
            svc.create_draft(
                user_id="u1", conversation_id=s.conversation_id, service_type="肩颈放松",
                fields={"project": "肩颈放松"},
            )
        finally:
            svc.close()

        reply, final = await run_turn(orch, s.conversation_id, "u1", "取消预约")
        assert "已为您取消" in reply
        assert called == [], "明确取消命令不应调用 LLM"
        # 草稿已被取消
        svc = AppointmentCommandService()
        try:
            assert svc.get_active_draft(s.conversation_id) is None
        finally:
            svc.close()

    @pytest.mark.asyncio
    async def test_confirm_command_completes_draft(self):
        orch = make_orchestrator()
        s = get_session_manager().create_conversation(user_id="u1")
        # 先建字段完整的 pending 草稿
        svc = AppointmentCommandService()
        try:
            techs = svc._availability().technician_repo.get_all_technicians()
            draft = svc.create_draft(
                user_id="u1", conversation_id=s.conversation_id, service_type="肩颈放松",
                fields={
                    "project": "肩颈放松", "technician_id": techs[0]["id"],
                    "start_time": __import__("datetime").datetime(2026, 8, 18, 10),
                    "end_time": __import__("datetime").datetime(2026, 8, 18, 11),
                    "duration_minutes": 60,
                },
            )
            svc.request_confirmation(draft["id"], "u1")
        finally:
            svc.close()

        reply, final = await run_turn(orch, s.conversation_id, "u1", "确认")
        assert "确认成功" in reply
        svc = AppointmentCommandService()
        try:
            assert svc.get_active_draft(s.conversation_id) is None  # 已确认，不再活跃
        finally:
            svc.close()
