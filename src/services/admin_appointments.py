"""Phase G G5：商家预约查询与领域命令适配。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from db.db_router import DatabaseRouter
from db.models import Appointment, AppointmentEvent
from services.appointment_domain import AppointmentCommandService, AppointmentDomainError


class AdminAppointmentService:
    def __init__(self, db_path: Optional[str] = None):
        self._router = DatabaseRouter(db_path)
        self.session_manager = self._router.session_manager
        self.commands = AppointmentCommandService(self._router)

    def close(self) -> None:
        self.commands.close()

    def list(self, store_id: int, status: Optional[str] = None, limit: int = 100):
        with self.session_manager.session_scope() as session:
            query = session.query(Appointment).filter(Appointment.store_id == store_id)
            if status:
                query = query.filter(Appointment.status == status)
            rows = query.order_by(Appointment.start_time.asc(), Appointment.created_at.desc()).limit(
                min(max(limit, 1), 500)
            ).all()
            return [self._dict(row) for row in rows]

    def get(self, store_id: int, appointment_id: str):
        with self.session_manager.session_scope() as session:
            row = session.query(Appointment).filter_by(id=appointment_id, store_id=store_id).first()
            if row is None:
                return None
            events = session.query(AppointmentEvent).filter_by(
                appointment_id=appointment_id
            ).order_by(AppointmentEvent.id.asc()).all()
            result = self._dict(row)
            result["events"] = [
                {"event_type": event.event_type, "from_status": event.from_status,
                 "to_status": event.to_status, "request_id": event.request_id,
                 "created_at": event.created_at.isoformat() if event.created_at else None}
                for event in events
            ]
            return result

    def cancel(self, store_id, appointment_id, reason, request_id, actor_id=None):
        row = self.get(store_id, appointment_id)
        if row is None:
            return None
        return self.commands.cancel(
            appointment_id, row["user_id"], reason, request_id, actor_id=actor_id
        )

    def confirm(self, store_id, appointment_id, idempotency_key, actor_id=None):
        row = self.get(store_id, appointment_id)
        if row is None:
            return None
        return self.commands.confirm(
            appointment_id, row["user_id"], idempotency_key, actor_id=actor_id
        )

    def reschedule(self, store_id, appointment_id, start_time, end_time, request_id, actor_id=None):
        row = self.get(store_id, appointment_id)
        if row is None:
            return None
        return self.commands.reschedule(
            appointment_id, row["user_id"], start_time, end_time, request_id,
            actor_id=actor_id,
        )

    @staticmethod
    def _dict(row):
        return {
            "appointment_id": row.id, "store_id": row.store_id, "user_id": row.user_id,
            "conversation_id": row.conversation_id, "service_type": row.service_type,
            "technician_id": row.technician_id, "start_time": row.start_time.isoformat() if row.start_time else None,
            "end_time": row.end_time.isoformat() if row.end_time else None,
            "duration_minutes": row.duration_minutes, "status": row.status,
            "version": row.version, "idempotency_key": row.idempotency_key,
        }
