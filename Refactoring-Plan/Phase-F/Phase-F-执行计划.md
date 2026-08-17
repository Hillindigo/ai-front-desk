# Phase F 执行计划：知识库治理与回答可信度

> 计划版本：2026-08-18  
> 文档状态：待执行  
> 所属项目：`AIFrontDesk`  
> 前置阶段：Phase E——上下文、摘要与客户偏好

## 0. 阶段目标与边界

Phase F 不是简单增加一个知识库页面，而是把知识从“可检索文本”提升为可维护、可发布、可回滚、可引用的门店事实来源：

```text
运营人员编辑草稿 → 校验/预览 → 发布知识版本 → 构建候选索引
    → 索引与知识版本原子生效 → 咨询检索结构化证据
    → 回答展示依据；无依据时明确降级
```

完成后：

- 运营人员可以新增、编辑、预览、发布、停用和恢复门店知识；
- 未发布或已撤回内容不会进入正式回答依据；
- 索引刷新有状态、版本和失败回退，不暴露半成品；
- 咨询工作流只使用通过阈值的 `RetrievedEvidence`；
- 用户能看到安全的回答依据，或明确看到“当前没有足够可靠依据”；
- Phase E 的上下文、摘要、偏好删除、预约事实和 SSE v1 不被重写。

本阶段不做：真实管理员鉴权、多租户、外部向量数据库、平台知识同步、复杂文档解析和生产级审核流。这些需求应单独进入后续阶段。

## 1. 当前代码事实与前置门禁

1. `KnowledgeDocument` 当前只有 `content`、`category`、`keywords`、`embedding`、时间戳和 `is_active`，没有标题、来源、发布状态、文档版本或知识版本。
2. `KnowledgeRepository` 有基础 CRUD，但没有草稿/发布生命周期、版本快照和变更记录。
3. `services/knowledge_service.py` 已具备 Phase E 的阈值、候选边界、关键词预过滤、`source_version` 和索引快照原子替换；Phase F 必须复用这些边界。
4. `RetrievedEvidence` 已有文档 ID、分类、片段、分数、索引版本和排名，但尚未形成面向用户的稳定引用模型。
5. `api/knowledge.py` 仍是旧 `/api/knowledge` 接口，使用 `question + answer` 拼接 `content`；新增路由还存在引用未定义 `app` 对象的代码事实。
6. 管理 API、启动流程、旧 `KnowledgeRetriever` 和证据读取器可能各自创建 `KnowledgeService`，发布后可能读取不同索引实例。
7. 旧 `agents/consultant` 路径仍有普通字典检索和字符串协议，规范 turns 链路必须以 Phase D/E 的统一编排和结构化证据为准。
8. 当前身份仍是 `DemoIdentityResolver`，没有真实管理员权限；本阶段不能宣称完成生产鉴权。

正式执行 F1 前必须重新记录：

```text
git branch --show-current
git status --short
pytest -q
```

当前工作区已有 `Refactoring-Plan/Phase-E/Phase-E-执行计划.md` 未提交修改。该修改必须保留，不能被覆盖、重置、清理或混入 Phase F 提交。

## 2. 必须锁定的设计决策

### 2.1 文档状态与版本

```text
draft     可编辑、可预览，不作为正式回答依据
published 已发布，可被正式检索和引用
archived  已归档，不再作为新回答依据，但保留历史
failed    发布/索引失败，保留上一份 published
```

建议逻辑字段：

```text
id / title / content / category / keywords
status / document_version / knowledge_version
source_type / source_label
created_by / updated_by / created_at / updated_at
published_at / archived_at / embedding
```

实际字段以 F1 契约评审为准；状态和版本不能只保存在进程内存中。

### 2.2 发布语义

采用“发布后才可正式检索”：保存草稿不等于上线；预览可以检索候选版本但必须标记 `preview: true`；正式发布必须在候选索引构建成功后原子切换；失败保留上一正式版本；归档后新查询不得继续使用该文档。

### 2.3 服务实例所有权

应用容器/lifespan 只持有一个 `KnowledgeService`：

```text
App lifespan / Container
    └── KnowledgeService
          ├── KnowledgeRepository
          ├── PublishedIndexSnapshot
          └── RefreshStatus
```

