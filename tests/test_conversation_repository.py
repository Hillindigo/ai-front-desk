"""B1：会话与消息 Repository 测试（Phase B 决策一：临时库隔离）"""

import pytest

from db.db_router import DatabaseRouter


@pytest.fixture()
def repo():
    router = DatabaseRouter()
    yield router.conversations
    router.close()


class TestConversationRepository:
    def test_create_conversation_returns_unique_id(self, repo):
        c1 = repo.create_conversation(user_id="u1")
        c2 = repo.create_conversation(user_id="u1")
        assert c1["id"] != c2["id"]
        assert c1["user_id"] == "u1"
        assert c1["status"] == "active"
        assert c1["channel"] == "web"

    def test_get_conversation_checks_ownership(self, repo):
        conv = repo.create_conversation(user_id="alice")
        # 正确归属
        assert repo.get_conversation(conv["id"], user_id="alice") is not None
        # 归属不符 -> None
        assert repo.get_conversation(conv["id"], user_id="bob") is None
        # 不存在 -> None
        assert repo.get_conversation("no-such-id", user_id="alice") is None

    def test_add_message_persists_and_updates_conversation(self, repo):
        conv = repo.create_conversation(user_id="u1")
        msg = repo.add_message(conv["id"], role="user", content="你好")
        assert msg is not None
        assert msg["role"] == "user"
        assert msg["content"] == "你好"
        assert msg["sequence"] == 1
        assert msg["message_type"] == "text"

        msg2 = repo.add_message(conv["id"], role="assistant", content="回复")
        assert msg2["sequence"] == 2

        updated = repo.get_conversation(conv["id"], user_id="u1")
        assert updated["updated_at"] >= conv["updated_at"]

    def test_add_message_rejects_unknown_conversation(self, repo):
        assert repo.add_message("no-such-conversation", "user", "x") is None

    def test_recent_messages_ordered_by_sequence(self, repo):
        conv = repo.create_conversation(user_id="u1")
        for i in range(5):
            repo.add_message(conv["id"], role="user", content=f"msg-{i}")

        recent = repo.get_recent_messages(conv["id"], limit=3)
        assert [m["content"] for m in recent] == ["msg-2", "msg-3", "msg-4"]
        # sequence 升序
        assert [m["sequence"] for m in recent] == [3, 4, 5]

    def test_messages_are_isolated_between_conversations(self, repo):
        a = repo.create_conversation(user_id="u1")
        b = repo.create_conversation(user_id="u2")
        repo.add_message(a["id"], role="user", content="A的消息")
        repo.add_message(b["id"], role="user", content="B的消息")

        assert [m["content"] for m in repo.get_recent_messages(a["id"])] == ["A的消息"]
        assert [m["content"] for m in repo.get_recent_messages(b["id"])] == ["B的消息"]

    def test_get_messages_after(self, repo):
        conv = repo.create_conversation(user_id="u1")
        repo.add_message(conv["id"], role="user", content="m1")
        repo.add_message(conv["id"], role="user", content="m2")
        after = repo.get_messages_after(conv["id"], after_sequence=1)
        assert [m["content"] for m in after] == ["m2"]

    def test_metadata_json_roundtrip(self, repo):
        conv = repo.create_conversation(user_id="u1")
        msg = repo.add_message(conv["id"], role="assistant", content="x", metadata={"run_id": "r1"})
        stored = repo.get_recent_messages(conv["id"])
        assert stored[0]["metadata"] == {"run_id": "r1"}
