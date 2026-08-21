from __future__ import annotations

import base64
from hashlib import sha256
from pathlib import Path
from typing import Literal, TypeAlias

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automarkov.canonical import canonical_json_bytes
from automarkov.domain import StrictFrozenModel, VerifiedEventHead
from automarkov.fixed_commit_runner import (
    CapabilityDecisionLog,
    EgressDecisionLog,
    ExecutionMount,
    MountAttestation,
    PhaseNetworkPolicy,
    RunnerArtifactReferencePayload,
    RunnerOutputBinding,
    execution_attestation_signing_bytes,
    runner_artifact_reference,
)
from automarkov.lifecycle import (
    ArtifactReference,
    ExecutionAttestation,
    ManifestEventSigningKey,
)
from automarkov.repository import InMemoryArtifactRepository
from automarkov.sealed_evaluation import (
    BoundedWorkerOutput,
    CandidateCapability,
    ComparatorCapability,
    DeniedCapability,
    E2EGateCommitError,
    E2EGateCommitter,
    E2EGateEvaluationRequest,
    E2EGateExecutionCommitInput,
    E2EGateKeyPolicy,
    E2EGateVerdict,
    FrozenE2EProtocolRegistry,
    GoldCapability,
    InMemoryE2EGateCommitter,
    ResolvedSealedWorkerExecution,
    SealedE2EGate,
    SealedWorkerBinding,
    SealedWorkerTopology,
    SqliteE2EGateCommitter,
    TrustedEvaluatorProtocol,
    WorkerIsolationPolicy,
    e2e_payload_hash,
    sign_e2e_request,
    sign_e2e_verdict,
    sign_worker_evidence,
)

_KEYS = {
    "coordinator": Ed25519PrivateKey.from_private_bytes(b"\x11" * 32),
    "evaluator": Ed25519PrivateKey.from_private_bytes(b"\x22" * 32),
    "candidate": Ed25519PrivateKey.from_private_bytes(b"\x33" * 32),
    "gold": Ed25519PrivateKey.from_private_bytes(b"\x44" * 32),
    "comparator": Ed25519PrivateKey.from_private_bytes(b"\x55" * 32),
}
_RUNNER_KEYS = {
    role: Ed25519PrivateKey.from_private_bytes(byte * 32)
    for role, byte in {
        "candidate": b"\x63",
        "gold": b"\x64",
        "comparator": b"\x65",
    }.items()
}
_PROFILES = {
    "candidate": ("rllib-core", "sha256:" + "7" * 64),
    "gold": ("sealed-env-taxi-gold", "sha256:" + "e" * 64),
    "comparator": ("sealed-evaluator-rllib", "sha256:" + "9" * 64),
}
_RUNNER_OUTPUT_REPOSITORY = InMemoryArtifactRepository()
_COMPARATOR_SUBJECT_KINDS = (
    "candidate_api",
    "candidate_behavior",
    "candidate_formal",
    "candidate_text",
    "gold_api",
    "gold_behavior",
    "gold_formal",
    "gold_text",
)
KeyKind: TypeAlias = Literal[
    "coordinator",
    "evaluator",
    "candidate_worker",
    "gold_worker",
    "comparator",
]
Case: TypeAlias = tuple[
    ArtifactReference,
    E2EGateEvaluationRequest,
    ArtifactReference,
    E2EGateVerdict,
    SealedWorkerTopology,
]


def _ref(name: str, digit: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=f"artifact_{sha256(name.encode()).hexdigest()}",
        payload_hash=f"sha256:{digit * 64}",
    )


def _payload_ref(name: str, value: StrictFrozenModel) -> ArtifactReference:
    try:
        payload_hash = e2e_payload_hash(value)
    except ValueError:
        payload_hash = (
            "sha256:"
            + sha256(
                canonical_json_bytes(
                    value.model_dump(mode="json", round_trip=True, warnings="error")
                )
            ).hexdigest()
        )
    return ArtifactReference(
        artifact_id=f"artifact_{sha256(name.encode()).hexdigest()}",
        payload_hash=payload_hash,
    )


def _nonce(value: int) -> str:
    return base64.urlsafe_b64encode(value.to_bytes(32, "big")).decode().rstrip("=")


