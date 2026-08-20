"""
FastAPI应用程序

主应用程序入口，配置中间件、路由和异常处理
自动初始化知识库和服务人员数据
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Keep the existing top-level package imports working after the source layout move.
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# 导入路由
from api import api_routers  # noqa: E402
from api.core.exceptions import (  # noqa: E402
    BusinessException,
    api_exception_handler,
    general_exception_handler,
    request_validation_handler,
)
from api.core.security import (  # noqa: E402
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from config.settings import settings  # noqa: E402
from services.appointment_cleanup import appointment_draft_cleanup_loop  # noqa: E402
from services.recommendation_service import RecommendationService  # noqa: E402
from services.technician_service import TechnicianService  # noqa: E402
from web import router as web_router  # noqa: E402

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except ValueError:
        return default


# Pydantic模型


class KnowledgeRequest(BaseModel):
    content: str
    category: str
    keywords: list[str] = []


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    category: str | None = None


async def initialize_system(app: FastAPI | None = None):
    """系统启动时自动初始化（Phase B 决策/A-R1：初始化失败不阻断应用启动）"""
    try:
        logger.info("🚀 正在初始化 AI Front Desk 运营系统...")

        # 初始化知识库服务（F2：由容器单一实例持有并初始化，管理与咨询共享）
        logger.info("📚 初始化知识库服务...")
        from api.chat_handler import get_container

        container = get_container()
        await container.initialize()

        # 初始化服务人员服务
        logger.info("👥 初始化服务人员服务...")
        technician_service = TechnicianService()
        technician_service.initialize_default_technicians()

        # 初始化推荐服务
        logger.info("🎯 启动推荐调度服务...")
        recommendation_service = RecommendationService()
        if recommendation_service.start_scheduler():
            logger.info("✅ 推荐调度服务启动成功")
        else:
            logger.warning("⚠️ 推荐调度服务启动失败")

        logger.info("✅ 系统初始化完成！")
        if app is not None:
            app.state.initialization_error = None

    except Exception as e:
        # A-R1：模型/知识库等外部依赖不可用时降级启动，避免整个应用无法启动。
        # 只记录脱敏错误信息，不输出密钥或完整敏感配置。
        logger.error(
            f"❌ 系统初始化失败（应用仍可启动，依赖将不可用）: {type(e).__name__}"
        )
        if app is not None:
            app.state.initialization_error = f"{type(e).__name__}: {str(e)[:200]}"


def create_app() -> FastAPI:
    """创建FastAPI应用实例"""

    # Phase I I1-E1：生产配置门禁（D12）——生产模式配置无效即拒启，
    # 不进入"降级启动"。开发/测试默认 APP_ENV=development，不启用门禁。
    if settings.is_production():
        settings.validate_production()

    app = FastAPI(
        title="AI Front Desk — 线下服务门店智能运营 Agent",
        description="提供门店咨询、预约管理、排班协同、知识检索和客户行为分析能力的 API 服务",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # 添加CORS中间件（来源由 config/settings.py 管理，环境变量 CORS_ORIGINS）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Phase I I1-E2：受信 Host（仅当设置 TRUSTED_HOSTS 时启用；未设置则不限制）
    if settings.trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)

    # Phase I I1-E2：安全响应头 + 基础限流（纯 ASGI，不破坏 SSE 流式）
    app.add_middleware(
        SecurityHeadersMiddleware,
        production=settings.is_production(),
        csp_policy=os.getenv("CSP_POLICY") or None,
    )
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=_env_int("RATE_LIMIT_PER_MIN", 600),
        window_seconds=_env_int("RATE_LIMIT_WINDOW", 60),
        enabled=os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower()
        in {"1", "true", "yes", "on"},
    )

    # 注册异常处理器
    app.add_exception_handler(
        BusinessException,
        cast(Any, api_exception_handler),
    )
    app.add_exception_handler(Exception, general_exception_handler)
    # Phase I I1-E4：校验错误不回显输入值（防密钥/PII 经 422 回显泄漏）
    app.add_exception_handler(
        RequestValidationError,
        cast(Any, request_validation_handler),
    )

    # 买家端与商家端使用独立进程和端口；服务端再次校验路由边界。
    app_role = os.getenv("APP_ROLE", "all").strip().lower()
    if app_role not in {"buyer", "admin", "all"}:
        raise RuntimeError("APP_ROLE 必须是 buyer、admin 或 all")
    app.state.app_role = app_role

    @app.middleware("http")
    async def enforce_app_role(request: Request, call_next):
        path = request.url.path
        if app_role == "buyer" and path.startswith(("/admin", "/api/v1/admin/")):
            return JSONResponse({"detail": "商家后台不在买家端口提供"}, status_code=404)
        if app_role == "admin" and not path.startswith(
            (
                "/admin",
                "/api/v1/admin/",
                "/static",
                "/knowledge",
                "/technician",
                "/technician_schedule",
                "/user_behavior",
                "/user_behavior_analysis",
            )
        ):
            return PlainTextResponse("商家后台端口仅提供后台页面和后台 API", status_code=404)
        return await call_next(request)

    # 注册API路由
    for router in api_routers:
        app.include_router(router)

    # 注册Web界面路由
    app.include_router(web_router)

    # 静态文件
    app.mount("/static", StaticFiles(directory="web/static"), name="static")

    # 添加启动事件（初始化失败降级，不阻断启动）
    @app.on_event("startup")
    async def startup_event():
        await initialize_system(app)
        app.state.appointment_cleanup_stop = asyncio.Event()
        app.state.appointment_cleanup_task = asyncio.create_task(
            appointment_draft_cleanup_loop(app.state.appointment_cleanup_stop)
        )

    @app.on_event("shutdown")
    async def shutdown_event():
        stop_event = getattr(app.state, "appointment_cleanup_stop", None)
        task = getattr(app.state, "appointment_cleanup_task", None)
        if stop_event is not None:
            stop_event.set()
        if task is not None:
            await task
        # Phase F F2：关闭共享容器（释放数据库与索引资源）
        try:
            from api.chat_handler import get_container

            get_container().close()
        except Exception:
            pass

    return app


# 创建应用实例
app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=_env_int("PORT", 8001),
    )
