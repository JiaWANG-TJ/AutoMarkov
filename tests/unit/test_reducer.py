from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime
from itertools import product
from typing import cast
from uuid import UUID

import pytest
from pydantic import BaseModel

from automarkov.domain import RunState
from automarkov.errors import (
    BudgetContractError,
    EventSchemaError,
    InvalidRunTransitionError,
    RunProjectionHeadError,
    RunResumeContractError,
    RunTerminalError,
)
from automarkov.lifecycle import (
    TERMINAL_STATES,
    ZERO_EVENT_HASH,
    EventHead,
    EventRecord,
    allowed_transition,
    encode_event_record,
    parse_event_record,
    project_records,
)

_RUN_ID = "run_reducer_contract"
_ISSUED_AT = "2026-08-10T10:00:00Z"
_HASH_A = "sha256:" + "a" * 64
_HASH_B = "sha256:" + "b" * 64

# §4.4 状态表及其跨阶段预算、失败与取消规则。
# 等待恢复边按状态分别声明；下方 reducer 测试继续约束其事件绑定。
_ACTIVE_TRANSITIONS: dict[str, set[str]] = {
    "RECEIVED": {"RESEARCHING", "BLOCKED", "CANCELLED"},
    "RESEARCHING": {
        "TEXT_DRAFTED",
        "WAITING_EVIDENCE",
        "BLOCKED",
        "WAITING_RUNTIME",
        "BUDGET_EXHAUSTED",
        "FAILED",
    },
    "TEXT_DRAFTED": {"TEXT_REVIEWED"},
    "TEXT_REVIEWED": {
        "WAITING_TEXT_CONFIRMATION",
        "CLARIFICATION_REQUIRED",
        "TEXT_DRAFTED",
    },
    "WAITING_TEXT_CONFIRMATION": {
        "TEXT_LOCKED",
        "TEXT_DRAFTED",
        "BLOCKED",
        "FAILED",
    },
    "TEXT_LOCKED": {"CLASSIFIED", "FAILED"},
    "CLASSIFIED": {
        "FORMAL_DRAFTED",
        "REDUCTION_PROPOSAL_DRAFTING",
        "OOD_HANDOFF_BUILDING",
        "FAILED",
    },
    "REDUCTION_PROPOSAL_DRAFTING": {
        "WAITING_REDUCTION_CONFIRMATION",
        "FAILED",
    },
    "WAITING_REDUCTION_CONFIRMATION": {
        "TEXT_DRAFTED",
        "OOD_HANDOFF_BUILDING",
        "BLOCKED",
    },
    "OOD_HANDOFF_BUILDING": {
        "OOD_HANDOFF_VALIDATING",
        "WAITING_ASSET",
        "BLOCKED",
    },
    "OOD_HANDOFF_VALIDATING": {
        "OOD_PACKAGED",
        "OOD_HANDOFF_BUILDING",
        "WAITING_ASSET",
        "FAILED",
    },
    "FORMAL_DRAFTED": {"FORMAL_REVIEWED", "TEXT_DRAFTED"},
    "FORMAL_REVIEWED": {
        "WAITING_FORMAL_CONFIRMATION",
        "FORMAL_DRAFTED",
        "TEXT_DRAFTED",
    },
    "WAITING_FORMAL_CONFIRMATION": {
        "FORMAL_LOCKED",
        "FORMAL_DRAFTED",
        "BLOCKED",
        "FAILED",
    },
    "FORMAL_LOCKED": {"IMPLEMENTATION_SELECTED", "FAILED"},
    "IMPLEMENTATION_SELECTED": {
        "ENVIRONMENT_IMPLEMENTED",
        "WAITING_RUNTIME",
        "WAITING_ASSET",
        "BLOCKED",
    },
    "ENVIRONMENT_IMPLEMENTED": {"UNIT_VALIDATING"},
    "UNIT_VALIDATING": {
        "SIMULATION_VALIDATING",
        "WAITING_RUNTIME",
        "ENVIRONMENT_IMPLEMENTED",
        "FORMAL_DRAFTED",
        "TEXT_DRAFTED",
    },
    "SIMULATION_VALIDATING": {
        "SEALED_E2E_VALIDATING",
        "WAITING_RUNTIME",
        "ENVIRONMENT_IMPLEMENTED",
        "FORMAL_DRAFTED",
        "TEXT_DRAFTED",
    },
    "SEALED_E2E_VALIDATING": {
        "TRAINING_SMOKE_TESTING",
        "WAITING_RUNTIME",
        "PARTIAL",
        "FAILED",
    },
    "TRAINING_SMOKE_TESTING": {
        "POLICY_TRAINING",
        "WAITING_RUNTIME",
        "WAITING_ASSET",
        "PARTIAL",
        "FAILED",
    },
    "POLICY_TRAINING": {
        "FINAL_EVALUATING",
        "WAITING_RUNTIME",
        "BUDGET_EXHAUSTED",
        "FAILED",
    },
    "FINAL_EVALUATING": {
        "PACKAGING",
        "WAITING_RUNTIME",
        "PARTIAL",
        "FAILED",
    },
    "PACKAGING": {"COMPLETED", "PARTIAL", "FAILED"},
}

