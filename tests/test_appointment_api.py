"""C6：预约 API 契约测试（/api/v1/appointments + 旧接口适配）"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import create_app
from services.technician_service import TechnicianService


@pytest.fixture(scope="module")
def client():
    TechnicianService().initialize_default_technicians()
    with TestClient(create_app()) as c:
        yield c


def dt_str(hour: int, minute: int = 0) -> str:
    return datetime(2026, 8, 18, hour, minute).isoformat()


def tech_id(client) -> int:
    techs = client.get("/api/technicians/").json()
    return techs[0]["id"]


class TestAppointmentAPI:
    def test_create_draft(self, client):
        resp = client.post("/api/v1/appointments", json={
            "user_id": "u1", "service_type": "肩颈放松", "mode": "draft",
            "project": "肩颈放松",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "draft"
        assert body["service_type"] == "肩颈放松"

    def test_confirm_flow_via_api(self, client):
        tid = tech_id(client)
        resp = client.post("/api/v1/appointments", json={
            "user_id": "u1", "service_type": "肩颈放松", "mode": "confirm",
            "technician_id": tid,
            "start_time": dt_str(10), "end_time": dt_str(11),
            "duration_minutes": 60,
            "idempotency_key": "api-confirm-1",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "confirmed"
        aid = body["id"]

        # 幂等重复提交返回同一预约
        resp2 = client.post("/api/v1/appointments", json={
            "user_id": "u1", "service_type": "肩颈放松", "mode": "confirm",
            "technician_id": tid,
            "start_time": dt_str(10), "end_time": dt_str(11),
            "duration_minutes": 60,
            "idempotency_key": "api-confirm-1",
        })
        assert resp2.json()["id"] == aid

        # GET 归属校验
        assert client.get(f"/api/v1/appointments/{aid}", params={"user_id": "u1"}).status_code == 200
        assert client.get(f"/api/v1/appointments/{aid}", params={"user_id": "u2"}).status_code == 404

    def test_cancel_via_api(self, client):
        tid = tech_id(client)
        created = client.post("/api/v1/appointments", json={
            "user_id": "u1", "service_type": "x", "mode": "confirm",
            "technician_id": tid, "start_time": dt_str(13), "end_time": dt_str(14),
            "duration_minutes": 60, "idempotency_key": "api-cancel-1",
        }).json()
        resp = client.post(f"/api/v1/appointments/{created['id']}/cancel",
                           json={"user_id": "u1", "reason": "行程有变"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"
        assert resp.json()["cancel_reason"] == "行程有变"

    def test_reschedule_conflict_via_api(self, client):
        tid = tech_id(client)
        a = client.post("/api/v1/appointments", json={
            "user_id": "u1", "service_type": "x", "mode": "confirm",
            "technician_id": tid, "start_time": dt_str(9), "end_time": dt_str(10),
            "duration_minutes": 60, "idempotency_key": "api-rs-a",
        }).json()
        client.post("/api/v1/appointments", json={
            "user_id": "u2", "service_type": "y", "mode": "confirm",
            "technician_id": tid, "start_time": dt_str(15), "end_time": dt_str(16),
            "duration_minutes": 60, "idempotency_key": "api-rs-b",
        })
        # A 改约到 15:00（与 B 冲突）-> 409
        resp = client.post(f"/api/v1/appointments/{a['id']}/reschedule",
                           json={"user_id": "u1", "new_start_time": dt_str(15), "new_end_time": dt_str(16)})
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "APPOINTMENT_CONFLICT"
        # 原预约不变
        assert client.get(f"/api/v1/appointments/{a['id']}", params={"user_id": "u1"}).json()["start_time"] == dt_str(9)

    def test_invalid_state_via_api(self, client):
        tid = tech_id(client)
        draft = client.post("/api/v1/appointments", json={
            "user_id": "u1", "service_type": "x", "mode": "draft",
        }).json()
        # draft 不能直接 cancel 之外的操作；confirm 非 pending -> 409
        resp = client.post(f"/api/v1/appointments/{draft['id']}/confirm", json={"user_id": "u1"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "APPOINTMENT_INVALID_STATE"

    def test_availability_endpoint(self, client):
        tid = tech_id(client)
        resp = client.get("/api/v1/appointments/availability", params={
            "technician_id": tid, "start_time": dt_str(10), "end_time": dt_str(11),
        })
        assert resp.status_code == 200
        assert resp.json()["available"] in (True, False)

    def test_legacy_appointment_create_uses_domain(self, client):
        """旧接口与新接口共享领域服务：创建的是领域 draft。"""
        resp = client.post("/api/appointment/create", json={
            "user_id": "u1", "service_type": "深度放松", "preferred_time": "明天下午",
        })
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "draft"
        assert resp.json()["data"]["service_type"] == "深度放松"
