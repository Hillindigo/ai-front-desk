# Phase D 执行计划：统一 Agent 编排、工具边界与事件协议

> 计划版本：2026-08-17
>
> 文档状态：待执行
>
> 所属项目：`AIFrontDesk`
>
> 前置阶段：Phase C——预约与排班领域化

## 0. 阶段定位与结论

### 0.1 阶段目标

Phase D 的目标不是继续增加 Agent，而是把现有多个 Agent 和旧字符串协议收敛为一条可测试、可追踪、可恢复的会话执行链：

```text
HTTP 请求
    ↓
请求校验、会话归属、错误边界
    ↓
ConversationOrchestrator.handle_turn()
    ↓
意图识别 → 工作流路由 → 明确工具调用
    ↓
确定性 Service / Repository / AppointmentDomain
    ↓
统一事件流 + 持久化 assistant 消息
```

完成后：

- Agent 只负责理解、追问和生成建议，不再互相直接调用或直接写业务数据；
- `AppointmentCommandService` 继续作为预约写入的唯一领域入口，Phase D 不重写 Phase C 状态机；
- `/api/v1/conversations/{conversation_id}/turns` 使用稳定的版本化事件协议；
- `[THOUGHT]`、`[REPLY]`、`[SIGNAL]` 不再作为业务协议或前端解析依据；
- API 对外返回稳定错误码，内部日志保留可定位的异常上下文；
- 旧接口只保留薄适配职责，满足迁移条件的重复入口和兼容层被删除；
- Phase A 的 A-R2（`user_behavior`）和 A-R3（旧任务/咨询 API 缺陷）完成收口，或明确记录阻塞原因。

### 0.2 当前代码事实

根据 Phase C 交接记录和当前源码，Phase D 开始时应以以下事实为基础：

1. `ConversationSession`、`SessionManager` 和消息持久化已经存在，会话 ID 是主标识。
2. `Appointment`、`AppointmentEvent` 和 `AppointmentCommandService` 已完成领域化，预约创建、确认、取消、改约、冲突和幂等不应在 Orchestrator 中重新实现。
3. `api/conversations.py` 已提供会话创建、读取和 turns 入口，但 turns 仍沿用旧 Agent 流式输出形态。
4. `api/chat_handler.py` 仍承担会话解析、Agent 创建和旧流式处理；`/chat/stream` 和 `/chat` 仍是兼容入口。
5. `agents/task_classification/agent_router.py`、`agents/appointment/`、`agents/consultant/` 和 `agents/user_behavior/` 之间仍存在历史编排和字符串标记。
6. 业务代码中仍可见 `[THOUGHT]`、`[REPLY]`、`[SIGNAL]recommendation_pending` 等内部协议；这些内容不能继续由前端按字符串解析。
7. `db/local_db.py`、`db/db_router.py` 中的兼容 Router、`AppointmentDatabase` 和部分 API 自建数据库入口仍需清理，但删除前必须完成引用审查和回归验证。
8. A-R3 的 `/api/appointment/create` 已由 Phase C 接入领域服务；`/api/task/classify` 和 `/api/consultation/ask` 仍需统一错误语义和调用路径。
9. A-R2 的 `user_behavior` 既有 skip 仍然存在。本阶段只收口记录边界和失败隔离，不扩展为完整推荐或复杂画像系统。

以上是当前源码和阶段文档形成的基线；任何与实际代码不一致的历史描述，应以 D0 重新检查结果为准。

### 0.3 Phase C 交接前提

Phase C 已记录：

- **121 passed, 10 skipped, 0 failed**，Fake 模式下无真实 LLM/Embedding 请求；
- 预约领域支持独立实体、状态机、半开区间冲突规则、幂等和事务边界；
- 新预约 API 为 `/api/v1/appointments`，旧预约接口是领域服务适配器；
- 当前验证边界仍是 SQLite、单进程、Fake 模式；多进程、鉴权、线上验收不在本阶段默认完成。

Phase D 必须重新执行基线测试，不把 Phase C 文档中的历史结果直接当作本次通过证据。

### 0.4 当前工作区门禁

计划编写时仓库位于 `dev` 分支，且存在未提交的 Phase C 相关改动。正式执行 D1 之前必须：

- 记录 `git branch --show-current` 和 `git status --short`；
- 逐项确认未提交改动是否属于 Phase C 验收修复；
- 不执行 `reset`、清理、覆盖或批量提交；
- Phase D 新增和修改文件不得把这些已有改动混入同一提交；
- 若未提交改动影响 D0 基线，应先完成边界确认，再开始 D1。

### 0.5 D1/D4 需要定死的两个小决策

这两个决策不阻塞 Phase D 的开始，但必须在对应任务执行时写入代码、契约测试和阶段结果，不能继续以“后续再决定”的状态进入实现。

#### 决策一：确定性意图规则的形态与位置

