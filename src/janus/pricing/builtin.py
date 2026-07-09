from __future__ import annotations

from .models import ModelPricing

BUILTIN_PRICING: dict[str, ModelPricing] = {
    # Anthropic
    "claude-opus-4-20250514": ModelPricing(15.0, 75.0, 18.75, 1.5),
    "claude-sonnet-4-20250514": ModelPricing(3.0, 15.0, 3.75, 0.3),
    "claude-3.7-sonnet-20250219": ModelPricing(3.0, 15.0, 3.75, 0.3),
    "claude-3-5-sonnet-20241022": ModelPricing(3.0, 15.0, 3.75, 0.3),
    "claude-3-5-haiku-20241022": ModelPricing(0.8, 4.0, 1.0, 0.08),
    "claude-3-opus-20240229": ModelPricing(15.0, 75.0, 18.75, 1.5),
    # OpenAI
    "gpt-4o": ModelPricing(2.5, 10.0, 0.0, 1.25),
    "gpt-4o-mini": ModelPricing(0.15, 0.6, 0.0, 0.075),
    "o3": ModelPricing(10.0, 40.0, 0.0, 0.0),
    "o4-mini": ModelPricing(1.1, 4.4, 0.0, 0.0),
    "gpt-4.1": ModelPricing(2.0, 8.0, 0.0, 0.5),
    "gpt-4.1-mini": ModelPricing(0.4, 1.6, 0.0, 0.1),
    "gpt-4.1-nano": ModelPricing(0.1, 0.4, 0.0, 0.025),
    # xAI
    "grok-4": ModelPricing(3.0, 15.0, 0.0, 0.75),
    "grok-4-fast-reasoning": ModelPricing(0.2, 0.5, 0.0, 0.05),
    "grok-code-fast-1": ModelPricing(0.2, 1.5, 0.0, 0.02),
    "grok-3": ModelPricing(3.0, 15.0, 0.0, 0.75),
    "grok-3-mini": ModelPricing(0.3, 0.5, 0.0, 0.07),
    # Google
    "gemini-2.5-pro": ModelPricing(1.25, 10.0, 0.0, 0.31),
    "gemini-2.0-flash": ModelPricing(0.1, 0.4, 0.0, 0.025),
    "gemini-2.0-flash-lite": ModelPricing(0.075, 0.3, 0.0, 0.01875),
    "gemini-1.5-pro": ModelPricing(1.25, 5.0, 0.0, 0.3125),
    "gemini-1.5-flash": ModelPricing(0.075, 0.3, 0.0, 0.01875),
    "gemini-1.5-flash-8b": ModelPricing(0.0375, 0.15, 0.0, 0.009375),
    # DeepSeek
    "deepseek-chat": ModelPricing(0.27, 1.1, 0.0, 0.07),
    "deepseek-reasoner": ModelPricing(0.55, 2.19, 0.0, 0.14),
    "deepseek-v4-pro": ModelPricing(0.435, 0.87, 0.0, 0.003625),
    "deepseek-v4-flash": ModelPricing(0.14, 0.28, 0.0, 0.0028),
    # Meta / others
    "llama-3.3-70b-instruct": ModelPricing(0.6, 0.6, 0.0, 0.0),
    "llama-3.1-405b-instruct": ModelPricing(3.0, 3.0, 0.0, 0.0),
    "mistral-large-2411": ModelPricing(2.0, 6.0, 0.0, 0.5),
    "qwen-max": ModelPricing(1.6, 6.4, 0.0, 0.4),
    "qwen-plus": ModelPricing(0.4, 1.2, 0.0, 0.1),
    "qwen-turbo": ModelPricing(0.05, 0.2, 0.0, 0.0125),
    "glm-4.7": ModelPricing(0.6, 2.2, 0.0, 0.0),
}
