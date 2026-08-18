"""Phase G G6：客户运营与回访任务契约。"""

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
        "owner-g6@example.test", "Correct-Horse-7!", "G6账号", "G6门店", "owner"
    )
    login = client.post(
        "/api/v1/admin/auth/login",
        json={"username": "owner-g6@example.test", "password": "Correct-Horse-7!"},
    )
    return account, login.json()["csrf_token"]


def create_customer(store_id, user_id):
    router = DatabaseRouter()
    try:
        return router.conversations.create_conversation(user_id, store_id=store_id)
    finally:
        router.close()


class TestAdminCustomers:
    def test_客户详情和回访任务状态流转(self, client, auth_service):
        account, csrf = setup(client, auth_service)
        create_customer(account["store_id"], "customer-g6")

        customers = client.get("/api/v1/admin/customers")
        assert customers.status_code == 200
        assert {x["customer_user_id"] for x in customers.json()["items"]} >= {"customer-g6"}

        detail = client.get("/api/v1/admin/customers/customer-g6")
        assert detail.status_code == 200
        assert detail.json()["store_id"] == account["store_id"]

        created = client.post(
            "/api/v1/admin/customers/customer-g6/follow-ups",
            json={"reason": "预约后回访"},
            headers={"X-CSRF-Token": csrf},
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["task_id"]

        completed = client.post(
            f"/api/v1/admin/customers/follow-ups/{task_id}/status",
            json={"status": "completed"},
            headers={"X-CSRF-Token": csrf},
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"

    def test_错误门店客户不可见(self, client, auth_service):
        account, _ = setup(client, auth_service)
        other = auth_service.create_store("G6另一门店")
        create_customer(other["store_id"], "other-g6")
        response = client.get("/api/v1/admin/customers/other-g6")
        assert response.status_code == 404

    def test_回访任务写操作需要csrf(self, client, auth_service):
        account, _ = setup(client, auth_service)
        create_customer(account["store_id"], "customer-g6")
        response = client.post(
            "/api/v1/admin/customers/customer-g6/follow-ups",
            json={"reason": "缺少 token"},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "CSRF_INVALID"
