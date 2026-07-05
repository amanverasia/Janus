"""Unified provider catalog — single source of truth for provider metadata.

Each entry may carry:
- ``inventory``: key-inventory metadata (auth, detection endpoints, billing model)
- ``gateway``: routing/dashboard metadata (api_type, prefix, default models, branding)

``janus.inventory.catalog`` and ``janus.dashboard.catalog`` derive their legacy
shapes from this module; the id bridges between the two namespaces
(``google``/``gemini``, ``dashscope``/``qwen``) are derived here as well.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "inventory": {
            "id": "openai",
            "name": "openai",
            "display_name": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "OPENAI_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "postpaid",
            "is_direct": True,
            "routing_note": None,
        },
        "gateway": {
            "id": "openai",
            "name": "OpenAI",
            "icon": "🟢",
            "logo": "openai.svg",
            "api_type": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "prefix": "openai",
            "default_models": ["gpt-4o", "gpt-4o-mini", "o3", "o4-mini"],
        },
    },
    "anthropic": {
        "inventory": {
            "id": "anthropic",
            "name": "anthropic",
            "display_name": "Anthropic",
            "base_url": "https://api.anthropic.com/v1",
            "auth_type": "api_key",
            "auth_header": "x-api-key",
            "auth_prefix": "",
            "key_env_var": "ANTHROPIC_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "postpaid",
            "is_direct": True,
            "routing_note": None,
        },
        "gateway": {
            "id": "anthropic",
            "name": "Anthropic",
            "icon": "🟠",
            "logo": "anthropic.svg",
            "api_type": "anthropic",
            "base_url": "https://api.anthropic.com",
            "prefix": "anthropic",
            "default_models": ["claude-sonnet-4-20250514", "claude-opus-4-20250514"],
        },
    },
    "openrouter": {
        "inventory": {
            "id": "openrouter",
            "name": "openrouter",
            "display_name": "OpenRouter",
            "base_url": "https://openrouter.ai/api/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "OPENROUTER_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": "/key",
            "billing_model": "prepaid",
            "is_direct": False,
            "routing_note": "Routes requests to multiple providers. Credits "
            "are OpenRouter-specific.",
        },
        "gateway": {
            "id": "openrouter",
            "name": "OpenRouter",
            "icon": "🔀",
            "logo": "openrouter.svg",
            "api_type": "openai_compat",
            "base_url": "https://openrouter.ai/api/v1",
            "prefix": "openrouter",
            "default_models": [],
        },
    },
    "google": {
        "inventory": {
            "id": "google",
            "name": "google",
            "display_name": "Google AI (Gemini)",
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "auth_type": "api_key",
            "auth_header": "x-goog-api-key",
            "auth_prefix": "",
            "key_env_var": "GOOGLE_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "free_tier",
            "is_direct": True,
            "routing_note": None,
        },
        "gateway": {
            "id": "gemini",
            "name": "Google Gemini",
            "icon": "🔵",
            "logo": "gemini.svg",
            "api_type": "gemini",
            "base_url": "https://generativelanguage.googleapis.com",
            "prefix": "gemini",
            "default_models": ["gemini-2.5-pro", "gemini-2.0-flash"],
        },
    },
    "groq": {
        "inventory": {
            "id": "groq",
            "name": "groq",
            "display_name": "Groq",
            "base_url": "https://api.groq.com/openai/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "GROQ_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "free_tier",
            "is_direct": True,
            "routing_note": None,
        },
        "gateway": {
            "id": "groq",
            "name": "Groq",
            "icon": "⚡",
            "logo": "groq.svg",
            "api_type": "openai_compat",
            "base_url": "https://api.groq.com/openai/v1",
            "prefix": "groq",
            "default_models": ["llama-3.3-70b-instruct"],
        },
    },
    "together": {
        "inventory": {
            "id": "together",
            "name": "together",
            "display_name": "Together AI",
            "base_url": "https://api.together.xyz/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "TOGETHER_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": None,
        },
        "gateway": {
            "id": "together",
            "name": "Together AI",
            "icon": "🤝",
            "logo": "together.svg",
            "api_type": "openai_compat",
            "base_url": "https://api.together.xyz/v1",
            "prefix": "together",
            "default_models": [],
        },
    },
    "perplexity": {
        "inventory": {
            "id": "perplexity",
            "name": "perplexity",
            "display_name": "Perplexity",
            "base_url": "https://api.perplexity.ai",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "PERPLEXITY_API_KEY",
            "models_endpoint": None,
            "health_check_endpoint": None,
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": None,
        },
        "gateway": {
            "id": "perplexity",
            "name": "Perplexity",
            "icon": "🔍",
            "logo": "perplexity.svg",
            "api_type": "openai_compat",
            "base_url": "https://api.perplexity.ai",
            "prefix": "perplexity",
            "default_models": [],
        },
    },
    "cohere": {
        "inventory": {
            "id": "cohere",
            "name": "cohere",
            "display_name": "Cohere",
            "base_url": "https://api.cohere.com/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "COHERE_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "postpaid",
            "is_direct": True,
            "routing_note": None,
        }
    },
    "mistral": {
        "inventory": {
            "id": "mistral",
            "name": "mistral",
            "display_name": "Mistral AI",
            "base_url": "https://api.mistral.ai/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "MISTRAL_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "postpaid",
            "is_direct": True,
            "routing_note": None,
        },
        "gateway": {
            "id": "mistral",
            "name": "Mistral",
            "icon": "🌬️",
            "logo": "mistral.svg",
            "api_type": "openai_compat",
            "base_url": "https://api.mistral.ai/v1",
            "prefix": "mistral",
            "default_models": ["mistral-large-2411"],
        },
    },
    "deepseek": {
        "inventory": {
            "id": "deepseek",
            "name": "deepseek",
            "display_name": "DeepSeek",
            "base_url": "https://api.deepseek.com/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "DEEPSEEK_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": "/user/balance",
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": None,
        },
        "gateway": {
            "id": "deepseek",
            "name": "DeepSeek",
            "icon": "🔬",
            "logo": "deepseek.svg",
            "api_type": "openai_compat",
            "base_url": "https://api.deepseek.com/v1",
            "prefix": "deepseek",
            "default_models": ["deepseek-chat", "deepseek-reasoner"],
        },
    },
    "xai": {
        "inventory": {
            "id": "xai",
            "name": "xai",
            "display_name": "xAI (Grok)",
            "base_url": "https://api.x.ai/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "XAI_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": None,
        },
        "gateway": {
            "id": "xai",
            "name": "xAI (Grok)",
            "icon": "❌",
            "logo": "xai.svg",
            "api_type": "openai_compat",
            "base_url": "https://api.x.ai/v1",
            "prefix": "xai",
            "default_models": [],
        },
    },
    "huggingface": {
        "inventory": {
            "id": "huggingface",
            "name": "huggingface",
            "display_name": "Hugging Face",
            "base_url": "https://api-inference.huggingface.co",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "HF_API_KEY",
            "models_endpoint": None,
            "health_check_endpoint": None,
            "credit_check_endpoint": None,
            "billing_model": "free_tier",
            "is_direct": True,
            "routing_note": None,
        }
    },
    "replicate": {
        "inventory": {
            "id": "replicate",
            "name": "replicate",
            "display_name": "Replicate",
            "base_url": "https://api.replicate.com/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "REPLICATE_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "postpaid",
            "is_direct": True,
            "routing_note": None,
        }
    },
    "fireworks": {
        "inventory": {
            "id": "fireworks",
            "name": "fireworks",
            "display_name": "Fireworks AI",
            "base_url": "https://api.fireworks.ai/inference/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "FIREWORKS_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "postpaid",
            "is_direct": True,
            "routing_note": None,
        },
        "gateway": {
            "id": "fireworks",
            "name": "Fireworks",
            "icon": "🎆",
            "logo": "fireworks.svg",
            "api_type": "openai_compat",
            "base_url": "https://api.fireworks.ai/inference/v1",
            "prefix": "fireworks",
            "default_models": [],
        },
    },
    "nvidia": {
        "inventory": {
            "id": "nvidia",
            "name": "nvidia",
            "display_name": "NVIDIA NIM",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "NVIDIA_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "free_tier",
            "is_direct": True,
            "routing_note": None,
        }
    },
    "moonshot": {
        "inventory": {
            "id": "moonshot",
            "name": "moonshot",
            "display_name": "Moonshot (Kimi)",
            "base_url": "https://api.moonshot.cn/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "MOONSHOT_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": "/users/me/balance",
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": None,
        }
    },
    "dashscope": {
        "inventory": {
            "id": "dashscope",
            "name": "dashscope",
            "display_name": "DashScope (Qwen)",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "DASHSCOPE_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "postpaid",
            "is_direct": True,
            "routing_note": None,
        },
        "gateway": {
            "id": "qwen",
            "name": "Qwen/DashScope",
            "icon": "🌐",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "prefix": "qwen",
            "default_models": ["qwen-max", "qwen-plus", "qwen-turbo"],
        },
    },
    "minimax": {
        "inventory": {
            "id": "minimax",
            "name": "minimax",
            "display_name": "MiniMax",
            "base_url": "https://api.minimaxi.com/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "MINIMAX_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "postpaid",
            "is_direct": True,
            "routing_note": "OpenAI-compatible endpoint; if keys show invalid, "
            "re-add with the exact base URL via the custom "
            "field.",
        }
    },
    "siliconflow": {
        "inventory": {
            "id": "siliconflow",
            "name": "siliconflow",
            "display_name": "SiliconFlow",
            "base_url": "https://api.siliconflow.com/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "SILICONFLOW_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": None,
        }
    },
    "stepfun": {
        "inventory": {
            "id": "stepfun",
            "name": "stepfun",
            "display_name": "StepFun",
            "base_url": "https://api.stepfun.com/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "STEPFUN_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": None,
        }
    },
    "zhipu": {
        "inventory": {
            "id": "zhipu",
            "name": "zhipu",
            "display_name": "Zhipu AI (GLM / Z.ai)",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "ZHIPU_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": "GLM models. Validated via a chat probe (no public "
            "model-list endpoint).",
        }
    },
    "xiaomi": {
        "inventory": {
            "id": "xiaomi",
            "name": "xiaomi",
            "display_name": "Xiaomi MiMo",
            "base_url": "https://api.xiaomimimo.com/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "XIAOMI_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": "Token-plan keys (tp- prefix). Regional endpoints: "
            "token-plan-cn.xiaomimimo.com, "
            "token-plan-sgp.xiaomimimo.com.",
        }
    },
    "tavily": {
        "inventory": {
            "id": "tavily",
            "name": "tavily",
            "display_name": "Tavily",
            "base_url": "https://api.tavily.com",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "TAVILY_API_KEY",
            "models_endpoint": None,
            "health_check_endpoint": None,
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": "Web search API. Keys start with tvly-.",
        }
    },
    "firecrawl": {
        "inventory": {
            "id": "firecrawl",
            "name": "firecrawl",
            "display_name": "Firecrawl",
            "base_url": "https://api.firecrawl.dev",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "FIRECRAWL_API_KEY",
            "models_endpoint": None,
            "health_check_endpoint": None,
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": "Web scraping API. Keys start with fc-.",
        }
    },
    "fal": {
        "inventory": {
            "id": "fal",
            "name": "fal",
            "display_name": "Fal.ai",
            "base_url": "https://api.fal.ai/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Key",
            "key_env_var": "FAL_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "postpaid",
            "is_direct": True,
            "routing_note": "Generative media platform. Key format: UUID:hex.",
        }
    },
    "exa": {
        "inventory": {
            "id": "exa",
            "name": "exa",
            "display_name": "Exa",
            "base_url": "https://api.exa.ai",
            "auth_type": "api_key",
            "auth_header": "x-api-key",
            "auth_prefix": "",
            "key_env_var": "EXA_API_KEY",
            "models_endpoint": None,
            "health_check_endpoint": None,
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": "Web search API. Key format: UUID.",
        }
    },
    "brave-search": {
        "inventory": {
            "id": "brave-search",
            "name": "brave-search",
            "display_name": "Brave Search",
            "base_url": "https://api.search.brave.com/res/v1",
            "auth_type": "api_key",
            "auth_header": "X-Subscription-Token",
            "auth_prefix": "",
            "key_env_var": "BRAVE_SEARCH_API_KEY",
            "models_endpoint": None,
            "health_check_endpoint": None,
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": "Web search API. Keys start with BSA.",
        }
    },
    "custom": {
        "inventory": {
            "id": "custom",
            "name": "custom",
            "display_name": "Custom Provider",
            "base_url": "",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "CUSTOM_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "unknown",
            "is_direct": True,
            "routing_note": "Self-hosted / OpenAI-compatible endpoint (base URL supplied per key).",
        },
        "gateway": {
            "id": "custom",
            "name": "Custom Provider",
            "icon": "⚙️",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "",
            "prefix": "",
            "default_models": [],
        },
    },
    "unidentified": {
        "inventory": {
            "id": "unidentified",
            "name": "unidentified",
            "display_name": "Unidentified (needs review)",
            "base_url": "",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": None,
            "models_endpoint": None,
            "health_check_endpoint": None,
            "credit_check_endpoint": None,
            "billing_model": "unknown",
            "is_direct": True,
            "routing_note": "Provider could not be auto-detected. Review "
            "the key and re-add with the correct provider "
            "or a custom base URL.",
        }
    },
    "opencode_free": {
        "gateway": {
            "id": "opencode_free",
            "name": "OpenCode Zen (Free)",
            "icon": "🆓",
            "logo": "",
            "api_type": "opencode_free",
            "base_url": "",
            "prefix": "opencode",
            "default_models": [],
        }
    },
    "github_copilot": {
        "gateway": {
            "id": "github_copilot",
            "name": "GitHub Copilot",
            "icon": "🐙",
            "logo": "",
            "api_type": "github_copilot",
            "base_url": "https://api.githubcopilot.com",
            "prefix": "copilot",
            "default_models": ["gpt-4o", "gpt-4.1", "o4-mini", "claude-sonnet-4"],
        }
    },
}

GATEWAY_ORDER: list[str] = [
    "openai",
    "anthropic",
    "gemini",
    "groq",
    "together",
    "deepseek",
    "openrouter",
    "mistral",
    "fireworks",
    "perplexity",
    "xai",
    "qwen",
    "github_copilot",
    "opencode_free",
    "custom",
]


def inventory_entries() -> dict[str, dict[str, Any]]:
    return {
        provider_id: deepcopy(entry["inventory"])
        for provider_id, entry in PROVIDERS.items()
        if "inventory" in entry
    }


def gateway_entries() -> dict[str, dict[str, Any]]:
    by_catalog_id = {
        entry["gateway"]["id"]: {
            key: deepcopy(value) for key, value in entry["gateway"].items() if key != "id"
        }
        for entry in PROVIDERS.values()
        if "gateway" in entry
    }
    ordered = {cid: by_catalog_id.pop(cid) for cid in GATEWAY_ORDER if cid in by_catalog_id}
    ordered.update(by_catalog_id)
    return ordered


def inventory_to_gateway_map() -> dict[str, str]:
    return {
        provider_id: entry["gateway"]["id"]
        for provider_id, entry in PROVIDERS.items()
        if "gateway" in entry and entry["gateway"]["id"] != provider_id
    }


def prefix_to_inventory_map() -> dict[str, str]:
    return {
        entry["gateway"]["prefix"]: provider_id
        for provider_id, entry in PROVIDERS.items()
        if "gateway" in entry
        and "inventory" in entry
        and entry["gateway"]["prefix"]
        and entry["gateway"]["prefix"] != provider_id
    }