管理 API、`KnowledgeEvidenceReader`、咨询工作流和旧适配器从容器获取同一实例。路由不得无条件 `KnowledgeService()`，不得依赖未定义的全局 `app`。

### 2.4 证据不是质量证明

`score >= 0.5` 只是 Phase E 的检索门槛，不代表答案正确。Phase F 必须增加固定评测集、证据命中率、无依据拒答率、引用完整率和真实模型人工评测计划。Fake 模式只能证明契约与回退。

## 3. 目标接口与回答链路

### 3.1 规范管理 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/knowledge/documents` | 按状态、分类、关键词分页查询 |
| POST | `/api/v1/knowledge/documents` | 创建草稿 |
| GET | `/api/v1/knowledge/documents/{id}` | 查看文档与版本 |
| PUT | `/api/v1/knowledge/documents/{id}` | 更新草稿或待发布版本 |
| POST | `/api/v1/knowledge/documents/{id}/preview` | 候选版本检索预览 |
| POST | `/api/v1/knowledge/documents/{id}/publish` | 校验、构建并发布 |
| POST | `/api/v1/knowledge/documents/{id}/archive` | 幂等归档 |
| GET | `/api/v1/knowledge/refresh` | 查询刷新状态 |
| POST | `/api/v1/knowledge/refresh` | 重建当前发布索引 |
| POST | `/api/v1/knowledge/search/preview` | 管理检索预览 |

旧 `/api/knowledge` 保留薄适配：不破坏现有契约、不重复创建服务实例，最终调用规范服务，并记录迁移/删除条件。

发布响应至少包含：`document_id`、`document_version`、`knowledge_version`、`source_version` 和 `status`。错误码至少区分 `INVALID_INPUT`、`KNOWLEDGE_NOT_FOUND`、`INVALID_STATE_TRANSITION`、`INDEX_BUILD_FAILED`、`KNOWLEDGE_VERSION_CONFLICT`、`KNOWLEDGE_NOT_READY` 和 `INTERNAL_ERROR`。

### 3.2 规范咨询链路

```text
用户问题 → ContextBuilder → KnowledgeEvidenceReader
    → RetrievedEvidence[]
        ├─ 有效证据 → 受证据约束的 ResponseGenerator
        └─ 无有效证据 → 确定性无依据回答
    → assistant 消息 + 证据 metadata
    → 用户回答 + 安全来源卡片
```

无依据、索引不可用、证据版本失效或问题超出范围时，不得让模型使用“通常”“大概”等方式编造价格、地址、政策或服务承诺。预约实时状态仍来自领域服务；不新增隐藏推理事件。

## 4. 执行任务

### F0：基线、交接与范围锁定

- 重跑分支、工作区和全量测试；
- 盘点模型、Repository、服务实例、容器、旧咨询入口和管理页面；
- 记录未定义 `app`、重复服务实例和索引刷新边界；
- 固定迁移方案、状态机、服务所有权和旧接口策略。

交付物：F0 基线记录、入口调用关系表、回滚点和不纳入清单。验收时区分代码事实、历史归档、推断和未知。

### F1：知识契约、状态机与数据库迁移

- 定义创建、编辑、预览、发布、归档和失败转换；
- 增加标题、来源、状态、文档/知识版本所需字段或兼容表；
- 建立必要索引和旧数据字段映射；
- 明确旧 `question + answer` 到新文档模型的迁移；
- 增加非法输入、状态转换和版本并发契约测试。

交付物：知识契约、迁移实现、字段映射、`tests/test_knowledge_contracts.py`。验收：老数据可读但不被错误标为新发布版本，重复请求不产生两个当前版本。

### F2：仓储、服务生命周期与管理用例

- 收敛文档列表、详情、创建、更新、归档、发布准备和版本查询；
- 将 `KnowledgeService` 注册到容器/lifespan；
- 让管理 API、证据读取器和咨询工作流共享同一实例；
- 删除路由内重复构造并修复未定义 `app` 依赖；
- 将旧 API 适配到规范服务并保留兼容测试。

交付物：管理应用服务、容器注册、适配器、`tests/test_knowledge_management_service.py`。验收：同一生命周期内管理和咨询读取同一实例，关闭时正确释放资源。

