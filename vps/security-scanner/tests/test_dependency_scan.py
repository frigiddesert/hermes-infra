import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dependency_scan  # noqa: E402


class TestFindingsFromDependabot(unittest.TestCase):
    def test_filters_to_critical_and_high_open(self):
        alerts = [
            {"state": "open", "security_advisory": {"severity": "critical", "ghsa_id": "GHSA-1", "summary": "s1"},
             "dependency": {"package": {"name": "left-pad"}}, "html_url": "u1"},
            {"state": "open", "security_advisory": {"severity": "low", "ghsa_id": "GHSA-2", "summary": "s2"},
             "dependency": {"package": {"name": "ok-pkg"}}, "html_url": "u2"},
            {"state": "dismissed", "security_advisory": {"severity": "critical", "ghsa_id": "GHSA-3", "summary": "s3"},
             "dependency": {"package": {"name": "dismissed-pkg"}}, "html_url": "u3"},
            {"state": "open", "security_advisory": {"severity": "high", "ghsa_id": "GHSA-4", "summary": "s4"},
             "dependency": {"package": {"name": "another"}}, "html_url": "u4"},
        ]
        findings = dependency_scan._findings_from_dependabot("myrepo", alerts)
        keys = {f["key"] for f in findings}
        self.assertEqual(keys, {"dep:myrepo:GHSA-1", "dep:myrepo:GHSA-4"})

    def test_finding_key_is_stable_per_advisory(self):
        alerts = [{"state": "open", "security_advisory": {"severity": "critical", "ghsa_id": "GHSA-1", "summary": "s"},
                    "dependency": {"package": {"name": "p"}}, "html_url": "u"}]
        f1 = dependency_scan._findings_from_dependabot("repo", alerts)
        f2 = dependency_scan._findings_from_dependabot("repo", alerts)
        self.assertEqual(f1[0]["key"], f2[0]["key"])


class TestRunDedup(unittest.TestCase):
    def test_already_known_findings_are_not_reposted(self):
        repos = [{"repo": "r1", "app_id": "app1", "dependabot": True}]
        state = {"dep:r1:GHSA-1": {"first_seen": "x"}}

        def fake_scan_repo(repo_cfg):
            return [{
                "key": "dep:r1:GHSA-1", "repo": "r1", "severity": "critical",
                "package": "p", "ghsa_id": "GHSA-1", "summary": "s", "url": "u", "source": "dependabot",
            }]

        orig = dependency_scan.scan_repo
        dependency_scan.scan_repo = fake_scan_repo
        try:
            new_wrappers, _ = dependency_scan.run(repos, state)
        finally:
            dependency_scan.scan_repo = orig
        self.assertEqual(new_wrappers, [])

    def test_new_finding_produces_event(self):
        repos = [{"repo": "r1", "app_id": "app1", "dependabot": True}]
        state = {}

        def fake_scan_repo(repo_cfg):
            return [{
                "key": "dep:r1:GHSA-9", "repo": "r1", "severity": "high",
                "package": "p", "ghsa_id": "GHSA-9", "summary": "s", "url": "u", "source": "dependabot",
            }]

        orig = dependency_scan.scan_repo
        dependency_scan.scan_repo = fake_scan_repo
        try:
            new_wrappers, _ = dependency_scan.run(repos, state)
        finally:
            dependency_scan.scan_repo = orig
        self.assertEqual(len(new_wrappers), 1)
        self.assertEqual(new_wrappers[0]["event"]["appId"], "app1")
        self.assertEqual(new_wrappers[0]["event"]["type"], "dependency_vuln")

    def test_one_repo_failing_does_not_abort_others(self):
        repos = [{"repo": "bad", "app_id": "a"}, {"repo": "good", "app_id": "b"}]
        state = {}

        def fake_scan_repo(repo_cfg):
            if repo_cfg["repo"] == "bad":
                raise RuntimeError("boom")
            return [{
                "key": "dep:good:GHSA-5", "repo": "good", "severity": "critical",
                "package": "p", "ghsa_id": "GHSA-5", "summary": "s", "url": "u", "source": "dependabot",
            }]

        orig = dependency_scan.scan_repo
        dependency_scan.scan_repo = fake_scan_repo
        try:
            new_wrappers, _ = dependency_scan.run(repos, state)
        finally:
            dependency_scan.scan_repo = orig
        self.assertEqual(len(new_wrappers), 1)
        self.assertEqual(new_wrappers[0]["finding"]["repo"], "good")


if __name__ == "__main__":
    unittest.main()
