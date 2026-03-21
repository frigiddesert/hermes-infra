#!/usr/bin/env python3
"""sync-model-catalog.py — Daily model catalog sync from OpenRouter.

Queries OpenRouter API, filters to affordable models (≤$3/M input tokens),
registers them as sub-provider groups in openclaw.json so the Discord picker
shows e.g. "or-openai", "or-google", "or-nvidia" as separate providers.

Each sub-provider points to OpenRouter's API with the same key, but models
are grouped for better Discord navigation.

Filtering rules:
  - ≤ $3/M input cost, ≥ 32k context
  - Exclude Anthropic
  - Exclude OpenAI models with "pro" in the name
  - Zero LLM tokens used
"""

import json
import sys
import subprocess
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

CONFIG_PATH = Path("/root/.openclaw/openclaw.json")
OPENROUTER_API = "https://openrouter.ai/api/v1/models"
MAX_INPUT_COST_PER_M = 3.0
MIN_CONTEXT = 32_000

SKIP_PROVIDERS = {"anthropic"}
DIRECT_PROVIDERS = {"bailian", "gemini", "groq", "openai"}

# Sub-provider prefix for OpenRouter groups in the config
OR_PREFIX = "or-"


def fetch_openrouter_models():
    req = urllib.request.Request(OPENROUTER_API)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["data"]


def price_per_million(pricing, key):
    val = pricing.get(key, "0") or "0"
    return float(val) * 1_000_000


def price_label(input_per_m):
    if input_per_m == 0:
        return "(Free)"
    return f"(${input_per_m:.2f}/M)"


def make_label(base_name, input_per_m):
    label = price_label(input_per_m)
    if label == "(Free)" and "free" in base_name.lower():
        return base_name
    return f"{base_name} {label}"


def should_include(model_id, provider, input_per_m, context):
    if provider in SKIP_PROVIDERS:
        return False
    if input_per_m < 0 or input_per_m > MAX_INPUT_COST_PER_M:
        return False
    if context < MIN_CONTEXT:
        return False
    if provider == "openai":
        model_part = model_id.split("/", 1)[-1] if "/" in model_id else model_id
        if "pro" in model_part.lower():
            return False
    return True


def sub_provider_key(or_provider_name):
    """Convert OpenRouter sub-provider name to config provider key."""
    return f"{OR_PREFIX}{or_provider_name}"


def main():
    print("Fetching OpenRouter model catalog...")
    or_models = fetch_openrouter_models()
    print(f"  {len(or_models)} models from OpenRouter")

    cfg = json.load(CONFIG_PATH.open())

    # Get OpenRouter API key from existing config
    or_config = cfg.get("models", {}).get("providers", {}).get("openrouter", {})
    or_api_key = or_config.get("apiKey", "os.environ/OPENROUTER_API_KEY")
    or_base_url = or_config.get("baseUrl", "https://openrouter.ai/api/v1")
    or_api = or_config.get("api", "openai-completions")

    # Group models by sub-provider
    by_sub = defaultdict(list)
    total_included = 0

    for m in or_models:
        model_id = m["id"]
        # Sub-provider is first segment: "openai/gpt-5" -> "openai"
        parts = model_id.split("/")
        if len(parts) >= 2:
            sub_prov = parts[0]
        else:
            sub_prov = "other"

        pricing = m.get("pricing", {})
        input_per_m = price_per_million(pricing, "prompt")
        output_per_m = price_per_million(pricing, "completion")
        context = m.get("context_length", 0)

        if not should_include(model_id, sub_prov, input_per_m, context):
            continue

        modalities = m.get("architecture", {}).get("input_modalities", [])
        input_types = ["text"]
        if "image" in modalities:
            input_types.append("image")

        base_name = m.get("name", model_id)
        entry = {
            "id": model_id,
            "name": make_label(base_name, input_per_m),
            "input": input_types,
            "contextWindow": m.get("context_length", 128000),
            "cost": {
                "input": input_per_m,
                "output": output_per_m,
                "cacheRead": 0,
                "cacheWrite": 0,
            },
        }
        max_tokens = m.get("top_provider", {}).get("max_completion_tokens")
        if max_tokens and isinstance(max_tokens, int) and max_tokens > 0:
            entry["maxTokens"] = max_tokens

        by_sub[sub_prov].append(entry)
        total_included += 1

    print(f"  {total_included} models across {len(by_sub)} sub-providers")

    # Consolidate tiny providers (≤2 models) into "other"
    MIN_GROUP_SIZE = 4
    consolidated = {}
    other_models = []
    for sub_prov, models in by_sub.items():
        if len(models) >= MIN_GROUP_SIZE:
            consolidated[sub_prov] = models
        else:
            other_models.extend(models)
    if other_models:
        consolidated["other"] = other_models
    by_sub = consolidated
    print(f"  Consolidated to {len(by_sub)} provider groups ({len(other_models)} models in 'other')")

    # --- Remove old or-* providers and openrouter models ---
    providers = cfg.setdefault("models", {}).setdefault("providers", {})
    old_or_keys = [k for k in providers if k.startswith(OR_PREFIX)]
    for k in old_or_keys:
        del providers[k]

    # Keep the base openrouter provider (for API key reference) but clear its models
    if "openrouter" in providers:
        providers["openrouter"]["models"] = []

    # --- Create sub-provider entries ---
    for sub_prov, models in sorted(by_sub.items()):
        key = sub_provider_key(sub_prov)
        providers[key] = {
            "baseUrl": or_base_url,
            "apiKey": or_api_key,
            "api": or_api,
            "models": models,
        }

    # --- Update allowlist ---
    existing_models = cfg.get("agents", {}).get("defaults", {}).get("models", {})
    preserved = {k: v for k, v in existing_models.items() if k.split("/")[0] in DIRECT_PROVIDERS}

    merged = dict(preserved)
    for sub_prov, models in by_sub.items():
        key = sub_provider_key(sub_prov)
        for m in models:
            merged[f"{key}/{m['id']}"] = {}

    cfg.setdefault("agents", {}).setdefault("defaults", {})["models"] = merged

    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")

    # Print summary
    print(f"  Config updated:")
    print(f"    {len(preserved)} direct-provider models")
    for sub_prov in sorted(by_sub.keys()):
        print(f"    {sub_provider_key(sub_prov)}: {len(by_sub[sub_prov])} models")
    print(f"    {len(merged)} total in allowlist")

    # Restart gateway
    print("  Restarting gateway...")
    subprocess.run(["systemctl", "--user", "restart", "openclaw-gateway"], check=False)

    print("  Waiting for gateway to start...")
    for _ in range(30):
        time.sleep(2)
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "openclaw-gateway"],
            capture_output=True, text=True
        )
        if result.stdout.strip() == "active":
            time.sleep(3)
            break
    else:
        print("  WARNING: gateway did not become active in 60s")

    print("  Done.")


if __name__ == "__main__":
    main()
