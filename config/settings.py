"""
应用程序设置模块

当前管理：CORS 来源列表（从环境变量 CORS_ORIGINS 读取，逗号分隔）。
"""

import os
from typing import List


class AppSettings:
    """应用程序设置"""

    def __init__(self):
        self.cors_origins: List[str] = self._parse_cors_origins()

    @staticmethod
    def _parse_cors_origins() -> List[str]:
        """从 CORS_ORIGINS 环境变量解析来源列表；未配置时默认本机来源。"""
        raw = os.getenv("CORS_ORIGINS", "").strip()
        if raw:
            return [origin.strip() for origin in raw.split(",") if origin.strip()]
        return ["http://127.0.0.1:8001", "http://localhost:8001"]


# 全局设置实例
settings = AppSettings()
