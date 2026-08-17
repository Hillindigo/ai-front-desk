"""应用容器（Phase D D3）。

应用启动时组装一次依赖，测试通过覆盖注入（临时数据库 / Fake LLM / Fake
BehaviorRecorder）。业务服务不再在请求内隐式创建数据库连接或全局单例。

组装：SessionManager/Repository -> 领域服务 -> IntentRouter -> Workflows
      -> ConversationOrchestrator -> 行为旁路记录器
"""

import logging
from typing import Any, Dict, Optional

from application.contracts import IntentClassification, IntentType
from application.orchestrator import ConversationOrchestrator, IntentRouter
from application.workflows import AppointmentWorkflow, ConsultationWorkflow, UnrelatedWorkflow
from db.db_router import DatabaseRouter
from services.appointment_domain import AppointmentCommandService

logger = logging.getLogger(__name__)


class Container:
    """应用依赖容器（单进程内单例）。"""

    def __init__(
        self,
        db_path: Optional[str] = None,
        llm_classifier: Optional[Any] = None,
        behavior_recorder: Optional[Any] = None,
        agent_factory: Optional[Any] = None,
    ):
        self.db_path = db_path
        self.db_router = DatabaseRouter(db_path)
        # 会话运行时管理器（ConversationSession 缓存/恢复）复用同一 db_router
        from application.session_runtime import SessionManager as RuntimeSessionManager

        self.session_manager = RuntimeSessionManager(self.db_router)

        # 领域服务
        self.appointment_service = AppointmentCommandService(self.db_router)

        # 意图路由（规则优先；LLM 兜底由外部注入）
        self.intent_router = IntentRouter(llm_classifier=llm_classifier)

        # 会话专属 Agent 工厂（默认使用 chat_handler 的实现，惰性避免循环导入）
        if agent_factory is None:
            from api.chat_handler import get_task_agent_for as _default_agent_factory
            agent_factory = _default_agent_factory

        self.workflows = {
            IntentType.APPOINTMENT: AppointmentWorkflow(),
            IntentType.CONSULTATION: ConsultationWorkflow(),
            IntentType.UNRELATED: UnrelatedWorkflow(),
        }
        self.orchestrator = ConversationOrchestrator(
            session_manager=self.session_manager,
            router=self.intent_router,
            workflows=self.workflows,
            agent_factory=agent_factory,
            behavior_recorder=behavior_recorder,
        )

    def close(self) -> None:
        self.db_router.close()

    # ---------------- 快捷访问 ----------------

    @property
    def repository(self):
        return self.db_router.conversations