**结论：** 采用 `application/intent_rules.py`，不放进通用配置文件。

规则表至少包含：

```text
意图枚举 → 关键词列表 / 正则列表 / 优先级 / 规则名称
```

约束：

- 规则属于应用行为和路由契约，应与 `IntentRouter` 放在 `application/`，而不是与环境变量混合的 `config/`；
- 先对输入做统一空白、大小写和常见标点归一化，再执行规则匹配；
- 明确冲突解决顺序：高优先级的明确命令（如取消、确认）优先于一般业务词；同优先级多命中且无法消歧时，不强行路由，交给 FakeLLM/真实模型兜底或返回澄清；
- 规则表只读、可审计、不可由请求或模型动态修改；
- FakeLLM 只用于规则未命中或结果有歧义的输入，不参与明确规则命中的路径；
- 每条规则必须有正例、反例和冲突例测试，避免“预约”这类宽泛词误把咨询或取消请求路由到错误工作流；
- 规则命中结果必须输出结构化 `IntentClassification`，不能只返回命中的字符串。

#### 决策二：事件流只描述当前轮次

**结论：** 一次 turns 请求对应一个 `run_id`，事件流只描述这一轮从 `run_started` 到唯一终止事件的执行过程。

跨轮预约状态不由事件流承载：

- 预约收集、推荐、等待确认和完成属于跨轮业务状态，由 Phase C 的持久化草稿/预约状态、会话锁和必要的会话运行时缓存承载；
- 下一轮开始时，Orchestrator 应从数据库恢复当前草稿或已确认预约，再决定本轮动作；
- 前一轮的事件不能作为下一轮的事实来源，也不能通过重放事件恢复预约状态；
- 本轮可以通过 `tool_result`、`assistant_message` 或 `data` 中的安全业务摘要告知“当前处于待确认”等结果，但不能把未完成的跨轮流程表示为一个持续打开的事件流；
- `run_completed` 表示本轮执行完成，不表示预约业务已经完成；预约是否完成以领域实体状态为准。

## 1. 必须达成的目标

### 1.1 统一编排

1. 新增一个会话级 `ConversationOrchestrator` 或等价组件，入口至少为：

   ```python
   handle_turn(conversation_id, user_id, user_input, request_id=None)
   ```

2. 一轮请求只允许由 Orchestrator 负责：

   ```text
   接收输入 → 分类 → 选择工作流 → 调用工具 → 生成响应 → 发出事件 → 保存结果
   ```

3. 分类使用“确定性规则优先、LLM 处理模糊输入”的顺序；分类结果必须是枚举或 Pydantic 模型，不能用自由文本决定路由。
4. 预约、咨询、无关请求和行为记录分别通过工作流/工具协议接入；工作流之间不直接实例化或调用彼此的 Agent。
5. 预约工作流只能调用 Phase C 的领域命令，不得直接写 `Appointment`、`TechnicianSchedule`、`busy_periods_dict` 或旧预约表。
6. Agent 和 Service 的依赖由应用容器注入，禁止在每个请求中隐式创建数据库连接、模型客户端或全局单例。

### 1.2 明确工具边界

每个工具必须有明确的输入、输出和失败语义，至少覆盖：

| 工具/工作流 | 输入 | 输出 | 约束 |
|---|---|---|---|
| `classify_intent` | 用户文本、会话上下文摘要 | 意图枚举、置信度、缺失信息 | 不写库、不改变预约状态 |
| `appointment_draft` | 会话 ID、结构化预约字段 | 草稿或待确认结果 | 只调用预约领域服务 |
| `appointment_confirm` | 预约 ID、幂等键、会话归属 | 确认结果或领域错误 | 不允许 LLM 绕过确认事务 |
| `consultation_search` | 查询文本、可选项目 | 引用化知识结果 | 搜索失败不能伪造知识答案 |
| `consultation_answer` | 用户问题、检索结果 | 面向用户的回答文本 | 不直接管理数据库连接 |
| `behavior_record` | 用户/会话、事件类型、结构化属性 | 成功/失败状态 | 旁路失败不阻断主对话 |

输入输出模型应集中在 `application/` 或 `api/core/`，禁止通过 `dict` 的隐含字段在 Agent 之间传递业务命令。

### 1.3 统一流式事件协议

以 `/api/v1/conversations/{conversation_id}/turns` 为规范入口，定义版本化事件协议。推荐采用 SSE 传输，事件数据使用 JSON：

```text
event: run_started
data: {"protocol_version":"v1", ...}

event: intent_detected
data: {"intent":"appointment", ...}

event: tool_started
data: {"tool":"appointment_draft", ...}

event: assistant_delta
data: {"text":"..."}

event: run_completed
data: {"message_id":"...", ...}
```

最小事件集合：

