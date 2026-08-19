"""自管消息列表（Phase B B3：替代 LangChain InMemoryChatMessageHistory）。

提供与 InputParser 兼容的最小接口（add_message/messages/clear），
消息为轻量 dict（role/content），不依赖 langchain_core 消息对象。
"""

from typing import Dict, List


def human_msg(content: str) -> Dict[str, str]:
    return {"role": "human", "content": content}


def ai_msg(content: str) -> Dict[str, str]:
    return {"role": "ai", "content": content}


class ChatHistoryBuffer:
    """进程内自管消息列表；持久化由 ConversationRepository 负责。"""

    def __init__(self, messages: List[Dict[str, str]] | None = None):
        self.messages: List[Dict[str, str]] = list(messages or [])

    def add_message(self, message: Dict[str, str]) -> None:
        self.messages.append(message)

    def clear(self) -> None:
        self.messages.clear()
