"""Phase I I1-E1：生产配置校验 / 启动门禁（D12 决策测试）。

覆盖：
- 开发/测试模式默认不启用门禁（保持既有行为与全量测试全绿）。
- 生产模式缺少会话密钥、允许来源、必要模型配置即拒启（ConfigError）。
- 拒绝 fake 测试模型、占位符密钥进入生产。
- create_app 接线：生产配置无效时创建应用抛 ConfigError。
"""

import pytest

from config.settings import AppSettings, ConfigError
from config import settings as settings_module


def _fresh(monkeypatch, **env):
    """在给定环境下新建 AppSettings（读 env 而非复用全局实例）。"""
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    return AppSettings()


# --- 开发/测试默认不门禁 ---------------------------------------------------

def test_dev_default_no_gate(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    s = _fresh(monkeypatch)
    assert s.is_production() is False
    assert s.security_issues() == []


# --- 生产缺密钥 ------------------------------------------------------------

def test_prod_missing_secret_rejected(monkeypatch):
    s = _fresh(monkeypatch,
               APP_ENV="production",
               ADMIN_SESSION_SECRET="",
               CORS_ORIGINS="https://admin.example.com",
               MODEL_PROVIDER="qwen",
               LLM_API_KEY="sk-real-test",
               LLM_MODEL="qwen-plus")
    issues = s.security_issues()
    assert any("ADMIN_SESSION_SECRET" in i for i in issues)
    with pytest.raises(ConfigError):
        s.validate_production()


def test_prod_placeholder_secret_rejected(monkeypatch):
    s = _fresh(monkeypatch,
               APP_ENV="production",
               ADMIN_SESSION_SECRET="your_llm_api_key_here",
               CORS_ORIGINS="https://admin.example.com",
               MODEL_PROVIDER="qwen",
               LLM_API_KEY="sk-real-test",
               LLM_MODEL="qwen-plus")
    assert any("ADMIN_SESSION_SECRET" in i for i in s.security_issues())


# --- 生产 CORS 通配符不允许 -------------------------------------------------

def test_prod_wildcard_cors_rejected(monkeypatch):
    s = _fresh(monkeypatch,
               APP_ENV="production",
               ADMIN_SESSION_SECRET="s3cr3t-strong",
               CORS_ORIGINS="*",
               MODEL_PROVIDER="qwen",
               LLM_API_KEY="sk-real-test",
               LLM_MODEL="qwen-plus")
    assert any("CORS_ORIGINS" in i for i in s.security_issues())


# --- 生产禁 fake 模型 / 占位符模型配置 ---------------------------------------

def test_prod_fake_model_rejected(monkeypatch):
    s = _fresh(monkeypatch,
               APP_ENV="production",
               ADMIN_SESSION_SECRET="s3cr3t-strong",
               CORS_ORIGINS="https://admin.example.com",
               MODEL_PROVIDER="fake",
               LLM_API_KEY="sk-real-test",
               LLM_MODEL="qwen-plus")
    assert any("fake" in i.lower() for i in s.security_issues())


def test_prod_placeholder_model_key_rejected(monkeypatch):
    s = _fresh(monkeypatch,
               APP_ENV="production",
               ADMIN_SESSION_SECRET="s3cr3t-strong",
               CORS_ORIGINS="https://admin.example.com",
               MODEL_PROVIDER="qwen",
               LLM_API_KEY="your_llm_api_key_here",
               LLM_MODEL="qwen-plus")
    assert any("LLM_API_KEY" in i for i in s.security_issues())


# --- 生产完整合法配置放行 ---------------------------------------------------

def test_prod_valid_config_passes(monkeypatch):
    s = _fresh(monkeypatch,
               APP_ENV="production",
               ADMIN_SESSION_SECRET="s3cr3t-strong-value",
               CORS_ORIGINS="https://admin.example.com,https://www.example.com",
               MODEL_PROVIDER="qwen",
               LLM_API_KEY="sk-real-test-value",
               LLM_MODEL="qwen-plus")
    assert s.security_issues() == []
    s.validate_production()  # 不应抛


# --- create_app 接线（生产无效配置拒启；开发正常） ----------------------------

def test_create_app_rejects_invalid_production_config(monkeypatch):
    from app import create_app
    s = settings_module  # config.settings 的全局实例（from config import settings 绑定的是实例）
    monkeypatch.setattr(s, "is_production", lambda: True)
    monkeypatch.setattr(s, "validate_production",
                        lambda: (_ for _ in ()).throw(ConfigError("ADMIN_SESSION_SECRET missing")))
    with pytest.raises(ConfigError):
        create_app()


def test_create_app_ok_in_dev(monkeypatch):
    from app import create_app
    s = settings_module  # config.settings 的全局实例（from config import settings 绑定的是实例）
    monkeypatch.setattr(s, "is_production", lambda: False)
    app = create_app()
    assert app is not None
