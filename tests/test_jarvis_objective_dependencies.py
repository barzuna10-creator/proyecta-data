"""jarvis/objective_dependencies.py -- M7 (Program-Level Planning Depth).
Pure-function tests: no I/O, no Chugel, no jarvis.storage anywhere in this
file -- every input is a plain dict/tuple constructed by hand."""

import unittest

from jarvis.objective_dependencies import (
    dependency_status,
    failed_dependencies,
    failed_root_cause_for,
    unmet_dependencies,
)


class UnmetDependenciesTests(unittest.TestCase):
    def test_missing_mission_is_unmet(self):
        self.assertEqual(("a",), unmet_dependencies(("a",), {}))

    def test_pre_merged_state_is_unmet(self):
        self.assertEqual(("a",), unmet_dependencies(("a",), {"a": "BUILDING"}))

    def test_merged_or_later_is_not_unmet(self):
        for state in ("MERGED", "DEPLOY_PENDING", "VERIFYING_PRODUCTION", "COMPLETED"):
            with self.subTest(state=state):
                self.assertEqual((), unmet_dependencies(("a",), {"a": state}))

    def test_failed_state_is_not_unmet(self):
        # failed_dependencies(), not unmet_dependencies(), is the
        # classification a terminally failed prerequisite gets.
        for state in ("FAILED", "CANCELLED", "ROLLED_BACK"):
            with self.subTest(state=state):
                self.assertEqual((), unmet_dependencies(("a",), {"a": state}))

    def test_only_the_unmet_subset_is_returned(self):
        result = unmet_dependencies(("a", "b", "c"), {"a": "MERGED", "b": "BUILDING"})
        self.assertEqual(("b", "c"), result)


class FailedDependenciesTests(unittest.TestCase):
    def test_immediate_terminal_failure_is_failed(self):
        self.assertEqual(("a",), failed_dependencies(("a",), {"a": "FAILED"}, {}))
        self.assertEqual(("a",), failed_dependencies(("a",), {"a": "CANCELLED"}, {}))
        self.assertEqual(("a",), failed_dependencies(("a",), {"a": "ROLLED_BACK"}, {}))

    def test_missing_or_pre_merged_is_not_failed(self):
        self.assertEqual((), failed_dependencies(("a",), {}, {}))
        self.assertEqual((), failed_dependencies(("a",), {"a": "BUILDING"}, {}))

    def test_satisfied_is_not_failed(self):
        self.assertEqual((), failed_dependencies(("a",), {"a": "MERGED"}, {}))

    def test_transitive_failure_two_deep(self):
        # C depends on B, B depends on A. A is FAILED. B has not been
        # authorized into a mission at all yet (absent from the state
        # map) -- still transitively failed because ITS OWN dependency
        # (A) is failed.
        depends_on_by_draft_id = {"b": ("a",), "c": ("b",)}
        state = {"a": "FAILED"}
        self.assertEqual(("b",), failed_dependencies(("b",), state, depends_on_by_draft_id))

    def test_transitive_failure_via_c_depends_on_b(self):
        depends_on_by_draft_id = {"b": ("a",), "c": ("b",)}
        state = {"a": "FAILED"}
        # Evaluate C's OWN depends_on tuple (b,) -- b is transitively failed.
        self.assertEqual(("b",), failed_dependencies(depends_on_by_draft_id["c"], state, depends_on_by_draft_id))

    def test_a_satisfied_link_breaks_the_chain(self):
        # C depends on B; B depends on A. A failed, but B itself already
        # MERGED (its own state, not a re-derivation) -- B is satisfied,
        # so C's dependency on B is NOT failed, regardless of A.
        depends_on_by_draft_id = {"b": ("a",), "c": ("b",)}
        state = {"a": "FAILED", "b": "MERGED"}
        self.assertEqual((), failed_dependencies(("b",), state, depends_on_by_draft_id))

    def test_only_the_failed_subset_is_returned(self):
        state = {"a": "FAILED", "b": "MERGED"}
        result = failed_dependencies(("a", "b", "c"), state, {})
        self.assertEqual(("a",), result)

    def test_cycle_terminates_deterministically_and_never_raises(self):
        """Defense-in-depth (M7 correction #4): even a hypothetical cyclic
        depends_on_by_draft_id (never produced by validate_objective(),
        which forbids this at persistence time) must not recurse forever.
        A cycle with no terminal failure anywhere in it resolves to "not
        failed" for every member -- never a stack overflow, never a hang."""
        cyclic = {"a": ("b",), "b": ("a",)}
        result = failed_dependencies(("a", "b"), {}, cyclic)
        self.assertEqual((), result)

    def test_cycle_with_a_real_failure_still_resolves(self):
        # a -> b -> c -> a (cycle), but c is itself terminally failed --
        # a and b must both resolve as transitively failed without hanging.
        cyclic = {"a": ("b",), "b": ("c",), "c": ("a",)}
        state = {"c": "FAILED"}
        result = failed_dependencies(("a", "b"), state, cyclic)
        self.assertEqual({"a", "b"}, set(result))


class DependencyStatusTests(unittest.TestCase):
    def test_no_dependencies_is_vacuously_satisfied(self):
        self.assertEqual("satisfied", dependency_status((), {}, {}))

    def test_failed_takes_precedence_over_unmet(self):
        state = {"a": "FAILED", "b": "BUILDING"}
        self.assertEqual("failed", dependency_status(("a", "b"), state, {}))

    def test_all_satisfied_is_satisfied(self):
        state = {"a": "MERGED", "b": "COMPLETED"}
        self.assertEqual("satisfied", dependency_status(("a", "b"), state, {}))

    def test_any_unmet_and_none_failed_is_unmet(self):
        state = {"a": "MERGED", "b": "BUILDING"}
        self.assertEqual("unmet", dependency_status(("a", "b"), state, {}))


class FailedRootCauseForTests(unittest.TestCase):
    def test_none_when_not_failed(self):
        self.assertIsNone(failed_root_cause_for("c", {"a": "MERGED"}, {"c": ("a",)}))

    def test_finds_the_root_two_levels_down(self):
        # c -> b -> a; a is FAILED. failed_root_cause_for(c, ...) must
        # name A, not B (B is only transitively failed, never itself the
        # terminal failure).
        depends_on_by_draft_id = {"b": ("a",), "c": ("b",)}
        state = {"a": "FAILED"}
        self.assertEqual("a", failed_root_cause_for("c", state, depends_on_by_draft_id))

    def test_immediate_failure_is_its_own_root_cause(self):
        self.assertEqual("a", failed_root_cause_for("c", {"a": "FAILED"}, {"c": ("a",)}))

    def test_cycle_terminates_and_returns_none_with_no_real_failure(self):
        cyclic = {"a": ("b",), "b": ("a",)}
        self.assertIsNone(failed_root_cause_for("a", {}, cyclic))


if __name__ == "__main__":
    unittest.main()
