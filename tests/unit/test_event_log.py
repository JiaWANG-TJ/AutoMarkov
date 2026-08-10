from __future__ import annotations

import base64
import copy
import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast
from uuid import RFC_4122, UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import Field

from automarkov.adapters import InMemoryArtifactRepository
from automarkov.canonical import canonical_json_bytes
from automarkov.domain import RunState, StrictFrozenModel
from automarkov.errors import AutoMarkovError
from automarkov.lifecycle import (
    RUN_PROJECTOR_HASH,
    RUN_PROJECTOR_VERSION,
    ZERO_EVENT_HASH,
    AppendRunEventsCommand,
    BudgetSnapshot,
    CommitTerminalCommand,
    EventAuthenticator,
    EventHead,
    EventRecord,
    EventSchemaRegistry,
    EventSigningKey,
    LifecycleCommitReceipt,
    RunCreated,
    RunEventSecurityContext,
    StateTransitioned,
    encode_event_record,
    parse_event_bytes,
    validate_lifecycle_command,
)
from automarkov.public import CommandAuthority, CommandPrincipalBinding
from automarkov.repository import ArtifactSchemaRegistry

REJECTED = (ValueError, AutoMarkovError)
_SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(b"\x23" * 32)
_EVENT_AUTHENTICATOR = EventAuthenticator(
    (
        EventSigningKey(
            signing_key_id="key_orchestrator",
            principal_id="principal_orchestrator",
            run_id="run_event_log",
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
    "authority_event_log_tests",
    (CommandPrincipalBinding("principal_orchestrator", None),),
)


class _SignedProbeEvent(StrictFrozenModel):
    schema_version: Literal["automarkov.test-signed-probe.v1"]
    event_type: Literal["SignedProbeEvent"]
    event_id: str
    experiment_id: str | None
    run_id: str
    sequence_no: Annotated[int, Field(strict=True, ge=0)]
    previous_event_hash: str
    actor_principal_id: str
    issued_at: str
    signature_b64url: str


class _DriftedSignedProbeEvent(_SignedProbeEvent):
    caller_controlled: bool


class _FixtureArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.event-log-fixture.v1"]
    value: str


class _RunManifestFixture(StrictFrozenModel):
    schema_version: Literal["automarkov.event-log-run-manifest.v1"]
    event_security_context: RunEventSecurityContext


def _repository() -> tuple[InMemoryArtifactRepository, dict[str, str]]:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "run_manifest",
        "automarkov.event-log-run-manifest.v1",
        _RunManifestFixture,
        direct_parent_artifact_types=("governance_report",),
    )
    registry.register(
        "governance_report",
        "automarkov.event-log-fixture.v1",
        _FixtureArtifact,
        direct_parent_artifact_types=(),
    )
    registry.register(
        "budget_snapshot",
        "automarkov.budget-snapshot.v1",
        BudgetSnapshot,
        direct_parent_artifact_types=(),
    )
    registry.freeze()
    repository = InMemoryArtifactRepository(
        registry,
        _EVENT_AUTHENTICATOR,
        _COMMAND_AUTHORITY,
    )
    references: dict[str, str] = {}
    governance = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "governance_report",
            "payload_bytes": canonical_json_bytes(
                {
                    "schema_version": "automarkov.event-log-fixture.v1",
                    "value": "event-security-policy",
                }
            ),
            "parent_artifact_ids": [],
            "created_by": "principal_orchestrator",
            "created_at": "2026-08-10T00:00:00Z",
            "source_evidence_ids": [],
        }
    )
    governance_reference = {
        "artifact_id": governance.artifact_id.root,
        "payload_hash": governance.payload_hash.root,
    }
    references["governance_report_id"] = governance.artifact_id.root
    references["governance_report_hash"] = governance.payload_hash.root
    public_key_b64url = (
        base64.urlsafe_b64encode(
            _SIGNING_KEY.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        )
        .decode()
        .rstrip("=")
    )
    for artifact_type in ("run_manifest", "budget_snapshot"):
        result = repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": artifact_type,
                "payload_bytes": canonical_json_bytes(
                    {
                        "schema_version": "automarkov.budget-snapshot.v1",
                        "contract_hash": "sha256:" + "7" * 64,
                        "counters": [
                            {
                                "metric": "stage_revisions",
                                "consumed": 0,
                                "limit": 1,
                            }
                        ],
                    }
                    if artifact_type == "budget_snapshot"
                    else {
                        "schema_version": "automarkov.event-log-run-manifest.v1",
                        "event_security_context": {
                            "schema_version": (
                                "automarkov.run-event-security-context.v1"
                            ),
                            "run_id": "run_event_log",
                            "experiment_id": None,
                            "root_ordinal": 0,
                            "creation_policy": governance_reference,
                            "max_clock_skew_ms": 5_000,
                            "actor_capabilities": [
                                {
                                    "principal_id": "principal_orchestrator",
                                    "process_execution_id": None,
                                    "allowed_event_types": [
                                        "RunCreated",
                                        "StageGatePassed",
                                        "StateTransitioned",
                                    ],
                                }
                            ],
                            "signing_keys": [
                                {
                                    "signing_key_id": "key_orchestrator",
                                    "principal_id": "principal_orchestrator",
                                    "signature_algorithm": "Ed25519",
                                    "public_key_b64url": public_key_b64url,
                                    "not_before": "2026-08-09T00:00:00Z",
                                    "not_after": "2026-08-11T00:00:00Z",
                                    "revoked_at": None,
                                }
                            ],
                            "run_creation": {
                                "creation_principal_id": "principal_orchestrator",
                                "signing_key_id": "key_orchestrator",
                            },
                            "approval": {
                                "approval_principal_id": "principal_orchestrator",
                                "approval_principal_kind": "interactive_user",
                                "signing_key_id": "key_orchestrator",
                                "policy_contract": governance_reference,
                                "policy_source_hash": None,
                                "policy_image_hash": None,
                                "policy_version": None,
                                "revocation_authorities": [],
                            },
                        },
                    }
                ),
                "parent_artifact_ids": (
                    [governance.artifact_id.root]
                    if artifact_type == "run_manifest"
                    else []
                ),
                "created_by": "principal_orchestrator",
                "created_at": "2026-08-10T00:00:00Z",
                "source_evidence_ids": [],
            }
        )
        references[f"{artifact_type}_id"] = result.artifact_id.root
        references[f"{artifact_type}_hash"] = result.payload_hash.root
    return repository, references


