"""Unit tests for pve-scanner common utilities."""
from __future__ import annotations

import json
import tempfile
import os
from pathlib import Path

import pytest

# Add parent to path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common


class TestCommon:
    def test_severity_key(self):
        assert common.severity_key("critical") == 3
        assert common.severity_key("error") == 2
        assert common.severity_key("warn") == 1
        assert common.severity_key("info") == 0
        assert common.severity_key("unknown") == -1

    def test_make_event(self):
        evt = common.make_event("test-app", "auth_anomaly", "error", "Test title", "tech details", {"host": "pve-2"})
        assert evt["app_id"] == "test-app"
        assert evt["type"] == "auth_anomaly"
        assert evt["severity"] == "error"
        assert evt["title"] == "Test title"
        assert evt["technical"] == "tech details"
        assert evt["context"]["host"] == "pve-2"

    def test_apply_first_run_seeding(self):
        # Test non-first-run
        findings = [
            {"severity": "critical", "title": "crit"},
            {"severity": "error", "title": "err"},
            {"severity": "warn", "title": "warn"},
        ]
        to_page, to_seed = common.apply_first_run_seeding(findings)
        # Without first_run.flag, all should page
        assert len(to_page) == 3
        assert len(to_seed) == 0

    def test_fingerprint_keys(self):
        keys_text = """ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@host
ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC... user@host2
# comment line
"""
        fps = common.fingerprint_keys(keys_text)
        assert len(fps) == 2
        assert all(len(fp) == 32 for fp in fps)

    def test_load_save_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            state_dir.mkdir()

            # Monkey-patch STATE_DIR
            original_state_dir = common.STATE_DIR
            common.STATE_DIR = state_dir

            try:
                common.save_state("test", {"key": "value", "num": 42})
                loaded = common.load_state("test")
                assert loaded == {"key": "value", "num": 42}

                # Non-existent state returns empty dict
                assert common.load_state("nonexistent") == {}
            finally:
                common.STATE_DIR = original_state_dir

    def test_first_run_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            state_dir.mkdir()

            original_state_dir = common.STATE_DIR
            common.STATE_DIR = state_dir

            try:
                assert common.is_first_run() is True
                common.mark_first_run_done()
                assert common.is_first_run() is False
            finally:
                common.STATE_DIR = original_state_dir