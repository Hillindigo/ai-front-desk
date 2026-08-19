# Phase H G0：基线与决策记录

> 日期：2026-08-18
> 分支：`dev`
> 状态：H0 基线、归属裁定与契约基线锁定；可进入 H1
> 依据：`Refactoring-Plan/Phase-H/Phase-H-执行计划.md` §1；`PROJECT_MEMORY.md` §9 Phase G 交接
> 本文件为 H0 交付物，后续 G 阶段如需调整契约/边界，先更新本文件再动工。

---

## 1. 基线快照与验证证据

### 1.1 Git 基线

- 当前分支：`dev`（未合并、未推送；`HEAD` 在本次 H0 之前包含 Phase G 收尾提交）。
- H0 开工前 `git status` 存在 21 个 modified + 2 个 untracked（Phase-G 收尾后未提交改动）。
- 全量测试基线（H0 验证时实测）：`382 passed, 10 skipped`。
  - 对比 Phase G 收尾记录的 `380 passed, 10 skipped`：新增 2 个涵盖知识隔离的安全回归测试。
  - `git diff --check` 通过（仅 CRLF 提示，无空白错误）。

### 1.2 验证命令（记录于本阶段）

```text
git branch --show-current                 # dev
git status --short                        # 见 §2 归属
git log --oneline -12                     # HEAD 之前 Phase G 收尾链
python -m pytest -q                       # 382 passed, 10 skipped
python -m compileall agents api application config db services web app.py   # 待 H1 时执行补记
git diff --check                          # 通过
```

---

## 2. 未提交改动归属表（用户已裁定）

### 2.1 归属结论

H0 开工前的未提交改动经逐文件盘点与用户（2026-08-18）裁定，**组 1~4 不纳入 Phase H**，作为 **Phase G 安全/审计收尾补丁**独立提交进 `dev`；仅 `Refactoring-Plan/Phase-H/` 属于 Phase H 本阶段。

### 2.2 分配到的 Phase G 提交（已在 dev）

| 提交 | 类型 | 内容要点 |
|---|---|---|
| `Phase G(security)-Cookie与CSRF加固` | security | `api/admin_auth.py`：secure cookie 可配置（`ADMIN_COOKIE_SECURE`）、CSRF cookie 独立（httpOnly=False 供 JS 读）、登出删除 CSRF cookie |
| `Phase G(security)-知识管理门店隔离鉴权` | security | `container.get_knowledge_bundle(store_id)` 按门店懒加载隔离实例；知识管理 API 改 `require_permission(view_knowledge/publish_knowledge)`+`require_csrf`+active_store 绑定；`knowledge_repository`/service 门店 scope；新增 `tests/test_phase_g_security_regressions.py`；`web/routes.py`+管理模板接线 |
| `Phase G(feat)-预约操作审计事件` | feat | `appointment_domain` confirm/cancel/reschedule 增加 `actor_id` 写 `AuditEvent`；`AdminAppointmentService` 改用 `DatabaseRouter` 并传入共享 command service |
| `Phase G(docs)-更新重构路线图` | docs | `Refactoring-Plan/All/整体重构路线图.md` |

### 2.3 变更边界（Phase H 承诺）

- Phase H 只对下述白名单（买家/商家 Web、Web API/SSE 契约、端到端验收）内新增与修改文件建立提交。
- 提交前复核：`git branch --show-current` 为 `dev`、格式 `Phase H(类型)-标题≤15字`、不携带未归属文件。
- 遇到归属不清的改动：暂停提交，记录证据与候选分类，请用户裁定，不以"看起来属于 H"为默认依据。
- 不用 `reset`/清理/覆盖处理已有工作区修改。

---

## 3. 采购端/商家端契约基线（H0 锁定，H1~H4 在此之上工作）

> 以下为当前代码事实（经源码核对），作为 H1~H3 页面开发和 H4 全局审计的参照基线。
> "已锁定"表示契约形态已固定；契约变更时先更新本文件与兼容测试，再删旧消费者依赖。

### 3.1 买家会话 / SSE 契约

| 资源/方法 | 请求要点 | 响应要点 | 说明 |
|---|---|---|---|
| `POST /api/v1/conversations` | `{user_id, channel}` | `{conversation_id, user_id, channel}` | 新建会话 |
| `GET /api/v1/conversations/{id}` | `?user_id=` | 会话 + 消息历史 | `user_id` 为兼容字段 |
| `GET /api/v1/conversations/{id}/sources` | `?user_id=` | 来源列表 | 供前端来源卡片 |
| `POST /api/v1/conversations/{id}/turns` | `{message, user_id, client_request_id}` | SSE 事件流 | `client_request_id`=请求去重标识，**不替代预约幂等键** |

SSE 事件包（`protocol_version: v1`，`EventEnvelope{run_id, conversation_id, sequence, type, data}`）：