def _run_created(
    *,
    run_id: str = "run_event_log",
    event_id: str = "019fe8f8-1400-7000-8000-000000000010",
    signature_b64url: str | None = None,
    manifest_id: str = "artifact_" + "1" * 64,
    manifest_hash: str = "sha256:" + "2" * 64,
) -> dict[str, object]:
    event = {
        "schema_version": "automarkov.run-created.v1",
        "event_type": "RunCreated",
        "signing_domain": "AutoMarkov-Run-Created-v1",
        "event_id": event_id,
        "experiment_id": None,
        "run_id": run_id,
        "sequence_no": 0,
        "previous_event_hash": ZERO_EVENT_HASH,
        "actor_principal_id": "principal_orchestrator",
        "issued_at": "2026-08-10T00:00:00Z",
        "run_manifest_artifact_id": manifest_id,
        "run_manifest_payload_hash": manifest_hash,
        "initial_state": "RECEIVED",
        "creation_principal_id": "principal_orchestrator",
        "reason_code": "run_created",
        "nonce_b64url": "AAAAAAAAAAAAAAAAAAAAAA",
        "signing_key_id": "key_orchestrator",
        "signature_algorithm": "Ed25519",
        "signature_b64url": signature_b64url or "A" * 86,
    }
    if signature_b64url is None:
        signature = _SIGNING_KEY.sign(
            canonical_json_bytes(
                {
                    key: value
                    for key, value in event.items()
                    if key != "signature_b64url"
                }
            )
        )
        event["signature_b64url"] = (
            base64.urlsafe_b64encode(signature).decode().rstrip("=")
        )
    return event


def _state_transitioned(
    previous_event_hash: str,
    *,
    run_id: str = "run_event_log",
    event_id: str = "019fe8f8-17e8-7000-8000-000000000011",
    sequence_no: int = 1,
    to_state: str = "RESEARCHING",
    budget_id: str = "artifact_" + "3" * 64,
    budget_hash: str = "sha256:" + "4" * 64,
    trigger_event_id: str = "019fe8f8-1400-7000-8000-000000000010",
    trigger_event_hash: str | None = None,
    gate_report_id: str | None = None,
    gate_report_hash: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "automarkov.state-transitioned.v1",
        "event_type": "StateTransitioned",
        "event_id": event_id,
        "experiment_id": None,
        "run_id": run_id,
        "sequence_no": sequence_no,
        "previous_event_hash": previous_event_hash,
        "actor_principal_id": "principal_orchestrator",
        "actor_process_execution_id": None,
        "issued_at": "2026-08-10T00:00:01Z",
        "from_state": "RECEIVED",
        "to_state": to_state,
        "trigger_event_id": trigger_event_id,
        "trigger_event_hash": trigger_event_hash or previous_event_hash,
        "input_artifact_ids": [],
        "gate_report_artifact_id": gate_report_id,
        "gate_report_payload_hash": gate_report_hash,
        "budget_snapshot_artifact_id": budget_id,
        "budget_snapshot_payload_hash": budget_hash,
        "reason_code": "intake_accepted",
    }


