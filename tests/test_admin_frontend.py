"""Phase G G8：统一后台壳层 HTTP 验收。"""

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


def test_登录后后台壳层恢复账号门店和模块入口(client, auth_service):
    auth_service.provision_account(
        "owner-g8@example.test", "Correct-Horse-7!", "G8账号", "G8门店", "owner"
    )
    login = client.post(
        "/api/v1/admin/auth/login",
        json={"username": "owner-g8@example.test", "password": "Correct-Horse-7!"},
    )
    assert login.status_code == 200

    page = client.get("/admin")
    assert page.status_code == 200
    assert "商家运营后台" in page.text
    assert "G8门店" in page.text
    assert "/api/v1/admin/conversations" in page.text
    assert "localStorage" not in page.text

    refreshed = client.get("/admin")
    assert refreshed.status_code == 200
