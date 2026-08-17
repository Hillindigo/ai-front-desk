"""业务工作流适配器（Phase D D2）。

每个工作流独立处理一类意图：
- AppointmentWorkflow：明确命令（取消/确认/改约）走确定性领域服务；
  常规预约走会话专属 AppointmentAgent（其写入已由 Phase C 领域服务收口）。
- ConsultationWorkflow：包装 ConsultantAgent。
- UnrelatedWorkflow：确定性回复，不调用 LLM/业务库。

工作流之间不互相调用，不直接写数据库。
"""

import logging
from typing import Any, AsyncGenerator, Dict, Optional

from application.contracts import AppointmentSubAction, IntentClassification, IntentType
from services.appointment_domain import AppointmentCommandService, AppointmentDomainError

logger = logging.getLogger(__name__)


def _cancel_reply(svc: AppointmentCommandService, conversation_id: str, user_id: str) -> str:
    """取消：优先取消会话活跃草稿，否则取消最近 confirmed 预约。"""
    draft = svc.get_active_draft(conversation_id)
    target = None
    if draft is not None:
        target = draft
    else:
        confirmed = svc.repo.list_by_user(user_id, status="confirmed", limit=1)
        if confirmed:
            target = confirmed[0]
    if target is None:
        return "[REPLY][预约机器人]您当前没有可取消的预约。"
    try:
        svc.cancel(target["id"], user_id, reason="用户取消")
        name = target.get("service_type") or target.get("project") or "预约"
        return f"[REPLY][预约机器人]已为您取消{name}的预约。"
    except AppointmentDomainError:
        raise


def _confirm_reply(svc: AppointmentCommandService, conversation_id: str, user_id: str) -> str:
    """确认：会话活跃草稿（draft/pending_confirmation）走确认命令。"""
    draft = svc.get_active_draft(conversation_id)
    if draft is None:
        return "[REPLY][预约机器人]当前没有待确认的预约。"
    try:
        if draft["status"] == "pending_confirmation":
            svc.confirm(draft["id"], user_id, idempotency_key=f"{conversation_id}:{draft['id']}")
            return "[REPLY][预约机器人]预约确认成功！"
        if draft["status"] == "draft":
            pending = svc.request_confirmation(draft["id"], user_id)
            svc.confirm(pending["id"], user_id, idempotency_key=f"{conversation_id}:{pending['id']}")
            return "[REPLY][预约机器人]预约确认成功！"
        return "[REPLY][预约机器人]该预约状态无法确认。"
    except AppointmentDomainError:
        raise


class AppointmentWorkflow:
    """预约工作流。"""

    def __init__(self, appointment_service: Optional[AppointmentCommandService] = None):
        # 生产容器注入共享的无状态领域服务；保留默认构造仅供旧测试/兼容调用。
        self.appointment_service = appointment_service

    async def run(
        self,
        session: Any,
        user_input: str,
        intent: IntentClassification,
        user_id: str = "default_user",
    ) -> AsyncGenerator[str, None]:
        sub = intent.sub_action
        if sub == AppointmentSubAction.CANCEL:
            owns_service = self.appointment_service is None
            svc = self.appointment_service or AppointmentCommandService()
            try:
                yield _cancel_reply(svc, session.conversation_id, user_id)
            finally:
                if owns_service:
                    svc.close()
            # 取消成功（或无可取消）后重置预约上下文，避免 sync_draft 复活草稿
            self._clear_history(session)
            return
        if sub == AppointmentSubAction.CONFIRM:
            owns_service = self.appointment_service is None
            svc = self.appointment_service or AppointmentCommandService()
            try:
                yield _confirm_reply(svc, session.conversation_id, user_id)
            finally:
                if owns_service:
                    svc.close()
            # 确认成功后预约不再活跃，重置上下文（sync_draft 不会复活草稿）
            self._clear_history(session)
            return
        if sub == AppointmentSubAction.RESCHEDULE:
            # 改约需要新时间：引导用户提供（确定性提示，不猜测）
            yield ("[REPLY][预约机器人]好的，改约需要您提供新的预约时间（例如：明天下午3点）。\n")
            return
        # 常规预约：走会话专属 AppointmentAgent（领域服务已收口写入）
        appointment_agent = session.agent.appointment_agent
        async for token in appointment_agent.run_stream(user_input):
            yield token

    @staticmethod
    def _clear_history(session) -> None:
        """清空会话预约 Agent 的预约上下文（取消/确认命令后）。"""
        if session.agent is not None and getattr(session.agent, "appointment_agent", None):
            session.agent.appointment_agent.appointment_history.clear()


class ConsultationWorkflow:
    """咨询工作流：包装 ConsultantAgent（不管理数据库连接）。"""

    async def run(
        self,
        session: Any,
        user_input: str,
        intent: IntentClassification,
        user_id: str = "default_user",
    ) -> AsyncGenerator[str, None]:
        consultant_agent = session.agent.consultant_agent
        async for token in consultant_agent.consult_stream(user_input):
            yield token


class UnrelatedWorkflow:
    """无关请求：确定性回复。"""

    async def run(
        self,
        session: Any,
        user_input: str,
        intent: IntentClassification,
        user_id: str = "default_user",
    ) -> AsyncGenerator[str, None]:
        yield "[REPLY][归类机器人]暂不支持该类型任务。请询问门店服务、预约、排班或客户服务相关问题。\n"