def _stage_gate_passed(
    previous_event_hash: str,
    *,
    report_id: str,
    report_hash: str,
) -> dict[str, object]:
    return {
        "schema_version": "automarkov.stage-gate-passed.v1",
        "event_type": "StageGatePassed",
        "event_id": "019fe8f8-17e8-7000-8000-000000000011",
        "experiment_id": None,
        "run_id": "run_event_log",
        "sequence_no": 1,
        "previous_event_hash": previous_event_hash,
        "actor_principal_id": "principal_orchestrator",
        "actor_process_execution_id": None,
        "issued_at": "2026-08-10T00:00:01Z",
        "gate_id": "INTAKE_SCHEMA_BUDGET_AUTHORITY",
        "gate_version": "v1",
        "gate_contract_hash": "sha256:" + "8" * 64,
        "subject_artifact_references": [],
        "gate_report": {
            "artifact_id": report_id,
            "payload_hash": report_hash,
        },
        "from_state": "RECEIVED",
        "to_state": "RESEARCHING",
        "reason_code": "intake_accepted",
        "result": "passed",
    }


def _append_command(
    events: list[dict[str, object]],
    *,
    run_id: str,
    expected_state: str | None,
    expected_head: EventHead | None,
    command_id: str,
    idempotency_key: str,
) -> dict[str, object]:
    return {
        "schema_version": "automarkov.lifecycle-command.v1",
        "command_type": "append_run_events",
        "command_id": command_id,
        "actor_principal_id": "principal_orchestrator",
        "issued_at": "2026-08-10T00:00:02Z",
        "idempotency_key": idempotency_key,
        "run_id": run_id,
        "expected_state": expected_state,
        "expected_head": (
            None
            if expected_head is None
            else expected_head.model_dump(mode="json", warnings="error")
        ),
        "events": events,
    }


def _append(
    repository: InMemoryArtifactRepository,
    event: dict[str, object],
    *,
    expected_state: str | None,
    expected_head: EventHead | None,
    command_id: str,
    idempotency_key: str,
) -> LifecycleCommitReceipt:
    return _append_events(
        repository,
        [event],
        expected_state=expected_state,
        expected_head=expected_head,
        command_id=command_id,
        idempotency_key=idempotency_key,
    )


def _append_events(
    repository: InMemoryArtifactRepository,
    events: list[dict[str, object]],
    *,
    expected_state: str | None,
    expected_head: EventHead | None,
    command_id: str,
    idempotency_key: str,
) -> LifecycleCommitReceipt:
    result = repository.commit(
        _append_command(
            events,
            run_id=str(events[0]["run_id"]),
            expected_state=expected_state,
            expected_head=expected_head,
            command_id=command_id,
            idempotency_key=idempotency_key,
        ),
        context=_COMMAND_AUTHORITY.issue(
            "principal_orchestrator",
            None,
            "2026-08-10T00:00:02Z",
        ),
    )
    assert isinstance(result, LifecycleCommitReceipt)
    return result


def test_default_event_union_is_closed_strict_and_requires_uuidv7() -> None:
    assert ZERO_EVENT_HASH == "sha256:" + "0" * 64

    encoded = encode_event_record(_run_created())
    record = parse_event_bytes(encoded)

    assert isinstance(record, EventRecord)
    assert isinstance(record.event, RunCreated)
    parsed_event_id = UUID(record.event.event_id)
    assert parsed_event_id.version == 7
    assert parsed_event_id.variant == RFC_4122
    assert int.from_bytes(parsed_event_id.bytes[:6], "big") == int(
        datetime(2026, 8, 10, tzinfo=UTC).timestamp() * 1000
    )

    invalid_events: list[dict[str, object]] = []
    for field_name, value in (
        ("event_type", "UnknownEvent"),
        ("schema_version", "automarkov.run-created.v2"),
        ("event_id", "019fe8f8-1400-4000-8000-000000000010"),
        ("sequence_no", "0"),
    ):
        candidate = copy.deepcopy(_run_created())
        candidate[field_name] = value
        invalid_events.append(candidate)
    invalid_events.append(_run_created() | {"caller_controlled": True})

    for candidate in invalid_events:
        with pytest.raises(REJECTED):
            encode_event_record(candidate)


