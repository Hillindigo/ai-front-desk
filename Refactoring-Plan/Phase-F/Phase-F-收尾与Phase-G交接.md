# Phase F 执行结果 — 收尾与 Phase G 交接

> 记录日期：2026-08-18
> 阶段：F0–F9 全部完成（dev 分支本地，未合并/未推送）
> 基线：281 passed, 10 skipped → 收尾 340 passed, 10 skipped, 0 failed

## 1. 各子阶段交付与提交

| 子阶段 | 提交 | 关键产物 | 验收要点 |
|---|---|---|---|
| F0 | `6df1c61` | 基线/盘点/决策 D1-D6 | 分支 dev、工作区基线复现、既有缺陷定位 |
| F1 | `04dc4fe` | `services/knowledge_contracts.py`、`db/migrations.py`、模型字段、`tests/test_knowledge_contracts.py` | 状态机/错误码/迁移/草稿不正式检索 |
| F2 | `59f5643` | `services/knowledge_management.py`、容器单实例、旧 API 修复、`tests/test_knowledge_management_service.py` | 修复未定义 `app` 与重复实例；管理/咨询共享一实例 |
| F3 | `0645513` | `services/knowledge_publish.py`、`knowledge_meta`、候选/快照原子交换、发布/索引生命周期测试 | 发布失败保留旧快照、语料版本持久化、重启恢复 |
| F4 | `d770583` | `api/knowledge_v1.py`、`api/knowledge_v1` 路由注册、`tests/test_knowledge_api.py` | 版本化接口、错误码映射、演示身份边界 |
| F5 | `e971d5a` | 咨询证据约束注入、无依据确定性降级、引用标识、`test_consultation_evidence` 等 | 低阈值不进上下文、空证据不编造、证据元数据入 assistant |
| F6 | `500d8b7` | 运营页状态驱动+生命周期、聊天来源卡片/无依据提示、`/sources` 接口 | 草稿不当正式依据、失败可见可重试、区分有/无依据 |
| F7 | `f9d99ab` | `evaluation/` 评测集/运行器/质量模板、`test_knowledge_evaluation.py` | 命中率/拒答率/引用完整/泄漏/发布可用；评测失败不达标 |
| F8 | `6f117fe` | `tests/test_phase_f_acceptance.py` | 故障注入保旧版本、SSE 唯一终止、不泄漏隐藏推理、C/E 语义保持 |
| F9 | 本期 | README / PROJECT_MEMORY / 阶段文档 / 交接清单 | 文档同步、提交边界、Phase G 交接 |

**最终全量：340 passed, 10 skipped, 0 failed**（Fake 模式零真实 LLM/Embedding；10 skip 仍为 user_behavior 组件，与 Phase D/E 一致）。`git diff --check` 通过，未混入 Phase E 修改。

## 2. 完成的 Phase F 目标

- 文档具备四态：draft / published / archived / failed，状态机 + 迁移回填。
- 编辑 / 预览 / 发布 / 归档 / 刷新有规范 `/api/v1/knowledge` 接口；旧 `/api/knowledge` 薄适配统一服务，路由内不再重复构造，未定义 `app` 已修复。
- 发布成功才进入正式检索（索引只从 published 构建）；失败保留上一份 published（旧快照不回退）。
- 证据带 document_id / category / snippet / score / source_version，写入 assistant 消息 metadata（`/sources` 供前端）。
- 咨询区分有依据（受证据约束+引用）与无依据（确定性降级拒答）；`[THOUGHT]/[SIGNAL]` 与内部错误不泄漏到内容/来源。
- 语料 knowledge_version 持久化于 `knowledge_meta`，重启恢复；source_version = index-N 对应完整快照。
- 评测集 + 运行器 + 质量记录模板；评测失败不会被标为质量达标，真实模型/人工评测永远 pending。

## 3. 实际文件路径（F9 交付）

- 契约：`services/knowledge_contracts.py`
- 服务：`services/knowledge_management.py`、`services/knowledge_publish.py`、`services/knowledge_service.py`（候选组装/原子交换/预览检索）
- 仓储：`db/repositories/knowledge_repository.py`（治理字段/列表/meta）
- 迁移：`db/migrations.py`（`SessionManager` 内幂等调用）
- API：`api/knowledge_v1.py`（新）、`api/knowledge.py`（旧适配）、`api/conversations.py`（`/sources`）
- 容器/生命周期：`application/container.py`（单实例 + initialize）、`app.py`（走容器、shutdown 关闭）
- 咨询：`application/workflows.py`、`application/orchestrator.py`、`agents/consultant_agent.py`、`agents/consultant/{consultation_processor,prompt_builder}.py`
- 前端：`web/templates/knowledge_management.html`、`web/templates/index.html`
- 评测：`evaluation/`（cases / runner / run_knowledge_eval / quality_template）
- 测试：`tests/test_knowledge_contracts.py`、`test_knowledge_management_service.py`、`test_knowledge_publish.py`、`test_knowledge_index_lifecycle.py`、`test_knowledge_api.py`、`test_consultation_evidence.py`、`test_no_evidence_fallback.py`、`test_knowledge_evaluation.py`、`test_phase_f_acceptance.py`

## 4. 旧接口删除条件与当前版本

- 旧 `/api/knowledge`：薄适配，仍响应 list/search 契约；**删除条件**：生产 /api/v1 前端切换完成 + Phase G 管理员鉴权落地，且无消费者依赖 `question+answer` 语义后可在独立阶段删除。
- 当前知识版本：由 `knowledge_meta.knowledge_version` 维护；回滚方式：回到上一验证快照（旧索引快照保留），不删原始文档；`POST /api/v1/knowledge/refresh` 重建索引对账。

## 5. Phase G 交接需求（未实现，明确移交）

1. **权限/审核**：真实管理员鉴权与 RBAC（现为演示身份 `DemoIdentityResolver`，请求体 user_id 只作兼容校验，不得作为权限来源）；发布/归档操作审计（谁、何时、改了哪个版本）。
2. **运营审计/指标**：发布操作日志、刷新失败记录、引用使用统计、无依据拒答率在生产侧可观测。
3. **门店隔离/多租户**：当前单门店本地 SQLite；多进程索引刷新未完成（现为单进程锁），多实例一致性待定。
4. **真实模型质量**：评测集已建（`evaluation/`），`manual_eval_pending=True`；需真实模型跑分 + 人工核对的"依据+引用/拒答"评测，独立于本阶段记录。
5. **文档解析/知识来源**：外部来源导入、复杂文档解析、平台知识同步不在本阶段。

## 6. 验证命令（收尾复核）

```text
git branch --show-current        -> dev
git status --short               -> 仅 Phase-E 已保留未提交修改
pytest -q                        -> 340 passed, 10 skipped, 0 failed
python -m evaluation.run_knowledge_eval   -> hit_rate 0.83 / refusal 1.0 / citation 1.0 / leak 0 / publish True
git diff --check                 -> OK
```

## 7. 未冒充完成事项（如实列出）

- 真实管理员鉴权、多租户、真实模型语义质量、多进程索引、生产级审核流均为"待验证/已知风险"或 Phase G 交接，未标记为已完成。
