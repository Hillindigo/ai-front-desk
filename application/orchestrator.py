"""ConversationOrchestrator / IntentRouter（Phase D D2）。

- IntentRouter：规则优先（application/intent_rules.py），模糊/未命中才 LLM 兜底。
- ConversationOrchestrator：唯一会话轮次编排入口。一轮请求只经过 Orchestrator：
  校验会话 -> 落用户消息 -> 分类 -> 工作流 -> 落 assistant 消息。
  不直接写数据库（经 repository/领域服务）；工作流之间不互相调用。
  事件流是单轮视角（决策二）：跨轮状态由持久化草稿/预约实体承载。
"""

import logging
import re
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
        context_builder: Optional[Any] = None,   # Phase E E5：统一上下文装配
        summary_service: Optional[Any] = None,   # Phase E E5：摘要旁路（失败不阻断）
        preference_service: Optional[Any] = None,  # Phase E E5：偏好命令确定性路由
        chat_control: Optional[Any] = None,     # Phase H H3：会话控制（接管/转人工）
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
        self.context_builder = context_builder
        self.summary_service = summary_service
        self.preference_service = preference_service
        self.chat_control = chat_control

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

            # 1.2 会话控制（Phase H H3）：人工接管/待人工期间禁止 AI 继续；
            # 买家可主动请求转人工。均不触发工作流/模型，避免 AI/人工双重回复。
            if self.chat_control is not None:
                try:
                    if self.chat_control.ai_blocked(conversation_id):
                        hint = self.chat_control.demand_hint(conversation_id)
                        yield stream.next(EventType.HANDOFF_REQUIRED, {"message": hint})
                        handoff_meta = {"intent": {"intent": "handoff"}, "evidence": []}
                        if request_id:
                            handoff_meta["client_request_id"] = request_id
                        h_msg = self.session_manager.repository.add_message(
                            conversation_id, "assistant", hint, metadata=handoff_meta,
                        )
                        if h_msg:
                            session.append_message(h_msg)
                        stream.mark_terminal()
                        yield stream.next(EventType.RUN_COMPLETED, {
                            "conversation_id": conversation_id,
                            "message_id": h_msg["id"] if h_msg else None,
                            "intent": {"intent": "handoff"},
                        })
                        return
                    if self._is_human_request(user_input):
                        self.chat_control.request_human(conversation_id)
                        hint = self.chat_control.demand_hint(conversation_id)
                        yield stream.next(EventType.HANDOFF_REQUIRED, {"message": hint})
                        handoff_meta = {"intent": {"intent": "handoff"}, "evidence": []}
                        if request_id:
                            handoff_meta["client_request_id"] = request_id
                        h_msg = self.session_manager.repository.add_message(
                            conversation_id, "assistant", hint, metadata=handoff_meta,
                        )
                        if h_msg:
                            session.append_message(h_msg)
                        stream.mark_terminal()
                        yield stream.next(EventType.RUN_COMPLETED, {
                            "conversation_id": conversation_id,
                            "message_id": h_msg["id"] if h_msg else None,
                            "intent": {"intent": "handoff"},
                        })
                        return
                except Exception:
                    # 控制状态未知时必须 fail-closed，不能放行 AI，避免与人工回复并发。
                    logger.exception("会话控制检查异常，阻断本轮 AI 处理")
                    stream.mark_terminal()
                    yield stream.next(EventType.RUN_FAILED, {
                        "error": ErrorCode.INTERNAL_ERROR.value,
                        "message": self._safe_error_message(ErrorCode.INTERNAL_ERROR),
                    })
                    return

            try:
                # 1.5 统一上下文装配（Phase E E5：只读、无副作用；失败降级为空上下文）
                context_input: Optional[Dict[str, Any]] = None
                if self.context_builder is not None:
                    try:
                        package = await self.context_builder.build(
                            conversation_id, user_id, user_input,
                            workflow_state={"conversation_id": conversation_id},
                        )
                        context_input = package.model_input()
                    except Exception:
                        logger.exception("上下文装配失败，降级为无上下文")

                # 2. 分类（规则优先 + LLM 兜底；结构化上下文只读传递）
                intent = await self.router.classify(
                    user_input,
                    context_input or {"conversation_id": conversation_id},
                )
                yield stream.next(EventType.INTENT_DETECTED, intent.to_dict())

                # 2.5 偏好命令确定性路由（决策二：不让 LLM 直接写偏好）
                preference_target = self._detect_preference_command(user_input)
                tool_name = self._tool_name(intent)

                if preference_target is not None and self.preference_service is not None:
                    ptype, pvalue = preference_target
                    yield stream.next(EventType.WORKFLOW_STARTED, {"workflow": "preference"})
                    yield stream.next(EventType.TOOL_STARTED, {"tool": "preference_memorize"})
                    try:
                        record = self.preference_service.set_preference(
                            user_id=user_id,
                            preference_type=ptype,
                            preference_value=pvalue,
                            source="explicit_memorize",
                            source_message_id=str(user_msg["id"]) if user_msg else None,
                        )
                        memorized = record["preference_value"]
                        reply = f"我已记住你偏好的{ptype}：{memorized}。后续会按这个偏好为你推荐。"
                    except Exception:
                        logger.exception("偏好保存失败（旁路，不阻断对话）")
                        reply = "这条偏好暂时无法保存，你可以稍后再试。"
                    collected = [reply]
                    yield stream.next(EventType.ASSISTANT_DELTA, {"text": reply})
                    yield stream.next(EventType.TOOL_RESULT, {"tool": "preference_memorize", "success": True})

                # 3. 选择工作流并执行
                elif intent.intent != IntentType.UNKNOWN:
                    self._ensure_agent(session)
                    workflow = self.workflows.get(intent.intent)
                    if workflow is not None:
                        yield stream.next(EventType.WORKFLOW_STARTED, {"workflow": intent.intent.value})
                        yield stream.next(EventType.TOOL_STARTED, {"tool": tool_name})

                    collected: list = []
                    if workflow is None:
                        fallback = "暂不支持该类型任务。请询问门店服务、预约、排班或客户服务相关问题。\n"
                        collected.append(fallback)
                        yield stream.next(EventType.ASSISTANT_DELTA, {"text": fallback})
                    else:
                        async for token in workflow.run(session, user_input, intent, user_id, context=context_input):
                            clean = clean_token(token)
                            if clean is None:
                                continue  # [THOUGHT]/[SIGNAL] 不外泄（隐藏推理不暴露）
                            collected.append(clean)
                            yield stream.next(EventType.ASSISTANT_DELTA, {"text": clean})

                    if workflow is not None:
                        yield stream.next(EventType.TOOL_RESULT, {
                            "tool": tool_name,
                            "success": True,
                        })
                else:
                    collected = ["暂不支持该类型任务。请询问门店服务、预约、排班或客户服务相关问题。\n"]
                    yield stream.next(EventType.ASSISTANT_DELTA, {"text": collected[0]})

                # 4. assistant 完整消息落库（增量不是事实来源；内容为清洗后文本）
                full_response = "".join(collected)
                # F5：证据元数据写入 assistant 消息（引用依据，供审计/前端来源卡片）
                evidence_meta = (context_input or {}).get("retrieved_evidence", [])
                assistant_metadata = {
                    "intent": intent.to_dict(),
                    "evidence": evidence_meta,
                }
                if request_id:
                    assistant_metadata["client_request_id"] = request_id
                assistant_msg = self.session_manager.repository.add_message(
                    conversation_id,
                    "assistant",
                    full_response,
                    metadata=assistant_metadata,
                )
                if assistant_msg:
                    session.append_message(assistant_msg)

                # 4.5 摘要旁路（Phase E E5：会话锁内触发；失败不影响主流程）
                if self.summary_service is not None:
                    try:
                        outcome = await self.summary_service.summarize_if_needed(conversation_id)
                        if outcome not in ("skipped", "no_op"):
                            logger.info("摘要旁路结果: %s conv=%s", outcome, conversation_id)
                    except Exception:
                        logger.exception("摘要旁路异常（不阻断主对话）")

                # 5. 预约草稿同步（Phase C C5 行为保持）
                self._sync_appointment_draft(session)

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

    # ---------------- Phase E E5：偏好命令确定性路由 ----------------

    # 称谓型关键词（值在关键词前，如"王师傅"）：技师/师傅/服务人员
    # 属性型关键词（值在关键词后，如"时间 下午2点"）：时间/时段/上午/下午/晚上/项目/服务/时长/分钟
    _PREFERENCE_KEYWORDS = {
        "technician": ("技师", "师傅", "服务人员"),
        "time": ("时间", "时段", "上午", "下午", "晚上"),
        "service": ("项目", "服务"),
        "duration": ("时长", "分钟"),
    }
    _PREFERENCE_TRIGGERS = ("请记住", "记住", "以后都", "以后就", "帮我记住", "我喜欢")
    # 引导词（最长优先剥离）："我喜欢王师傅" -> "王师傅"；"以后都选王师傅" -> "王师傅"
    _GUIDE_PHRASES = (
        "以后都要", "帮我记住", "请记住", "我想要", "我喜欢", "以后都", "以后就",
        "要选", "想选", "想找", "要找", "喜欢", "以后", "想要", "要", "选", "用",
    )

    def _detect_preference_command(self, text: str) -> Optional[tuple]:
        """检测明确的长期偏好表达（决策二门槛的确定性启发式）。

        返回 (preference_type, preference_value)；未命中返回 None。
        说明：原型启发式覆盖常见表述（称谓型"王师傅"、属性型"时间 下午2点"），
        不依赖 LLM；复杂表达由后续真实 NLU 能力接管，本阶段不冒充精确。
        """
        for trigger in self._PREFERENCE_TRIGGERS:
            trig_idx = text.find(trigger)
            if trig_idx < 0:
                continue
            seg = text[trig_idx + len(trigger):]
            for ptype, keywords in self._PREFERENCE_KEYWORDS.items():
                for kw in keywords:
                    kw_idx = seg.find(kw)
                    if kw_idx < 0:
                        continue
                    head = seg[:kw_idx].strip(" ，。！？,.!?；;的")
                    after = seg[kw_idx + len(kw):].strip(" ，。！？,.!?；;的")
                    if ptype == "technician":
                        # 称谓型：名字在关键词前（"我喜欢王师傅"）
                        value = self._strip_guides(head) + kw if head else (after or kw)
                    else:
                        # 属性型：值在关键词后（"时间 下午2点"）；无则取整段
                        value = after if after else seg.strip(" ，。！？,.!?；;的")
                    value = value.strip()
                    if value:
                        return ptype, value[:40]
        return None

    @staticmethod
    def _strip_guides(head: str) -> str:
        """剥离前缀引导词（最长优先）。"我喜欢王" -> "王"。"""
        for guide in ConversationOrchestrator._GUIDE_PHRASES:
            if head.startswith(guide):
                return head[len(guide):]
        return head

    # ---------------- Phase H H3：转人工 ----------------
    _HUMAN_KEYWORDS = (
        "转人工", "人工客服", "找人工", "真人客服", "人工服务",
        "转接人工", "接入人工", "人工处理", "联系人工",
    )

    @staticmethod
    def _is_human_request(text: str) -> bool:
        """启发式判断是否为转人工请求（确定性规则，不依赖 LLM）。"""
        t = text or ""
        return any(k in t for k in ConversationOrchestrator._HUMAN_KEYWORDS)
