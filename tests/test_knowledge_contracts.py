"""Phase F F1 契约测试：知识状态机、错误码、数据库迁移与"草稿不出现在正式检索"。

覆盖：
- 状态机合法/非法迁移；错误码契约。
- KnowledgeDocumentContract 不泄漏 embedding。
- 旧 schema 迁移：补齐字段、活跃->published、已软删->archived、不伪造 knowledge_version、幂等。
- 仓储 add_document 带新字段、get_published_documents 过滤草稿。
- 咨询服务：草稿不被正式检索，已发布可被检索。
"""

import pytest

from db.migrations import apply_knowledge_migrations
from services.knowledge_contracts import (
    InvalidStateTransitionError,
    KnowledgeDocumentContract,
    KnowledgeNotFoundError,
    KnowledgeStatus,
    PublishResult,
    can_transition,
    validate_transition,
)
from services.knowledge_service import KnowledgeService


# ---------------- 状态机与错误码 ----------------

class TestStateMachine:
    def test_合法迁移可通过(self):
        assert can_transition(KnowledgeStatus.DRAFT, KnowledgeStatus.PUBLISHED)
        assert can_transition(KnowledgeStatus.DRAFT, KnowledgeStatus.ARCHIVED)
        assert can_transition(KnowledgeStatus.PUBLISHED, KnowledgeStatus.ARCHIVED)
        assert can_transition(KnowledgeStatus.PUBLISHED, KnowledgeStatus.DRAFT)
        assert can_transition(KnowledgeStatus.ARCHIVED, KnowledgeStatus.PUBLISHED)
        assert can_transition(KnowledgeStatus.ARCHIVED, KnowledgeStatus.DRAFT)
        assert can_transition(KnowledgeStatus.FAILED, KnowledgeStatus.DRAFT)
        assert can_transition(KnowledgeStatus.FAILED, KnowledgeStatus.PUBLISHED)
        # 不抛异常即通过
        validate_transition(KnowledgeStatus.DRAFT, KnowledgeStatus.PUBLISHED)

    def test_非法迁移被拒绝(self):
        assert not can_transition(KnowledgeStatus.DRAFT, KnowledgeStatus.DRAFT)
        assert not can_transition(KnowledgeStatus.PUBLISHED, KnowledgeStatus.FAILED)
        assert not can_transition(KnowledgeStatus.PUBLISHED, KnowledgeStatus.PUBLISHED)
        with pytest.raises(InvalidStateTransitionError) as exc:
            validate_transition(KnowledgeStatus.DRAFT, KnowledgeStatus.FAILED)
        assert exc.value.code == "INVALID_STATE_TRANSITION"


class TestErrorCodes:
    def test_错误码与计划一致(self):
        from services.knowledge_contracts import (
            IndexBuildFailedError,
            InvalidKnowledgeInputError,
            KnowledgeNotReadyError,
            KnowledgeVersionConflictError,
        )
        assert InvalidKnowledgeInputError().code == "INVALID_INPUT"
        assert KnowledgeNotFoundError().code == "KNOWLEDGE_NOT_FOUND"
        assert InvalidStateTransitionError().code == "INVALID_STATE_TRANSITION"
        assert IndexBuildFailedError().code == "INDEX_BUILD_FAILED"
        assert KnowledgeVersionConflictError().code == "KNOWLEDGE_VERSION_CONFLICT"
        assert KnowledgeNotReadyError().code == "KNOWLEDGE_NOT_READY"


class TestDocumentContract:
    def test_to_dict不泄漏embedding(self):
        doc = KnowledgeDocumentContract(
            document_id=1, title="t", content="c", category="cat",
            keywords=["k"], status="published", document_version=2,
            knowledge_version=1, source_type="manual", source_label="运营",
            created_by="admin", updated_by=None, created_at=None,
            updated_at=None, published_at="2024-01-01T00:00:00", archived_at=None,
        )
        data = doc.to_dict()
        assert "embedding" not in data
        assert data["document_id"] == 1 and data["status"] == "published"

    def test_发布结果含版本字段(self):
        result = PublishResult(
            document_id=3, document_version=2, knowledge_version=5,
            source_version="index-6", status="published",
        )
        data = result.to_dict()
        assert data["knowledge_version"] == 5 and data["source_version"] == "index-6"


# ---------------- 迁移 ----------------

