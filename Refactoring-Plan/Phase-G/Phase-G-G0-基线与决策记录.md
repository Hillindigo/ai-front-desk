# Phase G G0：基线与决策记录

> 记录日期：2026-08-18  
> 阶段：Phase G——商家运营后台、权限与门店隔离  
> 状态：G0 完成，允许进入 G1  
> 仓库：`D:\1_qiuzhao\AIFrontDesk`

## 1. 基线证据

### 1.1 Git 与工作区

| 项目 | 实际结果 |
|---|---|
| 当前分支 | `dev` |
| G0 开始时最新提交 | `5fcf0a2 Phase F(docs)-收尾与PhaseG交接` |
| G0 开始时工作区 | 有 16 个已跟踪文件改动，另有 `Refactoring-Plan/Phase-G/` 未跟踪 |
| G0 处理结果 | Phase F 收尾改动已独立提交；Phase G 文档单独保留 |
| Phase F 收尾提交 | `db37cc9 Phase F(fix)-知识回滚与兼容` |
| 合并/推送 | 未执行 |

G0 开始时的 16 个已跟踪改动经逐文件检查，内容均属于 Phase F 收尾修复或验证补强：

- 知识发布失败时恢复数据库、知识版本和内存索引快照；
- 知识预览接口补充身份兼容字段校验；
- 旧咨询代理注入容器中的唯一 `KnowledgeService`；
- 知识评测引用完整率改为使用实际评测结果；
- 补充对应的发布回滚、身份边界、容器单实例和评测测试；
- 兼容不同 Starlette 版本的模板响应参数；
- Phase E/F 阶段文档同步已完成项和格式修订。

这些改动没有混入 Phase G 功能代码，已使用独立提交 `db37cc9` 固化。Phase G 计划及本记录属于 Phase G 文档边界。

### 1.2 测试基线

G0 当天执行：

```text
python -m pytest -q
343 passed, 10 skipped, 89 warnings in 18.64s
```

10 个 skip 仍来自 `tests/test_user_behavior_agent.py`，原因是旧 `user_behavior` 组件的测试与当前 API 签名、方法名和返回结构脱节。本阶段不把这些 skip 视为已完成能力。

```text
git diff --check
通过
```

当前确认的非阻塞警告主要是 SQLAlchemy `declarative_base()` 和 FastAPI `on_event` 弃用警告；不在 G1 中顺手扩大为框架升级任务。

### 1.3 代码事实盘点

| 领域 | 当前事实 | G 处理边界 |
|---|---|---|
| 身份 | `IdentityResolver` 只有 `DemoIdentityResolver`，固定 `default_user` | G1 新增独立商家身份，不修改客户身份语义 |
| 会话 | `Conversation.user_id`，无 `store_id`；消息通过会话归属 | G2 回填会话门店，消息继续继承会话范围 |
| 预约 | `Appointment.user_id`，无 `store_id`；状态由 `AppointmentCommandService` 控制 | G2 增加门店归属；G5 只适配领域命令 |
| 知识 | `KnowledgeDocument` 已有 `draft/published/archived/failed` 字段和迁移回填逻辑；当前仍为单进程索引 | G2 增加门店范围；多进程一致性不在本阶段冒充完成 |
| 服务人员 | `Technician`、`TechnicianSchedule` 无门店字段 | G2/G3 迁移为门店资源，保留现有冲突语义 |
| 偏好 | `Preference`、`PreferenceTombstone` 按客户 `user_id`，无门店字段 | G2 增加客户门店边界，删除墓碑语义保持不变 |
| 用户行为 | `UserBehavior` 和旧兼容组件存在，但有 10 个 skip | G6 不依赖旧行为分析组件，使用已验证会话/预约/偏好事实 |
| 页面 | `/admin`、`/knowledge`、`/technician`、`/technician_schedule` 等 Jinja 页面已存在 | G8 复用可复用页面，逐步收敛到鉴权后台壳层 |
| 审计 | 预约已有 `AppointmentEvent`，尚无通用商家操作审计 | G3 建立通用审计契约并接入关键写操作 |

### 1.4 当前配置事实来源

G0 盘点结果：

- 知识库默认数据中包含营业时间、服务项目价格/时长和预约政策，但这些目前是知识文档内容，不是结构化门店配置事实；
- 预约领域服务已有服务类型、时长、预约 TTL、时间冲突和状态迁移语义，但没有独立的门店营业时间/价格/预约政策模型；
- 服务人员和排班已有 `technicians`、`technician_schedules` 表及对应 Service/Repository；
- 因此 G3 不直接把知识文档当作配置写入目标，也不复制预约状态机；先建立结构化门店配置事实来源，再决定哪些知识文档由配置发布流程生成或同步。

## 2. G0 设计决策

### D-G1：商家身份与客户身份分离

- 新增商家账号实体，不复用客户 `user_id`；
- 商家账号保存用户名/邮箱标识、scrypt 强哈希密码、启停状态和时间字段；不保存明文密码；
- 使用 SQLite 持久化服务端会话，Cookie 只携带不可推导的随机 session token；服务端保存 token 哈希、actor、active store、过期时间和撤销时间；
- `IdentityResolver` 保留抽象，G1 增加商家 resolver；`DemoIdentityResolver` 只保留给显式测试夹具和客户兼容路径；
- 登录失败不区分账号不存在和密码错误。

