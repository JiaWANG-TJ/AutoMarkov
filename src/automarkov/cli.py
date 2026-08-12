from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from automarkov.api import compile_task
from automarkov.domain import validate_task_request_payload
from automarkov.pilots import PilotOutputCollisionError, run_engineering_pilot
from automarkov.provenance import verify_provenance


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automarkov")
    commands = parser.add_subparsers(dest="command", required=True)
    compile_parser = commands.add_parser("compile")
    compile_parser.add_argument("--request-id", required=True)
    compile_parser.add_argument("--task-text", required=True)
    provenance_parser = commands.add_parser("verify-provenance")
    provenance_parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
    )
    pilot_parser = commands.add_parser("pilot")
    pilot_commands = pilot_parser.add_subparsers(dest="pilot_command", required=True)
    pilot_run_parser = pilot_commands.add_parser("run")
    pilot_run_parser.add_argument("--manifest", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "verify-provenance":
        report = verify_provenance(args.repository_root)
        print(report.model_dump_json())
        return 0 if report.valid else 1
    if args.command == "pilot":
        if args.pilot_command != "run":  # pragma: no cover - argparse 限定集合。
            raise AssertionError("无法到达的 pilot 命令")
        try:
            report = run_engineering_pilot(
                args.manifest,
                repository_root=Path.cwd(),
            )
        except PilotOutputCollisionError as error:
            print(str(error), file=sys.stderr)
            return 5
        except KeyboardInterrupt:
            print("engineering pilot interrupted", file=sys.stderr)
            return 130
        except (OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 2
        except RuntimeError as error:
            print(str(error), file=sys.stderr)
            return 3
        print(report.model_dump_json())
        return report.worker_exit_code
    if args.command != "compile":  # pragma: no cover - argparse 限定命令集合。
        raise AssertionError("无法到达的命令")
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
