# Phase B 执行计划：会话隔离与消息持久化

> 项目：AI Front Desk  
> 阶段：Phase B  
> 日期：2026-08-17  
> 状态：已完成（2026-08-17）
> 前置基线：`phase-a-baseline`  
> 上一阶段记录：`Refactoring-Plan/Phase-A/Phase-A-执行计划.md`  
> 本文定位：记录 Phase B 的问题分析、执行计划、实际结果和 Phase C 交接边界。

## 0. 阶段结论

Phase A 已经建立了 Fake LLM、API 契约测试、CORS 配置和日志基线，但没有改变项目的核心会话模型。当前聊天入口仍然存在全局 Agent、全局会话标识和进程级业务状态，服务重启后也无法从数据库恢复完整的对话上下文。

因此 Phase B 的核心不是“增加一个会话参数”，而是完成以下边界迁移：

```text
全局 Agent / 进程内状态
        ↓
按 conversation_id 管理的会话对象
        ↓
conversations + messages 持久化
        ↓
服务重启后按会话恢复
```

Phase B 的完成结果应当是：两个不同会话不会互相看到消息、预约草稿或 Agent 状态；同一会话在服务重启后能够恢复最近上下文；现有页面仍然可以通过兼容接口工作。

## 1. Phase A 遗留问题审查

### 1.1 遗留问题总表

| 编号 | 问题 | 严重度 | Phase B 决策 | 理由 |
|---|---|---:|---|---|
| A-R1 | 默认模型配置缺失时，startup 初始化可能直接失败 | P1 | 纳入最小启动降级 | Phase B 需要反复启动、停止和验证会话恢复；模型不可用不应阻断会话层测试 |
| A-R2 | `user_behavior` 测试与实现 API 脱节，已整体 skip | P1 | 延期，保留审计记录 | 行为组件不是 Phase B 的核心边界，强行修复会扩大范围；但必须确认会话改造不依赖该坏接口 |
| A-R3 | 分类、预约、咨询接口存在已固化的 400 缺陷，服务人员路径命名不一致 | P1 | 延期到统一 API/编排阶段 | Phase B 先提供会话包装和消息持久化，不重新设计所有业务 API，避免与 Phase C/D 冲突 |
| A-R4 | 测试共享 `data/ai_front_desk.db`，测试之间可能互相污染 | P0 | Phase B 前置任务，必须修复 | 没有测试隔离就无法可信地证明多会话隔离和服务重启恢复 |
| A-R5 | `data/` 被忽略且没有 `.gitkeep`，首次运行可能缺少目录 | P2 | 纳入基础设施小修复 | 会影响新环境启动和测试；处理成本低，不应继续作为隐性前置条件 |

### 1.2 A-R1：模型不可用时的启动降级

**现状：** 默认 `MODEL_PROVIDER=azure` 且缺少 API key 时，应用 startup 初始化可能抛出 credentials 错误，整个应用无法启动。

**Phase B 修复范围：** 只处理启动边界，不重构模型提供商：

1. 将模型/知识库初始化失败与 FastAPI 应用本身启动解耦。
2. 记录结构化错误日志，避免输出密钥或完整敏感配置。
3. 应用可以启动并返回明确的“模型或知识库不可用”状态。
4. Fake 模式下保持当前测试行为，不改变已有契约测试的响应形状。
5. 对依赖真实模型的聊天请求返回稳定的内部错误，而不是抛出未处理异常。

**不在本阶段处理：** Azure provider 重构、多模型路由、真实模型评测、生产级健康检查体系。

**验收：** 无真实模型配置时应用能启动；Fake 模式全量测试仍通过；不可用依赖被记录且不会造成进程崩溃。

### 1.3 A-R2：用户行为组件 API 脱节

**现状：** `user_behavior` 测试因 `record_behavior` 签名、方法名和返回结构不匹配而整体 skip，`BehaviorRecorder` 与 Repository 之间还存在参数缺失问题。

**Phase B 决策：** 不在本阶段重写行为组件，但必须做边界检查：

- 会话和消息持久化不能调用未验证的行为 API 作为必要步骤。
- 如果当前聊天流程会自动记录行为，必须将其改为非阻塞旁路：行为记录失败不能回滚用户消息或破坏聊天主流程。
- 在 Phase B 测试中明确行为记录不是会话恢复的前置条件。
- 保留 skip 原因，后续由行为组件专门阶段重写测试和接口。

