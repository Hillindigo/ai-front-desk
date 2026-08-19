# Phase I I2：PII 数据字典（D14）

> 日期：2026-08-18
> 状态：随实现核对，作为删除/匿名化与日志脱敏的落地依据
> 原则：D4 —— 期望消失的临时/可重建数据用硬删；法定/业务审计事实去标识保留并说明原因。

## 1. 逐表 / 逐字段矩阵

| 表 / 字段 | PII 类别 | 主体键 | 门店键 | 展示范围 | 日志/指标规则 | 匿名化动作 | 保留理由 | 恢复后动作 |
|---|---|---|---|---|---|---|---|---|
| `messages.content` | 自由文本（含姓名/电话等） | conversation→user | conversation.store_id | 买家/商家会话页 | 不写日志；指标不绑定 | 置 `[已删除]` | 会话结构保留 | 重放登记 |
| `Message.metadata_json` | 结构化元数据 | conversation | 同上 | 内部 | 不写日志 | 置 `[已删除]` | — | 重放登记 |
| `conversations.user_id` | 准标识符 | user | store | 归属/查询 | 不直接入指标标签 | 保留（归属键） | 会话归属 | 保留 |
| `conversation_summaries.content/key_facts` | 可能含 PII 摘要 | conversation | 同上 | 内部 | 不写日志 | status=invalidated + 内容置 `[已删除]` | 摘要失效但不物理删 | 重放登记 |
| `preferences.preference_value` | 偏好（PII 可能） | user | store | 商家客户页 | 不写日志 | is_active=0 + deleted_at | 审计留存（行保留） | 重放登记 |
| `preference_tombstones.normalized_value` | 偏好（脱敏） | user | store | 内部 | 不写日志 | 保留（原本身份已弱化） | 防复活 | 保留 |
| `appointments.*` | 业务事实（项目/时间） | user | store | 商家预约页 | 聚合指标脱敏 | cancel_reason 置 `[已删除]`；记录保留 | 业务/审计事实（D4） | 保留+重放 |
| `AppointmentEvent.payload_json` | 事件负载（可能含输入） | appointment | store | 内部 | 不写日志 | 保留（幂等/审计），不入模型输入 | 审计 | 保留 |
| `follow_up_tasks.customer_user_id / reason` | 准标识符+文本 | user | store | 商家回访页 | 不写日志 | reason 置 `[已删除]` | 任务记录保留 | 重放登记 |
| `user_behaviors.action_data` | 行为数据（可能含文本） | user | store | 内部分析 | 不写日志 | action_data 置 `[已删除]` | 数量指标可留 | 重放登记 |
| `user_recommendations.content` | 生成文本 | user | — | 内部 | 不写日志 | content 置 `[已删除]` | — | 重放登记 |
| `legacy user_preferences` | 旧偏好（PII 可能） | user | — | 兼容仅 | 不写日志 | DELETE | 旧表临时 | 重放登记 |
| `audit_events.summary_json` | 审计摘要 | actor | store | 审计页 | 只存脱敏摘要 | 匿名化时摘要重建为计数 | 审计完整性 | 保留 |
| `conversation_control_events.content` | 接管备注（可能含 PII） | conversation | store | 工作台 | 不写日志 | 保留（不直接含客户主体） | 审计 | 保留 |
| `store_profiles.address/phone` | 门店联系方式（非客户 PII） | store | store | 配置页 | — | 不适用（机构数据） | — | — |
| 知识文档 `content` | 门店知识 | — | store | 知识管理 | — | 不适用（非客户 PII） | — | — |
| 评测/运行失败样本 | 可能合成/脱敏 | — | — | 仅离线 | 禁入生产 | 仅合成样本，不混入生产 | 质量评测 | — |

## 2. 删除/匿名化语义（决策 D4/D9/D10）

- **仅商家侧发起**：导出、删除、匿名化入口受 `manage_customer_data`（owner/manager）+ store scope + CSRF 保护；买家端不提供自助数据权利。
- **幂等**：每次删除请求带 `request_id`，写入 `privacy_deletion_registry`；同 id 重放返回幂等结果，不重复执行。
- **删除登记（D10）**：注册表只存 request_id、门店、客户 user_id、实体计数，**不含原始 PII**；供「从旧备份恢复后重放删除」防 PII 复活。
- **业务/审计事实**：已确认预约按 D4 去标识自由文本并保留记录，理由可追踪。
- **dry-run 默认**：`scripts/cleanup.py` 与匿名化 API 均默认 dry-run，真实执行需明确授权。

## 3. 保留期限（E9 初稿，I6 备份清理延续）

| 数据 | 建议保留 | 清理动作 |
|---|---|---|
| 在线业务数据 | 长期（业务事实） | 匿名化而非删除 |
| 会话消息 | 按门店配置 | 匿名化后不还原 |
| 日志 | 30 天 | 过期轮转 |
| 临时导出文件 | 5 分钟（一次性令牌） | 过期即弃/清理 |
| 上传文件 | 按门店配置 | 过期清理 dry-run |
| 备份 | 见 I6 RPO/RTO | 到期清除 + 脱敏审计 |
