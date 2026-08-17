"""C1：Appointment/AppointmentEvent 模型与约束测试"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from db.base.session_manager import SessionManager
from db.models import Appointment, AppointmentEvent


@pytest.fixture()
def session_manager():
    sm = SessionManager()
    yield sm
    sm.close()


def make_appointment(**overrides):
    data = {
        "id": str(uuid.uuid4()),
        "user_id": "u1",
        "conversation_id": None,
        "service_type": "肩颈放松",
        "status": "draft",
    }
    data.update(overrides)
    return Appointment(**data)


class TestAppointmentModel:
    def test_defaults(self, session_manager):
        with session_manager.session_scope() as s:
            appt = make_appointment()
            s.add(appt)
            s.flush()
            assert appt.status == "draft"
            assert appt.version == 1
            assert appt.created_at is not None
            assert appt.updated_at is not None

    def test_confirmed_requires_core_fields(self, session_manager):
        """已确认预约必须具备核心字段（领域服务层校验，模型层仅存储）。"""
        with session_manager.session_scope() as s:
            appt = make_appointment(
                status="confirmed",
                technician_id=1,
                start_time=datetime(2026, 8, 18, 10, 0),
                end_time=datetime(2026, 8, 18, 11, 0),
                duration_minutes=60,
            )
            s.add(appt)
            s.flush()
            assert appt.status == "confirmed"

    def test_idempotency_key_unique(self, session_manager):
        """相同 (user_id, idempotency_key) 只能有一条记录。"""
        with session_manager.session_scope() as s:
            s.add(make_appointment(idempotency_key="k1"))
            s.flush()
        with pytest.raises(IntegrityError):
            with session_manager.session_scope() as s:
                s.add(make_appointment(idempotency_key="k1"))
                s.flush()

    def test_idempotency_key_nullable(self, session_manager):
        """无幂等键的草稿可重复创建（NULL 不参与唯一性）。"""
        with session_manager.session_scope() as s:
            s.add(make_appointment(idempotency_key=None))
            s.flush()
        with session_manager.session_scope() as s:
            s.add(make_appointment(idempotency_key=None))
            s.flush()

    def test_one_active_draft_per_conversation(self, session_manager):
        """同一会话最多一个活跃草稿（部分唯一索引）。"""
        conv_id = str(uuid.uuid4())
        with session_manager.session_scope() as s:
            s.add(make_appointment(conversation_id=conv_id, status="draft"))
            s.flush()
        # 第二个活跃草稿 -> 冲突
        with pytest.raises(IntegrityError):
            with session_manager.session_scope() as s:
                s.add(make_appointment(conversation_id=conv_id, status="pending_confirmation"))
                s.flush()

    def test_active_draft_per_conversation_isolation(self, session_manager):
        """不同会话可以各自有活跃草稿。"""
        with session_manager.session_scope() as s:
            s.add(make_appointment(conversation_id="c1", status="draft"))
            s.add(make_appointment(conversation_id="c2", status="draft"))
            s.flush()

    def test_confirmed_does_not_block_new_draft(self, session_manager):
        """已确认预约不占用"活跃草稿"名额（部分索引只限 draft/pending）。"""
        conv_id = str(uuid.uuid4())
        with session_manager.session_scope() as s:
            s.add(make_appointment(conversation_id=conv_id, status="confirmed"))
            s.flush()
        with session_manager.session_scope() as s:
            s.add(make_appointment(conversation_id=conv_id, status="draft"))
            s.flush()

    def test_cancelled_does_not_block_new_draft(self, session_manager):
        conv_id = str(uuid.uuid4())
        with session_manager.session_scope() as s:
            s.add(make_appointment(conversation_id=conv_id, status="cancelled"))
            s.flush()
        with session_manager.session_scope() as s:
            s.add(make_appointment(conversation_id=conv_id, status="draft"))
            s.flush()


class TestAppointmentEventModel:
    def test_event_roundtrip(self, session_manager):
        with session_manager.session_scope() as s:
            appt = make_appointment()
            s.add(appt)
            s.flush()
            event = AppointmentEvent(
                appointment_id=appt.id,
                event_type="created",
                from_status=None,
                to_status="draft",
                request_id="req-1",
                payload_json='{"project": "肩颈放松"}',
            )
            s.add(event)
            s.flush()
            assert event.id is not None

    def test_event_requires_appointment(self, session_manager):
        """事件必须绑定预约（外键约束）。"""
        with pytest.raises(IntegrityError):
            with session_manager.session_scope() as s:
                s.add(AppointmentEvent(appointment_id="no-such-id", event_type="created"))
                s.flush()
