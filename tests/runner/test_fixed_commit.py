from __future__ import annotations

import base64
from collections.abc import Callable
from copy import deepcopy
from hashlib import sha1, sha256
from pathlib import Path
from threading import Event, Thread

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from automarkov.canonical import canonical_json_bytes
from automarkov.domain import RunId, Sha256Digest, VerifiedEventHead
from automarkov.fixed_commit_runner import (
    RUNNER_RESULT_PAYLOAD_SCHEMA_HASH,
    SEALED_SUBJECT_ARTIFACT_CONTRACTS,
    CandidateApiOutput,
    ExecutionMount,
    ExecutionMountPolicy,
    ExecutionOutputContract,
    FixedCommitExecutionRequest,
    FixedCommitJobManifest,
    FixedCommitResourceLimits,
    FixedCommitRunner,
    LinuxCgroupV2ResourceCollector,
    MemoryFixedCommitExecutor,
    MemoryRunnerArtifactStore,
    MemoryRunnerArtifactWriter,
    MemoryRunnerTerminalCommitter,
    MemoryTrustedRunnerArtifactResolver,
    OciCommandResult,
    OciFixedCommitExecutor,
    OciResourceObservation,
    OutputScanReport,
    OutputSchemaBinding,
    RawExecutionEvidence,
    RunnerArtifactReferencePayload,
    RunnerExecutionFailed,
    RunnerInput,
    RunnerOutputBinding,
    RunnerPreflightError,
    RunnerTerminalCommitReceipt,
    RunnerWaitingRuntimeError,
    RuntimeAttestationKeyPolicy,
    SealedSubjectRecord,
    execution_attestation_signature_bytes,
    execution_attestation_signing_bytes,
    runner_payload_reference,
)
from automarkov.lifecycle import (
    ArtifactReference,
    EventReference,
    ProcessExecutionTerminalRecord,
    TerminalResult,
)
from automarkov.provenance import RuntimeProfileManifest
from automarkov.repository import _default_schema_registry

_REF = {
    "artifact_id": "artifact_" + "1" * 64,
    "payload_hash": "sha256:" + "2" * 64,
}


def _job_payload() -> dict[str, object]:
    return {
        "schema_version": "automarkov.fixed-commit-job-manifest.v1",
        "job_id": "job_17",
        "process_execution_id": "process_17",
        "experiment_id": "experiment_17",
        "run_id": "run_17",
        "principal_id": "principal_runner",
        "repository_url": "https://github.com/example/benchmark.git",
        "source_commit": "a" * 40,
        "profile_manifest": {
            "artifact_id": "artifact_" + "3" * 64,
            "payload_hash": "sha256:" + "4" * 64,
        },
        "profile_id": "runner-control",
        "profile_lock_hash": "sha256:" + "5" * 64,
        "target_platform": "linux/amd64",
        "image_digest": "sha256:" + "6" * 64,
        "input_artifacts": (
            {
                "artifact_id": "artifact_" + "7" * 64,
                "payload_hash": "sha256:" + "8" * 64,
            },
        ),
        "suite_id": "suite_taxi",
        "variant_id": "variant_0",
        "track_id": "track_public",
        "method_id": "method_automarkov",
        "pair_id": "pair_0",
        "generation_seed": 17,
        "rl_seed": 23,
        "phase": "training",
        "argv": ("/opt/venv/bin/python", "-m", "automarkov.worker"),
        "working_directory": "checkout",
        "resource_limits": {
            "artifact_id": "artifact_" + "9" * 64,
            "payload_hash": "sha256:" + "a" * 64,
        },
        "network_policy": {
            "artifact_id": "artifact_" + "b" * 64,
            "payload_hash": "sha256:" + "c" * 64,
        },
        "mount_policy": {
            "artifact_id": "artifact_" + "d" * 64,
            "payload_hash": "sha256:" + "e" * 64,
        },
        "capability_policy": {
            "artifact_id": "artifact_" + "f" * 64,
            "payload_hash": "sha256:" + "0" * 64,
        },
        "output_contract": {
            "artifact_id": "artifact_" + "1" * 64,
            "payload_hash": "sha256:" + "3" * 64,
        },
        "scanner_policy": {
            "artifact_id": "artifact_" + "2" * 64,
            "payload_hash": "sha256:" + "4" * 64,
        },
        "from_phase": "TRAINING_SMOKE_TESTING",
        "to_phase": "POLICY_TRAINING",
        "launch_deadline": "2026-08-12T12:00:00Z",
    }


def test_job_manifest_accepts_only_exact_commit_and_non_shell_launch() -> None:
    manifest = FixedCommitJobManifest.model_validate(_job_payload(), strict=True)

    assert manifest.source_commit == "a" * 40
    assert manifest.argv[0] == "/opt/venv/bin/python"

    invalid_payloads: list[dict[str, object]] = []
    for field_name, invalid_value in (
        ("repository_url", "https://user@example.com/repo.git"),
        ("repository_url", "ssh://git@example.com/repo.git"),
        ("repository_url", "https://127.0.0.1/repo.git"),
        ("repository_url", "https://localhost./repo.git"),
        ("source_commit", "main"),
        ("argv", ("python -m automarkov.worker",)),
        ("argv", ("/bin/sh", "-c", "python -m automarkov.worker")),
        ("argv", ("/opt/worker", "--api-key", "[REDACTED_SECRET]")),
        ("argv", ("/opt/worker", "--token", "[REDACTED_SECRET]")),
        ("argv", ("/opt/worker", "--client-secret=value")),
        ("argv", ("/opt/worker", "--refresh_token", "value")),
        ("argv", ("/opt/worker", "--private-key=value")),
        ("argv", ("/opt/worker", "--authorization-header=value")),
        ("argv", ("/opt/worker", "--authorization=Bearer [REDACTED_SECRET]")),
        ("working_directory", "../checkout"),
    ):
        payload = deepcopy(_job_payload())
        payload[field_name] = invalid_value
        invalid_payloads.append(payload)

    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            FixedCommitJobManifest.model_validate(payload, strict=True)


def test_oci_checkout_rejects_repository_dns_that_resolves_to_loopback(
    tmp_path: Path,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(
            _job_payload()
            | {"repository_url": "https://127.0.0.1.nip.io/benchmark.git"},
            strict=True,
        )
    )
    executor = OciFixedCommitExecutor(
        resolver=resolver,
        specified_event_head=_head(),
        job_manifest=manifest_reference,
        seccomp_profile_path=tmp_path / "unused-seccomp",
        apparmor_profile_path=tmp_path / "unused-apparmor",
        artifact_writer=MemoryRunnerArtifactWriter(resolver),
        command_runner=lambda _command, _timeout: pytest.fail(
            "private repository resolution must fail before Git"
        ),
        repository_host_resolver=lambda _hostname, _port: ("127.0.0.1",),
    )

    with pytest.raises(RunnerPreflightError, match="outside the public network"):
        executor._materialize_checkout(manifest, tmp_path / "scratch")


