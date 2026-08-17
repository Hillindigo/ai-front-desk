"""C5：Agent 接入领域服务集成测试

验证：预约完成走领域服务（Appointment confirmed）、不再写排班表；
未完成预约草稿持久化且重启后可恢复。
"""

import pytest
from fastapi.testclient import TestClient

from api.chat_handler import ProcessUserInput_stream, get_session_manager, reset_session_manager
from db.db_router import DatabaseRouter
from services.appointment_domain import AppointmentCommandService
from services.technician_service import TechnicianService


@pytest.fixture(autouse=True)
def fresh():
    reset_session_manager()
    TechnicianService().initialize_default_technicians()
    yield
    reset_session_manager()


async def run_turn(conversation_id: str, user_id: str, text: str) -> str:
    out = []
    async for token in ProcessUserInput_stream(text, conversation_id=conversation_id, user_id=user_id):
        out.append(token)
    return "".join(out)


class TestAgentUsesDomainService:
    @pytest.mark.asyncio
    async def test_completed_appointment_creates_confirmed_record(self):
        """预约完成后：Appointment 表出现 confirmed 记录，排班表不再写入 busy。"""
        mgr = get_session_manager()
        s = mgr.create_conversation(user_id="u1")

        reply = await run_turn(s.conversation_id, "u1", "我想预约明天下午2点的肩颈放松，女服务人员")

        svc = AppointmentCommandService()
        try:
            # 会话下有 confirmed 预约
            appointments = svc.repo.list_by_user("default_user")
            assert len(appointments) == 1, f"预约回复: {reply[:80]}"
            assert appointments[0]["status"] == "confirmed"
            assert appointments[0]["service_type"] == "肩颈放松"
        finally:
            svc.close()

        # 排班表无新 busy 记录
        router = DatabaseRouter()
        try:
            schedules = router.technicians.get_technician_schedules(1, __import__("datetime").datetime(2026, 8, 18))
            busy = [s for s in schedules if s.get("status") == "busy"]
            assert busy == [], "不应再写 technician_schedules busy"
        finally:
            router.close()

    @pytest.mark.asyncio
    async def test_duplicate_completion_idempotent(self):
        """同会话重复完成预约：幂等键保证只有一条 confirmed。"""
        mgr = get_session_manager()
        s = mgr.create_conversation(user_id="u1")
        text = "我想预约明天下午2点的肩颈放松，女服务人员"

        await run_turn(s.conversation_id, "u1", text)
        # 重置 agent（模拟再次进入预约流程）
        s.agent = None
        await run_turn(s.conversation_id, "u1", text)

        svc = AppointmentCommandService()
        try:
            appointments = svc.repo.list_by_user("default_user", status="confirmed")
            assert len(appointments) == 1, "幂等键应防止重复预约"
        finally:
            svc.close()

    @pytest.mark.asyncio
    async def test_draft_persisted_and_restored(self):
        """未完成预约：草稿持久化，agent 重建后从草稿恢复项目字段。"""
        mgr = get_session_manager()
        s = mgr.create_conversation(user_id="u1")

        # 只提供项目（信息不完整 -> 草稿同步）
        await run_turn(s.conversation_id, "u1", "我想预约肩颈放松")

        # 草稿已持久化
        svc = AppointmentCommandService()
        try:
            draft = svc.get_active_draft(s.conversation_id)
            assert draft is not None
            assert draft["project"] == "肩颈放松"
        finally:
            svc.close()

        # 模拟重启：清缓存重建 agent -> 恢复项目字段
        mgr.drop_cache(s.conversation_id)
        restored = mgr.get_or_create_session(s.conversation_id, user_id="u1")
        from api.chat_handler import get_task_agent_for
        agent = get_task_agent_for(restored)
        assert agent.appointment_agent.appointment_history.get("project") == "肩颈放松"


class TestAgentRestartIntegration:
    def test_restart_recovers_appointment_context(self):
        """完整链路：写消息 -> 重启 -> 会话恢复仍可继续预约对话。"""
        from app import create_app

        reset_session_manager()
        with TestClient(create_app()) as c1:
            conv = c1.post("/api/v1/conversations", json={"user_id": "u1"}).json()
            cid = conv["conversation_id"]
            r = c1.post(
                f"/api/v1/conversations/{cid}/turns",
                json={"message": "我想预约肩颈放松", "user_id": "u1"},
            )
            assert r.status_code == 200

        # 模拟重启
        reset_session_manager()
        with TestClient(create_app()) as c2:
            resp = c2.get(f"/api/v1/conversations/{cid}", params={"user_id": "u1"})
            assert resp.status_code == 200
            roles = [m["role"] for m in resp.json()["messages"]]
            assert "user" in roles

        # 草稿仍在（重启后可从 DB 恢复预约上下文）
        svc = AppointmentCommandService()
        try:
            draft = svc.get_active_draft(cid)
            assert draft is not None and draft["project"] == "肩颈放松"
        finally:
            svc.close()