_WAITING_TRANSITIONS = {
    "WAITING_RUNTIME": {
        "RESEARCHING",
        "IMPLEMENTATION_SELECTED",
        "UNIT_VALIDATING",
        "SIMULATION_VALIDATING",
        "SEALED_E2E_VALIDATING",
        "TRAINING_SMOKE_TESTING",
        "POLICY_TRAINING",
        "FINAL_EVALUATING",
        "CANCELLED",
        "PARTIAL",
    },
    "WAITING_EVIDENCE": {
        "RESEARCHING",
        "BUDGET_EXHAUSTED",
        "BLOCKED",
        "CANCELLED",
    },
    "WAITING_ASSET": {
        "OOD_HANDOFF_BUILDING",
        "OOD_HANDOFF_VALIDATING",
        "IMPLEMENTATION_SELECTED",
        "TRAINING_SMOKE_TESTING",
        "PARTIAL",
    },
    "BLOCKED": {
        "RECEIVED",
        "RESEARCHING",
        "WAITING_TEXT_CONFIRMATION",
        "WAITING_REDUCTION_CONFIRMATION",
        "OOD_HANDOFF_BUILDING",
        "WAITING_FORMAL_CONFIRMATION",
        "IMPLEMENTATION_SELECTED",
        "CANCELLED",
        "PARTIAL",
    },
}

_EXPECTED_TRANSITIONS = {
    state: set(destinations)
    for state, destinations in (_ACTIVE_TRANSITIONS | _WAITING_TRANSITIONS).items()
}

# 候选冻结前，审批撤销必须回滚到受影响的最近草稿阶段。
_APPROVAL_REVOCATION_TRANSITIONS = {
    "TEXT_LOCKED": {"TEXT_DRAFTED"},
    "CLASSIFIED": {"TEXT_DRAFTED"},
    "REDUCTION_PROPOSAL_DRAFTING": {"TEXT_DRAFTED"},
    "WAITING_REDUCTION_CONFIRMATION": {"TEXT_DRAFTED"},
    "OOD_HANDOFF_BUILDING": {"TEXT_DRAFTED"},
    "OOD_HANDOFF_VALIDATING": {"TEXT_DRAFTED"},
    "FORMAL_DRAFTED": {"TEXT_DRAFTED"},
    "FORMAL_REVIEWED": {"TEXT_DRAFTED"},
    "WAITING_FORMAL_CONFIRMATION": {"TEXT_DRAFTED"},
    "FORMAL_LOCKED": {"TEXT_DRAFTED", "FORMAL_DRAFTED"},
    "IMPLEMENTATION_SELECTED": {"TEXT_DRAFTED", "FORMAL_DRAFTED"},
    "ENVIRONMENT_IMPLEMENTED": {"TEXT_DRAFTED", "FORMAL_DRAFTED"},
    "UNIT_VALIDATING": {"TEXT_DRAFTED", "FORMAL_DRAFTED"},
    "SIMULATION_VALIDATING": {"TEXT_DRAFTED", "FORMAL_DRAFTED"},
}
for _source, _destinations in _APPROVAL_REVOCATION_TRANSITIONS.items():
    _EXPECTED_TRANSITIONS[_source].update(_destinations)
for _destinations in _EXPECTED_TRANSITIONS.values():
    _destinations.add("BUDGET_EXHAUSTED")

_STAGE_GATE_CONTRACTS = {
    ("RECEIVED", "RESEARCHING"): (
        "INTAKE_SCHEMA_BUDGET_AUTHORITY",
        "intake_accepted",
    ),
    ("RESEARCHING", "TEXT_DRAFTED"): (
        "EVIDENCE_LEDGER_CLOSURE",
        "research_completed",
    ),
    ("TEXT_DRAFTED", "TEXT_REVIEWED"): ("TEXT_SCHEMA", "text_schema_passed"),
    ("TEXT_REVIEWED", "WAITING_TEXT_CONFIRMATION"): (
        "TEXT_CRITIC_REVIEW",
        "text_review_passed",
    ),
    ("TEXT_LOCKED", "CLASSIFIED"): (
        "CLASSIFICATION_BINDING",
        "classification_passed",
    ),
    ("CLASSIFIED", "FORMAL_DRAFTED"): (
        "CLASSIFICATION_IN_SCOPE",
        "in_scope_classification_selected",
    ),
    ("CLASSIFIED", "REDUCTION_PROPOSAL_DRAFTING"): (
        "CLASSIFICATION_REDUCTION",
        "reduction_required",
    ),
    ("CLASSIFIED", "OOD_HANDOFF_BUILDING"): (
        "CLASSIFICATION_OOD",
        "ood_classification_selected",
    ),
    ("REDUCTION_PROPOSAL_DRAFTING", "WAITING_REDUCTION_CONFIRMATION"): (
        "REDUCTION_PROPOSAL",
        "reduction_proposal_ready",
    ),
    ("OOD_HANDOFF_BUILDING", "OOD_HANDOFF_VALIDATING"): (
        "OOD_HANDOFF_BUILD",
        "ood_handoff_built",
    ),
    ("FORMAL_DRAFTED", "FORMAL_REVIEWED"): (
        "FORMAL_SCHEMA_STRUCTURAL",
        "formal_schema_passed",
    ),
    ("FORMAL_REVIEWED", "WAITING_FORMAL_CONFIRMATION"): (
        "FORMAL_CRITIC_REVIEW",
        "formal_review_passed",
    ),
    ("FORMAL_LOCKED", "IMPLEMENTATION_SELECTED"): (
        "FORMAL_LOCK_CLOSURE",
        "formal_contract_locked",
    ),
    ("IMPLEMENTATION_SELECTED", "ENVIRONMENT_IMPLEMENTED"): (
        "IMPLEMENTATION_ROUTE_SELECTION",
        "implementation_completed",
    ),
    ("ENVIRONMENT_IMPLEMENTED", "UNIT_VALIDATING"): (
        "ENVIRONMENT_ARTIFACT_FREEZE",
        "environment_artifacts_frozen",
    ),
    ("UNIT_VALIDATING", "SIMULATION_VALIDATING"): (
        "UNIT_VALIDATION",
        "unit_validation_passed",
    ),
    ("SIMULATION_VALIDATING", "SEALED_E2E_VALIDATING"): (
        "PUBLIC_SIMULATION_TESTER",
        "public_simulation_passed",
    ),
    ("SEALED_E2E_VALIDATING", "TRAINING_SMOKE_TESTING"): (
        "SEALED_E2E",
        "sealed_e2e_passed",
    ),
    ("TRAINING_SMOKE_TESTING", "POLICY_TRAINING"): (
        "TRAINING_SMOKE",
        "training_smoke_passed",
    ),
    ("POLICY_TRAINING", "FINAL_EVALUATING"): (
        "POLICY_TRAINING",
        "policy_training_completed",
    ),
    ("FINAL_EVALUATING", "PACKAGING"): (
        "FINAL_EVALUATION",
        "final_evaluation_completed",
    ),
}

