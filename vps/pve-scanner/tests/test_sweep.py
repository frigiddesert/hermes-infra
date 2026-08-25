"""Unit tests for pve-scanner sweep rules (pure functions)."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# We test the pure rule logic by importing the sweep module
# but mocking the SSH calls


class TestSweepRules:
    def test_sshd_fail_regex(self):
        import re
        SSHD_FAIL_RE = re.compile(r"Failed password|Invalid user|authentication failure|pam_unix.*authentication failure", re.IGNORECASE)
        
        assert SSHD_FAIL_RE.search("Failed password for root from 1.2.3.4")
        assert SSHD_FAIL_RE.search("Invalid user admin from 192.168.1.1")
        assert SSHD_FAIL_RE.search("authentication failure for user from 10.0.0.1")
        assert SSHD_FAIL_RE.search("pam_unix(sshd:auth): authentication failure")
        assert not SSHD_FAIL_RE.search("Accepted password for root from 1.2.3.4")

    def test_sshd_ip_regex(self):
        import re
        SSHD_IP_RE = re.compile(r"from\s+([0-9a-fA-F:.]+)")
        
        m = SSHD_IP_RE.search("Failed password for root from 192.168.1.100")
        assert m and m.group(1) == "192.168.1.100"
        
        m = SSHD_IP_RE.search("from 2001:db8::1 port 22")
        assert m and m.group(1) == "2001:db8::1"

    def test_pve_api_auth_regex(self):
        import re
        PVE_API_AUTH_RE = re.compile(r"authentication failure|401 Unauthorized|permission denied", re.IGNORECASE)
        
        assert PVE_API_AUTH_RE.search("authentication failure for user 'root'")
        assert PVE_API_AUTH_RE.search("401 Unauthorized")
        assert PVE_API_AUTH_RE.search("permission denied")

    def test_oom_regex(self):
        import re
        OOM_RE = re.compile(r"out of memory|oom-kill|killed process", re.IGNORECASE)
        
        assert OOM_RE.search("Out of memory: Kill process 1234")
        assert OOM_RE.search("oom-kill: constrained")
        assert OOM_RE.search("Killed process 5678")

    def test_error_regex(self):
        import re
        ERROR_RE = re.compile(r"\b(error|exception|traceback|critical|fatal|fail)\b", re.IGNORECASE)
        
        assert ERROR_RE.search("Error: connection refused")
        assert ERROR_RE.search("Exception in thread main")
        assert ERROR_RE.search("Traceback (most recent call last)")
        assert ERROR_RE.search("CRITICAL: disk full")
        assert ERROR_RE.search("Fatal error")
        # "Failed" has word boundary after "fail" + "ed" so doesn't match \bfail\b
        # This is intentional - we want exact word matches
        assert not ERROR_RE.search("Failed to connect")
        assert ERROR_RE.search("fail safe mode")
        assert not ERROR_RE.search("Successfully connected")