def test_terminal_cause_union_uses_exact_budget_wire_schema() -> None:
    common = {
        "schema_version": "automarkov.budget-exhausted.v1",
        "event_type": "BudgetExhausted",
        "event_id": "019fe8f8-17e8-7000-8000-000000000041",
        "experiment_id": None,
        "run_id": "run_event_log",
        "sequence_no": 1,
        "previous_event_hash": "sha256:" + "1" * 64,
        "actor_principal_id": "principal_orchestrator",
        "actor_process_execution_id": "process_budget_probe",
        "issued_at": "2026-08-10T00:00:01Z",
    }
    exact = common | {
        "budget_kind": "wall_time",
        "budget_policy_artifact_id": "artifact_" + "2" * 64,
        "budget_policy_payload_hash": "sha256:" + "3" * 64,
        "budget_snapshot_artifact_id": "artifact_" + "4" * 64,
        "budget_snapshot_payload_hash": "sha256:" + "5" * 64,
        "canonical_unit": "milliseconds",
        "limit": 10,
        "consumed": 9,
        "reserved": 1,
        "cause_receipt_artifact_id": "artifact_" + "6" * 64,
        "cause_receipt_payload_hash": "sha256:" + "7" * 64,
        "phase": "research",
        "reason_code": "budget_exhausted",
        "exhausted_at": "2026-08-10T00:00:01Z",
    }

    record = parse_event_bytes(encode_event_record(exact))
    assert record.event.event_type == "BudgetExhausted"

    for invalid in (
        exact | {"exhausted_metric": "wall_time_ms"},
        exact | {"reserved": 0},
        exact | {"canonical_unit": "tokens"},
        common
        | {
            "terminal_state": "FAILED",
            "reason_code": "generic_failure",
            "cause_artifacts": [],
        },
    ):
        with pytest.raises(REJECTED):
            encode_event_record(invalid)


def test_append_command_statically_excludes_terminal_and_incomplete_wait_tuples() -> (
    None
):
    previous_hash = "sha256:" + "1" * 64
    head = EventHead(
        run_id="run_event_log",
        sequence_no=0,
        event_hash=previous_hash,
    )
    transition = _state_transitioned(previous_hash)
    valid = _append_command(
        [transition],
        run_id="run_event_log",
        expected_state="RECEIVED",
        expected_head=head,
        command_id="019fe8f8-1bd0-7000-8000-000000000061",
        idempotency_key="ordinary-transition",
    )
    assert isinstance(validate_lifecycle_command(valid), AppendRunEventsCommand)

    terminal_transition = copy.deepcopy(transition)
    terminal_transition["to_state"] = "FAILED"
    incomplete_wait = {
        "schema_version": "automarkov.waiting-runtime.v1",
        "event_type": "WaitingRuntime",
        "event_id": "019fe8f8-17e8-7000-8000-000000000062",
        "experiment_id": None,
        "run_id": "run_event_log",
        "sequence_no": 1,
        "previous_event_hash": previous_hash,
        "actor_principal_id": "principal_orchestrator",
        "actor_process_execution_id": None,
        "issued_at": "2026-08-10T00:00:01Z",
        "resume_state": "RECEIVED",
        "wait_reason_code": "runtime_profile_unavailable",
        "trigger_event_id": _run_created()["event_id"],
        "trigger_event_hash": previous_hash,
        "failure_report_artifact_id": "artifact_" + "2" * 64,
        "failure_report_payload_hash": "sha256:" + "3" * 64,
        "recovery_gate_id": "gate_runtime",
        "recovery_condition_hash": "sha256:" + "4" * 64,
        "entered_at": "2026-08-10T00:00:01Z",
        "dependency_kind": "runtime_profile",
        "profile_id": "profile_test",
        "process_execution_id": None,
        "protocol_edge_id": None,
        "dependency_identity_hash": "sha256:" + "5" * 64,
        "failed_readiness_gate_id": "gate_runtime",
    }
    for index, events in enumerate(
        ([terminal_transition], [incomplete_wait]), start=62
    ):
        command = _append_command(
            events,
            run_id="run_event_log",
            expected_state="RECEIVED",
            expected_head=head,
            command_id=f"019fe8f8-1bd0-7000-8000-0000000000{index}",
            idempotency_key=f"reject-append-subset-{index}",
        )
        with pytest.raises(REJECTED):
            validate_lifecycle_command(command)