_APPROVAL_TRANSITION_CONTRACTS = {
    ("WAITING_TEXT_CONFIRMATION", "TEXT_LOCKED"): (
        "approved",
        "text_approved",
    ),
    ("WAITING_TEXT_CONFIRMATION", "TEXT_DRAFTED"): (
        "rejected",
        "text_rejected",
    ),
    ("WAITING_REDUCTION_CONFIRMATION", "OOD_HANDOFF_BUILDING"): (
        "rejected",
        "reduction_rejected",
    ),
    ("WAITING_FORMAL_CONFIRMATION", "FORMAL_LOCKED"): (
        "approved",
        "formal_approved",
    ),
    ("WAITING_FORMAL_CONFIRMATION", "FORMAL_DRAFTED"): (
        "rejected",
        "formal_rejected",
    ),
}

_CANONICAL_PATHS = {
    "RECEIVED": (),
    "RESEARCHING": ("RESEARCHING",),
    "TEXT_REVIEWED": ("RESEARCHING", "TEXT_DRAFTED", "TEXT_REVIEWED"),
    "OOD_HANDOFF_VALIDATING": (
        "RESEARCHING",
        "TEXT_DRAFTED",
        "TEXT_REVIEWED",
        "WAITING_TEXT_CONFIRMATION",
        "TEXT_LOCKED",
        "CLASSIFIED",
        "OOD_HANDOFF_BUILDING",
        "OOD_HANDOFF_VALIDATING",
    ),
    "IMPLEMENTATION_SELECTED": (
        "RESEARCHING",
        "TEXT_DRAFTED",
        "TEXT_REVIEWED",
        "WAITING_TEXT_CONFIRMATION",
        "TEXT_LOCKED",
        "CLASSIFIED",
        "FORMAL_DRAFTED",
        "FORMAL_REVIEWED",
        "WAITING_FORMAL_CONFIRMATION",
        "FORMAL_LOCKED",
        "IMPLEMENTATION_SELECTED",
    ),
    "PACKAGING": (
        "RESEARCHING",
        "TEXT_DRAFTED",
        "TEXT_REVIEWED",
        "WAITING_TEXT_CONFIRMATION",
        "TEXT_LOCKED",
        "CLASSIFIED",
        "FORMAL_DRAFTED",
        "FORMAL_REVIEWED",
        "WAITING_FORMAL_CONFIRMATION",
        "FORMAL_LOCKED",
        "IMPLEMENTATION_SELECTED",
        "ENVIRONMENT_IMPLEMENTED",
        "UNIT_VALIDATING",
        "SIMULATION_VALIDATING",
        "SEALED_E2E_VALIDATING",
        "TRAINING_SMOKE_TESTING",
        "POLICY_TRAINING",
        "FINAL_EVALUATING",
        "PACKAGING",
    ),
}

_TERMINAL_PREDECESSORS = {
    "COMPLETED": "PACKAGING",
    "CLARIFICATION_REQUIRED": "TEXT_REVIEWED",
    "OOD_PACKAGED": "OOD_HANDOFF_VALIDATING",
    "PARTIAL": "PACKAGING",
    "BUDGET_EXHAUSTED": "RESEARCHING",
    "FAILED": "RESEARCHING",
    "CANCELLED": "RECEIVED",
}


def _artifact_ref(marker: str) -> dict[str, str]:
    return {
        "artifact_id": "artifact_" + marker * 64,
        "payload_hash": f"sha256:{marker * 64}",
    }


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


def _record(raw_event: dict[str, object]) -> EventRecord:
    return parse_event_record(encode_event_record(raw_event))


def _root_record() -> EventRecord:
    nonce = base64.urlsafe_b64encode(bytes(range(16))).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(bytes(64)).decode().rstrip("=")
    return _record(
        {
            "schema_version": "automarkov.run-created.v1",
            "event_type": "RunCreated",
            "signing_domain": "AutoMarkov-Run-Created-v1",
            "event_id": _uuid7(0),
            "experiment_id": None,
            "run_id": _RUN_ID,
            "actor_principal_id": "principal_reducer_test",
            "issued_at": _ISSUED_AT,
            "sequence_no": 0,
            "previous_event_hash": ZERO_EVENT_HASH,
            "run_manifest_artifact_id": _artifact_ref("1")["artifact_id"],
            "run_manifest_payload_hash": _artifact_ref("1")["payload_hash"],
            "initial_state": "RECEIVED",
            "creation_principal_id": "principal_reducer_test",
            "reason_code": "run_created",
            "nonce_b64url": nonce,
            "signing_key_id": "key_reducer_test",
            "signature_algorithm": "Ed25519",
            "signature_b64url": signature,
        }
    )


