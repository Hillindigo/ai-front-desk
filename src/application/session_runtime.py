"""会话运行时（Phase B B2）

- ``ConversationSession``：单个会话的运行时对象。持有会话元数据、自管消息
  列表（替代 LangChain InMemoryChatMessageHistory）、会话级预约草稿
  （Phase B 临时挂靠，Phase C 领域化）与 asyncio.Lock。
- ``SessionManager``：按 conversation_id 获取/创建/恢复会话对象。缓存仅作
  性能优化，数据权威在数据库；缓存失效或进程重启后从 DB 重建。

决策二（每 turn 独立 DB Session）：本模块不持有长期 SQLAlchemy Session；
所有数据库写入经由 DatabaseRouter -> ConversationRepository 的
session_scope()（每次调用独立短生命周期 Session），Agent 处理期间不持有
数据库事务。
"""

import asyncio
from typing import Any, Dict, List, Optional

from db.db_router import DatabaseRouter

_CACHE_LIMIT = 200


class ConversationSession:
    """单个会话的运行时对象。"""

    def __init__(self, conversation: Dict[str, Any]):
        self.conversation_id: str = conversation["id"]
        self.user_id: str = conversation["user_id"]
        self.channel: str = conversation.get("channel", "web")
        self.status: str = conversation.get("status", "active")
        self.last_activity_at: Optional[str] = conversation.get("last_activity_at")
        self.messages: List[Dict[str, Any]] = []
        self.appointment_draft: Dict[str, Any] = {}
        self.agent: Any = None
        self.lock = asyncio.Lock()

    def load_messages(self, messages: List[Dict[str, Any]]) -> None:
        """从数据库恢复最近消息（按 sequence 升序）。"""
        self.messages = list(messages)

    def append_message(self, message: Dict[str, Any]) -> None:
        self.messages.append(message)

    @property
    def recent_messages(self) -> List[Dict[str, Any]]:
        return list(self.messages)


class SessionManager:
    """会话运行时管理器。"""

    def __init__(self, router: Optional[DatabaseRouter] = None):
        self._router = router or DatabaseRouter()
        self._cache: Dict[str, ConversationSession] = {}

    @property
    def repository(self):
        """会话/消息 Repository（每次调用独立短生命周期 DB Session）。"""
        return self._router.conversations

    # ---------------- 创建与恢复 ----------------

    def create_conversation(self, user_id: str = "default_user", channel: str = "web") -> ConversationSession:
        """创建会话并加入缓存。"""
        conv = self.repository.create_conversation(user_id=user_id, channel=channel)
        session = ConversationSession(conv)
        self._cache[session.conversation_id] = session
        self._trim_cache()
        return session

    def get_or_create_default(self, user_id: str = "default_user") -> ConversationSession:
        """获取默认演示会话；不存在则创建（/chat/stream 兼容包装用）。"""
        conv = self.repository.get_default_conversation(user_id)
        if conv is not None:
            return self.get_or_create_session(conv["id"], user_id=user_id)
        return self.create_conversation(user_id=user_id, channel="web")

    def get_or_create_session(self, conversation_id: str, user_id: Optional[str] = None) -> ConversationSession:
        """按 ID 获取会话运行时对象；user_id 非空时校验归属。

        缓存命中直接返回；未命中则从数据库恢复（含最近消息），
        缓存失效/进程重启后可重建。
        """
        cached = self._cache.get(conversation_id)
        if cached is not None:
            if user_id is not None and cached.user_id != user_id:
                raise PermissionError(f"会话 {conversation_id} 不属于用户 {user_id}")
            return cached

        conv = self.repository.get_conversation(conversation_id, user_id=user_id)
        if conv is None:
            raise KeyError(f"会话不存在: {conversation_id}")

        session = ConversationSession(conv)
        session.load_messages(self.repository.get_recent_messages(conversation_id, limit=50))
        self._cache[conversation_id] = session
        self._trim_cache()
        return session

    # ---------------- 工具 ----------------

    def drop_cache(self, conversation_id: str) -> None:
        """移除缓存中的会话对象（测试/清理用）。"""
        self._cache.pop(conversation_id, None)

    def _trim_cache(self) -> None:
        """简单容量上限：超出后清空缓存（缓存仅是优化，重建代价低）。"""
        if len(self._cache) > _CACHE_LIMIT:
            self._cache.clear()