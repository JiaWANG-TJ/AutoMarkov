from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import runpy
import sqlite3
import subprocess
import sys
import tarfile
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier
from typing import Any, Literal, cast
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, ValidationError, model_validator

from automarkov.adapters import (
    InMemoryArtifactRepository,
    SqliteArtifactRepository,
)
from automarkov.contracts.classification import ClassificationResult
from automarkov.contracts.task import (
    FixedCommitRunAuthorization,
    RunManifest,
    TaskContract,
    TaskContractTraceabilityReport,
    TextCriticReport,
    task_contract_claim_paths,
)
from automarkov.domain.canonical import (
    CanonicalPayloadCodec,
    FrozenSequence,
    SafeCanonicalInt,
    canonical_json_bytes,
)
from automarkov.domain.errors import (
    ArtifactIntegrityError,
    ArtifactParentContractError,
    ArtifactWriteAuthorityError,
    AutoMarkovError,
    CommandAuthenticationError,
    EventHeadConflictError,
    EventSchemaError,
    RunProjectorIdentityError,
    TerminalProvenanceError,
    UnknownArtifactError,
)
from automarkov.domain.models import (
    ArtifactId,
    RunId,
    RunState,
    Sha256Digest,
    StrictFrozenModel,
    VerifiedEventHead,
)
from automarkov.fixed_commit_runner import (
    ArtifactRepositoryRunnerCheckpointFinalizer,
    ArtifactRepositoryRunnerStore,
    ArtifactRepositoryTerminalCommitter,
    ArtifactRepositoryTrustedRunnerArtifactResolver,
    FixedCommitJobManifest,
    RawExecutionEvidence,
    RunnerArtifactReferencePayload,
    RunnerExecutionCheckpoint,
    RunnerInput,
    RunnerOutputBinding,
    RunnerReplayError,
    RunnerRuntimeAttestation,
    RunnerRuntimeEvidence,
    RuntimeProfileArtifactPayload,
    _sign_runtime_attestation,
    execution_attestation_signing_bytes,
    runner_artifact_reference,
)
from automarkov.lifecycle import (
    RUN_PROJECTOR_HASH,
    RUN_PROJECTOR_VERSION,
    ZERO_EVENT_HASH,
    ArtifactReference,
    BudgetSnapshot,
    EventAuthenticator,
    EventSigningKey,
    ExecutionAttestation,
    ExecutionPhaseTransition,
    LifecycleCommitReceipt,
    ManifestEventSigningKey,
    ProcessExecutionTerminalRecord,
    RunAuditProjection,
    RunCreated,
    TerminalResult,
    encode_event_record,
    parse_event_record,
    validate_lifecycle_command,
    validate_projection_request,
)
from automarkov.public import (
    AuthenticatedCommandContext,
    CommandAuthority,
    CommandPrincipalBinding,
)
from automarkov.repository import ArtifactSchemaRegistry, ParentBinding
from automarkov.sealed_evaluation import (
    ArtifactLifecycleAtomicReceipt,
    ArtifactLifecycleE2EGateCommitter,
    ArtifactRepositoryE2EGateLifecycleMaterializer,
    ArtifactRepositoryE2EGateLifecyclePlan,
    ArtifactRepositoryE2EKeyPolicyResolver,
    ArtifactRepositoryE2ERunnerGrantResolver,
    E2EGateCommitCommand,
    E2EGateEvaluationRequest,
    E2EGateLifecyclePlanConfig,
    E2EGateVerdict,
    SealedE2EGate,
    SealedWorkerTopology,
    _command_claims,
    _command_fingerprint,
    sign_e2e_request,
    sign_e2e_verdict,
)
from automarkov.security.provenance import RuntimeProfileManifest

_ISSUED_AT = "2026-08-10T11:00:00Z"
_STARTED_AT = "2026-08-10T10:59:00Z"
_RUN_ID = "run_repository_lifecycle"
_HASH_A = "sha256:" + "a" * 64
_HASH_B = "sha256:" + "b" * 64
_SQLITE_V8_SOURCE_COMMIT = "179603de28e60f1ee2b1b7997d097865d425caac"
_V8_REPOSITORY_CREATION_SCRIPT = """
import json
import runpy
import sys

from pathlib import Path

fixture = runpy.run_path(str(Path("tests/contract/test_run_lifecycle.py")))
repository = fixture["SqliteArtifactRepository"](
    sys.argv[1],
    fixture["_registry"](
        terminal_provenance=len(sys.argv) == 3 and sys.argv[2] == "terminal"
    ),
    fixture["_EVENT_AUTHENTICATOR"],
    fixture["_COMMAND_AUTHORITY"],
)
artifacts = fixture["_put_run_artifacts"](repository)
event = fixture["_root_event"](artifacts)
command = {
    "schema_version": "automarkov.lifecycle-command.v1",
    "command_type": "append_run_events",
    "command_id": fixture["_uuid7"](10_000),
    "actor_principal_id": "principal_lifecycle_fixture",
    "issued_at": fixture["_ISSUED_AT"],
    "idempotency_key": "lifecycle-command-0",
    "run_id": fixture["_RUN_ID"],
    "expected_state": None,
    "expected_head": None,
    "events": [event],
}
receipt = fixture["_commit"](repository, command)
if len(sys.argv) == 3 and sys.argv[2] == "terminal":
    researching = fixture["_advance_to_researching"](
        repository, artifacts, receipt
    )
    receipt = fixture["_commit"](
        repository,
        fixture["_terminal_command"](artifacts, researching),
    )
repository.close()
result = {
            "artifact_id": artifacts["run_manifest"].artifact_id.root,
            "payload_hash": artifacts["run_manifest"].payload_hash.root,
            "command": command,
            "receipt": receipt.model_dump(mode="json"),
}
print(json.dumps(result))
"""
_VALIDATION_FAILED_V8_SCHEMA_ID = (
    "sha256:7d4562dd80b5ee9e0204850ef7e09eff7c659430edd6f43f2e7e82db52259728"
)
_PAYLOAD_MEDIA_TYPE = "application/vnd.automarkov.canonical-payload+json"
_SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(b"\x17" * 32)
_RUNNER_SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(b"\x18" * 32)
_SEALED_RUNNER_KEYS = {
    role: Ed25519PrivateKey.from_private_bytes(seed * 32)
    for role, seed in {
        "candidate": b"\x19",
        "comparator": b"\x1a",
        "gold": b"\x1b",
    }.items()
}
_E2E_SIGNING_KEYS = {
    role: Ed25519PrivateKey.from_private_bytes(seed * 32)
    for role, seed in {
        "candidate_worker": b"\x1c",
        "comparator": b"\x1d",
        "coordinator": b"\x1e",
        "evaluator": b"\x1f",
        "gold_worker": b"\x20",
    }.items()
}
_EVENT_AUTHENTICATOR = EventAuthenticator(
    (
        EventSigningKey(
            signing_key_id="key_lifecycle_fixture",
            principal_id="principal_lifecycle_fixture",
            run_id=_RUN_ID,
            public_key_bytes=_SIGNING_KEY.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            ),
            not_before="2026-08-09T00:00:00Z",
            not_after="2026-08-11T00:00:00Z",
        ),
    )
)
_COMMAND_AUTHORITY = CommandAuthority(
    "authority_lifecycle_tests",
    (
        CommandPrincipalBinding(
            "principal_fixed_commit_runner", "execution_lifecycle_terminal"
        ),
        CommandPrincipalBinding("principal_lifecycle_fixture", None),
    ),
)

ArtifactRepositoryAdapter = InMemoryArtifactRepository | SqliteArtifactRepository


class _InjectedLifecycleWriteError(RuntimeError):
    pass


class _FixtureArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.lifecycle-fixture.v1"]
    name: str


class _ManifestSigningKey(StrictFrozenModel):
    signing_key_id: str
    principal_id: str
    signature_algorithm: Literal["Ed25519"]
    public_key_b64url: str
    not_before: str
    not_after: str
    revoked_at: str | None


class _ActorCapability(StrictFrozenModel):
    principal_id: str
    process_execution_id: str | None
    allowed_event_types: FrozenSequence[str]


class _RunCreationBinding(StrictFrozenModel):
    creation_principal_id: str
    signing_key_id: str


class _ApprovalBinding(StrictFrozenModel):
    approval_principal_id: str
    approval_principal_kind: Literal["interactive_user"]
    signing_key_id: str
    policy_contract: ArtifactReference
    policy_source_hash: str | None
    policy_image_hash: str | None
    policy_version: str | None
    revocation_authorities: FrozenSequence[_RevocationAuthority]


class _RevocationAuthority(StrictFrozenModel):
    principal_id: str
    principal_kind: Literal["registered_revocation_policy"]
    signing_key_id: str


class _RunEventSecurityContext(StrictFrozenModel):
    schema_version: Literal["automarkov.run-event-security-context.v1"]
    run_id: str
    experiment_id: str | None
    root_ordinal: SafeCanonicalInt
    creation_policy: ArtifactReference
    max_clock_skew_ms: SafeCanonicalInt
    actor_capabilities: FrozenSequence[_ActorCapability]
    signing_keys: FrozenSequence[_ManifestSigningKey]
    run_creation: _RunCreationBinding
    approval: _ApprovalBinding

    @model_validator(mode="after")
    def require_nonnegative_root_clock(self) -> _RunEventSecurityContext:
        if self.root_ordinal != 0 or self.max_clock_skew_ms < 0:
            raise ValueError("invalid root ordinal or clock policy")
        return self


class _RunManifestFixture(StrictFrozenModel):
    schema_version: Literal["automarkov.run-manifest-fixture.v1"]
    name: Literal["run-manifest"]
    event_security_context: _RunEventSecurityContext


def _registry(*, terminal_provenance: bool = False) -> ArtifactSchemaRegistry:
    registry = ArtifactSchemaRegistry()
    for artifact_type in (
        "evidence_omission_record",
        "governance_report",
        "job_manifest",
        "payload_output",
        "resource_usage",
        "task_contract_authoring_context",
        "task_request",
        "llm_completion_trace",
        "output_scan_report",
    ):
        registry.register(
            artifact_type,
            "automarkov.lifecycle-fixture.v1",
            _FixtureArtifact,
            direct_parent_artifact_types=(),
        )
    registry.register(
        "run_manifest",
        "automarkov.run-manifest-fixture.v1",
        _RunManifestFixture,
        direct_parent_artifact_types=("governance_report",),
    )
    registry.register(
        "task_contract",
        "automarkov.task-contract.v1",
        TaskContract,
        direct_parent_artifact_types=("task_contract_authoring_context",),
    )
    registry.register(
        "task_contract_traceability_report",
        "automarkov.task-contract-traceability-report.v1",
        TaskContractTraceabilityReport,
        direct_parent_artifact_types=("task_contract", "task_request"),
    )
    registry.register(
        "text_critic_report",
        "automarkov.text-critic-report.v1",
        TextCriticReport,
        direct_parent_artifact_types=(
            "llm_completion_trace",
            "task_contract",
            "task_contract_traceability_report",
        ),
    )
    registry.register(
        "classification_result",
        "automarkov.classification-result.v1",
        ClassificationResult,
        direct_parent_artifact_types=(
            "evidence_omission_record",
            "task_contract",
        ),
    )
    registry.register(
        "runner_runtime_evidence",
        "automarkov.runner-runtime-evidence.v1",
        RunnerRuntimeEvidence,
        direct_parent_artifact_types=(),
    )
    registry.register(
        "runner_runtime_attestation",
        "automarkov.runner-runtime-attestation.v1",
        RunnerRuntimeAttestation,
        payload_parent_bindings=(
            ParentBinding(
                artifact_id_path="evidence_refs.*.artifact_id",
                payload_hash_path="evidence_refs.*.payload_hash",
                allowed_artifact_types=("runner_runtime_evidence",),
                cardinality="many",
            ),
        ),
    )
    registry.register(
        "runtime_profile_manifest",
        "automarkov.runtime-profile-manifest.v2",
        RuntimeProfileArtifactPayload,
        payload_parent_bindings=(
            ParentBinding(
                artifact_id_path="build_attestation_id",
                payload_hash_path="build_attestation_hash",
                allowed_artifact_types=("runner_runtime_attestation",),
                cardinality="optional",
            ),
            ParentBinding(
                artifact_id_path="import_smoke_attestation_id",
                payload_hash_path="import_smoke_attestation_hash",
                allowed_artifact_types=("runner_runtime_attestation",),
                cardinality="optional",
            ),
        ),
    )
    registry.register(
        "fixed_commit_job_manifest",
        "automarkov.fixed-commit-job-manifest.v1",
        FixedCommitJobManifest,
        payload_parent_bindings=tuple(
            sorted(
                (
                    *(
                        ParentBinding(
                            artifact_id_path=f"{field}.artifact_id",
                            payload_hash_path=f"{field}.payload_hash",
                            allowed_artifact_types=(
                                ("runtime_profile_manifest",)
                                if field == "profile_manifest"
                                else ("governance_report",)
                            ),
                            cardinality="one",
                        )
                        for field in (
                            "capability_policy",
                            "mount_policy",
                            "network_policy",
                            "output_contract",
                            "profile_manifest",
                            "resource_limits",
                            "scanner_policy",
                        )
                    ),
                    ParentBinding(
                        artifact_id_path="input_artifacts.*.artifact_id",
                        payload_hash_path="input_artifacts.*.payload_hash",
                        allowed_artifact_types=(
                            "governance_report",
                            "payload_output",
                            "runner_input",
                        ),
                        cardinality="many",
                    ),
                ),
                key=lambda binding: binding.artifact_id_path.encode("utf-8"),
            )
        ),
    )
    registry.register(
        "fixed_commit_run_authorization",
        "automarkov.fixed-commit-run-authorization.v1",
        FixedCommitRunAuthorization,
        direct_parent_artifact_types=(
            "fixed_commit_job_manifest",
            "governance_report",
        ),
    )
    registry.register(
        "run_manifest",
        "automarkov.run-manifest.v2",
        RunManifest,
        direct_parent_artifact_types=(
            "fixed_commit_job_manifest",
            "fixed_commit_job_manifest",
            "fixed_commit_job_manifest",
            "fixed_commit_run_authorization",
            "fixed_commit_run_authorization",
            "fixed_commit_run_authorization",
            "fixed_commit_run_authorization",
            "governance_report",
        ),
    )
    registry.register(
        "budget_snapshot",
        "automarkov.budget-snapshot.v1",
        BudgetSnapshot,
        direct_parent_artifact_types=(),
    )
    registry.register(
        "e2e_gate_evaluation_request",
        "automarkov.e2e-gate-evaluation-request.v1",
        E2EGateEvaluationRequest,
        direct_parent_artifact_types=tuple(
            sorted(
                (
                    "classification_result",
                    "governance_report",
                    "payload_output",
                    "payload_output",
                    "run_manifest",
                    "task_contract",
                )
            )
        ),
    )
    registry.register(
        "e2e_gate_verdict",
        "automarkov.e2e-gate-verdict.v1",
        E2EGateVerdict,
        direct_parent_artifact_types=tuple(
            sorted(
                (
                    "classification_result",
                    "governance_report",
                    "payload_output",
                    "run_manifest",
                    "task_contract",
                )
            )
        ),
    )
    registry.register(
        "sealed_worker_topology",
        "automarkov.sealed-worker-topology.v1",
        SealedWorkerTopology,
        direct_parent_artifact_types=tuple(
            sorted(
                (
                    "classification_result",
                    "e2e_gate_evaluation_request",
                    "governance_report",
                    "payload_output",
                    "task_contract",
                )
            )
        ),
    )
    registry.register(
        "runner_input",
        "automarkov.runner-input.v1",
        RunnerInput,
        payload_parent_bindings=(
            ParentBinding(
                artifact_id_path="source_artifact.artifact_id",
                payload_hash_path="source_artifact.payload_hash",
                allowed_artifact_types=("payload_output",),
                cardinality="one",
            ),
        ),
    )
    registry.register(
        "runner_output_binding",
        "automarkov.runner-output-binding.v2",
        RunnerOutputBinding,
        direct_parent_artifact_types=(),
    )
    if terminal_provenance:
        registry.register(
            "process_execution_terminal_record",
            "automarkov.process-execution-terminal-record.v1",
            ProcessExecutionTerminalRecord,
            payload_parent_bindings=(
                ParentBinding(
                    artifact_id_path="job_manifest.artifact_id",
                    payload_hash_path="job_manifest.payload_hash",
                    allowed_artifact_types=(
                        "fixed_commit_job_manifest",
                        "job_manifest",
                    ),
                    cardinality="one",
                ),
                ParentBinding(
                    artifact_id_path="payload_outputs.*.artifact_id",
                    payload_hash_path="payload_outputs.*.payload_hash",
                    allowed_artifact_types=(
                        "e2e_gate_verdict",
                        "payload_output",
                        "runner_output_binding",
                    ),
                    cardinality="many",
                ),
                ParentBinding(
                    artifact_id_path="resource_usage.artifact_id",
                    payload_hash_path="resource_usage.payload_hash",
                    allowed_artifact_types=("resource_usage",),
                    cardinality="one",
                ),
            ),
        )
        registry.register(
            "terminal_result",
            "automarkov.terminal-result.v1",
            TerminalResult,
            payload_parent_bindings=(
                ParentBinding(
                    artifact_id_path="fixed_commit_job_manifest.artifact_id",
                    payload_hash_path="fixed_commit_job_manifest.payload_hash",
                    allowed_artifact_types=(
                        "fixed_commit_job_manifest",
                        "job_manifest",
                    ),
                    cardinality="one",
                ),
                ParentBinding(
                    artifact_id_path="payload_outputs.*.artifact_id",
                    payload_hash_path="payload_outputs.*.payload_hash",
                    allowed_artifact_types=(
                        "e2e_gate_verdict",
                        "payload_output",
                        "runner_output_binding",
                    ),
                    cardinality="many",
                ),
                ParentBinding(
                    artifact_id_path="process_execution_terminal_record.artifact_id",
                    payload_hash_path="process_execution_terminal_record.payload_hash",
                    allowed_artifact_types=("process_execution_terminal_record",),
                    cardinality="one",
                ),
            ),
        )
        registry.register(
            "execution_attestation",
            "automarkov.execution-attestation.v1",
            ExecutionAttestation,
            payload_parent_bindings=(
                ParentBinding(
                    artifact_id_path="job_manifest.artifact_id",
                    payload_hash_path="job_manifest.payload_hash",
                    allowed_artifact_types=(
                        "fixed_commit_job_manifest",
                        "job_manifest",
                    ),
                    cardinality="one",
                ),
                ParentBinding(
                    artifact_id_path="output_scan_report.artifact_id",
                    payload_hash_path="output_scan_report.payload_hash",
                    allowed_artifact_types=("output_scan_report",),
                    cardinality="optional",
                ),
                ParentBinding(
                    artifact_id_path="payload_outputs.*.artifact_id",
                    payload_hash_path="payload_outputs.*.payload_hash",
                    allowed_artifact_types=(
                        "e2e_gate_verdict",
                        "payload_output",
                        "runner_output_binding",
                    ),
                    cardinality="many",
                ),
                ParentBinding(
                    artifact_id_path="process_terminal_record.artifact_id",
                    payload_hash_path="process_terminal_record.payload_hash",
                    allowed_artifact_types=("process_execution_terminal_record",),
                    cardinality="one",
                ),
                ParentBinding(
                    artifact_id_path="terminal_result.artifact_id",
                    payload_hash_path="terminal_result.payload_hash",
                    allowed_artifact_types=("terminal_result",),
                    cardinality="optional",
                ),
            ),
        )
        registry.register(
            "run_audit_projection",
            "automarkov.run-audit-projection.v1",
            RunAuditProjection,
            payload_parent_bindings=(
                ParentBinding(
                    artifact_id_path="previous_projection.artifact_id",
                    payload_hash_path="previous_projection.payload_hash",
                    allowed_artifact_types=("run_audit_projection",),
                    cardinality="optional",
                ),
                ParentBinding(
                    artifact_id_path="signed_deviations.*.artifact_id",
                    payload_hash_path="signed_deviations.*.payload_hash",
                    allowed_artifact_types=("governance_report",),
                    cardinality="many",
                ),
                ParentBinding(
                    artifact_id_path="terminal_result.artifact_id",
                    payload_hash_path="terminal_result.payload_hash",
                    allowed_artifact_types=("terminal_result",),
                    cardinality="one",
                ),
            ),
        )
    registry.freeze()
    return registry


def _uuid7(index: int, *, variant: int = 0) -> str:
    timestamp_ms = int(datetime.fromisoformat(_ISSUED_AT).timestamp() * 1_000)
    value = (timestamp_ms << 80) | (7 << 76) | (variant << 64) | (2 << 62) | index
    return str(UUID(int=value))


def _budget(*, consumed: int = 0, limit: int = 10) -> dict[str, object]:
    return {
        "schema_version": "automarkov.budget-snapshot.v1",
        "contract_hash": _HASH_A,
        "counters": [
            {"metric": metric, "consumed": consumed, "limit": limit}
            for metric in sorted(
                (
                    "wall_time_ms",
                    "llm_tokens",
                    "tool_calls",
                    "provider_credits",
                    "cost_microunits",
                    "stage_revisions",
                )
            )
        ],
    }


def _put(
    repository: ArtifactRepositoryAdapter,
    artifact_type: str,
    name: str,
) -> Any:
    payload = (
        _budget()
        if artifact_type == "budget_snapshot"
        else {
            "schema_version": "automarkov.lifecycle-fixture.v1",
            "name": name,
        }
    )
    return repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": artifact_type,
            "payload_bytes": canonical_json_bytes(payload),
            "parent_artifact_ids": [],
            "created_by": "principal_lifecycle_fixture",
            "created_at": _ISSUED_AT,
            "source_evidence_ids": [],
        }
    )