| 事件 | 用途 | 是否终止事件 |
|---|---|---:|
| `run_started` | 标识本轮开始、协议版本和 `run_id` | 否 |
| `intent_detected` | 告知安全的意图类别 | 否 |
| `workflow_started` | 标识进入哪个业务工作流 | 否 |
| `tool_started` | 标识即将执行的公开工具 | 否 |
| `tool_result` | 返回可公开的工具结果摘要 | 否 |
| `assistant_delta` | 增量输出用户可见文本 | 否 |
| `assistant_message` | 完整 assistant 消息或消息引用 | 否 |
| `run_completed` | 本轮成功完成 | 是 |
| `run_failed` | 本轮以稳定错误结束 | 是 |

事件包至少包含：`protocol_version`、`event_id`、`run_id`、`conversation_id`、递增 `sequence`、`type`、`timestamp` 和 `data`。终止事件只能出现一次；客户端断开、模型失败或工具失败时不能伪造 `run_completed`。

本阶段不输出模型内部 chain-of-thought。原 `[THOUGHT]` 文本只能转换为有限的、面向用户的状态事件，不能把隐藏推理暴露为事件内容。

事件流是**单轮视角**：`run_id` 的生命周期只覆盖当前 turns 请求；跨轮预约状态不通过事件重放、事件拼接或未结束的长连接表达。事件中的业务摘要只能帮助当前客户端展示，不能替代 `Appointment`/草稿的持久化状态。

### 1.4 统一错误语义

新增集中错误定义，例如 `api/core/errors.py`：

```text
INVALID_INPUT
CONVERSATION_NOT_FOUND
CONVERSATION_ACCESS_DENIED
INTENT_UNSUPPORTED
APPOINTMENT_CONFLICT
APPOINTMENT_STATE_INVALID
IDEMPOTENCY_CONFLICT
MODEL_UNAVAILABLE
TOOL_FAILED
INTERNAL_ERROR
```

要求：

- 对外错误码稳定，不能直接返回 Python 异常字符串、SQL 错误或模型供应商原始响应；
- HTTP 状态码、流式 `run_failed` 和普通 JSON 错误使用同一错误映射；
- 错误响应带 `request_id`/`run_id`，内部日志带异常栈和关键上下文；
- `retryable` 只由服务端确定，不能让客户端猜测；
- 预约冲突、幂等冲突、会话归属错误和模型不可用必须可被客户端区分；
- 行为记录和推荐旁路失败不能把已成功的预约事务改写为失败。

### 1.5 删除或收缩兼容层

满足“无引用、替代路径已验证、兼容测试已迁移”后，按提交边界清理：

- 删除 `db/local_db.py` 的业务主路径依赖；
- 删除或收缩 `TechnicianDBRouter`、`KnowledgeDBRouter`、`UserBehaviorDBRouter` 等重复 Router；
- 删除 `AppointmentDatabase` 的主流程依赖，必要时保留短期只读适配并标记弃用；
- 删除 `/chat` 旧重复处理逻辑；`/chat/stream` 只能保留为薄适配器，不能再有独立编排；
- 移除业务代码中的 `[THOUGHT]`、`[REPLY]`、`[SIGNAL]` 协议依赖；
- 旧 `/api/task/classify`、`/api/consultation/ask` 和旧预约接口如需保留，只能转换请求并调用规范服务。

## 2. 明确不做的事情

1. 不重写 Phase C 的预约状态机、事务冲突算法、幂等规则或数据库模型。
2. 不在本阶段引入微服务、消息队列、分布式锁、完整事件总线或复杂工作流引擎。
3. 不把 `user_behavior` 扩展为完整推荐系统、用户画像平台或在线训练系统。
4. 不实施完整鉴权、多租户和生产级权限体系；只保留会话归属检查和当前本地边界，生产化另行规划。
5. 不把模型的隐藏思考过程包装成公开的 `thought` 事件。
6. 不为了删除兼容层而强制破坏已有演示接口；删除必须以迁移证据和回归测试为前提。
7. 不用本地 Fake 模型测试结果替代真实模型、线上、鉴权或多进程验收。
8. 不把日志统一误解为“所有日志必须无文本”；本阶段重点是禁止业务 `print()` 和未结构化错误泄漏，正常用户文案仍由响应协议承载。

## 3. 目标架构

### 3.1 分层关系

```text
api/
  - 负责 HTTP/SSE、请求校验、会话归属和响应适配
  - 不负责分类、业务状态迁移和数据库拼装
        ↓
application/
  - ConversationOrchestrator
  - IntentRouter
  - Workflow / Tool contracts
  - EventPublisher / ErrorMapper
        ↓
agents/
  - 保留理解、解析和生成能力
  - 不直接调用其他 Agent，不直接写业务库
        ↓
services/
  - ConsultationService、BehaviorRecorder、AppointmentCommandService
  - 负责确定性校验和业务规则
        ↓
db/repositories/
  - 统一 Repository 和短生命周期 Session
```

### 3.2 一轮会话的生命周期

