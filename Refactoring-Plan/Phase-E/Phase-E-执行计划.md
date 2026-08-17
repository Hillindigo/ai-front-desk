# Phase E 执行计划：上下文、摘要与客户偏好

> 计划版本：2026-08-17
>
> 文档状态：执行中（E0 已完成，2026-08-17）
>
> 所属项目：`AIFrontDesk`
>
> 前置阶段：Phase D——统一 Agent 编排、工具边界与事件协议

## 0. 阶段定位与结论

### 0.1 阶段目标

Phase E 的目标不是把聊天记录简单塞进 Prompt，也不是建设一个不可解释的“长期记忆系统”，而是在 Phase D 的统一编排和 Phase B 的持久化会话之上，建立一条有边界、可恢复、可删除的上下文链路：

```text
持久化会话事实
    ↓
当前工作流与预约状态
    ↓
客户明确授权的偏好
    ↓
摘要覆盖范围与最近原始消息
    ↓
按预算裁剪的 ContextBuilder
    ↓
IntentRouter / Workflow / ConsultationTool
```

完成后：

- 长对话不会无限增长为一次模型请求的输入；
- 摘要有明确的覆盖起止序号、版本和失败回退路径；
- 预约草稿、待确认动作和已确认预约等业务事实不会只存在于摘要或 Prompt 中；
- 只有来源明确、用户表达或确认过的偏好才会进入长期上下文；
- 用户能够查看、修改和删除自己的偏好；
- 摘要失败、模型不可用或知识检索失败时，主对话仍有确定性降级路径；
- Phase D 的 SSE 事件协议、错误码和预约状态机不被重新定义。

### 0.2 当前代码事实

Phase E 开始前应以当前源码和 Phase D 交接记录为准，不能把路线图中的设想当作已经存在的能力：

1. `Conversation`、`Message` 已持久化，消息带有 `sequence`、`metadata_json` 和会话归属；同一会话由运行时锁串行处理。
2. `ConversationOrchestrator.handle_turn()` 是 Phase D 的唯一轮次编排入口，当前轮次会落用户消息和 assistant 消息。
3. `IntentRouter` 已支持向分类器传递有限的 `session_context`，但当前尚未形成统一的、带预算的 `ContextBuilder`。
4. `UserPreference` 表和旧 `user_behavior` 组件已经存在，但字段、仓储和 `PreferenceManager` 的语义并不完全一致；既有偏好不能未经来源审查直接当作可信长期记忆。
5. `KnowledgeService` 使用数据库文档和进程内 FAISS 索引，当前搜索结果主要是文档字典和相似度分数，尚未形成稳定的引用模型、统一阈值和重建并发边界。
6. Phase D 记录的交接结果为 **173 passed, 10 skipped, 0 failed**，Fake 模式下无真实 LLM/Embedding 请求；该结果是阶段归档证据，Phase E 的 E0 必须重新执行基线测试。
7. 当前 Web 页面只在 `localStorage` 保存 `conversation_id`，请求体固定发送 `user_id=default_user`；服务端通过 `SessionManager` 检查会话记录与该 `user_id` 是否一致。当前没有 Authorization、Cookie 身份或真正的鉴权，这只是演示身份与会话归属校验，不是身份真实性证明。
8. 当前验证边界仍是 SQLite、单进程、Fake 模式；真实模型、线上、鉴权和多进程能力不能由本阶段的本地测试推导出来。

### 0.3 Phase D 交接与范围处理

Phase D 交接了知识库相似度阈值、关键词预过滤、引用返回和重建锁四项问题。它们会影响上下文可信度，因此 Phase E 只完成与 ContextBuilder 和咨询工作流直接相关的最小封口：

- 统一检索结果的结构化证据模型；
- 增加可配置的最低相关度阈值和必要的关键词预过滤；
- 保证索引重建不会与查询并发交换出半成品索引；
- 将通过阈值的来源信息交给咨询工作流和上下文装配器。

知识库后台、复杂分块、索引版本治理和完整运营能力留给 Phase F，不在 Phase E 扩展。

### 0.4 E0 必须锁定的四个决策

这四个决策不应以“后续再看”的状态进入实现：

#### 决策一：上下文预算与裁剪优先级

采用配置化的 `ContextBudget`，至少包含：

```text
max_input_tokens
reserved_output_tokens
max_recent_messages
max_evidence_items
```

裁剪优先级固定为：

```text
当前用户输入
  > 当前预约/工作流事实
  > 已确认的安全约束（本阶段暂无来源，保留结构化字段）
  > 用户明确偏好
  > 摘要中的关键事实
  > 最近原始消息
  > 知识库证据
```

任何裁剪都不能删除当前轮输入、活跃预约状态或待确认动作。Token 估算器必须可替换；Fake 模式可以使用确定性估算，但不能把字符数永久冒充为供应商精确 Token 数。

#### 决策二：偏好的写入门槛

只自动持久化以下两类内容：

1. 用户明确表达“以后/通常/请记住”等长期意图，并得到用户可见确认；
2. 用户在预约、改约或其他业务流程中明确确认的稳定偏好。