def _put_run_manifest(
    repository: ArtifactRepositoryAdapter,
    creation_policy: Any,
    *,
    max_clock_skew_ms: int = 0,
    revoked_at: str | None = None,
) -> Any:
    policy_reference = _artifact_ref(creation_policy)
    public_key = _SIGNING_KEY.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    payload = {
        "schema_version": "automarkov.run-manifest-fixture.v1",
        "name": "run-manifest",
        "event_security_context": {
            "schema_version": "automarkov.run-event-security-context.v1",
            "run_id": _RUN_ID,
            "experiment_id": None,
            "root_ordinal": 0,
            "creation_policy": policy_reference,
            "max_clock_skew_ms": max_clock_skew_ms,
            "actor_capabilities": [
                {
                    "principal_id": "principal_fixed_commit_runner",
                    "process_execution_id": "execution_lifecycle_terminal",
                    "allowed_event_types": [
                        "BudgetExhausted",
                        "StageGatePassed",
                        "StateTransitioned",
                        "ValidationFailed",
                    ],
                },
                {
                    "principal_id": "principal_lifecycle_fixture",
                    "process_execution_id": None,
                    "allowed_event_types": [
                        "ArtifactAccessRevoked",
                        "EvidenceTemporarilyUnavailable",
                        "RunCreated",
                        "SignedApprovalEvent",
                        "StageGatePassed",
                        "StateTransitioned",
                        "WaitResolved",
                        "WaitingEvidence",
                    ],
                },
            ],
            "signing_keys": [
                {
                    "signing_key_id": "key_lifecycle_fixture",
                    "principal_id": "principal_lifecycle_fixture",
                    "signature_algorithm": "Ed25519",
                    "public_key_b64url": base64.urlsafe_b64encode(public_key)
                    .decode()
                    .rstrip("="),
                    "not_before": "2026-08-09T00:00:00Z",
                    "not_after": "2026-08-11T00:00:00Z",
                    "revoked_at": revoked_at,
                }
            ],
            "run_creation": {
                "creation_principal_id": "principal_lifecycle_fixture",
                "signing_key_id": "key_lifecycle_fixture",
            },
            "approval": {
                "approval_principal_id": "principal_lifecycle_fixture",
                "approval_principal_kind": "interactive_user",
                "signing_key_id": "key_lifecycle_fixture",
                "policy_contract": policy_reference,
                "policy_source_hash": None,
                "policy_image_hash": None,
                "policy_version": None,
                "revocation_authorities": [],
            },
        },
    }
    return repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "run_manifest",
            "payload_bytes": canonical_json_bytes(payload),
            "parent_artifact_ids": [creation_policy.artifact_id.root],
            "created_by": "principal_lifecycle_fixture",
            "created_at": _ISSUED_AT,
            "source_evidence_ids": [],
        }
    )


def _artifact_ref(result: Any) -> dict[str, str]:
    return {
        "artifact_id": result.artifact_id.root,
        "payload_hash": result.payload_hash.root,
    }


def _event_record(raw_event: dict[str, object]) -> Any:
    return parse_event_record(encode_event_record(raw_event))


def _event_head(result: LifecycleCommitReceipt) -> dict[str, object]:
    return result.run_view.event_head.model_dump(mode="json")


def _project(
    repository: ArtifactRepositoryAdapter,
    *,
    sequence_no: int,
    event_head_hash: str,
) -> Any:
    return repository.project(
        RunId(root=_RUN_ID),
        VerifiedEventHead.model_validate(
            {
                "run_id": _RUN_ID,
                "sequence_no": sequence_no,
                "event_hash": event_head_hash,
            },
            strict=True,
        ),
        projector_version=RUN_PROJECTOR_VERSION,
        projector_hash=Sha256Digest(root=RUN_PROJECTOR_HASH),
    )


def _append_events(
    repository: ArtifactRepositoryAdapter,
    raw_events: list[dict[str, object]],
    *,
    command_index: int,
    expected_state: str | None,
    expected_head: dict[str, object] | None,
) -> LifecycleCommitReceipt:
    return _commit(
        repository,
        {
            "schema_version": "automarkov.lifecycle-command.v1",
            "command_type": "append_run_events",
            "command_id": _uuid7(10_000 + command_index),
            "actor_principal_id": "principal_lifecycle_fixture",
            "issued_at": _ISSUED_AT,
            "idempotency_key": f"lifecycle-command-{command_index}",
            "run_id": _RUN_ID,
            "expected_state": expected_state,
            "expected_head": expected_head,
            "events": raw_events,
        },
    )


def _commit(
    repository: ArtifactRepositoryAdapter,
    command: dict[str, object],
) -> LifecycleCommitReceipt:
    events = cast(list[dict[str, object]], command["events"])
    process_ids = {
        cast(str | None, event.get("actor_process_execution_id")) for event in events
    }
    if len(process_ids) != 1:
        raise ValueError("test command must use one authenticated process")
    result = repository.commit(
        command,
        context=_COMMAND_AUTHORITY.issue(
            cast(str, command["actor_principal_id"]),
            process_ids.pop(),
            cast(str, command["issued_at"]),
        ),
    )
    assert isinstance(result, LifecycleCommitReceipt)
    return result


def _root_event(artifacts: Mapping[str, Any]) -> dict[str, object]:
    nonce = base64.urlsafe_b64encode(bytes(range(16))).decode().rstrip("=")
    manifest = _artifact_ref(artifacts["run_manifest"])
    event = {
        "schema_version": "automarkov.run-created.v1",
        "event_type": "RunCreated",
        "signing_domain": "AutoMarkov-Run-Created-v1",
        "event_id": _uuid7(0),
        "experiment_id": None,
        "run_id": _RUN_ID,
        "actor_principal_id": "principal_lifecycle_fixture",
        "issued_at": _ISSUED_AT,
        "sequence_no": 0,
        "previous_event_hash": ZERO_EVENT_HASH,
        "run_manifest_artifact_id": manifest["artifact_id"],
        "run_manifest_payload_hash": manifest["payload_hash"],
        "initial_state": "RECEIVED",
        "creation_principal_id": "principal_lifecycle_fixture",
        "reason_code": "run_created",
        "nonce_b64url": nonce,
        "signing_key_id": "key_lifecycle_fixture",
        "signature_algorithm": "Ed25519",
        "signature_b64url": "A" * 86,
    }
    signature = _SIGNING_KEY.sign(
        canonical_json_bytes(
            {key: value for key, value in event.items() if key != "signature_b64url"}
        )
    )
    event["signature_b64url"] = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return event


def _event_common(
    sequence_no: int,
    previous_event_hash: str,
    schema_version: str,
    event_type: str,
    *,
    variant: int = 0,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "event_type": event_type,
        "event_id": _uuid7(sequence_no, variant=variant),
        "experiment_id": None,
        "run_id": _RUN_ID,
        "actor_principal_id": "principal_lifecycle_fixture",
        "actor_process_execution_id": None,
        "issued_at": _ISSUED_AT,
        "sequence_no": sequence_no,
        "previous_event_hash": previous_event_hash,
    }


def _transition_event(
    *,
    sequence_no: int,
    previous_event_hash: str,
    from_state: str,
    to_state: str,
    trigger: Any,
    budget: Any,
    variant: int = 0,
) -> dict[str, object]:
    budget_ref = _artifact_ref(budget)
    return _event_common(
        sequence_no,
        previous_event_hash,
        "automarkov.state-transitioned.v1",
        "StateTransitioned",
        variant=variant,
    ) | {
        "from_state": from_state,
        "to_state": to_state,
        "trigger_event_id": trigger.event.event_id,
        "trigger_event_hash": trigger.event_hash,
        "input_artifact_ids": [],
        "gate_report_artifact_id": None,
        "gate_report_payload_hash": None,
        "budget_snapshot_artifact_id": budget_ref["artifact_id"],
        "budget_snapshot_payload_hash": budget_ref["payload_hash"],
        "reason_code": "test_transition",
    }


def _start_run(
    repository: ArtifactRepositoryAdapter,
) -> tuple[dict[str, Any], LifecycleCommitReceipt]:
    artifacts = _put_run_artifacts(repository)
    root = _append_events(
        repository,
        [_root_event(artifacts)],
        command_index=0,
        expected_state=None,
        expected_head=None,
    )
    return artifacts, root


def _task_contract_payload() -> dict[str, object]:
    return {
        "schema_version": "automarkov.task-contract.v1",
        "contract_kind": "core_task",
        "task_identity": {
            "name": "Lifecycle fixture",
            "domain": "testing",
            "intended_use": "repository lifecycle validation",
            "excluded_uses": [],
        },
        "decision_structure": {
            "decision_makers": [
                {
                    "decision_maker_id": "agent_0",
                    "controlled_entity_ids": ["entity_0"],
                }
            ],
            "external_entity_ids": [],
            "coordination": "centralized",
            "decision_timing": {
                "timing": "simultaneous",
                "chance_turns": True,
                "environment_turns": True,
                "cycle_boundary": "one step",
            },
        },
        "objective": {
            "primary_objective": "validate lifecycle",
            "secondary_objectives": [],
            "success_criteria": ["repository transition commits"],
            "tradeoffs": [],
        },
        "information": {
            "observable_variables_by_decision_maker": {
                "agent_0": [
                    {
                        "name": "state",
                        "domain": {
                            "kind": "scalar",
                            "element_dtype": "int",
                            "bounds": {
                                "binding_kind": "explicit",
                                "minimum": 0,
                                "maximum": 1,
                                "minimum_inclusive": True,
                                "maximum_inclusive": True,
                            },
                        },
                        "unit": "state",
                        "semantic_definition": "fixture state",
                        "evidence_ids": ["E-fixture"],
                    }
                ]
            },
            "latent_variables": [],
            "joint_observation_semantics": None,
            "history_access_by_decision_maker": {
                "agent_0": {
                    "observation_lags": [],
                    "action_lags": [],
                    "reward_lags": [],
                    "message_lags": [],
                    "recurrent_state_allowed": False,
                    "boundary_reset": "episode",
                }
            },
            "message_processes_by_recipient": {"agent_0": []},
        },
        "dynamics": {
            "exogenous_processes": [],
            "stochastic_assumptions": [],
            "intervention_effects": [],
            "reward_randomness": [],
            "time_step": "one step",
            "horizon_binding": "one episode",
        },
        "constraints": {
            "hard_constraints": [],
            "soft_constraints": [],
            "safety_constraints": [],
            "resource_limits": [],
        },
        "risks": {
            "failure_events": [],
            "risk_measures": [],
            "tolerances": [],
            "tail_or_worst_case_requirements": [],
        },
        "episode": {
            "reset_conditions": ["new run"],
            "termination_conditions": ["terminal state"],
            "truncation_conditions": [],
        },
        "evidence_and_assumptions": {
            "evidence_ids": ["E-fixture"],
            "accepted_assumptions": [],
            "unresolved_questions": [],
        },
        "validation_target": {
            "required_level": "behavioral",
            "required_properties": ["repository_contract"],
            "accepted_tolerances": [],
        },
    }


def _put_run_artifacts(
    repository: ArtifactRepositoryAdapter,
) -> dict[str, Any]:
    governance = _put(repository, "governance_report", "governance")
    task_request = _put(repository, "task_request", "task-request")
    authoring_context = _put(
        repository,
        "task_contract_authoring_context",
        "authoring-context",
    )
    task_contract_payload = _task_contract_payload()
    task_contract = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "task_contract",
            "payload_bytes": canonical_json_bytes(task_contract_payload),
            "parent_artifact_ids": [authoring_context.artifact_id.root],
            "created_by": "principal_lifecycle_fixture",
            "created_at": _ISSUED_AT,
            "source_evidence_ids": [],
        }
    )
    task_contract_model = TaskContract.model_validate(
        task_contract_payload,
        strict=True,
    )
    task_contract_ref = _artifact_ref(task_contract)
    task_request_ref = _artifact_ref(task_request)
    trace = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "task_contract_traceability_report",
            "payload_bytes": canonical_json_bytes(
                {
                    "schema_version": (
                        "automarkov.task-contract-traceability-report.v1"
                    ),
                    "task_contract": task_contract_ref,
                    "task_request": task_request_ref,
                    "entries": [
                        {
                            "target_path": path,
                            "source_kind": "task_request",
                            "source_ids": ["request_lifecycle_fixture"],
                        }
                        for path in task_contract_claim_paths(task_contract_model)
                    ],
                    "uncovered_paths": [],
                    "generated_at": _ISSUED_AT,
                }
            ),
            "parent_artifact_ids": sorted(
                [task_contract.artifact_id.root, task_request.artifact_id.root]
            ),
            "created_by": "principal_lifecycle_fixture",
            "created_at": _ISSUED_AT,
            "source_evidence_ids": [],
        }
    )
    completion_trace = _put(
        repository,
        "llm_completion_trace",
        "critic-completion",
    )
    critic = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "text_critic_report",
            "payload_bytes": canonical_json_bytes(
                {
                    "schema_version": "automarkov.text-critic-report.v1",
                    "report_kind": "task_contract_review",
                    "task_contract": task_contract_ref,
                    "traceability_report": _artifact_ref(trace),
                    "critic_completion_trace": _artifact_ref(completion_trace),
                    "previous_critic_report": None,
                    "issues": [],
                    "reviewed_at": _ISSUED_AT,
                }
            ),
            "parent_artifact_ids": sorted(
                [
                    completion_trace.artifact_id.root,
                    task_contract.artifact_id.root,
                    trace.artifact_id.root,
                ]
            ),
            "created_by": "principal_lifecycle_fixture",
            "created_at": _ISSUED_AT,
            "source_evidence_ids": [],
        }
    )
    evidence_omission = _put(
        repository,
        "evidence_omission_record",
        "evidence-omission",
    )
    evidence_omission_ref = _artifact_ref(evidence_omission)
    classification = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "classification_result",
            "payload_bytes": canonical_json_bytes(
                {
                    "schema_version": "automarkov.classification-result.v1",
                    "result_kind": "classification",
                    "source_task_ref": task_contract_ref,
                    "evidence_binding": {
                        "schema_version": "automarkov.evidence-omission-binding.v1",
                        "binding_kind": "omitted_by_design",
                        "omission_record_ref": evidence_omission_ref,
                        "ablation_method_id": "automarkov_no_evidence",
                        "omitted_gate_id": "EVIDENCE_LEDGER_CLOSURE",
                    },
                    "classification": "IN_SCOPE_MDP",
                    "rationale": ["Lifecycle fixture classification."],
                }
            ),
            "parent_artifact_ids": sorted(
                [task_contract.artifact_id.root, evidence_omission.artifact_id.root]
            ),
            "created_by": "principal_lifecycle_fixture",
            "created_at": _ISSUED_AT,
            "source_evidence_ids": [],
        }
    )
    return {
        "run_manifest": _put_run_manifest(repository, governance),
        "job_manifest": _put(repository, "job_manifest", "job-manifest"),
        "output": _put(repository, "payload_output", "output"),
        "alternate_output": _put(repository, "payload_output", "alternate-output"),
        "candidate_output": _put(repository, "payload_output", "candidate-output"),
        "resource_usage": _put(repository, "resource_usage", "resource-usage"),
        "governance": governance,
        "classification": classification,
        "task_contract": task_contract,
        "trace": trace,
        "critic": critic,
        "budget": _put(repository, "budget_snapshot", "budget"),
    }


def _advance_to_researching(
    repository: ArtifactRepositoryAdapter,
    artifacts: Mapping[str, Any],
    root: LifecycleCommitReceipt,
    *,
    variant: int = 0,
    experiment_id: str | None = None,
) -> LifecycleCommitReceipt:
    gate_report = _artifact_ref(artifacts["governance"])
    gate = _event_common(
        1,
        previous_event_hash=root.event_record.event_hash,
        schema_version="automarkov.stage-gate-passed.v1",
        event_type="StageGatePassed",
        variant=variant,
    ) | {
        "experiment_id": experiment_id,
        "gate_id": "INTAKE_SCHEMA_BUDGET_AUTHORITY",
        "gate_version": "v1",
        "gate_contract_hash": _HASH_A,
        "subject_artifact_references": [],
        "gate_report": gate_report,
        "from_state": "RECEIVED",
        "to_state": "RESEARCHING",
        "reason_code": "intake_accepted",
        "result": "passed",
    }
    gate_record = _event_record(gate)
    event = _transition_event(
        sequence_no=2,
        previous_event_hash=gate_record.event_hash,
        from_state="RECEIVED",
        to_state="RESEARCHING",
        trigger=gate_record,
        budget=artifacts["budget"],
        variant=variant,
    ) | {
        "experiment_id": experiment_id,
        "gate_report_artifact_id": gate_report["artifact_id"],
        "gate_report_payload_hash": gate_report["payload_hash"],
        "reason_code": "intake_accepted",
    }
    return _append_events(
        repository,
        [gate, event],
        command_index=1 + variant,
        expected_state="RECEIVED",
        expected_head=_event_head(root),
    )


def _advance_to_sealed_e2e(
    repository: ArtifactRepositoryAdapter,
    artifacts: Mapping[str, Any],
    researching: LifecycleCommitReceipt,
    *,
    experiment_id: str | None = None,
) -> LifecycleCommitReceipt:
    edges = (
        (
            "RESEARCHING",
            "TEXT_DRAFTED",
            "EVIDENCE_LEDGER_CLOSURE",
            "research_completed",
        ),
        ("TEXT_DRAFTED", "TEXT_REVIEWED", "TEXT_SCHEMA", "text_schema_passed"),
        (
            "TEXT_REVIEWED",
            "WAITING_TEXT_CONFIRMATION",
            "TEXT_CRITIC_REVIEW",
            "text_review_passed",
        ),
        ("WAITING_TEXT_CONFIRMATION", "TEXT_LOCKED", None, "text_approved"),
        (
            "TEXT_LOCKED",
            "CLASSIFIED",
            "CLASSIFICATION_BINDING",
            "classification_passed",
        ),
        (
            "CLASSIFIED",
            "FORMAL_DRAFTED",
            "CLASSIFICATION_IN_SCOPE",
            "in_scope_classification_selected",
        ),
        (
            "FORMAL_DRAFTED",
            "FORMAL_REVIEWED",
            "FORMAL_SCHEMA_STRUCTURAL",
            "formal_schema_passed",
        ),
        (
            "FORMAL_REVIEWED",
            "WAITING_FORMAL_CONFIRMATION",
            "FORMAL_CRITIC_REVIEW",
            "formal_review_passed",
        ),
        ("WAITING_FORMAL_CONFIRMATION", "FORMAL_LOCKED", None, "formal_approved"),
        (
            "FORMAL_LOCKED",
            "IMPLEMENTATION_SELECTED",
            "FORMAL_LOCK_CLOSURE",
            "formal_contract_locked",
        ),
        (
            "IMPLEMENTATION_SELECTED",
            "ENVIRONMENT_IMPLEMENTED",
            "IMPLEMENTATION_ROUTE_SELECTION",
            "implementation_completed",
        ),
        (
            "ENVIRONMENT_IMPLEMENTED",
            "UNIT_VALIDATING",
            "ENVIRONMENT_ARTIFACT_FREEZE",
            "environment_artifacts_frozen",
        ),
        (
            "UNIT_VALIDATING",
            "SIMULATION_VALIDATING",
            "UNIT_VALIDATION",
            "unit_validation_passed",
        ),
        (
            "SIMULATION_VALIDATING",
            "SEALED_E2E_VALIDATING",
            "PUBLIC_SIMULATION_TESTER",
            "public_simulation_passed",
        ),
    )
    receipt = researching
    report = _artifact_ref(artifacts["governance"])
    approval = _artifact_ref(artifacts["task_contract"])
    approval_report_ids = sorted(
        (
            artifacts["trace"].artifact_id.root,
            artifacts["critic"].artifact_id.root,
        )
    )
    pending_events: list[dict[str, object]] = []
    pending_from_state = "RESEARCHING"
    previous_hash = receipt.after_head.event_hash
    sequence_no = receipt.after_head.sequence_no + 1
    for edge_index, (from_state, to_state, gate_id, reason) in enumerate(edges):
        subjects: list[dict[str, str]] = []
        if gate_id is not None:
            subjects = (
                sorted(
                    (
                        _artifact_ref(artifacts["classification"]),
                        _artifact_ref(artifacts["task_contract"]),
                    ),
                    key=lambda item: item["artifact_id"],
                )
                if gate_id == "CLASSIFICATION_BINDING"
                else []
            )
            cause = _event_common(
                sequence_no,
                previous_hash,
                "automarkov.stage-gate-passed.v1",
                "StageGatePassed",
                variant=7,
            ) | {
                "experiment_id": experiment_id,
                "gate_id": gate_id,
                "gate_version": "v1",
                "gate_contract_hash": _HASH_A,
                "subject_artifact_references": subjects,
                "gate_report": report,
                "from_state": from_state,
                "to_state": to_state,
                "reason_code": reason,
                "result": "passed",
            }
        else:
            cause = _event_common(
                sequence_no,
                previous_hash,
                "automarkov.approval-event.v1",
                "SignedApprovalEvent",
                variant=7,
            )
            cause.pop("actor_process_execution_id")
            cause |= {
                "experiment_id": experiment_id,
                "signing_domain": "AutoMarkov-Approval-v1",
                "decision": "approved",
                "artifact": approval,
                "supersedes_approval_event_id": None,
                "approval_principal_id": "principal_lifecycle_fixture",
                "approval_principal_kind": "interactive_user",
                "approval_policy_source_hash": None,
                "input_report_artifact_ids": approval_report_ids,
                "reason_code": reason,
                "nonce_b64url": base64.urlsafe_b64encode(
                    sequence_no.to_bytes(16, "big")
                )
                .decode()
                .rstrip("="),
                "signing_key_id": "key_lifecycle_fixture",
                "signature_algorithm": "Ed25519",
                "signature_b64url": "A" * 86,
            }
            cause["signature_b64url"] = (
                base64.urlsafe_b64encode(
                    _SIGNING_KEY.sign(
                        canonical_json_bytes(
                            {
                                key: value
                                for key, value in cause.items()
                                if key != "signature_b64url"
                            }
                        )
                    )
                )
                .decode()
                .rstrip("=")
            )
        cause_record = _event_record(cause)
        sequence_no += 1
        transition = _transition_event(
            sequence_no=sequence_no,
            previous_event_hash=cause_record.event_hash,
            from_state=from_state,
            to_state=to_state,
            trigger=cause_record,
            budget=artifacts["budget"],
            variant=7,
        ) | {
            "experiment_id": experiment_id,
            "input_artifact_ids": [item["artifact_id"] for item in subjects]
            if gate_id is not None
            else [],
            "gate_report_artifact_id": (
                report["artifact_id"] if gate_id is not None else None
            ),
            "gate_report_payload_hash": (
                report["payload_hash"] if gate_id is not None else None
            ),
            "reason_code": reason,
        }
        transition_record = _event_record(transition)
        pending_events.extend((cause, transition))
        previous_hash = transition_record.event_hash
        sequence_no += 1
        if to_state == "TEXT_LOCKED" or edge_index == len(edges) - 1:
            receipt = _append_events(
                repository,
                pending_events,
                command_index=81 + edge_index,
                expected_state=pending_from_state,
                expected_head=_event_head(receipt),
            )
            pending_events = []
            pending_from_state = to_state
    return receipt


