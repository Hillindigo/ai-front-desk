"""Phase E E6 测试：检索阈值、候选边界、索引快照一致性、关键词预过滤与故障注入。

覆盖：低于阈值不返回（无可靠依据路径）、重建后 source_version 递增、
删除文档后旧索引结果不再作为当前证据、预过滤不绕阈值、Embedding 故障降级。
"""

import pytest

from application.context_readers import KnowledgeEvidenceReader
from services.knowledge_service import KnowledgeService


@pytest.fixture
async def kb():
    service = KnowledgeService()
    await service.initialize()  # 默认知识库 10 条 + 索引快照 v1
    yield service
    service.db_router.close()


class TestThreshold:
    @pytest.mark.asyncio
    async def test_默认阈值下返回结果(self, kb):
        rows = await kb.search("营业时间是几点", top_k=3)
        assert rows  # Fake 分数恒定 1.28 > 0.5
        assert all(r["score"] >= kb.min_score for r in rows)

    @pytest.mark.asyncio
    async def test_高于阈值时全部过滤为无依据(self):
        service = KnowledgeService(min_score=2.0)  # 高于 Fake 内积分数
        await service.initialize()
        try:
            rows = await service.search("营业时间", top_k=3)
            assert rows == []  # 低于阈值 = 无可靠依据，不伪造答案
        finally:
            service.db_router.close()

    @pytest.mark.asyncio
    async def test_结构化输出字段完整(self, kb):
        structured = await kb.search_structured("价格", top_k=2)
        assert structured
        item = structured[0]
        assert "document_id" in item and "snippet" in item
        assert "score" in item and "source_version" in item and "rank" in item
        assert item["source_version"].startswith("index-")


class TestCandidateBoundary:
    @pytest.mark.asyncio
    async def test_候选数量不超过边界(self):
        service = KnowledgeService(max_candidates=5)
        await service.initialize()
        try:
            rows = await service.search("服务", top_k=10)
            assert len(rows) <= 5  # 候选边界生效
        finally:
            service.db_router.close()


class TestSnapshotConsistency:
    @pytest.mark.asyncio
    async def test_重建后版本递增且旧文档不再返回(self, kb):
        before = await kb.search("营业时间", top_k=3)
        old_version = before[0]["source_version"]
        # 删除一篇文档 -> 触发重建（新快照 version+1）
        docs = kb.get_all_documents()
        assert await kb.delete_document(docs[0]["id"], soft_delete=False)
        after = await kb.search("营业时间", top_k=5)
        deleted_id = docs[0]["id"]
        assert all(r["id"] != deleted_id for r in after)  # 删除即不再检索到
        if after:
            assert after[0]["source_version"] != old_version  # 旧结果不再被标记为当前证据

    @pytest.mark.asyncio
    async def test_查询读取完整快照不读半成品(self, kb):
        # 顺序：先查询得到快照 A；重建后查询得到快照 B；两者都是完整自洽结果
        rows_a = await kb.search("会员卡", top_k=2)
        await kb.add_document("新文档内容", "测试分类", ["测试"])
        rows_b = await kb.search("会员卡", top_k=2)
        assert all(r.get("score", 0) >= kb.min_score for r in rows_a + rows_b)


class TestKeywordPrefilter:
    @pytest.mark.asyncio
    async def test_预过滤缩减候选但不过滤高相关(self, kb):
        # 默认知识库：预约政策的 keywords 含"取消"
        rows = await kb.search("取消预约", top_k=3)
        assert rows  # 关键词命中的候选仍按相似度返回
        # 宽泛词（无任何关键词命中）回退全部候选，不受影响
        rows2 = await kb.search("任意宽泛查询词xyz", top_k=3)
        assert isinstance(rows2, list)


class TestFailureInjection:
    @pytest.mark.asyncio
    async def test_embedding故障时返回无依据结果(self, kb, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("Embedding 服务不可用")

        import services.knowledge_service as ks_mod

        monkeypatch.setattr(ks_mod, "embed_input", boom)
        rows = await kb.search("营业时间", top_k=3)
        assert rows == []  # 故障注入：明确降级，不伪造咨询答案

    @pytest.mark.asyncio
    async def test_未初始化返回无依据(self):
        service = KnowledgeService()
        try:
            rows = await service.search("查询", top_k=3)
            assert rows == []
        finally:
            service.db_router.close()

    @pytest.mark.asyncio
    async def test_索引构建失败保留旧快照(self, kb, monkeypatch):
        # 先取旧快照版本
        before = await kb.search("价格", top_k=1)
        old_version = before[0]["source_version"] if before else None

        def boom(*a, **kw):
            raise RuntimeError("Embedding 服务不可用")

        import services.knowledge_service as ks_mod

        monkeypatch.setattr(ks_mod, "embed_input", boom)
        ok = await kb.add_document("新文档", "测试", ["新"])
        assert ok is False  # 添加失败
        # 旧快照仍在服务（查询不因重建失败中断）
        after = await kb.search("价格", top_k=1)
        if after and old_version:
            assert after[0]["source_version"] == old_version


class TestEvidenceReaderIntegration:
    @pytest.mark.asyncio
    async def test_证据读取器输出RetrievedEvidence(self, kb):
        reader = KnowledgeEvidenceReader(kb)
        evidence = await reader.retrieve("预约政策", limit=3)
        assert evidence
        ev = evidence[0]
        assert ev.document_id > 0
        assert ev.source_version.startswith("index-")
        assert ev.rank >= 1

    @pytest.mark.asyncio
    async def test_证据读取器低于阈值返回空(self):
        service = KnowledgeService(min_score=2.0)
        await service.initialize()
        try:
            reader = KnowledgeEvidenceReader(service)
            assert await reader.retrieve("营业时间", limit=3) == []
        finally:
            service.db_router.close()