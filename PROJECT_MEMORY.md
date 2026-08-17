# AI Front Desk 项目记忆

> 最后更新：2026-08-17
> 适用范围：项目结构、重构阶段、工程规则、已知问题和验证边界
> 详细施工内容以 `Refactoring-Plan/Phase-*` 下的阶段文档为准；本文件记录长期有效的项目事实和工作约束。

## 1. 项目定位

AI Front Desk 是一个面向线下服务门店的本地优先 FastAPI 原型，目标是把咨询、预约、排班、知识库、客户行为和回访串成一条可验证的 Agent 工作流。

当前产品形态：

- Web 页面：Jinja2 模板、原生 HTML/CSS/JavaScript。
- API：FastAPI + Uvicorn。
- Agent：任务分类、咨询、预约、用户行为分析。
- 数据：SQLAlchemy + SQLite，默认位于 `data/`。
- 知识库：FAISS / Embedding，本地索引由知识服务管理。
- 测试：Pytest，默认使用 Fake LLM / Fake Embedding，不依赖真实模型 API。

项目当前仍是原型和重构中的模块化单体，不应把现有能力描述为已完成的生产级多租户系统、完整记忆系统或可靠预约平台。

## 2. 当前状态快照

### 2.1 Git 状态

- 当前仓库：`D:\1_qiuzhao\AIFrontDesk`
- 当前分支：`dev`
- 工作区：2026-08-17 检查时干净
- 远程：`origin=https://github.com/Hillindigo/ai-front-desk.git`
- Phase A 基线：tag `phase-a-baseline`
- `main` 指向 Phase A 基线；`dev` 包含基线之后的项目文档流程提交。

### 2.2 阶段文档索引

阶段状态、阶段归档结果、测试证据和下一阶段交接事项不在本文件展开记录，统一放在：

```text
Refactoring-Plan/Phase-A/
Refactoring-Plan/Phase-B/
Refactoring-Plan/Phase-C/
Refactoring-Plan/Phase-D/
Refactoring-Plan/Phase-E/
```

本文件只保留跨阶段长期有效的项目规则；具体阶段文档必须记录实际状态、验证结果和遗留问题。

## 3. 代码边界与当前架构

当前主要调用方向：

```text
Web / API
    ↓
Agents
    ↓
Services
    ↓
Repositories / Database
```

主要目录职责：

| 目录 | 职责 |
|---|---|
| `agents/` | 任务分类、咨询、预约、用户行为等 Agent 及其处理流程 |
| `api/` | FastAPI 接口、请求模型、响应模型和聊天处理入口 |
| `config/` | 设置、模型提供商、数据库、全局常量和时间配置 |
| `db/` | SQLAlchemy 模型、数据库路由、会话管理和 Repository |
| `services/` | 知识库、预约、服务人员、推荐、用户行为等业务服务 |
| `web/` | 页面路由、模板和静态资源 |
| `tests/` | Agent 测试、API 契约测试和行为组件测试 |
| `Refactoring-Plan/` | 整体路线、阶段计划、执行记录和验证材料 |

当前必须记住的实现事实：

