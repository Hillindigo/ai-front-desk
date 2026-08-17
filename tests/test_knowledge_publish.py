"""Phase F F3 测试：发布流水线、版本化与失败回退。

覆盖：
- 草稿发布 -> published，版本递增，正式检索返回，knowledge_version/source_version 更新。
- Embedding 失败：保持旧快照，文档标记 failed，旧已发布内容继续可查（失败回退）。
- 非草稿发布 / 空内容发布校验。
- 单进程锁下并发发布串行（不产生两个当前版本）。
- 刷新状态可观察，多进程明确未完成。
"""

import pytest

from application.container import Container
from services.knowledge_contracts import (
    IndexBuildFailedError,
    InvalidKnowledgeInputError,
    InvalidStateTransitionError,
)


@pytest.fixture
async def pub(tmp_path):
    c = Container(db_path=f"sqlite:///{(tmp_path / 'f3.db').as_posix()}")
    await c.initialize()  # 10 条默认 published + 索引 v1
    yield c
    c.close()


class TestPublishFlow:
    async def test_草稿发布后可检索且版本递增(self, pub):
        m = pub.knowledge_management
        p = pub.knowledge_publish
        kb = pub.knowledge_service
        doc = m.create_document(
            title="新门店价目", content="深度放松项目原价90元", category="服务项目",
            keywords=["深度放松", "90元"], created_by="admin",
        )
        doc_id = doc["document_id"]
        assert doc["status"] == "draft"

        # 发布前草稿不可检索
        before = await kb.search("深度放松", top_k=10)
        assert doc_id not in {r["id"] for r in before}

        result = await p.publish_document(doc_id)
        assert result["status"] == "published"
        assert result["document_version"] == 2
        assert result["knowledge_version"] >= 1
        assert result["source_version"].startswith("index-")
        assert result["document_id"] == doc_id

        # 发布后正式检索返回
        after = await kb.search("深度放松", top_k=10)
        ids = {r["id"] for r in after}
        assert doc_id in ids

        # knowledge_version 持久化到 meta
        assert pub.knowledge_publish.current_knowledge_version() == result["knowledge_version"]

    async def test_重复发布不产生两个当前版本(self, pub):
        m = pub.knowledge_management
        p = pub.knowledge_publish
        doc = m.create_document(title="A", content="内容A", category="c",
                                keywords=["kwA"], created_by="a")
        r1 = await p.publish_document(doc["document_id"])
        assert r1["document_version"] == 2
        # 已发布态再次发布被拒（不产生第二个当前版本）
        with pytest.raises(InvalidStateTransitionError):
            await p.publish_document(doc["document_id"])
        matches = m.list_documents(status="published", keyword="内容A")
        assert matches["total"] == 1

    async def test_非草稿发布被拒(self, pub):
        p = pub.knowledge_publish
        m = pub.knowledge_management
        # 归档一个已发布文档后再尝试发布 -> 非法迁移
        archived_doc = None
        for d in m.list_documents(status="published", page=1, page_size=100)["items"]:
            archived_doc = await m.archive_document(d["document_id"])
            break
        with pytest.raises(InvalidStateTransitionError):
            await p.publish_document(archived_doc["document_id"])

    async def test_空内容发布被拒(self, pub):
        # 管理入口会拦空内容，这里绕过管理层直接以空正文建草稿验证发布的防御性校验
        doc_id = pub.knowledge_service.db.add_document(
            content="   ", category="c", status="draft", title="空",
        )
        with pytest.raises(InvalidKnowledgeInputError):
            await pub.knowledge_publish.publish_document(doc_id)


class TestPublishFailureFallback:
    async def test_embedding失败保留旧快照并标记失败(self, pub, monkeypatch):
        m = pub.knowledge_management
        p = pub.knowledge_publish
        kb = pub.knowledge_service
        # 记录当前已发布可查内容（旧版本）
        old_rows = await kb.search("营业时间", top_k=10)
        assert old_rows
        old_ids = {r["id"] for r in old_rows}

        # 让 embed_input 抛错
        def boom(*a, **k):
            raise RuntimeError("fake embedding down")

        monkeypatch.setattr("services.text_embedding.embed_input", boom)

        doc = m.create_document(title="X", content="新内容X", category="c",
                                keywords=["kwX"], created_by="a")
        with pytest.raises(IndexBuildFailedError):
            await p.publish_document(doc["document_id"])

        # 文档标记 failed
        failed = m.get_document(doc["document_id"])
        assert failed["status"] == "failed"
        # 旧快照保留：原已发布内容仍可查（失败回退）
        still = await kb.search("营业时间", top_k=10)
        assert old_ids <= {r["id"] for r in still}
        # 刷新状态记录失败
        status = p.refresh_status()
        assert status["status"] == "failed"
        assert status["multi_process"] is False


class TestConcurrency:
    async def test_并发发布串行且唯一(self, pub):
        m = pub.knowledge_management
        p = pub.knowledge_publish
        d1 = m.create_document(title="A", content="内容A", category="c", keywords=["A"], created_by="a")
        d2 = m.create_document(title="B", content="内容B", category="c", keywords=["B"], created_by="a")

        import asyncio
        await asyncio.gather(
            p.publish_document(d1["document_id"]),
            p.publish_document(d2["document_id"]),
        )
        a = m.get_document(d1["document_id"])
        b = m.get_document(d2["document_id"])
        assert a["status"] == "published" and b["status"] == "published"
        # 两次发布语料版本串行递增（全局知识版本每次发布 +1）
        assert a["knowledge_version"] != b["knowledge_version"]
        assert a["document_version"] == 2 and b["document_version"] == 2