def _common(
    records: list[EventRecord],
    schema_version: str,
    event_type: str,
    *,
    variant: int = 0,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "event_type": event_type,
        "event_id": _uuid7(len(records), variant=variant),
        "experiment_id": None,
        "run_id": _RUN_ID,
        "actor_principal_id": "principal_reducer_test",
        "actor_process_execution_id": None,
        "issued_at": _ISSUED_AT,
        "sequence_no": len(records),
        "previous_event_hash": records[-1].event_hash,
    }


def _append(records: list[EventRecord], raw_event: dict[str, object]) -> EventRecord:
    record = _record(raw_event)
    records.append(record)
    return record


def _advance(
    records: list[EventRecord],
    to_state: str,
    *,
    trigger: EventRecord | None = None,
    budget_ref: dict[str, str] | None = None,
    reason_code: str = "test_transition",
) -> EventRecord:
    projection = project_records(
        records[:-1]
        if trigger is records[-1]
        and not isinstance(records[-1].event, type(records[0].event))
        and records[-1].event.event_type != "StateTransitioned"
        else records
    )
    gate_report: dict[str, str] | None = None
    if trigger is None:
        from_state = projection.state.value
        edge = (from_state, to_state)
        if edge in _STAGE_GATE_CONTRACTS:
            gate_id, reason_code = _STAGE_GATE_CONTRACTS[edge]
            gate_report = _artifact_ref("f")
            trigger = _append(
                records,
                _common(
                    records,
                    "automarkov.stage-gate-passed.v1",
                    "StageGatePassed",
                )
                | {
                    "gate_id": gate_id,
                    "gate_version": "v1",
                    "gate_contract_hash": _HASH_A,
                    "subject_artifact_references": [],
                    "gate_report": gate_report,
                    "from_state": from_state,
                    "to_state": to_state,
                    "reason_code": reason_code,
                    "result": "passed",
                },
            )
        else:
            decision, reason_code = _APPROVAL_TRANSITION_CONTRACTS[edge]
            trigger = _append(
                records,
                {
                    "schema_version": "automarkov.approval-event.v1",
                    "signing_domain": "AutoMarkov-Approval-v1",
                    "event_type": "SignedApprovalEvent",
                    "event_id": _uuid7(len(records)),
                    "experiment_id": None,
                    "run_id": _RUN_ID,
                    "sequence_no": len(records),
                    "previous_event_hash": records[-1].event_hash,
                    "actor_principal_id": "principal_reducer_test",
                    "issued_at": _ISSUED_AT,
                    "decision": decision,
                    "artifact": _artifact_ref("a"),
                    "supersedes_approval_event_id": None,
                    "approval_principal_id": "principal_reducer_test",
                    "approval_principal_kind": "experiment_approval_policy",
                    "approval_policy_source_hash": None,
                    "input_report_artifact_ids": [],
                    "reason_code": reason_code,
                    "nonce_b64url": "A" * 22,
                    "signing_key_id": "key_reducer_test",
                    "signature_algorithm": "Ed25519",
                    "signature_b64url": "A" * 86,
                },
            )
    else:
        gate_report = None
    budget_ref = budget_ref or _artifact_ref("2")
    return _append(
        records,
        _common(
            records,
            "automarkov.state-transitioned.v1",
            "StateTransitioned",
        )
        | {
            "from_state": projection.state.value,
            "to_state": to_state,
            "trigger_event_id": trigger.event.event_id,
            "trigger_event_hash": trigger.event_hash,
            "input_artifact_ids": [],
            "gate_report_artifact_id": (
                gate_report["artifact_id"] if gate_report is not None else None
            ),
            "gate_report_payload_hash": (
                gate_report["payload_hash"] if gate_report is not None else None
            ),
            "budget_snapshot_artifact_id": budget_ref["artifact_id"],
            "budget_snapshot_payload_hash": budget_ref["payload_hash"],
            "reason_code": reason_code,
        },
    )


def _records_at_state(target_state: str) -> list[EventRecord]:
    records = [_root_record()]
    for state in _CANONICAL_PATHS[target_state]:
        _advance(records, state)
    assert project_records(records).state.value == target_state
    return records


def _terminal_records(terminal_state: str) -> list[EventRecord]:
    records = _records_at_state(_TERMINAL_PREDECESSORS[terminal_state])
    if terminal_state == "BUDGET_EXHAUSTED":
        cause = _append(
            records,
            _common(
                records,
                "automarkov.budget-exhausted.v1",
                "BudgetExhausted",
            )
            | {
                "budget_kind": "wall_time",
                "budget_policy_artifact_id": _artifact_ref("3")["artifact_id"],
                "budget_policy_payload_hash": _artifact_ref("3")["payload_hash"],
                "budget_snapshot_artifact_id": _artifact_ref("9")["artifact_id"],
                "budget_snapshot_payload_hash": _artifact_ref("9")["payload_hash"],
                "canonical_unit": "milliseconds",
                "limit": 5,
                "consumed": 5,
                "reserved": 0,
                "cause_receipt_artifact_id": _artifact_ref("4")["artifact_id"],
                "cause_receipt_payload_hash": _artifact_ref("4")["payload_hash"],
                "phase": "research",
                "reason_code": "budget_exhausted",
                "exhausted_at": _ISSUED_AT,
            },
        )
        reason_code = "budget_exhausted"
    elif terminal_state in {"COMPLETED", "OOD_PACKAGED"}:
        cause = _append(
            records,
            _common(
                records,
                "automarkov.validation-claimed.v1",
                "ValidationClaimed",
            )
            | {
                "claim": _artifact_ref("3"),
                "subject": _artifact_ref("4"),
                "reports": [_artifact_ref("5")],
                "validator_id": "validator_terminal",
                "validator_version": "v1",
                "validation_level": "schema",
                "validation_scope": [
                    "package" if terminal_state == "COMPLETED" else "ood_handoff"
                ],
            },
        )
        reason_code = (
            "run_completed" if terminal_state == "COMPLETED" else "ood_handoff_packaged"
        )
    elif terminal_state == "CLARIFICATION_REQUIRED":
        cause = _append(
            records,
            _common(
                records,
                "automarkov.clarification-requested.v1",
                "ClarificationRequested",
            )
            | {
                "task": _artifact_ref("3"),
                "review": _artifact_ref("4"),
                "result": _artifact_ref("5"),
                "gap_ids": ["gap_1"],
                "clarification_policy": _artifact_ref("6"),
                "reason_code": "clarification_required",
            },
        )
        reason_code = "clarification_required"
    elif terminal_state in {"PARTIAL", "FAILED"}:
        failure_code = (
            "required_package_artifact_missing"
            if terminal_state == "PARTIAL"
            else "unrecoverable_internal_error"
        )
        cause = _append(
            records,
            _common(
                records,
                "automarkov.validation-failed.v1",
                "ValidationFailed",
            )
            | {
                "subject": _artifact_ref("3"),
                "report": _artifact_ref("4"),
                "validator_id": "validator_terminal",
                "validator_version": "v1",
                "validation_level": "terminal",
                "validation_scope": (
                    "packaging" if terminal_state == "PARTIAL" else "internal"
                ),
                "failure_code": failure_code,
            },
        )
        reason_code = failure_code
    else:
        cause = _append(
            records,
            _common(
                records,
                "automarkov.run-termination-requested.v1",
                "RunTerminationRequested",
            )
            | {
                "requested_terminal_state": "CANCELLED",
                "requesting_authority_principal_id": "principal_reducer_test",
                "request_evidence": None,
                "reason_code": "user_cancelled",
            },
        )
        reason_code = "user_cancelled"
    _advance(records, terminal_state, trigger=cause, reason_code=reason_code)
    return records


