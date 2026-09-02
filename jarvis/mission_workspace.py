"""Narrow Jarvis seam for deterministic per-mission workspace ownership."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from orchestrator import workspace

_SHA = re.compile(r"^[0-9a-f]{40}$")


class MissionWorkspaceError(Exception):
    pass


@dataclass(frozen=True)
class MissionWorkspaceBinding:
    worktree_path: str
    branch: str
    base_sha: str
    isolation_confirmed: bool
    head_sha: str


def derive_repository_placeholder(mission_id: str, base_sha: str) -> dict:
    """Pure canonical identity derivation; no lease, filesystem or Git effect."""
    if not isinstance(base_sha, str) or _SHA.fullmatch(base_sha) is None:
        raise MissionWorkspaceError("repository base_sha is not canonical")
    return {
        "worktree_path": "(unconfirmed)",
        "branch": workspace.derive_branch_name(mission_id),
        "base_sha": base_sha,
        "isolation_confirmed": False,
    }


class MissionWorkspaceManager:
    def __init__(self, base_root: Path, *, git_executable: str = "git", lease=None):
        self._base_root = Path(base_root)
        self._git_executable = git_executable
        self._lease = lease
        if lease is not None:
            try:
                workspace.validate_workspace_supervisor_lease(lease, self._base_root)
            except workspace.WorkspaceLeaseError as exc:
                raise MissionWorkspaceError(str(exc)) from exc

    @property
    def base_root(self) -> Path:
        return self._base_root

    def ensure(self, mission_id: str, repository: dict) -> MissionWorkspaceBinding:
        if self._lease is None:
            raise MissionWorkspaceError("workspace supervisor lease is not held")
        try:
            workspace.validate_workspace_supervisor_lease(self._lease, self._base_root)
        except workspace.WorkspaceLeaseError as exc:
            raise MissionWorkspaceError(str(exc)) from exc
        if repository.get("isolation_confirmed") is not False:
            raise MissionWorkspaceError("workspace is already confirmed")
        base_sha = repository.get("base_sha")
        if not isinstance(base_sha, str) or _SHA.fullmatch(base_sha) is None:
            raise MissionWorkspaceError("repository base_sha is not canonical")
        expected_path = workspace.derive_worktree_path(mission_id, self._base_root)
        expected_branch = workspace.derive_branch_name(mission_id)
        if repository.get("worktree_path") != "(unconfirmed)" or repository.get("branch") != expected_branch:
            raise MissionWorkspaceError("repository placeholder does not match deterministic identity")
        path = workspace.provision_mission_worktree(
            mission_id, base_root=self._base_root, base_sha=base_sha,
            git_executable=self._git_executable,
        )
        entry = workspace.verify_mission_worktree(
            mission_id, base_root=self._base_root, git_executable=self._git_executable,
        )
        return MissionWorkspaceBinding(str(path), expected_branch, base_sha, True, entry.head or "")

    def verify(self, mission_id: str, repository: dict) -> MissionWorkspaceBinding:
        if self._lease is None:
            raise MissionWorkspaceError("workspace supervisor lease is not held")
        try:
            workspace.validate_workspace_supervisor_lease(self._lease, self._base_root)
        except workspace.WorkspaceLeaseError as exc:
            raise MissionWorkspaceError(str(exc)) from exc
        if repository.get("isolation_confirmed") is not True:
            raise MissionWorkspaceError("workspace isolation is not confirmed")
        expected_path = workspace.derive_worktree_path(mission_id, self._base_root)
        expected_branch = workspace.derive_branch_name(mission_id)
        if repository.get("worktree_path") != str(expected_path) or repository.get("branch") != expected_branch:
            raise MissionWorkspaceError("canonical repository binding differs from deterministic identity")
        entry = workspace.verify_mission_worktree(
            mission_id, base_root=self._base_root, git_executable=self._git_executable,
        )
        return MissionWorkspaceBinding(
            str(expected_path), expected_branch, repository["base_sha"], True, entry.head or "",
        )
