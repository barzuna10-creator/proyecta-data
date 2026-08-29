"""Jarvis Mission 002/005 command-line surface. Mostly read-only
(missions/status/knowledge show/search); `knowledge propose-source` is
the sole write path, and even it only ever creates an unauthorized,
awaiting-review KnowledgeCandidate -- the exact same Emma-review +
José-authorization + promote() sequence in jarvis.knowledge_storage,
unmodified, still gates anything it produces from ever becoming a
citable KnowledgeEntry."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Sequence, TextIO
import sys

from jarvis import mission_query
from jarvis.knowledge_storage import (
    FileKnowledgeStore, KnowledgeNotFound, KnowledgePathUnsafe, KnowledgeCorrupt,
)
from jarvis.knowledge import (
    KnowledgeApplicability, KnowledgeCandidateContent, RepositoryBinding,
    build_candidate_envelope, knowledge_entry_to_dict, require_explicit_tier,
)
from jarvis.knowledge_retrieval import KnowledgeSearchResponse, search
from jarvis.repository_freshness import FreshnessError, RepositoryFreshnessResolver
from jarvis.zentra_evidence import ZentraSourcesPolicyError, gather_evidence, load_policy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvis")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("missions", help="list canonical missions")
    status = commands.add_parser("status", help="show one bounded mission status")
    status.add_argument("mission_id")
    knowledge = commands.add_parser("knowledge", help="read trusted knowledge")
    knowledge_commands = knowledge.add_subparsers(dest="knowledge_command", required=True)
    show = knowledge_commands.add_parser("show", help="show one exact knowledge entry")
    show.add_argument("knowledge_id")
    show.add_argument("--store-root", required=True)
    search_command = knowledge_commands.add_parser("search", help="deterministic bounded eligibility search")
    search_command.add_argument("--store-root", required=True)
    search_command.add_argument("--repository-root", required=True)
    search_command.add_argument("--product-area", action="append", default=[])
    search_command.add_argument("--top-k", type=int, default=10)
    propose = knowledge_commands.add_parser(
        "propose-source",
        help="create an unauthorized KnowledgeCandidate from one policy-allow-listed Zentra source file",
    )
    propose.add_argument("--store-root", required=True)
    propose.add_argument("--repository-root", required=True, help="local git checkout containing the policy's authorized commit")
    propose.add_argument("--candidate-id", required=True)
    propose.add_argument("--evidence-id", required=True)
    propose.add_argument("--path", required=True, help="must be one of jarvis/zentra_sources_policy.json's exact allow-listed paths")
    propose.add_argument("--claim", required=True, help="human-authored claim text -- never generated from the file content")
    propose.add_argument("--product-area", action="append", required=True, default=[])
    propose.add_argument("--target-knowledge-id", default=None, help="set only when revising an existing entry")
    propose.add_argument("--expected-target-revision", type=int, default=None)
    propose.add_argument("--expected-current-status", default=None, choices=["active", "stale", "conflicted", "superseded", "retired"])
    propose.add_argument("--proposed-entry-status", default="active", choices=["active", "stale", "conflicted", "superseded", "retired"])
    propose.add_argument("--supersedes", action="append", default=[], help="knowledge_id(s) this candidate's content supersedes")
    propose.add_argument("--contradicts", action="append", default=[])
    return parser


def _search_payload(response: KnowledgeSearchResponse) -> dict:
    return {
        "results": [
            {"entry": knowledge_entry_to_dict(item.entry), "match_reasons": list(item.match_reasons), "rank": item.rank}
            for item in response.results
        ],
        "omitted_count": response.omitted_count,
        "eligible_beyond_top_k": response.eligible_beyond_top_k,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    output: TextIO | None = None,
    error: TextIO | None = None,
) -> int:
    args = _parser().parse_args(argv)
    destination = output or sys.stdout
    try:
        if args.command == "missions":
            value = mission_query.list_missions()
        elif args.command == "status":
            value = mission_query.get_mission_status(args.mission_id)
        elif args.knowledge_command == "show":
            value = FileKnowledgeStore(Path(args.store_root)).get_latest_entry(args.knowledge_id)
        elif args.knowledge_command == "propose-source":
            policy = load_policy()
            evidence, matched = gather_evidence(
                args.path, repository_root=Path(args.repository_root),
                evidence_id=args.evidence_id, claim=args.claim, policy=policy,
            )
            content = KnowledgeCandidateContent(
                schema_version="1.0", candidate_id=args.candidate_id, revision=1,
                created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                target_knowledge_id=args.target_knowledge_id,
                expected_target_revision=args.expected_target_revision,
                expected_current_status=args.expected_current_status,
                proposed_entry_status=args.proposed_entry_status,
                claim=args.claim, label="FACT",
                applicability=KnowledgeApplicability(tuple(args.product_area)),
                repository_binding=RepositoryBinding(policy.authorized_ref, policy.authorized_commit_sha),
                research_evidence=(evidence,),
                contradicts=tuple(args.contradicts), supersedes=tuple(args.supersedes),
                tier=matched.tier,
            )
            require_explicit_tier(content)
            envelope = build_candidate_envelope(content)
            FileKnowledgeStore(Path(args.store_root)).save_candidate(envelope)
            payload = {
                "candidate_id": envelope.content.candidate_id, "revision": envelope.content.revision,
                "content_digest": envelope.content_digest, "tier": envelope.content.tier,
                "path": args.path, "commit_sha": policy.authorized_commit_sha,
            }
            json.dump(payload, destination, sort_keys=True, separators=(",", ":"))
            destination.write("\n")
            return 0
        else:
            store = FileKnowledgeStore(Path(args.store_root))
            resolver = RepositoryFreshnessResolver(Path(args.repository_root))
            response = search(store, resolver, product_areas=tuple(args.product_area), top_k=args.top_k)
            payload = _search_payload(response)
            json.dump(payload, destination, sort_keys=True, separators=(",", ":"))
            destination.write("\n")
            return 0
    except mission_query.MissionQueryError as exc:
        (error or sys.stderr).write(f"ERROR {exc.code}\n")
        return 2
    except (KnowledgeNotFound, KnowledgePathUnsafe, KnowledgeCorrupt, ValueError) as exc:
        code = exc.code if hasattr(exc, "code") else "KNOWLEDGE_ID_INVALID"
        (error or sys.stderr).write(f"ERROR {code}\n")
        return 2
    except FreshnessError as exc:
        (error or sys.stderr).write(f"ERROR {exc.code}\n")
        return 2
    except ZentraSourcesPolicyError as exc:
        (error or sys.stderr).write(f"ERROR {exc.code}\n")
        return 2
    payload = [asdict(item) for item in value] if isinstance(value, tuple) else (knowledge_entry_to_dict(value) if value.__class__.__name__ == "KnowledgeEntry" else asdict(value))
    json.dump(payload, destination, sort_keys=True, separators=(",", ":"))
    destination.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
