# Phase F 知识评测质量记录（模板）

> 每次发布/重大改动前运行 `python -m evaluation.run_knowledge_eval`，把结果贴到本文件。

## 运行信息
- 日期：
- 模式：fake_contract（Fake 模式契约评测）
- 数据源：默认知识库（或指定快照）

## 指标
| 指标 | 目标 | 实测 | 通过 |
|---|---|---|---|
| hit_rate（应命中命中率） | ≥0.80 |  |  |
| citation_completeness（引用完整性） | 1.00 |  |  |
| refusal_contract_rate（无依据拒答契约率） | ≥0.80 |  |  |
| old_version_leak（旧版本泄漏数） | 0 |  |  |
| post_publish_available（发布后可用） | True |  |  |

## 结论
- quality_pass：True/False
- manual_eval_pending：True（真实模型/人工评测永远待办，**不得**因 Fake 通过而标记质量达标）
- 未达标项与原因：

## 真实模型人工评测计划（另行执行，不计入本记录达标）
- 评测集：见 `evaluation/knowledge_eval_cases.py`
- 方法：真实模型对每例生成回答，人工核对是否依据证据+正确引用/正确拒答
- 记录：另存真实模型评测结果，与本文档分开归档