def test_terminal_command_public_ingress_freezes_the_raw_event_array() -> None:
    expected_hash = "sha256:" + "1" * 64
    budget_id = "artifact_" + "4" * 64
    budget_hash = "sha256:" + "5" * 64
    cause = {
        "schema_version": "automarkov.budget-exhausted.v1",
        "event_type": "BudgetExhausted",
        "event_id": "019fe8f8-17e8-7000-8000-000000000071",
        "experiment_id": None,
        "run_id": "run_event_log",
        "sequence_no": 1,
        "previous_event_hash": expected_hash,
        "actor_principal_id": "principal_orchestrator",
        "actor_process_execution_id": "process_terminal",
        "issued_at": "2026-08-10T00:00:01Z",
        "budget_kind": "wall_time",
        "budget_policy_artifact_id": "artifact_" + "2" * 64,
        "budget_policy_payload_hash": "sha256:" + "3" * 64,
        "budget_snapshot_artifact_id": budget_id,
        "budget_snapshot_payload_hash": budget_hash,
        "canonical_unit": "milliseconds",
        "limit": 10,
        "consumed": 10,
        "reserved": 0,
        "cause_receipt_artifact_id": "artifact_" + "6" * 64,
        "cause_receipt_payload_hash": "sha256:" + "7" * 64,
        "phase": "research",
        "reason_code": "budget_exhausted",
        "exhausted_at": "2026-08-10T00:00:01Z",
    }
    cause_record = parse_event_bytes(encode_event_record(cause))
    transition = {
        "schema_version": "automarkov.state-transitioned.v1",
        "event_type": "StateTransitioned",
        "event_id": "019fe8f8-17e8-7000-8000-000000000072",
        "experiment_id": None,
        "run_id": "run_event_log",
        "sequence_no": 2,
        "previous_event_hash": cause_record.event_hash,
        "actor_principal_id": "principal_orchestrator",
        "actor_process_execution_id": "process_terminal",
        "issued_at": "2026-08-10T00:00:01Z",
        "from_state": "RESEARCHING",
        "to_state": "BUDGET_EXHAUSTED",
        "trigger_event_id": cause["event_id"],
        "trigger_event_hash": cause_record.event_hash,
        "input_artifact_ids": [],
        "gate_report_artifact_id": None,
        "gate_report_payload_hash": None,
        "budget_snapshot_artifact_id": budget_id,
        "budget_snapshot_payload_hash": budget_hash,
        "reason_code": "budget_exhausted",
    }
    artifact_8 = {
        "artifact_id": "artifact_" + "8" * 64,
        "payload_hash": "sha256:" + "8" * 64,
    }
    raw = {
        "schema_version": "automarkov.lifecycle-command.v1",
        "command_type": "commit_terminal",
        "command_id": "019fe8f8-1bd0-7000-8000-000000000073",
        "actor_principal_id": "principal_orchestrator",
        "issued_at": "2026-08-10T00:00:02Z",
        "idempotency_key": "terminal-raw-array",
        "run_id": "run_event_log",
        "expected_state": "RESEARCHING",
        "expected_head": {
            "run_id": "run_event_log",
            "sequence_no": 0,
            "event_hash": expected_hash,
        },
        "events": [cause, transition],
        "process_terminal_record": {
            "schema_version": "automarkov.process-execution-terminal-record.v1",
            "signing_domain": "AutoMarkov-ProcessExecutionTerminalRecord-v1",
            "experiment_id": None,
            "run_id": "run_event_log",
            "job_id": "job_terminal",
            "process_execution_id": "process_terminal",
            "profile_id": "profile_terminal",
            "principal_id": "principal_orchestrator",
            "job_manifest": artifact_8,
            "status": "success",
            "exit_code": 0,
            "reason_code": "budget_exhausted",
            "started_at": "2026-08-10T00:00:00Z",
            "finished_at": "2026-08-10T00:00:01Z",
            "stdout_hash": "sha256:" + "9" * 64,
            "stderr_hash": "sha256:" + "a" * 64,
            "payload_outputs": [],
            "resource_usage": {
                "artifact_id": "artifact_" + "b" * 64,
                "payload_hash": "sha256:" + "b" * 64,
            },
            "network_log_hash": "sha256:" + "c" * 64,
            "mount_attestation_hash": "sha256:" + "d" * 64,
            "capability_decision_hash": "sha256:" + "e" * 64,
            "egress_log_hash": "sha256:" + "f" * 64,
            "created_at": "2026-08-10T00:00:01Z",
        },
        "fixed_commit_job_manifest": artifact_8,
        "terminal_time_approvals": [],
        "projector_version": RUN_PROJECTOR_VERSION,
        "projector_hash": RUN_PROJECTOR_HASH,
        "created_at": "2026-08-10T00:00:02Z",
    }

    command = validate_lifecycle_command(raw)
    assert isinstance(command, CommitTerminalCommand)
    assert type(command.events) is tuple
    assert tuple(event.event_type for event in command.events) == (
        "BudgetExhausted",
        "StateTransitioned",
    )


