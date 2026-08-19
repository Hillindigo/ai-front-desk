"""
Database Module

数据库模块，包含：
- 数据模型定义
- 数据访问对象 (Repository)
- 数据库路由器
- 会话管理
"""

from .db_router import DatabaseRouter, TechnicianDBRouter
from .repositories import TechnicianRepository, KnowledgeRepository, UserBehaviorRepository, ConversationRepository, AppointmentRepository
from .base import SessionManager
from .models import (
    Base, Technician, TechnicianSchedule, 
    KnowledgeDocument, UserBehavior, UserPreference, UserRecommendation
)

__all__ = [
    # 主要入口
    'DatabaseRouter',
    
    # 兼容性路由器（A-R2 user_behavior 收口后删除）
    'TechnicianDBRouter',

    # Repository模式
    'TechnicianRepository',
    'KnowledgeRepository', 
    'UserBehaviorRepository',
    'ConversationRepository',
    'AppointmentRepository',
    
    # 基础设施
    'SessionManager',
    
    # 数据模型
    'Base',
    'Technician',
    'TechnicianSchedule',
    'KnowledgeDocument',
    'UserBehavior',
    'UserPreference',
    'UserRecommendation'
]
