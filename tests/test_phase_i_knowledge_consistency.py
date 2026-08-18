"""Phase I I4-A：知识索引单实例一致性 —— /health 暴露版本与陈旧状态。

I0 D1/D2 默认单实例；多进程一致性（I4-B）未纳入本阶段。
"""

from fastapi.testclient import TestClient

from app import create_app


def test_health_ready_exposes_knowledge_versions():
    with TestClient(create_app()) as client:
        r = client.get("/health/ready")
        assert r.status_code == 200, r.text
        checks = r.json()["checks"]
        assert "knowledge_version" in checks
        assert "local_index_version" in checks
        # 单实例下本地索引存在，二者可对比
        assert checks["local_index_version"] is not None
        assert "knowledge_stale" in checks


def test_container_local_index_version_available():
    from api.chat_handler import get_container

    local = get_container().knowledge_service.get_source_version()
    assert isinstance(local, str)
    assert local.startswith("index-") or local == "index-0"
