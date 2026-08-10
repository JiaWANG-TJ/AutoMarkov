from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import model_validator

from automarkov.adapters import (
    InMemoryArtifactRepository,
    SqliteArtifactRepository,
)
from automarkov.canonical import (
    CanonicalPayloadCodec,
    FrozenSequence,
    FrozenStringMapping,
    SafeCanonicalInt,
    canonical_json_bytes,
)
from automarkov.domain import StrictFrozenModel
from automarkov.errors import ArtifactIntegrityError, EventReplayConflictError
from automarkov.lifecycle import (
    RUN_PROJECTOR_HASH,
    RUN_PROJECTOR_VERSION,
    ZERO_EVENT_HASH,
    ArtifactReference,
    BudgetSnapshot,
    CrossRunLifecycleCommitReceipt,
    ExecutionAttestation,
    LifecycleCommitReceipt,
    ProcessExecutionTerminalRecord,
    RunAuditProjection,
    TerminalResult,
    encode_event_record,
    parse_event_record,
)
from automarkov.public import CommandAuthority, CommandPrincipalBinding
from automarkov.repository import ArtifactSchemaRegistry, ParentBinding

_ISSUED_AT = "2026-08-10T12:00:00Z"
_PARENT_RUN_ID = "run_cross_parent"
_CHILD_RUN_ID = "run_cross_child"
_PRINCIPAL_ID = "principal_cross_run_authority"
_PROCESS_ID = "execution_cross_run_control"
_SIGNING_KEY_ID = "key_cross_run_authority"
_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"\x39" * 32)
_AUTHORITY = CommandAuthority(
    "authority_cross_run_tests",
    (
        CommandPrincipalBinding(_PRINCIPAL_ID, None),
        CommandPrincipalBinding(_PRINCIPAL_ID, _PROCESS_ID),
    ),
)


class _FixtureArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.cross-run-fixture.v1"]
    name: str


class _ReplacementPolicy(StrictFrozenModel):
    schema_version: Literal["automarkov.replacement-policy.v1"]
    signing_domain: Literal["AutoMarkov-Run-Replacement-Policy-v1"]
    experiment_id: str | None
    allowed_causes: FrozenSequence[
        Literal["approval_revocation", "runtime_identity_replacement"]
    ]
    authority_principal_id: str
    signing_key_id: str
    authority_status: Literal["active"]
    root_ordinal: SafeCanonicalInt
    child_ordinal_increment: SafeCanonicalInt
    eligibility_by_cause: FrozenStringMapping[str]
    maximum_child_count: SafeCanonicalInt
    issued_at: str
    nonce_b64url: str
    signature_b64url: str


class _SlotDecision(StrictFrozenModel):
    schema_version: Literal["automarkov.slot-decision.v1"]
    parent_run_id: str
    child_run_id: str
    replacement_eligibility: Literal[
        "confirmatory_slot_reused",
        "new_nonconfirmatory_slot",
        "slot_terminal_failure",
    ]
    replacement_policy: ArtifactReference


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
    revocation_authorities: FrozenSequence[str]


class _SecurityContext(StrictFrozenModel):
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
    def require_nonnegative_ordinal(self) -> _SecurityContext:
        if self.root_ordinal < 0 or self.max_clock_skew_ms < 0:
            raise ValueError("manifest ordinal and clock skew must be nonnegative")
        return self


class _RunManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.cross-run-manifest.v1"]
    event_security_context: _SecurityContext
    replacement_ordinal: SafeCanonicalInt
    clarification_continuation_ordinal: SafeCanonicalInt
    replacement_policy: ArtifactReference
    parent_run_id: str | None
    parent_run_superseded_event_id: str | None
    supersession_cause: str | None
    parent_clarification_result: ArtifactReference | None = None
    parent_terminal_result: ArtifactReference | None = None
    signed_answer_bundle: ArtifactReference | None = None
    continuation_policy: ArtifactReference | None = None


class _ContinuationPolicy(StrictFrozenModel):
    schema_version: Literal["automarkov.clarification-continuation-policy.v1"]
    signing_domain: Literal["AutoMarkov-Clarification-Continuation-Policy-v1"]
    authority_principal_id: str
    signing_key_id: str
    authority_status: Literal["active"]
    child_ordinal_increment: SafeCanonicalInt
    maximum_child_count: SafeCanonicalInt
    experiment_eligibility: Literal["nonconfirmatory"]
    allowed_answer_artifact_kinds: FrozenSequence[Literal["signed_answer_bundle"]]
    budget_reset_rule: Literal["fresh_child_budget"]
    runtime_reset_rule: Literal["revalidate_runtime"]
    issued_at: str
    nonce_b64url: str
    signature_b64url: str


class _SignedAnswerBundle(StrictFrozenModel):
    schema_version: Literal["automarkov.signed-answer-bundle.v1"]
    signing_domain: Literal["AutoMarkov-Signed-Answer-Bundle-v1"]
    principal_id: str
    signing_key_id: str
    answer_hash: str
    issued_at: str
    nonce_b64url: str
    signature_b64url: str


class _ReplacementJobManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.replacement-job-manifest.v1"]
    parent_run_id: str
    child_run_id: str
    old_run_manifest: ArtifactReference
    child_run_manifest: ArtifactReference
    replacement_policy: ArtifactReference
    slot_decision: ArtifactReference
    prerequisite_event_id: str
    prerequisite_event_hash: str
    run_superseded_event_id: str
    replacement_run_created_event_id: str


ArtifactRepositoryAdapter = InMemoryArtifactRepository | SqliteArtifactRepository


