"""Phase G G2：核心业务资源的服务端门店隔离。"""

from datetime import datetime, timedelta

import pytest

from db.db_router import DatabaseRouter
from db.models import Store
from db.repositories.preference_repository import PreferenceRepository


@pytest.fixture
def scoped_router(tmp_path):
    router = DatabaseRouter(f"sqlite:///{(tmp_path / 'stores.db').as_posix()}")
    with router.session_manager.session_scope() as session:
        a = Store(name="门店A")
        b = Store(name="门店B")
        session.add_all([a, b])
        session.flush()
        stores = {"a": a.id, "b": b.id}
    yield router, stores
    router.close()


def test_迁移创建默认门店并补齐核心表store_id(tmp_path):
    router = DatabaseRouter(f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    try:
        with router.session_manager.engine.connect() as conn:
            store_id = conn.execute(
                __import__("sqlalchemy").text("SELECT id FROM stores ORDER BY id LIMIT 1")
            ).scalar_one()
            for table in (
                "conversations", "appointments", "knowledge_documents",
                "knowledge_meta", "technicians", "user_behaviors",
                "preferences", "preference_tombstones",
            ):
                columns = {
                    row[1] for row in conn.execute(
                        __import__("sqlalchemy").text(f"PRAGMA table_info({table})")
                    ).fetchall()
                }
                assert "store_id" in columns
            assert store_id > 0
    finally:
        router.close()


def test_会话知识服务人员错误门店不可读(scoped_router):
    router, stores = scoped_router
    conversations = router.conversations
    knowledge = router.knowledge
    technicians = router.technicians

    conv = conversations.create_conversation("customer-a", store_id=stores["a"])
    assert conversations.get_conversation(conv["id"], store_id=stores["a"])
    assert conversations.get_conversation(conv["id"], store_id=stores["b"]) is None

    doc_id = knowledge.add_document(
        "A店规则", "政策", store_id=stores["a"], status="published"
    )
    assert knowledge.get_document(doc_id, store_id=stores["a"])
    assert knowledge.get_document(doc_id, store_id=stores["b"]) is None

    tech_id = technicians.add_technician("A店技师", store_id=stores["a"])
    assert technicians.get_technician_by_id(tech_id, store_id=stores["a"])
    assert technicians.get_technician_by_id(tech_id, store_id=stores["b"]) is None


def test_预约和偏好错误门店不可读(scoped_router):
    router, stores = scoped_router
    appointments = router.appointments
    preferences = PreferenceRepository(router.session_manager)

    appt = appointments.create_draft(
        user_id="customer-a",
        conversation_id=None,
        service_type="肩颈放松",
        store_id=stores["a"],
    )
    assert appointments.get(appt["id"], store_id=stores["a"])
    assert appointments.get(appt["id"], store_id=stores["b"]) is None

    pref = preferences.set_preference(
        "customer-a", "service", "肩颈放松", "explicit_memorize", store_id=stores["a"]
    )
    assert preferences.get_active_preference(
        "customer-a", "service", store_id=stores["a"]
    )
    assert preferences.get_active_preference(
        "customer-a", "service", store_id=stores["b"]
    ) is None