def test_run_created_requires_the_exact_manifest_artifact_type(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    governance = _put(lifecycle_repository, "governance_report", "not-a-manifest")

    with pytest.raises(TerminalProvenanceError):
        _append_events(
            lifecycle_repository,
            [_root_event({"run_manifest": governance})],
            command_index=0,
            expected_state=None,
            expected_head=None,
        )


@pytest.mark.parametrize("adapter_name", ("memory", "sqlite"))
def test_run_created_rejects_a_cache_key_that_differs_from_the_manifest(
    adapter_name: str,
    tmp_path: Path,
) -> None:
    wrong_private_key = Ed25519PrivateKey.from_private_bytes(b"\x31" * 32)
    wrong_authenticator = EventAuthenticator(
        (
            EventSigningKey(
                signing_key_id="key_lifecycle_fixture",
                principal_id="principal_lifecycle_fixture",
                run_id=_RUN_ID,
                public_key_bytes=wrong_private_key.public_key().public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw,
                ),
                not_before="2026-08-09T00:00:00Z",
                not_after="2026-08-11T00:00:00Z",
            ),
        )
    )
    repository: ArtifactRepositoryAdapter
    if adapter_name == "memory":
        repository = InMemoryArtifactRepository(
            _registry(), wrong_authenticator, _COMMAND_AUTHORITY
        )
    else:
        repository = SqliteArtifactRepository(
            tmp_path / "wrong-cache-key.sqlite",
            _registry(),
            wrong_authenticator,
            _COMMAND_AUTHORITY,
        )
    try:
        policy = _put(repository, "governance_report", "creation-policy")
        manifest = _put_run_manifest(repository, policy)
        event = _root_event({"run_manifest": manifest})
        event["signature_b64url"] = (
            base64.urlsafe_b64encode(
                wrong_private_key.sign(
                    canonical_json_bytes(
                        {
                            key: value
                            for key, value in event.items()
                            if key != "signature_b64url"
                        }
                    )
                )
            )
            .decode()
            .rstrip("=")
        )

        with pytest.raises(EventSchemaError):
            _append_events(
                repository,
                [event],
                command_index=0,
                expected_state=None,
                expected_head=None,
            )
    finally:
        if isinstance(repository, SqliteArtifactRepository):
            repository.close()


def test_repository_rejects_forged_or_clock_skewed_command_context(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    artifacts = _put_run_artifacts(lifecycle_repository)
    event = _root_event(artifacts)
    command = {
        "schema_version": "automarkov.lifecycle-command.v1",
        "command_type": "append_run_events",
        "command_id": _uuid7(9_800),
        "actor_principal_id": "principal_lifecycle_fixture",
        "issued_at": _ISSUED_AT,
        "idempotency_key": "lifecycle-command-auth-negative",
        "run_id": _RUN_ID,
        "expected_state": None,
        "expected_head": None,
        "events": [event],
    }
    before = _repository_storage_snapshot(lifecycle_repository)
    rejected_contexts = (
        AuthenticatedCommandContext(
            principal_id="principal_lifecycle_fixture",
            process_execution_id=None,
            received_at=_ISSUED_AT,
            authority_id=_COMMAND_AUTHORITY.authority_id,
            _issuer=object(),
        ),
        _COMMAND_AUTHORITY.issue(
            "principal_lifecycle_fixture",
            None,
            "2026-08-10T11:00:01Z",
        ),
    )

    for context in rejected_contexts:
        with pytest.raises(CommandAuthenticationError):
            lifecycle_repository.commit(command, context=context)
        assert _repository_storage_snapshot(lifecycle_repository) == before


def test_repository_rejects_backdated_event_from_revoked_key(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    governance = _put(lifecycle_repository, "governance_report", "governance")
    manifest = _put_run_manifest(
        lifecycle_repository,
        governance,
        max_clock_skew_ms=120_000,
        revoked_at="2026-08-10T10:59:30Z",
    )
    event = _root_event({"run_manifest": manifest})
    event["issued_at"] = _STARTED_AT
    timestamp_ms = int(datetime.fromisoformat(_STARTED_AT).timestamp() * 1_000)
    event["event_id"] = str(UUID(int=(timestamp_ms << 80) | (7 << 76) | (2 << 62)))
    event["signature_b64url"] = (
        base64.urlsafe_b64encode(
            _SIGNING_KEY.sign(
                canonical_json_bytes(
                    {
                        key: value
                        for key, value in event.items()
                        if key != "signature_b64url"
                    }
                )
            )
        )
        .decode()
        .rstrip("=")
    )
    command = {
        "schema_version": "automarkov.lifecycle-command.v1",
        "command_type": "append_run_events",
        "command_id": _uuid7(9_801),
        "actor_principal_id": "principal_lifecycle_fixture",
        "issued_at": _ISSUED_AT,
        "idempotency_key": "lifecycle-command-revoked-key",
        "run_id": _RUN_ID,
        "expected_state": None,
        "expected_head": None,
        "events": [event],
    }
    before = _repository_storage_snapshot(lifecycle_repository)

    with pytest.raises(CommandAuthenticationError):
        lifecycle_repository.commit(
            command,
            context=_COMMAND_AUTHORITY.issue(
                "principal_lifecycle_fixture",
                None,
                _ISSUED_AT,
            ),
        )
    assert _repository_storage_snapshot(lifecycle_repository) == before


def _terminal_cause(
    artifacts: Mapping[str, Any],
    researching: LifecycleCommitReceipt,
    *,
    experiment_id: str | None = None,
) -> dict[str, object]:
    return _event_common(
        researching.event_record.event.sequence_no + 1,
        researching.event_record.event_hash,
        "automarkov.validation-failed.v1",
        "ValidationFailed",
    ) | {
        "experiment_id": experiment_id,
        "actor_principal_id": "principal_fixed_commit_runner",
        "actor_process_execution_id": "execution_lifecycle_terminal",
        "subject": _artifact_ref(artifacts["output"]),
        "report": _artifact_ref(artifacts["governance"]),
        "validator_id": "validator_lifecycle_terminal",
        "validator_version": "v1",
        "validation_level": "terminal",
        "validation_scope": "internal",
        "failure_code": "unrecoverable_internal_error",
    }


def _process_terminal_record(
    artifacts: Mapping[str, Any],
    *,
    output_ref: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "automarkov.process-execution-terminal-record.v1",
        "signing_domain": "AutoMarkov-ProcessExecutionTerminalRecord-v1",
        "experiment_id": None,
        "run_id": _RUN_ID,
        "job_id": "job_lifecycle_terminal",
        "process_execution_id": "execution_lifecycle_terminal",
        "profile_id": "profile_lifecycle_terminal",
        "principal_id": "principal_fixed_commit_runner",
        "job_manifest": _artifact_ref(artifacts["job_manifest"]),
        "status": "terminal_failure",
        "exit_code": 1,
        "reason_code": "fixed_commit_failed",
        "started_at": _STARTED_AT,
        "finished_at": _ISSUED_AT,
        "stdout_hash": _HASH_A,
        "stderr_hash": _HASH_B,
        "payload_outputs": [output_ref or _artifact_ref(artifacts["output"])],
        "resource_usage": _artifact_ref(artifacts["resource_usage"]),
        "network_log_hash": _HASH_A,
        "mount_attestation_hash": _HASH_A,
        "capability_decision_hash": _HASH_A,
        "egress_log_hash": _HASH_A,
        "created_at": _ISSUED_AT,
    }


def _terminal_command(
    artifacts: Mapping[str, Any],
    researching: LifecycleCommitReceipt,
    *,
    process_record: dict[str, object] | None = None,
    terminal_time_approvals: list[dict[str, object]] | None = None,
    command_variant: int = 0,
    experiment_id: str | None = None,
) -> dict[str, object]:
    cause = _terminal_cause(artifacts, researching, experiment_id=experiment_id)
    cause_record = parse_event_record(encode_event_record(cause))
    transition = _transition_event(
        sequence_no=cause_record.event.sequence_no + 1,
        previous_event_hash=cause_record.event_hash,
        from_state="RESEARCHING",
        to_state="FAILED",
        trigger=cause_record,
        budget=artifacts["budget"],
    ) | {
        "experiment_id": experiment_id,
        "actor_principal_id": "principal_fixed_commit_runner",
        "actor_process_execution_id": "execution_lifecycle_terminal",
        "reason_code": "unrecoverable_internal_error",
    }
    return {
        "schema_version": "automarkov.lifecycle-command.v1",
        "command_type": "commit_terminal",
        "command_id": _uuid7(20_000 + command_variant),
        "actor_principal_id": "principal_fixed_commit_runner",
        "issued_at": _ISSUED_AT,
        "idempotency_key": f"commit-terminal-{command_variant}",
        "run_id": _RUN_ID,
        "expected_state": "RESEARCHING",
        "expected_head": _event_head(researching),
        "events": [cause, transition],
        "process_terminal_record": process_record
        or _process_terminal_record(artifacts),
        "fixed_commit_job_manifest": _artifact_ref(artifacts["job_manifest"]),
        "terminal_time_approvals": terminal_time_approvals or [],
        "projector_version": RUN_PROJECTOR_VERSION,
        "projector_hash": RUN_PROJECTOR_HASH,
        "created_at": _ISSUED_AT,
    }


def _post_terminal_audit_command(
    artifacts: Mapping[str, Any],
    terminal: LifecycleCommitReceipt,
) -> dict[str, object]:
    audit_event = _event_common(
        terminal.event_record.event.sequence_no + 1,
        terminal.event_record.event_hash,
        "automarkov.artifact-access-revoked.v1",
        "ArtifactAccessRevoked",
    ) | {
        "subject": _artifact_ref(artifacts["output"]),
        "reason_code": "retention_policy",
        "governance_policy": _artifact_ref(artifacts["governance"]),
        "revocation_authority_principal_id": "principal_lifecycle_fixture",
        "effective_at": _ISSUED_AT,
    }
    return {
        "schema_version": "automarkov.lifecycle-command.v1",
        "command_type": "append_run_events",
        "command_id": _uuid7(10_030),
        "actor_principal_id": "principal_lifecycle_fixture",
        "issued_at": _ISSUED_AT,
        "idempotency_key": "lifecycle-command-30",
        "run_id": _RUN_ID,
        "expected_state": "FAILED",
        "expected_head": _event_head(terminal),
        "events": [audit_event],
    }


def test_fixed_commit_adapter_uses_real_atomic_terminal_cas(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    artifacts, root = _start_run(lifecycle_repository)
    researching = _advance_to_researching(lifecycle_repository, artifacts, root)
    process = ProcessExecutionTerminalRecord.model_validate(
        _process_terminal_record(artifacts), strict=True
    )
    head = VerifiedEventHead(
        run_id=RunId(root=_RUN_ID),
        sequence_no=researching.event_record.event.sequence_no,
        event_hash=Sha256Digest(root=researching.event_record.event_hash),
    )
    committer = ArtifactRepositoryTerminalCommitter(
        repository=lifecycle_repository,
        context=_COMMAND_AUTHORITY.issue(
            process.principal_id,
            process.process_execution_id,
            _ISSUED_AT,
        ),
        specified_event_head=head,
        command_builder=lambda actual: _terminal_command(
            artifacts,
            researching,
            process_record=actual.model_dump(mode="json"),
        ),
    )

    receipt, terminal = committer.commit_terminal(process)

    assert terminal.process_execution_terminal_record == receipt.process_terminal_record
    assert terminal.fixed_commit_job_manifest == process.job_manifest
    assert terminal.payload_outputs == process.payload_outputs
    projected = lifecycle_repository.project(
        RunId(root=_RUN_ID),
        terminal.terminal_snapshot_event_head,
        projector_version=RUN_PROJECTOR_VERSION,
        projector_hash=Sha256Digest(root=RUN_PROJECTOR_HASH),
    )
    assert projected.state is RunState.FAILED
    assert projected.terminal_result == receipt.terminal_result


def _put_e2e_request_and_verdict(
    repository: ArtifactRepositoryAdapter,
    artifacts: Mapping[str, Any],
    sealed: LifecycleCommitReceipt,
    state: Literal["TRAINING_SMOKE_TESTING", "PARTIAL", "FAILED"],
) -> tuple[
    ArtifactReference,
    ArtifactReference,
    ArtifactReference,
    ArtifactReference,
]:
    run_manifest = ArtifactReference.model_validate(
        _artifact_ref(artifacts["run_manifest"]), strict=True
    )
    candidate_validation_freeze = ArtifactReference.model_validate(
        _artifact_ref(artifacts["output"]), strict=True
    )
    candidate_bundle = ArtifactReference.model_validate(
        _artifact_ref(artifacts["candidate_output"]), strict=True
    )
    task_contract = ArtifactReference.model_validate(
        _artifact_ref(artifacts["task_contract"]), strict=True
    )
    decision_process_spec = ArtifactReference.model_validate(
        _artifact_ref(artifacts["classification"]), strict=True
    )
    environment_binding = ArtifactReference.model_validate(
        _artifact_ref(artifacts["governance"]), strict=True
    )
    request = sign_e2e_request(
        {
            "schema_version": "automarkov.e2e-gate-evaluation-request.v1",
            "signing_domain": "AutoMarkov-E2E-Gate-Evaluation-Request-v1",
            "request_id": f"request_repository_e2e_{state.lower()}",
            "experiment_id": "experiment_runner_graph",
            "run_id": _RUN_ID,
            "run_manifest": run_manifest,
            "specified_event_head": VerifiedEventHead(
                run_id=RunId(root=_RUN_ID),
                sequence_no=sealed.after_head.sequence_no,
                event_hash=Sha256Digest(root=sealed.after_head.event_hash),
            ),
            "candidate_validation_freeze": candidate_validation_freeze,
            "candidate_bundle": candidate_bundle,
            "task_contract": task_contract,
            "decision_process_spec": decision_process_spec,
            "environment_binding": environment_binding,
            "candidate_worker_profile_id": "rllib-core",
            "candidate_worker_profile_hash": _HASH_A,
            "evaluator_protocol_id": "sealed-e2e-v1",
            "evaluator_protocol_hash": _HASH_A,
            "evaluator_profile_id": "sealed-evaluator-rllib",
            "evaluator_profile_hash": _HASH_A,
            "evaluator_lock_hash": _HASH_A,
            "evaluator_image_hash": _HASH_A,
            "evaluator_schema_id": "e2e-verdict-schema-v1",
            "evaluator_schema_hash": _HASH_A,
            "gold_worker_profile_id": "sealed-env-taxi-gold",
            "gold_worker_profile_hash": _HASH_A,
            "not_before": "2026-08-10T10:59:00Z",
            "expires_at": "2026-08-10T11:01:00Z",
            "coordinator_principal_id": "principal_coordinator",
            "coordinator_key_id": "key_coordinator",
            "issued_at": _ISSUED_AT,
            "nonce_b64url": base64.urlsafe_b64encode(bytes(range(32)))
            .decode()
            .rstrip("="),
            "signature_algorithm": "Ed25519",
        },
        _E2E_SIGNING_KEYS["coordinator"],
    )
    request_result = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "e2e_gate_evaluation_request",
            "payload_bytes": canonical_json_bytes(request.model_dump(mode="json")),
            "parent_artifact_ids": sorted(
                reference.artifact_id
                for reference in (
                    run_manifest,
                    candidate_validation_freeze,
                    candidate_bundle,
                    task_contract,
                    decision_process_spec,
                    environment_binding,
                )
            ),
            "created_by": "principal_coordinator",
            "created_at": _ISSUED_AT,
            "source_evidence_ids": [],
        }
    )
    request_ref = ArtifactReference.model_validate(
        _artifact_ref(request_result), strict=True
    )
    gates = (
        (True, True, True, True)
        if state == "TRAINING_SMOKE_TESTING"
        else (False, True, True, True)
    )
    verdict = sign_e2e_verdict(
        {
            "schema_version": "automarkov.e2e-gate-verdict.v1",
            "signing_domain": "AutoMarkov-E2E-Gate-Verdict-v1",
            "verdict_id": f"verdict_repository_e2e_{state.lower()}",
            "request_id": request.request_id,
            "request_payload_hash": request_ref.payload_hash,
            "run_id": request.run_id,
            "run_manifest": run_manifest,
            "candidate_bundle": candidate_bundle,
            "task_contract": task_contract,
            "decision_process_spec": decision_process_spec,
            "environment_binding": environment_binding,
            "text_passed": gates[0],
            "formal_passed": gates[1],
            "api_passed": gates[2],
            "hidden_behavior_passed": gates[3],
            "evaluator_principal_id": "principal_evaluator",
            "evaluator_key_id": "key_evaluator",
            "issued_at": _ISSUED_AT,
            "nonce_b64url": base64.urlsafe_b64encode(bytes(range(32, 64)))
            .decode()
            .rstrip("="),
            "signature_algorithm": "Ed25519",
        },
        _E2E_SIGNING_KEYS["evaluator"],
    )
    verdict_result = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "e2e_gate_verdict",
            "payload_bytes": canonical_json_bytes(verdict.model_dump(mode="json")),
            "parent_artifact_ids": sorted(
                reference.artifact_id
                for reference in (
                    run_manifest,
                    candidate_bundle,
                    task_contract,
                    decision_process_spec,
                    environment_binding,
                )
            ),
            "created_by": "principal_evaluator",
            "created_at": _ISSUED_AT,
            "source_evidence_ids": [],
        }
    )
    verdict_ref = ArtifactReference.model_validate(
        _artifact_ref(verdict_result), strict=True
    )
    topology_factory = cast(
        Any,
        runpy.run_path(str(Path(__file__).with_name("test_e2e_gate_protocol.py")))[
            "_case"
        ],
    )
    topology = topology_factory(
        request=request,
        request_ref=request_ref,
        verdict=verdict,
        verdict_ref=verdict_ref,
    )[4]
    topology_result = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "sealed_worker_topology",
            "payload_bytes": canonical_json_bytes(topology.model_dump(mode="json")),
            "parent_artifact_ids": sorted(
                reference.artifact_id
                for reference in (
                    request_ref,
                    candidate_bundle,
                    task_contract,
                    decision_process_spec,
                    environment_binding,
                )
            ),
            "created_by": "principal_evaluator",
            "created_at": _ISSUED_AT,
            "source_evidence_ids": [],
        }
    )
    return (
        request_ref,
        verdict_ref,
        candidate_bundle,
        ArtifactReference.model_validate(_artifact_ref(topology_result), strict=True),
    )


def _e2e_lifecycle_command(
    artifacts: Mapping[str, Any],
    sealed: LifecycleCommitReceipt,
    state: Literal["TRAINING_SMOKE_TESTING", "PARTIAL", "FAILED"],
    *,
    request_ref: ArtifactReference,
    verdict_ref: ArtifactReference,
    candidate_ref: ArtifactReference,
    topology_ref: ArtifactReference,
    runner_fingerprint: str | None = None,
    process_reference: ArtifactReference | None = None,
) -> E2EGateCommitCommand:
    return E2EGateCommitCommand(
        schema_version="automarkov.e2e-gate-commit-command.v1",
        request_ref=request_ref,
        verdict_ref=verdict_ref,
        request_id=f"request_repository_e2e_{state.lower()}",
        verdict_id=f"verdict_repository_e2e_{state.lower()}",
        request_nonce_b64url=base64.urlsafe_b64encode(bytes(range(32)))
        .decode()
        .rstrip("="),
        verdict_nonce_b64url=base64.urlsafe_b64encode(bytes(range(32, 64)))
        .decode()
        .rstrip("="),
        coordinator_key_id="key_repository_e2e_coordinator",
        evaluator_key_id="key_repository_e2e_evaluator",
        run_id=_RUN_ID,
        run_manifest=ArtifactReference.model_validate(
            _artifact_ref(artifacts["run_manifest"]), strict=True
        ),
        specified_event_head=VerifiedEventHead(
            run_id=RunId(root=_RUN_ID),
            sequence_no=sealed.after_head.sequence_no,
            event_hash=Sha256Digest(root=sealed.after_head.event_hash),
        ),
        candidate_bundle=candidate_ref,
        topology_ref=topology_ref,
        request_payload_hash=request_ref.payload_hash,
        verdict_payload_hash=verdict_ref.payload_hash,
        topology_payload_hash=topology_ref.payload_hash,
        runner_fingerprint=runner_fingerprint,
        process_execution_terminal_record=process_reference,
        decision=SealedE2EGate._decision(state),
        committed_at=_ISSUED_AT,
    )


