"""Bounded, read-only GitHub observation for policy-declared repositories."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

from orchestrator.gh_check_status import normalize_check_entry

_TIMEOUT = 10.0
_MAX_OUTPUT = 256_000
_PR_FIELDS = "number,title,headRefName,headRefOid,baseRefName,isDraft,mergeStateStatus,statusCheckRollup,updatedAt,url"
_RUN_FIELDS = "databaseId,headSha,status,conclusion,event,workflowName,createdAt,updatedAt,url"
_SHA = re.compile(r"\A[0-9a-f]{40}\Z")


class GitHubQueryError(Exception):
    code = "GITHUB_QUERY_FAILED"


@dataclass(frozen=True, slots=True)
class GitHubObservation:
    repository: str
    observed_at: str
    pull_requests: tuple[dict, ...]
    workflow_runs: tuple[dict, ...]


class ReadOnlyGitHubQuery:
    def __init__(self, executable: Path, allowed_repositories: frozenset[str], *, inherited_environment: dict[str, str] | None = None):
        executable = Path(executable)
        if not executable.is_absolute() or executable.is_symlink() or not executable.is_file():
            raise GitHubQueryError("unsafe executable")
        self._executable = executable
        self._allowed = allowed_repositories
        inherited = dict(os.environ if inherited_environment is None else inherited_environment)
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GH_HOST", "GH_REPO", "GH_CONFIG_DIR", "XDG_CONFIG_HOME"):
            inherited.pop(key, None)
        inherited.update({"GH_PROMPT_DISABLED":"1", "LC_ALL":"C", "LANG":"C"})
        self._environment = inherited

    def _run(self, argv: list[str]) -> tuple[dict, ...]:
        try:
            result = subprocess.run(
                [str(self._executable), *argv], shell=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                timeout=_TIMEOUT, env=dict(self._environment),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitHubQueryError("GitHub query unavailable") from exc
        if result.returncode != 0 or len(result.stdout) > _MAX_OUTPUT or len(result.stderr) > 8192:
            raise GitHubQueryError("GitHub query failed closed")
        try: payload = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc: raise GitHubQueryError("GitHub output invalid") from exc
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise GitHubQueryError("GitHub output shape invalid")
        return tuple(json.loads(json.dumps(item)) for item in payload)

    def observe(self, repository: str) -> GitHubObservation:
        if repository not in self._allowed or not repository.startswith("github.com/") or repository.count("/") != 2:
            raise GitHubQueryError("repository not allowed")
        raw_prs = self._run(["pr", "list", "--repo", repository, "--state", "open", "--limit", "20", "--json", _PR_FIELDS])
        raw_runs = self._run(["run", "list", "--repo", repository, "--branch", "main", "--limit", "10", "--json", _RUN_FIELDS])
        try:
            def text(item: dict, key: str, limit: int) -> str:
                value = item[key]
                if not isinstance(value, str) or not value or len(value) > limit:
                    raise ValueError(key)
                return value
            def sha(item: dict, key: str) -> str:
                value = text(item, key, 40)
                if _SHA.fullmatch(value) is None: raise ValueError(key)
                return value
            prs = tuple({
                "number": item["number"], "title": text(item,"title",500),
                "headRefName": text(item,"headRefName",200), "headRefOid": sha(item,"headRefOid"),
                "baseRefName": text(item,"baseRefName",200), "isDraft": item["isDraft"],
                "mergeStateStatus": text(item,"mergeStateStatus",50), "updatedAt": text(item,"updatedAt",50),
                "url": text(item,"url",500),
                "checks": tuple(normalize_check_entry(check) for check in item["statusCheckRollup"][:50]),
            } for item in raw_prs[:20] if isinstance(item["number"], int) and not isinstance(item["number"], bool) and isinstance(item["isDraft"], bool) and isinstance(item["statusCheckRollup"], list))
            runs = tuple({
                "databaseId": item["databaseId"], "headSha": sha(item,"headSha"),
                "status": text(item,"status",50), "conclusion": None if item["conclusion"] is None else text(item,"conclusion",50),
                "event": text(item,"event",50), "workflowName": text(item,"workflowName",200),
                "createdAt": text(item,"createdAt",50), "updatedAt": text(item,"updatedAt",50), "url": text(item,"url",500),
            } for item in raw_runs[:10] if isinstance(item["databaseId"], int) and not isinstance(item["databaseId"], bool))
            if len(prs) != len(raw_prs[:20]) or len(runs) != len(raw_runs[:10]): raise ValueError("typed fields")
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubQueryError("GitHub output schema invalid") from exc
        observed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return GitHubObservation(repository, observed_at, prs, runs)
