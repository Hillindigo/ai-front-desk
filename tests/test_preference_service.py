"""Phase E E4 测试：偏好覆盖语义、多用户隔离、墓碑删除联动、legacy 迁移、API 验收。

覆盖：单 active 值覆盖（PUT 替换非追加）、删除即失效（摘要/缓存/下一轮排除）、
来源消息屏蔽、不同用户/会话不串读、旧组件适配收敛、HTTP 接口与身份校验。
"""

from datetime import datetime, timezone

import pytest

from db.db_router import DatabaseRouter
from db.repositories.preference_repository import (
    CONTEXT_EXCLUDED_KEY,
    PreferenceRepository,
)
from db.repositories.summary_repository import SummaryRepository
from services.preference_service import PreferenceService


@pytest.fixture(autouse=True)
def _clean_phase_e_tables():
    """每个测试前清理 Phase E 表（避免偏好/摘要残留干扰覆盖语义断言）。"""
    from sqlalchemy import text

    router = DatabaseRouter()
    with router.session_manager.engine.connect() as conn:
        conn.execute(text("DELETE FROM preference_tombstones"))
        conn.execute(text("DELETE FROM preferences"))
        conn.execute(text("DELETE FROM conversation_summaries"))
        conn.execute(text("DELETE FROM user_preferences"))
        conn.commit()
    router.close()
    yield


@pytest.fixture
def pref_env():
    router = DatabaseRouter()
    repo = PreferenceRepository(router.session_manager)
    service = PreferenceService(repo)
    yield {"router": router, "repo": repo, "service": service}
    router.close()


class TestWriteAndOverwrite:
    def test_写入与读取(self, pref_env):
        env = pref_env
        rec = env["service"].set_preference("u1", "technician", "张三")
        assert rec["preference_type"] == "technician"
        assert rec["source"] == "explicit_memorize"
        assert rec["is_active"] is True
        active = env["service"].list_active_preferences("u1")
        assert len(active) == 1
        assert active[0].preference_value == "张三"

    def test_同类型覆盖原子停用旧值(self, pref_env):
        env = pref_env
        env["service"].set_preference("u1", "technician", "张三")
        env["service"].set_preference("u1", "technician", "李四")
        active = env["service"].list_active_preferences("u1")
        assert len(active) == 1  # 单 active：旧值停用，不是并存
        assert active[0].preference_value == "李四"
        # 历史行保留（审计），但不再 active
        all_rows = env["repo"].get_all_preferences("u1")
        assert len(all_rows) == 2
        assert sum(1 for r in all_rows if r["is_active"]) == 1

    def test_非法类型与空值被拒绝(self, pref_env):
        env = pref_env
        from application.context_contracts import PreferenceDomainError

        with pytest.raises(PreferenceDomainError):
            env["service"].set_preference("u1", "unknown_type", "x")
        with pytest.raises(PreferenceDomainError):
            env["service"].set_preference("u1", "technician", "  ")
        with pytest.raises(PreferenceDomainError):
            env["service"].set_preference("u1", "technician", "张三", source="legacy_unverified")


class TestIsolation:
    def test_多用户不串读(self, pref_env):
        env = pref_env
        env["service"].set_preference("u1", "technician", "张三")
        env["service"].set_preference("u2", "technician", "王五")
        assert len(env["service"].list_active_preferences("u1")) == 1
        assert len(env["service"].list_active_preferences("u2")) == 1
        assert env["service"].list_active_preferences("u1")[0].preference_value == "张三"
        assert env["service"].list_active_preferences("u2")[0].preference_value == "王五"

    def test_删除不泄漏其他用户(self, pref_env):
        env = pref_env
        env["service"].set_preference("u1", "technician", "张三")
        env["service"].delete_preference("u2", "technician")  # u2 不存在 -> 幂等 None
        assert env["service"].list_active_preferences("u1")  # u1 不受影响


