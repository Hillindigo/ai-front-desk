"""C4：预约领域服务与状态机测试"""

from datetime import datetime

import pytest

from db.db_router import DatabaseRouter
from services.appointment_domain import (
    AppointmentCommandService,
    AppointmentDomainError,
    can_transition,
)
from services.technician_service import TechnicianService


@pytest.fixture()
def service():
    TechnicianService().initialize_default_technicians()
    svc = AppointmentCommandService()
    yield svc
    svc.close()


@pytest.fixture()
def router():
    r = DatabaseRouter()
    yield r
    r.close()


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 18, hour, minute)


def make_full_draft(service, user_id="u1", tech_id=None, hour=10):
    """创建一个字段完整的待确认预约。"""
    if tech_id is None:
        tech_id = service.repo.session_manager  # placeholder，实际在调用处传入
    draft = service.create_draft(
        user_id=user_id, conversation_id=None, service_type="肩颈放松",
        fields={
            "technician_id": tech_id,
            "start_time": dt(hour),
            "end_time": dt(hour + 1),
            "duration_minutes": 60,
        },
    )
    return service.request_confirmation(draft["id"], user_id)


class TestStateMachine:
    def test_valid_transitions(self):
        assert can_transition("draft", "pending_confirmation")
        assert can_transition("pending_confirmation", "confirmed")
        assert can_transition("confirmed", "cancelled")
        assert can_transition("confirmed", "confirmed")  # 改约
        assert not can_transition("cancelled", "confirmed")  # 终态不可恢复
        assert not can_transition("draft", "confirmed")  # 不可跳级
        assert not can_transition("expired", "draft")


class TestFullFlow:
    def test_create_update_request_confirm_flow(self, service, router):
        techs = router.technicians.get_all_technicians()
        tech_id = techs[0]["id"]

        draft = service.create_draft(
            user_id="u1", conversation_id=None, service_type="肩颈放松",
            fields={"technician_id": tech_id, "start_time": dt(10), "end_time": dt(11),
                    "duration_minutes": 60},
        )
        assert draft["status"] == "draft"

        pending = service.request_confirmation(draft["id"], "u1")
        assert pending["status"] == "pending_confirmation"

        confirmed = service.confirm(pending["id"], "u1", idempotency_key="key-1")
        assert confirmed["status"] == "confirmed"
        assert confirmed["idempotency_key"] == "key-1"
        assert confirmed["version"] >= 2

    def test_confirm_requires_pending(self, service, router):
        techs = router.technicians.get_all_technicians()
        draft = service.create_draft(
            user_id="u1", conversation_id=None, service_type="x",
            fields={"technician_id": techs[0]["id"], "start_time": dt(10), "end_time": dt(11),
                    "duration_minutes": 60},
        )
        with pytest.raises(AppointmentDomainError) as exc:
            service.confirm(draft["id"], "u1")
        assert exc.value.code == "APPOINTMENT_INVALID_STATE"

    def test_missing_required_field(self, service, router):
        draft = service.create_draft(user_id="u1", conversation_id=None, service_type="x")
        with pytest.raises(AppointmentDomainError) as exc:
            service.request_confirmation(draft["id"], "u1")
        assert exc.value.code == "APPOINTMENT_REQUIRED_FIELD"

    def test_invalid_time_range(self, service, router):
        techs = router.technicians.get_all_technicians()
        draft = service.create_draft(
            user_id="u1", conversation_id=None, service_type="x",
            fields={"technician_id": techs[0]["id"], "start_time": dt(11), "end_time": dt(10),
                    "duration_minutes": 60},
        )
        with pytest.raises(AppointmentDomainError) as exc:
            service.request_confirmation(draft["id"], "u1")
        assert exc.value.code == "APPOINTMENT_TIME_INVALID"

    def test_confirm_rechecks_schedule_inside_transaction(self, service, router):
        tech_id = router.technicians.get_all_technicians()[0]["id"]
        draft = service.create_draft(
            user_id="u1", conversation_id=None, service_type="x",
            fields={"technician_id": tech_id, "start_time": dt(10), "end_time": dt(11),
                    "duration_minutes": 60},
        )
        pending = service.request_confirmation(draft["id"], "u1")

        # 排班在 request_confirmation 后发生变化；confirm 仍必须在其事务内重新检查。
        router.technicians.add_schedule(tech_id, dt(10), dt(11), status="busy")
        with pytest.raises(AppointmentDomainError) as exc:
            service.confirm(pending["id"], "u1", idempotency_key="schedule-race")
        assert exc.value.code == "TECHNICIAN_UNAVAILABLE"
        assert service.repo.get(pending["id"])["status"] == "pending_confirmation"


