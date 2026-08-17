"""聊天处理入口（Phase D D3：收缩为适配层）。

编排逻辑已移至 application/orchestrator.py（ConversationOrchestrator）；
本模块只做：默认会话解析兼容 + 通过应用容器调用 Orchestrator + 文本流适配。
D4 将把文本流替换为 SSE 事件流。
"""

import logging

from agents.appointment_agent import AppointmentAgent
from agents.consultant_agent import ConsultantAgent
from agents.task_classification_agent import TaskClassificationAgent
from application.container import Container

logger = logging.getLogger(__name__)

_container: Container | None = None


def get_container() -> Container:
    """应用容器（惰性单例，D3）。"""
    global _container
    if _container is None:
        _container = Container()
    return _container


def reset_session_manager():
    """测试隔离：重建容器（会话管理器随之重建）。"""
    global _container
    if _container is not None:
        try:
            _container.close()
        except Exception:
            pass
    _container = None


def get_session_manager():
    return get_container().session_manager


def get_task_agent_for(session):
    """会话专属 task_agent（惰性创建）。

    预约草稿由会话专属 AppointmentAgent 实例持有，天然不跨会话共享；
    创建时恢复最近消息历史，并从持久化草稿恢复已确定的项目字段（Phase C C5）。
    """
    if session.agent is None:
        session_id = session.conversation_id
        appointment_agent = AppointmentAgent(session_id=session_id)
        consultant_agent = ConsultantAgent(session_id=session_id)

        # 从 DB 恢复历史（role: user/assistant -> human/ai）
        for message in session.recent_messages:
            role = "human" if message["role"] == "user" else "ai"
            appointment_agent.chat_history.add_message(
                {"role": role, "content": message["content"]}
            )

        # Phase C C5：从持久化草稿恢复已确定的项目字段
        try:
            from services.appointment_domain import AppointmentCommandService

            svc = AppointmentCommandService()
            try:
                draft = svc.get_active_draft(session_id)
                if draft and draft.get("project"):
                    appointment_agent.appointment_history["project"] = draft["project"]
            finally:
                svc.close()
        except Exception:
            # 草稿恢复失败不影响会话主流程
            pass

        session.agent = TaskClassificationAgent(appointment_agent, consultant_agent)
    return session.agent


async def ProcessUserInput_stream(
    user_input,
    state=None,
    context=None,
    conversation_id=None,
    user_id=None,
):
    """兼容入口：解析会话 -> 调 Orchestrator -> 文本流输出（D4 替换为 SSE）。"""
    container = get_container()
    if not conversation_id:
        default_session = container.session_manager.get_or_create_default(
            user_id or "default_user"
        )
        conversation_id = default_session.conversation_id
    try:
        async for envelope in container.orchestrator.handle_turn(
            conversation_id, user_id or "default_user", user_input
        ):
            from application.contracts import EventType

            if envelope.type == EventType.ASSISTANT_DELTA:
                yield envelope.data.get("text", "")
            elif envelope.type == EventType.RUN_FAILED:
                # D4 兼容：run_failed 事件转稳定错误文本（旧 /chat/stream 消费者）
                yield "[ERROR]模型或服务暂不可用，请稍后再试。"
    except Exception as exc:
        logger.error("聊天处理失败", exc_info=True)
        yield "[ERROR]模型或服务暂不可用，请稍后再试。"
