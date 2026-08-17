"""Phase F F7：知识检索/引用/无依据评测用例集。

每例定义：查询、覆盖维度、是否应命中已有知识（及期望文档标题）、是否应拒答
（信息缺失/无可靠依据）。Fake 模式跑契约评测；真实模型语义质量在
`manual_eval_pending` 中记录，不自动标为达标。
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class EvalCase:
    id: str
    query: str
    dimension: str            # 价格/营业时间/地址/政策/服务说明/无关/缺失信息
    expected_hit: bool        # 是否应命中已有知识（关键字命中）
    expected_doc_keywords: Optional[List[str]] = None  # 期望命中的文档关键词
    expect_refusal: bool = False  # 是否应拒答/转人工（无可靠依据）
    note: str = ""


EVAL_CASES: List[EvalCase] = [
    EvalCase("price", "基础护理多少钱？", "价格", True, ["基础护理", "价格"]),
    EvalCase("open-hours", "你们几点开门、几点关门？", "营业时间", True,
             ["营业时间", "几点"]),
    EvalCase("service-desc", "肩颈放松有什么作用？", "服务说明", True,
             ["肩颈放松"]),
    EvalCase("cancel-policy", "我要取消预约有什么政策？", "政策", True,
             ["取消", "政策"]),
    EvalCase("membership", "会员卡充值有什么优惠？", "政策", True,
             ["会员", "充值"]),
    EvalCase("address-empty", "门店的具体门牌号和停车位是？", "地址", True,
             ["地址", "门店信息"], False, "仅存地址提示，无门牌号",
             ),
    EvalCase("unrelated", "今天的股票行情怎么样？", "无关", False, None, True),
    EvalCase("missing-info", "你们店能治疗失眠吗（医疗咨询）？", "缺失信息",
             False, None, True, "医疗建议超出范围，应拒答/转人工"),
]
