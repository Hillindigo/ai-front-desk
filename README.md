# AI Front Desk — 线下服务门店智能运营 Agent

AI Front Desk 是一个面向线下服务门店的智能前台与运营中枢。它把“咨询、预约、排班、知识库、客户偏好和回访”串成一条可扩展的 Agent 工作流，让门店可以用自然语言处理高频前台事务。

> **Project status**：这是一个本地优先的 FastAPI 原型，适合用于 Agent 架构学习、门店运营流程验证和二次开发。默认数据为演示数据，不代表任何真实门店的价格、地址或营业承诺。

## 为什么做 AI Front Desk

线下服务门店的前台工作通常不是单一的预约表单，而是连续的运营判断：客户想了解什么服务、哪个时段可用、哪位服务人员更匹配、是否需要追问信息、如何记录偏好，以及什么时候适合再次触达。

AI Front Desk 将这些工作拆成可组合的专业 Agent：

- **Task Classification Agent**：识别咨询、预约、客户分析和其他请求，并完成路由。
- **Consultation Agent**：基于门店知识库回答服务、价格、营业时间和门店信息问题。
- **Appointment Agent**：提取时间、服务项目、时长和人员偏好，检查档期并生成确认结果。
- **User Behavior Agent**：记录交互与预约行为，分析偏好并生成回访建议。

## 核心能力

- 多 Agent 任务分流与共享状态管理
- FAISS + Embedding 的 RAG 知识检索
- 服务项目、人员档期与预约状态管理
- 缺少信息时的自然追问与候选人员推荐
- 客户行为记录、偏好分析和个性化回访
- SQLite 本地数据存储，知识库变更后自动重建索引
- FastAPI API、流式聊天接口和可视化运营页面
- 日志、异常兜底和分层依赖边界

## 系统架构

![AI Front Desk architecture](architecture.svg)

```text
Web / Application
        ↓
API Layer
        ↓
Agents Layer
        ↓
Services Layer
        ↓
Database / Repository Layer
```

允许的调用方向：

```text
Web → API → Agents / Services → Database
```

当前代码为了兼容已有数据结构，内部仍使用 `technician` 作为“可预约服务人员”的技术字段名；产品文案和页面统一使用“服务人员”。后续可以通过数据库迁移将内部命名完全切换为 `staff`。

## 技术栈

- Python 3.10+
- FastAPI / Uvicorn
- LangChain-compatible Chat Model
- FAISS / NumPy
- SQLAlchemy / SQLite
- Jinja2 / 原生 HTML、CSS、JavaScript
- Pytest

## 快速开始

### 1. 创建虚拟环境

```bash
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置模型提供商

复制 `.env.example` 为 `.env`，填写当前模型提供商所需的环境变量。不要把真实密钥提交到 Git。

**没有可用模型 key 时**：应用可以降级启动（页面/API 可访问，聊天返回稳定错误提示）。
仅做功能/界面验证可用 Fake 模式：

```bash
MODEL_PROVIDER=fake EMBEDDING_PROVIDER=fake python app.py
```

### 4. 启动服务

```bash
python app.py
```

默认地址：<http://127.0.0.1:8001>

API 文档：

- Swagger UI：<http://127.0.0.1:8001/docs>
- ReDoc：<http://127.0.0.1:8001/redoc>

## 页面与接口

- `/`：AI Front Desk 对话入口（会话 ID 保存在浏览器 localStorage）
- `/knowledge`：知识库运营
- `/technician`：服务人员状态（兼容旧 API 路径）
- `/technician_schedule`：服务人员排班（兼容旧 API 路径）
- `/user_behavior`：客户行为分析
- `/admin`：运营概览
- `/chat/stream`：流式对话接口（兼容入口；带 `conversation_id` 转发到指定会话，不带则使用默认演示会话）

### 会话 API（Phase B）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/conversations` | 创建会话，返回 `conversation_id` |
| POST | `/api/v1/conversations/{id}/turns` | 发送一轮消息（流式返回） |
| GET | `/api/v1/conversations/{id}` | 获取会话元数据与最近消息 |

- 会话与消息持久化到 SQLite，服务重启后可按 `conversation_id` 恢复。
- URL 中的 `conversation_id` 是会话主标识；服务端校验会话存在与归属（`user_id`）。

### 预约 API（Phase C）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/appointments` | 创建草稿（`mode=draft`）或创建并确认（`mode=confirm`） |
| GET | `/api/v1/appointments/{id}` | 查询预约详情（归属校验） |
| POST | `/api/v1/appointments/{id}/confirm` | 确认待确认预约（支持 `idempotency_key`） |
| POST | `/api/v1/appointments/{id}/cancel` | 取消预约 |
| POST | `/api/v1/appointments/{id}/reschedule` | 原子改约（新时间冲突时原预约不变） |
| GET | `/api/v1/appointments/availability` | 查询服务人员可用性（`technician_id`/`start_time`/`end_time`） |

预约状态机：`draft → pending_confirmation → confirmed`，任意非终态可 `cancelled`，过期草稿 `expired`（TTL 默认 24h）。

- 冲突规则：半开区间 `[start, end)`，相邻预约不冲突；创建/改约在事务内完成冲突校验（`BEGIN IMMEDIATE`）。
- 幂等：`mode=confirm` 与 confirm 接口支持 `idempotency_key`，重复提交返回原预约。
- `mode` 必须显式为 `draft` 或 `confirm`；同一 `conversation_id` 重复提交 `draft` 会原子复用当前活跃草稿。
- 草稿默认 24 小时过期，应用启动后由后台清理循环按周期标记为 `expired`；可通过 `APPOINTMENT_DRAFT_CLEANUP_INTERVAL_SECONDS` 调整周期。
- 领域错误返回稳定 `code`（如 `APPOINTMENT_CONFLICT`、`IDEMPOTENCY_CONFLICT`）+ 可读 `message`。
- 旧 `/api/appointment/create` 已降级为领域服务适配器（不再实例化 Agent 写排班表）。

## 目录结构

```text
ai-front-desk/
├── application/            # 会话运行时（ConversationSession/SessionManager/消息缓冲）
├── agents/                 # 任务路由、咨询、预约和行为分析 Agent
├── api/                    # 对外 API 与响应模型（含会话 API）
├── config/                 # 设置、模型和数据库配置
├── db/                     # SQLAlchemy 模型与 Repository
├── services/               # 知识库、预约、推荐和行为服务
├── web/                    # 页面路由、模板和静态资源
├── tests/                  # Agent、契约、会话隔离与并发测试
├── app.py                  # FastAPI 入口
├── requirements.txt        # Python 依赖
└── .env.example            # 环境变量模板
```

## 数据与隐私边界

- 默认 SQLite 数据位于本地 `data/` 目录。
- `.env`、真实 API Key 和生产数据不应提交到仓库。
- 示例地址、价格、人员和客户数据仅用于演示，请在实际门店部署前替换。
- 生产环境需要补充认证、权限、审计日志、数据脱敏和备份策略。

## 验证

```bash
pytest -q
python -m compileall agents api config db services web app.py
```

- 测试默认运行在 `MODEL_PROVIDER=fake` 模式（零真实 LLM/Embedding 调用，可离线）。
- 测试使用独立临时 SQLite 数据库，不污染本地 `data/` 数据。
- 无真实模型 key 时应用可降级启动，聊天返回稳定错误提示。