1. 聊天链路已按会话运行（Phase B）：`api/chat_handler.py` 通过 `ConversationSession/SessionManager` 按 `conversation_id` 管理会话、消息持久化与并发锁；`/chat/stream` 是兼容包装，无 ID 时落到默认演示会话。
2. 统一编排（Phase D）：`application/orchestrator.py` 的 `ConversationOrchestrator.handle_turn` 是唯一轮次编排入口（校验会话→落用户消息→`IntentRouter` 分类→工作流→落 assistant 消息）；`application/container.py` 为依赖容器；`application/intent_rules.py` 是确定性意图规则表（规则优先，LLM 只兜底模糊输入）。
3. 事件协议（Phase D）：`/api/v1/conversations/{id}/turns` 返回 SSE 事件流（`protocol_version: v1`），一次 turns = 一个 `run_id`，`run_started` 首发、唯一 `run_completed`/`run_failed` 终止；事件只描述当前轮次，跨轮状态由持久化草稿/预约实体承载；`[THOUGHT]`/`[REPLY]`/`[SIGNAL]` 不进入事件流。
4. `config/constants.py` 中的 `busy_periods_dict` 是进程级共享状态，已弃用（Phase C 后新主流程不再写入/读取），保留定义仅为兼容导出。
5. 预约已领域化（Phase C）：独立 `Appointment`/`AppointmentEvent` 表、显式状态机（draft→pending_confirmation→confirmed / cancelled / expired）、`BEGIN IMMEDIATE` 事务内冲突校验与幂等键；`Agent` 通过领域服务完成预约，不再直接写 `technician_schedules` 或 `busy_periods_dict`。
6. SQLite 事务使用自定义方言 `sqlite+immediate`（`db/base/immediate_dialect.py`）：所有事务 `BEGIN IMMEDIATE` 写锁抢占，防并发确认 lost update。
7. 数据库层同时存在 Repository、Router 和兼容层（`TechnicianDBRouter` 仅剩 A-R2 user_behavior 组件使用），后续需要按阶段删除重复入口，不能只叠加新层。
8. 知识库索引仍由进程内服务管理，知识变更会触发重建；后续先解决可靠性和验证边界，不直接引入超出当前规模的复杂基础设施。
9. 内部技术字段仍使用 `technician`，产品文案优先使用“服务人员”；除非阶段计划明确迁移，不要随意修改数据库字段命名。
10. 统一上下文（Phase E）：`application/context_builder.py` 的 `ContextBuilder` 是唯一上下文装配器（只读、无副作用、固定优先级预算裁剪）；数据来自只读读取器（`application/context_readers.py`）；`ContextPackage.model_input()` 只输出允许公开字段，审计字段不泄漏到模型输入。
11. 会话摘要与偏好（Phase E）：`conversation_summaries`（按消息 sequence 覆盖、active/invalidated/failed、版本递增）；`preferences` + `preference_tombstones`（同一 user_id+type 单 active 覆盖语义）。删除偏好 = 单事务：停用偏好 + 写墓碑 + 该用户摘要失效 + 来源消息 metadata 置 `context_excluded` 屏蔽。旧 `user_preferences` 经 `migrate_legacy()` 迁移为 `legacy_unverified`（默认不注入，重新确认才提升）。
12. 身份边界（Phase E）：`application/identity.py` 的 `IdentityResolver` 抽象 + `DemoIdentityResolver`（本地演示固定 `default_user`，请求体 user_id 只作兼容字段必须一致）。偏好 API `/api/v1/preferences` 走此边界；后续真实鉴权只替换 resolver。
13. 知识检索（Phase E）：`KnowledgeService` 设 `min_score`（默认 0.5）阈值、候选边界、关键词预过滤；索引以 (index, doc_ids, version) 快照原子替换，`source_version` 随重建递增，旧结果不作当前证据；`search_structured()` 输出结构化证据供上下文。

## 4. 重构目标和长期原则

重构目标不是增加更多 Agent，而是逐步获得：

- 每个用户/会话拥有独立且可恢复的状态。
- LLM 负责理解和生成，确定性代码负责业务校验、状态迁移和数据写入。
- 预约、排班、确认、取消和改约具备明确的状态与事务边界。
- 消息、工作流状态、客户偏好和摘要有清晰的数据边界。
- API、流式事件、错误码、日志和测试契约稳定可追踪。
- 每个阶段结束系统仍然能启动、测试和演示。
- 保持模块化单体，不为当前个人项目过早引入微服务和复杂记忆基础设施。

长期原则：

1. 先保证状态、数据和边界正确，再追求模型能力。
2. Agent 可以理解、建议和编排，但关键业务动作必须经过确定性校验。
3. 结构化业务事实不能只保存在 Prompt、聊天摘要或 Python 进程内存中。
4. 先删后建：每个阶段都要识别可删除的兼容层和重复入口。
5. 前端跟随后端同步做最小改动，不能把前端兼容问题留到最后。
6. 每个阶段必须有明确的完成定义、自动化验证和运行证据。
7. 未验证的事项只能标记为“待验证”或“已知风险”，不能标记为已完成。

## 5. 阶段目录和阶段管理规则