**延期原因：** 行为 API 收口会涉及用户偏好、历史行为和推荐逻辑，属于独立领域问题；与 Phase B 的会话消息主链路混在一起会降低可验证性。

### 1.4 A-R3：已有 API 缺陷

**现状：** 已固化的缺陷包括：

- `POST /api/task/classify` 的请求字段与 handler 访问字段不一致。
- `POST /api/appointment/create` 调用了不存在的方法。
- `POST /api/consultation/ask` 调用了不存在的方法。
- 服务人员接口存在复数 prefix 与页面/文档单数用法不一致。

**Phase B 决策：** 保留现状作为已知基线，不在本阶段全面修复。Phase B 只新增会话 API，并为旧聊天入口提供薄兼容包装。

**防回归要求：**

- 新会话 API 不得复用这些已知错误的 handler 作为核心恢复逻辑。
- Phase B 新增测试应区分“会话层契约通过”和“业务 API 缺陷仍未修复”。
- Phase B 计划和结果中不得把这些 400 接口标记为已完成。
- Phase D 统一 API/编排时再修复，并同步前端、README 和契约测试。

### 1.5 A-R4：测试数据库隔离

**现状：** 测试直接写入共享的 `data/ai_front_desk.db`，已有用户行为数据残留，测试运行顺序可能影响结果。

**Phase B 修复方案：**

1. 为测试提供独立的临时 SQLite 数据库路径。
2. 每个测试函数或测试模块根据隔离需求创建数据库，并在结束时清理。
3. 应用、Repository 和测试 fixture 使用同一个可注入的数据库配置，不在业务代码中硬编码测试路径。
4. 多会话测试必须显式创建两个不同的 conversation，并断言数据库中的 `conversation_id` 归属。
5. 对服务重启恢复测试，使用同一临时数据库执行“创建应用 → 写入消息 → 销毁应用 → 创建新应用 → 恢复”的完整链路。

**验收：** 单独运行、反向顺序运行和全量运行时，测试结果一致；测试不污染仓库中的本地数据库。

### 1.6 A-R5：`data/` 目录初始化

**Phase B 最小处理：**

- 统一数据库路径解析。
- 应用启动或数据库初始化前确保父目录存在。
- 测试环境使用临时目录，不依赖仓库中的 `data/`。
- README 或启动说明补充首次运行行为。
- 不提交真实 SQLite 数据和演示客户数据。

**验收：** 新 checkout 在没有预先创建 `data/` 的情况下，可以在 Fake 模式启动和运行测试。

## 2. Phase B 目标

### 2.1 必须达成的目标

1. 引入可持久化的 `Conversation` 记录，明确用户与会话归属。
2. 引入可持久化的 `Message` 记录，保存用户消息和 Agent 消息。
3. 移除聊天主链路对全局 `task_agent`、全局 session ID 和全局预约草稿的依赖。
4. 每个会话使用独立的运行时对象、消息列表和并发锁。
5. 服务重启后可以根据 `conversation_id` 恢复最近消息和会话状态。
6. 新增版本化会话 API，同时保留 `/chat/stream` 兼容包装。
7. 前端保存并传递 `conversation_id`，旧页面仍能聊天。
8. 用自动化测试证明多会话隔离、恢复和并发边界。

### 2.2 明确不做的事情

Phase B 不处理以下内容：

- 完整预约领域模型、预约状态机、冲突事务和幂等键；属于 Phase C。
- 统一 Agent Orchestrator、完整事件协议和错误码体系；属于 Phase D。
- 完整长期记忆、向量记忆检索、复杂摘要版本链和 Token 压缩；属于 Phase E，或按实际需求后置。
- 全面修复分类、预约、咨询等旧业务 API；属于统一 API/编排阶段。
- 用户行为组件完整重写；保留给行为组件收口阶段。
- 认证、RBAC、多租户和生产部署安全；属于后续生产化阶段。
- 微服务拆分、消息队列和独立会话服务。

## 3. 目标设计

### 3.1 会话数据模型

新增 `conversations` 表，建议字段：

