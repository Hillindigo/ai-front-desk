"""Phase F F2 测试：知识管理服务生命周期与容器单实例共享。

覆盖：
- 容器持有单一 KnowledgeService，管理与证据读取共享同一实例。
- create/list/过滤/分页/detail/version。
- update：草稿直改；已发布编辑降为草稿。
- archive：published->archived 并从正式索引移除；幂等。
- publish_prepare 校验、restore 恢复。
- 非法输入（空内容）抛 InvalidKnowledgeInputError。
"""

import pytest

from application.container import Container
from services.knowledge_contracts import (
    InvalidKnowledgeInputError,
    KnowledgeNotFoundError,
)


@pytest.fixture
async def mgmt(tmp_path):
    c = Container(db_path=f"sqlite:///{(tmp_path / 'f2.db').as_posix()}")
    await c.initialize()  # 空库播种 10 条默认 published + 索引快照
    yield c
    c.close()


class TestContainerSingleInstance:
    def test_管理与证据读取共享同一知识服务(self, mgmt):
        assert mgmt.knowledge_management._kb is mgmt.knowledge_service
        # evidence reader 使用同一实例（context_builder._evidence）
        ev_reader = mgmt.context_builder._evidence
        assert ev_reader._knowledge is mgmt.knowledge_service

    def test_索引已初始化(self, mgmt):
        assert mgmt.knowledge_service.initialized is True


class TestCreateList:
    async def test_创建草稿带字段(self, mgmt):
        doc = mgmt.knowledge_management.create_document(
            title="新条目", content="门店周三休息", category="营业时间",
            keywords=["周三"], created_by="admin",
        )
        assert doc["status"] == "draft"
        assert doc["title"] == "新条目"
        assert doc["document_version"] == 1
        assert "embedding" not in doc

    async def test_空内容抛错(self, mgmt):
        with pytest.raises(InvalidKnowledgeInputError):
            mgmt.knowledge_management.create_document(
                title="x", content="   ", category="c", created_by="admin",
            )

    async def test_列表过滤与分页(self, mgmt):
        m = mgmt.knowledge_management
        m.create_document(title="草稿一", content="内容甲", category="服务项目", created_by="a")
        m.create_document(title="草稿二", content="内容乙", category="会员服务", created_by="a")

        published = m.list_documents(status="published", page=1, page_size=100)
        drafts = m.list_documents(status="draft", page=1, page_size=100)
        assert published["total"] == 10
        assert drafts["total"] == 2

        kw = m.list_documents(keyword="内容甲", page=1, page_size=100)
        assert kw["total"] == 1 and kw["items"][0]["title"] == "草稿一"

        p1 = m.list_documents(page=1, page_size=3)
        assert len(p1["items"]) == 3 and p1["total"] == 12

    async def test_详情与版本查询(self, mgmt):
        m = mgmt.knowledge_management
        doc = m.create_document(title="细则", content="正文细则", category="政策", created_by="a")
        d = m.get_document(doc["document_id"])
        assert d["title"] == "细则"
        v = m.get_version(doc["document_id"])
        assert v["document_id"] == doc["document_id"] and v["document_version"] >= 1

    async def test_详情不存在抛错(self, mgmt):
        with pytest.raises(KnowledgeNotFoundError):
            mgmt.knowledge_management.get_document(999999)


class TestUpdateArchive:
    async def test_更新草稿直改(self, mgmt):
        m = mgmt.knowledge_management
        doc = m.create_document(title="草稿", content="v1", category="c", created_by="a")
        nd = m.update_document(doc["document_id"], content="v2", title="改后", updated_by="a")
        assert nd["status"] == "draft"
        assert nd["content"] == "v2" and nd["title"] == "改后"

    async def test_编辑已发布文档降为草稿(self, mgmt):
        m = mgmt.knowledge_management
        pub = next(d for d in m.list_documents(status="published", page=1, page_size=100)["items"])
        assert pub["status"] == "published"
        nd = m.update_document(pub["document_id"], content="被编辑的新内容", updated_by="a")
        assert nd["status"] == "draft"

    async def test_归档从正式索引移除(self, mgmt):
        m = mgmt.knowledge_management
        kb = mgmt.knowledge_service
        # 归档第一个已发布文档
        pub = next(d for d in m.list_documents(status="published", page=1, page_size=100)["items"])
        doc_id = pub["document_id"]
        title = pub["title"]
        archived = await m.archive_document(doc_id, updated_by="a")
        assert archived["status"] == "archived"
        # 幂等归档
        again = await m.archive_document(doc_id, updated_by="a")
        assert again["status"] == "archived"
        # 文档已移出正式索引：不再被检索到
        rows = await kb.search(title, top_k=10)
        ids = {r["id"] for r in rows}
        assert doc_id not in ids

    async def test_恢复归档为草稿(self, mgmt):
        m = mgmt.knowledge_management
        pub = next(d for d in m.list_documents(status="published", page=1, page_size=100)["items"])
        doc_id = pub["document_id"]
        await m.archive_document(doc_id)
        restored = m.restore_document(doc_id)
        assert restored["status"] == "draft"

    async def test_发布准备校验(self, mgmt):
        m = mgmt.knowledge_management
        draft = m.create_document(title="待发布", content="内容", category="c", created_by="a")
        prep = m.publish_prepare(draft["document_id"])
        assert prep["can_publish"] is True and prep["status"] == "draft"

        pub = next(d for d in m.list_documents(status="published", page=1, page_size=100)["items"])
        prep_pub = m.publish_prepare(pub["document_id"])
        assert prep_pub["can_publish"] is False  # 已发布不可再次准备发布