def test_oci_checkout_rejects_git_without_dns_pin_support(tmp_path: Path) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    commands: list[tuple[str, ...]] = []

    def command_runner(
        command: tuple[str, ...], timeout_seconds: float
    ) -> OciCommandResult:
        del timeout_seconds
        commands.append(command)
        return OciCommandResult(0, b"git version 2.36.6\n", b"")

    executor = OciFixedCommitExecutor(
        resolver=resolver,
        specified_event_head=_head(),
        job_manifest=manifest_reference,
        seccomp_profile_path=tmp_path / "unused-seccomp",
        apparmor_profile_path=tmp_path / "unused-apparmor",
        artifact_writer=MemoryRunnerArtifactWriter(resolver),
        command_runner=command_runner,
        repository_host_resolver=lambda _hostname, _port: ("93.184.216.34",),
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with pytest.raises(RunnerWaitingRuntimeError, match="Git with DNS pin support"):
        executor._materialize_checkout(manifest, scratch)

    assert len(commands) == 1
    assert commands[0][-2:] == ("git", "--version")


@pytest.mark.parametrize("attack", ("cap_add", "extra_security_option"))
def test_oci_inspect_rejects_capability_or_security_option_escalation(
    tmp_path: Path,
    attack: str,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    executor = OciFixedCommitExecutor(
        resolver=resolver,
        specified_event_head=_head(),
        job_manifest=manifest_reference,
        seccomp_profile_path=tmp_path / "unused-seccomp",
        apparmor_profile_path=tmp_path / "unused-apparmor",
        artifact_writer=MemoryRunnerArtifactWriter(resolver),
    )
    resources = FixedCommitResourceLimits(
        schema_version="automarkov.fixed-commit-resource-limits.v1",
        phase=manifest.phase,
        cpu_millis=1_000,
        memory_bytes=1024 * 1024,
        pids=16,
        io_bytes=1024 * 1024,
        disk_bytes=1024 * 1024,
        wall_time_ms=60_000,
        gpu_devices=("cuda:0",),
    )
    mounts = ExecutionMountPolicy(
        schema_version="automarkov.execution-mount-policy.v1",
        candidate_worker=False,
        mounts=(),
    )
    seccomp_path = tmp_path / "seccomp.json"
    apparmor_name = "automarkov-" + "a" * 32
    security_options = [
        "no-new-privileges=true",
        f"seccomp={seccomp_path}",
        f"apparmor={apparmor_name}",
    ]
    document: dict[str, object] = {
        "Config": {
            "Cmd": list(manifest.argv[1:]),
            "Entrypoint": [manifest.argv[0]],
            "Labels": {
                "automarkov.job_id": manifest.job_id,
                "automarkov.process_execution_id": manifest.process_execution_id,
            },
            "User": "65532:65532",
            "WorkingDir": "/mnt/automarkov/" + manifest.working_directory,
        },
        "HostConfig": {
            "CapAdd": ["SYS_ADMIN"] if attack == "cap_add" else [],
            "CapDrop": ["ALL"],
            "CpuPeriod": 100_000,
            "CpuQuota": resources.cpu_millis * 100,
            "Memory": resources.memory_bytes,
            "MemorySwap": resources.memory_bytes,
            "NetworkMode": "none",
            "PidsLimit": resources.pids,
            "Privileged": False,
            "ReadonlyRootfs": True,
            "RestartPolicy": {"Name": "no"},
            "LogConfig": {
                "Type": "json-file",
                "Config": {"max-file": "1", "max-size": "16m"},
            },
            "SecurityOpt": security_options
            + (["seccomp=unconfined"] if attack == "extra_security_option" else []),
            "Tmpfs": None,
        },
        "Image": manifest.image_digest,
        "Mounts": [],
        "State": {"Pid": 4242, "Running": True},
    }

    with pytest.raises(RunnerPreflightError, match="frozen policy"):
        executor._verify_running_container(
            document,
            manifest,
            resources,
            mounts,
            {},
            seccomp_path,
            apparmor_name,
            manifest.image_digest,
        )


def _artifact_reference(character: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_id="artifact_" + character * 64,
        payload_hash="sha256:" + character * 64,
    )


def _head() -> VerifiedEventHead:
    return VerifiedEventHead(
        run_id=RunId(root="run_17"),
        sequence_no=0,
        event_hash=Sha256Digest(root="sha256:" + "f" * 64),
    )


def test_runner_rejects_tampered_payload_behind_same_reference_before_execution() -> (
    None
):
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    evidence = resolver.freeze_execution_evidence(manifest, manifest_reference)
    executor = MemoryFixedCommitExecutor(evidence)
    resolver.inject_corrupt_payload(
        manifest_reference,
        "fixed_commit_job_manifest",
        manifest.model_copy(update={"rl_seed": 99}),
    )
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )
    request = FixedCommitExecutionRequest(
        schema_version="automarkov.fixed-commit-execution-request.v1",
        specified_event_head=_head(),
        job_manifest=manifest_reference,
    )

    with pytest.raises(RunnerPreflightError, match="content identity"):
        runner.run_at_commit(request)

    assert executor.call_count == 0


def test_runner_rejects_private_key_not_granted_by_frozen_authorization() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )

    executor = MemoryFixedCommitExecutor(
        resolver.freeze_execution_evidence(manifest, reference)
    )
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=Ed25519PrivateKey.generate(),
        clock=lambda: "2026-08-12T11:00:00Z",
    )

    with pytest.raises(RunnerPreflightError, match="frozen job signing grant"):
        runner.run_at_commit(
            FixedCommitExecutionRequest(
                schema_version="automarkov.fixed-commit-execution-request.v1",
                specified_event_head=_head(),
                job_manifest=reference,
            )
        )
    assert executor.call_count == 0


def test_memory_runner_emits_signed_attestation_once() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    executor = MemoryFixedCommitExecutor(
        resolver.freeze_execution_evidence(manifest, manifest_reference)
    )
    store = MemoryRunnerArtifactStore()
    signing_key = resolver.runner_signing_key
    clock = {"now": "2026-08-12T11:00:00Z"}
    runner = FixedCommitRunner(
        artifact_store=store,
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=signing_key,
        clock=lambda: clock["now"],
    )
    request = FixedCommitExecutionRequest(
        schema_version="automarkov.fixed-commit-execution-request.v1",
        specified_event_head=_head(),
        job_manifest=manifest_reference,
    )

    result = runner.run_at_commit(request)
    resolver.inject_corrupt_payload(
        manifest_reference,
        "fixed_commit_job_manifest",
        manifest.model_copy(update={"rl_seed": 99}),
    )
    clock["now"] = "2026-08-12T12:00:01Z"
    replay = runner.run_at_commit(request)

    assert result == replay
    assert executor.call_count == 1
    assert result.terminal_result is None
    attestation = store.execution_attestation(result.execution_attestation)
    signing_key.public_key().verify(
        execution_attestation_signature_bytes(attestation),
        execution_attestation_signing_bytes(attestation),
    )
    assert attestation.process_terminal_record == result.process_terminal_record


def test_concurrent_identical_request_executes_only_once() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    evidence = resolver.freeze_execution_evidence(manifest, manifest_reference)
    entered = Event()
    release = Event()

    class BlockingExecutor:
        def __init__(self) -> None:
            self.call_count = 0

        def execute(self, manifest: FixedCommitJobManifest) -> RawExecutionEvidence:
            del manifest
            self.call_count += 1
            entered.set()
            assert release.wait(timeout=5)
            return evidence

    executor = BlockingExecutor()
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )
    request = FixedCommitExecutionRequest(
        schema_version="automarkov.fixed-commit-execution-request.v1",
        specified_event_head=_head(),
        job_manifest=manifest_reference,
    )
    results: list[object] = []

    first = Thread(target=lambda: results.append(runner.run_at_commit(request)))
    second = Thread(target=lambda: results.append(runner.run_at_commit(request)))
    first.start()
    assert entered.wait(timeout=5)
    second.start()
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert len(results) == 2
    assert results[0] == results[1]
    assert executor.call_count == 1


def test_waiting_runtime_releases_the_execution_reservation_for_retry() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    evidence = resolver.freeze_execution_evidence(manifest, manifest_reference)

    class WaitingOnceExecutor:
        def __init__(self) -> None:
            self.call_count = 0

        def execute(self, manifest: FixedCommitJobManifest) -> RawExecutionEvidence:
            del manifest
            self.call_count += 1
            if self.call_count == 1:
                raise RunnerWaitingRuntimeError("transient verified OCI runtime")
            return evidence

    executor = WaitingOnceExecutor()
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )
    request = FixedCommitExecutionRequest(
        schema_version="automarkov.fixed-commit-execution-request.v1",
        specified_event_head=_head(),
        job_manifest=manifest_reference,
    )

    with pytest.raises(RunnerWaitingRuntimeError):
        runner.run_at_commit(request)
    assert runner.run_at_commit(request).terminal_result is None
    assert executor.call_count == 2