### F3：发布流水线、索引版本与失败回退

- 为刷新记录状态、时间、目标知识版本、索引版本和失败原因；
- 仅从已发布文档构建候选索引；
- 复用 Phase E 快照原子替换；
- 验证 Embedding 失败、空文档、归档、重启和并发刷新；
- 单进程锁和版本检查明确多进程未完成。

交付物：发布/刷新服务、状态接口、回退实现、`tests/test_knowledge_publish.py`、`tests/test_knowledge_index_lifecycle.py`。验收：草稿不可正式检索，失败时旧版本继续可查，每个 `source_version` 对应完整快照。

### F4：版本化知识 API 与演示身份边界

- 实现第 3.1 节 API；
- 复用 `IdentityResolver`，只验证演示身份下的门店管理；
- `user_id` 只作兼容校验，不能成为权限来源；
- 统一分页、状态码、错误码、空结果和幂等响应；
- 不返回 embedding、Prompt 或供应商原始响应；
- 完成 OpenAPI/README 和旧接口迁移提示。

验收：创建→编辑→预览→发布→检索→归档可通过 HTTP 完成，但不宣称生产级管理员鉴权。

### F5：咨询证据约束与无依据回答

- 将 `KnowledgeEvidenceReader` 接入规范 `ConsultationWorkflow`；
- 将证据转为模型可见的最小片段并附引用标识；
- 区分有依据、证据不足、知识不可用和模型不可用；
- 将证据元数据写入 assistant 消息或运行审计字段；
- 旧咨询入口只做适配，不绕过结构化证据；
- 保持 SSE v1 和唯一终止事件。

交付物：证据约束生成器、无依据策略、`tests/test_consultation_evidence.py`、`tests/test_no_evidence_fallback.py`。验收：低于阈值不进入模型上下文，空证据不编造，内部错误和隐藏推理不泄漏。

### F6：运营页面和回答引用展示

- 更新 `knowledge_management.html`，展示草稿、发布、失败、归档和刷新状态；
- 提供编辑、预览、发布、归档、重试和版本显示；
- 聊天页面展示轻量来源卡片或“回答依据”；
- 无依据回答不显示伪造来源；
- 断线/刷新从服务端恢复状态，不只依赖 localStorage。

验收：草稿不被显示为正式依据，发布失败可见且可重试，用户能区分有依据和无依据。

### F7：检索、引用和无依据回答评测

- 建立覆盖价格、营业时间、地址、政策、服务说明、无关问题和缺失信息的评测集；
- 定义预期文档、允许来源及是否应拒答/转人工；
- 测量证据命中率、无依据拒答率、引用完整率、旧版本泄漏率和发布后可用率；
- Fake 模式跑契约评测，另列真实模型人工评测计划；
- 每次发布前运行最小回归集。

交付物：评测数据、运行脚本、`tests/test_knowledge_evaluation.py` 和质量记录模板。验收：评测失败不会被标为质量达标。

### F8：故障注入、HTTP 链路与全量回归

- 注入数据库写入、Embedding、索引构建和响应生成失败；
- 验证旧索引回退、刷新并发、重复发布、归档幂等和重启恢复；
- 验证 turns SSE 仍为 `run_started → ... → run_completed/run_failed`；
- 验证引用只进入允许字段，预约和偏好删除保持 Phase C/E 语义；
- 运行目标测试、实际 HTTP 端点和全量 `pytest -q`。

交付物：`tests/test_phase_f_acceptance.py`、故障注入结果、HTTP 验收和全量结果。验收：失败不丢旧版本、不泄漏内部错误、不伪造回答。

### F9：文档、提交与 Phase G 交接

- 更新执行结果、实际文件路径、测试结果和验证边界；
- 更新 `README.md` 的知识 API、发布、引用和无依据规则；
- 记录旧接口删除条件、当前版本和回滚方式；
- 按独立功能块提交，不混入 Phase E 修改；
- 输出 Phase G 所需的审核、权限、审计和运营指标需求。

## 5. 推荐顺序与阶段完成定义

```text
F0 → F1 → F2 → (F3 + F4) → F5 → F6 → F7 → F8 → F9
```