| 字段 | 说明 |
|---|---|
| `id` | 会话 ID，建议使用 UUID 字符串 |
| `user_id` | 当前用户标识；Phase B 可沿用演示用户，但必须保留字段边界 |
| `channel` | 来源渠道，Web 默认 `web` |
| `status` | `active`、`closed` 等会话状态 |
| `active_workflow` | 当前工作流类型，可为空；不在 Phase B 扩展复杂状态机 |
| `created_at` | 创建时间 |
| `updated_at` | 最近更新时间 |
| `last_activity_at` | 最近一次消息活动时间 |

约束：

- `id` 唯一且不可由客户端随意覆盖。
- 查询会话时必须同时校验会话归属，不能只按 ID 取数据。
- 不把预约字段、完整 Agent 对象或可执行对象序列化进会话表。

### 3.2 消息数据模型

新增 `messages` 表，建议字段：

| 字段 | 说明 |
|---|---|
| `id` | 消息 ID |
| `conversation_id` | 所属会话，外键或等价约束 |
| `role` | `user`、`assistant`、`system`、`tool` |
| `content` | 消息文本；Phase B 先保存可恢复原文 |
| `message_type` | `text`、`status`、`error` 等 |
| `metadata_json` | 非核心附加信息，不能替代结构化业务字段 |
| `sequence` | 会话内有序序号，或使用可稳定排序的创建序列 |
| `created_at` | 创建时间 |

约束：

- 消息必须绑定 `conversation_id`，禁止写入无归属消息。
- 恢复时按会话过滤并按稳定顺序读取。
- 用户消息和 Agent 最终消息至少要在一轮结束后持久化。
- 流式输出中途失败时，要明确记录失败消息或运行状态，不能伪造完整 assistant 消息。
- `metadata_json` 只保存可序列化、非敏感的附加信息。

### 3.3 运行时 `ConversationSession`

建议新增会话运行时对象，由会话管理器按需创建和恢复：

```text
ConversationSession
├── conversation_id
├── user_id
├── messages: list[MessageView]
├── appointment_draft: 当前阶段兼容的会话级草稿
├── lock: asyncio.Lock 或等价并发控制
└── last_activity_at
```

职责：

- 从数据库加载会话元数据和最近消息。
- 为当前会话持有短生命周期运行时状态。
- 将消息写入 Repository，不直接操作全局变量。
- 保证同一会话的 turn 不并发修改同一份草稿。
- 允许服务重启后重新构建，不能依赖 Python 对象身份。

生命周期：

```text
请求携带 conversation_id
        ↓
SessionManager 获取缓存对象或从 DB 恢复
        ↓
获取当前会话锁
        ↓
处理一轮消息并持久化
        ↓
释放锁
```

缓存只作为性能优化，不能作为数据来源。缓存失效或进程重启后，必须仍能从数据库恢复。

### 3.4 全局状态迁移清单

Phase B 必须审查并替换以下状态来源：

| 当前状态 | 风险 | Phase B 处理 |
|---|---|---|
| `api/chat_handler.py` 的全局 `task_agent` | 多用户共享 Agent 和历史 | 改为按会话获取运行时对象 |
| 全局 session ID | 所有请求可能落到同一会话 | 由请求显式携带 `conversation_id` |
| Agent 内的 `appointment_history` | 预约草稿串线且重启丢失 | 暂时挂到会话对象，明确后续 Phase C 再领域化 |
| `InMemoryChatMessageHistory` | 重启丢失、难以隔离 | 改为项目自管消息列表 + DB 持久化 |
| `config/constants.py` 的进程级共享状态 | 不属于任何会话 | 本阶段至少禁止其参与会话消息主链路；完整预约状态迁移留 Phase C |

### 3.5 执行前锁定的三个实现决策

#### 决策一：数据库路径只有一个来源

Phase B 从 B0 开始统一使用 `config/database.py` 中的全局 `db_config` 获取数据库路径和连接参数：

```python
from config.database import db_config

database_url = db_config.connection_string
```

约束：

- `app.py`、数据库初始化、Session 工厂、Repository、Service 和测试 fixture 不得各自读取 `DATABASE_URL` 或硬编码 `sqlite:///data/ai_front_desk.db`。
- 业务组件只依赖注入的数据库 Session/Repository；需要读取默认路径时统一从 `db_config` 取得。
- 测试临时数据库通过配置注入或受控的 fixture 覆盖，不在业务代码中增加测试专用路径分支。
- B0 必须完成一次数据库路径引用审查，并增加“所有数据库入口来自 `db_config`”的测试或静态检查。
- 如果后续需要从全局配置改为应用容器注入，应保持 `db_config` 作为当前阶段唯一事实来源，不能出现两套并行配置。

