from __future__ import annotations

import os
import shutil
import signal
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import monotonic_ns
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from automarkov.domain.canonical import SafeCanonicalInt, canonical_json_bytes
from automarkov.domain.models import StrictFrozenModel
from automarkov.lifecycle import ArtifactReference, ProcessExecutionTerminalRecord
from automarkov.security.provenance import load_runtime_profiles

PROFILE_MANIFEST_HASH = (
    "sha256:d655179755175ee164710a92dbf3b6220b80fff919a6ea67483b59b1de389428"
)
LOCK_HASH = "sha256:9e62ccf2a7c768b05aae4e078f97459f51aeb04c5d2bdf5ab682ca4061d34f6a"
DISCLAIMER = (
    "NONCONFIRMATORY engineering evidence; excluded from confirmatory statistics "
    "and publication claims."
)
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_DECIMAL_PATTERN = r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
_OUTPUT_NAMES = frozenset(
    {
        "pilot_manifest.json",
        "artifacts.sqlite3",
        "metrics.jsonl",
        "terminal_record.json",
        "compact_report.json",
        "compact_report.md",
    }
)
_SOURCE_PATHS = (
    "pilots/cartpole_cpu_smoke.v1.json",
    "src/automarkov/cli.py",
    "src/automarkov/pilot_worker.py",
    "src/automarkov/pilots.py",
)


class PilotPackageVersions(StrictFrozenModel):
    gymnasium: Literal["1.2.2"]
    ray: Literal["2.56.1"]
    torch: Literal["2.13.0"]


class PilotRuntimeProfile(StrictFrozenModel):
    profile_id: Literal["rllib-core"]
    profile_manifest_hash: Literal[
        "sha256:d655179755175ee164710a92dbf3b6220b80fff919a6ea67483b59b1de389428"
    ]
    lock_hash: Literal[
        "sha256:9e62ccf2a7c768b05aae4e078f97459f51aeb04c5d2bdf5ab682ca4061d34f6a"
    ]
    python_version: Literal["3.11.13"]
    worker_interpreter: Literal[
        "artifacts/pilots/runtime/rllib-core-py31113/bin/python"
    ]
    worker_module: Literal["automarkov.pilot_worker"]
    package_versions: PilotPackageVersions


class PilotWorkload(StrictFrozenModel):
    environment_id: Literal["CartPole-v1"]
    environment_upstream_commit: Literal["a923da5d4415a1aa5195d99341069da5e16deed7"]
    algorithm: Literal["PPO"]
    framework: Literal["torch"]
    seed: SafeCanonicalInt
    train_iterations: SafeCanonicalInt
    train_batch_size_per_learner: SafeCanonicalInt
    minibatch_size: SafeCanonicalInt
    num_epochs: SafeCanonicalInt
    num_env_runners: SafeCanonicalInt
    num_envs_per_env_runner: SafeCanonicalInt
    num_learners: SafeCanonicalInt
    num_gpus_per_learner: SafeCanonicalInt
    evaluation_episodes: SafeCanonicalInt
    evaluation_explore: bool
    checkpoint_policy: Literal["none"]

    @model_validator(mode="after")
    def require_exact_workload(self) -> PilotWorkload:
        actual = self.model_dump(mode="json")
        expected = {
            "environment_id": "CartPole-v1",
            "environment_upstream_commit": "a923da5d4415a1aa5195d99341069da5e16deed7",
            "algorithm": "PPO",
            "framework": "torch",
            "seed": 20260812,
            "train_iterations": 1,
            "train_batch_size_per_learner": 512,
            "minibatch_size": 128,
            "num_epochs": 2,
            "num_env_runners": 0,
            "num_envs_per_env_runner": 1,
            "num_learners": 0,
            "num_gpus_per_learner": 0,
            "evaluation_episodes": 1,
            "evaluation_explore": False,
            "checkpoint_policy": "none",
        }
        if actual != expected:
            raise ValueError("workload differs from the closed P0 contract")
        return self