- 事件类型：`run_started`(首发) → `intent_detected` / `workflow_started` / `tool_started` / `tool_result` / `assistant_delta` / `assistant_message` / `handoff_required`(H3 转人工/接管中，非终止) → **唯一终止** `run_completed` 或 `run_failed`。
- 终止事件集合 `TERMINAL_EVENTS = {run_completed, run_failed}`；每轮只有一个终止事件。
- `sequence` 单调递增；心跳为 SSE 注释帧。
- 内部标记 `[THOUGHT]`/`[SIGNAL]` 不进入事件流；`[REPLY]` 剥离标记保留可见内容（`application/events.py::clean_token`）。

### 3.2 预约领域契约（Phase C，H2 复用，不重写状态机）

- 状态机：`draft → pending_confirmation → confirmed / cancelled / expired`。
- 幂等：`idempotency_key` 传播至预约命令；`BEGIN IMMEDIATE` 事务内冲突校验（`sqlite+immediate`），防并发/重复写入。
- 操作：create / confirm / cancel（任意非终态→cancelled，重复取消幂等）/ reschedule（confirmed→confirmed，排除自身冲突）。
- 错误码：`APPOINTMENT_CONFLICT`、`APPOINTMENT_STATE_INVALID`、`IDEMPOTENCY_CONFLICT`。

### 3.3 商家会话工作台 / 人工接管契约（Phase G）

- 身份/权限边界：商家服务端会话（Cookie/CSRF）→ RBAC `owner/manager/operator/viewer` → 权限含 `view_sessions`、`manage_conversations`、`view_knowledge`、`publish_knowledge`、`write_appointments`、`read_customer_preferences`。
- 门店上下文：`active_store`；所有查询/写入按 `store_id` 隔离；跨门店访问返回稳定错误（404/403）。
- 会话接管状态机（H3，控制三态在 `ConversationControl.mode`）：`ai_active`（AI 正常）/ `human_active`（人工接管中）/ `awaiting_human`（买家已转人工，待处理）。接管/待人工期间 AI 不得自动继续同一会话（orchestrator `chat_control` 阻断，H3 实现）；恢复后按规则重新允许 AI。服务端状态机 + 应用层检查优先于页面按钮。
- 人工回复：`POST /api/v1/admin/conversations/{id}/reply`（权限 `manage_conversations` + CSRF + 门店范围）。人工消息写入同一会话事实来源（`message_type=human`），自动置 `human_active` 接管态，并产生 `ConversationControlEvent(action=human_reply)` 与审计事件；买家刷新（`GET /conversations/{id}`）可读到，消息权威在数据库（H3 修复：GET 直读 DB，不依赖运行时缓存快照）。
- 转人工队列：买家消息命中转人工意图（"转人工/人工客服/真人客服"等，`orchestrator._is_human_request`）→ 控制置 `awaiting_human`；商家会话列表每项带 `control_mode` 字段，可筛待人工队列。
- 管理动作（会话、预约、客户、审计、指标）均有审计事件可追溯（`AuditEvent`，含 `actor_id`/`store_id`/`action`/`resource`/`request_id`/`outcome`）。

### 3.4 身份边界（H0 明确，禁止客户端字段作为权限来源）

- 买家侧：`IdentityResolver`（演示 `DemoIdentityResolver` → `default_user`）；请求体 `user_id` 仅为兼容字段，不得作为权限来源。
- 商家侧：`AdminAuthService` 服务端会话 + Cookie + CSRF；任何情况禁止把客户端 `user_id`/`role`/`store_id` 作为权限来源。
- `localStorage` 仅作客户端导航提示，不作为身份/权限/业务事实来源；刷新后从服务端恢复会话与预约。

### 3.5 错误码表（公开契约）

`INVALID_INPUT`(422) / `CONVERSATION_NOT_FOUND` / `CONVERSATION_ACCESS_DENIED` / `INTENT_UNSUPPORTED` / `APPOINTMENT_CONFLICT` / `APPOINTMENT_STATE_INVALID` / `IDEMPOTENCY_CONFLICT` / `MODEL_UNAVAILABLE` / `TOOL_FAILED` / `INTERNAL_ERROR`(500)。商家侧统一 401/403/CSRF 语义（`/api/v1/admin/...` 前缀）。

---

## 4. 主路径状态机与失败状态表

### 4.1 买家主路径状态机（H1~H2）

```text
[新会话/继续会话] → [咨询] → [收集预约信息] → [确认前展示待确认]
   → [创建预约 pending_confirmation] → [确认 confirmed]
   → [改约/取消]          ← 商家接管/人工反馈后可回读
   → [转人工状态] → 商家待处理队列 → 买家回读最新结果
```

