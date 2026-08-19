"""摘要 Repository（Phase E E3）。

- 摘要覆盖范围以消息 sequence 为准；写前不覆盖旧版本（历史保留，可回退）。
- 查询永远只返回"最新有效"快照；invalidated/failed 不进 ContextPackage。
- invalidate_all_for_user 供 E4 偏好删除时使该用户所有会话摘要失效。
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..base.session_manager import SessionManager
from ..models import Conversation, ConversationSummary


def _summary_to_dict(row: ConversationSummary) -> Dict[str, Any]:
    key_facts = None
    if row.key_facts is not None:
        try:
            key_facts = json.loads(row.key_facts) if isinstance(row.key_facts, str) else row.key_facts
        except (ValueError, TypeError):
            key_facts = None
    return {
        "summary_id": row.id,
        "conversation_id": row.conversation_id,
        "from_sequence": row.from_sequence,
        "to_sequence": row.to_sequence,
        "content": row.content,
        "key_facts": key_facts or [],
        "status": row.status,
        "version": row.version,
        "model_provider": row.model_provider,
        "failure_log_id": row.failure_log_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class SummaryRepository:
    """会话摘要持久化仓库。"""

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    def add_snapshot(
        self,
        conversation_id: str,
        from_sequence: int,
        to_sequence: int,
        content: str,
        key_facts: Optional[List[Dict[str, Any]]] = None,
        status: str = "active",
        version: int = 1,
        model_provider: str = "fake",
        failure_log_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """写入新版本摘要。会话不存在返回 None（禁止无归属摘要）。"""
        now = datetime.utcnow()
        with self.session_manager.session_scope() as session:
            conv = session.query(Conversation).filter(Conversation.id == conversation_id).first()
            if conv is None:
                return None
            row = ConversationSummary(
                conversation_id=conversation_id,
                from_sequence=from_sequence,
                to_sequence=to_sequence,
                content=content,
                key_facts=json.dumps(key_facts or [], ensure_ascii=False),
                status=status,
                version=version,
                model_provider=model_provider,
                failure_log_id=failure_log_id,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _summary_to_dict(row)

    def get_latest(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """最新版本（任意状态；诊断/回退用）。"""
        with self.session_manager.session_scope() as session:
            row = (
                session.query(ConversationSummary)
                .filter(ConversationSummary.conversation_id == conversation_id)
                .order_by(ConversationSummary.version.desc())
                .first()
            )
            if row is None:
                return None
            return _summary_to_dict(row)

    def get_latest_active(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """最新 ACTIVE 快照（唯一有效快照规则）。"""
        with self.session_manager.session_scope() as session:
            row = (
                session.query(ConversationSummary)
                .filter(
                    ConversationSummary.conversation_id == conversation_id,
                    ConversationSummary.status == "active",
                )
                .order_by(ConversationSummary.version.desc())
                .first()
            )
            if row is None:
                return None
            return _summary_to_dict(row)

    def get_active_history(self, conversation_id: str) -> List[Dict[str, Any]]:
        """该会话全部 active 版本（按版本升序，测试/审计用）。"""
        with self.session_manager.session_scope() as session:
            rows = (
                session.query(ConversationSummary)
                .filter(
                    ConversationSummary.conversation_id == conversation_id,
                    ConversationSummary.status == "active",
                )
                .order_by(ConversationSummary.version.asc())
                .all()
            )
            return [_summary_to_dict(r) for r in rows]

    def invalidate_all_for_user(self, user_id: str) -> int:
        """使该用户所有会话的 active 摘要失效（E4 偏好删除的级联）。返回受影响行数。"""
        with self.session_manager.session_scope() as session:
            conv_ids = [c.id for c in session.query(Conversation.id).filter(Conversation.user_id == user_id)]
            if not conv_ids:
                return 0
            result = (
                session.query(ConversationSummary)
                .filter(
                    ConversationSummary.conversation_id.in_(conv_ids),
                    ConversationSummary.status == "active",
                )
                .update({"status": "invalidated"}, synchronize_session=False)
            )
            return result

    def count_active(self, conversation_id: str) -> int:
        with self.session_manager.session_scope() as session:
            return (
                session.query(ConversationSummary)
                .filter(
                    ConversationSummary.conversation_id == conversation_id,
                    ConversationSummary.status == "active",
                )
                .count()
            )