def _checkpoint_e2e_runner(
    repository: ArtifactRepositoryAdapter,
    artifacts: Mapping[str, Any],
    verdict_ref: ArtifactReference,
) -> tuple[
    str,
    RunnerExecutionCheckpoint,
    ArtifactRepositoryRunnerCheckpointFinalizer,
]:
    verdict_output_payload = RunnerArtifactReferencePayload(
        schema_version="automarkov.runner-artifact-reference-output.v1",
        artifact_type="e2e_gate_verdict",
        artifact=verdict_ref,
    )
    verdict_output_bytes = canonical_json_bytes(
        verdict_output_payload.model_dump(mode="json")
    )
    verdict_output = RunnerOutputBinding(
        schema_version="automarkov.runner-output-binding.v2",
        path="artifact-reference.json",
        byte_size=len(verdict_output_bytes),
        media_type="application/json",
        content_hash="sha256:" + hashlib.sha256(verdict_output_bytes).hexdigest(),
        content_schema_version=("automarkov.runner-artifact-reference-output.v1"),
        content_b64url=base64.urlsafe_b64encode(verdict_output_bytes)
        .decode()
        .rstrip("="),
        schema_valid=True,
    )
    verdict_output_result = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "runner_output_binding",
            "payload_bytes": canonical_json_bytes(
                verdict_output.model_dump(mode="json")
            ),
            "parent_artifact_ids": [],
            "created_by": "principal_fixed_commit_runner",
            "created_at": _ISSUED_AT,
            "source_evidence_ids": [],
        }
    )
    verdict_output_ref = ArtifactReference.model_validate(
        _artifact_ref(verdict_output_result), strict=True
    )
    output_scan_report = ArtifactReference.model_validate(
        _artifact_ref(_put(repository, "output_scan_report", "e2e-output-scan")),
        strict=True,
    )
    control_refs = tuple(
        ArtifactReference(
            artifact_id="artifact_" + character * 64,
            payload_hash="sha256:" + character * 64,
        )
        for character in "12345"
    )
    process_payload = _process_terminal_record(artifacts)
    process_payload |= {
        "experiment_id": "experiment_runner_graph",
        "job_id": "job_runner_graph",
        "profile_id": "runner-control",
        "status": "success",
        "exit_code": 0,
        "reason_code": "fixed_commit_completed",
        "network_log_hash": control_refs[0].payload_hash,
        "mount_attestation_hash": control_refs[1].payload_hash,
        "capability_decision_hash": control_refs[2].payload_hash,
        "egress_log_hash": control_refs[3].payload_hash,
        "payload_outputs": [verdict_output_ref.model_dump(mode="json")],
    }
    process = ProcessExecutionTerminalRecord.model_validate(
        process_payload,
        strict=True,
    )
    evidence = RawExecutionEvidence(
        schema_version="automarkov.raw-execution-evidence.v1",
        job_id=process.job_id,
        process_execution_id=process.process_execution_id,
        source_commit="a" * 40,
        profile_id=process.profile_id,
        image_digest=_HASH_B,
        status="success",
        exit_code=0,
        reason_code="fixed_commit_completed",
        started_at=process.started_at,
        finished_at=process.finished_at,
        stdout_hash=process.stdout_hash,
        stderr_hash=process.stderr_hash,
        payload_outputs=process.payload_outputs,
        resource_usage=process.resource_usage,
        network_log=control_refs[0],
        mount_attestation=control_refs[1],
        capability_decision_log=control_refs[2],
        egress_decision_log=control_refs[3],
        output_scan_report=output_scan_report,
        egress_revoked_at=process.finished_at,
    )
    fingerprint = "sha256:" + "9" * 64
    store = ArtifactRepositoryRunnerStore(repository)
    assert (
        store.reserve(
            process.job_id,
            process.process_execution_id,
            fingerprint,
        )
        is None
    )
    checkpoint = store.checkpoint(
        process.job_id,
        fingerprint,
        RunnerExecutionCheckpoint(
            schema_version="automarkov.runner-execution-checkpoint.v1",
            process=process,
            process_reference=runner_artifact_reference(
                "process_execution_terminal_record",
                process,
            ),
            evidence=evidence,
        ),
    )
    return (
        fingerprint,
        checkpoint,
        ArtifactRepositoryRunnerCheckpointFinalizer(
            repository,
            signing_key_id="key_runner_graph",
            signing_key=_RUNNER_SIGNING_KEY,
        ),
    )


@pytest.mark.parametrize("adapter_name", ("memory", "sqlite"))
@pytest.mark.parametrize("state", ("TRAINING_SMOKE_TESTING", "PARTIAL", "FAILED"))
def test_e2e_lifecycle_materializer_uses_real_repository_transaction_and_rolls_back(
    adapter_name: str,
    state: Literal["TRAINING_SMOKE_TESTING", "PARTIAL", "FAILED"],
    tmp_path: Path,
) -> None:
    database_path = tmp_path / f"e2e-lifecycle-{adapter_name}.sqlite"
    repository = _open_lifecycle_repository(adapter_name, database_path)
    try:
        artifacts, root = _start_repository_authorized_runner_run(repository)
        researching = _advance_to_researching(
            repository,
            artifacts,
            root,
            experiment_id="experiment_runner_graph",
        )
        sealed = _advance_to_sealed_e2e(
            repository,
            artifacts,
            researching,
            experiment_id="experiment_runner_graph",
        )
        request_ref, verdict_ref, candidate_ref, topology_ref = (
            _put_e2e_request_and_verdict(
                repository,
                artifacts,
                sealed,
                state,
            )
        )
        runner_fingerprint, checkpoint, runner_finalizer = _checkpoint_e2e_runner(
            repository, artifacts, verdict_ref
        )
        process_reference = checkpoint.process_reference
        command = _e2e_lifecycle_command(
            artifacts,
            sealed,
            state,
            request_ref=request_ref,
            verdict_ref=verdict_ref,
            candidate_ref=candidate_ref,
            topology_ref=topology_ref,
            runner_fingerprint=runner_fingerprint,
            process_reference=process_reference,
        )
        config = E2EGateLifecyclePlanConfig(
            actor_principal_id="principal_fixed_commit_runner",
            process_execution_id="execution_lifecycle_terminal",
            budget_snapshot=ArtifactReference.model_validate(
                _artifact_ref(artifacts["budget"]), strict=True
            ),
            runner_fingerprint=runner_fingerprint,
            process_execution_terminal_record=process_reference,
        )
        before = _repository_storage_snapshot(repository)
        failing = ArtifactLifecycleE2EGateCommitter(
            ArtifactRepositoryE2EGateLifecycleMaterializer(
                repository,
                ArtifactRepositoryE2EGateLifecyclePlan(
                    repository,
                    config,
                    lambda _principal, _process, issued_at: _COMMAND_AUTHORITY.issue(
                        "principal_lifecycle_fixture", None, issued_at
                    ),
                    runner_finalizer,
                ),
            )
        )
        with pytest.raises(CommandAuthenticationError):
            failing.commit(command)
        assert _repository_storage_snapshot(repository) == before

        synthesized = command.model_copy(
            update={
                "process_execution_terminal_record": ArtifactReference.model_validate(
                    _artifact_ref(artifacts["candidate_output"]), strict=True
                )
            }
        )
        with pytest.raises((KeyError, RunnerReplayError)):
            ArtifactRepositoryE2EGateLifecyclePlan(
                repository,
                config,
                lambda principal, process, issued_at: _COMMAND_AUTHORITY.issue(
                    principal, process, issued_at
                ),
                runner_finalizer,
            ).plan(synthesized)

        wrong_verdict = ArtifactReference.model_validate(
            _artifact_ref(artifacts["output"]), strict=True
        )
        missing_verdict_output = command.model_copy(
            update={
                "verdict_ref": wrong_verdict,
                "verdict_payload_hash": wrong_verdict.payload_hash,
            }
        )
        with pytest.raises(
            RunnerReplayError,
            match="verdict artifact identity|checkpoint graph",
        ):
            ArtifactRepositoryE2EGateLifecyclePlan(
                repository,
                config,
                lambda principal, process, issued_at: _COMMAND_AUTHORITY.issue(
                    principal, process, issued_at
                ),
                runner_finalizer,
            ).plan(missing_verdict_output)

        invalid_finalizer = ArtifactRepositoryRunnerCheckpointFinalizer(
            repository,
            signing_key_id="key_runner_graph",
            signing_key=Ed25519PrivateKey.from_private_bytes(b"\x37" * 32),
        )
        with pytest.raises(RunnerReplayError):
            ArtifactLifecycleE2EGateCommitter(
                ArtifactRepositoryE2EGateLifecycleMaterializer(
                    repository,
                    ArtifactRepositoryE2EGateLifecyclePlan(
                        repository,
                        config,
                        lambda principal, process, issued_at: _COMMAND_AUTHORITY.issue(
                            principal,
                            process,
                            issued_at,
                        ),
                        invalid_finalizer,
                    ),
                )
            ).commit(command)
        assert _repository_storage_snapshot(repository) == before

        committer = ArtifactLifecycleE2EGateCommitter(
            ArtifactRepositoryE2EGateLifecycleMaterializer(
                repository,
                ArtifactRepositoryE2EGateLifecyclePlan(
                    repository,
                    config,
                    lambda principal, process, issued_at: _COMMAND_AUTHORITY.issue(
                        principal, process, issued_at
                    ),
                    runner_finalizer,
                ),
            )
        )
        committed_command = command
        if state == "FAILED":
            bad_request = command.request_ref.model_copy(
                update={"payload_hash": _HASH_B}
            )
            bad_verdict = command.verdict_ref.model_copy(
                update={"payload_hash": _HASH_B}
            )
            bad_topology = command.topology_ref.model_copy(
                update={"payload_hash": _HASH_B}
            )
            committed_command = command.model_copy(
                update={
                    "request_ref": bad_request,
                    "request_payload_hash": bad_request.payload_hash,
                    "verdict_ref": bad_verdict,
                    "verdict_payload_hash": bad_verdict.payload_hash,
                    "topology_ref": bad_topology,
                    "topology_payload_hash": bad_topology.payload_hash,
                }
            )
        first = committer.commit(committed_command)
        assert first.materialization_backend == "artifact_lifecycle"
        assert first.next_state == state
        assert first.outcome_e2e_valid == (
            1 if state == "TRAINING_SMOKE_TESTING" else 0
        )
        assert first.training_outcome_missing is (state != "TRAINING_SMOKE_TESTING")
        assert first.process_execution_terminal_record == process_reference
        assert first.execution_attestation is not None
        if state == "TRAINING_SMOKE_TESTING":
            assert first.terminal_result is None
        else:
            assert first.terminal_result is not None
        persisted_process = repository.get(
            ArtifactId(root=process_reference.artifact_id)
        )
        process = ProcessExecutionTerminalRecord.model_validate(
            persisted_process.payload_document.model_dump(mode="json")["payload"],
            strict=True,
        )
        assert process.status == "success"
        assert process.exit_code == 0
        assert len(process.payload_outputs) == 1
        stored_output = repository.get(
            ArtifactId(root=process.payload_outputs[0].artifact_id)
        )
        output_binding = RunnerOutputBinding.model_validate(
            stored_output.payload_document.model_dump(mode="json")["payload"],
            strict=True,
        )
        assert (
            RunnerArtifactReferencePayload.model_validate_json(
                output_binding.verified_content_bytes(), strict=True
            ).artifact
            == command.verdict_ref
        )
        assert (
            ArtifactRepositoryRunnerStore(repository)
            .execution_attestation(first.execution_attestation)
            .terminal_result
            == first.terminal_result
        )
        assert committer.commit(committed_command) == first
        assert (
            committer.commit(
                committed_command.model_copy(
                    update={"committed_at": "2026-08-10T11:00:01Z"}
                )
            )
            == first
        )
        if isinstance(repository, InMemoryArtifactRepository):
            fingerprint, stored_materialization = next(
                iter(repository._e2e_gate_materializations.items())
            )
            stored_command_bytes, stored_receipt_bytes = stored_materialization
        else:
            row = repository._connection.execute(
                "SELECT command_fingerprint, command_bytes, receipt_bytes "
                "FROM e2e_gate_materializations"
            ).fetchone()
            assert row is not None
            fingerprint = cast(str, row[0])
            stored_command_bytes = bytes(row[1])
            stored_receipt_bytes = bytes(row[2])
        stored_receipt = ArtifactLifecycleAtomicReceipt.model_validate_json(
            stored_receipt_bytes, strict=True
        )
        tampered_receipt_bytes = canonical_json_bytes(
            stored_receipt.model_copy(
                update={"lifecycle_after_head_hash": _HASH_B}
            ).model_dump(mode="json")
        )
        if isinstance(repository, InMemoryArtifactRepository):
            repository._e2e_gate_materializations[fingerprint] = (
                stored_command_bytes,
                tampered_receipt_bytes,
            )
        else:
            repository._connection.execute(
                "UPDATE e2e_gate_materializations SET receipt_bytes = ? "
                "WHERE command_fingerprint = ?",
                (tampered_receipt_bytes, fingerprint),
            )
            repository._connection.commit()
        with pytest.raises(ArtifactIntegrityError):
            committer.commit(committed_command)
        if isinstance(repository, InMemoryArtifactRepository):
            repository._e2e_gate_materializations[fingerprint] = (
                stored_command_bytes,
                stored_receipt_bytes,
            )
        else:
            repository._connection.execute(
                "UPDATE e2e_gate_materializations SET receipt_bytes = ? "
                "WHERE command_fingerprint = ?",
                (stored_receipt_bytes, fingerprint),
            )
            repository._connection.commit()
        records = repository._load_run_event_records(_RUN_ID)
        assert _project(
            repository,
            sequence_no=records[-1].event.sequence_no,
            event_head_hash=records[-1].event_hash,
        ).state is RunState(state)

        if isinstance(repository, SqliteArtifactRepository):
            repository.close()
            repository = _open_lifecycle_repository(adapter_name, database_path)
            restarted = ArtifactLifecycleE2EGateCommitter(
                ArtifactRepositoryE2EGateLifecycleMaterializer(
                    repository,
                    ArtifactRepositoryE2EGateLifecyclePlan(
                        repository,
                        config,
                        lambda principal, process, issued_at: _COMMAND_AUTHORITY.issue(
                            principal, process, issued_at
                        ),
                        ArtifactRepositoryRunnerCheckpointFinalizer(
                            repository,
                            signing_key_id="key_runner_graph",
                            signing_key=_RUNNER_SIGNING_KEY,
                        ),
                    ),
                )
            )
            assert restarted.commit(committed_command) == first
    finally:
        if isinstance(repository, SqliteArtifactRepository):
            repository.close()