#### 决策二：每个 turn 使用独立、短生命周期的 DB Session

`ConversationSession` 是会话运行时对象，不持有 SQLAlchemy Session。每个 turn 按数据库写入边界创建独立的短生命周期 DB Session，不跨 turn、请求或会话复用：

```text
获取会话锁
  → DB Session 1：校验会话并写入 user message，commit 后关闭
  → 执行 Agent / 流式生成，不持有数据库事务
  → DB Session 2：写入 assistant 完成/失败消息并更新 conversation，commit/rollback 后关闭
  → 释放会话锁
```

具体规则：

- 每个 DB Session 都必须明确 `commit`、异常 `rollback` 和最终 `close`。
- 不把 DB Session 放进全局对象、`ConversationSession` 或异步任务的长期状态中。
- 用户消息先落库再调用模型，避免模型调用失败时丢失用户输入。
- assistant 流式输出完成后再写最终消息；中断时写入可识别的失败/未完成状态，不伪造完整回答。
- `conversation.updated_at` 与对应消息写入放在同一事务内。
- 同一会话的并发顺序由 `ConversationSession.lock` 或版本校验保证；不同会话可以使用各自的 DB Session 并行处理。
- B2 必须增加“turn 之间不共享 DB Session”和“异常后 Session 已关闭”的测试或可观测断言。

这项决策只解决 Phase B 的会话级持久化边界，不提前引入跨进程分布式锁或完整幂等事务体系。

#### 决策三：在 B6 前扩展 FakeLLM 预设表

Phase B 的隔离、恢复和并发测试不能依赖当前有限的 FakeLLM 关键词。B6 开始前必须扩展 `config/model_provider.py` 的预设表，并保持“输入关键词决定输出、测试之间无隐藏调用状态”的原则。

预设至少覆盖：

| 场景 | 输入特征 | 预期用途 |
|---|---|---|
| 预约项目 A | `肩颈放松` | 会话 A 的草稿隔离 |
| 预约项目 B | `足疗` | 会话 B 的草稿隔离 |
| 缺少字段 | 只有项目或只有时间 | 多轮恢复和继续追问 |
| 确认 | `确认`、`确定` | 恢复后继续预约 |
| 取消/否定 | `取消`、`不要` | 会话内状态不串线 |
| 咨询 | `价格`、`好处`、`服务项目` | 保持咨询链路回归 |
| 无关请求 | `天气` 等 | 分类和兼容接口回归 |
| 空输入/未知输入 | 空文本或未命中关键词 | 错误边界和默认响应 |

扩展规则：

- 预设按输入内容匹配，不依赖全局调用次数或测试执行顺序。
- `FakeChatModel.calls` 继续只作为调用审计，不作为业务状态存储。
- 每个测试 fixture 必须清理调用记录；不得让上一个会话的 FakeLLM 调用影响下一个测试。
- 如需模拟多轮行为，使用输入中的明确标记或测试专用响应映射，不在 FakeLLM 中保存跨会话状态。
- B6 前必须先为上述场景补充单元/契约覆盖，再编写依赖这些预设的会话隔离测试。

## 4. API 和前端方案

### 4.1 新增会话 API

建议新增以下接口：

```text
POST /api/v1/conversations
POST /api/v1/conversations/{conversation_id}/turns
GET  /api/v1/conversations/{conversation_id}
```

创建会话请求示例：

```json
{
  "user_id": "demo-user",
  "channel": "web"
}
```

创建会话响应至少包含：

```json
{
  "conversation_id": "<uuid>",
  "user_id": "demo-user",
  "channel": "web",
  "status": "active"
}
```

发送一轮消息请求示例：

```json
{
  "message": "我想预约肩颈放松",
  "user_id": "demo-user"
}
```

要求：

- URL 中的 `conversation_id` 是会话主标识。
- 服务端校验会话存在和归属关系。
- turns 可以暂时返回现有流式格式；事件协议统一留到 Phase D。
- 不把默认用户和默认会话当作新 API 的唯一实现。
- `GET` 用于恢复会话元数据和最近消息，返回字段要稳定。

