from __future__ import annotations

import pytest

from automarkov.adapters import FixedCommitExecutionSandbox
from automarkov.domain.models import ArtifactId, RunId, Sha256Digest, VerifiedEventHead
from automarkov.fixed_commit_runner import (
    ExecutionResourceUsage,
    FixedCommitExecutionRequest,
    FixedCommitJobManifest,
    FixedCommitRunner,
    MemoryFixedCommitExecutor,
    MemoryRunnerArtifactStore,
    MemoryRunnerTerminalCommitter,
    MemoryTrustedRunnerArtifactResolver,
    OutputScanReport,
    RunnerPreflightError,
    RunnerReplayError,
    verify_execution_attestation_signature,
)
from automarkov.lifecycle import (
    ArtifactReference,
    EventReference,
    ProcessExecutionTerminalRecord,
    TerminalResult,
)
from automarkov.public import ExecutionSandbox, FixedCommitJobRequest


def _reference(character: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_id="artifact_" + character * 64,
        payload_hash="sha256:" + character * 64,
    )


def _manifest() -> FixedCommitJobManifest:
    references = iter("123456")

    def reference() -> ArtifactReference:
        return _reference(next(references))

    return FixedCommitJobManifest(
        schema_version="automarkov.fixed-commit-job-manifest.v1",
        job_id="job_contract",
        process_execution_id="process_contract",
        experiment_id="experiment_contract",
        run_id="run_contract",
        principal_id="principal_runner",
        repository_url="https://github.com/example/benchmark.git",
        source_commit="a" * 40,
        profile_manifest=reference(),
        profile_id="runner-control",
        profile_lock_hash="sha256:" + "7" * 64,
        target_platform="linux/amd64",
        image_digest="sha256:" + "8" * 64,
        input_artifacts=(_reference("9"),),
        suite_id="suite_taxi",
        variant_id="variant_0",
        track_id="track_public",
        method_id="method_automarkov",
        pair_id="pair_0",
        generation_seed=17,
        rl_seed=23,
        phase="training",
        argv=("/opt/venv/bin/python", "-m", "automarkov.worker"),
        working_directory="checkout",
        resource_limits=reference(),
        network_policy=reference(),
        mount_policy=reference(),
        capability_policy=reference(),
        output_contract=_reference("a"),
        scanner_policy=_reference("b"),
        from_phase="TRAINING_SMOKE_TESTING",
        to_phase="POLICY_TRAINING",
        launch_deadline="2026-08-12T12:00:00Z",
    )


def _head() -> VerifiedEventHead:
    return VerifiedEventHead(
        run_id=RunId(root="run_contract"),
        sequence_no=9,
        event_hash=Sha256Digest(root="sha256:" + "f" * 64),
    )


def test_attestation_signature_replay_and_exact_parent_dag() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(_manifest())
    evidence = resolver.freeze_execution_evidence(manifest, manifest_reference)
    key = resolver.runner_signing_key
    store = MemoryRunnerArtifactStore()
    executor = MemoryFixedCommitExecutor(evidence)
    runner = FixedCommitRunner(
        artifact_store=store,
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )
    request = FixedCommitExecutionRequest(
        schema_version="automarkov.fixed-commit-execution-request.v1",
        specified_event_head=_head(),
        job_manifest=manifest_reference,
    )

    result = runner.run_at_commit(request)
    attestation = store.execution_attestation(result.execution_attestation)
    verify_execution_attestation_signature(attestation, key.public_key())

    expected_process_parents = tuple(
        sorted(
            {
                manifest_reference.artifact_id,
                evidence.resource_usage.artifact_id,
                *(reference.artifact_id for reference in attestation.payload_outputs),
            }
        )
    )
    expected_attestation_parents = tuple(
        sorted(
            {
                manifest_reference.artifact_id,
                result.process_terminal_record.artifact_id,
                evidence.output_scan_report.artifact_id,
                *(reference.artifact_id for reference in attestation.payload_outputs),
            }
        )
    )
    assert store.parents(result.process_terminal_record) == expected_process_parents
    assert store.parents(result.execution_attestation) == expected_attestation_parents
    process = store.process_terminal_record(result.process_terminal_record)
    assert set(process.model_dump(mode="json")) == {
        "capability_decision_hash",
        "created_at",
        "egress_log_hash",
        "exit_code",
        "experiment_id",
        "finished_at",
        "job_id",
        "job_manifest",
        "mount_attestation_hash",
        "network_log_hash",
        "payload_outputs",
        "principal_id",
        "process_execution_id",
        "profile_id",
        "reason_code",
        "resource_usage",
        "run_id",
        "schema_version",
        "signing_domain",
        "started_at",
        "status",
        "stderr_hash",
        "stdout_hash",
    }
    assert process.network_log_hash == evidence.network_log.payload_hash
    assert process.mount_attestation_hash == evidence.mount_attestation.payload_hash
    assert (
        process.capability_decision_hash
        == evidence.capability_decision_log.payload_hash
    )
    assert process.egress_log_hash == evidence.egress_decision_log.payload_hash
    assert attestation.output_scan_report == evidence.output_scan_report

    tampered = attestation.model_copy(update={"signature_b64url": "A" * 86})
    with pytest.raises(RunnerReplayError, match="signature"):
        verify_execution_attestation_signature(tampered, key.public_key())

    _, conflicting_reference = resolver.freeze_job(
        manifest.model_copy(update={"rl_seed": 24})
    )
    conflicting = request.model_copy(update={"job_manifest": conflicting_reference})
    with pytest.raises(RunnerReplayError, match="conflicting"):
        runner.run_at_commit(conflicting)
    assert executor.call_count == 1


