# Phase C 执行计划：预约与排班领域化

> 计划版本：2026-08-17
>
> 文档状态：待执行
>
> 所属项目：`AIFrontDesk`
>
> 前置阶段：Phase B——会话隔离与消息持久化

## 0. 阶段定位与结论

### 0.1 阶段目标

Phase C 的目标是把预约从 Agent 内部的临时字典和排班表附属字段，改造成一个可持久化、可恢复、可校验、可重试的业务领域：

```text
用户表达预约意图
    ↓
会话级预约草稿
    ↓
确定性字段校验与可用性查询
    ↓
用户确认
    ↓
事务内创建预约 + 占用时间
    ↓
可查询、可取消、可改约、可恢复
```

完成后，Agent 只负责理解、追问、推荐和发起业务命令；预约状态、时间冲突、状态迁移、数据库写入和幂等处理由确定性的领域服务负责。

### 0.2 当前代码事实

根据当前源码审查，Phase C 开始前存在以下事实：

1. `db/models.py` 只有 `Technician` 和 `TechnicianSchedule`，没有独立的 `Appointment` 实体。
2. `services/appointment_service.py::save_appointment()` 通过时间戳生成预约 ID，只新增一条 `TechnicianSchedule(status="busy")`，没有独立保存用户、项目、预约状态或会话归属。
3. `TechnicianSchedule.appointment_id` 只是可空整数列，目前没有外键约束，也没有对应的预约表。
4. `TechnicianRepository.is_technician_available()` 采用“先查询冲突、再写入排班”的分离调用，尚未形成原子预约事务。
5. `config/constants.py::busy_periods_dict` 和 `AppointmentDatabase.update_memory_schedule()` 仍维护进程级忙碌时间，不能作为可靠业务数据源。
6. `AppointmentAgent.appointment_history` 位于会话运行时对象中，Phase B 已实现会话隔离，但服务重启后未确认的预约草稿仍会丢失。
7. `api/appointment.py` 的 `/api/appointment/create` 直接实例化 `AppointmentAgent`，未形成稳定的预约领域 API。
8. `api/technician.py` 仍直接把 `TechnicianSchedule` 的 `busy/free` 语义作为排班结果对外暴露。

以上内容是当前源码事实；Phase C 计划中的模型和接口是待实施设计，不代表已经存在。

### 0.3 Phase B 交接前提

Phase B 已记录以下结果：

- `conversation_id` 已成为会话主标识。
- 用户消息和 Agent 消息可以按会话持久化和恢复。
- 会话之间的预约草稿不会串线，但预约草稿本身仍是内存态。
- Phase B 本地测试结果为 **63 passed, 10 skipped, 0 failed**。
- 会话锁目前只保证单进程边界；多进程分布式锁不纳入本阶段。
- `[THOUGHT]/[REPLY]` 流式文本协议留给 Phase D 统一处理。

Phase C 必须以当前仓库重新运行的测试结果作为基线，不直接把 Phase B 文档中的历史结果当作本次验证结果。

## 1. 必须达成的目标

### 1.1 预约领域

1. 新增独立的 `Appointment` 持久化实体。
2. 预约至少保存用户、会话、服务项目、服务人员、开始时间、结束时间、时长和当前状态。
3. 预约草稿可以从数据库恢复，不再只依赖 `AppointmentAgent.appointment_history`。
4. 预约状态迁移由显式状态机控制，禁止由 LLM 输出直接决定数据库状态。
5. 支持创建/确认、取消、改约和失败恢复。
6. 所有关键状态迁移保留可追溯记录。

### 1.2 排班与冲突

1. 明确区分“服务人员可工作/不可工作时间”和“已经被预约占用的时间”。
2. 可用性查询必须同时检查服务人员排班规则和有效预约。
3. 时间区间统一使用半开区间 `[start_time, end_time)`，避免首尾相接的预约被误判为冲突。
4. 创建和改约必须在同一业务事务内完成冲突校验与写入。
5. 取消预约后释放占用；改约失败时原预约保持不变。

### 1.3 可靠性

1. 关键写入具备明确的 `commit / rollback / close` 边界。
2. 客户端重试不会因重复请求创建多个预约。
3. 数据库写入失败时，不得向用户返回“预约成功”。
4. Agent、进程或服务重启后，已确认、已取消和待确认预约的状态可恢复。
5. 旧兼容入口继续使用同一领域服务，不再复制第二套预约写入逻辑。

