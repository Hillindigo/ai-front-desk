# Phase G 执行结果：收尾与 Phase H/I 交接

> 日期：2026-08-18  
> 阶段：Phase G——商家运营后台、权限与门店隔离  
> 分支：`dev`  
> 状态：G9 验收完成，待用户决定是否合并/推送

## 1. 已交付能力

- 商家账号与客户 `user_id` 分离；scrypt 密码哈希；可撤销服务端会话；HttpOnly/SameSite Cookie；写操作 CSRF token；
- `owner/manager/operator/viewer` RBAC 及服务端 capability 检查；管理页面不再匿名开放；
- `Store`、`StoreMembership`、默认门店迁移和核心业务表 `store_id` 边界；会话、预约、知识、服务人员、偏好和客户查询支持门店 scope；
- 知识服务实例绑定门店，正式检索不会把其他门店文档纳入当前快照；
- 结构化门店资料、服务目录、价格/时长、营业时间和预约政策；非法配置由服务层拒绝；
- 会话列表/详情、人工接管、恢复 AI、内部备注和控制事件；
- 门店范围预约列表/详情，确认、取消、改约通过既有 `AppointmentCommandService`；
- 客户列表/详情、偏好/预约事实读取和回访任务创建/完成/取消；
- 审计查询和基础运营指标；
- 鉴权后台壳层 `/admin`，显示当前账号、角色、门店和运营模块入口。

## 2. 阶段提交

```text
c5feb88 Phase G(docs)-G0基线与决策
cb3c495 Phase G(feat)-身份与RBAC
3552dde Phase G(feat)-门店隔离与迁移
ef61bed Phase G(feat)-门店配置与审计
9ff5430 Phase G(feat)-会话工作台接管
db6a8f0 Phase G(feat)-预约管理适配
31a0690 Phase G(feat)-客户运营与回访
de555db Phase G(feat)-审计与运营指标
2c8c3ab Phase G(feat)-后台壳层与导航
```

Phase F 在 Phase G 开始前另有独立收尾修复提交：

```text
db37cc9 Phase F(fix)-知识回滚与兼容
```

所有提交均在 `dev`，未合并、未推送。

## 3. 验证证据

G9 HTTP 验收覆盖：

- 登录→后台壳层→会话/预约/客户/审计/指标查询；
- CSRF 写保护；
- 当前门店上下文；
- 配置变更后审计可查询；
- 后台页面刷新后仍由服务端 Cookie 恢复身份和门店；
- Fake 模式测试，不调用真实 LLM/Embedding。

阶段期间全量测试结果从 G0 的 `343 passed, 10 skipped` 增长到最终结果；最终命令和结果以 G9 实际输出为准：

```text
python -m pytest -q
380 passed, 10 skipped, 181 warnings in 31.08s
```

`git diff --check`：通过。

已知 181 条 warning 主要是 SQLAlchemy `declarative_base()` 和 FastAPI `on_event` 弃用提示，不属于本阶段功能失败。

## 4. 未完成与边界

以下事项没有被冒充为 Phase G 已完成：

- 多进程/多实例知识索引刷新一致性；当前仍是单进程边界；
- 生产密钥管理、HTTPS 部署加固、密码参数轮换和完整安全运营；
- PII 脱敏、客户导出/删除、备份清理和数据保留策略；
- 真实模型质量、人工语义评测和线上质量分数；
- 外部渠道接入（美团、淘宝、微信、小程序等）；
- 完整浏览器端交互、复杂筛选分页和商业级性能/可用性；
- 完整配置版本、生效时间和审核工作流。

## 5. Phase H/I 交接

Phase H 可直接使用以下稳定边界：

- 商家会话、门店 membership、RBAC 和 active store 上下文；
- `/api/v1/admin/...` 后台 API 前缀及统一 401/403/CSRF 语义；
- 会话接管控制状态/事件、预约领域命令、回访任务状态机；
- 审计事件 schema 与门店范围指标查询；
- 知识服务按门店实例隔离的当前单进程边界。

Phase I 继续负责 PII、备份清理、生产多进程、可观测性、成本/延迟、真实模型质量和更深安全加固。