单次聊天中的临时要求、模型推断、敏感健康信息和未确认的行为统计只能作为当前轮上下文或候选项，不能直接写入长期偏好。

#### 决策三：身份与删除边界

偏好读取、修改和删除必须使用服务端解析出的当前用户身份；不能信任请求体中的任意 `user_id` 覆盖会话归属。完整鉴权仍属于后续阶段，但本阶段必须保留可注入的身份解析边界。

当前无鉴权时，采用 `IdentityResolver` 抽象：本地演示适配器从服务端配置得到固定的 `DEFAULT_USER_ID`，测试通过依赖注入模拟多个用户；请求体中的 `user_id` 只作为兼容字段，必须与已解析身份一致，否则拒绝，不得据此切换身份。后续接入真实鉴权时，只替换 resolver，不改偏好和上下文服务的归属校验。

#### 决策四：同一类型偏好的更新语义

Phase E 选择**覆盖**而不是并存：同一 `user_id + preference_type` 最多有一个 active 值，`PUT /preferences/{preference_type}` 会原子地停用旧值并写入新值；不支持同一类型多值并存，也不把 PUT 解释为追加。未来若需要多值偏好，另定义列表型类型和接口，不能在本阶段混用两种语义。

#### E0 锁定的摘要触发默认值

摘要触发采用“较早者”策略：默认累计 **20 条会话消息**，或下一轮估算输入达到 `max_input_tokens` 的 **75%** 时触发；两个阈值都可配置，实际值在 E0 基线记录中固定。摘要成功后**不删除原始消息**，原始消息按现有会话数据保留策略保存。

删除后，偏好必须立即从 ContextBuilder、推荐和咨询上下文中排除。历史消息是否保留按会话数据规则处理，但不得通过摘要或缓存继续恢复被删除的偏好。

删除偏好采用“持久化墓碑 + 摘要失效 + 来源消息屏蔽”的确定性机制，不依赖自由文本中的定点替换：

1. 在同一事务中将偏好置为 inactive，并写入不可复用的 `PreferenceTombstone`（用户、类型、规范化值、原偏好 ID、来源消息/预约 ID、删除时间）。
2. 将受影响用户的有效摘要全部标记为 `invalidated`（至少覆盖当前会话；本阶段用户偏好是跨会话的，默认覆盖该用户的所有会话摘要）。自由文本摘要不做局部编辑，失效摘要保留用于审计但永不进入 `ContextPackage`。
3. 对墓碑关联的来源消息及其记忆引用消息建立 context-exclusion 标记；原始消息仍保留在数据库，但 `ContextBuilder` 不再把这些消息作为摘要或最近消息输入。
4. 下一轮 `ContextBuilder` 读取墓碑，排除 inactive 偏好、invalidated 摘要和被屏蔽消息，并附加仅供内部规则使用的 suppression 元数据；它不是向用户展示的“删除事件”，也不是唯一防线。
5. 下一次摘要只从未被屏蔽的原始消息和当前结构化事实生成，成功后产生新的有效摘要；在新摘要成功前继续使用“无摘要/最近未屏蔽消息”降级路径。

因此，“删除后下一轮不再生效”以失效和屏蔽为硬门槛，墓碑只是防止旧缓存、旧摘要或并发读取重新激活该偏好的持久化依据。

## 1. 必须达成的目标

### 1.1 统一上下文装配

新增 `ContextBuilder` 或等价应用层组件，负责把不同来源的数据组装为结构化上下文：

| 来源 | 是否必须 | 处理规则 |
|---|---:|---|
| 当前用户输入 | 是 | 原文保留，不参与摘要替代 |
| 当前工作流状态 | 是 | 结构化字段，不能只放自然语言 |
| 活跃预约/草稿 | 是 | 以 Phase C 领域数据为事实来源 |
| 用户明确偏好 | 否 | 仅使用未删除、未过期且来源可信的记录 |
| 会话摘要 | 否 | 带覆盖序号和版本，失败时可跳过 |
| 最近原始消息 | 是 | 至少保留可配置窗口，优先保留当前轮相关消息 |
| 知识库证据 | 否 | 只使用通过阈值的结构化检索结果 |

ContextBuilder 不直接写数据库、不调用 Agent、不生成隐藏推理；它只读取已确认数据并输出可审计的 `ContextPackage`。

### 1.2 会话摘要可恢复

摘要至少要记录：

- `conversation_id`；
- 覆盖的 `from_sequence`、`to_sequence`；
- 摘要正文；
- 结构化关键事实；
- 生成状态、版本、创建时间和更新时间；
- 使用的模型提供商或 Fake 标识；
- 失败原因的内部日志关联 ID，而不是原始供应商错误。

摘要必须满足：

- 只对已持久化消息生成；
- 覆盖范围连续且可验证，不能跳过消息；
- 新摘要成功保存前不覆盖旧摘要；
- 生成失败时保留原始消息，下一轮可以继续使用旧摘要和最近消息；
- 摘要成功后也不删除原始消息，原始消息仍是可回放和重新生成摘要的事实来源；
- 服务重启后能够从数据库恢复；
- 不把摘要当作预约、支付或身份事实的唯一来源。

### 1.3 长期偏好有来源、可管理