## 2. 明确不做的事情

Phase C 不处理以下内容：

- 不引入统一 `ConversationOrchestrator`；属于 Phase D。
- 不重写完整流式事件协议、`[THOUGHT]/[REPLY]` 标记或统一错误码目录；属于 Phase D。
- 不实现完整商家后台、人工接管工作台和预约运营看板；属于 Phase G。
- 不实现认证、RBAC、多租户、跨店铺权限和生产级数据隔离；属于后续安全与平台化阶段。
- 不接入美团、淘宝、微信等外部渠道。
- 不引入微服务、消息队列或分布式锁。
- 不在本阶段完成长期客户记忆、偏好生命周期和摘要压缩；属于 Phase E。
- 不以修复所有既有 API 缺陷为目标；A-R3 仍交给 Phase D 的统一 API/编排阶段。
- 不把 `user_behavior` 组件整体重写作为预约领域的前置条件；A-R2 仍按 Phase B 记录的延期策略处理。

## 3. 领域模型设计

### 3.1 `Appointment` 实体

建议在 `db/models.py` 增加独立 `Appointment` 模型，字段如下：

| 字段 | 类型/约束 | 说明 |
|---|---|---|
| `id` | UUID 字符串，主键 | 服务端生成，客户端不可覆盖 |
| `user_id` | 非空字符串 | 预约归属用户 |
| `conversation_id` | 可空 UUID 字符串 | 来源会话；兼容后台创建时允许为空 |
| `service_type` | 非空字符串 | 对外服务项目字段 |
| `project` | 可空字符串 | 兼容当前 Agent 的项目字段，迁移期保留 |
| `technician_id` | 可空外键 | 草稿阶段可为空，确认时必须确定 |
| `start_time` | 可空 DateTime | 草稿阶段可为空 |
| `end_time` | 可空 DateTime | 草稿阶段可为空 |
| `duration_minutes` | 可空正整数 | 与时间区间交叉校验 |
| `status` | 非空字符串 | 见 3.2 状态机 |
| `idempotency_key` | 可空字符串，按用户唯一 | 防止重试重复创建 |
| `version` | 非空整数 | 乐观版本号，默认 `1` |
| `created_at` | DateTime | 创建时间 |
| `updated_at` | DateTime | 最近更新时间 |
| `expires_at` | 可空 DateTime | 草稿/待确认状态的过期时间 |
| `cancelled_at` | 可空 DateTime | 取消时间 |
| `cancel_reason` | 可空字符串 | 取消原因 |

说明：

- 预约 ID 建议使用 UUID 字符串，与 `conversation_id` 的标识方式一致，避免继续使用时间戳作为业务主键。
- `service_type` 是新领域字段；`project` 仅作为迁移期兼容字段，后续由统一 API 决定是否删除。
- 已确认预约必须具备 `user_id`、服务项目、服务人员、开始时间、结束时间和时长。
- 不能把 Agent 对象、Prompt、聊天全文或可执行对象序列化到 `Appointment` 表。

### 3.2 预约状态机

第一版允许以下状态和迁移：

```text
draft
  ├─→ pending_confirmation
  ├─→ cancelled
  └─→ expired

pending_confirmation
  ├─→ confirmed
  ├─→ draft              （用户修改预约信息）
  ├─→ cancelled
  └─→ expired

confirmed
  ├─→ cancelled
  └─→ confirmed           （改约成功，版本号递增）
```

规则：

1. `draft` 表示信息尚未完整或尚未通过确定性校验。
2. `pending_confirmation` 表示字段完整、时间可用，等待用户明确确认；此时不占用正式预约名额，除非后续实现明确的临时锁定策略。
3. `confirmed` 只有在冲突校验和持久化事务全部成功后才能返回给用户。
4. `cancelled` 和 `expired` 均为终态，不允许直接恢复为 `confirmed`；恢复需求通过新建预约或显式改约流程处理。
5. 改约必须使用领域服务，不允许 Agent 直接修改时间字段。
6. 非法状态迁移返回明确的领域错误，不依赖字符串模糊判断。

活跃草稿约束：

