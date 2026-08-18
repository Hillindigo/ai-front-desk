# Phase I I0：基线与决策记录

> 日期：2026-08-18
> 分支：`dev`（当前仅新增 `Refactoring-Plan/Phase-I/`，未合并、未推送）
> 状态：I0 基线、威胁模型、支持拓扑与破坏性操作规则锁定；可进入 I1
> 依据：`Refactoring-Plan/Phase-I/Phase-I-执行计划.md` §1；`PROJECT_MEMORY.md` §10 Phase H 交接
> 本文件为 I0 交付物；本期如调整数据删除语义、支持拓扑或质量口径，先更新本文件再动工。

---

## 1. 基线快照与验证证据

### 1.1 Git 基线

- 当前分支：`dev`（未合并、未推送；HEAD 在 Phase H 收尾提交 `21f707f Phase H(fix)-修复接管事务一致性` 之后）。
- I0 开工 `git status --short`：仅 `?? Refactoring-Plan/Phase-I/` 一个 untracked 目录，无 modified 文件，工作区干净。
- `git diff --check`：通过（无空白错误）。

### 1.2 验证与测试基线（I0 实测）

```text
git branch --show-current                 # dev
git status --short                        # ?? Refactoring-Plan/Phase-I/
python -m pytest -q                       # 427 passed, 10 skipped, 349 warnings in 39.35s
git diff --check                          # 通过
```

- 对比 Phase H 收尾记录的 `422 passed, 10 skipped`：新增 5 个（含上游文档流程/契约补测试）。
- 349 条 warning 主要为 SQLAlchemy `declarative_base()`、FastAPI `on_event` 弃用提示（`app.py:118`），非功能失败，I0 记录为已知噪音，不视为门禁阻塞。

### 1.3 当前代码事实（源码核对，作为 I1~I7 基线）

| 现状 | 证据 | 对 Phase I 的含义 |
|---|---|---|
| 配置层极简 | `config/settings.py` 仅管理 `CORS_ORIGINS`（默认本机 127.0.0.1:8001 / localhost:8001）；模型提供商经 `config/model_provider.py` 环境变量切换（azure 默认，qwen/deepseek/zhipu/openai 兼容，fake 离线） | I1 需建立生产配置门禁（会话密钥、允许来源、模型配置缺失即拒启）；当前无集中 secret/强校验 |
| 无健康/就绪端点 | 全仓 grep `health|ready|/health` 无匹配 | I3 需新增健康/就绪检查（DB、迁移、知识版本、后台任务） |
| 会话安全已部分建立 | Phase G 已实现 secure cookie 可配置（`ADMIN_COOKIE_SECURE`）、CSRF cookie 独立、登出删 CSRF cookie；商家权限/门店 scope/审计见 Phase G | I1 在其上做生产加固与越权回归，不重做身份模型 |
| 存在 Prompt/输入泄漏防护雏形 | `application/context_contracts.py:441` 对 `sk-`/`bearer `/`api_key`/`secret` 做模型输入过滤 | I2/I3 需扩展为结构化、可测的日志/指标脱敏 |
| 知识的版本协议已存在 | `services/knowledge_contracts.py` 含 `knowledge_version/source_version/index_version`、快照原子替换；`application/container.py` 单实例持有；**单进程锁发布，多进程未完成** | I4 在既有版本协议上实现多进程版本检测/刷新/降级，不重写发布流水线 |
| 评测仅有知识质检雏形 | `evaluation/`：`run_knowledge_eval.py`（默认 fake）、`knowledge_eval_cases.py`、`knowledge_evaluation.py`、`quality_template.md` | I5 扩展为四类评测（意图/字段/RAG/摘要）并版本化资产 |
| PII 现状有限且集中 | 无 `id_number/ssn/id_card/birthday` 等客户列；仅门店 profile 有 `address/phone`（`db/models.py:219-220`、`services/store_config.py`） | I2 起点较低，但仍需完整数据流盘点（含消息原文、grep 日志、备份、导出）并定义删除/匿名化边界 |
| 无备份/恢复/部署资产 | 顶层仅 `requirements.txt`；无 `Dockerfile`、`Makefile`、`.env.example`、`.env`、备份脚本、`scripts/` 内容待盘点 | I6/I7 需从零建立可恢复备份、脚本、干净环境部署与恢复演练 |