def test_runner_resolver_recomputes_real_repository_identity_at_verified_head(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    artifacts, root = _start_run(lifecycle_repository)
    researching = _advance_to_researching(lifecycle_repository, artifacts, root)
    head = VerifiedEventHead(
        run_id=RunId(root=_RUN_ID),
        sequence_no=researching.event_record.event.sequence_no,
        event_hash=Sha256Digest(root=researching.event_record.event_hash),
    )
    reference = ArtifactReference.model_validate(
        _artifact_ref(artifacts["output"]), strict=True
    )

    resolved = ArtifactRepositoryTrustedRunnerArtifactResolver(
        lifecycle_repository, head
    ).resolve(head, reference)

    assert resolved.reference == reference
    assert resolved.artifact_type == "payload_output"
    assert resolved.payload_schema_version == "automarkov.lifecycle-fixture.v1"
    assert resolved.parent_artifact_ids == ()
    assert resolved.identity_scheme == "artifact_repository_v2"


@pytest.mark.parametrize("adapter_name", ("memory", "sqlite"))
def test_default_runner_input_requires_its_source_as_an_existing_dag_parent(
    adapter_name: str,
    tmp_path: Path,
) -> None:
    source_request = {
        "schema_version": "automarkov.artifact-put-request.v2",
        "artifact_type": "task_request",
        "payload_bytes": canonical_json_bytes(
            {
                "schema_version": "automarkov.task-request.v1",
                "request_id": "request_runner_input_source",
                "task_text": "Model a finite-horizon inventory process.",
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
            }
        ),
        "parent_artifact_ids": [],
        "created_by": "principal_lifecycle_fixture",
        "created_at": _ISSUED_AT,
        "source_evidence_ids": [],
    }
    source = InMemoryArtifactRepository().put(source_request)
    source_reference = ArtifactReference(
        artifact_id=source.artifact_id.root,
        payload_hash=source.payload_hash.root,
    )
    runner_input = RunnerInput(
        schema_version="automarkov.runner-input.v1",
        input_index=0,
        source_artifact=source_reference,
        source_artifact_type="task_request",
        source_commitment=source_reference.payload_hash,
    )
    runner_input_request: dict[str, object] = {
        "schema_version": "automarkov.artifact-put-request.v2",
        "artifact_type": "runner_input",
        "payload_bytes": canonical_json_bytes(runner_input.model_dump(mode="json")),
        "parent_artifact_ids": [],
        "created_by": "principal_lifecycle_fixture",
        "created_at": _ISSUED_AT,
        "source_evidence_ids": [],
    }
    repository: ArtifactRepositoryAdapter = (
        InMemoryArtifactRepository()
        if adapter_name == "memory"
        else SqliteArtifactRepository(tmp_path / "runner-input-dag.sqlite")
    )
    try:
        with pytest.raises(ArtifactParentContractError):
            repository.put(runner_input_request)

        persisted_source = repository.put(source_request)
        assert persisted_source.artifact_id == source.artifact_id
        stored_input = repository.put(
            runner_input_request
            | {"parent_artifact_ids": [source_reference.artifact_id]}
        )
        assert repository.get(
            ArtifactId(root=stored_input.artifact_id.root)
        ).envelope.parent_artifact_ids == (source.artifact_id,)
    finally:
        if isinstance(repository, SqliteArtifactRepository):
            repository.close()


def _put_repository_authorized_job(
    repository: ArtifactRepositoryAdapter,
    *,
    input_artifacts: tuple[ArtifactReference, ...] | None = None,
) -> tuple[Any, Any]:
    governance = _put(repository, "governance_report", "runner-graph")
    governance_ref = _artifact_ref(governance)
    policy_references = {
        field: ArtifactReference.model_validate(
            _artifact_ref(_put(repository, "governance_report", f"runner-{field}")),
            strict=True,
        )
        for field in (
            "capability_policy",
            "mount_policy",
            "network_policy",
            "output_contract",
            "resource_limits",
            "scanner_policy",
        )
    }

    def put_runtime_profile(profile_id: str) -> ArtifactReference:
        evidence_references: dict[str, ArtifactReference] = {}
        attestation_references: dict[str, ArtifactReference] = {}
        for kind in ("build", "import_smoke"):
            evidence = RunnerRuntimeEvidence(
                schema_version="automarkov.runner-runtime-evidence.v1",
                evidence_kind=kind,
                image_digest=_HASH_B,
            )
            evidence_result = repository.put(
                {
                    "schema_version": "automarkov.artifact-put-request.v2",
                    "artifact_type": "runner_runtime_evidence",
                    "payload_bytes": canonical_json_bytes(
                        evidence.model_dump(mode="json")
                    ),
                    "parent_artifact_ids": [],
                    "created_by": "principal_lifecycle_fixture",
                    "created_at": _ISSUED_AT,
                    "source_evidence_ids": [],
                }
            )
            evidence_reference = ArtifactReference.model_validate(
                _artifact_ref(evidence_result), strict=True
            )
            evidence_references[kind] = evidence_reference
            attestation = _sign_runtime_attestation(
                {
                    "schema_version": "automarkov.runner-runtime-attestation.v1",
                    "signing_domain": "AutoMarkov-Runner-Runtime-Attestation-v1",
                    "attestation_kind": kind,
                    "issuer_id": "issuer_runner_fixture",
                    "signing_key_id": "key_runner_fixture",
                    "profile_id": profile_id,
                    "image_digest": _HASH_B,
                    "observed_at": _ISSUED_AT,
                    "nonce_b64url": base64.urlsafe_b64encode(
                        hashlib.sha256(f"{profile_id}:{kind}".encode()).digest()[:16]
                    )
                    .decode()
                    .rstrip("="),
                    "evidence_refs": (evidence_reference,),
                    "signature_algorithm": "Ed25519",
                },
                _RUNNER_SIGNING_KEY,
            )
            attestation_result = repository.put(
                {
                    "schema_version": "automarkov.artifact-put-request.v2",
                    "artifact_type": "runner_runtime_attestation",
                    "payload_bytes": canonical_json_bytes(
                        attestation.model_dump(mode="json")
                    ),
                    "parent_artifact_ids": [evidence_reference.artifact_id],
                    "created_by": "principal_lifecycle_fixture",
                    "created_at": _ISSUED_AT,
                    "source_evidence_ids": [],
                }
            )
            attestation_references[kind] = ArtifactReference.model_validate(
                _artifact_ref(attestation_result), strict=True
            )
        profile = RuntimeProfileManifest.model_validate(
            {
                "schema_version": "automarkov.runtime-profile-manifest.v2",
                "profile_id": profile_id,
                "python_version": "3.11.13",
                "lockfile_path": "uv.lock",
                "lock_hash": _HASH_A,
                "containerfile_path": "Containerfile",
                "build_context_files": [
                    ".dockerignore",
                    "Containerfile",
                    "pyproject.toml",
                    "uv.lock",
                ],
                "build_context_hash": "sha256:" + "9" * 64,
                "target_platform": "linux/amd64",
                "image_status": "built",
                "image_digest": _HASH_B,
                "platform": "linux/amd64",
                "libc_version": "glibc-2.36",
                "openssl_version": "OpenSSL-3.0.17",
                "ca_bundle_hash": "sha256:" + "8" * 64,
                "build_attestation_id": attestation_references["build"].artifact_id,
                "build_attestation_hash": attestation_references["build"].payload_hash,
                "import_smoke_attestation_id": attestation_references[
                    "import_smoke"
                ].artifact_id,
                "import_smoke_attestation_hash": attestation_references[
                    "import_smoke"
                ].payload_hash,
                "sbom_path": "sbom.spdx.json",
                "sbom_hash": "sha256:" + "7" * 64,
                "license_manifest_path": "license-manifest.json",
                "license_manifest_hash": "sha256:" + "6" * 64,
                "smoke_contract_path": "smoke.json",
                "smoke_contract_hash": "sha256:" + "5" * 64,
                "package_versions": {},
                "repository_commits": {},
                "dataset_revisions": {},
                "model_revisions": {},
                "hardware_contract": "cpu",
                "capabilities": [],
                "conflict_groups": [],
                "egress_allowlist": [],
                "credential_ids": ["fixed-commit-signing.v1"],
                "read_mounts": ["/mnt/automarkov/artifacts/control"],
                "write_mounts": ["/mnt/automarkov/artifacts/attestations"],
                "protocol_edges": ["FixedCommitRunner", "RemoteEnv"],
                "restricted": False,
                "build_enabled": True,
                "publishable": True,
            },
            strict=True,
        )
        result = repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": "runtime_profile_manifest",
                "payload_bytes": canonical_json_bytes(
                    profile.model_dump(mode="json", exclude_computed_fields=True)
                ),
                "parent_artifact_ids": sorted(
                    reference.artifact_id
                    for reference in attestation_references.values()
                ),
                "created_by": "principal_lifecycle_fixture",
                "created_at": _ISSUED_AT,
                "source_evidence_ids": [],
            }
        )
        return ArtifactReference.model_validate(_artifact_ref(result), strict=True)

    profile_references = {
        profile_id: put_runtime_profile(profile_id)
        for profile_id in (
            "profile-candidate-sealed",
            "profile-comparator-sealed",
            "profile-gold-sealed",
            "runner-control",
        )
    }
    if input_artifacts is None:
        input_source_result = _put(repository, "payload_output", "runner-input")
        input_source = ArtifactReference.model_validate(
            _artifact_ref(input_source_result), strict=True
        )
        input_value = RunnerInput(
            schema_version="automarkov.runner-input.v1",
            input_index=0,
            source_artifact=input_source,
            source_artifact_type="payload_output",
            source_commitment=input_source.payload_hash,
        )
        input_result = repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": "runner_input",
                "payload_bytes": canonical_json_bytes(
                    input_value.model_dump(mode="json")
                ),
                "parent_artifact_ids": [input_source.artifact_id],
                "created_by": "principal_lifecycle_fixture",
                "created_at": _ISSUED_AT,
                "source_evidence_ids": [],
            }
        )
        resolved_inputs = (
            ArtifactReference.model_validate(_artifact_ref(input_result), strict=True),
        )
    else:
        resolved_inputs = input_artifacts
    bootstrap = _put_run_manifest(repository, governance)
    bootstrap_payload = repository.get(
        bootstrap.artifact_id
    ).payload_document.model_dump(mode="json")["payload"]
    assert type(bootstrap_payload) is dict
    security_context = cast(dict[str, object], bootstrap_payload)[
        "event_security_context"
    ]
    assert type(security_context) is dict
    security_context = dict(cast(dict[str, object], security_context))
    security_context["experiment_id"] = "experiment_runner_graph"
    runner_public_key = _RUNNER_SIGNING_KEY.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    runner_key_grant = {
        "signing_key_id": "key_runner_graph",
        "principal_id": "principal_fixed_commit_runner",
        "signature_algorithm": "Ed25519",
        "public_key_b64url": base64.urlsafe_b64encode(runner_public_key)
        .decode()
        .rstrip("="),
        "not_before": "2026-08-09T00:00:00Z",
        "not_after": "2026-08-13T00:00:00Z",
        "revoked_at": None,
    }
    job_payload: dict[str, object] = {
        "schema_version": "automarkov.fixed-commit-job-manifest.v1",
        "job_id": "job_runner_graph",
        "process_execution_id": "execution_lifecycle_terminal",
        "experiment_id": "experiment_runner_graph",
        "run_id": _RUN_ID,
        "principal_id": "principal_fixed_commit_runner",
        "repository_url": "https://github.com/example/benchmark.git",
        "source_commit": "a" * 40,
        "profile_manifest": profile_references["runner-control"].model_dump(
            mode="json"
        ),
        "profile_id": "runner-control",
        "profile_lock_hash": _HASH_A,
        "target_platform": "linux/amd64",
        "image_digest": _HASH_B,
        "input_artifacts": [item.model_dump(mode="json") for item in resolved_inputs],
        "suite_id": "suite_taxi",
        "variant_id": "variant_0",
        "track_id": "track_public",
        "method_id": "method_automarkov",
        "pair_id": "pair_0",
        "generation_seed": 17,
        "rl_seed": 23,
        "phase": "training",
        "argv": ["/opt/venv/bin/python", "-m", "automarkov.worker"],
        "working_directory": "checkout",
        "resource_limits": policy_references["resource_limits"].model_dump(mode="json"),
        "network_policy": policy_references["network_policy"].model_dump(mode="json"),
        "mount_policy": policy_references["mount_policy"].model_dump(mode="json"),
        "capability_policy": policy_references["capability_policy"].model_dump(
            mode="json"
        ),
        "output_contract": policy_references["output_contract"].model_dump(mode="json"),
        "scanner_policy": policy_references["scanner_policy"].model_dump(mode="json"),
        "from_phase": "TRAINING_SMOKE_TESTING",
        "to_phase": "POLICY_TRAINING",
        "launch_deadline": "2026-08-12T12:00:00Z",
    }
    job = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "fixed_commit_job_manifest",
            "payload_bytes": canonical_json_bytes(job_payload),
            "parent_artifact_ids": sorted(
                {
                    *(item.artifact_id for item in policy_references.values()),
                    profile_references["runner-control"].artifact_id,
                    *(item.artifact_id for item in resolved_inputs),
                }
            ),
            "created_by": "principal_lifecycle_fixture",
            "created_at": _ISSUED_AT,
            "source_evidence_ids": [],
        }
    )
    job_model = FixedCommitJobManifest.model_validate(job_payload, strict=True)
    authorization = FixedCommitRunAuthorization(
        schema_version="automarkov.fixed-commit-run-authorization.v1",
        job_manifest=ArtifactReference.model_validate(_artifact_ref(job), strict=True),
        repository_url=job_model.repository_url,
        source_commit=job_model.source_commit,
        profile_manifest=job_model.profile_manifest,
        profile_id=job_model.profile_id,
        image_digest=job_model.image_digest,
        input_artifacts=job_model.input_artifacts,
        resource_limits=job_model.resource_limits,
        network_policy=job_model.network_policy,
        mount_policy=job_model.mount_policy,
        capability_policy=job_model.capability_policy,
        output_contract=job_model.output_contract,
        scanner_policy=job_model.scanner_policy,
        suite_id=job_model.suite_id,
        variant_id=job_model.variant_id,
        track_id=job_model.track_id,
        method_id=job_model.method_id,
        pair_id=job_model.pair_id,
        generation_seed=job_model.generation_seed,
        rl_seed=job_model.rl_seed,
        phase=job_model.phase,
        argv=job_model.argv,
        working_directory=job_model.working_directory,
        from_phase=job_model.from_phase,
        to_phase=job_model.to_phase,
        launch_deadline=job_model.launch_deadline,
        runner_key_grant=ManifestEventSigningKey.model_validate(
            runner_key_grant,
            strict=True,
        ),
    )
    authorization_result = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "fixed_commit_run_authorization",
            "payload_bytes": canonical_json_bytes(
                authorization.model_dump(mode="json")
            ),
            "parent_artifact_ids": sorted(
                [governance.artifact_id.root, job.artifact_id.root]
            ),
            "created_by": "principal_lifecycle_fixture",
            "created_at": _ISSUED_AT,
            "source_evidence_ids": [],
        }
    )
    sealed_worker_authorizations: list[dict[str, object]] = []
    sealed_worker_parent_ids: list[str] = []
    sealed_worker_grants: list[dict[str, object]] = []
    for role in ("candidate", "comparator", "gold"):
        worker_job_payload = job_payload | {
            "job_id": f"job_{role}_001",
            "process_execution_id": f"process_{role}_001",
            "principal_id": f"principal_{role}",
            "profile_id": f"profile-{role}-sealed",
            "profile_manifest": profile_references[f"profile-{role}-sealed"].model_dump(
                mode="json"
            ),
        }
        worker_job = repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": "fixed_commit_job_manifest",
                "payload_bytes": canonical_json_bytes(worker_job_payload),
                "parent_artifact_ids": sorted(
                    {
                        *(item.artifact_id for item in policy_references.values()),
                        profile_references[f"profile-{role}-sealed"].artifact_id,
                        *(item.artifact_id for item in resolved_inputs),
                    }
                ),
                "created_by": "principal_lifecycle_fixture",
                "created_at": _ISSUED_AT,
                "source_evidence_ids": [],
            }
        )
        worker_public_key = (
            _SEALED_RUNNER_KEYS[role]
            .public_key()
            .public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        )
        worker_grant = {
            "signing_key_id": f"key_runner_{role}",
            "principal_id": f"principal_{role}",
            "signature_algorithm": "Ed25519",
            "public_key_b64url": base64.urlsafe_b64encode(worker_public_key)
            .decode()
            .rstrip("="),
            "not_before": "2026-08-09T00:00:00Z",
            "not_after": "2026-08-13T00:00:00Z",
            "revoked_at": None,
        }
        worker_authorization = FixedCommitRunAuthorization.model_validate(
            authorization.model_dump(mode="json")
            | {
                "job_manifest": ArtifactReference.model_validate(
                    _artifact_ref(worker_job), strict=True
                ).model_dump(mode="json"),
                "profile_id": f"profile-{role}-sealed",
                "profile_manifest": profile_references[
                    f"profile-{role}-sealed"
                ].model_dump(mode="json"),
                "runner_key_grant": worker_grant,
            },
            strict=True,
        )
        worker_authorization_result = repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": "fixed_commit_run_authorization",
                "payload_bytes": canonical_json_bytes(
                    worker_authorization.model_dump(mode="json")
                ),
                "parent_artifact_ids": sorted(
                    [governance.artifact_id.root, worker_job.artifact_id.root]
                ),
                "created_by": "principal_lifecycle_fixture",
                "created_at": _ISSUED_AT,
                "source_evidence_ids": [],
            }
        )
        sealed_worker_authorizations.append(
            {
                "worker_kind": role,
                "principal_id": f"principal_{role}",
                "job_manifest": _artifact_ref(worker_job),
                "fixed_commit_authorization": _artifact_ref(
                    worker_authorization_result
                ),
            }
        )
        sealed_worker_parent_ids.extend(
            (worker_job.artifact_id.root, worker_authorization_result.artifact_id.root)
        )
        sealed_worker_grants.append(worker_grant)
    e2e_principals = {
        "candidate_worker": "principal_candidate",
        "comparator": "principal_comparator",
        "coordinator": "principal_coordinator",
        "evaluator": "principal_evaluator",
        "gold_worker": "principal_gold",
    }
    e2e_key_grants = [
        {
            "signing_key_id": f"key_{kind.removesuffix('_worker')}",
            "principal_id": e2e_principals[kind],
            "signature_algorithm": "Ed25519",
            "public_key_b64url": base64.urlsafe_b64encode(
                _E2E_SIGNING_KEYS[kind]
                .public_key()
                .public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw,
                )
            )
            .decode()
            .rstrip("="),
            "not_before": "2026-08-09T00:00:00Z",
            "not_after": "2026-08-13T00:00:00Z",
            "revoked_at": None,
        }
        for kind in (
            "candidate_worker",
            "comparator",
            "coordinator",
            "evaluator",
            "gold_worker",
        )
    ]
    security_context["signing_keys"] = sorted(
        [
            *cast(list[dict[str, object]], security_context["signing_keys"]),
            runner_key_grant,
            *sealed_worker_grants,
            *e2e_key_grants,
        ],
        key=lambda item: cast(str, item["signing_key_id"]).encode("utf-8"),
    )
    run_manifest = RunManifest.model_validate(
        {
            "schema_version": "automarkov.run-manifest.v2",
            "manifest_kind": "frozen_run",
            "run_id": _RUN_ID,
            "experiment_id": "experiment_runner_graph",
            "root_ordinal": 0,
            "task_request": governance_ref,
            "event_security_context": security_context,
            "fixed_commit_authorization": _artifact_ref(authorization_result),
            "sealed_e2e_signing_authorities": [
                {
                    "principal_kind": kind,
                    "principal_id": e2e_principals[kind],
                    "signing_key_id": f"key_{kind.removesuffix('_worker')}",
                }
                for kind in (
                    "candidate_worker",
                    "comparator",
                    "coordinator",
                    "evaluator",
                    "gold_worker",
                )
            ],
            "sealed_worker_authorizations": sealed_worker_authorizations,
            "created_at": _ISSUED_AT,
        },
        strict=True,
    )
    manifest_result = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "run_manifest",
            "payload_bytes": canonical_json_bytes(run_manifest.model_dump(mode="json")),
            "parent_artifact_ids": sorted(
                [
                    governance.artifact_id.root,
                    authorization_result.artifact_id.root,
                    *sealed_worker_parent_ids,
                ]
            ),
            "created_by": "principal_lifecycle_fixture",
            "created_at": _ISSUED_AT,
            "source_evidence_ids": [],
        }
    )
    return job, manifest_result


def _start_repository_authorized_runner_run(
    repository: ArtifactRepositoryAdapter,
) -> tuple[dict[str, Any], LifecycleCommitReceipt]:
    artifacts = _put_run_artifacts(repository)
    input_sources = tuple(
        sorted(
            (
                ArtifactReference.model_validate(
                    _artifact_ref(artifacts["output"]), strict=True
                ),
                ArtifactReference.model_validate(
                    _artifact_ref(artifacts["candidate_output"]), strict=True
                ),
            ),
            key=lambda item: item.artifact_id.encode("utf-8"),
        )
    )
    runner_inputs: list[ArtifactReference] = []
    for index, source in enumerate(input_sources):
        source_type = repository.get(
            ArtifactId(root=source.artifact_id)
        ).envelope.artifact_type
        value = RunnerInput(
            schema_version="automarkov.runner-input.v1",
            input_index=index,
            source_artifact=source,
            source_artifact_type=source_type,
            source_commitment=source.payload_hash,
        )
        result = repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": "runner_input",
                "payload_bytes": canonical_json_bytes(value.model_dump(mode="json")),
                "parent_artifact_ids": [source.artifact_id],
                "created_by": "principal_lifecycle_fixture",
                "created_at": _ISSUED_AT,
                "source_evidence_ids": [],
            }
        )
        runner_inputs.append(
            ArtifactReference.model_validate(_artifact_ref(result), strict=True)
        )
    job, run_manifest = _put_repository_authorized_job(
        repository,
        input_artifacts=tuple(
            sorted(runner_inputs, key=lambda item: item.artifact_id.encode("utf-8"))
        ),
    )
    artifacts |= {
        "run_manifest": run_manifest,
        "job_manifest": job,
    }
    root_event = _root_event(artifacts)
    root_event["experiment_id"] = "experiment_runner_graph"
    unsigned = {
        key: value for key, value in root_event.items() if key != "signature_b64url"
    }
    root_event["signature_b64url"] = (
        base64.urlsafe_b64encode(_SIGNING_KEY.sign(canonical_json_bytes(unsigned)))
        .decode()
        .rstrip("=")
    )
    return artifacts, _append_events(
        repository,
        [root_event],
        command_index=0,
        expected_state=None,
        expected_head=None,
    )


