# Phase I I7：部署 / 运维手册

> 日期：2026-08-18
> 范围：单实例 FastAPI + SQLite + 进程内 FAISS（I0 D1/D2；**不承诺多进程**）。

## 1. 启动（生产）

```text
1) 复制 .env.production.example 为 .env 并填充（ADMIN_SESSION_SECRET、CORS、模型）。
2) 校验配置（生产缺密钥/占位符会拒启）：
   python -m [应用配置校验]     # 或直接尝试启动，配置无效即拒绝
3) 迁移建表（SessionManager 自动 create_all + 幂等迁移）：
   （启动时自动执行；数据库在 data/ 下）
4) 创建首个管理员：
   python -c "from services.admin_auth import AdminAuthService as S; s=S(); print(s.provision_account('owner','<强密码>','店主','门店',role='owner'))"
5) 启动（强制单 worker）：
   uvicorn app:app --host 127.0.0.1 --port 8001 --workers 1
```

生产模式配置无效（缺会话密钥/白名单 CORS/fake 模型/占位符密钥）时，`create_app()` 抛 `ConfigError` 拒绝启动（I1-E1/D12）。

## 2. 健康 / 就绪

```text
GET /health/live    # 进程存活
GET /health/ready   # DB/迁移/知识版本真实状态；就绪失败返回 503（不伪健康）
```

## 3. 观测 / 指标

```text
GET /api/v1/admin/metrics   # 当前门店审计指标 + run_metrics（进程内 Agent 运行摘要）
```
阈值与告警规则见 `指标与阈值.md`（无外部告警平台，不伪造通知）。

## 4. 备份 / 恢复

```text
python -m scripts.backup backup <out_dir>            # 一致性备份 + manifest(sha256)
python -m scripts.backup verify <backup.db>           # PRAGMA integrity_check + 表抽查
python -m scripts.backup restore <backup.db> <target> # 默认 dry-run
python -m scripts.backup restore <backup.db> <target> --execute  # 真实恢复
```
恢复后先迁移 + 跑验收查询；涉及已删除客户数据时按 `privacy_deletion_registry` 重放删除（D10），防止旧备份 PII 复活。RPO/RTO 按 I6 实测记录，超过 RPO 无成功备份需告警。

## 5. 数据清理

```text
python -m scripts.cleanup --dry-run    # 统计（默认）
python -m scripts.cleanup --execute    # 显式授权后真实清理（带审计）
```

## 6. 密钥轮换与会话失效

- 修改 `ADMIN_SESSION_SECRET`：仅影响后续签名/会话，建议同时清空 `admin_sessions` 表强制全员重新登录。
- 单个账号登出走 `/api/v1/admin/auth/logout`（撤 `revoked_at`）。
- 生产 Cookie 强制 `Secure`（I1-E3），HTTPS 边界需在反向代理正确透传 `X-Forwarded-Proto`（仅信任明确代理来源）。

## 7. 常见故障定位

| 症状 | 定位 | 处置 |
|---|---|---|
| 生产拒绝启动 | ConfigError 列出缺失项 | 补 `ADMIN_SESSION_SECRET`/CORS/模型配置 |
| /health/ready=503 | checks.reason: database/migration | 检查 DB 文件/迁移 |
| knowledge_stale=true | 本地索引版本与 DB 版本不一致 | 触发重建/刷新；高风险知识拒答 |
| turn 连续失败 | /metrics run_metrics.error_categories | 查看失败类别；连续失败转人工 |
| backup 校验失败 | sha256/完整性 | 重建备份并验证 |
| 已删除 PII 又出现 | 未重放删除登记 | 恢复后按 registry 重放删除 |

## 8. 验收矩阵引用

- 安全：`tests/test_phase_i_security.py`
- 隐私：`tests/test_phase_i_privacy.py`
- 观测：`tests/test_phase_i_observability.py`
- 知识一致性：`tests/test_phase_i_knowledge_consistency.py`
- 评测：`tests/test_phase_i_evaluation.py`、`evaluation/phase_i_eval.py`
- 可靠性：`tests/test_phase_i_reliability.py`
- 验收：`tests/test_phase_i_acceptance.py`

完整浏览器/移动人工验收（登录、会话恢复、预约、接管、权限拒绝、会话过期、错误恢复）仍未自动化执行，列为待办并在收尾文档注明。
