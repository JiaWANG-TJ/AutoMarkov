from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

from automarkov.pilots import (
    DISCLAIMER,
    EngineeringPilotWorkerResponse,
    PilotEvaluationMetrics,
    PilotExecutionResult,
    PilotOutputCollisionError,
    PilotPackageVersions,
    PilotTrainingMetrics,
    _run_bounded_process,
    run_engineering_pilot,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "pilots/cartpole_cpu_smoke.v1.json"


def _executor(
    request: object, manifest: object, workspace: Path, root: Path
) -> PilotExecutionResult:
    del request, manifest, workspace
    assert root == ROOT
    return PilotExecutionResult(
        exit_code=0,
        response=EngineeringPilotWorkerResponse(
            schema_version="automarkov.engineering-pilot-worker-response.v1",
            pilot_id="cartpole_cpu_smoke_v1",
            status="success",
            package_versions=PilotPackageVersions(
                gymnasium="1.2.2", ray="2.56.1", torch="2.13.0"
            ),
            python_version="3.11.13",
            training=PilotTrainingMetrics(
                iterations_completed=1,
                num_env_steps_sampled_lifetime=512,
                episode_return_mean_decimal="20.5",
                episode_length_mean_decimal="20.5",
            ),
            evaluation=PilotEvaluationMetrics(
                episodes_completed=1,
                episode_return_mean_decimal="42",
                episode_length_mean_decimal="42",
            ),
            reason_code=None,
            error_type=None,
            error_message=None,
        ),
        stdout=b"",
        stderr=b"",
        wall_time_ms=1250,
        started_at="2026-08-12T12:00:00Z",
        finished_at="2026-08-12T12:00:01.25Z",
    )


def test_orchestrator_atomically_writes_six_nonconfirmatory_outputs_and_reuses(
    tmp_path: Path,
) -> None:
    calls = 0

    def executor(*args: object) -> PilotExecutionResult:
        nonlocal calls
        calls += 1
        return _executor(*args)  # type: ignore[arg-type]

    first = run_engineering_pilot(
        MANIFEST, repository_root=ROOT, output_root=tmp_path, executor=executor
    )
    second = run_engineering_pilot(
        MANIFEST, repository_root=ROOT, output_root=tmp_path, executor=executor
    )
    assert first == second
    assert calls == 1
    assert first.status == "passed"
    assert first.disclaimer == DISCLAIMER
    assert first.publication_eligible is False
    assert first.python_version == "3.11.13"
    assert first.source_tree_hash.startswith("sha256:")
    assert tuple(item.path for item in first.source_file_hashes) == (
        "pilots/cartpole_cpu_smoke.v1.json",
        "src/automarkov/cli.py",
        "src/automarkov/pilot_worker.py",
        "src/automarkov/pilots.py",
    )

    directory = tmp_path / "cartpole_cpu_smoke_v1"
    assert {item.name for item in directory.iterdir()} == {
        "pilot_manifest.json",
        "artifacts.sqlite3",
        "metrics.jsonl",
        "terminal_record.json",
        "compact_report.json",
        "compact_report.md",
    }
    assert DISCLAIMER in (directory / "compact_report.md").read_text(encoding="utf-8")
    with sqlite3.connect(directory / "artifacts.sqlite3") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        }
        assert tables == {
            "pilot_artifact_parents",
            "pilot_artifacts",
            "pilot_store_meta",
        }
        types = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT artifact_type FROM pilot_artifacts"
            )
        }
        assert types == {
            "engineering_pilot_manifest",
            "engineering_pilot_metrics",
            "engineering_pilot_resource_usage",
            "engineering_pilot_report",
        }


def test_orchestrator_never_overwrites_partial_output(tmp_path: Path) -> None:
    directory = tmp_path / "cartpole_cpu_smoke_v1"
    directory.mkdir()
    (directory / "operator-note.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(PilotOutputCollisionError):
        run_engineering_pilot(
            MANIFEST, repository_root=ROOT, output_root=tmp_path, executor=_executor
        )
    assert (directory / "operator-note.txt").read_text(encoding="utf-8") == "preserve"


def test_execution_result_rejects_zero_exit_with_terminal_failure() -> None:
    with pytest.raises(ValueError, match="exit code"):
        PilotExecutionResult(
            exit_code=0,
            response=EngineeringPilotWorkerResponse(
                schema_version="automarkov.engineering-pilot-worker-response.v1",
                pilot_id="cartpole_cpu_smoke_v1",
                status="terminal_failure",
                package_versions=PilotPackageVersions(
                    gymnasium="1.2.2", ray="2.56.1", torch="2.13.0"
                ),
                python_version="3.11.13",
                training=None,
                evaluation=None,
                reason_code="worker_execution_failed",
                error_type="RuntimeError",
                error_message="failed",
            ),
            stdout=b"",
            stderr=b"",
            wall_time_ms=1,
            started_at="2026-08-12T12:00:00Z",
            finished_at="2026-08-12T12:00:00.001Z",
        )


def test_bounded_process_captures_only_the_configured_stream_prefix(
    tmp_path: Path,
) -> None:
    result = _run_bounded_process(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('o'*10000); sys.stderr.write('e'*10000)",
        ],
        cwd=tmp_path,
        env={},
        timeout_seconds=5,
        max_stdout_bytes=17,
        max_stderr_bytes=19,
    )
    assert result.returncode == 0
    assert result.stdout == b"o" * 17
    assert result.stderr == b"e" * 19


def test_bounded_process_timeout_terminates_the_worker_process_group(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "child.pid"
    script = (
        "import subprocess,sys,time;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        f"open({str(pid_path)!r},'w').write(str(p.pid));"
        "time.sleep(30)"
    )
    result = _run_bounded_process(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={},
        timeout_seconds=1,
        max_stdout_bytes=32,
        max_stderr_bytes=32,
    )
    child_pid = int(pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert result.timed_out is True
    assert not Path(f"/proc/{child_pid}").exists()


def test_pilot_store_rejects_the_old_formal_repository_schema(tmp_path: Path) -> None:
    directory = tmp_path / "cartpole_cpu_smoke_v1"
    directory.mkdir()
    with sqlite3.connect(directory / "artifacts.sqlite3") as connection:
        connection.execute("CREATE TABLE run_events(run_id TEXT)")
    for name in (
        "pilot_manifest.json",
        "metrics.jsonl",
        "terminal_record.json",
        "compact_report.json",
        "compact_report.md",
    ):
        (directory / name).write_text("{}", encoding="utf-8")
    with pytest.raises(PilotOutputCollisionError):
        run_engineering_pilot(
            MANIFEST, repository_root=ROOT, output_root=tmp_path, executor=_executor
        )
