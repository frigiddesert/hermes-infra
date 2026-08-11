#!/usr/bin/env python3
"""
Model Scout — live OpenRouter filter for recently released, low-output-cost models.

Default behavior matches Eric's chooser rule:
- released in the last 4 months
- OpenRouter completion price strictly under $3.00 per 1M output tokens
- text-output models only
- sorted newest first so the newest qualifying model is always at the top

Outputs:
- output/recent-cheap-models-YYYY-MM-DD.md
- output/recent-cheap-models-YYYY-MM-DD.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OUTPUT_DIR = Path("/root/workspace/model-researcher/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def parse_price(value: Any) -> Optional[float]:
    """Return OpenRouter per-token price as a float, or None when unknown."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-1":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def per_million(token_price: Optional[float]) -> Optional[float]:
    if token_price is None:
        return None
    return token_price * 1_000_000


def fmt_money(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    if value == 0:
        return "$0.00"
    return f"${value:,.2f}"


def fmt_token_price(token_price: Optional[float]) -> str:
    if token_price is None:
        return "unknown"
    if token_price == 0:
        return "$0.000000"
    return f"${token_price:.9f}"


def fmt_age(ts: int) -> str:
    now = time.time()
    seconds = max(0, now - int(ts))
    days = seconds / 86400
    if days < 1:
        return "today"
    if days < 30:
        return f"{int(days)}d"
    months = days / 30.4375
    if months < 12:
        return f"{months:.1f}mo"
    return f"{months / 12:.1f}y"


def fmt_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def fmt_context(ctx: Any) -> str:
    try:
        value = int(ctx or 0)
    except (TypeError, ValueError):
        return "unknown"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}K"
    return str(value)


# ── Value Leaders scoring ─────────────────────────────────────────────────
def _estimate_iq(model_id: str, raw_benchmarks: Any) -> float:
    """Best-effort intelligence estimate.

    Uses live Artificial Analysis intelligence_index when present (raw 0–~55 scale
    where 50 ≈ frontier flash tier). Falls back to a conservative tier estimate
    so day-0 models like Solar Pro 4 still rank.
    """
    try:
        aa = (raw_benchmarks or {}).get("artificial_analysis") or {}
        val = aa.get("intelligence_index")
        if val is not None:
            return float(val)
    except Exception:
        pass
    low = model_id.lower()
    if any(x in low for x in ["opus", "gpt-5", "grok-4", "405b", "ultra-550b"]):
        return 52.0
    if any(x in low for x in ["solar-pro4", "solar-pro-3", "k2.6", "k2.5", "maverick"]):
        return 50.0
    if any(x in low for x in ["sonnet", "pro", "max", "70b", "3.7-plus", "3.6-plus"]):
        return 48.0
    if any(x in low for x in ["flash", "mini", "haiku", "small", "air", "8b", "7b", "nemo", "tiny"]):
        return 38.0
    return 42.0


def value_score(model: Dict[str, Any]) -> Optional[float]:
    """Intelligence per dollar — higher is better value.

    Score = IQ ÷ $/M out.  Free models use IQ×10 so they top the combined
    ranking without completely burying the best paid value story.
    """
    cpm = model.get("completion_per_m")
    if cpm is None:
        return None
    iq = model.get("_est_iq")
    if iq is None:
        iq = _estimate_iq(model["id"], model.get("_raw_benchmarks"))
    if cpm == 0:
        return float(iq) * 10
    return float(iq) / float(cpm)


def get_value_leaders(models: List[Dict[str, Any]], top_n: int = 10, paid_only: bool = False) -> List[Dict[str, Any]]:
    """Rank models by value_score descending."""
    scored = []
    for m in models:
        if paid_only and (m.get("completion_per_m") or 0) == 0:
            continue
        vs = value_score(m)
        if vs is None:
            continue
        scored.append((vs, m))
    scored.sort(key=lambda x: (-x[0], -int(x[1].get("created") or 0), float(x[1].get("completion_per_m") or 0)))
    return [m for _, m in scored[:top_n]]