- 同一 `conversation_id` 下最多允许一个 `status IN (draft, pending_confirmation)` 的活跃预约。
- 新预约意图到来时，旧活跃草稿必须按明确规则置为 `cancelled` 或 `expired`，然后才能创建/更新新的草稿；不得留下多个无法判断归属的活跃草稿。
- `draft` 和 `pending_confirmation` 必须有 TTL 或等价过期策略。过期任务可以物理清理，也可以保留记录并标记为 `expired`；第一版优先采用“标记过期并保留事件”的可追溯方案。
- 清理任务必须可重复执行，不能误处理 `confirmed`、`cancelled` 或已被改约的预约。

### 3.3 `AppointmentEvent` 追踪实体

建议新增轻量级 `AppointmentEvent` 表，用于记录关键操作：

| 字段 | 说明 |
|---|---|
| `id` | 事件 ID |
| `appointment_id` | 预约 ID |
| `event_type` | `created`、`confirmed`、`cancelled`、`expired`、`rescheduled`、`failed` |
| `from_status` / `to_status` | 状态变化前后 |
| `request_id` | 请求或幂等请求标识，可为空 |
| `payload_json` | 非敏感的结构化变更摘要 |
| `created_at` | 事件时间 |

事件与预约主记录必须在同一个事务中写入。事件表只做审计和追踪，不作为当前状态的唯一数据源。

### 3.4 排班模型边界

Phase C 不再把 `TechnicianSchedule.status="busy"` 作为预约主数据。建议按以下语义收敛：

- `TechnicianSchedule`：服务人员工作时间、休息时间或不可用时间块。
- `Appointment`：客户预约及其状态，是“预约占用”的唯一业务来源。
- 可用性服务：查询排班约束和 `Appointment(status="confirmed")` 的时间冲突，返回统一的可用性结果。

迁移期处理：

1. 先盘点现有 `technician_schedules` 中的 `busy` 记录。
2. 对能够识别预约信息的旧记录迁移为 `Appointment` 或建立兼容映射。
3. 无法安全映射的旧记录不得静默删除，应记录迁移结果并保留只读兼容查询。
4. 新代码不得继续写入 `config.constants.busy_periods_dict`。
5. `TechnicianSchedule.appointment_id` 的去留、类型迁移和外键约束必须在 C1 的数据库方案评审中锁定；在未完成迁移前，不得声称排班与预约已完全解耦。

## 4. 事务、冲突和幂等设计

### 4.1 创建/确认预约事务

确认预约的领域服务必须遵循以下顺序：

```text
校验预约状态与必需字段
    ↓
校验用户/会话归属
    ↓
开启短生命周期数据库事务
    ↓
查询服务人员与排班约束
    ↓
查询有效预约的时间冲突
    ↓
创建或更新 Appointment
    ↓
写入 AppointmentEvent
    ↓
提交事务
    ↓
返回 confirmed 结果
```

要求：

- 冲突查询和预约写入不能继续由两个独立的 Service 调用完成。
- 失败、异常或冲突时必须回滚；不能留下“已占用排班但没有预约记录”的半成功状态。
- 事务内不得调用 LLM、天气服务或其他外部网络服务。
- 成功提示可以在事务提交后生成；生成失败不能撤销已经确认的预约，也不能伪造失败。

### 4.2 时间冲突规则

两个有效时间段 `A` 和 `B` 冲突的判断统一为：

```text
A.start < B.end AND A.end > B.start
```

边界规则：

- `end_time <= start_time` 直接拒绝。
- 相邻区间，例如 `10:00-11:00` 与 `11:00-12:00`，不冲突。
- 取消状态不参与冲突判断。
- 改约时排除当前预约自身，再检查新时间。
- 服务人员不存在、已停用或不满足排班约束时，返回确定性业务错误。

### 4.3 幂等规则

创建/确认接口必须支持 `idempotency_key`：

1. 相同用户、相同幂等键、相同业务请求重复提交，返回原预约结果。
2. 相同幂等键但请求内容不同，返回幂等键冲突，不创建新预约。
3. 第一次请求在提交后响应前断开，第二次请求仍能查到同一预约。
4. 幂等键约束必须在数据库或 Repository 层实现，不能只放在进程缓存中。
5. 取消和改约也应接受请求标识，重复执行不得产生重复事件或错误地改变状态。

### 4.4 SQLite 边界

Phase C 在当前本地 SQLite、模块化单体和单进程运行边界内验证：