偏好记录至少要有：

```text
user_id
preference_type
preference_value
source
source_message_id / source_appointment_id
confidence
last_confirmed_at
expires_at（可选）
is_active / deleted_at
```

要求：

- 不同用户之间严格隔离；
- 同一类型的更新采用单 active 值覆盖语义，不支持同一类型多值并存；
- 低可信或历史兼容数据不能静默提升为已确认偏好；
- 查看、修改、删除操作可重复执行并返回稳定结果；
- 删除或撤回后不再进入新一轮上下文；
- 偏好内容不包含密钥、Prompt、内部日志和未脱敏的供应商响应。

### 1.4 轻量知识证据接入

检索结果统一为类似以下结构，不再把数据库文档字典直接透传给 Agent：

```json
{
  "document_id": 12,
  "category": "预约政策",
  "snippet": "如需取消或更改预约，请提前至少2小时通知我们。",
  "score": 0.82,
  "source_version": "index-7",
  "rank": 1
}
```

低于阈值的结果不能作为事实依据进入回答上下文；没有足够证据时，咨询工作流必须使用明确的“暂无可靠依据”路径，不能让模型自行补齐价格、政策或地址。

### 1.5 保持 Phase D 的编排和协议边界

- `POST /api/v1/conversations/{conversation_id}/turns` 继续使用事件协议 v1；
- 不新增公开的 chain-of-thought 或“记忆推理”事件；
- 摘要、偏好和检索失败统一映射为既有错误语义或内部旁路日志；
- 预约写入仍只经过 `AppointmentCommandService`；
- ContextBuilder 不绕过 Repository 直接操作 ORM 或数据库连接；
- 偏好、摘要和证据的写入失败不能回滚已成功的预约事务，除非本轮明确要求的用户可见偏好操作本身失败。

## 2. 明确不做的事情

1. 不建设通用向量记忆库、复杂 Agent Memory Graph 或独立记忆微服务。
2. 不用摘要替代 `Appointment`、`AppointmentEvent`、消息原文或其他结构化业务事实。
3. 不从用户的单次闲聊、模型猜测或敏感信息自动推导长期偏好。
4. 不在本阶段实现完整账号、角色、租户和生产级鉴权；只实现可测试的身份解析与会话归属边界。
5. 不把知识库后台、复杂文档切分、在线索引集群和多租户知识隔离混入本阶段。
6. 不为了摘要引入消息队列、分布式锁或后台任务平台；当前仍保持模块化单体。
7. 不修改 Phase D 的 SSE 事件类型、终止事件规则和 `run_id` 生命周期。
8. 不用 Fake 模型通过来宣称真实模型的摘要质量、线上延迟或生产可靠性。
9. 不删除旧 `user_behavior` 表或兼容组件，除非先完成引用审查、数据迁移和回归验证；历史数据默认按“来源未确认”处理。

## 3. 目标设计

### 3.1 `ContextPackage`

建议在 `application/` 中集中定义结构化模型：

```text
ContextPackage
  - conversation_id
  - user_id
  - current_input
  - workflow_state
  - appointment_facts
  - confirmed_preferences
  - summary
  - recent_messages
  - retrieved_evidence
  - budget
  - included_sources
  - omitted_sources
```

模型输入和内部审计信息分离：发送给模型的内容只包含允许公开给该工作流的字段；`included_sources`、裁剪原因和预算统计用于测试与日志，不能泄漏到用户文案。

### 3.2 上下文装配顺序

```text
1. 校验 conversation_id 与 user_id 归属
2. 读取当前工作流、活跃预约草稿和待确认状态
3. 读取未删除且来源可信的用户偏好
4. 读取最新有效摘要及覆盖范围
5. 从摘要覆盖点之后读取最近原始消息
6. 对当前问题执行必要的知识检索
7. 按 ContextBudget 裁剪非关键内容
8. 输出 ContextPackage，并记录版本和来源
```

上下文必须明确区分：

- **事实**：来自数据库实体或用户明确确认；
- **摘要**：对历史消息的压缩表达；
- **证据**：来自知识库且带文档 ID 与分数；
- **推断**：模型可生成的建议，不得回写为事实。

### 3.3 摘要生命周期

推荐的默认流程：

```text
消息累计达到阈值
  ↓
在会话锁内确定待覆盖序号范围
  ↓
读取结构化业务事实和未覆盖原始消息
  ↓
生成候选摘要
  ↓
校验覆盖范围、关键事实和敏感字段边界
  ↓
事务写入新版本
  ↓
ContextBuilder 使用新摘要，失败则保留旧摘要
```

摘要不能依赖“摘要 + 未覆盖消息”之外的不可追溯记忆。每次更新都要能够回答“这条摘要覆盖了哪些消息”。

### 3.4 偏好模型与兼容策略

现有 `UserPreference` 和旧 `PreferenceManager` 需要先盘点再收敛：

