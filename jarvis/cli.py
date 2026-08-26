"""Deterministic, read-only Jarvis Mission 002 command-line surface."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Sequence, TextIO
import sys

from jarvis import mission_query
from jarvis.knowledge_storage import (
    FileKnowledgeStore, KnowledgeNotFound, KnowledgePathUnsafe, KnowledgeCorrupt,
)
from jarvis.knowledge import knowledge_entry_to_dict
from jarvis.knowledge_retrieval import KnowledgeSearchResponse, search
from jarvis.repository_freshness import FreshnessError, RepositoryFreshnessResolver


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
    payload = [asdict(item) for item in value] if isinstance(value, tuple) else (knowledge_entry_to_dict(value) if value.__class__.__name__ == "KnowledgeEntry" else asdict(value))
    json.dump(payload, destination, sort_keys=True, separators=(",", ":"))
    destination.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
