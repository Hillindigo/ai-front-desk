# Phase F 执行结果 — F0：基线、交接与范围锁定

> 记录日期：2026-08-18
> 阶段：F0（前置任务：Phase E —上下文、摘要与客户偏好，已完成）
> 文档状态：已完成

## 0. 基线复现

执行门禁（Phase-F-执行计划.md 第 1 节）复现结果：

```text
git branch --show-current   -> dev
git status --short          ->  M Refactoring-Plan/Phase-E/Phase-E-执行计划.md
                                ?? Refactoring-Plan/Phase-F/
pytest -q                   -> 281 passed, 10 skipped, 0 failed in 11.58s
```

- 工作区中的 `Refactoring-Plan/Phase-E/Phase-E-执行计划.md` 有未提交修改（历史阶段文档续写）。**该修改必须保留，不得被覆盖、重置、清理或混入任何 Phase F 提交**（Phase F 提交仅 `git add Refactoring-Plan/Phase-F/` 及本期代码）。
- `Refactoring-Plan/Phase-F/` 目录当前未跟踪，本次 F0 提交一并纳入。

## 1. 代码事实盘点（F0 交付物）

### 1.1 数据模型
`db/models.py::KnowledgeDocument`（142-151 行）：字段仅 `id / content / category / keywords(JSON) / embedding(JSON) / created_at / updated_at / is_active`。
→ **无标题、来源、状态、文档/知识版本**，与计划第 1.1 条一致。

### 1.2 仓储
`db/repositories/knowledge_repository.py::KnowledgeRepository`：`add/get/get_all/update/delete(软删)/search_by_category/keywords/content/get_all_categories/get_documents_count/get_documents_by_category_count`。**无草稿/发布生命周期、无版本快照、无变更记录**。

### 1.3 服务与重复实例
`services/knowledge_service.py::KnowledgeService`：进程内 FAISS 索引 + `(index, doc_ids, version)` 快照原子替换、`min_score=0.5` 阈值、关键词预过滤、`search_structured()` 输出结构化证据、`default_knowledge` 空库播种。**E6 边界将被 Phase F 复用**。

`KnowledgeService()` 直接实例化位置（除测试外共 6 处，F2 必须收敛）：
| 位置 | 行 | 问题 |
|---|---|---|
| `api/knowledge.py` | 29/56/97/125/146 | 路由内重复构造 |
| `api/knowledge.py` | 76 | **引用未定义 `app`（NameError）** |
| `app.py` | 48 | 启动独立实例，未接入容器 |
| `application/container.py` | 89 | `KnowledgeEvidenceReader(KnowledgeService())` 又一份 |
| `agents/consultant/knowledge_retriever.py` | 17 | 旧咨询链路独立实例 |

### 1.4 容器与生命周期
`application/container.py::Container`：持有 db_router、appointment_service、identity_resolver、preference_service、intent_router、behavior_recorder、workflows、context_builder（其内 `KnowledgeEvidenceReader(KnowledgeService())`）、summary_service、orchestrator、`close()`。
→ 容器已具备 `context_builder`，但其内 KnowledgeService 为独立实例，未与 app 启动实例共享；Phase F 需要将单一 KnowledgeService 注册进容器并贯穿管理 API / EvidenceReader / 咨询链路。

### 1.5 身份边界
`application/identity.py`：`IdentityResolver` 抽象 + `DemoIdentityResolver`（固定 `default_user`）。偏好 API 已走该边界。**无真实管理员权限，Phase F 不宣称完成生产鉴权**。

### 1.6 数据库与迁移
- `config/database.py`：`DATABASE_URL` 默认 `sqlite:///data/ai_front_desk.db`，已存在真实库 `data/ai_front_desk.db`。
- 建表：`db/base/session_manager.py` 用 `Base.metadata.create_all(engine)` —— **只建缺失表，不会给已有表补列**。
- 结论：给 `knowledge_documents` 新增字段必须做 ALTER TABLE ADD COLUMN 迁移；新表（如刷新状态表）可被 create_all 自动创建。

