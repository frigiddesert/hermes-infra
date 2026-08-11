"""Module 2: secret scanning with gitleaks (heimdall issue #24 task 2).

Downloads the static gitleaks binary to bin/ on first use (no package manager dependency). Runs it
over a shallow clone (working tree only, --no-git so history isn't scanned — repos are shallow
clones anyway) of each configured repo, default rules + gitleaks-allowlist.toml. NEW finding (not
in state/secrets.json) → one `secret_exposure` security event. Report-only per the issue — no
autonomous remediation.
"""
from __future__ import annotations

import json
import platform
import stat
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from common import SCANNER_DIR, log, make_event, run_cmd

GH_ORG = "frigiddesert"
GITLEAKS_VERSION = "8.21.2"
GITLEAKS_BIN = SCANNER_DIR / "bin" / "gitleaks"
ALLOWLIST_PATH = SCANNER_DIR / "gitleaks-allowlist.toml"


def _gitleaks_asset_name() -> str | None:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        return None
    return f"gitleaks_{GITLEAKS_VERSION}_linux_{arch}.tar.gz"


def ensure_gitleaks() -> bool:
    if GITLEAKS_BIN.exists():
        return True
    asset = _gitleaks_asset_name()
    if not asset:
        log(f"unsupported architecture {platform.machine()} — cannot fetch gitleaks")
        return False
    url = f"https://github.com/gitleaks/gitleaks/releases/download/v{GITLEAKS_VERSION}/{asset}"
    log(f"downloading gitleaks {GITLEAKS_VERSION}...")
    GITLEAKS_BIN.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            with urllib.request.urlopen(url, timeout=60) as resp:
                tmp.write(resp.read())
        with tarfile.open(tmp_path) as tar:
            member = next((m for m in tar.getmembers() if m.name == "gitleaks"), None)
            if member is None:
                log("gitleaks binary not found inside release archive")
                return False
            extracted = tar.extractfile(member)
            if extracted is None:
                return False
            GITLEAKS_BIN.write_bytes(extracted.read())
        GITLEAKS_BIN.chmod(GITLEAKS_BIN.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        tmp_path.unlink(missing_ok=True)
        return True
    except (urllib.error.URLError, OSError, tarfile.TarError) as e:
        log(f"gitleaks download failed: {e}")
        return False


def scan_repo(repo: str) -> list[dict[str, Any]]:
    if not ensure_gitleaks():
        return []
    findings: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=f"secscan-{repo}-") as tmp:
        rc, _, err = run_cmd(
            ["git", "clone", "--depth", "1", f"https://github.com/{GH_ORG}/{repo}.git", tmp],
            timeout=120,
        )
        if rc != 0:
            log(f"{repo}: clone failed, skipping secret scan: {err.strip()[:200]}")
            return findings
        report_path = Path(tmp) / "gitleaks-report.json"
        cmd = [str(GITLEAKS_BIN), "detect", "--source", tmp, "--no-git",
               "--report-format", "json", "--report-path", str(report_path), "--exit-code", "0"]
        if ALLOWLIST_PATH.exists():
            cmd += ["--config", str(ALLOWLIST_PATH)]
        rc, _out, err = run_cmd(cmd, timeout=180)
        if rc == -1:
            log(f"{repo}: gitleaks run failed: {err.strip()[:200]}")
            return findings
        if not report_path.exists():
            return findings
        try:
            raw = json.loads(report_path.read_text())
        except json.JSONDecodeError:
            return findings
        for item in raw:
            file_ = item.get("File", "unknown")
            rule = item.get("RuleID", "unknown")
            line = item.get("StartLine", 0)
            findings.append({
                "key": f"secret:{repo}:{file_}:{rule}:{line}",
                "repo": repo,
                "file": file_,
                "rule": rule,
                "line": line,
                # never carry the raw secret value into state/events — commit + description only
                "description": item.get("Description", rule),
            })
    return findings


def to_event(finding: dict[str, Any], app_id: str) -> dict[str, Any]:
    title = f"secret exposure: {finding['rule']} in {finding['repo']}/{finding['file']}"
    technical = f"{finding['description']} at {finding['file']}:{finding['line']} (rule: {finding['rule']})"
    return make_event(
        app_id, "secret_exposure", "critical", title, technical,
        context={"op": "secret_scan", "repo": finding["repo"], "file": finding["file"], "rule": finding["rule"]},
    )


def run(repos: list[dict[str, Any]], state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    new_wrappers: list[dict[str, Any]] = []
    for repo_cfg in repos:
        repo = repo_cfg["repo"]
        app_id = repo_cfg.get("app_id") or repo
        try:
            findings = scan_repo(repo)
        except Exception as e:
            log(f"{repo}: secret scan failed: {e}")
            continue
        for finding in findings:
            key = finding["key"]
            if key in state:
                continue
            event = to_event(finding, app_id)
            new_wrappers.append({"key": key, "event": event, "finding": finding})
    return new_wrappers, state
