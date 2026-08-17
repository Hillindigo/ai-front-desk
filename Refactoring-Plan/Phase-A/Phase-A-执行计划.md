# Phase A 执行计划（基线与安全）

> 日期：2026-08-17 ｜ 来源：《8.17-重构计划评审与我的改造方案.md》Phase A
> 状态：**✅ 已完成（2026-08-17）**，基线 tag: `phase-a-baseline`

## 0. 目标与完成定义

**目标**：固化当前行为基线，建立无真实 LLM 的测试工具链，修掉明显安全与日志问题，为 Phase B 会话隔离打好地基。

**完成定义（Done = 全部满足）**：
1. `pytest` 全绿，且**零真实 LLM/Embedding API 调用**（通过 `MODEL_PROVIDER=fake` 环境开关实现）
2. 新增 API 契约测试（快乐路径）覆盖核心接口
3. CORS 来源改为配置项，不再硬编码 `*` + credentials
4. 业务代码 `print()` 已清理清单清零（指定文件）
5. `uvicorn` 可启动、`/docs` 可访问
6. `git tag phase-a-baseline` 记录基线

**执行结果（2026-08-17）**：

| 完成定义 | 结果 |
|---|---|
| 1. pytest 全绿零真实 LLM | ✅ 30 passed, 10 skipped, 0 failed（fake 模式） |
| 2. API 契约测试 | ✅ tests/test_api_contract.py 10 项（含 2 个已知缺陷基线） |
| 3. CORS 配置化 | ✅ settings.py + CORS_ORIGINS，验证同源放行/跨源拒绝 |
| 4. print 清零 | ✅ 13 文件 23 处 print → logging（分级 error/info/debug/warning） |
| 5. uvicorn 启动 /docs | ✅ fake 模式启动验证：/、/docs、/chat/stream、/api/knowledge 全 200 |
| 6. git 基线 | ✅ tag `phase-a-baseline`；期间 7 个本地 commit |

## 1. 环境侦查结论（2026-08-17 实测）

| 项 | 现状 |
|---|---|
| Python | 3.11.15（python 命令） |
| 依赖 | **基本未安装**：langchain/faiss-cpu/sqlalchemy/schedule/pytest 均缺失；仅有 fastapi 0.133.1、openai 2.24.0、uvicorn、jinja2、pydantic、numpy 等零散包 |
| tests/ | 4 个测试文件，均直接构造真实 Agent（内部 `create_chat_model` 打真实 API），无 conftest.py |
| 配置 | `config/settings.py` 为空壳；`config/constants.py` 有全局 `busy_periods_dict`（本 Phase 不动，Phase B 处理） |
| git | main 分支，4 个 commit，无 tag |
| 数据 | `data/` 目录不存在（SQLite 首次运行创建） |

## 2. 任务清单

| # | 任务 | 涉及文件 | 验证 |
|---|---|---|---|
| T1 | 安装依赖 | requirements.txt + pytest/pytest-asyncio/httpx | `pytest --version` 可用；`python -c "import langchain, faiss, sqlalchemy"` |
| T2 | git 基线 tag + 锁定 requirements | requirements.txt | `git tag` 存在；diff 干净 |
| T3 | FakeLLM/FakeEmbeddings：`create_chat_model`/`create_embedding_model` 增加 `fake` provider 分支 | `config/model_provider.py` | 单元测试：`MODEL_PROVIDER=fake` 构造出 Fake 模型且不联网 |
| T4 | conftest.py autouse fixture：设 `MODEL_PROVIDER=fake`，提供响应预设覆盖接口 | `tests/conftest.py`（新增） | 现有 4 个测试全部改走 fake |
| T5 | 现有测试适配：4 个测试文件确认无需大改即可在 fake 下运行；必要时按预设表调整断言 | tests/ 4 文件 | `pytest tests/` 全绿 |
| T6 | API 契约测试：`/chat/stream`、`/api/task/classify`、`/api/knowledge`、`/api/technician` 快乐路径 | `tests/test_api_contract.py`（新增） | `pytest tests/test_api_contract.py` 绿 |
| T7 | CORS 配置化 | `config/settings.py`、`app.py` | 启动后响应头 `access-control-allow-origin` 来自配置 |
| T8 | print → logging 清理（指定文件清单） | `agents/appointment/appointment_database.py`、`agents/task_classification_agent.py`、`agents/consultant_agent.py`、`services/text_embedding.py` | `grep -rn "print(" agents/ services/ | grep -v __pycache__` 为零 |
| T9 | 启动验证 + 收尾 | — | `uvicorn` 起服务，`/docs` 200；`git tag phase-a-baseline`；本文件归档 |

## 3. 关键技术决策

### 3.1 FakeLLM 注入方式：环境开关（零侵入），不用 monkeypatch 替换工厂
- **原因**：`agents/*.py` 均 `from config.model_provider import create_chat_model`（import-time 绑定），monkeypatch 工厂函数无效；而所有 Agent 都经 `_initialize_llm()` 调工厂，在工厂内部加 `fake` 分支可一次性覆盖全部调用点。
- **实现**：`config/model_provider.py`：
  - `FakeChatModel(BaseChatModel)`：实现 `_generate` + `_stream`；按最后一条用户消息内容做关键词匹配，返回预设 JSON（预约提取/无关请求/知识问答三类），未命中返回默认 JSON；记录 `calls` 列表供断言。仅测试用，注释注明。
  - `FakeEmbeddings`：`embed_query` 返回固定长度向量（128 维全 0.1），保证 FAISS 索引流程可本地跑通。
  - `get_model_provider() == "fake"` 时返回上述实例。
