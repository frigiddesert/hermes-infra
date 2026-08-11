#!/usr/bin/env python3
"""Entrypoint for the weekly security scan (heimdall issue #24 + Cloudflare posture).

Runs all three modules, applies first-run seeding (cap the very first run's paging at the 5 most
severe findings; everything else is seeded into state as known — see common.apply_first_run_seeding),
posts exactly one event per genuinely new actionable finding to the hub, and otherwise produces NO
human-visible output. Intended to run from system crontab, weekly.

Usage: python3 run_scan.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cf_posture
import dependency_scan
import secret_scan
from common import (
    SCANNER_DIR,
    apply_first_run_seeding,
    is_first_run,
    load_state,
    log,
    mark_first_run_done,
    post_ingest,
    save_state,
)
from config import load_repos


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="don't POST to the hub, just report what would page")
    args = parser.parse_args()

    repos = load_repos()
    first_run = is_first_run()
    if first_run:
        log("first run detected — will seed all but the 5 most severe new findings into state without paging")

    all_new: list[dict] = []

    dep_state = load_state("dependency")
    dep_new, dep_state = dependency_scan.run(repos, dep_state)
    all_new += [{"module": "dependency", **w} for w in dep_new]

    sec_state = load_state("secrets")
    sec_new, sec_state = secret_scan.run(repos, sec_state)
    all_new += [{"module": "secret", **w} for w in sec_new]

    cf_state = load_state("cf_posture")
    cf_orphans = load_state("cf_orphans")
    baseline = json.loads((SCANNER_DIR / "posture-baseline.json").read_text())
    registered_apps = json.loads((SCANNER_DIR / "apps-registry.json").read_text())["apps"]
    cf_new, cf_state, cf_orphans = cf_posture.run(cf_state, cf_orphans, baseline, registered_apps)
    all_new += [{"module": "cf_posture", **w} for w in cf_new]

    if not all_new:
        log("scan complete — nothing new")
        if not args.dry_run:
            _persist(dep_state, sec_state, cf_state, cf_orphans)
            if first_run:
                mark_first_run_done()
        return 0

    if first_run:
        to_page, to_seed_only = apply_first_run_seeding(all_new)
        log(f"first-run seeding: {len(to_page)} paged, {len(to_seed_only)} seeded silently (known from now on)")
    else:
        to_page, to_seed_only = all_new, []

    if args.dry_run:
        # Preview only — NEVER mutate state or the first-run flag, so a dry-run can be re-run
        # freely and a real run afterwards still sees a genuinely fresh first-run seeding decision.
        for w in to_page:
            log(f"[dry-run] would page: {w['event']['title']}")
        log(f"[dry-run] would seed silently (no page): {len(to_seed_only)}")
        return 0

    events = [w["event"] for w in to_page]
    ok = post_ingest(events) if events else True
    if not ok:
        log("WARNING: ingest failed for one or more new findings — they remain unmarked so the next run retries them")
        # Don't mark as seen — retry next run.
        to_page = []

    # Mark everything we decided on (paged + seeded) as known, per its own module's state, so it
    # never posts again regardless of outcome above (except the ingest-failed retry case).
    module_states = {"dependency": dep_state, "secret": sec_state, "cf_posture": cf_state}
    for w in to_page + to_seed_only:
        state = module_states[w["module"]]
        if w["module"] == "cf_posture":
            state.setdefault("drift_seen", [])
            if w["key"] not in state["drift_seen"]:
                state["drift_seen"].append(w["key"])
        else:
            state[w["key"]] = {"first_seen": w["event"]["ts"], "severity": w["event"]["severity"], "title": w["event"]["title"]}

    _persist(dep_state, sec_state, cf_state, cf_orphans)
    if first_run:
        mark_first_run_done()

    log(f"scan complete — {len(to_page)} paged, {len(all_new) - len(to_page)} accumulated silently")
    return 0


def _persist(dep_state, sec_state, cf_state, cf_orphans) -> None:
    save_state("dependency", dep_state)
    save_state("secrets", sec_state)
    save_state("cf_posture", cf_state)
    save_state("cf_orphans", cf_orphans)


if __name__ == "__main__":
    sys.exit(main())
