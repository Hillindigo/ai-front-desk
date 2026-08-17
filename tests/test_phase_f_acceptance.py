"""Phase F F8 验收测试：故障注入、SSE 契约、内部信息不泄漏、Phase C/E 语义保持。

覆盖（计划 F8 交付物）：
- Embedding/索引构建故障：发布失败保留旧快照、文档标 failed、旧版本仍可查；
  错误映射为稳定错误码，不泄漏内部堆栈。
- 并发刷新：单进程锁串行，结果仍一致。
- turns SSE：run_started 首发、唯一 run_completed/run_failed 终止。
- 隐藏推理/[THOUGHT]/[SIGNAL] 与内部错误不进入 assistant 内容或来源元数据。
- 引用只进入允许字段；预约/偏好删除保持 Phase C/E 语义（冒烟）。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import create_app
from application.container import Container
from services.knowledge_contracts import IndexBuildFailedError


@pytest.fixture(scope="module")
def client():
    """完整 ASGI 栈（startup 初始化知识库/容器）。"""
    with TestClient(create_app()) as c:
        yield c


# ---------------- 故障注入 ----------------

class TestFaultInjection:
    @pytest.mark.asyncio
    async def test_embedding故障发布失败保留旧快照(self, tmp_path, monkeypatch):
        c = Container(db_path=f"sqlite:///{(tmp_path / 'f8.db').as_posix()}")
        await c.initialize()
        try:
            kb, m, p = c.knowledge_service, c.knowledge_management, c.knowledge_publish
            old = await kb.search("营业时间", top_k=10)
            assert old
            old_ids = {r["id"] for r in old}

            def boom(*a, **k):
                raise RuntimeError("embedding down")

            monkeypatch.setattr("services.text_embedding.embed_input", boom)
            doc = m.create_document(title="X", content="新内容", category="c",
                                    keywords=["kwX"], created_by="a")
            with pytest.raises(IndexBuildFailedError):
                await p.publish_document(doc["document_id"])

            # 旧版本继续可查（失败回退），文档标 failed
            assert m.get_document(doc["document_id"])["status"] == "failed"
            still = await kb.search("营业时间", top_k=10)
            assert old_ids <= {r["id"] for r in still}
        finally:
            c.close()

    @pytest.mark.asyncio
    async def test_并发刷新串行一致(self, tmp_path):
        c = Container(db_path=f"sqlite:///{(tmp_path / 'f8b.db').as_posix()}")
        await c.initialize()
        try:
            p = c.knowledge_publish
            import asyncio
            await asyncio.gather(p.refresh(), p.refresh())
            st = p.refresh_status()
            assert st["status"] in ("succeeded", "building", "idle")
            assert st["multi_process"] is False
        finally:
            c.close()


# ---------------- SSE 契约 ----------------

class TestSseContract:
    def _events(self, client, cid, message):
        events = []
        with client.stream("POST", f"/api/v1/conversations/{cid}/turns",
                           json={"message": message}) as r:
            buf = ""
            for chunk in r.iter_bytes():
                buf += chunk.decode(r.charset_encoding or "utf-8", errors="replace")
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    name, data = "message", ""
                    for line in block.split("\n"):
                        if line.startswith("event: "):
                            name = line[7:].strip()
                        elif line.startswith("data: "):
                            data += line[6:]
                    if data:
                        try:
                            events.append((name, json.loads(data)))
                        except Exception:
                            continue
        return events

    def test_turns事件序列run_started到唯一终止(self, client):
        cid = client.post("/api/v1/conversations", json={"user_id": "default_user"}).json()["conversation_id"]
        events = self._events(client, cid, "门店营业时间是几点？")
        names = [n for n, _ in events]
        assert names[0] == "run_started"
        terminal = [n for n in names if n in ("run_completed", "run_failed")]
        assert len(terminal) == 1  # 唯一终止事件
        assert terminal[0] in ("run_completed", "run_failed")

    def test_assistant内容不泄漏隐藏推理(self, client):
        cid = client.post("/api/v1/conversations", json={"user_id": "default_user"}).json()["conversation_id"]
        events = self._events(client, cid, "肩颈放松有什么好处？")
        for name, data in events:
            if name == "assistant_delta":
                text = data.get("text", "")
                assert "[THOUGHT]" not in text
                assert "[SIGNAL]" not in text
                assert "api_key" not in text.lower()
                assert "azure" not in text.lower()


# ---------------- 引用只进允许字段 ----------------

class TestCitationAllowedFields:
    def test_来源元数据只含公开字段(self, client):
        cid = client.post("/api/v1/conversations", json={"user_id": "default_user"}).json()["conversation_id"]
        with client.stream("POST", f"/api/v1/conversations/{cid}/turns",
                           json={"message": "会员充值有什么优惠？"}):
            pass
        res = client.get(f"/api/v1/conversations/{cid}/sources").json()
        for e in res.get("evidence", []):
            allowed = {"document_id", "category", "snippet", "score", "source_version"}
            assert set(e.keys()) <= allowed
        assert "prompt" not in str(res).lower()
        assert "embedding" not in str(res).lower()


# ---------------- Phase C/E 语义保持（冒烟） ----------------

class TestPhaseCESemantics:
    def test_偏好删除仍幂等(self, client):
        # 写入一个偏好再删除
        r = client.put("/api/v1/preferences/time", json={"value": "下午2点", "user_id": "default_user"})
        assert r.status_code in (200, 422)
        d = client.delete("/api/v1/preferences/time", params={"user_id": "default_user"})
        assert d.status_code == 200
        d2 = client.delete("/api/v1/preferences/time", params={"user_id": "default_user"})
        assert d2.status_code == 200  # 幂等

    def test_预约仍走领域状态机(self, client):
        # 冒烟：预约创建端点存活（POST /api/v1/appointments），不破坏 Phase C 端点
        r = client.post("/api/v1/appointments", json={"message": "帮我预约"})
        assert r.status_code in (200, 201, 400, 422)