- 使用短事务和数据库写锁降低并发冲突。
- 对同一服务人员的并发预约增加自动化测试。
- 不把单进程 `asyncio.Lock` 当作跨进程一致性保证。
- 多进程部署、分布式锁、数据库迁移工具和生产备份策略留给后续生产化阶段。

## 5. Agent 与会话接入方案

### 5.1 Agent 职责收缩

`AppointmentAgent` 保留以下职责：

- 从自然语言中提取项目、时间、时长、服务人员偏好等字段。
- 发现字段缺失时追问。
- 调用可用性查询并展示候选服务人员。
- 识别用户确认、取消和改约意图。
- 调用预约领域服务并展示领域服务返回的结果。

`AppointmentAgent` 不得再负责：

- 直接生成预约 ID。
- 直接写 `TechnicianSchedule`。
- 直接更新 `busy_periods_dict`。
- 通过 LLM 输出决定 `confirmed`、`cancelled` 等最终状态。
- 在数据库失败后返回成功文案。

### 5.2 草稿持久化

会话运行时可以保留解析缓存，但数据库中的预约草稿必须成为恢复来源：

1. 首次识别到预约意图时创建或获取当前会话的 `draft` 预约。
2. 每次补充结构化字段时，只写入白名单字段，并更新版本号。
3. 信息完整且可用性通过后转为 `pending_confirmation`。
4. 用户确认后调用确认命令，成功后转为 `confirmed`。
5. 服务重启后根据 `conversation_id` 查询未完成草稿，重新构建 Agent 运行时状态。
6. 草稿与消息持久化的先后顺序要明确；不能把未提交的 Agent 内存字典当作成功证据。

### 5.3 失败恢复

- 解析失败：保留已有草稿，不修改预约状态。
- 冲突：保持 `draft` 或 `pending_confirmation`，返回可选择的新时间/人员，不创建 `confirmed` 记录。
- 数据库异常：写入失败事件（若事务可用），返回稳定失败信息；不得清理原有有效预约。
- 推荐确认过程中进程重启：从 `pending_confirmation` 和结构化候选信息恢复，而不是重新猜测用户是否确认。

## 6. API 方案

### 6.1 新增版本化预约 API

建议新增以下接口，具体响应字段在 C0/C1 评审后锁定：

| 方法 | 路径 | 目的 |
|---|---|---|
| `POST` | `/api/v1/appointments` | 创建草稿或提交确认请求 |
| `GET` | `/api/v1/appointments/{id}` | 查询预约详情和当前状态 |
| `POST` | `/api/v1/appointments/{id}/confirm` | 确认待确认预约 |
| `POST` | `/api/v1/appointments/{id}/cancel` | 取消预约 |
| `POST` | `/api/v1/appointments/{id}/reschedule` | 原子改约 |
| `GET` | `/api/v1/availability` | 查询时间段和服务人员可用性 |

`POST /api/v1/appointments` 不允许同时承载两个未声明的业务语义。请求必须显式携带 `mode`：

```json
{
  "mode": "draft"
}
```

`mode` 只允许：

- `draft`：创建或更新当前会话唯一活跃草稿，不执行正式确认和时间占用。
- `confirm`：提交已有草稿或明确的完整预约进入确认事务；必须经过冲突检查，并要求 `idempotency_key`。

确认动作也可以继续使用独立的 `POST /api/v1/appointments/{id}/confirm`，但两者语义必须一致，不得各自实现一套状态迁移。

除 `mode` 外，请求必须显式携带或由服务端可靠推导：

- `user_id`
- `conversation_id`（后台场景可为空，但必须说明归属来源）
- 服务项目
- 时间和时长
- 服务人员或偏好条件
- `idempotency_key`（创建/确认必须支持）

响应至少包含：

- `appointment_id`
- `status`
- 结构化预约字段
- 领域错误时的稳定 `code` 和可读 `message`

Phase C 只定义预约领域所需的最小错误集合，例如：

```text
APPOINTMENT_NOT_FOUND
APPOINTMENT_INVALID_STATE
APPOINTMENT_TIME_INVALID
APPOINTMENT_CONFLICT
TECHNICIAN_NOT_FOUND
TECHNICIAN_UNAVAILABLE
IDEMPOTENCY_CONFLICT
APPOINTMENT_PERSISTENCE_FAILED
```

完整全局错误码规范留给 Phase D。

### 6.2 旧接口兼容

`/api/appointment/create` 继续保留为兼容入口，但必须降级为参数转换层：

