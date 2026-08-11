#!/usr/bin/env python3
"""Fix categorization logic - separate file to avoid token limits."""

# Model categorization based on actual capabilities, not just keywords
MODEL_CATEGORIES = {
    # Explicit coding models
    "coding": [
        "pareto-code", "claude-3.5-sonnet", "claude-3.7-sonnet", "claude-4-sonnet",
        "gpt-4.1", "gpt-4o", "gpt-5", "deepseek-coder", "deepseek-v3", "deepseek-v4",
        "qwen2.5-coder", "qwen2.5-coder-32b", "codellama", "starcoder",
        "wizardcoder", "phind-codellama", "magicoder", "codegeex",
        "openrouter/owl-alpha", "openrouter/auto"
    ],
    
    # Explicit reasoning models
    "reasoning": [
        "o1", "o3", "o4", "r1", "deepseek-r1", "qwq", "qwen-qwq",
        "nemotron-3-ultra", "nemotron-4", "llama-3.1-405b",
        "gpt-5-pro", "gpt-5-thinking", "claude-3.7-sonnet-thinking",
        "gemini-2.0-flash-thinking"
    ],
    
    # Writing/Creative models
    "writing": [
        "claude-3.5-sonnet", "claude-3.7-sonnet", "claude-3-opus",
        "gpt-4o", "gpt-4.5", "gpt-5", "gemini-1.5-pro", "gemini-2.0-flash",
        "llama-3.1-70b", "llama-3.1-405b", "nemotron-3-ultra",
        "command-r-plus", "command-r", "mixtral-8x22b"
    ],
    
    # General purpose / chat
    "general": [
        "llama-3.1-8b", "llama-3.1-70b", "llama-3.2", "gemma-2", "gemma-3",
        "mistral-nemo", "mistral-small", "phi-3", "phi-4"
    ]
}

# Known free models on OpenRouter (cost = 0)
FREE_MODELS = {
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "nvidia/nemotron-nano-9b-v2:free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "deepseek/deepseek-v4-pro:free",
    "deepseek/deepseek-v4-flash:free",
    "deepseek/deepseek-r1:free",
    "qwen/qwen3-235b:free",
    "qwen/qwen2.5-72b:free",
    "meta-llama/llama-3.1-405b:free",
    "meta-llama/llama-3.1-70b:free",
    "meta-llama/llama-3.2-90b:free",
    "google/gemma-2-27b:free",
    "google/gemma-3-27b:free",
    "mistralai/mistral-nemo:free",
    "microsoft/phi-4:free",
    "x-ai/grok-2:free",
    "x-ai/grok-3:free",
}

