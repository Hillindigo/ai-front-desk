"""Phase H H1：买家 Web 会话与咨询体验 —— HTTP/SSE 契约验收。

覆盖（页面不可单测，以 SSE/HTTP 契约为渲染数据源验证）：
- 两个不同会话通过 Web API 互不串线（各自历史独立）；
- 刷新后从服务端恢复会话与最近消息（GET conversations）；
- 空消息返回 422 INVALID_INPUT；
- 会话不存在返回 404 CONVERSATION_NOT_FOUND，不伪造成功；
- 归属不符返回 403 CONVERSATION_ACCESS_DENIED（前端不静默重建掩盖）；
- SSE 事件流不泄漏 [THOUGHT]/[SIGNAL]/[REPLY] 等内部标记，唯一终止事件；
- 买家页面包含 H1 的会话隔离与恢复脚本，且不再渲染旧内部标记。

运行在 Fake LLM/Embedding（conftest 强制），零真实模型网络请求。
"""

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from api.chat_handler import reset_session_manager
    from app import create_app

    reset_session_manager()
    with TestClient(create_app()) as c:
        yield c
    reset_session_manager()


def _create_conversation(client, user_id="default_user") -> str:
    r = client.post("/api/v1/conversations", json={"user_id": user_id, "channel": "web"})
    assert r.status_code == 200, r.text
    return r.json()["conversation_id"]


def _stream_events(client, conversation_id: str, message: str, user_id="default_user",
                   client_request_id=None):
    """消费 turns SSE 流，返回 (type, data_dict) 列表。"""
    body = {"message": message, "user_id": user_id}
    if client_request_id:
        body["client_request_id"] = client_request_id
    r = client.post(f"/api/v1/conversations/{conversation_id}/turns", json=body)
    assert r.status_code == 200, f"turns 应 200，实际 {r.status_code}: {r.text}"
    events = []
    for raw_line in r.text.split("\n"):
        if raw_line.startswith("data: "):
            data = json.loads(raw_line[len("data: "):])
            events.append((data["type"], data))
    return events


class TestConversationIsolation:
    """两会话通过 Web API 不串线。"""

    def test_two_conversations_do_not_leak_via_web(self, client):
        a = _create_conversation(client, user_id="u1")
        b = _create_conversation(client, user_id="u2")
        _stream_events(client, a, "我想预约肩颈放松", user_id="u1")
        _stream_events(client, b, "今天天气怎么样", user_id="u2")

        hist_a = client.get(f"/api/v1/conversations/{a}", params={"user_id": "u1"}).json()["messages"]
        hist_b = client.get(f"/api/v1/conversations/{b}", params={"user_id": "u2"}).json()["messages"]
        ta = " ".join(m["content"] for m in hist_a)
        tb = " ".join(m["content"] for m in hist_b)
        assert "肩颈放松" in ta
        assert "肩颈放松" not in tb
        assert len(hist_a) == 2 and len(hist_b) == 2


class TestRefreshRecovery:
    """刷新后从服务端恢复会话与最近消息。"""

    def test_refresh_restores_history(self, client):
        cid = _create_conversation(client)
        _stream_events(client, cid, "你好，请介绍你们店")
        r = client.get(f"/api/v1/conversations/{cid}", params={"user_id": "default_user"})
        assert r.status_code == 200
        body = r.json()
        assert body["conversation_id"] == cid
        roles = [m["role"] for m in body["messages"]]
        assert roles == ["user", "assistant"]  # 服务端持久化的主/助手消息成对恢复
        assert all(m["content"] for m in body["messages"])

    def test_get_missing_conversation_404(self, client):
        r = client.get("/api/v1/conversations/does-not-exist", params={"user_id": "default_user"})
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "CONVERSATION_NOT_FOUND"


class TestErrorContract:
    """空消息、会话不存在、归属不符返回稳定契约错误。"""

    def test_empty_message_422(self, client):
        cid = _create_conversation(client)
        r = client.post(f"/api/v1/conversations/{cid}/turns", json={"message": "   ", "user_id": "default_user"})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "INVALID_INPUT"

    def test_turn_missing_conversation_404(self, client):
        r = client.post(
            "/api/v1/conversations/nope/turns",
            json={"message": "hello", "user_id": "default_user"},
        )
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "CONVERSATION_NOT_FOUND"

    def test_ownership_mismatch_403(self, client):
        cid = _create_conversation(client, user_id="u1")
        # 用不同 user_id 访问既有会话：归属不符，前端不应静默重建
        r = client.get(f"/api/v1/conversations/{cid}", params={"user_id": "u2"})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CONVERSATION_ACCESS_DENIED"

    def test_ownership_mismatch_turn_403(self, client):
        cid = _create_conversation(client, user_id="u1")
        r = client.post(
            f"/api/v1/conversations/{cid}/turns",
            json={"message": "hello", "user_id": "u2"},
        )
        assert r.status_code == 403


class TestSSEContract:
    """SSE 事件流不泄漏内部标记，且每轮唯一终止事件。"""

    def test_no_internal_markers_and_single_terminal(self, client):
        cid = _create_conversation(client)
        events = _stream_events(client, cid, "请记住我喜欢王师傅")
        types = [t for t, _ in events]
        # 唯一终止事件
        terminals = [t for t in types if t in ("run_completed", "run_failed")]
        assert len(terminals) == 1
        assert terminals[0] == "run_completed"
        # 首发
        assert types[0] == "run_started"
        # 不泄漏内部标记
        blob = json.dumps(events, ensure_ascii=False)
        for marker in ("[THOUGHT]", "[SIGNAL]", "[REPLY]", "[ERROR]"):
            assert marker not in blob, f"事件流泄漏内部标记 {marker}"
        # protocol_version 与单调序号
        for _, data in events:
            assert data["protocol_version"] == "v1"
        seqs = [d["sequence"] for _, d in events]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


class TestBuyerPageAssets:
    """买家页面包含 H1 的会话隔离/恢复脚本，不再依赖旧内部标记渲染。"""

    def test_index_has_h1_session_scripts(self, client):
        r = client.get("/")
        assert r.status_code == 200
        html = r.text
        assert 'id="chat-form"' in html and 'id="user-input"' in html
        # H1：标签页级会话隔离 + 服务端恢复
        assert "chat_tab_session" in html
        assert "restoreFromServer" in html
        # 页面不再通过解析 [THOUGHT]/[REPLY] 渲染（Phase D 起已移除）
        assert "replace(/\\[THOUGHT\\]/g" not in html
        assert "replace(/\\[REPLY\\]/g" not in html
