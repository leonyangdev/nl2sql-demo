"""LLM 客户端 —— DeepSeek（OpenAI 兼容的 /chat/completions 接口）。

设计要点：

1. 只用标准库 `urllib.request`，不给项目增加任何依赖。
2. 配置在 `config` 导入时就从 .env / 环境变量装配完毕，所以"启动后
   自动可用"，不需要任何显式初始化代码。
3. 支持多个 OpenAI 兼容的提供方（deepseek / dashscope），按可用性自动选择。

相关环境变量（都有默认值，只有 API Key 是必须的）：

    NL2SQL_LLM_PROVIDER      deepseek | dashscope（不填则自动探测）
    DEEPSEEK_API_KEY         必填（DeepSeek）
    DEEPSEEK_BASE_URL        默认 https://api.deepseek.com/v1
    DEEPSEEK_MODEL           默认 deepseek-chat
    NL2SQL_LLM_TEMPERATURE   默认 0（生成 SQL 要确定性）
    NL2SQL_LLM_TIMEOUT       默认 60 秒
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from http_client import HttpError, post_json


class LLMError(RuntimeError):
    """调用大模型失败。"""


# ---------------------------------------------------------------------------
# 提供方预设：都是 OpenAI 兼容协议，只是 base_url / model / key 变量名不同
# ---------------------------------------------------------------------------
_PROVIDER_PRESETS: dict[str, dict] = {
    "deepseek": {
        "api_key_vars": ("DEEPSEEK_API_KEY",),
        "base_url_vars": ("DEEPSEEK_BASE_URL", "DEEPSEEK_API_BASE"),
        "model_vars": ("DEEPSEEK_MODEL",),
        "default_base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    "dashscope": {
        "api_key_vars": ("DASHSCOPE_API_KEY",),
        "base_url_vars": ("DASHSCOPE_BASE_URL", "DASHSCOPE_API_BASE"),
        "model_vars": ("DASHSCOPE_MODEL",),
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
    },
}


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    api_key: str
    base_url: str
    model: str
    temperature: float = 0.0
    timeout: int = 60

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def describe(self) -> str:
        # 绝不打印 api_key 的内容
        return (
            f"provider={self.provider} model={self.model} "
            f"endpoint={self.endpoint} api_key=已配置(len={len(self.api_key)})"
        )


def resolve_settings() -> LLMSettings | None:
    """按 NL2SQL_LLM_PROVIDER 或自动探测，解析出一个可用的配置。

    自动探测顺序就是 _PROVIDER_PRESETS 的声明顺序（deepseek 优先）。
    """
    requested = (os.getenv("NL2SQL_LLM_PROVIDER") or "").strip().lower()
    if requested:
        if requested not in _PROVIDER_PRESETS:
            raise LLMError(
                f"未知的 NL2SQL_LLM_PROVIDER={requested}，"
                f"可选：{', '.join(_PROVIDER_PRESETS)}"
            )
        candidates = [requested]
    else:
        candidates = list(_PROVIDER_PRESETS)

    for name in candidates:
        preset = _PROVIDER_PRESETS[name]
        api_key = _first_env(preset["api_key_vars"])
        if not api_key:
            continue

        return LLMSettings(
            provider=name,
            api_key=api_key,
            base_url=_first_env(preset["base_url_vars"]) or preset["default_base_url"],
            model=_first_env(preset["model_vars"]) or preset["default_model"],
            temperature=float(os.getenv("NL2SQL_LLM_TEMPERATURE") or 0),
            timeout=int(os.getenv("NL2SQL_LLM_TIMEOUT") or 60),
        )

    return None


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
class LLMClient:
    def __init__(self, settings: LLMSettings):
        self.settings = settings

    @property
    def name(self) -> str:
        return f"{self.settings.provider}:{self.settings.model}"

    def chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int = 2048,
    ) -> str:
        """一次对话补全，返回助手回复的纯文本。"""
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": (
                self.settings.temperature if temperature is None else temperature
            ),
            "max_tokens": max_tokens,
            "stream": False,
        }

        try:
            data = post_json(
                url=self.settings.endpoint,
                payload=payload,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                timeout=self.settings.timeout,
            )
        except HttpError as exc:
            raise LLMError(str(exc)) from exc

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"响应格式异常：{str(data)[:200]}") from exc


_cached_client: LLMClient | None = None
_cached_resolved = False


def get_llm() -> LLMClient | None:
    """返回可用的 LLM 客户端；没有配置 API Key 时返回 None（触发本地规则降级）。"""
    global _cached_client, _cached_resolved
    if _cached_resolved:
        return _cached_client

    _cached_resolved = True
    settings = resolve_settings()
    if settings is None:
        print(
            "[llm] 未检测到 API Key，SQL 生成将退化为本地规则模式。\n"
            "      在项目根目录 .env 里配置 DEEPSEEK_API_KEY=sk-xxx 即可启用大模型。"
        )
        return None

    _cached_client = LLMClient(settings)
    print(f"[llm] {settings.describe()}")
    return _cached_client
