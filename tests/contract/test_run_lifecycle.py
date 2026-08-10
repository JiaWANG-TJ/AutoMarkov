from __future__ import annotations

import base64
import hashlib
import sqlite3
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
from pydantic import BaseModel, model_validator

from automarkov.adapters import (
    InMemoryArtifactRepository,
    InMemoryCompiler,
    SqliteArtifactRepository,
)
from automarkov.canonical import (
    CanonicalPayloadCodec,
    FrozenSequence,
    SafeCanonicalInt,
    canonical_json_bytes,
)
from automarkov.domain import (
    ArtifactId,
    RunId,
    RunState,
    Sha256Digest,
    StrictFrozenModel,
    VerifiedEventHead,
    validate_task_request_payload,
)
from automarkov.errors import (
    ArtifactIntegrityError,
    ArtifactWriteAuthorityError,
    AutoMarkovError,
    CommandAuthenticationError,
    EventHeadConflictError,
    EventSchemaError,
    RunProjectorIdentityError,
    TerminalProvenanceError,
    UnknownArtifactError,
)
from automarkov.lifecycle import (
    RUN_PROJECTOR_HASH,
    RUN_PROJECTOR_VERSION,
    ZERO_EVENT_HASH,
    ArtifactReference,
    BudgetSnapshot,
    EventAuthenticator,
    EventSigningKey,
    LifecycleCommitReceipt,
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

_ISSUED_AT = "2026-08-10T11:00:00Z"
_STARTED_AT = "2026-08-10T10:59:00Z"
_RUN_ID = "run_repository_lifecycle"
_HASH_A = "sha256:" + "a" * 64
_HASH_B = "sha256:" + "b" * 64
_PAYLOAD_MEDIA_TYPE = "application/vnd.automarkov.canonical-payload+json"
_SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(b"\x17" * 32)
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
        "governance_report",
        "job_manifest",
        "payload_output",
        "resource_usage",
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
        "budget_snapshot",
        "automarkov.budget-snapshot.v1",
        BudgetSnapshot,
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
                    artifact_id_path="process_execution_terminal_record.artifact_id",
                    payload_hash_path="process_execution_terminal_record.payload_hash",
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
    return repository.commit(
        command,
        context=_COMMAND_AUTHORITY.issue(
            cast(str, command["actor_principal_id"]),
            process_ids.pop(),
            cast(str, command["issued_at"]),
        ),
    )


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


def _put_run_artifacts(
    repository: ArtifactRepositoryAdapter,
) -> dict[str, Any]:
    governance = _put(repository, "governance_report", "governance")
    return {
        "run_manifest": _put_run_manifest(repository, governance),
        "job_manifest": _put(repository, "job_manifest", "job-manifest"),
        "output": _put(repository, "payload_output", "output"),
        "alternate_output": _put(repository, "payload_output", "alternate-output"),
        "resource_usage": _put(repository, "resource_usage", "resource-usage"),
        "governance": governance,
        "budget": _put(repository, "budget_snapshot", "budget"),
    }


def test_compiler_routes_lifecycle_commands_and_projects_the_verified_head(
    lifecycle_repository: ArtifactRepositoryAdapter,
) -> None:
    issued_contexts: list[tuple[str, str | None, str]] = []

    def context_provider(command: Any) -> AuthenticatedCommandContext:
        process_execution_id = (
            "execution_lifecycle_terminal"
            if command.actor_principal_id == "principal_fixed_commit_runner"
            else None
        )
        issued_contexts.append(
            (command.actor_principal_id, process_execution_id, command.issued_at)
        )
        return _COMMAND_AUTHORITY.issue(
            command.actor_principal_id,
            process_execution_id,
            command.issued_at,
        )

    compiler = InMemoryCompiler(
        run_id_factory=lambda: RunId(root=_RUN_ID),
        repository=lifecycle_repository,
        command_context_provider=context_provider,
    )
    run_id = compiler.start(
        validate_task_request_payload(
            {
                "schema_version": "automarkov.task-request.v1",
                "request_id": "request_lifecycle_compiler",
                "task_text": "Compile the lifecycle integration fixture.",
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
        )
    )
    artifacts = _put_run_artifacts(lifecycle_repository)
    command = {
        "schema_version": "automarkov.lifecycle-command.v1",
        "command_type": "append_run_events",
        "command_id": _uuid7(10_000),
        "actor_principal_id": "principal_lifecycle_fixture",
        "issued_at": _ISSUED_AT,
        "idempotency_key": "lifecycle-command-0",
        "run_id": _RUN_ID,
        "expected_state": None,
        "expected_head": None,
        "events": [_root_event(artifacts)],
    }

    committed = compiler.dispatch(command)
    head = VerifiedEventHead.model_validate(
        {
            "run_id": run_id.root,
            "sequence_no": committed.after_head.sequence_no,
            "event_hash": committed.after_head.event_hash,
        },
        strict=True,
    )

    assert committed.run_id == run_id.root
    assert compiler.resume(run_id, head) == committed.run_view

    researching = _advance_to_researching(
        lifecycle_repository,
        artifacts,
        committed,
    )
    terminal = compiler.dispatch(_terminal_command(artifacts, researching))

    assert terminal.run_view.state is RunState.FAILED
    assert issued_contexts == [
        ("principal_lifecycle_fixture", None, _ISSUED_AT),
        (
            "principal_fixed_commit_runner",
            "execution_lifecycle_terminal",
            _ISSUED_AT,
        ),
    ]


def _advance_to_researching(
    repository: ArtifactRepositoryAdapter,
    artifacts: Mapping[str, Any],
    root: LifecycleCommitReceipt,
    *,
    variant: int = 0,
) -> LifecycleCommitReceipt:
    gate_report = _artifact_ref(artifacts["governance"])
    gate = _event_common(
        1,
        previous_event_hash=root.event_record.event_hash,
        schema_version="automarkov.stage-gate-passed.v1",
        event_type="StageGatePassed",
        variant=variant,
    ) | {
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
) -> dict[str, object]:
    return _event_common(
        researching.event_record.event.sequence_no + 1,
        researching.event_record.event_hash,
        "automarkov.validation-failed.v1",
        "ValidationFailed",
    ) | {
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
) -> dict[str, object]:
    cause = _terminal_cause(artifacts, researching)
    cause_record = parse_event_record(encode_event_record(cause))
    transition = _transition_event(
        sequence_no=cause_record.event.sequence_no + 1,
        previous_event_hash=cause_record.event_hash,
        from_state="RESEARCHING",
        to_state="FAILED",
        trigger=cause_record,
        budget=artifacts["budget"],
    ) | {
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
        "SELECT * FROM run_terminal_results ORDER BY rowid",
        "SELECT * FROM run_audit_projections ORDER BY rowid",
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
