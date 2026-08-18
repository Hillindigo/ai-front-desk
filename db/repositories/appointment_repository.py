"""预约与事件 Repository（Phase C C2）

提供不依赖 Agent 的预约数据访问与原子事务边界：
- 查询：按 ID（归属校验）、按会话活跃草稿、按用户、按服务人员+时间冲突。
- 写入：草稿创建/更新（白名单字段 + 版本递增）、状态迁移 + 事件同事务写入。
- 幂等：按 (user_id, idempotency_key) 查询（唯一约束已在模型层）。

约定：状态迁移的**合法性校验**由领域服务（C4）负责；Repository 只保证
"状态变更 + 事件"在同一事务内原子落库，异常回滚。
"""

import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, or_

from ..base.session_manager import SessionManager
from ..models import Appointment, AppointmentEvent
from db.store_scope import resolve_store_id

# 草稿白名单字段（C5 Agent 草稿持久化只允许写这些）
DRAFT_FIELD_WHITELIST = {
    "service_type", "project", "technician_id",
    "start_time", "end_time", "duration_minutes",
}

ACTIVE_DRAFT_STATUSES = ("draft", "pending_confirmation")


class ActiveDraftOwnershipError(Exception):
    """同一会话的活跃草稿属于其他用户。"""


def _appointment_to_dict(appt: Appointment) -> Dict[str, Any]:
    return {
        "id": appt.id,
        "user_id": appt.user_id,
        "store_id": appt.store_id,
        "conversation_id": appt.conversation_id,
        "service_type": appt.service_type,
        "project": appt.project,
        "technician_id": appt.technician_id,
        "start_time": appt.start_time.isoformat() if appt.start_time else None,
        "end_time": appt.end_time.isoformat() if appt.end_time else None,
        "duration_minutes": appt.duration_minutes,
        "status": appt.status,
        "idempotency_key": appt.idempotency_key,
        "version": appt.version,
        "expires_at": appt.expires_at.isoformat() if appt.expires_at else None,
        "created_at": appt.created_at.isoformat() if appt.created_at else None,
        "updated_at": appt.updated_at.isoformat() if appt.updated_at else None,
        "cancelled_at": appt.cancelled_at.isoformat() if appt.cancelled_at else None,
        "cancel_reason": appt.cancel_reason,
    }