```text
1. API 校验 conversation_id、user_id、message 非空
2. 创建 request_id/run_id，获取会话锁
3. 持久化 user message（失败则不调用模型）
4. Orchestrator 分类并选择 workflow
5. workflow 调用结构化 tool
6. tool 结果转换为公开事件和用户回答
7. 完成或失败时写入 assistant 消息及状态
8. 发送唯一终止事件
9. 释放会话锁
```

数据库 Session 仍须遵守 Phase B 的短生命周期规则：每次持久化操作明确 `commit`、异常 `rollback`、最终 `close`，不能把 Session 放进会话对象或长期 Agent 状态。

### 3.3 依赖注入

建议新增 `application/container.py` 或等价容器，在应用启动时组装：

- 数据库 Session/Repository 工厂；
- `AppointmentCommandService`、知识服务和行为记录器；
- Fake/真实模型提供商；
- `IntentRouter`、各 Workflow、`ConversationOrchestrator`；
- 事件序列化器和错误映射器。

测试通过依赖覆盖注入 Fake 实现，不依赖模块导入顺序、全局调用计数或真实模型密钥。

## 4. API 与协议设计

### 4.1 规范 turns 接口

规范入口保持：

```text
POST /api/v1/conversations/{conversation_id}/turns
Content-Type: application/json
Accept: text/event-stream
```

请求最小字段：

```json
{
  "message": "我想预约肩颈放松",
  "user_id": "demo-user",
  "client_request_id": "optional-idempotency-key"
}
```

要求：

- `conversation_id` 以 URL 为准，不能被请求体中的同名字段覆盖；
- 空消息、过长消息、归属不匹配和不存在会话返回稳定错误；
- `client_request_id` 用于请求去重时，不能替代 Phase C 预约幂等键；
- 事件流开始后，所有失败均使用 `run_failed`，不能中途切换为未定义 JSON；
- 完整 assistant 消息仍按 Phase B 规则持久化，增量事件不是持久化事实来源。

### 4.2 事件字段约束

```json
{
  "protocol_version": "v1",
  "event_id": "uuid",
  "run_id": "uuid",
  "conversation_id": "uuid",
  "sequence": 3,
  "type": "assistant_delta",
  "timestamp": "2026-08-17T12:00:00Z",
  "data": {"text": "您好"}
}
```

禁止：

- 使用未注册的事件类型作为业务分支；
- 用字符串前缀承载状态；
- 在 `data` 中返回数据库异常、Prompt、密钥或供应商原始错误；
- 依赖事件到达顺序之外的隐含全局状态。

### 4.3 兼容接口策略

| 接口/入口 | Phase D 处理 | 删除条件 |
|---|---|---|
| `/api/v1/conversations/{id}/turns` | 唯一规范编排和事件入口 | 不删除 |
| `/chat/stream` | 参数转换到规范 turns；保留短期兼容 | 前端和契约迁移完成后再评估 |
| `/chat` | 不再保留独立业务逻辑，标记弃用或移除 | 无生产/演示调用且兼容测试迁移 |
| `/api/task/classify` | 适配到 `IntentRouter`，统一错误码 | 后续版本再删除 |
| `/api/consultation/ask` | 适配到咨询 Workflow | 后续版本再删除 |
| `/api/appointment/create` | 继续适配 Phase C 领域服务 | 新客户端全部使用 v1 后再评估 |

兼容接口不得重新创建 Agent、Repository 或业务规则；所有写入和路由必须回到规范应用层。

## 5. 执行任务拆分

### D0：基线、交接和引用盘点

**目标：** 在不覆盖已有工作区改动的前提下锁定 Phase D 范围。

任务：

- 检查分支、工作区和最近提交；
- 重新运行 Phase C 全量测试和 `git diff --check`；
- 记录当前通过、跳过、失败、警告和测试数据库边界；
- 盘点所有 Agent 间调用、旧协议字符串、数据库 Router、旧 API 和 `print()`；
- 建立删除清单和引用清单，删除前后各执行一次；
- 确认未提交 Phase C 改动不属于 Phase D；
- 固定 D1 的文件范围和回滚点。

完成条件：存在可复现基线记录；所有待删除入口均有引用位置；没有把历史 Phase C 结果误当作当前验证结果。

### D1：应用层契约与错误模型

**目标：** 先定义类型和边界，再迁移现有 Agent。

任务：

- 新增意图枚举、分类结果、工作流命令、工具输入输出模型；
- 新增 `application/intent_rules.py`，定义只读的关键词/正则规则表、优先级和规则名称；
- 新增事件类型枚举、事件包模型、序列号和终止事件校验；
- 新增统一领域/API 错误类型、错误码和 HTTP/SSE 映射；
- 为预约领域错误建立从 Phase C 异常到公开错误码的映射；
- 为请求 ID、运行 ID、会话 ID 定义日志上下文字段；
- 先补充契约测试，确保模型拒绝缺失字段和未知事件类型，并覆盖规则归一化、优先级、冲突和 FakeLLM 兜底边界。

