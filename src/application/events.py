"""SSE 事件流（Phase D D4）。

- EventStream：按序生成 EventEnvelope（run_started 首发，唯一终止事件收尾）。
- 事件只描述当前轮次（决策二）：run_id 生命周期覆盖单个 turns 请求。
- 心跳：长时间无增量时发送 SSE 注释行保持连接（本地场景通常不需要）。
"""

import json
import logging
from typing import Any, AsyncGenerator, Dict, Optional

from application.contracts import EventEnvelope, EventType, TERMINAL_EVENTS

logger = logging.getLogger(__name__)

# [THOUGHT]/[REPLY]/[SIGNAL] 是旧内部协议，规范路径不允许外泄
_INTERNAL_MARKERS = ("[THOUGHT]", "[SIGNAL]", "[REPLY]", "[ERROR]")


def clean_token(token: str) -> Optional[str]:
    """清洗 Agent 内部 token：旧标记剥离为纯用户可见文本。

    - [THOUGHT]... / [SIGNAL]... 行：不进入事件流（隐藏推理不外泄）。
    - [REPLY]xxx：剥离标记，只保留用户可见内容。
    - [ERROR]...：保留（兼容错误标记，D6 统一错误码后移除）。
    - 其他：原样。
    """
    if not token:
        return None
    stripped = token.strip()
    if not stripped:
        return None
    if stripped.startswith("[THOUGHT]") or stripped.startswith("[SIGNAL]"):
        return None
    if stripped.startswith("[REPLY]"):
        return stripped[len("[REPLY]"):]
    return token


def sse_frame(event: EventEnvelope) -> str:
    """SSE framing：event + data 两行，空行结束。"""
    data = json.dumps(event.to_dict(), ensure_ascii=False)
    return f"event: {event.type.value}\ndata: {data}\n\n"


def sse_heartbeat() -> str:
    return ": ping\n\n"


class EventStream:
    """单轮事件流生成器（决策二：只描述当前轮次）。"""

    def __init__(self, run_id: str, conversation_id: str):
        self.run_id = run_id
        self.conversation_id = conversation_id
        self.sequence = 0
        self._terminal_sent = False

    def next(self, type: EventType, data: Optional[Dict[str, Any]] = None) -> EventEnvelope:
        self.sequence += 1
        return EventEnvelope(
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            sequence=self.sequence,
            type=type,
            data=data,
        )

    @property
    def terminal_sent(self) -> bool:
        return self._terminal_sent

    def mark_terminal(self) -> None:
        self._terminal_sent = True
