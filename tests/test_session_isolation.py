"""B6：会话隔离、服务重启恢复与并发测试。

覆盖计划 B6 测试表：两会话消息隔离、预约草稿隔离、服务重启恢复、
同会话并发、不同会话并发、消息顺序、数据库隔离（无模型启动见
test_startup_guard.py）。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from api.chat_handler import ProcessUserInput_stream, get_session_manager, reset_session_manager


@pytest.fixture(autouse=True)
def fresh_session_manager():
    """每个测试使用干净的会话管理器（模拟进程内无缓存状态）。"""
    reset_session_manager()
    yield
    reset_session_manager()


async def run_turn(conversation_id: str, user_id: str, text: str) -> str:
    out = []
    async for token in ProcessUserInput_stream(text, conversation_id=conversation_id, user_id=user_id):
        out.append(token)
    return "".join(out)


# ---------------- 消息隔离 ----------------

@pytest.mark.asyncio
async def test_two_conversations_messages_isolated():
    mgr = get_session_manager()
    a = mgr.create_conversation(user_id="u1")
    b = mgr.create_conversation(user_id="u2")

    await run_turn(a.conversation_id, "u1", "我想预约明天下午2点的肩颈放松，女服务人员")
    await run_turn(b.conversation_id, "u2", "今天天气怎么样？")

    msgs_a = mgr.repository.get_recent_messages(a.conversation_id)
    msgs_b = mgr.repository.get_recent_messages(b.conversation_id)
    a_text = " ".join(m["content"] for m in msgs_a)
    b_text = " ".join(m["content"] for m in msgs_b)
    assert "肩颈放松" in a_text
    assert "肩颈放松" not in b_text


# ---------------- 预约草稿隔离 ----------------

@pytest.mark.asyncio
async def test_two_conversations_draft_isolated():
    mgr = get_session_manager()
    a = mgr.create_conversation(user_id="u1")
    b = mgr.create_conversation(user_id="u2")

    # A：部分预约信息（只有项目）-> 草稿 project=肩颈放松
    await run_turn(a.conversation_id, "u1", "我想预约肩颈放松")
    # B：不同项目 -> 草稿 project=足疗（"我想预约足疗"确保走预约流程）
    await run_turn(b.conversation_id, "u2", "我想预约足疗")

    sa = mgr.get_or_create_session(a.conversation_id, user_id="u1")
    sb = mgr.get_or_create_session(b.conversation_id, user_id="u2")
    draft_a = sa.agent.appointment_agent.appointment_history
    draft_b = sb.agent.appointment_agent.appointment_history
    assert draft_a.get("project") == "肩颈放松"
    assert draft_b.get("project") == "足疗"
    assert draft_a is not draft_b


# ---------------- 服务重启恢复 ----------------

def test_service_restart_recovers_conversation():
    """模拟服务重启：旧应用写消息 -> 清空内存缓存 -> 新应用实例恢复。"""
    reset_session_manager()
    with TestClient(__import__("app").create_app()) as c1:
        conv = c1.post("/api/v1/conversations", json={"user_id": "u1"}).json()
        cid = conv["conversation_id"]
        turn = c1.post(
            f"/api/v1/conversations/{cid}/turns",
            json={"message": "我想预约肩颈放松", "user_id": "u1"},
        )
        assert turn.status_code == 200

    # 模拟重启：进程内存缓存清空
    reset_session_manager()
    with TestClient(__import__("app").create_app()) as c2:
        resp = c2.get(f"/api/v1/conversations/{cid}", params={"user_id": "u1"})
        assert resp.status_code == 200
        roles = [m["role"] for m in resp.json()["messages"]]
        assert roles == ["user", "assistant"]


# ---------------- 并发 ----------------

@pytest.mark.asyncio
async def test_same_conversation_concurrent_turns_do_not_interleave():
    """同会话两个并发 turn：锁串行，消息成对（user->assistant）不交错。"""
    mgr = get_session_manager()
    s = mgr.create_conversation(user_id="u1")
    cid = s.conversation_id

    await asyncio.gather(
        run_turn(cid, "u1", "我想预约肩颈放松"),
        run_turn(cid, "u1", "今天天气怎么样？"),
    )

    msgs = mgr.repository.get_recent_messages(cid)
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "user", "assistant"], f"消息交错: {roles}"


@pytest.mark.asyncio
async def test_different_conversations_concurrent_turns_isolated():
    """不同会话并发 turn：独立锁与消息列表，互不影响。"""
    mgr = get_session_manager()
    a = mgr.create_conversation(user_id="u1")
    b = mgr.create_conversation(user_id="u2")

    await asyncio.gather(
        run_turn(a.conversation_id, "u1", "我想预约明天下午2点的肩颈放松，女服务人员"),
        run_turn(b.conversation_id, "u2", "我想预约足疗"),
    )

    msgs_a = mgr.repository.get_recent_messages(a.conversation_id)
    msgs_b = mgr.repository.get_recent_messages(b.conversation_id)
    assert len(msgs_a) == 2
    assert len(msgs_b) == 2


# ---------------- 消息顺序 ----------------

@pytest.mark.asyncio
async def test_message_order_stable_after_multiple_turns():
    mgr = get_session_manager()
    s = mgr.create_conversation(user_id="u1")
    for i in range(3):
        await run_turn(s.conversation_id, "u1", f"测试消息{i}")

    msgs = mgr.repository.get_recent_messages(s.conversation_id)
    seqs = [m["sequence"] for m in msgs]
    assert seqs == sorted(seqs), "sequence 应单调递增"
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant"] * 3, f"角色序列异常: {roles}"


# ---------------- 数据库隔离 ----------------

def test_database_isolation_config():
    """测试运行在独立临时库上，不指向仓库共享数据库。"""
    from config.database import db_config

    assert "data/ai_front_desk.db" not in db_config.db_path, \
        f"测试数据库不应指向共享库: {db_config.db_path}"
