"""Phase I I7：干净环境部署验收 —— 核心闭环 + 安全/隐私/运行验收矩阵落地。

在会话级隔离 DB（conftest）中：初始化商家 → 登录 → 买家建会话/咨询 → 健康检查 →
隐私 dry-run → 指标，验证一段可按文档重复的核心闭环。
"""

import pytest
from fastapi.testclient import TestClient

from app import create_app


@pytest.fixture
def auth_service():
    from services.admin_auth import AdminAuthService

    service = AdminAuthService()
    service.clear_for_tests()
    yield service
    service.clear_for_tests()
    service.close()


@pytest.fixture
def client(auth_service):
    with TestClient(create_app()) as c:
        yield c


def test_clean_env_core_loop_acceptance(client, auth_service):
    """干净环境：配置/迁移/初始化/启动/健康/咨询闭环 一次性跑通。"""
    acct = auth_service.provision_account(
        "owner-accept@example.test", "Correct-Horse-7!", "验收", "验收门店", "owner"
    )
    login = client.post("/api/v1/admin/auth/login", json={
        "username": "owner-accept@example.test", "password": "Correct-Horse-7!",
    })
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]

    # 健康检查
    assert client.get("/health/live").status_code == 200
    ready = client.get("/health/ready")
    assert ready.status_code == 200

    # 买家咨询（Fake，零真实模型调用）
    conv = client.post("/api/v1/conversations", json={"user_id": "cust-accept"}).json()
    cid = conv["conversation_id"]
    turn = client.post(f"/api/v1/conversations/{cid}/turns",
                       json={"message": "基础护理多少钱？", "user_id": "cust-accept"})
    assert turn.status_code == 200

    # 隐私 dry-run + 权限（I2 最小闭环）
    anon = client.post(f"/api/v1/admin/customers/cust-accept/anonymize",
                       json={"request_id": "accept-dr", "dry_run": True},
                       headers={"X-CSRF-Token": csrf})
    assert anon.status_code == 200
    assert anon.json()["dry_run"] is True

    # 门店指标（I3 最小闭环）
    metrics = client.get("/api/v1/admin/metrics")
    assert metrics.status_code == 200
    assert "run_metrics" in metrics.json()


def test_security_acceptance_samples(client, auth_service):
    """I1/I5 安全验收矩阵代表性样本。"""
    auth_service.provision_account(
        "owner-sec@example.test", "Correct-Horse-7!", "S", "S门店", "owner"
    )
    r = client.get("/api/v1/admin/auth/me")
    assert r.status_code == 401  # 未登录拒绝

    # 错误脱敏：非法输入不回显
    bad = client.post("/api/v1/conversations", json={"user_id": 99, "message": "sk-leak-xxxx"})
    assert bad.status_code == 422
    assert "sk-leak" not in bad.text and "99" not in bad.text