class PilotLimits(StrictFrozenModel):
    wall_time_seconds: SafeCanonicalInt
    ray_num_cpus: SafeCanonicalInt
    max_stdout_bytes: SafeCanonicalInt
    max_stderr_bytes: SafeCanonicalInt

    @model_validator(mode="after")
    def require_exact_limits(self) -> PilotLimits:
        if self.model_dump(mode="json") != {
            "wall_time_seconds": 300,
            "ray_num_cpus": 2,
            "max_stdout_bytes": 1048576,
            "max_stderr_bytes": 1048576,
        }:
            raise ValueError("limits differ from the closed P0 contract")
        return self


class EngineeringPilotManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.engineering-pilot-manifest.v1"]
    pilot_id: Literal["cartpole_cpu_smoke_v1"]
    purpose: Literal["engineering"]
    experiment_eligibility: Literal["nonconfirmatory"]
    publication_eligible: bool
    runtime_profile: PilotRuntimeProfile
    workload: PilotWorkload
    limits: PilotLimits
    output_root: Literal["artifacts/pilots"]

    @model_validator(mode="after")
    def require_nonconfirmatory(self) -> EngineeringPilotManifest:
        if self.publication_eligible is not False:
            raise ValueError("engineering pilot is never publication eligible")
        return self


class EngineeringPilotWorkerRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.engineering-pilot-worker-request.v1"]
    pilot_id: Literal["cartpole_cpu_smoke_v1"]
    profile_id: Literal["rllib-core"]
    environment_id: Literal["CartPole-v1"]
    algorithm: Literal["PPO"]
    framework: Literal["torch"]
    seed: Literal[20260812]
    train_iterations: Literal[1]
    train_batch_size_per_learner: Literal[512]
    minibatch_size: Literal[128]
    num_epochs: Literal[2]
    num_env_runners: Literal[0]
    num_envs_per_env_runner: Literal[1]
    num_learners: Literal[0]
    num_gpus_per_learner: Literal[0]
    evaluation_episodes: Literal[1]
    evaluation_explore: Literal[False]
    checkpoint_policy: Literal["none"]
    wall_time_seconds: Literal[300]
    ray_num_cpus: Literal[2]
    max_stdout_bytes: Literal[1048576]
    max_stderr_bytes: Literal[1048576]


DecimalString = Annotated[str, Field(strict=True, pattern=_DECIMAL_PATTERN)]


class PilotTrainingMetrics(StrictFrozenModel):
    iterations_completed: Literal[1]
    num_env_steps_sampled_lifetime: SafeCanonicalInt
    episode_return_mean_decimal: DecimalString | None
    episode_length_mean_decimal: DecimalString | None


class PilotEvaluationMetrics(StrictFrozenModel):
    episodes_completed: Literal[1]
    episode_return_mean_decimal: DecimalString
    episode_length_mean_decimal: DecimalString


class EngineeringPilotWorkerResponse(StrictFrozenModel):
    schema_version: Literal["automarkov.engineering-pilot-worker-response.v1"]
    pilot_id: Literal["cartpole_cpu_smoke_v1"]
    status: Literal["success", "terminal_failure"]
    package_versions: PilotPackageVersions
    python_version: str
    training: PilotTrainingMetrics | None
    evaluation: PilotEvaluationMetrics | None
    reason_code: str | None
    error_type: str | None
    error_message: str | None

    @model_validator(mode="after")
    def require_discriminated_payload(self) -> EngineeringPilotWorkerResponse:
        if self.status == "success":
            if (
                self.python_version != "3.11.13"
                or self.training is None
                or self.evaluation is None
                or any(
                    item is not None
                    for item in (self.reason_code, self.error_type, self.error_message)
                )
            ):
                raise ValueError("success worker response has inconsistent fields")
        elif (
            self.training is not None
            or self.evaluation is not None
            or not self.reason_code
            or not self.error_type
            or not self.error_message
        ):
            raise ValueError("terminal failure response has inconsistent fields")
        return self


class EngineeringPilotMetricsIndex(StrictFrozenModel):
    schema_version: Literal["automarkov.engineering-pilot-metrics.v1"]
    pilot_id: Literal["cartpole_cpu_smoke_v1"]
    manifest_ref: ArtifactReference
    metrics_jsonl_hash: Annotated[str, Field(strict=True, pattern=_SHA256_PATTERN)]
    record_count: SafeCanonicalInt