> 结论：Phase I 不是修一个已收敛到位的系统，而是要把一个已可演示闭环但**尚无生产安全门禁、观测、评测基线和恢复资产**的原型补成“可运行、可度量、可恢复”。起点越低，越需坚持先锁基线再动工。

---

## 2. 未提交改动归属

I0 开工时工作区仅有一个 untracked 目录（本阶段文档），全部归属 Phase I，无待裁定改动。后续 I1~I7 提交前均复核 `git branch`/`git status`，只提交当期白名单文件。

---

## 3. 资产与信任边界

```text
[买家浏览器]──HTTPS──┐
                     ├→ FastAPI (Uvicorn) ─→ SQLAlchemy ─→ SQLite(data/)
[商家浏览器]──HTTPS──┘        │        └→ SQLAlchemy ─→ 门店/预约/偏好/审计表
                     ↓        └→ KnowledgeService ─→ FAISS 索引（进程内）
                     ↓        └→ LLM / Embedding 提供商（外部 API）
                     ↓
              [备份文件 / 日志 / 临时导出 / 上传]（磁盘，敏感）
```

信任边界要点（I1~I3 必须守住）：

1. 浏览器侧永不作为权限/门店/业务事实来源（H0 已锁，Phase I 全期生效）。
2. 外部 LLM/Embedding 提供商是**不可信输入 + 不可信输出**：输入侧防 Prompt 泄漏，输出侧不信任其结构化结果，关键业务校验必须在确定性代码内。
3. LLM API Key、会话 Cookie 等机密只在进程内存/操作系统凭证环境，不得进入日志、响应、审计、数据库明文或 Git。
4. SQLite、FAISS 索引、日志、备份、导出均为磁盘敏感资产，需纳入脱敏与备份/清理策略。
5. 故障/依赖不可用时，健康检查不得返回伪健康，知识检索不得静默用旧版本冒充当前事实。

---

## 4. 威胁清单与控制归属

| 威胁 | 风险等级 | 现有防护 | I 阶段处理归属 |
|---|---:|---|---|
| 越权读取/操作其他买家或门店数据 | 高 | `conversation 归属`、商家 RBAC + active_store scope | I1 越权回归 + I2 导出/删除 scope |
| 未登录/跨门店/CSRF 访问管理接口 | 高 | Cookie/CSRF、`/api/v1/admin` 前缀 | I1 生产加固与安全回归 |
| 缺省密钥、调试错误页、配置回显 | 高 | 无集中门禁 | I1 安全配置校验/启动门禁 |
| 日志/错误/响应泄漏 Prompt 或密钥 | 高 | 部分（`context_contracts` 过滤、错误脱敏雏形） | I2/I3 结构化脱敏 + 关联 ID |
| 关键竞态：轮次/预约/接管/发布并发 | 高 | `client_request_id`、预约 `idempotency_key`、`BEGIN IMMEDIATE`、控制三态、单进程发布锁 | I6 并发/故障注入 |
| 重启后状态丢失或不一致 | 高 | 会话/工作流/预约持久化、知识版本恢复 | I6 重启/恢复演练 |
| 多进程旧知识索引静默回答 | 中 | 单进程边界、版本协议已存在 | I4 多进程同步 + stale/degraded 降级 |
| 备份不可恢复或泄漏 | 高 | 无 | I6 一致性备份/隔离恢复 + I2 备份脱敏/清理 |
| 删除/匿名化遗漏导致 PII 复活 | 高 | 无（尚未实现删除） | I2 数据流盘点 + 级联策略 + 恢复验证 |
| 真实模型质量无评测导致回归 | 中 | Fake 契约测试 + 知识质检雏形 | I5 版本化评测集 + 人工/真实模型分栏 |
| 部署步骤不可复现 | 中 | README 基础 | I7 干净环境部署演练与运维手册 |

---

## 5. 支持拓扑（I0 决策）

**D1 — 默认支持拓扑：单实例 FastAPI 进程 + SQLite + 进程内 FAISS 索引。**
- 当前 `knowledge` 单进程锁发布、容器单实例持有，是唯一被验证的语义。
- I4 之前，部署文档、README、验收不得宣称“多进程知识一致性”。