1. 将旧 `AppointmentRequest` 转换为 Phase C 的领域命令。
2. 调用同一个 `AppointmentService` 或 `AppointmentCommandService`。
3. 将领域结果转换为旧 `DataResponse`。
4. 不在兼容路由中实例化一套独立 Agent 写库流程。
5. 记录旧字段 `service_type`、`preferred_time` 与新领域字段的映射。

Phase C 不删除旧接口，也不宣称已经完成全局 API 统一。

### 6.3 服务人员排班接口

现有 `/api/technicians/*/schedule` 保持兼容，但数据来源需要逐步切换：

- 排班接口展示工作/不可用时间与预约占用时段时，必须标明来源和状态。
- 预约占用必须来自 `Appointment`，不能只读取 `TechnicianSchedule.appointment_id`。
- 兼容响应字段 `appointment_id` 在迁移期允许为空；新 API 不应依赖旧表的整型 ID 假设。

## 7. 执行任务拆分

### C0：基线、遗留数据与方案锁定

**目标：** 在写模型前确认当前数据库、测试和兼容边界。

任务：

- 检查 `dev` 分支和工作区状态，确认没有覆盖用户改动。
- 重新运行 Phase B 全量测试并记录 passed/skipped/failed。
- 盘点现有 `technician_schedules`、预约行为记录和演示数据。
- 明确 `TechnicianSchedule` 的新语义、旧 `busy` 数据迁移方式和 `appointment_id` 类型策略。
- 锁定预约状态机、时间区间规则、幂等键范围和 SQLite 并发边界。
- 锁定草稿生命周期：同一 `conversation_id` 最多一个活跃 `draft/pending_confirmation`；新预约意图如何使旧草稿 `cancelled/expired`；TTL、清理频率和保留期限。
- 锁定 `POST /api/v1/appointments` 的显式模式字段，例如 `mode: "draft" | "confirm"`；禁止通过字段是否完整或隐含请求上下文猜测该请求是创建草稿还是提交确认。
- 锁定 SQLite 并发实现：确认/改约事务使用 `BEGIN IMMEDIATE`，或使用等价的数据库级占用方式；同时明确 WAL、busy timeout 和重试策略，避免只依赖进程内锁。
- 为历史兼容字段建立映射表，不在代码中隐式猜测。

完成条件：形成 C0 决策记录；所有待定模型选择均有明确结论、影响和回滚方式；草稿、API 双语义和 SQLite 并发实现不再留作隐含约定。

### C1：预约模型与数据库初始化

**目标：** 建立可持久化的预约和事件数据结构。

候选文件：

- `db/models.py`
- `db/__init__.py`
- `db/base/session_manager.py`
- `db/base/interfaces.py`
- `tests/conftest.py`

任务：

- 新增 `Appointment` 和 `AppointmentEvent` 模型。
- 添加必要索引：用户、会话、状态、服务人员+时间、幂等键。
- 明确新数据库初始化和已有 SQLite 数据库的兼容策略。
- 不把 `Base.metadata.create_all()` 可以创建新表，误认为它已经完成旧表结构迁移。
- 为状态、时间、ID 和幂等键增加模型级测试。

完成条件：临时数据库可以初始化新表；不影响 Phase B 会话和消息表；模型约束可被测试验证。

### C2：Repository 与事务单元

**目标：** 提供不依赖 Agent 的预约数据访问和事务边界。

候选文件：

- `db/repositories/appointment_repository.py`
- `db/repositories/technician_repository.py`
- `db/db_router.py`
- `db/base/session_manager.py`

任务：

- 增加预约创建、查询、更新状态、版本校验和事件写入方法。
- 提供按用户、会话、服务人员、状态查询预约的接口。
- 提供事务内冲突查询，不把读和写拆为两个独立业务调用。
- 为非空 `conversation_id` 建立“最多一个活跃草稿”的数据库约束或等价原子 upsert；仅靠应用层先查后写不算完成。
- 统一 Repository 的异常、提交、回滚和关闭行为。
- 为幂等键建立数据库约束或等价的 Repository 级原子处理。

完成条件：不经过 Agent 可以完成预约 CRUD、事件追踪和事务回滚测试。

### C3：排班语义与可用性服务

**目标：** 分离服务人员排班约束与客户预约占用。

候选文件：

- `db/models.py`
- `db/repositories/technician_repository.py`
- `services/technician_service.py`
- `services/appointment_service.py`
- `config/constants.py`

