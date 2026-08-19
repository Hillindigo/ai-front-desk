"""会话与消息 Repository（Phase B B1）

职责：
- Conversation 创建、归属校验查询、最近活动更新。
- Message 按会话追加（会话内序列号）、最近 N 条恢复、顺序读取。
- 所有方法返回稳定 dict 结构，不暴露 ORM 对象生命周期。

关键约束（计划 3.1/3.2）：
- 消息必须绑定 conversation_id，禁止写入无归属消息。
- 查询会话必须同时校验 user_id 归属。
- 消息写入与 conversation 的 updated_at / last_activity_at 在同一事务内完成。
"""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..base.session_manager import SessionManager
from ..models import Conversation, Message
from db.store_scope import resolve_store_id


def _conversation_to_dict(conv: Conversation) -> Dict[str, Any]:
    return {
        "id": conv.id,
        "user_id": conv.user_id,
        "store_id": conv.store_id,
        "channel": conv.channel,
        "status": conv.status,
        "active_workflow": conv.active_workflow,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
        "last_activity_at": conv.last_activity_at.isoformat() if conv.last_activity_at else None,
    }


def _message_to_dict(msg: Message) -> Dict[str, Any]:
    metadata = None
    if msg.metadata_json:
        try:
            metadata = json.loads(msg.metadata_json)
        except (ValueError, TypeError):
            metadata = None
    return {
        "id": msg.id,
        "conversation_id": msg.conversation_id,
        "role": msg.role,
        "content": msg.content,
        "message_type": msg.message_type,
        "metadata": metadata,
        "sequence": msg.sequence,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }


class ConversationRepository:
    """会话与消息持久化仓库。"""

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    # ---------------- Conversation ----------------

    def create_conversation(
        self,
        user_id: str = "default_user",
        channel: str = "web",
        store_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """创建会话，返回新会话 dict。"""
        conv = Conversation(
            id=str(uuid.uuid4()), user_id=user_id, store_id=store_id,
            channel=channel, status="active",
        )
        with self.session_manager.session_scope() as session:
            conv.store_id = resolve_store_id(session, store_id)
            session.add(conv)
            session.flush()
            session.refresh(conv)
            return _conversation_to_dict(conv)

    def get_conversation(
        self,
        conversation_id: str,
        user_id: Optional[str] = None,
        store_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """按 ID 获取会话；user_id 非空时同时校验归属。返回 dict 或 None。"""
        with self.session_manager.session_scope() as session:
            query = session.query(Conversation).filter(Conversation.id == conversation_id)
            if user_id is not None:
                query = query.filter(Conversation.user_id == user_id)
            if store_id is not None:
                query = query.filter(Conversation.store_id == store_id)
            conv = query.first()
            if conv is None:
                return None
            session.refresh(conv)
            return _conversation_to_dict(conv)

    def get_default_conversation(self, user_id: str = "default_user") -> Optional[Dict[str, Any]]:
        """获取该用户的默认演示会话（最早创建的 active web 会话），没有则 None。

        供 /chat/stream 兼容包装使用（无 conversation_id 时落到默认会话）。
        """
        with self.session_manager.session_scope() as session:
            conv = (
                session.query(Conversation)
                .filter(
                    Conversation.user_id == user_id,
                    Conversation.status == "active",
                    Conversation.channel == "web",
                )
                .order_by(Conversation.created_at.asc())
                .first()
            )
            if conv is None:
                return None
            session.refresh(conv)
            return _conversation_to_dict(conv)

    def touch_conversation(self, conversation_id: str) -> bool:
        """更新会话活动时间（updated_at/last_activity_at）。"""
        now = datetime.utcnow()
        with self.session_manager.session_scope() as session:
            conv = session.query(Conversation).filter(Conversation.id == conversation_id).first()
            if conv is None:
                return False
            conv.updated_at = now
            conv.last_activity_at = now
            return True

    # ---------------- Message ----------------

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """向会话追加一条消息（同一事务更新会话活动时间）。

        会话不存在时返回 None（禁止写入无归属消息）。
        """
        with self.session_manager.session_scope() as session:
            return self.add_message_in_session(
                session,
                conversation_id,
                role,
                content,
                message_type=message_type,
                metadata=metadata,
            )

    def add_message_in_session(
        self,
        session,
        conversation_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """在调用方事务中追加消息，并更新会话活动时间。

        需要把消息与其他领域事实（例如人工接管、控制事件和审计）
        原子提交时使用此方法；调用方负责提交或回滚事务。
        """
        now = datetime.utcnow()
        conv = session.query(Conversation).filter(Conversation.id == conversation_id).first()
        if conv is None:
            return None

        max_seq = (
            session.query(Message.sequence)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.sequence.desc())
            .first()
        )
        next_seq = (max_seq[0] + 1) if max_seq else 1

        msg = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            message_type=message_type,
            metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
            sequence=next_seq,
            created_at=now,
        )
        session.add(msg)
        conv.updated_at = now
        conv.last_activity_at = now
        session.flush()
        session.refresh(msg)
        return _message_to_dict(msg)

    def get_turn_by_request_id(
        self,
        conversation_id: str,
        request_id: str,
    ) -> Optional[Dict[str, Any]]:
        """查找已处理的请求，用于 turns 重试去重。

        请求 ID 保存在 user 消息 metadata 中，assistant 消息紧随其后。
        该查询只依赖持久化消息，不依赖进程内缓存，因此同一进程重建会话后
        仍能返回原结果。跨进程一致性仍由 Phase D 的 SQLite 单进程边界约束。
        """
        if not request_id:
            return None
        with self.session_manager.session_scope() as session:
            rows = (
                session.query(Message)
                .filter(
                    Message.conversation_id == conversation_id,
                    Message.role == "user",
                )
                .order_by(Message.sequence.desc())
                .all()
            )
            for user_row in rows:
                metadata = {}
                if user_row.metadata_json:
                    try:
                        metadata = json.loads(user_row.metadata_json) or {}
                    except (ValueError, TypeError):
                        metadata = {}
                if metadata.get("client_request_id") != request_id:
                    continue

                assistant_row = (
                    session.query(Message)
                    .filter(
                        Message.conversation_id == conversation_id,
                        Message.role == "assistant",
                        Message.sequence > user_row.sequence,
                    )
                    .order_by(Message.sequence.asc())
                    .first()
                )
                if assistant_row is None:
                    # 用户消息已经落库但本轮未完成，允许客户端重试恢复执行。
                    return None
                return {
                    "user": _message_to_dict(user_row),
                    "assistant": _message_to_dict(assistant_row) if assistant_row else None,
                }
        return None

    def get_recent_messages(self, conversation_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """按会话返回最近 N 条消息，按 sequence 升序（恢复历史用）。"""
        with self.session_manager.session_scope() as session:
            rows = (
                session.query(Message)
                .filter(Message.conversation_id == conversation_id)
                .order_by(Message.sequence.desc())
                .limit(limit)
                .all()
            )
            rows.reverse()
            return [_message_to_dict(m) for m in rows]

    def get_messages_after(self, conversation_id: str, after_sequence: int) -> List[Dict[str, Any]]:
        """返回 sequence 大于 after_sequence 的消息（增量读取）。"""
        with self.session_manager.session_scope() as session:
            rows = (
                session.query(Message)
                .filter(
                    Message.conversation_id == conversation_id,
                    Message.sequence > after_sequence,
                )
                .order_by(Message.sequence.asc())
                .all()
            )
            return [_message_to_dict(m) for m in rows]