执行门禁：F1 前不改变正式查询语义；F2 前不继续复制 `KnowledgeService`；F3 前不把数据库保存显示为已上线；F5 前不由前端拼接引用；F7/F8 前不标记阶段完成。

Phase F 完成必须同时满足：

- [ ] 文档具有草稿、已发布、已归档和失败可观察状态；
- [ ] 编辑、预览、发布、归档和刷新有规范接口；
- [ ] 旧 API 适配统一服务，无路由内重复实例；
- [ ] 发布成功才进入正式检索，失败保留旧版本；
- [ ] 证据带文档、知识版本和索引版本；
- [ ] 咨询区分有依据回答和无依据降级；
- [ ] 用户可看到安全来源或无依据提示；
- [ ] 契约、发布回退、证据、前端、故障和 HTTP 有直接测试；
- [ ] 全量 `pytest -q` 已重新执行并记录；
- [ ] Fake 模式零真实 LLM/Embedding 调用已验证；
- [ ] 真实模型、线上、多进程和生产权限边界已明确记录；
- [ ] `git diff --check` 通过且未混入 Phase E 修改。

## 6. 风险、回滚与 Phase G 交接

| 风险 | 应对 |
|---|---|
| 直接修改已发布文档导致回答漂移 | 草稿/版本/发布后构建候选索引 |
| 索引构建失败导致服务中断 | 保留旧快照，状态可见并可重试 |
| 多个服务实例读取不同版本 | 容器统一持有并显式注入 |
| 只用 score 判断质量 | 评测集、引用检查和人工评测 |
| 旧链路绕过证据模型 | 规范链路统一 `RetrievedEvidence` |
| 页面把保存显示为发布 | API 状态驱动页面并做 HTTP 验收 |
| 演示身份被误认为真实权限 | 复用 resolver，明确生产鉴权未完成 |

回滚时回到上一份已验证知识/索引快照，不删除原始文档；证据适配回归时可暂时关闭引用展示，但不得恢复无依据自由回答；迁移不安全时旧表只读并停止自动发布。

Phase G 交接内容：文档状态机、版本和 API 契约；当前知识/索引版本和回滚方式；发布与失败恢复证据；证据到用户引用映射；评测集和验证边界；旧 API 删除条件；真实鉴权、管理员角色、门店隔离、审核流和操作审计需求。

## 7. 验证命令与执行结果模板

计划中的最低验证入口，实际执行时必须填写真实结果：

```powershell
git branch --show-current
git status --short
pytest -q tests/test_knowledge_contracts.py tests/test_knowledge_management_service.py
pytest -q tests/test_knowledge_publish.py tests/test_knowledge_index_lifecycle.py
pytest -q tests/test_consultation_evidence.py tests/test_no_evidence_fallback.py
pytest -q tests/test_phase_f_acceptance.py
pytest -q
git diff --check
```

Fake 模式应记录：`MODEL_PROVIDER=fake`、`EMBEDDING_PROVIDER=fake`、无真实 LLM/Embedding 网络调用。只检查进程启动不算 HTTP 验收。

| 任务 | 日期 | 关键产物 | 验证证据 | 结果 | 未完成事项 |
|---|---|---|---|---|---|
| F0 | 待执行 | 基线与决策 | 待执行 | 待执行 | 待执行 |
| F1 | 待执行 | 契约与迁移 | 待执行 | 待执行 | 待执行 |
| F2 | 待执行 | 管理服务与容器 | 待执行 | 待执行 | 待执行 |
| F3 | 待执行 | 发布与索引回退 | 待执行 | 待执行 | 待执行 |
| F4 | 待执行 | 版本化 API | 待执行 | 待执行 | 待执行 |
| F5 | 待执行 | 证据约束咨询 | 待执行 | 待执行 | 待执行 |
| F6 | 待执行 | 页面与引用 | 待执行 | 待执行 | 待执行 |
| F7 | 待执行 | 评测集 | 待执行 | 待执行 | 待执行 |
| F8 | 待执行 | 故障与 HTTP 验收 | 待执行 | 待执行 | 待执行 |
| F9 | 待执行 | 文档与 Phase G 交接 | 待执行 | 待执行 | 待执行 |

