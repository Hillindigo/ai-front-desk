"""Phase G G6：客户运营与回访任务服务。"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Optional

from db.base.session_manager import SessionManager
from db.models import (
    Appointment,
    AuditEvent,
    Conversation,
    FollowUpTask,
    Preference,
)


class CustomerOpsError(ValueError):
    pass


class AdminCustomerService:
    def __init__(self, db_path: Optional[str] = None):
        self.session_manager = SessionManager(db_path)

    def close(self):
        self.session_manager.close()

    def list_customers(self, store_id: int):
        with self.session_manager.session_scope() as session:
            users = session.query(Conversation.user_id).filter(
                Conversation.store_id == store_id
            ).distinct().all()
            return [{"customer_user_id": user_id} for (user_id,) in users]

    def get_customer(self, store_id: int, customer_user_id: str):
        with self.session_manager.session_scope() as session:
            conversations = session.query(Conversation).filter_by(
                store_id=store_id, user_id=customer_user_id
            ).order_by(Conversation.updated_at.desc()).all()
            if not conversations:
                return None
            appointments = session.query(Appointment).filter_by(
                store_id=store_id, user_id=customer_user_id
            ).order_by(Appointment.created_at.desc()).all()
            prefs = session.query(Preference).filter_by(
                store_id=store_id, user_id=customer_user_id, is_active=1
            ).all()
            return {
                "customer_user_id": customer_user_id,
                "store_id": store_id,
                "conversation_count": len(conversations),
                "appointment_history": [
                    {"appointment_id": row.id, "status": row.status,
                     "service_type": row.service_type,
                     "start_time": row.start_time.isoformat() if row.start_time else None}
                    for row in appointments
                ],
                "preferences": [
                    {"preference_type": row.preference_type,
                     "preference_value": row.preference_value,
                     "source": row.source}
                    for row in prefs
                ],
            }

    def create_follow_up(self, store_id, actor_id, customer_user_id, values, request_id=None):
        reason = str(values.get("reason") or "").strip()
        if not customer_user_id or not reason:
            raise CustomerOpsError("客户和回访原因不能为空")
        with self.session_manager.session_scope() as session:
            exists = session.query(Conversation.id).filter_by(
                store_id=store_id, user_id=customer_user_id
            ).first()
            if not exists:
                raise CustomerOpsError("客户不属于当前门店")
            task = FollowUpTask(
                id=secrets.token_urlsafe(24), store_id=store_id,
                customer_user_id=customer_user_id, assignee_id=values.get("assignee_id"),
                reason=reason, due_at=values.get("due_at"), status="open",
                source_type=values.get("source_type"), source_id=values.get("source_id"),
                created_by=actor_id,
            )
            session.add(task)
            session.flush()
            self._audit(session, actor_id, store_id, "follow_up.created", task.id, request_id)
            return self._task_dict(task)

    def list_follow_ups(self, store_id, customer_user_id=None):
        with self.session_manager.session_scope() as session:
            query = session.query(FollowUpTask).filter(FollowUpTask.store_id == store_id)
            if customer_user_id:
                query = query.filter(FollowUpTask.customer_user_id == customer_user_id)
            rows = query.order_by(FollowUpTask.created_at.desc()).all()
            return [self._task_dict(row) for row in rows]

    def change_task(self, store_id, actor_id, task_id, status, request_id=None):
        if status not in {"completed", "cancelled"}:
            raise CustomerOpsError("非法回访任务状态")
        with self.session_manager.session_scope() as session:
            task = session.query(FollowUpTask).filter_by(id=task_id, store_id=store_id).first()
            if task is None:
                return None
            task.status = status
            task.completed_at = datetime.utcnow() if status == "completed" else None
            task.updated_at = datetime.utcnow()
            session.flush()
            self._audit(session, actor_id, store_id, f"follow_up.{status}", task.id, request_id)
            return self._task_dict(task)

    @staticmethod
    def _audit(session, actor_id, store_id, action, resource_id, request_id):
        session.add(AuditEvent(
            id=secrets.token_urlsafe(24), actor_id=actor_id, store_id=store_id,
            action=action, resource_type="follow_up_task", resource_id=str(resource_id),
            request_id=request_id, outcome="succeeded", summary_json="{}",
            created_at=datetime.utcnow(),
        ))

    @staticmethod
    def _task_dict(row):
        return {
            "task_id": row.id, "store_id": row.store_id,
            "customer_user_id": row.customer_user_id, "assignee_id": row.assignee_id,
            "reason": row.reason, "due_at": row.due_at.isoformat() if row.due_at else None,
            "status": row.status, "source_type": row.source_type, "source_id": row.source_id,
            "created_by": row.created_by,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
