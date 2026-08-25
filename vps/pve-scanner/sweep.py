#!/usr/bin/env python3
"""
PVE log sweep — runs every ~30 min from cron on openclaw-vps.

Mirrors log-sweep/sweep.py patterns: reads journalctl via SSH for each PVE host,
applies rules for auth anomalies, crash loops, disk pressure, OOM, and posts
to Heimdall hub. State is persisted per-host for cursor + rolling baselines.

Rules:
- auth_anomaly: SSH/PVE-API auth failures over threshold
- restart: unexpected service restarts / crash loops
- boundary_failure: disk >90% on rpool/local-lvm
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
from common import is_first_run, mark_first_run_done
APP_ID = "openclaw-vps"
STATE_PATH = Path(__file__).resolve().parent / "state" / "sweep_state.json"
ENV_CANDIDATES = ["/root/pve-scanner/.env", "/root/pve-scanner.env"]
HERMES_PROFILE_ENV = "/root/.hermes/profiles/heimdall/.env"

# Units to watch on each PVE host (systemd unit names)
WATCHED_UNITS = {
    "pve-2": [
        "ssh.service",
        "pveproxy.service",
        "pvedaemon.service",
        "pve-firewall.service",
    ],
    "mosthutte": [
        "ssh.service",
        "pveproxy.service",
        "pvedaemon.service",
        "pve-firewall.service",
    ],
}

# Files to tail
WATCHED_FILES = {
    "pve-2": ["/var/log/pve/tasks/index", "/var/log/pve/firewall.log"],
    "mosthutte": ["/var/log/pve/tasks/index", "/var/log/pve/firewall.log"],
}

# Thresholds
SSHD_MIN_ATTEMPTS = 10
SSHD_MIN_IPS = 3
CRASHLOOP_MIN_STARTS = 2
DISK_PCT_THRESHOLD = 90.0
ERROR_SPIKE_MULTIPLE = 5.0
ERROR_SPIKE_MIN_ABS = 20
BASELINE_MIN_SAMPLES = 3
BASELINE_HISTORY_LEN = 48

SSHD_FAIL_RE = re.compile(r"Failed password|Invalid user|authentication failure|pam_unix.*authentication failure", re.IGNORECASE)
SSHD_IP_RE = re.compile(r"from\s+([0-9a-fA-F:.]+)")
PVE_API_AUTH_RE = re.compile(r"authentication failure|401 Unauthorized|permission denied", re.IGNORECASE)
RESTART_RE = re.compile(r"Started|Stopped|Restart", re.IGNORECASE)
ERROR_RE = re.compile(r"\b(error|exception|traceback|critical|fatal|fail)\b", re.IGNORECASE)
OOM_RE = re.compile(r"out of memory|oom-kill|killed process", re.IGNORECASE)


def _log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat()} {msg}", flush=True)


def _load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _load_env() -> None:
    for path in ENV_CANDIDATES:
        _load_env_file(path)
    _load_env_file(HERMES_PROFILE_ENV)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_PATH)


def _norm_msg(d: dict) -> dict:
    """journald emits MESSAGE as a list of byte values when the log line isn't valid UTF-8."""
    msg = d.get("MESSAGE")
    if isinstance(msg, list):
        try:
            d["MESSAGE"] = bytes(msg).decode("utf-8", errors="replace")
        except Exception:
            d["MESSAGE"] = str(msg)
    return d


def read_journal(host_name: str, cursor: str | None, units: list[str]) -> tuple[list[dict], str | None]:
    """Returns (entries, new_cursor). new_cursor is None if nothing new (keep old cursor)."""
    host_ip = common.PVE_HOSTS[host_name]
    if cursor:
        since = cursor
    else:
        # first run: seed at current tail (don't dump history)
        return [], None

    code, stdout, stderr = common.ssh_journal(host_ip, since, units)
    if code != 0:
        _log(f"journalctl failed for {host_name}: {stderr}")
        return [], cursor

    entries = []
    last_cursor = cursor
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            entry = _norm_msg(entry)
            if entry.get("__CURSOR"):
                last_cursor = entry["__CURSOR"]
            entries.append(entry)
        except json.JSONDecodeError:
            continue

    return entries, last_cursor


def read_file_tail(host_name: str, path: str, offset: int) -> tuple[list[str], int]:
    host_ip = common.PVE_HOSTS[host_name]
    cmd = f"tail -c +{offset + 1} {path} 2>/dev/null || echo ''"
    code, stdout, stderr = common.ssh_run(host_ip, cmd)
    if code != 0:
        _log(f"tail failed for {host_name}:{path}: {stderr}")
        return [], offset

    lines = stdout.splitlines()
    new_offset = offset + len(stdout.encode()) + len(lines)  # approximate
    return lines, new_offset


# --- rules ---

def rule_sshd_auth(host_name: str, entries: list[dict], state: dict) -> list[dict]:
    findings = []
    host_state = state.setdefault(host_name, {})
    sshd_fails = host_state.setdefault("sshd_fails", {"attempts": 0, "ips": set()})

    for entry in entries:
        msg = entry.get("MESSAGE", "")
        if not SSHD_FAIL_RE.search(msg):
            continue
        sshd_fails["attempts"] += 1
        ip_match = SSHD_IP_RE.search(msg)
        if ip_match:
            sshd_fails["ips"].add(ip_match.group(1))

    if sshd_fails["attempts"] >= SSHD_MIN_ATTEMPTS or len(sshd_fails["ips"]) >= SSHD_MIN_IPS:
        findings.append({
            "event_type": "auth_anomaly",
            "severity": "error",
            "title": f"SSH auth anomaly on {host_name}",
            "technical": f"{sshd_fails['attempts']} failed attempts from {len(sshd_fails['ips'])} IPs",
            "context": {
                "host": host_name,
                "attempts": sshd_fails["attempts"],
                "unique_ips": len(sshd_fails["ips"]),
                "ips": list(sshd_fails["ips"]),
            },
        })
        # reset after firing
        sshd_fails["attempts"] = 0
        sshd_fails["ips"] = set()

    return findings