class EngineeringPilotResourceUsage(StrictFrozenModel):
    schema_version: Literal["automarkov.engineering-pilot-resource-usage.v1"]
    pilot_id: Literal["cartpole_cpu_smoke_v1"]
    manifest_ref: ArtifactReference
    wall_time_ms: SafeCanonicalInt
    ray_num_cpus: SafeCanonicalInt
    gpu_count: SafeCanonicalInt
    max_rss_bytes: SafeCanonicalInt | None

    @model_validator(mode="after")
    def require_cpu_only(self) -> EngineeringPilotResourceUsage:
        if self.ray_num_cpus != 2 or self.gpu_count != 0:
            raise ValueError("engineering pilot resource usage must remain CPU-only")
        return self


class SourceFileHash(StrictFrozenModel):
    path: Literal[
        "pilots/cartpole_cpu_smoke.v1.json",
        "src/automarkov/cli.py",
        "src/automarkov/pilot_worker.py",
        "src/automarkov/pilots.py",
    ]
    sha256: Annotated[str, Field(strict=True, pattern=_SHA256_PATTERN)]


class EngineeringPilotReport(StrictFrozenModel):
    schema_version: Literal["automarkov.engineering-pilot-report.v1"]
    pilot_id: Literal["cartpole_cpu_smoke_v1"]
    purpose: Literal["engineering"]
    experiment_eligibility: Literal["nonconfirmatory"]
    publication_eligible: bool
    status: Literal["passed", "waiting", "failed", "interrupted"]
    reason_code: str
    manifest_payload_hash: Annotated[str, Field(strict=True, pattern=_SHA256_PATTERN)]
    manifest_ref: ArtifactReference
    metrics_ref: ArtifactReference
    source_commit: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{40}$")]
    worktree_clean: bool
    profile_id: Literal["rllib-core"]
    profile_manifest_hash: Literal[
        "sha256:d655179755175ee164710a92dbf3b6220b80fff919a6ea67483b59b1de389428"
    ]
    lock_hash: Literal[
        "sha256:9e62ccf2a7c768b05aae4e078f97459f51aeb04c5d2bdf5ab682ca4061d34f6a"
    ]
    package_versions: PilotPackageVersions
    python_version: Literal["3.11.13"]
    worker_exit_code: SafeCanonicalInt
    started_at: str
    finished_at: str
    wall_time_ms: SafeCanonicalInt
    iterations_completed: SafeCanonicalInt
    evaluation_episodes_completed: SafeCanonicalInt
    num_env_steps_sampled_lifetime: SafeCanonicalInt
    training_episode_return_mean_decimal: DecimalString | None
    evaluation_episode_return_mean_decimal: DecimalString | None
    metrics_jsonl_hash: Annotated[str, Field(strict=True, pattern=_SHA256_PATTERN)]
    source_file_hashes: tuple[SourceFileHash, ...]
    source_tree_hash: Annotated[str, Field(strict=True, pattern=_SHA256_PATTERN)]
    disclaimer: Literal[
        "NONCONFIRMATORY engineering evidence; excluded from confirmatory statistics and publication claims."
    ]

    @model_validator(mode="after")
    def require_nonpublication_report(self) -> EngineeringPilotReport:
        if self.publication_eligible is not False:
            raise ValueError("engineering pilot report is never publication eligible")
        return self


class PilotExecutionResult(StrictFrozenModel):
    exit_code: SafeCanonicalInt
    response: EngineeringPilotWorkerResponse
    stdout: bytes
    stderr: bytes
    wall_time_ms: SafeCanonicalInt
    started_at: str
    finished_at: str

    @model_validator(mode="after")
    def require_exit_status_consistency(self) -> PilotExecutionResult:
        if (self.exit_code == 0) != (self.response.status == "success"):
            raise ValueError("worker exit code and response status are inconsistent")
        return self


class PilotOutputCollisionError(RuntimeError):
    pass


PilotExecutor: TypeAlias = Callable[
    [EngineeringPilotWorkerRequest, EngineeringPilotManifest, Path, Path],
    PilotExecutionResult,
]


def load_pilot_manifest(path: Path) -> EngineeringPilotManifest:
    return EngineeringPilotManifest.model_validate_json(path.read_bytes(), strict=True)


def _utc_now() -> str:
    rendered = datetime.now(UTC).isoformat(timespec="microseconds")
    return rendered.removesuffix("+00:00").rstrip("0").rstrip(".") + "Z"


