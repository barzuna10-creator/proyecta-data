"""Focused tests for orchestrator/deploy_recovery_policy.py --
recovery_action_for() is a pure function over an exhaustive table; these
tests exercise every classification x recovery_status x budget
combination the frozen M4 design specifies, plus the exhaustion override
and the fail-closed DENY default."""

from __future__ import annotations

import unittest

from orchestrator.deploy_recovery_policy import RecoveryAction, recovery_action_for

_PLAIN_RESUME_ELIGIBLE = (
    "ANCESTRY_UNVERIFIABLE", "HEALTH_UNREACHABLE_OR_MALFORMED", "IDENTITY_NEVER_OBSERVED",
)
_ALWAYS_REOPEN_REQUIRED = (
    "OBSERVATION_BUDGET_EXHAUSTED", "IDENTITY_MISMATCH_DEFINITIVE",
    "HEALTH_DEGRADED", "IDENTITY_CONTRADICTED_DURING_HEALTH_CHECK",
)


class RecoveryExhaustionOverrideTests(unittest.TestCase):
    def test_exhausted_recovery_status_always_wins_regardless_of_classification(self):
        for classification in _PLAIN_RESUME_ELIGIBLE + _ALWAYS_REOPEN_REQUIRED + (None, "IDENTITY_NEVER_OBSERVED"):
            with self.subTest(classification=classification):
                action = recovery_action_for(
                    classification, recovery_status="exhausted", remaining_budget_seconds=900.0
                )
                self.assertEqual(action, RecoveryAction.RECOVERY_EXHAUSTED)


class PlainResumeEligibleTests(unittest.TestCase):
    def test_plain_resume_when_budget_remains(self):
        for classification in _PLAIN_RESUME_ELIGIBLE:
            with self.subTest(classification=classification):
                action = recovery_action_for(
                    classification, recovery_status="available", remaining_budget_seconds=1.0
                )
                self.assertEqual(action, RecoveryAction.PLAIN_RESUME)

    def test_zero_or_negative_budget_falls_through_to_exceptional_or_deny(self):
        # ANCESTRY_UNVERIFIABLE/HEALTH_UNREACHABLE_OR_MALFORMED are not in
        # the always-reopen-required set, so exhausted budget with those
        # two falls all the way to DENY (fails closed, never guesses).
        for classification in ("ANCESTRY_UNVERIFIABLE", "HEALTH_UNREACHABLE_OR_MALFORMED"):
            with self.subTest(classification=classification):
                action = recovery_action_for(
                    classification, recovery_status="available", remaining_budget_seconds=0.0
                )
                self.assertEqual(action, RecoveryAction.DENY)

        # IDENTITY_NEVER_OBSERVED is in BOTH sets -- with budget exhausted
        # it falls through to the always-reopen-required branch instead.
        action = recovery_action_for(
            "IDENTITY_NEVER_OBSERVED", recovery_status="available", remaining_budget_seconds=0.0
        )
        self.assertEqual(action, RecoveryAction.EXCEPTIONAL_REOPEN_REQUIRED)


class ExceptionalReopenRequiredTests(unittest.TestCase):
    def test_always_requires_reopen_regardless_of_budget(self):
        for classification in _ALWAYS_REOPEN_REQUIRED:
            for remaining in (900.0, 0.0, -5.0):
                with self.subTest(classification=classification, remaining=remaining):
                    action = recovery_action_for(
                        classification, recovery_status="available", remaining_budget_seconds=remaining
                    )
                    self.assertEqual(action, RecoveryAction.EXCEPTIONAL_REOPEN_REQUIRED)

    def test_identity_never_observed_with_budget_remaining_is_plain_resume_not_reopen(self):
        # IDENTITY_NEVER_OBSERVED appears in both frozensets -- the design's
        # own ordering means budget-remaining wins (checked first).
        action = recovery_action_for(
            "IDENTITY_NEVER_OBSERVED", recovery_status="available", remaining_budget_seconds=1.0
        )
        self.assertEqual(action, RecoveryAction.PLAIN_RESUME)


class DenyFallbackTests(unittest.TestCase):
    def test_unrecognized_classification_denies(self):
        action = recovery_action_for(
            "NOT_A_REAL_CLASSIFICATION", recovery_status="available", remaining_budget_seconds=900.0
        )
        self.assertEqual(action, RecoveryAction.DENY)

    def test_none_classification_with_budget_denies(self):
        action = recovery_action_for(None, recovery_status="available", remaining_budget_seconds=900.0)
        self.assertEqual(action, RecoveryAction.DENY)


if __name__ == "__main__":
    unittest.main()