# Provider pricing for paid models (per 1M output tokens) — Aug 2026
PROVIDER_PRICING = {
    # OpenAI (via OpenRouter)
    "openai/gpt-5.5": 30.00,
    "openai/gpt-5": 15.00,
    "openai/gpt-5-mini": 2.00,
    "openai/o4-mini": 2.50,
    "openai/o3": 8.00,
    "openai/o3-mini": 2.50,

    # Anthropic (via OpenRouter)
    "anthropic/claude-sonnet-4.6": 15.00,
    "anthropic/claude-opus-4.8": 25.00,
    "anthropic/claude-haiku-4.5": 1.50,
    "anthropic/claude-3.5-sonnet": 3.00,

    # Google (via OpenRouter)
    "google/gemini-2.5-pro": 10.00,
    "google/gemini-2.5-flash": 2.50,
    "google/gemini-2.5-flash-lite": 0.40,
    "google/gemini-3.5-flash": 9.00,
    "google/gemini-3.5-flash-lite": 2.50,
    "google/gemini-1.5-pro": 2.10,
    "google/gemini-1.5-flash": 0.35,

    # DeepSeek
    "deepseek/deepseek-v4-pro": 0.87,
    "deepseek/deepseek-v4-flash": 0.20,
    "deepseek/deepseek-v4-flash-0731": 0.18,
    "~deepseek/deepseek-v4-flash-latest": 0.25,
    "deepseek/deepseek-r1": 2.00,
    "deepseek/deepseek-v3.2": 1.50,

    # Qwen (via OpenRouter) — Aug 2026 refresh
    "qwen/qwen3.8-max": 6.00,
    "qwen/qwen3.7-flash": 0.13,
    "qwen/qwen3.7-plus": 1.28,
    "qwen/qwen3.6-plus": 1.50,
    "qwen/qwen3.5-plus": 1.50,
    "qwen/qwen3-max": 2.00,
    "qwen/qwen3-coder": 0.50,

    # MoonshotAI
    "moonshotai/kimi-k2.6": 3.42,
    "moonshotai/kimi-k2.5": 2.00,

    # NVIDIA (via OpenRouter)
    "nvidia/nemotron-3-ultra-550b-a55b": 2.50,
    "nvidia/nemotron-3-super-120b-a12b": 1.50,
    "nvidia/nemotron-3-nano-30b-a3b": 0.50,

    # xAI
    "x-ai/grok-4.20": 2.50,
    "x-ai/grok-4.3": 5.00,
    "x-ai/grok-3": 5.00,

    # Meta — Aug 2026
    "meta/muse-spark-1.2": 4.25,
    "meta/muse-glimmer-30b": 1.50,
    "meta-llama/llama-3.3-70b": 0.50,
    "meta-llama/llama-3.1-405b": 1.50,
    "meta-llama/llama-3.1-70b": 0.30,
    "meta-llama/llama-4-maverick": 1.00,

    # Upstage — NEW Aug 10, 2026
    "upstage/solar-pro4": 0.12,
    "upstage/solar-pro-3": 0.60,

    # Sakana — NEW Aug 11, 2026
    "sakana/sakana-namazu": 4.00,

    # InclusionAI — Aug 2026
    "inclusionai/ling-3.0-tiny": 0.00,
    "inclusionai/ling-3.0-flash": 0.06,
    "inclusionai/ling-3.0-tiny:free": 0.00,
    "inclusionai/ring-2.6-1t": 0.62,

    # Poolside
    "poolside/laguna-s-2.1": 0.18,
    "poolside/laguna-xs-2.1": 0.12,

    # Thinking Machines
    "thinkingmachines/inkling-small": 1.20,
    "thinkingmachines/inkling": 4.04,

    # Others
    "xiaomi/mimo-v2.5-pro": 1.50,
    "xiaomi/mimo-v2.5": 0.75,
    "z-ai/glm-5": 1.50,
    "z-ai/glm-4.7": 1.00,
    "cohere/command-r-plus": 2.00,
    "cohere/command-r": 0.50,
    "mistralai/mistral-large-2512": 3.00,
    "mistralai/mistral-medium-3-5": 1.50,
    "mistralai/devstral-2512": 1.50,
}

def get_model_cost(model_id: str) -> float:
    """Get output cost per 1M tokens for a model."""
    # Check free models first
    if model_id in FREE_MODELS or model_id.endswith(":free"):
        return 0.0
    
    # Check known pricing
    if model_id in PROVIDER_PRICING:
        return PROVIDER_PRICING[model_id]
    
    # Try base model (without provider prefix)
    base = model_id.split("/")[-1]
    for key, price in PROVIDER_PRICING.items():
        if base in key or key in base:
            return price
    
    # Default fallback based on model name patterns
    if any(x in model_id.lower() for x in ["405b", "opus", "gpt-5", "o1-pro"]):
        return 15.0
    if any(x in model_id.lower() for x in ["70b", "72b", "sonnet", "gpt-4o", "gpt-4.1"]):
        return 3.0
    if any(x in model_id.lower() for x in ["8b", "7b", "mini", "flash", "haiku", "nemo", "small", "phi"]):
        return 0.15
    if any(x in model_id.lower() for x in ["32b", "pro", "plus", "large"]):
        return 1.0
    return 0.5  # reasonable default


def categorize_model(model_id: str, description: str = "") -> list:
    """Categorize model by capability based on known model IDs."""
    text = (model_id + " " + description).lower()
    cats = []
    
    # Check explicit lists
    for cat, models in MODEL_CATEGORIES.items():
        if any(m in model_id.lower() for m in models):
            cats.append(cat)
    
    # Keyword fallbacks
    if not cats:
        if any(kw in text for kw in ["coder", "code", "programming", "developer"]):
            cats.append("coding")
        if any(kw in text for kw in ["reasoning", "thinking", "logic", "math", "o1", "r1", "qwq"]):
            cats.append("reasoning")
        if any(kw in text for kw in ["chat", "instruct", "conversation", "general"]):
            cats.append("general")
        if any(kw in text for kw in ["creative", "writing", "content", "story"]):
            cats.append("writing")
    
    return cats if cats else ["general"]