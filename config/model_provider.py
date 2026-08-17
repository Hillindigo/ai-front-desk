"""Model provider factory for chat LLMs and embeddings.

Supports Azure OpenAI and OpenAI-compatible providers such as Qwen,
DeepSeek, Zhipu, and OpenAI by switching environment variables.

``MODEL_PROVIDER=fake`` / ``EMBEDDING_PROVIDER=fake`` return offline fake
models (Phase A: tests must never touch a real LLM API).
"""

from __future__ import annotations

import json
import os
from typing import Any, ClassVar, Dict, Iterator, List, Tuple

from dotenv import load_dotenv
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_openai import (
    AzureChatOpenAI,
    AzureOpenAIEmbeddings,
    ChatOpenAI,
    OpenAIEmbeddings,
)
from pydantic import SecretStr

load_dotenv()


CHAT_PROVIDERS = {"openai", "qwen", "deepseek", "zhipu", "openai-compatible"}
EMBEDDING_PROVIDERS = {"openai", "qwen", "zhipu", "openai-compatible"}


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def get_model_provider() -> str:
    """Return configured provider, defaulting to Azure for backward compatibility."""
    return (_env("MODEL_PROVIDER", "azure") or "azure").strip().lower()


def create_chat_model(temperature: float = 0):
    """Create a chat model from environment configuration.

    Azure-compatible env vars:
        MODEL_PROVIDER=azure
        AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT,
        AZURE_OPENAI_VERSION

    OpenAI-compatible env vars:
        MODEL_PROVIDER=qwen|deepseek|zhipu|openai|openai-compatible
        LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

    Test-only env var:
        MODEL_PROVIDER=fake  ->  FakeChatModel (no network)
    """
    provider = get_model_provider()

    if provider == "fake":
        return FakeChatModel(temperature=temperature)

    if provider == "azure":
        return AzureChatOpenAI(
            azure_deployment=_env("AZURE_OPENAI_DEPLOYMENT"),
            api_version=_env("AZURE_OPENAI_VERSION"),
            temperature=temperature,
            azure_endpoint=_env("AZURE_OPENAI_ENDPOINT"),
            api_key=SecretStr(_env("AZURE_OPENAI_API_KEY", "") or ""),
        )

    if provider in CHAT_PROVIDERS:
        return ChatOpenAI(
            model=_env("LLM_MODEL", "qwen-plus") or "qwen-plus",
            api_key=SecretStr(_env("LLM_API_KEY", "") or ""),
            base_url=_env("LLM_BASE_URL"),
            temperature=temperature,
        )

    raise ValueError(
        f"Unsupported MODEL_PROVIDER={provider!r}. "
        "Use azure, qwen, deepseek, zhipu, openai, openai-compatible, or fake."
    )


def create_embedding_model():
    """Create an embedding model from environment configuration."""
    provider = (_env("EMBEDDING_PROVIDER") or get_model_provider()).strip().lower()

    if provider == "fake":
        return FakeEmbeddings()

    if provider == "azure":
        return AzureOpenAIEmbeddings(
            azure_deployment=_env("AZURE_OPENAI_DEPLOYMENT_EMBEDDING"),
            api_key=SecretStr(_env("AZURE_OPENAI_API_KEY", "") or ""),
            api_version=_env("AZURE_OPENAI_EMBEDDING_VERSION", "2023-05-15"),
            azure_endpoint=_env("AZURE_OPENAI_ENDPOINT_EMBEDDING"),
        )

    if provider in EMBEDDING_PROVIDERS:
        return OpenAIEmbeddings(
            model=_env("EMBEDDING_MODEL", "text-embedding-v3") or "text-embedding-v3",
            api_key=SecretStr(_env("EMBEDDING_API_KEY") or _env("LLM_API_KEY", "") or ""),
            base_url=_env("EMBEDDING_BASE_URL") or _env("LLM_BASE_URL"),
            # OpenAI-compatible providers like DashScope (Qwen) only accept raw
            # strings; disable token-id batching to send plain text.
            check_embedding_ctx_length=False,
        )

    raise ValueError(
        f"Unsupported EMBEDDING_PROVIDER={provider!r}. "
        "Use azure, qwen, zhipu, openai, openai-compatible, or fake."
    )


