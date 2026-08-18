"""偏好 Repository（Phase E E4）。

- 同一 user_id + preference_type 只保留一个 active 值（决策四：覆盖语义）。
- 删除在同一事务内完成：置 inactive + 写墓碑 + 用户摘要失效 + 来源消息屏蔽。
- 历史旧表 user_preferences 数据可一次性迁移为 legacy_unverified（不静默提升可信度）。
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from ..base.session_manager import SessionManager
from ..models import Conversation, Message, Preference, PreferenceTombstone
from .summary_repository import SummaryRepository

# 屏蔽标记：写入 Message.metadata_json
CONTEXT_EXCLUDED_KEY = "context_excluded"
CONTEXT_EXCLUDED_REASON = "preference_tombstone"


def _preference_to_dict(row: Preference) -> Dict[str, Any]:
    return {
        "preference_id": row.id,
        "user_id": row.user_id,
        "store_id": row.store_id,
        "preference_type": row.preference_type,
        "preference_value": row.preference_value,
        "source": row.source,
        "source_message_id": row.source_message_id,
        "source_appointment_id": row.source_appointment_id,
        "confidence": row.confidence,
        "last_confirmed_at": row.last_confirmed_at.isoformat() if row.last_confirmed_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "is_active": bool(row.is_active),
        "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None,
    }


def _tombstone_to_dict(row: PreferenceTombstone) -> Dict[str, Any]:
    return {
        "tombstone_id": row.id,
        "user_id": row.user_id,
        "preference_type": row.preference_type,
        "normalized_value": row.normalized_value,
        "original_preference_id": row.original_preference_id,
        "source_message_id": row.source_message_id,
        "source_appointment_id": row.source_appointment_id,
        "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None,
    }


class PreferenceRepository:
    """长期偏好持久化仓库。"""

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    # ---------------- 读取 ----------------

    def get_active_preference(
        self,
        user_id: str,
        preference_type: str,
        store_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            query = session.query(Preference).filter(
                Preference.user_id == user_id,
                Preference.preference_type == preference_type,
                Preference.is_active == 1,
                Preference.deleted_at.is_(None),
            )
            if store_id is not None:
                query = query.filter(Preference.store_id == store_id)
            row = query.first()
            return _preference_to_dict(row) if row else None

    def get_active_preferences(self, user_id: str) -> List[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            rows = (
                session.query(Preference)
                .filter(
                    Preference.user_id == user_id,
                    Preference.is_active == 1,
                    Preference.deleted_at.is_(None),
                )
                .order_by(Preference.preference_type.asc())
                .all()
            )
            return [_preference_to_dict(r) for r in rows]

    def get_all_preferences(self, user_id: str) -> List[Dict[str, Any]]:
        """管理展示：含 inactive/未确认历史（前端只展示当前用户自己的偏好）。"""
        with self.session_manager.session_scope() as session:
            rows = (
                session.query(Preference)
                .filter(Preference.user_id == user_id)
                .order_by(Preference.updated_at.desc())
                .all()
            )
            return [_preference_to_dict(r) for r in rows]

    def get_tombstones(self, user_id: str) -> List[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            rows = (
                session.query(PreferenceTombstone)
                .filter(PreferenceTombstone.user_id == user_id)
                .order_by(PreferenceTombstone.deleted_at.desc())
                .all()
            )
            return [_tombstone_to_dict(r) for r in rows]

    # ---------------- 写入（覆盖语义） ----------------

    def set_preference(
        self,
        user_id: str,
        preference_type: str,
        preference_value: str,
        source: str,
        source_message_id: Optional[str] = None,
        source_appointment_id: Optional[str] = None,
        confidence: int = 100,
        last_confirmed_at: Optional[datetime] = None,
        store_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """原子覆盖：同一 user_id+preference_type 的旧 active 值停用并写入新值。

        返回新偏好 dict；`last_confirmed_at` 缺省使用当前时间（显式确认路径）。
        """
        now = last_confirmed_at or datetime.utcnow()
        with self.session_manager.session_scope() as session:
            # 停用旧 active（覆盖语义）
            query = session.query(Preference).filter(
                Preference.user_id == user_id,
                Preference.preference_type == preference_type,
                Preference.is_active == 1,
                Preference.deleted_at.is_(None),
            )
            if store_id is not None:
                query = query.filter(Preference.store_id == store_id)
            query.update({"is_active": 0}, synchronize_session=False)

            row = Preference(
                user_id=user_id,
                store_id=store_id,
                preference_type=preference_type,
                preference_value=preference_value,
                source=source,
                source_message_id=source_message_id,
                source_appointment_id=source_appointment_id,
                confidence=confidence,
                last_confirmed_at=now,
                is_active=1,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return _preference_to_dict(row)

    # ---------------- 删除（墓碑 + 摘要失效 + 消息屏蔽，同一事务） ----------------

    def atomic_delete(self, user_id: str, preference_type: str) -> Optional[Dict[str, Any]]:
        """删除偏好：置 inactive + 写墓碑 + 该用户摘要失效 + 屏蔽来源/记忆引用消息。

        全部在同一数据库事务内完成；不存在 active 偏好时返回 None（幂等成功语义）。
        """
        with self.session_manager.session_scope() as session:
            row = (
                session.query(Preference)
                .filter(
                    Preference.user_id == user_id,
                    Preference.preference_type == preference_type,
                    Preference.is_active == 1,
                    Preference.deleted_at.is_(None),
                )
                .first()
            )
            if row is None:
                return None

            now = datetime.utcnow()
            row.is_active = 0
            row.deleted_at = now

            tombstone = PreferenceTombstone(
                user_id=user_id,
                preference_type=preference_type,
                normalized_value=row.preference_value.strip(),
                original_preference_id=row.id,
                source_message_id=row.source_message_id,
                source_appointment_id=row.source_appointment_id,
                deleted_at=now,
            )
            session.add(tombstone)

            # 该用户所有会话摘要失效（决策三第 2 步）
            conv_ids = [c.id for c in session.query(Conversation.id).filter(Conversation.user_id == user_id)]
            if conv_ids:
                from ..models import ConversationSummary

                session.query(ConversationSummary).filter(
                    ConversationSummary.conversation_id.in_(conv_ids),
                    ConversationSummary.status == "active",
                ).update({"status": "invalidated"}, synchronize_session=False)

                # 屏蔽来源消息 + 同会话紧随其后的记忆引用消息（决策三第 3 步）
                target_ids = []
                if row.source_message_id:
                    target_ids.append(row.source_message_id)
                if conv_ids:
                    follow_ups = (
                        session.query(Message)
                        .filter(
                            Message.conversation_id.in_(conv_ids),
                            Message.role == "assistant",
                        )
                        .order_by(Message.created_at.desc())
                        .limit(5)
                        .all()
                    )
                    for m in follow_ups:
                        meta = {}
                        if m.metadata_json:
                            try:
                                meta = json.loads(m.metadata_json) or {}
                            except (ValueError, TypeError):
                                meta = {}
                        if meta.get("preference_type") == preference_type or meta.get("memorized"):
                            target_ids.append(str(m.id))
                            break
                if target_ids:
                    for msg in session.query(Message).filter(Message.id.in_(target_ids)):
                        meta = {}
                        if msg.metadata_json:
                            try:
                                meta = json.loads(msg.metadata_json) or {}
                            except (ValueError, TypeError):
                                meta = {}
                        meta[CONTEXT_EXCLUDED_KEY] = True
                        meta["context_excluded_reason"] = CONTEXT_EXCLUDED_REASON
                        meta["tombstone_ref"] = str(tombstone.id)
                        msg.metadata_json = json.dumps(meta, ensure_ascii=False)

            session.flush()
            return _tombstone_to_dict(tombstone)

    # ---------------- 历史兼容与迁移 ----------------

    def migrate_legacy(self) -> int:
        """将旧 user_preferences 一次性迁移为新表 legacy_unverified 记录。

        旧表数据保留不动（约束 9）；迁移记录来源未确认，默认不注入长期上下文。
        """
        from ..models import UserPreference

        migrated = 0
        with self.session_manager.session_scope() as session:
            legacy_rows = session.query(UserPreference).all()
            for legacy in legacy_rows:
                exists = (
                    session.query(Preference)
                    .filter(
                        Preference.user_id == legacy.user_id,
                        Preference.preference_type == legacy.preference_type,
                        Preference.source == "legacy_unverified",
                    )
                    .first()
                )
                if exists:
                    continue
                session.add(
                    Preference(
                        user_id=legacy.user_id,
                        preference_type=legacy.preference_type,
                        preference_value=legacy.preference_value,
                        source="legacy_unverified",
                        confidence=1,  # 低可信：不静默提升
                        is_active=0,   # 默认不注入，直到用户重新确认
                    )
                )
                migrated += 1
        return migrated