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

        # Phase E：身份解析与偏好服务（决策三：请求体 user_id 只作兼容字段）
        from application.identity import DemoIdentityResolver
        from db.repositories.preference_repository import PreferenceRepository
        from services.preference_service import PreferenceService

        self.identity_resolver = DemoIdentityResolver()
        self.preference_repository = PreferenceRepository(self.db_router.session_manager)
        self.preference_service = PreferenceService(self.preference_repository)

        # 意图路由（规则优先；LLM 兜底由外部注入）
        self.intent_router = IntentRouter(llm_classifier=llm_classifier)

        # 行为旁路记录器（D6：失败不阻断主流程）
        from application.behavior import BehaviorRecorder

        self.behavior_recorder = behavior_recorder or BehaviorRecorder()

        # 会话专属 Agent 工厂（默认使用 chat_handler 的实现，惰性避免循环导入）
        if agent_factory is None:
            from api.chat_handler import get_task_agent_for as _default_agent_factory
            agent_factory = _default_agent_factory

        self.workflows = {
            IntentType.APPOINTMENT: AppointmentWorkflow(self.appointment_service),
            IntentType.CONSULTATION: ConsultationWorkflow(),
            IntentType.UNRELATED: UnrelatedWorkflow(),
        }

        # Phase E E5：统一上下文装配（只读读取器 -> ContextBuilder -> Orchestrator）
        from application.context_builder import ContextBuilder
        from application.context_readers import (
            KnowledgeEvidenceReader,
            RepositoryAppointmentReader,
            RepositoryMessageReader,
            RepositorySummaryReader,
            ServicePreferenceReader,
        )
        from db.repositories.summary_repository import SummaryRepository
        from services.summary_service import SummaryService
        from services.knowledge_service import KnowledgeService

        self.summary_repository = SummaryRepository(self.db_router.session_manager)
        self.context_builder = ContextBuilder(
            message_reader=RepositoryMessageReader(self.db_router.conversations),
            appointment_reader=RepositoryAppointmentReader(self.db_router.appointments),
            preference_reader=ServicePreferenceReader(self.preference_service),
            summary_reader=RepositorySummaryReader(self.summary_repository),
            evidence_reader=KnowledgeEvidenceReader(KnowledgeService()),
        )
        self.summary_service = SummaryService(
            repository=self.summary_repository,
            message_reader=RepositoryMessageReader(self.db_router.conversations),
            appointment_reader=RepositoryAppointmentReader(self.db_router.appointments),
        )

        self.orchestrator = ConversationOrchestrator(
            session_manager=self.session_manager,
            router=self.intent_router,
            workflows=self.workflows,
            agent_factory=agent_factory,
            behavior_recorder=self.behavior_recorder,
            context_builder=self.context_builder,
            summary_service=self.summary_service,
            preference_service=self.preference_service,
        )

    def close(self) -> None:
        self.db_router.close()

    # ---------------- 快捷访问 ----------------

    @property
    def repository(self):
        return self.db_router.conversations