### 4.2 `/chat/stream` 兼容策略

旧接口暂时保留，但降级为薄包装：

1. 如果请求携带 `conversation_id`，转发到对应会话。
2. 如果没有携带，使用明确的默认演示会话，而不是创建全局 Agent 状态。
3. 兼容包装不新增业务逻辑，不复制一套聊天处理流程。
4. 在文档中标明旧接口是兼容入口，后续统一 API 阶段再决定是否弃用。

Phase B 不修改完整流式事件协议，不在本阶段同时处理 `[THOUGHT]`、`[REPLY]` 等历史标记。

### 4.3 前端最小同步

修改 `web/templates/index.html` 或对应前端脚本：

- 首次进入页面时创建或读取 `conversation_id`。
- 使用 `localStorage` 保存当前 Web 会话 ID。
- 发送聊天请求时带上 `conversation_id`。
- 页面刷新后继续使用同一会话。
- 增加“新建会话/清空当前会话”入口时，必须明确是创建新 ID，不要只清空浏览器文本。
- 不把完整消息历史只保存在浏览器；历史以服务端数据库为准。

## 5. 执行任务拆分

### B0：基线和测试隔离

**目标：** 先让 Phase B 的测试可信、启动行为可控。

任务：

- 审查所有数据库路径读取点，统一改为从 `config.database.db_config` 取得。
- 让测试通过受控 fixture 注入临时数据库 URL，不修改业务代码中的默认配置。
- 确认 `data/` 父目录的创建策略。
- 固定测试入口和项目导入路径。
- 增加无模型配置时的启动降级测试或最小验证。
- 扩展 FakeLLM 预设表，为 B6 的多会话、多轮和错误边界测试准备稳定输入。
- 记录已有 10 个 skip，不在本任务中无理由删除。

主要文件候选：

- `tests/conftest.py`
- `config/database.py`
- `config/settings.py`
- `config/model_provider.py`
- `app.py`
- `.env.example`

完成条件：所有数据库入口统一从 `db_config` 获取；原有测试在隔离数据库下保持结果；FakeLLM 预设可覆盖 B6 场景；测试不修改仓库共享数据库。

### B1：数据模型和 Repository

**目标：** 建立会话和消息的持久化基础。

任务：

- 在 `db/models.py` 增加 Conversation、Message 模型。
- 增加会话和消息 Repository，统一创建、查询、追加和恢复接口。
- 增加数据库初始化/迁移策略，不能依赖手工 SQL 才能启动。
- 增加会话归属和消息归属校验。
- 为消息排序、最近 N 条恢复和会话更新时间增加测试。

完成条件：不经过 Agent 也可以独立创建会话、写入消息、读取消息和恢复最近消息。

### B2：会话运行时和并发边界

**目标：** 替换全局聊天状态。

任务：

- 新增 `ConversationSession` 和 `SessionManager` 或等价组件。
- 将全局 `task_agent` 改为按会话创建/恢复。
- 将 `InMemoryChatMessageHistory` 替换为自管消息列表。
- 将现有预约草稿暂时绑定到会话对象，禁止跨会话共享。
- 为同一会话增加锁或版本校验。
- 确保缓存失效后可从数据库重建。
- 落实“每个 turn 独立 DB Session”决策：用户消息写入、Agent 处理和 assistant 结果写入不得共享长期数据库 Session。
- 为每次 DB 写入定义 commit、rollback、close 边界，并验证异常路径不会泄漏 Session。
- 确保 `conversation.updated_at` 与对应消息写入保持同一事务一致性。

完成条件：两个会话拥有不同的运行时对象、消息列表和锁；同一会话的并发 turn 不会互相覆盖；不同 turn 不共享 DB Session；异常后 DB Session 均已关闭。

### B3：聊天处理链路接入

**目标：** 将实际 `/chat/stream` 处理迁移到会话上下文。

任务：

- 修改 `api/chat_handler.py`，移除全局 session/Agent 作为主路径。
- 每轮开始时校验会话并追加用户消息。
- Agent 处理完成后追加 assistant 消息。
- 处理异常和流式中断，保证消息状态可解释。
- 行为记录若仍在主流程中，改为失败不阻断聊天的旁路。
- 不在本任务中重写预约业务规则。

完成条件：旧聊天流程在 Fake 模式下可以通过指定会话运行，消息写入数据库且恢复顺序稳定。

