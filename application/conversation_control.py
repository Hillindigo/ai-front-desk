"""会话控制状态解析（Phase H H3）。

买家端（orchestrator）用本模块判断会话是否处于"人工接管中"或"待人工"，
从而在人工接管期间禁止 AI 自动继续处理同一会话，避免 AI/人工双重回复。

状态（ConversationControl.mode）：
- ai_active：AI 正常处理；
- human_active：人工已接管，AI 不得继续；
- awaiting_human：买家已请求转人工，AI 暂停，商家待处理队列可见。

买家发起转人工无 merchant actor，故只改 mode 而不写 ConversationControlEvent
（其 actor_id 指向商家账号）；商家接管/恢复/人工回复时才写不可变事件与审计。
"""

from __future__ import annotations

from typing import Any

from db.models import ConversationControl

HUMAN_MODES = ("human_active", "awaiting_human")


class ConversationControlResolver:
    def __init__(self, db_router: Any):
        self._router = db_router

    def _session_scope(self):
        return self._router.session_manager.session_scope()

    def mode(self, conversation_id: str) -> str:
        """返回会话当前控制模式；无控制记录视为 ai_active。"""
        with self._session_scope() as session:
            control = session.query(ConversationControl).filter_by(
                conversation_id=conversation_id
            ).first()
            return control.mode if control is not None else "ai_active"

    def ai_blocked(self, conversation_id: str) -> bool:
        """AI 是否应被阻断：人工接管中或待人工。"""
        return self.mode(conversation_id) in HUMAN_MODES

    def demand_hint(self, conversation_id: str) -> str:
        """人工接管/待人工状态下，买家 turn 应看到的稳定提示。"""
        return (
            "当前会话已由人工客服接管，客服将为您继续服务，请稍候。"
            if self.mode(conversation_id) == "human_active"
            else "已为您转接人工客服，客服会尽快回复您。"
        )

    def request_human(self, conversation_id: str) -> None:
        """买家请求转人工：ai_active -> awaiting_human。

        已在人工/待人工状态则保持；不改动商家事件与审计（无 merchant actor）。
        """
        with self._session_scope() as session:
            control = session.query(ConversationControl).filter_by(
                conversation_id=conversation_id
            ).first()
            if control is None:
                from db.models import Conversation

                conv = session.query(Conversation).filter_by(id=conversation_id).first()
                if conv is None:
                    return
                control = ConversationControl(
                    conversation_id=conversation_id,
                    store_id=conv.store_id,
                    mode="awaiting_human",
                )
                session.add(control)
            elif control.mode == "ai_active":
                control.mode = "awaiting_human"
