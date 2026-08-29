"""orchestrator/gh_check_status.py::normalize_check_entry() -- the shared
per-entry normalizer for statusCheckRollup, used by both
publish_executor.py and merge_executor.py. The exact incident this
module resolves: PR #5's real payload mixed a StatusContext ("Vercel",
state=SUCCESS) with a CheckRun (status=COMPLETED, conclusion=SUCCESS);
code that only ever read conclusion/status treated the StatusContext
entry as permanently unresolved, because it has neither field. Test 7
below uses that exact captured payload."""

from __future__ import annotations

import unittest

from orchestrator.gh_check_status import normalize_check_entry


def _check_run(status: str, conclusion: str | None = None, **extra) -> dict:
    return {"__typename": "CheckRun", "status": status, "conclusion": conclusion, **extra}


def _status_context(state: str, context: str = "some-context", **extra) -> dict:
    return {"__typename": "StatusContext", "context": context, "state": state, **extra}


class CheckRunTests(unittest.TestCase):
    def test_completed_success_check_run(self):
        self.assertEqual(normalize_check_entry(_check_run("COMPLETED", "SUCCESS")), "SUCCESS")

    def test_in_progress_check_run_is_pending(self):
        self.assertEqual(normalize_check_entry(_check_run("IN_PROGRESS")), "PENDING")

    def test_completed_failure_check_run(self):
        self.assertEqual(normalize_check_entry(_check_run("COMPLETED", "FAILURE")), "FAILURE")


class StatusContextTests(unittest.TestCase):
    def test_success_status_context(self):
        self.assertEqual(normalize_check_entry(_status_context("SUCCESS")), "SUCCESS")

    def test_pending_status_context(self):
        self.assertEqual(normalize_check_entry(_status_context("PENDING")), "PENDING")

    def test_error_status_context(self):
        self.assertEqual(normalize_check_entry(_status_context("ERROR")), "FAILURE")


class RealPr5PayloadTests(unittest.TestCase):
    def test_real_pr5_status_context_entry_normalizes_to_success(self):
        """The exact live StatusContext entry captured from `gh pr view 5
        --json statusCheckRollup` on barzuna10-creator/Proyecta."""
        entry = {
            "__typename": "StatusContext",
            "context": "Vercel",
            "startedAt": "2026-08-29T03:55:09Z",
            "state": "SUCCESS",
            "targetUrl": "https://vercel.com/proyecta3/proyecta/GyyiseRmzmab56hSGiBsWRVi8B7Z",
        }
        self.assertEqual(normalize_check_entry(entry), "SUCCESS")

    def test_real_pr5_check_run_entry_normalizes_to_success(self):
        """The exact live CheckRun entry captured alongside it."""
        entry = {
            "__typename": "CheckRun",
            "completedAt": "2026-08-29T04:33:55Z",
            "conclusion": "SUCCESS",
            "detailsUrl": "https://vercel.com/github",
            "name": "Vercel Preview Comments",
            "startedAt": "2026-08-29T04:33:55Z",
            "status": "COMPLETED",
            "workflowName": "",
        }
        self.assertEqual(normalize_check_entry(entry), "SUCCESS")


class FailClosedTests(unittest.TestCase):
    def test_unrecognized_typename_fails_closed(self):
        self.assertEqual(normalize_check_entry({"__typename": "SomeFutureNodeType", "state": "SUCCESS"}), "FAILURE")

    def test_missing_typename_fails_closed(self):
        self.assertEqual(normalize_check_entry({"state": "SUCCESS"}), "FAILURE")

    def test_completed_check_run_with_no_conclusion_fails_closed(self):
        self.assertEqual(normalize_check_entry(_check_run("COMPLETED", conclusion=None)), "FAILURE")


if __name__ == "__main__":
    unittest.main()
