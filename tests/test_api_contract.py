"""Phase A: API 契约测试。

固化当前 API 行为基线（快乐路径 + 已知缺陷路径），全程运行在
MODEL_PROVIDER=fake 下，不依赖真实 LLM/Embedding。
"""

import pytest
from fastapi.testclient import TestClient

from app import create_app


@pytest.fixture(scope="module")
def client():
    """完整的 ASGI 栈（含 startup 初始化知识库/服务人员）。"""
    with TestClient(create_app()) as test_client:
        yield test_client


# ---------- 页面 ----------

def test_home_page_contract(client):
    """主页返回 HTML。"""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


# ---------- 聊天 ----------

def test_chat_stream_contract(client):
    """/chat/stream 返回非空文本流。"""
    resp = client.post("/chat/stream", json={"message": "你好"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert len(resp.text) > 0


def test_chat_legacy_contract(client):
    """/chat 兼容接口返回非空文本流。"""
    resp = client.post("/chat", json={"message": "你好"})
    assert resp.status_code == 200
    assert len(resp.text) > 0


# ---------- 任务分类 ----------

def test_task_classify_contract(client):
    """基线：TaskClassificationRequest 只有 text 字段（api/core/response_models.py），
    但 handler 访问 request.message（api/task.py:22），因此当前必然 400。
    契约固化现状；Phase D 统一 API 时修复。"""
    resp = client.post("/api/task/classify", json={"text": "肩颈放松有什么好处？"})
    assert resp.status_code in (200, 400), f"状态码异常: {resp.status_code}"
    if resp.status_code == 200:
        body = resp.json()
        assert "message" in body and "data" in body


# ---------- 知识库 ----------

def test_knowledge_list_contract(client):
    """知识库列表返回 documents/categories/total_count。"""
    resp = client.get("/api/knowledge/")
    assert resp.status_code == 200
    body = resp.json()
    assert "documents" in body
    assert "categories" in body
    assert "total_count" in body


def test_knowledge_search_contract(client):
    """知识搜索返回 status/data/count。"""
    resp = client.post("/api/knowledge/search", json={"query": "营业时间"})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "success"
    assert "data" in body
    assert "count" in body


# ---------- 服务人员 ----------

def test_technician_list_contract(client):
    """服务人员列表返回数组（注意 prefix 是复数 /api/technicians）。"""
    resp = client.get("/api/technicians/")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_technician_schedules_today_contract(client):
    """今日排班接口可访问（prefix 复数）。"""
    resp = client.get("/api/technicians/schedules/today")
    assert resp.status_code == 200


# ---------- 会话 API（Phase B B4） ----------

def test_create_conversation_contract(client):
    """创建会话返回 conversation_id。"""
    resp = client.post("/api/v1/conversations", json={"user_id": "demo-user"})
    assert resp.status_code == 200
    body = resp.json()
    assert "conversation_id" in body
    assert body["user_id"] == "demo-user"
    assert body["status"] == "active"


def test_conversation_turns_contract(client):
    """向会话发送消息，返回流式文本。"""
    conv = client.post("/api/v1/conversations", json={"user_id": "demo-user"}).json()
    resp = client.post(
        f"/api/v1/conversations/{conv['conversation_id']}/turns",
        json={"message": "你好", "user_id": "demo-user"},
    )
    assert resp.status_code == 200
    assert len(resp.text) > 0


def test_conversation_get_restores_messages(client):
    """GET 会话返回最近消息（含刚写入的 user/assistant 消息）。"""
    conv = client.post("/api/v1/conversations", json={"user_id": "demo-user"}).json()
    cid = conv["conversation_id"]
    client.post(f"/api/v1/conversations/{cid}/turns", json={"message": "我想预约肩颈放松", "user_id": "demo-user"})

    resp = client.get(f"/api/v1/conversations/{cid}", params={"user_id": "demo-user"})
    assert resp.status_code == 200
    body = resp.json()
    roles = [m["role"] for m in body["messages"]]
    assert "user" in roles
    assert "assistant" in roles


def test_conversation_not_found_contract(client):
    """不存在的会话返回 404。"""
    resp = client.get("/api/v1/conversations/no-such-id", params={"user_id": "u"})
    assert resp.status_code == 404


def test_conversation_ownership_contract(client):
    """归属不符返回 403。"""
    conv = client.post("/api/v1/conversations", json={"user_id": "alice"}).json()
    resp = client.get(f"/api/v1/conversations/{conv['conversation_id']}", params={"user_id": "bob"})
    assert resp.status_code == 403


def test_turns_empty_message_contract(client):
    """空消息返回 422。"""
    conv = client.post("/api/v1/conversations", json={"user_id": "u"}).json()
    resp = client.post(
        f"/api/v1/conversations/{conv['conversation_id']}/turns",
        json={"message": "", "user_id": "u"},
    )
    assert resp.status_code == 422


def test_chat_stream_with_explicit_conversation(client):
    """旧 /chat/stream 携带 conversation_id 时转发到对应会话。"""
    conv = client.post("/api/v1/conversations", json={"user_id": "u"}).json()
    resp = client.post("/chat/stream", json={
        "message": "你好",
        "conversation_id": conv["conversation_id"],
        "user_id": "u",
    })
    assert resp.status_code == 200
    assert len(resp.text) > 0


def test_chat_stream_default_conversation_stable(client):
    """旧 /chat/stream 不带 ID 时落到同一默认会话（两次请求一致）。"""
    # 默认会话的 ID 无法从响应直接拿到；通过两次调用后查询默认会话确认
    from api.chat_handler import get_session_manager
    mgr = get_session_manager()
    d1 = mgr.get_or_create_default("default_user")
    d2 = mgr.get_or_create_default("default_user")
    assert d1.conversation_id == d2.conversation_id
    assert len(mgr.repository.get_recent_messages(d1.conversation_id)) >= 2


# ---------- 已知缺陷基线（Phase D 修复） ----------

def test_appointment_create_defect_baseline(client):
    """Phase C C6 已修复：/api/appointment/create 改为领域服务适配器。

    A-R3 原缺陷（调用不存在方法恒 400）已修复；现在返回 200 草稿。
    旧接口与新接口共享同一领域服务，不再产生第二套写入逻辑。
    """
    resp = client.post("/api/appointment/create", json={
        "user_id": "u1",
        "service_type": "肩颈放松",
        "preferred_time": "明天下午",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "draft"


def test_consultation_ask_defect_baseline(client):
    """Phase D D6 已修复：/api/consultation/ask 走统一咨询路径（ConsultantAgent.consult）。

    A-R3 原缺陷（调用不存在方法恒 400）已修复，现在返回 200 咨询结果。
    """
    resp = client.post("/api/consultation/ask", json={
        "user_id": "u1",
        "question": "营业时间是什么？",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["answer"] is not None