def _registry() -> ArtifactSchemaRegistry:
    registry = ArtifactSchemaRegistry()
    for artifact_type in (
        "governance_report",
        "payload_output",
        "resource_usage",
    ):
        registry.register(
            artifact_type,
            "automarkov.cross-run-fixture.v1",
            _FixtureArtifact,
            direct_parent_artifact_types=(),
        )
    registry.register(
        "budget_snapshot",
        "automarkov.budget-snapshot.v1",
        BudgetSnapshot,
        direct_parent_artifact_types=(),
    )
    registry.register(
        "replacement_policy",
        "automarkov.replacement-policy.v1",
        _ReplacementPolicy,
        direct_parent_artifact_types=(),
    )
    registry.register(
        "clarification_continuation_policy",
        "automarkov.clarification-continuation-policy.v1",
        _ContinuationPolicy,
        direct_parent_artifact_types=(),
    )
    registry.register(
        "signed_answer_bundle",
        "automarkov.signed-answer-bundle.v1",
        _SignedAnswerBundle,
        direct_parent_artifact_types=(),
    )
    registry.register(
        "slot_decision",
        "automarkov.slot-decision.v1",
        _SlotDecision,
        direct_parent_artifact_types=(),
    )
    registry.register(
        "run_manifest",
        "automarkov.cross-run-manifest.v1",
        _RunManifest,
        direct_parent_artifact_types=(),
    )
    registry.register(
        "job_manifest",
        "automarkov.replacement-job-manifest.v1",
        _ReplacementJobManifest,
        direct_parent_artifact_types=(),
    )
    registry.register(
        "process_execution_terminal_record",
        "automarkov.process-execution-terminal-record.v1",
        ProcessExecutionTerminalRecord,
        payload_parent_bindings=(
            ParentBinding(
                artifact_id_path="job_manifest.artifact_id",
                payload_hash_path="job_manifest.payload_hash",
                allowed_artifact_types=("job_manifest",),
                cardinality="one",
            ),
            ParentBinding(
                artifact_id_path="payload_outputs.*.artifact_id",
                payload_hash_path="payload_outputs.*.payload_hash",
                allowed_artifact_types=("payload_output",),
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
                allowed_artifact_types=("job_manifest",),
                cardinality="one",
            ),
            ParentBinding(
                artifact_id_path="payload_outputs.*.artifact_id",
                payload_hash_path="payload_outputs.*.payload_hash",
                allowed_artifact_types=("payload_output",),
                cardinality="many",
            ),
            ParentBinding(
                artifact_id_path=("process_execution_terminal_record.artifact_id"),
                payload_hash_path=("process_execution_terminal_record.payload_hash"),
                allowed_artifact_types=("process_execution_terminal_record",),
                cardinality="one",
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
    registry.register(
        "execution_attestation",
        "automarkov.execution-attestation.v1",
        ExecutionAttestation,
        payload_parent_bindings=(
            ParentBinding(
                artifact_id_path="job_manifest.artifact_id",
                payload_hash_path="job_manifest.payload_hash",
                allowed_artifact_types=("job_manifest",),
                cardinality="one",
            ),
            ParentBinding(
                artifact_id_path="payload_outputs.*.artifact_id",
                payload_hash_path="payload_outputs.*.payload_hash",
                allowed_artifact_types=("payload_output",),
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
                cardinality="one",
            ),
        ),
    )
    registry.freeze()
    return registry


def _uuid7(index: int) -> str:
    timestamp_ms = int(datetime.fromisoformat(_ISSUED_AT).timestamp() * 1_000)
    return str(UUID(int=(timestamp_ms << 80) | (7 << 76) | (2 << 62) | index))


def _nonce(index: int) -> str:
    return base64.urlsafe_b64encode(index.to_bytes(16, "big")).decode().rstrip("=")


def _sign_payload(payload: dict[str, object]) -> dict[str, object]:
    signed = dict(payload)
    signed["signature_b64url"] = (
        base64.urlsafe_b64encode(_PRIVATE_KEY.sign(canonical_json_bytes(payload)))
        .decode()
        .rstrip("=")
    )
    return signed


def _reference(result: Any) -> dict[str, str]:
    return {
        "artifact_id": result.artifact_id.root,
        "payload_hash": result.payload_hash.root,
    }


def _put(
    repository: ArtifactRepositoryAdapter,
    artifact_type: str,
    payload: dict[str, object],
) -> Any:
    return repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": artifact_type,
            "payload_bytes": canonical_json_bytes(payload),
            "parent_artifact_ids": [],
            "created_by": _PRINCIPAL_ID,
            "created_at": _ISSUED_AT,
            "source_evidence_ids": [],
        }
    )


def _security_context(
    run_id: str,
    root_ordinal: int,
    creation_policy: dict[str, str],
    approval_policy: dict[str, str],
) -> dict[str, object]:
    public_key = (
        base64.urlsafe_b64encode(_PRIVATE_KEY.public_key().public_bytes_raw())
        .decode()
        .rstrip("=")
    )
    event_types = (
        ["ClarificationChildRunCreated", "ReplacementRunCreated"]
        if root_ordinal
        else [
            "ClarificationRequested",
            "LlmRuntimeDegraded",
            "RunCreated",
            "RunSuperseded",
            "StageGatePassed",
            "StateTransitioned",
            "WaitResolved",
            "WaitingRuntime",
        ]
    )
    return {
        "schema_version": "automarkov.run-event-security-context.v1",
        "run_id": run_id,
        "experiment_id": None,
        "root_ordinal": root_ordinal,
        "creation_policy": creation_policy,
        "max_clock_skew_ms": 0,
        "actor_capabilities": [
            {
                "principal_id": _PRINCIPAL_ID,
                "process_execution_id": None,
                "allowed_event_types": sorted(event_types),
            },
            *(
                [
                    {
                        "principal_id": _PRINCIPAL_ID,
                        "process_execution_id": _PROCESS_ID,
                        "allowed_event_types": [
                            "ClarificationRequested",
                            "StateTransitioned",
                        ],
                    }
                ]
                if not root_ordinal
                else []
            ),
        ],
        "signing_keys": [
            {
                "signing_key_id": _SIGNING_KEY_ID,
                "principal_id": _PRINCIPAL_ID,
                "signature_algorithm": "Ed25519",
                "public_key_b64url": public_key,
                "not_before": "2026-08-09T00:00:00Z",
                "not_after": "2026-08-11T00:00:00Z",
                "revoked_at": None,
            }
        ],
        "run_creation": {
            "creation_principal_id": _PRINCIPAL_ID,
            "signing_key_id": _SIGNING_KEY_ID,
        },
        "approval": {
            "approval_principal_id": _PRINCIPAL_ID,
            "approval_principal_kind": "interactive_user",
            "signing_key_id": _SIGNING_KEY_ID,
            "policy_contract": approval_policy,
            "policy_source_hash": None,
            "policy_image_hash": None,
            "policy_version": None,
            "revocation_authorities": [],
        },
    }


def _event_record(raw: dict[str, object]) -> Any:
    return parse_event_record(encode_event_record(raw))


def _common_event(
    *,
    index: int,
    sequence_no: int,
    previous_event_hash: str,
    schema_version: str,
    event_type: str,
    process_execution_id: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "event_type": event_type,
        "event_id": _uuid7(index),
        "experiment_id": None,
        "run_id": _PARENT_RUN_ID,
        "actor_principal_id": _PRINCIPAL_ID,
        "actor_process_execution_id": process_execution_id,
        "issued_at": _ISSUED_AT,
        "sequence_no": sequence_no,
        "previous_event_hash": previous_event_hash,
    }


def _expected_reference(
    model_type: type[Any],
    payload: dict[str, object],
    *,
    artifact_type: str,
    parents: list[dict[str, str]],
) -> dict[str, str]:
    codec = CanonicalPayloadCodec(model_type)
    payload_bytes = codec.encode(payload)
    payload_hash = f"sha256:{hashlib.sha256(payload_bytes).hexdigest()}"
    parent_ids = sorted({parent["artifact_id"] for parent in parents})
    envelope = {
        "artifact_type": artifact_type,
        "schema_version": payload["schema_version"],
        "schema_id": codec.schema_id,
        "payload_media_type": ("application/vnd.automarkov.canonical-payload+json"),
        "payload_hash": payload_hash,
        "parent_artifact_ids": parent_ids,
        "created_by": _PRINCIPAL_ID,
        "created_at": _ISSUED_AT,
        "source_evidence_ids": [],
    }
    return {
        "artifact_id": "artifact_"
        + hashlib.sha256(canonical_json_bytes(envelope)).hexdigest(),
        "payload_hash": payload_hash,
    }


def _replacement_fixture(
    repository: ArtifactRepositoryAdapter,
) -> tuple[dict[str, object], Any]:
    governance = _put(
        repository,
        "governance_report",
        {"schema_version": "automarkov.cross-run-fixture.v1", "name": "governance"},
    )
    approval_policy = _put(
        repository,
        "governance_report",
        {"schema_version": "automarkov.cross-run-fixture.v1", "name": "approval"},
    )
    governance_ref = _reference(governance)
    approval_policy_ref = _reference(approval_policy)
    policy_payload = _sign_payload(
        {
            "schema_version": "automarkov.replacement-policy.v1",
            "signing_domain": "AutoMarkov-Run-Replacement-Policy-v1",
            "experiment_id": None,
            "allowed_causes": [
                "approval_revocation",
                "runtime_identity_replacement",
            ],
            "authority_principal_id": _PRINCIPAL_ID,
            "signing_key_id": _SIGNING_KEY_ID,
            "authority_status": "active",
            "root_ordinal": 0,
            "child_ordinal_increment": 1,
            "eligibility_by_cause": {
                "approval_revocation": "slot_terminal_failure",
                "runtime_identity_replacement": "confirmatory_slot_reused",
            },
            "maximum_child_count": 1,
            "issued_at": _ISSUED_AT,
            "nonce_b64url": _nonce(1),
        }
    )
    policy = _put(repository, "replacement_policy", policy_payload)
    policy_ref = _reference(policy)
    superseded_event_id = _uuid7(20)
    child_event_id = _uuid7(22)
    parent_manifest = _put(
        repository,
        "run_manifest",
        {
            "schema_version": "automarkov.cross-run-manifest.v1",
            "event_security_context": _security_context(
                _PARENT_RUN_ID,
                0,
                governance_ref,
                approval_policy_ref,
            ),
            "replacement_ordinal": 0,
            "clarification_continuation_ordinal": 0,
            "replacement_policy": policy_ref,
            "parent_run_id": None,
            "parent_run_superseded_event_id": None,
            "supersession_cause": None,
        },
    )
    child_manifest = _put(
        repository,
        "run_manifest",
        {
            "schema_version": "automarkov.cross-run-manifest.v1",
            "event_security_context": _security_context(
                _CHILD_RUN_ID,
                1,
                governance_ref,
                approval_policy_ref,
            ),
            "replacement_ordinal": 1,
            "clarification_continuation_ordinal": 0,
            "replacement_policy": policy_ref,
            "parent_run_id": _PARENT_RUN_ID,
            "parent_run_superseded_event_id": superseded_event_id,
            "supersession_cause": "runtime_identity_replacement",
        },
    )
    failure_report = _put(
        repository,
        "governance_report",
        {"schema_version": "automarkov.cross-run-fixture.v1", "name": "failure"},
    )
    budget = _put(
        repository,
        "budget_snapshot",
        {
            "schema_version": "automarkov.budget-snapshot.v1",
            "contract_hash": "sha256:" + "9" * 64,
            "counters": [
                {"metric": metric, "consumed": 0, "limit": 10}
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
        },
    )
    output = _put(
        repository,
        "payload_output",
        {"schema_version": "automarkov.cross-run-fixture.v1", "name": "output"},
    )
    resource_usage = _put(
        repository,
        "resource_usage",
        {"schema_version": "automarkov.cross-run-fixture.v1", "name": "usage"},
    )

    root_event = _sign_payload(
        {
            "schema_version": "automarkov.run-created.v1",
            "event_type": "RunCreated",
            "signing_domain": "AutoMarkov-Run-Created-v1",
            "event_id": _uuid7(10),
            "experiment_id": None,
            "run_id": _PARENT_RUN_ID,
            "actor_principal_id": _PRINCIPAL_ID,
            "issued_at": _ISSUED_AT,
            "sequence_no": 0,
            "previous_event_hash": ZERO_EVENT_HASH,
            "run_manifest_artifact_id": parent_manifest.artifact_id.root,
            "run_manifest_payload_hash": parent_manifest.payload_hash.root,
            "initial_state": "RECEIVED",
            "creation_principal_id": _PRINCIPAL_ID,
            "reason_code": "run_created",
            "nonce_b64url": _nonce(10),
            "signing_key_id": _SIGNING_KEY_ID,
            "signature_algorithm": "Ed25519",
        }
    )
    root = repository.commit(
        {
            "schema_version": "automarkov.lifecycle-command.v1",
            "command_type": "append_run_events",
            "command_id": _uuid7(100),
            "actor_principal_id": _PRINCIPAL_ID,
            "issued_at": _ISSUED_AT,
            "idempotency_key": "cross-run-root",
            "run_id": _PARENT_RUN_ID,
            "expected_state": None,
            "expected_head": None,
            "events": [root_event],
        },
        context=_AUTHORITY.issue(_PRINCIPAL_ID, None, _ISSUED_AT),
    )
    assert isinstance(root, LifecycleCommitReceipt)
    intake_gate = _common_event(
        index=11,
        sequence_no=1,
        previous_event_hash=root.after_head.event_hash,
        schema_version="automarkov.stage-gate-passed.v1",
        event_type="StageGatePassed",
    ) | {
        "gate_id": "INTAKE_SCHEMA_BUDGET_AUTHORITY",
        "gate_version": "gate_v1",
        "gate_contract_hash": "sha256:" + "8" * 64,
        "subject_artifact_references": [],
        "gate_report": _reference(failure_report),
        "from_state": "RECEIVED",
        "to_state": "RESEARCHING",
        "reason_code": "intake_accepted",
        "result": "passed",
    }
    intake_record = _event_record(intake_gate)
    received_transition = _common_event(
        index=15,
        sequence_no=2,
        previous_event_hash=intake_record.event_hash,
        schema_version="automarkov.state-transitioned.v1",
        event_type="StateTransitioned",
    ) | {
        "from_state": "RECEIVED",
        "to_state": "RESEARCHING",
        "trigger_event_id": intake_gate["event_id"],
        "trigger_event_hash": intake_record.event_hash,
        "input_artifact_ids": [],
        "gate_report_artifact_id": failure_report.artifact_id.root,
        "gate_report_payload_hash": failure_report.payload_hash.root,
        "budget_snapshot_artifact_id": budget.artifact_id.root,
        "budget_snapshot_payload_hash": budget.payload_hash.root,
        "reason_code": "intake_accepted",
    }
    researching = repository.commit(
        {
            "schema_version": "automarkov.lifecycle-command.v1",
            "command_type": "append_run_events",
            "command_id": _uuid7(101),
            "actor_principal_id": _PRINCIPAL_ID,
            "issued_at": _ISSUED_AT,
            "idempotency_key": "cross-run-researching",
            "run_id": _PARENT_RUN_ID,
            "expected_state": "RECEIVED",
            "expected_head": root.after_head.model_dump(mode="json"),
            "events": [intake_gate, received_transition],
        },
        context=_AUTHORITY.issue(_PRINCIPAL_ID, None, _ISSUED_AT),
    )
    assert isinstance(researching, LifecycleCommitReceipt)
    failure_ref = _reference(failure_report)
    degraded = _common_event(
        index=12,
        sequence_no=3,
        previous_event_hash=researching.after_head.event_hash,
        schema_version="automarkov.llm-runtime-degraded.v1",
        event_type="LlmRuntimeDegraded",
    ) | {
        "dependency_identity_hash": "sha256:" + "a" * 64,
        "failed_gate_id": "gate_local_llm",
        "failure_report": failure_ref,
        "affected_state": "RESEARCHING",
    }
    degraded_record = _event_record(degraded)
    waiting = _common_event(
        index=13,
        sequence_no=4,
        previous_event_hash=degraded_record.event_hash,
        schema_version="automarkov.waiting-runtime.v1",
        event_type="WaitingRuntime",
    ) | {
        "resume_state": "RESEARCHING",
        "wait_reason_code": "local_llm_unavailable",
        "trigger_event_id": degraded["event_id"],
        "trigger_event_hash": degraded_record.event_hash,
        "failure_report_artifact_id": failure_ref["artifact_id"],
        "failure_report_payload_hash": failure_ref["payload_hash"],
        "recovery_gate_id": "gate_local_llm",
        "recovery_condition_hash": "sha256:" + "b" * 64,
        "entered_at": _ISSUED_AT,
        "dependency_kind": "local_llm",
        "profile_id": "profile_local_llm",
        "process_execution_id": None,
        "protocol_edge_id": None,
        "dependency_identity_hash": "sha256:" + "a" * 64,
        "failed_readiness_gate_id": "gate_local_llm",
    }
    waiting_record = _event_record(waiting)
    wait_transition = _common_event(
        index=14,
        sequence_no=5,
        previous_event_hash=waiting_record.event_hash,
        schema_version="automarkov.state-transitioned.v1",
        event_type="StateTransitioned",
    ) | {
        "from_state": "RESEARCHING",
        "to_state": "WAITING_RUNTIME",
        "trigger_event_id": waiting["event_id"],
        "trigger_event_hash": waiting_record.event_hash,
        "input_artifact_ids": [],
        "gate_report_artifact_id": None,
        "gate_report_payload_hash": None,
        "budget_snapshot_artifact_id": budget.artifact_id.root,
        "budget_snapshot_payload_hash": budget.payload_hash.root,
        "reason_code": "test_transition",
    }
    waiting_commit = repository.commit(
        {
            "schema_version": "automarkov.lifecycle-command.v1",
            "command_type": "append_run_events",
            "command_id": _uuid7(102),
            "actor_principal_id": _PRINCIPAL_ID,
            "issued_at": _ISSUED_AT,
            "idempotency_key": "cross-run-waiting",
            "run_id": _PARENT_RUN_ID,
            "expected_state": "RESEARCHING",
            "expected_head": researching.after_head.model_dump(mode="json"),
            "events": [degraded, waiting, wait_transition],
        },
        context=_AUTHORITY.issue(_PRINCIPAL_ID, None, _ISSUED_AT),
    )
    assert isinstance(waiting_commit, LifecycleCommitReceipt)

    superseded = _sign_payload(
        {
            "schema_version": "automarkov.run-superseded.v1",
            "event_type": "RunSuperseded",
            "signing_domain": "AutoMarkov-Run-Superseded-v1",
            "event_id": superseded_event_id,
            "experiment_id": None,
            "run_id": _PARENT_RUN_ID,
            "sequence_no": 6,
            "previous_event_hash": waiting_commit.after_head.event_hash,
            "supersession_cause": "runtime_identity_replacement",
            "child_run_id": _CHILD_RUN_ID,
            "replacement_ordinal": 1,
            "old_run_manifest_artifact_id": parent_manifest.artifact_id.root,
            "old_run_manifest_payload_hash": parent_manifest.payload_hash.root,
            "child_run_manifest_artifact_id": child_manifest.artifact_id.root,
            "child_run_manifest_payload_hash": child_manifest.payload_hash.root,
            "replacement_policy_artifact_id": policy.artifact_id.root,
            "replacement_policy_payload_hash": policy.payload_hash.root,
            "replacement_eligibility": "confirmatory_slot_reused",
            "replacement_authority_principal_id": _PRINCIPAL_ID,
            "reason_code": "runtime_identity_replacement",
            "issued_at": _ISSUED_AT,
            "nonce_b64url": _nonce(20),
            "signing_key_id": _SIGNING_KEY_ID,
            "failed_waiting_event_id": waiting["event_id"],
            "failed_readiness_gate_id": "gate_local_llm",
            "old_dependency_identity_hash": "sha256:" + "a" * 64,
            "new_dependency_identity_hash": "sha256:" + "c" * 64,
        }
    )
    superseded_record = _event_record(superseded)
    terminal_transition = _common_event(
        index=21,
        sequence_no=7,
        previous_event_hash=superseded_record.event_hash,
        schema_version="automarkov.state-transitioned.v1",
        event_type="StateTransitioned",
        process_execution_id=_PROCESS_ID,
    ) | {
        "from_state": "WAITING_RUNTIME",
        "to_state": "CANCELLED",
        "trigger_event_id": superseded_event_id,
        "trigger_event_hash": superseded_record.event_hash,
        "input_artifact_ids": [],
        "gate_report_artifact_id": None,
        "gate_report_payload_hash": None,
        "budget_snapshot_artifact_id": budget.artifact_id.root,
        "budget_snapshot_payload_hash": budget.payload_hash.root,
        "reason_code": "run_superseded",
    }
    transition_record = _event_record(terminal_transition)
    child_created = _sign_payload(
        {
            "schema_version": "automarkov.replacement-run-created.v1",
            "event_type": "ReplacementRunCreated",
            "signing_domain": "AutoMarkov-Replacement-Run-Created-v1",
            "event_id": child_event_id,
            "experiment_id": None,
            "run_id": _CHILD_RUN_ID,
            "sequence_no": 0,
            "previous_event_hash": ZERO_EVENT_HASH,
            "run_manifest_artifact_id": child_manifest.artifact_id.root,
            "run_manifest_payload_hash": child_manifest.payload_hash.root,
            "parent_run_id": _PARENT_RUN_ID,
            "parent_run_superseded_event_id": superseded_event_id,
            "supersession_cause": "runtime_identity_replacement",
            "replacement_ordinal": 1,
            "replacement_policy_artifact_id": policy.artifact_id.root,
            "replacement_policy_payload_hash": policy.payload_hash.root,
            "replacement_authority_principal_id": _PRINCIPAL_ID,
            "issued_at": _ISSUED_AT,
            "nonce_b64url": _nonce(22),
            "signing_key_id": _SIGNING_KEY_ID,
        }
    )
    slot_payload = {
        "schema_version": "automarkov.slot-decision.v1",
        "parent_run_id": _PARENT_RUN_ID,
        "child_run_id": _CHILD_RUN_ID,
        "replacement_eligibility": "confirmatory_slot_reused",
        "replacement_policy": policy_ref,
    }
    slot = _put(repository, "slot_decision", slot_payload)
    job_payload = {
        "schema_version": "automarkov.replacement-job-manifest.v1",
        "parent_run_id": _PARENT_RUN_ID,
        "child_run_id": _CHILD_RUN_ID,
        "old_run_manifest": _reference(parent_manifest),
        "child_run_manifest": _reference(child_manifest),
        "replacement_policy": policy_ref,
        "slot_decision": _reference(slot),
        "prerequisite_event_id": cast(str, waiting["event_id"]),
        "prerequisite_event_hash": waiting_record.event_hash,
        "run_superseded_event_id": superseded_event_id,
        "replacement_run_created_event_id": child_event_id,
    }
    job = _put(repository, "job_manifest", job_payload)
    process_payload = {
        "schema_version": "automarkov.process-execution-terminal-record.v1",
        "signing_domain": "AutoMarkov-ProcessExecutionTerminalRecord-v1",
        "experiment_id": None,
        "run_id": _PARENT_RUN_ID,
        "job_id": "job_replacement_control",
        "process_execution_id": _PROCESS_ID,
        "profile_id": "profile_cross_run_control",
        "principal_id": _PRINCIPAL_ID,
        "job_manifest": _reference(job),
        "status": "success",
        "exit_code": 0,
        "reason_code": "replacement_control_succeeded",
        "started_at": _ISSUED_AT,
        "finished_at": _ISSUED_AT,
        "stdout_hash": "sha256:" + "d" * 64,
        "stderr_hash": "sha256:" + "e" * 64,
        "payload_outputs": [_reference(output)],
        "resource_usage": _reference(resource_usage),
        "network_log_hash": "sha256:" + "1" * 64,
        "mount_attestation_hash": "sha256:" + "2" * 64,
        "capability_decision_hash": "sha256:" + "3" * 64,
        "egress_log_hash": "sha256:" + "4" * 64,
        "created_at": _ISSUED_AT,
    }
    process_ref = _expected_reference(
        ProcessExecutionTerminalRecord,
        process_payload,
        artifact_type="process_execution_terminal_record",
        parents=[_reference(job), _reference(output), _reference(resource_usage)],
    )
    terminal_payload = {
        "schema_version": "automarkov.terminal-result.v1",
        "signing_domain": "AutoMarkov-TerminalResult-v1",
        "run_id": _PARENT_RUN_ID,
        "experiment_id": None,
        "fixed_commit_job_manifest": _reference(job),
        "process_execution_terminal_record": process_ref,
        "process_execution_id": _PROCESS_ID,
        "terminal_event": {
            "event_id": terminal_transition["event_id"],
            "sequence_no": 7,
            "event_hash": transition_record.event_hash,
        },
        "terminal_snapshot_event_head": {
            "run_id": _PARENT_RUN_ID,
            "sequence_no": 7,
            "event_hash": transition_record.event_hash,
        },
        "terminal_state": "CANCELLED",
        "terminal_reason_code": "run_superseded",
        "payload_outputs": [_reference(output)],
        "terminal_time_approvals": [],
        "projector_version": RUN_PROJECTOR_VERSION,
        "projector_hash": RUN_PROJECTOR_HASH,
        "created_at": _ISSUED_AT,
    }
    terminal_ref = _expected_reference(
        TerminalResult,
        terminal_payload,
        artifact_type="terminal_result",
        parents=[_reference(job), process_ref, _reference(output)],
    )
    attestation_payload = _sign_payload(
        {
            "schema_version": "automarkov.execution-attestation.v1",
            "signing_domain": "AutoMarkov-Execution-Attestation-v1",
            "experiment_id": None,
            "run_id": _PARENT_RUN_ID,
            "job_id": "job_replacement_control",
            "process_execution_id": _PROCESS_ID,
            "profile_id": "profile_cross_run_control",
            "principal_id": _PRINCIPAL_ID,
            "job_manifest": _reference(job),
            "process_terminal_record": process_ref,
            "payload_outputs": [_reference(output)],
            "terminal_result": terminal_ref,
            "network_policy_hash": "sha256:" + "5" * 64,
            "mount_table_hash": "sha256:" + "6" * 64,
            "capability_decision_log_hash": "sha256:" + "7" * 64,
            "actual_phase_transition": {
                "from_phase": "replacement_control",
                "to_phase": "committed",
                "transitioned_at": _ISSUED_AT,
            },
            "egress_decision_log_hash": "sha256:" + "8" * 64,
            "egress_revoked_at": _ISSUED_AT,
            "issued_at": _ISSUED_AT,
            "nonce_b64url": _nonce(30),
            "signing_key_id": _SIGNING_KEY_ID,
            "signature_algorithm": "Ed25519",
        }
    )
    command = {
        "schema_version": "automarkov.lifecycle-command.v1",
        "command_type": "create_replacement_run",
        "command_id": _uuid7(103),
        "actor_principal_id": _PRINCIPAL_ID,
        "issued_at": _ISSUED_AT,
        "idempotency_key": "cross-run-replacement",
        "parent_run_id": _PARENT_RUN_ID,
        "child_run_id": _CHILD_RUN_ID,
        "expected_parent_state": "WAITING_RUNTIME",
        "expected_parent_head": waiting_commit.after_head.model_dump(mode="json"),
        "expected_child_head": None,
        "old_run_manifest": _reference(parent_manifest),
        "child_run_manifest": _reference(child_manifest),
        "replacement_policy": policy_ref,
        "cause_prerequisite": {
            "prerequisite_type": "runtime_identity_replacement",
            "failed_waiting_event": {
                "event_id": waiting["event_id"],
                "sequence_no": 4,
                "event_hash": waiting_record.event_hash,
            },
            "failed_readiness_gate_id": "gate_local_llm",
            "old_dependency_identity_hash": "sha256:" + "a" * 64,
            "new_dependency_identity_hash": "sha256:" + "c" * 64,
        },
        "slot_decision": _reference(slot),
        "replacement_eligibility": "confirmatory_slot_reused",
        "fixed_commit_job_manifest": _reference(job),
        "process_terminal_record": process_payload,
        "run_superseded_event": superseded,
        "parent_terminal_transition": terminal_transition,
        "replacement_run_created_event": child_created,
        "execution_attestation": attestation_payload,
        "projector_version": RUN_PROJECTOR_VERSION,
        "projector_hash": RUN_PROJECTOR_HASH,
    }
    return command, waiting_commit


def _clarification_fixture(
    repository: ArtifactRepositoryAdapter,
) -> tuple[dict[str, object], Any]:
    replacement_command, waiting_commit = _replacement_fixture(repository)
    prerequisite = cast(
        dict[str, object],
        replacement_command["cause_prerequisite"],
    )
    waiting_reference = cast(dict[str, object], prerequisite["failed_waiting_event"])
    report_reference = cast(dict[str, str], replacement_command["slot_decision"])
    terminal_transition = cast(
        dict[str, object],
        replacement_command["parent_terminal_transition"],
    )
    budget_reference = {
        "artifact_id": terminal_transition["budget_snapshot_artifact_id"],
        "payload_hash": terminal_transition["budget_snapshot_payload_hash"],
    }

    events: list[dict[str, object]] = []
    previous_hash = waiting_commit.after_head.event_hash
    wait_resolved = _common_event(
        index=40,
        sequence_no=6,
        previous_event_hash=previous_hash,
        schema_version="automarkov.wait-resolved.v1",
        event_type="WaitResolved",
    ) | {
        "wait_kind": "runtime",
        "waiting_event_id": waiting_reference["event_id"],
        "waiting_event_hash": waiting_reference["event_hash"],
        "resume_state": "RESEARCHING",
        "recovery_gate_id": prerequisite["failed_readiness_gate_id"],
        "recovery_report_artifact_id": report_reference["artifact_id"],
        "recovery_report_payload_hash": report_reference["payload_hash"],
        "identity_hash": prerequisite["old_dependency_identity_hash"],
        "resolved_at": _ISSUED_AT,
    }
    events.append(wait_resolved)
    resolved_record = _event_record(wait_resolved)
    resume_transition = _common_event(
        index=41,
        sequence_no=7,
        previous_event_hash=resolved_record.event_hash,
        schema_version="automarkov.state-transitioned.v1",
        event_type="StateTransitioned",
    ) | {
        "from_state": "WAITING_RUNTIME",
        "to_state": "RESEARCHING",
        "trigger_event_id": wait_resolved["event_id"],
        "trigger_event_hash": resolved_record.event_hash,
        "input_artifact_ids": [],
        "gate_report_artifact_id": None,
        "gate_report_payload_hash": None,
        "budget_snapshot_artifact_id": budget_reference["artifact_id"],
        "budget_snapshot_payload_hash": budget_reference["payload_hash"],
        "reason_code": "runtime_ready",
    }
    events.append(resume_transition)
    previous_hash = _event_record(resume_transition).event_hash

    for offset, (from_state, to_state, gate_id, reason_code) in enumerate(
        (
            (
                "RESEARCHING",
                "TEXT_DRAFTED",
                "EVIDENCE_LEDGER_CLOSURE",
                "research_completed",
            ),
            (
                "TEXT_DRAFTED",
                "TEXT_REVIEWED",
                "TEXT_SCHEMA",
                "text_schema_passed",
            ),
        )
    ):
        gate_sequence = 8 + offset * 2
        gate = _common_event(
            index=42 + offset * 2,
            sequence_no=gate_sequence,
            previous_event_hash=previous_hash,
            schema_version="automarkov.stage-gate-passed.v1",
            event_type="StageGatePassed",
        ) | {
            "gate_id": gate_id,
            "gate_version": "gate_v1",
            "gate_contract_hash": "sha256:" + "8" * 64,
            "subject_artifact_references": [],
            "gate_report": report_reference,
            "from_state": from_state,
            "to_state": to_state,
            "reason_code": reason_code,
            "result": "passed",
        }
        gate_record = _event_record(gate)
        transition = _common_event(
            index=43 + offset * 2,
            sequence_no=gate_sequence + 1,
            previous_event_hash=gate_record.event_hash,
            schema_version="automarkov.state-transitioned.v1",
            event_type="StateTransitioned",
        ) | {
            "from_state": from_state,
            "to_state": to_state,
            "trigger_event_id": gate["event_id"],
            "trigger_event_hash": gate_record.event_hash,
            "input_artifact_ids": [],
            "gate_report_artifact_id": report_reference["artifact_id"],
            "gate_report_payload_hash": report_reference["payload_hash"],
            "budget_snapshot_artifact_id": budget_reference["artifact_id"],
            "budget_snapshot_payload_hash": budget_reference["payload_hash"],
            "reason_code": reason_code,
        }
        events.extend((gate, transition))
        previous_hash = _event_record(transition).event_hash

    reviewed = repository.commit(
        {
            "schema_version": "automarkov.lifecycle-command.v1",
            "command_type": "append_run_events",
            "command_id": _uuid7(104),
            "actor_principal_id": _PRINCIPAL_ID,
            "issued_at": _ISSUED_AT,
            "idempotency_key": "cross-run-text-reviewed",
            "run_id": _PARENT_RUN_ID,
            "expected_state": "WAITING_RUNTIME",
            "expected_head": waiting_commit.after_head.model_dump(mode="json"),
            "events": events,
        },
        context=_AUTHORITY.issue(_PRINCIPAL_ID, None, _ISSUED_AT),
    )
    assert isinstance(reviewed, LifecycleCommitReceipt)

    task = _put(
        repository,
        "governance_report",
        {"schema_version": "automarkov.cross-run-fixture.v1", "name": "task"},
    )
    review = _put(
        repository,
        "governance_report",
        {"schema_version": "automarkov.cross-run-fixture.v1", "name": "review"},
    )
    clarification_result = _put(
        repository,
        "governance_report",
        {"schema_version": "automarkov.cross-run-fixture.v1", "name": "result"},
    )
    clarification_policy = _put(
        repository,
        "governance_report",
        {"schema_version": "automarkov.cross-run-fixture.v1", "name": "clarification"},
    )
    cause = _common_event(
        index=46,
        sequence_no=12,
        previous_event_hash=reviewed.after_head.event_hash,
        schema_version="automarkov.clarification-requested.v1",
        event_type="ClarificationRequested",
        process_execution_id=_PROCESS_ID,
    ) | {
        "task": _reference(task),
        "review": _reference(review),
        "result": _reference(clarification_result),
        "gap_ids": ["missing_scope"],
        "clarification_policy": _reference(clarification_policy),
        "reason_code": "clarification_required",
    }
    cause_record = _event_record(cause)
    terminal_event = _common_event(
        index=47,
        sequence_no=13,
        previous_event_hash=cause_record.event_hash,
        schema_version="automarkov.state-transitioned.v1",
        event_type="StateTransitioned",
        process_execution_id=_PROCESS_ID,
    ) | {
        "from_state": "TEXT_REVIEWED",
        "to_state": "CLARIFICATION_REQUIRED",
        "trigger_event_id": cause["event_id"],
        "trigger_event_hash": cause_record.event_hash,
        "input_artifact_ids": [],
        "gate_report_artifact_id": None,
        "gate_report_payload_hash": None,
        "budget_snapshot_artifact_id": budget_reference["artifact_id"],
        "budget_snapshot_payload_hash": budget_reference["payload_hash"],
        "reason_code": "clarification_required",
    }
    process_payload = dict(
        cast(dict[str, object], replacement_command["process_terminal_record"])
    )
    process_payload["reason_code"] = "clarification_recorded"
    terminal_commit = repository.commit(
        {
            "schema_version": "automarkov.lifecycle-command.v1",
            "command_type": "commit_terminal",
            "command_id": _uuid7(105),
            "actor_principal_id": _PRINCIPAL_ID,
            "issued_at": _ISSUED_AT,
            "idempotency_key": "cross-run-clarification-terminal",
            "run_id": _PARENT_RUN_ID,
            "expected_state": "TEXT_REVIEWED",
            "expected_head": reviewed.after_head.model_dump(mode="json"),
            "events": [cause, terminal_event],
            "process_terminal_record": process_payload,
            "fixed_commit_job_manifest": replacement_command[
                "fixed_commit_job_manifest"
            ],
            "terminal_time_approvals": [],
            "projector_version": RUN_PROJECTOR_VERSION,
            "projector_hash": RUN_PROJECTOR_HASH,
            "created_at": _ISSUED_AT,
        },
        context=_AUTHORITY.issue(_PRINCIPAL_ID, _PROCESS_ID, _ISSUED_AT),
    )
    assert isinstance(terminal_commit, LifecycleCommitReceipt)
    terminal_reference = cast(ArtifactReference, terminal_commit.terminal_result)
    answer = _put(
        repository,
        "signed_answer_bundle",
        _sign_payload(
            {
                "schema_version": "automarkov.signed-answer-bundle.v1",
                "signing_domain": "AutoMarkov-Signed-Answer-Bundle-v1",
                "principal_id": _PRINCIPAL_ID,
                "signing_key_id": _SIGNING_KEY_ID,
                "answer_hash": "sha256:" + "f" * 64,
                "issued_at": _ISSUED_AT,
                "nonce_b64url": _nonce(47),
            }
        ),
    )
    continuation_policy_payload = _sign_payload(
        {
            "schema_version": "automarkov.clarification-continuation-policy.v1",
            "signing_domain": "AutoMarkov-Clarification-Continuation-Policy-v1",
            "authority_principal_id": _PRINCIPAL_ID,
            "signing_key_id": _SIGNING_KEY_ID,
            "authority_status": "active",
            "child_ordinal_increment": 1,
            "maximum_child_count": 1,
            "experiment_eligibility": "nonconfirmatory",
            "allowed_answer_artifact_kinds": ["signed_answer_bundle"],
            "budget_reset_rule": "fresh_child_budget",
            "runtime_reset_rule": "revalidate_runtime",
            "issued_at": _ISSUED_AT,
            "nonce_b64url": _nonce(48),
        }
    )
    continuation_policy = _put(
        repository,
        "clarification_continuation_policy",
        continuation_policy_payload,
    )
    replacement_policy = cast(
        dict[str, str],
        replacement_command["replacement_policy"],
    )
    child_manifest = _put(
        repository,
        "run_manifest",
        {
            "schema_version": "automarkov.cross-run-manifest.v1",
            "event_security_context": _security_context(
                _CHILD_RUN_ID,
                1,
                replacement_policy,
                replacement_policy,
            ),
            "replacement_ordinal": 0,
            "clarification_continuation_ordinal": 1,
            "replacement_policy": replacement_policy,
            "parent_run_id": _PARENT_RUN_ID,
            "parent_run_superseded_event_id": None,
            "supersession_cause": None,
            "parent_clarification_result": _reference(clarification_result),
            "parent_terminal_result": terminal_reference.model_dump(mode="json"),
            "signed_answer_bundle": _reference(answer),
            "continuation_policy": _reference(continuation_policy),
        },
    )
    child_event = _sign_payload(
        {
            "schema_version": "automarkov.clarification-child-run-created.v1",
            "event_type": "ClarificationChildRunCreated",
            "signing_domain": "AutoMarkov-Clarification-Child-Run-Created-v1",
            "event_id": _uuid7(49),
            "experiment_id": None,
            "run_id": _CHILD_RUN_ID,
            "issued_at": _ISSUED_AT,
            "nonce_b64url": _nonce(49),
            "signing_key_id": _SIGNING_KEY_ID,
            "sequence_no": 0,
            "previous_event_hash": ZERO_EVENT_HASH,
            "run_manifest_artifact_id": child_manifest.artifact_id.root,
            "run_manifest_payload_hash": child_manifest.payload_hash.root,
            "parent_run_id": _PARENT_RUN_ID,
            "parent_clarification_result_artifact_id": clarification_result.artifact_id.root,
            "parent_clarification_result_payload_hash": clarification_result.payload_hash.root,
            "parent_terminal_result_artifact_id": terminal_reference.artifact_id,
            "parent_terminal_result_payload_hash": terminal_reference.payload_hash,
            "parent_terminal_snapshot_event_head_hash": terminal_commit.after_head.event_hash,
            "signed_answer_bundle_artifact_id": answer.artifact_id.root,
            "signed_answer_bundle_payload_hash": answer.payload_hash.root,
            "continuation_policy_artifact_id": continuation_policy.artifact_id.root,
            "continuation_policy_payload_hash": continuation_policy.payload_hash.root,
            "clarification_continuation_ordinal": 1,
            "continuation_authority_principal_id": _PRINCIPAL_ID,
            "reason_code": "clarification_answer_received",
        }
    )
    command = {
        "schema_version": "automarkov.lifecycle-command.v1",
        "command_type": "create_clarification_child_run",
        "command_id": _uuid7(106),
        "actor_principal_id": _PRINCIPAL_ID,
        "issued_at": _ISSUED_AT,
        "idempotency_key": "cross-run-clarification-child",
        "parent_run_id": _PARENT_RUN_ID,
        "child_run_id": _CHILD_RUN_ID,
        "expected_parent_head": terminal_commit.after_head.model_dump(mode="json"),
        "expected_child_head": None,
        "parent_clarification_result": _reference(clarification_result),
        "parent_terminal_result": terminal_reference.model_dump(mode="json"),
        "parent_terminal_snapshot_event_head": terminal_commit.after_head.model_dump(
            mode="json"
        ),
        "signed_answer_bundle": _reference(answer),
        "continuation_policy": _reference(continuation_policy),
        "child_run_manifest": _reference(child_manifest),
        "clarification_child_run_created_event": child_event,
    }
    return command, terminal_commit


@pytest.fixture(params=("memory", "sqlite"))
def repository(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> Iterator[ArtifactRepositoryAdapter]:
    if request.param == "memory":
        result: ArtifactRepositoryAdapter = InMemoryArtifactRepository(
            _registry(), command_authority=_AUTHORITY
        )
    else:
        result = SqliteArtifactRepository(
            tmp_path / "cross-run.sqlite",
            _registry(),
            command_authority=_AUTHORITY,
        )
    try:
        yield result
    finally:
        if isinstance(result, SqliteArtifactRepository):
            result.close()


def test_replacement_commit_is_atomic_and_exact_retry_is_identical(
    repository: ArtifactRepositoryAdapter,
) -> None:
    command, parent_before = _replacement_fixture(repository)
    context = _AUTHORITY.issue(_PRINCIPAL_ID, _PROCESS_ID, _ISSUED_AT)

    committed = repository.commit(command, context=context)
    retried = repository.commit(command, context=context)

    assert isinstance(committed, CrossRunLifecycleCommitReceipt)
    assert committed.parent_before_head == parent_before.after_head
    assert committed.parent_run_view.state.value == "CANCELLED"
    assert committed.child_run_view.state.value == "RECEIVED"
    assert committed.process_execution_terminal_record is not None
    assert committed.terminal_result is not None
    assert committed.run_audit_projection is not None
    assert committed.execution_attestation is not None
    assert canonical_json_bytes(committed.model_dump(mode="json")) == (
        canonical_json_bytes(retried.model_dump(mode="json"))
    )


def test_clarification_commit_preserves_parent_and_creates_one_exact_child(
    repository: ArtifactRepositoryAdapter,
) -> None:
    command, parent_before = _clarification_fixture(repository)
    context = _AUTHORITY.issue(_PRINCIPAL_ID, None, _ISSUED_AT)

    committed = repository.commit(command, context=context)
    retried = repository.commit(command, context=context)

    assert isinstance(committed, CrossRunLifecycleCommitReceipt)
    assert committed.parent_before_head == parent_before.after_head
    assert committed.parent_after_head == parent_before.after_head
    assert committed.parent_event_records == ()
    assert committed.parent_run_view.state.value == "CLARIFICATION_REQUIRED"
    assert committed.child_run_view.state.value == "RECEIVED"
    assert committed.artifact_references == ()
    assert canonical_json_bytes(committed.model_dump(mode="json")) == (
        canonical_json_bytes(retried.model_dump(mode="json"))
    )


def test_replacement_failpoint_rolls_back_the_entire_cross_run_commit(
    repository: ArtifactRepositoryAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command, _ = _replacement_fixture(repository)
    context = _AUTHORITY.issue(_PRINCIPAL_ID, _PROCESS_ID, _ISSUED_AT)

    def fail_after_indexes(stage: str) -> None:
        if stage == "after_indexes":
            raise RuntimeError("injected cross-run failure")

    monkeypatch.setattr(repository, "_cross_run_commit_failpoint", fail_after_indexes)
    with pytest.raises(RuntimeError, match="injected cross-run failure"):
        repository.commit(command, context=context)

    monkeypatch.setattr(repository, "_cross_run_commit_failpoint", lambda _stage: None)
    committed = repository.commit(command, context=context)
    assert isinstance(committed, CrossRunLifecycleCommitReceipt)
    assert committed.parent_run_view.state.value == "CANCELLED"
    assert committed.child_run_view.state.value == "RECEIVED"


def test_replacement_different_retry_and_edge_tamper_fail_closed(
    repository: ArtifactRepositoryAdapter,
) -> None:
    command, _ = _replacement_fixture(repository)
    context = _AUTHORITY.issue(_PRINCIPAL_ID, _PROCESS_ID, _ISSUED_AT)
    repository.commit(command, context=context)

    different = dict(command)
    different_attestation = dict(
        cast(dict[str, object], command["execution_attestation"])
    )
    different_attestation["nonce_b64url"] = _nonce(31)
    different["execution_attestation"] = different_attestation
    with pytest.raises(EventReplayConflictError):
        repository.commit(different, context=context)

    wrong_policy_id = cast(dict[str, str], command["old_run_manifest"])["artifact_id"]
    if isinstance(repository, InMemoryArtifactRepository):
        edge = repository._run_replacements[_PARENT_RUN_ID]
        repository._run_replacements[_PARENT_RUN_ID] = edge[:4] + (
            wrong_policy_id,
            *edge[5:],
        )
    else:
        repository._connection.execute(
            "UPDATE run_replacements SET replacement_policy_artifact_id = ? "
            "WHERE parent_run_id = ?",
            (wrong_policy_id, _PARENT_RUN_ID),
        )
    with pytest.raises(ArtifactIntegrityError):
        repository.commit(command, context=context)
