from __future__ import annotations

import base64
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import TypeAdapter

from automarkov.domain.canonical import canonical_json_bytes
from automarkov.domain.errors import EventSchemaError, InvalidRunTransitionError
from automarkov.lifecycle import (
    RUN_PROJECTOR_HASH,
    RUN_PROJECTOR_VERSION,
    ZERO_EVENT_HASH,
    AppendRunEvent,
    AppendRunEventsCommand,
    ClarificationChildRunCreated,
    CommitTerminalCommand,
    CreateClarificationChildRunCommand,
    CreateReplacementRunCommand,
    CrossRunLifecycleCommitReceipt,
    EventAuthenticator,
    EventSigningKey,
    ExecutionAttestation,
    LifecycleCommitResult,
    ReplacementRunCreated,
    RunSuperseded,
    TerminalCauseEvent,
    default_event_schema_registry,
    encode_event_record,
    event_signature_preimage,
    parse_event_bytes,
    project_records,
    validate_lifecycle_command,
)

_ISSUED_AT = "2026-08-10T00:00:00Z"
_SIGNATURE = "A" * 86
_NONCE = "A" * 22


def _artifact(marker: str) -> dict[str, str]:
    return {
        "artifact_id": "artifact_" + marker * 64,
        "payload_hash": "sha256:" + marker * 64,
    }


def _runtime_superseded() -> dict[str, object]:
    return {
        "schema_version": "automarkov.run-superseded.v1",
        "event_type": "RunSuperseded",
        "signing_domain": "AutoMarkov-Run-Superseded-v1",
        "event_id": "019fe8f8-1400-7000-8000-000000000101",
        "experiment_id": "experiment_cross_run",
        "run_id": "run_parent",
        "sequence_no": 8,
        "previous_event_hash": "sha256:" + "1" * 64,
        "supersession_cause": "runtime_identity_replacement",
        "child_run_id": "run_child",
        "replacement_ordinal": 1,
        "old_run_manifest_artifact_id": _artifact("2")["artifact_id"],
        "old_run_manifest_payload_hash": _artifact("2")["payload_hash"],
        "child_run_manifest_artifact_id": _artifact("3")["artifact_id"],
        "child_run_manifest_payload_hash": _artifact("3")["payload_hash"],
        "replacement_policy_artifact_id": _artifact("4")["artifact_id"],
        "replacement_policy_payload_hash": _artifact("4")["payload_hash"],
        "replacement_eligibility": "confirmatory_slot_reused",
        "replacement_authority_principal_id": "principal_replacement_authority",
        "reason_code": "runtime_identity_replacement",
        "issued_at": _ISSUED_AT,
        "nonce_b64url": _NONCE,
        "signing_key_id": "key_replacement_authority",
        "signature_b64url": _SIGNATURE,
        "failed_waiting_event_id": "019fe8f8-1400-7000-8000-000000000100",
        "failed_readiness_gate_id": "gate_runtime_readiness",
        "old_dependency_identity_hash": "sha256:" + "5" * 64,
        "new_dependency_identity_hash": "sha256:" + "6" * 64,
    }


def _run_created() -> dict[str, object]:
    return {
        "schema_version": "automarkov.run-created.v1",
        "event_type": "RunCreated",
        "signing_domain": "AutoMarkov-Run-Created-v1",
        "event_id": "019fe8f8-1400-7000-8000-000000000001",
        "experiment_id": "experiment_cross_run",
        "run_id": "run_parent",
        "actor_principal_id": "principal_replacement_authority",
        "issued_at": _ISSUED_AT,
        "sequence_no": 0,
        "previous_event_hash": ZERO_EVENT_HASH,
        "run_manifest_artifact_id": _artifact("2")["artifact_id"],
        "run_manifest_payload_hash": _artifact("2")["payload_hash"],
        "initial_state": "RECEIVED",
        "creation_principal_id": "principal_replacement_authority",
        "reason_code": "run_created",
        "nonce_b64url": "A" * 21 + "A",
        "signing_key_id": "key_replacement_authority",
        "signature_algorithm": "Ed25519",
        "signature_b64url": _SIGNATURE,
    }