class TestIdempotency:
    def test_duplicate_confirm_returns_original(self, service, router):
        techs = router.technicians.get_all_technicians()
        draft = service.create_draft(
            user_id="u1", conversation_id=None, service_type="x",
            fields={"technician_id": techs[0]["id"], "start_time": dt(10), "end_time": dt(11),
                    "duration_minutes": 60},
        )
        service.request_confirmation(draft["id"], "u1")
        first = service.confirm(draft["id"], "u1", idempotency_key="k")
        second = service.confirm(draft["id"], "u1", idempotency_key="k")
        assert first["id"] == second["id"]
        # 没有产生第二条记录
        assert len(service.repo.list_by_user("u1")) == 1

    def test_idempotency_conflict(self, service, router):
        techs = router.technicians.get_all_technicians()
        # 预约 A 用 key
        a = service.create_draft(
            user_id="u1", conversation_id=None, service_type="x",
            fields={"technician_id": techs[0]["id"], "start_time": dt(10), "end_time": dt(11),
                    "duration_minutes": 60},
        )
        service.request_confirmation(a["id"], "u1")
        service.confirm(a["id"], "u1", idempotency_key="same-key")
        # 预约 B 用同 key（不同时间）
        b = service.create_draft(
            user_id="u1", conversation_id=None, service_type="y",
            fields={"technician_id": techs[1]["id"], "start_time": dt(15), "end_time": dt(16),
                    "duration_minutes": 60},
        )
        service.request_confirmation(b["id"], "u1")
        with pytest.raises(AppointmentDomainError) as exc:
            service.confirm(b["id"], "u1", idempotency_key="same-key")
        assert exc.value.code == "IDEMPOTENCY_CONFLICT"


class TestConcurrency:
    def test_same_slot_only_one_confirms(self, service, router):
        """同一服务人员同一时段：两个 pending 并发确认，至多一个成功。"""
        techs = router.technicians.get_all_technicians()
        tech_id = techs[0]["id"]

        a = service.create_draft(
            user_id="u1", conversation_id=None, service_type="x",
            fields={"technician_id": tech_id, "start_time": dt(10), "end_time": dt(11),
                    "duration_minutes": 60},
        )
        b = service.create_draft(
            user_id="u2", conversation_id=None, service_type="y",
            fields={"technician_id": tech_id, "start_time": dt(10), "end_time": dt(11),
                    "duration_minutes": 60},
        )
        service.request_confirmation(a["id"], "u1")
        service.request_confirmation(b["id"], "u2")

        # 串行确认（BEGIN IMMEDIATE 保证原子）：第二个必须冲突
        service.confirm(a["id"], "u1", idempotency_key="k-a")
        with pytest.raises(AppointmentDomainError) as exc:
            service.confirm(b["id"], "u2", idempotency_key="k-b")
        assert exc.value.code == "APPOINTMENT_CONFLICT"
        # B 仍处于 pending（未被破坏）
        assert service.repo.get(b["id"])["status"] == "pending_confirmation"


class TestCancelReschedule:
    def test_cancel_releases_slot(self, service, router):
        techs = router.technicians.get_all_technicians()
        tech_id = techs[0]["id"]
        appt = make_full_draft(service, tech_id=tech_id, hour=10)
        confirmed = service.confirm(appt["id"], "u1")
        cancelled = service.cancel(confirmed["id"], "u1", reason="用户取消")
        assert cancelled["status"] == "cancelled"
        assert cancelled["cancel_reason"] == "用户取消"
        # 时段释放：他人可预约
        c2 = service.create_draft(
            user_id="u2", conversation_id=None, service_type="z",
            fields={"technician_id": tech_id, "start_time": dt(10), "end_time": dt(11),
                    "duration_minutes": 60},
        )
        service.request_confirmation(c2["id"], "u2")
        service.confirm(c2["id"], "u2", idempotency_key="k2")
        assert service.repo.get(c2["id"])["status"] == "confirmed"

    def test_duplicate_cancel_idempotent(self, service, router):
        techs = router.technicians.get_all_technicians()
        appt = make_full_draft(service, tech_id=techs[0]["id"])
        confirmed = service.confirm(appt["id"], "u1")
        service.cancel(confirmed["id"], "u1")
        again = service.cancel(confirmed["id"], "u1")  # 重复取消不报错
        assert again["status"] == "cancelled"

    def test_reschedule_success(self, service, router):
        techs = router.technicians.get_all_technicians()
        tech_id = techs[0]["id"]
        appt = make_full_draft(service, tech_id=tech_id, hour=10)
        confirmed = service.confirm(appt["id"], "u1")

        rescheduled = service.reschedule(confirmed["id"], "u1", dt(14), dt(15), request_id="r1")
        assert rescheduled["status"] == "confirmed"
        assert rescheduled["start_time"] == dt(14).isoformat()
        assert rescheduled["version"] > confirmed["version"]
        # 旧时段释放
        assert service._availability().check_technician_availability(tech_id, dt(10), dt(11))["available"]

    def test_reschedule_conflict_keeps_original(self, service, router):
        techs = router.technicians.get_all_technicians()
        tech_id = techs[0]["id"]
        a = make_full_draft(service, tech_id=tech_id, hour=10)
        service.confirm(a["id"], "u1")
        b = make_full_draft(service, user_id="u2", tech_id=tech_id, hour=14)
        service.confirm(b["id"], "u2")

        # A 改约到 14:00（与 B 冲突）-> 失败且原预约不变
        with pytest.raises(AppointmentDomainError) as exc:
            service.reschedule(a["id"], "u1", dt(14), dt(15))
        assert exc.value.code == "APPOINTMENT_CONFLICT"
        assert service.repo.get(a["id"])["start_time"] == dt(10).isoformat()
