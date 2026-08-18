"""
应用程序设置模块（Phase I I1-E1：安全配置与生产门禁）

职责：
- CORS 来源（环境变量 CORS_ORIGINS，逗号分隔，默认本机 8001）。
- APP_ENV 区分开发/生产，生产模式下对缺少会话密钥、允许来源或必要模型配置
  执行"安全失败"（验证失败即拒启），符合 I0 决策 D12。

设计要点：
- 默认 APP_ENV=development，不启用门禁，保证开发/测试行为不变、全量测试保持全绿。
- 生产门禁只允许显式白名单 CORS（禁止 *），并拒绝占位符密钥。
- 模型配置校验从 config.model_provider 惰性读取，避免循环依赖。
"""

import os
from typing import List

_PLACEHOLDERS = {"your_llm_api_key_here", "your_embedding_api_key_here",
                 "your_openai_api_key_here", "xxx", "changeme", "todo"}


class ConfigError(Exception):
    """生产配置无效；由调用方决定拒绝启动。"""


class AppSettings:
    """应用程序设置；生产门禁相关方法不改动既有属性访问。"""

    def __init__(self):
        self.app_env = (os.getenv("APP_ENV", "development") or "development").strip().lower()
        self.cors_origins: List[str] = self._parse_cors_origins()
        # Phase I I1：服务端会话/CSRF 签名密钥（生产必填）。
        self.session_secret = (os.getenv("ADMIN_SESSION_SECRET", "") or "").strip()
        # 生产 Cookie 是否 Secure（复用 admin_auth 既有语义）。
        self.cookie_secure = os.getenv("ADMIN_COOKIE_SECURE", "false").strip().lower() in {
            "1", "true", "yes", "on"
        }
        self.trusted_hosts: List[str] = self._parse_trusted_hosts()

    @staticmethod
    def _parse_cors_origins() -> List[str]:
        raw = os.getenv("CORS_ORIGINS", "").strip()
        if raw:
            return [o.strip() for o in raw.split(",") if o.strip()]
        return ["http://127.0.0.1:8001", "http://localhost:8001"]

    @staticmethod
    def _parse_trusted_hosts() -> List[str]:
        """受信 Host 列表（I1：TrustedHost 校验）；未配置时不启用宿主限制。"""
        raw = os.getenv("TRUSTED_HOSTS", "").strip()
        if not raw:
            return []
        return [h.strip().lower() for h in raw.split(",") if h.strip()]

    def is_production(self) -> bool:
        return self.app_env in ("production", "prod")

    # ------------------------------------------------------------------
    # 模型配置校验（惰性读 config.model_provider，避免循环导入）
    # ------------------------------------------------------------------
    def _model_issues(self) -> List[str]:
        from config import model_provider

        provider = (model_provider.get_model_provider() or "").strip().lower()
        if provider == "fake":
            # fake 是测试专用，生产必须换成真实模型。
            return ["MODEL_PROVIDER=fake is test-only and not allowed in production"]

        if provider == "azure":
            key = (os.getenv("AZURE_OPENAI_API_KEY", "") or "").strip()
            deployment = (os.getenv("AZURE_OPENAI_DEPLOYMENT", "") or "").strip()
            issues = []
            if _empty_or_placeholder(key):
                issues.append("AZURE_OPENAI_API_KEY missing or placeholder")
            if not deployment:
                issues.append("AZURE_OPENAI_DEPLOYMENT missing")
            return issues

        if provider in model_provider.CHAT_PROVIDERS:
            key = (os.getenv("LLM_API_KEY", "") or "").strip()
            model = (os.getenv("LLM_MODEL", "") or "").strip()
            issues = []
            if _empty_or_placeholder(key):
                issues.append("LLM_API_KEY missing or placeholder")
            if _empty_or_placeholder(model):
                issues.append("LLM_MODEL missing or placeholder")
            return issues

        return [f"unsupported MODEL_PROVIDER={provider!r}"]

    # ------------------------------------------------------------------
    # 生产门禁
    # ------------------------------------------------------------------
    def security_issues(self) -> List[str]:
        """返回生产配置问题列表；非生产返回空列表。"""
        if not self.is_production():
            return []
        issues: List[str] = []
        if _empty_or_placeholder(self.session_secret):
            issues.append("ADMIN_SESSION_SECRET missing or placeholder (required to sign sessions in production)")
        if not self.cors_origins or (len(self.cors_origins) == 1 and self.cors_origins[0] == "*"):
            issues.append("CORS_ORIGINS must be an explicit allow-list in production (wildcard '*' forbidden)")
        issues.extend(self._model_issues())
        return issues

    def validate_production(self) -> None:
        """生产模式下发现配置问题即抛 ConfigError（调用方据此拒启）。"""
        issues = self.security_issues()
        if issues:
            raise ConfigError("; ".join(issues))


def _empty_or_placeholder(value: str) -> bool:
    v = (value or "").strip().lower()
    return not v or v in _PLACEHOLDERS


# 全局设置实例
settings = AppSettings()
