"""Phase E E5 测试：Orchestrator 接入 ContextBuilder、偏好命令确定性路由、旁路失败隔离。

覆盖：上下文装配被调用并传入分类器、偏好命令落库 + 文案、
偏好/摘要旁路失败不阻断主对话、真实容器一轮事件流完好。
"""

from typing import Any, AsyncGenerator, Dict, Optional

import pytest

from application.context_builder import ContextBuilder
from application.context_contracts import ContextPackage
from application.contracts import (
    EventType,
    IntentClassification,
    IntentType,
    TERMINAL_EVENTS,
)
from application.orchestrator import ConversationOrchestrator, IntentRouter
from db.db_router import DatabaseRouter
from services.preference_service import PreferenceService


def make_orchestrator(
    session_manager=None,
    router=None,
    context_builder=None,
    summary_service=None,
    preference_service=None,
):
    from application.workflows import UnrelatedWorkflow

    sm = session_manager or _new_session_manager()
    return ConversationOrchestrator(
        session_manager=sm,
        router=router,
        workflows={IntentType.UNRELATED: UnrelatedWorkflow()},
        context_builder=context_builder,
        summary_service=summary_service,
        preference_service=preference_service,
    )


def _new_session_manager():
    from application.session_runtime import SessionManager

    return SessionManager(DatabaseRouter())


class RecordingBuilder(ContextBuilder):
    """记录 build 调用的假 ContextBuilder。"""

    def __init__(self, package=None):
        self.calls = []
        self._package = package or ContextPackage(
            conversation_id="c1", user_id="u1", current_input="x",
        )

    async def build(self, conversation_id, user_id, current_input, workflow_state=None):
        self.calls.append((conversation_id, current_input))
        return self._package


class RecordingRouter(IntentRouter):
    """记录 classify 收到的上下文。"""

    def __init__(self):
        super().__init__()
        self.last_context = None

    async def classify(self, text: str, session_context: Optional[Dict[str, Any]] = None):
        self.last_context = session_context
        return IntentClassification(intent=IntentType.UNRELATED)


async def run_turn(orchestrator, conversation_id=None, user_input="你好", user_id="u1"):
    if conversation_id is None:
        session = orchestrator.session_manager.create_conversation(user_id=user_id)
        conversation_id = session.conversation_id
    events = []
    async for envelope in orchestrator.handle_turn(conversation_id, user_id, user_input):
        events.append(envelope)
    return events


def events_of(events, etype):
    return [e for e in events if e.type == etype]


class TestContextInjection:
    @pytest.mark.asyncio
    async def test_上下文装配被调用并传入分类器(self):
        builder = RecordingBuilder()
        router = RecordingRouter()
        orch = make_orchestrator(router=router, context_builder=builder)
        events = await run_turn(orch)
        assert builder.calls  # build 被调用
        assert builder.calls[0][1] == "你好"  # 当前输入传入
        assert router.last_context is not None  # model_input 传给分类器
        # 事件协议完好：唯一终止事件
        terminals = events_of(events, EventType.RUN_COMPLETED) + events_of(events, EventType.RUN_FAILED)
        assert len(terminals) == 1
        assert terminals[0].type == EventType.RUN_COMPLETED

    @pytest.mark.asyncio
    async def test_上下文装配失败降级不阻断(self):
        class ExplodingBuilder:
            async def build(self, *a, **kw):
                raise RuntimeError("上下文读取失败")

        router = RecordingRouter()
        orch = make_orchestrator(router=router, context_builder=ExplodingBuilder())
        events = await run_turn(orch)
        terminals = [e for e in events if e.type in TERMINAL_EVENTS]
        assert len(terminals) == 1 and terminals[0].type == EventType.RUN_COMPLETED

    @pytest.mark.asyncio
    async def test_摘要旁路失败不阻断主对话(self):
        class ExplodingSummary:
            async def summarize_if_needed(self, conversation_id):
                raise RuntimeError("摘要服务故障")

        orch = make_orchestrator(summary_service=ExplodingSummary())
        events = await run_turn(orch)
        terminals = [e for e in events if e.type in TERMINAL_EVENTS]
        assert len(terminals) == 1 and terminals[0].type == EventType.RUN_COMPLETED

    @pytest.mark.asyncio
    async def test_偏好保存失败不阻断(self):
        class ExplodingPreference:
            def set_preference(self, **kw):
                raise RuntimeError("偏好写入故障")

        orch = make_orchestrator(preference_service=ExplodingPreference())
        events = await run_turn(orch, user_input="请记住我喜欢王师傅")
        deltas = "".join(e.data.get("text", "") for e in events_of(events, EventType.ASSISTANT_DELTA))
        assert "无法保存" in deltas  # 文案反馈失败，不假装成功
        terminals = [e for e in events if e.type in TERMINAL_EVENTS]
        assert terminals[0].type == EventType.RUN_COMPLETED