def _approval_superseded(previous_hash: str) -> dict[str, object]:
    event = _runtime_superseded()
    for field in (
        "failed_waiting_event_id",
        "failed_readiness_gate_id",
        "old_dependency_identity_hash",
        "new_dependency_identity_hash",
    ):
        del event[field]
    event.update(
        {
            "event_id": "019fe8f8-1400-7000-8000-000000000105",
            "sequence_no": 1,
            "previous_event_hash": previous_hash,
            "supersession_cause": "approval_revocation",
            "reason_code": "approval_revocation",
            "replacement_eligibility": "slot_terminal_failure",
            "revocation_event_id": "019fe8f8-1400-7000-8000-000000000106",
            "revoked_approval_event_id": ("019fe8f8-1400-7000-8000-000000000107"),
            "artifact_id": _artifact("6")["artifact_id"],
            "artifact_payload_hash": _artifact("6")["payload_hash"],
        }
    )
    return event


def _replacement_created() -> dict[str, object]:
    return {
        "schema_version": "automarkov.replacement-run-created.v1",
        "event_type": "ReplacementRunCreated",
        "signing_domain": "AutoMarkov-Replacement-Run-Created-v1",
        "event_id": "019fe8f8-1400-7000-8000-000000000102",
        "experiment_id": "experiment_cross_run",
        "run_id": "run_child",
        "sequence_no": 0,
        "previous_event_hash": ZERO_EVENT_HASH,
        "run_manifest_artifact_id": _artifact("3")["artifact_id"],
        "run_manifest_payload_hash": _artifact("3")["payload_hash"],
        "parent_run_id": "run_parent",
        "parent_run_superseded_event_id": ("019fe8f8-1400-7000-8000-000000000101"),
        "supersession_cause": "runtime_identity_replacement",
        "replacement_ordinal": 1,
        "replacement_policy_artifact_id": _artifact("4")["artifact_id"],
        "replacement_policy_payload_hash": _artifact("4")["payload_hash"],
        "replacement_authority_principal_id": "principal_replacement_authority",
        "issued_at": _ISSUED_AT,
        "nonce_b64url": "A" * 21 + "Q",
        "signing_key_id": "key_replacement_authority",
        "signature_b64url": _SIGNATURE,
    }


def _clarification_created() -> dict[str, object]:
    return {
        "schema_version": "automarkov.clarification-child-run-created.v1",
        "event_type": "ClarificationChildRunCreated",
        "signing_domain": "AutoMarkov-Clarification-Child-Run-Created-v1",
        "event_id": "019fe8f8-1400-7000-8000-000000000103",
        "experiment_id": "experiment_cross_run",
        "run_id": "run_clarification_child",
        "sequence_no": 0,
        "previous_event_hash": ZERO_EVENT_HASH,
        "run_manifest_artifact_id": _artifact("7")["artifact_id"],
        "run_manifest_payload_hash": _artifact("7")["payload_hash"],
        "parent_run_id": "run_clarification_parent",
        "parent_clarification_result_artifact_id": _artifact("8")["artifact_id"],
        "parent_clarification_result_payload_hash": _artifact("8")["payload_hash"],
        "parent_terminal_result_artifact_id": _artifact("9")["artifact_id"],
        "parent_terminal_result_payload_hash": _artifact("9")["payload_hash"],
        "parent_terminal_snapshot_event_head_hash": "sha256:" + "a" * 64,
        "signed_answer_bundle_artifact_id": _artifact("b")["artifact_id"],
        "signed_answer_bundle_payload_hash": _artifact("b")["payload_hash"],
        "continuation_policy_artifact_id": _artifact("c")["artifact_id"],
        "continuation_policy_payload_hash": _artifact("c")["payload_hash"],
        "clarification_continuation_ordinal": 1,
        "continuation_authority_principal_id": "principal_continuation_authority",
        "reason_code": "clarification_answer_received",
        "issued_at": _ISSUED_AT,
        "nonce_b64url": "A" * 21 + "g",
        "signing_key_id": "key_continuation_authority",
        "signature_b64url": _SIGNATURE,
    }


