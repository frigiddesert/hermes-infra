"""Module 1: fleet dependency audit (heimdall issue #24 task 1).

Per repo: prefer `gh api repos/frigiddesert/<r>/dependabot/alerts` (cheap, no clone). If Dependabot
is disabled there (403/404), fall back to a shallow clone + `npm audit --json` (only if a
package.json / package-lock.json exists at the repo root).

NEW critical/high finding (not already in state/dependency.json) → one `dependency_vuln` security
event, fingerprinted per repo+advisory so recurrence never re-posts (contracts/src/event.ts already
has `dependency_vuln` — no new type needed).
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from common import log, make_event, run_cmd

GH_ORG = "frigiddesert"
SEVERITIES_THAT_MATTER = {"critical", "high"}


def _dependabot_alerts(repo: str) -> list[dict[str, Any]] | None:
    """Returns list of open alerts, or None if Dependabot alerts aren't available for this repo
    (disabled / not accessible) — caller should fall back to npm audit."""
    rc, out, err = run_cmd(
        ["gh", "api", f"repos/{GH_ORG}/{repo}/dependabot/alerts", "--paginate",
         "-q", "."],
        timeout=30,
    )
    if rc != 0:
        if "403" in err or "404" in err or "disabled" in err.lower():
            return None
        log(f"dependabot alerts fetch failed for {repo}: {err.strip()[:200]}")
        return None
    alerts: list[dict[str, Any]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            alerts.extend(parsed)
        else:
            alerts.append(parsed)
    return alerts


def _findings_from_dependabot(repo: str, alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for alert in alerts:
        if alert.get("state") != "open":
            continue
        advisory = alert.get("security_advisory") or {}
        severity = (advisory.get("severity") or alert.get("security_vulnerability", {}).get("severity") or "").lower()
        if severity not in SEVERITIES_THAT_MATTER:
            continue
        ghsa_id = advisory.get("ghsa_id") or alert.get("number")
        package = ((alert.get("dependency") or {}).get("package") or {}).get("name", "unknown")
        summary = advisory.get("summary", "dependency vulnerability")
        url = alert.get("html_url", "")
        findings.append({
            "key": f"dep:{repo}:{ghsa_id}",
            "repo": repo,
            "severity": severity,
            "package": package,
            "ghsa_id": ghsa_id,
            "summary": summary,
            "url": url,
            "source": "dependabot",
        })
    return findings


def _npm_audit_fallback(repo: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=f"secscan-{repo}-") as tmp:
        rc, _, err = run_cmd(
            ["git", "clone", "--depth", "1", f"https://github.com/{GH_ORG}/{repo}.git", tmp],
            timeout=120,
        )
        if rc != 0:
            log(f"clone failed for {repo}, skipping npm audit fallback: {err.strip()[:200]}")
            return findings
        if not (Path(tmp) / "package.json").exists():
            return findings  # not a node project — nothing npm audit can do
        run_cmd(["npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit"], cwd=tmp, timeout=180)
        rc, out, _err = run_cmd(["npm", "audit", "--json"], cwd=tmp, timeout=120)
        if not out.strip():
            return findings
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return findings
        vulns = data.get("vulnerabilities") or {}
        for pkg_name, info in vulns.items():
            severity = (info.get("severity") or "").lower()
            if severity not in SEVERITIES_THAT_MATTER:
                continue
            via = info.get("via") or []
            advisory_ids = sorted({str(v.get("source", v)) for v in via if isinstance(v, dict) and v.get("source")}) or ["npm-audit"]
            key_id = "-".join(advisory_ids) or pkg_name
            findings.append({
                "key": f"dep:{repo}:npm:{pkg_name}:{key_id}",
                "repo": repo,
                "severity": severity,
                "package": pkg_name,
                "ghsa_id": key_id,
                "summary": f"npm audit: {pkg_name} has a {severity} vulnerability",
                "url": "",
                "source": "npm_audit",
            })
    return findings


def scan_repo(repo_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    repo = repo_cfg["repo"]
    if repo_cfg.get("dependabot", True):
        alerts = _dependabot_alerts(repo)
        if alerts is not None:
            return _findings_from_dependabot(repo, alerts)
        log(f"{repo}: dependabot unavailable, falling back to npm audit")
    if shutil.which("npm") is None or shutil.which("git") is None:
        log(f"{repo}: npm/git not available, skipping fallback scan")
        return []
    return _npm_audit_fallback(repo)


def to_event(finding: dict[str, Any], app_id: str) -> dict[str, Any]:
    severity = "critical" if finding["severity"] == "critical" else "error"  # security floor is 'error'
    title = f"dependency vulnerability: {finding['package']} ({finding['severity']}) in {finding['repo']}"
    technical = f"{finding['summary']}\nadvisory: {finding['ghsa_id']}\n{finding['url']}"
    return make_event(
        app_id, "dependency_vuln", severity, title, technical,
        context={"op": "dependency_scan", "repo": finding["repo"], "package": finding["package"],
                 "advisory": finding["ghsa_id"], "source": finding["source"]},
    )


def run(repos: list[dict[str, Any]], state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Returns (new_finding_wrappers, updated_state). Each wrapper is {"key", "event", "finding"}."""
    new_wrappers: list[dict[str, Any]] = []
    for repo_cfg in repos:
        repo = repo_cfg["repo"]
        app_id = repo_cfg.get("app_id") or repo
        try:
            findings = scan_repo(repo_cfg)
        except Exception as e:  # never let one repo's scan crash the whole run
            log(f"{repo}: dependency scan failed: {e}")
            continue
        for finding in findings:
            key = finding["key"]
            if key in state:
                continue  # already known — no re-post, ever
            event = to_event(finding, app_id)
            new_wrappers.append({"key": key, "event": event, "finding": finding})
    return new_wrappers, state
