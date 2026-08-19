"""Phase G repair regressions: authentication and active-store isolation."""

import pytest
from fastapi.testclient import TestClient

from app import create_app
from services.admin_auth import AdminAuthService


@pytest.fixture
def admin_context():
    auth = AdminAuthService()
    auth.clear_for_tests()
    account = auth.provision_account(
        username="phase-g-regression@example.test",
        password="Correct-Horse-7!",
        display_name="Phase G",
        store_name="Phase G A",
        role="owner",
    )
    other = auth.create_store("Phase G B")
    auth.add_membership(account["actor_id"], other["store_id"], "owner")
    with TestClient(create_app()) as client:
        login = client.post(
            "/api/v1/admin/auth/login",
            json={
                "username": "phase-g-regression@example.test",
                "password": "Correct-Horse-7!",
            },
        )
        assert login.status_code == 200, login.text
        csrf = login.json()["csrf_token"]
        client.headers.update({"X-CSRF-Token": csrf})
        yield client, csrf, account, other
    auth.clear_for_tests()
    auth.close()


def test_knowledge_endpoints_reject_anonymous_reads_and_writes():
    with TestClient(create_app()) as client:
        assert client.get("/api/knowledge/").status_code == 401
        assert client.get("/api/v1/knowledge/documents").status_code == 401
        assert client.post(
            "/api/knowledge/",
            json={"question": "q", "answer": "a", "category": "c"},
        ).status_code == 401
        assert client.post(
            "/api/v1/knowledge/documents",
            json={"title": "q", "content": "a", "category": "c"},
        ).status_code == 401


def test_knowledge_and_dashboard_follow_active_store(admin_context):
    client, csrf, account, other = admin_context
    created_a = client.post(
        "/api/v1/knowledge/documents",
        json={"title": "仅 A", "content": "A 内容", "category": "测试"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created_a.status_code == 201, created_a.text

    switched = client.post(
        "/api/v1/admin/auth/stores/switch",
        json={"store_id": other["store_id"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert switched.status_code == 200, switched.text

    listed_b = client.get("/api/v1/knowledge/documents")
    assert listed_b.status_code == 200
    assert all(item["title"] != "仅 A" for item in listed_b.json()["items"])
    dashboard_b = client.get("/admin")
    assert dashboard_b.status_code == 200
    assert "仅 A" not in dashboard_b.text

    created_b = client.post(
        "/api/v1/knowledge/documents",
        json={"title": "仅 B", "content": "B 内容", "category": "测试"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created_b.status_code == 201, created_b.text
    assert any(
        item["title"] == "仅 B"
        for item in client.get("/api/v1/knowledge/documents").json()["items"]
    )

    back = client.post(
        "/api/v1/admin/auth/stores/switch",
        json={"store_id": account["store_id"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert back.status_code == 200
    titles_a = {
        item["title"]
        for item in client.get("/api/v1/knowledge/documents").json()["items"]
    }
    assert "仅 A" in titles_a and "仅 B" not in titles_a
