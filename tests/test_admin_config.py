"""Phase G G3：门店配置、服务目录和审计 HTTP 契约。"""

import pytest
from fastapi.testclient import TestClient

from app import create_app
from services.admin_auth import AdminAuthService
from services.store_config import StoreConfigService


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


def login(client, service, username="owner-g3@example.test", role="owner"):
    service.provision_account(
        username=username,
        password="Correct-Horse-7!",
        display_name="G3账号",
        store_name="G3门店",
        role=role,
    )
    response = client.post(
        "/api/v1/admin/auth/login",
        json={"username": username, "password": "Correct-Horse-7!"},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


class TestStoreConfig:
    def test_门店资料和服务目录写入并审计(self, client, auth_service):
        csrf = login(client, auth_service)

        profile = client.put(
            "/api/v1/admin/config/store",
            json={
                "name": "G3旗舰店",
                "address": "人民路 1 号",
                "phone": "010-12345678",
                "timezone": "Asia/Shanghai",
                "is_open": True,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert profile.status_code == 200, profile.text
        assert profile.json()["name"] == "G3旗舰店"

        created = client.post(
            "/api/v1/admin/config/services",
            json={
                "name": "肩颈放松",
                "price_cents": 8800,
                "duration_minutes": 30,
                "description": "基础肩颈放松",
                "is_bookable": True,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert created.status_code == 201, created.text
        assert created.json()["price_cents"] == 8800
        assert client.get("/api/v1/admin/config/services").json()["items"]

        audit = StoreConfigService().list_audit(
            store_id=profile.json()["store_id"], limit=20
        )
        assert any(row["action"] == "store.profile.updated" for row in audit)
        assert any(row["action"] == "service.created" for row in audit)

    def test_非法价格和时长被服务层拒绝(self, client, auth_service):
        csrf = login(client, auth_service)
        response = client.post(
            "/api/v1/admin/config/services",
            json={"name": "错误项目", "price_cents": -1, "duration_minutes": 0},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_CONFIG"

    def test_viewer不能修改门店配置(self, client, auth_service):
        csrf = login(
            client,
            auth_service,
            username="viewer-g3@example.test",
            role="viewer",
        )
        response = client.put(
            "/api/v1/admin/config/store",
            json={"name": "越权门店"},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "PERMISSION_DENIED"
