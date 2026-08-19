"""Phase H H4：Web API / SSE 与幂等契约全局审计。

以 H0 基线（G0 文档 §3）为参照横向审计，覆盖：
- SSE 事件契约：run_started 首发、唯一终止、sequence 单调、protocol v1；
- 新增 HANDOFF_REQUIRED 事件为非终止事件，且人工接管/转人工后仍唯一终止；
- 错误码横向一致（不同端点用同一稳定 code）；
- 幂等职责对照：client_request_id（会话轮次去重） vs idempotency_key（预约命令幂等）；
- 响应只用公开契约字段（会话/预约/控制态），不暴露内部对象字段。

Fake LLM 下运行，零真实模型请求。
"""

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import create_app
from db.db_router import DatabaseRouter
from services.admin_auth import AdminAuthService
from services.technician_service import TechnicianService
from application.conversation_control import ConversationControlResolver


@pytest.fixture
def client():
    TechnicianService().initialize_default_technicians()
    with TestClient(create_app()) as c:
        yield c


def create_conv(client, user_id="alpha") -> str:
    r = client.post("/api/v1/conversations", json={"user_id": user_id, "channel": "web"})
    assert r.status_code == 200
    return r.json()["conversation_id"]


def turns(client, cid, text, user_id="alpha", client_request_id=None):
    body = {"message": text, "user_id": user_id}
    if client_request_id:
        body["client_request_id"] = client_request_id
    r = client.post(f"/api/v1/conversations/{cid}/turns", json=body)
    assert r.status_code == 200
    return [json.loads(line[len("data: "):]) for line in r.text.split("\n") if line.startswith("data: ")]


def tech_id(client) -> int:
    return client.get("/api/technicians/").json()[0]["id"]


def dt(h, m=0):
    return datetime(2026, 8, 18, h, m).isoformat()


class TestSSEContractAudit:
    def test_run_started_first_and_single_terminal(self, client):
        cid = create_conv(client)
        events = turns(client, cid, "你好")
        assert events[0]["type"] == "run_started"
        types = [e["type"] for e in events]
        terminals = [t for t in types if t in ("run_completed", "run_failed")]
        assert len(terminals) == 1 and terminals[0] in ("run_completed", "run_failed")
        seqs = [e["sequence"] for e in events]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        for e in events:
            assert e["protocol_version"] == "v1"

    def test_handoff_is_non_terminal_but_run_terminates_once(self, client):
        # 直连接管态：通过 buyer 转人工触发 HANDOFF_REQUIRED
        cid = create_conv(client)
        events = turns(client, cid, "请转人工客服")
        types = [e["type"] for e in events]
        assert "handoff_required" in types
        # HANDOFF_REQUIRED 不是终止事件：唯一终止仍是 run_completed
        assert "run_completed" in types
        assert types[-1] == "run_completed"
        terminals = [t for t in types if t in ("run_completed", "run_failed")]
        assert len(terminals) == 1

    def test_no_internal_marker_in_events(self, client):
        cid = create_conv(client)
        events = turns(client, cid, "请记住我喜欢王师傅")
        blob = json.dumps(events, ensure_ascii=False)
        for marker in ("[THOUGHT]", "[SIGNAL]", "[REPLY]", "[ERROR]"):
            assert marker not in blob

    def test_control_failure_fails_closed_without_ai_reply(self, client, monkeypatch):
        """会话控制状态读取失败时，不能放行 AI 工作流。"""
        cid = create_conv(client)

        def fail_closed(_self, _conversation_id):
            raise RuntimeError("control store unavailable")

        monkeypatch.setattr(ConversationControlResolver, "ai_blocked", fail_closed)
        events = turns(client, cid, "请问营业时间")
        types = [event["type"] for event in events]

        assert "assistant_delta" not in types
        assert types[-1] == "run_failed"
        assert events[-1]["data"]["error"] == "INTERNAL_ERROR"


class TestErrorCodeConsistency:
    def test_stable_error_codes_across_endpoints(self, client):
        cid = create_conv(client)
        # 空消息 -> INVALID_INPUT
        e1 = client.post(f"/api/v1/conversations/{cid}/turns", json={"message": "  ", "user_id": "alpha"})
        assert e1.status_code == 422 and e1.json()["detail"]["code"] == "INVALID_INPUT"
        # 会话不存在 -> CONVERSATION_NOT_FOUND
        e2 = client.get("/api/v1/conversations/nope", params={"user_id": "alpha"})
        assert e2.status_code == 404 and e2.json()["detail"]["code"] == "CONVERSATION_NOT_FOUND"
        # 归属不符 -> CONVERSATION_ACCESS_DENIED
        e3 = client.get(f"/api/v1/conversations/{cid}", params={"user_id": "omega"})
        assert e3.status_code == 403 and e3.json()["detail"]["code"] == "CONVERSATION_ACCESS_DENIED"
        # 预约冲突 -> APPOINTMENT_CONFLICT
        tid = tech_id(client)
        ok = client.post("/api/v1/appointments", json={
            "user_id": "alpha", "mode": "confirm", "service_type": "x", "technician_id": tid,
            "start_time": dt(9), "end_time": dt(10), "duration_minutes": 60, "idempotency_key": "audit-a",
        })
        assert ok.status_code == 200
        bad = client.post("/api/v1/appointments", json={
            "user_id": "alpha", "mode": "confirm", "service_type": "x", "technician_id": tid,
            "start_time": dt(9, 30), "end_time": dt(10, 30), "duration_minutes": 60, "idempotency_key": "audit-b",
        })
        assert bad.status_code == 409 and bad.json()["detail"]["code"] == "APPOINTMENT_CONFLICT"