def test_registry_decodes_closed_signed_cross_run_events() -> None:
    registry = default_event_schema_registry()

    superseded = registry.decode(_runtime_superseded())
    replacement = registry.decode(_replacement_created())
    clarification = registry.decode(_clarification_created())

    assert isinstance(superseded, RunSuperseded)
    assert isinstance(replacement, ReplacementRunCreated)
    assert isinstance(clarification, ClarificationChildRunCreated)
    assert superseded.new_dependency_identity_hash != (
        superseded.old_dependency_identity_hash
    )
    expected_preimage = deepcopy(_runtime_superseded())
    del expected_preimage["signature_b64url"]
    assert event_signature_preimage(superseded) == canonical_json_bytes(
        expected_preimage
    )

    mixed_branch = _runtime_superseded()
    mixed_branch["revocation_event_id"] = "019fe8f8-1400-7000-8000-000000000104"
    with pytest.raises(EventSchemaError):
        registry.decode(mixed_branch)


def _execution_attestation() -> dict[str, object]:
    return {
        "schema_version": "automarkov.execution-attestation.v1",
        "signing_domain": "AutoMarkov-Execution-Attestation-v1",
        "experiment_id": "experiment_cross_run",
        "run_id": "run_parent",
        "job_id": "job_replacement_control",
        "process_execution_id": "execution_replacement_control",
        "profile_id": "profile_runner_control",
        "principal_id": "principal_replacement_authority",
        "job_manifest": _artifact("d"),
        "process_terminal_record": _artifact("e"),
        "payload_outputs": [_artifact("f")],
        "terminal_result": _artifact("1"),
        "network_policy_hash": "sha256:" + "2" * 64,
        "mount_table_hash": "sha256:" + "3" * 64,
        "capability_decision_log_hash": "sha256:" + "4" * 64,
        "actual_phase_transition": {
            "from_phase": "replacement_control",
            "to_phase": "committed",
            "transitioned_at": _ISSUED_AT,
        },
        "egress_decision_log_hash": "sha256:" + "5" * 64,
        "egress_revoked_at": _ISSUED_AT,
        "issued_at": _ISSUED_AT,
        "nonce_b64url": "A" * 21 + "w",
        "signing_key_id": "key_runner_control",
        "signature_algorithm": "Ed25519",
        "signature_b64url": _SIGNATURE,
    }


def test_execution_attestation_is_strict_and_output_bound() -> None:
    attestation = ExecutionAttestation.model_validate(
        _execution_attestation(),
        strict=True,
    )

    assert attestation.terminal_result is not None
    assert attestation.terminal_result.artifact_id == _artifact("1")["artifact_id"]

    duplicate_output = _execution_attestation()
    duplicate_output["payload_outputs"] = [_artifact("f"), _artifact("f")]
    with pytest.raises(ValueError, match="sorted and unique"):
        ExecutionAttestation.model_validate(duplicate_output, strict=True)

    extra_field = _execution_attestation()
    extra_field["secret_locator"] = "forbidden"
    with pytest.raises(ValueError):
        ExecutionAttestation.model_validate(extra_field, strict=True)

    noncanonical_signature = _execution_attestation()
    noncanonical_signature["signature_b64url"] = "A" * 85 + "B"
    with pytest.raises(ValueError, match="canonical Ed25519"):
        ExecutionAttestation.model_validate(noncanonical_signature, strict=True)


def _process_terminal_record() -> dict[str, object]:
    return {
        "schema_version": "automarkov.process-execution-terminal-record.v1",
        "signing_domain": "AutoMarkov-ProcessExecutionTerminalRecord-v1",
        "experiment_id": "experiment_cross_run",
        "run_id": "run_parent",
        "job_id": "job_replacement_control",
        "process_execution_id": "execution_replacement_control",
        "profile_id": "profile_runner_control",
        "principal_id": "principal_replacement_authority",
        "job_manifest": _artifact("d"),
        "status": "success",
        "exit_code": 0,
        "reason_code": "replacement_control_succeeded",
        "started_at": _ISSUED_AT,
        "finished_at": _ISSUED_AT,
        "stdout_hash": "sha256:" + "6" * 64,
        "stderr_hash": "sha256:" + "7" * 64,
        "payload_outputs": [_artifact("f")],
        "resource_usage": _artifact("8"),
        "network_log_hash": "sha256:" + "2" * 64,
        "mount_attestation_hash": "sha256:" + "3" * 64,
        "capability_decision_hash": "sha256:" + "4" * 64,
        "egress_log_hash": "sha256:" + "5" * 64,
        "created_at": _ISSUED_AT,
    }


