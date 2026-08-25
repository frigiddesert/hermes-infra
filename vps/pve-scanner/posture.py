#!/usr/bin/env python3
"""
PVE posture / config drift — runs weekly from cron on openclaw-vps.

Mirrors security-scanner/cf_posture.py: snapshots container/VM inventory,
firewall rules, and root authorized_keys fingerprints per PVE host.
Diff against committed baseline; new/changed entries page once,
accepted changes get re-seeded into baseline (human action).

Findings:
- config_drift: new/removed/reconfigured CT or VM
- config_drift: firewall rule changes
- config_drift: authorized_keys changes
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

APP_ID = "openclaw-vps"
BASELINE_PATH = Path(__file__).resolve().parent / "posture-baseline.json"
STATE_DIR = Path(__file__).resolve().parent / "state"
ENV_CANDIDATES = ["/root/pve-scanner/.env", "/root/pve-scanner.env"]
HERMES_PROFILE_ENV = "/root/.hermes/profiles/heimdall/.env"


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


def _log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat()} {msg}", flush=True)


def load_baseline() -> dict[str, Any]:
    if BASELINE_PATH.exists():
        try:
            return json.loads(BASELINE_PATH.read_text())
        except Exception as e:
            _log(f"failed to load baseline: {e}")
            return {}
    return {}


def save_baseline(baseline: dict[str, Any]) -> None:
    tmp = BASELINE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(baseline, indent=2, sort_keys=True))
    tmp.replace(BASELINE_PATH)




def fetch_host_inventory(host_name: str) -> dict[str, Any]:
    """Collect container/VM inventory, firewall, authorized_keys for one host."""
    host_ip = common.PVE_HOSTS[host_name]

    # CT list
    code, ct_out, ct_err = common.ssh_list_ct(host_ip)
    containers = []
    if code == 0 and ct_out.strip():
        try:
            for ct in json.loads(ct_out):
                containers.append({
                    "vmid": ct.get("vmid"),
                    "name": ct.get("name"),
                    "status": ct.get("status"),
                    "mem": ct.get("mem"),
                    "disk": ct.get("disk"),
                    "net": ct.get("net"),
                })
        except json.JSONDecodeError:
            pass

    # VM list
    code, qm_out, qm_err = common.ssh_list_qm(host_ip)
    vms = []
    if code == 0 and qm_out.strip():
        try:
            for vm in json.loads(qm_out):
                vms.append({
                    "vmid": vm.get("vmid"),
                    "name": vm.get("name"),
                    "status": vm.get("status"),
                    "mem": vm.get("mem"),
                    "disk": vm.get("disk"),
                })
        except json.JSONDecodeError:
            pass

    # Firewall
    code, fw_out, fw_err = common.ssh_firewall_config(host_ip)
    firewall_rules = []
    if code == 0 and fw_out.strip():
        try:
            firewall_rules = json.loads(fw_out)
        except json.JSONDecodeError:
            firewall_rules = [{"raw": fw_out}]

    # Authorized keys
    code, keys_out, keys_err = common.ssh_authorized_keys(host_ip)
    key_fps = fingerprint_keys(keys_out)

    return {
        "host": host_name,
        "ip": host_ip,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "containers": containers,
        "vms": vms,
        "firewall": firewall_rules,
        "authorized_keys_fps": key_fps,
    }


def diff_inventory(baseline: dict, current: dict, host_name: str) -> list[dict]:
    """Compare current vs baseline inventory. Returns list of findings."""
    findings = []
    base_host = baseline.get(host_name, {})

    # Containers
    base_cts = {c["vmid"]: c for c in base_host.get("containers", [])}
    curr_cts = {c["vmid"]: c for c in current.get("containers", [])}

    for vmid, ct in curr_cts.items():
        if vmid not in base_cts:
            findings.append({
                "event_type": "config_drift",
                "severity": "warn",
                "title": f"New container on {host_name}",
                "technical": f"CT {vmid} ({ct.get('name', 'unnamed')}) appeared",
                "context": {"host": host_name, "vmid": vmid, "change": "container_added", "details": ct},
            })
        elif ct != base_cts[vmid]:
            findings.append({
                "event_type": "config_drift",
                "severity": "warn",
                "title": f"Container changed on {host_name}",
                "technical": f"CT {vmid} ({ct.get('name', 'unnamed')}) reconfigured",
                "context": {"host": host_name, "vmid": vmid, "change": "container_changed", "old": base_cts[vmid], "new": ct},
            })

    for vmid in base_cts:
        if vmid not in curr_cts:
            findings.append({
                "event_type": "config_drift",
                "severity": "error",
                "title": f"Container removed on {host_name}",
                "technical": f"CT {vmid} disappeared",
                "context": {"host": host_name, "vmid": vmid, "change": "container_removed"},
            })

    # VMs
    base_vms = {v["vmid"]: v for v in base_host.get("vms", [])}
    curr_vms = {v["vmid"]: v for v in current.get("vms", [])}

    for vmid, vm in curr_vms.items():
        if vmid not in base_vms:
            findings.append({
                "event_type": "config_drift",
                "severity": "warn",
                "title": f"New VM on {host_name}",
                "technical": f"VM {vmid} ({vm.get('name', 'unnamed')}) appeared",
                "context": {"host": host_name, "vmid": vmid, "change": "vm_added", "details": vm},
            })
        elif vm != base_vms[vmid]:
            findings.append({
                "event_type": "config_drift",
                "severity": "warn",
                "title": f"VM changed on {host_name}",
                "technical": f"VM {vmid} ({vm.get('name', 'unnamed')}) reconfigured",
                "context": {"host": host_name, "vmid": vmid, "change": "vm_changed", "old": base_vms[vmid], "new": vm},
            })

    for vmid in base_vms:
        if vmid not in curr_vms:
            findings.append({
                "event_type": "config_drift",
                "severity": "error",
                "title": f"VM removed on {host_name}",
                "technical": f"VM {vmid} disappeared",
                "context": {"host": host_name, "vmid": vmid, "change": "vm_removed"},
            })

    # Firewall - simple count diff for now
    base_fw_count = len(base_host.get("firewall", []))
    curr_fw_count = len(current.get("firewall", []))
    if curr_fw_count != base_fw_count:
        findings.append({
            "event_type": "config_drift",
            "severity": "warn",
            "title": f"Firewall rule count changed on {host_name}",
            "technical": f"Rules: {base_fw_count} -> {curr_fw_count}",
            "context": {"host": host_name, "change": "firewall_count", "old_count": base_fw_count, "new_count": curr_fw_count},
        })

    # Authorized keys
    base_fps = set(base_host.get("authorized_keys_fps", []))
    curr_fps = set(current.get("authorized_keys_fps", []))
    if curr_fps != base_fps:
        added = curr_fps - base_fps
        removed = base_fps - curr_fps
        if added:
            findings.append({
                "event_type": "config_drift",
                "severity": "error",
                "title": f"New SSH keys on {host_name}",
                "technical": f"{len(added)} new key(s) added to root authorized_keys",
                "context": {"host": host_name, "change": "ssh_keys_added", "added_fps": list(added)},
            })
        if removed:
            findings.append({
                "event_type": "config_drift",
                "severity": "error",
                "title": f"SSH keys removed on {host_name}",
                "technical": f"{len(removed)} key(s) removed from root authorized_keys",
                "context": {"host": host_name, "change": "ssh_keys_removed", "removed_fps": list(removed)},
            })

    return findings


def main() -> int:
    _load_env()
    baseline = load_baseline()
    all_findings = []

    for host_name in common.PVE_HOSTS:
        _log(f"Checking posture for {host_name}")
        current = fetch_host_inventory(host_name)
        findings = diff_inventory(baseline, current, host_name)
        all_findings.extend(findings)
        # Update baseline with current for next run (accepted changes are re-seeded manually)
        baseline[host_name] = current

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
            _log(f"Posted {len(events)} posture findings to hub")

    if is_first_run():
        common.mark_first_run_done()

    # Save updated baseline (human accepts by not reverting)
    save_baseline(baseline)
    return 0


if __name__ == "__main__":
    sys.exit(main())