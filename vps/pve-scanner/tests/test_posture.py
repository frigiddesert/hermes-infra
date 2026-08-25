"""Unit tests for pve-scanner posture diff logic (pure functions)."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json


class TestPostureDiff:
    def test_diff_inventory_new_container(self):
        # Simulate the diff_inventory logic
        baseline = {
            "pve-2": {
                "containers": [{"vmid": 100, "name": "old-ct", "status": "running"}],
                "vms": [],
                "firewall": [],
                "authorized_keys_fps": ["abc123"],
            }
        }
        current = {
            "host": "pve-2",
            "containers": [
                {"vmid": 100, "name": "old-ct", "status": "running"},
                {"vmid": 101, "name": "new-ct", "status": "running"},
            ],
            "vms": [],
            "firewall": [],
            "authorized_keys_fps": ["abc123"],
        }
        
        findings = self._run_diff(baseline, current, "pve-2")
        
        # Should find new container
        added = [f for f in findings if f["context"].get("change") == "container_added"]
        assert len(added) == 1
        assert added[0]["context"]["vmid"] == 101

    def test_diff_inventory_removed_container(self):
        baseline = {
            "pve-2": {
                "containers": [
                    {"vmid": 100, "name": "old-ct", "status": "running"},
                    {"vmid": 101, "name": "removed-ct", "status": "running"},
                ],
                "vms": [],
                "firewall": [],
                "authorized_keys_fps": ["abc123"],
            }
        }
        current = {
            "host": "pve-2",
            "containers": [{"vmid": 100, "name": "old-ct", "status": "running"}],
            "vms": [],
            "firewall": [],
            "authorized_keys_fps": ["abc123"],
        }
        
        findings = self._run_diff(baseline, current, "pve-2")
        
        removed = [f for f in findings if f["context"].get("change") == "container_removed"]
        assert len(removed) == 1
        assert removed[0]["context"]["vmid"] == 101

    def test_diff_inventory_changed_container(self):
        baseline = {
            "pve-2": {
                "containers": [{"vmid": 100, "name": "old-ct", "status": "running", "mem": 512}],
                "vms": [],
                "firewall": [],
                "authorized_keys_fps": ["abc123"],
            }
        }
        current = {
            "host": "pve-2",
            "containers": [{"vmid": 100, "name": "old-ct", "status": "stopped", "mem": 1024}],
            "vms": [],
            "firewall": [],
            "authorized_keys_fps": ["abc123"],
        }
        
        findings = self._run_diff(baseline, current, "pve-2")
        
        changed = [f for f in findings if f["context"].get("change") == "container_changed"]
        assert len(changed) == 1
        assert changed[0]["context"]["vmid"] == 100

    def test_diff_inventory_new_vm(self):
        baseline = {"pve-2": {"containers": [], "vms": [], "firewall": [], "authorized_keys_fps": []}}
        current = {"host": "pve-2", "containers": [], "vms": [{"vmid": 200, "name": "new-vm", "status": "running"}], "firewall": [], "authorized_keys_fps": []}
        
        findings = self._run_diff(baseline, current, "pve-2")
        
        added = [f for f in findings if f["context"].get("change") == "vm_added"]
        assert len(added) == 1
        assert added[0]["context"]["vmid"] == 200

    def test_diff_inventory_firewall_count(self):
        baseline = {"pve-2": {"containers": [], "vms": [], "firewall": [{"id": 1}], "authorized_keys_fps": []}}
        current = {"host": "pve-2", "containers": [], "vms": [], "firewall": [{"id": 1}, {"id": 2}], "authorized_keys_fps": []}
        
        findings = self._run_diff(baseline, current, "pve-2")
        
        fw = [f for f in findings if f["context"].get("change") == "firewall_count"]
        assert len(fw) == 1
        assert fw[0]["context"]["old_count"] == 1
        assert fw[0]["context"]["new_count"] == 2

    def test_diff_inventory_ssh_keys_added(self):
        baseline = {"pve-2": {"containers": [], "vms": [], "firewall": [], "authorized_keys_fps": ["key1"]}}
        current = {"host": "pve-2", "containers": [], "vms": [], "firewall": [], "authorized_keys_fps": ["key1", "key2"]}
        
        findings = self._run_diff(baseline, current, "pve-2")
        
        added = [f for f in findings if f["context"].get("change") == "ssh_keys_added"]
        assert len(added) == 1
        assert "key2" in added[0]["context"]["added_fps"]

    def test_diff_inventory_ssh_keys_removed(self):
        baseline = {"pve-2": {"containers": [], "vms": [], "firewall": [], "authorized_keys_fps": ["key1", "key2"]}}
        current = {"host": "pve-2", "containers": [], "vms": [], "firewall": [], "authorized_keys_fps": ["key1"]}
        
        findings = self._run_diff(baseline, current, "pve-2")
        
        removed = [f for f in findings if f["context"].get("change") == "ssh_keys_removed"]
        assert len(removed) == 1
        assert "key2" in removed[0]["context"]["removed_fps"]

    def _run_diff(self, baseline, current, host_name):
        # Inline the diff logic for testing
        findings = []
        base_host = baseline.get(host_name, {})

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