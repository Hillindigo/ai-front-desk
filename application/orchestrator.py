"""ConversationOrchestrator / IntentRouter（Phase D D2）。

- IntentRouter：规则优先（application/intent_rules.py），模糊/未命中才 LLM 兜底。
- ConversationOrchestrator：唯一会话轮次编排入口。一轮请求只经过 Orchestrator：
  校验会话 -> 落用户消息 -> 分类 -> 工作流 -> 落 assistant 消息。
  不直接写数据库（经 repository/领域服务）；工作流之间不互相调用。
  事件流是单轮视角（决策二）：跨轮状态由持久化草稿/预约实体承载。
"""

import logging
import uuid
from typing import Any, AsyncGenerator, Callable, Dict, Optional

from application.contracts import (
    EventEnvelope,
    EventType,
    IntentClassification,
    IntentType,
    ErrorCode,
    map_appointment_error,
)
from application.events import EventStream, clean_token
from application.intent_rules import match_intent
from application.workflows import AppointmentWorkflow, ConsultationWorkflow, UnrelatedWorkflow
from services.appointment_domain import AppointmentDomainError

logger = logging.getLogger(__name__)


class IntentRouter:
    """规则优先 + LLM 兜底分类（决策一：明确命令命中规则时不调用 LLM）。"""

    def __init__(self, llm_classifier: Optional[Callable[[str, Dict[str, Any]], Any]] = None):
        self._llm_classifier = llm_classifier  # async (text, session_context) -> IntentClassification

    def classify_rule(self, text: str) -> IntentClassification:
        """纯规则分类（测试用）。"""
        return match_intent(text)

    async def classify(self, text: str, session_context: Optional[Dict[str, Any]] = None) -> IntentClassification:
        rule_result = match_intent(text)
        if rule_result.intent != IntentType.UNKNOWN and not rule_result.requires_clarification:
            return rule_result
        if self._llm_classifier is not None:
            try:
                return await self._llm_classifier(text, session_context or {})
            except Exception:
                logger.exception("LLM 分类失败，回退规则结果")
        return rule_result