class TestDeletePipeline:
    def test_删除后下一轮不再使用(self, pref_env):
        env = pref_env
        env["service"].set_preference("u1", "technician", "张三")
        tomb = env["service"].delete_preference("u1", "technician")
        assert tomb is not None
        assert tomb["normalized_value"] == "张三"
        assert env["service"].list_active_preferences("u1") == []

    def test_删除联动摘要失效(self, pref_env):
        env = pref_env
        # 构造成熟摘要：直接写一条 active 摘要
        from db.models import ConversationSummary

        conv = env["router"].conversations.create_conversation(user_id="u1")
        env["service"].set_preference("u1", "technician", "张三")
        with env["router"].session_manager.session_scope() as session:
            session.add(ConversationSummary(
                conversation_id=conv["id"], from_sequence=1, to_sequence=5,
                content="旧摘要", key_facts='[]', status="active", version=1,
            ))
        env["service"].delete_preference("u1", "technician")
        active = SummaryRepository(env["router"].session_manager).get_latest_active(conv["id"])
        assert active is None  # 摘要已失效（保留审计但不进入上下文）

    def test_删除屏蔽来源消息(self, pref_env):
        env = pref_env
        conv = env["router"].conversations.create_conversation(user_id="u1")
        msg = env["router"].conversations.add_message(conv["id"], "user", "请记住我偏好张三")
        assert msg is not None
        env["service"].set_preference("u1", "technician", "张三", source_message_id=str(msg["id"]))
        env["service"].delete_preference("u1", "technician")
        recent = env["router"].conversations.get_recent_messages(conv["id"])
        target = [m for m in recent if str(m["id"]) == str(msg["id"])][0]
        assert (target["metadata"] or {}).get(CONTEXT_EXCLUDED_KEY) is True
        assert (target["metadata"] or {}).get("context_excluded_reason") == "preference_tombstone"

    def test_重复删除幂等(self, pref_env):
        env = pref_env
        env["service"].set_preference("u1", "technician", "张三")
        assert env["service"].delete_preference("u1", "technician") is not None
        assert env["service"].delete_preference("u1", "technician") is None  # 幂等成功


class TestLegacyMigration:
    def test_旧表数据迁移为legacy_unverified(self, pref_env):
        env = pref_env
        # 造旧表数据（user_preferences）
        from db.models import UserPreference
        from datetime import datetime

        with env["router"].session_manager.session_scope() as session:
            session.add(UserPreference(
                user_id="u1", preference_type="technician",
                preference_value="旧技师", confidence_score=3,
            ))
        n = env["repo"].migrate_legacy()
        assert n >= 1
        rows = env["repo"].get_all_preferences("u1")
        legacy = [r for r in rows if r["source"] == "legacy_unverified"]
        assert legacy and legacy[0]["is_active"] is False  # 默认不注入
        assert env["service"].list_active_preferences("u1") == []  # 不静默提升

    def test_重新确认后提升为可信(self, pref_env):
        env = pref_env
        from db.models import UserPreference

        with env["router"].session_manager.session_scope() as session:
            session.add(UserPreference(
                user_id="u1", preference_type="technician",
                preference_value="旧技师", confidence_score=3,
            ))
        env["repo"].migrate_legacy()
        record = env["service"].reconfirm_legacy("u1", "technician")
        assert record is not None
        active = env["service"].list_active_preferences("u1")
        assert len(active) == 1
        assert active[0].source.value == "explicit_memorize"


class TestLegacyAdapter:
    def test_旧组件写入收敛到新表(self, pref_env):
        env = pref_env
        from db.repositories.user_behavior_repository import UserBehaviorRepository

        adapter_repo = UserBehaviorRepository(
            env["router"].session_manager, preference_repository=env["repo"]
        )
        assert adapter_repo.update_user_preference("u1", "technician", "适配器写入") is True
        active = env["service"].list_active_preferences("u1")
        assert len(active) == 1
        assert active[0].preference_value == "适配器写入"


class TestPreferenceAPI:
    """HTTP 验收（TestClient，本地演示边界）。"""

    def _client(self):
        from fastapi.testclient import TestClient

        from api.chat_handler import reset_session_manager
        from app import create_app

        reset_session_manager()
        return TestClient(create_app())

    def test_写入读取删除闭环(self):
        client = self._client()
        r = client.put("/api/v1/preferences/technician", json={"value": "张三", "user_id": "default_user"})
        assert r.status_code == 200, r.text
        assert r.json()["preference"]["preference_value"] == "张三"

        r = client.get("/api/v1/preferences", params={"user_id": "default_user"})
        assert r.status_code == 200
        prefs = r.json()["preferences"]
        assert any(p["preference_type"] == "technician" for p in prefs)

        r = client.delete("/api/v1/preferences/technician", params={"user_id": "default_user"})
        assert r.status_code == 200
        assert r.json()["tombstone"] is not None

        r = client.get("/api/v1/preferences", params={"user_id": "default_user"})
        active = [p for p in r.json()["preferences"] if p["is_active"]]
        assert active == [] or all(p["preference_type"] != "technician" for p in active)

    def test_身份不一致被拒绝(self):
        client = self._client()
        r = client.put("/api/v1/preferences/technician", json={"value": "张三", "user_id": "hacker"})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "CONVERSATION_ACCESS_DENIED"

    def test_非法输入返回422(self):
        client = self._client()
        r = client.put("/api/v1/preferences/technician", json={"value": "", "user_id": "default_user"})
        assert r.status_code == 422

    def test_删除他人不存在偏好幂等(self):
        client = self._client()
        r = client.delete("/api/v1/preferences/service", params={"user_id": "default_user"})
        assert r.status_code == 200
        assert r.json()["tombstone"] is None