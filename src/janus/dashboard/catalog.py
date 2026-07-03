from __future__ import annotations

import copy
from typing import Any

CATALOG: dict[str, dict[str, Any]] = {
    "openai": {
        "name": "OpenAI",
        "icon": "🟢",
        "logo": "openai.svg",
        "api_type": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "prefix": "openai",
        "default_models": ["gpt-4o", "gpt-4o-mini", "o3", "o4-mini"],
    },
    "anthropic": {
        "name": "Anthropic",
        "icon": "🟠",
        "logo": "anthropic.svg",
        "api_type": "anthropic",
        "base_url": "https://api.anthropic.com",
        "prefix": "anthropic",
        "default_models": ["claude-sonnet-4-20250514", "claude-opus-4-20250514"],
    },
    "gemini": {
        "name": "Google Gemini",
        "icon": "🔵",
        "logo": "gemini.svg",
        "api_type": "gemini",
        "base_url": "https://generativelanguage.googleapis.com",
        "prefix": "gemini",
        "default_models": ["gemini-2.5-pro", "gemini-2.0-flash"],
    },
    "groq": {
        "name": "Groq",
        "icon": "⚡",
        "logo": "groq.svg",
        "api_type": "openai_compat",
        "base_url": "https://api.groq.com/openai/v1",
        "prefix": "groq",
        "default_models": ["llama-3.3-70b-instruct"],
    },
    "together": {
        "name": "Together AI",
        "icon": "🤝",
        "logo": "together.svg",
        "api_type": "openai_compat",
        "base_url": "https://api.together.xyz/v1",
        "prefix": "together",
        "default_models": [],
    },
    "deepseek": {
        "name": "DeepSeek",
        "icon": "🔬",
        "logo": "deepseek.svg",
        "api_type": "openai_compat",
        "base_url": "https://api.deepseek.com/v1",
        "prefix": "deepseek",
        "default_models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "openrouter": {
        "name": "OpenRouter",
        "icon": "🔀",
        "logo": "openrouter.svg",
        "api_type": "openai_compat",
        "base_url": "https://openrouter.ai/api/v1",
        "prefix": "openrouter",
        "default_models": [],
    },
    "mistral": {
        "name": "Mistral",
        "icon": "🌬️",
        "logo": "mistral.svg",
        "api_type": "openai_compat",
        "base_url": "https://api.mistral.ai/v1",
        "prefix": "mistral",
        "default_models": ["mistral-large-2411"],
    },
    "fireworks": {
        "name": "Fireworks",
        "icon": "🎆",
        "logo": "fireworks.svg",
        "api_type": "openai_compat",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "prefix": "fireworks",
        "default_models": [],
    },
    "perplexity": {
        "name": "Perplexity",
        "icon": "🔍",
        "logo": "perplexity.svg",
        "api_type": "openai_compat",
        "base_url": "https://api.perplexity.ai",
        "prefix": "perplexity",
        "default_models": [],
    },
    "xai": {
        "name": "xAI (Grok)",
        "icon": "❌",
        "logo": "xai.svg",
        "api_type": "openai_compat",
        "base_url": "https://api.x.ai/v1",
        "prefix": "xai",
        "default_models": [],
    },
    "qwen": {
        "name": "Qwen/DashScope",
        "icon": "🌐",
        "logo": "",
        "api_type": "openai_compat",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "prefix": "qwen",
        "default_models": ["qwen-max", "qwen-plus", "qwen-turbo"],
    },
    "opencode_free": {
        "name": "OpenCode Zen (Free)",
        "icon": "🆓",
        "logo": "",
        "api_type": "opencode_free",
        "base_url": "",
        "prefix": "opencode",
        "default_models": [],
    },
    "custom": {
        "name": "Custom Provider",
        "icon": "⚙️",
        "logo": "",
        "api_type": "openai_compat",
        "base_url": "",
        "prefix": "",
        "default_models": [],
    },
}


def get_catalog() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(CATALOG)


def provider_logo_url(filename: str) -> str:
    return f"/dashboard/static/logos/{filename}"


def get_provider_logo_map() -> dict[str, str]:
    logos: dict[str, str] = {}
    for key, entry in CATALOG.items():
        logo = entry.get("logo")
        if not logo:
            continue
        logos[key] = str(logo)
        prefix = entry.get("prefix")
        if prefix:
            logos[str(prefix)] = str(logo)
    return logos