- 为新路径定义统一的偏好类型枚举和来源枚举；
- 旧字段如 `time_period`、`technician_type` 与新类型不一致时，通过适配器转换，不在 ContextBuilder 中散落兼容判断；
- 缺少来源、确认时间或用户归属的历史记录标记为 `legacy_unverified`；
- `legacy_unverified` 可展示，但默认不注入长期上下文，除非用户重新确认；
- Phase E 对同一 `user_id + preference_type` 只保留一个 active 值，PUT 采用覆盖语义；并存偏好留给未来独立的列表型契约；
- 清理操作必须按本文件的“持久化墓碑 + 摘要失效 + 来源消息屏蔽”流程执行：不编辑自由文本摘要，不删除原始消息，但必须让旧摘要和关联消息退出下一轮 `ContextPackage`；
- 摘要没有 `preference_id` 关联时，删除操作按用户维度使其失效，不能假设可以安全地只删某一段文字。

### 3.5 知识证据与索引并发

KnowledgeService 仍可使用进程内 FAISS，但必须满足：

- 查询读取一个完整且不可变的索引快照；
- 重建在新索引完成后原子替换引用；
- 查询不会读到 `document_ids` 与 FAISS 索引不匹配的中间状态；
- 结果经过阈值和可选关键词预过滤后才能进入 `RetrievedEvidence`；
- 文档删除或更新后，旧索引结果不能继续被标记为当前来源。

## 4. API 与用户交互方案

### 4.1 turns 接口保持兼容

继续使用：

```text
POST /api/v1/conversations/{conversation_id}/turns
```

上下文构建、摘要更新和偏好识别都属于 Orchestrator 内部工作流。不得要求前端上传完整历史、摘要或用户偏好作为事实来源。

对用户可见的结果通过普通 assistant 文案表达，例如：

- “我已记住你偏好的服务项目。”
- “这条偏好已删除，之后不会再用于推荐。”
- “我没有找到足够可靠的门店信息，建议向门店确认。”

不新增公开的内部摘要内容和隐藏推理事件。

### 4.2 偏好管理接口

建议提供稳定的管理接口，具体路径以现有 API 命名约定和身份适配器为准：

```text
GET    /api/v1/preferences
PUT    /api/v1/preferences/{preference_type}
DELETE /api/v1/preferences/{preference_type}
```

约束：

- 用户身份从会话或身份上下文解析，不从请求体覆盖；
- 写入请求包含值、来源说明和可选确认消息 ID，不能由客户端伪造高可信度；
- 删除返回幂等成功，不存在时不泄漏其他用户数据；
- 旧 `PreferenceManager` 只作为过渡适配器，不建立第二套持久化事实；
- 在未完成正式鉴权前，接口文档明确“本地演示边界”，不宣称生产可用。

### 4.3 内部摘要接口

本阶段默认不提供面向普通用户的原始摘要 CRUD 接口。测试和诊断可以使用内部 Service 或受限调试入口读取：

- 当前摘要版本；
- 覆盖到的消息序号；
- 生成状态；
- 预算和回退原因。

不得通过调试接口返回 Prompt、密钥、完整内部错误或模型隐藏推理。

## 5. 执行任务拆分

### E0：基线、交接和范围锁定

**目标：** 在不覆盖既有改动的前提下确认 Phase D 状态、Phase E 文件边界和历史偏好风险。

任务：

- 检查 `git branch --show-current`、`git status --short` 和最近提交；
- 重新运行 Phase D/B/C 全量基线，记录 passed/skipped/failed/warnings；
- 盘点 `Conversation`、`Message`、`UserPreference`、`KnowledgeDocument` 的实际字段和引用；
- 盘点现有摘要、偏好、上下文拼装和知识检索入口，确认哪些只是兼容代码；
- 盘点当前身份链路：前端 `localStorage` 的 `conversation_id`、请求体 `user_id`、`SessionManager` 的归属校验，以及没有 Authorization/Cookie 的事实；记录其为未鉴权演示身份，并固定 `IdentityResolver` 的本地适配方案；
- 固定 E0 的四个决策：预算、偏好写入门槛、身份与删除边界、同类型覆盖语义；
- 固定摘要默认触发值为“20 条消息或输入预算 75% 的较早者”，并记录超预算、重启和失败回退口径；
- 固定删除机制为持久化墓碑、用户摘要失效和来源消息屏蔽，不采用自由文本摘要定点删除；
- 建立历史偏好迁移/降级清单和摘要数据迁移方案；
- 确认 E1-E8 的文件范围、测试数据库和回滚点。

完成条件：有可复现的基线记录；四个决策（预算、偏好写入门槛、身份与删除边界、同类型覆盖语义）、摘要触发默认值与墓碑删除机制已固定并记录；旧偏好不会被误当作新可信记忆；没有把 Phase F 的知识库后台混入 Phase E。

### E1：上下文、摘要、偏好和证据契约

**目标：** 先定义结构化模型和稳定错误语义，再接入现有 Agent。

任务：

- 新增 `ContextPackage`、`ContextBudget`、`SummarySnapshot`、`PreferenceRecord`、`RetrievedEvidence` 等模型；
- 定义来源、可信度、删除、过期和裁剪原因枚举；
- 定义摘要覆盖范围、版本和唯一有效快照规则；
- 为 `PreferenceTombstone`、摘要失效状态和消息 context-exclusion 定义契约；
- 定义知识证据的最小来源字段和阈值配置；
- 固定同一类型偏好为单 active 值覆盖语义，补充 PUT 替换而非追加的契约测试；
- 为空输入、超预算、摘要不可用、偏好不存在、偏好归属错误和检索无依据建立契约测试；
- 不修改 Phase D 事件协议，只复用现有错误映射和 `tool_result` 结构。

