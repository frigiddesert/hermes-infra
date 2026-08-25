"""Shared helpers for the pve-scanner package (heimdall issue #35)."""
from __future__ import annotations

import json
import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Any

SCANNER_DIR = Path(__file__).resolve().parent
STATE_DIR = SCANNER_DIR / "state"
DEFAULT_HUB_URL = "https://heimdall-hub.eric-c5f.workers.dev"
DEFAULT_SECRETS_FILE = SCANNER_DIR / ".env"
HERMES_ENV_FILE = Path("/root/.hermes/.env")

FIRST_RUN_PAGE_CAP = 5
SEVERITY_RANK = {"critical": 3, "error": 2, "warn": 1, "info": 0}

PVE_HOSTS = {
    "pve-2": "100.126.92.41",
    "mosthutte": "100.113.108.73",
}

SSH_TIMEOUT = 10
SSH_USER = "root"


def log(msg: str) -> None:
    print(f"[pve-scanner] {msg}", file=sys.stderr, flush=True)


# --- state (the noise gate) -------------------------------------------------

def load_state(name: str) -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{name}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception as e:
            log(f"failed to load {name} state: {e}")
            return {}
    return {}


def save_state(name: str, data: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{name}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def is_first_run() -> bool:
    return not (STATE_DIR / "first_run.flag").exists()


def mark_first_run_done() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "first_run.flag").write_text(f"seeded {int(time.time())}\n")


# --- secret resolution -------------------------------------------------------

def resolve_service_key() -> str | None:
    key = os.environ.get("HEIMDALL_SERVICE_KEY")
    if key:
        return key
    # read from .env (KEY=value lines, no export/source)
    if DEFAULT_SECRETS_FILE.exists():
        for line in DEFAULT_SECRETS_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("HEIMDALL_SERVICE_KEY="):
                return line.split("=", 1)[1].strip()
            if line.startswith("export HEIMDALL_SERVICE_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def _grep_env_file(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


# --- hub ingest --------------------------------------------------------------

def make_event(app_id: str, event_type: str, severity: str, title: str,
               technical: str = "", context: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "app_id": app_id,
        "env": "production",
        "commit": "pve-scanner",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "type": event_type,
        "severity": severity,
        "title": title,
        "technical": technical,
        "context": context or {},
    }


def post_ingest(events: list[dict[str, Any]], hub_url: str | None = None) -> bool:
    """Best-effort POST /ingest. Never raises; returns whether it succeeded. Never logs the key."""
    service_key = resolve_service_key()
    if not service_key:
        log("no HEIMDALL_SERVICE_KEY; skipping ingest")
        return False
    hub = hub_url or DEFAULT_HUB_URL
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{hub}/ingest",
            data=json.dumps({"events": events}).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {service_key}",
                "User-Agent": "pve-scanner/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if 200 <= resp.status < 300:
                return True
            log(f"ingest returned {resp.status}")
            return False
    except Exception as e:
        log(f"ingest error: {e}")
        return False


def severity_key(sev: str) -> int:
    return SEVERITY_RANK.get(sev, -1)


def apply_first_run_seeding(new_findings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """First-run behavior: don't flood — page only the FIRST_RUN_PAGE_CAP most
    severe findings; everything else is seeded into state as known."""
    if not is_first_run():
        return new_findings, []

    ranked = sorted(new_findings, key=lambda f: severity_key(f.get("severity", "")), reverse=True)
    to_page = ranked[:FIRST_RUN_PAGE_CAP]
    to_seed_only = ranked[FIRST_RUN_PAGE_CAP:]
    return to_page, to_seed_only


def run_cmd(args: list[str], cwd: str | None = None, timeout: int = 120) -> tuple[int, str, str]:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except Exception as e:
        return -1, "", str(e)


def ssh_run(host_ip: str, command: str, timeout: int = SSH_TIMEOUT) -> tuple[int, str, str]:
    """Run a command via SSH on a PVE host. Returns (exit_code, stdout, stderr)."""
    args = [
        "ssh",
        "-o", f"ConnectTimeout={SSH_TIMEOUT}",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{SSH_USER}@{host_ip}",
        command,
    ]
    return run_cmd(args, timeout=timeout + 5)


def ssh_journal(host_ip: str, since: str, units: list[str], timeout: int = SSH_TIMEOUT) -> tuple[int, str, str]:
    """Fetch journalctl JSON output from a PVE host for given units since cursor."""
    unit_args = " ".join(f"-u {u}" for u in units)
    cmd = f"journalctl {unit_args} --since '{since}' -o json"
    return ssh_run(host_ip, cmd, timeout)


def ssh_list_ct(host_ip: str) -> tuple[int, str, str]:
    return ssh_run(host_ip, "pct list -o json")


def ssh_list_qm(host_ip: str) -> tuple[int, str, str]:
    return ssh_run(host_ip, "qm list -o json")


def ssh_firewall_config(host_ip: str) -> tuple[int, str, str]:
    return ssh_run(host_ip, "pve-firewall compile --output-format json 2>/dev/null || pve-firewall compile")



def fingerprint_keys(keys_text: str) -> list[str]:
    """Extract SSH key fingerprints from authorized_keys content."""
    import hashlib
    fps = []
    for line in keys_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            fp = hashlib.sha256(line.encode()).hexdigest()[:32]
            fps.append(fp)
        except Exception:
            continue
    return sorted(fps)