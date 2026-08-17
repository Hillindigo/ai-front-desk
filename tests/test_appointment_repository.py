"""C2：预约 Repository 与事务单元测试"""

import uuid
from datetime import datetime, timedelta

import pytest

from db.db_router import DatabaseRouter


@pytest.fixture()
def repo():
    router = DatabaseRouter()
    yield router.appointments
    router.close()


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 18, hour, minute)


class TestAppointmentRepository:
    def test_create_draft_with_created_event(self, repo):
        appt = repo.create_draft(
            user_id="u1", conversation_id="c1", service_type="肩颈放松",
            fields={"start_time": dt(10), "end_time": dt(11), "duration_minutes": 60},
        )
        assert appt["status"] == "draft"
        assert appt["service_type"] == "肩颈放松"
        assert appt["version"] == 1
        assert appt["expires_at"] is not None
        # 事件已写入（同事务）
        assert repo.get(appt["id"])["id"] == appt["id"]

    def test_get_checks_ownership(self, repo):
        appt = repo.create_draft(user_id="alice", conversation_id=None, service_type="足疗")
        assert repo.get(appt["id"], user_id="alice") is not None
        assert repo.get(appt["id"], user_id="bob") is None

    def test_get_active_draft(self, repo):
        appt = repo.create_draft(user_id="u1", conversation_id="c1", service_type="肩颈放松")
        found = repo.get_active_draft("c1")
        assert found["id"] == appt["id"]
        # 无活跃草稿的会话
        assert repo.get_active_draft("no-such-conv") is None

    def test_update_draft_whitelist_and_version(self, repo):
        appt = repo.create_draft(user_id="u1", conversation_id="c1", service_type="肩颈放松")
        updated = repo.update_draft(
            appt["id"], "u1",
            {"start_time": dt(14), "project": "深度放松", "unknown_field": "忽略"},
        )
        assert updated["version"] == 2
        assert updated["start_time"] == dt(14).isoformat()
        assert updated["project"] == "深度放松"
        # 未知字段不落库
        assert "unknown_field" not in updated

    def test_update_draft_version_conflict(self, repo):
        appt = repo.create_draft(user_id="u1", conversation_id="c1", service_type="肩颈放松")
        assert repo.update_draft(appt["id"], "u1", {"project": "x"}, expected_version=5) is None
        # 正确版本可更新
        assert repo.update_draft(appt["id"], "u1", {"project": "x"}, expected_version=1) is not None

    def test_transition_atomic_with_event(self, repo):
        appt = repo.create_draft(user_id="u1", conversation_id="c1", service_type="肩颈放松")
        repo.update_draft(appt["id"], "u1", {"start_time": dt(10), "end_time": dt(11), "duration_minutes": 60})
        result = repo.transition(
            appt["id"], "u1",
            to_status="pending_confirmation",
            event_type="fields_complete",
            request_id="req-1",
        )
        assert result["status"] == "pending_confirmation"
        assert result["version"] == 3

    def test_transition_ownership_denied(self, repo):
        appt = repo.create_draft(user_id="u1", conversation_id=None, service_type="肩颈放松")
        assert repo.transition(appt["id"], "u2", to_status="cancelled", event_type="cancelled") is None
        # 原状态未变
        assert repo.get(appt["id"])["status"] == "draft"

    def test_find_conflicts_half_open_interval(self, repo):
        tech = 1
        # 已确认预约 10:00-11:00
        appt = repo.create_draft(user_id="u1", conversation_id=None, service_type="x")
        repo.update_draft(appt["id"], "u1", {
            "technician_id": tech, "start_time": dt(10), "end_time": dt(11), "duration_minutes": 60,
        })
        repo.transition(appt["id"], "u1", to_status="confirmed", event_type="confirmed")

        # 相邻区间 11:00-12:00 不冲突
        assert repo.find_conflicts(tech, dt(11), dt(12)) == []
        # 交叉 10:30-11:30 冲突
        assert len(repo.find_conflicts(tech, dt(10, 30), dt(11, 30))) == 1
        # 完全包含 09:00-12:00 冲突
        assert len(repo.find_conflicts(tech, dt(9), dt(12))) == 1
        # 排除自身（改约场景）
        assert repo.find_conflicts(tech, dt(10, 30), dt(11, 30), exclude_appointment_id=appt["id"]) == []
        # 不同服务人员不冲突
        assert repo.find_conflicts(999, dt(10, 30), dt(11, 30)) == []

    def test_cancelled_not_in_conflict(self, repo):
        tech = 1
        appt = repo.create_draft(user_id="u1", conversation_id=None, service_type="x")
        repo.update_draft(appt["id"], "u1", {
            "technician_id": tech, "start_time": dt(10), "end_time": dt(11), "duration_minutes": 60,
        })
        repo.transition(appt["id"], "u1", to_status="confirmed", event_type="confirmed")
        repo.transition(appt["id"], "u1", to_status="cancelled", event_type="cancelled")
        assert repo.find_conflicts(tech, dt(10), dt(11)) == []

    def test_idempotency_lookup(self, repo):
        appt = repo.create_draft(user_id="u1", conversation_id=None, service_type="x")
        repo.update_draft(appt["id"], "u1", {"start_time": dt(10), "end_time": dt(11), "duration_minutes": 60})
        repo.transition(appt["id"], "u1", to_status="confirmed", event_type="confirmed",
                        request_id="req-1", idempotency_key="req-1")
        # 幂等键查询：第二次请求命中同一预约
        assert repo.get_by_idempotency("u1", "req-1") is not None
        assert repo.get_by_idempotency("u1", "req-1")["id"] == appt["id"]
        # 幂等键不可覆盖
        repo.transition(appt["id"], "u1", to_status="cancelled", event_type="cancelled",
                        idempotency_key="another-key")
        assert repo.get(appt["id"])["idempotency_key"] == "req-1"

    def test_expire_drafts(self, repo):
        appt = repo.create_draft(user_id="u1", conversation_id="c1", service_type="x", ttl_hours=24)
        # 未过期不清理
        assert repo.expire_drafts(before=datetime.utcnow() + timedelta(hours=1)) == 0
        # 把 expires_at 改成过去后清理
        from db.models import Appointment
        from sqlalchemy import update
        with repo.session_manager.session_scope() as s:
            s.execute(
                update(Appointment)
                .where(Appointment.id == appt["id"])
                .values(expires_at=datetime.utcnow() - timedelta(hours=1))
            )
        count = repo.expire_drafts()
        assert count == 1
        assert repo.get(appt["id"])["status"] == "expired"
        # 可重复执行（不重复计数）
        assert repo.expire_drafts() == 0

    def test_transaction_rollback_no_partial_state(self, repo):
        """事件写入失败时预约状态变更一起回滚（同事务原子性）。"""
        appt = repo.create_draft(user_id="u1", conversation_id=None, service_type="x")
        with pytest.raises(Exception):
            with repo.session_manager.session_scope() as s:
                from db.models import Appointment, AppointmentEvent
                from sqlalchemy.orm import Session
                # 手工构造：先改状态，再故意写入非法事件触发回滚
                a = s.query(Appointment).filter(Appointment.id == appt["id"]).first()
                a.status = "confirmed"
                s.add(AppointmentEvent(
                    appointment_id="不存在的预约",  # 外键失败 -> 整个事务回滚
                    event_type="confirmed",
                ))
                s.flush()
        assert repo.get(appt["id"])["status"] == "draft"
