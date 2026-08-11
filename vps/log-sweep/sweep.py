#!/usr/bin/env python3
"""
log-sweep — programmatic-first log analysis for openclaw-vps (Heimdall issue #25).

Prime directive (Eric's words): "I don't want to spend even 15 minutes a week reading reports
with no information I need to act on." So: no digests, no summaries. Cheap deterministic rules
fire fingerprinted incidents through the Heimdall hub (the hub dedups — repeat hits bump an
already-open incident instead of re-paging). Quiet runs produce zero output. The one AI call
(Hermes, local heimdall profile) is reserved for anomalies the rules can't classify, and is
hard-capped per day so it can never become the new noise source.

Run every 30 min from system cron:
    */30 * * * * cd /root/log-sweep && /usr/bin/python3 sweep.py >> /root/log-sweep/sweep.log 2>&1

State (cursors + rolling baselines + Hermes call budget) lives in state.json next to this file.

Event-type mapping note: the issue text (and originally this task) reaches for `resource_exhaustion`
and `crash` event types. Neither exists in contracts/src/event.ts (checked directly — the full
EventType enum is: exception, boundary_failure, schema_violation, data_integrity, degraded,
synthetic_fail, auth_anomaly, access_denied, integrity_violation, secret_exposure, dependency_vuln,
anomalous_traffic, config_drift). Mapping used here, closest-fit:
  - sshd auth anomaly          -> auth_anomaly   (exact match, security lane)
  - service crash-loop         -> exception       (closest reliability type to "process died")
  - OOM kill                   -> exception       (same reasoning, context notes it's an OOM)
  - disk >90%                  -> config_drift    (resource_exhaustion doesn't exist; the issue text
                                                     itself offers config_drift as the fallback)
  - Hermes escalation (error)  -> exception       (unclassified reliability anomaly)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heimdall_reporter as heimdall  # vendored copy, see heimdall_reporter.py

APP_ID = "openclaw-vps"
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
ENV_CANDIDATES = ["/root/log-sweep/.env", "/root/log-sweep.env"]
# Hermes profile secrets (API_SERVER_KEY for the local heimdall-profile chat-completions server).
# Read at runtime only, never logged, never passed to a child process's argv.
HERMES_PROFILE_ENV = "/root/.hermes/profiles/heimdall/.env"

WATCHED_UNITS = [
    "hermes-gateway-brand-manager.service",
    "hermes-gateway-contour-vrp.service",
    "hermes-gateway-heimdall.service",
    "hermes-gateway-main.service",
    "hermes-gateway-pm-cfo.service",
    "hermes-curated-proxy.service",
    "cloudflared.service",
    "cron.service",
]
SSHD_UNIT = "ssh.service"  # the actual unit name on this box (checked with `systemctl list-units`); "sshd" is not a unit here

WATCHED_FILES = [
    "/root/heimdall-bridge/bridge.log",
    "/root/security-scanner/scan.log",
]

# --- thresholds (all "since last sweep" i.e. within one ~30 min window unless noted) ---
SSHD_MIN_ATTEMPTS = 10       # >10 distinct failed attempts
SSHD_MIN_IPS = 3             # or >3 distinct source IPs
CRASHLOOP_MIN_STARTS = 2     # unit (re)started more than 2 times in the window
DISK_PCT_THRESHOLD = 90.0    # disk usage on /
ERROR_SPIKE_MULTIPLE = 5.0   # current window's error count > 5x rolling baseline
ERROR_SPIKE_MIN_ABS = 20     # AND > 20 absolute
BASELINE_MIN_SAMPLES = 3     # need this many prior windows before spike detection kicks in
BASELINE_HISTORY_LEN = 48    # ~24h of 30-min windows
HERMES_DAILY_CAP = 5
HERMES_EXCERPT_MAX_BYTES = 4096
HERMES_URL = "http://127.0.0.1:8660/v1/chat/completions"
HERMES_TIMEOUT_S = 30

ERROR_LINE_RE = re.compile(r"\b(error|exception|traceback|critical|fatal)\b", re.IGNORECASE)
SSHD_FAIL_RE = re.compile(r"Failed password|Invalid user|authentication failure", re.IGNORECASE)
SSHD_IP_RE = re.compile(r"from ([0-9a-fA-F:.]+)")


def _log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat()} {msg}", flush=True)


def _load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def _load_env() -> None:
    for path in ENV_CANDIDATES:
        if os.path.exists(path):
            _load_env_file(path)
            break
    # Hermes profile .env is loaded separately (and always) so API_SERVER_KEY is available for
    # escalation calls regardless of which log-sweep .env candidate matched above.
    _load_env_file(HERMES_PROFILE_ENV)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "journal_cursor": None,
        "file_cursors": {},
        "baselines": {},
        "hermes": {"date": _today(), "count": 0},
        "last_run": None,
    }


def save_state(state: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)


def hermes_budget_remaining(state: dict) -> int:
    h = state.setdefault("hermes", {"date": _today(), "count": 0})
    if h.get("date") != _today():
        h["date"] = _today()
        h["count"] = 0
    return max(0, HERMES_DAILY_CAP - int(h.get("count", 0)))


def hermes_record_call(state: dict) -> None:
    h = state.setdefault("hermes", {"date": _today(), "count": 0})
    if h.get("date") != _today():
        h["date"] = _today()
        h["count"] = 0
    h["count"] = int(h.get("count", 0)) + 1


# --- journal reading ---

def _journal_bootstrap_cursor() -> str | None:
    """First run: seed the cursor at the current journal tail so we don't dump the whole
    pre-existing history as 'new' events on day one. A quiet first run is the expected/desired
    result on a healthy box."""
    try:
        out = subprocess.run(
            ["journalctl", "-o", "json", "-n", "1"],
            capture_output=True, text=True, timeout=30,
        )
        for line in reversed(out.stdout.splitlines()):
            try:
                d = json.loads(line)
            except Exception:
                continue
            return d.get("__CURSOR")
    except Exception as e:
        _log(f"WARN: could not bootstrap journal cursor: {e}")
    return None


def read_journal(cursor: str | None, units: list[str], since_fallback: str = "30 minutes ago") -> tuple[list[dict], str | None]:
    """Returns (entries, new_cursor). new_cursor is None if nothing new (keep old cursor)."""
    cmd = ["journalctl", "-o", "json"]
    for u in units:
        cmd += ["-u", u]
    if cursor:
        cmd += [f"--after-cursor={cursor}"]
    else:
        cmd += ["--since", since_fallback]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        _log(f"WARN: journalctl failed: {e}")
        return [], None
    entries = []
    last_cursor = None
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        entries.append(d)
        c = d.get("__CURSOR")
        if c:
            last_cursor = c
    return entries, last_cursor


def read_kernel_oom(cursor: str | None) -> tuple[list[dict], str | None]:
    cmd = ["journalctl", "-k", "-o", "json"]
    if cursor:
        cmd += [f"--after-cursor={cursor}"]
    else:
        cmd += ["--since", "30 minutes ago"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        _log(f"WARN: journalctl -k failed: {e}")
        return [], None
    entries = []
    last_cursor = None
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        entries.append(d)
        c = d.get("__CURSOR")
        if c:
            last_cursor = c
    return entries, last_cursor


def read_file_tail(path: str, offset: int) -> tuple[list[str], int]:
    if not os.path.exists(path):
        return [], offset
    try:
        size = os.path.getsize(path)
        if size < offset:
            offset = 0  # file was rotated/truncated; restart from top
        with open(path, "r", errors="replace") as f:
            f.seek(offset)
            data = f.read()
        return data.splitlines(), offset + len(data.encode("utf-8", errors="replace"))
    except Exception as e:
        _log(f"WARN: could not tail {path}: {e}")
        return [], offset


# --- rules ---

def rule_sshd_auth(entries: list[dict], state: dict) -> None:
    fails = [d for d in entries if (d.get("_SYSTEMD_UNIT") == SSHD_UNIT or d.get("UNIT") == SSHD_UNIT)
             and SSHD_FAIL_RE.search(d.get("MESSAGE", ""))]
    if not fails:
        return
    ips = set()
    for d in fails:
        m = SSHD_IP_RE.search(d.get("MESSAGE", ""))
        if m:
            ips.add(m.group(1))
    n_attempts = len(fails)
    n_ips = len(ips)
    if n_attempts > SSHD_MIN_ATTEMPTS or n_ips > SSHD_MIN_IPS:
        _log(f"RULE HIT: sshd auth anomaly — {n_attempts} failed attempts, {n_ips} distinct IPs")
        # Title/technical first line are deliberately generic and constant so repeated hits within
        # the same open episode dedup at the hub (fingerprint = appId+type+normalized title+first
        # line of technical) instead of paging again; a NEW episode (after the incident is resolved)
        # naturally reopens it and re-alerts. That's "one page per episode, not per attempt".
        heimdall.security(
            type="auth_anomaly",
            title="SSH authentication anomaly on openclaw-vps",
            severity="error",
            technical=(
                "Elevated failed SSH authentication attempts detected by log-sweep.\n"
                f"This window: {n_attempts} failed attempts from {n_ips} distinct source IP(s): "
                f"{', '.join(sorted(ips)) or 'unknown'}."
            ),
        )


def rule_crash_loop(entries: list[dict], units: list[str]) -> None:
    for unit in units:
        starts = [
            d for d in entries
            if (d.get("UNIT") == unit or d.get("_SYSTEMD_UNIT") == unit)
            and str(d.get("MESSAGE", "")).startswith("Started ")
        ]
        if len(starts) > CRASHLOOP_MIN_STARTS:
            _log(f"RULE HIT: crash-loop — {unit} started {len(starts)} times this window")
            heimdall.report(
                type="exception",
                title=f"Service crash-loop: {unit}",
                severity="error",
                technical=(
                    "Unit restarted repeatedly within a single log-sweep window (>2 starts).\n"
                    f"Start count this window: {len(starts)}."
                ),
                ctx={"op": "log-sweep.crash_loop", "extra": {"unit": unit}},
            )


def rule_oom(kernel_entries: list[dict], all_entries: list[dict]) -> None:
    oom_re = re.compile(r"out of memory|oom-kill|killed process", re.IGNORECASE)
    hits = [d for d in kernel_entries if oom_re.search(d.get("MESSAGE", ""))]
    hits += [d for d in all_entries if oom_re.search(d.get("MESSAGE", ""))]
    if not hits:
        return
    proc_re = re.compile(r"[Kk]illed process \d+ \(([^)]+)\)")
    procs = set()
    for d in hits:
        m = proc_re.search(d.get("MESSAGE", ""))
        if m:
            procs.add(m.group(1))
    proc_label = ", ".join(sorted(procs)) if procs else "unknown process"
    _log(f"RULE HIT: OOM kill — {proc_label}")
    heimdall.report(
        type="exception",
        title=f"OOM kill: {proc_label}",
        severity="error",
        technical=(
            "OOM killer activity detected by log-sweep (journal/dmesg).\n"
            f"{len(hits)} matching line(s). Process(es): {proc_label}."
        ),
        ctx={"op": "log-sweep.oom", "extra": {"processes": sorted(procs)}},
    )


def rule_disk() -> None:
    total, used, free = shutil.disk_usage("/")
    pct = used / total * 100.0
    if pct > DISK_PCT_THRESHOLD:
        _log(f"RULE HIT: disk usage {pct:.1f}%")
        heimdall.security(
            type="config_drift",
            title="Disk usage on / exceeded 90%",
            severity="warn",
            technical=f"Disk usage on / is {pct:.1f}% (used={used} total={total}). Threshold: {DISK_PCT_THRESHOLD}%.",
        )


def error_line_count(entries: list[dict], unit: str) -> int:
    return sum(
        1 for d in entries
        if (d.get("_SYSTEMD_UNIT") == unit or d.get("UNIT") == unit)
        and ERROR_LINE_RE.search(d.get("MESSAGE", ""))
    )


def is_error_spike(current: int, history: list[int]) -> bool:
    """Pure function (unit-tested): True if `current` is a spike vs `history`."""
    if len(history) < BASELINE_MIN_SAMPLES:
        return False
    baseline = sum(history) / len(history)
    baseline = max(baseline, 1.0)
    return current > ERROR_SPIKE_MULTIPLE * baseline and current > ERROR_SPIKE_MIN_ABS


def update_baseline(history: list[int], current: int) -> list[int]:
    history = list(history) + [current]
    return history[-BASELINE_HISTORY_LEN:]


def rule_error_rate_spikes(entries: list[dict], units: list[str], file_sources: dict[str, list[str]], state: dict) -> list[tuple[str, int, str]]:
    """Returns list of (source_name, count, excerpt) escalation candidates. Updates baselines
    in-place in `state`."""
    baselines = state.setdefault("baselines", {})
    candidates = []
    sources: dict[str, int] = {}
    excerpts: dict[str, str] = {}

    for unit in units:  # sshd excluded: it has its own dedicated rule above
        matches = [d.get("MESSAGE", "") for d in entries
                   if (d.get("_SYSTEMD_UNIT") == unit or d.get("UNIT") == unit)
                   and ERROR_LINE_RE.search(d.get("MESSAGE", ""))]
        sources[unit] = len(matches)
        excerpts[unit] = "\n".join(matches[-50:])

    for path, lines in file_sources.items():
        matches = [ln for ln in lines if ERROR_LINE_RE.search(ln)]
        sources[path] = len(matches)
        excerpts[path] = "\n".join(matches[-50:])

    for name, count in sources.items():
        history = baselines.get(name, [])
        if is_error_spike(count, history):
            candidates.append((name, count, excerpts.get(name, "")))
            _log(f"ESCALATION CANDIDATE: {name} — {count} error lines this window (baseline {sum(history)/len(history):.1f})")
        baselines[name] = update_baseline(history, count)

    return candidates


# --- Hermes escalation ---

def call_hermes(excerpt: str) -> dict | None:
    api_key = os.environ.get("HERMES_API_SERVER_KEY") or os.environ.get("API_SERVER_KEY")
    if not api_key:
        _log("WARN: no Hermes API key available (checked HERMES_API_SERVER_KEY / API_SERVER_KEY) — skipping escalation")
        return None
    excerpt_bytes = excerpt.encode("utf-8", errors="replace")[:HERMES_EXCERPT_MAX_BYTES]
    excerpt = excerpt_bytes.decode("utf-8", errors="ignore")
    prompt = (
        "You are triaging a log-error spike on a production VPS for the Heimdall monitoring "
        "platform. Analyze the excerpt below and respond with ONLY a JSON object, no prose, "
        "matching exactly this shape: "
        '{"severity": "info"|"warn"|"error", "explanation": "<one or two sentences>", '
        '"recommended_action": "<one sentence>"}.\n\n'
        f"Log excerpt:\n{excerpt}"
    )
    body = json.dumps({
        "model": "heimdall",
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        HERMES_URL, data=body,
        headers={"content-type": "application/json", "authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HERMES_TIMEOUT_S) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        content = raw["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if parsed.get("severity") not in ("info", "warn", "error"):
            raise ValueError(f"unexpected severity: {parsed.get('severity')!r}")
        return parsed
    except Exception as e:
        _log(f"WARN: Hermes call failed or returned unparseable output: {e}")
        return None


def escalate(source: str, count: int, excerpt: str, state: dict) -> None:
    remaining = hermes_budget_remaining(state)
    if remaining <= 0:
        _log(f"HERMES CAP: {source} spike ({count} lines) — capped, filing incident with raw excerpt")
        heimdall.report(
            type="exception",
            title=f"Unclassified log anomaly: {source}",
            severity="warn",
            technical=(
                f"Error-rate spike ({count} lines this window) escalated past the daily Hermes cap "
                f"({HERMES_DAILY_CAP}/day). AI analysis capped — raw excerpt attached.\n\n{excerpt[:HERMES_EXCERPT_MAX_BYTES]}"
            ),
            ctx={"op": "log-sweep.escalation", "extra": {"source": source, "capped": True}},
        )
        return

    hermes_record_call(state)
    result = call_hermes(excerpt)
    if result is None:
        # Hermes unreachable/broken — don't lose the signal, file with what we have.
        heimdall.report(
            type="exception",
            title=f"Unclassified log anomaly: {source}",
            severity="warn",
            technical=(
                f"Error-rate spike ({count} lines this window). Hermes AI analysis failed or was "
                f"unreachable — raw excerpt attached.\n\n{excerpt[:HERMES_EXCERPT_MAX_BYTES]}"
            ),
            ctx={"op": "log-sweep.escalation", "extra": {"source": source, "hermes_failed": True}},
        )
        return

    severity = result["severity"]
    if severity == "error":
        heimdall.report(
            type="exception",
            title=f"Unclassified log anomaly: {source}",
            severity="error",
            technical=(
                f"log-sweep escalated to Hermes AI analysis for {source}.\n\n"
                f"{result['explanation']}\n\nRecommended action: {result['recommended_action']}"
            ),
            ctx={"op": "log-sweep.escalation", "extra": {"source": source, "hermes_severity": severity}},
        )
    else:
        # warn/info: accumulate silently — no page, per the prime directive.
        acc = state.setdefault("hermes_findings", [])
        acc.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": source, "severity": severity,
            "explanation": result["explanation"],
        })
        state["hermes_findings"] = acc[-100:]
        _log(f"HERMES {severity.upper()}: {source} — {result['explanation']}")


# --- main ---

def main() -> int:
    _load_env()
    hub_url = os.environ.get("HEIMDALL_HUB_URL", "https://heimdall-hub.eric-c5f.workers.dev")
    service_key = os.environ.get("HEIMDALL_SERVICE_KEY")
    if not service_key:
        _log("FATAL: HEIMDALL_SERVICE_KEY not set (checked env + .env candidates) — cannot report")
        return 1

    heimdall.init(app_id=APP_ID, env="production", commit=os.environ.get("GIT_SHA", "vps"),
                  hub_url=hub_url, api_key=service_key)
    heimdall.heartbeat()

    state = load_state()

    cursor = state.get("journal_cursor")
    if cursor is None:
        cursor = _journal_bootstrap_cursor()
        entries: list[dict] = []
        new_cursor = cursor
        _log("First run: bootstrapped journal cursor at current tail, no backlog scanned")
    else:
        entries, new_cursor = read_journal(cursor, WATCHED_UNITS + [SSHD_UNIT])
        if new_cursor:
            cursor = new_cursor
    state["journal_cursor"] = cursor

    k_cursor = state.get("kernel_cursor")
    if k_cursor is None:
        k_entries: list[dict] = []
        k_new_cursor = _journal_bootstrap_cursor()
    else:
        k_entries, k_new_cursor = read_kernel_oom(k_cursor)
        if k_new_cursor:
            k_cursor = k_new_cursor
    if k_cursor is None:
        k_cursor = k_new_cursor
    state["kernel_cursor"] = k_cursor

    file_cursors = state.setdefault("file_cursors", {})
    file_sources: dict[str, list[str]] = {}
    for path in WATCHED_FILES:
        if path not in file_cursors:
            # First time seeing this file: bootstrap at current EOF (mirrors the journal cursor
            # bootstrap) so a large pre-existing log doesn't get scanned as "this window"'s traffic
            # and skew the very first baseline sample.
            file_cursors[path] = os.path.getsize(path) if os.path.exists(path) else 0
            _log(f"First run for {path}: bootstrapped file cursor at EOF (offset {file_cursors[path]})")
            continue
        offset = file_cursors[path]
        lines, new_offset = read_file_tail(path, offset)
        file_cursors[path] = new_offset
        if lines:
            file_sources[path] = lines

    _log(f"Sweep: {len(entries)} journal entries, {len(k_entries)} kernel entries, "
         f"{sum(len(v) for v in file_sources.values())} file lines")

    rule_sshd_auth(entries, state)
    rule_crash_loop(entries, WATCHED_UNITS)
    rule_oom(k_entries, entries)
    rule_disk()

    candidates = rule_error_rate_spikes(entries, WATCHED_UNITS, file_sources, state)
    for source, count, excerpt in candidates:
        escalate(source, count, excerpt, state)

    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    heimdall.flush(timeout_s=10)
    _log("Sweep complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
