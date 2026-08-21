"""
Web界面路由

处理前端页面渲染和聊天功能
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from api.admin_auth import get_current_admin, require_permission
from api.chat_handler import ProcessUserInput_stream

# 创建logger实例
logger = logging.getLogger(__name__)
# 模板配置
templates = Jinja2Templates(directory="web/templates")


def _render_template(name: str, context: dict):
    """Render a Jinja template using the current Starlette API."""
    return templates.TemplateResponse(
        request=context["request"], name=name, context=context
    )


# Web路由器
router = APIRouter(tags=["Web界面"])


class ChatRequest(BaseModel):
    message: str
    state: str | None = None
    conversation_id: str | None = None  # Phase B：显式会话 ID（可选）
    user_id: str = "default_user"


@router.get("/", response_class=HTMLResponse, summary="主页")
async def read_root(request: Request):
    """渲染主页聊天界面"""
    return _render_template("index.html", {"request": request})


@router.post("/chat/stream", summary="流式聊天")
def chat_stream_endpoint(chat: ChatRequest):
    """处理流式聊天请求（Phase B 兼容包装：转发到显式会话或默认演示会话）"""

    async def token_generator():
        async for token in ProcessUserInput_stream(
            chat.message,
            conversation_id=chat.conversation_id,
            user_id=chat.user_id,
        ):
            yield token

    return StreamingResponse(token_generator(), media_type="text/plain")


@router.get("/user_behavior", response_class=HTMLResponse, summary="用户行为分析页面")
async def user_behavior_page(
    request: Request,
    identity=Depends(get_current_admin),
):
    """用户行为分析页面"""
    return _render_template("user_behavior_analysis.html", {"request": request})


@router.get("/knowledge", response_class=HTMLResponse, summary="知识库管理页面")
async def knowledge_page(
    request: Request,
    identity=Depends(require_permission("view_knowledge")),
):
    """知识库管理页面"""
    try:
        from api.chat_handler import get_container

        kb, _, _ = get_container().get_knowledge_bundle(
            int(identity["active_store"]["store_id"])
        )
        documents = kb.get_all_documents()
        categories = kb.get_all_categories()

        return _render_template(
            "knowledge_management.html",
            {"request": request, "documents": documents, "categories": categories},
        )
    except Exception as e:
        return _render_template(
            "knowledge_management.html",
            {"request": request, "documents": [], "categories": [], "error": str(e)},
        )


@router.get("/technician", response_class=HTMLResponse, summary="服务人员状态页面")
async def technician_page(
    request: Request,
    identity=Depends(get_current_admin),
):
    """服务人员状态页面"""
    # 通过API层获取服务人员数据
    try:
        from api.chat_handler import get_container

        technicians = get_container().db_router.technicians.get_all_technicians(
            store_id=int(identity["active_store"]["store_id"])
        )

        return _render_template(
            "technician.html", {"request": request, "technicians": technicians}
        )
    except Exception as e:
        return _render_template(
            "technician.html", {"request": request, "technicians": [], "error": str(e)}
        )


@router.get(
    "/technician_schedule", response_class=HTMLResponse, summary="服务人员排班页面"
)
async def technician_schedule_page(
    request: Request,
    identity=Depends(get_current_admin),
):
    """服务人员排班页面"""
    try:
        from api.chat_handler import get_container
        from config.time_config import time_config

        # 获取当前日期
        current_date = time_config.current_date_str()

        container = get_container()
        store_id = int(identity["active_store"]["store_id"])
        technicians = container.db_router.technicians.get_all_technicians(
            store_id=store_id
        )
        today = time_config.today()
        schedules_data = []
        for tech in technicians:
            schedules = container.db_router.technicians.get_technician_schedules(
                tech["id"], today
            )
            schedules_data.append(
                {
                    "technician_id": tech["id"],
                    "technician_name": tech["name"],
                    "busy_periods": [
                        {
                            "start": s["start_time"].strftime("%H:%M"),
                            "end": s["end_time"].strftime("%H:%M"),
                            "appointment_id": s.get("appointment_id"),
                        }
                        for s in schedules
                        if s.get("status") == "busy"
                    ],
                }
            )

        # 构建排班数据格式 - 直接使用API返回的数据
        schedule = []
        for schedule_item in schedules_data:
            schedule.append(
                {
                    "id": schedule_item["technician_id"],
                    "name": schedule_item["technician_name"],
                    "busy_periods": schedule_item["busy_periods"],
                }
            )

        return _render_template(
            "technician_schedule.html",
            {"request": request, "schedule": schedule, "current_date": current_date},
        )
    except Exception as e:
        logger.error(f"加载服务人员排班数据失败: {str(e)}")
        return _render_template(
            "technician_schedule.html",
            {"request": request, "schedule": [], "error": str(e)},
        )


@router.get(
    "/user_behavior_analysis", response_class=HTMLResponse, summary="用户行为分析页面"
)
async def user_behavior_analysis_page(
    request: Request,
    identity=Depends(get_current_admin),
):
    """用户行为分析页面"""
    return _render_template("user_behavior_analysis.html", {"request": request})


@router.get("/admin/login", response_class=HTMLResponse, summary="商家登录页面")
async def admin_login_page(request: Request):
    """商家后台登录入口；登录动作由 /api/v1/admin/auth/login 完成。"""
    return _render_template("admin_login.html", {"request": request})


@router.get("/admin", response_class=HTMLResponse, summary="系统管理页面")
async def admin_dashboard(
    request: Request,
    identity=Depends(get_current_admin),
):
    """系统管理仪表板；未登录时由统一认证依赖返回 401。"""
    try:
        from api.chat_handler import get_container

        container = get_container()
        store_id = int(identity["active_store"]["store_id"])
        knowledge_repo = container.db_router.knowledge
        knowledge_count = knowledge_repo.get_documents_count(store_id=store_id)
        categories = knowledge_repo.get_all_categories(store_id=store_id)
        technicians = container.db_router.technicians.get_all_technicians(
            store_id=store_id
        )

        # 数据库信息
        db_info = {
            "knowledge_count": knowledge_count,
            "categories_count": len(categories),
            "technicians_count": len(technicians),
            "categories": categories,
        }

        return _render_template(
            "admin_dashboard.html",
            {
                "request": request,
                "db_info": db_info,
                "technicians": technicians[:5],
                "actor": identity["actor"],
                "active_store": identity["active_store"],
                "role": identity["role"],
            },
        )
    except Exception as e:
        return _render_template(
            "admin_dashboard.html",
            {
                "request": request,
                "db_info": {},
                "technicians": [],
                "actor": identity["actor"],
                "active_store": identity["active_store"],
                "role": identity["role"],
                "error": str(e),
            },
        )


@router.get("/admin/workbench", response_class=HTMLResponse, summary="会话工作台页面")
async def admin_workbench_page(
    request: Request,
    identity=Depends(get_current_admin),
):
    """商家会话工作台（H3）：列表/详情/接管/人工回复/恢复AI。"""
    return _render_template(
        "admin_workbench.html",
        {
            "request": request,
            "actor": identity["actor"],
            "active_store": identity["active_store"],
            "role": identity["role"],
        },
    )


@router.get("/admin/database", response_class=HTMLResponse, summary="数据库管理页面")
async def database_admin_page(
    request: Request,
    identity=Depends(get_current_admin),
):
    """数据库管理页面"""
    try:
        from api.chat_handler import get_container

        container = get_container()
        store_id = int(identity["active_store"]["store_id"])
        knowledge_repo = container.db_router.knowledge
        knowledge_count = knowledge_repo.get_documents_count(store_id=store_id)
        categories = knowledge_repo.get_all_categories(store_id=store_id)
        technicians = container.db_router.technicians.get_all_technicians(
            store_id=store_id
        )

        stats = {
            "knowledge_documents": knowledge_count,
            "categories": len(categories),
            "technicians": len(technicians),
            "appointments": 0,  # TODO: 通过API获取预约数量
        }

        return _render_template(
            "database_admin.html", {"request": request, "stats": stats}
        )
    except Exception as e:
        return _render_template(
            "database_admin.html", {"request": request, "stats": {}, "error": str(e)}
        )