完成条件：ContextBuilder、摘要服务、偏好服务和咨询工作流共享结构化契约，不再通过自由字典猜字段含义。

### E2：ContextBuilder 与预算裁剪

**目标：** 建立可测试、无副作用的上下文装配器。

任务：

- 在 `application/` 新增 ContextBuilder 及可注入的消息、预约、偏好、摘要和证据读取器；
- 实现固定的来源优先级和字段白名单；
- 实现可替换的 Token 估算器和确定性裁剪策略；
- 保证当前输入、预约关键事实、待确认动作不会被裁剪；
- 输出包含 included/omitted 来源和预算统计的 ContextPackage；
- 增加敏感字段、内部错误、旧协议标记和已删除偏好不进入模型上下文的测试；
- 验证 ContextBuilder 不创建长期数据库 Session、不写库、不调用其他 Agent。

完成条件：给定相同数据库状态、输入和预算，ContextPackage 稳定可复现；超预算时只裁剪允许裁剪的内容。

### E3：会话摘要服务与失败回退

**目标：** 让长会话可压缩、可追溯、可恢复。

任务：

- 新增 SummaryService 和摘要 Repository/迁移；
- 以消息序号确定摘要覆盖范围，禁止用时间戳猜测范围；
- 设计达到阈值后的触发策略，优先复用会话锁避免同一会话重复压缩；
- 生成候选摘要后先校验关键预约事实和覆盖连续性，再写入新版本；
- 摘要失败、模型不可用、校验失败或写入失败时保留旧摘要和原始消息；
- 服务重启后恢复最新有效摘要，损坏或不完整快照回退到上一版本；
- 记录摘要延迟、失败和覆盖范围日志，但不记录原始敏感内容。

完成条件：摘要成功不会丢消息；摘要失败不会阻断主对话；同一会话不会产生交错覆盖或重复版本。

### E4：偏好服务、管理接口和删除语义

**目标：** 收敛现有偏好实现，建立来源明确的长期偏好能力。

任务：

- 新增偏好 Repository/Service，统一类型、来源、可信度和删除语义；
- 为显式“记住”或用户确认的稳定偏好建立写入路径；
- 将旧 `PreferenceManager` 和 `UserBehaviorDBRouter` 通过适配器接入，避免新旧两套事实并存；
- 增加查看、覆盖、删除和重复请求测试；
- 删除在同一事务内写入墓碑、停用偏好并使受影响用户的有效摘要失效；
- 屏蔽墓碑关联的来源消息/记忆引用消息；原始消息不删除，但 `ContextBuilder` 不再读取为摘要或最近消息；
- 下一轮通过墓碑过滤 inactive 偏好、invalidated 摘要和被屏蔽消息；新摘要只在未屏蔽来源上重建；
- 验证不同 `user_id`、不同会话和不同门店边界不会串读偏好；
- 对缺少来源的历史记录执行 `legacy_unverified` 降级，不静默提升可信度。

完成条件：偏好 API 能查看、修改和删除当前用户的偏好；删除立即生效并通过墓碑/摘要失效/消息屏蔽机制验证；前端至少反馈成功、失败和删除结果；旧组件不再绕过新服务写入长期上下文。独立的偏好管理页面不是本阶段门槛。

### E5：Orchestrator 与工作流接入

**目标：** 让统一上下文真正参与分类、咨询和预约对话，同时保持 Phase D 边界。

任务：

- 在 `ConversationOrchestrator` 中通过依赖注入调用 ContextBuilder；
- 将结构化上下文传递给 IntentRouter、咨询 Workflow 和预约字段提取流程；
- 预约草稿、待确认预约和已确认预约始终以领域实体为事实来源；
- 将用户明确的偏好命令接入确定性路由或结构化工具，不让 LLM 直接写偏好；
- 摘要和偏好旁路失败不能把已成功的预约事务改写为失败；
- SSE 仍只输出已有协议事件，不输出摘要正文、隐藏推理和内部来源细节；
- 为同会话并发、重启恢复和上下文读取失败增加端到端测试。

完成条件：新旧工作流都通过同一个 ContextBuilder；不存在 Agent 自行读取全量消息或直接写偏好的旁路路径。

### E6：知识检索最小可靠性封口

**目标：** 为上下文中的知识证据提供可解释、可并发使用的最小保障。

任务：

- 为 `KnowledgeService.search()` 增加配置化相似度阈值和候选数量边界；
- 增加必要的关键词预过滤，但不得因宽泛关键词直接绕过相似度阈值；
- 将文档 ID、分类、片段、分数、索引版本转换为 `RetrievedEvidence`；
- 通过读写锁或不可变快照保证索引和 `document_ids` 一致；
- 更新、删除和重建后验证旧结果不会被标记为当前证据；
- 检索无结果或低于阈值时返回明确的无依据结果，不伪造咨询答案；
- 为索引初始化失败和 Embedding 不可用建立 Fake/故障注入路径。