def test_transition_table_accepts_every_spec_edge_and_rejects_its_complement() -> None:
    all_states = {state.value for state in RunState}
    assert all_states == set(_EXPECTED_TRANSITIONS) | {
        state
        for destinations in _EXPECTED_TRANSITIONS.values()
        for state in destinations
    }

    for from_name, to_name in product(sorted(all_states), repeat=2):
        expected = to_name in _EXPECTED_TRANSITIONS.get(from_name, set())
        assert allowed_transition(RunState(from_name), RunState(to_name)) is expected


@pytest.mark.parametrize(
    ("waiting_state", "origin_state", "wait_kind"),
    [
        ("WAITING_RUNTIME", "RESEARCHING", "runtime"),
        ("WAITING_EVIDENCE", "RESEARCHING", "evidence"),
        ("WAITING_ASSET", "IMPLEMENTATION_SELECTED", "asset"),
    ],
)
def test_waiting_states_resume_only_exact_state_identity_gate_and_authority(
    waiting_state: str,
    origin_state: str,
    wait_kind: str,
) -> None:
    records = _records_at_state(origin_state)
    cause = records[-1]
    if waiting_state == "WAITING_EVIDENCE":
        cause = _append(
            records,
            _common(
                records,
                "automarkov.evidence-temporarily-unavailable.v1",
                "EvidenceTemporarilyUnavailable",
            )
            | {
                "lease_pool_artifact_id": _artifact_ref("4")["artifact_id"],
                "lease_pool_payload_hash": _artifact_ref("4")["payload_hash"],
                "lease_snapshot_artifact_id": _artifact_ref("5")["artifact_id"],
                "lease_snapshot_payload_hash": _artifact_ref("5")["payload_hash"],
                "availability_probe_artifact_id": _artifact_ref("6")["artifact_id"],
                "availability_probe_payload_hash": _artifact_ref("6")["payload_hash"],
                "slot_state_counts": {
                    "available": 0,
                    "leased": 1,
                    "cooldown": 0,
                    "invalid_credential": 0,
                },
                "earliest_availability": _ISSUED_AT,
            },
        )
    waiting_raw = _common(
        records,
        {
            "WAITING_RUNTIME": "automarkov.waiting-runtime.v1",
            "WAITING_EVIDENCE": "automarkov.waiting-evidence.v1",
            "WAITING_ASSET": "automarkov.waiting-asset.v1",
        }[waiting_state],
        {
            "WAITING_RUNTIME": "WaitingRuntime",
            "WAITING_EVIDENCE": "WaitingEvidence",
            "WAITING_ASSET": "WaitingAsset",
        }[waiting_state],
    ) | {
        "resume_state": origin_state,
        "trigger_event_id": cause.event.event_id,
        "trigger_event_hash": cause.event_hash,
        "failure_report_artifact_id": _artifact_ref("3")["artifact_id"],
        "failure_report_payload_hash": _artifact_ref("3")["payload_hash"],
        "recovery_gate_id": f"gate_{wait_kind}",
        "recovery_condition_hash": _HASH_B,
        "entered_at": _ISSUED_AT,
    }
    if waiting_state == "WAITING_RUNTIME":
        waiting_raw |= {
            "wait_reason_code": "runtime_profile_unavailable",
            "dependency_kind": "runtime_profile",
            "profile_id": "profile_test",
            "process_execution_id": None,
            "protocol_edge_id": None,
            "dependency_identity_hash": _HASH_A,
            "failed_readiness_gate_id": "gate_runtime",
        }
    elif waiting_state == "WAITING_EVIDENCE":
        waiting_raw |= {
            "wait_reason_code": "evidence_temporarily_unavailable",
            "lease_pool_artifact_id": _artifact_ref("4")["artifact_id"],
            "lease_pool_payload_hash": _artifact_ref("4")["payload_hash"],
            "lease_snapshot_artifact_id": _artifact_ref("5")["artifact_id"],
            "lease_snapshot_payload_hash": _artifact_ref("5")["payload_hash"],
            "lease_identity_hash": _HASH_A,
            "earliest_availability": _ISSUED_AT,
        }
    else:
        waiting_raw |= {
            "wait_reason_code": "provisioning_pending",
            "asset_identity_hash": _HASH_A,
            "license_identity_hash": _HASH_B,
            "provisioning_authority_principal_id": "principal_asset_authority",
        }
    waiting = _append(records, waiting_raw)
    _advance(records, waiting_state, trigger=waiting)
    waiting_prefix = deepcopy(records)

    restore_raw = _common(
        records,
        "automarkov.wait-resolved.v1",
        "WaitResolved",
    ) | {
        "wait_kind": wait_kind,
        "waiting_event_id": waiting.event.event_id,
        "waiting_event_hash": waiting.event_hash,
        "resume_state": origin_state,
        "recovery_gate_id": f"gate_{wait_kind}",
        "recovery_report_artifact_id": _artifact_ref("6")["artifact_id"],
        "recovery_report_payload_hash": _artifact_ref("6")["payload_hash"],
        "identity_hash": _HASH_A,
        "resolved_at": _ISSUED_AT,
    }

    restored = _append(records, restore_raw)
    _advance(records, origin_state, trigger=restored)
    assert project_records(records).waiting is None

    mismatched = waiting_prefix
    mismatched_raw = deepcopy(restore_raw)
    mismatched_raw["previous_event_hash"] = mismatched[-1].event_hash
    mismatched_raw["identity_hash"] = _HASH_B
    mismatched_restore = _append(mismatched, mismatched_raw)
    _advance(mismatched, origin_state, trigger=mismatched_restore)
    with pytest.raises(RunResumeContractError):
        project_records(mismatched)


