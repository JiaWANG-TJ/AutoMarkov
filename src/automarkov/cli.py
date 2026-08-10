from __future__ import annotations

import argparse
from collections.abc import Sequence

from automarkov.api import compile_task
from automarkov.domain import validate_task_request_payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automarkov")
    commands = parser.add_subparsers(dest="command", required=True)
    compile_parser = commands.add_parser("compile")
    compile_parser.add_argument("--request-id", required=True)
    compile_parser.add_argument("--task-text", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "compile":  # pragma: no cover - argparse owns this branch.
        raise AssertionError("unreachable command")
    request = validate_task_request_payload(
        {
            "schema_version": "automarkov.task-request.v1",
            "request_id": args.request_id,
            "task_text": args.task_text,
            "budget": {
                "schema_version": "automarkov.request-budget.v1",
                "wall_time_seconds": 60,
                "llm_token_limit": 0,
                "tool_call_limit": 0,
            },
            "permissions": {
                "schema_version": "automarkov.request-permissions.v1",
                "allow_retrieval": False,
                "allow_clarification": False,
                "allow_code_execution": False,
            },
        },
    )
    print(compile_task(request).model_dump_json())
    return 0
