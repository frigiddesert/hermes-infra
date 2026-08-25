#!/usr/bin/env python3
"""
Entrypoint for the PVE security scan (heimdall issue #35).

Runs both modules (sweep + posture), applies first-run seeding
(cap the very first run's paging at the 5 most severe findings;
everything else is seeded into state as known — see common.apply_first_run_seeding),
posts exactly one event per genuinely new actionable finding to the hub,
and otherwise produces NO human-visible output.

Intended to run from system crontab:
- sweep.py: every 30 minutes
- posture.py: weekly (e.g., Sunday 03:00)

Usage:
  python3 run_scan.py sweep    # run log sweep
  python3 run_scan.py posture  # run config drift check
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sweep
import posture
from common import is_first_run, mark_first_run_done


def main() -> int:
    parser = argparse.ArgumentParser(description="PVE security scanner")
    parser.add_argument("mode", choices=["sweep", "posture"], help="Which module to run")
    parser.add_argument("--dry-run", action="store_true", help="Run without posting to hub")
    args = parser.parse_args()

    if args.mode == "sweep":
        return sweep.main()
    elif args.mode == "posture":
        return posture.main()
    else:
        print("Invalid mode", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())