def test_blocked_resumes_only_registered_authority_and_revalidated_evidence() -> None:
    records = _records_at_state("RECEIVED")
    blocked = _append(
        records,
        _common(records, "automarkov.blocked.v1", "Blocked")
        | {
            "resume_state": "RECEIVED",
            "block_reason_code": "credential_required",
            "external_authority_kind": "credential",
            "external_authority_principal_id": "principal_registered_authority",
            "resolution_condition_hash": _HASH_A,
            "failure_report_artifact_id": _artifact_ref("3")["artifact_id"],
            "failure_report_payload_hash": _artifact_ref("3")["payload_hash"],
            "recheck_gate_id": "gate_blocked",
            "entered_at": _ISSUED_AT,
        },
    )
    _advance(records, "BLOCKED", trigger=blocked)
    prefix = deepcopy(records)
    resolved_raw = _common(
        records,
        "automarkov.block-resolved.v1",
        "BlockResolved",
    ) | {
        "blocked_event_id": blocked.event.event_id,
        "blocked_event_hash": blocked.event_hash,
        "authority_principal_id": "principal_registered_authority",
        "resolution_evidence_artifact_id": _artifact_ref("4")["artifact_id"],
        "resolution_evidence_payload_hash": _artifact_ref("4")["payload_hash"],
        "revalidation_report_artifact_id": _artifact_ref("5")["artifact_id"],
        "revalidation_report_payload_hash": _artifact_ref("5")["payload_hash"],
    }
    resolved = _append(records, resolved_raw)
    _advance(records, "RECEIVED", trigger=resolved)
    assert project_records(records).waiting is None

    wrong = prefix
    wrong_raw = deepcopy(resolved_raw)
    wrong_raw["previous_event_hash"] = wrong[-1].event_hash
    wrong_raw["authority_principal_id"] = "principal_wrong_authority"
    wrong_resolved = _append(wrong, wrong_raw)
    _advance(wrong, "RECEIVED", trigger=wrong_resolved)
    with pytest.raises(RunResumeContractError):
        project_records(wrong)


