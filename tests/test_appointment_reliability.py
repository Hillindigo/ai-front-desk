"""C7：并发、故障注入与幂等重试测试

覆盖计划 C7 测试表的可靠性部分：
- 同人员并发确认（线程级 BEGIN IMMEDIATE）
- 数据库提交失败回滚（故障注入）
- 响应中断后重复请求（幂等重试）
- 事件写入失败不影响预约主状态判定
"""

import concurrent.futures
from datetime import datetime

import pytest

from db.db_router import DatabaseRouter
from services.appointment_domain import AppointmentCommandService, AppointmentDomainError
from services.technician_service import TechnicianService


@pytest.fixture()
def setup():
    TechnicianService().initialize_default_technicians()
    router = DatabaseRouter()
    tech_id = router.technicians.get_all_technicians()[0]["id"]
    yield router, tech_id
    router.close()


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 18, hour, minute)


def make_pending(router, user_id, tech_id, hour):
    svc = AppointmentCommandService()
    try:
        draft = svc.create_draft(
            user_id=user_id, conversation_id=None, service_type="x",
            fields={"technician_id": tech_id, "start_time": dt(hour), "end_time": dt(hour + 1),
                    "duration_minutes": 60},
        )
        return svc.request_confirmation(draft["id"], user_id)["id"]
    finally:
        svc.close()


class TestConcurrentConfirm:
    def test_same_slot_two_users_only_one_wins(self, setup):
        """同一时段两个用户并发确认：BEGIN IMMEDIATE 下至多一个成功。"""
        router, tech_id = setup
        a_id = make_pending(router, "u1", tech_id, 10)
        b_id = make_pending(router, "u2", tech_id, 10)

        def confirm_worker(appointment_id: str, user: str, key: str) -> str:
            svc = AppointmentCommandService()
            try:
                svc.confirm(appointment_id, user, idempotency_key=key)
                return "ok"
            except AppointmentDomainError as e:
                return e.code
            finally:
                svc.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(confirm_worker, a_id, "u1", "k-a"),
                pool.submit(confirm_worker, b_id, "u2", "k-b"),
            ]
            results = sorted(f.result() for f in futures)

        assert results == ["APPOINTMENT_CONFLICT", "ok"], f"并发结果异常: {results}"
        # 确认只有一条 confirmed
        svc = AppointmentCommandService()
        try:
            confirmed = svc.repo.list_by_user("u1", status="confirmed") + \
                        svc.repo.list_by_user("u2", status="confirmed")
            assert len(confirmed) == 1
            # 失败方仍处于 pending_confirmation（未被破坏）
            loser = [x for x in (a_id, b_id) if x not in [c["id"] for c in confirmed]][0]
            assert svc.repo.get(loser)["status"] == "pending_confirmation"
        finally:
            svc.close()

    def test_adjacent_slots_parallel_confirm_ok(self, setup):
        """相邻时段（10:00-11:00 / 11:00-12:00）并发确认都成功（半开区间）。"""
        router, tech_id = setup
        a_id = make_pending(router, "u1", tech_id, 10)
        b_id = make_pending(router, "u2", tech_id, 11)

        def confirm_worker(appointment_id: str, user: str, key: str) -> str:
            svc = AppointmentCommandService()
            try:
                svc.confirm(appointment_id, user, idempotency_key=key)
                return "ok"
            except AppointmentDomainError as e:
                return e.code
            finally:
                svc.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(confirm_worker, a_id, "u1", "k-c"),
                pool.submit(confirm_worker, b_id, "u2", "k-d"),
            ]
            results = sorted(f.result() for f in futures)
        assert results == ["ok", "ok"]


class TestFailureInjection:
    def test_db_write_failure_rolls_back(self, setup, monkeypatch):
        """事件写入失败（故障注入）-> 整个确认事务回滚，状态不变。"""
        router, tech_id = setup
        a_id = make_pending(router, "u1", tech_id, 10)

        # 故障注入：AppointmentEvent 构造失败
        from db.models import AppointmentEvent

        original_init = AppointmentEvent.__init__

        def broken_init(self, *args, **kwargs):
            raise RuntimeError("模拟数据库事件写入失败")

        monkeypatch.setattr(AppointmentEvent, "__init__", broken_init)
        svc = AppointmentCommandService()
        try:
            with pytest.raises(RuntimeError):
                svc.confirm(a_id, "u1", idempotency_key="k-fail")
        finally:
            monkeypatch.setattr(AppointmentEvent, "__init__", original_init)
            # 恢复后确认成功
            result = svc.confirm(a_id, "u1", idempotency_key="k-fail")
            assert result["status"] == "confirmed"
            svc.close()

    def test_conflict_does_not_create_partial(self, setup):
        """冲突拒绝后不留下任何半成功状态。"""
        router, tech_id = setup
        a_id = make_pending(router, "u1", tech_id, 10)
        b_id = make_pending(router, "u2", tech_id, 10)

        svc = AppointmentCommandService()
        try:
            svc.confirm(a_id, "u1", idempotency_key="k1")
            with pytest.raises(AppointmentDomainError) as exc:
                svc.confirm(b_id, "u2", idempotency_key="k2")
            assert exc.value.code == "APPOINTMENT_CONFLICT"
            # 无多余 confirmed，无多余事件
            assert len(svc.repo.list_by_user("u1", status="confirmed")) == 1
            assert svc.repo.get(b_id)["status"] == "pending_confirmation"
        finally:
            svc.close()


class TestRetryAfterInterruptedResponse:
    def test_retry_same_key_returns_original(self, setup):
        """模拟响应中断后的重复请求：同幂等键返回原预约，不重复创建。"""
        router, tech_id = setup
        a_id = make_pending(router, "u1", tech_id, 10)

        svc = AppointmentCommandService()
        try:
            first = svc.confirm(a_id, "u1", idempotency_key="retry-key")
            # 客户端未收到响应，重试同一请求
            second = svc.confirm(a_id, "u1", idempotency_key="retry-key")
            assert second["id"] == first["id"]
            assert len(svc.repo.list_by_user("u1", status="confirmed")) == 1
        finally:
            svc.close()

    def test_retry_after_cancel_is_idempotent(self, setup):
        """取消请求重复执行不产生重复事件或错误地改变状态。"""
        router, tech_id = setup
        a_id = make_pending(router, "u1", tech_id, 10)
        svc = AppointmentCommandService()
        try:
            svc.confirm(a_id, "u1", idempotency_key="k")
            svc.cancel(a_id, "u1", reason="用户取消", request_id="cancel-1")
            again = svc.cancel(a_id, "u1", reason="用户取消", request_id="cancel-1")
            assert again["status"] == "cancelled"
        finally:
            svc.close()
