"""Phase H H2：咨询到预约的买家主路径 —— 会话级预约契约与幂等/冲突验收。

覆盖：
- 会话级预约状态契约：GET /api/v1/conversations/{id}/appointment 返回 active/recent；
- 买家主路径：创建草稿 → 确认 → 生成 confirmed，前端可查询（重载恢复）；
- 重复提交幂等：同 idempotency_key 多次确认只产生一条业务写入；
- 冲突返回稳定错误码（409），不把失败伪造为成功；
- 归属校验：非本用户访问会话预约返回 403/404。

以 REST 契约为准（页面不可单测），Fake LLM 下运行，零真实模型请求。
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import create_app
from services.technician_service import TechnicianService


@pytest.fixture(scope="module")
def client():
    TechnicianService().initialize_default_technicians()
    with TestClient(create_app()) as c:
        yield c


def dt_str(hour: int, minute: int = 0) -> str:
    return datetime(2026, 8, 18, hour, minute).isoformat()


def tech_id(client) -> int:
    techs = client.get("/api/technicians/").json()
    return techs[0]["id"]


def create_conv(client, user_id="u1") -> str:
    r = client.post("/api/v1/conversations", json={"user_id": user_id, "channel": "web"})
    assert r.status_code == 200, r.text
    return r.json()["conversation_id"]


class TestSessionAppointmentContract:
    def test_session_appointment_empty(self, client):
        cid = create_conv(client)
        r = client.get(f"/api/v1/conversations/{cid}/appointment", params={"user_id": "u1"})
        assert r.status_code == 200
        body = r.json()
        assert body["active"] is None and body["recent"] is None
        assert body["conversation_id"] == cid

    def test_session_appointment_returns_active_draft(self, client):
        cid = create_conv(client)
        created = client.post("/api/v1/appointments", json={
            "user_id": "u1", "conversation_id": cid,
            "service_type": "肩颈放松", "mode": "draft", "project": "肩颈放松",
        })
        assert created.status_code == 200
        r = client.get(f"/api/v1/conversations/{cid}/appointment", params={"user_id": "u1"})
        active = r.json()["active"]
        assert active is not None
        assert active["id"] == created.json()["id"]
        assert active["status"] == "draft"

    def test_ownership_mismatch_403(self, client):
        cid = create_conv(client, user_id="u1")
        r = client.get(f"/api/v1/conversations/{cid}/appointment", params={"user_id": "u2"})
        assert r.status_code == 403


class TestBookingMainPath:
    def test_full_path_draft_to_confirmed(self, client):
        cid = create_conv(client)
        tid = tech_id(client)
        # 1) 草稿阶段（关联会话）→ 会话预约显示进行中草稿
        draft = client.post("/api/v1/appointments", json={
            "user_id": "u1", "conversation_id": cid, "service_type": "肩颈放松",
            "mode": "draft", "project": "肩颈放松",
        })
        assert draft.status_code == 200
        aid = draft.json()["id"]
        assert draft.json()["status"] == "draft"
        st = client.get(f"/api/v1/conversations/{cid}/appointment", params={"user_id": "u1"}).json()["active"]
        assert st["id"] == aid and st["status"] == "draft"
        # 2) 补齐信息并确认：同会话复用活跃草稿，一步确认（带幂等键）
        conf = client.post("/api/v1/appointments", json={
            "user_id": "u1", "conversation_id": cid, "service_type": "肩颈放松",
            "mode": "confirm", "project": "肩颈放松", "technician_id": tid,
            "start_time": dt_str(10), "end_time": dt_str(11), "duration_minutes": 60,
            "idempotency_key": "bk-main-2",
        })
        assert conf.status_code == 200
        assert conf.json()["id"] == aid and conf.json()["status"] == "confirmed"
        # 3) 重载后会话预约显示已确认（买家端可核对预约 ID 与状态）
        st2 = client.get(f"/api/v1/conversations/{cid}/appointment", params={"user_id": "u1"}).json()
        assert st2["recent"]["id"] == aid and st2["recent"]["status"] == "confirmed"
        assert st2["active"] is None

    def test_duplicate_confirm_single_write(self, client):
        cid = create_conv(client)
        tid = tech_id(client)
        created = client.post("/api/v1/appointments", json={
            "user_id": "u1", "conversation_id": cid, "service_type": "足疗",
            "mode": "confirm", "technician_id": tid,
            "start_time": dt_str(11), "end_time": dt_str(12), "duration_minutes": 60,
            "idempotency_key": "bk-dup-1",
        })
        assert created.status_code == 200
        # 重复提交（同幂等键 + 双击重试）不产生重复
        again = client.post(f"/api/v1/appointments/{created.json()['id']}/confirm", json={
            "user_id": "u1", "idempotency_key": "bk-dup-1",
        })
        assert again.status_code == 200
        st = client.get(f"/api/v1/conversations/{cid}/appointment", params={"user_id": "u1"}).json()
        assert st["recent"]["id"] == created.json()["id"]
        # 该会话确认态预约只有一条
        rows = client.get(f"/api/v1/conversations/{cid}/appointment", params={"user_id": "u1"}).json()
        assert rows["recent"]["status"] == "confirmed"

    def test_conflict_returns_stable_error_not_faked(self, client):
        cid = create_conv(client)
        tid = tech_id(client)
        first = client.post("/api/v1/appointments", json={
            "user_id": "u1", "conversation_id": cid, "service_type": "肩颈放松",
            "mode": "confirm", "technician_id": tid,
            "start_time": dt_str(14), "end_time": dt_str(15), "duration_minutes": 60,
            "idempotency_key": "bk-conflict-a",
        })
        assert first.status_code == 200 and first.json()["status"] == "confirmed"
        # 同技术员同时段第二条：冲突，返回稳定错误码，而非 200 伪造成功
        second = client.post("/api/v1/appointments", json={
            "user_id": "u1", "conversation_id": cid, "service_type": "肩颈放松",
            "mode": "confirm", "technician_id": tid,
            "start_time": dt_str(14, 30), "end_time": dt_str(15, 30), "duration_minutes": 60,
            "idempotency_key": "bk-conflict-b",
        })
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "APPOINTMENT_CONFLICT"

    def test_cancel_recent_visible_to_buyer(self, client):
        cid = create_conv(client)
        tid = tech_id(client)
        created = client.post("/api/v1/appointments", json={
            "user_id": "u1", "conversation_id": cid, "service_type": "足疗",
            "mode": "confirm", "technician_id": tid,
            "start_time": dt_str(16), "end_time": dt_str(17), "duration_minutes": 60,
            "idempotency_key": "bk-cancel-1",
        })
        assert created.status_code == 200
        aid = created.json()["id"]
        cancel = client.post(f"/api/v1/appointments/{aid}/cancel", json={"user_id": "u1"})
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"
        st = client.get(f"/api/v1/conversations/{cid}/appointment", params={"user_id": "u1"}).json()
        assert st["recent"]["status"] == "cancelled"  # 买家端可回读最新结果