def test_waiting_evidence_to_blocked_requires_typed_authority_trigger() -> None:
    records = _records_at_state("RESEARCHING")
    unavailable = _append(
        records,
        _common(
            records,
            "automarkov.evidence-temporarily-unavailable.v1",
            "EvidenceTemporarilyUnavailable",
        )
        | {
            "lease_pool_artifact_id": _artifact_ref("4")["artifact_id"],
            "lease_pool_payload_hash": _artifact_ref("4")["payload_hash"],
            "lease_snapshot_artifact_id": _artifact_ref("5")["artifact_id"],
            "lease_snapshot_payload_hash": _artifact_ref("5")["payload_hash"],
            "availability_probe_artifact_id": _artifact_ref("6")["artifact_id"],
            "availability_probe_payload_hash": _artifact_ref("6")["payload_hash"],
            "slot_state_counts": {
                "available": 0,
                "leased": 0,
                "cooldown": 0,
                "invalid_credential": 1,
            },
            "earliest_availability": _ISSUED_AT,
        },
    )
    waiting = _append(
        records,
        _common(records, "automarkov.waiting-evidence.v1", "WaitingEvidence")
        | {
            "resume_state": "RESEARCHING",
            "wait_reason_code": "evidence_temporarily_unavailable",
            "trigger_event_id": unavailable.event.event_id,
            "trigger_event_hash": unavailable.event_hash,
            "failure_report_artifact_id": _artifact_ref("3")["artifact_id"],
            "failure_report_payload_hash": _artifact_ref("3")["payload_hash"],
            "recovery_gate_id": "gate_evidence",
            "recovery_condition_hash": _HASH_B,
            "entered_at": _ISSUED_AT,
            "lease_pool_artifact_id": _artifact_ref("4")["artifact_id"],
            "lease_pool_payload_hash": _artifact_ref("4")["payload_hash"],
            "lease_snapshot_artifact_id": _artifact_ref("5")["artifact_id"],
            "lease_snapshot_payload_hash": _artifact_ref("5")["payload_hash"],
            "lease_identity_hash": _HASH_A,
            "earliest_availability": _ISSUED_AT,
        },
    )
    _advance(records, "WAITING_EVIDENCE", trigger=waiting)
    _append(
        records,
        _common(
            records,
            "automarkov.evidence-authority-required.v1",
            "EvidenceAuthorityRequired",
        )
        | {
            "lease_pool_artifact_id": _artifact_ref("4")["artifact_id"],
            "lease_pool_payload_hash": _artifact_ref("4")["payload_hash"],
            "lease_snapshot_artifact_id": _artifact_ref("5")["artifact_id"],
            "lease_snapshot_payload_hash": _artifact_ref("5")["payload_hash"],
            "slot_state_counts": {
                "available": 0,
                "leased": 0,
                "cooldown": 0,
                "invalid_credential": 1,
            },
            "external_authority_principal_id": "principal_evidence_authority",
            "resolution_condition_hash": _HASH_A,
            "failure_report_artifact_id": _artifact_ref("7")["artifact_id"],
            "failure_report_payload_hash": _artifact_ref("7")["payload_hash"],
            "reason_code": "evidence_authority_required",
        },
    )
    blocked = _append(
        records,
        _common(records, "automarkov.blocked.v1", "Blocked")
        | {
            "resume_state": "WAITING_EVIDENCE",
            "block_reason_code": "evidence_authority_required",
            "external_authority_kind": "evidence_authority",
            "external_authority_principal_id": "principal_evidence_authority",
            "resolution_condition_hash": _HASH_A,
            "failure_report_artifact_id": _artifact_ref("7")["artifact_id"],
            "failure_report_payload_hash": _artifact_ref("7")["payload_hash"],
            "recheck_gate_id": "gate_evidence_authority",
            "entered_at": _ISSUED_AT,
        },
    )
    _advance(records, "BLOCKED", trigger=blocked)

    projection = project_records(records)
    assert projection.state is RunState.BLOCKED
    assert projection.waiting is not None
    assert projection.waiting.authority_principal_id == "principal_evidence_authority"


def test_wait_entry_requires_cause_wait_transition_adjacency() -> None:
    records = _records_at_state("RESEARCHING")
    original_cause = records[-1]
    _append(
        records,
        _common(
            records,
            "automarkov.artifact-access-revoked.v1",
            "ArtifactAccessRevoked",
        )
        | {
            "subject": _artifact_ref("7"),
            "reason_code": "retention_policy",
            "governance_policy": _artifact_ref("8"),
            "revocation_authority_principal_id": "principal_reducer",
            "effective_at": _ISSUED_AT,
        },
    )
    waiting = _append(
        records,
        _common(records, "automarkov.waiting-runtime.v1", "WaitingRuntime")
        | {
            "resume_state": "RESEARCHING",
            "wait_reason_code": "runtime_profile_unavailable",
            "trigger_event_id": original_cause.event.event_id,
            "trigger_event_hash": original_cause.event_hash,
            "failure_report_artifact_id": _artifact_ref("3")["artifact_id"],
            "failure_report_payload_hash": _artifact_ref("3")["payload_hash"],
            "recovery_gate_id": "gate_runtime",
            "recovery_condition_hash": _HASH_B,
            "entered_at": _ISSUED_AT,
            "dependency_kind": "runtime_profile",
            "profile_id": "profile_test",
            "process_execution_id": None,
            "protocol_edge_id": None,
            "dependency_identity_hash": _HASH_A,
            "failed_readiness_gate_id": "gate_runtime",
        },
    )
    _advance(records, "WAITING_RUNTIME", trigger=waiting)
    with pytest.raises(RunResumeContractError):
        project_records(records)


def test_budget_snapshots_are_monotone_and_exhaustion_requires_exact_proof() -> None:
    initial = _budget(consumed=2, limit=5)
    regressed = _budget(consumed=1, limit=5)
    exhausted = _budget(consumed=5, limit=5)
    initial_ref = _artifact_ref("7")
    regressed_ref = _artifact_ref("8")
    exhausted_ref = _artifact_ref("9")
    snapshots = {
        initial_ref["artifact_id"]: initial,
        regressed_ref["artifact_id"]: regressed,
        exhausted_ref["artifact_id"]: exhausted,
    }

    records = [_root_record()]
    _advance(records, "RESEARCHING", budget_ref=initial_ref)
    _advance(records, "TEXT_DRAFTED", budget_ref=regressed_ref)
    with pytest.raises(BudgetContractError):
        project_records(records, budget_snapshots=snapshots)

    bypass = [_root_record()]
    _advance(bypass, "RESEARCHING", budget_ref=initial_ref)
    _advance(bypass, "TEXT_DRAFTED", budget_ref=exhausted_ref)
    with pytest.raises(BudgetContractError):
        project_records(bypass, budget_snapshots=snapshots)

    terminal = [_root_record()]
    _advance(terminal, "RESEARCHING", budget_ref=initial_ref)
    cause = _append(
        terminal,
        _common(
            terminal,
            "automarkov.budget-exhausted.v1",
            "BudgetExhausted",
        )
        | {
            "budget_kind": "wall_time",
            "budget_policy_artifact_id": _artifact_ref("3")["artifact_id"],
            "budget_policy_payload_hash": _artifact_ref("3")["payload_hash"],
            "budget_snapshot_artifact_id": exhausted_ref["artifact_id"],
            "budget_snapshot_payload_hash": exhausted_ref["payload_hash"],
            "canonical_unit": "milliseconds",
            "limit": 5,
            "consumed": 5,
            "reserved": 0,
            "cause_receipt_artifact_id": _artifact_ref("4")["artifact_id"],
            "cause_receipt_payload_hash": _artifact_ref("4")["payload_hash"],
            "phase": "research",
            "reason_code": "budget_exhausted",
            "exhausted_at": _ISSUED_AT,
        },
    )
    _advance(
        terminal,
        "BUDGET_EXHAUSTED",
        trigger=cause,
        budget_ref=exhausted_ref,
        reason_code="budget_exhausted",
    )
    assert (
        project_records(terminal, budget_snapshots=snapshots).state
        is RunState.BUDGET_EXHAUSTED
    )