def _public_key_b64(role: str) -> str:
    raw = (
        _KEYS[role]
        .public_key()
        .public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _key_policies(*, revoked_role: str | None = None) -> tuple[E2EGateKeyPolicy, ...]:
    kinds: tuple[tuple[str, KeyKind], ...] = (
        ("coordinator", "coordinator"),
        ("evaluator", "evaluator"),
        ("candidate", "candidate_worker"),
        ("gold", "gold_worker"),
        ("comparator", "comparator"),
    )
    return tuple(
        E2EGateKeyPolicy(
            schema_version="automarkov.e2e-key-policy.v1",
            key_id=f"key_{role}",
            principal_id=f"principal_{role}",
            principal_kind=kind,
            public_key_b64url=_public_key_b64(role),
            valid_from="2026-08-12T11:00:00Z",
            valid_until="2026-08-12T13:00:00Z",
            revoked_at=("2026-08-12T12:00:10Z" if revoked_role == role else None),
        )
        for role, kind in kinds
    )


def _protocol() -> TrustedEvaluatorProtocol:
    return TrustedEvaluatorProtocol(
        schema_version="automarkov.trusted-evaluator-protocol.v1",
        evaluator_protocol_id="sealed-e2e-v1",
        evaluator_protocol_hash="sha256:" + "8" * 64,
        evaluator_profile_id=_PROFILES["comparator"][0],
        evaluator_profile_hash=_PROFILES["comparator"][1],
        evaluator_lock_hash="sha256:" + "a" * 64,
        evaluator_image_hash="sha256:" + "b" * 64,
        evaluator_schema_id="e2e-verdict-schema-v1",
        evaluator_schema_hash="sha256:" + "c" * 64,
        candidate_worker_profile_id=_PROFILES["candidate"][0],
        candidate_worker_profile_hash=_PROFILES["candidate"][1],
        gold_worker_profile_id=_PROFILES["gold"][0],
        gold_worker_profile_hash=_PROFILES["gold"][1],
    )


def _request(
    *,
    request_id: str = "request_e2e_001",
    run_id: str = "run_e2e_001",
    nonce: int = 1,
    protocol_hash: str = "sha256:" + "8" * 64,
) -> E2EGateEvaluationRequest:
    protocol = _protocol()
    return sign_e2e_request(
        {
            "schema_version": "automarkov.e2e-gate-evaluation-request.v1",
            "signing_domain": "AutoMarkov-E2E-Gate-Evaluation-Request-v1",
            "request_id": request_id,
            "experiment_id": "experiment_main",
            "run_id": run_id,
            "run_manifest": _ref("manifest", "1"),
            "specified_event_head": {
                "run_id": run_id,
                "sequence_no": 17,
                "event_hash": "sha256:" + "f" * 64,
            },
            "candidate_validation_freeze": _ref("freeze", "2"),
            "candidate_bundle": _ref("candidate", "3"),
            "task_contract": _ref("task", "4"),
            "decision_process_spec": _ref("spec", "5"),
            "environment_binding": _ref("binding", "6"),
            "candidate_worker_profile_id": protocol.candidate_worker_profile_id,
            "candidate_worker_profile_hash": protocol.candidate_worker_profile_hash,
            "gold_worker_profile_id": protocol.gold_worker_profile_id,
            "gold_worker_profile_hash": protocol.gold_worker_profile_hash,
            "evaluator_protocol_id": protocol.evaluator_protocol_id,
            "evaluator_protocol_hash": protocol_hash,
            "evaluator_profile_id": protocol.evaluator_profile_id,
            "evaluator_profile_hash": protocol.evaluator_profile_hash,
            "evaluator_lock_hash": protocol.evaluator_lock_hash,
            "evaluator_image_hash": protocol.evaluator_image_hash,
            "evaluator_schema_id": protocol.evaluator_schema_id,
            "evaluator_schema_hash": protocol.evaluator_schema_hash,
            "issued_at": "2026-08-12T12:00:00Z",
            "not_before": "2026-08-12T11:59:00Z",
            "expires_at": "2026-08-12T12:05:00Z",
            "nonce_b64url": _nonce(nonce),
            "signature_algorithm": "Ed25519",
            "coordinator_principal_id": "principal_coordinator",
            "coordinator_key_id": "key_coordinator",
        },
        _KEYS["coordinator"],
    )


def _verdict(
    request: E2EGateEvaluationRequest,
    *,
    gates: tuple[bool, bool, bool, bool] = (True, True, True, True),
    verdict_id: str = "verdict_e2e_001",
    nonce: int = 2,
) -> E2EGateVerdict:
    return sign_e2e_verdict(
        {
            "schema_version": "automarkov.e2e-gate-verdict.v1",
            "signing_domain": "AutoMarkov-E2E-Gate-Verdict-v1",
            "verdict_id": verdict_id,
            "request_id": request.request_id,
            "request_payload_hash": e2e_payload_hash(request),
            "run_id": request.run_id,
            "run_manifest": request.run_manifest,
            "candidate_bundle": request.candidate_bundle,
            "task_contract": request.task_contract,
            "decision_process_spec": request.decision_process_spec,
            "environment_binding": request.environment_binding,
            "text_passed": gates[0],
            "formal_passed": gates[1],
            "api_passed": gates[2],
            "hidden_behavior_passed": gates[3],
            "issued_at": "2026-08-12T12:01:00Z",
            "nonce_b64url": _nonce(nonce),
            "signature_algorithm": "Ed25519",
            "evaluator_principal_id": "principal_evaluator",
            "evaluator_key_id": "key_evaluator",
        },
        _KEYS["evaluator"],
    )


def _policy_values(
    role: Literal["candidate", "gold", "comparator"],
) -> tuple[
    tuple[CandidateCapability | GoldCapability | ComparatorCapability, ...],
    tuple[DeniedCapability, ...],
    bool,
    bool,
]:
    fields = {
        "candidate": (
            ("bounded_candidate_execute",),
            (
                "arbitrary_ipc",
                "network",
                "sealed_asset",
                "sealed_credential",
                "sealed_key",
                "sealed_locator",
            ),
            False,
            True,
        ),
        "gold": (
            ("sealed_gold_execute",),
            ("candidate_code", "generation", "network", "training"),
            True,
            False,
        ),
        "comparator": (
            ("sealed_compare",),
            ("candidate_code", "generation", "network", "training"),
            True,
            False,
        ),
    }[role]
    return fields


def _runtime_isolation_evidence(
    role: Literal["candidate", "gold", "comparator"],
    job: ArtifactReference,
    *,
    candidate_sealed_mount: bool = False,
    candidate_subjects: tuple[ArtifactReference, ...] = (),
    subject_outputs: tuple[ArtifactReference, ...] = (),
) -> tuple[
    PhaseNetworkPolicy,
    MountAttestation,
    CapabilityDecisionLog,
    EgressDecisionLog,
]:
    network = PhaseNetworkPolicy(
        schema_version="automarkov.phase-network-policy.v1",
        phase="sealed_evaluation",
        egress_allowlist=(),
        protocol_edges=(),
        gateway_principal_id=None,
        deny_ip_literals=True,
        deny_redirect_egress=True,
        revoke_before_output_scan=True,
    )
    sealed_access = role != "candidate" or candidate_sealed_mount
    mounts = (
        (
            *tuple(
                ExecutionMount(
                    source_kind="input_artifact",
                    source_id=subject.artifact_id,
                    target_path=f"/mnt/automarkov/input/{kind}",
                    access="read_only",
                )
                for kind, subject in zip(
                    ("bundle", "contract", "process", "binding"),
                    candidate_subjects,
                    strict=True,
                )
            ),
            ExecutionMount(
                source_kind="output_root",
                source_id="candidate_output",
                target_path="/mnt/automarkov/output",
                access="write_only",
            ),
            *(
                (
                    ExecutionMount(
                        source_kind="sealed_asset",
                        source_id="forbidden_sealed_asset",
                        target_path="/mnt/automarkov/sealed",
                        access="read_only",
                    ),
                )
                if candidate_sealed_mount
                else ()
            ),
        )
        if role == "candidate"
        else (
            ExecutionMount(
                source_kind="output_root",
                source_id=f"{role}_output",
                target_path="/mnt/automarkov/output",
                access="write_only",
            ),
            ExecutionMount(
                source_kind="sealed_asset",
                source_id=f"{role}_sealed_asset",
                target_path="/mnt/automarkov/sealed",
                access="read_only",
            ),
            *(
                tuple(
                    ExecutionMount(
                        source_kind="input_artifact",
                        source_id=subject.artifact_id,
                        target_path=f"/mnt/automarkov/subjects/{kind}",
                        access="read_only",
                    )
                    for kind, subject in zip(
                        _COMPARATOR_SUBJECT_KINDS,
                        subject_outputs,
                        strict=True,
                    )
                )
                if role == "comparator" and subject_outputs
                else ()
            ),
        )
    )
    mount = MountAttestation(
        schema_version="automarkov.mount-attestation.v1",
        job_manifest=job,
        mount_policy=_ref(f"{role}_mount_policy", "6"),
        actual_mounts=tuple(
            sorted(mounts, key=lambda item: item.target_path.encode("utf-8"))
        ),
    )
    capability = CapabilityDecisionLog(
        schema_version="automarkov.capability-decision-log.v1",
        job_manifest=job,
        capability_policy=_ref(f"{role}_capability_policy", "7"),
        denied_capabilities=("capability:all",),
        effective_uid=65532,
        no_new_privileges=True,
        read_only_rootfs=True,
        dropped_capabilities=("ALL",),
        seccomp_profile_hash="sha256:" + "7" * 64,
        apparmor_profile_hash="sha256:" + "8" * 64,
    )
    egress = EgressDecisionLog(
        schema_version="automarkov.egress-decision-log.v1",
        job_manifest=job,
        network_policy=_payload_ref(f"{role}_network_policy", network),
        decisions=(),
        revoked_at="2026-08-12T12:00:10Z",
    )
    assert sealed_access == any(
        item.source_kind == "sealed_asset" for item in mount.actual_mounts
    )
    return network, mount, capability, egress


def _policy(
    role: Literal["candidate", "gold", "comparator"],
    runtime_evidence: tuple[
        PhaseNetworkPolicy,
        MountAttestation,
        CapabilityDecisionLog,
        EgressDecisionLog,
    ],
) -> WorkerIsolationPolicy:
    fields = _policy_values(role)
    network, mount, capability, egress = runtime_evidence
    return WorkerIsolationPolicy(
        schema_version="automarkov.worker-isolation-policy.v1",
        worker_kind=role,
        allowed_capabilities=fields[0],
        denied_capabilities=fields[1],
        linux_denied_capabilities=("capability:all",),
        network_access=False,
        sealed_access=fields[2],
        candidate_code_access=fields[3],
        network_policy_hash=e2e_payload_hash(network),
        mount_table_hash=e2e_payload_hash(mount),
        capability_decision_log_hash=e2e_payload_hash(capability),
        egress_decision_log_hash=e2e_payload_hash(egress),
    )


def _outputs(
    role: Literal["candidate", "gold", "comparator"],
    verdict_ref: ArtifactReference,
    request: E2EGateEvaluationRequest | None = None,
) -> tuple[BoundedWorkerOutput, ...]:
    kinds = {
        "candidate": (
            "candidate_api",
            "candidate_behavior",
            "candidate_formal",
            "candidate_text",
        ),
        "gold": ("gold_api", "gold_behavior", "gold_formal", "gold_text"),
        "comparator": ("e2e_verdict",),
    }[role]
    return tuple(
        BoundedWorkerOutput(
            output_kind=kind,
            output_ref=(
                verdict_ref if role == "comparator"
                else ArtifactReference(
                    artifact_id=f"artifact_{sha256(kind.encode()).hexdigest()}",
                    # 真实 runner 中 candidate/gold 输出是新生成的 artifact，
                    # payload hash 永远不会等于输入 spec 的 hash。
                    # 这里使用独立 hash 以模拟真实场景。
                    payload_hash=(
                        "sha256:"
                        + sha256(f"output_{role}_{kind}".encode()).hexdigest()
                    ),
                )
            ),
            byte_length=128 + index,
        )
        for index, kind in enumerate(kinds)
    )


def _persisted_runner_output_refs(
    runner_outputs: tuple[RunnerOutputBinding, ...],
) -> tuple[ArtifactReference, ...]:
    return tuple(
        ArtifactReference(
            artifact_id=result.artifact_id.root,
            payload_hash=result.payload_hash.root,
        )
        for output in runner_outputs
        for result in (
            _RUNNER_OUTPUT_REPOSITORY.put(
                {
                    "schema_version": "automarkov.artifact-put-request.v2",
                    "artifact_type": "runner_output_binding",
                    "payload_bytes": canonical_json_bytes(
                        output.model_dump(mode="json")
                    ),
                    "parent_artifact_ids": [],
                    "created_by": "principal_e2e_fixture",
                    "created_at": "2026-08-12T12:00:35Z",
                    "source_evidence_ids": [],
                }
            ),
        )
    )


def _attestation(
    *,
    role: Literal["candidate", "gold", "comparator"],
    request: E2EGateEvaluationRequest,
    job_manifest: ArtifactReference,
    policy: WorkerIsolationPolicy,
    runner_output_refs: tuple[ArtifactReference, ...],
    issued_at: str = "2026-08-12T12:00:40Z",
    self_signed: bool = False,
) -> ExecutionAttestation:
    fields: dict[str, object] = {
        "schema_version": "automarkov.execution-attestation.v1",
        "signing_domain": "AutoMarkov-Execution-Attestation-v1",
        "experiment_id": request.experiment_id,
        "run_id": request.run_id,
        "job_id": f"job_{role}_001",
        "process_execution_id": f"process_{role}_001",
        "profile_id": _PROFILES[role][0],
        "principal_id": f"principal_{role}",
        "job_manifest": job_manifest,
        "process_terminal_record": _ref(f"{role}_terminal", "d"),
        "payload_outputs": tuple(
            sorted(
                runner_output_refs,
                key=lambda item: item.artifact_id.encode("utf-8"),
            )
        ),
        "terminal_result": None,
        "network_policy_hash": policy.network_policy_hash,
        "mount_table_hash": policy.mount_table_hash,
        "capability_decision_log_hash": policy.capability_decision_log_hash,
        "actual_phase_transition": {
            "from_phase": f"{role}_worker_started",
            "to_phase": f"{role}_outputs_committed",
            "transitioned_at": "2026-08-12T12:00:20Z",
        },
        "egress_decision_log_hash": policy.egress_decision_log_hash,
        "egress_revoked_at": "2026-08-12T12:00:30Z",
        "issued_at": issued_at,
        "nonce_b64url": base64.urlsafe_b64encode(
            bytes({"candidate": [1], "gold": [2], "comparator": [3]}[role]) * 16
        )
        .decode()
        .rstrip("="),
        "signing_key_id": f"key_{role}" if self_signed else f"key_runner_{role}",
        "signature_algorithm": "Ed25519",
        "signature_b64url": "A" * 86,
    }
    provisional = ExecutionAttestation.model_validate(fields, strict=True)
    fields["signature_b64url"] = (
        base64.urlsafe_b64encode(
            (_KEYS[role] if self_signed else _RUNNER_KEYS[role]).sign(
                execution_attestation_signing_bytes(provisional)
            )
        )
        .decode()
        .rstrip("=")
    )
    return ExecutionAttestation.model_validate(fields, strict=True)


def _binding(
    *,
    role: Literal["candidate", "gold", "comparator"],
    request_ref: ArtifactReference,
    request: E2EGateEvaluationRequest,
    verdict_ref: ArtifactReference,
    attestation_issued_at: str = "2026-08-12T12:00:40Z",
    evidence_issued_at: str = "2026-08-12T12:00:50Z",
    attestation_self_signed: bool = False,
    candidate_sealed_mount: bool = False,
    gold_candidate_mount: bool = False,
    subject_outputs: tuple[ArtifactReference, ...] = (),
    consume_subject_outputs: bool = True,
    use_inner_runner_output_refs: bool = False,
) -> SealedWorkerBinding:
    job = _ref(f"{role}_job", {"candidate": "d", "gold": "e", "comparator": "f"}[role])
    runtime_evidence = _runtime_isolation_evidence(
        role,
        job,
        candidate_sealed_mount=candidate_sealed_mount,
        candidate_subjects=(
            request.candidate_bundle,
            request.task_contract,
            request.decision_process_spec,
            request.environment_binding,
        ),
        subject_outputs=subject_outputs if consume_subject_outputs else (),
    )
    if role == "gold" and gold_candidate_mount:
        network, mount, capability, egress = runtime_evidence
        mount = mount.model_copy(
            update={
                "actual_mounts": tuple(
                    sorted(
                        (
                            *mount.actual_mounts,
                            ExecutionMount(
                                source_kind="input_artifact",
                                source_id=request.candidate_bundle.artifact_id,
                                target_path="/mnt/automarkov/candidate",
                                access="read_only",
                            ),
                        ),
                        key=lambda item: item.target_path.encode("utf-8"),
                    )
                )
            }
        )
        runtime_evidence = (network, mount, capability, egress)
    policy = _policy(role, runtime_evidence)
    outputs = _outputs(role, verdict_ref, request=request)
    runner_outputs = tuple(
        RunnerOutputBinding(
            schema_version="automarkov.runner-output-binding.v2",
            path=f"{output.output_kind}.json",
            byte_size=len(content),
            media_type="application/json",
            content_hash="sha256:" + sha256(content).hexdigest(),
            content_schema_version=("automarkov.runner-artifact-reference-output.v1"),
            content_b64url=base64.urlsafe_b64encode(content).decode().rstrip("="),
            schema_valid=True,
        )
        for output in outputs
        for content in (
            canonical_json_bytes(
                RunnerArtifactReferencePayload(
                    schema_version=("automarkov.runner-artifact-reference-output.v1"),
                    artifact_type=(
                        "e2e_gate_verdict"
                        if output.output_kind == "e2e_verdict"
                        else output.output_kind
                    ),
                    artifact=output.output_ref,
                ).model_dump(mode="json")
            ),
        )
    )
    runner_output_refs = (
        tuple(
            runner_artifact_reference("runner_output_binding", output)
            for output in runner_outputs
        )
        if use_inner_runner_output_refs
        else _persisted_runner_output_refs(runner_outputs)
    )
    attestation = _attestation(
        role=role,
        request=request,
        job_manifest=job,
        policy=policy,
        runner_output_refs=runner_output_refs,
        issued_at=attestation_issued_at,
        self_signed=attestation_self_signed,
    )
    evidence_fields: dict[str, object] = {
        "schema_version": "automarkov.signed-worker-evidence.v1",
        "signing_domain": "AutoMarkov-Signed-Worker-Evidence-v1",
        "worker_id": f"worker_{role}_001",
        "worker_kind": role,
        "principal_id": f"principal_{role}",
        "profile_id": _PROFILES[role][0],
        "profile_hash": _PROFILES[role][1],
        "request_ref": request_ref,
        "candidate_bundle": request.candidate_bundle,
        "task_contract": request.task_contract,
        "decision_process_spec": request.decision_process_spec,
        "environment_binding": request.environment_binding,
        "job_manifest": job,
        "process_execution_id": f"process_{role}_001",
        "execution_attestation_ref": _payload_ref(f"{role}_attestation", attestation),
        "isolation_policy_hash": e2e_payload_hash(policy),
        "subject_outputs": subject_outputs,
        "outputs": outputs,
        "issued_at": evidence_issued_at,
        "nonce_b64url": _nonce({"candidate": 10, "gold": 11, "comparator": 12}[role]),
        "signature_algorithm": "Ed25519",
        "worker_key_id": f"key_{role}",
    }
    evidence = sign_worker_evidence(
        evidence_fields,
        _KEYS[role],
    )
    fields = {
        "worker_id": evidence.worker_id,
        "principal_id": evidence.principal_id,
        "profile_id": evidence.profile_id,
        "profile_hash": evidence.profile_hash,
        "process_execution_id": evidence.process_execution_id,
        "job_manifest": job,
        "execution_attestation": attestation,
        "runner_output_refs": runner_output_refs,
        "runner_outputs": runner_outputs,
        "isolation_policy": policy,
        "network_policy": runtime_evidence[0],
        "mount_attestation": runtime_evidence[1],
        "capability_decision_log": runtime_evidence[2],
        "egress_decision_log": runtime_evidence[3],
        "evidence": evidence,
    }
    if candidate_sealed_mount or not consume_subject_outputs:
        return SealedWorkerBinding.model_construct(**fields)
    return SealedWorkerBinding(
        **fields,
    )


def test_worker_binding_accepts_repository_persisted_runner_output_refs() -> None:
    request = _request()
    request_ref = _payload_ref("request", request)
    verdict = _verdict(request)
    binding = _binding(
        role="candidate",
        request_ref=request_ref,
        request=request,
        verdict_ref=_payload_ref("verdict_e2e_001", verdict),
    )

    assert binding.execution_attestation.payload_outputs == tuple(
        sorted(
            binding.runner_output_refs,
            key=lambda item: item.artifact_id.encode("utf-8"),
        )
    )
    assert all(
        persisted != runner_artifact_reference("runner_output_binding", wrapper)
        for persisted, wrapper in zip(
            binding.runner_output_refs,
            binding.runner_outputs,
            strict=True,
        )
    )


def test_worker_binding_rejects_attested_inner_model_output_refs() -> None:
    request = _request()
    request_ref = _payload_ref("request", request)
    verdict = _verdict(request)

    with pytest.raises(ValueError, match="runner output wrapper"):
        _binding(
            role="candidate",
            request_ref=request_ref,
            request=request,
            verdict_ref=_payload_ref("verdict_e2e_001", verdict),
            use_inner_runner_output_refs=True,
        )


def test_comparator_signed_evidence_binds_and_mounts_all_subject_outputs() -> None:
    request = _request()
    request_ref = _payload_ref("request", request)
    verdict = _verdict(request)
    verdict_ref = _payload_ref("verdict_e2e_001", verdict)
    subject_outputs = tuple(
        _ref(kind, str(index + 1))
        for index, kind in enumerate(_COMPARATOR_SUBJECT_KINDS)
    )

    binding = _binding(
        role="comparator",
        request_ref=request_ref,
        request=request,
        verdict_ref=verdict_ref,
        subject_outputs=subject_outputs,
    )

    assert binding.evidence.subject_outputs == subject_outputs
    mounted_subjects = tuple(
        (mount.target_path, mount.source_id)
        for mount in binding.mount_attestation.actual_mounts
        if mount.source_kind == "input_artifact"
    )
    assert mounted_subjects == tuple(
        (f"/mnt/automarkov/subjects/{kind}", subject.artifact_id)
        for kind, subject in zip(
            _COMPARATOR_SUBJECT_KINDS,
            subject_outputs,
            strict=True,
        )
    )


def _case(
    *,
    gates: tuple[bool, bool, bool, bool] = (True, True, True, True),
    request: E2EGateEvaluationRequest | None = None,
    request_ref: ArtifactReference | None = None,
    verdict: E2EGateVerdict | None = None,
    verdict_ref: ArtifactReference | None = None,
    verdict_id: str = "verdict_e2e_001",
    verdict_nonce: int = 2,
    candidate_attestation_issued_at: str = "2026-08-12T12:00:40Z",
    candidate_attestation_self_signed: bool = False,
    comparator_attestation_issued_at: str = "2026-08-12T12:01:10Z",
    comparator_evidence_issued_at: str = "2026-08-12T12:01:20Z",
    candidate_sealed_mount: bool = False,
    gold_candidate_mount: bool = False,
    comparator_consumes_subject_outputs: bool = True,
    substitute_comparator_subject_output: Literal["candidate", "gold"] | None = None,
) -> Case:
    request = request or _request()
    request_ref = request_ref or _payload_ref("request", request)
    verdict = verdict or _verdict(
        request, gates=gates, verdict_id=verdict_id, nonce=verdict_nonce
    )
    verdict_ref = verdict_ref or _payload_ref(verdict_id, verdict)
    candidate = _binding(
        role="candidate",
        request_ref=request_ref,
        request=request,
        verdict_ref=verdict_ref,
        attestation_issued_at=candidate_attestation_issued_at,
        attestation_self_signed=candidate_attestation_self_signed,
        candidate_sealed_mount=candidate_sealed_mount,
    )
    gold = _binding(
        role="gold",
        request_ref=request_ref,
        request=request,
        verdict_ref=verdict_ref,
        gold_candidate_mount=gold_candidate_mount,
    )
    subject_outputs = tuple(
        output.output_ref
        for binding in (candidate, gold)
        for output in binding.evidence.outputs
    )
    if substitute_comparator_subject_output:
        substituted_index = (
            0 if substitute_comparator_subject_output == "candidate" else 4
        )
        substituted = _ref(
            f"substituted_{substitute_comparator_subject_output}_api", "0"
        )
        subject_outputs = tuple(
            substituted if index == substituted_index else subject
            for index, subject in enumerate(subject_outputs)
        )
    topology_fields = {
        "schema_version": "automarkov.sealed-worker-topology.v1",
        "candidate": candidate,
        "gold": gold,
        "comparator": _binding(
            role="comparator",
            request_ref=request_ref,
            request=request,
            verdict_ref=verdict_ref,
            attestation_issued_at=comparator_attestation_issued_at,
            evidence_issued_at=comparator_evidence_issued_at,
            subject_outputs=subject_outputs,
            consume_subject_outputs=comparator_consumes_subject_outputs,
        ),
    }
    topology = (
        SealedWorkerTopology.model_construct(**topology_fields)
        if candidate_sealed_mount
        or gold_candidate_mount
        or not comparator_consumes_subject_outputs
        or substitute_comparator_subject_output
        else SealedWorkerTopology(**topology_fields)
    )
    return request_ref, request, verdict_ref, verdict, topology


def test_comparator_verdict_precedes_runner_attestation_and_worker_evidence() -> None:
    case = _case(
        comparator_attestation_issued_at="2026-08-12T12:01:10Z",
        comparator_evidence_issued_at="2026-08-12T12:01:20Z",
    )

    assert (
        _evaluate(_gate(InMemoryE2EGateCommitter()), case).next_state
        == "TRAINING_SMOKE_TESTING"
    )


def test_comparator_cannot_claim_subject_outputs_without_mounting_them() -> None:
    result = _evaluate(
        _gate(InMemoryE2EGateCommitter()),
        _case(comparator_consumes_subject_outputs=False),
    )

    assert result.next_state == "FAILED"
    assert result.terminal_reason_code == "sealed_e2e_integrity_failed"


@pytest.mark.parametrize("side", ("candidate", "gold"))
def test_comparator_cannot_replace_candidate_or_gold_subject_output(
    side: Literal["candidate", "gold"],
) -> None:
    result = _evaluate(
        _gate(InMemoryE2EGateCommitter()),
        _case(substitute_comparator_subject_output=side),
    )

    assert result.next_state == "FAILED"
    assert result.terminal_reason_code == "sealed_e2e_integrity_failed"


class _KeyPolicyResolver:
    def __init__(self, policies: tuple[E2EGateKeyPolicy, ...]) -> None:
        self._policies = {policy.key_id: policy for policy in policies}

    def resolve(
        self,
        *,
        run_id: str,
        specified_event_head: VerifiedEventHead,
        run_manifest: ArtifactReference,
        key_id: str,
        principal_id: str,
        principal_kind: KeyKind,
    ) -> E2EGateKeyPolicy:
        assert run_id == specified_event_head.run_id.root
        assert run_manifest == _ref("manifest", "1")
        policy = self._policies[key_id]
        assert policy.principal_id == principal_id
        assert policy.principal_kind == principal_kind
        return policy


def _gate(
    committer: E2EGateCommitter,
    *,
    revoked_role: str | None = None,
    protocol: TrustedEvaluatorProtocol | None = None,
    key_policies: tuple[E2EGateKeyPolicy, ...] | None = None,
    runner_grant_resolver: _RunnerGrantResolver | None = None,
) -> SealedE2EGate:
    return SealedE2EGate(
        key_policy_resolver=_KeyPolicyResolver(
            key_policies or _key_policies(revoked_role=revoked_role)
        ),
        protocol_resolver=FrozenE2EProtocolRegistry((protocol or _protocol(),)),
        runner_grant_resolver=runner_grant_resolver or _RunnerGrantResolver(),
        committer=committer,
        clock=lambda: "2026-08-12T12:01:30Z",
        maximum_clock_skew_ms=1_000,
    )


class _RunnerGrantResolver:
    def __init__(
        self,
        *,
        candidate_not_before: str = "2026-08-12T11:00:00Z",
        substitute_candidate_key: bool = False,
        candidate_inputs_bound: bool = True,
        candidate_profile_hash: str | None = None,
        task_contract_hash: str = "sha256:" + "4" * 64,
        decision_process_spec_hash: str = "sha256:" + "5" * 64,
    ) -> None:
        self._candidate_not_before = candidate_not_before
        self._substitute_candidate_key = substitute_candidate_key
        self._candidate_inputs_bound = candidate_inputs_bound
        self._candidate_profile_hash = candidate_profile_hash
        self._task_contract_hash = task_contract_hash
        self._decision_process_spec_hash = decision_process_spec_hash

    def resolve(
        self,
        *,
        run_id: str,
        specified_event_head: VerifiedEventHead,
        run_manifest: ArtifactReference,
        job_manifest: ArtifactReference,
        principal_id: str,
    ) -> ResolvedSealedWorkerExecution:
        assert run_manifest == _ref("manifest", "1")
        assert run_id == specified_event_head.run_id.root
        role = principal_id.removeprefix("principal_")
        assert job_manifest == _ref(
            f"{role}_job",
            {"candidate": "d", "gold": "e", "comparator": "f"}[role],
        )
        runner_key = (
            _KEYS["candidate"]
            if role == "candidate" and self._substitute_candidate_key
            else _RUNNER_KEYS[role]
        )
        public_key = runner_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        grant = ManifestEventSigningKey(
            signing_key_id=f"key_runner_{role}",
            principal_id=principal_id,
            signature_algorithm="Ed25519",
            public_key_b64url=base64.urlsafe_b64encode(public_key).decode().rstrip("="),
            not_before=(
                self._candidate_not_before
                if role == "candidate"
                else "2026-08-12T11:00:00Z"
            ),
            not_after="2026-08-12T13:00:00Z",
            revoked_at=None,
        )
        expected_candidate_inputs = tuple(
            sorted(
                (
                    _ref("candidate", "3"),
                    _ref("task", "4"),
                    _ref("spec", "5"),
                    _ref("binding", "6"),
                ),
                key=lambda item: item.artifact_id.encode("utf-8"),
            )
        )
        return ResolvedSealedWorkerExecution(
            runner_key_grant=grant,
            job_manifest=job_manifest,
            principal_id=principal_id,
            profile_id=_PROFILES[role][0],
            profile_hash=(
                self._candidate_profile_hash
                if role == "candidate" and self._candidate_profile_hash is not None
                else _PROFILES[role][1]
            ),
            image_digest=(
                _protocol().evaluator_image_hash
                if role == "comparator"
                else "sha256:" + {"candidate": "a", "gold": "c"}[role] * 64
            ),
            input_sources=(
                expected_candidate_inputs
                if role == "candidate" and self._candidate_inputs_bound
                else (
                    tuple(
                        sorted(
                            (
                                ArtifactReference(
                                    artifact_id=f"artifact_{sha256(kind.encode()).hexdigest()}",
                                    payload_hash=(
                                        "sha256:"
                                        + sha256(
                                            f"output_{kind.partition('_')[0]}_{kind}".encode()
                                        ).hexdigest()
                                    ),
                                )
                                for index, kind in enumerate(
                                    _COMPARATOR_SUBJECT_KINDS
                                )
                            ),
                            key=lambda item: item.artifact_id.encode("utf-8"),
                        )
                    )
                    if role == "comparator"
                    else ()
                )
            ),
        )


def _evaluate(gate: SealedE2EGate, case: Case):
    request_ref, request, verdict_ref, verdict, topology = case
    return gate.evaluate(
        request_ref=request_ref,
        request=request,
        verdict_ref=verdict_ref,
        verdict=verdict,
        topology=topology,
        topology_ref=_payload_ref("sealed_topology", topology),
        execution=E2EGateExecutionCommitInput(
            schema_version="automarkov.e2e-execution-commit-input.v1",
            runner_fingerprint="sha256:" + "7" * 64,
            process_execution_terminal_record=_ref("gate_process", "8"),
        ),
    )


def test_signed_e2e_success_is_atomically_materialized_and_exact_retry_is_idempotent() -> (
    None
):
    gate = _gate(InMemoryE2EGateCommitter())
    case = _case()

    first = _evaluate(gate, case)
    retried = _evaluate(gate, case)

    assert first == retried
    assert first.next_state == "TRAINING_SMOKE_TESTING"
    assert first.outcome_e2e_valid == 1
    assert first.request_ref == case[0]
    assert first.verdict_ref == case[2]
    assert first.retry_permitted is False


def test_each_false_gate_materializes_partial_zero_and_missingness() -> None:
    for index in range(4):
        gates = [True, True, True, True]
        gates[index] = False
        request = _request(
            request_id=f"request_e2e_00{index + 2}",
            run_id=f"run_e2e_00{index + 2}",
            nonce=index + 20,
        )
        result = _evaluate(
            _gate(InMemoryE2EGateCommitter()),
            _case(
                request=request,
                gates=(gates[0], gates[1], gates[2], gates[3]),
                verdict_id=f"verdict_e2e_00{index + 2}",
                verdict_nonce=index + 30,
            ),
        )

        assert result.next_state == "PARTIAL"
        assert result.terminal_reason_code == "sealed_e2e_gate_failed"
        assert result.outcome_e2e_valid == 0
        assert result.training_outcome_missing is True


def test_forged_request_does_not_claim_candidate_slot() -> None:
    committer = InMemoryE2EGateCommitter()
    gate = _gate(committer)
    valid = _case()
    forged = valid[1].model_copy(
        update={"candidate_worker_profile_hash": "sha256:" + "0" * 64}
    )

    failed = gate.evaluate(
        request_ref=_payload_ref("forged_request", forged),
        request=forged,
        verdict_ref=valid[2],
        verdict=valid[3],
        topology=valid[4],
        topology_ref=_payload_ref("sealed_topology", valid[4]),
        execution=E2EGateExecutionCommitInput(
            schema_version="automarkov.e2e-execution-commit-input.v1",
            runner_fingerprint="sha256:" + "7" * 64,
            process_execution_terminal_record=_ref("gate_process", "8"),
        ),
    )

    assert failed.next_state == "FAILED"
    assert failed.terminal_reason_code == "sealed_e2e_integrity_failed"
    assert failed.outcome_e2e_valid == 0
    assert failed.training_outcome_missing is True
    assert _evaluate(gate, valid).next_state == "FAILED"


def test_payload_reference_mismatch_is_durably_materialized_as_failed() -> None:
    committer = InMemoryE2EGateCommitter()
    gate = _gate(committer)
    case = _case()
    tampered_request = case[1].model_copy(
        update={"candidate_worker_profile_hash": "sha256:" + "0" * 64}
    )

    failed = gate.evaluate(
        request_ref=case[0],
        request=tampered_request,
        verdict_ref=case[2],
        verdict=case[3],
        topology=case[4],
        topology_ref=_payload_ref("sealed_topology", case[4]),
        execution=E2EGateExecutionCommitInput(
            schema_version="automarkov.e2e-execution-commit-input.v1",
            runner_fingerprint="sha256:" + "7" * 64,
            process_execution_terminal_record=_ref("gate_process", "8"),
        ),
    )

    assert failed.next_state == "FAILED"
    assert failed.terminal_reason_code == "sealed_e2e_integrity_failed"
    assert _evaluate(gate, case).next_state == "FAILED"


def test_candidate_runner_evidence_cannot_hide_a_sealed_mount() -> None:
    case = _case(candidate_sealed_mount=True)

    failed = _evaluate(_gate(InMemoryE2EGateCommitter()), case)

    assert failed.next_state == "FAILED"
    assert failed.terminal_reason_code == "sealed_e2e_integrity_failed"


def test_candidate_claims_must_match_runner_resolved_job_inputs() -> None:
    failed = _evaluate(
        _gate(
            InMemoryE2EGateCommitter(),
            runner_grant_resolver=_RunnerGrantResolver(candidate_inputs_bound=False),
        ),
        _case(),
    )

    assert failed.next_state == "FAILED"
    assert failed.terminal_reason_code == "sealed_e2e_integrity_failed"


def test_gold_worker_cannot_mount_candidate_code_with_sealed_assets() -> None:
    failed = _evaluate(
        _gate(InMemoryE2EGateCommitter()),
        _case(gold_candidate_mount=True),
    )

    assert failed.next_state == "FAILED"
    assert failed.terminal_reason_code == "sealed_e2e_integrity_failed"


def test_worker_profile_claim_must_match_runner_resolved_profile() -> None:
    failed = _evaluate(
        _gate(
            InMemoryE2EGateCommitter(),
            runner_grant_resolver=_RunnerGrantResolver(
                candidate_profile_hash="sha256:" + "0" * 64
            ),
        ),
        _case(),
    )

    assert failed.next_state == "FAILED"
    assert failed.terminal_reason_code == "sealed_e2e_integrity_failed"


def test_nested_model_copy_cannot_bypass_worker_policy_validation_or_claim_slot() -> (
    None
):
    committer = InMemoryE2EGateCommitter()
    gate = _gate(committer)
    case = _case()
    topology = case[4]
    tampered_policy = topology.candidate.isolation_policy.model_copy(
        update={"network_access": True}
    )
    tampered_candidate = topology.candidate.model_copy(
        update={"isolation_policy": tampered_policy}
    )
    tampered_topology = topology.model_copy(update={"candidate": tampered_candidate})

    failed = gate.evaluate(
        request_ref=case[0],
        request=case[1],
        verdict_ref=case[2],
        verdict=case[3],
        topology=tampered_topology,
        topology_ref=_payload_ref("sealed_topology", tampered_topology),
        execution=E2EGateExecutionCommitInput(
            schema_version="automarkov.e2e-execution-commit-input.v1",
            runner_fingerprint="sha256:" + "7" * 64,
            process_execution_terminal_record=_ref("gate_process", "8"),
        ),
    )

    assert failed.next_state == "FAILED"
    assert _evaluate(gate, case).next_state == "FAILED"


def test_sqlite_replay_claims_survive_restart_and_exact_retry_returns_prior_bytes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "e2e.sqlite"
    case = _case(gates=(False, True, True, True))
    first_committer = SqliteE2EGateCommitter(path)
    first = _evaluate(_gate(first_committer), case)
    first_committer.close()

    restarted = SqliteE2EGateCommitter(path)
    assert _evaluate(_gate(restarted), case) == first
    changed_case = _case(
        gates=(True, True, True, True),
        request=case[1],
        verdict_id="verdict_e2e_changed",
        verdict_nonce=99,
    )
    replay_failed = _evaluate(_gate(restarted), changed_case)
    assert replay_failed.next_state == "FAILED"
    restarted.close()
    restarted_again = SqliteE2EGateCommitter(path)
    assert _evaluate(_gate(restarted_again), changed_case) == replay_failed
    restarted_again.close()


@pytest.mark.parametrize("failure", ("revoked", "registry"))
def test_key_status_and_trusted_protocol_evidence_fail_before_claim(
    failure: str,
) -> None:
    committer = InMemoryE2EGateCommitter()
    case = _case()
    if failure == "revoked":
        invalid_gate = _gate(committer, revoked_role="coordinator")
    else:
        drifted = _protocol().model_copy(
            update={"evaluator_protocol_hash": "sha256:" + "0" * 64}
        )
        invalid_gate = _gate(committer, protocol=drifted)

    failed = _evaluate(invalid_gate, case)

    assert failed.next_state == "FAILED"
    assert _evaluate(_gate(committer), case).next_state == "FAILED"


def test_signing_key_valid_until_is_an_exclusive_boundary() -> None:
    policies = tuple(
        policy.model_copy(update={"valid_until": "2026-08-12T12:00:00Z"})
        if policy.key_id == "key_coordinator"
        else policy
        for policy in _key_policies()
    )
    assert (
        _evaluate(
            _gate(InMemoryE2EGateCommitter(), key_policies=policies),
            _case(),
        ).next_state
        == "FAILED"
    )


def test_key_policy_resolver_cannot_alias_the_requested_key_id() -> None:
    class AliasedKeyPolicyResolver(_KeyPolicyResolver):
        def resolve(
            self,
            *,
            run_id: str,
            specified_event_head: VerifiedEventHead,
            run_manifest: ArtifactReference,
            key_id: str,
            principal_id: str,
            principal_kind: KeyKind,
        ) -> E2EGateKeyPolicy:
            return (
                super()
                .resolve(
                    run_id=run_id,
                    specified_event_head=specified_event_head,
                    run_manifest=run_manifest,
                    key_id=key_id,
                    principal_id=principal_id,
                    principal_kind=principal_kind,
                )
                .model_copy(update={"key_id": "key_unrequested_alias"})
            )

    gate = SealedE2EGate(
        key_policy_resolver=AliasedKeyPolicyResolver(_key_policies()),
        protocol_resolver=FrozenE2EProtocolRegistry((_protocol(),)),
        runner_grant_resolver=_RunnerGrantResolver(),
        committer=InMemoryE2EGateCommitter(),
        clock=lambda: "2026-08-12T12:01:30Z",
        maximum_clock_skew_ms=1_000,
    )

    assert _evaluate(gate, _case()).next_state == "FAILED"


def test_worker_key_interval_and_attestation_evidence_order_fail_closed() -> None:
    ordering_case = _case(candidate_attestation_issued_at="2026-08-12T12:00:55Z")
    assert (
        _evaluate(_gate(InMemoryE2EGateCommitter()), ordering_case).next_state
        == "FAILED"
    )
    self_signed_case = _case(candidate_attestation_self_signed=True)
    assert (
        _evaluate(_gate(InMemoryE2EGateCommitter()), self_signed_case).next_state
        == "FAILED"
    )
    substitution_case = _case()
    assert (
        _evaluate(
            _gate(
                InMemoryE2EGateCommitter(),
                runner_grant_resolver=_RunnerGrantResolver(
                    substitute_candidate_key=True
                ),
            ),
            substitution_case,
        ).next_state
        == "FAILED"
    )

    interval_case = _case()
    assert (
        _evaluate(
            _gate(
                InMemoryE2EGateCommitter(),
                runner_grant_resolver=_RunnerGrantResolver(
                    candidate_not_before="2026-08-12T12:00:45Z"
                ),
            ),
            interval_case,
        ).next_state
        == "FAILED"
    )


class _FailingCommitter:
    def commit(self, command: object) -> object:
        raise E2EGateCommitError("injected atomic storage failure")


def test_commit_failure_never_returns_an_unmaterialized_decision() -> None:
    gate = _gate(_FailingCommitter())  # type: ignore[arg-type]

    with pytest.raises(E2EGateCommitError):
        _evaluate(gate, _case())
