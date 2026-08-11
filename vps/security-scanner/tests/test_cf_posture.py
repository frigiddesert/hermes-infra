import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cf_posture  # noqa: E402


class TestKnownNamesFromBaseline(unittest.TestCase):
    def test_parses_pipe_delimited_entries(self):
        baseline = {"workers": ["heimdall-hub|2026-08-11", "rimtours|2026-01-12"], "pages": ["rimtours|deploy:2026-08-11|rimtours.pages.dev"]}
        workers, pages = cf_posture.known_names_from_baseline(baseline)
        self.assertEqual(workers, {"heimdall-hub", "rimtours"})
        self.assertEqual(pages, {"rimtours"})


class TestRegisteredAppMatching(unittest.TestCase):
    def setUp(self):
        self.apps = [{"id": "heimdall-platform", "repo": "github.com/frigiddesert/heimdall"},
                     {"id": "contour-crm", "repo": "github.com/frigiddesert/contour-crm"}]
        self.tokens = cf_posture.registered_app_tokens(self.apps)

    def test_exact_match(self):
        self.assertTrue(cf_posture._is_registered("heimdall-platform", self.tokens))

    def test_repo_basename_match(self):
        self.assertTrue(cf_posture._is_registered("heimdall", self.tokens))

    def test_variant_suffix_still_matches(self):
        # e.g. contour-crm-mcp / contour-crm-worker are variants of a registered app
        self.assertTrue(cf_posture._is_registered("contour-crm-mcp", self.tokens))

    def test_unrelated_name_does_not_match(self):
        self.assertFalse(cf_posture._is_registered("qbo-oauth-callback", self.tokens))


class TestDiffDeployments(unittest.TestCase):
    def test_new_unregistered_worker_flagged(self):
        current = [{"name": "qbo-oauth-callback", "modified_on": "2026-02-02T00:00:00Z"}]
        new, stale = cf_posture.diff_deployments("worker", current, baseline_names=set(), app_tokens=set(), now=time.time())
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["name"], "qbo-oauth-callback")

    def test_known_baseline_worker_not_flagged(self):
        current = [{"name": "heimdall-hub", "modified_on": "2026-08-11T00:00:00Z"}]
        new, stale = cf_posture.diff_deployments("worker", current, baseline_names={"heimdall-hub"}, app_tokens=set(), now=time.time())
        self.assertEqual(new, [])

    def test_registered_app_variant_not_flagged(self):
        current = [{"name": "contour-crm-mcp", "modified_on": "2026-08-11T00:00:00Z"}]
        new, stale = cf_posture.diff_deployments("worker", current, baseline_names=set(), app_tokens={"contour-crm"}, now=time.time())
        self.assertEqual(new, [])

    def test_stale_worker_accumulates_not_flags_as_new(self):
        old_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 200 * 86400))
        current = [{"name": "old-worker", "modified_on": old_iso}]
        new, stale = cf_posture.diff_deployments("worker", current, baseline_names={"old-worker"}, app_tokens=set(), now=time.time())
        self.assertEqual(new, [])  # known, so not "new unregistered"
        self.assertEqual(len(stale), 1)
        self.assertGreaterEqual(stale[0]["age_days"], 90)

    def test_fresh_worker_not_stale(self):
        current = [{"name": "w", "modified_on": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())}]
        _, stale = cf_posture.diff_deployments("worker", current, baseline_names={"w"}, app_tokens=set(), now=time.time())
        self.assertEqual(stale, [])


class TestBypassEveryonePolicy(unittest.TestCase):
    def test_detects_bypass_with_everyone(self):
        apps = {"admin-panel": [{"decision": "bypass", "id": "p1", "name": "skip-auth", "include": [{"everyone": {}}]}]}
        hits = cf_posture.find_bypass_everyone_policies(apps)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["app"], "admin-panel")

    def test_ignores_non_bypass_decision(self):
        apps = {"app": [{"decision": "allow", "id": "p1", "include": [{"everyone": {}}]}]}
        self.assertEqual(cf_posture.find_bypass_everyone_policies(apps), [])

    def test_ignores_bypass_without_everyone(self):
        apps = {"app": [{"decision": "bypass", "id": "p1", "include": [{"email": {"email": "a@b.com"}}]}]}
        self.assertEqual(cf_posture.find_bypass_everyone_policies(apps), [])


class TestDiffApiTokens(unittest.TestCase):
    def test_new_token_detected(self):
        tokens = [{"id": "t1", "name": "new-token", "issued_on": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())}]
        newly_created, stale = cf_posture.diff_api_tokens(tokens, seen_token_ids=set(), now=time.time())
        self.assertEqual(len(newly_created), 1)

    def test_already_seen_token_not_new(self):
        tokens = [{"id": "t1", "name": "old", "issued_on": "2020-01-01T00:00:00Z"}]
        newly_created, _ = cf_posture.diff_api_tokens(tokens, seen_token_ids={"t1"}, now=time.time())
        self.assertEqual(newly_created, [])

    def test_unused_over_60_days_is_stale(self):
        old_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 90 * 86400))
        tokens = [{"id": "t1", "name": "stale", "last_used_on": old_iso}]
        _, stale = cf_posture.diff_api_tokens(tokens, seen_token_ids={"t1"}, now=time.time())
        self.assertEqual(len(stale), 1)


if __name__ == "__main__":
    unittest.main()