def rule_pve_api_auth(host_name: str, entries: list[dict], state: dict) -> list[dict]:
    findings = []
    host_state = state.setdefault(host_name, {})
    api_fails = host_state.setdefault("pve_api_fails", 0)

    for entry in entries:
        msg = entry.get("MESSAGE", "")
        unit = entry.get("_SYSTEMD_UNIT") or entry.get("UNIT", "")
        if unit not in ("pveproxy.service", "pvedaemon.service"):
            continue
        if PVE_API_AUTH_RE.search(msg):
            api_fails += 1

    host_state["pve_api_fails"] = api_fails

    if api_fails >= SSHD_MIN_ATTEMPTS:
        findings.append({
            "event_type": "auth_anomaly",
            "severity": "error",
            "title": f"PVE API auth anomaly on {host_name}",
            "technical": f"{api_fails} failed API auth attempts",
            "context": {"host": host_name, "api_failures": api_fails},
        })
        host_state["pve_api_fails"] = 0

    return findings


def rule_crash_loop(host_name: str, entries: list[dict], units: list[str], state: dict) -> list[dict]:
    findings = []
    host_state = state.setdefault(host_name, {})
    starts = host_state.setdefault("unit_starts", {})

    for entry in entries:
        msg = entry.get("MESSAGE", "")
        unit = entry.get("_SYSTEMD_UNIT") or entry.get("UNIT", "")
        if unit not in units:
            continue
        if "Started" in msg or "Restart" in msg:
            starts[unit] = starts.get(unit, 0) + 1

    for unit, count in list(starts.items()):
        if count >= CRASHLOOP_MIN_STARTS:
            findings.append({
                "event_type": "boundary_failure",
                "severity": "warn",
                "title": f"Service restart loop on {host_name}",
                "technical": f"{unit} started {count} times in window",
                "context": {"host": host_name, "unit": unit, "starts": count},
            })
            starts[unit] = 0  # reset after firing

    return findings


def rule_disk(host_name: str, state: dict) -> list[dict]:
    findings = []
    host_ip = common.PVE_HOSTS[host_name]
    code, stdout, stderr = common.ssh_run(host_ip, "df -h / /dev/mapper/pve-root /dev/mapper/rpool-root 2>/dev/null | tail -n +2")
    if code != 0:
        return findings

    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            used_pct = float(parts[4].rstrip("%"))
            mount = parts[5] if len(parts) > 5 else parts[0]
            if used_pct >= DISK_PCT_THRESHOLD:
                findings.append({
                    "event_type": "boundary_failure",
                    "severity": "error",
                    "title": f"Disk pressure on {host_name}",
                    "technical": f"{mount} at {used_pct}% used",
                    "context": {"host": host_name, "mount": mount, "used_pct": used_pct},
                })
        except (ValueError, IndexError):
            continue

    return findings


def rule_oom(host_name: str, entries: list[dict], state: dict) -> list[dict]:
    findings = []
    for entry in entries:
        msg = entry.get("MESSAGE", "")
        if OOM_RE.search(msg):
            findings.append({
                "event_type": "boundary_failure",
                "severity": "error",
                "title": f"OOM kill on {host_name}",
                "technical": msg[:500],
                "context": {"host": host_name},
            })
    return findings


# --- main ---

def main() -> int:
    _load_env()
    state = load_state()
    all_findings = []

    for host_name, units in WATCHED_UNITS.items():
        _log(f"Sweeping {host_name} ({common.PVE_HOSTS[host_name]})")
        host_state = state.setdefault(host_name, {})
        cursor = host_state.get("journal_cursor")

        entries, new_cursor = read_journal(host_name, cursor, units)
        if not entries and cursor:
            # nothing new
            host_state["journal_cursor"] = cursor
            continue

        if entries:
            all_findings.extend(rule_sshd_auth(host_name, entries, state))
            all_findings.extend(rule_pve_api_auth(host_name, entries, state))
            all_findings.extend(rule_crash_loop(host_name, entries, units, state))
            all_findings.extend(rule_oom(host_name, entries, state))
            host_state["journal_cursor"] = new_cursor or cursor

        # tail watched files
        for fpath in WATCHED_FILES.get(host_name, []):
            offset = host_state.get(f"file_offset_{fpath.replace('/', '_')}", 0)
            lines, new_offset = read_file_tail(host_name, fpath, offset)
            # file-based rules could go here
            host_state[f"file_offset_{fpath.replace('/', '_')}"] = new_offset

        all_findings.extend(rule_disk(host_name, state))

    # first-run seeding
    to_page, to_seed = common.apply_first_run_seeding(all_findings)

    if to_page:
        events = []
        for f in to_page:
            events.append(common.make_event(
                app_id=APP_ID,
                event_type=f["event_type"],
                severity=f["severity"],
                title=f["title"],
                technical=f["technical"],
                context=f.get("context"),
            ))
        if common.post_ingest(events):
            _log(f"Posted {len(events)} sweep findings to hub")

    if is_first_run():
        mark_first_run_done()

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())