@pytest.mark.parametrize(
    "post_start_failure",
    ("cleanup", "logs_over_16_mib", "evidence_collection"),
)
def test_started_execution_failure_is_never_released_for_retry(
    post_start_failure: str,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    baseline = resolver.freeze_execution_evidence(manifest, manifest_reference)
    empty_scan = OutputScanReport.model_validate_json(
        resolver.resolve(_head(), baseline.output_scan_report).payload_bytes,
        strict=True,
    ).model_copy(
        update={
            "scanned_outputs": (),
            "scanned_paths": (),
            "total_bytes": 0,
        }
    )
    empty_scan_ref = resolver.register(
        "output_scan_report",
        empty_scan,
        parent_artifact_ids=tuple(
            sorted(
                {
                    manifest_reference.artifact_id,
                    manifest.scanner_policy.artifact_id,
                    manifest.output_contract.artifact_id,
                }
            )
        ),
    )
    fallback = baseline.model_copy(
        update={
            "status": "terminal_failure",
            "exit_code": 126,
            "reason_code": "fixed_commit_post_start_failure",
            "payload_outputs": (),
            "output_scan_report": empty_scan_ref,
        }
    )

    class StartedFailingExecutor:
        execution_started = False

        def __init__(self) -> None:
            self.call_count = 0

        def execute(self, manifest: FixedCommitJobManifest) -> RawExecutionEvidence:
            del manifest
            self.call_count += 1
            self.execution_started = True
            raise RunnerWaitingRuntimeError(post_start_failure)

        def terminal_failure_evidence(
            self, error: BaseException
        ) -> RawExecutionEvidence:
            assert post_start_failure in str(error)
            return fallback

    executor = StartedFailingExecutor()
    store = MemoryRunnerArtifactStore()
    runner = FixedCommitRunner(
        artifact_store=store,
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )
    request = FixedCommitExecutionRequest(
        schema_version="automarkov.fixed-commit-execution-request.v1",
        specified_event_head=_head(),
        job_manifest=manifest_reference,
    )

    first = runner.run_at_commit(request)
    retried = runner.run_at_commit(request)
    assert first == retried
    process = store.process_terminal_record(first.process_terminal_record)
    attestation = store.execution_attestation(first.execution_attestation)
    assert process.status == "terminal_failure"
    assert process.reason_code == "fixed_commit_post_start_failure"
    assert attestation.process_terminal_record == first.process_terminal_record
    assert executor.call_count == 1


def test_oversized_complete_log_stream_becomes_persistent_execution_failure(
    tmp_path: Path,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    _manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    oversized = b"x" * (16 * 1024 * 1024 + 1)
    oci = OciFixedCommitExecutor(
        resolver=resolver,
        specified_event_head=_head(),
        job_manifest=manifest_reference,
        seccomp_profile_path=tmp_path / "unused-seccomp",
        apparmor_profile_path=tmp_path / "unused-apparmor",
        artifact_writer=MemoryRunnerArtifactWriter(resolver),
        command_runner=lambda command, timeout: OciCommandResult(0, oversized, b""),
    )

    class OversizedLogExecutor:
        execution_started = True
        call_count = 0

        def execute(self, manifest: FixedCommitJobManifest) -> RawExecutionEvidence:
            del manifest
            self.call_count += 1
            oci._run(("docker", "container", "logs", "c" * 64), 30.0)
            raise AssertionError("oversized log collection must fail closed")

    executor = OversizedLogExecutor()
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )
    request = FixedCommitExecutionRequest(
        schema_version="automarkov.fixed-commit-execution-request.v1",
        specified_event_head=_head(),
        job_manifest=manifest_reference,
    )

    with pytest.raises(RunnerExecutionFailed, match="bounded Docker CLI"):
        runner.run_at_commit(request)
    with pytest.raises(RunnerExecutionFailed, match="bounded Docker CLI"):
        runner.run_at_commit(request)
    assert executor.call_count == 1


def test_oci_terminal_failure_evidence_synthesizes_fallback_when_no_evidence_exists(
    tmp_path: Path,
) -> None:
    """F1 RED: terminal_failure_evidence MUST synthesize fallback evidence from
    _last_manifest when execution_started=True but _last_terminal_evidence=None.

    Before fix: raises RunnerExecutionFailed("started OCI execution has no
    bounded terminal evidence"), losing the execution slot irrevocably.
    After fix: constructs a valid terminal_failure RawExecutionEvidence from
    the still-available _last_manifest, so _record_started_failure can persist
    the failure atomically.
    """
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    oci = OciFixedCommitExecutor(
        resolver=resolver,
        specified_event_head=_head(),
        job_manifest=manifest_reference,
        seccomp_profile_path=tmp_path / "unused-seccomp",
        apparmor_profile_path=tmp_path / "unused-apparmor",
        artifact_writer=MemoryRunnerArtifactWriter(resolver),
        command_runner=lambda _cmd, _timeout: OciCommandResult(0, b"", b""),
        clock=lambda: "2026-08-12T11:00:00Z",
    )
    # Simulate: execute() set _execution_started=True and _last_manifest,
    # but an exception occurred before _last_terminal_evidence was assigned.
    oci._execution_started = True
    oci._last_manifest = manifest
    oci._last_terminal_evidence = None

    evidence = oci.terminal_failure_evidence(ValueError("post-start failure"))

    assert evidence.status == "terminal_failure"
    assert evidence.exit_code == 126
    assert evidence.reason_code == "fixed_commit_post_start_failure"
    assert evidence.payload_outputs == ()


@pytest.mark.parametrize("limit_exceeded", (False, True))
@pytest.mark.parametrize("fast_exit", (False, True))
def test_oci_executor_runs_the_closed_local_docker_subset(
    tmp_path: Path,
    limit_exceeded: bool,
    fast_exit: bool,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    seccomp_profile = tmp_path / "seccomp.json"
    apparmor_profile = tmp_path / "apparmor.profile"
    seccomp_bytes = b'{"defaultAction":"SCMP_ACT_ERRNO"}'
    apparmor_profile_name = "automarkov-" + sha256(b"process_17").hexdigest()[:32]
    apparmor_bytes = (
        f"profile {apparmor_profile_name} flags=(attach_disconnected) {{}}\n".encode()
    )
    seccomp_profile.write_bytes(seccomp_bytes)
    apparmor_profile.write_bytes(apparmor_bytes)
    draft = _job_payload()
    draft.update(
        {
            "phase": "analysis",
            "working_directory": "artifacts/control",
            "from_phase": "ENVIRONMENT_IMPLEMENTED",
            "to_phase": "PUBLIC_VALIDATION",
        }
    )
    mounts = (
        ExecutionMount(
            source_kind="output_root",
            source_id="process_17",
            target_path="/mnt/automarkov/artifacts/attestations",
            access="write_only",
        ),
        ExecutionMount(
            source_kind="checkout",
            source_id="a" * 40,
            target_path="/mnt/automarkov/artifacts/control",
            access="read_only",
        ),
    )
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(draft, strict=True),
        mounts=mounts,
        seccomp_profile_hash="sha256:" + sha256(seccomp_bytes).hexdigest(),
        apparmor_profile_hash="sha256:" + sha256(apparmor_bytes).hexdigest(),
    )
    commands: list[tuple[str, ...]] = []
    created_mounts: list[dict[str, object]] = []
    created_security_options: list[str] = []
    container_id = "c" * 64
    checkout_blob = b"print('worker')\n"
    checkout_blob_id = sha1(
        b"blob " + str(len(checkout_blob)).encode("ascii") + b"\0" + checkout_blob
    ).hexdigest()

    def result(stdout: bytes = b"", stderr: bytes = b"") -> OciCommandResult:
        return OciCommandResult(returncode=0, stdout=stdout, stderr=stderr)

    def command_runner(
        command: tuple[str, ...], timeout_seconds: float
    ) -> OciCommandResult:
        assert timeout_seconds > 0
        commands.append(command)
        git_command = command[command.index("git") :] if "git" in command else ()
        if git_command == ("git", "--version"):
            return result(b"git version 2.39.5\n")
        if git_command[:3] == ("git", "init", "--bare"):
            return result()
        if git_command and git_command[-4:-1] == (
            "remote",
            "add",
            "origin",
        ):
            assert git_command[-1] == manifest.repository_url
            return result()
        if "fetch" in git_command:
            assert git_command
            assert git_command[-1] == manifest.source_commit
            assert "protocol.file.allow=never" in git_command
            assert "http.followRedirects=false" in git_command
            assert "http.curloptResolve=github.com:443:93.184.216.34" in git_command
            return result()
        if git_command and git_command[-2:] == (
            "rev-parse",
            "FETCH_HEAD^{commit}",
        ):
            return result(manifest.source_commit.encode())
        if "ls-tree" in git_command:
            assert "-r" in git_command
            return result(
                b"100644 blob " + checkout_blob_id.encode("ascii") + b"\tworker.py\0"
            )
        if "cat-file" in git_command:
            assert git_command[-2:] == ("blob", checkout_blob_id)
            return result(checkout_blob)
        if command[:3] == ("apparmor_parser", "--replace", "--warn=all"):
            assert Path(command[-1]).read_bytes() == apparmor_bytes
            return result()
        if command[:3] == ("docker", "image", "inspect"):
            return result(
                canonical_json_bytes(
                    {
                        "Architecture": "amd64",
                        "Id": manifest.image_digest,
                        "Os": "linux",
                        "RepoDigests": [],
                    }
                )
            )
        if command[:3] == ("docker", "container", "create"):
            created_security_options.extend(
                command[index + 1]
                for index, argument in enumerate(command)
                if argument == "--security-opt"
            )
            for index, argument in enumerate(command):
                if argument != "--mount":
                    continue
                specification = command[index + 1]
                fields = dict(
                    field.split("=", 1)
                    for field in specification.split(",")
                    if "=" in field
                )
                created_mounts.append(
                    {
                        "Destination": fields["dst"],
                        "Mode": Path(fields["src"]).stat().st_mode & 0o777,
                        "RW": "readonly" not in specification,
                        "Source": fields["src"],
                        "Type": "bind",
                    }
                )
                if fields["dst"] == "/mnt/automarkov/artifacts/attestations":
                    output_root = Path(fields["src"])
                    output_root.joinpath("result.json").write_bytes(
                        b'{"schema_version":"automarkov.runner-output.v1","status":"ok"}'
                    )
            return result(container_id.encode())
        if command == ("docker", "container", "start", container_id):
            return result(container_id.encode())
        if command[:4] == ("docker", "container", "inspect", "--format={{json .}}"):
            return result(
                canonical_json_bytes(
                    {
                        "Config": {
                            "Cmd": list(manifest.argv[1:]),
                            "Entrypoint": [manifest.argv[0]],
                            "Labels": {
                                "automarkov.job_id": manifest.job_id,
                                "automarkov.process_execution_id": manifest.process_execution_id,
                            },
                            "User": "65532:65532",
                            "WorkingDir": "/mnt/automarkov/artifacts/control",
                        },
                        "HostConfig": {
                            "CapAdd": [],
                            "CapDrop": ["ALL"],
                            "CpuPeriod": 100_000,
                            "CpuQuota": 100_000,
                            "Memory": 1024 * 1024,
                            "MemorySwap": 1024 * 1024,
                            "NetworkMode": "none",
                            "PidsLimit": 16,
                            "Privileged": False,
                            "ReadonlyRootfs": True,
                            "RestartPolicy": {"Name": "no"},
                            "LogConfig": {
                                "Type": "json-file",
                                "Config": {"max-file": "1", "max-size": "16m"},
                            },
                            "SecurityOpt": created_security_options,
                            "Tmpfs": None,
                        },
                        "Image": manifest.image_digest,
                        "Mounts": created_mounts,
                        "State": (
                            {
                                "Pid": 0,
                                "Running": False,
                                "Status": "exited",
                                "ExitCode": 0,
                            }
                            if fast_exit
                            else {"Pid": 4242, "Running": True}
                        ),
                    }
                )
            )
        if command == ("docker", "container", "wait", container_id):
            return result(b"0\n")
        if command == ("docker", "container", "logs", container_id):
            return result(b"worker stdout", b"worker stderr")
        if command == (
            "docker",
            "container",
            "rm",
            "--force",
            "--volumes",
            container_id,
        ):
            return result()
        if command[:2] == ("apparmor_parser", "--remove"):
            assert Path(command[-1]).read_bytes() == apparmor_bytes
            return result()
        pytest.fail(f"unexpected command: {command!r}")

    class ResourceCollector:
        def collect(
            self,
            *,
            container_pid: int,
            output_root: Path,
            wait_for_exit: Callable[[], OciCommandResult],
            terminate: Callable[[], None],
            limits: FixedCommitResourceLimits,
            execution_started_monotonic: float,
        ) -> tuple[OciResourceObservation, OciCommandResult]:
            del terminate
            assert container_pid == 4242
            assert output_root.joinpath("result.json").is_file()
            assert limits.wall_time_ms == 60_000
            assert execution_started_monotonic == 10.0
            waited = wait_for_exit()
            return (
                OciResourceObservation(
                    schema_version="automarkov.oci-resource-observation.v1",
                    cpu_time_ms=1001 if limit_exceeded else 10,
                    peak_memory_bytes=4096,
                    peak_pids=2,
                    io_read_bytes=64,
                    io_write_bytes=128,
                    peak_disk_bytes=62,
                    wall_time_ms=100,
                    gpu_devices=(),
                    timed_out=False,
                    limit_exceeded=limit_exceeded,
                ),
                waited,
            )

    executor = OciFixedCommitExecutor(
        resolver=resolver,
        specified_event_head=_head(),
        job_manifest=manifest_reference,
        seccomp_profile_path=seccomp_profile,
        apparmor_profile_path=apparmor_profile,
        artifact_writer=MemoryRunnerArtifactWriter(resolver),
        resource_collector=None if fast_exit else ResourceCollector(),
        clock=lambda: "2026-08-12T11:00:00Z",
        monotonic=lambda: 10.0,
        process_profile_reader=lambda process_id: (
            apparmor_profile_name + " (enforce)"
            if process_id == 4242
            else pytest.fail("an exited container has no live process profile")
        ),
        command_runner=command_runner,
        repository_host_resolver=lambda _hostname, _port: ("93.184.216.34",),
    )
    store = MemoryRunnerArtifactStore()
    runner = FixedCommitRunner(
        artifact_store=store,
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )

    execution = runner.run_at_commit(
        FixedCommitExecutionRequest(
            schema_version="automarkov.fixed-commit-execution-request.v1",
            specified_event_head=_head(),
            job_manifest=manifest_reference,
        )
    )

    assert execution.terminal_result is None
    process = store.process_terminal_record(execution.process_terminal_record)
    assert (process.status, process.exit_code, process.reason_code) == (
        ("terminal_failure", 126, "fixed_commit_post_start_failure")
        if fast_exit
        else (
            ("terminal_failure", 125, "fixed_commit_resource_limit")
            if limit_exceeded
            else ("success", 0, "fixed_commit_completed")
        )
    )
    create = next(
        command
        for command in commands
        if command[:3] == ("docker", "container", "create")
    )
    assert ("--network", "none") == create[
        create.index("--network") : create.index("--network") + 2
    ]
    assert "--read-only" in create
    assert ("--log-driver", "json-file") == create[
        create.index("--log-driver") : create.index("--log-driver") + 2
    ]
    assert create.count("--log-opt") == 2
    first_log_option = create.index("--log-opt")
    second_log_option = create.index("--log-opt", first_log_option + 1)
    assert create[first_log_option : first_log_option + 2] == (
        "--log-opt",
        "max-size=16m",
    )
    assert create[second_log_option : second_log_option + 2] == (
        "--log-opt",
        "max-file=1",
    )
    assert "--tmpfs" not in create
    assert ("--cap-drop", "ALL") == create[
        create.index("--cap-drop") : create.index("--cap-drop") + 2
    ]
    assert any(
        command[-4:] == ("rm", "--force", "--volumes", container_id)
        for command in commands
    )
    assert commands[-1][:2] == ("apparmor_parser", "--remove")
    checkout_mount = next(
        item
        for item in created_mounts
        if item["Destination"] == "/mnt/automarkov/artifacts/control"
    )
    assert checkout_mount["Source"] != str(repository_root)
    assert checkout_mount["Mode"] == 0o555
    assert not Path(str(checkout_mount["Source"])).joinpath(".git").exists()


def test_oci_snapshot_uses_isolated_git_and_exact_blob_bytes(tmp_path: Path) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    blob = b"pointer-like bytes\r\nwithout checkout conversion\r\n"
    blob_id = sha1(b"blob " + str(len(blob)).encode("ascii") + b"\0" + blob).hexdigest()
    commands: list[tuple[str, ...]] = []

    def command_runner(
        command: tuple[str, ...], timeout_seconds: float
    ) -> OciCommandResult:
        assert timeout_seconds > 0
        commands.append(command)
        assert command[0] == "env"
        assert "GIT_CONFIG_NOSYSTEM=1" in command
        assert "GIT_CONFIG_GLOBAL=/dev/null" in command
        assert "GIT_CONFIG_SYSTEM=/dev/null" in command
        assert "GIT_ATTR_NOSYSTEM=1" in command
        assert "GIT_TERMINAL_PROMPT=0" in command
        assert "GIT_LFS_SKIP_SMUDGE=1" in command
        git_index = command.index("git")
        git_command = command[git_index:]
        if git_command == ("git", "--version"):
            return OciCommandResult(0, b"git version 2.39.5\n", b"")
        if git_command[:3] == ("git", "init", "--bare"):
            return OciCommandResult(0, b"", b"")
        if git_command[-2:] == ("rev-parse", "FETCH_HEAD^{commit}"):
            return OciCommandResult(0, manifest.source_commit.encode(), b"")
        if "ls-tree" in git_command:
            return OciCommandResult(
                0,
                b"100644 blob " + blob_id.encode("ascii") + b"\tworker.py\0",
                b"",
            )
        if "cat-file" in git_command:
            assert git_command[-2:] == ("blob", blob_id)
            return OciCommandResult(0, blob, b"")
        return OciCommandResult(0, b"", b"")

    executor = OciFixedCommitExecutor(
        resolver=resolver,
        specified_event_head=_head(),
        job_manifest=manifest_reference,
        seccomp_profile_path=tmp_path / "unused-seccomp",
        apparmor_profile_path=tmp_path / "unused-apparmor",
        artifact_writer=MemoryRunnerArtifactWriter(resolver),
        command_runner=command_runner,
        repository_host_resolver=lambda _hostname, _port: ("93.184.216.34",),
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    checkout = executor._materialize_checkout(manifest, scratch)

    assert checkout.joinpath("worker.py").read_bytes() == blob
    assert not any("checkout" in command for command in commands)


def test_oci_snapshot_rejects_a_nested_submodule_before_checkout(
    tmp_path: Path,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    commands: list[tuple[str, ...]] = []

    def command_runner(
        command: tuple[str, ...], timeout_seconds: float
    ) -> OciCommandResult:
        del timeout_seconds
        commands.append(command)
        if command[-2:] == ("git", "--version"):
            return OciCommandResult(0, b"git version 2.39.5\n", b"")
        if command[-2:] == ("rev-parse", "FETCH_HEAD^{commit}"):
            return OciCommandResult(
                returncode=0, stdout=manifest.source_commit.encode(), stderr=b""
            )
        if "ls-tree" in command:
            assert "-r" in command
            return OciCommandResult(
                returncode=0,
                stdout=(
                    b"160000 commit "
                    + b"2" * 40
                    + b"\tvendor/dependencies/unsafe-submodule\0"
                ),
                stderr=b"",
            )
        return OciCommandResult(returncode=0, stdout=b"", stderr=b"")

    executor = OciFixedCommitExecutor(
        resolver=resolver,
        specified_event_head=_head(),
        job_manifest=manifest_reference,
        seccomp_profile_path=tmp_path / "unused-seccomp",
        apparmor_profile_path=tmp_path / "unused-apparmor",
        artifact_writer=MemoryRunnerArtifactWriter(resolver),
        command_runner=command_runner,
        repository_host_resolver=lambda _hostname, _port: ("93.184.216.34",),
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with pytest.raises(RunnerPreflightError, match="submodule-free"):
        executor._materialize_checkout(manifest, scratch)

    assert not any("checkout" in command for command in commands)


def test_linux_cgroup_v2_collector_reads_the_exact_container_membership(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    proc_pid = proc_root / "4242"
    cgroup = cgroup_root / "automarkov" / "job"
    output_root = tmp_path / "outputs"
    proc_pid.mkdir(parents=True)
    cgroup.mkdir(parents=True)
    output_root.mkdir()
    proc_pid.joinpath("cgroup").write_text("0::/automarkov/job\n", encoding="ascii")
    cgroup.joinpath("cpu.stat").write_text("usage_usec 1500000\n", encoding="ascii")
    cgroup.joinpath("memory.peak").write_text("4096\n", encoding="ascii")
    cgroup.joinpath("pids.peak").write_text("3\n", encoding="ascii")
    cgroup.joinpath("io.stat").write_text(
        "8:0 rbytes=64 wbytes=128\n", encoding="ascii"
    )
    output_root.joinpath("result.json").write_bytes(b"{}")
    collector = LinuxCgroupV2ResourceCollector(
        proc_root=proc_root,
        cgroup_root=cgroup_root,
        monotonic=lambda: 1.0,
    )

    observation, waited = collector.collect(
        container_pid=4242,
        output_root=output_root,
        wait_for_exit=lambda: OciCommandResult(0, b"0\n", b""),
        terminate=lambda: pytest.fail("successful collection must not terminate"),
        limits=FixedCommitResourceLimits(
            schema_version="automarkov.fixed-commit-resource-limits.v1",
            phase="analysis",
            cpu_millis=1000,
            memory_bytes=8192,
            pids=4,
            io_bytes=1024,
            disk_bytes=1024,
            wall_time_ms=1000,
            gpu_devices=(),
        ),
        execution_started_monotonic=1.0,
    )

    assert waited.stdout == b"0\n"
    assert observation.model_dump(mode="json") == {
        "schema_version": "automarkov.oci-resource-observation.v1",
        "cpu_time_ms": 1500,
        "peak_memory_bytes": 4096,
        "peak_pids": 3,
        "io_read_bytes": 64,
        "io_write_bytes": 128,
        "peak_disk_bytes": 2,
        "wall_time_ms": 0,
        "gpu_devices": [],
        "timed_out": False,
        "limit_exceeded": False,
    }


def test_oci_output_is_scanned_before_any_immutable_artifact_write(
    tmp_path: Path,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )

    class RejectWrites:
        def write(self, *args: object, **kwargs: object) -> ArtifactReference:
            del args, kwargs
            pytest.fail("unscanned executor output must never reach the repository")

    executor = OciFixedCommitExecutor(
        resolver=resolver,
        specified_event_head=_head(),
        job_manifest=manifest_reference,
        seccomp_profile_path=tmp_path / "unused-seccomp",
        apparmor_profile_path=tmp_path / "unused-apparmor",
        artifact_writer=RejectWrites(),
    )
    content = (
        b'{"apiKey":"sk-proj-abcdefghijklmnopqrstuvwxyz",'
        b'"schema_version":"automarkov.runner-output.v1","status":"ok"}'
    )
    output = RunnerOutputBinding(
        schema_version="automarkov.runner-output-binding.v2",
        path="result.json",
        byte_size=len(content),
        media_type="application/json",
        content_hash="sha256:" + sha256(content).hexdigest(),
        content_schema_version="automarkov.runner-output.v1",
        content_b64url=base64.urlsafe_b64encode(content).decode().rstrip("="),
        schema_valid=True,
    )
    contract = resolver.resolve(_head(), manifest.output_contract)

    with pytest.raises(RunnerPreflightError, match="canonical JSON schema"):
        executor._persist_scanned_outputs(
            (output,),
            ExecutionOutputContract.model_validate_json(
                contract.payload_bytes, strict=True
            ),
            manifest,
            "2026-08-12T11:00:00Z",
            require_complete=True,
        )


def test_oci_embedded_payload_is_scanned_before_any_immutable_artifact_write(
    tmp_path: Path,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(
            _job_payload() | {"phase": "sealed_evaluation"}, strict=True
        ),
        output_paths=("candidate_api.json",),
    )
    embedded = _sealed_subject_payload(
        "candidate_api",
        manifest_reference,
        {"apiKey": "[REDACTED_SECRET]"},
    )
    wrapped = canonical_json_bytes(
        RunnerArtifactReferencePayload(
            schema_version="automarkov.runner-artifact-reference-output.v1",
            artifact_type="candidate_api",
            artifact=runner_payload_reference("candidate_api", embedded),
            artifact_payload_b64url=base64.urlsafe_b64encode(embedded)
            .decode()
            .rstrip("="),
        ).model_dump(mode="json")
    )
    output = RunnerOutputBinding(
        schema_version="automarkov.runner-output-binding.v2",
        path="candidate_api.json",
        byte_size=len(wrapped),
        media_type="application/json",
        content_hash="sha256:" + sha256(wrapped).hexdigest(),
        content_schema_version="automarkov.runner-artifact-reference-output.v1",
        content_b64url=base64.urlsafe_b64encode(wrapped).decode().rstrip("="),
        schema_valid=True,
    )

    class RejectWrites:
        def write(self, *args: object, **kwargs: object) -> ArtifactReference:
            del args, kwargs
            pytest.fail("embedded secret must be rejected before immutable write")

    executor = OciFixedCommitExecutor(
        resolver=resolver,
        specified_event_head=_head(),
        job_manifest=manifest_reference,
        seccomp_profile_path=tmp_path / "unused-seccomp",
        apparmor_profile_path=tmp_path / "unused-apparmor",
        artifact_writer=RejectWrites(),
    )
    contract = ExecutionOutputContract.model_validate_json(
        resolver.resolve(_head(), manifest.output_contract).payload_bytes, strict=True
    )

    with pytest.raises(RunnerPreflightError, match="secret field"):
        executor._persist_scanned_outputs(
            (output,),
            contract,
            manifest,
            "2026-08-12T11:00:00Z",
            require_complete=True,
        )


def test_oci_embedded_output_uses_the_repository_generated_reference(
    tmp_path: Path,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(
            _job_payload() | {"phase": "sealed_evaluation"}, strict=True
        ),
        output_paths=("candidate_api.json",),
    )
    embedded = _sealed_subject_payload(
        "candidate_api", manifest_reference, {"status": "safe"}
    )
    declared = runner_payload_reference("candidate_api", embedded)
    actual = ArtifactReference(
        artifact_id="artifact_" + "d" * 64,
        payload_hash="sha256:" + "e" * 64,
    )
    wrapped = canonical_json_bytes(
        RunnerArtifactReferencePayload(
            schema_version="automarkov.runner-artifact-reference-output.v1",
            artifact_type="candidate_api",
            artifact=declared,
            artifact_payload_b64url=base64.urlsafe_b64encode(embedded)
            .decode()
            .rstrip("="),
        ).model_dump(mode="json")
    )
    output = RunnerOutputBinding(
        schema_version="automarkov.runner-output-binding.v2",
        path="candidate_api.json",
        byte_size=len(wrapped),
        media_type="application/json",
        content_hash="sha256:" + sha256(wrapped).hexdigest(),
        content_schema_version="automarkov.runner-artifact-reference-output.v1",
        content_b64url=base64.urlsafe_b64encode(wrapped).decode().rstrip("="),
        schema_valid=True,
    )

    class RepositoryIdentityWriter:
        rebound: RunnerOutputBinding | None = None

        def write(
            self,
            artifact_type: str,
            value: object,
            **kwargs: object,
        ) -> ArtifactReference:
            del kwargs
            if artifact_type == "candidate_api":
                return actual
            assert artifact_type == "runner_output_binding"
            assert isinstance(value, RunnerOutputBinding)
            self.rebound = value
            return _artifact_reference("f")

    writer = RepositoryIdentityWriter()
    executor = OciFixedCommitExecutor(
        resolver=resolver,
        specified_event_head=_head(),
        job_manifest=manifest_reference,
        seccomp_profile_path=tmp_path / "unused-seccomp",
        apparmor_profile_path=tmp_path / "unused-apparmor",
        artifact_writer=writer,
    )
    contract = ExecutionOutputContract.model_validate_json(
        resolver.resolve(_head(), manifest.output_contract).payload_bytes, strict=True
    )

    executor._persist_scanned_outputs(
        (output,),
        contract,
        manifest,
        "2026-08-12T11:00:00Z",
        require_complete=True,
    )

    assert writer.rebound is not None
    rebound = RunnerArtifactReferencePayload.model_validate_json(
        writer.rebound.verified_content_bytes(), strict=True
    )
    assert rebound.artifact == actual


def test_oci_materialized_input_is_readable_by_the_non_root_container_user(
    tmp_path: Path,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    executor = OciFixedCommitExecutor(
        resolver=resolver,
        specified_event_head=_head(),
        job_manifest=manifest_reference,
        seccomp_profile_path=tmp_path / "unused-seccomp",
        apparmor_profile_path=tmp_path / "unused-apparmor",
        artifact_writer=MemoryRunnerArtifactWriter(resolver),
    )
    destination = tmp_path / "input"

    executor._materialize_input(manifest.input_artifacts[0], destination)

    assert destination.stat().st_mode & 0o777 == 0o555
    assert destination.joinpath("runner-input.json").stat().st_mode & 0o777 == 0o444
    assert destination.joinpath("payload.json").stat().st_mode & 0o777 == 0o444


def test_oci_multi_output_references_are_canonicalized_by_artifact_identity(
    tmp_path: Path,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    paths = ("candidate_api.json", "candidate_behavior.json")
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True),
        output_paths=paths,
    )
    executor = OciFixedCommitExecutor(
        resolver=resolver,
        specified_event_head=_head(),
        job_manifest=manifest_reference,
        seccomp_profile_path=tmp_path / "unused-seccomp",
        apparmor_profile_path=tmp_path / "unused-apparmor",
        artifact_writer=MemoryRunnerArtifactWriter(resolver),
    )
    outputs: list[RunnerOutputBinding] = []
    for index, path in enumerate(paths):
        payload = RunnerArtifactReferencePayload(
            schema_version="automarkov.runner-artifact-reference-output.v1",
            artifact_type=("candidate_api" if index == 0 else "candidate_behavior"),
            artifact=_artifact_reference(str(index + 3)),
        )
        content = canonical_json_bytes(payload.model_dump(mode="json"))
        outputs.append(
            RunnerOutputBinding(
                schema_version="automarkov.runner-output-binding.v2",
                path=path,
                byte_size=len(content),
                media_type="application/json",
                content_hash="sha256:" + sha256(content).hexdigest(),
                content_schema_version=(
                    "automarkov.runner-artifact-reference-output.v1"
                ),
                content_b64url=base64.urlsafe_b64encode(content).decode().rstrip("="),
                schema_valid=True,
            )
        )
    contract = ExecutionOutputContract.model_validate_json(
        resolver.resolve(_head(), manifest.output_contract).payload_bytes,
        strict=True,
    )

    references = executor._persist_scanned_outputs(
        tuple(outputs),
        contract,
        manifest,
        "2026-08-12T11:00:00Z",
        require_complete=True,
    )

    assert tuple(item.artifact_id for item in references) == tuple(
        sorted(item.artifact_id for item in references)
    )


def test_checkpoint_recovery_after_deadline_does_not_reexecute() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    executor = MemoryFixedCommitExecutor(
        resolver.freeze_execution_evidence(manifest, manifest_reference)
    )
    terminal = MemoryRunnerTerminalCommitter(
        terminal_event=EventReference(
            event_id="0198a123-4567-789a-8bcd-0123456789ab",
            sequence_no=_head().sequence_no,
            event_hash=_head().event_hash.root,
        ),
        terminal_head=_head(),
        terminal_state="COMPLETED",
        terminal_reason_code="completed",
    )

    class FailOnceTerminalCommitter:
        def __init__(self) -> None:
            self.call_count = 0

        def commit_terminal(
            self, process: ProcessExecutionTerminalRecord
        ) -> tuple[RunnerTerminalCommitReceipt, TerminalResult]:
            self.call_count += 1
            if self.call_count == 1:
                raise RuntimeError("injected post-checkpoint failure")
            return terminal.commit_terminal(process)

    committer = FailOnceTerminalCommitter()
    clock = {"now": "2026-08-12T11:00:00Z"}
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: clock["now"],
        terminal_committer=committer,
    )
    request = FixedCommitExecutionRequest(
        schema_version="automarkov.fixed-commit-execution-request.v1",
        specified_event_head=_head(),
        job_manifest=manifest_reference,
    )

    with pytest.raises(RuntimeError, match="post-checkpoint"):
        runner.run_at_commit(request)
    clock["now"] = "2026-08-12T12:00:01Z"
    result = runner.run_at_commit(request)

    assert result.terminal_result is not None
    assert executor.call_count == 1
    assert committer.call_count == 2


def test_expired_launch_and_semantically_invalid_scan_fail_closed() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    evidence = resolver.freeze_execution_evidence(manifest, manifest_reference)
    expired_executor = MemoryFixedCommitExecutor(evidence)
    expired_runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=expired_executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T12:00:01Z",
    )
    request = FixedCommitExecutionRequest(
        schema_version="automarkov.fixed-commit-execution-request.v1",
        specified_event_head=_head(),
        job_manifest=manifest_reference,
    )
    with pytest.raises(RunnerPreflightError, match="deadline"):
        expired_runner.run_at_commit(request)
    assert expired_executor.call_count == 0

    scan = OutputScanReport.model_validate_json(
        resolver.resolve(_head(), evidence.output_scan_report).payload_bytes,
        strict=True,
    )
    invalid_scan = scan.model_copy(update={"scanned_at": "2026-08-12T10:59:00Z"})
    invalid_scan_reference = resolver.register("output_scan_report", invalid_scan)
    scan_executor = MemoryFixedCommitExecutor(
        evidence.model_copy(update={"output_scan_report": invalid_scan_reference})
    )
    scan_runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=scan_executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )
    with pytest.raises(RunnerPreflightError, match="offline execution evidence"):
        scan_runner.run_at_commit(request)
    assert scan_executor.call_count == 1


def test_real_profile_and_input_bytes_are_rehashed_before_execution() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    evidence = resolver.freeze_execution_evidence(manifest, manifest_reference)
    profile = RuntimeProfileManifest.model_validate_json(
        resolver.resolve(_head(), manifest.profile_manifest).payload_bytes,
        strict=True,
    )
    assert profile.image_status == "built"
    assert profile.build_attestation_id is not None
    assert profile.import_smoke_attestation_id is not None
    executor = MemoryFixedCommitExecutor(evidence)
    resolver.inject_corrupt_bytes(
        manifest.input_artifacts[0],
        "runner_input",
        b'{"schema_version":"automarkov.runner-input.v1","tampered":true}',
    )
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )

    with pytest.raises(RunnerPreflightError, match="resolved artifact"):
        runner.run_at_commit(
            FixedCommitExecutionRequest(
                schema_version="automarkov.fixed-commit-execution-request.v1",
                specified_event_head=_head(),
                job_manifest=manifest_reference,
            )
        )
    assert executor.call_count == 0


def test_built_runtime_profile_attestations_are_payload_bound_parents() -> None:
    registered = _default_schema_registry().resolve(
        "runtime_profile_manifest",
        {"schema_version": "automarkov.runtime-profile-manifest.v2"},
    )

    assert {
        (
            binding.artifact_id_path,
            binding.payload_hash_path,
            binding.allowed_artifact_types,
            binding.cardinality,
        )
        for binding in registered.payload_parent_bindings
    } == {
        (
            "build_attestation_id",
            "build_attestation_hash",
            ("runner_runtime_attestation",),
            "optional",
        ),
        (
            "import_smoke_attestation_id",
            "import_smoke_attestation_hash",
            ("runner_runtime_attestation",),
            "optional",
        ),
    }


def test_runner_rehashes_the_exact_source_artifact_behind_each_input() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    descriptor = RunnerInput.model_validate_json(
        resolver.resolve(_head(), manifest.input_artifacts[0]).payload_bytes,
        strict=True,
    )
    resolver.inject_corrupt_bytes(
        descriptor.source_artifact,
        descriptor.source_artifact_type,
        b'{"schema_version":"automarkov.runner-source.v1","tampered":true}',
    )
    executor = MemoryFixedCommitExecutor(
        resolver.freeze_execution_evidence(manifest, manifest_reference)
    )
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )

    with pytest.raises(RunnerPreflightError, match="payload content identity"):
        runner.run_at_commit(
            FixedCommitExecutionRequest(
                schema_version="automarkov.fixed-commit-execution-request.v1",
                specified_event_head=_head(),
                job_manifest=manifest_reference,
            )
        )
    assert executor.call_count == 0


