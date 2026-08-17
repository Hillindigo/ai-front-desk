"""B2：会话运行时（ConversationSession / SessionManager）测试"""

import pytest

from application.session_runtime import SessionManager


@pytest.fixture()
def manager():
    sm = SessionManager()
    yield sm
    sm._router.close()


class TestSessionManager:
    def test_create_and_get_session(self, manager):
        s1 = manager.create_conversation(user_id="u1")
        s2 = manager.get_or_create_session(s1.conversation_id, user_id="u1")
        assert s1 is s2  # 缓存命中同一对象
        assert s1.conversation_id == s2.conversation_id

    def test_get_session_checks_ownership(self, manager):
        s = manager.create_conversation(user_id="alice")
        with pytest.raises(PermissionError):
            manager.get_or_create_session(s.conversation_id, user_id="bob")

    def test_get_unknown_session_raises(self, manager):
        with pytest.raises(KeyError):
            manager.get_or_create_session("no-such-id")

    def test_session_restores_messages_from_db(self, manager):
        s = manager.create_conversation(user_id="u1")
        manager.repository.add_message(s.conversation_id, "user", "你好")
        manager.repository.add_message(s.conversation_id, "assistant", "回复")

        # 丢弃缓存 -> 从 DB 重建
        manager.drop_cache(s.conversation_id)
        restored = manager.get_or_create_session(s.conversation_id, user_id="u1")
        contents = [m["content"] for m in restored.recent_messages]
        assert contents == ["你好", "回复"]

    def test_sessions_are_isolated(self, manager):
        a = manager.create_conversation(user_id="u1")
        b = manager.create_conversation(user_id="u2")
        assert a.lock is not b.lock
        assert a.messages is not b.messages
        assert a.appointment_draft is not b.appointment_draft

        manager.repository.add_message(a.conversation_id, "user", "A的消息")
        manager.drop_cache(a.conversation_id)
        manager.drop_cache(b.conversation_id)
        ra = manager.get_or_create_session(a.conversation_id)
        rb = manager.get_or_create_session(b.conversation_id)
        assert [m["content"] for m in ra.recent_messages] == ["A的消息"]
        assert rb.recent_messages == []

    def test_default_conversation_is_stable(self, manager):
        d1 = manager.get_or_create_default("default_user")
        d2 = manager.get_or_create_default("default_user")
        assert d1.conversation_id == d2.conversation_id  # 同一默认会话

    def test_default_conversation_isolated_per_user(self, manager):
        d_a = manager.get_or_create_default("user_a")
        d_b = manager.get_or_create_default("user_b")
        assert d_a.conversation_id != d_b.conversation_id

    def test_write_failure_does_not_leak_session(self, manager):
        """决策二：一次写入失败（事务回滚）后，后续写入仍正常。"""
        s = manager.create_conversation(user_id="u1")
        # 非法写入（空内容违反非空约束）应抛异常且回滚
        with pytest.raises(Exception):
            manager.repository.add_message(s.conversation_id, "user", None)
        # 后续正常写入不受影响
        ok = manager.repository.add_message(s.conversation_id, "user", "正常消息")
        assert ok is not None
