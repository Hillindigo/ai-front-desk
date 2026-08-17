"""Phase F F5 测试：咨询证据约束。

覆盖：
- RetrievedEvidence -> 提示词知识片段（含 document_id/来源标识，不含 embedding/prompt）。
- PromptBuilder 输出 [来源i] 引用标识与强约束指令。
- 有证据时咨询工作流把权威片段传给 consultant，不再自行检索。
- ContextBuilder 命中证据时 model_input 的 retrieved_evidence 高于阈值。
"""

import pytest

from application.container import Container
from application.context_contracts import KNOWLEDGE_MIN_SCORE_DEFAULT
from application.contracts import IntentClassification, IntentType
from application.workflows import ConsultationWorkflow
from agents.consultant.prompt_builder import PromptBuilder


class _FakeConsultant:
    def __init__(self):
        self.calls = 0
        self.received = None

    async def consult_stream(self, user_input, knowledge_docs=None):
        self.calls += 1
        self.received = knowledge_docs
        content = str(knowledge_docs[0]["content"])
        yield "[REPLY][咨询机器人]"
        for ch in content:
            yield ch


class _FakeSession:
    def __init__(self):
        self.agent = type("A", (), {"consultant_agent": _FakeConsultant()})()


def _evidence(**overrides):
    base = {
        "document_id": 7, "category": "营业时间", "snippet": "门店每天9点到22点营业。",
        "score": 0.9, "source_version": "index-3", "rank": 1,
    }
    base.update(overrides)
    return [base]


def _intent():
    return IntentClassification(intent=IntentType.CONSULTATION, sub_action="none",
                                confidence=1.0, requires_clarification=False)


@pytest.mark.asyncio
async def test_证据转知识片段不含embedding且带来源():
    docs = ConsultationWorkflow._evidence_to_knowledge_docs(_evidence())
    assert len(docs) == 1
    d = docs[0]
    assert d["document_id"] == 7
    assert d["content"] == "门店每天9点到22点营业。"
    assert "来源" in d["source_label"] or "知识库" in d["source_label"]
    assert "embedding" not in d
    assert "prompt" not in str(d).lower()


@pytest.mark.asyncio
async def test_有证据时工作流把权威片段传给consultant():
    wf = ConsultationWorkflow()
    session = _FakeSession()
    fake = session.agent.consultant_agent
    collected = []
    async for token in wf.run(session, "营业时间", _intent(),
                              context={"retrieved_evidence": _evidence()}):
        collected.append(token)
    text = "".join(collected)
    assert fake.calls == 1
    assert fake.received[0]["document_id"] == 7
    assert fake.received[0]["content"] == "门店每天9点到22点营业。"
    assert "9点到22点" in text


@pytest.mark.asyncio
async def test_证据为空片段时降级并跳过模型():
    wf = ConsultationWorkflow()
    session = _FakeSession()
    fake = session.agent.consultant_agent
    collected = []
    async for token in wf.run(session, "价格多少", _intent(),
                              context={"retrieved_evidence": [
                                  {"document_id": 1, "snippet": "  ",
                                   "category": "c", "score": 0.9,
                                   "source_version": "index-1", "rank": 1},
                              ]}):
        collected.append(token)
    text = "".join(collected)
    assert fake.calls == 0  # 空片段不得调用模型
    assert "没有" in text or "依据" in text or "咨询" in text


def test_提示词含来源引用与强约束():
    pb = PromptBuilder()
    ctx = pb._build_knowledge_context([
        {"content": "价格88元", "document_id": 3, "category": "服务项目", "source_label": "运营"},
    ])
    assert "[来源1]" in ctx
    assert "文档3" in ctx or "3" in ctx
    assert "禁止编造" in ctx
    assert "通常/大概" in ctx


def test_提示词空依据禁编造():
    pb = PromptBuilder()
    ctx = pb._build_knowledge_context([])
    assert "禁止编造" in ctx


class TestBundleContext:
    @pytest.mark.asyncio
    async def test_咨询命中证据且高于阈值(self, tmp_path):
        c = Container(db_path=f"sqlite:///{(tmp_path / 'f5.db').as_posix()}")
        await c.initialize()  # 10 条默认 published
        try:
            package = await c.context_builder.build(
                "conv-f5", "default_user", "营业时间是几点？",
                workflow_state={"conversation_id": "conv-f5"},
            )
            mi = package.model_input()
            ev = mi.get("retrieved_evidence") or []
            assert ev
            assert all(float(e["score"]) >= KNOWLEDGE_MIN_SCORE_DEFAULT for e in ev)
            assert all("document_id" in e and "snippet" in e for e in ev)
        finally:
            c.close()