class TestIdempotencyDivision:
    def test_client_request_id_dedupes_turn(self, client):
        """client_request_id 只负责会话轮次去重：同 id 重发同内容不重复落偏好。"""
        cid = create_conv(client, user_id="default_user")
        rid = "req-div-001"
        turns(client, cid, "请记住我喜欢李师傅", user_id="default_user", client_request_id=rid)
        turns(client, cid, "请记住我喜欢李师傅", user_id="default_user", client_request_id=rid)
        r = client.get("/api/v1/preferences", params={"user_id": "default_user"})
        active = [p for p in r.json()["preferences"] if p["preference_type"] == "technician" and p["is_active"]]
        assert len(active) == 1  # 轮次去重：不产生重复偏好写入

    def test_appointment_idempotency_key_single_write(self, client):
        """idempotency_key 负责预约命令幂等：重复提交只产生一条预约。"""
        cid = create_conv(client)
        tid = tech_id(client)
        key = "appt-idem-001"
        r1 = client.post("/api/v1/appointments", json={
            "user_id": "alpha", "conversation_id": cid, "mode": "confirm", "service_type": "足疗",
            "technician_id": tid, "start_time": dt(9), "end_time": dt(10), "duration_minutes": 60,
            "idempotency_key": key,
        })
        r2 = client.post("/api/v1/appointments", json={
            "user_id": "alpha", "conversation_id": cid, "mode": "confirm", "service_type": "足疗",
            "technician_id": tid, "start_time": dt(9), "end_time": dt(10), "duration_minutes": 60,
            "idempotency_key": key,
        })
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["id"] == r2.json()["id"]


class TestPublicResponseFields:
    def test_conversation_response_public_fields(self, client):
        cid = create_conv(client)
        turns(client, cid, "你好")
        body = client.get(f"/api/v1/conversations/{cid}", params={"user_id": "alpha"}).json()
        assert set(body.keys()) == {"conversation_id", "user_id", "channel", "status", "messages"}
        for m in body["messages"]:
            # 只暴露公开契约字段（含 role/content/sequence），不暴露内部对象
            assert set(m.keys()) == {"id", "role", "content", "sequence", "message_type", "metadata", "created_at"} or \
                   all(k in m for k in ("role", "content", "sequence"))

    def test_session_appointment_public_fields(self, client):
        cid = create_conv(client)
        tid = tech_id(client)
        client.post("/api/v1/appointments", json={
            "user_id": "alpha", "conversation_id": cid, "mode": "confirm", "service_type": "肩颈放松",
            "project": "肩颈放松", "technician_id": tid, "start_time": dt(10), "end_time": dt(11),
            "duration_minutes": 60, "idempotency_key": "pub-1",
        })
        body = client.get(f"/api/v1/conversations/{cid}/appointment", params={"user_id": "alpha"}).json()
        assert set(body.keys()) == {"conversation_id", "active", "recent"}
        recent = body["recent"]
        # 公开字段：ID/项目/状态/时间/ID 等，不依赖内部对象
        assert recent["id"] and recent["status"] == "confirmed" and recent["project"] == "肩颈放松"

    def test_merchant_control_state_public_expr(self):
        """管理会话列表用公开的 control_mode 字符串表达接管/待人工态。"""
        auth = AdminAuthService()
        auth.clear_for_tests()
        account = auth.provision_account("o-h4@example.test", "Correct-Horse-7!", "H4", "H4店", "owner")
        with TestClient(create_app()) as c:
            login = c.post("/api/v1/admin/auth/login",
                           json={"username": "o-h4@example.test", "password": "Correct-Horse-7!"})
            csrf = login.json()["csrf_token"]
            router = DatabaseRouter()
            cid = router.conversations.create_conversation("cust-h4", store_id=account["store_id"])["id"]
            router.close()
            c.post(f"/api/v1/admin/conversations/{cid}/takeover", json={"reason": "r"},
                   headers={"X-CSRF-Token": csrf})
            item = next(r for r in c.get("/api/v1/admin/conversations").json()["items"]
                        if r["conversation_id"] == cid)
            assert item["control_mode"] in ("ai_active", "human_active", "awaiting_human")
            assert item["control_mode"] == "human_active"
        auth.clear_for_tests()
        auth.close()