完成条件：咨询上下文只接收通过阈值的来源；查询与重建不会读到半成品索引；完整知识库运营仍明确留在 Phase F。

### E7：前端最小交互与状态展示

**目标：** 让用户知道记忆操作是否成功，但不把内部上下文暴露到页面。

任务：

- 保持现有 `conversation_id` 传递和 SSE v1 解析逻辑；
- 为“已记住”“已删除”“无法保存”提供明确的用户可见文案；
- 偏好管理页面属于可选增强，不作为本阶段完成门槛；若提供，只展示当前用户自己的偏好及来源/更新时间等必要信息；
- 不在前端保存完整摘要、内部证据分数或未经授权的跨会话记忆；
- 验证断线、刷新和重新进入同一会话后，业务事实与偏好状态一致。

完成条件：前端不需要自行拼接历史或解释旧字符串协议，且记忆管理失败不会被显示为成功。

### E8：自动化测试、故障注入和 HTTP 验收

**目标：** 证明上下文能力可恢复而不是只在单个 happy path 中工作。

测试至少覆盖：

- ContextBuilder 来源顺序、预算裁剪和关键事实保留；
- 摘要覆盖连续性、版本更新、旧摘要保留和失败回退；
- 删除偏好后，墓碑、摘要失效、来源消息屏蔽和下一轮 `ContextPackage` 排除测试通过；
- 摘要生成后服务重启恢复；
- 同一会话并发摘要不会产生交错版本；
- 用户偏好隔离、来源、覆盖、删除和重复请求；
- 已删除偏好不进入摘要、缓存和下一轮 ContextPackage；
- 预约草稿/待确认/已确认事实不会被摘要覆盖或模型幻觉替换；
- 知识阈值、引用字段、索引重建并发和无依据降级；
- 模型不可用、Embedding 不可用、数据库写入失败和摘要校验失败；
- Phase B/C/D 全量回归、Fake 模式零真实模型请求、临时数据库隔离；
- 最小真实 HTTP turns、偏好管理接口和断线/重试边界。

完成条件：记录完整的 passed/skipped/failed/warnings，并将真实模型、线上、鉴权、多进程和摘要质量人工评测边界单独列出。

### E9：文档、提交和 Phase F 交接

**目标：** 让阶段结果、未完成项和知识库后续范围可追溯。

任务：

- 更新 README、`PROJECT_MEMORY.md` 和本文件的实际完成状态；
- 记录数据表、迁移、兼容适配器和删除语义；
- 记录每条自动化和 HTTP 验收证据及环境边界；
- 执行 `git diff --check`；
- 按独立功能提交，不提交工作区既有无关改动；
- 向 Phase F 交接知识来源模型、索引版本、阈值、重建并发边界和剩余后台需求。

## 6. 推荐执行顺序

```text
E0 基线、交接和范围锁定
  ↓
E1 上下文/摘要/偏好/证据契约
  ↓
E2 ContextBuilder 与预算裁剪
  ↓
E3 摘要服务与失败回退
  ↓
E4 偏好服务、管理接口和删除
  ↓
E5 Orchestrator 与工作流接入
  ↓
E6 知识检索最小可靠性封口
  ↓
E7 前端最小交互
  ↓
E8 自动化、故障注入和 HTTP 验收
  ↓
E9 文档、提交和 Phase F 交接
```

建议提交边界：

```text
Phase E(test)-建立上下文基线
Phase E(feat)-定义记忆与证据契约
Phase E(feat)-新增ContextBuilder
Phase E(feat)-实现会话摘要
Phase E(feat)-收敛客户偏好
Phase E(refactor)-接入统一上下文
Phase E(fix)-加固知识检索边界
Phase E(feat)-补充前端记忆交互
Phase E(test)-验证上下文恢复与删除
Phase E(docs)-记录PhaseE执行结果
```

每个提交围绕一个独立大块事项；不执行 `git reset --hard`、清理或覆盖既有用户改动。

## 7. Phase E 完成定义

### 7.1 功能完成

- [ ] 存在统一的 `ContextBuilder` 和结构化 `ContextPackage`。
- [ ] 上下文有配置化预算、固定优先级和可替换 Token 估算器。
- [ ] 当前输入、活跃预约、待确认动作和关键工作流状态不会因裁剪丢失。
- [ ] 摘要记录会话、覆盖序号、版本、状态和生成来源。
- [ ] 摘要成功可恢复，失败保留旧版本和原始消息，不阻断主对话。
- [ ] 用户偏好有来源、可信度、确认时间和删除语义。
- [ ] 偏好 API 可以查看、修改和删除当前用户的偏好；删除后下一轮通过墓碑、摘要失效和来源消息屏蔽立即不再使用；前端有成功/失败/删除文案反馈。独立偏好管理页面不作为门槛。
- [ ] 旧偏好数据经过来源审查，未确认记录不会静默成为长期事实。
- [ ] 统一编排链路使用 ContextBuilder，Agent 不自行拼装全量历史或直接写长期偏好。
- [ ] 知识证据带文档 ID、分数、分类和索引版本，并经过阈值过滤。
- [ ] 知识索引重建与查询不会产生索引/ID 映射不一致。
- [ ] Phase D 的 SSE v1、错误语义和预约领域状态机保持兼容。