def _event_payload(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    return json.dumps(payload, ensure_ascii=False) if payload is not None else None


class AppointmentRepository:
    """预约数据访问仓库。"""

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    # ---------------- 查询 ----------------

    def get(
        self,
        appointment_id: str,
        user_id: Optional[str] = None,
        store_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """按 ID 获取；user_id 非空时校验归属。"""
        with self.session_manager.session_scope() as session:
            query = session.query(Appointment).filter(Appointment.id == appointment_id)
            if user_id is not None:
                query = query.filter(Appointment.user_id == user_id)
            if store_id is not None:
                query = query.filter(Appointment.store_id == store_id)
            appt = query.first()
            if appt is None:
                return None
            session.refresh(appt)
            return _appointment_to_dict(appt)

    def get_active_draft(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """获取会话的活跃草稿（draft/pending_confirmation）。"""
        with self.session_manager.session_scope() as session:
            appt = (
                session.query(Appointment)
                .filter(
                    Appointment.conversation_id == conversation_id,
                    Appointment.status.in_(ACTIVE_DRAFT_STATUSES),
                )
                .first()
            )
            if appt is None:
                return None
            session.refresh(appt)
            return _appointment_to_dict(appt)

    def get_by_idempotency(self, user_id: str, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """幂等查询：相同 (user_id, idempotency_key) 的已存在预约。"""
        with self.session_manager.session_scope() as session:
            appt = (
                session.query(Appointment)
                .filter(
                    Appointment.user_id == user_id,
                    Appointment.idempotency_key == idempotency_key,
                )
                .first()
            )
            if appt is None:
                return None
            session.refresh(appt)
            return _appointment_to_dict(appt)

    def list_by_user(self, user_id: str, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            query = session.query(Appointment).filter(Appointment.user_id == user_id)
            if status:
                query = query.filter(Appointment.status == status)
            appts = query.order_by(Appointment.created_at.desc()).limit(limit).all()
            return [_appointment_to_dict(a) for a in appts]

    def find_conflicts(
        self,
        technician_id: int,
        start_time: datetime,
        end_time: datetime,
        exclude_appointment_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """事务内冲突查询：半开区间 [start, end) 与已确认预约的重叠。

        规则（计划 4.2）：A.start < B.end AND A.end > B.start；
        相邻区间不冲突；cancelled/expired 不参与；改约排除自身。
        """
        with self.session_manager.session_scope() as session:
            query = session.query(Appointment).filter(
                Appointment.technician_id == technician_id,
                Appointment.status == "confirmed",
                Appointment.start_time < end_time,
                Appointment.end_time > start_time,
            )
            if exclude_appointment_id is not None:
                query = query.filter(Appointment.id != exclude_appointment_id)
            appts = query.all()
            return [_appointment_to_dict(a) for a in appts]

    # ---------------- 事务控制（Phase C D6） ----------------

    def run_in_immediate_transaction(self, fn):
        """在 BEGIN IMMEDIATE 事务内执行 fn(session)（SQLite 写锁抢占）。

        用于确认/改约等"冲突检查 + 写入必须同事务"的场景，防止并发 lost update：
        事务一开始即持有写锁，后续事务的 BEGIN 阻塞到提交后重新读取最新快照。
        异常自动回滚。fn 的返回值作为本方法返回值。
        """
        with self.session_manager.session_scope() as session:
            dbapi_conn = session.connection().connection.driver_connection
            original = dbapi_conn.isolation_level
            # sqlite3 连接级 isolation_level="IMMEDIATE"：隐式 BEGIN 变为 BEGIN IMMEDIATE
            dbapi_conn.isolation_level = "IMMEDIATE"
            try:
                return fn(session)
            finally:
                dbapi_conn.isolation_level = original

    # ---------------- 写入（原子：预约 + 事件同事务） ----------------

    def create_draft(
        self,
        user_id: str,
        conversation_id: Optional[str],
        service_type: str,
        fields: Optional[Dict[str, Any]] = None,
        ttl_hours: int = 24,
        store_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """创建草稿预约 + created 事件（同事务）。"""
        now = datetime.utcnow()
        with self.session_manager.session_scope() as session:
            appt = Appointment(
                id=str(uuid.uuid4()),
                user_id=user_id,
                store_id=store_id,
                conversation_id=conversation_id,
                service_type=service_type,
                status="draft",
                expires_at=now + timedelta(hours=ttl_hours),
            )
            appt.store_id = resolve_store_id(session, store_id)
            self._apply_fields(appt, fields or {})
            session.add(appt)
            session.flush()
            event = AppointmentEvent(
                appointment_id=appt.id,
                event_type="created",
                from_status=None,
                to_status="draft",
                payload_json=_event_payload({"service_type": service_type}),
            )
            session.add(event)
            session.flush()
            session.refresh(appt)
            return _appointment_to_dict(appt)

    def upsert_active_draft(
        self,
        user_id: str,
        conversation_id: str,
        service_type: str,
        fields: Optional[Dict[str, Any]] = None,
        ttl_hours: int = 24,
        store_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """原子创建或更新会话唯一活跃草稿。

        同一会话重复提交预约意图时复用现有草稿，不让数据库唯一索引异常
        泄漏为 500；若草稿属于其他用户，则拒绝跨用户修改。
        """
        now = datetime.utcnow()

        def _upsert_in_tx(session):
            appt = (
                session.query(Appointment)
                .filter(
                    Appointment.conversation_id == conversation_id,
                    Appointment.status.in_(ACTIVE_DRAFT_STATUSES),
                )
                .first()
            )
            if appt is not None:
                if appt.user_id != user_id:
                    raise ActiveDraftOwnershipError(conversation_id)
                from_status = appt.status
                # 用户在待确认阶段提交新字段时，重新打开草稿，要求再次校验。
                if appt.status == "pending_confirmation":
                    appt.status = "draft"
                appt.service_type = service_type
                appt.store_id = resolve_store_id(session, store_id)
                self._apply_fields(appt, fields or {})
                appt.expires_at = now + timedelta(hours=ttl_hours)
                appt.version += 1
                appt.updated_at = now
                session.add(
                    AppointmentEvent(
                        appointment_id=appt.id,
                        event_type="updated",
                        from_status=from_status,
                        to_status=appt.status,
                        payload_json=_event_payload({"service_type": service_type}),
                    )
                )
                session.flush()
                session.refresh(appt)
                return _appointment_to_dict(appt)

            appt = Appointment(
                id=str(uuid.uuid4()),
                user_id=user_id,
                store_id=store_id,
                conversation_id=conversation_id,
                service_type=service_type,
                status="draft",
                expires_at=now + timedelta(hours=ttl_hours),
            )
            appt.store_id = resolve_store_id(session, store_id)
            self._apply_fields(appt, fields or {})
            session.add(appt)
            session.flush()
            session.add(
                AppointmentEvent(
                    appointment_id=appt.id,
                    event_type="created",
                    from_status=None,
                    to_status="draft",
                    payload_json=_event_payload({"service_type": service_type}),
                )
            )
            session.flush()
            session.refresh(appt)
            return _appointment_to_dict(appt)

        return self.run_in_immediate_transaction(_upsert_in_tx)

    def update_draft(
        self,
        appointment_id: str,
        user_id: str,
        fields: Dict[str, Any],
        expected_version: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """更新草稿（仅白名单字段，版本递增；expected_version 不符返回 None）。"""
        now = datetime.utcnow()
        with self.session_manager.session_scope() as session:
            appt = (
                session.query(Appointment)
                .filter(Appointment.id == appointment_id, Appointment.user_id == user_id)
                .first()
            )
            if appt is None or appt.status not in ACTIVE_DRAFT_STATUSES:
                return None
            if expected_version is not None and appt.version != expected_version:
                return None
            self._apply_fields(appt, fields)
            appt.version += 1
            appt.updated_at = now
            session.flush()
            session.refresh(appt)
            return _appointment_to_dict(appt)

    def transition(
        self,
        appointment_id: str,
        user_id: str,
        to_status: str,
        event_type: str,
        request_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """原子状态迁移 + 事件写入（同事务）。

        调用方（领域服务）负责校验迁移合法性；本方法只执行：
        from_status 由 DB 当前值确定，与事件一并写入。
        idempotency_key 在首次设置后不可覆盖（幂等键属于创建/确认语义）。
        返回迁移后的 dict；预约不存在或归属不符返回 None。
        """
        now = datetime.utcnow()
        with self.session_manager.session_scope() as session:
            appt = (
                session.query(Appointment)
                .filter(Appointment.id == appointment_id, Appointment.user_id == user_id)
                .first()
            )
            if appt is None:
                return None
            from_status = appt.status
            appt.status = to_status
            appt.version += 1
            appt.updated_at = now
            if to_status == "cancelled":
                appt.cancelled_at = now
            if idempotency_key and appt.idempotency_key is None:
                appt.idempotency_key = idempotency_key
            if extra_fields:
                self._apply_fields(appt, extra_fields)
                if "cancel_reason" in extra_fields:
                    appt.cancel_reason = extra_fields["cancel_reason"]
            session.flush()
            event = AppointmentEvent(
                appointment_id=appt.id,
                event_type=event_type,
                from_status=from_status,
                to_status=to_status,
                request_id=request_id,
                payload_json=_event_payload(payload),
            )
            session.add(event)
            session.flush()
            session.refresh(appt)
            return _appointment_to_dict(appt)

    def expire_drafts(self, before: Optional[datetime] = None) -> int:
        """TTL 清理：把已过期的 draft/pending_confirmation 标记为 expired（可重复执行）。"""
        before = before or datetime.utcnow()
        now = datetime.utcnow()
        count = 0
        with self.session_manager.session_scope() as session:
            appts = (
                session.query(Appointment)
                .filter(
                    Appointment.status.in_(ACTIVE_DRAFT_STATUSES),
                    Appointment.expires_at.isnot(None),
                    Appointment.expires_at < before,
                )
                .all()
            )
            for appt in appts:
                from_status = appt.status
                appt.status = "expired"
                appt.updated_at = now
                session.add(
                    AppointmentEvent(
                        appointment_id=appt.id,
                        event_type="expired",
                        from_status=from_status,
                        to_status="expired",
                        payload_json=_event_payload({"reason": "ttl"}),
                    )
                )
                count += 1
            return count

    # ---------------- 工具 ----------------

    @staticmethod
    def _apply_fields(appt: Appointment, fields: Dict[str, Any]) -> None:
        """只应用白名单字段，忽略未知字段。"""
        for key, value in fields.items():
            if key in DRAFT_FIELD_WHITELIST:
                setattr(appt, key, value)