完成条件：Orchestrator、API 和测试共享同一组结构化模型，不再靠自由字典或字符串前缀沟通。

### D2：IntentRouter 与 ConversationOrchestrator

**目标：** 建立唯一的会话轮次编排入口。

任务：

- 新增 `application/orchestrator.py` 和 `IntentRouter`；
- 将任务分类从 Agent 互相回调改为返回结构化意图；
- 使用 `application/intent_rules.py` 的规则表优先覆盖预约、咨询、取消、改约、确认、无关和未知输入；
- 明确命令命中高优先级规则时不调用 LLM；同优先级冲突或规则未命中时才调用 Fake/真实 LLM 分类；
- 为 appointment、consultation、unrelated 建立工作流适配器；
- 每一轮开始时从持久化草稿/预约状态恢复跨轮上下文，不从上一轮事件流推断业务状态；
- 将 user behavior 改为主流程旁路记录器，不阻断主回答；
- 保证 Orchestrator 不直接写数据库，所有写入通过 Service/Repository；
- 为每个工作流定义成功、业务拒绝、工具失败和模型失败路径。

完成条件：指定 turns 请求只经过 Orchestrator 一次；Agent 不再直接调用另一个 Agent；预约流程仍只调用 Phase C 领域服务。

### D3：依赖注入与运行时边界

**目标：** 清除请求内自建依赖和隐式全局状态。

任务：

- 新增应用容器和 FastAPI 依赖入口；
- 统一 Repository/Service/Model Provider 的创建方式；
- 将 `api/chat_handler.py` 的会话解析和 Agent 创建逻辑收缩为适配层；
- 测试中注入临时数据库、Fake LLM、Fake Embedding 和 Fake BehaviorRecorder；
- 检查 DB Session 是否跨 turn、跨会话或跨异步生成器泄漏；
- 为依赖创建失败定义 `MODEL_UNAVAILABLE` 或 `INTERNAL_ERROR`，不能在导入阶段无解释崩溃。

完成条件：应用和测试都通过容器获得同一组依赖；业务服务不再自行创建重复数据库入口。

### D4：SSE 事件流接入

**目标：** 用结构化事件替换历史文本标记。

任务：

- 将规范 turns 入口改为 SSE/等价可测试事件流；
- 明确一个 turns 请求只生成一个 `run_id`，事件流从 `run_started` 开始，以唯一 `run_completed` 或 `run_failed` 结束；
- 实现事件序列生成、JSON 序列化、SSE framing 和心跳/结束策略；
- 把分类、工作流、工具、assistant 增量和终止状态映射到事件；
- 为异常、中断、客户端断开和重复请求设计收尾行为；
- assistant 完成后写持久化消息，再发 `run_completed`；
- 失败时写可解释的失败消息/状态，再发唯一 `run_failed`；
- 在事件契约中标注“事件只描述当前轮次”；不得用持续连接、前一轮事件或事件重放表示预约跨轮状态；
- 对“收集字段”“推荐候选”“等待确认”“预约完成”等结果，只发送当前轮安全摘要；真实状态以数据库草稿/预约实体为准；
- 清理前端不可见的内部 `[THOUGHT]`、`[REPLY]` 和 `[SIGNAL]` 依赖。

完成条件：客户端只需解析事件包即可渲染回答；事件顺序、终止事件和持久化状态均可由测试证明。

### D5：前端事件渲染和兼容切换

**目标：** 让现有页面使用新协议，不重做页面 UI。

任务：

- 将 `web/templates/index.html` 或对应静态脚本的字符串解析改为事件解析；
- 只渲染 `assistant_delta`/`assistant_message` 的用户可见文本；
- 对 `run_started`、`tool_started`、`run_failed` 和 `run_completed` 提供最小状态展示；
- 处理事件流断开、重试、重复 terminal 事件和错误码；
- 保持 `conversation_id` 的 localStorage 和刷新恢复行为；
- 确认旧 `/chat/stream` 兼容路径不会被前端继续作为规范协议依赖。

完成条件：本地页面可创建/恢复会话、发送一轮消息、显示增量回答和稳定错误；刷新后不串会话。

### D6：行为记录、日志与错误收口

**目标：** 完成 A-R2/A-R3 的最小可验证收口。

任务：

- 将 `user_behavior` 记录改为注入式 `BehaviorRecorder`；
- 行为记录失败只记录结构化日志/旁路事件，不覆盖主回答结果；
- 将 `api/task.py`、`api/consultation.py` 和相关旧入口接到统一 Router/Workflow；
- 统一空输入、未识别意图、模型不可用、知识检索失败和内部异常响应；
- 清理业务 `print()`，改用统一 logger；日志不得包含密钥、完整 Prompt 或不必要的用户敏感数据；
- 为旧接口增加弃用标识和迁移说明。