- 关键点：前端**不直接写数据库**；创建/确认/改约/取消均由预约领域服务决定。
- 知识回答只使用已发布且有依据的门店事实；预约政策/价格/地址不由页面硬编码。
- 失败不伪造成功：预约领域错误映射为稳定用户可见状态，不把失败响应渲染为成功卡片。

### 4.2 失败状态表（H1~H5 需覆盖的界面状态）

| 触发 | 页面行为 | 可恢复动作 |
|---|---|---|
| 服务不可用/模型失败 | 明确失败态，不显示伪成功 | 可执行重试；不可重试错误提示 |
| 会话不存在/归属不符 | 显示错误提示 | 导航到新建会话，不删服务端数据 |
| 会话过期 | 明确过期态 | 重新认证/新建会话 |
| 重复提交/双击/断线重试 | 幂等去重，不产生重复业务写入 | 以终止事件 + 服务端状态查询判断成功，不以客户端已显示文本判断 |
| 预约冲突 | 可理解替代方案或转人工 | 转人工路径 |
| 无权限/跨门店/失效 CSRF | 稳定错误码 | 重新认证/正确门店上下文 |
| 转人工 | 显示转人工状态 | 商家待处理队列处理 |

---

## 5. 浏览器验收矩阵（H5/H6 执行）

| 场景 | 角色/视口 | 前置数据 | 预期结果 | 证据 |
|---|---|---|---|---|
| 新建/继续/恢复会话，双会话不串线 | 买家桌面/移动 | 两会话 | 各自独立 | 测试 + 截图 |
| 咨询→来源/有依据/无依据/错误状态区分 | 买家 | 已发布知识 | 来源卡片、拒答、错误区分 | HTTP/SSE 摘要 |
| 收集→确认→创建预约→核对业务结果 | 买家 | 门店服务目录 | 确认动作 + 预约 ID/状态 | HTTP 摘要 |
| 改约/取消/冲突/重复提交/失败重试 | 买家 | 预约数据 | 幂等、无重复写入 | 故障注入测试 |
| 刷新/短断线后恢复 | 买家 | 会话/预约 | 恢复到最新状态 | 截图 |
| 转人工状态可见 | 买家 | 人工触发 | 转人工标识 | 截图 |
| 商家登录→查同会话/预约→接管→回复→恢复 AI | 商家桌面 | 会话/预约 | 状态机正确、双重回复禁用 | 录屏 + HTTP |
| 商家改约/处理异常后买家回读最新状态 | 双端 | 修改后 | 买家见最新结果 | 截图 |
| 无权限/跨门店/CSRF/会话过期/某接口失败 | 双端 | 故障注入 | 稳定错误态、可恢复 | 记录 |

> Fake 模式必须确认无真实 LLM/Embedding 网络请求；真实模型只记录独立待评测结果，不作为自动化门禁。

---

## 6. 回滚点与回滚原则

1. 页面功能优先通过独立模板/静态资源和小范围 API 适配提交，避免破坏现有规范 API。
2. 契约变更先增加兼容读取和测试，再删除旧消费者依赖。
3. 任何无法证明幂等或状态一致性的写操作不上线主路径，回退到只读/人工处理路径。
4. 不使用 `reset`、清理命令或覆盖操作处理已有工作区修改。
5. 明确哪些事项是本地 Fake 验证、哪些需浏览器人工验证、哪些仍属 Phase I。

---

## 7. 未纳入清单与 Phase I 边界（H 不冒充完成）

以下事项 **不属于 Phase H 完成范围**，交由 Phase I 或独立阶段：

- 生产级买家注册/实名/OAuth/短信登录/多因素认证；
- PII 脱敏、客户导出/删除、备份清理与数据保留制度；
- 多进程/多实例知识索引一致性、生产部署高可用与可观测性；
- 真实模型语义质量、人工评测、成本/延迟监控与线上 SLO；
- 第三方渠道（小程序/微信/美团/淘宝）、通用 `ChannelAdapter`、客户身份合并。
- 知识管理的生产密钥/部署加固与完整安全运营。

Phase I 不应重新实现 Phase H 的 Web 主路径，而应在已记录的事件、审计和业务结果之上完成安全、隐私、生产运行与质量评测建设。

---

## 8. H1 前置契约门槛（已满足检查项）

- [x] 买家会话创建/读取、turns 请求响应、SSE 事件包、`client_request_id`、会话归属约定已在本文件 §3.1 锁定。
- [x] 预约状态机与幂等边界在本文件 §3.2 锁定（H2 只复用，不重写）。
- [x] 商家身份/RBAC/门店/接管/审计边界在本文件 §3.3 锁定。
- [x] 身份边界与"禁止客户端字段当权限来源"在本文件 §3.4 锁定。
- [x] 工作区未提交改动已归属并经用户裁定（§2），Phase H 起点干净。

**结论：契约基线已锁定，可进入 H1（买家 Web 会话与咨询体验）。**
