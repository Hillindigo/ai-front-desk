"""聊天处理入口（Phase B B3：会话化）。

移除全局 task_agent 作为主路径：每个会话拥有独立的运行时对象、Agent 实例
（含预约草稿）与 asyncio.Lock。消息按"用户消息先落库 -> 会话锁内处理 ->
assistant 结果落库"写入（决策二：每轮独立短生命周期 DB Session）。
"""

from application.session_runtime import ConversationSession, SessionManager
from agents.task_classification_agent import TaskClassificationAgent
from agents.appointment_agent import AppointmentAgent
from agents.consultant_agent import ConsultantAgent

_session_manager = SessionManager()


def get_session_manager() -> SessionManager:
    return _session_manager


def get_task_agent_for(session: ConversationSession):
    """会话专属 task_agent（惰性创建）。

    预约草稿由会话专属 AppointmentAgent 实例持有，天然不跨会话共享；
    创建时把数据库恢复的最近消息注入预约 Agent 的历史（重启恢复上下文）。
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

        session.agent = TaskClassificationAgent(appointment_agent, consultant_agent)
    return session.agent


def reset_session_manager() -> None:
    """重置会话管理器（测试隔离用）。"""
    global _session_manager
    _session_manager = SessionManager()


async def ProcessUserInput_stream(
    user_input,
    state=None,
    context=None,
    conversation_id=None,
    user_id=None,
):
    """
    会话化流式聊天入口。

    conversation_id 为空时落到默认演示会话（/chat/stream 兼容行为）。
    """
    if context is None:
        context = {}

    manager = get_session_manager()
    if conversation_id:
        session = manager.get_or_create_session(conversation_id, user_id=user_id)
    else:
        session = manager.get_or_create_default(user_id or "default_user")

    # 1. 会话锁内处理（同一会话并发 turn 完全串行，保证 user->assistant 成对）
    async with session.lock:
        # 1a. 用户消息先落库（模型失败也不丢失用户输入），并同步运行时列表
        user_message = manager.repository.add_message(session.conversation_id, "user", user_input)
        if user_message:
            session.append_message(user_message)

        try:
            agent = get_task_agent_for(session)
            collected = []
            async for token in agent.classify_task_stream(user_input):
                collected.append(token)
                yield token

            # 3. assistant 最终消息落库（完成后），并同步运行时列表
            full_response = "".join(collected)
            assistant_message = manager.repository.add_message(
                session.conversation_id, "assistant", full_response
            )
            if assistant_message:
                session.append_message(assistant_message)
        except Exception:
            # 模型等外部依赖不可用时返回稳定错误（A-R1）
            yield "[ERROR]模型或服务暂不可用，请稍后再试。"
            error_message = manager.repository.add_message(
                session.conversation_id,
                "assistant",
                "[ERROR]模型或服务暂不可用。",
                message_type="error",
            )
            if error_message:
                session.append_message(error_message)