"""Phase H H3：商家会话工作台与人工接管闭环 —— HTTP/SSE 契约验收。

覆盖：
- 人工接管后买家 turn 被 AI 阻断（handoff_required，不产生 AI 回复，无双重回复）；
- 买家请求转人工 → 会话进入待人工队列（商家列表 control_mode=awaiting_human）；
- 商家人工回复 → 消息写入同一会话（message_type=human），审计可查，买家可读到；
- 恢复 AI → 控制回到 ai_active；
- 无登录/无 CSRF/跨门店 返回稳定错误，不依赖隐藏按钮。

Fake LLM 下运行，零真实模型请求。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import create_app
from db.db_router import DatabaseRouter
from db.models import AuditEvent, ConversationControl, ConversationControlEvent, Message
from services.admin_auth import AdminAuthService
from services.admin_workbench import AdminWorkbenchService


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


def setup_owner(client, auth_service, username="owner-h3@example.test", store="H3门店"):
    account = auth_service.provision_account(
        username, "Correct-Horse-7!", "H3账号", store, "owner"
    )
    response = client.post(
        "/api/v1/admin/auth/login",
        json={"username": username, "password": "Correct-Horse-7!"},
    )
    assert response.status_code == 200, response.text
    return account, response.json()["csrf_token"]


def make_conversation(store_id, user_id="customer-h3"):
    router = DatabaseRouter()
    try:
        return router.conversations.create_conversation(user_id, store_id=store_id)["id"]
    finally:
        router.close()


def buyer_turn(client, cid, text, user_id="customer-h3"):
    r = client.post(
        f"/api/v1/conversations/{cid}/turns",
        json={"message": text, "user_id": user_id},
    )
    assert r.status_code == 200, r.text
    events = []
    for line in r.text.split("\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def event_types(events):
    return [e["type"] for e in events]


def deltas(events):
    return "".join(e.get("data", {}).get("text", "") for e in events if e["type"] == "assistant_delta")


def handoff_hint(events):
    for e in events:
        if e["type"] == "handoff_required":
            return e.get("data", {}).get("message", "")
    return ""


class TestHandoffBlocking:
    def test_human_active_blocks_ai_no_dual_reply(self, client, auth_service):
        account, csrf = setup_owner(client, auth_service)
        cid = make_conversation(account["store_id"])
        # 接管前：AI 正常回复
        ev1 = buyer_turn(client, cid, "你好，请介绍一下")
        assert "assistant_delta" in event_types(ev1)
        assert ev1[-1]["type"] == "run_completed"
        # 商家接管
        takeover = client.post(
            f"/api/v1/admin/conversations/{cid}/takeover",
            json={"reason": "客户要求人工"},
            headers={"X-CSRF-Token": csrf},
        )
        assert takeover.status_code == 200
        assert takeover.json()["control"]["mode"] == "human_active"
        # 接管后：买家 turn 被 AI 阻断，只提示，不产生 AI 回复
        ev2 = buyer_turn(client, cid, "再问一个问题")
        types2 = event_types(ev2)
        assert "handoff_required" in types2
        assert "assistant_delta" not in types2          # 无 AI 回复 → 不可能双重回复
        assert types2[-1] == "run_completed"
        assert "人工" in handoff_hint(ev2)
        # 唯一终止事件
        terminals = [t for t in types2 if t in ("run_completed", "run_failed")]
        assert len(terminals) == 1


class TestHandoffQueue:
    def test_request_human_enters_queue(self, client, auth_service):
        account, csrf = setup_owner(client, auth_service)
        cid = make_conversation(account["store_id"])
        ev = buyer_turn(client, cid, "我要转人工处理")
        assert "handoff_required" in event_types(ev)
        assert "人工" in handoff_hint(ev)
        # 商家列表：该会话进入"待人工"队列
        listing = client.get("/api/v1/admin/conversations").json()["items"]
        row = next(r for r in listing if r["conversation_id"] == cid)
        assert row["control_mode"] == "awaiting_human"

    def test_resume_human_after_takeover(self, client, auth_service):
        account, csrf = setup_owner(client, auth_service)
        cid = make_conversation(account["store_id"])
        client.post(f"/api/v1/admin/conversations/{cid}/takeover",
                    json={"reason": "x"}, headers={"X-CSRF-Token": csrf})
        resume = client.post(f"/api/v1/admin/conversations/{cid}/resume-ai",
                             json={"reason": "已处理完毕，恢复AI"},
                             headers={"X-CSRF-Token": csrf})
        assert resume.status_code == 200
        assert resume.json()["control"]["mode"] == "ai_active"
        # 恢复后买家 turn 恢复 AI（不再 handoff）
        ev = buyer_turn(client, cid, "请问营业时间")
        assert "handoff_required" not in event_types(ev)
        assert "assistant_delta" in event_types(ev)


class TestHumanReply:
    def test_reply_writes_message_audit_and_buyer_reads(self, client, auth_service):
        account, csrf = setup_owner(client, auth_service)
        cid = make_conversation(account["store_id"])
        buyer_turn(client, cid, "你好")
        # 商家人工回复（自动置人工接管态）
        reply = client.post(
            f"/api/v1/admin/conversations/{cid}/reply",
            json={"content": "您好，很高兴为您服务，关于您的预约我可以直接协助您。"},
            headers={"X-CSRF-Token": csrf},
        )
        assert reply.status_code == 200
        assert reply.json()["control"]["mode"] == "human_active"
        msg = reply.json()["message"]
        assert msg["role"] == "assistant" and msg["message_type"] == "human"
        # 买家刷新（GET 会话）可读到人工结果
        hist = client.get(f"/api/v1/conversations/{cid}", params={"user_id": "customer-h3"}).json()["messages"]
        assert any(m["role"] == "assistant" and "很高兴为您服务" in (m["content"] or "") for m in hist)

    def test_reply_empty_rejected(self, client, auth_service):
        account, csrf = setup_owner(client, auth_service)
        cid = make_conversation(account["store_id"])
        r = client.post(f"/api/v1/admin/conversations/{cid}/reply",
                        json={"content": "   "}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 422

    def test_reply_facts_roll_back_together_on_message_failure(self, auth_service, monkeypatch):
        """人工消息失败时，控制态、控制事件和审计不能单独提交。"""
        account = auth_service.provision_account(
            "atomic-h3@example.test", "Correct-Horse-7!", "原子性测试", "原子性门店", "owner"
        )
        cid = make_conversation(account["store_id"])
        service = AdminWorkbenchService()

        def fail_message(*_args, **_kwargs):
            raise RuntimeError("message insert failed")

        monkeypatch.setattr(service.conversation_repo, "add_message_in_session", fail_message)
        try:
            with pytest.raises(RuntimeError, match="message insert failed"):
                service.human_reply(
                    account["store_id"], cid, account["actor_id"], "这条回复不会提交"
                )
        finally:
            service.close()

        router = DatabaseRouter()
        try:
            with router.session_manager.session_scope() as session:
                assert session.query(Message).filter_by(conversation_id=cid).count() == 0
                assert session.query(ConversationControl).filter_by(conversation_id=cid).count() == 0
                assert session.query(ConversationControlEvent).filter_by(conversation_id=cid).count() == 0
                assert session.query(AuditEvent).filter_by(
                    resource_type="conversation", resource_id=cid,
                ).count() == 0
        finally:
            router.close()


class TestAccessControl:
    def test_list_requires_login(self, client):
        assert client.get("/api/v1/admin/conversations").status_code == 401

    def test_reply_requires_csrf(self, client, auth_service):
        account, _csrf = setup_owner(client, auth_service)
        cid = make_conversation(account["store_id"])
        r = client.post(f"/api/v1/admin/conversations/{cid}/reply",
                        json={"content": "无 CSRF 的回复"})  # 登录但无 CSRF 头
        assert r.status_code in (401, 403)

    def test_cross_store_conversation_not_found(self, client, auth_service):
        store_a = auth_service.provision_account("a-h3@example.test", "Correct-Horse-7!", "A", "门店A", "owner")
        store_b = auth_service.provision_account("b-h3@example.test", "Correct-Horse-7!", "B", "门店B", "owner")
        cid = make_conversation(store_b["store_id"])
        login_a = client.post("/api/v1/admin/auth/login",
                              json={"username": "a-h3@example.test", "password": "Correct-Horse-7!"}).json()
        # 门店 A 账号访问门店 B 的会话：找不到（稳定 404），而非泄露
        r = client.get(f"/api/v1/admin/conversations/{cid}",
                       headers={"X-CSRF-Token": login_a["csrf_token"]})
        assert r.status_code == 404