def _hash(payload: bytes) -> str:
    return "sha256:" + sha256(payload).hexdigest()


def _source_identity(root: Path) -> tuple[tuple[SourceFileHash, ...], str]:
    files = tuple(
        SourceFileHash(path=path, sha256=_hash((root / path).read_bytes()))
        for path in _SOURCE_PATHS
    )
    tree_hash = _hash(
        canonical_json_bytes(
            {
                "domain": "AutoMarkov-EngineeringPilotSourceTree-v1",
                "files": [item.model_dump(mode="json") for item in files],
            }
        )
    )
    return files, tree_hash


@dataclass(frozen=True)
class _BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _run_bounded_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
) -> _BoundedProcessResult:
    """以独立进程组运行命令，并只从临时流读取受限前缀。"""

    with (
        tempfile.TemporaryFile() as stdout_file,
        tempfile.TemporaryFile() as stderr_file,
    ):
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
        )
        timed_out = False
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process_group(process)
        stdout_file.seek(0)
        stderr_file.seek(0)
        return _BoundedProcessResult(
            returncode=process.returncode if process.returncode is not None else 4,
            stdout=stdout_file.read(max_stdout_bytes),
            stderr=stderr_file.read(max_stderr_bytes),
            timed_out=timed_out,
        )


def build_worker_request(
    manifest: EngineeringPilotManifest,
) -> EngineeringPilotWorkerRequest:
    workload = manifest.workload
    limits = manifest.limits
    return EngineeringPilotWorkerRequest.model_validate(
        {
            "schema_version": "automarkov.engineering-pilot-worker-request.v1",
            "pilot_id": manifest.pilot_id,
            "profile_id": manifest.runtime_profile.profile_id,
            "environment_id": workload.environment_id,
            "algorithm": workload.algorithm,
            "framework": workload.framework,
            "seed": workload.seed,
            "train_iterations": workload.train_iterations,
            "train_batch_size_per_learner": workload.train_batch_size_per_learner,
            "minibatch_size": workload.minibatch_size,
            "num_epochs": workload.num_epochs,
            "num_env_runners": workload.num_env_runners,
            "num_envs_per_env_runner": workload.num_envs_per_env_runner,
            "num_learners": workload.num_learners,
            "num_gpus_per_learner": workload.num_gpus_per_learner,
            "evaluation_episodes": workload.evaluation_episodes,
            "evaluation_explore": workload.evaluation_explore,
            "checkpoint_policy": workload.checkpoint_policy,
            "wall_time_seconds": limits.wall_time_seconds,
            "ray_num_cpus": limits.ray_num_cpus,
            "max_stdout_bytes": limits.max_stdout_bytes,
            "max_stderr_bytes": limits.max_stderr_bytes,
        },
        strict=True,
    )


def _profile_preflight(manifest: EngineeringPilotManifest, root: Path) -> None:
    profiles = load_runtime_profiles(root / "profiles")
    profile = next(
        (candidate for candidate in profiles if candidate.profile_id == "rllib-core"),
        None,
    )
    if profile is None:
        raise ValueError("rllib-core profile identity is missing")
    profile_hash = _hash(
        canonical_json_bytes(profile.model_dump(mode="json", exclude={"manifest_hash"}))
    )
    lock_hash = _hash((root / "profiles/rllib-core/uv.lock").read_bytes())
    expected_versions = manifest.runtime_profile.package_versions.model_dump(
        mode="json"
    )
    if (
        profile_hash != manifest.runtime_profile.profile_manifest_hash
        or profile.lock_hash != manifest.runtime_profile.lock_hash
        or lock_hash != manifest.runtime_profile.lock_hash
        or any(
            profile.package_versions.get(key) != value
            for key, value in expected_versions.items()
        )
        or profile.python_version != manifest.runtime_profile.python_version
    ):
        raise ValueError(
            "rllib-core profile identity does not match the pilot contract"
        )
    interpreter = root / manifest.runtime_profile.worker_interpreter
    if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        raise RuntimeError("rllib-core worker runtime is unavailable")
    probe = _run_bounded_process(
        [
            str(interpreter),
            "-I",
            "-c",
            "import platform;print(platform.python_version())",
        ],
        cwd=root,
        env={"PATH": os.environ.get("PATH", "")},
        timeout_seconds=10,
        max_stdout_bytes=64,
        max_stderr_bytes=1024,
    )
    if (
        probe.timed_out
        or probe.returncode != 0
        or probe.stdout != b"3.11.13\n"
        or probe.stderr
    ):
        raise ValueError(
            "rllib-core interpreter identity does not match Python 3.11.13"
        )


