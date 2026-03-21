#!/usr/bin/env python3
"""sync-model-catalog.py — Daily model catalog sync from OpenRouter.

Queries OpenRouter API, filters to affordable models (≤$3/M input tokens),
updates:
  1. openclaw.json agents.defaults.models — allowlist for Discord model picker
  2. openclaw.json models.providers.openrouter.models — provider definitions
  3. models.json — patches ALL openrouter model names with price labels

Run with --patch-registry to only do step 3 (used by ExecStartPost).
Run without flags for the full daily sync (steps 1-3 + gateway restart).

Filtering rules:
  - ≤ $3/M input cost, ≥ 32k context
  - Exclude Anthropic
  - Exclude OpenAI models with "pro" in the name
  - Zero LLM tokens used
"""

import json
import sys
import urllib.request
from pathlib import Path

CONFIG_PATH = Path("/root/.openclaw/openclaw.json")
MODELS_JSON_PATH = Path("/root/.openclaw/agents/main/agent/models.json")
OPENROUTER_API = "https://openrouter.ai/api/v1/models"
MAX_INPUT_COST_PER_M = 3.0
MIN_CONTEXT = 32_000

SKIP_PROVIDERS = {"anthropic"}
DIRECT_PROVIDERS = {"bailian", "gemini", "groq", "openai"}

# Cache file so --patch-registry doesn't need to re-fetch OpenRouter
PRICING_CACHE = Path("/root/.openclaw/openrouter-pricing.json")


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


def strip_price_label(name):
    """Remove existing price label from a name."""
    import re
    name = re.sub(r'\s*\(\$[\d.]+/M\)\s*$', '', name)
    name = re.sub(r'\s*\(Free\)\s*$', '', name)
    return name.strip()


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


def build_pricing_map(or_models):
    """Build model_id -> pricing info map and cache it."""
    pricing_map = {}
    for m in or_models:
        pricing = m.get("pricing", {})
        input_per_m = price_per_million(pricing, "prompt")
        pricing_map[m["id"]] = {
            "input_per_m": input_per_m,
            "name": m.get("name", m["id"]),
            "context": m.get("context_length", 0),
        }
    # Cache for --patch-registry
    PRICING_CACHE.write_text(json.dumps(pricing_map, indent=2) + "\n")
    return pricing_map


def load_pricing_cache():
    """Load cached pricing map."""
    if not PRICING_CACHE.exists():
        return None
    return json.loads(PRICING_CACHE.read_text())


def patch_registry(pricing_map):
    """Patch models.json: inject price labels into all openrouter model names."""
    if not MODELS_JSON_PATH.exists():
        print("  models.json not found, skipping registry patch")
        return 0

    registry = json.loads(MODELS_JSON_PATH.read_text())
    providers = registry.get("providers", {})
    or_registry = providers.get("openrouter", {})
    or_models = or_registry.get("models", [])

    patched = 0
    for entry in or_models:
        model_id = entry.get("id", "")
        if model_id not in pricing_map:
            continue
        info = pricing_map[model_id]
        base_name = strip_price_label(entry.get("name", model_id))
        new_name = make_label(base_name, info["input_per_m"])
        if entry.get("name") != new_name:
            entry["name"] = new_name
            patched += 1

    if patched > 0:
        MODELS_JSON_PATH.write_text(json.dumps(registry, indent=2) + "\n")
    print(f"  Registry: patched {patched}/{len(or_models)} model names")
    return patched


def full_sync():
    """Full daily sync: fetch OpenRouter, update config, patch registry."""
    print("Fetching OpenRouter model catalog...")
    or_models = fetch_openrouter_models()
    print(f"  {len(or_models)} models from OpenRouter")

    pricing_map = build_pricing_map(or_models)
    cfg = json.load(CONFIG_PATH.open())

    # Build provider model definitions
    or_model_defs = []
    or_allowlist_keys = []

    for m in or_models:
        model_id = m["id"]
        provider = model_id.split("/")[0] if "/" in model_id else "unknown"
        input_per_m = pricing_map[model_id]["input_per_m"]
        context = m.get("context_length", 0)

        if not should_include(model_id, provider, input_per_m, context):
            continue

        modalities = m.get("architecture", {}).get("input_modalities", [])
        input_types = ["text"]
        if "image" in modalities:
            input_types.append("image")

        base_name = m.get("name", model_id)
        pricing = m.get("pricing", {})
        output_per_m = price_per_million(pricing, "completion")
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

        or_model_defs.append(entry)
        or_allowlist_keys.append(f"openrouter/{model_id}")

    print(f"  {len(or_model_defs)} models pass filters")

    # Update openrouter provider models
    or_provider = cfg.setdefault("models", {}).setdefault("providers", {}).setdefault("openrouter", {})
    or_provider["models"] = or_model_defs

    # Update allowlist
    existing_models = cfg.get("agents", {}).get("defaults", {}).get("models", {})
    preserved = {k: v for k, v in existing_models.items() if k.split("/")[0] in DIRECT_PROVIDERS}
    merged = dict(preserved)
    for key in or_allowlist_keys:
        merged[key] = {}
    cfg.setdefault("agents", {}).setdefault("defaults", {})["models"] = merged

    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"  Config: {len(preserved)} direct + {len(or_model_defs)} OpenRouter = {len(merged)} total")

    # Restart gateway, wait for it to regenerate models.json, then patch
    import subprocess
    import time
    print("  Restarting gateway...")
    subprocess.run(["systemctl", "--user", "restart", "openclaw-gateway"], check=False)

    # Wait for gateway to be ready (it regenerates models.json on startup)
    print("  Waiting for gateway to start...")
    for _ in range(30):
        time.sleep(2)
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "openclaw-gateway"],
            capture_output=True, text=True
        )
        if result.stdout.strip() == "active":
            # Give it a moment to finish writing models.json
            time.sleep(3)
            break
    else:
        print("  WARNING: gateway did not become active in 60s")

    # Now patch the freshly generated models.json
    patch_registry(pricing_map)
    print("  Done. Gateway is running with price-labeled models.")


def patch_only():
    """Post-start hook: patch models.json with cached pricing data."""
    pricing_map = load_pricing_cache()
    if not pricing_map:
        print("  No pricing cache found, fetching fresh...")
        or_models = fetch_openrouter_models()
        pricing_map = build_pricing_map(or_models)
    patch_registry(pricing_map)


if __name__ == "__main__":
    if "--patch-registry" in sys.argv:
        patch_only()
    else:
        full_sync()
