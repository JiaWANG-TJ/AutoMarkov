from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from typing import cast
from uuid import UUID

from automarkov import lifecycle
from automarkov.canonical import canonical_json_bytes
from automarkov.lifecycle import (
    ZERO_EVENT_HASH,
    EventHead,
    EventRecord,
    encode_event_record,
    parse_event_record,
    project_records,
)

_RUN_ID = "run_projection_identity"
_ISSUED_AT = "2026-08-10T12:00:00Z"
_HASH = "sha256:" + "a" * 64


def _artifact(marker: str) -> dict[str, str]:
    return {
        "artifact_id": "artifact_" + marker * 64,
        "payload_hash": f"sha256:{marker * 64}",
    }


def _uuid7(index: int) -> str:
    timestamp_ms = int(datetime.fromisoformat(_ISSUED_AT).timestamp() * 1_000)
    value = (timestamp_ms << 80) | (7 << 76) | (2 << 62) | index
    return str(UUID(int=value))


def _record(raw_event: dict[str, object]) -> EventRecord:
    return parse_event_record(encode_event_record(raw_event))


def _root() -> EventRecord:
    return _record(
        {
            "schema_version": "automarkov.run-created.v1",
            "event_type": "RunCreated",
            "signing_domain": "AutoMarkov-Run-Created-v1",
            "event_id": _uuid7(0),
            "experiment_id": None,
            "run_id": _RUN_ID,
            "actor_principal_id": "principal_projection_test",
            "issued_at": _ISSUED_AT,
            "sequence_no": 0,
            "previous_event_hash": ZERO_EVENT_HASH,
            "run_manifest_artifact_id": _artifact("1")["artifact_id"],
            "run_manifest_payload_hash": _artifact("1")["payload_hash"],
            "initial_state": "RECEIVED",
            "creation_principal_id": "principal_projection_test",
            "reason_code": "run_created",
            "nonce_b64url": base64.urlsafe_b64encode(bytes(range(16)))
            .decode()
            .rstrip("="),
            "signing_key_id": "key_projection_test",
            "signature_algorithm": "Ed25519",
            "signature_b64url": base64.urlsafe_b64encode(bytes(64))
            .decode()
            .rstrip("="),
        }
    )


def _common(
    records: list[EventRecord], schema_version: str, event_type: str
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "event_type": event_type,
        "event_id": _uuid7(len(records)),
        "experiment_id": None,
        "run_id": _RUN_ID,
        "actor_principal_id": "principal_projection_test",
        "actor_process_execution_id": None,
        "issued_at": _ISSUED_AT,
        "sequence_no": len(records),
        "previous_event_hash": records[-1].event_hash,
    }


def _append(records: list[EventRecord], raw_event: dict[str, object]) -> EventRecord:
    record = _record(raw_event)
    records.append(record)
    return record


def _approval(
    records: list[EventRecord],
    *,
    decision: str,
    supersedes: str | None,
) -> EventRecord:
    raw = _common(
        records,
        "automarkov.approval-event.v1",
        "SignedApprovalEvent",
    )
    raw.pop("actor_process_execution_id")
    return _append(
        records,
        raw
        | {
            "signing_domain": "AutoMarkov-Approval-v1",
            "decision": decision,
            "artifact": _artifact("b"),
            "supersedes_approval_event_id": supersedes,
            "approval_principal_id": "principal_projection_test",
            "approval_principal_kind": "experiment_approval_policy",
            "approval_policy_source_hash": None,
            "input_report_artifact_ids": [],
            "reason_code": "approval_decision",
            "nonce_b64url": "A" * 22,
            "signing_key_id": "key_projection_test",
            "signature_algorithm": "Ed25519",
            "signature_b64url": "A" * 86,
        },
    )


