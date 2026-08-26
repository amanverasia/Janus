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
            "default_models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "o4-mini", "o3"],
        },
        "capabilities": {"vision": True, "pdf": True, "tool_use": True},
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
            "default_models": [
                "claude-sonnet-4-5-20250929",
                "claude-haiku-4-5-20251001",
                "claude-opus-4-5-20251101",
                "claude-sonnet-4-6",
                "claude-opus-4-6",
            ],
        },
        "capabilities": {
            "vision": True,
            "pdf": True,
            "tool_use": True,
            "reasoning": True,
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
            "default_headers": {
                "HTTP-Referer": "https://janus.local",
                "X-Title": "Janus",
            },
            # OpenRouter speaks Anthropic Messages natively at /messages.
            "transports": {
                "anthropic": "https://openrouter.ai/api/v1",
            },
        },
        "capabilities": {"vision": True, "pdf": True, "tool_use": True},
    },
    "ollama": {
        "inventory": {
            "id": "ollama",
            "name": "ollama",
            "display_name": "Ollama Cloud",
            "base_url": "https://ollama.com/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "OLLAMA_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "subscription",
            "is_direct": True,
            "routing_note": "Ollama Cloud API keys from ollama.com/settings/keys. "
            "Model list is public; keys are validated via an authenticated chat probe.",
        },
        "gateway": {
            "id": "ollama",
            "name": "Ollama Cloud",
            "icon": "🦙",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://ollama.com/v1",
            "prefix": "ollama",
            "default_models": [],
        },
        "capabilities": {"vision": True, "pdf": False, "tool_use": True, "reasoning": True},
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
            "default_models": [
                "gemini-2.5-flash",
                "gemini-2.5-flash-lite",
                "gemini-2.5-pro",
                "gemini-3-flash-preview",
                "gemini-3.1-flash-lite",
                "gemini-flash-latest",
            ],
        },
        "capabilities": {
            "vision": True,
            "pdf": True,
            "tool_use": True,
            "reasoning": True,
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
            "default_models": [
                "llama-3.3-70b-versatile",
                "meta-llama/llama-4-maverick-17b-128e-instruct",
                "openai/gpt-oss-120b",
                "qwen/qwen3-32b",
            ],
        },
        "capabilities": {"vision": False, "pdf": False, "tool_use": True},
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
            "default_models": [
                "meta-llama/Llama-3.3-70B-Instruct-Turbo",
                "deepseek-ai/DeepSeek-R1",
                "Qwen/Qwen3-235B-A22B",
            ],
        },
        "capabilities": {"vision": True, "pdf": False, "tool_use": True},
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
            "default_models": ["sonar-pro", "sonar"],
        },
        "capabilities": {"vision": False, "pdf": False, "tool_use": True},
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
        },
        "gateway": {
            "id": "cohere",
            "name": "Cohere",
            "icon": "🔗",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://api.cohere.ai/v1",
            "prefix": "cohere",
            "default_models": [
                "command-a-03-2025",
                "command-r-plus-08-2024",
                "command-r-08-2024",
            ],
        },
        "capabilities": {"vision": False, "pdf": False, "tool_use": True},
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
            "default_models": ["mistral-large-latest", "mistral-medium-latest", "codestral-latest"],
        },
        "capabilities": {"vision": True, "pdf": False, "tool_use": True},
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
            "default_models": [
                "deepseek-v4-pro",
                "deepseek-v4-pro-max",
                "deepseek-v4-pro-none",
                "deepseek-v4-flash",
                "deepseek-chat",
                "deepseek-reasoner",
            ],
            "transports": {
                "anthropic": "https://api.deepseek.com/anthropic/v1",
            },
        },
        "capabilities": {"vision": False, "pdf": False, "tool_use": True},
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
            "default_models": [
                "grok-4",
                "grok-4-fast-reasoning",
                "grok-code-fast-1",
                "grok-3",
                "grok-4.20-0309-non-reasoning",
                "grok-4.20-0309-reasoning",
            ],
        },
        "capabilities": {
            "vision": True,
            "pdf": False,
            "tool_use": True,
            "reasoning": True,
            "search": True,
        },
    },
    "cerebras": {
        "inventory": {
            "id": "cerebras",
            "name": "cerebras",
            "display_name": "Cerebras",
            "base_url": "https://api.cerebras.ai/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "CEREBRAS_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "free_tier",
            "is_direct": True,
            "routing_note": None,
        },
        "gateway": {
            "id": "cerebras",
            "name": "Cerebras",
            "icon": "🧠",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://api.cerebras.ai/v1",
            "prefix": "cerebras",
            "default_models": ["llama-3.3-70b", "qwen-3-32b", "gpt-oss-120b"],
        },
        "capabilities": {"vision": False, "pdf": False, "tool_use": True},
    },
    "hyperbolic": {
        "inventory": {
            "id": "hyperbolic",
            "name": "hyperbolic",
            "display_name": "Hyperbolic",
            "base_url": "https://api.hyperbolic.xyz/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "HYPERBOLIC_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": None,
        },
        "gateway": {
            "id": "hyperbolic",
            "name": "Hyperbolic",
            "icon": "⚡",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://api.hyperbolic.xyz/v1",
            "prefix": "hyperbolic",
            "default_models": [
                "deepseek-ai/DeepSeek-R1",
                "deepseek-ai/DeepSeek-V3",
                "meta-llama/Llama-3.3-70B-Instruct",
            ],
        },
        "capabilities": {"vision": False, "pdf": False, "tool_use": True},
    },
    "nebius": {
        "inventory": {
            "id": "nebius",
            "name": "nebius",
            "display_name": "Nebius AI",
            "base_url": "https://api.studio.nebius.ai/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "NEBIUS_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": None,
        },
        "gateway": {
            "id": "nebius",
            "name": "Nebius AI",
            "icon": "☁️",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://api.studio.nebius.ai/v1",
            "prefix": "nebius",
            "default_models": ["meta-llama/Llama-3.3-70B-Instruct"],
        },
        "capabilities": {"vision": False, "pdf": False, "tool_use": True},
    },
    "chutes": {
        "inventory": {
            "id": "chutes",
            "name": "chutes",
            "display_name": "Chutes AI",
            "base_url": "https://llm.chutes.ai/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "CHUTES_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": None,
        },
        "gateway": {
            "id": "chutes",
            "name": "Chutes AI",
            "icon": "💧",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://llm.chutes.ai/v1",
            "prefix": "chutes",
            "default_models": [],
        },
        "capabilities": {"vision": False, "pdf": False, "tool_use": True},
    },
    "venice": {
        "inventory": {
            "id": "venice",
            "name": "venice",
            "display_name": "Venice AI",
            "base_url": "https://api.venice.ai/api/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "VENICE_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": "Private/uncensored inference. OpenAI-compatible.",
        },
        "gateway": {
            "id": "venice",
            "name": "Venice AI",
            "icon": "🛡️",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://api.venice.ai/api/v1",
            "prefix": "venice",
            "default_models": ["qwen3-235b-a22b-instruct-2507", "llama-3.3-70b"],
        },
        "capabilities": {"vision": False, "pdf": False, "tool_use": True},
    },
    "vercel-ai-gateway": {
        "inventory": {
            "id": "vercel-ai-gateway",
            "name": "vercel-ai-gateway",
            "display_name": "Vercel AI Gateway",
            "base_url": "https://ai-gateway.vercel.sh/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "VERCEL_AI_GATEWAY_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": "/credits",
            "billing_model": "prepaid",
            "is_direct": False,
            "routing_note": "Unified gateway; use provider/model ids like anthropic/claude-sonnet.",
        },
        "gateway": {
            "id": "vercel-ai-gateway",
            "name": "Vercel AI Gateway",
            "icon": "▲",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://ai-gateway.vercel.sh/v1",
            "prefix": "vercel",
            "default_models": [],
        },
        "capabilities": {"vision": True, "pdf": False, "tool_use": True},
    },
    "volcengine-ark": {
        "inventory": {
            "id": "volcengine-ark",
            "name": "volcengine-ark",
            "display_name": "Volcengine Ark",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "ARK_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": "Doubao / DeepSeek models via Volcengine Ark.",
        },
        "gateway": {
            "id": "volcengine-ark",
            "name": "Volcengine Ark",
            "icon": "🌋",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "prefix": "ark",
            "default_models": [],
        },
        "capabilities": {"vision": False, "pdf": False, "tool_use": True},
    },
    "byteplus": {
        "inventory": {
            "id": "byteplus",
            "name": "byteplus",
            "display_name": "BytePlus ModelArk",
            "base_url": "https://ark.ap-southeast.bytepluses.com/api/v3",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "BYTEPLUS_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "free_tier",
            "is_direct": True,
            "routing_note": "Seed / Kimi / GLM models via BytePlus ModelArk.",
        },
        "gateway": {
            "id": "byteplus",
            "name": "BytePlus ModelArk",
            "icon": "🅱️",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://ark.ap-southeast.bytepluses.com/api/v3",
            "prefix": "byteplus",
            "default_models": [],
        },
        "capabilities": {"vision": False, "pdf": False, "tool_use": True},
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
            "default_models": [
                "accounts/fireworks/models/deepseek-v3p1",
                "accounts/fireworks/models/llama-v3p3-70b-instruct",
                "accounts/fireworks/models/qwen3-235b-a22b",
            ],
        },
        "capabilities": {"vision": True, "pdf": False, "tool_use": True},
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
        },
        "gateway": {
            "id": "moonshot",
            "name": "Moonshot (Kimi)",
            "icon": "🌙",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://api.moonshot.cn/v1",
            "prefix": "moonshot",
            "default_models": [
                "kimi-k2.5",
                "moonshot-v1-auto",
                "moonshot-v1-8k",
                "moonshot-v1-128k",
            ],
        },
        "capabilities": {
            "vision": True,
            "pdf": False,
            "tool_use": True,
            "reasoning": True,
        },
    },
    "kimi_coding": {
        "gateway": {
            "id": "kimi_coding",
            "name": "Kimi Coding",
            "icon": "🌙",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://api.kimi.com/coding/v1",
            "prefix": "kimi",
            "default_models": [
                "kimi-k2.5",
                "kimi-k2.5-thinking",
                "kimi-k2.6",
            ],
            "transports": {
                "anthropic": "https://api.kimi.com/coding/v1",
            },
        },
        "capabilities": {
            "vision": True,
            "pdf": False,
            "tool_use": True,
            "reasoning": True,
        },
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
            "logo": "qwen.svg",
            "api_type": "openai_compat",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "prefix": "qwen",
            "default_models": ["qwen-max", "qwen-plus", "qwen-turbo"],
        },
        "capabilities": {"vision": True, "pdf": False, "tool_use": True},
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
        },
        "gateway": {
            "id": "minimax",
            "name": "MiniMax",
            "icon": "🟣",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://api.minimaxi.com/v1",
            "prefix": "minimax",
            "default_models": [
                "MiniMax-M2.7",
                "MiniMax-M2.5",
                "MiniMax-M2.1",
                "MiniMax-M3",
            ],
            "transports": {
                "anthropic": "https://api.minimaxi.com/anthropic/v1",
            },
        },
        "capabilities": {
            "vision": True,
            "pdf": False,
            "tool_use": True,
            "reasoning": True,
        },
    },
    "minimax_io": {
        "gateway": {
            "id": "minimax_io",
            "name": "MiniMax (International)",
            "icon": "🟣",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://api.minimax.io/v1",
            "prefix": "minimax-io",
            "default_models": [
                "MiniMax-M2.7",
                "MiniMax-M2.5",
                "MiniMax-M2.1",
                "MiniMax-M3",
            ],
            "transports": {
                "anthropic": "https://api.minimax.io/anthropic/v1",
            },
        },
        "capabilities": {
            "vision": True,
            "pdf": False,
            "tool_use": True,
            "reasoning": True,
        },
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
        },
        "gateway": {
            "id": "zhipu",
            "name": "Zhipu AI (GLM)",
            "icon": "🟦",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "prefix": "zhipu",
            "default_models": [
                "glm-4.7",
                "glm-4.6",
                "glm-4-flash",
                "glm-4.5",
            ],
            "transports": {
                "anthropic": "https://open.bigmodel.cn/api/anthropic",
            },
        },
        "capabilities": {
            "vision": True,
            "pdf": False,
            "tool_use": True,
            "reasoning": True,
        },
    },
    "glm_coding": {
        "gateway": {
            "id": "glm_coding",
            "name": "GLM Coding (Z.ai)",
            "icon": "🟦",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://api.z.ai/api/coding/paas/v4",
            "prefix": "glm",
            "default_models": [
                "glm-5.2",
                "glm-5.1",
                "glm-4.7",
                "glm-4.6",
            ],
            "transports": {
                "anthropic": "https://api.z.ai/api/anthropic",
            },
        },
        "capabilities": {
            "vision": True,
            "pdf": False,
            "tool_use": True,
            "reasoning": True,
        },
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
            "routing_note": "Xiaomi MiMo API keys (pay-as-you-go). OpenAI + Anthropic "
            "transports at api.xiaomimimo.com.",
        },
        "gateway": {
            "id": "xiaomi",
            "name": "Xiaomi MiMo",
            "icon": "📱",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://api.xiaomimimo.com/v1",
            "prefix": "xiaomi",
            "default_models": [
                "mimo-v2.5-pro",
                "mimo-v2.5",
                "mimo-v2-omni",
                "mimo-v2-flash",
                "mimo-v2.5-pro-claude",
            ],
            "transports": {
                "anthropic": "https://api.xiaomimimo.com/anthropic/v1",
            },
        },
        "capabilities": {"vision": True, "pdf": False, "tool_use": True, "reasoning": True},
    },
    "xiaomi_tokenplan": {
        "inventory": {
            "id": "xiaomi_tokenplan",
            "name": "xiaomi_tokenplan",
            "display_name": "Xiaomi MiMo (Token Plan)",
            "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "XIAOMI_TOKENPLAN_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "prepaid",
            "is_direct": True,
            "routing_note": "Token Plan keys (tp- prefix) are region-specific. "
            "Set custom base URL per key: token-plan-sgp / token-plan-cn / "
            "token-plan-ams.xiaomimimo.com/v1.",
        },
        "gateway": {
            "id": "xiaomi_tokenplan",
            "name": "Xiaomi MiMo (Token Plan)",
            "icon": "📱",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
            "prefix": "xmtp",
            "default_models": [
                "mimo-v2.5-pro",
                "mimo-v2.5",
                "mimo-v2-pro",
                "mimo-v2-omni",
                "mimo-v2.5-tts",
                "mimo-v2.5-asr",
            ],
            "transports": {
                "anthropic": "https://token-plan-sgp.xiaomimimo.com/anthropic/v1",
            },
        },
        "capabilities": {"vision": True, "pdf": False, "tool_use": True, "reasoning": True},
    },
    "mimo_free": {
        "gateway": {
            "id": "mimo_free",
            "name": "MiMo Code Free",
            "icon": "🆓",
            "logo": "",
            "api_type": "mimo_free",
            "base_url": "https://api.xiaomimimo.com/api/free-ai/openai/chat",
            "prefix": "mmf",
            "default_models": ["mimo-auto"],
        },
        "capabilities": {"vision": False, "pdf": False, "tool_use": True},
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
    "gorouter": {
        "inventory": {
            "id": "gorouter",
            "name": "gorouter",
            "display_name": "Gorouter",
            "base_url": "https://gorouter.app/v1",
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": "GOROUTER_API_KEY",
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "unknown",
            "is_direct": True,
            "routing_note": "OpenAI-compatible Claude gateway behind Cloudflare (gorouter.app).",
        },
        "gateway": {
            "id": "gorouter",
            "name": "Gorouter",
            "icon": "🔀",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": "https://gorouter.app/v1",
            "prefix": "gorouter",
            "default_models": [
                "claude-opus-5-thinking",
                "claude-opus-5",
                "claude-opus-4-8",
                "claude-opus-4-8-thinking",
            ],
        },
        "capabilities": {"vision": True, "pdf": False, "tool_use": True},
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
            "logo": "custom.svg",
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
            "logo": "opencode.svg",
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
            "logo": "github-copilot.svg",
            "api_type": "github_copilot",
            "base_url": "https://api.githubcopilot.com",
            "prefix": "copilot",
            "default_models": ["gpt-4o", "gpt-4.1", "o4-mini", "claude-sonnet-4"],
        },
        "capabilities": {"vision": True, "pdf": False, "tool_use": True},
    },
    "antigravity": {
        "inventory": {
            "id": "antigravity",
            "name": "antigravity",
            "display_name": "Antigravity (Google)",
            "base_url": "https://cloudcode-pa.googleapis.com",
            "auth_type": "oauth",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": None,
            "models_endpoint": None,
            "health_check_endpoint": None,
            "credit_check_endpoint": None,
            "billing_model": "subscription",
            "is_direct": True,
            "routing_note": "Paste an Antigravity OAuth JSON blob with access/refresh tokens. "
            "Project discovery and onboarding use Cloud Code Assist.",
        },
        "gateway": {
            "id": "antigravity",
            "name": "Antigravity (Google)",
            "icon": "✨",
            "logo": "",
            "api_type": "antigravity",
            "base_url": "https://daily-cloudcode-pa.googleapis.com",
            "prefix": "antigravity",
            "default_models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
        },
        "capabilities": {
            "vision": True,
            "pdf": False,
            "tool_use": True,
            "reasoning": True,
        },
    },
    "codex": {
        "inventory": {
            "id": "codex",
            "name": "codex",
            "display_name": "Codex (ChatGPT)",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "auth_type": "oauth",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": None,
            "models_endpoint": None,
            "health_check_endpoint": None,
            "credit_check_endpoint": None,
            "billing_model": "subscription",
            "is_direct": True,
            "routing_note": "Paste a Janus OAuth JSON blob, a 9router Codex connection "
            "object (or providerConnections array), or a bare access token. "
            "Select Codex explicitly. Providers-page paste still works.",
        },
        "gateway": {
            "id": "codex",
            "name": "Codex (ChatGPT)",
            "icon": "⚙️",
            "logo": "",
            "api_type": "codex",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "prefix": "codex",
            "default_models": ["gpt-5.1-codex", "o3", "o4-mini"],
        },
        "capabilities": {
            "vision": True,
            "pdf": False,
            "tool_use": True,
            "reasoning": True,
        },
    },
    "kiro": {
        "inventory": {
            "id": "kiro",
            "name": "kiro",
            "display_name": "Kiro (AWS)",
            "base_url": "https://runtime.us-east-1.kiro.dev",
            "auth_type": "oauth",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": None,
            "models_endpoint": None,
            "health_check_endpoint": None,
            "credit_check_endpoint": None,
            "billing_model": "subscription",
            "is_direct": True,
            "routing_note": (
                "Paste a Kiro OAuth credential JSON blob containing accessToken and refreshToken."
            ),
        },
        "gateway": {
            "id": "kiro",
            "name": "Kiro",
            "icon": "☁️",
            "logo": "",
            "api_type": "kiro",
            "base_url": "https://runtime.us-east-1.kiro.dev",
            "prefix": "kiro",
            "default_models": ["claude-sonnet-4"],
        },
        "capabilities": {"vision": True, "pdf": False, "tool_use": True, "reasoning": True},
    },
    "cursor": {
        "gateway": {
            "id": "cursor",
            "name": "Cursor",
            "icon": "🖱️",
            "logo": "",
            "api_type": "cursor",
            "base_url": "https://api2.cursor.sh",
            "prefix": "cursor",
            "default_models": ["composer-1", "gpt-4o", "claude-sonnet-4"],
        },
        "capabilities": {"vision": True, "pdf": False, "tool_use": True},
    },
    "claude_oauth": {
        "gateway": {
            "id": "claude_oauth",
            "name": "Claude Code (OAuth)",
            "icon": "🤖",
            "logo": "anthropic.svg",
            "api_type": "claude_oauth",
            "base_url": "https://api.anthropic.com",
            "prefix": "claude",
            "default_models": [
                "claude-sonnet-4-20250514",
                "claude-opus-4-20250514",
                "claude-haiku-4-5-20251001",
            ],
        },
        "capabilities": {
            "vision": True,
            "pdf": True,
            "tool_use": True,
            "reasoning": True,
        },
    },
}