def _parent_terminal_transition() -> dict[str, object]:
    superseded_record = parse_event_bytes(encode_event_record(_runtime_superseded()))
    return {
        "schema_version": "automarkov.state-transitioned.v1",
        "event_type": "StateTransitioned",
        "event_id": "019fe8f8-1400-7000-8000-000000000104",
        "experiment_id": "experiment_cross_run",
        "run_id": "run_parent",
        "actor_principal_id": "principal_replacement_authority",
        "issued_at": _ISSUED_AT,
        "sequence_no": 9,
        "previous_event_hash": superseded_record.event_hash,
        "actor_process_execution_id": "execution_replacement_control",
        "from_state": "WAITING_RUNTIME",
        "to_state": "CANCELLED",
        "trigger_event_id": _runtime_superseded()["event_id"],
        "trigger_event_hash": superseded_record.event_hash,
        "input_artifact_ids": [],
        "gate_report_artifact_id": None,
        "gate_report_payload_hash": None,
        "budget_snapshot_artifact_id": _artifact("9")["artifact_id"],
        "budget_snapshot_payload_hash": _artifact("9")["payload_hash"],
        "reason_code": "run_superseded",
    }


def _replacement_command() -> dict[str, object]:
    return {
        "schema_version": "automarkov.lifecycle-command.v1",
        "command_type": "create_replacement_run",
        "command_id": "019fe8f8-1400-7000-8000-000000000110",
        "actor_principal_id": "principal_replacement_authority",
        "issued_at": _ISSUED_AT,
        "idempotency_key": "replacement-command-1",
        "parent_run_id": "run_parent",
        "child_run_id": "run_child",
        "expected_parent_state": "WAITING_RUNTIME",
        "expected_parent_head": {
            "run_id": "run_parent",
            "sequence_no": 7,
            "event_hash": "sha256:" + "1" * 64,
        },
        "expected_child_head": None,
        "old_run_manifest": _artifact("2"),
        "child_run_manifest": _artifact("3"),
        "replacement_policy": _artifact("4"),
        "cause_prerequisite": {
            "prerequisite_type": "runtime_identity_replacement",
            "failed_waiting_event": {
                "event_id": "019fe8f8-1400-7000-8000-000000000100",
                "sequence_no": 6,
                "event_hash": "sha256:" + "e" * 64,
            },
            "failed_readiness_gate_id": "gate_runtime_readiness",
            "old_dependency_identity_hash": "sha256:" + "5" * 64,
            "new_dependency_identity_hash": "sha256:" + "6" * 64,
        },
        "slot_decision": _artifact("a"),
        "replacement_eligibility": "confirmatory_slot_reused",
        "fixed_commit_job_manifest": _artifact("d"),
        "process_terminal_record": _process_terminal_record(),
        "run_superseded_event": _runtime_superseded(),
        "parent_terminal_transition": _parent_terminal_transition(),
        "replacement_run_created_event": _replacement_created(),
        "execution_attestation": _execution_attestation(),
        "projector_version": RUN_PROJECTOR_VERSION,
        "projector_hash": RUN_PROJECTOR_HASH,
    }


def test_replacement_command_binds_parent_terminal_and_child_bootstrap() -> None:
    command = validate_lifecycle_command(_replacement_command())

    assert isinstance(command, CreateReplacementRunCommand)
    assert command.schema_version == "automarkov.lifecycle-command.v1"
    assert command.execution_attestation.terminal_result is not None

    mismatched_identity = _replacement_command()
    prerequisite = mismatched_identity["cause_prerequisite"]
    assert isinstance(prerequisite, dict)
    prerequisite["new_dependency_identity_hash"] = "sha256:" + "5" * 64
    with pytest.raises(EventSchemaError):
        validate_lifecycle_command(mismatched_identity)

    missing_terminal_result = _replacement_command()
    attestation = missing_terminal_result["execution_attestation"]
    assert isinstance(attestation, dict)
    attestation["terminal_result"] = None
    with pytest.raises(EventSchemaError):
        validate_lifecycle_command(missing_terminal_result)


