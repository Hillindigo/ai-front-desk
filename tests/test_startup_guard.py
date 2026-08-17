"""B0/A-R1：无真实模型配置时应用仍可启动（降级模式）。

验证目标：模型/知识库依赖不可用不阻断应用启动，聊天请求返回稳定错误。
"""

from fastapi.testclient import TestClient


def test_app_starts_without_model_credentials(monkeypatch):
    """无真实模型 key（qwen 空 key）时应用可启动，聊天返回稳定错误。"""
    # 清掉可能被其他测试缓存的 fake task_agent（惰性单例）
    from api import chat_handler

    chat_handler.reset_task_agent()

    monkeypatch.setenv("MODEL_PROVIDER", "qwen")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "qwen")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")

    from app import create_app

    app = create_app()
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        assert c.get("/docs").status_code == 200
        # 依赖不可用时聊天返回稳定错误文本（HTTP 200 + 可识别错误标记）
        resp = c.post("/chat/stream", json={"message": "你好"})
        assert resp.status_code == 200
        assert ("ERROR" in resp.text) or ("不可用" in resp.text)


def test_app_starts_in_fake_mode(monkeypatch):
    """Fake 模式下应用完整启动，无初始化错误。"""
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")

    from app import create_app

    app = create_app()
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        resp = c.post("/chat/stream", json={"message": "你好"})
        assert resp.status_code == 200
        assert "ERROR" not in resp.text