def execute_profile_worker(
    request: EngineeringPilotWorkerRequest,
    manifest: EngineeringPilotManifest,
    workspace: Path,
    repository_root: Path,
) -> PilotExecutionResult:
    request_path = workspace / ".worker-request.json"
    response_path = workspace / ".worker-response.json"
    request_path.write_bytes(
        canonical_json_bytes(request.model_dump(mode="json")) + b"\n"
    )
    started_at = _utc_now()
    started_ns = monotonic_ns()
    environment = {
        key: value
        for key in ("HOME", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "PATH", "TMPDIR")
        if (value := os.environ.get(key)) is not None
    }
    environment["PYTHONPATH"] = str(repository_root / "src")
    environment["CUDA_VISIBLE_DEVICES"] = ""
    command = [
        str(repository_root / manifest.runtime_profile.worker_interpreter),
        "-m",
        manifest.runtime_profile.worker_module,
        "--request",
        str(request_path),
        "--result",
        str(response_path),
    ]
    completed = _run_bounded_process(
        command,
        cwd=repository_root,
        env=environment,
        timeout_seconds=manifest.limits.wall_time_seconds,
        max_stdout_bytes=manifest.limits.max_stdout_bytes,
        max_stderr_bytes=manifest.limits.max_stderr_bytes,
    )
    if completed.timed_out:
        finished_at = _utc_now()
        request_path.unlink(missing_ok=True)
        response_path.unlink(missing_ok=True)
        return PilotExecutionResult(
            exit_code=4,
            response=EngineeringPilotWorkerResponse(
                schema_version="automarkov.engineering-pilot-worker-response.v1",
                pilot_id=manifest.pilot_id,
                status="terminal_failure",
                package_versions=manifest.runtime_profile.package_versions,
                python_version=manifest.runtime_profile.python_version,
                training=None,
                evaluation=None,
                reason_code="worker_timeout",
                error_type="TimeoutExpired",
                error_message="worker exceeded the closed 300-second wall-time limit",
            ),
            stdout=completed.stdout,
            stderr=completed.stderr,
            wall_time_ms=(monotonic_ns() - started_ns) // 1_000_000,
            started_at=started_at,
            finished_at=finished_at,
        )
    finished_at = _utc_now()
    wall_time_ms = (monotonic_ns() - started_ns) // 1_000_000
    stdout = completed.stdout
    stderr = completed.stderr
    response_bytes = response_path.read_bytes()
    request_path.unlink()
    response_path.unlink()
    response = EngineeringPilotWorkerResponse.model_validate_json(
        response_bytes, strict=True
    )
    return PilotExecutionResult(
        exit_code=completed.returncode,
        response=response,
        stdout=stdout,
        stderr=stderr,
        wall_time_ms=wall_time_ms,
        started_at=started_at,
        finished_at=finished_at,
    )


_PILOT_STORE_SCHEMA = (
    """CREATE TABLE pilot_store_meta (
        schema_version TEXT PRIMARY KEY
    ) STRICT""",
    """CREATE TABLE pilot_artifacts (
        artifact_id TEXT PRIMARY KEY,
        artifact_type TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        payload_bytes BLOB NOT NULL,
        created_at TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE pilot_artifact_parents (
        artifact_id TEXT NOT NULL REFERENCES pilot_artifacts(artifact_id),
        position INTEGER NOT NULL CHECK(position >= 0),
        parent_id TEXT NOT NULL REFERENCES pilot_artifacts(artifact_id),
        PRIMARY KEY (artifact_id, position),
        UNIQUE (artifact_id, parent_id)
    ) STRICT""",
)
_PILOT_STORE_TABLES = frozenset(
    {"pilot_store_meta", "pilot_artifacts", "pilot_artifact_parents"}
)


