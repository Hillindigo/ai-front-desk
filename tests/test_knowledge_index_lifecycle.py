"""Phase F F3 测试：索引生命周期 —— 版本快照与重启恢复。

覆盖：
- 每次发布 source_version 递增，旧结果不再作为当前证据。
- 归档 + 刷新后该文档移出正式检索。
- 重启（新 KnowledgeService 同一库）后索引按 published 重建，knowledge_version 从
  meta 恢复。
- 每个 source_version 对应一个完整的 (index, doc_ids, version) 快照。
"""

import pytest

from application.container import Container
from services.knowledge_service import KnowledgeService


def _db_url(tmp_path, name):
    return f"sqlite:///{(tmp_path / name).as_posix()}"


class TestSourceVersionLifecycle:
    async def test_发布递增source_version且旧版本不作当前证据(self, tmp_path):
        url = _db_url(tmp_path, "lf1.db")
        c = Container(db_path=url)
        await c.initialize()
        m, p, kb = c.knowledge_management, c.knowledge_publish, c.knowledge_service
        try:
            v0 = p.get_source_version()
            doc0 = next(d for d in m.list_documents(status="published", page=1, page_size=1)["items"])
            # 编辑已发布文档 -> 草稿
            edited = m.update_document(doc0["document_id"], content="新的营业时间说明", updated_by="a")
            assert edited["status"] == "draft"

            r = await p.publish_document(doc0["document_id"])
            v1 = p.get_source_version()
            assert v1 != v0  # 索引快照已替换
            assert r["source_version"] == v1

            # 查询读的是新快照（source_version 一致）
            rows = await kb.search("营业时间", top_k=10)
            assert all(x["source_version"] == f"index-{kb._index_version}" for x in rows)
        finally:
            c.close()

    async def test_快照包含完整文档与版本(self, tmp_path):
        url = _db_url(tmp_path, "lf2.db")
        c = Container(db_path=url)
        await c.initialize()
        kb = c.knowledge_service
        try:
            with kb._lock:
                snapshot = kb._snapshot
            assert snapshot is not None
            index, doc_ids, version = snapshot
            assert isinstance(doc_ids, tuple)
            assert version == kb._index_version
            assert len(doc_ids) > 0
        finally:
            c.close()

    async def test_归档刷新后文档移出检索(self, tmp_path):
        url = _db_url(tmp_path, "lf3.db")
        c = Container(db_path=url)
        await c.initialize()
        m, p, kb = c.knowledge_management, c.knowledge_publish, c.knowledge_service
        try:
            doc = next(d for d in m.list_documents(status="published", page=1, page_size=1)["items"])
            doc_id, title = doc["document_id"], doc["title"]

            assert doc_id in {r["id"] for r in await kb.search(title, top_k=10)}
            await m.archive_document(doc_id)  # archive 内部重建索引
            assert doc_id not in {r["id"] for r in await kb.search(title, top_k=10)}
            # 刷新状态可查
            st = p.refresh_status()
            assert "status" in st
        finally:
            c.close()


class TestRestartRecovery:
    async def test_重启后按published重建并恢复语料版本(self, tmp_path):
        url = _db_url(tmp_path, "lf4.db")
        c1 = Container(db_path=url)
        await c1.initialize()
        m1, p1 = c1.knowledge_management, c1.knowledge_publish
        # 发布一个新文档，记住 knowledge_version
        doc = m1.create_document(title="持久条目", content="持久内容", category="c",
                                 keywords=["持久"], created_by="a")
        await p1.publish_document(doc["document_id"])
        kv = p1.current_knowledge_version()
        c1.close()

        # 模拟重启：同一库新建容器
        c2 = Container(db_path=url)
        await c2.initialize()
        try:
            m2, p2, kb2 = c2.knowledge_management, c2.knowledge_publish, c2.knowledge_service
            assert p2.current_knowledge_version() == kv  # 语料版本恢复
            # 持久条目仍在 published 且可检索
            listed = m2.list_documents(status="published", keyword="持久")
            assert listed["total"] == 1
            rows = await kb2.search("持久", top_k=10)
            assert rows
        finally:
            c2.close()
