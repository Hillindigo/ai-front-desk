"""Phase B 测试基线与 FakeLLM 配置。

所有测试默认运行在 MODEL_PROVIDER=fake / EMBEDDING_PROVIDER=fake 下，
保证零真实 LLM/Embedding API 调用、可离线、可重复。

Phase B（决策一）：数据库统一使用 session 级临时 SQLite，不碰仓库 data/ 共享库。
"""

import os

import pytest

os.environ.setdefault("MODEL_PROVIDER", "fake")
os.environ.setdefault("EMBEDDING_PROVIDER", "fake")


@pytest.fixture(scope="session", autouse=True)
def _test_database(tmp_path_factory):
    """为整个测试会话注入独立临时 SQLite 数据库。

    覆盖 config.database.db_config.db_path 与 DATABASE_URL 环境变量，
    测试结束后恢复原值。业务代码与测试 fixture 都从 db_config 取路径，
    因此无需修改任何业务代码即可完成隔离。
    """
    from config.database import db_config

    original_path = db_config.db_path
    original_env = os.environ.get("DATABASE_URL")
    db_dir = tmp_path_factory.mktemp("test_db")
    test_url = f"sqlite:///{db_dir.as_posix()}/test.db"

    db_config.db_path = test_url
    os.environ["DATABASE_URL"] = test_url

    yield test_url

    db_config.db_path = original_path
    if original_env is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = original_env


@pytest.fixture(autouse=True)
def _fake_llm_env(monkeypatch):
    """强制 fake 提供商，并清理 FakeChatModel 的调用记录。"""
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    from config.model_provider import FakeChatModel

    FakeChatModel.calls.clear()
    yield
    FakeChatModel.calls.clear()


@pytest.fixture(autouse=True)
def _clean_appointment_tables():
    """每个测试前清空预约/事件表（Phase C：session 级临时库避免数据残留干扰）。"""
    from sqlalchemy import text

    from db.db_router import DatabaseRouter

    router = DatabaseRouter()
    with router.session_manager.engine.connect() as conn:
        conn.execute(text("DELETE FROM appointment_events"))
        conn.execute(text("DELETE FROM appointments"))
        conn.commit()
    router.close()
    yield