class _PilotArtifactStore:
    """P0 专用不可变内容寻址 store；不包含正式 Run/Event schema。"""

    def __init__(self, path: Path) -> None:
        existed = path.exists()
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA foreign_keys = ON")
        if not existed:
            for statement in _PILOT_STORE_SCHEMA:
                self._connection.execute(statement)
            self._connection.execute(
                "INSERT INTO pilot_store_meta VALUES (?)",
                ("automarkov.engineering-pilot-store.v1",),
            )
            self._connection.commit()
        self._verify_schema()

    def _verify_schema(self) -> None:
        tables = {
            row[0]
            for row in self._connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        }
        meta = (
            self._connection.execute(
                "SELECT schema_version FROM pilot_store_meta"
            ).fetchall()
            if tables == _PILOT_STORE_TABLES
            else []
        )
        if tables != _PILOT_STORE_TABLES or meta != [
            ("automarkov.engineering-pilot-store.v1",)
        ]:
            raise PilotOutputCollisionError(
                "pilot-local artifact store schema mismatch"
            )

    def put(
        self,
        artifact_type: str,
        payload: StrictFrozenModel,
        parents: tuple[ArtifactReference, ...],
        created_at: str,
    ) -> ArtifactReference:
        payload_bytes = canonical_json_bytes(payload.model_dump(mode="json"))
        payload_hash = _hash(payload_bytes)
        for parent in parents:
            self.get(parent)
        envelope = canonical_json_bytes(
            {
                "domain": "AutoMarkov-EngineeringPilotArtifact-v1",
                "artifact_type": artifact_type,
                "payload_hash": payload_hash,
                "parent_artifact_ids": [parent.artifact_id for parent in parents],
                "created_at": created_at,
            }
        )
        artifact_id = "artifact_" + sha256(envelope).hexdigest()
        self._connection.execute(
            "INSERT INTO pilot_artifacts VALUES (?, ?, ?, ?, ?)",
            (artifact_id, artifact_type, payload_hash, payload_bytes, created_at),
        )
        self._connection.executemany(
            "INSERT INTO pilot_artifact_parents VALUES (?, ?, ?)",
            [
                (artifact_id, position, parent.artifact_id)
                for position, parent in enumerate(parents)
            ],
        )
        self._connection.commit()
        return ArtifactReference(artifact_id=artifact_id, payload_hash=payload_hash)

    def get(self, reference: ArtifactReference) -> bytes:
        row = self._connection.execute(
            "SELECT payload_hash, payload_bytes FROM pilot_artifacts WHERE artifact_id=?",
            (reference.artifact_id,),
        ).fetchone()
        if (
            row is None
            or row[0] != reference.payload_hash
            or _hash(row[1]) != reference.payload_hash
        ):
            raise PilotOutputCollisionError("pilot-local artifact integrity mismatch")
        return bytes(row[1])

    def close(self) -> None:
        self._connection.close()


PilotReportStatus: TypeAlias = Literal["passed", "waiting", "failed", "interrupted"]


def _status(exit_code: int) -> tuple[PilotReportStatus, str]:
    if exit_code == 0:
        return "passed", "completed"
    if exit_code == 3:
        return "waiting", "runtime_unavailable"
    if exit_code == 130:
        return "interrupted", "interrupted"
    return "failed", "worker_failure"


def _git_identity(root: Path) -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    clean = not subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return commit, clean


