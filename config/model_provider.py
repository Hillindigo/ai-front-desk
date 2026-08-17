"""Model provider factory for chat LLMs and embeddings.

Supports Azure OpenAI and OpenAI-compatible providers such as Qwen,
DeepSeek, Zhipu, and OpenAI by switching environment variables.

``MODEL_PROVIDER=fake`` / ``EMBEDDING_PROVIDER=fake`` return offline fake
models (Phase A: tests must never touch a real LLM API).
"""

from __future__ import annotations

import json
import os
from typing import Any, ClassVar, Dict, Iterator, List

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

# 预约字段提取的预设响应（关键词 → JSON 字典）。所有 key 与 InputParser 的
# prompt 模板保持一致；测试断言依赖其中 project/gender/start_time/unrelated。
FAKE_APPOINTMENT_JSON: Dict[str, Dict[str, Any]] = {
    "天气": {
        "gender": "未知", "start_time": "未知", "duration": "未知",
        "project": "未知", "preference": "未知", "technician_name": "未知",
        "confirmation": "未知", "info_complete": False, "unrelated": True,
        "missing_info": [],
    },
    "肩颈放松": {
        "gender": "女", "start_time": "2026-08-18 14:00", "duration": "60分钟",
        "project": "肩颈放松", "preference": "无", "technician_name": "未知",
        "confirmation": "未知", "info_complete": True, "unrelated": False,
        "missing_info": [],
    },
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


class FakeChatModel(BaseChatModel):
    """测试用假聊天模型：不联网，按输入 prompt 的关键词返回预设响应。

    匹配优先级：
    1. 含"只返回类别英文名"        -> 任务分类（appointment/query/other）
    2. 含"只输出纯JSON"           -> 预约字段提取（JSON 文本）
    3. 含"只回答YES或NO"          -> 咨询相关性分类（YES/NO）
    4. 其他                       -> 咨询回答/文案生成（文本）

    所有调用输入记录在类变量 ``calls`` 中，供测试断言；conftest 负责清理。
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
            user_input = self._extract_user_input(text)
            for keyword, payload in FAKE_APPOINTMENT_JSON.items():
                if keyword in user_input:
                    return json.dumps(payload, ensure_ascii=False)
            return json.dumps(FAKE_APPOINTMENT_JSON["肩颈放松"], ensure_ascii=False)
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
