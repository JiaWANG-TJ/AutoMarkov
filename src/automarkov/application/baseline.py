"""R04: Pre-flight baseline gate for the entire AutoMarkov codebase.

Validates that every foundational check is green before any further work
proceeds. Runs six independent checks and reports PASS/FAIL per step.

Runnable as CLI: ``python -m automarkov.baseline_gate``
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field

from automarkov.domain.models import StrictFrozenModel

_SCHEMA_VERSION: Literal["automarkov.baseline-gate-report.v1"] = (
    "automarkov.baseline-gate-report.v1"
)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_KEY_TEST_PATHS = (
    "tests/contract/test_statistics.py",
    "tests/contract/test_ablation_ledger.py",
    "tests/contract/test_benchmark_suites.py",
    "tests/contract/test_generation_methods.py",
    "tests/contract/test_policy_export.py",
    "tests/contract/test_policy_evaluation_request.py",
    "tests/contract/test_release_pipeline.py",
    "tests/contract/test_import_boundaries.py",
    "tests/contract/test_validation_claims.py",
    "tests/security/test_checkpoint_boundary.py",
)
_EXPECTED_EVENT_TYPE_COUNT = 28


class BaselineGateReport(StrictFrozenModel):
    """Typed result of every pre-flight check step."""

    schema_version: Literal["automarkov.baseline-gate-report.v1"] = (
        "automarkov.baseline-gate-report.v1"
    )
    ruff_pass: bool = Field(strict=True)
    pyright_pass: bool = Field(strict=True)
    tests_pass: bool = Field(strict=True)
    freeze_pass: bool = Field(strict=True)
    provenance_pass: bool = Field(strict=True)
    schema_pass: bool = Field(strict=True)
    is_ready: bool = Field(strict=True)
    missing_field_evidence: tuple[str, ...] = ()


def _run_subprocess(
    argv: tuple[str, ...],
    *,
    timeout: int = 120,
) -> tuple[bool, str]:
    """Run a subprocess and return ``(ok, summary_line)``."""
    try:
        completed = subprocess.run(
            argv,
            cwd=_REPO_ROOT,
            check=False,
            capture_output=True,
            timeout=timeout,
        )
        output = completed.stdout + completed.stderr
        last_line = output.decode("utf-8", errors="replace").strip().split("\n")[-1]
        return completed.returncode == 0, last_line
    except FileNotFoundError:
        return False, f"command not found: {argv[0]}"
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    except OSError as error:
        return False, str(error)


def _check_ruff() -> tuple[bool, str]:
    """Run ``ruff check src/``."""
    ok, line = _run_subprocess(("ruff", "check", "src/"))
    return ok, f"ruff: {'PASS' if ok else 'FAIL'} -- {line}"


def _check_pyright() -> tuple[bool, str]:
    """Run ``pyright`` type checking."""
    ok, line = _run_subprocess(("uv", "run", "--locked", "pyright"))
    return ok, f"pyright: {'PASS' if ok else 'FAIL'} -- {line}"


def _check_tests() -> tuple[bool, str]:
    """Run the key contract test suite."""
    ok, line = _run_subprocess(
        ("pytest", *_KEY_TEST_PATHS, "-x"),
        timeout=180,
    )
    return ok, f"tests: {'PASS' if ok else 'FAIL'} -- {line}"


def _check_freeze_gate() -> tuple[bool, str]:
    """Validate that ``freeze_gate.py`` parses and the schema is intact."""
    try:
        import ast

        ast.parse(
            (_REPO_ROOT / "src/automarkov/application/freeze.py").read_text()
        )
        from automarkov.application.freeze import (
            FreezeGateReport,
            PredicateKind,
            PredicateVerdict,
            check_freeze_gate,
            preflight_experiment,
        )

        assert FreezeGateReport is not None
        assert len(PredicateKind.__args__) == 15
        assert PredicateVerdict.model_fields["is_satisfied"].is_required()
        assert check_freeze_gate is not None
        assert preflight_experiment is not None
        return True, "freeze gate: PASS -- schema and 15 predicates intact"
    except (SyntaxError, ImportError, AssertionError, ValueError) as exc:
        return False, f"freeze gate: FAIL -- {exc}"


def _check_schema_registry() -> tuple[bool, str]:
    """Validate the event schema registry completeness."""
    try:
        from automarkov.lifecycle import (
            _CORE_EVENTS,
            default_event_schema_registry,
        )

        if len(_CORE_EVENTS) != _EXPECTED_EVENT_TYPE_COUNT:
            return (
                False,
                f"schema registry: FAIL -- {_EXPECTED_EVENT_TYPE_COUNT} expected event types, found {len(_CORE_EVENTS)}",
            )

        registry = default_event_schema_registry()
        snapshot = registry.snapshot()
        if len(snapshot) < _EXPECTED_EVENT_TYPE_COUNT:
            return (
                False,
                f"schema registry: FAIL -- registered {len(snapshot)} entries, expected {_EXPECTED_EVENT_TYPE_COUNT}",
            )

        for entry in snapshot:
            if len(entry) != 3:
                return (
                    False,
                    f"schema registry: FAIL -- malformed entry {entry!r}",
                )

        return (
            True,
            f"schema registry: PASS -- {len(snapshot)} registered event types, registry frozen",
        )
    except (SyntaxError, ImportError, RuntimeError, ValueError) as exc:
        return False, f"schema registry: FAIL -- {exc}"


def _check_provenance() -> tuple[bool, str]:
    """Run the repository provenance verification."""
    try:
        from automarkov.provenance import verify_provenance

        report = verify_provenance(_REPO_ROOT)
        if report.valid:
            return (
                True,
                f"provenance: PASS -- {report.profile_count} profiles, {report.upstream_count} upstreams, {len(report.passed_checks)} checks passed",
            )
        error_count = len(report.errors)
        preview = report.errors[0] if error_count else "unknown"
        return (
            False,
            f"provenance: FAIL -- {error_count} error(s), first: {preview}",
        )
    except (ImportError, OSError, ValueError) as exc:
        return False, f"provenance: FAIL -- {exc}"


def run_baseline_gate() -> BaselineGateReport:
    """Run all six pre-flight checks steps and return typed report."""
    ruff_ok, _ = _check_ruff()
    pyright_ok, _ = _check_pyright()
    tests_ok, _ = _check_tests()
    freeze_ok, _ = _check_freeze_gate()
    prov_ok, _ = _check_provenance()
    schema_ok, _ = _check_schema_registry()

    is_ready = all(
        (ruff_ok, pyright_ok, tests_ok, freeze_ok, prov_ok, schema_ok)
    )
    missing: list[str] = []
    for ok, field_name in (
        (ruff_ok, "ruff"),
        (pyright_ok, "pyright"),
        (tests_ok, "tests"),
        (freeze_ok, "freeze_gate"),
        (prov_ok, "provenance"),
        (schema_ok, "schema_registry"),
    ):
        if not ok:
            missing.append(field_name)

    return BaselineGateReport(
        ruff_pass=ruff_ok,
        pyright_pass=pyright_ok,
        tests_pass=tests_ok,
        freeze_pass=freeze_ok,
        provenance_pass=prov_ok,
        schema_pass=schema_ok,
        is_ready=is_ready,
        missing_field_evidence=tuple(missing),
    )


_STATUS = {True: "PASS", False: "FAIL"}


def main() -> int:
    """CLI entry point for ``python -m automarkov.baseline_gate``."""
    report = run_baseline_gate()

    print("=" * 60)
    print("AutoMarkov Baseline Gate Report")
    print("=" * 60)
    print(f"  Ruff check:       {_STATUS[report.ruff_pass]}")
    print(f"  Pyright check:      {_STATUS[report.pyright_pass]}")
    print(f"  Contract tests:    {_STATUS[report.tests_pass]}")
    print(f"  Freeze gate:       {_STATUS[report.freeze_pass]}")
    print(f"  Provenance:        {_STATUS[report.provenance_pass]}")
    print(f"  Schema registry:   {_STATUS[report.schema_pass]}")
    print("-" * 60)
    print(f"  Overall is_ready:   {_STATUS[report.is_ready]}")
    if report.missing_field_evidence:
        print(f"  Missing:            {', '.join(report.missing_field_evidence)}")
    print("=" * 60)

    return 0 if report.is_ready else 1


if __name__ == "__main__":
    sys.exit(main())

__all__ = [
    "BaselineGateReport",
    "run_baseline_gate",
]