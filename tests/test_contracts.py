"""D1：应用层契约测试（意图规则 / 事件模型 / 错误映射）"""

import pytest

from application.contracts import (
    ErrorCode,
    EventEnvelope,
    EventType,
    IntentClassification,
    IntentType,
    map_appointment_error,
)
from application.intent_rules import match_intent, match_intent_or_llm, normalize


class TestIntentRules:
    """正例 / 反例 / 冲突例 / 归一化 / 兜底边界。"""

    def test_appointment_positive(self):
        for text in ("我想预约肩颈放松", "帮我预约明天下午", "预约", "帮我安排一个"):
            result = match_intent(text)
            assert result.intent == IntentType.APPOINTMENT, text
            assert result.matched_rule == "appointment_request"

    def test_consultation_positive(self):
        for text in ("肩颈放松有什么好处", "多少钱一次", "营业时间", "在哪里", "服务项目有哪些"):
            result = match_intent(text)
            assert result.intent == IntentType.CONSULTATION, text

    def test_unrelated_positive(self):
        for text in ("你好", "谢谢", "今天天气怎么样", "你是谁"):
            result = match_intent(text)
            assert result.intent == IntentType.UNRELATED, text

    def test_cancel_command_priority(self):
        """高优先级明确命令优先于一般业务词。"""
        for text in ("取消预约", "预约取消", "我不约了", "把预约退掉", "取消"):
            result = match_intent(text)
            assert result.intent == IntentType.APPOINTMENT, text
            assert result.sub_action.value == "cancel", text

    def test_confirm_command(self):
        for text in ("确认预约", "确定预约", "确认"):
            result = match_intent(text)
            assert result.intent == IntentType.APPOINTMENT
            assert result.sub_action.value == "confirm"

    def test_reschedule_command(self):
        for text in ("改约", "我想改时间", "换个时间"):
            result = match_intent(text)
            assert result.intent == IntentType.APPOINTMENT
            assert result.sub_action.value == "reschedule"

    def test_negative_consultation_does_not_match_appointment(self):
        """反例：咨询关键词不误判为预约。"""
        assert match_intent("肩颈放松的好处").intent == IntentType.CONSULTATION

    def test_conflict_same_priority_requires_clarification(self):
        """同优先级多命中且意图不一致 -> 不强行路由，requires_clarification。"""
        # 构造：输入同时含预约与咨询关键词且无高优先级命令
        result = match_intent("预约和咨询哪个好")
        assert result.requires_clarification is True
        assert result.intent == IntentType.UNKNOWN

    def test_normalization(self):
        """归一化：全角空格/标点/大小写不影响匹配。"""
        assert normalize(" 我想预约肩颈放松  ").startswith("我想预约")
        assert normalize("我想预约，肩颈放松").startswith("我想预约")
        assert match_intent("我想预约。").intent == IntentType.APPOINTMENT
        assert match_intent(" I want to book ").intent in (IntentType.UNKNOWN,)

    def test_empty_input(self):
        result = match_intent("")
        assert result.intent == IntentType.UNKNOWN
        assert match_intent("   ").intent == IntentType.UNKNOWN

    def test_unknown_falls_to_llm(self):
        """规则未命中 -> LLM 兜底被调用；规则命中 -> LLM 不被调用。"""
        called = []

        def fake_llm(text, ctx):
            called.append(text)
            return IntentClassification(IntentType.CONSULTATION, matched_rule="llm")

        # 明确规则命中：不调用 LLM
        result = match_intent_or_llm("我想预约", fake_llm)
        assert result.intent == IntentType.APPOINTMENT
        assert called == []

        # 模糊输入：调用 LLM 兜底
        result2 = match_intent_or_llm("帮我看看这个套餐划算吗", fake_llm)
        assert called, "模糊输入应调用 LLM 兜底"
        assert result2.matched_rule == "llm"


class TestEventEnvelope:
    def test_envelope_roundtrip(self):
        env = EventEnvelope(
            run_id="r1", conversation_id="c1", sequence=1,
            type=EventType.RUN_STARTED, data={"protocol": "v1"},
        )
        d = env.to_dict()
        assert d["protocol_version"] == "v1"
        assert d["sequence"] == 1
        assert EventEnvelope.from_dict(d).type == EventType.RUN_STARTED

    def test_missing_field_rejected(self):
        with pytest.raises(ValueError):
            EventEnvelope.from_dict({"type": "run_started"})

    def test_unknown_event_type_rejected(self):
        raw = EventEnvelope(
            run_id="r", conversation_id="c", sequence=1, type=EventType.RUN_STARTED
        ).to_dict()
        raw["type"] = "not_an_event"
        with pytest.raises(ValueError):
            EventEnvelope.from_dict(raw)

    def test_unknown_protocol_rejected(self):
        raw = EventEnvelope(
            run_id="r", conversation_id="c", sequence=1, type=EventType.RUN_STARTED
        ).to_dict()
        raw["protocol_version"] = "v0"
        with pytest.raises(ValueError):
            EventEnvelope.from_dict(raw)

    def test_terminal_events_unique(self):
        from application.contracts import TERMINAL_EVENTS

        assert len(TERMINAL_EVENTS) == 2
        assert EventType.RUN_COMPLETED in TERMINAL_EVENTS
        assert EventType.RUN_FAILED in TERMINAL_EVENTS
        assert EventType.ASSISTANT_DELTA not in TERMINAL_EVENTS


class TestErrorMapping:
    def test_domain_to_public_code(self):
        assert map_appointment_error("APPOINTMENT_CONFLICT") == ErrorCode.APPOINTMENT_CONFLICT
        assert map_appointment_error("IDEMPOTENCY_CONFLICT") == ErrorCode.IDEMPOTENCY_CONFLICT
        assert map_appointment_error("APPOINTMENT_INVALID_STATE") == ErrorCode.APPOINTMENT_STATE_INVALID
        assert map_appointment_error("APPOINTMENT_REQUIRED_FIELD") == ErrorCode.INVALID_INPUT
        assert map_appointment_error("APPOINTMENT_NOT_FOUND") == ErrorCode.CONVERSATION_ACCESS_DENIED
        assert map_appointment_error("UNKNOWN_STRANGE") == ErrorCode.INTERNAL_ERROR

    def test_public_error_codes_stable(self):
        codes = {e.value for e in ErrorCode}
        assert codes == {
            "INVALID_INPUT", "CONVERSATION_NOT_FOUND", "CONVERSATION_ACCESS_DENIED",
            "INTENT_UNSUPPORTED", "APPOINTMENT_CONFLICT", "APPOINTMENT_STATE_INVALID",
            "IDEMPOTENCY_CONFLICT", "MODEL_UNAVAILABLE", "TOOL_FAILED", "INTERNAL_ERROR",
        }
