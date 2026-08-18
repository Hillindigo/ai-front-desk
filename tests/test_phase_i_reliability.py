"""Phase I I6：并发/恢复/备份 —— 一致性备份、恢复演练、重启恢复、幂等。"""

import os

from sqlalchemy import create_engine, text


def _sqlite_url(path):
    return "sqlite:///" + str(path).replace("\\", "/")


def test_backup_verify_restore_drill(tmp_path):
    from scripts import backup as B

    # 用会话测试库（conftest 临时库）作为源，写入数据
    from db.db_router import DatabaseRouter

    router = DatabaseRouter()
    conv = router.conversations.create_conversation("cust-i6")
    router.conversations.add_message(conv["id"], "user", "备份前数据 XYZ-13800000000")
    router.close()

    manifest = B.backup(str(tmp_path))
    backup_path = os.path.join(str(tmp_path), manifest["backup"])
    assert B.verify(backup_path)["ok"] is True

    # 恢复：先 dry-run，再真实恢复到隔离目录
    target = str(tmp_path / "restored.db")
    assert B.restore(backup_path, target, dry_run=True)["dry_run"] is True
    res = B.restore(backup_path, target, dry_run=False)
    assert res["dry_run"] is False
    assert B.verify(target)["ok"] is True

    eng = create_engine(_sqlite_url(target))
    try:
        with eng.connect() as c:
            n = c.execute(text(
                "SELECT COUNT(*) FROM messages WHERE content LIKE '%备份前数据%'"
            )).scalar()
    finally:
        eng.dispose()
    assert n == 1


def test_restart_recovery_persists(tmp_path):
    from db.db_router import DatabaseRouter

    url = _sqlite_url(tmp_path / "restart.db")
    r1 = DatabaseRouter(url)
    conv = r1.conversations.create_conversation("cust-restart")
    r1.conversations.add_message(conv["id"], "user", "重启前的消息保留")
    r1.close()

    r2 = DatabaseRouter(url)  # 模拟重启后重新打开
    try:
        msgs = r2.conversations.get_recent_messages(conv["id"], limit=10)
    finally:
        r2.close()
    assert any(m["content"] == "重启前的消息保留" for m in msgs)


def test_repeat_appointment_confirm_idempotent(tmp_path):
    """E20：同幂等键重复 confirm 只产生一个确认结果。"""
    from datetime import datetime, timedelta

    from db.db_router import DatabaseRouter

    router = DatabaseRouter()
    try:
        start = datetime.utcnow() + timedelta(days=2)
        draft = router.appointments.create_draft(
            user_id="cust-rep", conversation_id=None, service_type="肩颈放松",
            fields={"start_time": start, "end_time": start + timedelta(minutes=30),
                    "duration_minutes": 30},
        )
        a1 = router.appointments.transition(
            draft["id"], "cust-rep", "confirmed", "confirmed", request_id="rep-key")
        a2 = router.appointments.transition(
            draft["id"], "cust-rep", "confirmed", "confirmed", request_id="rep-key")
        # 幂等：同 request_id 第二次 confirm 返回同一结果，不产生新预约
        assert a1["id"] == a2["id"]
        confirmed = router.appointments.list_by_user("cust-rep", status="confirmed")
        assert len(confirmed) == 1
    finally:
        router.close()