完成条件：A-R2 不再因为测试缺少边界而被伪装成通过；若仍有 skip，原因、范围和下一步清楚可追踪。A-R3 的 task/consultation 旧接口具备稳定契约并调用统一路径。

### D7：兼容层删除与重复入口清理

**目标：** 在新路径稳定后减少重复实现。

任务：

- 根据 D0/D2/D3 的引用清单，删除 `AppointmentDatabase` 主路径依赖；
- 删除 `db/local_db.py` 和旧 Router 的业务引用，必要时按提交拆分物理删除；
- 删除 `/chat` 重复处理逻辑；`/chat/stream` 只保留薄转发；
- 删除旧协议解析分支和遗留标记生成器；
- 更新 `__init__.py` 导出、README、测试导入和启动注册；
- 运行静态引用检查，确保删除后没有隐式导入失败。

完成条件：删除清单中的目标无业务主路径引用；新旧 API 只共享一套编排和写入逻辑；无未记录的兼容分支。

### D8：自动化测试、故障注入和 HTTP 验收

**目标：** 用自动化证据证明统一编排没有破坏 Phase B/C 能力。

至少增加以下测试：

| 测试 | 核心断言 |
|---|---|
| 意图规则优先 | 明确预约/咨询输入不调用 LLM 分类 |
| 模糊意图兜底 | 只有模糊输入才使用 Fake LLM |
| 工作流路由 | 每种意图进入正确 workflow，不跨 Agent 互调 |
| 工具契约 | 缺字段、未知字段和错误输出被拒绝 |
| 预约边界 | Orchestrator 通过 Phase C 领域服务完成草稿、确认、取消/改约 |
| 事件顺序 | `sequence` 单调递增，`run_started` 首发，terminal 唯一 |
| 规则路由 | 明确命令按规则表路由，冲突/模糊输入才调用 FakeLLM |
| 单轮边界 | 每轮 `run_id` 独立；下一轮从 DB 草稿/预约状态恢复，不重放上一轮事件 |
| 增量持久化 | `assistant_delta` 不单独写事实，完成后保存完整消息 |
| 工具失败 | 返回 `TOOL_FAILED`，不伪造 `run_completed` |
| 模型不可用 | 返回 `MODEL_UNAVAILABLE`，不泄漏供应商异常 |
| 会话不存在/越权 | 返回稳定错误，不访问其他会话数据 |
| 行为旁路 | BehaviorRecorder 失败不阻断主回答 |
| 客户端断开 | 释放会话锁和 DB Session，不写虚假成功状态 |
| 旧接口兼容 | `/chat/stream`、旧 task/consultation/appointment 接口只转发到统一路径 |
| 旧协议清理 | 新规范路径不产生 `[THOUGHT]`、`[REPLY]`、`[SIGNAL]` |
| 前端事件解析 | 页面只渲染用户可见事件，错误状态可恢复 |
| 全量回归 | Phase B/C 测试保持通过，既有 skip 原因不变 |
| 数据库隔离 | 测试不污染 `data/ai_front_desk.db` |

故障注入至少覆盖：分类模型失败、咨询检索失败、预约领域冲突、事件序列化失败、assistant 持久化失败、行为记录失败、客户端断开和重复 `client_request_id`。

验收顺序：

```text
契约测试 → Orchestrator 单测 → 工作流集成测试
  → API TestClient/SSE 测试 → 前端本地手工验收
  → 全量 pytest → git diff --check
```

### D9：文档、运行证据与阶段归档

**目标：** 形成可以交接给下一阶段的事实记录。

任务：

- 更新本文件的实际执行结果、测试命令、通过/跳过/失败数量和警告；
- 更新 `README.md` 的规范 turns API、事件协议、错误码、Fake 模式和兼容接口说明；
- 更新 `PROJECT_MEMORY.md` 中稳定的编排、事件和错误边界；
- 记录删除的兼容层、保留的兼容入口和回滚点；
- 记录本地自动化、手工 HTTP、真实模型、线上、鉴权和多进程验证边界；
- 仅提交 Phase D 范围文件，不提交工作区原有无关改动；
- 形成 Phase E 的 RAG 可靠性和引用返回交接项。

## 6. 推荐执行顺序

```text
D0 基线、交接和引用盘点
  ↓
D1 应用契约、事件模型和错误码
  ↓
D2 IntentRouter + ConversationOrchestrator
  ↓
D3 依赖注入与运行时边界
  ↓
D4 turns SSE 事件流
  ↓
D5 前端事件渲染与兼容切换
  ↓
D6 行为记录、日志和旧 API 错误收口
  ↓
D7 删除兼容层和重复入口
  ↓
D8 自动化、故障注入和 HTTP 验收
  ↓
D9 文档、提交和 Phase E 交接
```

建议提交边界：