@pytest.mark.parametrize("terminal_state", sorted(_TERMINAL_PREDECESSORS))
def test_all_seven_terminal_states_are_unique_and_then_audit_only(
    terminal_state: str,
) -> None:
    assert {state.value for state in TERMINAL_STATES} == set(_TERMINAL_PREDECESSORS)
    records = _terminal_records(terminal_state)
    terminal = project_records(records)
    assert terminal.state.value == terminal_state
    assert terminal.terminal_event is not None
    assert terminal.terminal_snapshot_head == terminal.event_head

    second_terminal = deepcopy(records)
    _append(
        second_terminal,
        _common(
            second_terminal,
            "automarkov.run-termination-requested.v1",
            "RunTerminationRequested",
        )
        | {
            "requested_terminal_state": "CANCELLED",
            "requesting_authority_principal_id": "principal_reducer_test",
            "request_evidence": None,
            "reason_code": "user_cancelled",
        },
    )
    with pytest.raises(RunTerminalError):
        project_records(second_terminal)

    audit = deepcopy(records)
    _append(
        audit,
        _common(
            audit,
            "automarkov.artifact-access-revoked.v1",
            "ArtifactAccessRevoked",
        )
        | {
            "subject": _artifact_ref("5"),
            "reason_code": "retention_policy",
            "governance_policy": _artifact_ref("6"),
            "revocation_authority_principal_id": "principal_reducer",
            "effective_at": _ISSUED_AT,
        },
    )
    audited = project_records(audit)
    assert audited.state == terminal.state
    assert audited.terminal_event == terminal.terminal_event
    assert audited.terminal_snapshot_head == terminal.terminal_snapshot_head
    assert audited.event_head != terminal.event_head


def test_validation_projection_keeps_highest_level_per_subject_scope() -> None:
    records = _records_at_state("TEXT_REVIEWED")
    for index, level in enumerate(("schema", "structural", "schema"), start=1):
        _append(
            records,
            _common(
                records,
                "automarkov.validation-claimed.v1",
                "ValidationClaimed",
            )
            | {
                "claim": _artifact_ref(str(index)),
                "subject": _artifact_ref("8"),
                "reports": [_artifact_ref(str(index + 3))],
                "validator_id": f"validator_{level}",
                "validator_version": "v1",
                "validation_level": level,
                "validation_scope": ["transition_kernel"],
            },
        )

    projection = project_records(records)
    assert projection.state is RunState.TEXT_REVIEWED
    assert len(projection.validation_levels) == 1
    assert projection.validation_levels[0].level == "structural"
    assert tuple(projection.validation_levels[0].scope) == ("transition_kernel",)


def test_failed_transition_requires_the_exact_predecessor_for_its_cause() -> None:
    records = _records_at_state("RESEARCHING")
    cause = _append(
        records,
        _common(
            records,
            "automarkov.validation-failed.v1",
            "ValidationFailed",
        )
        | {
            "subject": _artifact_ref("3"),
            "report": _artifact_ref("4"),
            "validator_id": "validator_terminal",
            "validator_version": "v1",
            "validation_level": "terminal",
            "validation_scope": "packaging",
            "failure_code": "secret_or_license_violation",
        },
    )
    _advance(
        records,
        "FAILED",
        trigger=cause,
        reason_code="secret_or_license_violation",
    )

    with pytest.raises(InvalidRunTransitionError):
        project_records(records)


def test_replay_at_verified_head_is_deterministic_and_never_defaults_to_latest() -> (
    None
):
    records = _records_at_state("TEXT_REVIEWED")
    verified_head = EventHead(
        run_id=_RUN_ID,
        sequence_no=records[-1].event.sequence_no,
        event_hash=records[-1].event_hash,
    )
    _advance(records, "WAITING_TEXT_CONFIRMATION")

    root_head = EventHead(
        run_id=_RUN_ID,
        sequence_no=0,
        event_hash=records[0].event_hash,
    )
    assert project_records(records, as_of_head=root_head).state is RunState.RECEIVED

    first = project_records(records, as_of_head=verified_head)
    second = project_records(deepcopy(records), as_of_head=verified_head)
    assert first == second
    assert first.state is RunState.TEXT_REVIEWED
    assert first.event_head == verified_head

    with pytest.raises(RunProjectionHeadError):
        project_records(
            records,
            as_of_head=EventHead(
                run_id=_RUN_ID,
                sequence_no=verified_head.sequence_no,
                event_hash=_HASH_B,
            ),
        )


class _DictSubclass(dict[str, object]):
    pass


class _ForgedEvent(BaseModel):
    event_type: str


def test_public_event_ingress_accepts_only_exact_raw_dicts() -> None:
    root = _root_record()
    raw_event = cast(dict[str, object], root.event.model_dump(mode="json"))
    assert parse_event_record(encode_event_record(deepcopy(raw_event))) == root

    for forbidden in (
        _DictSubclass(raw_event),
        _ForgedEvent.model_construct(event_type="RunCreated"),
        raw_event | {"unexpected": "not closed"},
    ):
        with pytest.raises(EventSchemaError):
            encode_event_record(forbidden)