### B4：新 API 和兼容包装

**目标：** 提供显式会话 API，同时保持旧页面可用。

任务：

- 增加 `POST /api/v1/conversations`。
- 增加 `POST /api/v1/conversations/{id}/turns`。
- 增加 `GET /api/v1/conversations/{id}`。
- 将 `/chat/stream` 改为薄兼容包装。
- 为会话不存在、归属不符、消息为空等情况定义稳定响应。
- 更新 API 契约测试和 README 的接口说明。

完成条件：新 API 可以创建、发送消息、读取和恢复会话；旧 `/chat/stream` 仍可以完成基础聊天。

### B5：前端最小改造

**目标：** 让浏览器客户端真正使用会话 ID。

任务：

- 在前端保存 `conversation_id`。
- 请求中携带会话 ID。
- 刷新页面后继续当前会话。
- 新建会话时生成新的会话 ID并清晰提示用户。
- 处理服务端返回的会话不存在或恢复失败。

完成条件：手动刷新页面后，服务端仍能返回同一会话的最近消息；创建新会话后不会显示旧会话的服务端历史。

### B6：隔离、恢复和并发测试

**目标：** 用自动化测试证明 Phase B 的核心目标。

前置条件：B0 已完成 FakeLLM 预设表扩展，B2 已完成独立 DB Session 的写入边界；否则不得把 B6 标记为可执行或通过。

至少增加以下测试：

| 测试 | 核心断言 |
|---|---|
| 两会话消息隔离 | A 的消息不会出现在 B 的恢复结果中 |
| 两会话预约草稿隔离 | A 的草稿不会影响 B 的 Agent 处理 |
| 服务重启恢复 | 新应用实例可以从同一 DB 恢复会话和最近消息 |
| 同会话并发 | 同一会话的两个 turn 不会交错覆盖状态 |
| 不同会话并发 | 不同会话不会共享锁或消息列表 |
| 消息顺序 | 恢复顺序与写入顺序一致 |
| 旧接口兼容 | `/chat/stream` 在默认会话和显式会话下均可用 |
| 数据库隔离 | 测试运行不污染共享 `data/ai_front_desk.db` |
| 无模型启动 | Fake 或降级模式下应用能启动，错误边界可观察 |

FakeLLM 还必须覆盖：预约项目 A/B、缺少字段后恢复、确认/取消、咨询、无关请求、空输入/未知输入；所有场景均不得依赖调用次数或跨测试状态。

### B7：文档和收尾

**目标：** 形成可交接的阶段证据。

任务：

- 更新 README 的启动、会话 API 和兼容接口说明。
- 更新 Phase B 文档的执行结果、测试结果和未完成事项。
- 记录新增文件、删除的全局状态和保留的兼容层。
- 检查 `git diff --check`、测试结果和工作区边界。
- 只提交 Phase B 范围内的文件。

## 6. 推荐执行顺序

```text
B0 测试隔离与启动边界
  ↓
B1 Conversation / Message 模型与 Repository
  ↓
B2 ConversationSession / SessionManager
  ↓
B3 聊天处理链路接入
  ↓
B4 新 API + 旧接口兼容包装
  ↓
B5 前端 localStorage 会话同步
  ↓
B6 隔离、恢复、并发测试
  ↓
B7 文档、验证、提交和交接
```

建议把 B0、B1、B2 分成可独立回滚的小步；不要先一次性修改后端、API 和前端，再通过最后一次全量测试判断成败。

建议提交边界：

```text
Phase B(test)-隔离测试数据库
Phase B(feat)-新增会话消息模型
Phase B(refactor)-移除聊天全局状态
Phase B(feat)-接入会话API
Phase B(feat)-前端携带会话ID
Phase B(test)-验证会话恢复隔离
Phase B(docs)-记录PhaseB执行结果
```

实际提交时仍需遵守标题不超过 15 个字、单一大块改动和不混入无关文件的规则。

## 7. Phase B 完成定义

Phase B 只有同时满足以下条件才能标记为完成：

### 7.1 功能完成

