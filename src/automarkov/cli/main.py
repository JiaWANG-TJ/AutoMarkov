from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from automarkov.api import compile_task
from automarkov.domain.models import validate_task_request_payload
from automarkov.pilots import PilotOutputCollisionError, run_engineering_pilot
from automarkov.security.provenance import verify_provenance


def _compile(json_path: str) -> int:
    """compile CLI entry point."""
    try:
        data = Path(json_path).read_bytes()
        result = compile_task(validate_task_request_payload(json.loads(data)))
        print(result.model_dump_json())
        return 0
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _pilot(json_path: str) -> int:
    """pilot CLI entry point."""
    try:
        data = Path(json_path).read_bytes()
        result = run_engineering_pilot(json.loads(data))  # type: ignore
        print(result.model_dump_json())
        return 0
    except (PilotOutputCollisionError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _verify_provenance(repository_root: Path) -> int:
    """Verify the repository provenance catalog through the public CLI."""
    report = verify_provenance(repository_root)
    print(report.model_dump_json())
    return 0 if report.valid else 1


def _args(prog: str | None = None) -> Sequence[str]:
    return sys.argv[1:] if prog is None else sys.argv[2:]


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="automarkov")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("compile").add_argument("json_path")
    sub.add_parser("pilot").add_argument("json_path")
    provenance = sub.add_parser("verify-provenance")
    provenance.add_argument("--repository-root", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "compile":
        return _compile(args.json_path)
    if args.command == "pilot":
        return _pilot(args.json_path)
    if args.command == "verify-provenance":
        return _verify_provenance(args.repository_root)
    print(f"unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