### 1.7 索引注册边界
`app.py::initialize_system` 启动时 `KnowledgeService().initialize()`（独立实例播种+建索引），未写入容器；`application/container.py` 的 context_builder 持有另一实例。查询（`KnowledgeEvidenceReader`）依赖容器实例，管理 API 依赖各自独立实例 → **发布后可能读到不同索引实例**，与计划第 1.6 条一致。

### 1.8 旧咨询链路
`agents/consultant/knowledge_retriever.py` 仍用普通字典检索 + 字符串协议，独立 `KnowledgeService()`。规范 turns 链路（Phase D/E 的 `ConversationOrchestrator` + `ConsultationWorkflow` + `ContextBuilder`）才是主路径；旧链路 Phase F 只做适配不扩展。

## 2. 锁定决策（F0）

- **D1（字段与版本）**：`KnowledgeDocument` 新增 `title / status(String) / document_version(int) / knowledge_version(int) / source_type(String) / source_label(String) / created_by / updated_by / published_at / archived_at`；`status` 取值 `draft|published|archived|failed`。保留 `content/category/keywords/embedding/is_active`。字段名用 `technician` 之外的既有约定不动。
- **D2（迁移）**：新增字段用轻量迁移（PRAGMA table_info 探测 + `ALTER TABLE ADD COLUMN`）补齐既有 `knowledge_documents` 表；旧行回填为 `status='published'`（因其本就对外生效）、`document_version=1`、`source_type='legacy'`、`published_at=updated_at`。**不伪造新的 knowledge_version / 发布时间**（计划 F1 验收：老数据可读但不被错误标为新发布版本）。
- **D3（服务所有权）**：应用启动/容器只持有一个 `KnowledgeService`；管理 API、`KnowledgeEvidenceReader`、咨询工作流从容器取得同一实例。路由不得再 `KnowledgeService()`，不得依赖未定义 `app`。`api/knowledge.py` 的未定义 `app` 为本次必须修复缺陷。
- **D4（发布语义）**：索引只从 `published` 文档构建；候选版本预览标记 `preview: true`；发布在候选索引构建成功后原子切换；失败保留上一份 `published`；归档从新查询范围移除（软删/过滤）。
- **D5（旧接口策略）**：`/api/knowledge` 保留薄适配，末尾收敛到统一服务，不重复构造；删除条件记录到 F9 交接。
- **D6（身份）**：复用 `IdentityResolver`，仅演示身份；请求体 `user_id` 只作兼容校验，不作权限来源；真实管理员鉴权明确"未完成"。

## 3. 回滚点与不纳入清单

**回滚点**：F0 无代码改动（纯记录），基线 tag 沿用 `phase-a-baseline`；后续每个 F1-F9 提交为独立可回滚点。

**不纳入范围**（如实标记，非本阶段责任）：真实管理员鉴权与 RBAC、多租户/门店隔离、外部向量数据库、平台知识同步、复杂文档解析、生产级审核流、多进程索引刷新（单进程锁，多进程标记为未完成）。

## 4. F0 验收

- [x] 分支/工作区/全量测试复现（281/10/0）
- [x] 模型、Repository、服务实例、容器、旧咨询入口、管理页面盘点完成
- [x] 未定义 `app`、重复服务实例、索引刷新边界均已记录
- [x] 迁移方案、状态机、服务所有权、旧接口策略已锁定（D1-D6）
- [ ] 未定义 `app` 修复 → 归入 F2"修复路由内重复构造并修复未定义 app 依赖"执行

## 5. F1 交接要点

- 按 D1 扩展模型 + D2 迁移；定义状态机转换合法表；明确 `question+answer` 旧数据到新文档模型的迁移。
- 交付 `tests/test_knowledge_contracts.py`。