# ============================================================================
# Fake models (TEST-ONLY). Never used outside of MODEL_PROVIDER=fake.
# ============================================================================

# 预约字段提取预设（Phase B 决策三：有序列表，先长特征串后短关键词）。
# 所有 key 与 InputParser 的 prompt 模板保持一致。
# 场景覆盖：预约项目 A/B、缺少字段、确认、取消、无关、未知输入。
FAKE_APPOINTMENT_PRESETS: List[Tuple[str, Dict[str, Any]]] = [
    # 1. 预约项目 A（完整信息）—— 测试断言依赖 project/gender/start_time/unrelated
    (
        "女服务人员",
        {
            "gender": "女", "start_time": "2026-08-18 14:00", "duration": "60分钟",
            "project": "肩颈放松", "preference": "无", "technician_name": "未知",
            "confirmation": "未知", "info_complete": True, "unrelated": False,
            "missing_info": [],
        },
    ),
    # 2. 缺少字段：只有项目，缺时间/性别/时长 -> 多轮追问与草稿隔离场景
    (
        "肩颈放松",
        {
            "gender": "未知", "start_time": "未知", "duration": "未知",
            "project": "肩颈放松", "preference": "无", "technician_name": "未知",
            "confirmation": "未知", "info_complete": False, "unrelated": False,
            "missing_info": ["start_time", "duration", "gender"],
        },
    ),
    # 3. 预约项目 B（足疗，完整信息）—— 会话 B 的草稿隔离
    (
        "足疗",
        {
            "gender": "男", "start_time": "2026-08-18 15:00", "duration": "45分钟",
            "project": "足疗", "preference": "无", "technician_name": "未知",
            "confirmation": "未知", "info_complete": True, "unrelated": False,
            "missing_info": [],
        },
    ),
    # 4. 确认（恢复后继续预约）
    (
        "确认",
        {
            "gender": "未知", "start_time": "未知", "duration": "未知",
            "project": "未知", "preference": "无", "technician_name": "未知",
            "confirmation": "是", "info_complete": False, "unrelated": False,
            "missing_info": [],
        },
    ),
    # 5. 取消/否定（会话内状态不串线）
    (
        "取消",
        {
            "gender": "未知", "start_time": "未知", "duration": "未知",
            "project": "未知", "preference": "无", "technician_name": "未知",
            "confirmation": "否", "info_complete": False, "unrelated": False,
            "missing_info": [],
        },
    ),
    # 6. 无关请求（天气等）
    (
        "天气",
        {
            "gender": "未知", "start_time": "未知", "duration": "未知",
            "project": "未知", "preference": "未知", "technician_name": "未知",
            "confirmation": "未知", "info_complete": False, "unrelated": True,
            "missing_info": [],
        },
    ),
]

# 未命中时的默认响应（空输入/未知输入的错误边界）
FAKE_APPOINTMENT_DEFAULT: Dict[str, Any] = {
    "gender": "未知", "start_time": "未知", "duration": "未知",
    "project": "未知", "preference": "未知", "technician_name": "未知",
    "confirmation": "未知", "info_complete": False, "unrelated": False,
    "missing_info": ["所有信息"],
}

# 任务分类的预设响应（关键词 → 类别英文名）
FAKE_CATEGORY: Dict[str, str] = {
    "预约": "appointment",
    "安排": "appointment",
    "好处": "query",
    "多少钱": "query",
    "服务项目": "query",
    "是什么": "query",
    "价格": "query",
    "地址": "query",
    "营业": "query",
}


def _match_appointment_json(user_input: str) -> str:
    """按有序预设表匹配预约提取 JSON（长特征串优先）。"""
    for feature, payload in FAKE_APPOINTMENT_PRESETS:
        if feature in user_input:
            return json.dumps(payload, ensure_ascii=False)
    return json.dumps(FAKE_APPOINTMENT_DEFAULT, ensure_ascii=False)