def test_runtime_attestation_requires_the_trusted_ed25519_key() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    executor = MemoryFixedCommitExecutor(
        resolver.freeze_execution_evidence(manifest, manifest_reference)
    )
    trusted_policy = next(iter(resolver.runtime_attestation_key_policies().values()))
    wrong_policy = RuntimeAttestationKeyPolicy(
        signing_key_id=trusted_policy.signing_key_id,
        issuer_id=trusted_policy.issuer_id,
        public_key=Ed25519PrivateKey.generate().public_key(),
        not_before=trusted_policy.not_before,
        not_after=trusted_policy.not_after,
        allowed_profile_ids=trusted_policy.allowed_profile_ids,
        allowed_kinds=trusted_policy.allowed_kinds,
    )
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
        trusted_runtime_attestation_keys={wrong_policy.signing_key_id: wrong_policy},
    )

    with pytest.raises(RunnerPreflightError, match="runtime attestation signature"):
        runner.run_at_commit(
            FixedCommitExecutionRequest(
                schema_version="automarkov.fixed-commit-execution-request.v1",
                specified_event_head=_head(),
                job_manifest=manifest_reference,
            )
        )

    assert executor.call_count == 0


def test_runtime_attestation_rejects_semantically_mismatched_evidence() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True),
        build_evidence_kind="import_smoke",
    )
    executor = MemoryFixedCommitExecutor(
        resolver.freeze_execution_evidence(manifest, manifest_reference)
    )
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )

    with pytest.raises(RunnerPreflightError, match="runtime evidence"):
        runner.run_at_commit(
            FixedCommitExecutionRequest(
                schema_version="automarkov.fixed-commit-execution-request.v1",
                specified_event_head=_head(),
                job_manifest=manifest_reference,
            )
        )
    assert executor.call_count == 0