def test_repository_resolver_binds_root_manifest_authorization_graph(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    job, run_manifest = _put_repository_authorized_job(lifecycle_repository)
    root_event = _root_event({"run_manifest": run_manifest})
    root_event["experiment_id"] = "experiment_runner_graph"
    unsigned = {
        key: value for key, value in root_event.items() if key != "signature_b64url"
    }
    root_event["signature_b64url"] = (
        base64.urlsafe_b64encode(_SIGNING_KEY.sign(canonical_json_bytes(unsigned)))
        .decode()
        .rstrip("=")
    )
    root = _append_events(
        lifecycle_repository,
        [root_event],
        command_index=0,
        expected_state=None,
        expected_head=None,
    )
    head = VerifiedEventHead(
        run_id=RunId(root=_RUN_ID),
        sequence_no=0,
        event_hash=Sha256Digest(root=root.event_record.event_hash),
    )
    reference = ArtifactReference.model_validate(_artifact_ref(job), strict=True)
    resolver = ArtifactRepositoryTrustedRunnerArtifactResolver(
        lifecycle_repository, head
    )
    manifest = FixedCommitJobManifest.model_validate(
        lifecycle_repository.get(job.artifact_id).payload_document.model_dump(
            mode="json"
        )["payload"],
        strict=True,
    )

    resolver.validate_job_authorization(
        head, reference, manifest, resolver.runner_signing_key_grant(reference)
    )
    with pytest.raises(ValueError, match="projected run manifest"):
        resolver.validate_job_authorization(
            head,
            reference,
            manifest.model_copy(update={"rl_seed": 24}),
            resolver.runner_signing_key_grant(reference),
        )
    frozen_run = RunManifest.model_validate(
        lifecycle_repository.get(run_manifest.artifact_id).payload_document.model_dump(
            mode="json"
        )["payload"],
        strict=True,
    )
    mismatched_principal = frozen_run.model_dump(mode="json")
    authorities = cast(
        list[dict[str, object]],
        mismatched_principal["sealed_e2e_signing_authorities"],
    )
    next(
        authority
        for authority in authorities
        if authority["principal_kind"] == "candidate_worker"
    )["principal_id"] = "principal_unrelated_candidate_authority"
    with pytest.raises(ValidationError):
        RunManifest.model_validate(mismatched_principal, strict=True)
    candidate_job = next(
        item.job_manifest
        for item in frozen_run.sealed_worker_authorizations
        if item.worker_kind == "candidate"
    )
    candidate_manifest = FixedCommitJobManifest.model_validate(
        lifecycle_repository.get(
            ArtifactId(root=candidate_job.artifact_id)
        ).payload_document.model_dump(mode="json")["payload"],
        strict=True,
    )
    candidate_grant = resolver.runner_signing_key_grant(candidate_job)
    assert candidate_grant.signing_key_id == "key_runner_candidate"
    assert resolver.worker_kind_for_job(candidate_job) == "candidate"
    resolver.validate_job_authorization(
        head,
        candidate_job,
        candidate_manifest,
        candidate_grant,
    )


def test_repository_finalizes_sealed_worker_with_its_own_runner_authorization(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    artifacts, _ = _start_repository_authorized_runner_run(lifecycle_repository)
    run_manifest = RunManifest.model_validate(
        lifecycle_repository.get(
            artifacts["run_manifest"].artifact_id
        ).payload_document.model_dump(mode="json")["payload"],
        strict=True,
    )
    candidate = next(
        item
        for item in run_manifest.sealed_worker_authorizations
        if item.worker_kind == "candidate"
    )
    job = FixedCommitJobManifest.model_validate(
        lifecycle_repository.get(
            ArtifactId(root=candidate.job_manifest.artifact_id)
        ).payload_document.model_dump(mode="json")["payload"],
        strict=True,
    )
    controls = tuple(
        ArtifactReference(
            artifact_id="artifact_" + character * 64,
            payload_hash="sha256:" + character * 64,
        )
        for character in "12345"
    )
    output_scan_report = ArtifactReference.model_validate(
        _artifact_ref(
            _put(
                lifecycle_repository,
                "output_scan_report",
                "sealed-worker-output-scan",
            )
        ),
        strict=True,
    )
    process_payload = _process_terminal_record(artifacts)
    process_payload |= {
        "experiment_id": job.experiment_id,
        "job_id": job.job_id,
        "process_execution_id": job.process_execution_id,
        "profile_id": job.profile_id,
        "principal_id": job.principal_id,
        "job_manifest": candidate.job_manifest.model_dump(mode="json"),
        "status": "success",
        "exit_code": 0,
        "reason_code": "fixed_commit_completed",
        "network_log_hash": controls[0].payload_hash,
        "mount_attestation_hash": controls[1].payload_hash,
        "capability_decision_hash": controls[2].payload_hash,
        "egress_log_hash": controls[3].payload_hash,
    }
    process = ProcessExecutionTerminalRecord.model_validate(
        process_payload,
        strict=True,
    )
    evidence = RawExecutionEvidence(
        schema_version="automarkov.raw-execution-evidence.v1",
        job_id=process.job_id,
        process_execution_id=process.process_execution_id,
        source_commit=job.source_commit,
        profile_id=process.profile_id,
        image_digest=job.image_digest,
        status="success",
        exit_code=0,
        reason_code="fixed_commit_completed",
        started_at=process.started_at,
        finished_at=process.finished_at,
        stdout_hash=process.stdout_hash,
        stderr_hash=process.stderr_hash,
        payload_outputs=process.payload_outputs,
        resource_usage=process.resource_usage,
        network_log=controls[0],
        mount_attestation=controls[1],
        capability_decision_log=controls[2],
        egress_decision_log=controls[3],
        output_scan_report=output_scan_report,
        egress_revoked_at=process.finished_at,
    )
    fingerprint = "sha256:" + "8" * 64
    store = ArtifactRepositoryRunnerStore(lifecycle_repository)
    assert store.reserve(job.job_id, job.process_execution_id, fingerprint) is None
    checkpoint = store.checkpoint(
        job.job_id,
        fingerprint,
        RunnerExecutionCheckpoint(
            schema_version="automarkov.runner-execution-checkpoint.v1",
            process=process,
            process_reference=runner_artifact_reference(
                "process_execution_terminal_record", process
            ),
            evidence=evidence,
        ),
    )
    key = _SEALED_RUNNER_KEYS["candidate"]
    attestation = ExecutionAttestation(
        schema_version="automarkov.execution-attestation.v1",
        signing_domain="AutoMarkov-Execution-Attestation-v1",
        experiment_id=process.experiment_id,
        run_id=process.run_id,
        job_id=process.job_id,
        process_execution_id=process.process_execution_id,
        profile_id=process.profile_id,
        principal_id=process.principal_id,
        job_manifest=process.job_manifest,
        process_terminal_record=checkpoint.process_reference,
        payload_outputs=process.payload_outputs,
        output_scan_report=checkpoint.evidence.output_scan_report,
        terminal_result=None,
        network_policy_hash=job.network_policy.payload_hash,
        mount_table_hash=process.mount_attestation_hash,
        capability_decision_log_hash=process.capability_decision_hash,
        actual_phase_transition=ExecutionPhaseTransition(
            from_phase=job.from_phase,
            to_phase=job.to_phase,
            transitioned_at=process.finished_at,
        ),
        egress_decision_log_hash=process.egress_log_hash,
        egress_revoked_at=process.finished_at,
        issued_at=process.finished_at,
        nonce_b64url="A" * 22,
        signing_key_id="key_runner_candidate",
        signature_algorithm="Ed25519",
        signature_b64url="A" * 86,
    )
    attestation = attestation.model_copy(
        update={
            "signature_b64url": base64.urlsafe_b64encode(
                key.sign(execution_attestation_signing_bytes(attestation))
            )
            .decode()
            .rstrip("=")
        }
    )

    result = store.commit(
        fingerprint=fingerprint,
        process=process,
        process_reference=checkpoint.process_reference,
        attestation=attestation,
        resolved_evidence={},
    )

    assert store.execution_attestation(result.execution_attestation) == attestation


def test_e2e_runner_grant_resolver_uses_exact_root_authorization_graph(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    _, run_manifest = _put_repository_authorized_job(lifecycle_repository)
    run_manifest_ref = ArtifactReference.model_validate(
        _artifact_ref(run_manifest), strict=True
    )
    root_event = _root_event({"run_manifest": run_manifest})
    root_event["experiment_id"] = "experiment_runner_graph"
    root_event["signature_b64url"] = (
        base64.urlsafe_b64encode(
            _SIGNING_KEY.sign(
                canonical_json_bytes(
                    {
                        key: value
                        for key, value in root_event.items()
                        if key != "signature_b64url"
                    }
                )
            )
        )
        .decode()
        .rstrip("=")
    )
    root = _append_events(
        lifecycle_repository,
        [root_event],
        command_index=0,
        expected_state=None,
        expected_head=None,
    )
    head = VerifiedEventHead(
        run_id=RunId(root=_RUN_ID),
        sequence_no=root.after_head.sequence_no,
        event_hash=Sha256Digest(root=root.after_head.event_hash),
    )
    manifest = RunManifest.model_validate(
        lifecycle_repository.get(run_manifest.artifact_id).payload_document.model_dump(
            mode="json"
        )["payload"],
        strict=True,
    )
    candidate = manifest.sealed_worker_authorizations[0]
    resolver = ArtifactRepositoryE2ERunnerGrantResolver(lifecycle_repository)

    resolved = resolver.resolve(
        run_id=_RUN_ID,
        specified_event_head=head,
        run_manifest=run_manifest_ref,
        job_manifest=candidate.job_manifest,
        principal_id="principal_candidate",
    )

    assert resolved.runner_key_grant.signing_key_id == "key_runner_candidate"
    assert resolved.principal_id == "principal_candidate"
    assert resolved.profile_id == "profile-candidate-sealed"
    with pytest.raises(ValueError, match="unavailable"):
        resolver.resolve(
            run_id=_RUN_ID,
            specified_event_head=head,
            run_manifest=run_manifest_ref,
            job_manifest=manifest.sealed_worker_authorizations[2].job_manifest,
            principal_id="principal_candidate",
        )
    with pytest.raises(AutoMarkovError):
        resolver.resolve(
            run_id="run_wrong_root",
            specified_event_head=head,
            run_manifest=run_manifest_ref,
            job_manifest=candidate.job_manifest,
            principal_id="principal_candidate",
        )
    with pytest.raises(AutoMarkovError):
        resolver.resolve(
            run_id=_RUN_ID,
            specified_event_head=head.model_copy(
                update={"event_hash": Sha256Digest(root=_HASH_B)}
            ),
            run_manifest=run_manifest_ref,
            job_manifest=candidate.job_manifest,
            principal_id="principal_candidate",
        )


def test_e2e_key_policy_resolver_uses_exact_root_manifest_role(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    _, run_manifest = _put_repository_authorized_job(lifecycle_repository)
    run_manifest_ref = ArtifactReference.model_validate(
        _artifact_ref(run_manifest), strict=True
    )
    root_event = _root_event({"run_manifest": run_manifest})
    root_event["experiment_id"] = "experiment_runner_graph"
    root_event["signature_b64url"] = (
        base64.urlsafe_b64encode(
            _SIGNING_KEY.sign(
                canonical_json_bytes(
                    {
                        key: value
                        for key, value in root_event.items()
                        if key != "signature_b64url"
                    }
                )
            )
        )
        .decode()
        .rstrip("=")
    )
    root = _append_events(
        lifecycle_repository,
        [root_event],
        command_index=0,
        expected_state=None,
        expected_head=None,
    )
    head = VerifiedEventHead(
        run_id=RunId(root=_RUN_ID),
        sequence_no=root.after_head.sequence_no,
        event_hash=Sha256Digest(root=root.after_head.event_hash),
    )
    resolver = ArtifactRepositoryE2EKeyPolicyResolver(lifecycle_repository)

    policy = resolver.resolve(
        run_id=_RUN_ID,
        specified_event_head=head,
        run_manifest=run_manifest_ref,
        key_id="key_coordinator",
        principal_id="principal_coordinator",
        principal_kind="coordinator",
    )

    assert policy.public_key_b64url == base64.urlsafe_b64encode(
        _E2E_SIGNING_KEYS["coordinator"]
        .public_key()
        .public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    ).decode().rstrip("=")
    with pytest.raises(ValueError, match="unavailable"):
        resolver.resolve(
            run_id=_RUN_ID,
            specified_event_head=head,
            run_manifest=run_manifest_ref,
            key_id="key_evaluator",
            principal_id="principal_evaluator",
            principal_kind="coordinator",
        )


@pytest.mark.parametrize("adapter_name", ("memory", "sqlite"))
def test_repository_runner_store_reopens_and_finalizes_without_reexecution(
    adapter_name: str,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runner-recovery.sqlite"
    first = _open_lifecycle_repository(adapter_name, database_path)
    artifacts, root = _start_repository_authorized_runner_run(first)
    researching = _advance_to_researching(
        first, artifacts, root, experiment_id="experiment_runner_graph"
    )
    process_payload = _process_terminal_record(artifacts)
    process_payload["experiment_id"] = "experiment_runner_graph"
    process_payload["job_id"] = "job_runner_graph"
    process_payload["profile_id"] = "runner-control"
    process = ProcessExecutionTerminalRecord.model_validate(
        process_payload, strict=True
    )
    output = ArtifactReference.model_validate(
        _artifact_ref(artifacts["output"]), strict=True
    )
    resource = ArtifactReference.model_validate(
        _artifact_ref(artifacts["resource_usage"]), strict=True
    )
    refs = tuple(
        ArtifactReference(
            artifact_id="artifact_" + character * 64,
            payload_hash="sha256:" + character * 64,
        )
        for character in "12345"
    )
    output_scan_report = ArtifactReference.model_validate(
        _artifact_ref(_put(first, "output_scan_report", "runner-output-scan")),
        strict=True,
    )
    process = ProcessExecutionTerminalRecord.model_validate(
        process.model_dump(mode="json")
        | {
            "network_log_hash": refs[0].payload_hash,
            "mount_attestation_hash": refs[1].payload_hash,
            "capability_decision_hash": refs[2].payload_hash,
            "egress_log_hash": refs[3].payload_hash,
        },
        strict=True,
    )
    evidence = RawExecutionEvidence(
        schema_version="automarkov.raw-execution-evidence.v1",
        job_id=process.job_id,
        process_execution_id=process.process_execution_id,
        source_commit="a" * 40,
        profile_id=process.profile_id,
        image_digest=_HASH_A,
        status="terminal_failure",
        exit_code=1,
        reason_code="fixed_commit_failed",
        started_at=process.started_at,
        finished_at=process.finished_at,
        stdout_hash=process.stdout_hash,
        stderr_hash=process.stderr_hash,
        payload_outputs=(output,),
        resource_usage=resource,
        network_log=refs[0],
        mount_attestation=refs[1],
        capability_decision_log=refs[2],
        egress_decision_log=refs[3],
        output_scan_report=output_scan_report,
        egress_revoked_at=process.finished_at,
    )
    store = ArtifactRepositoryRunnerStore(first)
    fingerprint = "sha256:" + "9" * 64
    assert (
        store.reserve(process.job_id, process.process_execution_id, fingerprint) is None
    )
    checkpoint = store.checkpoint(
        process.job_id,
        fingerprint,
        RunnerExecutionCheckpoint(
            schema_version="automarkov.runner-execution-checkpoint.v1",
            process=process,
            process_reference=output,
            evidence=evidence,
        ),
    )
    assert store.checkpoint(process.job_id, fingerprint, checkpoint) == checkpoint
    conflicting_checkpoint = checkpoint.model_copy(
        update={
            "evidence": checkpoint.evidence.model_copy(
                update={"reason_code": "conflicting_checkpoint"}
            )
        }
    )
    with pytest.raises(RunnerReplayError, match="checkpoint"):
        store.checkpoint(process.job_id, fingerprint, conflicting_checkpoint)
    terminal_receipt, terminal = ArtifactRepositoryTerminalCommitter(
        repository=first,
        context=_COMMAND_AUTHORITY.issue(
            process.principal_id,
            process.process_execution_id,
            _ISSUED_AT,
        ),
        specified_event_head=VerifiedEventHead(
            run_id=RunId(root=_RUN_ID),
            sequence_no=researching.event_record.event.sequence_no,
            event_hash=Sha256Digest(root=researching.event_record.event_hash),
        ),
        command_builder=lambda actual: _terminal_command(
            artifacts,
            researching,
            process_record=actual.model_dump(mode="json"),
            experiment_id="experiment_runner_graph",
        ),
    ).commit_terminal(process)
    assert terminal_receipt.process_terminal_record == checkpoint.process_reference
    if isinstance(first, SqliteArtifactRepository):
        first.close()
        second: ArtifactRepositoryAdapter = _open_lifecycle_repository(
            adapter_name, database_path
        )
    else:
        second = first
    try:
        restarted = ArtifactRepositoryRunnerStore(second)
        recovered = restarted.reserve(
            process.job_id, process.process_execution_id, fingerprint
        )
        assert recovered == checkpoint
        fixed_job = FixedCommitJobManifest.model_validate(
            second.get(
                ArtifactId(root=process.job_manifest.artifact_id)
            ).payload_document.model_dump(mode="json")["payload"],
            strict=True,
        )
        attestation = ExecutionAttestation(
            schema_version="automarkov.execution-attestation.v1",
            signing_domain="AutoMarkov-Execution-Attestation-v1",
            experiment_id=process.experiment_id,
            run_id=process.run_id,
            job_id=process.job_id,
            process_execution_id=process.process_execution_id,
            profile_id=process.profile_id,
            principal_id=process.principal_id,
            job_manifest=process.job_manifest,
            process_terminal_record=checkpoint.process_reference,
            payload_outputs=process.payload_outputs,
            output_scan_report=checkpoint.evidence.output_scan_report,
            terminal_result=terminal_receipt.terminal_result,
            network_policy_hash=fixed_job.network_policy.payload_hash,
            mount_table_hash=process.mount_attestation_hash,
            capability_decision_log_hash=process.capability_decision_hash,
            actual_phase_transition=ExecutionPhaseTransition(
                from_phase="TRAINING_SMOKE_TESTING",
                to_phase="POLICY_TRAINING",
                transitioned_at=process.finished_at,
            ),
            egress_decision_log_hash=process.egress_log_hash,
            egress_revoked_at=process.finished_at,
            issued_at=process.finished_at,
            nonce_b64url="A" * 22,
            signing_key_id="key_runner_graph",
            signature_algorithm="Ed25519",
            signature_b64url="A" * 86,
        )
        attestation = attestation.model_copy(
            update={
                "signature_b64url": base64.urlsafe_b64encode(
                    _RUNNER_SIGNING_KEY.sign(
                        execution_attestation_signing_bytes(attestation)
                    )
                )
                .decode()
                .rstrip("=")
            }
        )
        invalid_finalizes = (
            (
                attestation.model_copy(update={"signature_b64url": "A" * 86}),
                checkpoint.process_reference,
                terminal,
                terminal_receipt.terminal_result,
            ),
            (
                attestation.model_copy(update={"job_id": "job_conflicting_finalize"}),
                checkpoint.process_reference,
                terminal,
                terminal_receipt.terminal_result,
            ),
            (
                attestation.model_copy(
                    update={"process_execution_id": "process_conflicting_finalize"}
                ),
                checkpoint.process_reference,
                terminal,
                terminal_receipt.terminal_result,
            ),
            (
                attestation.model_copy(update={"payload_outputs": ()}),
                checkpoint.process_reference,
                terminal,
                terminal_receipt.terminal_result,
            ),
            (
                attestation.model_copy(update={"process_terminal_record": output}),
                output,
                terminal,
                terminal_receipt.terminal_result,
            ),
            (
                attestation.model_copy(update={"terminal_result": None}),
                checkpoint.process_reference,
                None,
                None,
            ),
        )
        for (
            invalid,
            invalid_process,
            invalid_terminal,
            invalid_terminal_ref,
        ) in invalid_finalizes:
            with pytest.raises(RunnerReplayError, match="finalize"):
                restarted.commit(
                    fingerprint=fingerprint,
                    process=process,
                    process_reference=invalid_process,
                    attestation=invalid,
                    resolved_evidence={},
                    terminal_result=invalid_terminal,
                    terminal_reference=invalid_terminal_ref,
                )
        completed = restarted.commit(
            fingerprint=fingerprint,
            process=process,
            process_reference=checkpoint.process_reference,
            attestation=attestation,
            resolved_evidence={},
            terminal_result=terminal,
            terminal_reference=terminal_receipt.terminal_result,
        )
        assert restarted.replay(fingerprint) == completed
        followup_process = process.model_copy(
            update={
                "job_id": "job_runner_graph_followup",
                "process_execution_id": "execution_runner_graph_followup",
            }
        )
        followup_evidence = evidence.model_copy(
            update={
                "job_id": followup_process.job_id,
                "process_execution_id": followup_process.process_execution_id,
            }
        )
        followup_fingerprint = "sha256:" + "8" * 64
        assert (
            restarted.reserve(
                followup_process.job_id,
                followup_process.process_execution_id,
                followup_fingerprint,
            )
            is None
        )
        followup_checkpoint = restarted.checkpoint(
            followup_process.job_id,
            followup_fingerprint,
            RunnerExecutionCheckpoint(
                schema_version="automarkov.runner-execution-checkpoint.v1",
                process=followup_process,
                process_reference=output,
                evidence=followup_evidence,
            ),
        )
        followup_attestation = attestation.model_copy(
            update={
                "job_id": followup_process.job_id,
                "process_execution_id": followup_process.process_execution_id,
                "process_terminal_record": followup_checkpoint.process_reference,
                "terminal_result": None,
                "nonce_b64url": "AAECAwQFBgcICQoLDA0ODw",
            }
        )
        followup_attestation = followup_attestation.model_copy(
            update={
                "signature_b64url": base64.urlsafe_b64encode(
                    _RUNNER_SIGNING_KEY.sign(
                        execution_attestation_signing_bytes(followup_attestation)
                    )
                )
                .decode()
                .rstrip("=")
            }
        )
        followup = restarted.commit(
            fingerprint=followup_fingerprint,
            process=followup_process,
            process_reference=followup_checkpoint.process_reference,
            attestation=followup_attestation,
            resolved_evidence={},
            terminal_result=None,
            terminal_reference=None,
        )
        assert followup.terminal_result is None
        assert (
            restarted.commit(
                fingerprint=fingerprint,
                process=process,
                process_reference=checkpoint.process_reference,
                attestation=attestation,
                resolved_evidence={},
                terminal_result=terminal,
                terminal_reference=terminal_receipt.terminal_result,
            )
            == completed
        )
        with pytest.raises(RunnerReplayError, match="checkpoint state"):
            restarted.checkpoint(process.job_id, fingerprint, checkpoint)
        for completed_conflict in (
            attestation.model_copy(update={"nonce_b64url": "B" * 22}),
            attestation.model_copy(update={"profile_id": "profile_conflicting_replay"}),
        ):
            with pytest.raises(RunnerReplayError, match="finalize"):
                restarted.commit(
                    fingerprint=fingerprint,
                    process=process,
                    process_reference=checkpoint.process_reference,
                    attestation=completed_conflict,
                    resolved_evidence={},
                    terminal_result=terminal,
                    terminal_reference=terminal_receipt.terminal_result,
                )
        replay = restarted.reserve(
            process.job_id, process.process_execution_id, fingerprint
        )
        assert replay == completed
        assert (
            restarted.execution_attestation(completed.execution_attestation)
            == attestation
        )
        if isinstance(second, SqliteArtifactRepository):
            corrupted = completed.model_copy(
                update={
                    "execution_attestation": ArtifactReference(
                        artifact_id="artifact_" + "0" * 64,
                        payload_hash="sha256:" + "0" * 64,
                    )
                }
            )
            second._connection.execute(
                "UPDATE runner_executions SET result_bytes = ? WHERE job_id = ?",
                (
                    canonical_json_bytes(corrupted.model_dump(mode="json")),
                    process.job_id,
                ),
            )
            second._connection.commit()
            with pytest.raises(RunnerReplayError, match="persistent"):
                restarted.reserve(
                    process.job_id,
                    process.process_execution_id,
                    fingerprint,
                )
    finally:
        if isinstance(second, SqliteArtifactRepository):
            second.close()


def _repository_storage_snapshot(
    repository: ArtifactRepositoryAdapter,
) -> object:
    if isinstance(repository, InMemoryArtifactRepository):
        return (
            dict(repository._artifacts),
            dict(repository._event_records),
            dict(repository._event_ids),
            dict(repository._signed_event_nonces),
            dict(repository._signed_event_slots),
            dict(repository._lifecycle_commands),
            dict(repository._lifecycle_idempotency),
            dict(repository._terminal_results),
            {
                run_id: dict(versions)
                for run_id, versions in repository._audit_projections.items()
            },
            dict(repository._e2e_gate_materializations),
            dict(repository._e2e_gate_claims),
            dict(repository._runner_executions),
            dict(repository._runner_attestation_nonces),
        )
    queries = (
        "SELECT * FROM payload_blobs ORDER BY rowid",
        "SELECT * FROM artifacts ORDER BY rowid",
        "SELECT * FROM artifact_schema_contracts ORDER BY rowid",
        "SELECT * FROM event_schema_contracts ORDER BY rowid",
        "SELECT * FROM artifact_parents ORDER BY rowid",
        "SELECT * FROM run_events ORDER BY rowid",
        "SELECT * FROM run_heads ORDER BY rowid",
        "SELECT * FROM signed_event_nonces ORDER BY rowid",
        "SELECT * FROM lifecycle_commands ORDER BY rowid",
        "SELECT * FROM e2e_gate_materializations ORDER BY rowid",
        "SELECT * FROM e2e_gate_claims ORDER BY rowid",
        "SELECT * FROM run_terminal_results ORDER BY rowid",
        "SELECT * FROM run_audit_projections ORDER BY rowid",
        "SELECT * FROM runner_executions ORDER BY rowid",
    )
    return tuple(
        tuple(repository._connection.execute(query).fetchall()) for query in queries
    )


def _open_lifecycle_repository(
    adapter_name: str,
    database_path: Path,
) -> ArtifactRepositoryAdapter:
    if adapter_name == "memory":
        return InMemoryArtifactRepository(
            _registry(terminal_provenance=True),
            _EVENT_AUTHENTICATOR,
            _COMMAND_AUTHORITY,
        )
    return SqliteArtifactRepository(
        database_path,
        _registry(terminal_provenance=True),
        _EVENT_AUTHENTICATOR,
        _COMMAND_AUTHORITY,
    )


def test_sqlite_repository_created_by_v8_source_commit_migrates_to_v10(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    archived = subprocess.run(
        ("git", "archive", "--format=tar", _SQLITE_V8_SOURCE_COMMIT),
        cwd=repository_root,
        check=True,
        capture_output=True,
    ).stdout
    legacy_checkout = tmp_path / "v8-source"
    legacy_checkout.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archived), mode="r:") as source_archive:
        source_archive.extractall(legacy_checkout, filter="data")
    database_path = tmp_path / "repository-real-v8.sqlite"
    legacy_process = subprocess.run(
        (sys.executable, "-c", _V8_REPOSITORY_CREATION_SCRIPT, str(database_path)),
        cwd=legacy_checkout,
        env=os.environ
        | {"PYTHONPATH": str(legacy_checkout / "src"), "PYTHONNOUSERSITE": "1"},
        check=True,
        capture_output=True,
        text=True,
    )
    stored_identity = json.loads(legacy_process.stdout)
    assert set(stored_identity) == {
        "artifact_id",
        "command",
        "payload_hash",
        "receipt",
    }
    legacy_receipt = cast(dict[str, object], stored_identity["receipt"])
    legacy_view = cast(dict[str, object], legacy_receipt["run_view"])
    assert legacy_view["projector_hash"] != RUN_PROJECTOR_HASH
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (8,)
        assert connection.execute(
            "SELECT schema_id FROM event_schema_contracts "
            "WHERE event_type = 'ValidationFailed' "
            "AND schema_version = 'automarkov.validation-failed.v1'"
        ).fetchone() == (_VALIDATION_FAILED_V8_SCHEMA_ID,)

    migrated = SqliteArtifactRepository(
        database_path,
        _registry(),
        _EVENT_AUTHENTICATOR,
        _COMMAND_AUTHORITY,
    )
    try:
        stored = migrated.get(ArtifactId(root=stored_identity["artifact_id"]))
        assert stored.envelope.payload_hash == stored_identity["payload_hash"]
        assert migrated._connection.execute("PRAGMA user_version").fetchone() == (10,)
        assert migrated._connection.execute(
            "SELECT schema_id FROM event_schema_contracts "
            "WHERE event_type = 'ValidationFailed' "
            "AND schema_version = 'automarkov.validation-failed.v1'"
        ).fetchone() != (_VALIDATION_FAILED_V8_SCHEMA_ID,)
        replayed = _commit(
            migrated,
            cast(dict[str, object], stored_identity["command"]),
        )
        assert replayed.command_fingerprint == legacy_receipt["command_fingerprint"]
        assert replayed.run_view.projector_hash == RUN_PROJECTOR_HASH
        assert replayed.run_view.run_manifest == ArtifactReference(
            artifact_id=stored_identity["artifact_id"],
            payload_hash=stored_identity["payload_hash"],
        )
        assert migrated._connection.execute(
            "SELECT count(*) FROM lifecycle_commands"
        ).fetchone() == (1,)
    finally:
        migrated.close()


def test_sqlite_v8_terminal_artifacts_remain_readable_after_migration(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    archived = subprocess.run(
        ("git", "archive", "--format=tar", _SQLITE_V8_SOURCE_COMMIT),
        cwd=repository_root,
        check=True,
        capture_output=True,
    ).stdout
    legacy_checkout = tmp_path / "v8-terminal-source"
    legacy_checkout.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archived), mode="r:") as source_archive:
        source_archive.extractall(legacy_checkout, filter="data")
    database_path = tmp_path / "repository-real-v8-terminal.sqlite"
    legacy_process = subprocess.run(
        (
            sys.executable,
            "-c",
            _V8_REPOSITORY_CREATION_SCRIPT,
            str(database_path),
            "terminal",
        ),
        cwd=legacy_checkout,
        env=os.environ
        | {"PYTHONPATH": str(legacy_checkout / "src"), "PYTHONNOUSERSITE": "1"},
        check=True,
        capture_output=True,
        text=True,
    )
    stored_identity = json.loads(legacy_process.stdout)
    legacy_receipt = cast(dict[str, object], stored_identity["receipt"])
    legacy_view = cast(dict[str, object], legacy_receipt["run_view"])
    terminal_ref = ArtifactReference.model_validate(
        legacy_receipt["terminal_result"], strict=True
    )
    audit_ref = ArtifactReference.model_validate(
        legacy_view["run_audit_projection"], strict=True
    )
    assert legacy_view["projector_hash"] != RUN_PROJECTOR_HASH

    migrated = SqliteArtifactRepository(
        database_path,
        _registry(terminal_provenance=True),
        _EVENT_AUTHENTICATOR,
        _COMMAND_AUTHORITY,
    )
    try:
        terminal = TerminalResult.model_validate(
            migrated.get(
                ArtifactId(root=terminal_ref.artifact_id)
            ).payload_document.model_dump(mode="json")["payload"],
            strict=True,
        )
        audit = RunAuditProjection.model_validate(
            migrated.get(
                ArtifactId(root=audit_ref.artifact_id)
            ).payload_document.model_dump(mode="json")["payload"],
            strict=True,
        )
        process = ProcessExecutionTerminalRecord.model_validate(
            migrated.get(
                ArtifactId(root=terminal.process_execution_terminal_record.artifact_id)
            ).payload_document.model_dump(mode="json")["payload"],
            strict=True,
        )
        assert terminal.projector_hash == audit.projector_hash != RUN_PROJECTOR_HASH
        assert process.run_id == terminal.run_id
        projected = migrated.project(
            RunId(root=_RUN_ID),
            VerifiedEventHead.model_validate(legacy_receipt["after_head"], strict=True),
            projector_version=RUN_PROJECTOR_VERSION,
            projector_hash=Sha256Digest(root=RUN_PROJECTOR_HASH),
        )
        assert projected.state is RunState.FAILED
        assert projected.terminal_result == terminal_ref
        assert projected.run_audit_projection == audit_ref
    finally:
        migrated.close()


def test_sqlite_v8_migration_rejects_a_forged_legacy_projector_hash(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "repository-v8-forged-projector.sqlite"
    repository = SqliteArtifactRepository(
        database_path,
        _registry(),
        _EVENT_AUTHENTICATOR,
        _COMMAND_AUTHORITY,
    )
    _, root = _start_run(repository)
    legacy_receipt = root.model_dump(mode="json")
    legacy_view = cast(dict[str, object], legacy_receipt["run_view"])
    legacy_view.pop("run_manifest")
    forged_projector_hash = "sha256:" + "0" * 64
    legacy_view["projector_hash"] = forged_projector_hash
    repository._connection.execute(
        "UPDATE lifecycle_commands SET result_bytes = ? WHERE command_id = ?",
        (canonical_json_bytes(legacy_receipt), root.command_id),
    )
    repository.close()

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE event_schema_contracts SET schema_id = ? "
            "WHERE event_type = 'ValidationFailed' "
            "AND schema_version = 'automarkov.validation-failed.v1'",
            (_VALIDATION_FAILED_V8_SCHEMA_ID,),
        )
        assert connection.execute(
            "SELECT schema_id FROM event_schema_contracts "
            "WHERE event_type = 'ValidationFailed' "
            "AND schema_version = 'automarkov.validation-failed.v1'"
        ).fetchone() == (_VALIDATION_FAILED_V8_SCHEMA_ID,)
        connection.execute("DROP TABLE e2e_gate_claims")
        connection.execute("DROP TABLE e2e_gate_materializations")
        connection.execute("DROP TABLE runner_executions")
        connection.execute("PRAGMA user_version = 8")
        connection.commit()

    with pytest.raises(ArtifactIntegrityError, match="lifecycle-receipt"):
        SqliteArtifactRepository(
            database_path,
            _registry(),
            _EVENT_AUTHENTICATOR,
            _COMMAND_AUTHORITY,
        )

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (8,)
        stored_receipt = json.loads(
            bytes(
                connection.execute(
                    "SELECT result_bytes FROM lifecycle_commands WHERE command_id = ?",
                    (root.command_id,),
                ).fetchone()[0]
            )
        )
        assert type(stored_receipt) is dict
        stored_view = cast(dict[str, object], stored_receipt["run_view"])
        assert stored_view["projector_hash"] == forged_projector_hash
        assert (
            connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE name IN ('e2e_gate_claims', 'e2e_gate_materializations', "
                "'runner_executions')"
            ).fetchall()
            == []
        )


def test_sqlite_v8_migration_rejects_a_tampered_event_schema_contract(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "repository-v8-tampered-event-schema.sqlite"
    repository = SqliteArtifactRepository(
        database_path,
        _registry(),
        _EVENT_AUTHENTICATOR,
        _COMMAND_AUTHORITY,
    )
    repository.close()
    tampered_schema_id = "sha256:" + "0" * 64
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE event_schema_contracts SET schema_id = ? "
            "WHERE event_type = 'ValidationFailed' "
            "AND schema_version = 'automarkov.validation-failed.v1'",
            (tampered_schema_id,),
        )
        connection.execute("DROP TABLE e2e_gate_claims")
        connection.execute("DROP TABLE e2e_gate_materializations")
        connection.execute("DROP TABLE runner_executions")
        connection.execute("PRAGMA user_version = 8")
        connection.commit()

    with pytest.raises(ArtifactIntegrityError, match="event-schema"):
        SqliteArtifactRepository(
            database_path,
            _registry(),
            _EVENT_AUTHENTICATOR,
            _COMMAND_AUTHORITY,
        )

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (8,)
        assert connection.execute(
            "SELECT schema_id FROM event_schema_contracts "
            "WHERE event_type = 'ValidationFailed' "
            "AND schema_version = 'automarkov.validation-failed.v1'"
        ).fetchone() == (tampered_schema_id,)
        assert (
            connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE name IN ('e2e_gate_claims', 'e2e_gate_materializations', "
                "'runner_executions')"
            ).fetchall()
            == []
        )


@pytest.mark.parametrize("adapter_name", ("memory", "sqlite"))
def test_failed_e2e_claim_conflict_does_not_return_a_foreign_receipt(
    adapter_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _open_lifecycle_repository(
        adapter_name,
        tmp_path / f"e2e-conflict-{adapter_name}.sqlite",
    )
    try:

        def ref(character: str) -> ArtifactReference:
            return ArtifactReference(
                artifact_id="artifact_" + character * 64,
                payload_hash="sha256:" + character * 64,
            )

        def command(run_id: str, character: str) -> E2EGateCommitCommand:
            process = ref("8")
            return E2EGateCommitCommand(
                schema_version="automarkov.e2e-gate-commit-command.v1",
                request_ref=ref(character),
                verdict_ref=ref("2"),
                request_id="request_shared_conflict",
                verdict_id=f"verdict_{run_id}",
                request_nonce_b64url=base64.urlsafe_b64encode(bytes(range(32)))
                .decode()
                .rstrip("="),
                verdict_nonce_b64url=base64.urlsafe_b64encode(bytes(range(32, 64)))
                .decode()
                .rstrip("="),
                coordinator_key_id="key_shared_coordinator",
                evaluator_key_id=f"key_{run_id}",
                run_id=run_id,
                run_manifest=ref("3"),
                specified_event_head=VerifiedEventHead(
                    run_id=RunId(root=run_id),
                    sequence_no=0,
                    event_hash=Sha256Digest(root=_HASH_A),
                ),
                candidate_bundle=ref("4"),
                topology_ref=ref("5"),
                request_payload_hash=ref(character).payload_hash,
                verdict_payload_hash=ref("2").payload_hash,
                topology_payload_hash=ref("5").payload_hash,
                runner_fingerprint=_HASH_A,
                process_execution_terminal_record=process,
                decision=SealedE2EGate._decision("FAILED"),
                committed_at=_ISSUED_AT,
            )

        prior_command = command("run_prior_failed", "1")
        current_command = command("run_current_failed", "6")
        prior_fingerprint = _command_fingerprint(prior_command)
        prior_receipt = ArtifactLifecycleAtomicReceipt(
            schema_version="automarkov.artifact-lifecycle-e2e-receipt.v1",
            command_fingerprint=prior_fingerprint,
            atomic_receipt_id=_HASH_A,
            request_ref=prior_command.request_ref,
            verdict_ref=prior_command.verdict_ref,
            candidate_bundle=prior_command.candidate_bundle,
            topology_ref=prior_command.topology_ref,
            terminal_state="FAILED",
            terminal_reason_code="sealed_e2e_integrity_failed",
            outcome_e2e_valid=0,
            training_outcome_missing=True,
            lifecycle_command_fingerprint=_HASH_A,
            lifecycle_after_head_hash=_HASH_A,
            process_execution_terminal_record=prior_command.process_execution_terminal_record,
            terminal_result=ref("9"),
            execution_attestation=ref("a"),
            committed_at=_ISSUED_AT,
        )
        command_bytes = canonical_json_bytes(prior_command.model_dump(mode="json"))
        receipt_bytes = canonical_json_bytes(prior_receipt.model_dump(mode="json"))
        if isinstance(repository, InMemoryArtifactRepository):
            repository._e2e_gate_materializations[prior_fingerprint] = (
                command_bytes,
                receipt_bytes,
            )
            for claim in _command_claims(prior_command):
                repository._e2e_gate_claims[claim] = prior_fingerprint
        else:
            repository._connection.execute(
                "INSERT INTO e2e_gate_materializations VALUES (?, ?, ?)",
                (prior_fingerprint, command_bytes, receipt_bytes),
            )
            repository._connection.executemany(
                "INSERT INTO e2e_gate_claims VALUES (?, ?, ?)",
                [
                    (*claim, prior_fingerprint)
                    for claim in _command_claims(prior_command)
                ],
            )
            repository._connection.commit()
        monkeypatch.setattr(
            repository,
            "_revalidate_e2e_replay",
            lambda *_args: prior_receipt,
        )

        with pytest.raises(UnknownArtifactError):
            repository.materialize_e2e_gate_atomically(
                current_command,
                cast(Any, object()),
            )
    finally:
        if isinstance(repository, SqliteArtifactRepository):
            repository.close()


def _expected_artifact_id(
    model_type: type[BaseModel],
    payload: Mapping[str, object],
    *,
    artifact_type: str,
    parent_artifact_ids: list[str],
    created_by: str,
) -> ArtifactId:
    codec = CanonicalPayloadCodec(model_type)
    payload_bytes = codec.encode(dict(payload))
    payload_hash = f"sha256:{hashlib.sha256(payload_bytes).hexdigest()}"
    envelope = {
        "artifact_type": artifact_type,
        "schema_version": cast(str, payload["schema_version"]),
        "schema_id": codec.schema_id,
        "payload_media_type": _PAYLOAD_MEDIA_TYPE,
        "payload_hash": payload_hash,
        "parent_artifact_ids": sorted(parent_artifact_ids),
        "created_by": created_by,
        "created_at": _ISSUED_AT,
        "source_evidence_ids": [],
    }
    return ArtifactId(
        root=f"artifact_{hashlib.sha256(canonical_json_bytes(envelope)).hexdigest()}"
    )


@pytest.fixture(params=("memory", "sqlite"))
def lifecycle_repository(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> Iterator[ArtifactRepositoryAdapter]:
    repository: ArtifactRepositoryAdapter
    if request.param == "memory":
        repository = InMemoryArtifactRepository(
            _registry(terminal_provenance=True),
            _EVENT_AUTHENTICATOR,
            _COMMAND_AUTHORITY,
        )
    else:
        repository = SqliteArtifactRepository(
            tmp_path / "lifecycle.sqlite",
            _registry(terminal_provenance=True),
            _EVENT_AUTHENTICATOR,
            _COMMAND_AUTHORITY,
        )
    try:
        yield repository
    finally:
        if isinstance(repository, SqliteArtifactRepository):
            repository.close()


def _run_resume_trace(repository: ArtifactRepositoryAdapter) -> list[dict[str, object]]:
    artifacts, root = _start_run(repository)
    researching = _advance_to_researching(repository, artifacts, root)
    evidence_reference = _artifact_ref(artifacts["governance"])

    unavailable_raw = _event_common(
        researching.event_record.event.sequence_no + 1,
        researching.event_record.event_hash,
        "automarkov.evidence-temporarily-unavailable.v1",
        "EvidenceTemporarilyUnavailable",
    ) | {
        "lease_pool_artifact_id": evidence_reference["artifact_id"],
        "lease_pool_payload_hash": evidence_reference["payload_hash"],
        "lease_snapshot_artifact_id": evidence_reference["artifact_id"],
        "lease_snapshot_payload_hash": evidence_reference["payload_hash"],
        "availability_probe_artifact_id": evidence_reference["artifact_id"],
        "availability_probe_payload_hash": evidence_reference["payload_hash"],
        "slot_state_counts": {
            "available": 0,
            "leased": 1,
            "cooldown": 0,
            "invalid_credential": 0,
        },
        "earliest_availability": _ISSUED_AT,
    }
    unavailable_record = _event_record(unavailable_raw)
    waiting_raw = _event_common(
        unavailable_record.event.sequence_no + 1,
        unavailable_record.event_hash,
        "automarkov.waiting-evidence.v1",
        "WaitingEvidence",
    ) | {
        "resume_state": "RESEARCHING",
        "wait_reason_code": "evidence_temporarily_unavailable",
        "trigger_event_id": unavailable_record.event.event_id,
        "trigger_event_hash": unavailable_record.event_hash,
        "failure_report_artifact_id": evidence_reference["artifact_id"],
        "failure_report_payload_hash": evidence_reference["payload_hash"],
        "recovery_gate_id": "gate_evidence_pool",
        "recovery_condition_hash": _HASH_A,
        "entered_at": _ISSUED_AT,
        "lease_pool_artifact_id": evidence_reference["artifact_id"],
        "lease_pool_payload_hash": evidence_reference["payload_hash"],
        "lease_snapshot_artifact_id": evidence_reference["artifact_id"],
        "lease_snapshot_payload_hash": evidence_reference["payload_hash"],
        "lease_identity_hash": _HASH_A,
        "earliest_availability": _ISSUED_AT,
    }
    waiting_record = _event_record(waiting_raw)
    enter_waiting = _transition_event(
        sequence_no=waiting_record.event.sequence_no + 1,
        previous_event_hash=waiting_record.event_hash,
        from_state="RESEARCHING",
        to_state="WAITING_EVIDENCE",
        trigger=waiting_record,
        budget=artifacts["budget"],
    )
    waiting = _append_events(
        repository,
        [unavailable_raw, waiting_raw, enter_waiting],
        command_index=2,
        expected_state="RESEARCHING",
        expected_head=_event_head(researching),
    )

    restored_raw = _event_common(
        waiting.event_record.event.sequence_no + 1,
        waiting.event_record.event_hash,
        "automarkov.wait-resolved.v1",
        "WaitResolved",
    ) | {
        "wait_kind": "evidence",
        "waiting_event_id": waiting_record.event.event_id,
        "waiting_event_hash": waiting_record.event_hash,
        "resume_state": "RESEARCHING",
        "recovery_gate_id": "gate_evidence_pool",
        "recovery_report_artifact_id": evidence_reference["artifact_id"],
        "recovery_report_payload_hash": evidence_reference["payload_hash"],
        "identity_hash": _HASH_A,
        "resolved_at": _ISSUED_AT,
    }
    restored_record = _event_record(restored_raw)
    resume = _transition_event(
        sequence_no=restored_record.event.sequence_no + 1,
        previous_event_hash=restored_record.event_hash,
        from_state="WAITING_EVIDENCE",
        to_state="RESEARCHING",
        trigger=restored_record,
        budget=artifacts["budget"],
    )
    resumed = _append_events(
        repository,
        [restored_raw, resume],
        command_index=4,
        expected_state="WAITING_EVIDENCE",
        expected_head=_event_head(waiting),
    )
    return [
        result.model_dump(mode="json")
        for result in (root, researching, waiting, resumed)
    ]


def test_memory_and_sqlite_persist_same_trace_and_specified_head(
    tmp_path: Path,
) -> None:
    memory = InMemoryArtifactRepository(
        _registry(), _EVENT_AUTHENTICATOR, _COMMAND_AUTHORITY
    )
    sqlite_path = tmp_path / "same-trace.sqlite"
    sqlite = SqliteArtifactRepository(
        sqlite_path,
        _registry(),
        _EVENT_AUTHENTICATOR,
        _COMMAND_AUTHORITY,
    )
    try:
        memory_trace = _run_resume_trace(memory)
        sqlite_trace = _run_resume_trace(sqlite)
        assert sqlite_trace == memory_trace
        final_head = cast(
            dict[str, object],
            cast(dict[str, object], memory_trace[-1]["run_view"])["event_head"],
        )
        memory_view = _project(
            memory,
            sequence_no=cast(int, final_head["sequence_no"]),
            event_head_hash=cast(str, final_head["event_hash"]),
        )
        sqlite_view = _project(
            sqlite,
            sequence_no=memory_view.event_head.sequence_no,
            event_head_hash=memory_view.event_head.event_hash,
        )
        assert sqlite_view.model_dump(mode="json") == memory_view.model_dump(
            mode="json"
        )
        with pytest.raises(RunProjectorIdentityError):
            memory.project(
                RunId(root=_RUN_ID),
                VerifiedEventHead.model_validate(
                    {
                        "run_id": _RUN_ID,
                        "sequence_no": memory_view.event_head.sequence_no,
                        "event_hash": memory_view.event_head.event_hash,
                    },
                    strict=True,
                ),
                projector_version=RUN_PROJECTOR_VERSION,
                projector_hash=Sha256Digest(root=_HASH_B),
            )
    finally:
        sqlite.close()

    reopened = SqliteArtifactRepository(
        sqlite_path,
        _registry(),
        _EVENT_AUTHENTICATOR,
        _COMMAND_AUTHORITY,
    )
    try:
        reopened_view = _project(
            reopened,
            sequence_no=memory_view.event_head.sequence_no,
            event_head_hash=memory_view.event_head.event_hash,
        )
        assert reopened_view.model_dump(mode="json") == memory_view.model_dump(
            mode="json"
        )
    finally:
        reopened.close()


def test_sqlite_rejects_persisted_event_schema_drift(tmp_path: Path) -> None:
    database_path = tmp_path / "event-schema-drift.sqlite"
    repository = SqliteArtifactRepository(
        database_path,
        _registry(),
        _EVENT_AUTHENTICATOR,
        _COMMAND_AUTHORITY,
    )
    repository.close()

    connection = sqlite3.connect(database_path)
    connection.execute(
        "UPDATE event_schema_contracts SET schema_id = ? "
        "WHERE event_type = 'RunCreated'",
        ("sha256:" + "0" * 64,),
    )
    connection.commit()
    connection.close()

    with pytest.raises(ArtifactIntegrityError):
        SqliteArtifactRepository(
            database_path,
            _registry(),
            _EVENT_AUTHENTICATOR,
            _COMMAND_AUTHORITY,
        )


def test_signed_event_replay_index_drift_fails_closed(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    _, root = _start_run(lifecycle_repository)
    root_event = cast(RunCreated, root.event_record.event)

    if isinstance(lifecycle_repository, InMemoryArtifactRepository):
        lifecycle_repository._signed_event_slots[
            (root_event.signing_key_id, root_event.run_id, root_event.sequence_no)
        ] = _uuid7(60_000)
    else:
        lifecycle_repository._connection.execute(
            "UPDATE signed_event_nonces SET sequence_no = ? WHERE event_id = ?",
            (root_event.sequence_no + 1, root_event.event_id),
        )

    with pytest.raises(ArtifactIntegrityError):
        _project(
            lifecycle_repository,
            sequence_no=root.event_record.event.sequence_no,
            event_head_hash=root.event_record.event_hash,
        )


def test_event_scalar_artifact_hash_binding_fails_closed(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    artifacts, root = _start_run(lifecycle_repository)
    researching = _advance_to_researching(
        lifecycle_repository,
        artifacts,
        root,
    )
    evidence_reference = _artifact_ref(artifacts["governance"])
    event = _event_common(
        researching.event_record.event.sequence_no + 1,
        researching.event_record.event_hash,
        "automarkov.evidence-temporarily-unavailable.v1",
        "EvidenceTemporarilyUnavailable",
    ) | {
        "lease_pool_artifact_id": evidence_reference["artifact_id"],
        "lease_pool_payload_hash": _HASH_B,
        "lease_snapshot_artifact_id": evidence_reference["artifact_id"],
        "lease_snapshot_payload_hash": evidence_reference["payload_hash"],
        "availability_probe_artifact_id": evidence_reference["artifact_id"],
        "availability_probe_payload_hash": evidence_reference["payload_hash"],
        "slot_state_counts": {
            "available": 0,
            "leased": 1,
            "cooldown": 0,
            "invalid_credential": 0,
        },
        "earliest_availability": _ISSUED_AT,
    }
    before = _repository_storage_snapshot(lifecycle_repository)

    with pytest.raises(TerminalProvenanceError):
        _append_events(
            lifecycle_repository,
            [event],
            command_index=6,
            expected_state="RESEARCHING",
            expected_head=_event_head(researching),
        )
    assert _repository_storage_snapshot(lifecycle_repository) == before


@pytest.mark.parametrize("adapter_name", ["memory", "sqlite"])
def test_compare_and_swap_has_one_winner_for_competing_heads(
    adapter_name: str,
    tmp_path: Path,
) -> None:
    if adapter_name == "memory":
        first: ArtifactRepositoryAdapter = InMemoryArtifactRepository(
            _registry(),
            _EVENT_AUTHENTICATOR,
            _COMMAND_AUTHORITY,
        )
        second: ArtifactRepositoryAdapter = first
    else:
        database_path = tmp_path / "cas.sqlite"
        first = SqliteArtifactRepository(
            database_path,
            _registry(),
            _EVENT_AUTHENTICATOR,
            _COMMAND_AUTHORITY,
        )
        second = SqliteArtifactRepository(
            database_path,
            _registry(),
            _EVENT_AUTHENTICATOR,
            _COMMAND_AUTHORITY,
        )
    try:
        artifacts, root = _start_run(first)
        barrier = Barrier(2)

        def compete(repository: ArtifactRepositoryAdapter, variant: int) -> object:
            barrier.wait()
            try:
                return _advance_to_researching(
                    repository,
                    artifacts,
                    root,
                    variant=variant,
                )
            except AutoMarkovError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(
                executor.map(
                    lambda pair: compete(*pair),
                    ((first, 1), (second, 2)),
                )
            )
        winners = [
            item for item in outcomes if isinstance(item, LifecycleCommitReceipt)
        ]
        conflicts = [
            item for item in outcomes if isinstance(item, EventHeadConflictError)
        ]
        assert len(winners) == len(conflicts) == 1
        winning_head = winners[0].run_view.event_head
        assert (
            _project(
                first,
                sequence_no=winning_head.sequence_no,
                event_head_hash=winning_head.event_hash,
            ).event_head
            == winning_head
        )
    finally:
        if isinstance(second, SqliteArtifactRepository):
            second.close()
        if isinstance(first, SqliteArtifactRepository):
            first.close()


def test_terminal_commit_is_atomic_and_payload_bound(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    artifacts, root = _start_run(lifecycle_repository)
    researching = _advance_to_researching(
        lifecycle_repository,
        artifacts,
        root,
    )
    cause = _terminal_cause(artifacts, researching)
    with pytest.raises(EventSchemaError):
        _commit(
            lifecycle_repository,
            {
                "schema_version": "automarkov.lifecycle-command.v1",
                "command_type": "append_run_events",
                "command_id": _uuid7(19_000),
                "actor_principal_id": "principal_fixed_commit_runner",
                "issued_at": _ISSUED_AT,
                "idempotency_key": "terminal-cause-must-be-atomic",
                "run_id": _RUN_ID,
                "expected_state": "RESEARCHING",
                "expected_head": _event_head(researching),
                "events": [cause],
            },
        )
    with pytest.raises(AutoMarkovError):
        _commit(
            lifecycle_repository,
            _terminal_command(
                artifacts,
                researching,
                terminal_time_approvals=[
                    {
                        "event_id": _uuid7(18_000),
                        "sequence_no": 1,
                        "event_hash": _HASH_A,
                    }
                ],
                command_variant=2,
            ),
        )
    wrong_output_reference = {
        "artifact_id": artifacts["alternate_output"].artifact_id.root,
        "payload_hash": artifacts["output"].payload_hash.root,
    }
    invalid_record = _process_terminal_record(
        artifacts,
        output_ref=wrong_output_reference,
    )
    predicted_invalid_id = _expected_artifact_id(
        ProcessExecutionTerminalRecord,
        invalid_record,
        artifact_type="process_execution_terminal_record",
        parent_artifact_ids=[
            artifacts["job_manifest"].artifact_id.root,
            artifacts["alternate_output"].artifact_id.root,
            artifacts["resource_usage"].artifact_id.root,
        ],
        created_by="principal_fixed_commit_runner",
    )
    with pytest.raises(AutoMarkovError):
        _commit(
            lifecycle_repository,
            _terminal_command(
                artifacts,
                researching,
                process_record=invalid_record,
                command_variant=1,
            ),
        )
    researching_head = researching.run_view.event_head
    assert (
        _project(
            lifecycle_repository,
            sequence_no=researching_head.sequence_no,
            event_head_hash=researching_head.event_hash,
        ).event_head
        == researching_head
    )
    with pytest.raises(UnknownArtifactError):
        lifecycle_repository.get(predicted_invalid_id)

    terminal_command = _terminal_command(artifacts, researching)
    committed = _commit(lifecycle_repository, terminal_command)
    retried = _commit(lifecycle_repository, terminal_command)
    assert retried.model_dump_json() == committed.model_dump_json()
    assert committed.run_view.state is RunState.FAILED
    assert committed.before_head == researching.run_view.event_head
    assert committed.after_head == committed.run_view.event_head
    assert tuple(record.event.sequence_no for record in committed.event_records) == (
        researching.event_record.event.sequence_no + 1,
        researching.event_record.event.sequence_no + 2,
    )
    assert committed.run_view.terminal_result is not None
    assert committed.process_execution_terminal_record is not None
    assert committed.terminal_result == committed.run_view.terminal_result
    assert committed.run_view.terminal_snapshot_head == committed.after_head

    process_reference = committed.process_execution_terminal_record
    terminal_reference = committed.terminal_result
    audit_reference = committed.run_view.run_audit_projection
    assert (
        process_reference is not None
        and terminal_reference is not None
        and audit_reference is not None
    )
    assert sorted(
        reference.model_dump_json() for reference in committed.artifact_references
    ) == sorted(
        reference.model_dump_json()
        for reference in (process_reference, terminal_reference, audit_reference)
    )
    assert lifecycle_repository.lineage(
        ArtifactId(root=process_reference.artifact_id)
    ).artifact_ids == tuple(
        ArtifactId(root=value)
        for value in sorted(
            (
                artifacts["job_manifest"].artifact_id.root,
                artifacts["output"].artifact_id.root,
                artifacts["resource_usage"].artifact_id.root,
            )
        )
    )
    assert lifecycle_repository.lineage(
        ArtifactId(root=terminal_reference.artifact_id)
    ).artifact_ids == tuple(
        ArtifactId(root=value)
        for value in sorted(
            (
                artifacts["job_manifest"].artifact_id.root,
                process_reference.artifact_id,
                artifacts["output"].artifact_id.root,
            )
        )
    )

    terminal_payload = cast(
        dict[str, object],
        lifecycle_repository.get(
            ArtifactId(root=terminal_reference.artifact_id)
        ).payload_document.model_dump(mode="json")["payload"],
    )
    assert terminal_payload["process_execution_terminal_record"] == {
        "artifact_id": process_reference.artifact_id,
        "payload_hash": process_reference.payload_hash,
    }
    assert set(terminal_payload) == {
        "created_at",
        "experiment_id",
        "fixed_commit_job_manifest",
        "payload_outputs",
        "process_execution_id",
        "process_execution_terminal_record",
        "projector_hash",
        "projector_version",
        "run_id",
        "schema_version",
        "signing_domain",
        "terminal_event",
        "terminal_reason_code",
        "terminal_snapshot_event_head",
        "terminal_state",
        "terminal_time_approvals",
    }
    assert terminal_payload["terminal_snapshot_event_head"] == {
        "run_id": committed.after_head.run_id,
        "sequence_no": committed.after_head.sequence_no,
        "event_hash": committed.after_head.event_hash,
    }
    assert "artifact_id" not in terminal_payload
    assert TerminalResult.model_validate_json(canonical_json_bytes(terminal_payload))


def test_idempotent_retry_rejects_receipt_artifact_set_tamper(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    artifacts, root = _start_run(lifecycle_repository)
    researching = _advance_to_researching(
        lifecycle_repository,
        artifacts,
        root,
    )
    command = _terminal_command(artifacts, researching)
    committed = _commit(lifecycle_repository, command)
    extra_reference = ArtifactReference.model_validate(
        _artifact_ref(artifacts["governance"]),
        strict=True,
    )
    tampered = committed.model_copy(
        update={
            "artifact_references": committed.artifact_references + (extra_reference,)
        }
    )

    if isinstance(lifecycle_repository, InMemoryArtifactRepository):
        fingerprint, _ = lifecycle_repository._lifecycle_commands[
            cast(str, command["command_id"])
        ]
        lifecycle_repository._lifecycle_commands[cast(str, command["command_id"])] = (
            fingerprint,
            tampered,
        )
        lifecycle_repository._lifecycle_idempotency[
            cast(str, command["idempotency_key"])
        ] = (fingerprint, tampered)
    else:
        lifecycle_repository._connection.execute(
            "UPDATE lifecycle_commands SET result_bytes = ? WHERE command_id = ?",
            (
                canonical_json_bytes(tampered.model_dump(mode="json")),
                command["command_id"],
            ),
        )

    with pytest.raises(ArtifactIntegrityError):
        _commit(lifecycle_repository, command)


def test_projection_rejects_terminal_index_artifact_substitution(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    artifacts, root = _start_run(lifecycle_repository)
    researching = _advance_to_researching(
        lifecycle_repository,
        artifacts,
        root,
    )
    terminal = _commit(
        lifecycle_repository,
        _terminal_command(artifacts, researching),
    )
    wrong_reference = terminal.run_view.run_audit_projection
    assert wrong_reference is not None

    if isinstance(lifecycle_repository, InMemoryArtifactRepository):
        lifecycle_repository._terminal_results[_RUN_ID] = (
            terminal.after_head.sequence_no,
            wrong_reference,
        )
    else:
        lifecycle_repository._connection.execute(
            "UPDATE run_terminal_results SET artifact_id = ?, payload_hash = ? "
            "WHERE run_id = ?",
            (
                wrong_reference.artifact_id,
                wrong_reference.payload_hash,
                _RUN_ID,
            ),
        )
        lifecycle_repository._connection.commit()

    with pytest.raises(ArtifactIntegrityError):
        _project(
            lifecycle_repository,
            sequence_no=terminal.after_head.sequence_no,
            event_head_hash=terminal.after_head.event_hash,
        )


def test_idempotent_retry_rejects_receipt_index_tamper(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    artifacts, root = _start_run(lifecycle_repository)
    researching = _advance_to_researching(
        lifecycle_repository,
        artifacts,
        root,
    )
    command = _terminal_command(artifacts, researching)
    committed = _commit(lifecycle_repository, command)

    if isinstance(lifecycle_repository, InMemoryArtifactRepository):
        del lifecycle_repository._audit_projections[_RUN_ID][
            committed.after_head.sequence_no
        ]
    else:
        lifecycle_repository._connection.execute(
            "DELETE FROM run_audit_projections "
            "WHERE run_id = ? AND as_of_sequence_no = ?",
            (_RUN_ID, committed.after_head.sequence_no),
        )

    with pytest.raises(ArtifactIntegrityError):
        _commit(lifecycle_repository, command)


def test_public_put_cannot_publish_lifecycle_derived_artifacts(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    artifacts, root = _start_run(lifecycle_repository)
    researching = _advance_to_researching(
        lifecycle_repository,
        artifacts,
        root,
    )
    committed = _commit(
        lifecycle_repository,
        _terminal_command(artifacts, researching),
    )
    references = (
        committed.process_execution_terminal_record,
        committed.terminal_result,
        committed.run_view.run_audit_projection,
    )
    assert all(reference is not None for reference in references)

    for reference in references:
        assert reference is not None
        artifact_id = ArtifactId(root=reference.artifact_id)
        stored = lifecycle_repository.get(artifact_id)
        parents = lifecycle_repository.lineage(artifact_id).artifact_ids
        with pytest.raises(ArtifactWriteAuthorityError):
            lifecycle_repository.put(
                {
                    "schema_version": "automarkov.artifact-put-request.v2",
                    "artifact_type": stored.envelope.artifact_type,
                    "payload_bytes": canonical_json_bytes(
                        stored.payload_document.model_dump(mode="json")["payload"]
                    ),
                    "parent_artifact_ids": [parent.root for parent in parents],
                    "created_by": stored.envelope.created_by,
                    "created_at": stored.envelope.created_at,
                    "source_evidence_ids": list(stored.envelope.source_evidence_ids),
                }
            )


@pytest.mark.parametrize("adapter_name", ("memory", "sqlite"))
@pytest.mark.parametrize(
    "failure_stage",
    (
        "after_process_artifact",
        "after_terminal_artifact",
        "after_audit_artifact",
        "after_event_records",
        "after_head",
        "after_terminal_index",
        "after_audit_index",
        "after_receipt",
    ),
)
def test_terminal_write_failpoints_roll_back_the_complete_commit(
    adapter_name: str,
    failure_stage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / f"terminal-{failure_stage}.sqlite"
    repository = _open_lifecycle_repository(adapter_name, database_path)
    try:
        artifacts, root = _start_run(repository)
        researching = _advance_to_researching(
            repository,
            artifacts,
            root,
        )
        before = _repository_storage_snapshot(repository)

        def fail_at_stage(stage: str) -> None:
            if stage == failure_stage:
                raise _InjectedLifecycleWriteError(stage)

        monkeypatch.setattr(repository, "_terminal_commit_failpoint", fail_at_stage)
        with pytest.raises(_InjectedLifecycleWriteError, match=failure_stage):
            _commit(repository, _terminal_command(artifacts, researching))
        assert _repository_storage_snapshot(repository) == before

        if isinstance(repository, SqliteArtifactRepository):
            repository.close()
            repository = _open_lifecycle_repository(adapter_name, database_path)
            assert _repository_storage_snapshot(repository) == before
        assert (
            _project(
                repository,
                sequence_no=researching.after_head.sequence_no,
                event_head_hash=researching.after_head.event_hash,
            ).event_head
            == researching.after_head
        )
    finally:
        if isinstance(repository, SqliteArtifactRepository):
            repository.close()


@pytest.mark.parametrize("adapter_name", ("memory", "sqlite"))
@pytest.mark.parametrize(
    "failure_stage",
    (
        "after_audit_artifact",
        "after_event_records",
        "after_head",
        "after_audit_index",
        "after_receipt",
    ),
)
def test_post_terminal_write_failpoints_roll_back_event_and_projection(
    adapter_name: str,
    failure_stage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / f"post-terminal-{failure_stage}.sqlite"
    repository = _open_lifecycle_repository(adapter_name, database_path)
    try:
        artifacts, root = _start_run(repository)
        researching = _advance_to_researching(
            repository,
            artifacts,
            root,
        )
        terminal = _commit(repository, _terminal_command(artifacts, researching))
        before = _repository_storage_snapshot(repository)

        def fail_at_stage(stage: str) -> None:
            if stage == failure_stage:
                raise _InjectedLifecycleWriteError(stage)

        monkeypatch.setattr(
            repository,
            "_post_terminal_commit_failpoint",
            fail_at_stage,
        )
        with pytest.raises(_InjectedLifecycleWriteError, match=failure_stage):
            _commit(repository, _post_terminal_audit_command(artifacts, terminal))
        assert _repository_storage_snapshot(repository) == before

        if isinstance(repository, SqliteArtifactRepository):
            repository.close()
            repository = _open_lifecycle_repository(adapter_name, database_path)
            assert _repository_storage_snapshot(repository) == before
        at_terminal = _project(
            repository,
            sequence_no=terminal.after_head.sequence_no,
            event_head_hash=terminal.after_head.event_hash,
        )
        assert at_terminal.event_head == terminal.after_head
        assert (
            at_terminal.run_audit_projection == terminal.run_view.run_audit_projection
        )
    finally:
        if isinstance(repository, SqliteArtifactRepository):
            repository.close()


def test_post_terminal_audit_creates_new_projection_without_snapshot_mutation(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    artifacts, root = _start_run(lifecycle_repository)
    researching = _advance_to_researching(
        lifecycle_repository,
        artifacts,
        root,
    )
    terminal = _commit(
        lifecycle_repository,
        _terminal_command(artifacts, researching),
    )
    terminal_head = terminal.run_view.event_head
    terminal_result = terminal.terminal_result
    root_projection = terminal.run_view.run_audit_projection
    assert terminal_result is not None and root_projection is not None

    audit_event = _event_common(
        terminal.event_record.event.sequence_no + 1,
        terminal.event_record.event_hash,
        "automarkov.artifact-access-revoked.v1",
        "ArtifactAccessRevoked",
    ) | {
        "subject": _artifact_ref(artifacts["output"]),
        "reason_code": "retention_policy",
        "governance_policy": _artifact_ref(artifacts["governance"]),
        "revocation_authority_principal_id": "principal_lifecycle_fixture",
        "effective_at": _ISSUED_AT,
    }
    audited = _append_events(
        lifecycle_repository,
        [audit_event],
        command_index=30,
        expected_state="FAILED",
        expected_head=_event_head(terminal),
    )
    latest = _project(
        lifecycle_repository,
        sequence_no=audited.run_view.event_head.sequence_no,
        event_head_hash=audited.run_view.event_head.event_hash,
    )
    at_terminal = _project(
        lifecycle_repository,
        sequence_no=terminal_head.sequence_no,
        event_head_hash=terminal_head.event_hash,
    )

    assert (
        audited.run_view.state is latest.state is at_terminal.state is RunState.FAILED
    )
    assert latest.terminal_result == at_terminal.terminal_result == terminal_result
    assert latest.terminal_snapshot_head == terminal_head
    assert at_terminal.run_audit_projection == root_projection
    assert latest.run_audit_projection != root_projection
    projection_reference = latest.run_audit_projection
    projection_payload = cast(
        Mapping[str, object],
        lifecycle_repository.get(
            ArtifactId(root=projection_reference.artifact_id)
        ).payload_document.model_dump(mode="json")["payload"],
    )
    assert set(projection_payload) == {
        "as_of_event_head",
        "current_approval_snapshots",
        "experiment_id",
        "outcome_mask",
        "post_terminal_audit_event_references",
        "previous_projection",
        "projection_id",
        "projector_hash",
        "projector_version",
        "run_id",
        "schema_version",
        "signed_deviations",
        "signing_domain",
        "terminal_result",
    }
    assert projection_payload["as_of_event_head"] == {
        "run_id": latest.event_head.run_id,
        "sequence_no": latest.event_head.sequence_no,
        "event_hash": latest.event_head.event_hash,
    }
    assert projection_payload["terminal_result"] == terminal_result.model_dump(
        mode="json"
    )
    assert projection_payload["post_terminal_audit_event_references"] == [
        {
            "event_id": audited.event_record.event.event_id,
            "sequence_no": audited.event_record.event.sequence_no,
            "event_hash": audited.event_record.event_hash,
        }
    ]
    assert projection_payload["outcome_mask"] == {
        "e2e_valid": 0,
        "gold_policy_evaluation_valid": 0,
        "q_gate": 0,
    }
    assert lifecycle_repository.lineage(
        ArtifactId(root=projection_reference.artifact_id)
    ).artifact_ids == tuple(
        ArtifactId(root=value)
        for value in sorted((root_projection.artifact_id, terminal_result.artifact_id))
    )


def test_budget_exhaustion_uses_the_same_atomic_terminal_command(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    artifacts, root = _start_run(lifecycle_repository)
    researching = _advance_to_researching(
        lifecycle_repository,
        artifacts,
        root,
    )
    exhausted_budget = lifecycle_repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "budget_snapshot",
            "payload_bytes": canonical_json_bytes(_budget(consumed=10, limit=10)),
            "parent_artifact_ids": [],
            "created_by": "principal_fixed_commit_runner",
            "created_at": _ISSUED_AT,
            "source_evidence_ids": [],
        }
    )
    terminal_artifacts = dict(artifacts) | {"budget": exhausted_budget}
    budget_reference = _artifact_ref(exhausted_budget)
    governance_reference = _artifact_ref(artifacts["governance"])
    cause = _event_common(
        researching.event_record.event.sequence_no + 1,
        researching.event_record.event_hash,
        "automarkov.budget-exhausted.v1",
        "BudgetExhausted",
    ) | {
        "actor_principal_id": "principal_fixed_commit_runner",
        "actor_process_execution_id": "execution_lifecycle_terminal",
        "budget_kind": "global_cost",
        "budget_policy_artifact_id": governance_reference["artifact_id"],
        "budget_policy_payload_hash": governance_reference["payload_hash"],
        "budget_snapshot_artifact_id": budget_reference["artifact_id"],
        "budget_snapshot_payload_hash": budget_reference["payload_hash"],
        "canonical_unit": "microunits",
        "limit": 10,
        "consumed": 10,
        "reserved": 0,
        "cause_receipt_artifact_id": governance_reference["artifact_id"],
        "cause_receipt_payload_hash": governance_reference["payload_hash"],
        "phase": "research",
        "reason_code": "budget_exhausted",
        "exhausted_at": _ISSUED_AT,
    }
    cause_record = parse_event_record(encode_event_record(cause))
    transition = _transition_event(
        sequence_no=cause_record.event.sequence_no + 1,
        previous_event_hash=cause_record.event_hash,
        from_state="RESEARCHING",
        to_state="BUDGET_EXHAUSTED",
        trigger=cause_record,
        budget=exhausted_budget,
    ) | {
        "actor_principal_id": "principal_fixed_commit_runner",
        "actor_process_execution_id": "execution_lifecycle_terminal",
        "reason_code": "budget_exhausted",
    }
    command = _terminal_command(terminal_artifacts, researching)
    command["events"] = [cause, transition]

    committed = _commit(lifecycle_repository, command)
    assert committed.run_view.state is RunState.BUDGET_EXHAUSTED
    assert tuple(record.event.sequence_no for record in committed.event_records) == (
        researching.event_record.event.sequence_no + 1,
        researching.event_record.event.sequence_no + 2,
    )


class _DictSubclass(dict[str, object]):
    pass


class _ForgedCommand(BaseModel):
    command_type: str


def test_repository_lifecycle_ingress_accepts_only_exact_raw_dicts() -> None:
    raw_root = _root_event(
        {
            "run_manifest": type(
                "ManifestRef",
                (),
                {
                    "artifact_id": ArtifactId(root="artifact_" + "1" * 64),
                    "payload_hash": type(
                        "PayloadHash",
                        (),
                        {"root": "sha256:" + "1" * 64},
                    )(),
                },
            )()
        }
    )
    valid_append = {
        "schema_version": "automarkov.lifecycle-command.v1",
        "command_type": "append_run_events",
        "command_id": _uuid7(40_000),
        "actor_principal_id": "principal_lifecycle_fixture",
        "issued_at": _ISSUED_AT,
        "idempotency_key": "raw-ingress-command",
        "run_id": _RUN_ID,
        "expected_state": None,
        "expected_head": None,
        "events": [raw_root],
    }
    valid_project = {
        "schema_version": "automarkov.run-projection-request.v1",
        "run_id": _RUN_ID,
        "as_of_sequence_no": 0,
        "as_of_event_head_hash": _HASH_A,
        "projector_version": RUN_PROJECTOR_VERSION,
        "projector_hash": RUN_PROJECTOR_HASH,
    }
    assert validate_lifecycle_command(valid_append).command_type == "append_run_events"
    assert validate_projection_request(valid_project).run_id == _RUN_ID

    nested_subclass = dict(valid_append)
    nested_subclass["events"] = [_DictSubclass(raw_root)]
    for forbidden in (
        _DictSubclass(valid_append),
        _ForgedCommand.model_construct(command_type="append_run_events"),
        valid_append | {"unexpected": "not closed"},
        nested_subclass,
    ):
        with pytest.raises(EventSchemaError):
            validate_lifecycle_command(forbidden)
    for forbidden in (
        _DictSubclass(valid_project),
        _ForgedCommand.model_construct(command_type="project"),
        valid_project | {"unexpected": "not closed"},
    ):
        with pytest.raises(EventSchemaError):
            validate_projection_request(forbidden)
