"""Phase G G5：商家预约管理契约。"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import create_app
from db.db_router import DatabaseRouter
from services.admin_auth import AdminAuthService


@pytest.fixture
def auth_service():
    service = AdminAuthService()
    service.clear_for_tests()
    yield service
    service.clear_for_tests()
    service.close()


@pytest.fixture
def client(auth_service):
    with TestClient(create_app()) as c:
        yield c


def setup(client, auth_service):
    account = auth_service.provision_account(
        "owner-g5@example.test", "Correct-Horse-7!", "G5账号", "G5门店", "owner"
    )
    login = client.post(
        "/api/v1/admin/auth/login",
        json={"username": "owner-g5@example.test", "password": "Correct-Horse-7!"},
    )
    return account, login.json()["csrf_token"]


def make_confirmed(store_id, user_id="customer-g5"):
    router = DatabaseRouter()
    try:
        start = datetime.utcnow() + timedelta(days=1)
        draft = router.appointments.create_draft(
            user_id=user_id, conversation_id=None, service_type="肩颈放松",
            fields={"start_time": start, "end_time": start + timedelta(minutes=30),
                    "duration_minutes": 30}, store_id=store_id,
        )
        return router.appointments.transition(
            draft["id"], user_id, "confirmed", "confirmed", request_id="seed-g5"
        )
    finally:
        router.close()


class TestAdminAppointments:
    def test_列表详情和取消复用领域服务(self, client, auth_service):
        account, csrf = setup(client, auth_service)
        appointment = make_confirmed(account["store_id"])

        listing = client.get("/api/v1/admin/appointments")
        assert listing.status_code == 200
        assert any(x["appointment_id"] == appointment["id"] for x in listing.json()["items"])

        cancel = client.post(
            f"/api/v1/admin/appointments/{appointment['id']}/cancel",
            json={"reason": "客户要求取消"},
            headers={"X-CSRF-Token": csrf, "X-Request-ID": "g5-cancel-1"},
        )
        assert cancel.status_code == 200, cancel.text
        assert cancel.json()["status"] == "cancelled"

        detail = client.get(f"/api/v1/admin/appointments/{appointment['id']}")
        assert detail.json()["events"][-1]["event_type"] == "cancelled"

    def test_错误门店预约返回404(self, client, auth_service):
        account, _ = setup(client, auth_service)
        other = auth_service.create_store("G5另一门店")
        appointment = make_confirmed(other["store_id"], "other-g5")
        response = client.get(f"/api/v1/admin/appointments/{appointment['id']}")
        assert response.status_code == 404

    def test_预约写操作需要csrf(self, client, auth_service):
        account, _ = setup(client, auth_service)
        appointment = make_confirmed(account["store_id"])
        response = client.post(
            f"/api/v1/admin/appointments/{appointment['id']}/cancel",
            json={"reason": "缺少 token"},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "CSRF_INVALID"