def _approval_replacement_command() -> dict[str, object]:
    command = _replacement_command()
    superseded = _approval_superseded("sha256:" + "1" * 64)
    superseded["sequence_no"] = 8
    child_created = _replacement_created()
    child_created["parent_run_superseded_event_id"] = superseded["event_id"]
    child_created["supersession_cause"] = "approval_revocation"
    transition = _parent_terminal_transition()
    superseded_record = parse_event_bytes(encode_event_record(superseded))
    transition.update(
        {
            "previous_event_hash": superseded_record.event_hash,
            "from_state": "FORMAL_LOCKED",
            "trigger_event_id": superseded["event_id"],
            "trigger_event_hash": superseded_record.event_hash,
        }
    )
    command.update(
        {
            "expected_parent_state": "FORMAL_LOCKED",
            "cause_prerequisite": {
                "prerequisite_type": "approval_revocation",
                "revocation_event": {
                    "event_id": superseded["revocation_event_id"],
                    "sequence_no": 7,
                    "event_hash": "sha256:" + "1" * 64,
                },
                "revoked_approval_event": {
                    "event_id": superseded["revoked_approval_event_id"],
                    "sequence_no": 6,
                    "event_hash": "sha256:" + "2" * 64,
                },
                "artifact": _artifact("6"),
            },
            "replacement_eligibility": "slot_terminal_failure",
            "run_superseded_event": superseded,
            "parent_terminal_transition": transition,
            "replacement_run_created_event": child_created,
        }
    )
    return command


def test_approval_replacement_uses_its_closed_prerequisite_branch() -> None:
    command = validate_lifecycle_command(_approval_replacement_command())

    assert isinstance(command, CreateReplacementRunCommand)
    assert command.cause_prerequisite.prerequisite_type == "approval_revocation"

    mixed_prerequisite = _approval_replacement_command()
    prerequisite = mixed_prerequisite["cause_prerequisite"]
    assert isinstance(prerequisite, dict)
    prerequisite["old_dependency_identity_hash"] = "sha256:" + "5" * 64
    with pytest.raises(EventSchemaError):
        validate_lifecycle_command(mixed_prerequisite)


def _clarification_command() -> dict[str, object]:
    return {
        "schema_version": "automarkov.lifecycle-command.v1",
        "command_type": "create_clarification_child_run",
        "command_id": "019fe8f8-1400-7000-8000-000000000120",
        "actor_principal_id": "principal_continuation_authority",
        "issued_at": _ISSUED_AT,
        "idempotency_key": "clarification-command-1",
        "parent_run_id": "run_clarification_parent",
        "child_run_id": "run_clarification_child",
        "expected_parent_head": {
            "run_id": "run_clarification_parent",
            "sequence_no": 20,
            "event_hash": "sha256:" + "a" * 64,
        },
        "expected_child_head": None,
        "parent_clarification_result": _artifact("8"),
        "parent_terminal_result": _artifact("9"),
        "parent_terminal_snapshot_event_head": {
            "run_id": "run_clarification_parent",
            "sequence_no": 20,
            "event_hash": "sha256:" + "a" * 64,
        },
        "signed_answer_bundle": _artifact("b"),
        "continuation_policy": _artifact("c"),
        "child_run_manifest": _artifact("7"),
        "clarification_child_run_created_event": _clarification_created(),
    }


def test_clarification_command_preserves_parent_and_bootstraps_one_child() -> None:
    command = validate_lifecycle_command(_clarification_command())

    assert isinstance(command, CreateClarificationChildRunCommand)
    assert command.parent_terminal_snapshot_event_head == command.expected_parent_head

    command_models = (
        AppendRunEventsCommand,
        CommitTerminalCommand,
        CreateReplacementRunCommand,
        CreateClarificationChildRunCommand,
    )
    assert {
        model.model_json_schema()["properties"]["schema_version"]["const"]
        for model in command_models
    } == {"automarkov.lifecycle-command.v1"}

    mismatched_snapshot = _clarification_command()
    event = mismatched_snapshot["clarification_child_run_created_event"]
    assert isinstance(event, dict)
    event["parent_terminal_snapshot_event_head_hash"] = "sha256:" + "d" * 64
    with pytest.raises(EventSchemaError):
        validate_lifecycle_command(mismatched_snapshot)