选择 Python 标准库 `hashlib.scrypt`、`secrets` 和 HMAC，暂不新增密码库依赖；后续若进入生产部署，再单独评估密码参数和密钥轮换。

### D-G2：角色与权限

角色固定为 `owner`、`manager`、`operator`、`viewer`。权限以服务端 capability 检查实现，不依赖页面隐藏按钮。

| 权限 | owner | manager | operator | viewer |
|---|---:|---:|---:|---:|
| 查看会话/预约/客户 | ✓ | ✓ | ✓ | ✓ |
| 知识发布 | ✓ | ✓ | 按显式授权 | ✗ |
| 门店配置 | ✓ | ✓ | 读 | 读 |
| 预约确认/取消/改约 | ✓ | ✓ | ✓ | ✗ |
| 偏好读取/回访 | ✓ | ✓ | ✓ | 读 |
| 成员与权限管理 | ✓ | 读 | ✗ | ✗ |
| 审计查询 | ✓ | 读 | ✗ | ✗ |

G1 将权限拆成可测试的 capability，不把角色判断散落在路由函数中。

### D-G3：门店上下文与迁移

- 新增 `stores`、`store_memberships`；G1 提供门店列表/切换所需的最小结构，G2 完成所有业务表回填和强制 scope；
- 创建唯一默认演示门店，将当前单门店数据回填到该门店；迁移保留原字段和回滚脚本；
- 会话、预约、知识、服务人员、排班、偏好和行为数据都必须有直接或可通过父实体解析的门店范围；
- `Message` 通过 `Conversation` 继承门店；`AppointmentEvent` 通过 `Appointment` 继承门店；摘要、偏好墓碑和知识元信息同步审查归属；
- Repository 的公开查询接口使用显式 `store_id`/`StoreContext`，不提供默认全表查询；
- 门店切换先验证 membership，再写入服务端 session 的 active store；客户端提交的 store_id 只作为候选值，不能直接授权。

### D-G4：会话与 CSRF

- Cookie：HttpOnly、SameSite=Lax；本地 HTTP 测试允许 `Secure=False`，部署配置默认要求 HTTPS 与 `Secure=True`；
- 所有 POST/PUT/PATCH/DELETE 后台写请求必须带服务端 session 绑定的 `X-CSRF-Token`；token 使用 `secrets.token_urlsafe`，只在登录/当前身份接口返回，不写入权限真相的 localStorage；
- 登出、过期、撤销和权限变更后旧 session/CSRF token 立即失效；
- G1 测试覆盖无 Cookie、过期 Cookie、缺 CSRF、错误 CSRF、正确 CSRF 和跨账号 token。

### D-G5：人工接管

- 使用会话控制状态/事件，而不是不可追踪的单一布尔值；
- 初始状态 `ai_active`，可切换为 `human_active`，并记录 actor、原因和时间；
- G4 再锁定接管期间 AI 是否暂停生成、并发写入和断线恢复的具体状态机；未锁定前不增加后台回复旁路。

### D-G6：审计事务语义

- 关键业务写入与审计事件在同一 SQLite 事务中提交；审计失败则业务失败并回滚，不静默丢审计；
- 审计事件包含 `event_id/actor_id/store_id/action/resource_type/resource_id/request_id/outcome/summary/created_at`；
- summary 只保存安全变更摘要，禁止密码、Cookie、token、Prompt、完整客户敏感内容和供应商响应；
- 指标优先从审计事件、预约事件、知识版本和来源 metadata 派生，不额外创建无法解释的计数事实。

### D-G7：知识索引边界

G2 只实现按门店的快照、版本和刷新状态隔离。当前多进程刷新一致性仍是阻塞项；在未完成前，不开启多进程多门店演示，不宣称生产可用。

## 3. G1 进入条件与提交边界

G0 已满足：

- 当前分支和基线已记录；
- 既有未提交改动已归属并独立提交；
- 343/10/0 测试基线已记录；
- `archived` 状态已由当前 ORM 和迁移代码确认；
- G3 的现有配置数据来源已盘点；
- 页面复用范围已锁定；
- 身份、角色、门店、CSRF、人工接管和审计的关键方向已记录。

进入 G1 后，每个子阶段独立提交，格式遵循：

```text
Phase G(类型)-标题
```

G1 第一条代码提交必须同时包含：服务端身份边界、对应失败测试、最小实现和测试结果；不得先写无测试的认证代码。

## 4. G0 未纳入范围

- 不在 G0 修改 Phase D/E/F 业务语义；
- 不在 G0 删除旧 API 或旧表；
- 不在 G0 声称已完成真实生产鉴权、多进程索引、PII 脱敏、外部渠道和真实模型质量；
- 不更新 `AGENTS.md`：当前仓库未找到该文件，长期项目约定以 `PROJECT_MEMORY.md` 和本阶段文档为准。
