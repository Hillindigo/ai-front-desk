"""Phase I I5：四类评测运行器（意图/字段/RAG/P0）离线可重复。"""

import pytest


@pytest.mark.asyncio
async def test_phase_i_eval_runs_offline_and_separates_tiers():
    from evaluation.phase_i_eval import run

    result = await run()
    assert result["mode"] == "fake_contract"
    assert result["manual_eval_pending"] is True  # 真实模型/人工永不自动达标
    assert "version" in result
    assert result["version"]["tier"] == "fake_contract"
    m = result["metrics"]
    for key in ("intent_accuracy", "field_precision", "field_recall",
                "rag_hit_rate", "refusal_contract_rate", "p0_zero_tolerance"):
        assert key in m
    # P0 零容忍样本必须全过
    assert result["metrics"]["p0_zero_tolerance"] is True


@pytest.mark.asyncio
async def test_field_extraction_returns_expected_fields():
    from config.model_provider import FakeChatModel
    from agents.appointment.input_parser import InputParser
    from evaluation.phase_i_eval import _extract

    parser = InputParser(FakeChatModel())
    out = await _extract(parser, "女服务人员")
    assert out.get("gender") == "女"
    assert out.get("project") == "肩颈放松"