def test_旧库迁移补齐字段并回填(tmp_path):
    from sqlalchemy import create_engine, text

    engine = create_engine(f"sqlite:///{(tmp_path / 'mig.db').as_posix()}")
    with engine.begin() as c:
        c.execute(text(
            "CREATE TABLE knowledge_documents ("
            "id INTEGER PRIMARY KEY, content TEXT, category TEXT, keywords TEXT, "
            "embedding TEXT, created_at DATETIME, updated_at DATETIME, is_active INTEGER DEFAULT 1)"
        ))
        c.execute(text(
            "INSERT INTO knowledge_documents(content, category, is_active, updated_at) "
            "VALUES ('old-active', 'c1', 1, '2024-01-01 10:00:00'), "
            "('old-deleted', 'c2', 0, '2024-01-02 11:00:00')"
        ))

    applied = apply_knowledge_migrations(engine)
    assert applied is True

    with engine.connect() as c:
        cols = {r[1] for r in c.execute(text("PRAGMA table_info(knowledge_documents)")).fetchall()}
        required = {"title", "status", "document_version", "knowledge_version",
                    "source_type", "source_label", "published_at", "archived_at"}
        assert required <= cols
        rows = c.execute(text(
            "SELECT id,status,document_version,source_type,knowledge_version,published_at "
            "FROM knowledge_documents ORDER BY id"
        )).fetchall()
        # 活跃旧行 -> published, legacy, v1, 不伪造 knowledge_version, published_at 有值
        assert rows[0][1] == "published"
        assert rows[0][2] == 1
        assert rows[0][3] == "legacy"
        assert rows[0][4] is None
        assert rows[0][5] is not None
        # 已软删旧行 -> archived
        assert rows[1][1] == "archived"
        assert rows[1][3] == "legacy"
    engine.dispose()


def test_迁移幂等(tmp_path):
    from sqlalchemy import create_engine, text

    engine = create_engine(f"sqlite:///{(tmp_path / 'mig2.db').as_posix()}")
    with engine.begin() as c:
        c.execute(text(
            "CREATE TABLE knowledge_documents ("
            "id INTEGER PRIMARY KEY, content TEXT, category TEXT, "
            "created_at DATETIME, updated_at DATETIME, is_active INTEGER DEFAULT 1)"
        ))
        c.execute(text(
            "INSERT INTO knowledge_documents(content, category, is_active, updated_at) VALUES ('x','c',1,'2024-01-01 00:00:00')"
        ))

    assert apply_knowledge_migrations(engine) is True
    assert apply_knowledge_migrations(engine) is False  # 已迁移，不再 ALTER
    with engine.connect() as c:
        cols1 = {r[1] for r in c.execute(text("PRAGMA table_info(knowledge_documents)")).fetchall()}
        assert "knowledge_version" in cols1
        # 二次迁移不应新增列或报错：列数与首次一致
        assert apply_knowledge_migrations(engine) is False
        cols2 = {r[1] for r in c.execute(text("PRAGMA table_info(knowledge_documents)")).fetchall()}
        assert cols1 == cols2
    engine.dispose()


# ---------------- 仓储与正式检索语义 ----------------

def _repo():
    from db.db_router import DatabaseRouter
    router = DatabaseRouter()
    repo = router.knowledge
    return router, repo


def test_仓储新增带状态字段并过滤发布():
    router, repo = _repo()
    try:
        draft_id = repo.add_document(
            content="草稿内容A", category="测试", keywords=["草稿A"],
            title="草稿标题", status="draft", source_type="manual",
        )
        pub_id = repo.add_document(
            content="发布内容B", category="测试", keywords=["发布B"],
            title="发布标题", status="published", source_type="manual",
            created_by="admin",
        )
        draft = repo.get_document(draft_id)
        assert draft["status"] == "draft" and draft["title"] == "草稿标题"
        assert draft["document_version"] == 1
        pub = repo.get_document(pub_id)
        assert pub["status"] == "published" and pub["created_by"] == "admin"

        published_ids = {d["id"] for d in repo.get_published_documents()}
        assert pub_id in published_ids
        assert draft_id not in published_ids
    finally:
        router.close()


@pytest.mark.asyncio
async def test_草稿不进入正式检索(tmp_path):
    svc = KnowledgeService(f"sqlite:///{(tmp_path / 'k.db').as_posix()}")
    await svc.initialize()  # 空库存入 10 条默认 published
    try:
        draft_id = svc.db.add_document(
            content="独家测试DRAFT机密条款", category="测试",
            keywords=["DRAFT机密"], status="draft", title="草稿",
        )
        pub_id = svc.db.get_published_documents()
        pub = svc.db.add_document(
            content="独家测试PUBLIC公开条款", category="测试",
            keywords=["PUBLIC公开"], status="published", title="发布",
        )
        await svc._build_vector_index()

        # 草稿不在索引快照中
        with svc._lock:
            snapshot = svc._snapshot
        _, doc_ids, _ = snapshot
        assert draft_id not in doc_ids
        assert pub in doc_ids

        # 检索只返回已发布
        rows = await svc.search("PUBLIC公开", top_k=5)
        assert rows
        ids = {r["id"] for r in rows}
        assert pub in ids
        assert draft_id not in ids
    finally:
        svc.db_router.close()
