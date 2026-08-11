#!/usr/bin/env python3
"""
Model Researcher v2 — Only reports NEW models published since last run.
Tracks state in state.json to diff models between runs.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import requests

sys.path.insert(0, str(Path(__file__).parent))
from fix_categorization import get_model_cost, categorize_model
from model_scout import (
    fetch_openrouter_models as fetch_scout_openrouter_models,
    filter_models as filter_scout_models,
    markdown_table as scout_markdown_table,
    value_leaders_table as scout_value_leaders_table,
    get_value_leaders as scout_get_value_leaders,
)

# ─── CONFIG ──────────────────────────────────────────────────────────────
OUTPUT_DIR = Path("/root/workspace/model-researcher/output")
STATE_FILE = Path("/root/workspace/model-researcher/state.json")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CHEAP_THRESHOLD = 0.50
STRATEGIC_THRESHOLD = 3.00
EXPENSIVE_THRESHOLD = 10.00

CATEGORIES = ["coding", "reasoning", "writing", "analysis", "general"]

# ─── STATE MANAGEMENT ────────────────────────────────────────────────────
def load_state() -> Dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"known_model_ids": set(), "last_run": None, "run_count": 0}

def save_state(state: Dict):
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    state["run_count"] = state.get("run_count", 0) + 1
    STATE_FILE.write_text(json.dumps(state, indent=2, default=list))

# ─── FETCHERS ────────────────────────────────────────────────────────────
def fetch_openrouter() -> List[Dict]:
    url = "https://openrouter.ai/api/v1/models"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    models = data.get("data", [])
    for m in models:
        m["_source"] = "openrouter"
        m["_model_id"] = m.get("id", "")
        m["_name"] = m.get("name", m.get("id", ""))
        m["_description"] = m.get("description", "")
        m["_context"] = m.get("context_length", 0)
        m["_out_cost_per_m"] = get_model_cost(m["_model_id"])
        m["_categories"] = categorize_model(m["_model_id"], m["_description"])
        m["_created"] = m.get("created", 0)  # Unix timestamp of model creation
    return models

# ─── DIFFING ─────────────────────────────────────────────────────────────
def find_new_models(models: List[Dict], state: Dict) -> List[Dict]:
    """Return only models not seen in previous runs."""
    known = state.get("known_model_ids", set())
    if isinstance(known, list):
        known = set(known)
    new = [m for m in models if m["_model_id"] not in known]
    return new

def update_known_models(models: List[Dict], state: Dict):
    """Add all current model IDs to known set."""
    current_ids = {m["_model_id"] for m in models}
    known = state.get("known_model_ids", set())
    if isinstance(known, list):
        known = set(known)
    state["known_model_ids"] = list(known | current_ids)

# ─── COST TIER ───────────────────────────────────────────────────────────
def cost_tier(cost_per_m: float) -> str:
    if cost_per_m < CHEAP_THRESHOLD: return "cheap"
    elif cost_per_m < STRATEGIC_THRESHOLD: return "strategic"
    elif cost_per_m < EXPENSIVE_THRESHOLD: return "expensive"
    else: return "premium"

# ─── REPORT ──────────────────────────────────────────────────────────────
def generate_report(new_models: List[Dict], total_models: int, state: Dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%B %d, %Y")
    
    if not new_models:
        lines = [
            f"# Model Researcher — {ts}",
            "",
            f"> **Run #{state.get('run_count', 0) + 1}** | {datetime.now(timezone.utc).isoformat()}",
            "",
            "## No New Models This Week",
            "",
            f"No new models have been published on OpenRouter since the last report. Currently tracking **{total_models} models**.",
            "",
            f"*Next run: in 7 days.*",
        ]
        return "\n".join(lines)
    
    # Categorize new models
    enriched = []
    for m in new_models:
        m["_cost_tier"] = cost_tier(m["_out_cost_per_m"])
        enriched.append(m)
    
    lines = [
        f"# Model Researcher — {ts}",
        "",
        f"> **Run #{state.get('run_count', 0) + 1}** | {datetime.now(timezone.utc).isoformat()}",
        f"> **New models this week:** {len(new_models)}",
        f"> **Total tracked:** {total_models}",
        f"> **Sources:** OpenRouter, DeepSeek, OpenAI API",
        "",
        "## New Models This Week",
        "",
    ]
    
    # Group by cost tier
    for tier_name, tier_label in [("cheap", f"<${CHEAP_THRESHOLD:.2f}/M"), ("strategic", f"${CHEAP_THRESHOLD:.2f}–${STRATEGIC_THRESHOLD:.2f}/M"), ("expensive", f"${STRATEGIC_THRESHOLD:.2f}–${EXPENSIVE_THRESHOLD:.2f}/M"), ("premium", f">${EXPENSIVE_THRESHOLD:.2f}/M")]:
        tier_models = [m for m in enriched if m["_cost_tier"] == tier_name]
        if tier_models:
            lines.append(f"### {tier_label} ({len(tier_models)} models)")
            for m in tier_models:
                cats = ", ".join(m["_categories"])
                lines.append(f"- **`{m['_model_id']}`** — {m['_context']:,} ctx | {cats}")
                if m.get("_description"):
                    lines.append(f"  - {m['_description'][:150]}...")
    
    # Coding models specifically
    coding_new = [m for m in enriched if "coding" in m["_categories"]]
    if coding_new:
        lines.append("")
        lines.append("## New Coding Models")
        for m in coding_new:
            lines.append(f"- **`{m['_model_id']}`** — ${m['_out_cost_per_m']:.4f}/M out, {m['_context']:,} ctx | {m['_cost_tier']} tier")
    
    # Writing models specifically
    writing_new = [m for m in enriched if "writing" in m["_categories"]]
    if writing_new:
        lines.append("")
        lines.append("## New Writing Models")
        for m in writing_new:
            lines.append(f"- **`{m['_model_id']}`** — ${m['_out_cost_per_m']:.4f}/M out, {m['_context']:,} ctx | {m['_cost_tier']} tier")
    
    lines.extend([
        "",
        "---",
        f"*Next run: in 7 days. State file tracks {total_models} known models.*",
    ])
    
    return "\n".join(lines)

# ─── MAIN ────────────────────────────────────────────────────────────────
def main():
    state = load_state()
    
    print(f"Run #{state.get('run_count', 0) + 1} — fetching models...")
    models = fetch_openrouter()
    print(f"Fetched {len(models)} models from OpenRouter")
    
    try:
        scout_models = fetch_scout_openrouter_models()
        scout_filtered = filter_scout_models(scout_models, months=4, max_output_per_m=3.00)
        scout_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        scout_report_path = OUTPUT_DIR / f"recent-cheap-models-{scout_timestamp}.md"
        scout_json_path = OUTPUT_DIR / f"recent-cheap-models-{scout_timestamp}.json"
        # Value Leaders — IQ per dollar (mirrors OpenRouter /discover)
        leaders_table = scout_value_leaders_table(scout_filtered)
        leaders = scout_get_value_leaders(scout_filtered, top_n=10)
        scout_report_text = scout_markdown_table(scout_filtered, max_output_per_m=3.00, months=4) + "\n" + leaders_table
        scout_report_path.write_text(scout_report_text, encoding="utf-8")
        # Also write dedicated value-leaders file for easy linking
        vl_path = OUTPUT_DIR / f"value-leaders-{scout_timestamp}.md"
        vl_path.write_text(leaders_table, encoding="utf-8")
        scout_json_path.write_text(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "filter": {"months": 4, "max_output_per_m": 3.00},
            "total_models_scanned": len(scout_models),
            "qualifying_models": len(scout_filtered),
            "models": scout_filtered,
            "value_leaders": leaders,
        }, indent=2, default=str), encoding="utf-8")
        print(f"Model Scout refresh: {len(scout_filtered)} qualifying recent/cheap models")
        print(f"Model Scout report: {scout_report_path}")
        print(f"Value Leaders: {vl_path} (top: {leaders[0]['id'] if leaders else 'none'})")
    except Exception as exc:
        print(f"Model Scout refresh skipped: {exc}")
    
    new_models = find_new_models(models, state)
    print(f"New models since last run: {len(new_models)}")
    
    report = generate_report(new_models, len(models), state)
    
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = OUTPUT_DIR / f"new-models-{timestamp}.md"
    report_path.write_text(report)
    (OUTPUT_DIR / "latest-briefing.md").write_text(report)
    (OUTPUT_DIR / f"model-data-{timestamp}.json").write_text(json.dumps({
        "total": len(models), "new": len(new_models), "new_models": new_models
    }, indent=2, default=str))
    
    # Update state AFTER successful run
    update_known_models(models, state)
    save_state(state)
    
    print(f"Report: {report_path} ({len(report)} chars)")
    print(f"State updated. Known models: {len(state['known_model_ids'])}")
    print("\n" + "="*60)
    print(report)

if __name__ == "__main__":
    main()