任务：

- 实现统一半开时间区间和冲突查询。
- 让可用性查询同时考虑排班约束和有效预约。
- 增加旧 `busy` 数据迁移/只读兼容策略。
- 删除新主流程对 `busy_periods_dict` 的写入和读取。
- 保留必要的旧接口适配，但不再让兼容层成为新领域数据源。

完成条件：可用性查询能够区分工作时间、不可用时间和已确认预约；边界时间、取消释放和改约回滚均有测试。

### C4：预约领域服务与状态机

**目标：** 把预约业务规则集中到确定性服务。

候选文件：

- `services/appointment_service.py`
- `services/appointment_domain.py` 或等价领域模块
- `tests/test_appointment_service.py`

任务：

- 实现创建草稿、更新草稿、进入待确认、确认、取消和改约命令。
- 实现同一会话活跃草稿的获取、替换、取消/过期和 TTL 清理命令。
- 实现状态迁移校验、必需字段校验和时间校验。
- 实现创建/确认和改约的原子冲突检查。
- 实现幂等重试和版本冲突处理。
- 确认/改约事务使用 `BEGIN IMMEDIATE` 或等价数据库级占用方案；在 WAL 模式下配置合理的 busy timeout，并对可重试的锁冲突定义有限重试策略。
- 将 `AppointmentDatabase.save_appointment()` 改为适配器或删除其直接写排班的职责。
- 保证行为记录失败不回滚已经成功的预约，但行为记录不能影响预约成功判定。

完成条件：领域服务可以在没有 LLM 的情况下独立通过核心业务测试。

### C5：Agent 草稿与领域命令接入

**目标：** 让现有预约 Agent 使用领域服务，而不是直接操纵旧状态。

候选文件：

- `agents/appointment_agent.py`
- `agents/appointment/appointment_processor.py`
- `agents/appointment/appointment_database.py`
- `agents/appointment/technician_finder.py`
- `application/session_runtime.py`

任务：

- 将当前会话预约草稿映射到持久化 `Appointment(status="draft")`。
- 服务重启后从数据库恢复未完成预约上下文。
- 将可用性查询改为调用统一领域服务。
- 将确认、取消、改约动作改为显式领域命令。
- 移除新路径对 `busy_periods_dict` 的依赖。
- 保持 FakeLLM 测试可离线运行，不让真实模型调用成为预约领域测试前置条件。

完成条件：预约 Agent 的成功、冲突、取消、改约和重启恢复路径均经过领域服务；Agent 不直接写排班表。

### C6：版本化 API 与兼容包装

**目标：** 对外提供最小可用预约 API，并保留现有页面和旧接口。

候选文件：

- `api/appointment.py`
- 新增 `api/appointments.py`
- `api/technician.py`
- `api/core/response_models.py`
- `app.py`

任务：

- 新增 `/api/v1/appointments` 及确认、取消、改约、可用性接口。
- 定义请求/响应模型，不让 API 接受任意字典直接写库。
- 将旧 `/api/appointment/create` 改为领域服务适配器。
- 定义预约不存在、归属不符、冲突、非法状态、幂等冲突和持久化失败响应。
- 更新 README 的 API、状态机和本地验证说明。

完成条件：新 API 可以创建/查询/确认/取消/改约；旧接口仍可用且不产生第二套业务逻辑。

### C7：自动化测试、并发与故障注入

**目标：** 用可重复测试证明预约领域的可靠性。

至少增加以下测试：

