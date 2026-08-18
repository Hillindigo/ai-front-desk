"""Phase G G7：审计查询与基础运营指标。"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from typing import Optional

from sqlalchemy import func

from db.base.session_manager import SessionManager
from db.models import (
    Appointment,
    AuditEvent,
    ConversationControlEvent,
    FollowUpTask,
)


class AuditMetricsService:
    def __init__(self, db_path: Optional[str] = None):
        self.session_manager = SessionManager(db_path)

    def close(self):
        self.session_manager.close()

    def list_audit(self, store_id, actor_id=None, action=None, outcome=None, limit=100):
        with self.session_manager.session_scope() as session:
            query = session.query(AuditEvent).filter(AuditEvent.store_id == store_id)
            if actor_id is not None:
                query = query.filter(AuditEvent.actor_id == actor_id)
            if action:
                query = query.filter(AuditEvent.action == action)
            if outcome:
                query = query.filter(AuditEvent.outcome == outcome)
            rows = query.order_by(AuditEvent.created_at.desc()).limit(min(max(limit, 1), 500)).all()
            return [self._audit_dict(row) for row in rows]

    def metrics(self, store_id):
        with self.session_manager.session_scope() as session:
            audit_rows = session.query(AuditEvent).filter_by(store_id=store_id).all()
            action_counts = Counter(row.action for row in audit_rows)
            appointment_rows = session.query(Appointment.status, func.count(Appointment.id)).filter_by(
                store_id=store_id
            ).group_by(Appointment.status).all()
            follow_rows = session.query(FollowUpTask.status, func.count(FollowUpTask.id)).filter_by(
                store_id=store_id
            ).group_by(FollowUpTask.status).all()
            control_rows = session.query(ConversationControlEvent.action, func.count(
                ConversationControlEvent.id
            )).filter_by(store_id=store_id).group_by(ConversationControlEvent.action).all()
            return {
                "store_id": store_id,
                "audit_action_counts": dict(action_counts),
                "appointment_status_counts": {status: count for status, count in appointment_rows},
                "follow_up_status_counts": {status: count for status, count in follow_rows},
                "conversation_control_counts": {action: count for action, count in control_rows},
                "definitions": {
                    "audit_action_counts": "当前门店 AuditEvent 按 action 计数",
                    "appointment_status_counts": "当前门店 appointments 按 status 计数",
                    "follow_up_status_counts": "当前门店 follow_up_tasks 按 status 计数",
                    "conversation_control_counts": "当前门店会话控制事件按 action 计数",
                },
            }

    @staticmethod
    def _audit_dict(row):
        try:
            summary = json.loads(row.summary_json) if row.summary_json else {}
        except (ValueError, TypeError):
            summary = {}
        return {
            "event_id": row.id, "actor_id": row.actor_id, "store_id": row.store_id,
            "action": row.action, "resource_type": row.resource_type,
            "resource_id": row.resource_id, "request_id": row.request_id,
            "outcome": row.outcome, "summary": summary,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
