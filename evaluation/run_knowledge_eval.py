"""Phase F F7：运行知识评测（CLI）。

    python -m evaluation.run_knowledge_eval

Fake 模式下跑契约评测，输出 JSON 质量记录；真实模型语义质量由 manual_eval_pending
标记，不自动达标。退出码 0 表示评测已运行（不等于质量达标）。
"""

import asyncio
import json
import sys


async def main() -> int:
    # 契约评测默认 Fake 模式（零真实 LLM/Embedding 调用）
    import os
    os.environ.setdefault("MODEL_PROVIDER", "fake")
    os.environ.setdefault("EMBEDDING_PROVIDER", "fake")

    from services.knowledge_service import KnowledgeService
    from application.workflows import ConsultationWorkflow
    from evaluation.knowledge_evaluation import KnowledgeEvaluationRunner

    kb = KnowledgeService()
    await kb.initialize()
    try:
        runner = KnowledgeEvaluationRunner(kb, workflow=ConsultationWorkflow())
        result = await runner.evaluate()
    finally:
        kb.db_router.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("\n质量达标(quality_pass):", result["quality_pass"])
    print("真实模型/人工评测待办(manual_eval_pending):", result["manual_eval_pending"])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
