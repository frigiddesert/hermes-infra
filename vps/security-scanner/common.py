"""Shared helpers for the security-scanner package (heimdall issue #24).

Design constraint (the prime directive, Eric's words): "I don't want to spend even 15 minutes a
week reading security reports with no information I need to act on." So this module's only two
jobs are:
  1. Track what's already known (state/*.json) so a repeat finding NEVER posts again.
  2. When something genuinely new and actionable shows up, POST exactly one fingerprinted event to
     the hub's /ingest — the hub's own dedup (contracts computeFingerprint) then owns recurrence,
     but we don't even rely on that: our state file is the primary noise gate.

stdlib only. Never print/log a secret value.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCANNER_DIR = Path(__file__).resolve().parent
STATE_DIR = SCANNER_DIR / "state"
DEFAULT_HUB_URL = "https://heimdall-hub.eric-c5f.workers.dev"
DEFAULT_SECRETS_FILE = SCANNER_DIR / ".env"
HERMES_ENV_FILE = Path("/root/.hermes/.env")

FIRST_RUN_PAGE_CAP = 5
SEVERITY_RANK = {"critical": 3, "error": 2, "warn": 1, "info": 0}


def log(msg: str) -> None:
    print(f"[security-scanner] {msg}", file=sys.stderr, flush=True)


# --- state (the noise gate) -------------------------------------------------

def load_state(name: str) -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{name}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        log(f"state file {path} unreadable/corrupt — treating as empty")
        return {}


def save_state(name: str, data: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{name}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


def is_first_run() -> bool:
    return not (STATE_DIR / "first_run.flag").exists()


def mark_first_run_done() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "first_run.flag").write_text(f"seeded {int(time.time())}\n")


# --- secret resolution (same discipline as scripts/heimdall-deploy / self-test.sh) -----

def resolve_service_key() -> str | None:
    key = os.environ.get("HEIMDALL_SERVICE_KEY")
    if key:
        return key
    key = _grep_env_file(DEFAULT_SECRETS_FILE, "HEIMDALL_SERVICE_KEY")
    if key:
        return key
    key = _grep_env_file(HERMES_ENV_FILE, "HEIMDALL_SERVICE_KEY")
    if key:
        return key
    return None


def resolve_cf_token() -> str | None:
    """Read-only Cloudflare API token for the posture module. See README for required scopes.
    Absent → cf-posture module is skipped entirely (silent, not an error)."""
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if token:
        return token
    return _grep_env_file(DEFAULT_SECRETS_FILE, "CLOUDFLARE_API_TOKEN")


def resolve_cf_account_id() -> str | None:
    acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if acct:
        return acct
    return _grep_env_file(DEFAULT_SECRETS_FILE, "CLOUDFLARE_ACCOUNT_ID")


def _grep_env_file(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


# --- hub ingest --------------------------------------------------------------

def make_event(app_id: str, event_type: str, severity: str, title: str,
               technical: str = "", context: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "appId": app_id,
        "env": "production",
        "commit": "scanner",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "type": event_type,
        "severity": severity,
        "title": title,
        "technical": technical[:4000] if technical else "",
        "context": context or {},
    }


def post_ingest(events: list[dict[str, Any]], hub_url: str | None = None) -> bool:
    """Best-effort POST /ingest. Never raises; returns whether it succeeded. Never logs the key."""
    if not events:
        return True
    key = resolve_service_key()
    if not key:
        log("no HEIMDALL_SERVICE_KEY found — cannot report to the hub, skipping ingest")
        return False
    url = (hub_url or os.environ.get("HEIMDALL_HUB_URL") or DEFAULT_HUB_URL).rstrip("/") + "/ingest"
    body = json.dumps({"events": events}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"content-type": "application/json", "authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = 200 <= resp.status < 300
            if not ok:
                log(f"ingest returned http {resp.status}")
            return ok
    except urllib.error.HTTPError as e:
        log(f"ingest failed: http {e.code}")
        return False
    except (urllib.error.URLError, OSError) as e:
        log(f"ingest failed: {e}")
        return False


def severity_key(sev: str) -> int:
    return SEVERITY_RANK.get(sev, -1)


def apply_first_run_seeding(new_findings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """First-run behavior (issue #24 task 7): don't flood — page only the FIRST_RUN_PAGE_CAP most
    severe NEW findings across the whole run, seed the rest into state as already-known (so they
    never post later either). Returns (to_page, to_seed_only).
    Caller is responsible for actually writing seeded findings into each module's state before
    marking first_run done.
    """
    ordered = sorted(new_findings, key=lambda f: severity_key(f["event"]["severity"]), reverse=True)
    to_page = ordered[:FIRST_RUN_PAGE_CAP]
    to_seed_only = ordered[FIRST_RUN_PAGE_CAP:]
    return to_page, to_seed_only


def run_cmd(args: list[str], cwd: str | None = None, timeout: int = 120) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except OSError as e:
        return -1, "", str(e)
