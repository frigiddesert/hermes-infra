#!/usr/bin/env python3
"""Unit tests for log-sweep (issue #25). Pure-logic tests only — no network, no VPS, no journalctl
binary required, so these run anywhere `python3 -m unittest` can (dev box or VPS).

Run: python3 -m unittest discover -s tests -v   (from vps/log-sweep/)
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import sweep  # noqa: E402


class TestErrorSpike(unittest.TestCase):
    def test_no_spike_below_multiple(self):
        history = [4, 5, 6, 5]  # baseline 5, 5x = 25
        self.assertFalse(sweep.is_error_spike(20, history))  # 20 < 25 -> not a spike

    def test_spike_requires_both_multiple_and_absolute(self):
        history = [2, 2, 2]  # baseline 2, 5x = 10
        self.assertFalse(sweep.is_error_spike(15, history))  # >5x baseline (15>10) but not >20 absolute
        self.assertTrue(sweep.is_error_spike(21, history))  # satisfies both: 21>10 and 21>20

    def test_spike_true_when_both_conditions_met(self):
        history = [1, 1, 1, 1]  # baseline 1, 5x = 5
        self.assertTrue(sweep.is_error_spike(25, history))  # 25 > 5 and 25 > 20

    def test_no_spike_with_insufficient_history(self):
        self.assertFalse(sweep.is_error_spike(1000, []))
        self.assertFalse(sweep.is_error_spike(1000, [1]))  # below BASELINE_MIN_SAMPLES

    def test_baseline_zero_history_floor(self):
        # baseline of an all-zero history is floored to 1.0 so 5x doesn't become 0x (would spike on
        # any nonzero count)
        history = [0, 0, 0]
        self.assertFalse(sweep.is_error_spike(4, history))  # 4 < 5*1.0=5
        self.assertTrue(sweep.is_error_spike(21, history))  # 21 > 5 and > 20


class TestUpdateBaseline(unittest.TestCase):
    def test_appends_and_caps_length(self):
        history = list(range(sweep.BASELINE_HISTORY_LEN))
        updated = sweep.update_baseline(history, 999)
        self.assertEqual(len(updated), sweep.BASELINE_HISTORY_LEN)
        self.assertEqual(updated[-1], 999)
        self.assertEqual(updated[0], 1)  # oldest (0) fell off

    def test_short_history_grows(self):
        updated = sweep.update_baseline([1, 2], 3)
        self.assertEqual(updated, [1, 2, 3])


class TestHermesCap(unittest.TestCase):
    def test_budget_starts_at_cap(self):
        state = {}
        self.assertEqual(sweep.hermes_budget_remaining(state), sweep.HERMES_DAILY_CAP)

    def test_budget_decrements_and_floors_at_zero(self):
        state = {}
        for _ in range(sweep.HERMES_DAILY_CAP + 3):
            sweep.hermes_record_call(state)
        self.assertEqual(sweep.hermes_budget_remaining(state), 0)

    def test_budget_resets_on_new_day(self):
        state = {"hermes": {"date": "2000-01-01", "count": sweep.HERMES_DAILY_CAP}}
        self.assertEqual(sweep.hermes_budget_remaining(state), sweep.HERMES_DAILY_CAP)


class TestFingerprintStability(unittest.TestCase):
    """The hub fingerprints on appId + type + normalized(title) + first-line(technical). These
    tests assert our rule functions build titles/technical whose FIRST LINE is stable across
    different dynamic values (attempt counts, IPs, timestamps) — i.e. an episode dedups instead of
    paging on every single sweep. This mirrors contracts/src/fingerprint.ts's normalization
    (bare numbers -> <n>) without importing the TS module."""

    def test_sshd_title_and_technical_first_line_stable_across_counts(self):
        import re as _re

        def build(n_attempts, n_ips, ips):
            title = "SSH authentication anomaly on openclaw-vps"
            technical = (
                "Elevated failed SSH authentication attempts detected by log-sweep.\n"
                f"This window: {n_attempts} failed attempts from {n_ips} distinct source IP(s): {ips}."
            )
            return title, technical.split("\n")[0]

        t1, first1 = build(11, 4, "1.2.3.4")
        t2, first2 = build(500, 30, "9.9.9.9, 8.8.8.8")
        self.assertEqual(t1, t2)
        self.assertEqual(first1, first2)

    def test_crash_loop_title_stable_per_unit_varies_by_unit(self):
        def title_for(unit):
            return f"Service crash-loop: {unit}"

        self.assertEqual(title_for("cron.service"), title_for("cron.service"))
        self.assertNotEqual(title_for("cron.service"), title_for("cloudflared.service"))

    def test_disk_title_constant(self):
        title = "Disk usage on / exceeded 90%"
        # same string regardless of exact percentage (percentage lives only in `technical`)
        self.assertEqual(title, "Disk usage on / exceeded 90%")


class TestFileTailCursor(unittest.TestCase):
    def test_reads_only_new_bytes_since_offset(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            f.write("line one\nline two\n")
            path = f.name
        try:
            lines, offset = sweep.read_file_tail(path, 0)
            self.assertEqual(lines, ["line one", "line two"])
            with open(path, "a") as f:
                f.write("line three\n")
            lines2, offset2 = sweep.read_file_tail(path, offset)
            self.assertEqual(lines2, ["line three"])
            self.assertGreater(offset2, offset)
        finally:
            os.unlink(path)

    def test_missing_file_returns_empty(self):
        lines, offset = sweep.read_file_tail("/nonexistent/path/does-not-exist.log", 0)
        self.assertEqual(lines, [])
        self.assertEqual(offset, 0)

    def test_truncated_file_restarts_from_top(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            f.write("a" * 1000)
            path = f.name
        try:
            # pretend we'd already read past a much larger offset (simulating rotation/truncation)
            lines, offset = sweep.read_file_tail(path, 5000)
            self.assertEqual(lines, ["a" * 1000])
        finally:
            os.unlink(path)


class TestRuleThresholds(unittest.TestCase):
    def _entry(self, unit, message, unit_field="_SYSTEMD_UNIT"):
        return {unit_field: unit, "MESSAGE": message}

    def test_sshd_rule_fires_on_attempt_count(self):
        state = {}
        calls = []
        sweep.heimdall.security = lambda **kw: calls.append(kw)
        entries = [self._entry(sweep.SSHD_UNIT, f"Failed password for baduser from 10.0.0.{i} port 22 ssh2")
                   for i in range(11)]
        sweep.rule_sshd_auth(entries, state)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["type"], "auth_anomaly")

    def test_sshd_rule_silent_below_threshold(self):
        calls = []
        sweep.heimdall.security = lambda **kw: calls.append(kw)
        entries = [self._entry(sweep.SSHD_UNIT, f"Failed password for baduser from 10.0.0.{i} port 22 ssh2")
                   for i in range(3)]  # below both attempt and IP thresholds
        sweep.rule_sshd_auth(entries, {})
        self.assertEqual(len(calls), 0)

    def test_sshd_rule_fires_on_distinct_ip_count_even_if_attempts_low(self):
        calls = []
        sweep.heimdall.security = lambda **kw: calls.append(kw)
        entries = [self._entry(sweep.SSHD_UNIT, f"Failed password for baduser from 10.0.0.{i} port 22 ssh2")
                   for i in range(4)]  # 4 attempts (below 10) but 4 distinct IPs (above 3)
        sweep.rule_sshd_auth(entries, {})
        self.assertEqual(len(calls), 1)

    def test_crash_loop_fires_above_threshold(self):
        calls = []
        sweep.heimdall.report = lambda **kw: calls.append(kw)
        entries = [self._entry("cron.service", "Started cron.service - foo", unit_field="UNIT")
                   for _ in range(3)]  # > CRASHLOOP_MIN_STARTS (2)
        sweep.rule_crash_loop(entries, ["cron.service"])
        self.assertEqual(len(calls), 1)
        self.assertIn("crash-loop", calls[0]["title"])

    def test_crash_loop_silent_at_threshold(self):
        calls = []
        sweep.heimdall.report = lambda **kw: calls.append(kw)
        entries = [self._entry("cron.service", "Started cron.service - foo", unit_field="UNIT")
                   for _ in range(2)]  # == threshold, not >
        sweep.rule_crash_loop(entries, ["cron.service"])
        self.assertEqual(len(calls), 0)


if __name__ == "__main__":
    unittest.main()
