#!/usr/bin/env python3
"""
sync_models_to_hermes.py — Run AFTER fetch_models_v2.py each week.
Reads the latest model briefing output and updates Hermes Web UI + CLI pickers
with a curated list: FREE, cheap, strategic, expensive, premium models only.
"""

import json
import sys
from pathlib import Path

OUTPUT_DIR = Path("/root/workspace/model-researcher/output")
MODELS_CACHE = Path("/root/.hermes/webui/models_cache.json")
STATE_FILE = Path("/root/workspace/model-researcher/picker_state.json")

# ─── TIER BOUNDARIES ──────────────────────────────────────────────────────
CHEAP_THRESHOLD = 0.50
STRATEGIC_THRESHOLD = 2.00
EXPENSIVE_THRESHOLD = 5.00

def cost_tier(cost: float) -> str:
    if cost == 0.0 or cost < 0.001:
        return "free"
    if cost < CHEAP_THRESHOLD:
        return "cheap"
    if cost < STRATEGIC_THRESHOLD:
        return "strategic"
    if cost < EXPENSIVE_THRESHOLD:
        return "expensive"
    return "premium"

def make_label(model_id: str, cost: float, tier: str, new: bool = False, date_str: str = "") -> str:
    """Create a clean, scannable label for the picker with discovery date."""
    new_tag = " ⭐NEW" if new else ""
    date_tag = f" ⏱{date_str}" if date_str else ""
    if tier == "free":
        return f"{model_id} (FREE){date_tag}{new_tag}"
    return f"{model_id} (${cost:.2f}/M, {tier}){date_tag}{new_tag}"

def load_all_models() -> list:
    """Load all models from the latest model-data JSON file."""
    data_files = sorted(OUTPUT_DIR.glob("model-data-*.json"))
    if not data_files:
        print("No model data files found", file=sys.stderr)
        return []
    
    latest = data_files[-1]
    with open(latest) as f:
        data = json.load(f)
    
    models = data.get("models", data.get("new_models", []))
    if not models:
        # Fall back to state.json for all known models
        state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"known_model_ids": []}
        return [{"id": mid, "_out_cost_per_m": 0} for mid in state.get("known_model_ids", [])]
    return models

def load_from_catalog() -> list:
    """Fetch the full OpenRouter catalog to get pricing for all models."""
    import requests
    try:
        resp = requests.get("https://openrouter.ai/api/v1/models", timeout=30)
        resp.raise_for_status()
        data = resp.json()
        models = []
        for m in data.get("data", []):
            pricing = m.get("pricing", {})
            prompt = float(pricing.get("prompt", "0"))
            completion = float(pricing.get("completion", "0"))
            out_cost = completion * 1_000_000  # per 1M tokens
            models.append({
                "id": m["id"],
                "name": m.get("name", m["id"]),
                "context": m.get("context_length", 0),
                "cost_input": prompt,
                "cost_output": out_cost,
                "created": m.get("created", 0),
            })
        return models
    except Exception as e:
        print(f"Failed to fetch catalog: {e}", file=sys.stderr)
        return []

def build_curated_list(models: list, existing_ids: set) -> list:
    """Build the curated picker list from catalog models."""
    from datetime import datetime
    curated = []
    now = datetime.utcnow()
    date_str = now.strftime("%b %d")
    
    for m in models:
        mid = m["id"]
        cost = m.get("cost_output", 999)
        tier = cost_tier(cost)
        is_new = mid not in existing_ids
        
        # Skip models we'd never pick:
        # - models with no pricing data (unknown cost)
        # - routing/auto models
        # - old deprecated models
        # - premium models
        # - anthropic/claude models
        if "auto" in mid.lower() and ("router" in mid.lower() or mid == "openrouter/auto"):
            continue
        if "fusion" in mid.lower() or "pareto" in mid.lower():
            pass  # Keep routing models that are useful
        if tier == "premium":
            continue
        if 'anthropic/' in mid.lower() or 'claude/' in mid.lower():
            continue
        
        curated.append({
            "id": mid,
            "label": make_label(mid, cost, tier, is_new, date_str),
            "cost": round(cost, 4),
            "tier": tier,
        })
    
    # Sort: FREE first, then by ascending cost
    curated.sort(key=lambda m: (0 if m["tier"] == "free" else 1, m["cost"]))
    return curated