| 测试 | 核心断言 |
|---|---|
| 草稿持久化 | 预约字段和状态可从数据库恢复 |
| 会话归属 | 不能用其他用户或会话操作预约 |
| 草稿唯一性 | 同一会话最多存在一个活跃 `draft/pending_confirmation` |
| 草稿替代 | 新预约意图使旧活跃草稿按规则 `cancelled/expired`，不会串草稿 |
| 草稿 TTL | 过期草稿被标记 `expired`，清理任务可重复执行且不影响有效预约 |
| 确认成功 | 必需字段、可用性和事件在同一事务内完成 |
| 时间边界 | 相邻区间不冲突，交叉区间冲突 |
| 同人员并发 | 两个相同时间预约至多一个确认成功 |
| 取消释放 | 取消后时间段可重新预约 |
| 改约成功 | 新时间可用时更新并递增版本 |
| 改约冲突 | 新时间冲突时原预约保持不变 |
| 非法迁移 | 终态和非法状态不能被绕过 |
| 幂等重试 | 相同键重复请求不产生第二条预约 |
| 幂等冲突 | 相同键不同业务内容返回冲突 |
| 事务回滚 | 事件或占用写入失败时不留下半成功数据 |
| 服务重启 | 已确认、已取消和待确认状态可恢复 |
| Agent 集成 | Fake 模式下 Agent 只通过领域服务完成预约 |
| 旧 API 兼容 | 旧接口和新接口共享同一写入路径 |
| API 模式 | `mode=draft` 与 `mode=confirm` 产生明确且可验证的不同状态迁移 |
| SQLite 抢占 | `BEGIN IMMEDIATE`/等价占用、WAL、busy timeout 和有限重试下，并发测试不依赖偶然的进程锁 |
| 共享数据库隔离 | 测试不污染 `data/ai_front_desk.db` |

故障注入至少覆盖：冲突、数据库提交失败、事件写入失败、响应中断后的重复请求和领域服务返回失败。

### C8：文档、验收与阶段归档

**目标：** 形成可交接的阶段证据。

任务：

- 更新本文件的实际执行结果、测试命令和遗留问题。
- 更新 `PROJECT_MEMORY.md` 中稳定的预约领域事实。
- 更新 README 的预约 API、状态、排班语义和 Fake 模式说明。
- 记录数据库变更、兼容字段、迁移结果和回滚步骤。
- 运行 `git diff --check`、全量测试和必要的 HTTP 端点验收。
- 区分本地测试、手工 HTTP 验收、线上/鉴权验收；未执行的内容不得标记完成。
- 只提交 Phase C 范围内的文件，不混入 Phase D 或无关改动。

## 8. 推荐执行顺序

```text
C0 基线与方案锁定
  ↓
C1 Appointment / AppointmentEvent 模型
  ↓
C2 Repository 与事务单元
  ↓
C3 排班语义与可用性服务
  ↓
C4 预约状态机、冲突和幂等
  ↓
C5 Agent 草稿与领域命令接入
  ↓
C6 新 API 与旧接口包装
  ↓
C7 自动化、并发和故障测试
  ↓
C8 文档、验收与交接
```

建议提交边界：

```text
Phase C(test)-建立预约领域基线
Phase C(feat)-新增预约与事件模型
Phase C(refactor)-分离排班与预约数据
Phase C(feat)-实现预约状态机
Phase C(feat)-接入预约幂等事务
Phase C(refactor)-预约Agent调用领域服务
Phase C(feat)-新增预约管理API
Phase C(test)-验证冲突恢复与并发
Phase C(docs)-记录PhaseC执行结果
```

每个提交应围绕一个独立大块事项；不执行 `git reset --hard`、清理或覆盖既有用户改动。

## 9. Phase C 完成定义

### 9.1 功能完成

- [ ] 存在独立的 `Appointment` 记录，预约不再只存在于 `TechnicianSchedule` 或 Agent 字典中。
- [ ] 预约草稿、待确认、已确认、已取消和已过期状态可以持久化和恢复。
- [ ] 同一 `conversation_id` 最多存在一个活跃 `draft/pending_confirmation`，新预约意图会明确终止旧草稿。
- [ ] 草稿/待确认预约具备 TTL 和可重复执行的过期清理策略。
- [ ] 状态迁移只能通过确定性领域服务完成。
- [ ] 排班约束与预约占用已经分离，并有明确的数据来源。
- [ ] 时间冲突使用统一半开区间规则。
- [ ] 创建/确认、取消、改约均具备事务边界。
- [ ] 改约冲突时原预约不受破坏；取消后占用正确释放。
- [ ] 重复请求不会产生重复预约。
- [ ] `POST /api/v1/appointments` 使用显式 `mode=draft|confirm`，不会通过隐含条件解释双重语义。
- [ ] 同人员并发确认使用 `BEGIN IMMEDIATE` 或等价数据库级占用方案，并验证 WAL、busy timeout 和有限重试行为。
- [ ] Agent 不再直接写 `TechnicianSchedule` 或 `busy_periods_dict`。
- [ ] 旧预约接口仍可用，但只作为新领域服务的兼容包装。

### 9.2 测试完成