阶段文档统一放在 `Refactoring-Plan/` 下，并为每个阶段建立独立目录：

```text
Refactoring-Plan/
├── All/                         # 整体路线图
├── Phase-A/                     # Phase A 计划、执行记录、验证材料
├── Phase-B/                     # Phase B 计划、执行记录、验证材料
├── Phase-C/
├── Phase-D/
├── Phase-E/
└── ...
```

阶段规则：

1. 新阶段开始前，必须审查上一阶段的完成记录、遗留问题、跳过测试和未验证事项。
2. 当前阶段计划必须先列出前阶段遗留问题，并写明修复策略、优先级、影响范围和验收标准。
3. 阶段计划、执行记录和验证结果放在同一个阶段目录中，不能只依赖聊天记录。
4. 一个阶段完成后，必须更新阶段状态、验证证据、剩余风险和下一阶段交接事项。
5. 不得因为代码已经修改或测试数量增加，就自动把阶段标记为完成；必须满足该阶段的 Done 定义。
6. 阶段内出现范围扩大时，应先更新计划并说明原因，不能无记录地把额外任务混入当前阶段。
7. 阶段文档应引用实际文件路径、测试命令和结果；推测性内容必须明确标注为假设。

## 6. Git 和提交规则

### 分支职责

- `main`：稳定分支，只接收已经完成测试的阶段成果，不做日常开发。
- `dev`：日常功能开发、修复、重构和联调分支。

### 开始修改前

1. 先检查当前分支和工作区状态。
2. 保留已有未提交改动，不执行会覆盖、清理或重置用户改动的操作。
3. 确认当前任务范围和阶段归属。

### 合并和推送

- 只有用户明确要求“合并并推送”时，才将 `dev` 合并到本地 `main`。
- 合并使用 `git merge --no-ff dev`，合并后在 `main` 再跑全量测试。
- 未经明确授权不得自行合并、推送或宣称已部署。
- Git push 成功不等于线上部署成功；工作流、部署平台和线上/鉴权验收必须分别报告。

### Commit 规则

格式：

```text
Phase X(类型)-标题
```

- 标题是一句话总结，不超过 15 个字。
- 类型包括 `feat`、`fix`、`docs`、`refactor`、`test`、`style`、`perf`、`build`、`ci`、`chore`、`revert`、`security`。
- 一个提交围绕一个独立的大块事项，不把无关改动混在一起。
- Bug 修复和新功能尽量分开提交。
- 不因为阶段提交规则而提交整个工作区中无关的文件。

示例：

```text
Phase A(test)-建立FakeLLM测试基线
Phase A(security)-CORS来源配置化
Phase B(feat)-会话隔离与消息持久化
```

## 7. 验证和事实标注规则

所有结论按证据边界区分：

- **代码事实**：由当前源码、配置或数据库结构直接确认。
- **本地验证**：当前机器执行命令得到的结果。
- **阶段归档结果**：阶段文档记录的历史结果，除非重新运行，不等于当前环境可复现。
- **推断**：根据代码和计划推导出的风险或建议，必须标记为推断。
- **未知/待验证**：当前没有足够证据，不能用肯定语气表述。

最低验证要求：

1. 修改代码后运行与改动直接相关的测试。
2. 阶段收尾运行项目全量测试，并记录通过、跳过、失败和警告。
3. 需要启动服务时验证实际 HTTP 端点，而不只验证进程是否启动。
4. 需要生产或鉴权行为时，不能用本地构建、Git 引用或未鉴权重定向代替线上验收。
5. 测试依赖 Fake 模型时，必须确认没有真实 LLM/Embedding 请求。

## 8. 更新本文件的触发条件

以下变化发生时，必须同步更新本文件：

- 阶段状态、阶段目录结构或完成定义发生变化。
- Git 分支、合并、推送或提交规则发生变化。
- 核心架构边界、数据模型或 API 兼容策略发生变化。
- Phase 遗留问题被修复、延期、重新分级或发现新的阻塞问题。
- 测试入口、依赖环境或验证方式发生变化。

本文件应记录稳定的项目知识，不替代具体 Phase 施工计划，也不记录未经验证的乐观结论。
