"""预约领域服务与状态机（Phase C C4）。

确定性业务规则，不依赖 LLM：
- 状态迁移合法性由显式状态机表控制。
- 必需字段校验、时间校验、可用性校验由领域服务负责。
- 确认/改约在 BEGIN IMMEDIATE 事务内完成"冲突检查 + 状态迁移 + 事件"。
- 幂等：confirm 支持 idempotency_key，重复提交返回原预约；同键不同业务返回冲突。
"""

import uuid
import json
from datetime import datetime
from typing import Any, Dict, Optional

from db.db_router import DatabaseRouter
from db.models import Appointment, AppointmentEvent, AuditEvent, TechnicianSchedule
from db.repositories.appointment_repository import (
    ActiveDraftOwnershipError,
    _appointment_to_dict,
)


class AppointmentDomainError(Exception):
    """预约领域错误（稳定错误码）。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


# 合法状态迁移表（终态不可迁出）
TRANSITIONS: Dict[str, set] = {
    "draft": {"pending_confirmation", "cancelled", "expired"},
    "pending_confirmation": {"confirmed", "draft", "cancelled", "expired"},
    "confirmed": {"cancelled", "confirmed"},  # confirmed->confirmed 表示改约
    "cancelled": set(),
    "expired": set(),
}

# 已确认预约的必需字段
CONFIRM_REQUIRED_FIELDS = ("service_type", "technician_id", "start_time", "end_time", "duration_minutes")


def validate_time(start_time: datetime, end_time: datetime) -> None:
    """半开区间时间校验：end <= start 拒绝（计划 4.2）。"""
    if start_time is None or end_time is None:
        raise AppointmentDomainError("APPOINTMENT_TIME_INVALID", "缺少开始或结束时间")
    if end_time <= start_time:
        raise AppointmentDomainError("APPOINTMENT_TIME_INVALID", "结束时间必须晚于开始时间")


def can_transition(from_status: str, to_status: str) -> bool:
    return to_status in TRANSITIONS.get(from_status, set())


class AppointmentCommandService:
    """预约命令服务：领域规则的唯一执行者。"""

    def __init__(self, router: Optional[DatabaseRouter] = None):
        self._router = router or DatabaseRouter()
        self.repo = self._router.appointments
        # 延迟导入避免循环依赖（AppointmentService 用于可用性检查）
        self._availability_service = None

    def _availability(self):
        if self._availability_service is None:
            from services.appointment_service import AppointmentService
            self._availability_service = AppointmentService()
        return self._availability_service

    def close(self):
        self._router.close()

    # ---------------- 草稿 ----------------

    def create_draft(
        self,
        user_id: str,
        service_type: str,
        conversation_id: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
        ttl_hours: int = 24,
    ) -> Dict[str, Any]:
        """创建草稿；带会话时复用该会话唯一活跃草稿。"""
        if conversation_id is not None:
            return self.upsert_active_draft(
                user_id=user_id,
                conversation_id=conversation_id,
                service_type=service_type,
                fields=fields,
                ttl_hours=ttl_hours,
            )
        return self.repo.create_draft(
            user_id=user_id,
            conversation_id=conversation_id,
            service_type=service_type,
            fields=fields,
            ttl_hours=ttl_hours,
        )

    def upsert_active_draft(
        self,
        user_id: str,
        conversation_id: str,
        service_type: str,
        fields: Optional[Dict[str, Any]] = None,
        ttl_hours: int = 24,
    ) -> Dict[str, Any]:
        """原子复用会话唯一活跃草稿。"""
        try:
            return self.repo.upsert_active_draft(
                user_id=user_id,
                conversation_id=conversation_id,
                service_type=service_type,
                fields=fields,
                ttl_hours=ttl_hours,
            )
        except ActiveDraftOwnershipError as exc:
            raise AppointmentDomainError(
                "APPOINTMENT_NOT_FOUND", "会话中的活跃预约不属于当前用户"
            ) from exc

    def update_draft(
        self, appointment_id: str, user_id: str, fields: Dict[str, Any],
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """更新草稿（白名单字段 + 版本递增）。"""
        result = self.repo.update_draft(appointment_id, user_id, fields, expected_version)
        if result is None:
            raise AppointmentDomainError("APPOINTMENT_NOT_FOUND", "草稿不存在或版本冲突")
        return result

    def get_active_draft(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        return self.repo.get_active_draft(conversation_id)

    # ---------------- 待确认 ----------------

    def request_confirmation(self, appointment_id: str, user_id: str) -> Dict[str, Any]:
        """draft -> pending_confirmation：校验字段、时间、可用性后迁移。"""
        appt = self._get_owned(appointment_id, user_id)
        if not can_transition(appt["status"], "pending_confirmation"):
            raise AppointmentDomainError(
                "APPOINTMENT_INVALID_STATE",
                f"状态 {appt['status']} 不允许进入待确认",
            )
        self._validate_required_fields(appt)
        validate_time(appt["start_time"], appt["end_time"])
        availability = self._availability().check_technician_availability(
            appt["technician_id"], appt["start_time"], appt["end_time"]
        )
        if not availability["available"]:
            raise AppointmentDomainError(availability["reason"], "当前时段不可用")
        result = self.repo.transition(
            appointment_id, user_id,
            to_status="pending_confirmation", event_type="fields_complete",
        )
        return result

    # ---------------- 确认（幂等 + 事务） ----------------

    def confirm(
        self, appointment_id: str, user_id: str,
        idempotency_key: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """pending_confirmation -> confirmed：BEGIN IMMEDIATE 事务内冲突检查+迁移+事件。

        幂等：相同 (user_id, idempotency_key) 已存在且为同一预约 -> 返回原结果；
        同键不同预约 -> IDEMPOTENCY_CONFLICT。
        """
        appt = self._get_owned(appointment_id, user_id)

        # 幂等优先：同 (user_id, idempotency_key) 已有记录直接返回/冲突
        # （必须在状态校验之前：重复确认时预约已是 confirmed）
        if idempotency_key:
            existing = self.repo.get_by_idempotency(user_id, idempotency_key)
            if existing is not None:
                if existing["id"] == appointment_id:
                    return existing  # 幂等返回原结果
                raise AppointmentDomainError(
                    "IDEMPOTENCY_CONFLICT", "幂等键已被其他预约使用"
                )

        if appt["status"] != "pending_confirmation":
            raise AppointmentDomainError(
                "APPOINTMENT_INVALID_STATE",
                f"只有待确认预约可以确认，当前状态 {appt['status']}",
            )

        def _confirm_in_tx(session):
            row = session.query(Appointment).filter(Appointment.id == appointment_id).first()
            schedule_conflict = (
                session.query(TechnicianSchedule)
                .filter(
                    TechnicianSchedule.technician_id == row.technician_id,
                    TechnicianSchedule.status == "busy",
                    TechnicianSchedule.start_time < row.end_time,
                    TechnicianSchedule.end_time > row.start_time,
                )
                .first()
            )
            if schedule_conflict:
                raise AppointmentDomainError(
                    "TECHNICIAN_UNAVAILABLE", "服务人员在该时段不可用"
                )
            conflicts = (
                session.query(Appointment)
                .filter(
                    Appointment.technician_id == row.technician_id,
                    Appointment.status == "confirmed",
                    Appointment.id != row.id,
                    Appointment.start_time < row.end_time,
                    Appointment.end_time > row.start_time,
                )
                .count()
            )
            if conflicts:
                raise AppointmentDomainError("APPOINTMENT_CONFLICT", "该时段已被占用")
            from_status = row.status
            row.status = "confirmed"
            row.version += 1
            row.updated_at = datetime.utcnow()
            if idempotency_key and row.idempotency_key is None:
                row.idempotency_key = idempotency_key
            session.add(
                AppointmentEvent(
                    appointment_id=row.id,
                    event_type="confirmed",
                    from_status=from_status,
                    to_status="confirmed",
                    request_id=idempotency_key,
                )
            )
            if actor_id is not None:
                session.add(AuditEvent(
                    id=uuid.uuid4().hex,
                    actor_id=actor_id,
                    store_id=row.store_id,
                    action="appointment.confirmed",
                    resource_type="appointment",
                    resource_id=str(row.id),
                    request_id=idempotency_key,
                    outcome="succeeded",
                    summary_json=json.dumps({"status": row.status}, ensure_ascii=False),
                ))
            session.flush()
            session.refresh(row)
            return _appointment_to_dict(row)

        return self.repo.run_in_immediate_transaction(_confirm_in_tx)

    # ---------------- 取消 ----------------

    def cancel(
        self, appointment_id: str, user_id: str,
        reason: Optional[str] = None, request_id: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """任意非终态 -> cancelled（重复取消幂等返回当前状态）。"""
        appt = self._get_owned(appointment_id, user_id)
        if appt["status"] in ("cancelled", "expired"):
            return appt  # 幂等：已取消直接返回
        if not can_transition(appt["status"], "cancelled"):
            raise AppointmentDomainError(
                "APPOINTMENT_INVALID_STATE", f"状态 {appt['status']} 不允许取消"
            )
        result = self.repo.transition(
            appointment_id, user_id,
            to_status="cancelled", event_type="cancelled",
            request_id=request_id,
            payload={"reason": reason} if reason else None,
            extra_fields={"cancel_reason": reason} if reason else None,
            audit_event={
                "actor_id": actor_id,
                "action": "appointment.cancelled",
                "request_id": request_id,
                "summary": {"reason": reason} if reason else {},
            } if actor_id is not None else None,
        )
        return result

    # ---------------- 改约 ----------------

    def reschedule(
        self, appointment_id: str, user_id: str,
        new_start_time: datetime, new_end_time: datetime,
        request_id: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """confirmed -> confirmed（改约）：新时间校验 + 事务内冲突检查（排除自身）。"""
        appt = self._get_owned(appointment_id, user_id)
        if appt["status"] != "confirmed":
            raise AppointmentDomainError(
                "APPOINTMENT_INVALID_STATE", "只有已确认预约可以改约"
            )
        validate_time(new_start_time, new_end_time)

        def _reschedule_in_tx(session):
            row = session.query(Appointment).filter(Appointment.id == appointment_id).first()
            schedule_conflict = (
                session.query(TechnicianSchedule)
                .filter(
                    TechnicianSchedule.technician_id == row.technician_id,
                    TechnicianSchedule.status == "busy",
                    TechnicianSchedule.start_time < new_end_time,
                    TechnicianSchedule.end_time > new_start_time,
                )
                .first()
            )
            if schedule_conflict:
                raise AppointmentDomainError(
                    "TECHNICIAN_UNAVAILABLE", "服务人员在新时段不可用"
                )
            conflicts = (
                session.query(Appointment)
                .filter(
                    Appointment.technician_id == row.technician_id,
                    Appointment.status == "confirmed",
                    Appointment.id != row.id,
                    Appointment.start_time < new_end_time,
                    Appointment.end_time > new_start_time,
                )
                .count()
            )
            if conflicts:
                raise AppointmentDomainError("APPOINTMENT_CONFLICT", "新时段已被占用")
            row.start_time = new_start_time
            row.end_time = new_end_time
            row.version += 1
            row.updated_at = datetime.utcnow()
            session.add(
                AppointmentEvent(
                    appointment_id=row.id,
                    event_type="rescheduled",
                    from_status="confirmed",
                    to_status="confirmed",
                    request_id=request_id,
                    payload_json=f'{{"new_start": "{new_start_time.isoformat()}", "new_end": "{new_end_time.isoformat()}"}}',
                )
            )
            if actor_id is not None:
                session.add(AuditEvent(
                    id=uuid.uuid4().hex,
                    actor_id=actor_id,
                    store_id=row.store_id,
                    action="appointment.rescheduled",
                    resource_type="appointment",
                    resource_id=str(row.id),
                    request_id=request_id,
                    outcome="succeeded",
                    summary_json=json.dumps({
                        "new_start": new_start_time.isoformat(),
                        "new_end": new_end_time.isoformat(),
                    }, ensure_ascii=False),
                ))
            session.flush()
            session.refresh(row)
            return _appointment_to_dict(row)

        return self.repo.run_in_immediate_transaction(_reschedule_in_tx)

    def expire_drafts(self, before: Optional[datetime] = None) -> int:
        """执行一次可重复的草稿 TTL 清理。"""
        return self.repo.expire_drafts(before)

    # ---------------- 工具 ----------------

    def _get_owned(self, appointment_id: str, user_id: str) -> Dict[str, Any]:
        appt = self.repo.get(appointment_id, user_id)
        if appt is None:
            raise AppointmentDomainError("APPOINTMENT_NOT_FOUND", "预约不存在")
        return appt

    @staticmethod
    def _validate_required_fields(appt: Dict[str, Any]) -> None:
        missing = [f for f in CONFIRM_REQUIRED_FIELDS if appt.get(f) is None]
        if missing:
            raise AppointmentDomainError(
                "APPOINTMENT_REQUIRED_FIELD", f"缺少必需字段: {missing}"
            )