def update_pickers(curated: list, default_model: str):
    """Write the curated list to Web UI cache, preserving non-OpenRouter groups."""
    from datetime import datetime, timezone
    import os
    
    free_models = [m for m in curated if m["tier"] == "free"]
    cheap_models = [m for m in curated if m["tier"] == "cheap"]
    strategic_models = [m for m in curated if m["tier"] == "strategic"]
    expensive_models = [m for m in curated if m["tier"] == "expensive"]
    premium_models = [m for m in curated if m["tier"] == "premium"]
    
    # Preserve existing non-OpenRouter groups (Copilot, DeepSeek, Codex) - filter out Anthropic/Claude
    existing_groups = []
    if MODELS_CACHE.exists():
        try:
            old = json.loads(MODELS_CACHE.read_text())
            for g in old.get("groups", []):
                if g.get("provider_id") != "openrouter":
                    # Filter out Anthropic/Claude models from preserved groups
                    if "models" in g:
                        g["models"] = [m for m in g["models"] 
                                     if not ('anthropic/' in m.get('id','').lower() or 'claude/' in m.get('id','').lower())]
                    existing_groups.append(g)
        except Exception:
            pass
    
    groups = [
        {
            "provider": "OpenRouter (Curated)",
            "provider_id": "openrouter",
            "models": free_models + cheap_models + strategic_models,
            "extra_models": expensive_models + premium_models,
        },
    ] + existing_groups
    
    cache = {
        "_schema_version": 3,
        "_updated_by": "model-researcher-sync",
        "_last_sync": datetime.now(timezone.utc).isoformat(),
        "active_provider": "openrouter",
        "default_model": default_model,
        "configured_model_badges": {
            default_model: {
                "role": "primary",
                "label": "Primary",
                "provider": "openrouter"
            }
        },
        "groups": groups,
        "_tier_counts": {
            "free": len(free_models),
            "cheap": len(cheap_models),
            "strategic": len(strategic_models),
            "expensive": len(expensive_models),
            "premium": len(premium_models),
            "total": len(curated),
        },
        "_webui_version": "v0.52.41"
    }
    
    MODELS_CACHE.write_text(json.dumps(cache, indent=2))
    
    # Track what we've seen
    state = {"last_sync": datetime.now(timezone.utc).isoformat(), "model_ids": [m["id"] for m in curated]}
    STATE_FILE.write_text(json.dumps(state, indent=2))
    
    # Restart the curated proxy to pick up new models
    print("  Restarting curated proxy on :4000...")
    os.system("fuser -k 4000/tcp 2>/dev/null; sleep 1")
    os.system("cd /root/.hermes && nohup python3 curated_proxy.py > /tmp/curated_proxy.log 2>&1 &")
    
    print(f"  FREE:      {len(free_models)}")
    print(f"  Cheap:     {len(cheap_models)}")
    print(f"  Strategic: {len(strategic_models)}")
    print(f"  Expensive: {len(expensive_models)}")
    print(f"  Premium:   {len(premium_models)}")
    print(f"  TOTAL:     {len(curated)}")
    print(f"\n  Default:   {default_model}")
    print(f"  Cache:     {MODELS_CACHE}")

def main():
    print("Syncing curated model list to Hermes pickers...")
    
    # Load the full OpenRouter catalog
    print("Fetching OpenRouter catalog...")
    models = load_from_catalog()
    if not models:
        print("ERROR: Could not load models", file=sys.stderr)
        sys.exit(1)
    print(f"  Catalog: {len(models)} models")
    
    # Load existing state to track what's new
    existing_ids = set()
    if STATE_FILE.exists():
        existing_ids = set(json.loads(STATE_FILE.read_text()).get("model_ids", []))
    print(f"  Previously tracked: {len(existing_ids)} models")
    
    # Build curated list
    curated = build_curated_list(models, existing_ids)
    
    # Determine default: prefer FREE model with coding capability
    default = "poolside/laguna-s-2.1:free"
    update_pickers(curated, default)
    
    print("\n✓ Done. Web UI (port 8787) and CLI TUI now show curated model list.")
    print("  Restart your CLI TUI session to see the updated list.")

if __name__ == "__main__":
    main()