- [ ] Phase B 原有测试无新增回归。
- [ ] 预约 Repository、状态机和领域服务测试通过。
- [ ] 冲突边界、取消、改约、幂等、事务回滚和并发测试通过。
- [ ] Fake 模式下无真实 LLM/Embedding 请求。
- [ ] 测试使用临时数据库，不污染共享 `data/` 数据库。
- [ ] 数据库提交、回滚、关闭和异常路径有可验证证据。
- [ ] 既有 `user_behavior` 的 10 个 skip 仍被准确记录，不被伪装为 Phase C 通过。

### 9.3 运行和文档完成

- [ ] 新数据库可初始化，已有数据库的迁移/兼容策略已记录并验证。
- [ ] 新 API 已通过 TestClient 契约测试和最小 HTTP 端点验收。
- [ ] README、`PROJECT_MEMORY.md` 和 Phase C 执行记录已同步。
- [ ] `git diff --check` 通过。
- [ ] 阶段文档记录实际测试结果、跳过项、警告、遗留风险和下一阶段交接事项。
- [ ] 尚未完成的鉴权、线上、生产多进程和外部渠道验收保持明确的“待验证”状态。

## 10. 风险、回滚与边界

| 风险 | 影响 | 缓解与回滚 |
|---|---|---|
| 旧 `busy` 排班数据无法可靠映射 | 高 | C0 先盘点；无法映射的记录只读保留并记录，不静默删除 |
| 预约与排班迁移期间出现双重数据源 | 高 | 新写入只走领域服务；兼容查询明确来源和截止时间 |
| 先查可用、后写入造成并发重复预约 | 高 | 在同一事务内完成冲突校验与写入；增加并发测试 |
| 改约失败破坏原预约 | 高 | 新时间校验和写入失败必须回滚，原记录保持不变 |
| Agent 仍保留旧直接写库路径 | 高 | C5 增加调用边界测试和代码审查；旧类只保留适配职责 |
| 幂等键只存在内存 | 高 | 数据库唯一约束或等价原子查询；重启后仍可恢复 |
| 草稿长期堆积或多个草稿串联 | 中 | 会话活跃草稿唯一约束；新意图终止旧草稿；TTL 标记 `expired` 并保留事件 |
| API 双语义导致误确认 | 中 | `mode=draft|confirm` 必填；领域服务和独立 confirm 路由共享同一状态迁移 |
| `Base.metadata.create_all()` 被误当作迁移 | 中 | 记录实际迁移步骤；对已有 SQLite 文件做结构验证 |
| SQLite 单写者限制导致测试不稳定 | 中 | `BEGIN IMMEDIATE`/等价占用、WAL、busy timeout 和有限重试；明确单库边界，不宣称多进程能力 |
| 预约成功提示依赖天气/LLM | 中 | 先提交业务事务，再生成可选关怀文案；文案失败不改变预约结果 |
| Phase C 范围扩展到统一编排或后台 | 中 | 将 Orchestrator、事件协议、商家后台列为 Phase D/G，不在本阶段混入 |

回滚原则：

1. 模型、Repository、领域服务、Agent 适配和 API 变更按提交边界可单独回退。
2. 如果新 API 尚未稳定，可暂时保留旧接口包装，但旧接口必须继续调用同一领域服务。
3. 如果数据库迁移无法证明数据安全，应停止迁移并保留原表只读，不以删除旧数据换取测试通过。
4. 不通过“回退到内存预约”来伪造 Phase C 完成；持久化失败必须报告为阻塞或未完成。

## 11. Phase C 交接到 Phase D

Phase C 完成后，应向 Phase D 输出：

- `Appointment`、`AppointmentEvent` 的最终字段和状态迁移图。
- 预约与排班的最终数据边界，以及旧 `technician_schedules` 兼容层去向。
- 新预约 API 的请求、响应、错误码和幂等语义。
- Agent 调用预约领域服务的命令接口和返回模型。
- 冲突、并发、回滚、重启恢复的实际测试证据。
- 当前仍只在 SQLite、单进程、Fake 模式下验证的边界。
- A-R2 `user_behavior` 和 A-R3 旧 API 缺陷的后续处理状态。
- 尚未完成的认证、权限、生产多进程、线上和外部渠道验收。

Phase D 不应再次重写预约核心状态机，而应在 Phase C 已验证的领域服务之上统一 Agent 编排、工具输入输出、流式事件和全局错误语义。
