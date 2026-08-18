"""Phase G G4：会话工作台与人工接管服务。"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any, Dict, Optional

from db.base.session_manager import SessionManager
from db.models import (
    AuditEvent,
    Conversation,
    ConversationControl,
    ConversationControlEvent,
    Message,
)


class WorkbenchError(ValueError):
    pass


class AdminWorkbenchService:
    def __init__(self, db_path: Optional[str] = None):
        self.session_manager = SessionManager(db_path)

    def close(self) -> None:
        self.session_manager.close()

    def list_conversations(self, store_id: int, limit: int = 50) -> list[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            rows = session.query(Conversation).filter(
                Conversation.store_id == store_id
            ).order_by(Conversation.last_activity_at.desc()).limit(min(max(limit, 1), 200)).all()
            return [self._conversation_dict(row) for row in rows]

    def get_detail(self, store_id: int, conversation_id: str) -> Optional[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            conversation = session.query(Conversation).filter_by(
                id=conversation_id, store_id=store_id
            ).first()
            if conversation is None:
                return None
            control = self._ensure_control(session, conversation)
            messages = session.query(Message).filter_by(
                conversation_id=conversation_id
            ).order_by(Message.sequence.asc()).all()
            events = session.query(ConversationControlEvent).filter_by(
                conversation_id=conversation_id, store_id=store_id
            ).order_by(ConversationControlEvent.created_at.asc()).all()
            return {
                **self._conversation_dict(conversation),
                "messages": [self._message_dict(row) for row in messages],
                "control": self._control_dict(control),
                "control_events": [self._event_dict(row) for row in events],
            }

    def change_control(
        self,
        store_id: int,
        conversation_id: str,
        actor_id: int,
        mode: str,
        reason: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if mode not in {"ai_active", "human_active"}:
            raise WorkbenchError("非法会话控制状态")
        with self.session_manager.session_scope() as session:
            conversation = session.query(Conversation).filter_by(
                id=conversation_id, store_id=store_id
            ).first()
            if conversation is None:
                return None
            control = self._ensure_control(session, conversation)
            old_mode = control.mode
            control.mode = mode
            control.assignee_id = actor_id if mode == "human_active" else None
            control.updated_at = datetime.utcnow()
            action = "takeover" if mode == "human_active" else "resume_ai"
            event = ConversationControlEvent(
                id=secrets.token_urlsafe(24), conversation_id=conversation_id,
                store_id=store_id, actor_id=actor_id, action=action,
                content=reason, created_at=datetime.utcnow(),
            )
            session.add(event)
            self._audit(session, actor_id, store_id, f"conversation.{action}", conversation_id,
                        request_id, {"from_mode": old_mode, "to_mode": mode})
            session.flush()
            return self._control_dict(control)

    def add_event(
        self,
        store_id: int,
        conversation_id: str,
        actor_id: int,
        action: str,
        content: str,
        request_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if action not in {"internal_note", "flagged"}:
            raise WorkbenchError("非法会话事件")
        if not content or not content.strip():
            raise WorkbenchError("事件内容不能为空")
        with self.session_manager.session_scope() as session:
            conversation = session.query(Conversation).filter_by(
                id=conversation_id, store_id=store_id
            ).first()
            if conversation is None:
                return None
            event = ConversationControlEvent(
                id=secrets.token_urlsafe(24), conversation_id=conversation_id,
                store_id=store_id, actor_id=actor_id, action=action,
                content=content.strip(), created_at=datetime.utcnow(),
            )
            session.add(event)
            self._audit(session, actor_id, store_id, f"conversation.{action}", conversation_id,
                        request_id, {"content_length": len(content.strip())})
            session.flush()
            return self._event_dict(event)

    @staticmethod
    def _ensure_control(session, conversation) -> ConversationControl:
        control = session.query(ConversationControl).filter_by(
            conversation_id=conversation.id, store_id=conversation.store_id
        ).first()
        if control is None:
            control = ConversationControl(
                conversation_id=conversation.id, store_id=conversation.store_id,
                mode="ai_active", assignee_id=None, updated_at=datetime.utcnow(),
            )
            session.add(control)
            session.flush()
        return control

    @staticmethod
    def _audit(session, actor_id, store_id, action, resource_id, request_id, summary):
        session.add(AuditEvent(
            id=secrets.token_urlsafe(24), actor_id=actor_id, store_id=store_id,
            action=action, resource_type="conversation", resource_id=str(resource_id),
            request_id=request_id, outcome="succeeded",
            summary_json=json.dumps(summary, ensure_ascii=False), created_at=datetime.utcnow(),
        ))

    @staticmethod
    def _conversation_dict(row) -> Dict[str, Any]:
        return {
            "conversation_id": row.id, "user_id": row.user_id, "store_id": row.store_id,
            "channel": row.channel, "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "last_activity_at": row.last_activity_at.isoformat() if row.last_activity_at else None,
        }

    @staticmethod
    def _message_dict(row) -> Dict[str, Any]:
        try:
            metadata = json.loads(row.metadata_json) if row.metadata_json else None
        except (TypeError, ValueError):
            metadata = None
        return {"id": row.id, "role": row.role, "content": row.content,
                "message_type": row.message_type, "metadata": metadata,
                "sequence": row.sequence}

    @staticmethod
    def _control_dict(row) -> Dict[str, Any]:
        return {"conversation_id": row.conversation_id, "store_id": row.store_id,
                "mode": row.mode, "assignee_id": row.assignee_id,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None}

    @staticmethod
    def _event_dict(row) -> Dict[str, Any]:
        return {"event_id": row.id, "conversation_id": row.conversation_id,
                "store_id": row.store_id, "actor_id": row.actor_id,
                "action": row.action, "content": row.content,
                "created_at": row.created_at.isoformat() if row.created_at else None}