def test_authenticator_verifies_every_signed_cross_run_event() -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(b"\x42" * 32)
    registry = default_event_schema_registry()
    cases = (
        (_runtime_superseded(), "new_dependency_identity_hash"),
        (_replacement_created(), "replacement_ordinal"),
        (_clarification_created(), "clarification_continuation_ordinal"),
    )

    for raw, tampered_field in cases:
        unsigned = registry.decode(raw)
        assert isinstance(
            unsigned,
            (RunSuperseded, ReplacementRunCreated, ClarificationChildRunCreated),
        )
        signed_raw = deepcopy(raw)
        signed_raw["signature_b64url"] = (
            base64.urlsafe_b64encode(
                private_key.sign(event_signature_preimage(unsigned))
            )
            .decode()
            .rstrip("=")
        )
        signed = registry.decode(signed_raw)
        assert isinstance(
            signed,
            (RunSuperseded, ReplacementRunCreated, ClarificationChildRunCreated),
        )
        if isinstance(signed, ClarificationChildRunCreated):
            principal_id = signed.continuation_authority_principal_id
        else:
            principal_id = signed.replacement_authority_principal_id
        authenticator = EventAuthenticator(
            (
                EventSigningKey(
                    signing_key_id=signed.signing_key_id,
                    principal_id=principal_id,
                    run_id=signed.run_id,
                    public_key_bytes=private_key.public_key().public_bytes_raw(),
                    not_before="2026-08-09T00:00:00Z",
                    not_after="2026-08-11T00:00:00Z",
                ),
            )
        )
        authenticator.authenticate(signed)

        tampered = deepcopy(signed_raw)
        tampered[tampered_field] = (
            "sha256:" + "7" * 64
            if tampered_field == "new_dependency_identity_hash"
            else 2
        )
        with pytest.raises(EventSchemaError):
            authenticator.authenticate(registry.decode(tampered))


def _event_record(raw: dict[str, object]) -> dict[str, object]:
    return parse_event_bytes(encode_event_record(raw)).model_dump(
        mode="json",
        round_trip=True,
        warnings="error",
    )


def _replacement_receipt() -> dict[str, object]:
    superseded_record = _event_record(_runtime_superseded())
    transition_record = _event_record(_parent_terminal_transition())
    child_record = _event_record(_replacement_created())
    parent_after_head = {
        "run_id": "run_parent",
        "sequence_no": 9,
        "event_hash": transition_record["event_hash"],
    }
    child_after_head = {
        "run_id": "run_child",
        "sequence_no": 0,
        "event_hash": child_record["event_hash"],
    }
    process_reference = _artifact("e")
    terminal_reference = _artifact("1")
    audit_reference = _artifact("2")
    attestation_reference = _artifact("3")
    return {
        "schema_version": "automarkov.cross-run-lifecycle-commit-receipt.v1",
        "command_type": "create_replacement_run",
        "command_id": "019fe8f8-1400-7000-8000-000000000110",
        "idempotency_key": "replacement-command-1",
        "command_fingerprint": "sha256:" + "f" * 64,
        "parent_run_id": "run_parent",
        "child_run_id": "run_child",
        "parent_before_head": {
            "run_id": "run_parent",
            "sequence_no": 7,
            "event_hash": "sha256:" + "1" * 64,
        },
        "parent_after_head": parent_after_head,
        "child_after_head": child_after_head,
        "parent_event_records": [superseded_record, transition_record],
        "child_event_records": [child_record],
        "artifact_references": [
            terminal_reference,
            audit_reference,
            attestation_reference,
            process_reference,
        ],
        "parent_run_view": {
            "schema_version": "automarkov.run-view.v2",
            "run_id": "run_parent",
            "experiment_id": "experiment_cross_run",
            "projector_version": RUN_PROJECTOR_VERSION,
            "projector_hash": RUN_PROJECTOR_HASH,
            "state": "CANCELLED",
            "event_head": parent_after_head,
            "budget_snapshot": _artifact("9"),
            "waiting": None,
            "current_approval_snapshots": [],
            "validation_levels": [],
            "post_terminal_audit_event_references": [],
            "terminal_event": {
                "event_id": "019fe8f8-1400-7000-8000-000000000104",
                "sequence_no": 9,
                "event_hash": transition_record["event_hash"],
            },
            "terminal_snapshot_head": parent_after_head,
            "terminal_result": terminal_reference,
            "run_audit_projection": audit_reference,
        },
        "child_run_view": {
            "schema_version": "automarkov.run-view.v2",
            "run_id": "run_child",
            "experiment_id": "experiment_cross_run",
            "projector_version": RUN_PROJECTOR_VERSION,
            "projector_hash": RUN_PROJECTOR_HASH,
            "state": "RECEIVED",
            "event_head": child_after_head,
            "budget_snapshot": None,
            "waiting": None,
            "current_approval_snapshots": [],
            "validation_levels": [],
            "post_terminal_audit_event_references": [],
            "terminal_event": None,
            "terminal_snapshot_head": None,
            "terminal_result": None,
            "run_audit_projection": None,
        },
        "process_execution_terminal_record": process_reference,
        "terminal_result": terminal_reference,
        "run_audit_projection": audit_reference,
        "execution_attestation": attestation_reference,
    }