def test_executor_output_bytes_are_rehashed_before_attestation() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    evidence = resolver.freeze_execution_evidence(manifest, manifest_reference)
    resolver.inject_corrupt_bytes(
        evidence.payload_outputs[0],
        "runner_output_binding",
        b'{"schema_version":"automarkov.runner-output-binding.v2","tampered":true}',
        payload_schema_version="automarkov.runner-output-binding.v2",
    )
    executor = MemoryFixedCommitExecutor(evidence)
    store = MemoryRunnerArtifactStore()
    runner = FixedCommitRunner(
        artifact_store=store,
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )

    with pytest.raises(RunnerPreflightError, match="payload is invalid"):
        runner.run_at_commit(
            FixedCommitExecutionRequest(
                schema_version="automarkov.fixed-commit-execution-request.v1",
                specified_event_head=_head(),
                job_manifest=manifest_reference,
            )
        )

    assert executor.call_count == 1


def test_terminal_failure_without_outputs_is_still_audited_and_attested() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    evidence = resolver.freeze_execution_evidence(manifest, manifest_reference)
    scan = OutputScanReport.model_validate_json(
        resolver.resolve(_head(), evidence.output_scan_report).payload_bytes,
        strict=True,
    ).model_copy(
        update={
            "scanned_outputs": (),
            "scanned_paths": (),
            "total_bytes": 0,
        }
    )
    scan_reference = resolver.register(
        "output_scan_report",
        scan,
        parent_artifact_ids=tuple(
            sorted(
                {
                    manifest_reference.artifact_id,
                    manifest.scanner_policy.artifact_id,
                    manifest.output_contract.artifact_id,
                }
            )
        ),
    )
    failed_evidence = evidence.model_copy(
        update={
            "status": "terminal_failure",
            "exit_code": 137,
            "reason_code": "process_oom",
            "payload_outputs": (),
            "output_scan_report": scan_reference,
        }
    )
    store = MemoryRunnerArtifactStore()
    runner = FixedCommitRunner(
        artifact_store=store,
        resolver=resolver,
        executor=MemoryFixedCommitExecutor(failed_evidence),
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:02:00Z",
    )

    result = runner.run_at_commit(
        FixedCommitExecutionRequest(
            schema_version="automarkov.fixed-commit-execution-request.v1",
            specified_event_head=_head(),
            job_manifest=manifest_reference,
        )
    )

    process = store.process_terminal_record(result.process_terminal_record)
    attestation = store.execution_attestation(result.execution_attestation)
    assert process.status == "terminal_failure"
    assert process.exit_code == 137
    assert process.payload_outputs == ()
    assert attestation.payload_outputs == ()


