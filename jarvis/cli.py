"""Deterministic, read-only Jarvis Mission 002 command-line surface."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from typing import Sequence, TextIO
import sys

from jarvis import mission_query


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvis")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("missions", help="list canonical missions")
    status = commands.add_parser("status", help="show one bounded mission status")
    status.add_argument("mission_id")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    output: TextIO | None = None,
    error: TextIO | None = None,
) -> int:
    args = _parser().parse_args(argv)
    destination = output or sys.stdout
    try:
        value = (
            mission_query.list_missions()
            if args.command == "missions"
            else mission_query.get_mission_status(args.mission_id)
        )
    except mission_query.MissionQueryError as exc:
        (error or sys.stderr).write(f"ERROR {exc.code}\n")
        return 2
    payload = [asdict(item) for item in value] if isinstance(value, tuple) else asdict(value)
    json.dump(payload, destination, sort_keys=True, separators=(",", ":"))
    destination.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