class FakeChatModel(BaseChatModel):
    """测试用假聊天模型：不联网，按输入 prompt 的关键词返回预设响应。

    匹配优先级：
    1. 含"只返回类别英文名"        -> 任务分类（appointment/query/other）
    2. 含"只输出纯JSON"           -> 预约字段提取（JSON 文本，有序预设表）
    3. 含"只回答YES或NO"          -> 咨询相关性分类（YES/NO）
    4. 其他                       -> 咨询回答/文案生成（文本）

    原则（决策三）：预设按输入内容匹配，不依赖调用次数或跨测试状态；
    所有调用输入记录在类变量 ``calls`` 供审计，conftest 负责清理。
    """

    calls: ClassVar[List[str]] = []

    @property
    def _llm_type(self) -> str:
        return "fake-chat"

    # -- 匹配逻辑 ---------------------------------------------------------

    def _extract_user_input(self, text: str) -> str:
        """从 prompt 中提取用户输入（模板各异的多种标记）。"""
        for marker in ("任务内容：", "用户输入：", "用户问题："):
            if marker in text:
                rest = text.split(marker, 1)[1]
                # 预约提取 prompt 在用户输入后还有后续指令，取第一行
                first_line = rest.splitlines()[0].strip()
                return first_line if first_line else rest.strip()
        return text

    def _match(self, text: str) -> str:
        # 1. 任务分类（TaskClassifier）
        if "只返回类别英文名" in text:
            task = self._extract_user_input(text)
            for keyword, category in FAKE_CATEGORY.items():
                if keyword in task:
                    return category
            return "other"
        # 2. 预约字段提取（InputParser）
        if "只输出纯JSON" in text:
            return _match_appointment_json(self._extract_user_input(text))
        # 3. 咨询相关性分类（ConsultationClassifier）
        if "只回答YES或NO" in text:
            user_input = self._extract_user_input(text)
            if any(k in user_input for k in ("好处", "多少钱", "价格", "营业", "地址", "效果", "什么", "吗", "是")):
                return "YES"
            return "NO"
        # 4. 文本生成（咨询回答 / 推荐文案 / 回访消息）
        user_input = self._extract_user_input(text)
        if any(k in user_input for k in ("预约", "安排", "确认")):
            return "好的，已为您确认预约，服务人员会按时为您服务。"
        if "足疗" in user_input:
            return (
                "足疗服务以舒适体验和日常放松为主，适合缓解足部疲劳、改善睡眠。"
                "欢迎您到店体验足疗服务。"
            )
        if "价格" in user_input or "多少钱" in user_input:
            return (
                "我们提供多种服务项目：基础护理120元/60分钟、肩颈放松80元/30分钟、"
                "足部护理100元/45分钟。实际价格以门店配置为准。"
            )
        return (
            "肩颈放松服务对缓解肌肉疲劳有很好的效果，能够促进血液循环、"
            "缓解肩颈紧张，适合长期伏案的人群。欢迎您预约体验。"
        )

    # -- langchain 接口 ----------------------------------------------------

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: List[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        text = self._latest_human_text(messages)
        self.calls.append(text)
        content = self._match(text)
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=content))]
        )

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: List[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        text = self._latest_human_text(messages)
        self.calls.append(text)
        content = self._match(text)
        yield ChatGenerationChunk(message=AIMessageChunk(content=content))

    @staticmethod
    def _latest_human_text(messages: List[BaseMessage]) -> str:
        for message in reversed(messages):
            if getattr(message, "type", "") in ("human", "user"):
                return str(message.content or "")
        return ""


class FakeEmbeddings(Embeddings):
    """测试用假嵌入模型：返回固定向量，不联网（FAISS 流程可本地跑通）。"""

    _VECTOR: List[float] = [0.1] * 128

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [list(self._VECTOR) for _ in texts]

    def embed_query(self, text: str) -> List[float]:
        return list(self._VECTOR)