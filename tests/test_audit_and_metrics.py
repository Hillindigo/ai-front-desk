"""Phase G G7：审计查询与运营指标契约。"""

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


@pytest.fixture
def client(auth_service):
    with TestClient(create_app()) as c:
        yield c


def login(client, service, username, role):
    service.provision_account(username, "Correct-Horse-7!", "G7", "G7门店", role)
    response = client.post(
        "/api/v1/admin/auth/login",
        json={"username": username, "password": "Correct-Horse-7!"},
    )
    return response.json().get("csrf_token")


class TestAuditMetrics:
    def test_审计查询和指标按门店返回(self, client, auth_service):
        csrf = login(client, auth_service, "owner-g7@example.test", "owner")
        changed = client.put(
            "/api/v1/admin/config/store",
            json={"name": "G7更新店"},
            headers={"X-CSRF-Token": csrf, "X-Request-ID": "g7-config-1"},
        )
        assert changed.status_code == 200
        audit = client.get("/api/v1/admin/audit", params={"action": "store.profile.updated"})
        assert audit.status_code == 200
        assert audit.json()["items"]
        assert audit.json()["items"][0]["request_id"] == "g7-config-1"

        metrics = client.get("/api/v1/admin/metrics")
        assert metrics.status_code == 200
        body = metrics.json()
        assert body["store_id"] == changed.json()["store_id"]
        assert body["audit_action_counts"]["store.profile.updated"] >= 1
        assert "definitions" in body

    def test_viewer不能读取审计(self, client, auth_service):
        login(client, auth_service, "viewer-g7@example.test", "viewer")
        response = client.get("/api/v1/admin/audit")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "PERMISSION_DENIED"