class TestPreferenceRouting:
    @pytest.mark.asyncio
    async def test_偏好命令落库并反馈(self):
        router = DatabaseRouter()
        from db.repositories.preference_repository import PreferenceRepository

        service = PreferenceService(PreferenceRepository(router.session_manager))
        orch = make_orchestrator(preference_service=service)
        events = await run_turn(orch, user_input="请记住我喜欢王师傅")
        active = service.list_active_preferences("u1")
        assert len(active) == 1
        assert active[0].preference_value == "王师傅"
        deltas = "".join(e.data.get("text", "") for e in events_of(events, EventType.ASSISTANT_DELTA))
        assert "已记住" in deltas
        tools = events_of(events, EventType.TOOL_STARTED)
        assert tools[0].data["tool"] == "preference_memorize"

    @pytest.mark.asyncio
    async def test_非偏好表达不进偏好工具(self):
        orch = make_orchestrator()
        events = await run_turn(orch, user_input="你好请问营业时间")
        tools = events_of(events, EventType.TOOL_STARTED)
        assert not any(t.data.get("tool") == "preference_memorize" for t in tools)

    @pytest.mark.asyncio
    async def test_偏好来源消息关联(self):
        router = DatabaseRouter()
        from db.repositories.preference_repository import PreferenceRepository

        service = PreferenceService(PreferenceRepository(router.session_manager))
        orch = make_orchestrator(preference_service=service)
        await run_turn(orch, user_input="请记住我的服务项目是足部护理")
        active = service.list_active_preferences("u1")
        assert active[0].preference_type.value == "service"
        assert active[0].source_message_id is not None  # 来源消息关联


class TestRealContainer:
    def test_容器组装并可跑一轮(self):
        from api.chat_handler import reset_session_manager
        from application.container import Container

        reset_session_manager()
        container = Container()
        try:
            assert container.context_builder is not None
            assert container.summary_service is not None
            assert container.preference_service is not None
            assert container.orchestrator.context_builder is container.context_builder
            session = container.session_manager.create_conversation(user_id="u1")
            assert session.conversation_id
        finally:
            container.close()
            reset_session_manager()

    @pytest.mark.asyncio
    async def test_长会话触发摘要端到端(self):
        from api.chat_handler import reset_session_manager
        from application.container import Container

        reset_session_manager()
        container = Container()
        try:
            conv = container.session_manager.create_conversation(user_id="u1")
            conv_id = conv.conversation_id
            # 多轮对话（每轮完整消费事件流），消息数超过触发阈值后摘要生成
            for i in range(12):
                async for _e in container.orchestrator.handle_turn(conv_id, "u1", f"营业时间是几点第{i}次"):
                    pass
            # 12 轮 * 2 条消息 = 24 条 >= 20 阈值
            snap = container.summary_repository.get_latest_active(conv_id)
            assert snap is not None
            assert snap["to_sequence"] >= 20
        finally:
            container.close()
            reset_session_manager()