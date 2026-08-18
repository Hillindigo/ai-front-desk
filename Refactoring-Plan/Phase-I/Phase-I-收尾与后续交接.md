# Phase I 收尾与后续交接

> 日期：2026-08-18
> 分支：`dev`（全部阶段提交在 dev，未合并、未推送）
> 状态标记：阶段代码与测试已提交；完整浏览器人工验收与真实模型评测**仍为待办**（不冒充完成）。

## 1. 阶段已交付（I1–I7）

| 任务 | 交付 | 证据 |
|---|---|---|
| I1 安全 | 生产配置门禁（拒启）、安全响应头+受信Host+限流、Cookie/会话加固、错误脱敏、越权回归 | `tests/test_phase_i_security.py` |
| I2 隐私 | 商家侧客户导出（短时令牌）、删除/匿名化+删除登记(D10)、保留/清理脚本、PII 数据字典 | `tests/test_phase_i_privacy.py`、`PII-数据字典.md`、`scripts/cleanup.py` |
| I3 观测 | /health live+ready、RunRecorder 埋点、门店指标、阈值文档 | `tests/test_phase_i_observability.py`、`指标与阈值.md` |
| I4 知识一致性 | 单实例门禁与版本 health、stale 标记、部署限制 | `tests/test_phase_i_knowledge_consistency.py`、`部署与单实例限制.md` |
| I5 评测 | 四类评测运行器（意图/字段/RAG/P0）、Fake/真实/人工分栏 | `tests/test_phase_i_evaluation.py`、`evaluation/phase_i_eval.py` |
| I6 可靠性 | 一致性备份+校验+恢复、重启恢复、幂等确认演练 | `tests/test_phase_i_reliability.py`、`scripts/backup.py` |
| I7 部署收尾 | 干净环境验收测试、运维手册、生产示例 env | `tests/test_phase_i_acceptance.py`、`Phase-I-运维手册.md`、`.env.production.example` |

**阶段间全量回归**：每阶段全量 `python -m pytest -q` 保持全绿（从 427 基线增长至 470+）。

## 2. 阶段 Done 核对（关键项）

- [x] 生产配置缺强密钥/白名单 CORS/必要模型配置即拒启（I1-E1）
- [x] Cookie/CSRF/CORS/安全头/错误脱敏/会话撤销有自动化测试
- [x] 买家归属、商家 RBAC、门店隔离越权回归通过
- [x] 日志/指标/页面不暴露密码/令牌/完整 PII（redact + 校验不回显）
- [x] 客户数据导出/删除/匿名化由商家侧发起，权限/dry-run/幂等/审计/删除登记（D9/D10）
- [x] 健康/就绪反映 DB、迁移、知识版本真实状态（live/ready 分离, D12）
- [x] 单实例知识版本 health 可暴露与比对（I4-A）；不宣称多进程
- [x] 并发/重复/幂等/重启/备份恢复演练通过（I6）
- [x] 四类评测运行器离线可重复，P0 零容忍 + Fake/人工分栏（I5）
- [x] 干净环境可按文档跑通核心闭环（I7 验收测试）

## 3. 未完成 / 待办（不冒充完成）

- **完整浏览器端人工验收**（桌面/移动：登录/退出、会话恢复、预约、接管、权限拒绝、会话过期、错误恢复）：尚未自动化执行，需人工或 Playwright 环境补齐。
- **真实模型语义评测 + 人工评测**：`evaluation/phase_i_eval.py` 默认 Fake；真实模型与人工结果未运行，均标"待验证"。Fake 通过不冒充语义质量。
- **请求级服务端 request_id 独立贯穿**：当前以 client_request_id 关联 run，服务端独立 request_id 为后续待办（见 `指标与阈值.md` 说明）。
- **备份静态加密**：备份脚本支持校验/清单，生产级静态加密 + 密钥分离仍未落地（D11 列为下一步）。
- **多进程知识一致性（I4-B）**：明确未纳入当前阶段。
- 第三方渠道、复杂度身、商业级 HA：持续排除。

## 4. 后续交接项

- 下一阶段（如 Phase J / 正式上线加固）应基于：已固化的安全门禁、隐私/删除登记、观测与指标、评测分栏、备份/恢复脚本。
- 接入第二个真实渠道前，须先获得官方接口 + 测试环境 + 业务价值证明，再从已出现差异提取适配层。
- 浏览器人工验收 + 真实模型评测完成后，才可对外宣称"线上质量达标"。

## 5. Git 状态

- 全部 Phase I 提交在 `dev`，未合并到 `main`、未推送（用户要求确认后才合并/推送）。
- 提交格式符 `Phase I(类型)-标题≤15字`。
