"""聊天处理入口。

Phase B（A-R1/B0）：全局 task_agent 改为惰性创建，避免 import 时实例化真实
LLM 导致无 key 环境无法启动应用；依赖不可用时返回稳定错误文本。
B3 将把本模块改造成按会话获取运行时对象。
"""

import uuid
from agents.task_classification_agent import TaskClassificationAgent
from agents.appointment_agent import AppointmentAgent
from agents.consultant_agent import ConsultantAgent

_task_agent = None


def get_task_agent():
    """惰性创建全局任务 Agent（首次调用时才实例化 LLM 依赖）。"""
    global _task_agent
    if _task_agent is None:
        session_id = str(uuid.uuid4())
        _task_agent = TaskClassificationAgent(
            AppointmentAgent(session_id=session_id),
            ConsultantAgent(session_id=session_id)
        )
    return _task_agent


def reset_task_agent():
    """重置全局 Agent（测试隔离用）。"""
    global _task_agent
    previous = _task_agent
    _task_agent = None
    return previous


async def ProcessUserInput_stream(user_input, state=None, context=None):
    """
    user_input: 用户输入
    state: 当前对话状态（如 None, 'classify', 'appointment', 'query', ...）
    context: 可选，保存多轮对话上下文（如 dict，可存储 agent 的 history 等）
    返回: (reply, next_state, next_context)
    """
    # 初始化 context
    if context is None:
        context = {}

    try:
        async for token in get_task_agent().classify_task_stream(user_input):
            yield token
    except Exception:
        # A-R1：模型等外部依赖不可用时返回稳定错误，不抛出未处理异常
        yield "[ERROR]模型或服务暂不可用，请稍后再试。"