def test_cross_run_receipt_closes_replacement_cardinality_and_result_union() -> None:
    raw = _replacement_receipt()
    receipt = CrossRunLifecycleCommitReceipt.model_validate_json(
        canonical_json_bytes(raw)
    )

    assert receipt.parent_after_head == receipt.parent_run_view.event_head
    assert receipt.child_after_head == receipt.child_run_view.event_head
    assert isinstance(
        TypeAdapter(LifecycleCommitResult).validate_json(canonical_json_bytes(raw)),
        CrossRunLifecycleCommitReceipt,
    )

    missing_parent_record = _replacement_receipt()
    parent_records = missing_parent_record["parent_event_records"]
    assert isinstance(parent_records, list)
    missing_parent_record["parent_event_records"] = parent_records[:1]
    with pytest.raises(ValueError):
        CrossRunLifecycleCommitReceipt.model_validate_json(
            canonical_json_bytes(missing_parent_record)
        )

    extra_artifact = _replacement_receipt()
    references = extra_artifact["artifact_references"]
    assert isinstance(references, list)
    references.append(_artifact("4"))
    with pytest.raises(ValueError):
        CrossRunLifecycleCommitReceipt.model_validate_json(
            canonical_json_bytes(extra_artifact)
        )


def _clarification_receipt() -> dict[str, object]:
    child_record = _event_record(_clarification_created())
    parent_head = {
        "run_id": "run_clarification_parent",
        "sequence_no": 20,
        "event_hash": "sha256:" + "a" * 64,
    }
    child_head = {
        "run_id": "run_clarification_child",
        "sequence_no": 0,
        "event_hash": child_record["event_hash"],
    }
    return {
        "schema_version": "automarkov.cross-run-lifecycle-commit-receipt.v1",
        "command_type": "create_clarification_child_run",
        "command_id": "019fe8f8-1400-7000-8000-000000000120",
        "idempotency_key": "clarification-command-1",
        "command_fingerprint": "sha256:" + "e" * 64,
        "parent_run_id": "run_clarification_parent",
        "child_run_id": "run_clarification_child",
        "parent_before_head": parent_head,
        "parent_after_head": parent_head,
        "child_after_head": child_head,
        "parent_event_records": [],
        "child_event_records": [child_record],
        "artifact_references": [],
        "parent_run_view": {
            "schema_version": "automarkov.run-view.v2",
            "run_id": "run_clarification_parent",
            "experiment_id": "experiment_cross_run",
            "projector_version": RUN_PROJECTOR_VERSION,
            "projector_hash": RUN_PROJECTOR_HASH,
            "state": "CLARIFICATION_REQUIRED",
            "event_head": parent_head,
            "budget_snapshot": _artifact("9"),
            "waiting": None,
            "current_approval_snapshots": [],
            "validation_levels": [],
            "post_terminal_audit_event_references": [],
            "terminal_event": {
                "event_id": "019fe8f8-1400-7000-8000-000000000090",
                "sequence_no": 20,
                "event_hash": "sha256:" + "a" * 64,
            },
            "terminal_snapshot_head": parent_head,
            "terminal_result": _artifact("9"),
            "run_audit_projection": _artifact("8"),
        },
        "child_run_view": {
            "schema_version": "automarkov.run-view.v2",
            "run_id": "run_clarification_child",
            "experiment_id": "experiment_cross_run",
            "projector_version": RUN_PROJECTOR_VERSION,
            "projector_hash": RUN_PROJECTOR_HASH,
            "state": "RECEIVED",
            "event_head": child_head,
            "budget_snapshot": None,
            "waiting": None,
            "current_approval_snapshots": [],
            "validation_levels": [],
            "post_terminal_audit_event_references": [],
            "terminal_event": None,
            "terminal_snapshot_head": None,
            "terminal_result": None,
            "run_audit_projection": None,
        },
        "process_execution_terminal_record": None,
        "terminal_result": None,
        "run_audit_projection": None,
        "execution_attestation": None,
    }


