"""
FastAPI应用程序

主应用程序入口，配置中间件、路由和异常处理
自动初始化知识库和服务人员数据
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from services.technician_service import TechnicianService
from services.recommendation_service import RecommendationService
from services.appointment_cleanup import appointment_draft_cleanup_loop
from config.settings import settings
from api.core.security import SecurityHeadersMiddleware, RateLimitMiddleware
from typing import List, Optional
import logging
import asyncio
import os

# 导入路由
from api import api_routers
from api.core.exceptions import (
    api_exception_handler, general_exception_handler, BusinessException,
    request_validation_handler,
)
from fastapi.exceptions import RequestValidationError
from web import router as web_router

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Pydantic模型
from pydantic import BaseModel

class KnowledgeRequest(BaseModel):
    content: str
    category: str
    keywords: List[str] = []

class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    category: Optional[str] = None

async def initialize_system(app: Optional[FastAPI] = None):
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
        logger.error(f"❌ 系统初始化失败（应用仍可启动，依赖将不可用）: {type(e).__name__}")
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
        redoc_url="/redoc"
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
        max_requests=int(os.getenv("RATE_LIMIT_PER_MIN", "600") or "600"),
        window_seconds=int(os.getenv("RATE_LIMIT_WINDOW", "60") or "60"),
        enabled=os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower()
        in {"1", "true", "yes", "on"},
    )

    # 注册异常处理器
    app.add_exception_handler(BusinessException, api_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)
    # Phase I I1-E4：校验错误不回显输入值（防密钥/PII 经 422 回显泄漏）
    app.add_exception_handler(RequestValidationError, request_validation_handler)

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
    uvicorn.run(app, host="127.0.0.1", port=8001)