class ConversationOrchestrator:
    """会话轮次编排器。"""

    def __init__(
        self,
        session_manager,
        router: Optional[IntentRouter] = None,
        workflows: Optional[Dict[IntentType, Any]] = None,
        agent_factory: Optional[Callable[[Any], Any]] = None,
        behavior_recorder: Optional[Any] = None,
    ):
        self.session_manager = session_manager
        self.router = router or IntentRouter()
        self.workflows = workflows or {
            IntentType.APPOINTMENT: AppointmentWorkflow(),
            IntentType.CONSULTATION: ConsultationWorkflow(),
            IntentType.UNRELATED: UnrelatedWorkflow(),
        }
        self.agent_factory = agent_factory
        self.behavior_recorder = behavior_recorder  # 旁路记录器（D6 接入）

    def _ensure_agent(self, session) -> None:
        if session.agent is None and self.agent_factory is not None:
            session.agent = self.agent_factory(session)

    @staticmethod
    def _tool_name(intent: IntentClassification) -> str:
        if intent.intent == IntentType.APPOINTMENT:
            return f"appointment_{intent.sub_action.value}" if intent.sub_action.value != "none" else "appointment_draft"
        if intent.intent == IntentType.CONSULTATION:
            return "consultation_answer"
        return "unrelated_reply"

    @staticmethod
    def _safe_error_message(code: ErrorCode) -> str:
        return {
            ErrorCode.APPOINTMENT_CONFLICT: "该时间段已被预约，请选择其他时间。",
            ErrorCode.APPOINTMENT_STATE_INVALID: "当前预约状态无法执行此操作。",
            ErrorCode.IDEMPOTENCY_CONFLICT: "请求标识已被其他内容使用。",
            ErrorCode.TOOL_FAILED: "业务工具暂时不可用，请稍后再试。",
            ErrorCode.INTERNAL_ERROR: "处理失败，请稍后再试。",
        }.get(code, "处理失败，请稍后再试。")

    async def handle_turn(
        self,
        conversation_id: str,
        user_id: str,
        user_input: str,
        request_id: Optional[str] = None,
    ) -> AsyncGenerator[EventEnvelope, None]:
        """执行一轮会话，输出事件流（D4）。

        事件流只描述当前轮次（决策二）：run_started 首发，唯一
        run_completed/run_failed 终止；跨轮预约状态由持久化草稿承载。
        """
        session = self.session_manager.get_or_create_session(conversation_id, user_id)
        stream = EventStream(
            run_id=request_id or str(uuid.uuid4()),
            conversation_id=conversation_id,
        )

        async with session.lock:
            # 0. 重试去重：相同请求 ID 返回已持久化结果，不重复写入消息或执行工具。
            existing = self.session_manager.repository.get_turn_by_request_id(
                conversation_id, request_id
            ) if request_id else None
            if existing is not None:
                previous_user = existing["user"]
                previous_assistant = existing.get("assistant")
                stream = EventStream(run_id=request_id, conversation_id=conversation_id)
                yield stream.next(EventType.RUN_STARTED, {"request_id": request_id, "replayed": True})
                if previous_user.get("content") != user_input:
                    stream.mark_terminal()
                    yield stream.next(EventType.RUN_FAILED, {
                        "error": ErrorCode.IDEMPOTENCY_CONFLICT.value,
                        "message": self._safe_error_message(ErrorCode.IDEMPOTENCY_CONFLICT),
                    })
                    return
                content = (previous_assistant or {}).get("content", "")
                if content:
                    yield stream.next(EventType.ASSISTANT_DELTA, {"text": content, "replayed": True})
                stream.mark_terminal()
                yield stream.next(EventType.RUN_COMPLETED, {
                    "conversation_id": conversation_id,
                    "message_id": (previous_assistant or {}).get("id"),
                    "replayed": True,
                })
                return

            # 1. 用户消息先落库（决策二：先落库再调模型）
            user_msg = self.session_manager.repository.add_message(
                conversation_id,
                "user",
                user_input,
                metadata={"client_request_id": request_id} if request_id else None,
            )
            if user_msg:
                session.append_message(user_msg)

            yield stream.next(EventType.RUN_STARTED, {"request_id": request_id} if request_id else None)

            try:
                # 2. 分类（规则优先 + LLM 兜底）
                intent = await self.router.classify(
                    user_input,
                    {"conversation_id": conversation_id},
                )
                yield stream.next(EventType.INTENT_DETECTED, intent.to_dict())

                # 3. 选择工作流并执行
                self._ensure_agent(session)
                workflow = self.workflows.get(intent.intent)
                tool_name = self._tool_name(intent)
                if workflow is not None:
                    yield stream.next(EventType.WORKFLOW_STARTED, {"workflow": intent.intent.value})
                    yield stream.next(EventType.TOOL_STARTED, {"tool": tool_name})

                collected: list = []
                if workflow is None:
                    fallback = "暂不支持该类型任务。请询问门店服务、预约、排班或客户服务相关问题。\n"
                    collected.append(fallback)
                    yield stream.next(EventType.ASSISTANT_DELTA, {"text": fallback})
                else:
                    async for token in workflow.run(session, user_input, intent, user_id):
                        clean = clean_token(token)
                        if clean is None:
                            continue  # [THOUGHT]/[SIGNAL] 不外泄（隐藏推理不暴露）
                        collected.append(clean)
                        yield stream.next(EventType.ASSISTANT_DELTA, {"text": clean})

                # 4. assistant 完整消息落库（增量不是事实来源；内容为清洗后文本）
                full_response = "".join(collected)
                assistant_msg = self.session_manager.repository.add_message(
                    conversation_id,
                    "assistant",
                    full_response,
                    metadata={
                        "client_request_id": request_id,
                        "intent": intent.to_dict(),
                    } if request_id else {"intent": intent.to_dict()},
                )
                if assistant_msg:
                    session.append_message(assistant_msg)

                # 5. 预约草稿同步（Phase C C5 行为保持）
                self._sync_appointment_draft(session)

                if workflow is not None:
                    yield stream.next(EventType.TOOL_RESULT, {
                        "tool": tool_name,
                        "success": True,
                    })

                stream.mark_terminal()
                yield stream.next(EventType.RUN_COMPLETED, {
                    "conversation_id": conversation_id,
                    "message_id": assistant_msg["id"] if assistant_msg else None,
                    "intent": intent.to_dict(),
                })

                # 6. 行为记录（旁路：失败不影响主流程，D6）
                if self.behavior_recorder is not None:
                    try:
                        self.behavior_recorder.record(
                            user_id, conversation_id, intent.intent.value,
                            {"sub_action": intent.sub_action.value, "run_id": stream.run_id},
                        )
                    except Exception:
                        logger.warning("行为记录旁路异常", exc_info=True)
            except AppointmentDomainError as exc:
                logger.warning("预约领域操作失败: %s", exc.code)
                code = map_appointment_error(exc.code)
                tool_name = self._tool_name(intent) if "intent" in locals() else "appointment"
                yield stream.next(EventType.TOOL_RESULT, {
                    "tool": tool_name,
                    "success": False,
                    "error": code.value,
                })
                stream.mark_terminal()
                yield stream.next(EventType.RUN_FAILED, {
                    "error": code.value,
                    "message": self._safe_error_message(code),
                })
            except Exception as exc:
                # 唯一失败终止事件；不伪造 run_completed
                logger.exception("编排轮次失败")
                if "tool_name" in locals():
                    yield stream.next(EventType.TOOL_RESULT, {
                        "tool": tool_name,
                        "success": False,
                        "error": ErrorCode.TOOL_FAILED.value,
                    })
                stream.mark_terminal()
                yield stream.next(EventType.RUN_FAILED, {
                    "error": "INTERNAL_ERROR",
                    "message": "处理失败，请稍后再试。",
                })

    def _sync_appointment_draft(self, session) -> None:
        """预约对话进行中 -> 同步持久化草稿（Phase C C5）。"""
        if session.agent is None or not getattr(session.agent, "appointment_agent", None):
            return
        try:
            session.agent.appointment_agent.appointment_database.sync_draft(
                session.conversation_id,
                session.agent.appointment_agent.appointment_history,
            )
        except Exception:
            logger.exception("同步预约草稿失败（旁路）")