@pytest.mark.parametrize(
    "content",
    (
        b'{"schema_version":"automarkov.runner-output.v1","status":"ok","unexpected":"self-reported-valid"}',
        b'{"schema_version":"automarkov.unknown-output.v1","status":"ok"}',
        b'{ "schema_version": "automarkov.runner-output.v1", "status": "ok" }',
        b'{"api_key":"claimed-safe","schema_version":"automarkov.runner-output.v1"}',
        b'{"gold_answer":"hidden","schema_version":"automarkov.runner-output.v1"}',
        b'{"credential_locator":"secret://runner","schema_version":"automarkov.runner-output.v1"}',
    ),
)
def test_actual_output_bytes_are_locally_schema_validated_and_scanned(
    content: bytes,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(_job_payload(), strict=True)
    )
    evidence = resolver.freeze_execution_evidence(manifest, manifest_reference)
    output = RunnerOutputBinding(
        schema_version="automarkov.runner-output-binding.v2",
        path="result.json",
        byte_size=len(content),
        media_type="application/json",
        content_hash="sha256:" + sha256(content).hexdigest(),
        content_schema_version=(
            "automarkov.unknown-output.v1"
            if b"unknown-output" in content
            else "automarkov.runner-output.v1"
        ),
        content_b64url=base64.urlsafe_b64encode(content).decode().rstrip("="),
        schema_valid=True,
    )
    output_reference = resolver.register("runner_output_binding", output)
    scan = OutputScanReport.model_validate_json(
        resolver.resolve(_head(), evidence.output_scan_report).payload_bytes,
        strict=True,
    ).model_copy(
        update={
            "scanned_outputs": (output_reference,),
            "total_bytes": len(content),
            "schema_valid": True,
            "scan_passed": True,
        }
    )
    scan_reference = resolver.register(
        "output_scan_report",
        scan,
        parent_artifact_ids=tuple(
            sorted(
                {
                    manifest_reference.artifact_id,
                    manifest.scanner_policy.artifact_id,
                    manifest.output_contract.artifact_id,
                    output_reference.artifact_id,
                }
            )
        ),
    )
    executor = MemoryFixedCommitExecutor(
        evidence.model_copy(
            update={
                "payload_outputs": (output_reference,),
                "output_scan_report": scan_reference,
            }
        )
    )
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )

    with pytest.raises(RunnerPreflightError, match="actual output"):
        runner.run_at_commit(
            FixedCommitExecutionRequest(
                schema_version="automarkov.fixed-commit-execution-request.v1",
                specified_event_head=_head(),
                job_manifest=manifest_reference,
            )
        )
    assert executor.call_count == 1


