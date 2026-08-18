"""
简化的API模块
# 创建API路由列表（用于注册到FastAPI应用）
api_routers = [
    appointment_router,
    consultation_router,
    task_router,
    knowledge_router,
    technician_router,
    user_behavior_analysis_router,
    user_behavior_analysis_underscore_router
]心功能API
管理员功能已移至scripts目录
"""

# 导入各业务模块的路由
from .appointment import router as appointment_router
from .consultation import router as consultation_router
from .task import router as task_router
from .knowledge import router as knowledge_router
from .technician import router as technician_router
from .user_behavior_analysis import router as user_behavior_analysis_router
from .user_behavior_analysis import router_underscore as user_behavior_analysis_underscore_router
from .conversations import router as conversations_router
from .appointments import router as appointments_router
from .preferences import router as preferences_router
from .knowledge_v1 import router as knowledge_v1_router
from .admin_auth import router as admin_auth_router
from .admin_config import router as admin_config_router
from .admin_conversations import router as admin_conversations_router

# 创建API路由列表（用于注册到FastAPI应用）
api_routers = [
    appointment_router,
    consultation_router,
    task_router,
    knowledge_router,
    technician_router,
    user_behavior_analysis_router,
    user_behavior_analysis_underscore_router,
    conversations_router,
    appointments_router,
    preferences_router,
    knowledge_v1_router,
    admin_auth_router,
    admin_config_router,
    admin_conversations_router,
]