def _terminal_records() -> tuple[list[EventRecord], EventRecord]:
    records = [_root()]
    report = _artifact("f")
    gate = _append(
        records,
        _common(records, "automarkov.stage-gate-passed.v1", "StageGatePassed")
        | {
            "gate_id": "INTAKE_SCHEMA_BUDGET_AUTHORITY",
            "gate_version": "v1",
            "gate_contract_hash": _HASH,
            "subject_artifact_references": [],
            "gate_report": report,
            "from_state": "RECEIVED",
            "to_state": "RESEARCHING",
            "reason_code": "intake_accepted",
            "result": "passed",
        },
    )
    _append(
        records,
        _common(records, "automarkov.state-transitioned.v1", "StateTransitioned")
        | {
            "from_state": "RECEIVED",
            "to_state": "RESEARCHING",
            "trigger_event_id": gate.event.event_id,
            "trigger_event_hash": gate.event_hash,
            "input_artifact_ids": [],
            "gate_report_artifact_id": report["artifact_id"],
            "gate_report_payload_hash": report["payload_hash"],
            "budget_snapshot_artifact_id": _artifact("2")["artifact_id"],
            "budget_snapshot_payload_hash": _artifact("2")["payload_hash"],
            "reason_code": "intake_accepted",
        },
    )
    approved = _approval(records, decision="approved", supersedes=None)
    failure = _append(
        records,
        _common(records, "automarkov.validation-failed.v1", "ValidationFailed")
        | {
            "subject": _artifact("3"),
            "report": _artifact("4"),
            "validator_id": "validator_projection_test",
            "validator_version": "v1",
            "validation_level": "terminal",
            "validation_scope": "internal",
            "failure_code": "secret_or_license_violation",
        },
    )
    _append(
        records,
        _common(records, "automarkov.state-transitioned.v1", "StateTransitioned")
        | {
            "from_state": "RESEARCHING",
            "to_state": "FAILED",
            "trigger_event_id": failure.event.event_id,
            "trigger_event_hash": failure.event_hash,
            "input_artifact_ids": [],
            "gate_report_artifact_id": None,
            "gate_report_payload_hash": None,
            "budget_snapshot_artifact_id": _artifact("2")["artifact_id"],
            "budget_snapshot_payload_hash": _artifact("2")["payload_hash"],
            "reason_code": "secret_or_license_violation",
        },
    )
    return records, approved


def test_projection_replays_approvals_and_terminal_audit_at_exact_head() -> None:
    records, approved = _terminal_records()
    terminal_head = EventHead(
        run_id=_RUN_ID,
        sequence_no=records[-1].event.sequence_no,
        event_hash=records[-1].event_hash,
    )
    terminal_projection = project_records(records)

    access_revoked = _append(
        records,
        _common(
            records,
            "automarkov.artifact-access-revoked.v1",
            "ArtifactAccessRevoked",
        )
        | {
            "subject": _artifact("5"),
            "governance_policy": _artifact("6"),
            "revocation_authority_principal_id": "principal_projection_test",
            "reason_code": "retention_policy",
            "effective_at": _ISSUED_AT,
        },
    )
    revoked = _approval(
        records,
        decision="revoked",
        supersedes=approved.event.event_id,
    )

    historical = project_records(records, as_of_head=terminal_head)
    assert canonical_json_bytes(historical.model_dump(mode="json")) == (
        canonical_json_bytes(terminal_projection.model_dump(mode="json"))
    )
    assert [
        (item.event.event_id, item.validity)
        for item in historical.current_approval_snapshots
    ] == [(approved.event.event_id, "valid")]
    assert historical.post_terminal_audit_event_references == ()

    latest = project_records(records)
    assert [
        (item.event.event_id, item.validity)
        for item in latest.current_approval_snapshots
    ] == [(approved.event.event_id, "revoked")]
    assert [
        (item.event_id, item.sequence_no, item.event_hash)
        for item in latest.post_terminal_audit_event_references
    ] == [
        (
            access_revoked.event.event_id,
            access_revoked.event.sequence_no,
            access_revoked.event_hash,
        ),
        (revoked.event.event_id, revoked.event.sequence_no, revoked.event_hash),
    ]


def test_projector_identity_covers_every_closed_causal_contract_family() -> None:
    preimage = lifecycle._run_projector_contract_preimage()
    contracts = cast(dict[str, object], preimage["causal_contracts"])

    assert set(contracts) == {
        "approval",
        "budget_metric_by_kind",
        "partial_failure_codes",
        "revision",
        "revocation",
        "stage_gate",
        "terminal",
        "validation_scope_predecessors",
        "waiting",
    }
    assert [
        "RECEIVED",
        "RESEARCHING",
        "INTAKE_SCHEMA_BUDGET_AUTHORITY",
        "intake_accepted",
    ] in cast(list[list[str]], contracts["stage_gate"])
    assert [
        "WAITING_FORMAL_CONFIRMATION",
        "FORMAL_LOCKED",
        "approved",
        "formal_approved",
    ] in cast(list[list[str]], contracts["approval"])
    assert [
        "ClarificationRequested",
        "CLARIFICATION_REQUIRED",
        "clarification_required",
        "TEXT_REVIEWED",
    ] in cast(list[list[str]], contracts["terminal"])

    perturbed = deepcopy(preimage)
    changed_contracts = cast(dict[str, object], perturbed["causal_contracts"])
    stage_contracts = cast(list[list[str]], changed_contracts["stage_gate"])
    stage_contracts[0][2] = "CHANGED_GATE"
    perturbed_hash = "sha256:" + sha256(canonical_json_bytes(perturbed)).hexdigest()
    assert perturbed_hash != lifecycle.RUN_PROJECTOR_HASH