### 7.2 测试完成

- [ ] Phase B/C/D 原有测试无新增回归。
- [ ] ContextBuilder、预算、摘要覆盖、版本和回退测试通过。
- [ ] 摘要重启恢复、同会话并发和故障注入测试通过。
- [ ] 偏好隔离、来源、覆盖、删除和重复请求测试通过。
- [ ] 预约事实不可被摘要或模型输出替换的测试通过。
- [ ] 知识阈值、引用、索引重建并发和无依据降级测试通过。
- [ ] Fake 模式下无真实 LLM/Embedding 请求，测试使用临时数据库。
- [ ] 既有 10 个 `user_behavior` skip 的状态和原因被准确记录。
- [ ] 通过 TestClient 和最小 HTTP 请求验证 turns、偏好接口和断线/重试边界。

### 7.3 运行和文档完成

- [ ] 新数据库可初始化，摘要和偏好数据的迁移/兼容策略已记录并验证。
- [ ] README、`PROJECT_MEMORY.md` 和本文件已同步实际接口、边界和完成状态。
- [ ] `git diff --check` 通过，阶段提交未混入无关工作区改动。
- [ ] 报告明确区分本地自动化、Fake 模式、真实模型、线上、鉴权和多进程证据。
- [ ] 未完成的摘要质量、真实模型稳定性、生产鉴权和多租户隔离不能标记为已完成。
- [ ] Phase F 的知识库后台、索引治理和运营能力交接项已形成清单。

## 8. 风险、回滚与边界

| 风险 | 影响 | 缓解与回滚 |
|---|---|---|
| 摘要遗漏预约关键事实 | 高 | 业务事实单独读取；摘要只做压缩；增加字段保留和回放测试 |
| 摘要覆盖范围出现空洞或重复 | 高 | 使用消息 `sequence`；事务写入覆盖边界；版本校验和并发测试 |
| 摘要失败阻断正常对话 | 高 | 摘要作为可降级旁路；保留原始消息和旧摘要；失败不伪造成功状态 |
| 历史偏好来源不明 | 高 | 标记 `legacy_unverified`；默认不注入；要求用户重新确认 |
| 删除偏好后仍从摘要或缓存恢复 | 高 | 同一事务写墓碑并停用偏好；用户摘要标记 invalidated；屏蔽关联来源消息；每轮构建检查 active/deleted/tombstone |
| 不同用户或会话串读偏好 | 高 | Repository 查询同时限定 user_id；身份不信任请求体；增加隔离测试 |
| ContextBuilder 变成新的隐式编排层 | 中 | 只读、无副作用、依赖注入；禁止调用 Agent 和写业务库 |
| Token 估算不准确导致上下文超限 | 中 | 估算器可替换；保留安全余量；真实供应商验证单独记录 |
| 知识索引重建读到半成品 | 高 | 不可变索引快照、原子替换和并发测试；失败保留旧索引 |
| 偏好写入与预约事务耦合 | 高 | 业务事务与记忆旁路分离；失败不回滚已成功预约 |
| 引入完整记忆基础设施造成范围膨胀 | 中 | 只做单体内轻量摘要/偏好；向量记忆和知识后台延期到后续阶段 |
| 本地 Fake 结果被误报为长期记忆质量 | 中 | 将摘要质量、真实模型和线上行为列为待验证；补充人工评测计划 |

回滚原则：

1. ContextBuilder 接入、摘要触发和前端交互按独立提交，可单独回退到 Phase D 的稳定编排路径。
2. 摘要写入失败时优先关闭摘要触发或恢复旧快照，不删除原始消息。
3. 偏好迁移无法证明来源或数据安全时，保留旧数据只读并停止自动注入，不静默转换。
4. 知识阈值或索引快照改造出现回归时，只回退知识证据适配，不回退已验证的会话、预约和事件协议。
5. 不通过把全部历史重新塞进 Prompt、把偏好放回进程全局变量或恢复 Agent 直接写库来伪造 Phase E 完成。

## 9. Phase E 交接到 Phase F

Phase E 完成后，应向 Phase F 输出：

- `ContextPackage`、`ContextBudget`、`SummarySnapshot` 和 `RetrievedEvidence` 的最终契约；
- 摘要表、偏好表的字段、索引、迁移和删除记录；
- 偏好墓碑、摘要失效和来源消息屏蔽的最终实现；阈值/引用能力已由 Phase E 建立，Phase F 只在此基础上扩展知识库治理，不重复实现；
- 摘要覆盖范围、版本、失败回退和并发锁的实际测试证据；
- 偏好来源、确认、过期、删除和历史兼容数据的最终状态；
- 知识检索阈值、关键词预过滤、引用字段、索引版本和重建并发边界；
- 当前只在 SQLite、单进程、Fake 模式下验证的边界；
- 未完成的真实模型摘要质量、线上/鉴权、多租户和知识库运营需求；
- Phase F 需要继续处理的知识文档管理、索引刷新、相关度评测、引用展示和无依据回答治理。