def test_event_record_hash_uses_domain_separated_jcs_and_the_full_signature() -> None:
    encoded = encode_event_record(_run_created(signature_b64url="A" * 86))
    record = parse_event_bytes(encoded)

    assert record.event_hash == (
        "sha256:476ccd3bf5c733de45c2da5aaa3a64f524a112f59bf41811f80e878228fffae3"
    )
    assert encoded == (
        b'{"event":{"actor_principal_id":"principal_orchestrator",'
        b'"creation_principal_id":"principal_orchestrator","event_id":'
        b'"019fe8f8-1400-7000-8000-000000000010","event_type":"RunCreated",'
        b'"experiment_id":null,"initial_state":"RECEIVED","issued_at":'
        b'"2026-08-10T00:00:00Z","nonce_b64url":"AAAAAAAAAAAAAAAAAAAAAA",'
        b'"previous_event_hash":"sha256:'
        + b"0"
        * 64
        + b'","reason_code":"run_created","run_id":"run_event_log",'
        b'"run_manifest_artifact_id":"artifact_'
        + b"1" * 64
        + b'","run_manifest_payload_hash":"sha256:'
        + b"2" * 64
        + b'","schema_version":"automarkov.run-created.v1",'
        b'"sequence_no":0,"signature_algorithm":"Ed25519",'
        b'"signature_b64url":"'
        + b"A"
        * 86
        + b'","signing_domain":"AutoMarkov-Run-Created-v1",'
        b'"signing_key_id":"key_orchestrator"},"event_hash":'
        b'"sha256:476ccd3bf5c733de45c2da5aaa3a64f524a112f59bf41811f80e878228fffae3",'
        b'"schema_version":"automarkov.event-record.v1"}'
    )

    changed = parse_event_bytes(
        encode_event_record(_run_created(signature_b64url="A" * 85 + "B"))
    )
    assert changed.event_hash == (
        "sha256:1ac7a29f0c6bd2e93e19aa72a281916f13d6616b1a81fa753fa1cebcefacbb96"
    )

    forged_model = RunCreated.model_construct(**cast(dict[str, Any], _run_created()))
    with pytest.raises(REJECTED):
        encode_event_record(forged_model)


def test_record_bytes_reject_duplicates_unknown_schema_and_noncanonical_data() -> None:
    encoded = encode_event_record(_run_created())
    duplicate_key = encoded.replace(
        b'"event_type":"RunCreated"',
        b'"event_type":"RunCreated","event_type":"StateTransitioned"',
        1,
    )
    unknown_record_schema = encoded.replace(
        b'"automarkov.event-record.v1"',
        b'"automarkov.event-record.v2"',
        1,
    )
    tampered_event = encoded.replace(
        b"019fe8f8-1400-7000-8000-000000000010",
        b"019fe8f8-1400-7000-8000-000000000019",
        1,
    )
    noncanonical = json.dumps(json.loads(encoded), indent=2).encode("utf-8")

    for candidate in (
        duplicate_key,
        unknown_record_schema,
        tampered_event,
        noncanonical,
    ):
        with pytest.raises(REJECTED):
            parse_event_bytes(candidate)