def test_public_execution_sandbox_adapts_the_production_runner_exactly() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(_manifest())
    evidence = resolver.freeze_execution_evidence(manifest, manifest_reference)
    store = MemoryRunnerArtifactStore()
    executor = MemoryFixedCommitExecutor(evidence)
    committer = MemoryRunnerTerminalCommitter(
        terminal_event=EventReference(
            event_id="0198a123-4567-789a-8bcd-0123456789ab",
            sequence_no=_head().sequence_no,
            event_hash=_head().event_hash.root,
        ),
        terminal_head=_head(),
        terminal_state="COMPLETED",
        terminal_reason_code="completed",
    )
    sandbox = FixedCommitExecutionSandbox(
        FixedCommitRunner(
            artifact_store=store,
            resolver=resolver,
            executor=executor,
            signing_key_id="key_runner",
            signing_key=resolver.runner_signing_key,
            clock=lambda: "2026-08-12T11:00:00Z",
            terminal_committer=committer,
        )
    )

    result = sandbox.run_at_commit(
        FixedCommitJobRequest(
            schema_version="automarkov.fixed-commit-job-request.v2",
            specified_event_head=_head(),
            job_manifest=manifest_reference,
        )
    )

    assert isinstance(sandbox, ExecutionSandbox)
    assert result.schema_version == "automarkov.execution-result.v2"
    assert result.process_terminal_record is not None
    assert result.execution_attestation is not None
    assert result.terminal_result is not None
    assert result.terminal_record_artifact_id.root == (
        result.process_terminal_record.artifact_id
    )
    process = store.process_terminal_record(result.process_terminal_record)
    attestation = store.execution_attestation(result.execution_attestation)
    terminal = store.get(result.terminal_result, TerminalResult)
    assert process.job_manifest == manifest_reference
    assert attestation.process_terminal_record == result.process_terminal_record
    assert attestation.terminal_result == result.terminal_result
    assert isinstance(terminal, TerminalResult)
    assert terminal.process_execution_terminal_record == result.process_terminal_record
    assert executor.call_count == 1
    assert committer.call_count == 1

    with pytest.raises(ValueError, match="v2"):
        sandbox.run_at_commit(
            FixedCommitJobRequest(
                schema_version="automarkov.fixed-commit-job-request.v1",
                job_manifest_artifact_id=ArtifactId(
                    root=manifest_reference.artifact_id
                ),
            )
        )
    assert executor.call_count == 1
    assert committer.call_count == 1


def test_terminal_commit_precedes_attestation_and_all_evidence_is_revalidated() -> None:
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(_manifest())
    evidence = resolver.freeze_execution_evidence(manifest, manifest_reference)
    store = MemoryRunnerArtifactStore()
    committer = MemoryRunnerTerminalCommitter(
        terminal_event=EventReference(
            event_id="0198a123-4567-789a-8bcd-0123456789ab",
            sequence_no=_head().sequence_no,
            event_hash=_head().event_hash.root,
        ),
        terminal_head=_head(),
        terminal_state="COMPLETED",
        terminal_reason_code="completed",
    )
    runner = FixedCommitRunner(
        artifact_store=store,
        resolver=resolver,
        executor=MemoryFixedCommitExecutor(evidence),
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
        terminal_committer=committer,
    )

    result = runner.run_at_commit(
        FixedCommitExecutionRequest(
            schema_version="automarkov.fixed-commit-execution-request.v1",
            specified_event_head=_head(),
            job_manifest=manifest_reference,
        )
    )

    assert result.terminal_result is not None
    assert committer.call_count == 1
    terminal = store.get(result.terminal_result, TerminalResult)
    process = store.process_terminal_record(result.process_terminal_record)
    attestation = store.execution_attestation(result.execution_attestation)
    assert isinstance(terminal, TerminalResult)
    assert isinstance(process, ProcessExecutionTerminalRecord)
    assert terminal.process_execution_terminal_record == result.process_terminal_record
    assert attestation.terminal_result == result.terminal_result
    assert process.payload_outputs == evidence.payload_outputs
    assert evidence.output_scan_report not in process.payload_outputs
    assert isinstance(
        store.get(evidence.output_scan_report, OutputScanReport), OutputScanReport
    )
    assert isinstance(
        store.get(evidence.resource_usage, ExecutionResourceUsage),
        ExecutionResourceUsage,
    )


def test_resource_usage_and_concrete_policy_decisions_are_mechanically_bounded() -> (
    None
):
    resolver = MemoryTrustedRunnerArtifactResolver(_head())
    manifest, manifest_reference = resolver.freeze_job(_manifest())
    evidence = resolver.freeze_execution_evidence(manifest, manifest_reference)
    usage = ExecutionResourceUsage.model_validate_json(
        resolver.resolve(_head(), evidence.resource_usage).payload_bytes,
        strict=True,
    )
    oversized_usage = usage.model_copy(update={"peak_pids": 17})
    oversized_reference = resolver.register("execution_resource_usage", oversized_usage)
    executor = MemoryFixedCommitExecutor(
        evidence.model_copy(update={"resource_usage": oversized_reference})
    )
    runner = FixedCommitRunner(
        artifact_store=MemoryRunnerArtifactStore(),
        resolver=resolver,
        executor=executor,
        signing_key_id="key_runner",
        signing_key=resolver.runner_signing_key,
        clock=lambda: "2026-08-12T11:00:00Z",
    )

    with pytest.raises(RunnerPreflightError, match="offline execution evidence"):
        runner.run_at_commit(
            FixedCommitExecutionRequest(
                schema_version="automarkov.fixed-commit-execution-request.v1",
                specified_event_head=_head(),
                job_manifest=manifest_reference,
            )
        )
    assert executor.call_count == 1
