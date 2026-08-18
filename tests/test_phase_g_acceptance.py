"""Phase G G9：商家后台最小 HTTP 闭环验收。"""

import pytest
from fastapi.testclient import TestClient

from app import create_app
from services.admin_auth import AdminAuthService


@pytest.fixture
def auth_service():
    service = AdminAuthService()
    service.clear_for_tests()
    yield service
    service.clear_for_tests()
    service.close()


def test_登录到后台查询审计和指标(client=None, auth_service=None):
    auth_service = auth_service or AdminAuthService()
    auth_service.clear_for_tests()
    try:
        auth_service.provision_account(
            "owner-g9@example.test", "Correct-Horse-7!", "G9账号", "G9门店", "owner"
        )
        with TestClient(create_app()) as client:
            login = client.post(
                "/api/v1/admin/auth/login",
                json={"username": "owner-g9@example.test", "password": "Correct-Horse-7!"},
            )
            assert login.status_code == 200
            csrf = login.json()["csrf_token"]
            assert client.get("/admin").status_code == 200
            assert client.get("/api/v1/admin/conversations").status_code == 200
            assert client.get("/api/v1/admin/appointments").status_code == 200
            assert client.get("/api/v1/admin/customers").status_code == 200
            assert client.get("/api/v1/admin/audit").status_code == 200
            assert client.get("/api/v1/admin/metrics").status_code == 200
            updated = client.put(
                "/api/v1/admin/config/store",
                json={"name": "G9验收店"},
                headers={"X-CSRF-Token": csrf, "X-Request-ID": "g9-http-1"},
            )
            assert updated.status_code == 200
            assert client.get("/api/v1/admin/audit", params={"action": "store.profile.updated"}).json()["items"]
    finally:
        auth_service.clear_for_tests()
        auth_service.close()