def _existing(
    target: Path,
    manifest_hash: str,
    repository_root: Path,
) -> EngineeringPilotReport | None:
    if not target.exists():
        return None
    if not target.is_dir() or {item.name for item in target.iterdir()} != _OUTPUT_NAMES:
        raise PilotOutputCollisionError("existing pilot output is incomplete")
    manifest_bytes = (target / "pilot_manifest.json").read_bytes()
    report_bytes = (target / "compact_report.json").read_bytes()
    terminal_bytes = (target / "terminal_record.json").read_bytes()
    try:
        report = EngineeringPilotReport.model_validate_json(report_bytes, strict=True)
    except ValueError as error:
        raise PilotOutputCollisionError("existing pilot report is invalid") from error
    source_files, source_tree_hash = _source_identity(repository_root)
    if (
        report.manifest_payload_hash != manifest_hash
        or _hash(manifest_bytes.rstrip(b"\n")) != manifest_hash
        or report.status != "passed"
        or report.worker_exit_code != 0
        or report_bytes != canonical_json_bytes(report.model_dump(mode="json")) + b"\n"
        or (target / "compact_report.md").read_text(encoding="utf-8")
        != _markdown(report)
        or report.source_file_hashes != source_files
        or report.source_tree_hash != source_tree_hash
    ):
        raise PilotOutputCollisionError("existing pilot uses a different manifest")
    metrics = (target / "metrics.jsonl").read_bytes()
    if _hash(metrics) != report.metrics_jsonl_hash:
        raise PilotOutputCollisionError("existing pilot metrics are corrupted")
    try:
        terminal = ProcessExecutionTerminalRecord.model_validate_json(
            terminal_bytes, strict=True
        )
    except ValueError as error:
        raise PilotOutputCollisionError(
            "existing pilot terminal record is invalid"
        ) from error
    if (
        terminal_bytes != canonical_json_bytes(terminal.model_dump(mode="json")) + b"\n"
        or terminal.status != "success"
        or terminal.exit_code != 0
        or terminal.job_manifest != report.manifest_ref
        or report.metrics_ref not in terminal.payload_outputs
    ):
        raise PilotOutputCollisionError("existing pilot terminal record is corrupted")
    repository = _PilotArtifactStore(target / "artifacts.sqlite3")
    try:
        for reference in (
            report.manifest_ref,
            report.metrics_ref,
            terminal.resource_usage,
            *terminal.payload_outputs,
        ):
            repository.get(reference)
    finally:
        repository.close()
    return report


def _markdown(report: EngineeringPilotReport) -> str:
    return "\n".join(
        (
            "# CartPole CPU engineering pilot",
            "",
            f"- 状态：`{report.status}`",
            f"- 训练迭代：`{report.iterations_completed}`",
            f"- 评价 episode：`{report.evaluation_episodes_completed}`",
            f"- 训练平均回报：`{report.training_episode_return_mean_decimal}`",
            f"- 评价平均回报：`{report.evaluation_episode_return_mean_decimal}`",
            f"- 源码树：`{report.source_tree_hash}`",
            "",
            f"> {DISCLAIMER}",
            "",
        )
    )


