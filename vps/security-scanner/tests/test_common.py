import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common  # noqa: E402


class TestStateRoundtrip(unittest.TestCase):
    def test_save_and_load_state(self):
        with mock.patch.object(common, "STATE_DIR", Path(self._tmp())):
            common.save_state("dependency", {"dep:foo:GHSA-1": {"severity": "critical"}})
            loaded = common.load_state("dependency")
            self.assertEqual(loaded["dep:foo:GHSA-1"]["severity"], "critical")

    def test_load_missing_state_is_empty(self):
        with mock.patch.object(common, "STATE_DIR", Path(self._tmp())):
            self.assertEqual(common.load_state("nope"), {})

    def test_load_corrupt_state_is_empty(self):
        d = Path(self._tmp())
        with mock.patch.object(common, "STATE_DIR", d):
            d.mkdir(parents=True, exist_ok=True)
            (d / "broken.json").write_text("{not json")
            self.assertEqual(common.load_state("broken"), {})

    def _tmp(self):
        import tempfile
        return tempfile.mkdtemp(prefix="secscan-test-")


class TestFirstRunFlag(unittest.TestCase):
    def test_first_run_true_then_false(self):
        import tempfile
        d = Path(tempfile.mkdtemp(prefix="secscan-test-"))
        with mock.patch.object(common, "STATE_DIR", d):
            self.assertTrue(common.is_first_run())
            common.mark_first_run_done()
            self.assertFalse(common.is_first_run())


class TestFirstRunSeeding(unittest.TestCase):
    def _finding(self, sev):
        return {"key": f"k-{sev}-{id(object())}", "event": {"severity": sev, "title": "t"}}

    def test_caps_at_five_most_severe(self):
        findings = (
            [self._finding("critical") for _ in range(3)]
            + [self._finding("error") for _ in range(4)]
            + [self._finding("warn") for _ in range(5)]
        )
        to_page, to_seed = common.apply_first_run_seeding(findings)
        self.assertEqual(len(to_page), 5)
        self.assertEqual(len(to_seed), len(findings) - 5)
        # the 5 paged must be the most severe: 3 critical + 2 error
        severities = sorted((f["event"]["severity"] for f in to_page))
        self.assertEqual(severities, sorted(["critical", "critical", "critical", "error", "error"]))

    def test_fewer_than_cap_pages_all(self):
        findings = [self._finding("critical"), self._finding("warn")]
        to_page, to_seed = common.apply_first_run_seeding(findings)
        self.assertEqual(len(to_page), 2)
        self.assertEqual(to_seed, [])


class TestSecretResolution(unittest.TestCase):
    def test_env_var_wins(self):
        with mock.patch.dict("os.environ", {"HEIMDALL_SERVICE_KEY": "from-env"}):
            self.assertEqual(common.resolve_service_key(), "from-env")

    def test_falls_back_to_file(self):
        import tempfile
        d = Path(tempfile.mkdtemp(prefix="secscan-test-"))
        env_file = d / ".env"
        env_file.write_text("HEIMDALL_SERVICE_KEY=from-file\n")
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("HEIMDALL_SERVICE_KEY", None)
            with mock.patch.object(common, "DEFAULT_SECRETS_FILE", env_file):
                with mock.patch.object(common, "HERMES_ENV_FILE", Path("/nonexistent")):
                    self.assertEqual(common.resolve_service_key(), "from-file")

    def test_none_when_nowhere_found(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("HEIMDALL_SERVICE_KEY", None)
            with mock.patch.object(common, "DEFAULT_SECRETS_FILE", Path("/nonexistent1")):
                with mock.patch.object(common, "HERMES_ENV_FILE", Path("/nonexistent2")):
                    self.assertIsNone(common.resolve_service_key())


class TestMakeEvent(unittest.TestCase):
    def test_shape_matches_contract(self):
        e = common.make_event("app1", "dependency_vuln", "critical", "title", "tech", {"a": 1})
        self.assertEqual(e["appId"], "app1")
        self.assertEqual(e["type"], "dependency_vuln")
        self.assertEqual(e["severity"], "critical")
        self.assertIn("ts", e)
        self.assertEqual(e["context"], {"a": 1})


if __name__ == "__main__":
    unittest.main()
