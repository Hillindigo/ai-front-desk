"""Phase I I3：运行观测 —— 健康/就绪端点、指标端点、RunRecorder 埋点。"""

import pytest
from fastapi.testclient import TestClient

from app import create_app


@pytest.fixture
def auth_service():
    from services.admin_auth import AdminAuthService

    service = AdminAuthService()
    service.clear_for_tests()
    yield service
    service.clear_for_tests()
    service.close()


@pytest.fixture
def client(auth_service):
    with TestClient(create_app()) as c:
        yield c


def provision(service, username="owner-i3@example.test", role="owner"):
    return service.provision_account(
        username=username, password="Correct-Horse-7!",
        display_name=username.split("@")[0], store_name="I3门店", role=role,
    )


def login(client):
    r = client.post("/api/v1/admin/auth/login", json={
        "username": "owner-i3@example.test", "password": "Correct-Horse-7!",
    })
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


class TestHealth:
    def test_live_ok(self, client):
        r = client.get("/health/live")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_ready_reflects_db(self, client):
        r = client.get("/health/ready")
        assert r.status_code in (200, 503)
        body = r.json()
        assert "ready" in body
        assert body["checks"]["database"] is True


class TestMetrics:
    def test_metrics_requires_read_audit(self, auth_service, client):
        provision(auth_service, username="viewer-i3@example.test", role="viewer")
        login_viewer = client.post("/api/v1/admin/auth/login", json={
            "username": "viewer-i3@example.test", "password": "Correct-Horse-7!",
        })
        assert login_viewer.status_code == 200
        r = client.get("/api/v1/admin/metrics")
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "PERMISSION_DENIED"

    def test_metrics_returns_counts(self, auth_service, client):
        owner = provision(auth_service)
        login(client)
        r = client.get("/api/v1/admin/metrics")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["store_id"] == owner["store_id"]
        assert "audit_action_counts" in body
        assert "run_metrics" in body
        assert "total" in body["run_metrics"]
        assert body["run_metrics"]["scope"] == "process"


class TestRunRecorder:
    def test_recorder_timings_and_summary(self):
        from application.run_log import RunRecorder

        rec = RunRecorder(max_entries=10)
        e1 = rec.begin("c1", request_id="r1")
        rec.end(e1, outcome="completed", workflow="consultation")
        e2 = rec.begin("c2", request_id="r2")
        rec.end(e2, outcome="failed", error_category="model_timeout")
        s = rec.summary()
        assert s["total"] == 2
        assert s["outcomes"]["completed"] == 1
        assert s["outcomes"]["failed"] == 1
        assert s["error_categories"]["model_timeout"] == 1
        assert s["avg_duration_ms"] is not None

    def test_recorder_drop_counter(self):
        from application.run_log import RunRecorder

        rec = RunRecorder(max_entries=1)
        a = rec.begin("c1")
        b = rec.begin("c2")  # 超上限：deque(maxlen=1) 丢弃最旧
        rec.end(a, outcome="completed")
        rec.end(b, outcome="completed")
        s = rec.summary()
        assert s["total"] <= 2
        assert "drop_count" in s
