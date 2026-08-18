"""Phase I I2：PII 治理 —— 客户数据导出与删除/匿名化（D9 商家侧 / D10 登记 / D11 令牌）。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

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


def provision(service, username="owner-i2@example.test", role="owner"):
    return service.provision_account(
        username=username, password="Correct-Horse-7!",
        display_name=username.split("@")[0], store_name="I2门店", role=role,
    )


def login(client, username="owner-i2@example.test"):
    r = client.post("/api/v1/admin/auth/login", json={
        "username": username, "password": "Correct-Horse-7!",
    })
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


def seed_customer(router, store_id, user_id="cust-i2"):
    conv = router.conversations.create_conversation(user_id, store_id=store_id)
    router.conversations.add_message(conv["id"], "user", "你好，我叫张三，电话 13800000000")
    router.conversations.add_message(conv["id"], "assistant", "已为您记录")
    with router.session_manager.session_scope() as session:
        session.execute(text(
            "INSERT INTO preferences (user_id, store_id, preference_type, preference_value, source, confidence) "
            "VALUES (:u, :s, 'service', '肩颈放松', 'explicit_memorize', 100)"
        ), {"u": user_id, "s": store_id})
    return conv


def message_contents(router, conv_id):
    with router.session_manager.session_scope() as session:
        rows = session.execute(text(
            "SELECT content FROM messages WHERE conversation_id=:c ORDER BY sequence"
        ), {"c": conv_id}).fetchall()
        return [r[0] for r in rows]


def registry_count(auth_service, request_id):
    with auth_service.session_manager.session_scope() as session:
        return session.execute(text(
            "SELECT COUNT(*) FROM privacy_deletion_registry WHERE request_id=:r"
        ), {"r": request_id}).scalar()


class TestExport:
    def test_export_requires_permission_and_csrf(self, auth_service, client):
        owner = provision(auth_service)
        provision(auth_service, username="viewer-i2@example.test", role="viewer")
        csrf = login(client, "viewer-i2@example.test")
        r = client.post(f"/api/v1/admin/customers/any/export",
                        headers={"X-CSRF-Token": csrf})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "PERMISSION_DENIED"

        login(client)  # owner
        r = client.post("/api/v1/admin/customers/any/export")
        assert r.status_code == 403  # 缺 CSRF

    def test_export_and_single_use_download(self, auth_service, client):
        owner = provision(auth_service)
        router = DatabaseRouter()
        try:
            seed_customer(router, owner["store_id"])
        finally:
            router.close()
        csrf = login(client)
        r = client.post(f"/api/v1/admin/customers/cust-i2/export",
                        headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["counts"]["messages"] >= 2
        assert body["download_token"]
        down = client.get(
            f"/api/v1/admin/customer-exports/{body['export_id']}",
            params={"token": body["download_token"]},
        )
        assert down.status_code == 200
        data = down.json()
        assert any("张三" in m["content"] for m in data["messages"])
        # 一次性：再次领取被拒
        again = client.get(
            f"/api/v1/admin/customer-exports/{body['export_id']}",
            params={"token": body["download_token"]},
        )
        assert again.status_code == 422


class TestAnonymize:
    def test_dry_run_does_not_change(self, auth_service, client):
        owner = provision(auth_service)
        router = DatabaseRouter()
        try:
            conv = seed_customer(router, owner["store_id"])
        finally:
            router.close()
        csrf = login(client)
        before = message_contents(DatabaseRouter(), conv["id"])
        r = client.post(f"/api/v1/admin/customers/cust-i2/anonymize",
                        json={"request_id": "dr-1", "dry_run": True},
                        headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.text
        assert r.json()["dry_run"] is True
        assert "张三" in before[0]
        assert message_contents(DatabaseRouter(), conv["id"]) == before
        assert registry_count(auth_service, "dr-1") == 0

    def test_real_anonymize_clears_and_idempotent(self, auth_service, client):
        owner = provision(auth_service)
        router = DatabaseRouter()
        try:
            conv = seed_customer(router, owner["store_id"])
        finally:
            router.close()
        csrf = login(client)
        r = client.post(f"/api/v1/admin/customers/cust-i2/anonymize",
                        json={"request_id": "real-1", "dry_run": False},
                        headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.text
        assert r.json()["dry_run"] is False
        assert r.json()["anonymized"] is True

        rr = DatabaseRouter()
        try:
            contents = message_contents(rr, conv["id"])
        finally:
            rr.close()
        assert all(c == "[已删除]" for c in contents)

        with auth_service.session_manager.session_scope() as session:
            active = session.execute(text(
                "SELECT COUNT(*) FROM preferences WHERE user_id='cust-i2' AND is_active=1"
            )).scalar()
        assert active == 0
        assert registry_count(auth_service, "real-1") == 1

        # 幂等：同 request_id 重放
        r2 = client.post(f"/api/v1/admin/customers/cust-i2/anonymize",
                         json={"request_id": "real-1", "dry_run": False},
                         headers={"X-CSRF-Token": csrf})
        assert r2.status_code == 200
        assert r2.json().get("idempotent_replay") is True

    def test_cross_store_does_not_match(self, auth_service, client):
        owner = provision(auth_service)
        other = auth_service.create_store("另一门店")
        router = DatabaseRouter()
        try:
            seed_customer(router, other["store_id"], user_id="cust-other")
        finally:
            router.close()
        csrf = login(client)
        # owner 当前门店是 owner 的门店，不是 other → 不命中
        r = client.post(f"/api/v1/admin/customers/cust-other/anonymize",
                        json={"request_id": "xs-1", "dry_run": True},
                        headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        assert r.json()["messages"] == 0