```text
Phase D(test)-建立编排基线
Phase D(feat)-定义应用层契约
Phase D(feat)-新增统一会话编排
Phase D(refactor)-接入依赖注入
Phase D(feat)-统一流式事件协议
Phase D(feat)-前端解析事件流
Phase D(fix)-统一错误和行为记录
Phase D(refactor)-删除重复兼容入口
Phase D(test)-验证编排协议和故障恢复
Phase D(docs)-记录PhaseD执行结果
```

每个提交围绕一个独立大块事项；不执行 `git reset --hard`、清理或覆盖既有用户改动。

## 7. Phase D 完成定义

### 7.1 功能完成

- [ ] 存在唯一的 `ConversationOrchestrator` 负责会话轮次编排。
- [ ] 意图识别、工作流和工具调用使用结构化输入输出模型。
- [ ] 确定性规则集中在 `application/intent_rules.py`，具备优先级、冲突处理、正例/反例测试；FakeLLM 只兜底模糊输入。
- [ ] Agent 不再互相直接调用；预约写入仍只经过 Phase C 领域服务。
- [ ] `/api/v1/conversations/{conversation_id}/turns` 使用版本化 SSE/等价事件协议。
- [ ] 事件包含 `run_id`、`conversation_id`、递增序列和唯一 terminal 事件。
- [ ] 事件流明确只描述当前轮次；跨轮预约状态由持久化草稿/预约实体和会话锁承载，不能由事件重放恢复。
- [ ] 新规范路径不再生成或解析 `[THOUGHT]`、`[REPLY]`、`[SIGNAL]`。
- [ ] 对外错误码和 HTTP/SSE 错误语义统一，内部异常不泄漏。
- [ ] `user_behavior` 作为失败隔离的旁路能力接入，A-R2 状态真实可追踪。
- [ ] A-R3 的 task/consultation 旧接口完成统一适配和稳定错误响应。
- [ ] `/chat` 重复业务逻辑被删除；保留的兼容入口只有参数转换和转发。
- [ ] `AppointmentDatabase`、旧 DB Router 和 `local_db.py` 的业务主路径依赖已删除或有明确延期记录。
- [ ] 前端已切换为事件解析，页面能显示成功、失败、断开和恢复状态。

### 7.2 测试完成

- [ ] Phase B/C 原有测试无新增回归。
- [ ] Orchestrator、Router、Workflow、Tool、事件模型和错误映射测试通过。
- [ ] SSE 事件顺序、唯一 terminal、失败和客户端断开测试通过。
- [ ] 预约冲突、幂等、状态机和事务行为仍通过 Phase C 测试。
- [ ] 会话隔离、重启恢复、同会话并发和不同会话并发仍通过 Phase B 测试。
- [ ] Fake 模式下无真实 LLM/Embedding 请求。
- [ ] 测试不污染共享 SQLite 数据库。
- [ ] 行为记录失败、模型失败、工具失败和持久化失败均有故障注入证据。
- [ ] 既有 skip 项逐项记录原因；未完成内容不能通过增加测试数量伪装为完成。

### 7.3 运行和文档完成

- [ ] 新应用可以在 Fake 模式启动并通过最小 HTTP/SSE 验收。
- [ ] README、`PROJECT_MEMORY.md` 和本文件已同步规范入口、事件、错误和兼容策略。
- [ ] 运行证据明确区分本地测试、手工 HTTP、真实模型、线上、鉴权和多进程边界。
- [ ] `git diff --check` 通过。
- [ ] 阶段提交不包含已有工作区无关改动。
- [ ] Phase E 的输入、未完成事项和剩余风险已记录。

## 8. 风险、回滚与边界

| 风险 | 影响 | 缓解与回滚 |
|---|---|---|
| Orchestrator 迁移破坏现有预约链路 | 高 | 先用适配器接入；保留 Phase C 领域服务；D8 对预约全链路回归 |
| SSE 改造导致前端无法显示 | 高 | 先固定事件契约和 TestClient 测试，再改前端；保留短期 `/chat/stream` 薄包装 |
| 旧 Agent 继续生成字符串标记 | 高 | D1 先定义事件映射；D4 后禁止规范路径消费旧标记；静态检查加回归测试 |
| 工具边界不清导致 Agent 越权写库 | 高 | Pydantic 命令模型、调用边界测试和代码审查；预约只允许调用领域命令 |
| 统一错误码掩盖排障信息 | 中 | 外部脱敏、内部 request_id/run_id + 全栈日志；测试同时检查两侧信息 |
| 删除兼容层过早破坏演示接口 | 高 | D0 建引用清单；每项满足迁移条件后单独提交；必要时回滚单项提交 |
| BehaviorRecorder 失败阻断主流程 | 中 | 旁路执行、超时/异常隔离、独立测试；不改变已提交预约结果 |
| 注入容器引入隐式全局状态 | 中 | 容器只负责组装，Session/Repository 按工厂创建；测试检查生命周期 |
| SSE 客户端断开造成锁或 Session 泄漏 | 高 | `try/finally` 释放资源；增加断开故障测试和进程/连接观察证据 |
| SQLite 单进程边界被误宣称为生产能力 | 中 | 文档明确 SQLite、单进程、Fake 验证范围；多进程和线上验收延期 |
| 当前未提交 Phase C 改动与 D 变更混淆 | 高 | D0 锁定状态；不批量提交；按文件和提交范围分别验证 |