**D2 — 多进程边界：I4 达成前，任何第二个进程的索引一致性问题都标记为“不支持/待验证”。**
- 若 I4 选择支持有限多进程，必须：持久 `knowledge_version` 为事实源、轻量版本检测 + 本进程原子重建、刷新失败保留旧快照并标记 `stale/degraded`、高风险知识拒绝确定性回答转人工。
- 单进程也不掩盖：进程内索引必须与持久版本一致，启动/健康检查暴露版本信息。

**D3 — 不引入超出当前规模的分布式基础设施**：无消息队列、无外部缓存集群、无 Kubernetes 编排；备份用文件系统一致性快照。除非真实第二渠道/多进程需求证明必要性，不预建。

---

## 6. 数据与破坏性操作规则（I0 决策）

**D4 — 删除/匿名化通用护栏**：
- 删除语义分为两种，禁止混用：**硬删**（物理移除，仅限名义上应消失的临时/可重建数据）与**匿名化/去标识化**（保留审计与业务事实但不可逆移除直接标识符）。
- 所有批量导出、删除、匿名化、清理、备份恢复命令默认 **dry-run**；真实执行需带明确授权标志并在隔离副本验证后运行。
- 每个批次必须输出逐实体结果清单（处理数量、ID、状态、失败原因）并写审计事件。
- 破坏性迁移前必须有可恢复备份，禁止无备份迁移。

**D5 — 备份/恢复底线**：
- 备份须包含：SQLite 数据库文件（以一致性方式拷贝，不复制正在写入的库）、知识源/上传（如有）、版本清单（schema 版本、知识版本、备份时间）。
- 备份有效性以“能恢复到隔离目录并跑通迁移 + 核心验收查询”为准，不以文件存在验收。
- 备份文件按敏感数据处理：不写入 Git、遵循保留/清理策略、恢复演练在隔离临时目录完成。
- I6 记录当前实测 RPO（备份间隔）与 RTO（恢复耗时），单实例限制不承诺商业级 SLA。

**D6 — 数据保留与清理**：清理任务默认 dry-run + 审计；在线数据、日志、临时导出、上传、备份各自设定保留期限（I2 确定具体数值口径）。

---

## 7. 指标、评测与质量口径（I0 决策）

**D7 — 指标字典初稿（I3 细化）**：请求成功率、P95 延迟、模型失败率、知识拒答率、转人工率、单轮估算 token/成本、索引版本落后时间、备份过期天数。指标标签不绑定 PII。

**D8 — 评测口径（I5 细化）**：
- Fake 模式只验证契约、控制流、确定性指标；真实模型/人工评测作为独立运行与独立记录。
- 评测资产（意图、字段提取、RAG、摘要）只使用合成/授权脱敏样本，不混入生产客户原文。
- 同一版本代码 + 模型 + Prompt + 知识版本必须可重复生成结果。
- 未执行真实模型评测时，结果一律标注“待验证”，不得用 Fake 通过冒充语义质量。

---

## 7A. 评审后增补决策 D9–D14（2026-08-18）

> 依据 Phase I 计划评审意见补齐；D9、D10、D11 为**红色**机制缺口，必须在进入 I2/I6 前落实。

**D9 — 身份治理边界（客户导出/删除仅商家侧发起）**

- Phase I 的客户导出、删除、匿名化**仅由已登录商家发起**，受 RBAC、active store scope、CSRF 保护；客户主体以门店范围内稳定 `customer_user_id` 标识。
- 买家端维持 `DemoIdentityResolver`（固定默认用户）演示边界：**不宣称生产级买家身份隔离，不提供买家自助导出/删除入口**。
- 任何接口不得以请求体 `user_id` 作为授权依据；未落地买家服务端身份令牌前，买家归属/自助数据权利一律标注“不支持/待验证”。
- 此项消除“买家归属验证 + 自助删除”的语义悬空，并把 I2 范围收敛为商家侧客户治理。

**D10 — 删除与备份恢复闭环（防 PII 复活）**

- 服务端对删除动作建立**删除登记/抑制记录**：只含不可逆标识或最小必要信息，不含原始 PII。
- **从旧备份恢复时，不得让已删除 PII 复活**：对外服务前必须按登记重放删除/匿名化 → 重建摘要/偏好/知识索引 → 通过“被删除信息不可查询、不可注入”验收。
- 新增验收场景：*创建客户数据 → 制作备份 → 执行客户删除 → 从删除前备份恢复 → 重放删除登记 → 验证全部公开入口及 ContextBuilder 均无法重新获得该客户 PII*。
- 审计与删除登记独立保留、到期可清、记脱敏审计，不留不可追溯的永久 PII。