Phase F 不应重新实现会话摘要、偏好删除或预约事实保护，而应在 Phase E 的上下文和证据边界上建设可维护的知识库与回答可信度能力。

## 10. Phase E 执行结果

### E0 基线、交接和范围锁定（2026-08-17）

**工作区门禁**：
- 分支 `dev`；未跟踪改动 = `Refactoring-Plan/Phase-E/`（计划文档本体，评审后补完 E0 完成条件）；无其他无关改动。
- 回滚点：`0ef89bc`（Phase D 最后提交 `Phase D(docs)-更新修复验收结果`）。

**基线测试（重新执行）**：**173 passed, 10 skipped, 0 failed, 40 warnings**（6.70s），与 Phase D 交接记录完全一致；Fake 模式零真实 LLM/Embedding；测试数据库为 `tmp_path_factory` 临时 sqlite（conftest.py 既有 fixture），无污染。

**模型盘点（db/models.py）**：

| 模型 | 关键字段 | Phase E 相关结论 |
|---|---|---|
| `Conversation` | id、user_id（default 'default_user'）、channel、status、active_workflow | user_id 维度可用于偏好归属 |
| `Message` | id、conversation_id、role、content、message_type、metadata_json、**sequence**、created_at | `sequence` 已存在，摘要覆盖范围可直接使用 |
| `UserPreference` | id、user_id、preference_type（technician/time/service/duration）、preference_value、confidence_score（出现次数）、last_updated | **缺 source/last_confirmed_at/expires_at/is_active/deleted_at**；现有记录默认按来源未确认处理 |
| `KnowledgeDocument` | id、content、category、keywords(JSON)、embedding(JSON)、is_active（软删） | 检索结果需转 `RetrievedEvidence` |
| `UserBehavior` | user_id、action_type、action_data(JSON)、session_id | A-R2 延期组件，10 个 skip 来源，不动 |

**入口盘点**：
- **无现成 ContextBuilder / SummaryService**；`application/message_buffer.py` 的 `ChatHistoryBuffer` 只是进程内列表（InputParser 兼容层），持久化由 ConversationRepository 负责——摘要服务为全新建设。
- **偏好相关集中在 user_behavior 组件 9 个文件**（pattern_analyzer / preference_manager / user_behavior_agent / user_behavior_service / user_behavior_repository / db/base/interfaces / db/__init__ 等），E4 通过适配器收敛。
- **KnowledgeService.search()**：FAISS IndexFlatIP 进程内索引，top_k 默认 3（候选 2 倍），**无阈值、无引用模型、无重建锁**；add/update/delete 均全量重建索引——E6 四项封口全部空缺，确认最小改造范围。
- **上下文拼装现状**：`session_context` 仅传入 IntentRouter 分类器（orchestrator.py classify），无预算裁剪。

**身份链路盘点**：
- 前端 localStorage 保存 `conversation_id`；请求体 `ChatRequest` 含 `user_id`（默认 `"default_user"`，客户端可任意填写）。
- `SessionManager.get_or_create_session(conversation_id, user_id)` 做归属校验，不一致抛 `PermissionError`——校验基于请求体 user_id，**不是身份真实性证明**；无 Authorization/Cookie。
- 结论：固定 `IdentityResolver` 本地演示适配器方案（0.4 决策三）：身份从服务端配置解析 `DEFAULT_USER_ID`，请求体 user_id 只作兼容字段必须一致，后续鉴权只替换 resolver。

**决策与默认值固定**：四个决策（预算与裁剪优先级 / 偏好写入门槛 / 身份与删除边界 / 同类型覆盖语义）、摘要触发默认值（20 条消息或输入预算 75% 较早者）、墓碑删除机制——已在 0.4 锁定，E1 起按此实现。

**历史偏好迁移/降级清单**：
- 现有 `user_preferences` 行缺少来源与确认时间 → 标记 `legacy_unverified`，可展示不注入；
- `preference_type` 现有枚举（technician/time/service/duration）与新类型枚举映射关系在 E1 契约中定义；
- 无既有摘要表/墓碑表 → 全新建表，无存量迁移负担（E3/E4 按新表初始化）。

**E1-E8 文件范围（规划）**：`application/`（contracts.py 扩展、context_builder.py、summary_service.py、preference_service.py、identity.py）、`db/models.py` + `db/repositories/`（摘要/偏好/墓碑仓储）、`services/knowledge_service.py`（E6）、`api/`（preferences 路由）、`application/orchestrator.py`（E5 接入）、`web/` 前端文案（E7）、`tests/`（契约/故障注入/HTTP 验收）。测试数据库沿用 conftest 临时 sqlite，回滚点 `0ef89bc`。

**E0 完成条件核对**：
- [x] 有可复现的基线记录（173/10/0/40）；
- [x] 四个决策、摘要触发默认值与墓碑删除机制已固定并记录；
- [x] 旧偏好不会误当作新可信记忆（legacy_unverified 降级路径已定）；
- [x] 没有把 Phase F 的知识库后台混入 Phase E（E6 仅最小封口）。

以下小节在 E1-E9 实际执行后继续填写。