def is_text_output_model(item: Dict[str, Any]) -> bool:
    """Keep chat-capable text-output models; exclude image/video/TTS-only models."""
    text = " ".join([
        str(item.get("id") or ""),
        str(item.get("name") or ""),
        str(item.get("description") or ""),
    ]).lower()
    blocked_terms = [
        "content safety", "moderation", "guardrail", "embed", "rerank",
        "text-to-speech", "speech-to-text", "music generation", "audio generation",
        "image generation", "video generation",
    ]
    if any(term in text for term in blocked_terms):
        return False

    architecture = item.get("architecture") or {}
    if isinstance(architecture, dict):
        output_modalities = architecture.get("output_modalities") or []
        modality = str(architecture.get("modality") or "").lower()
        if output_modalities:
            normalized_outputs = {str(x).lower() for x in output_modalities}
            return normalized_outputs == {"text"}
        if modality and "text->text" in modality:
            return True
    return True


def fetch_openrouter_models(timeout: float = 30.0) -> List[Dict[str, Any]]:
    req = Request(OPENROUTER_MODELS_URL, headers={"User-Agent": "HermesModelScout/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"OpenRouter returned HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach OpenRouter model catalog: {exc}") from exc

    raw_models = payload.get("data", [])
    if not isinstance(raw_models, list):
        raise RuntimeError("OpenRouter model catalog response did not contain a data list")

    enriched: List[Dict[str, Any]] = []
    now = time.time()
    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        model_id = str(raw.get("id") or "").strip()
        if not model_id or model_id.startswith("openrouter/"):
            continue
        if not is_text_output_model(raw):
            continue

        created = raw.get("created")
        try:
            created_ts = int(created)
        except (TypeError, ValueError):
            continue

        pricing = raw.get("pricing") or {}
        if not isinstance(pricing, dict):
            pricing = {}
        prompt_token = parse_price(pricing.get("prompt"))
        completion_token = parse_price(pricing.get("completion"))
        completion_per_m = per_million(completion_token)
        prompt_per_m = per_million(prompt_token)

        raw_benchmarks = raw.get("benchmarks") or {}
        est_iq = _estimate_iq(model_id, raw_benchmarks)

        enriched.append({
            "id": model_id,
            "name": raw.get("name") or model_id,
            "description": raw.get("description") or "",
            "created": created_ts,
            "created_iso": datetime.fromtimestamp(created_ts, tz=timezone.utc).isoformat(),
            "age": fmt_age(created_ts),
            "age_days": (now - created_ts) / 86400,
            "context_length": raw.get("context_length"),
            "context_label": fmt_context(raw.get("context_length")),
            "pricing": pricing,
            "prompt_per_token": prompt_token,
            "completion_per_token": completion_token,
            "prompt_per_m": prompt_per_m,
            "completion_per_m": completion_per_m,
            "top_provider": raw.get("top_provider") or {},
            "architecture": raw.get("architecture") or {},
            "canonical_slug": raw.get("canonical_slug") or "",
            "_est_iq": est_iq,
            "_raw_benchmarks": raw_benchmarks,
        })

    return enriched


def filter_models(
    models: List[Dict[str, Any]],
    months: float,
    max_output_per_m: float,
) -> List[Dict[str, Any]]:
    cutoff = time.time() - (months * 30.4375 * 86400)
    filtered = []
    for model in models:
        created = int(model.get("created") or 0)
        completion_per_m = model.get("completion_per_m")
        if created < cutoff:
            continue
        if completion_per_m is None:
            continue
        if completion_per_m >= max_output_per_m:
            continue
        filtered.append(model)
    filtered.sort(key=lambda m: (-int(m["created"]), float(m["completion_per_m"] or 0), m["id"]))
    return filtered


def markdown_table(models: List[Dict[str, Any]], max_output_per_m: float, months: float) -> str:
    lines = [
        "# OpenRouter Model Scout",
        "",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Filter: released in last {months:g} months, output cost < {fmt_money(max_output_per_m)}/1M tokens",
        f"Qualifying models: {len(models)}",
        "",
        "| Rank | Model | Released | Age | Output $/M | Input $/M | Context | Name |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for idx, model in enumerate(models, 1):
        lines.append(
            f"| {idx} | `{model['id']}` | {fmt_date(model['created'])} | {model['age']} | "
            f"{fmt_money(model['completion_per_m'])} | {fmt_money(model['prompt_per_m'])} | "
            f"{model['context_label']} | {model['name']} |"
        )
    lines.extend([
        "",
        "## Notes",
        "",
        "- OpenRouter reports pricing per token; this report converts completion price to dollars per 1M output tokens.",
        "- Models with unknown completion pricing are excluded from the strict cost filter.",
        "- Sorted newest first, then cheapest output, so the newest qualifying models stay at the top.",
        "",
    ])
    return "\n".join(lines)


def value_leaders_table(models: List[Dict[str, Any]], title: str = "Value Leaders — Intelligence per Dollar", paid_only: bool = False) -> str:
    """Markdown table ranking models by IQ/$ — mirrors OpenRouter /discover Value Leaders."""
    leaders = get_value_leaders(models, top_n=10, paid_only=paid_only)
    scope = "paid models only" if paid_only else "all qualifying (free top)"
    lines = [
        f"## {title}",
        "",
        f"> Higher = more intelligence per dollar.  Score = AA intelligence_index ÷ $/M out ({scope}).  Free models score IQ×10.",
        "",
        "| Rank | Model | Out $/M | AA IQ | IQ/$ | Context | Age |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for idx, m in enumerate(leaders, 1):
        vs = value_score(m)
        vs_str = f"{vs:,.0f}" if vs else "—"
        lines.append(
            f"| {idx} | `{m['id']}` | {fmt_money(m['completion_per_m'])} | {m.get('_est_iq', '—')} | {vs_str} | {m['context_label']} | {m['age']} |"
        )
    lines += ["", f"_Source: OpenRouter live pricing + Artificial Analysis intelligence_index (est. when missing).  Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}._", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Filter OpenRouter models by release age and output price.")
    parser.add_argument("--months", type=float, default=4.0, help="Release window in months; default: 4")
    parser.add_argument("--max-output-per-m", type=float, default=3.00, help="Maximum output price in $/1M tokens; default: 3.00")
    parser.add_argument("--limit", type=int, default=0, help="Optional row limit for quick terminal output; default: all")
    parser.add_argument("--json", action="store_true", help="Also print compact JSON to stdout")
    args = parser.parse_args(argv)

    if args.max_output_per_m <= 0:
        raise SystemExit("--max-output-per-m must be positive")
    if args.months <= 0:
        raise SystemExit("--months must be positive")

    try:
        all_models = fetch_openrouter_models()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    filtered = filter_models(all_models, args.months, args.max_output_per_m)
    shown = filtered if not args.limit else filtered[: args.limit]

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md_path = OUTPUT_DIR / f"recent-cheap-models-{timestamp}.md"
    json_path = OUTPUT_DIR / f"recent-cheap-models-{timestamp}.json"

    md_path.write_text(markdown_table(filtered, args.max_output_per_m, args.months), encoding="utf-8")
    json_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter": {
            "months": args.months,
            "max_output_per_m": args.max_output_per_m,
            "max_output_per_token": args.max_output_per_m / 1_000_000,
        },
        "total_models_scanned": len(all_models),
        "qualifying_models": len(filtered),
        "models": filtered,
    }, indent=2), encoding="utf-8")

    print(f"Fetched {len(all_models)} text-output OpenRouter models")
    print(f"Qualifying: {len(filtered)} released in last {args.months:g} months with output < ${args.max_output_per_m:.2f}/M")
    print(f"Markdown: {md_path}")
    print(f"JSON: {json_path}")
    print()

    if shown:
        for idx, model in enumerate(shown, 1):
            print(
                f"{idx:2d}. {model['id']} | {fmt_date(model['created'])} | {model['age']} | "
                f"out {fmt_money(model['completion_per_m'])}/M | in {fmt_money(model['prompt_per_m'])}/M | "
                f"ctx {model['context_label']} | {model['name']}"
            )
    else:
        print("No models matched the filter.")

    if args.json:
        print(json.dumps({
            "total_models_scanned": len(all_models),
            "qualifying_models": len(filtered),
            "models": filtered,
        }, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
