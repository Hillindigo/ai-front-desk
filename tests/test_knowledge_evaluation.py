"""Phase F F7 测试：知识评测运行器（Fake 契约模式）。

覆盖：
- 评测产出完整指标与质量记录。
- 引用完整性 100%、旧版本泄漏为 0、发布后可用、无依据拒答契约达成。
- 诚实性：任何单点指标未达标（如泄漏>0）时 quality_pass 必须为 False，
  评测失败不会被标记为质量达标。
- real_model / manual_eval_pending 始终为 True，不自动冒充达标。
"""

import pytest

from application.container import Container
from application.workflows import ConsultationWorkflow
from evaluation.knowledge_evaluation import KnowledgeEvaluationRunner
from evaluation.knowledge_eval_cases import EVAL_CASES


@pytest.fixture
async def eval_env(tmp_path):
    c = Container(db_path=f"sqlite:///{(tmp_path / 'f7.db').as_posix()}")
    await c.initialize()  # 10 条默认 published
    runner = KnowledgeEvaluationRunner(c.knowledge_service, workflow=ConsultationWorkflow())
    yield c, runner
    c.close()


@pytest.mark.asyncio
async def test_评测产出合法指标(eval_env):
    _, runner = eval_env
    result = await runner.evaluate(EVAL_CASES)
    m = result["metrics"]
    for key in ("hit_rate", "refusal_contract_rate", "citation_completeness",
                "old_version_leak", "post_publish_available"):
        assert key in m
    assert m["citation_completeness"] == 1.0
    assert m["old_version_leak"] == 0
    assert m["post_publish_available"] is True
    assert m["refusal_contract_rate"] >= 0.8
    assert isinstance(result["quality_pass"], bool)
    assert result["manual_eval_pending"] is True  # 真实模型评测永不自动达标
    assert len(result["cases"]) == len(EVAL_CASES)


@pytest.mark.asyncio
async def test_评测失败不被标为达标(eval_env):
    _, runner = eval_env
    # 模拟单点达标失败：旧版本泄漏 1 条 -> quality_pass 必须 False（诚实记录）
    runner.old_version_leak = _async_ret(1)
    result = await runner.evaluate(EVAL_CASES)
    assert result["metrics"]["old_version_leak"] == 1
    assert result["quality_pass"] is False  # 评测失败不会被标为质量达标


@pytest.mark.asyncio
async def test_引用完整率来自实际结果而非固定常量(eval_env):
    _, runner = eval_env
    runner._citation_ok = _async_ret(False)
    result = await runner.evaluate(EVAL_CASES)
    assert result["metrics"]["citation_completeness"] == 0.0
    assert result["quality_pass"] is False


@pytest.mark.asyncio
async def test_命中与拒答契约覆盖(eval_env):
    _, runner = eval_env
    result = await runner.evaluate()
    # 无关与缺失信息用例应触发拒答契约
    unrelated = next(c for c in result["cases"] if c["case_id"] == "unrelated")
    missing = next(c for c in result["cases"] if c["case_id"] == "missing-info")
    assert unrelated["expected_refusal"] and unrelated["refused"] is True
    assert missing["expected_refusal"] and missing["refused"] is True


def _async_ret(value):
    async def _fn(*a, **k):
        return value
    return _fn
