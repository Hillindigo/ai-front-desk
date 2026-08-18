"""Phase G G1：商家身份、会话、门店上下文和 CSRF HTTP 契约。"""

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


def provision(service, username="owner@example.test", role="owner"):
    return service.provision_account(
        username=username,
        password="Correct-Horse-7!",
        display_name=username.split("@")[0],
        store_name="演示门店",
        role=role,
    )


class TestAdminAuthentication:
    def test_未登录访问当前身份返回401(self, client):
        response = client.get("/api/v1/admin/auth/me")
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "UNAUTHENTICATED"

    def test_登录建立服务端会话并返回csrf(self, client, auth_service):
        provision(auth_service)

        response = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "owner@example.test", "password": "Correct-Horse-7!"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["actor"]["username"] == "owner@example.test"
        assert response.json()["active_store"]["name"] == "演示门店"
        assert response.json()["csrf_token"]
        assert "admin_session" in response.cookies
        assert response.cookies["admin_session"]

        me = client.get("/api/v1/admin/auth/me")
        assert me.status_code == 200
        assert me.json()["actor"]["username"] == "owner@example.test"
        assert me.json()["active_store"]["name"] == "演示门店"

    def test_登录失败不泄漏账号是否存在(self, client, auth_service):
        provision(auth_service)

        unknown = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "missing@example.test", "password": "wrong"},
        )
        wrong = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "owner@example.test", "password": "wrong"},
        )

        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json() == wrong.json()

    def test_退出撤销会话(self, client, auth_service):
        provision(auth_service)
        login = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "owner@example.test", "password": "Correct-Horse-7!"},
        )
        csrf = login.json()["csrf_token"]

        missing_csrf = client.post("/api/v1/admin/auth/logout")
        assert missing_csrf.status_code == 403
        assert missing_csrf.json()["detail"]["code"] == "CSRF_INVALID"

        logout = client.post(
            "/api/v1/admin/auth/logout",
            headers={"X-CSRF-Token": csrf},
        )
        assert logout.status_code == 204
        assert client.get("/api/v1/admin/auth/me").status_code == 401
    def test_管理页面未登录不可访问(self, client):
        for path in (
            "/admin",
            "/admin/database",
            "/knowledge",
            "/technician",
            "/technician_schedule",
            "/user_behavior",
        ):
            response = client.get(path)
            assert response.status_code == 401, (path, response.status_code)


class TestStoreContext:
    def test_只能切换到有membership的门店(self, client, auth_service):
        account = provision(auth_service)
        other = auth_service.create_store("另一家门店")
        login = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "owner@example.test", "password": "Correct-Horse-7!"},
        )
        csrf = login.json()["csrf_token"]

        denied = client.post(
            "/api/v1/admin/auth/stores/switch",
            json={"store_id": other["store_id"]},
            headers={"X-CSRF-Token": csrf},
        )
        assert denied.status_code == 403
        assert denied.json()["detail"]["code"] == "STORE_FORBIDDEN"

        auth_service.add_membership(account["actor_id"], other["store_id"], "viewer")
        allowed = client.post(
            "/api/v1/admin/auth/stores/switch",
            json={"store_id": other["store_id"]},
            headers={"X-CSRF-Token": csrf},
        )
        assert allowed.status_code == 200
        assert allowed.json()["active_store"]["name"] == "另一家门店"
        assert client.get("/api/v1/admin/auth/me").json()["active_store"]["name"] == "另一家门店"

    def test_切换门店需要csrf(self, client, auth_service):
        account = provision(auth_service)
        other = auth_service.create_store("另一家门店")
        auth_service.add_membership(account["actor_id"], other["store_id"], "manager")
        client.post(
            "/api/v1/admin/auth/login",
            json={"username": "owner@example.test", "password": "Correct-Horse-7!"},
        )

        response = client.post(
            "/api/v1/admin/auth/stores/switch",
            json={"store_id": other["store_id"]},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "CSRF_INVALID"
