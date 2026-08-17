"""Phase A 测试基线与 FakeLLM 配置。

所有测试默认运行在 MODEL_PROVIDER=fake / EMBEDDING_PROVIDER=fake 下，
保证零真实 LLM/Embedding API 调用、可离线、可重复。
"""

import os
import pytest

os.environ.setdefault("MODEL_PROVIDER", "fake")
os.environ.setdefault("EMBEDDING_PROVIDER", "fake")


@pytest.fixture(autouse=True)
def _fake_llm_env(monkeypatch):
    """强制 fake 提供商，并清理 FakeChatModel 的调用记录。"""
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    from config.model_provider import FakeChatModel

    FakeChatModel.calls.clear()
    yield
    FakeChatModel.calls.clear()
