"""Standalone worker process for a genuine cross-process Chugel race test
(tests/test_orchestrator_chugel.py, PruebaBloqueoGeneralizadoCrossProceso).
Not itself a test module -- invoked as a subprocess by the real test, one
process per contender, so the race is between two independent OS
processes, not just threads sharing a GIL.

Usage: python3 _chugel_cross_process_race_worker.py <repo_root> <missions_dir>
       <mission_id> <mode> <barrier_prefix> <index>

mode "reserve" calls chugel.reserve_dispatch(role="emilio", attempt=0).
mode "mutate" calls chugel.record_repository_state() with a distinct
worktree_path, an entirely unrelated field from the dispatch ledger --
exactly the kind of concurrent, unrelated Mission Record mutation Emma's
P2-1 finding identified as able to silently race and lose a reservation
before the mission-wide lock generalization."""

import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import orchestrator.chugel as chugel  # noqa: E402

chugel._MISSIONS_DIR = Path(sys.argv[2])
mission_id = sys.argv[3]
mode = sys.argv[4]
barrier_prefix = sys.argv[5]
index = sys.argv[6]

Path(f"{barrier_prefix}.{index}").write_text("ready")
deadline = time.time() + 5
while time.time() < deadline:
    if Path(f"{barrier_prefix}.0").exists() and Path(f"{barrier_prefix}.1").exists():
        break
    time.sleep(0.001)

try:
    if mode == "reserve":
        _, invocation_id = chugel.reserve_dispatch(mission_id, role="emilio", attempt=0)
        print(f"OK invocation_id={invocation_id}")
    elif mode == "mutate":
        chugel.record_repository_state(mission_id, {
            "worktree_path": f"/tmp/race-worktree-{index}",
            "branch": f"race/{index}",
            "base_sha": "c" * 40,
            "isolation_confirmed": True,
        })
        print(f"OK worktree_path=/tmp/race-worktree-{index}")
    else:
        raise ValueError(f"unknown mode {mode!r}")
except Exception as exc:
    print(f"ERROR {type(exc).__name__}: {exc}")
