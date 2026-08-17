"""C3：排班语义分离 + 领域可用性服务测试"""

from datetime import datetime

import pytest

from db.db_router import DatabaseRouter
from services.appointment_service import AppointmentService
from services.technician_service import TechnicianService


@pytest.fixture()
def service():
    # 初始化默认服务人员（10 人），供可用性查询使用
    TechnicianService().initialize_default_technicians()
    svc = AppointmentService()
    yield svc
    svc.db_router.close()


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 18, hour, minute)


class TestTechnicianAvailability:
    def test_technician_not_found(self, service):
        result = service.check_technician_availability(9999, dt(10), dt(11))
        assert result == {"available": False, "reason": "TECHNICIAN_NOT_FOUND"}

    def test_available_when_no_conflicts(self, service):
        # 找到一个真实服务人员
        techs = service.technician_repo.get_all_technicians()
        assert len(techs) >= 1
        result = service.check_technician_availability(techs[0]["id"], dt(10), dt(11))
        assert result["available"] is True

    def test_schedule_busy_conflict(self, service):
        """排班约束（status='busy'）冲突 -> TECHNICIAN_UNAVAILABLE。"""
        techs = service.technician_repo.get_all_technicians()
        tech_id = techs[0]["id"]
        service.technician_repo.add_schedule(tech_id, dt(10), dt(12), status="busy")
        result = service.check_technician_availability(tech_id, dt(10, 30), dt(11, 30))
        assert result == {"available": False, "reason": "TECHNICIAN_UNAVAILABLE"}
        # 排班块之外仍可用
        assert service.check_technician_availability(tech_id, dt(13), dt(14))["available"] is True

    def test_appointment_conflict(self, service):
        """已确认预约冲突 -> APPOINTMENT_CONFLICT。"""
        techs = service.technician_repo.get_all_technicians()
        tech_id = techs[0]["id"]
        appt = service.appointment_repo.create_draft(
            user_id="u1", conversation_id=None, service_type="肩颈放松",
            fields={"technician_id": tech_id, "start_time": dt(10), "end_time": dt(11),
                    "duration_minutes": 60},
        )
        service.appointment_repo.transition(
            appt["id"], "u1", to_status="confirmed", event_type="confirmed"
        )
        result = service.check_technician_availability(tech_id, dt(10), dt(11))
        assert result == {"available": False, "reason": "APPOINTMENT_CONFLICT"}
        # 相邻时段仍可用
        assert service.check_technician_availability(tech_id, dt(11), dt(12))["available"] is True


class TestAvailableTechnicians:
    def test_get_available_technicians(self, service):
        techs = service.get_available_technicians(dt(10), dt(11))
        assert len(techs) >= 1
        # 返回的都是服务人员结构
        assert "id" in techs[0] and "name" in techs[0]

    def test_get_available_technicians_gender_filter(self, service):
        # 先看默认数据里有没有女服务人员
        all_techs = service.technician_repo.get_all_technicians()
        female_ids = [t["id"] for t in all_techs if t.get("gender") == "女"]
        if not female_ids:
            pytest.skip("默认数据无女服务人员")
        available = service.get_available_technicians(dt(10), dt(11), gender="女")
        assert all(t.get("gender") == "女" for t in available)

    def test_conflicted_technician_excluded(self, service):
        """被预约占用的服务人员从可用列表排除。"""
        techs = service.technician_repo.get_all_technicians()
        tech_id = techs[0]["id"]
        appt = service.appointment_repo.create_draft(
            user_id="u1", conversation_id=None, service_type="x",
            fields={"technician_id": tech_id, "start_time": dt(10), "end_time": dt(11),
                    "duration_minutes": 60},
        )
        service.appointment_repo.transition(
            appt["id"], "u1", to_status="confirmed", event_type="confirmed"
        )
        available_ids = [t["id"] for t in service.get_available_technicians(dt(10), dt(11))]
        assert tech_id not in available_ids
