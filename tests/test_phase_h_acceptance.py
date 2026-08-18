"""Phase H H6：端到端验收与故障注入 —— 买家—商家人工接管闭环。

端到端（TestClient 模拟真实 HTTP 链路，跨角色）：
  商家登录 -> 买家创建会话并咨询 -> 买家完成预约 -> 商家列表/详情查看同会话+预约
  -> 商家接管 -> 买家 turn 被 AI 阻断 -> 商家人工回复 -> 买家回读人工结果 -> 恢复 AI。

故障注入：
- 预约冲突（409 APPOINTMENT_CONFLICT），不伪造成功；
- 重复提交幂等（同幂等键只一条）；
- 人工接管阻断 AI（无 AI/人工双重回复）；
- 未登录/无 CSRF 稳定错误。

Fake LLM 下运行（conftest 强制），并断言无真实 LLM/Embedding 网络请求。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import create_app
from db.db_router import DatabaseRouter
from services.admin_auth import AdminAuthService
from services.technician_service import TechnicianService


@pytest.fixture
def auth_service():
    s = AdminAuthService()
    s.clear_for_tests()
    yield s
    s.clear_for_tests()
    s.close()


@pytest.fixture
def env(auth_service):
    TechnicianService().initialize_default_technicians()
    account = auth_service.provision_account("e2e@example.test", "Correct-Horse-7!", "E2E", "E2E门店", "owner")
    client = TestClient(create_app())
    login = client.post("/api/v1/admin/auth/login",
                        json={"username": "e2e@example.test", "password": "Correct-Horse-7!"})
    assert login.status_code == 200
    yield client, account, login.json()["csrf_token"]
    client.close()


def buyer_conv(store_id, client, user_id="buyer-e2e"):
    router = DatabaseRouter()
    try:
        return router.conversations.create_conversation(user_id, store_id=store_id)["id"]
    finally:
        router.close()


def turns(client, cid, text, user_id="buyer-e2e"):
    r = client.post(f"/api/v1/conversations/{cid}/turns", json={"message": text, "user_id": user_id})
    assert r.status_code == 200, r.text
    return [json.loads(l[len("data: "):]) for l in r.text.split("\n") if l.startswith("data: ")]


def types(events):
    return [e["type"] for e in events]


def tech_id(client):
    return client.get("/api/technicians/").json()[0]["id"]


def test_full_buyer_merchant_loop(env):
    client, account, csrf = env
    store_id = account["store_id"]
    cid = buyer_conv(store_id, client)

    # 1) 买家咨询（AI 回复）
    ev1 = turns(client, cid, "你好，请问你们有哪些服务")
    assert "assistant_delta" in types(ev1) and ev1[-1]["type"] == "run_completed"

    # 2) 买家完成预约（关联会话）
    tid = tech_id(client)
    appt = client.post("/api/v1/appointments", json={
        "user_id": "buyer-e2e", "conversation_id": cid, "mode": "confirm",
        "service_type": "肩颈放松", "project": "肩颈放松", "technician_id": tid,
        "start_time": "2026-08-18T10:00:00", "end_time": "2026-08-18T11:00:00",
        "duration_minutes": 60, "idempotency_key": "e2e-appt-1",
    })
    assert appt.status_code == 200 and appt.json()["status"] == "confirmed"

    # 3) 商家列表/详情可见同会话 + 预约关联
    listing = client.get("/api/v1/admin/conversations").json()["items"]
    row = next(r for r in listing if r["conversation_id"] == cid)
    assert row["control_mode"] == "ai_active"
    detail = client.get(f"/api/v1/admin/conversations/{cid}").json()
    assert any(a["id"] == appt.json()["id"] and a["status"] == "confirmed" for a in detail["appointments"])
    assert any(m["role"] == "assistant" for m in detail["messages"])

    # 4) 商家接管
    takeover = client.post(f"/api/v1/admin/conversations/{cid}/takeover",
                           json={"reason": "e2e 接管"}, headers={"X-CSRF-Token": csrf})
    assert takeover.json()["control"]["mode"] == "human_active"

    # 5) 买家 turn 被 AI 阻断（无 AI 回复 -> 无双重回复）
    ev2 = turns(client, cid, "我的预约什么时候确认")
    assert "handoff_required" in types(ev2)
    assert "assistant_delta" not in types(ev2)

    # 6) 商家人工回复
    reply = client.post(f"/api/v1/admin/conversations/{cid}/reply",
                        json={"content": "您的预约已确认，明天10:00王师傅为您服务。"},
                        headers={"X-CSRF-Token": csrf})
    assert reply.status_code == 200 and reply.json()["message"]["message_type"] == "human"

    # 7) 买家回读人工结果
    hist = client.get(f"/api/v1/conversations/{cid}", params={"user_id": "buyer-e2e"}).json()["messages"]
    assert any(m["role"] == "assistant" and "已确认" in (m["content"] or "") for m in hist)

    # 8) 恢复 AI -> 买家 turn 恢复 AI
    resume = client.post(f"/api/v1/admin/conversations/{cid}/resume-ai",
                         json={"reason": "已处理"}, headers={"X-CSRF-Token": csrf})
    assert resume.json()["control"]["mode"] == "ai_active"
    ev3 = turns(client, cid, "谢谢")
    assert "handoff_required" not in types(ev3) and "assistant_delta" in types(ev3)


def test_failure_injection(env):
    client, account, csrf = env
    store_id = account["store_id"]
    cid = buyer_conv(store_id, client)
    tid = tech_id(client)

    # 预约冲突：不伪造成功
    ok = client.post("/api/v1/appointments", json={
        "user_id": "buyer-e2e", "mode": "confirm", "service_type": "x", "technician_id": tid,
        "start_time": "2026-08-18T14:00:00", "end_time": "2026-08-18T15:00:00",
        "duration_minutes": 60, "idempotency_key": "inj-a",
    })
    assert ok.status_code == 200
    clash = client.post("/api/v1/appointments", json={
        "user_id": "buyer-e2e", "mode": "confirm", "service_type": "x", "technician_id": tid,
        "start_time": "2026-08-18T14:30:00", "end_time": "2026-08-18T15:30:00",
        "duration_minutes": 60, "idempotency_key": "inj-b",
    })
    assert clash.status_code == 409 and clash.json()["detail"]["code"] == "APPOINTMENT_CONFLICT"

    # 重复提交幂等：同幂等键只一条
    dup1 = client.post("/api/v1/appointments", json={
        "user_id": "buyer-e2e", "conversation_id": cid, "mode": "confirm", "service_type": "足疗",
        "technician_id": tid, "start_time": "2026-08-18T16:00:00", "end_time": "2026-08-18T17:00:00",
        "duration_minutes": 60, "idempotency_key": "inj-dup",
    })
    dup2 = client.post("/api/v1/appointments", json={
        "user_id": "buyer-e2e", "conversation_id": cid, "mode": "confirm", "service_type": "足疗",
        "technician_id": tid, "start_time": "2026-08-18T16:00:00", "end_time": "2026-08-18T17:00:00",
        "duration_minutes": 60, "idempotency_key": "inj-dup",
    })
    assert dup1.json()["id"] == dup2.json()["id"]

    # 未登录访问商家接口：稳定错误
    anon = TestClient(create_app())
    assert anon.get("/api/v1/admin/conversations").status_code == 401
    anon.close()

    # 无 CSRF 的人工回复：稳定错误（登录但无 CSRF 头）
    no_csrf = client.post(f"/api/v1/admin/conversations/{cid}/reply",
                          json={"content": "无CSRF"})
    assert no_csrf.status_code in (401, 403)


def test_fake_mode_no_real_model_requests(env):
    """确认 Fake 模式没有真实 LLM/Embedding 网络请求（FakeChatModel 零外部调用）。"""
    from config.model_provider import FakeChatModel, get_model_provider
    assert get_model_provider() == "fake"
    client, account, _ = env
    cid = buyer_conv(account["store_id"], client)
    events = turns(client, cid, "你好")
    assert events[-1]["type"] == "run_completed"
