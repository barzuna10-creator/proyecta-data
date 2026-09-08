"""M6 (BLOCKED-State Resume Wiring & Unified Recovery Contract) --
mechanically proves docs/zentra/RECOVERY_CONTRACT_V1.md's
`# PHANTOM_BLOCKED_OUT_EDGES` fenced block never silently drifts from the
real code. Parses orchestrator/validator.py::TRANSITIONS and
jarvis/mission_write.py::_RESUMABLE_PRIOR_STATES at runtime (never
hand-copied numbers), computes the real phantom-edge set, parses the
document's own fenced block via ast.literal_eval per non-comment line,
and asserts exact set equality -- not merely equal counts. If TRANSITIONS
or _RESUMABLE_PRIOR_STATES is ever extended (wiring one of the 11 phantom
edges today, or adding a new BLOCKED-reachable state), this test fails
loudly until the document is updated to match."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from orchestrator.validator import TRANSITIONS
from jarvis.mission_write import _RESUMABLE_PRIOR_STATES

ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "zentra" / "RECOVERY_CONTRACT_V1.md"

_FENCE_HEADER = "# PHANTOM_BLOCKED_OUT_EDGES"


def _real_blocked_edges() -> set[tuple[str, str]]:
    return {edge for edge in TRANSITIONS if edge[0] == "BLOCKED"}


def _real_resumable_edges() -> set[tuple[str, str]]:
    return {("BLOCKED", state) for state in _RESUMABLE_PRIOR_STATES}


def _real_phantom_edges() -> set[tuple[str, str]]:
    return _real_blocked_edges() - _real_resumable_edges()


def _parse_phantom_edges_from_doc() -> set[tuple[str, str]]:
    text = DOC_PATH.read_text(encoding="utf-8")
    # The fenced block is the first ``` ... ``` block whose first line is
    # the literal header -- located structurally (via the header text),
    # never by a fixed line number, so reflowing surrounding prose can
    # never silently break this test's ability to find the block.
    match = re.search(
        r"```\n" + re.escape(_FENCE_HEADER) + r"\n(.*?)```",
        text, re.DOTALL,
    )
    if match is None:
        raise AssertionError(
            f"could not locate a fenced code block starting with {_FENCE_HEADER!r} "
            f"in {DOC_PATH}"
        )
    edges: set[tuple[str, str]] = set()
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = ast.literal_eval(line)
        if not (isinstance(parsed, tuple) and len(parsed) == 2 and all(isinstance(x, str) for x in parsed)):
            raise AssertionError(f"malformed edge literal in {DOC_PATH}: {line!r}")
        edges.add(parsed)
    return edges


class RecoveryContractDocSyncTests(unittest.TestCase):
    def test_doc_exists(self):
        self.assertTrue(DOC_PATH.is_file(), DOC_PATH)

    def test_pinned_edge_counts(self):
        blocked_edges = _real_blocked_edges()
        resumable_edges = _real_resumable_edges()
        self.assertEqual(17, len(blocked_edges), blocked_edges)
        # Zero orphans: every _RESUMABLE_PRIOR_STATES member must be a
        # real TRANSITIONS edge target -- a typo'd or stale entry in
        # _RESUMABLE_PRIOR_STATES that names a state with no
        # ("BLOCKED", state) edge at all would otherwise pass silently.
        orphans = resumable_edges - blocked_edges
        self.assertEqual(set(), orphans, "resumable state with no real BLOCKED edge")
        self.assertEqual(6, len(resumable_edges & blocked_edges))
        self.assertEqual(11, len(_real_phantom_edges()))

    def test_doc_phantom_edges_exactly_match_the_real_code(self):
        real = _real_phantom_edges()
        documented = _parse_phantom_edges_from_doc()
        self.assertEqual(real, documented)

    def test_doc_phantom_edge_count_matches_pinned_count(self):
        self.assertEqual(11, len(_parse_phantom_edges_from_doc()))


if __name__ == "__main__":
    unittest.main()