**D11 — 导出/备份文件级安全**

- 导出/备份文件仅置于专用目录，文件名由服务端生成，最小文件权限；禁止静态目录直接暴露。
- 下载需重新授权或**短时一次性令牌**，到期即失效并清理；下载/过期均记脱敏审计。
- 导出有明确 schema（按 D14 字段白名单），禁止整库 dump。
- 生产备份（SQLite、上传、知识源）须定义**静态加密方案与密钥分离**；备份清单记录 schema 版本、知识版本、时间与**校验和**；恢复前先校验完整性再迁移。
- 自动化测试覆盖：路径穿越、跨门店下载、过期文件访问失败。

**D12 — 启动-就绪语义（配置失败 vs 依赖降级）**

| 状态 | 行为 |
|---|---|
| 配置校验失败（缺会话密钥/允许来源/必要模型配置） | 拒绝启动 |
| 数据库/迁移失败 | 拒绝启动，或 ready=失败 |
| 知识索引无法加载 | 进程存活，ready=失败，不静默用旧版 |
| LLM/Embedding 暂时不可用 | 进程存活；ready 按产品策略，咨询路径明确降级/转人工 |

- `/health/live` = 进程存活；`/health/ready` = 可承接当前支持范围请求。
- 与现有 `initialize_system()` 降级启动并存，I1 用“必要模型配置”精确清单区分配置错误与外部依赖暂不可用，避免把暂态模型故障变成整个应用重启循环。

**D13 — 关联 ID 信任边界**

| ID | 来源 | 用途与约束 |
|---|---|---|
| `request_id` | 服务端生成 | 每 HTTP 请求唯一，贯穿链路/审计 |
| `client_request_id` | 客户端 | 轮次幂等/重试键；长度+字符校验；**不替代服务端关联 ID** |
| `run_id` | 服务端 | Agent 执行实例；replay 时引用 `original_run_id`，不把客户端键当运行身份 |
| `idempotency_key` | 客户端可选 | 仅用于对应业务命令（预约等） |

- 外部 `X-Request-ID` 至多作 `external_request_id` 经校验后保存，不替代 `request_id`。
- 指标标签禁止 PII；高基数客户端 ID 不直接入指标标签。

**D14 — PII 数据字典（逐表/逐字段）**

I2 落盘时必须产出逐表/逐字段矩阵，覆盖但不限于：`messages.content`、`Message.metadata_json`、`conversation_summaries.content/key_facts`、`preferences.preference_value`、`preference_tombstones.normalized_value`、`appointments`、`AppointmentEvent.payload_json`、`follow_up_tasks`、`audit_events.summary_json`、`conversation_control_events.content`、`user_behaviors.action_data`、旧 `user_preferences`、`user_recommendations`、`store_profiles.address/phone`、知识文档内容、评测/运行失败样本。

每字段标注：PII 类别 / 主体键 / 门店键 / 展示范围 / 日志规则 / 删除动作 / 匿名化动作 / 保留理由 / 恢复后动作。

---

## 8. I1 前置门槛（已满足检查项）

- [x] P0 威胁均在本文件 §4 给出处理归属与测试方法。
- [x] 支持拓扑（单实例默认、多进程边界）在 §5 锁定。
- [x] 数据删除/匿名化、备份、保留、破坏性操作规则在 §6 锁定。
- [x] 指标与评测口径在 §7 锁定；破坏性操作均默认 dry-run。
- [x] 测试基线、Git 基线与代码事实在 §1 实测记录，工作区归属干净。
- [x] 不重做 Phase H Web 主路径；仅在安全、隐私、观测、评测、恢复上动工。
- [x] 评审后增补决策 D9（商家侧客户治理身份边界）、D10（删除↔备份恢复闭环）、D11（导出/备份文件安全）、D12（启动-就绪语义）、D13（关联 ID 信任边界）、D14（PII 数据字典矩阵）在 §7A 锁定。

**结论：I0 基线已锁定，期初“评审后增补决策”D9–D14 已补齐，可进入 I1（生产配置与安全加固）；I1 前暂不进入 I2 的导出/删除与 I6 的备份恢复实现（受 D9/D10/D11 门禁约束）。**
