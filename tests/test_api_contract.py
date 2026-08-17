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


# ---------- 已知缺陷基线（Phase D 修复） ----------

def test_appointment_create_defect_baseline(client):
    """基线：api/appointment.py 调用 AppointmentAgent.process_appointment_request
    （方法不存在），当前返回 400 + detail。Phase D 修复后此测试应改为 200。"""
    resp = client.post("/api/appointment/create", json={
        "user_id": "u1",
        "service_type": "肩颈放松",
        "preferred_time": "明天下午",
    })
    assert resp.status_code == 400
    assert "detail" in resp.json()


def test_consultation_ask_defect_baseline(client):
    """基线：api/consultation.py 调用 ConsultantAgent.process_consultation
    （方法不存在），当前返回 400 + detail。Phase D 修复后此测试应改为 200。"""
    resp = client.post("/api/consultation/ask", json={
        "user_id": "u1",
        "question": "营业时间是什么？",
    })
    assert resp.status_code == 400
    assert "detail" in resp.json()