_CANDIDATE_SUBJECT_PATHS = (
    "candidate_api.json",
    "candidate_behavior.json",
    "candidate_formal.json",
    "candidate_text.json",
)
_GOLD_SUBJECT_PATHS = tuple(
    path.replace("candidate_", "gold_", 1) for path in _CANDIDATE_SUBJECT_PATHS
)


def _sealed_subject_payload(
    artifact_type: str,
    job_manifest: ArtifactReference,
    value: object,
) -> bytes:
    contract = SEALED_SUBJECT_ARTIFACT_CONTRACTS[artifact_type]
    model = contract.model_type.model_validate(
        {
            "schema_version": contract.schema_version,
            "job_manifest": job_manifest.model_dump(mode="json"),
            "records": (
                {
                    "record_id": "record_0",
                    "value": value,
                },
            ),
        },
        strict=True,
    )
    return canonical_json_bytes(model.model_dump(mode="json"))


def _reference_output_evidence(
    resolver: MemoryTrustedRunnerArtifactResolver,
    manifest: FixedCommitJobManifest,
    manifest_reference: ArtifactReference,
    targets: tuple[tuple[str, ArtifactReference], ...],
) -> RawExecutionEvidence:
    evidence = resolver.freeze_execution_evidence(manifest, manifest_reference)
    wrappers: list[tuple[ArtifactReference, RunnerOutputBinding]] = []
    for output_kind, target in targets:
        content = canonical_json_bytes(
            RunnerArtifactReferencePayload(
                schema_version="automarkov.runner-artifact-reference-output.v1",
                artifact_type=output_kind,
                artifact=target,
            ).model_dump(mode="json")
        )
        wrapper = RunnerOutputBinding(
            schema_version="automarkov.runner-output-binding.v2",
            path=f"{output_kind}.json",
            byte_size=len(content),
            media_type="application/json",
            content_hash="sha256:" + sha256(content).hexdigest(),
            content_schema_version="automarkov.runner-artifact-reference-output.v1",
            content_b64url=base64.urlsafe_b64encode(content).decode().rstrip("="),
            schema_valid=True,
        )
        wrappers.append((resolver.register("runner_output_binding", wrapper), wrapper))
    wrappers.sort(key=lambda item: item[0].artifact_id.encode("utf-8"))
    output_references = tuple(item[0] for item in wrappers)
    output_paths = tuple(
        sorted((item[1].path for item in wrappers), key=lambda item: item.encode())
    )
    total_bytes = sum(item[1].byte_size for item in wrappers)
    scan = OutputScanReport.model_validate_json(
        resolver.resolve(_head(), evidence.output_scan_report).payload_bytes,
        strict=True,
    ).model_copy(
        update={
            "scanned_outputs": output_references,
            "scanned_paths": output_paths,
            "total_bytes": total_bytes,
        }
    )
    scan_reference = resolver.register(
        "output_scan_report",
        scan,
        parent_artifact_ids=tuple(
            sorted(
                {
                    manifest_reference.artifact_id,
                    manifest.scanner_policy.artifact_id,
                    manifest.output_contract.artifact_id,
                    *(item.artifact_id for item in output_references),
                }
            )
        ),
    )
    return evidence.model_copy(
        update={
            "payload_outputs": output_references,
            "output_scan_report": scan_reference,
        }
    )


@pytest.mark.parametrize(
    "subject_paths", (_CANDIDATE_SUBJECT_PATHS, _GOLD_SUBJECT_PATHS)
)
def test_sealed_worker_job_accepts_four_typed_subject_output_paths(
    subject_paths: tuple[str, ...],
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    draft = FixedCommitJobManifest.model_validate(
        _job_payload() | {"phase": "sealed_evaluation"}, strict=True
    )
    manifest, manifest_reference = resolver.freeze_job(
        draft,
        output_paths=subject_paths,
    )
    targets = tuple(
        (
            path.removesuffix(".json"),
            resolver.register_payload(
                path.removesuffix(".json"),
                SEALED_SUBJECT_ARTIFACT_CONTRACTS[
                    path.removesuffix(".json")
                ].schema_version,
                _sealed_subject_payload(
                    path.removesuffix(".json"),
                    manifest_reference,
                    {"status": "safe"},
                ),
            ),
        )
        for path in subject_paths
    )
    evidence = _reference_output_evidence(
        resolver, manifest, manifest_reference, targets
    )
    store = MemoryRunnerArtifactStore()
    runner = FixedCommitRunner(
        artifact_store=store,
        resolver=resolver,
        executor=MemoryFixedCommitExecutor(evidence),
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )

    result = runner.run_at_commit(
        FixedCommitExecutionRequest(
            schema_version="automarkov.fixed-commit-execution-request.v1",
            specified_event_head=_head(),
            job_manifest=manifest_reference,
        )
    )

    process = store.process_terminal_record(result.process_terminal_record)
    assert len(process.payload_outputs) == 4


def test_oci_executor_persists_fresh_embedded_sealed_worker_payload() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(
            _job_payload() | {"phase": "sealed_evaluation"}, strict=True
        ),
        output_paths=("candidate_api.json",),
    )
    payload = _sealed_subject_payload(
        "candidate_api", manifest_reference, {"status": "fresh"}
    )
    target = runner_payload_reference("candidate_api", payload)
    wrapped = canonical_json_bytes(
        RunnerArtifactReferencePayload(
            schema_version="automarkov.runner-artifact-reference-output.v1",
            artifact_type="candidate_api",
            artifact=target,
            artifact_payload_b64url=base64.urlsafe_b64encode(payload)
            .decode()
            .rstrip("="),
        ).model_dump(mode="json")
    )
    output = RunnerOutputBinding(
        schema_version="automarkov.runner-output-binding.v2",
        path="candidate_api.json",
        byte_size=len(wrapped),
        media_type="application/json",
        content_hash="sha256:" + sha256(wrapped).hexdigest(),
        content_schema_version="automarkov.runner-artifact-reference-output.v1",
        content_b64url=base64.urlsafe_b64encode(wrapped).decode().rstrip("="),
        schema_valid=True,
    )
    contract = ExecutionOutputContract.model_validate_json(
        resolver.resolve(_head(), manifest.output_contract).payload_bytes, strict=True
    )
    executor = OciFixedCommitExecutor(
        resolver=resolver,
        specified_event_head=_head(),
        job_manifest=manifest_reference,
        seccomp_profile_path=Path("unused-seccomp"),
        apparmor_profile_path=Path("unused-apparmor"),
        artifact_writer=MemoryRunnerArtifactWriter(resolver),
    )

    persisted = executor._persist_scanned_outputs(
        (output,), contract, manifest, "2026-08-12T11:00:00Z", require_complete=True
    )

    assert len(persisted) == 1
    resolved_target = resolver.resolve(_head(), target)
    assert resolved_target.artifact_type == "candidate_api"
    assert resolved_target.payload_bytes == payload


def test_oci_executor_rejects_oversized_output_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / "output"
    output_root.mkdir()
    oversized = output_root / "result.json"
    oversized.write_bytes(b"x" * 17)
    contract = ExecutionOutputContract(
        schema_version="automarkov.execution-output-contract.v1",
        allowed_paths=("result.json",),
        output_schemas=(
            OutputSchemaBinding(
                path="result.json",
                schema_version="automarkov.runner-output.v1",
                schema_identity_hash=RUNNER_RESULT_PAYLOAD_SCHEMA_HASH,
            ),
        ),
        maximum_total_bytes=16,
        require_regular_files=True,
        forbid_symlinks=True,
        forbid_extra_outputs=True,
    )
    monkeypatch.setattr(
        OciFixedCommitExecutor,
        "_read_exact_file",
        staticmethod(lambda descriptor, size: (_ for _ in ()).throw(AssertionError())),
    )

    with pytest.raises(RunnerPreflightError, match="byte limit"):
        OciFixedCommitExecutor._read_outputs(output_root, contract)


