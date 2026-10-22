"""LLM 供应商目录（UI 与账户页共用的唯一来源）。

之前供应商列表写死在 ``web/components/sidebar.py``，账户页也要展示同一份清单；
复制一份迟早会漂移（新增供应商只改一处）。这里集中定义显示名、内部 key、
对应的环境变量名，以及「是否必须有 Key」。
"""

from __future__ import annotations

# 顺序即侧栏下拉顺序：国内可直连的放前面。
PROVIDERS: list[tuple[str, str]] = [
    ("MiniMax（推荐·国内直连）", "minimax"),
    ("DeepSeek", "deepseek"),
    ("通义千问 Qwen", "qwen"),
    ("智谱 GLM", "glm"),
    ("OpenAI", "openai"),
    ("Anthropic", "anthropic"),
    ("Google Gemini", "google"),
    ("xAI Grok", "xai"),
    ("OpenRouter（聚合·填 vendor/model 形式 ID）", "openrouter"),
    ("OpenAI 兼容（自定义 base_url·9Router/AI Router/自建代理）", "openai_compatible"),
    ("Ollama（本地）", "ollama"),
]

PROVIDER_DISPLAY = [name for name, _ in PROVIDERS]
PROVIDER_KEYS = [key for _, key in PROVIDERS]

# 账户页展示「该供应商的 Key 存在哪个环境变量」时用。多用户模式下这些环境变量
# **只对 owner 生效**；普通成员的 Key 从自己的 secrets.json 读取。
ENV_VAR_NAMES: dict[str, str] = {
    "minimax": "MINIMAX_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "glm": "ZHIPU_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
}

# ollama 本地服务不需要 Key；其它供应商都需要。
KEYLESS_PROVIDERS = frozenset({"ollama", "claude_agent_sdk"})


def provider_label(provider: str) -> str:
    try:
        return PROVIDER_DISPLAY[PROVIDER_KEYS.index(provider)]
    except ValueError:
        return provider


def provider_needs_key(provider: str) -> bool:
    return provider not in KEYLESS_PROVIDERS


def env_var_candidates(provider: str) -> tuple[str, ...]:
    """客户端实际会接受的 env 变量名（用于 owner 的 .env 兜底检查）。"""
    primary = ENV_VAR_NAMES.get(provider)
    extra: tuple[str, ...] = ()
    if provider == "openai_compatible":
        extra = ("OPENAI_API_KEY",)
    elif provider == "google":
        extra = ("GEMINI_API_KEY",)
    return tuple(name for name in ((primary,) + extra) if name)


def env_var_for(provider: str) -> str:
    return ENV_VAR_NAMES.get(provider, "API_KEY")
