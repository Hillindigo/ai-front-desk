"""Phase E E7 测试：前端最小交互的 HTTP 验收。

覆盖（前端不可单测，以 SSE/HTTP 契约验证渲染数据源）：
- 偏好命令轮次产出"已记住"文案（前端按 assistant_delta 渲染）。
- 会话刷新/重新进入后历史消息与偏好状态一致（GET conversations 恢复）。
- 前端所需的 conversation_id 传递与 SSE v1 解析数据源保持兼容。
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


def _create_conversation(client) -> str:
    r = client.post("/api/v1/conversations", json={"user_id": "default_user"})
    assert r.status_code == 200, r.text
    return r.json()["conversation_id"]


def _stream_events(client, conversation_id: str, message: str):
    """消费 turns SSE 流，返回 (event_name, data) 列表。"""
    r = client.post(
        f"/api/v1/conversations/{conversation_id}/turns",
        json={"message": message, "user_id": "default_user"},
    )
    assert r.status_code == 200, r.text
    events = []
    for raw_line in r.text.split("\n"):
        if raw_line.startswith("data: "):
            data = json.loads(raw_line[len("data: "):])
            events.append((data["type"], data))
    return events


class TestMemoryFeedback:
    def test_偏好命令产生已记住文案(self, client):
        conv_id = _create_conversation(client)
        events = _stream_events(client, conv_id, "请记住我喜欢王师傅")
        deltas = "".join(e[1].get("data", {}).get("text", "") for e in events if e[0] == "assistant_delta")
        assert "已记住" in deltas  # 前端按 assistant_delta 渲染该文案
        tools = [e[1]["data"]["tool"] for e in events if e[0] == "tool_started"]
        assert "preference_memorize" in tools
        terminal = [e for e in events if e[0] in ("run_completed", "run_failed")]
        assert len(terminal) == 1 and terminal[0][0] == "run_completed"

    def test_偏好已持久化可查询(self, client):
        conv_id = _create_conversation(client)
        _stream_events(client, conv_id, "请记住我的服务项目是足部护理")
        r = client.get("/api/v1/preferences", params={"user_id": "default_user"})
        assert r.status_code == 200
        prefs = r.json()["preferences"]
        service_prefs = [p for p in prefs if p["preference_type"] == "service" and p["is_active"]]
        assert service_prefs and "足部护理" in service_prefs[0]["preference_value"]

    def test_偏好删除后不再用于推荐上下文(self, client):
        conv_id = _create_conversation(client)
        _stream_events(client, conv_id, "请记住我喜欢王师傅")
        r = client.delete("/api/v1/preferences/technician", params={"user_id": "default_user"})
        assert r.status_code == 200 and r.json()["tombstone"] is not None
        # 删除后管理列表不再有 active 技师偏好
        r = client.get("/api/v1/preferences", params={"user_id": "default_user"})
        active_tech = [p for p in r.json()["preferences"]
                       if p["preference_type"] == "technician" and p["is_active"]]
        assert active_tech == []


class TestSessionRecovery:
    def test_刷新后重新进入同一会话历史一致(self, client):
        conv_id = _create_conversation(client)
        _stream_events(client, conv_id, "请记住我喜欢王师傅")
        _stream_events(client, conv_id, "你好")
        # 模拟刷新：GET 会话（前端 localStorage 持有 conversation_id）
        r = client.get(f"/api/v1/conversations/{conv_id}", params={"user_id": "default_user"})
        assert r.status_code == 200
        messages = r.json()["messages"]
        contents = [m["content"] for m in messages]
        assert any("已记住" in c for c in contents)  # 历史消息可恢复
        assert len(messages) >= 4  # 用户2 + 助手2

    def test_断线重试不产生重复偏好(self, client):
        """client_request_id 幂等：重试复用原结果，不重复落偏好。"""
        conv_id = _create_conversation(client)
        req_id = "req-pref-001"
        r1 = client.post(
            f"/api/v1/conversations/{conv_id}/turns",
            json={"message": "请记住我喜欢李师傅", "user_id": "default_user", "client_request_id": req_id},
        )
        r2 = client.post(
            f"/api/v1/conversations/{conv_id}/turns",
            json={"message": "请记住我喜欢李师傅", "user_id": "default_user", "client_request_id": req_id},
        )
        assert r1.status_code == 200 and r2.status_code == 200
        r = client.get("/api/v1/preferences", params={"user_id": "default_user"})
        active = [p for p in r.json()["preferences"]
                  if p["preference_type"] == "technician" and p["is_active"]]
        assert len(active) == 1  # 重试不重复写入（幂等）