@pytest.mark.parametrize(
    ("actual_type", "declared_type", "target_payload", "tamper", "match"),
    (
        (
            "candidate_text",
            "candidate_api",
            {"schema_version": "automarkov.candidate-output.v1", "status": "safe"},
            False,
            "artifact type",
        ),
        (
            "candidate_api",
            "candidate_api",
            {
                "schema_version": "automarkov.candidate-output.v1",
                "api_key": "claimed-safe",
            },
            False,
            "secret field",
        ),
        (
            "candidate_api",
            "candidate_api",
            {"schema_version": "automarkov.candidate-output.v1", "status": "safe"},
            True,
            "content identity",
        ),
    ),
)
def test_referenced_output_type_hash_and_payload_are_verified_before_issuance(
    actual_type: str,
    declared_type: str,
    target_payload: object,
    tamper: bool,
    match: str,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(
            _job_payload() | {"phase": "sealed_evaluation"}, strict=True
        ),
        output_paths=("candidate_api.json",),
    )
    payload_bytes = _sealed_subject_payload(
        actual_type,
        manifest_reference,
        target_payload,
    )
    contract = SEALED_SUBJECT_ARTIFACT_CONTRACTS[actual_type]
    target = resolver.register_payload(
        actual_type,
        contract.schema_version,
        payload_bytes,
    )
    if tamper:
        resolver.inject_corrupt_bytes(
            target,
            actual_type,
            payload_bytes + b" ",
            payload_schema_version=contract.schema_version,
        )
    evidence = _reference_output_evidence(
        resolver,
        manifest,
        manifest_reference,
        ((declared_type, target),),
    )
    executor = MemoryFixedCommitExecutor(evidence)
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )

    with pytest.raises(RunnerPreflightError, match=match):
        runner.run_at_commit(
            FixedCommitExecutionRequest(
                schema_version="automarkov.fixed-commit-execution-request.v1",
                specified_event_head=_head(),
                job_manifest=manifest_reference,
            )
        )
    assert executor.call_count == 1


def test_sealed_subject_artifact_contracts_are_closed_and_exact() -> None:
    expected_types = {
        "candidate_api",
        "candidate_behavior",
        "candidate_formal",
        "candidate_text",
        "gold_api",
        "gold_behavior",
        "gold_formal",
        "gold_text",
    }
    assert set(SEALED_SUBJECT_ARTIFACT_CONTRACTS) == expected_types
    registry = _default_schema_registry()
    job_manifest = ArtifactReference.model_validate(_REF, strict=True)
    for artifact_type, contract in SEALED_SUBJECT_ARTIFACT_CONTRACTS.items():
        registered = registry.resolve(
            artifact_type,
            {"schema_version": contract.schema_version},
        )
        assert tuple(
            (
                binding.artifact_id_path,
                binding.payload_hash_path,
                binding.allowed_artifact_types,
                binding.cardinality,
            )
            for binding in registered.payload_parent_bindings
        ) == (
            (
                "job_manifest.artifact_id",
                "job_manifest.payload_hash",
                ("fixed_commit_job_manifest",),
                "one",
            ),
        )
        payload = {
            "schema_version": contract.schema_version,
            "job_manifest": job_manifest.model_dump(mode="json"),
            "records": ({"record_id": "record_0", "value": {"ok": True}},),
        }
        model = contract.model_type.model_validate(payload, strict=True)
        assert model.model_dump(mode="json")["schema_version"] == (
            contract.schema_version
        )
        with pytest.raises(ValidationError):
            contract.model_type.model_validate(
                payload | {"unexpected": True}, strict=True
            )
        with pytest.raises(ValidationError):
            contract.model_type.model_validate(
                payload | {"schema_version": "automarkov.wrong-output.v1"},
                strict=True,
            )

    assert CandidateApiOutput.model_json_schema()["properties"]["schema_version"][
        "const"
    ] == ("automarkov.candidate-api-output.v1")
    assert SealedSubjectRecord.model_fields.keys() == {"record_id", "value"}


@pytest.mark.parametrize(
    "secret_alias",
    (
        "secret",
        "client_secret",
        "api_secret",
        "token",
        "auth_token",
        "bearer_token",
        "id_token",
        "session_token",
        "credential",
        "credentials",
        "authorization",
        "authorization_header",
        "apiKey",
        "clientSecret",
        "authorizationHeader",
        "bearer-token",
    ),
)
def test_referenced_subject_payload_rejects_exact_secret_aliases(
    secret_alias: str,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(
            _job_payload() | {"phase": "sealed_evaluation"}, strict=True
        ),
        output_paths=("candidate_api.json",),
    )
    contract = SEALED_SUBJECT_ARTIFACT_CONTRACTS["candidate_api"]
    payload_bytes = _sealed_subject_payload(
        "candidate_api",
        manifest_reference,
        {secret_alias: "claimed-safe"},
    )
    target = resolver.register_payload(
        "candidate_api", contract.schema_version, payload_bytes
    )
    evidence = _reference_output_evidence(
        resolver,
        manifest,
        manifest_reference,
        (("candidate_api", target),),
    )
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=MemoryFixedCommitExecutor(evidence),
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )

    with pytest.raises(RunnerPreflightError, match="secret field"):
        runner.run_at_commit(
            FixedCommitExecutionRequest(
                schema_version="automarkov.fixed-commit-execution-request.v1",
                specified_event_head=_head(),
                job_manifest=manifest_reference,
            )
        )


def test_referenced_subject_payload_does_not_reject_nearby_benign_keys() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(
            _job_payload() | {"phase": "sealed_evaluation"}, strict=True
        ),
        output_paths=("candidate_api.json",),
    )
    contract = SEALED_SUBJECT_ARTIFACT_CONTRACTS["candidate_api"]
    payload_bytes = _sealed_subject_payload(
        "candidate_api",
        manifest_reference,
        {
            "secretary": "available",
            "token_count": 3,
            "credential_status": "absent",
            "authorization_status": "not-required",
        },
    )
    target = resolver.register_payload(
        "candidate_api", contract.schema_version, payload_bytes
    )
    evidence = _reference_output_evidence(
        resolver,
        manifest,
        manifest_reference,
        (("candidate_api", target),),
    )
    store = MemoryRunnerArtifactStore()
    result = FixedCommitRunner(
        artifact_store=store,
        resolver=resolver,
        executor=MemoryFixedCommitExecutor(evidence),
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    ).run_at_commit(
        FixedCommitExecutionRequest(
            schema_version="automarkov.fixed-commit-execution-request.v1",
            specified_event_head=_head(),
            job_manifest=manifest_reference,
        )
    )

    assert store.process_terminal_record(result.process_terminal_record).status == (
        "success"
    )


@pytest.mark.parametrize(
    "credential_value",
    (
        "sk-proj-" + "A" * 48,
        "ghp_" + "A" * 36,
        "AKIA" + "A" * 16,
        "Bearer " + "A" * 48,
    ),
)
def test_referenced_subject_payload_rejects_high_confidence_credential_values(
    credential_value: str,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(
            _job_payload() | {"phase": "sealed_evaluation"}, strict=True
        ),
        output_paths=("candidate_api.json",),
    )
    contract = SEALED_SUBJECT_ARTIFACT_CONTRACTS["candidate_api"]
    payload_bytes = _sealed_subject_payload(
        "candidate_api",
        manifest_reference,
        {"message": credential_value},
    )
    target = resolver.register_payload(
        "candidate_api", contract.schema_version, payload_bytes
    )
    evidence = _reference_output_evidence(
        resolver,
        manifest,
        manifest_reference,
        (("candidate_api", target),),
    )
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=MemoryFixedCommitExecutor(evidence),
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )

    with pytest.raises(RunnerPreflightError, match="credential value"):
        runner.run_at_commit(
            FixedCommitExecutionRequest(
                schema_version="automarkov.fixed-commit-execution-request.v1",
                specified_event_head=_head(),
                job_manifest=manifest_reference,
            )
        )


@pytest.mark.parametrize(
    ("violation", "match"),
    (
        ("extra_field", "subject artifact schema"),
        ("schema_identity", "artifact schema"),
        ("job_binding", "subject artifact binding"),
    ),
)
def test_referenced_subject_payload_requires_strict_schema_and_job_binding(
    violation: str,
    match: str,
) -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(
        FixedCommitJobManifest.model_validate(
            _job_payload() | {"phase": "sealed_evaluation"}, strict=True
        ),
        output_paths=("candidate_api.json",),
    )
    contract = SEALED_SUBJECT_ARTIFACT_CONTRACTS["candidate_api"]
    payload = {
        "schema_version": contract.schema_version,
        "job_manifest": manifest_reference.model_dump(mode="json"),
        "records": [{"record_id": "record_0", "value": {"status": "safe"}}],
    }
    if violation == "extra_field":
        payload["unexpected"] = True
    elif violation == "job_binding":
        payload["job_manifest"] = _REF
    payload_bytes = canonical_json_bytes(payload)
    target = resolver.register_payload(
        "candidate_api",
        (
            "automarkov.wrong-output.v1"
            if violation == "schema_identity"
            else contract.schema_version
        ),
        payload_bytes,
    )
    evidence = _reference_output_evidence(
        resolver,
        manifest,
        manifest_reference,
        (("candidate_api", target),),
    )
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=MemoryFixedCommitExecutor(evidence),
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )

    with pytest.raises(RunnerPreflightError, match=match):
        runner.run_at_commit(
            FixedCommitExecutionRequest(
                schema_version="automarkov.fixed-commit-execution-request.v1",
                specified_event_head=_head(),
                job_manifest=manifest_reference,
            )
        )