- **响应预设表**（`tests/conftest.py` 中维护，测试可覆盖）：
  - 含"肩颈放松"且含"预约/我想"→ 预约提取 JSON（project=肩颈放松, gender=女, start_time=今天 14:00, info_complete=True）
  - 含"天气"→ unrelated=True 的预约 JSON
  - 含"好处/多少钱/服务项目"→ 咨询回答文本（"肩颈放松有缓解疲劳的好处…"）
  - 其他 → 默认 JSON（各字段"未知"）

### 3.2 API 契约测试：FastAPI TestClient + httpx
- 用 `app.create_app()` 构造应用，`TestClient` 走完整 ASGI 栈（含 startup 初始化）。
- startup 里 `KnowledgeService.initialize()` 会调 embedding → fake 模式下全本地。
- 覆盖：`/chat/stream`（POST 文本流）、`/api/task/classify`、`/api/knowledge/search`、`/api/technician`、`/`（页面 200）。只验证 HTTP 契约（状态码/响应形状），不断言 LLM 语义。

### 3.3 CORS 配置化
- `config/settings.py` 增加 `cors_origins: list[str]`，从环境变量 `CORS_ORIGINS`（逗号分隔）读取，默认 `["http://127.0.0.1:8001", "http://localhost:8001"]`。
- `app.py` 读取 settings，去掉硬编码 `["*"]`；`allow_credentials=True` 仅在同源列表时保持（当前默认列表均为同源/本地，安全）。
- `.env.example` 增加 `CORS_ORIGINS` 注释示例。

### 3.4 日志清理范围（本 Phase 只清清单内文件）
- 原则：**只替换 print → logging，不改行为逻辑**；其他文件的 print 留到 Phase D 统一协议时处理，避免本 Phase 扩大改动面。
- `agents/appointment/appointment_database.py`：2 处 print（保存失败/行为记录失败）
- `agents/task_classification_agent.py`：2 处 `[DEBUG]` print
- `agents/consultant_agent.py`：1 处启动 print
- `services/text_embedding.py`：无 print（核查确认后跳过）

## 4. 风险与回滚

| 风险 | 应对 |
|---|---|
| 依赖安装失败（faiss-cpu 在 py3.11 的 wheel） | 若 pip 装不上 faiss，改用 conda/uv；仍失败则报告阻塞，不伪造结果 |
| FakeLLM 预设与实际断言不匹配导致现有测试改断言 | 预设表按测试 docstring 的意图设计；改断言处逐一注释说明 |
| `/chat/stream` 走全局 task_agent 依赖真实 LLM，契约测试可能超时 | 契约测试只断言响应可读（200/文本流），不要求语义正确；必要时设短超时 |
| TestClient 触发 startup 初始化慢（知识库 embedding） | fake embedding 本地向量，毫秒级，无风险 |
| 回滚 | 每任务独立 commit；问题任务 revert 单 commit 即可 |

## 5. 执行顺序

T1(依赖) → T2(tag) → T3(FakeLLM) → T4(conftest) → T5(现有测试绿) → T6(契约测试) → T7(CORS) → T8(日志) → T9(启动验证+收尾 tag)

## 6. 执行中发现的已知问题（留待后续 Phase）

1. **默认 MODEL_PROVIDER=azure 且无 API key 时应用无法启动**（startup 初始化抛 Missing credentials）。Phase A 不做行为变更；启动/演示需显式设 `MODEL_PROVIDER`（fake 或真实 provider + .env）。建议 Phase B/D 将初始化失败降级为"应用可启动、知识库不可用"。
2. **user_behavior 测试与实现 API 脱节**（record_behavior 签名、get_recent_behavior 方法名、返回结构均不匹配），已整体 skip 并记录审计说明；`BehaviorRecorder.get_user_behaviors` 调用 repository 缺 user_id 参数（真实接口缺陷）。Phase B/D 行为组件收口时按真实 API 重写测试。
3. **API 缺陷基线（契约测试固化）**：
   - `POST /api/task/classify`：请求模型只有 `text` 字段，handler 访问 `request.message`（api/task.py:22）→ 恒 400。
   - `POST /api/appointment/create`：调用不存在的 `AppointmentAgent.process_appointment_request` → 恒 400。
   - `POST /api/consultation/ask`：调用不存在的 `ConsultantAgent.process_consultation` → 恒 400。
   - 服务人员 prefix 是复数 `/api/technicians`（与文档/前端单数用法不一致）。
   以上 Phase D 统一 API 时修复。
4. **测试与本地库共享**：tests 直接写 `data/ai_front_desk.db`（含 user_behavior 测试数据残留），测试间可能互相影响。Phase B 引入测试隔离（临时 DB / fixture 清理）。
5. **`data/` 目录被 .gitignore 忽略**（`data/` 规则），仓库中无 .gitkeep；首次 clone 后运行需手动 `mkdir data`（或启动脚本处理）。