回滚原则：

1. D2/D4/D5 采用适配器和独立提交，事件协议或前端问题可单独回退。
2. 不通过恢复 Agent 互相调用、内存预约或字符串协议来伪造 Phase D 完成。
3. 如果兼容层删除后发现遗漏调用方，只回滚对应删除提交，不回滚已验证的 Phase C 领域实现。
4. 如果真实模型或线上行为尚未验证，阶段结论只能写“本地 Fake 模式通过，真实环境待验证”。

## 9. Phase D 交接到 Phase E

Phase D 完成后，应向 Phase E 输出：

- Orchestrator、IntentRouter、Workflow 和 Tool 的最终接口；
- 事件协议 v1 的事件类型、字段、顺序和失败语义；
- API 错误码、HTTP 映射、日志字段和脱敏规则；
- 预约领域服务调用边界以及不允许被编排层绕过的规则；
- user_behavior 旁路记录的实际状态和剩余 skip；
- 已删除、保留和延期的兼容层清单；
- 当前只在 SQLite、单进程、Fake 模式下验证的边界；
- Phase E 需要处理的知识库相似度阈值、关键词预过滤、引用返回和重建锁。
Phase E 不应重新定义会话事件协议或预约核心状态机，而应在 Phase D 的统一编排、错误和工具边界之上加固知识库可靠性。

## 10. Phase D 执行结果（待执行）

本节在 D0-D9 实际执行后填写，至少包含：

- 实际测试命令和完整结果（passed/skipped/failed/warnings）；
- 事件协议和错误契约的 HTTP/SSE 验收证据；
- 删除清单的逐项结果和剩余引用；
- A-R2、A-R3 的最终状态；
- 本地、Fake、真实模型、线上、鉴权、多进程验证边界；
- 提交记录、未提交改动处理和 Phase E 交接事项。

## 10. Phase D 执行结果

### D0 基线、交接和引用盘点（2026-08-17）

**工作区门禁**：
- 分支 `dev`；未提交改动 = Phase-D 计划文档（用户补充的 0.5 两个决策），已单独提交 `Phase D(docs)-补充执行决策`（c7d42d3）。
- Phase D 后续提交不与计划文档混入。

**基线测试（重新执行）**：
- 首次全量：**1 failed** —— `test_same_slot_two_users_only_one_wins`（并发确认）偶发失败，定位为 **Phase C 遗留真实 bug**：
  - SQLAlchemy 2.0 的 sqlite 方言自行管理事务（`do_begin` 按 `_isolation_lookup` 发 BEGIN），此前 `run_in_immediate_transaction` 的 `dbapi.isolation_level` 设置与 `connect_args` 均被方言覆盖，**BEGIN IMMEDIATE 从未真正生效**（实际为 deferred/autocommit），并发确认存在 lost update 窗口（两个确认可同时成功）。
  - 修复：自定义方言 `db/base/immediate_dialect.py`（`sqlite+immediate` URL），覆盖 `do_begin` 恒发 `BEGIN IMMEDIATE`；WAL 设置改用 dbapi 层执行（避免被 IMMEDIATE 事务包裹）。并发测试连跑 10/10 通过。
  - 提交：`Phase D(fix)-SQLite事务IMMEDIATE生效`（6ad6520）。

**修复后基线**：**124 passed, 10 skipped, 0 failed**（40 warnings：既有 DeprecationWarning/MovedIn20Warning）。

**引用盘点**：
| 项 | 数量/位置 |
|---|---|
| `[THOUGHT]`/`[REPLY]`/`[SIGNAL]` 旧协议字符串 | 41 处（agents/ 各 processor 的 yield 标记） |
| `busy_periods_dict` | 弃用保留（config/constants.py），无新写入 |
| 旧兼容层引用（local_db/旧 Router） | 5 文件：pattern_analyzer、user_behavior_agent、api/user_behavior_analysis、db/db_router、db/__init__（集中在 user_behavior 组件，A-R2 相关） |
| `print()` | 0（Phase A 已清零） |
| 旧 API | `/api/task/classify`、`/api/consultation/ask`（A-R3，需统一） |
| 预约 Agent 互调 | `TaskClassificationAgent` 内 `AgentRouter` 回调链（D2 拆解对象） |

**D0 结论**：基线可复现；D1 文件范围 = application/ 新契约模型 + api/core/errors.py + 对应契约测试；回滚点 = 6ad6520（修复后基线）。
