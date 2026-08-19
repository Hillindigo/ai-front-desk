"""确定性意图规则（Phase D D1/决策一）。

- 规则表只读、可审计，不允许请求或模型动态修改。
- 高优先级明确命令（取消/确认/改约）优先于一般业务词。
- 同优先级多命中且意图不一致 -> requires_clarification（交 LLM 兜底或澄清）。
- 输入先归一化（空白/大小写/常见标点）再匹配。
- FakeLLM 只兜底规则未命中或有歧义的输入。
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from .contracts import (
    AppointmentSubAction,
    IntentClassification,
    IntentType,
)


@dataclass(frozen=True)
class IntentRule:
    name: str
    intent: IntentType
    keywords: Tuple[str, ...] = ()
    priority: int = 0
    sub_action: AppointmentSubAction = AppointmentSubAction.NONE


# 只读规则表（按优先级降序匹配）
RULES: Tuple[IntentRule, ...] = (
    # ---- 高优先级明确命令（优先于一般业务词） ----
    IntentRule(
        "cancel_appointment", IntentType.APPOINTMENT,
        ("取消预约", "取消我的预约", "不约了", "退掉", "取消"),
        priority=100, sub_action=AppointmentSubAction.CANCEL,
    ),
    IntentRule(
        "confirm_appointment", IntentType.APPOINTMENT,
        ("确认预约", "确定预约", "确认", "确定"),
        priority=90, sub_action=AppointmentSubAction.CONFIRM,
    ),
    IntentRule(
        "reschedule_appointment", IntentType.APPOINTMENT,
        ("改约", "改时间", "换个时间", "重新预约时间"),
        priority=80, sub_action=AppointmentSubAction.RESCHEDULE,
    ),
    # ---- 预约 ----
    IntentRule(
        "appointment_request", IntentType.APPOINTMENT,
        ("我想预约", "我要预约", "帮我约", "预约", "约一个", "帮我安排"),
        priority=10, sub_action=AppointmentSubAction.DRAFT,
    ),
    # ---- 天气（明确无关，独立优先级避免与咨询词冲突） ----
    IntentRule(
        "weather_query", IntentType.UNRELATED,
        ("天气", "气温", "下雨", "温度"),
        priority=15,
    ),
    # ---- 咨询 ----
    IntentRule(
        "consultation_query", IntentType.CONSULTATION,
        ("好处", "多少钱", "价格", "营业时间", "几点营业", "地址", "在哪里",
         "服务项目", "是什么", "效果", "多久", "怎么样", "区别", "作用", "适合",
         "咨询", "哪个好"),
        priority=10,
    ),
    # ---- 无关/寒暄（低优先级：仅单独出现时命中，不干扰业务混合句） ----
    IntentRule(
        "unrelated_chitchat", IntentType.UNRELATED,
        ("你好", "您好", "谢谢", "再见", "你是谁", "哈哈", "在吗", "早安", "晚安"),
        priority=5,
    ),
)

# 归一化：去空白（含全角）、转小写、去常见标点
_PUNCT_RE = re.compile(r"[，。！？、；：,.!?;:～~·•\s\u3000]+")


def normalize(text: str) -> str:
    return _PUNCT_RE.sub("", (text or "").strip().lower())


def _rule_hits(rule: IntentRule, norm: str) -> bool:
    return any(kw in norm for kw in rule.keywords)


def match_intent(text: str) -> IntentClassification:
    """确定性意图匹配：返回结构化 IntentClassification。"""
    norm = normalize(text)
    if not norm:
        return IntentClassification(IntentType.UNKNOWN)

    # 按优先级降序分组匹配；高优先级先判定
    by_priority: dict = {}
    for rule in RULES:
        by_priority.setdefault(rule.priority, []).append(rule)

    for priority in sorted(by_priority, reverse=True):
        hits = [r for r in by_priority[priority] if _rule_hits(r, norm)]
        if not hits:
            continue
        if len(hits) == 1:
            rule = hits[0]
            return IntentClassification(
                rule.intent, matched_rule=rule.name, sub_action=rule.sub_action,
            )
        # 同优先级多命中
        intents = {h.intent for h in hits}
        if len(intents) == 1:
            rule = hits[0]
            return IntentClassification(
                rule.intent, matched_rule=rule.name, sub_action=rule.sub_action,
            )
        # 意图不一致 -> 不强行路由（交 LLM 兜底或澄清）
        return IntentClassification(
            IntentType.UNKNOWN,
            matched_rule=",".join(h.name for h in hits),
            requires_clarification=True,
        )

    return IntentClassification(IntentType.UNKNOWN)


def match_intent_or_llm(text: str, llm_classify, session_context: Optional[dict] = None):
    """规则优先，模糊/未命中才调用 LLM 兜底（D2 IntentRouter 使用）。"""
    result = match_intent(text)
    if result.intent != IntentType.UNKNOWN and not result.requires_clarification:
        return result
    if llm_classify is None:
        return result
    llm_result = llm_classify(text, session_context or {})
    if isinstance(llm_result, IntentClassification):
        return llm_result
    return result
