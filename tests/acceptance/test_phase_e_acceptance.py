"""Phase E E8 测试：并发摘要、故障注入汇总与 HTTP 事件序列验收。

覆盖 E8 验收清单中由本文件补充的场景：
- 同一会话并发摘要不产生交错版本（复用会话锁串行语义）。
- turns SSE 事件序列完整（run_started -> ... -> 唯一终止事件）。
- Phase B/C/D 全量回归由全量套件保证；10 个 skip 仍为 user_behavior 组件。
"""

import asyncio
import json

import pytest

from fastapi.testclient import TestClient

from application.context_builder import AppointmentReader, MessageReader
from db.db_router import DatabaseRouter
from db.repositories.summary_repository import SummaryRepository
from services.summary_service import SummaryService


class _MsgReader(MessageReader):
    def __init__(self, message_count=40):
        self._msgs = [
            {"role": "user", "content": f"并发消息-{i}", "sequence": i}
            for i in range(1, message_count + 1)
        ]

    def recent_messages(self, conversation_id, after_sequence=None, limit=None):
        msgs = [m for m in self._msgs if m["sequence"] > (after_sequence or 0)]
        if limit is not None:
            msgs = msgs[-limit:]
        return msgs


class _ApptReader(AppointmentReader):
    def active_facts(self, conversation_id):
        return {}


@pytest.fixture
def concurrent_env():
    router = DatabaseRouter()
    conv = router.conversations.create_conversation(user_id="u1")
    conv_id = conv["id"]
    for i in range(1, 41):
        router.conversations.add_message(conv_id, "user", f"并发消息-{i}")
    repo = SummaryRepository(router.session_manager)
    yield {"repo": repo, "conv_id": conv_id}
    router.close()


class TestConcurrentSummary:
    @pytest.mark.asyncio
    async def test_并发摘要不交错版本(self, concurrent_env):
        """生产语义：Orchestrator 在会话锁内调用摘要；测试用同一 Lock 模拟串行。"""
        repo = concurrent_env["repo"]
        reader = _MsgReader(40)
        svc1 = SummaryService(repository=repo, message_reader=reader, appointment_reader=_ApptReader())
        svc2 = SummaryService(repository=repo, message_reader=reader, appointment_reader=_ApptReader())

        lock = asyncio.Lock()  # 模拟会话锁（session.lock）

        async def guarded(svc):
            async with lock:
                return await svc.summarize_if_needed(concurrent_env["conv_id"])

        await asyncio.gather(guarded(svc1), guarded(svc2))
        snaps = repo.get_active_history(concurrent_env["conv_id"])
        versions = [s["version"] for s in snaps]
        assert versions == sorted(set(versions))  # 版本唯一且有序
        for i in range(1, len(snaps)):
            assert snaps[i]["from_sequence"] == snaps[i - 1]["to_sequence"] + 1  # 连续无交错
        assert snaps[0]["from_sequence"] == 1
        assert snaps[-1]["to_sequence"] == 40

    @pytest.mark.asyncio
    async def test_摘要尝试不丢消息(self, concurrent_env):
        repo = concurrent_env["repo"]
        svc = SummaryService(repository=repo, message_reader=_MsgReader(40), appointment_reader=_ApptReader())
        result = await svc.summarize_if_needed(concurrent_env["conv_id"])
        assert result == "succeeded"
        snaps = repo.get_active_history(concurrent_env["conv_id"])
        assert snaps[0]["from_sequence"] == 1
        assert snaps[-1]["to_sequence"] == 40


class TestHTTPTurnEventSequence:
    """turns 事件序列验收（SSE v1 协议完整性）。"""

    @pytest.fixture
    def client(self):
        from api.chat_handler import reset_session_manager
        from app import create_app

        reset_session_manager()
        with TestClient(create_app()) as c:
            yield c
        reset_session_manager()

    def _events(self, client, conv_id, message):
        r = client.post(f"/api/v1/conversations/{conv_id}/turns",
                        json={"message": message, "user_id": "default_user"})
        assert r.status_code == 200, r.text
        events = []
        for raw in r.text.split("\n"):
            if raw.startswith("data: "):
                events.append(json.loads(raw[len("data: "):]))
        return events

    def test_普通轮次事件序列完整(self, client):
        conv_id = client.post("/api/v1/conversations", json={"user_id": "default_user"}).json()["conversation_id"]
        events = self._events(client, conv_id, "你好")
        types = [e["type"] for e in events]
        assert types[0] == "run_started"
        terminals = [t for t in types if t in ("run_completed", "run_failed")]
        assert len(terminals) == 1 and terminals[0] == "run_completed"
        seqs = [e["sequence"] for e in events]
        assert seqs == sorted(seqs)  # sequence 单调
        assert all(e["protocol_version"] == "v1" for e in events)

    def test_偏好轮次事件含工具与反馈(self, client):
        conv_id = client.post("/api/v1/conversations", json={"user_id": "default_user"}).json()["conversation_id"]
        events = self._events(client, conv_id, "请记住我喜欢李师傅")
        types = [e["type"] for e in events]
        assert "tool_started" in types
        assert "tool_result" in types
        assert types[-1] == "run_completed"
        for e in events:
            text = e.get("data", {}).get("text", "")
            assert "[THOUGHT]" not in text and "[SIGNAL]" not in text  # 隐藏推理不外泄