- [x] 可以创建会话并获得唯一 `conversation_id`。
- [x] 用户消息和 Agent 消息都能持久化到正确会话。
- [x] 两个会话之间不共享消息历史、预约草稿、Agent 运行时状态或锁。
- [x] 服务重启后可以恢复会话元数据和最近消息。
- [x] 同一会话并发请求不会造成消息顺序或状态覆盖错误。
- [x] 旧 `/chat/stream` 仍可用，新会话 API 可用。
- [x] 前端刷新后可以继续原会话，创建新会话后不会串历史。

### 7.2 测试完成

- [x] 原 Phase A 测试不出现新增回归。
- [x] 新增多会话隔离测试通过。
- [x] 新增服务重启恢复测试通过。
- [x] 新增同会话并发测试通过。
- [x] 测试使用临时数据库，不污染共享本地数据库。
- [x] Fake 模式下测试不调用真实 LLM 或 Embedding API。
- [x] 所有数据库路径均来自 `db_config`，没有业务代码或 fixture 继续硬编码数据库路径。
- [x] 每个 turn 的 DB Session 均独立、短生命周期，commit/rollback/close 边界有测试证据。
- [x] FakeLLM 预设表覆盖 B6 所需的多会话、多轮和错误边界场景。
- [x] `user_behavior` 的既有 skip 原因仍被准确记录，不能伪装成通过。

### 7.3 运行和文档完成

- [x] 没有预先创建 `data/` 目录时，Fake 模式可以初始化。
- [x] 无真实模型 key 时，应用启动失败边界已明确，不能出现无解释的崩溃。
- [x] README 已说明会话 API、旧接口兼容策略和本地运行方式。
- [x] Phase B 执行结果记录了实际命令、通过/跳过/失败数量和警告。
- [x] 剩余的 A-R1、A-R2、A-R3、A-R5 状态已更新并说明去向。
- [x] `git diff --check` 通过，工作区只包含 Phase B 范围内的改动。

## 8. 风险、回滚和边界

| 风险 | 影响 | 缓解与回滚 |
|---|---|---|
| 全局 Agent 替换导致现有聊天回归 | 高 | 先保留兼容包装；B2/B3 分步接入；按提交粒度回滚 |
| SQLite 测试隔离不完整 | 高 | 先完成 B0；测试中断时清理临时目录；禁止使用共享生产路径 |
| 会话锁只在进程内有效 | 中 | Phase B 明确单进程边界；持久化版本/更新时间；多进程锁留后续生产化处理 |
| 流式输出中途失败导致 assistant 消息不完整 | 中 | 区分开始、增量和完成状态；失败时记录可解释状态，不伪造完整响应 |
| 旧接口和新 API 产生两套逻辑 | 高 | 旧接口只做参数转换和转发，不复制业务处理代码 |
| A-R2 行为记录继续失败 | 中 | 行为记录从主聊天链路旁路化；不把行为记录作为 Phase B Done 条件 |
| A-R1 启动降级扩大范围 | 中 | 只做初始化失败隔离和错误边界，不重构 provider；超出范围的内容延期 |
| 现有本地 SQLite 数据结构不兼容 | 中 | Phase B 只新增会话/消息表；预约历史迁移和领域化留 Phase C |

回滚原则：

1. 每个 B 任务形成独立、可说明的提交边界。
2. 如果新会话 API 失败，可暂时保留旧接口包装，但不得恢复全局状态作为最终方案。
3. 如果消息持久化失败，必须报告阻塞，不得退化成“只存在内存中但标记成功”。
4. 不执行 `git reset --hard`、清理或覆盖已有用户改动。

## 9. Phase B 交接到 Phase C

Phase B 完成后，应向 Phase C 输出以下稳定前置条件：

- `conversation_id` 已成为会话主标识。
- 消息可以按会话持久化和恢复。
- 预约草稿至少不会跨会话共享。
- 预约业务仍可能依赖旧 `technician_schedules` 结构，但其运行时归属已经明确。
- Phase C 可以在会话边界之上新增 `Appointment` 实体和状态机，而不再同时解决全局会话问题。
- A-R3 旧 API 缺陷仍需在统一 API/编排阶段处理。
- A-R2 行为组件测试仍需单独收口。

Phase B 最终报告必须区分：

- 已实现并通过测试的会话功能；
- 仍保留的兼容层；
- 延期的 Phase A 遗留问题；
- 当前只在单进程或 Fake 模式下验证的边界；
- 尚未完成的线上、鉴权或生产验收。

## 10. Phase B 执行结果（2026-08-17）

### 测试结果

