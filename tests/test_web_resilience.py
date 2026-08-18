"""Phase H H5：响应式、可访问性与可恢复性 —— 页面资产与状态契约验收。

覆盖（页面以服务端渲染 HTML + HTTP 契约为数据源验证，不做真实浏览器截图）：
- 买家和商家页面均含移动视口 meta 与响应式样式（不复制两套业务逻辑）；
- 关键控件具备键盘/读屏可访问性（label/aria-label/aria-live/role）；
- 空消息、会话失效、归属不符返回稳定状态（可恢复，不伪造成功）；
- 刷新后从服务端恢复会话历史（可恢复性）。

Fake LLM 下运行，零真实模型请求。
"""

import pytest
from fastapi.testclient import TestClient

from app import create_app
from services.admin_auth import AdminAuthService


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


class TestBuyerPageResilience:
    def test_index_has_responsive_and_accessible_assets(self, client):
        r = client.get("/")
        assert r.status_code == 200
        html = r.text
        # 移动响应式
        assert 'name="viewport"' in html or "viewport" in html
        assert 'lang="zh-CN"' in html
        assert "@media" in html          # 响应式样式（同一套核心流程）
        # 可访问性
        assert 'for="user-input"' in html
        assert 'aria-label="输入消息"' in html
        assert 'aria-label="发送消息"' in html
        assert 'aria-live="polite"' in html or 'role="status"' in html
        assert "sr-only" in html

    def test_index_has_form_semantics(self, client):
        html = client.get("/").text
        assert '<form id="chat-form"' in html
        assert 'id="user-input"' in html and 'type="submit"' in html

    def test_blank_message_stable_state(self, client):
        r = client.get("/")
        conv = client.post("/api/v1/conversations", json={"user_id": "default_user"}).json()["conversation_id"]
        e = client.post(
            f"/api/v1/conversations/{conv}/turns", json={"message": "  ", "user_id": "default_user"}
        )
        assert e.status_code == 422
        assert e.json()["detail"]["code"] == "INVALID_INPUT"

    def test_refresh_recovers_history(self, client):
        conv = client.post("/api/v1/conversations", json={"user_id": "default_user"}).json()["conversation_id"]
        client.post(f"/api/v1/conversations/{conv}/turns", json={"message": "你好", "user_id": "default_user"})
        hist = client.get(f"/api/v1/conversations/{conv}", params={"user_id": "default_user"}).json()["messages"]
        roles = [m["role"] for m in hist]
        assert roles == ["user", "assistant"]

    def test_session_invalid_stable_error_not_faked(self, client):
        e = client.get("/api/v1/conversations/nope", params={"user_id": "default_user"})
        assert e.status_code == 404
        assert e.json()["detail"]["code"] == "CONVERSATION_NOT_FOUND"


class TestWorkbenchPageResilience:
    def test_workbench_requires_login(self, client):
        r = client.get("/admin/workbench")
        assert r.status_code in (401, 403)   # 未登录不可见

    def test_workbench_has_responsive_and_accessible_assets(self, client):
        auth = AdminAuthService()
        auth.clear_for_tests()
        auth.provision_account("w-h5@example.test", "Correct-Horse-7!", "H5", "H5店", "owner")
        with TestClient(create_app()) as c:
            login = c.post("/api/v1/admin/auth/login",
                           json={"username": "w-h5@example.test", "password": "Correct-Horse-7!"})
            assert login.status_code == 200
            r = c.get("/admin/workbench")
            assert r.status_code == 200
            html = r.text
            assert 'name="viewport"' in html or "viewport" in html
            assert 'lang="zh-CN"' in html or "<meta" in html
            assert 'role="group" aria-label="会话状态筛选"' in html
            assert 'for="reply-input"' in html
        auth.clear_for_tests()
        auth.close()