def test_run_created_is_the_unique_zero_sequence_root() -> None:
    nonzero_root = _run_created()
    nonzero_root["sequence_no"] = 1
    wrong_sentinel = _run_created()
    wrong_sentinel["previous_event_hash"] = "sha256:" + "1" * 64

    for candidate in (nonzero_root, wrong_sentinel):
        with pytest.raises(REJECTED):
            encode_event_record(candidate)

    repository = InMemoryArtifactRepository(command_authority=_COMMAND_AUTHORITY)
    transition_at_root = _state_transitioned(ZERO_EVENT_HASH, sequence_no=0)
    with pytest.raises(REJECTED):
        _append(
            repository,
            transition_at_root,
            expected_state=None,
            expected_head=None,
            command_id="019fe8f8-1bd0-7000-8000-000000000020",
            idempotency_key="root-must-be-run-created",
        )


def test_repository_rejects_sequence_gaps_wrong_predecessors_and_replay() -> None:
    repository, references = _repository()
    with pytest.raises(REJECTED):
        _append(
            repository,
            _run_created(
                signature_b64url="A" * 86,
                manifest_id=references["run_manifest_id"],
                manifest_hash=references["run_manifest_hash"],
            ),
            expected_state=None,
            expected_head=None,
            command_id="019fe8f8-1bd0-7000-8000-000000000019",
            idempotency_key="reject-forged-root-signature",
        )
    noncanonical_signature = _run_created(
        manifest_id=references["run_manifest_id"],
        manifest_hash=references["run_manifest_hash"],
    )
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    signature = cast(str, noncanonical_signature["signature_b64url"])
    noncanonical_signature["signature_b64url"] = (
        signature[:-1] + alphabet[alphabet.index(signature[-1]) + 1]
    )
    with pytest.raises(REJECTED):
        _append(
            repository,
            noncanonical_signature,
            expected_state=None,
            expected_head=None,
            command_id="019fe8f8-1bd0-7000-8000-000000000020",
            idempotency_key="reject-noncanonical-root-signature",
        )
    root = _append(
        repository,
        _run_created(
            manifest_id=references["run_manifest_id"],
            manifest_hash=references["run_manifest_hash"],
        ),
        expected_state=None,
        expected_head=None,
        command_id="019fe8f8-1bd0-7000-8000-000000000021",
        idempotency_key="create-run-event-log",
    )

    assert root.command_id == "019fe8f8-1bd0-7000-8000-000000000021"
    assert root.idempotency_key == "create-run-event-log"
    assert isinstance(root.event_record.event, RunCreated)
    assert root.run_view.state is RunState.RECEIVED
    root_head = root.run_view.event_head
    assert root_head is not None
    assert root_head.run_id == "run_event_log"
    assert root_head.event_hash == root.event_record.event_hash
    assert root_head.sequence_no == 0

    gap = _state_transitioned(
        root_head.event_hash,
        event_id="019fe8f8-17e8-7000-8000-000000000012",
        sequence_no=2,
        budget_id=references["budget_snapshot_id"],
        budget_hash=references["budget_snapshot_hash"],
    )
    wrong_predecessor = _state_transitioned(
        "sha256:" + "5" * 64,
        event_id="019fe8f8-17e8-7000-8000-000000000013",
        budget_id=references["budget_snapshot_id"],
        budget_hash=references["budget_snapshot_hash"],
    )
    for index, candidate in enumerate((gap, wrong_predecessor), start=22):
        with pytest.raises(REJECTED):
            _append(
                repository,
                candidate,
                expected_state="RECEIVED",
                expected_head=root_head,
                command_id=f"019fe8f8-1bd0-7000-8000-0000000000{index}",
                idempotency_key=f"reject-chain-{index}",
            )

    gate = _stage_gate_passed(
        root_head.event_hash,
        report_id=references["governance_report_id"],
        report_hash=references["governance_report_hash"],
    )
    gate_record = parse_event_bytes(encode_event_record(gate))
    transition = _append_events(
        repository,
        [
            gate,
            _state_transitioned(
                gate_record.event_hash,
                event_id="019fe8f8-17e8-7000-8000-000000000014",
                sequence_no=2,
                budget_id=references["budget_snapshot_id"],
                budget_hash=references["budget_snapshot_hash"],
                trigger_event_id=str(gate["event_id"]),
                trigger_event_hash=gate_record.event_hash,
                gate_report_id=references["governance_report_id"],
                gate_report_hash=references["governance_report_hash"],
            ),
        ],
        expected_state="RECEIVED",
        expected_head=root_head,
        command_id="019fe8f8-1bd0-7000-8000-000000000024",
        idempotency_key="transition-to-researching",
    )
    assert isinstance(transition.event_record.event, StateTransitioned)
    assert transition.event_record.event.from_state is RunState.RECEIVED
    assert transition.event_record.event.to_state is RunState.RESEARCHING
    transition_head = transition.run_view.event_head
    assert transition_head is not None

    stale_head_event = _state_transitioned(
        transition_head.event_hash,
        event_id="019fe8f8-1bd0-7000-8000-000000000014",
        sequence_no=2,
        budget_id=references["budget_snapshot_id"],
        budget_hash=references["budget_snapshot_hash"],
    )
    with pytest.raises(REJECTED):
        _append(
            repository,
            stale_head_event,
            expected_state="RESEARCHING",
            expected_head=root_head,
            command_id="019fe8f8-1bd0-7000-8000-000000000025",
            idempotency_key="stale-head",
        )

    replayed_id = _run_created(
        run_id="run_replay_target",
        event_id=root.event_record.event.event_id,
        manifest_id=references["run_manifest_id"],
        manifest_hash=references["run_manifest_hash"],
    )
    with pytest.raises(REJECTED):
        _append(
            repository,
            replayed_id,
            expected_state=None,
            expected_head=None,
            command_id="019fe8f8-1bd0-7000-8000-000000000026",
            idempotency_key="replayed-event-id",
        )