- **63 passed, 10 skipped, 0 failed**（user_behavior 既有 skip 保留，原因记录不变）
- 全程 `MODEL_PROVIDER=fake`，零真实 LLM/Embedding 调用，可离线重复
- 测试使用 session 级临时 SQLite（conftest），未污染 `data/ai_front_desk.db`（mtime 验证）

### 完成定义核对

功能完成：

- [x] 创建会话并获得唯一 `conversation_id`（`POST /api/v1/conversations`）
- [x] 用户/Agent 消息持久化到正确会话（每 turn 独立 DB Session 写入）
- [x] 两会话不共享消息历史、预约草稿、运行时状态或锁（B6 测试证明）
- [x] 服务重启后恢复会话元数据和最近消息（B6 restart 测试）
- [x] 同会话并发 turn 串行（锁内落库 + 处理，消息成对不交错）
- [x] 旧 `/chat/stream` 可用（默认会话 + 显式会话），新会话 API 可用
- [x] 前端 localStorage 保存 `conversation_id`，刷新保持，新建会话生成新 ID

测试完成：

- [x] Phase A 测试无新增回归（契约测试全部保持）
- [x] 多会话隔离 / 重启恢复 / 同会话并发 / 不同会话并发 / 消息顺序测试通过
- [x] 测试使用临时数据库（`test_database_isolation_config` 断言不指向共享库）
- [x] 所有数据库路径来自 `db_config`（B0 grep 审查 + 硬编码清零）
- [x] 每 turn 独立 DB Session：用户消息、agent 处理、assistant 结果分 Session 写入，异常路径可恢复（`test_write_failure_does_not_leak_session`）
- [x] FakeLLM 预设覆盖 B6 场景（项目 A/B、缺字段、确认、取消、咨询、无关、未知输入）
- [x] `user_behavior` 10 个 skip 保留并记录审计原因

运行和文档完成：

- [x] 无 `data/` 预创建可运行（SessionManager 自动创建父目录）
- [x] 无模型 key 时应用降级启动、聊天返回稳定错误（`test_startup_guard`）
- [x] README 更新（会话 API、兼容策略、Fake 模式、测试说明）
- [x] `git diff --check` 通过（0 输出）
- [x] A-R1~A-R5 状态更新（见下）

### 遗留状态（Phase A 问题去向）

| 编号 | 状态 | 说明 |
|---|---|---|
| A-R1 启动降级 | ✅ 已处理 | 惰性 task_agent + initialize 降级 + 稳定错误文本 |
| A-R2 user_behavior | ⏸️ 延期 | skip 保留，Phase D 行为组件收口时重写 |
| A-R3 旧 API 缺陷 | ⏸️ 延期 | 400 基线由契约测试固化，Phase D 统一 API 时修复 |
| A-R4 测试隔离 | ✅ 已处理 | conftest 临时库注入 |
| A-R5 data/ 目录 | ✅ 已处理 | SessionManager 自动创建父目录 |

### 新增 / 删除 / 兼容层

- **新增**：`application/`（session_runtime、message_buffer）、`api/conversations.py`、Conversation/Message 模型、ConversationRepository
- **删除的全局状态**：`chat_handler.py` 模块级 `task_agent` / 全局 session ID → 惰性会话化（B3）
- **保留的兼容层**：`/chat/stream`、`/chat` 薄包装（Phase D 决定弃用）；无 `conversation_id` 时默认演示会话（临时回退）

### 提交记录（Phase B，7 个 commit）

```
Phase B(test)-B0测试隔离与启动降级
Phase B(feat)-新增会话消息模型
Phase B(refactor)-移除聊天全局状态
Phase B(feat)-接入会话API
Phase B(feat)-前端携带会话ID
Phase B(test)-验证会话恢复隔离
Phase B(docs)-记录PhaseB执行结果（本提交）
```

### 交接到 Phase C 的边界说明

- `conversation_id` 已是会话主标识，消息可按会话持久化/恢复。
- 预约草稿挂在会话专属 Agent 实例上（不跨会话共享），但**仍是内存态**，重启丢失——领域化与恢复属于 Phase C。
- 会话锁只在单进程内有效；多进程部署需持久化版本/分布式锁（后续生产化）。
- 流式协议仍是 `[THOUGHT]/[REPLY]` 文本标记，事件协议统一在 Phase D。
