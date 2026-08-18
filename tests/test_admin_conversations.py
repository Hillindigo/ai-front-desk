"""Phase G G4：商家会话工作台与人工接管 HTTP 契约。"""

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


def setup_owner(client, auth_service):
    account = auth_service.provision_account(
        "owner-g4@example.test", "Correct-Horse-7!", "G4账号", "G4门店", "owner"
    )
    response = client.post(
        "/api/v1/admin/auth/login",
        json={"username": "owner-g4@example.test", "password": "Correct-Horse-7!"},
    )
    assert response.status_code == 200
    return account, response.json()["csrf_token"]


def create_conversation(store_id, user_id="customer-g4"):
    router = DatabaseRouter()
    try:
        return router.conversations.create_conversation(user_id, store_id=store_id)
    finally:
        router.close()


class TestAdminWorkbench:
    def test_列表详情和人工接管闭环(self, client, auth_service):
        account, csrf = setup_owner(client, auth_service)
        conversation = create_conversation(account["store_id"])
        cid = conversation["id"]

        listing = client.get("/api/v1/admin/conversations")
        assert listing.status_code == 200
        assert any(row["conversation_id"] == cid for row in listing.json()["items"])

        detail = client.get(f"/api/v1/admin/conversations/{cid}")
        assert detail.status_code == 200
        assert detail.json()["conversation_id"] == cid
        assert detail.json()["control"]["mode"] == "ai_active"

        takeover = client.post(
            f"/api/v1/admin/conversations/{cid}/takeover",
            json={"reason": "客户要求人工处理"},
            headers={"X-CSRF-Token": csrf},
        )
        assert takeover.status_code == 200
        assert takeover.json()["control"]["mode"] == "human_active"

        note = client.post(
            f"/api/v1/admin/conversations/{cid}/notes",
            json={"content": "已联系客户确认需求"},
            headers={"X-CSRF-Token": csrf},
        )
        assert note.status_code == 201

        resume = client.post(
            f"/api/v1/admin/conversations/{cid}/resume-ai",
            json={"reason": "人工处理完成"},
            headers={"X-CSRF-Token": csrf},
        )
        assert resume.status_code == 200
        assert resume.json()["control"]["mode"] == "ai_active"

    def test_错误门店不能读取会话(self, client, auth_service):
        account, _ = setup_owner(client, auth_service)
        router = DatabaseRouter()
        try:
            other_store = auth_service.create_store("G4另一门店")
            conversation = router.conversations.create_conversation(
                "other-customer", store_id=other_store["store_id"]
            )
        finally:
            router.close()
        response = client.get(f"/api/v1/admin/conversations/{conversation['id']}")
        assert response.status_code == 404

    def test_接管写操作需要csrf(self, client, auth_service):
        account, _ = setup_owner(client, auth_service)
        conversation = create_conversation(account["store_id"])
        response = client.post(
            f"/api/v1/admin/conversations/{conversation['id']}/takeover",
            json={"reason": "无 token"},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "CSRF_INVALID"