def run_engineering_pilot(
    manifest_path: Path,
    *,
    repository_root: Path,
    output_root: Path | None = None,
    executor: PilotExecutor = execute_profile_worker,
) -> EngineeringPilotReport:
    manifest = load_pilot_manifest(manifest_path)
    _profile_preflight(manifest, repository_root)
    manifest_bytes = canonical_json_bytes(manifest.model_dump(mode="json"))
    manifest_hash = _hash(manifest_bytes)
    root = output_root or repository_root / manifest.output_root
    target = root / manifest.pilot_id
    existing = _existing(target, manifest_hash, repository_root)
    if existing is not None:
        return existing
    root.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix=f".{manifest.pilot_id}.", dir=root))
    try:
        execution = executor(
            build_worker_request(manifest), manifest, workspace, repository_root
        )
        response = execution.response
        metrics_records: list[dict[str, object]] = []
        if response.training is not None:
            metrics_records.append(
                {"metric_kind": "training", **response.training.model_dump(mode="json")}
            )
        if response.evaluation is not None:
            metrics_records.append(
                {
                    "metric_kind": "evaluation",
                    **response.evaluation.model_dump(mode="json"),
                }
            )
        metrics_bytes = b"".join(
            canonical_json_bytes(record) + b"\n" for record in metrics_records
        )
        metrics_hash = _hash(metrics_bytes)
        source_commit, clean = _git_identity(repository_root)
        source_files, source_tree_hash = _source_identity(repository_root)
        repository = _PilotArtifactStore(workspace / "artifacts.sqlite3")
        try:
            manifest_ref = repository.put(
                "engineering_pilot_manifest",
                manifest,
                (),
                execution.finished_at,
            )
            metrics_index = EngineeringPilotMetricsIndex(
                schema_version="automarkov.engineering-pilot-metrics.v1",
                pilot_id=manifest.pilot_id,
                manifest_ref=manifest_ref,
                metrics_jsonl_hash=metrics_hash,
                record_count=len(metrics_records),
            )
            metrics_ref = repository.put(
                "engineering_pilot_metrics",
                metrics_index,
                (manifest_ref,),
                execution.finished_at,
            )
            resource = EngineeringPilotResourceUsage(
                schema_version="automarkov.engineering-pilot-resource-usage.v1",
                pilot_id=manifest.pilot_id,
                manifest_ref=manifest_ref,
                wall_time_ms=execution.wall_time_ms,
                ray_num_cpus=manifest.limits.ray_num_cpus,
                gpu_count=0,
                max_rss_bytes=None,
            )
            resource_ref = repository.put(
                "engineering_pilot_resource_usage",
                resource,
                (manifest_ref,),
                execution.finished_at,
            )
            status, reason = _status(execution.exit_code)
            training = response.training
            evaluation = response.evaluation
            report = EngineeringPilotReport(
                schema_version="automarkov.engineering-pilot-report.v1",
                pilot_id=manifest.pilot_id,
                purpose=manifest.purpose,
                experiment_eligibility=manifest.experiment_eligibility,
                publication_eligible=manifest.publication_eligible,
                status=status,
                reason_code=reason,
                manifest_payload_hash=manifest_hash,
                manifest_ref=manifest_ref,
                metrics_ref=metrics_ref,
                source_commit=source_commit,
                worktree_clean=clean,
                profile_id=manifest.runtime_profile.profile_id,
                profile_manifest_hash=manifest.runtime_profile.profile_manifest_hash,
                lock_hash=manifest.runtime_profile.lock_hash,
                package_versions=response.package_versions,
                python_version="3.11.13",
                worker_exit_code=execution.exit_code,
                started_at=execution.started_at,
                finished_at=execution.finished_at,
                wall_time_ms=execution.wall_time_ms,
                iterations_completed=training.iterations_completed if training else 0,
                evaluation_episodes_completed=evaluation.episodes_completed
                if evaluation
                else 0,
                num_env_steps_sampled_lifetime=training.num_env_steps_sampled_lifetime
                if training
                else 0,
                training_episode_return_mean_decimal=training.episode_return_mean_decimal
                if training
                else None,
                evaluation_episode_return_mean_decimal=evaluation.episode_return_mean_decimal
                if evaluation
                else None,
                metrics_jsonl_hash=metrics_hash,
                source_file_hashes=source_files,
                source_tree_hash=source_tree_hash,
                disclaimer=DISCLAIMER,
            )
            report_ref = repository.put(
                "engineering_pilot_report",
                report,
                (manifest_ref, metrics_ref),
                execution.finished_at,
            )
        finally:
            repository.close()
        terminal = ProcessExecutionTerminalRecord(
            schema_version="automarkov.process-execution-terminal-record.v1",
            signing_domain="AutoMarkov-ProcessExecutionTerminalRecord-v1",
            experiment_id=None,
            run_id="run_pilot_cartpole_cpu_smoke_v1",
            job_id="cartpole_cpu_smoke_v1",
            process_execution_id="cartpole_cpu_smoke_v1",
            profile_id="rllib-core",
            principal_id="principal_engineering_pilot_worker",
            job_manifest=manifest_ref,
            status="success" if execution.exit_code == 0 else "terminal_failure",
            exit_code=execution.exit_code,
            reason_code=reason,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            stdout_hash=_hash(execution.stdout),
            stderr_hash=_hash(execution.stderr),
            payload_outputs=tuple(
                sorted((metrics_ref, report_ref), key=lambda item: item.artifact_id)
            ),
            resource_usage=resource_ref,
            network_log_hash=_hash(b""),
            mount_attestation_hash=_hash(b""),
            capability_decision_hash=_hash(b""),
            egress_log_hash=_hash(b""),
            created_at=execution.finished_at,
        )
        (workspace / "pilot_manifest.json").write_bytes(manifest_bytes + b"\n")
        (workspace / "metrics.jsonl").write_bytes(metrics_bytes)
        (workspace / "terminal_record.json").write_bytes(
            canonical_json_bytes(terminal.model_dump(mode="json")) + b"\n"
        )
        (workspace / "compact_report.json").write_bytes(
            canonical_json_bytes(report.model_dump(mode="json")) + b"\n"
        )
        (workspace / "compact_report.md").write_text(
            _markdown(report), encoding="utf-8"
        )
        os.replace(workspace, target)
        return report
    except BaseException:
        shutil.rmtree(workspace, ignore_errors=True)
        raise