def _gateway_only(
    provider_id: str,
    name: str,
    base_url: str,
    *,
    prefix: str | None = None,
    default_models: list[str] | None = None,
    key_optional: bool = False,
    allow_private_network: bool = False,
) -> dict[str, Any]:
    return {
        "gateway": {
            "id": provider_id,
            "name": name,
            "icon": "⚙️",
            "logo": "",
            "api_type": "openai_compat",
            "base_url": base_url,
            "prefix": prefix or provider_id,
            "default_models": list(default_models or []),
            "auth_kind": "local" if key_optional and allow_private_network else "key",
            "key_optional": key_optional,
            "allow_private_network": allow_private_network,
            "live_models": True,
            "default_model": default_models[0] if default_models else None,
        },
        "capabilities": {"vision": True, "pdf": False, "tool_use": True},
    }


_OPENAI_COMPAT_GATEWAYS = {
    "deepinfra": _gateway_only("deepinfra", "DeepInfra", "https://api.deepinfra.com/v1/openai"),
    "nscale": _gateway_only(
        "nscale",
        "Nscale Serverless Inference",
        "https://inference.api.nscale.com/v1",
        default_models=["meta-llama/Llama-3.1-8B-Instruct"],
    ),
    "vultr": _gateway_only(
        "vultr",
        "Vultr Serverless Inference",
        "https://api.vultrinference.com/v1",
        default_models=["kimi-k2-instruct"],
    ),
    "baseten": _gateway_only("baseten", "Baseten Model APIs", "https://inference.baseten.co/v1"),
    "sambanova": _gateway_only("sambanova", "SambaNova Cloud", "https://api.sambanova.ai/v1"),
    "digitalocean": _gateway_only(
        "digitalocean",
        "DigitalOcean Serverless Inference",
        "https://inference.do-ai.run/v1",
    ),
    "scaleway": _gateway_only("scaleway", "Scaleway Generative APIs", "https://api.scaleway.ai/v1"),
    "featherless": _gateway_only("featherless", "Featherless AI", "https://api.featherless.ai/v1"),
    "novita": _gateway_only("novita", "Novita AI", "https://api.novita.ai/openai/v1"),
    "huggingface": _gateway_only("huggingface", "Hugging Face", "https://router.huggingface.co/v1"),
    "nvidia": _gateway_only("nvidia", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1"),
    "nanogpt": _gateway_only("nanogpt", "NanoGPT", "https://nano-gpt.com/api/v1"),
    "synthetic": _gateway_only("synthetic", "Synthetic", "https://api.synthetic.new/openai/v1"),
    "siliconflow": _gateway_only("siliconflow", "SiliconFlow", "https://api.siliconflow.cn/v1"),
    "qianfan": _gateway_only("qianfan", "Qianfan (Baidu)", "https://qianfan.baidubce.com/v2"),
    "parallel": _gateway_only("parallel", "Parallel", "https://platform.parallel.ai"),
    "zenmux": _gateway_only(
        "zenmux",
        "ZenMux",
        "https://zenmux.ai/api/v1",
        default_models=["moonshotai/kimi-k3-free", "moonshotai/kimi-k3"],
    ),
    "litellm": _gateway_only(
        "litellm",
        "LiteLLM (self-hosted)",
        "http://localhost:4000/v1",
        key_optional=True,
        allow_private_network=True,
    ),
    "ollama-local": _gateway_only(
        "ollama-local",
        "Ollama (local)",
        "http://localhost:11434/v1",
        key_optional=True,
        allow_private_network=True,
    ),
    "ollama-cloud": _gateway_only("ollama-cloud", "Ollama Cloud", "https://ollama.com/v1"),
    "vllm": _gateway_only(
        "vllm",
        "vLLM (local)",
        "http://localhost:8000/v1",
        key_optional=True,
        allow_private_network=True,
    ),
    "lm-studio": _gateway_only(
        "lm-studio",
        "LM Studio (local)",
        "http://localhost:1234/v1",
        key_optional=True,
        allow_private_network=True,
    ),
    "cline": _gateway_only(
        "cline",
        "Cline",
        "https://api.cline.bot/api/v1",
        default_models=["anthropic/claude-sonnet-4-6"],
    ),
    "cline-pass": _gateway_only(
        "cline-pass",
        "ClinePass",
        "https://api.cline.bot/api/v1",
        default_models=["cline-pass/kimi-k3"],
    ),
    "orcarouter": _gateway_only(
        "orcarouter",
        "OrcaRouter",
        "https://api.orcarouter.ai/v1",
        default_models=["openai/gpt-5.5"],
    ),
    "bizrouter": _gateway_only(
        "bizrouter",
        "BizRouter",
        "https://api.bizrouter.ai/v1",
        default_models=["openai/gpt-5.6-sol"],
    ),
    "kilo": _gateway_only("kilo", "Kilo", "https://api.kilo.ai/api/gateway"),
    "gitlab-duo": _gateway_only(
        "gitlab-duo",
        "GitLab Duo",
        "https://cloud.gitlab.com/ai/v1/proxy/openai/v1",
    ),
    "cloudflare-workers-ai": _gateway_only(
        "cloudflare-workers-ai",
        "Cloudflare Workers AI",
        "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        default_models=["@cf/meta/llama-3.3-70b-instruct-fp8-fast"],
    ),
}

for _provider_id, _extra in _OPENAI_COMPAT_GATEWAYS.items():
    if _provider_id in PROVIDERS:
        PROVIDERS[_provider_id]["gateway"] = _extra["gateway"]
        PROVIDERS[_provider_id].setdefault("capabilities", _extra["capabilities"])
    else:
        PROVIDERS[_provider_id] = _extra

GATEWAY_ORDER: list[str] = [
    "openai",
    "anthropic",
    "gemini",
    "groq",
    "together",
    "deepseek",
    "openrouter",
    "ollama",
    "mistral",
    "fireworks",
    "perplexity",
    "xai",
    "cohere",
    "cerebras",
    "hyperbolic",
    "nebius",
    "chutes",
    "venice",
    "vercel-ai-gateway",
    "volcengine-ark",
    "byteplus",
    "qwen",
    "minimax",
    "minimax_io",
    "moonshot",
    "kimi_coding",
    "zhipu",
    "glm_coding",
    "github_copilot",
    "codex",
    "kiro",
    "cursor",
    "antigravity",
    "claude_oauth",
    "opencode_free",
    "xiaomi",
    "xiaomi_tokenplan",
    "mimo_free",
    "gorouter",
    "custom",
]

for _entry in PROVIDERS.values():
    _gateway = _entry.get("gateway")
    if isinstance(_gateway, dict) and _gateway["id"] not in GATEWAY_ORDER:
        GATEWAY_ORDER.append(str(_gateway["id"]))


def inventory_entries() -> dict[str, dict[str, Any]]:
    return {
        provider_id: deepcopy(entry["inventory"])
        for provider_id, entry in PROVIDERS.items()
        if "inventory" in entry
    }


def inventory_catalog_entries() -> dict[str, dict[str, Any]]:
    result = inventory_entries()
    for provider_id, entry in PROVIDERS.items():
        gateway = entry.get("gateway")
        if (
            provider_id in result
            or not isinstance(gateway, dict)
            or gateway.get("api_type") != "openai_compat"
        ):
            continue
        result[provider_id] = {
            "id": provider_id,
            "name": provider_id,
            "display_name": str(gateway.get("name") or provider_id),
            "base_url": str(gateway.get("base_url") or ""),
            "auth_type": "api_key",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer",
            "key_env_var": None,
            "models_endpoint": "/models",
            "health_check_endpoint": "/models",
            "credit_check_endpoint": None,
            "billing_model": "unknown",
            "is_direct": True,
            "routing_note": "Provider preset",
            "model_format": "openai",
            "allow_private_network": bool(gateway.get("allow_private_network")),
        }
    return result


def gateway_entries() -> dict[str, dict[str, Any]]:
    by_catalog_id: dict[str, dict[str, Any]] = {}
    account_api_types = {
        "antigravity",
        "claude_oauth",
        "codex",
        "cursor",
        "github_copilot",
        "kiro",
    }
    featured_ids = {
        "anthropic",
        "deepseek",
        "gemini",
        "groq",
        "ollama-local",
        "openai",
        "openrouter",
        "xai",
    }
    for provider_id, entry in PROVIDERS.items():
        if "gateway" not in entry:
            continue
        gw = entry["gateway"]
        catalog_id = gw["id"]
        item = {key: deepcopy(value) for key, value in gw.items() if key != "id"}
        # Top-level multi-format transports (e.g. DeepSeek Anthropic endpoint)
        if "transports" in entry and "transports" not in item:
            item["transports"] = deepcopy(entry["transports"])
        inventory = entry.get("inventory")
        api_type = str(item.get("api_type") or "")
        auth_kind = item.get("auth_kind")
        if not auth_kind:
            if api_type in account_api_types:
                auth_kind = "oauth"
            elif item.get("allow_private_network") and item.get("key_optional"):
                auth_kind = "local"
            else:
                auth_kind = "key"
        item["auth_kind"] = auth_kind
        item.setdefault("key_optional", api_type in {"mimo_free", "opencode_free"})
        models_endpoint = inventory.get("models_endpoint") if isinstance(inventory, dict) else None
        item.setdefault(
            "live_models",
            bool(models_endpoint)
            or api_type
            in {"antigravity", "cursor", "gemini", "github_copilot", "kiro", "openai_compat"},
        )
        default_models = item.get("default_models")
        if "default_model" not in item:
            item["default_model"] = (
                default_models[0] if isinstance(default_models, list) and default_models else None
            )
        item["featured"] = provider_id in featured_ids
        if catalog_id == "custom":
            group = "custom"
        elif auth_kind == "local":
            group = "local"
        elif auth_kind == "oauth":
            group = "accounts"
        elif isinstance(inventory, dict) and inventory.get("billing_model") == "free_tier":
            group = "free"
        else:
            group = "paid"
        item["group"] = group
        item["capabilities"] = deepcopy(entry.get("capabilities") or {})
        if isinstance(models_endpoint, str) and models_endpoint:
            item["model_discovery"] = {"path": models_endpoint}
        by_catalog_id[catalog_id] = item
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
        and ("inventory" in entry or entry["gateway"].get("api_type") == "openai_compat")
        and entry["gateway"]["prefix"]
        and entry["gateway"]["prefix"] != provider_id
    }