def test_command_retry_is_byte_stable_and_event_identity_cannot_be_replayed() -> None:
    repository, references = _repository()
    event = _run_created(
        manifest_id=references["run_manifest_id"],
        manifest_hash=references["run_manifest_hash"],
    )
    first = _append(
        repository,
        event,
        expected_state=None,
        expected_head=None,
        command_id="019fe8f8-1bd0-7000-8000-000000000027",
        idempotency_key="first-root-write",
    )
    second = _append(
        repository,
        copy.deepcopy(event),
        expected_state=None,
        expected_head=None,
        command_id="019fe8f8-1bd0-7000-8000-000000000027",
        idempotency_key="first-root-write",
    )

    assert second == first
    assert second.model_dump_json() == first.model_dump_json()

    for command_id, idempotency_key in (
        (
            "019fe8f8-1bd0-7000-8000-000000000028",
            "new-key-cannot-replay-event",
        ),
        (
            "019fe8f8-1bd0-7000-8000-000000000031",
            "first-root-write",
        ),
    ):
        with pytest.raises(REJECTED):
            _append(
                repository,
                copy.deepcopy(event),
                expected_state=None,
                expected_head=None,
                command_id=command_id,
                idempotency_key=idempotency_key,
            )

    conflicting_root = _run_created(
        event_id="019fe8f8-1400-7000-8000-000000000015",
        manifest_id=references["run_manifest_id"],
        manifest_hash=references["run_manifest_hash"],
    )
    with pytest.raises(REJECTED):
        _append(
            repository,
            conflicting_root,
            expected_state=None,
            expected_head=None,
            command_id="019fe8f8-1bd0-7000-8000-000000000029",
            idempotency_key="conflicting-root-write",
        )

    raw_command = _append_command(
        [event],
        run_id="run_event_log",
        expected_state=None,
        expected_head=None,
        command_id="019fe8f8-1bd0-7000-8000-000000000030",
        idempotency_key="forged-command-model",
    )
    constructed_command = AppendRunEventsCommand.model_construct(
        **cast(dict[str, Any], raw_command)
    )
    with pytest.raises(REJECTED):
        repository.commit(
            cast(Any, constructed_command),
            context=_COMMAND_AUTHORITY.issue(
                "principal_orchestrator",
                None,
                "2026-08-10T00:00:02Z",
            ),
        )


def test_event_schema_registry_freezes_and_rejects_schema_drift() -> None:
    registry = EventSchemaRegistry()
    schema_id = registry.register(
        "SignedProbeEvent",
        "automarkov.test-signed-probe.v1",
        _SignedProbeEvent,
    )

    assert schema_id.startswith("sha256:")
    assert (
        registry.register(
            "SignedProbeEvent",
            "automarkov.test-signed-probe.v1",
            _SignedProbeEvent,
        )
        == schema_id
    )
    with pytest.raises(ValueError, match="already registered differently"):
        registry.register(
            "SignedProbeEvent",
            "automarkov.test-signed-probe.v1",
            _DriftedSignedProbeEvent,
        )

    registry.freeze()
    with pytest.raises(RuntimeError, match="frozen"):
        registry.register(
            "SignedProbeEvent",
            "automarkov.test-signed-probe.v1",
            _SignedProbeEvent,
        )