def test_cross_run_receipt_preserves_clarification_parent_head() -> None:
    receipt = CrossRunLifecycleCommitReceipt.model_validate_json(
        canonical_json_bytes(_clarification_receipt())
    )

    assert receipt.parent_before_head == receipt.parent_after_head
    assert receipt.parent_event_records == ()
    assert receipt.artifact_references == ()

    mutated_parent = _clarification_receipt()
    mutated_parent["parent_after_head"] = {
        "run_id": "run_clarification_parent",
        "sequence_no": 21,
        "event_hash": "sha256:" + "b" * 64,
    }
    with pytest.raises(ValueError):
        CrossRunLifecycleCommitReceipt.model_validate_json(
            canonical_json_bytes(mutated_parent)
        )


def test_child_bootstrap_events_project_without_widening_normal_append() -> None:
    for raw in (_replacement_created(), _clarification_created()):
        record = parse_event_bytes(encode_event_record(raw))
        projection = project_records((record,))

        assert projection.state.value == "RECEIVED"
        assert projection.event_head.sequence_no == 0
        with pytest.raises(ValueError):
            TypeAdapter(AppendRunEvent).validate_python(raw, strict=True)


def test_reducer_replays_only_the_fixed_run_supersession_terminal_pair() -> None:
    root_record = parse_event_bytes(encode_event_record(_run_created()))
    superseded_raw = _approval_superseded(root_record.event_hash)
    superseded_record = parse_event_bytes(encode_event_record(superseded_raw))
    transition_raw = {
        "schema_version": "automarkov.state-transitioned.v1",
        "event_type": "StateTransitioned",
        "event_id": "019fe8f8-1400-7000-8000-000000000108",
        "experiment_id": "experiment_cross_run",
        "run_id": "run_parent",
        "actor_principal_id": "principal_replacement_authority",
        "issued_at": _ISSUED_AT,
        "sequence_no": 2,
        "previous_event_hash": superseded_record.event_hash,
        "actor_process_execution_id": "execution_replacement_control",
        "from_state": "RECEIVED",
        "to_state": "CANCELLED",
        "trigger_event_id": superseded_raw["event_id"],
        "trigger_event_hash": superseded_record.event_hash,
        "input_artifact_ids": [],
        "gate_report_artifact_id": None,
        "gate_report_payload_hash": None,
        "budget_snapshot_artifact_id": _artifact("9")["artifact_id"],
        "budget_snapshot_payload_hash": _artifact("9")["payload_hash"],
        "reason_code": "run_superseded",
    }
    transition_record = parse_event_bytes(encode_event_record(transition_raw))

    projection = project_records((root_record, superseded_record, transition_record))

    assert projection.state.value == "CANCELLED"
    assert projection.terminal_event is not None
    assert projection.terminal_event.event_id == transition_raw["event_id"]
    with pytest.raises(ValueError):
        TypeAdapter(AppendRunEvent).validate_python(superseded_raw, strict=True)
    with pytest.raises(ValueError):
        TypeAdapter(TerminalCauseEvent).validate_python(superseded_raw, strict=True)

    wrong_reason = deepcopy(transition_raw)
    wrong_reason["reason_code"] = "user_cancelled"
    with pytest.raises(InvalidRunTransitionError):
        project_records(
            (
                root_record,
                superseded_record,
                parse_event_bytes(encode_event_record(wrong_reason)),
            )
        )
