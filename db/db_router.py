from .base import SessionManager
from .repositories import (
    TechnicianRepository,
    KnowledgeRepository,
    UserBehaviorRepository,
    ConversationRepository,
    AppointmentRepository,
)
from typing import Optional


class DatabaseRouter:
    """
    数据库路由器

    职责：
    1. 管理数据库连接和会话
    2. 提供统一的数据访问入口
    3. 协调各个Repository的操作
    """

    def __init__(self, db_path: str | None = None):  # Phase B 决策一：None 时取 db_config
        """
        初始化数据库路由器

        Args:
            db_path: 数据库连接路径
        """
        self.session_manager = SessionManager(db_path)

        # 初始化各个Repository
        self.technician_repo = TechnicianRepository(self.session_manager)
        self.knowledge_repo = KnowledgeRepository(self.session_manager)
        from .repositories.preference_repository import PreferenceRepository

        self.preference_repo = PreferenceRepository(self.session_manager)
        # Phase E E4：旧 user_behavior 偏好读写收敛到新表（避免两套事实并存）
        self.user_behavior_repo = UserBehaviorRepository(self.session_manager, preference_repository=self.preference_repo)
        self.conversation_repo = ConversationRepository(self.session_manager)
        self.appointment_repo = AppointmentRepository(self.session_manager)

    @property
    def technicians(self) -> TechnicianRepository:
        """获取服务人员数据仓库"""
        return self.technician_repo

    @property
    def knowledge(self) -> KnowledgeRepository:
        """获取知识库数据仓库"""
        return self.knowledge_repo

    @property
    def user_behavior(self) -> UserBehaviorRepository:
        """获取用户行为数据仓库"""
        return self.user_behavior_repo

    @property
    def conversations(self) -> ConversationRepository:
        """获取会话与消息数据仓库（Phase B）"""
        return self.conversation_repo

    @property
    def appointments(self) -> AppointmentRepository:
        """获取预约数据仓库（Phase C）"""
        return self.appointment_repo

    def close(self):
        """关闭数据库连接"""
        self.session_manager.close()


# 为了兼容性，保留原有的类名
class TechnicianDBRouter:
    """
    服务人员数据库路由器（兼容性类，Phase D D7 弃用）

    仅剩 A-R2 user_behavior 组件使用；Phase E/D 收口后删除。
    """
    
    def __init__(self, db_type='local', **kwargs):
        self.db_router = DatabaseRouter(**kwargs)
        self.technician_repo = self.db_router.technicians

    # 服务人员相关方法
    def add_technician(self, name, gender=None, strength=None) -> None:
        return self.technician_repo.add_technician(name, gender, strength)

    def get_technician_by_name(self, name: str):
        return self.technician_repo.get_technician_by_name(name)

    def get_technician_by_id(self, technician_id: int):
        return self.technician_repo.get_technician_by_id(technician_id)

    def get_all_technicians(self):
        return self.technician_repo.get_all_technicians()

    def get_all_strengths(self):
        return self.technician_repo.get_all_strengths()

    # 排班相关方法
    def add_schedule(self, technician_id: int, start_time, end_time, status, appointment_id=None) -> None:
        return self.technician_repo.add_schedule(technician_id, start_time, end_time, status, appointment_id)

    def get_technician_schedules(self, technician_id: int, date):
        return self.technician_repo.get_technician_schedules(technician_id, date)

    def is_technician_available(self, technician_id: int, start_time, end_time) -> bool:
        return self.technician_repo.is_technician_available(technician_id, start_time, end_time)

    def get_technicians_by_gender(self, gender: str):
        return self.technician_repo.get_technicians_by_gender(gender)
