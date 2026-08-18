# Phase H 执行结果：收尾与 Phase I 交接

> 日期：2026-08-18
> 阶段：Phase H——Web 渠道产品化与买家—商家闭环
> 分支：`dev`（未合并、未推送）
> 状态：H1~H6 完成并各自提交，端到端验收通过

## 1. 已交付能力

- **H1 买家会话与咨询**：买家页面切规范会话 API（`/api/v1/conversations` + SSE `turns`）；刷新从服务端恢复历史（`GET /conversations/{id}` 直读数据库）；标签页级会话隔离（`sessionStorage`，多标签不串线）；失败分类 + 可执行重试；不通过删本地状态掩盖服务端错误（403 归属不符不再静默重建）。
- **H2 咨询到预约主路径**：新增会话级预约状态契约 `GET /conversations/{id}/appointment`（active/recent）；买家预约卡片（展示项目/时间/状态/ID、待确认确认、取消）；重载可查询未完成动作；重复/冲突由领域服务幂等与事务保证，前端不伪造成功。
- **H3 商家接管闭环**：`ConversationControl` 三态（ai_active/human_active/awaiting_human）；orchestrator 注入 `chat_control`——接管/待人工期间**阻断 AI，杜绝 AI/人工双重回复**；买家命中转人工意图 → 进入待人工队列；`POST /admin/conversations/{id}/reply` 人工回复写入同一会话（message_type=human）+ 控制事件 + 审计；会话工作台页面（列表/筛选/详情/预约关联/接管/恢复/备注/回复）；修复 buyer GET 读缓存快照导致人工消息不可见的缺口。
- **H4 契约全局审计**：集中契约测试横向审计 SSE 事件（run_started 首发、唯一终止、sequence 单调、protocol v1、handoff_required 非终止）、稳定错误码、幂等职责划分（client_request_id 轮次去重 vs idempotency_key 预约幂等）、响应公开字段；G0 契约表补 H3 契约。
- **H5 响应式与可访问性**：两页面均含移动视口 meta、`lang`、响应式样式（同一套核心流程不复制逻辑）；补 label/aria-label/aria-live/role 与 sr-only；可恢复/稳定状态契约测试。
- **H6 端到端验收**：跨角色闭环（商家登录→买家咨询→预约→商家查看→接管→人工回复→买家回读→恢复 AI）+ 故障注入（预约冲突、重复提交幂等、未登录、无 CSRF）。

## 2. 阶段提交（均在 dev，未合并/未推送）

```text
94da9d0 Phase H(feat)-买家会话恢复与隔离
381f18d Phase H(feat)-买家预约主路径与卡片
509823b Phase H(feat)-商家接管与人工回复闭环
47880d3 Phase H(test)-契约审计测试与基线
20b56ab Phase H(feat)-响应式与可访问性
（H6 收尾提交：`Phase H(test)-端到端验收测试` 与 `Phase H(docs)-H计划与I-I交接`，见 git log）
```

## 3. 验证证据

- 全量测试（H6 后）：`422 passed, 10 skipped`（H0 基线 382 → 新增约 40 个 Phase H 测试）。
  - 新增测试：`test_buyer_conversation_web.py`(9)、`test_buyer_booking_flow.py`(7)、`test_admin_handoff_web.py`(8)、`test_phase_h_contract.py`(9)、`test_web_resilience.py`(7)、`test_phase_h_acceptance.py`(3)。
- `git diff --check` 通过。
- 关键命令：
  ```text
  git branch --show-current        # dev
  python -m pytest -q              # 全量
  python -m compileall agents api application config db services web app.py
  git diff --check
  ```
- SSE/浏览器/HTTP 验收证据：各 H 阶段测试覆盖买家新建/咨询/预约/确认、商家登录/查同会话/接管/回复、买家回读人工结果、故障注入（冲突/重复/CSRF/未登录/接管阻断）、Fake 模式无真实 LLM/Embedding 请求。

## 4. 未完成与边界（不冒充完成，移交 Phase I）

- 生产级买家注册/实名/OAuth/短信登录/多因素认证；
- PII 脱敏、客户导出/删除、备份清理与数据保留；
- 多进程/多实例知识索引一致性、生产部署高可用与可观测性、成本/延迟 SLO；
- 真实模型语义质量、人工评测与线上质量分数；
- 第三方渠道（小程序/微信/美团/淘宝）、通用 `ChannelAdapter`、客户身份合并；
- 完整浏览器端人工验收与复杂筛选分页仍未建（当前以 HTTP/SSE 契约验收 + 服务端渲染资产验证，浏览器自动化/截图留待人工或 Playwright 环境）。

## 5. Phase I 交接

Phase H 已完成 Web 主路径闭环的事件、审计与业务结果基础设施；Phase I 应在已记录的事件、审计与业务结果之上完成安全、隐私、生产运行与质量评测，**不应重新实现 Phase H 的 Web 主路径**：

- 买家/商家公开页面路径、SSE/请求响应契约（`protocol_version=v1`、错误码表、`handoff_required` 事件、控制三态）；
- 幂等职责边界（`client_request_id` 轮次去重 / `idempotency_key` 预约幂等）与已验证结果；
- 人工接管状态机与审计事件结构；
- Fake/真实模型/人工评测的证据边界；
- 上节"未完成与边界"清单即 Phase I 待办输入。
