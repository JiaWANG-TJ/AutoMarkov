from __future__ import annotations

import base64
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import TypeAdapter, ValidationError

from automarkov.domain.canonical import canonical_json_bytes
from automarkov.domain.errors import EventSchemaError, InvalidRunTransitionError
from automarkov.lifecycle import (
    AppendRunEventsCommand,
    ArtifactSuperseded,
    ClarificationChildRunCreated,
    ClarificationEvaluationRecorded,
    ClarificationEvaluationRequested,
    EventAuthenticator,
    EventRecord,
    EventSigningKey,
    ExecutionTopologySubstituted,
    GateOmittedByDesign,
    OrdinaryAppendEvent,
    PostTerminalEvent,
    ReplacementRunCreated,
    RunCreated,
    RunEvent,
    RunEventSecurityContext,
    RunSuperseded,
    RuntimeReady,
    RuntimeReplacementPrerequisite,
    SignedApprovalEvent,
    SpecificationConflictDetected,
    StageGatePassed,
    default_event_schema_registry,
    encode_event_record,
    event_signature_preimage,
    parse_event_bytes,
    project_records,
)

_ISSUED_AT = "2026-08-10T00:00:00Z"
_RUN_ID = "run_union_closure"
_PREVIOUS_HASH = "sha256:" + "0" * 64


def _artifact(marker: str) -> dict[str, str]:
    return {
        "artifact_id": "artifact_" + marker * 64,
        "payload_hash": "sha256:" + marker * 64,
    }


def _common(
    schema_version: str,
    event_type: str,
    *,
    index: int,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "event_type": event_type,
        "event_id": f"019fe8f8-1400-7000-8000-{index:012x}",
        "experiment_id": "experiment_union_closure",
        "run_id": _RUN_ID,
        "sequence_no": index,
        "previous_event_hash": _PREVIOUS_HASH,
        "actor_principal_id": "principal_union_closure",
        "actor_process_execution_id": "execution_union_closure",
        "issued_at": _ISSUED_AT,
    }


def _artifact_superseded() -> dict[str, object]:
    return _common(
        "automarkov.artifact-superseded.v1",
        "ArtifactSuperseded",
        index=1,
    ) | {
        "old_artifact": _artifact("1"),
        "new_artifact": _artifact("2"),
        "lineage_report": _artifact("3"),
        "supersession_reason_code": "approval_revoked",
    }


def _runtime_ready() -> dict[str, object]:
    return _common(
        "automarkov.runtime-ready.v1",
        "RuntimeReady",
        index=2,
    ) | {
        "dependency_kind": "runtime_profile",
        "dependency_identity_hash": "sha256:" + "4" * 64,
        "profile_id": "profile_union_closure",
        "process_execution_id": None,
        "protocol_edge_id": None,
        "readiness_report": _artifact("5"),
        "passed_gate_id": "gate_runtime_profile_smoke",
    }


def _specification_conflict() -> dict[str, object]:
    return _common(
        "automarkov.specification-conflict-detected.v1",
        "SpecificationConflictDetected",
        index=3,
    ) | {
        "specification": _artifact("6"),
        "first_conflict_locus_id": "specification.section_4_4",
        "first_conflict_locus_hash": "sha256:" + "7" * 64,
        "second_conflict_locus_id": "specification.section_11_11",
        "second_conflict_locus_hash": "sha256:" + "8" * 64,
        "affected_contract_ids": ["contract_lifecycle", "contract_projection"],
        "conflict_code": "cross_section_contract_conflict",
    }


def _terminal_event_reference() -> dict[str, object]:
    return {
        "event_id": "019fe8f8-1400-7000-8000-000000000010",
        "sequence_no": 10,
        "event_hash": "sha256:" + "9" * 64,
    }


def _clarification_requested() -> dict[str, object]:
    return _common(
        "automarkov.clarification-evaluation-requested.v1",
        "ClarificationEvaluationRequested",
        index=4,
    ) | {
        "evaluation_request": _artifact("a"),
        "terminal_result": _artifact("b"),
        "terminal_event": _terminal_event_reference(),
        "terminal_snapshot_event_head_hash": "sha256:" + "9" * 64,
    }


def _clarification_recorded() -> dict[str, object]:
    return _common(
        "automarkov.clarification-evaluation-recorded.v1",
        "ClarificationEvaluationRecorded",
        index=5,
    ) | {
        "evaluation_request": _artifact("a"),
        "evaluation_verdict": _artifact("c"),
        "terminal_result": _artifact("b"),
        "terminal_event": _terminal_event_reference(),
        "terminal_snapshot_event_head_hash": "sha256:" + "9" * 64,
    }


def test_registry_closes_unsigned_lifecycle_and_post_terminal_branches() -> None:
    registry = default_event_schema_registry()
    cases = (
        (_artifact_superseded(), ArtifactSuperseded),
        (_runtime_ready(), RuntimeReady),
        (_specification_conflict(), SpecificationConflictDetected),
        (_clarification_requested(), ClarificationEvaluationRequested),
        (_clarification_recorded(), ClarificationEvaluationRecorded),
    )

    for raw, expected_type in cases:
        assert isinstance(registry.decode(raw), expected_type)

    mismatched_terminal_head = _clarification_recorded()
    mismatched_terminal_head["terminal_snapshot_event_head_hash"] = "sha256:" + "d" * 64
    with pytest.raises(EventSchemaError):
        registry.decode(mismatched_terminal_head)

    extra = deepcopy(_artifact_superseded())
    extra["free_form_data"] = {}
    with pytest.raises(EventSchemaError):
        registry.decode(extra)


def _signed_common(
    schema_version: str,
    event_type: str,
    signing_domain: str,
    *,
    index: int,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "event_type": event_type,
        "signing_domain": signing_domain,
        "event_id": f"019fe8f8-1400-7000-8000-{index:012x}",
        "experiment_id": "experiment_union_closure",
        "run_id": _RUN_ID,
        "sequence_no": index,
        "previous_event_hash": _PREVIOUS_HASH,
        "issued_at": _ISSUED_AT,
        "nonce_b64url": "A" * 22,
        "signing_key_id": "key_fixed_commit_runner",
        "signature_b64url": "A" * 86,
    }


def _gate_omitted(
    gate_id: str,
    method_id: str,
    subject_ids: list[str],
    missing_kinds: list[str],
    output_ids: list[str],
    *,
    index: int,
) -> dict[str, object]:
    return _signed_common(
        "automarkov.gate-omitted-event.v1",
        "GateOmittedByDesign",
        "AutoMarkov-Gate-Omitted-v1",
        index=index,
    ) | {
        "track": "AUTO",
        "variant_id": "v1_canonical",
        "cell_id": "cell_union_closure",
        "ablation_execution_plan_artifact_id": _artifact("d")["artifact_id"],
        "ablation_execution_plan_hash": _artifact("d")["payload_hash"],
        "pair_binding_id": "pair_union_closure",
        "task_card_artifact_id": _artifact("e")["artifact_id"],
        "subject_artifact_ids": subject_ids,
        "expected_missing_artifact_kinds": missing_kinds,
        "output_artifact_ids": output_ids,
        "reason": "controlled_ablation",
        "ablation_method_id": method_id,
        "omitted_gate_id": gate_id,
    }


def _topology_substituted() -> dict[str, object]:
    return _signed_common(
        "automarkov.execution-topology-substituted.v1",
        "ExecutionTopologySubstituted",
        "AutoMarkov-Execution-Topology-Substituted-v1",
        index=11,
    ) | {
        "ablation_execution_plan_artifact_id": _artifact("d")["artifact_id"],
        "ablation_execution_plan_hash": _artifact("d")["payload_hash"],
        "ablation_method_id": "automarkov_single_agent_workflow",
        "cell_id": "cell_union_closure",
        "from_topology": "multi_role",
        "to_topology": "single_qwen_sequential",
        "role_order": ["researcher", "text_agent", "formal_agent"],
        "prompt_hashes": [
            "sha256:" + "1" * 64,
            "sha256:" + "2" * 64,
            "sha256:" + "3" * 64,
        ],
        "model_identity_hash": "sha256:" + "4" * 64,
    }


def test_registry_closes_five_gate_omissions_and_topology_substitution() -> None:
    registry = default_event_schema_registry()
    artifact_id = _artifact("1")["artifact_id"]
    cases = (
        _gate_omitted(
            "EVIDENCE_LEDGER_CLOSURE",
            "automarkov_no_evidence",
            [],
            ["EvidenceLedger"],
            [_artifact("2")["artifact_id"]],
            index=6,
        ),
        _gate_omitted(
            "TEXT_CRITIC_REVIEW",
            "automarkov_no_text_critic",
            [artifact_id],
            ["TextCriticReport"],
            [],
            index=7,
        ),
        _gate_omitted(
            "FORMAL_CRITIC_REVIEW",
            "automarkov_no_formal_critic",
            [artifact_id],
            ["FormalCriticReport"],
            [],
            index=8,
        ),
        _gate_omitted(
            "PUBLIC_SIMULATION_TESTER",
            "automarkov_no_simulation_tester",
            [artifact_id],
            [
                "PropertyTestReport",
                "MetamorphicTestReport",
                "DifferentialTestReport",
                "TrajectoryTestReport",
            ],
            [],
            index=9,
        ),
        _gate_omitted(
            "PUBLIC_DEV_LEARNING_PROBE_AND_ROLLBACK",
            "automarkov_no_training_feedback",
            [artifact_id],
            ["PublicDevLearningProbeReport"],
            [],
            index=10,
        ),
    )

    for raw in cases:
        assert isinstance(registry.decode(raw), GateOmittedByDesign)
    topology = registry.decode(_topology_substituted())
    assert isinstance(topology, ExecutionTopologySubstituted)

    expected_preimage = deepcopy(cases[0])
    del expected_preimage["signature_b64url"]
    gate = registry.decode(cases[0])
    assert isinstance(gate, GateOmittedByDesign)
    assert event_signature_preimage(gate) == canonical_json_bytes(expected_preimage)

    mismatched_method = deepcopy(cases[1])
    mismatched_method["ablation_method_id"] = "automarkov_no_formal_critic"
    with pytest.raises(EventSchemaError):
        registry.decode(mismatched_method)


def _sign_event(
    raw: dict[str, object],
    private_key: Ed25519PrivateKey,
) -> RunEvent:
    unsigned = default_event_schema_registry().decode(raw)
    assert isinstance(
        unsigned,
        (
            RunCreated,
            SignedApprovalEvent,
            RunSuperseded,
            ReplacementRunCreated,
            ClarificationChildRunCreated,
            GateOmittedByDesign,
            ExecutionTopologySubstituted,
        ),
    )
    signed = deepcopy(raw)
    signed["signature_b64url"] = (
        base64.urlsafe_b64encode(private_key.sign(event_signature_preimage(unsigned)))
        .decode()
        .rstrip("=")
    )
    return default_event_schema_registry().decode(signed)


def _record(raw: dict[str, object]) -> EventRecord:
    return parse_event_bytes(encode_event_record(raw))


def _signing_key(
    private_key: Ed25519PrivateKey,
    *,
    run_id: str = _RUN_ID,
) -> EventSigningKey:
    return EventSigningKey(
        signing_key_id="key_fixed_commit_runner",
        principal_id="principal_union_closure",
        run_id=run_id,
        public_key_bytes=private_key.public_key().public_bytes_raw(),
        not_before="2026-08-09T00:00:00Z",
        not_after="2026-08-11T00:00:00Z",
    )


def test_signed_audit_events_are_authenticated_and_union_membership_is_closed() -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(b"\x51" * 32)
    signed_gate = _sign_event(
        _gate_omitted(
            "TEXT_CRITIC_REVIEW",
            "automarkov_no_text_critic",
            [_artifact("1")["artifact_id"]],
            ["TextCriticReport"],
            [],
            index=12,
        ),
        private_key,
    )
    signed_topology = _sign_event(_topology_substituted(), private_key)
    authenticator = EventAuthenticator((_signing_key(private_key),))

    authenticator.authenticate(signed_gate)
    authenticator.authenticate(signed_topology)
    manifest_context = RunEventSecurityContext.model_validate(
        _security_context(
            private_key,
            run_id=_RUN_ID,
            root_ordinal=0,
            allowed_event_types=[
                "ExecutionTopologySubstituted",
                "GateOmittedByDesign",
            ],
        ),
        strict=True,
    )
    EventAuthenticator().authenticate(signed_gate, manifest_context)
    EventAuthenticator().authenticate(signed_topology, manifest_context)
    assert (
        TypeAdapter(OrdinaryAppendEvent)
        .validate_python(signed_gate.model_dump(mode="json"), strict=True)
        .event_type
        == "GateOmittedByDesign"
    )
    assert (
        TypeAdapter(OrdinaryAppendEvent)
        .validate_python(_artifact_superseded(), strict=True)
        .event_type
        == "ArtifactSuperseded"
    )
    assert (
        TypeAdapter(PostTerminalEvent)
        .validate_python(_clarification_recorded(), strict=True)
        .event_type
        == "ClarificationEvaluationRecorded"
    )

    with pytest.raises(ValidationError):
        TypeAdapter(PostTerminalEvent).validate_python(_runtime_ready(), strict=True)

    tampered = _topology_substituted()
    tampered["model_identity_hash"] = "sha256:" + "f" * 64
    with pytest.raises(EventSchemaError, match="signature is invalid"):
        authenticator.authenticate(default_event_schema_registry().decode(tampered))


def _security_context(
    private_key: Ed25519PrivateKey,
    *,
    run_id: str,
    root_ordinal: int,
    allowed_event_types: list[str],
) -> dict[str, object]:
    public_key = (
        base64.urlsafe_b64encode(private_key.public_key().public_bytes_raw())
        .decode()
        .rstrip("=")
    )
    return {
        "schema_version": "automarkov.run-event-security-context.v1",
        "run_id": run_id,
        "experiment_id": "experiment_union_closure",
        "root_ordinal": root_ordinal,
        "creation_policy": _artifact("a"),
        "max_clock_skew_ms": 0,
        "actor_capabilities": [
            {
                "principal_id": "principal_union_closure",
                "process_execution_id": None,
                "allowed_event_types": sorted(allowed_event_types),
            }
        ],
        "signing_keys": [
            {
                "signing_key_id": "key_fixed_commit_runner",
                "principal_id": "principal_union_closure",
                "signature_algorithm": "Ed25519",
                "public_key_b64url": public_key,
                "not_before": "2026-08-09T00:00:00Z",
                "not_after": "2026-08-11T00:00:00Z",
                "revoked_at": None,
            }
        ],
        "run_creation": {
            "creation_principal_id": "principal_union_closure",
            "signing_key_id": "key_fixed_commit_runner",
        },
        "approval": {
            "approval_principal_id": "principal_union_closure",
            "approval_principal_kind": "experiment_approval_policy",
            "signing_key_id": "key_fixed_commit_runner",
            "policy_contract": _artifact("b"),
            "policy_source_hash": None,
            "policy_image_hash": None,
            "policy_version": None,
            "revocation_authorities": [],
        },
    }


def _replacement_created(*, ordinal: int = 2) -> dict[str, object]:
    return _signed_common(
        "automarkov.replacement-run-created.v1",
        "ReplacementRunCreated",
        "AutoMarkov-Replacement-Run-Created-v1",
        index=13,
    ) | {
        "run_id": "run_replacement_child",
        "sequence_no": 0,
        "previous_event_hash": _PREVIOUS_HASH,
        "run_manifest_artifact_id": _artifact("1")["artifact_id"],
        "run_manifest_payload_hash": _artifact("1")["payload_hash"],
        "parent_run_id": "run_replacement_parent",
        "parent_run_superseded_event_id": "019fe8f8-1400-7000-8000-000000000012",
        "supersession_cause": "runtime_identity_replacement",
        "replacement_ordinal": ordinal,
        "replacement_policy_artifact_id": _artifact("2")["artifact_id"],
        "replacement_policy_payload_hash": _artifact("2")["payload_hash"],
        "replacement_authority_principal_id": "principal_union_closure",
    }


def _clarification_child_created(*, ordinal: int = 3) -> dict[str, object]:
    return _signed_common(
        "automarkov.clarification-child-run-created.v1",
        "ClarificationChildRunCreated",
        "AutoMarkov-Clarification-Child-Run-Created-v1",
        index=15,
    ) | {
        "run_id": "run_clarification_child",
        "sequence_no": 0,
        "previous_event_hash": _PREVIOUS_HASH,
        "run_manifest_artifact_id": _artifact("4")["artifact_id"],
        "run_manifest_payload_hash": _artifact("4")["payload_hash"],
        "parent_run_id": "run_clarification_parent",
        "parent_clarification_result_artifact_id": _artifact("5")["artifact_id"],
        "parent_clarification_result_payload_hash": _artifact("5")["payload_hash"],
        "parent_terminal_result_artifact_id": _artifact("6")["artifact_id"],
        "parent_terminal_result_payload_hash": _artifact("6")["payload_hash"],
        "parent_terminal_snapshot_event_head_hash": "sha256:" + "7" * 64,
        "signed_answer_bundle_artifact_id": _artifact("8")["artifact_id"],
        "signed_answer_bundle_payload_hash": _artifact("8")["payload_hash"],
        "continuation_policy_artifact_id": _artifact("9")["artifact_id"],
        "continuation_policy_payload_hash": _artifact("9")["payload_hash"],
        "clarification_continuation_ordinal": ordinal,
        "continuation_authority_principal_id": "principal_union_closure",
        "reason_code": "clarification_answer_received",
    }


def test_security_context_binds_root_and_child_ordinals() -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(b"\x52" * 32)
    child = _sign_event(_replacement_created(), private_key)
    child_context = RunEventSecurityContext.model_validate(
        _security_context(
            private_key,
            run_id="run_replacement_child",
            root_ordinal=2,
            allowed_event_types=["ReplacementRunCreated"],
        ),
        strict=True,
    )
    EventAuthenticator().authenticate(child, child_context)

    wrong_ordinal = RunEventSecurityContext.model_validate(
        _security_context(
            private_key,
            run_id="run_replacement_child",
            root_ordinal=1,
            allowed_event_types=["ReplacementRunCreated"],
        ),
        strict=True,
    )
    with pytest.raises(EventSchemaError, match="ordinal"):
        EventAuthenticator().authenticate(child, wrong_ordinal)

    clarification_child = _sign_event(_clarification_child_created(), private_key)
    assert isinstance(clarification_child, ClarificationChildRunCreated)
    clarification_context = RunEventSecurityContext.model_validate(
        _security_context(
            private_key,
            run_id="run_clarification_child",
            root_ordinal=3,
            allowed_event_types=["ClarificationChildRunCreated"],
        ),
        strict=True,
    )
    EventAuthenticator().authenticate(clarification_child, clarification_context)
    wrong_clarification_context = RunEventSecurityContext.model_validate(
        _security_context(
            private_key,
            run_id="run_clarification_child",
            root_ordinal=2,
            allowed_event_types=["ClarificationChildRunCreated"],
        ),
        strict=True,
    )
    with pytest.raises(EventSchemaError, match="ordinal"):
        EventAuthenticator().authenticate(
            clarification_child,
            wrong_clarification_context,
        )

    root_raw = {
        "schema_version": "automarkov.run-created.v1",
        "event_type": "RunCreated",
        "signing_domain": "AutoMarkov-Run-Created-v1",
        "event_id": "019fe8f8-1400-7000-8000-000000000014",
        "experiment_id": "experiment_union_closure",
        "run_id": "run_root",
        "actor_principal_id": "principal_union_closure",
        "issued_at": _ISSUED_AT,
        "sequence_no": 0,
        "previous_event_hash": _PREVIOUS_HASH,
        "run_manifest_artifact_id": _artifact("3")["artifact_id"],
        "run_manifest_payload_hash": _artifact("3")["payload_hash"],
        "initial_state": "RECEIVED",
        "creation_principal_id": "principal_union_closure",
        "reason_code": "run_created",
        "nonce_b64url": "A" * 22,
        "signing_key_id": "key_fixed_commit_runner",
        "signature_algorithm": "Ed25519",
        "signature_b64url": "A" * 86,
    }
    root = _sign_event(root_raw, private_key)
    root_context = RunEventSecurityContext.model_validate(
        _security_context(
            private_key,
            run_id="run_root",
            root_ordinal=0,
            allowed_event_types=["RunCreated"],
        ),
        strict=True,
    )
    EventAuthenticator().authenticate(root, root_context)

    nonroot_context = RunEventSecurityContext.model_validate(
        _security_context(
            private_key,
            run_id="run_root",
            root_ordinal=1,
            allowed_event_types=["RunCreated"],
        ),
        strict=True,
    )
    with pytest.raises(EventSchemaError, match="ordinal"):
        EventAuthenticator().authenticate(root, nonroot_context)


def test_runtime_replacement_binds_active_wait_before_transition_head() -> None:
    prerequisite = RuntimeReplacementPrerequisite.model_validate(
        {
            "prerequisite_type": "runtime_identity_replacement",
            "failed_waiting_event": {
                "event_id": "019fe8f8-1400-7000-8000-000000000015",
                "sequence_no": 6,
                "event_hash": "sha256:" + "c" * 64,
            },
            "failed_readiness_gate_id": "gate_runtime_readiness",
            "old_dependency_identity_hash": "sha256:" + "d" * 64,
            "new_dependency_identity_hash": "sha256:" + "e" * 64,
        },
        strict=True,
    )

    assert prerequisite.binds_parent_head(
        failed_waiting_event_id="019fe8f8-1400-7000-8000-000000000015",
        expected_parent_head_sequence_no=7,
    )
    assert not prerequisite.binds_parent_head(
        failed_waiting_event_id="019fe8f8-1400-7000-8000-000000000016",
        expected_parent_head_sequence_no=7,
    )
    assert not prerequisite.binds_parent_head(
        failed_waiting_event_id="019fe8f8-1400-7000-8000-000000000015",
        expected_parent_head_sequence_no=6,
    )


def _stage_gate(
    previous_hash: str,
    *,
    index: int,
    from_state: str,
    to_state: str,
    gate_id: str,
    reason_code: str,
) -> dict[str, object]:
    return _common(
        "automarkov.stage-gate-passed.v1",
        "StageGatePassed",
        index=index,
    ) | {
        "sequence_no": index,
        "previous_event_hash": previous_hash,
        "gate_id": gate_id,
        "gate_version": "gate-v1",
        "gate_contract_hash": "sha256:" + "1" * 64,
        "subject_artifact_references": [_artifact("2")],
        "gate_report": _artifact("3"),
        "from_state": from_state,
        "to_state": to_state,
        "reason_code": reason_code,
        "result": "passed",
    }


def _transition(
    previous_hash: str,
    trigger: EventRecord,
    *,
    index: int,
    from_state: str,
    to_state: str,
    reason_code: str,
) -> dict[str, object]:
    return _common(
        "automarkov.state-transitioned.v1",
        "StateTransitioned",
        index=index,
    ) | {
        "sequence_no": index,
        "previous_event_hash": previous_hash,
        "from_state": from_state,
        "to_state": to_state,
        "trigger_event_id": trigger.event.event_id,
        "trigger_event_hash": trigger.event_hash,
        "input_artifact_ids": [_artifact("2")["artifact_id"]],
        "gate_report_artifact_id": _artifact("3")["artifact_id"],
        "gate_report_payload_hash": _artifact("3")["payload_hash"],
        "budget_snapshot_artifact_id": _artifact("4")["artifact_id"],
        "budget_snapshot_payload_hash": _artifact("4")["payload_hash"],
        "reason_code": reason_code,
    }


def test_stage_gate_is_the_only_ordinary_forward_transition_cause() -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(b"\x53" * 32)
    root_raw = {
        "schema_version": "automarkov.run-created.v1",
        "event_type": "RunCreated",
        "signing_domain": "AutoMarkov-Run-Created-v1",
        "event_id": "019fe8f8-1400-7000-8000-000000000020",
        "experiment_id": "experiment_union_closure",
        "run_id": _RUN_ID,
        "actor_principal_id": "principal_union_closure",
        "issued_at": _ISSUED_AT,
        "sequence_no": 0,
        "previous_event_hash": _PREVIOUS_HASH,
        "run_manifest_artifact_id": _artifact("5")["artifact_id"],
        "run_manifest_payload_hash": _artifact("5")["payload_hash"],
        "initial_state": "RECEIVED",
        "creation_principal_id": "principal_union_closure",
        "reason_code": "run_created",
        "nonce_b64url": "A" * 22,
        "signing_key_id": "key_fixed_commit_runner",
        "signature_algorithm": "Ed25519",
        "signature_b64url": "A" * 86,
    }
    signed_root = _sign_event(root_raw, private_key)
    root_record = _record(signed_root.model_dump(mode="json"))
    gate_record = _record(
        _stage_gate(
            root_record.event_hash,
            index=1,
            from_state="RECEIVED",
            to_state="RESEARCHING",
            gate_id="INTAKE_SCHEMA_BUDGET_AUTHORITY",
            reason_code="intake_accepted",
        )
    )
    transition_record = _record(
        _transition(
            gate_record.event_hash,
            gate_record,
            index=2,
            from_state="RECEIVED",
            to_state="RESEARCHING",
            reason_code="intake_accepted",
        )
    )

    assert isinstance(gate_record.event, StageGatePassed)
    assert project_records(
        (root_record, gate_record, transition_record)
    ).state.value == ("RESEARCHING")

    self_triggered = _record(
        _transition(
            transition_record.event_hash,
            transition_record,
            index=3,
            from_state="RESEARCHING",
            to_state="TEXT_DRAFTED",
            reason_code="research_completed",
        )
    )
    with pytest.raises(InvalidRunTransitionError):
        project_records((root_record, gate_record, transition_record, self_triggered))

    wrong_gate = _stage_gate(
        root_record.event_hash,
        index=1,
        from_state="RECEIVED",
        to_state="RESEARCHING",
        gate_id="CALLER_SELECTED_GATE",
        reason_code="intake_accepted",
    )
    with pytest.raises(EventSchemaError):
        default_event_schema_registry().decode(wrong_gate)


def _approval(
    previous_hash: str,
    *,
    index: int,
    decision: str,
    supersedes: str | None,
) -> dict[str, object]:
    return {
        "schema_version": "automarkov.approval-event.v1",
        "signing_domain": "AutoMarkov-Approval-v1",
        "event_type": "SignedApprovalEvent",
        "event_id": f"019fe8f8-1400-7000-8000-{index:012x}",
        "experiment_id": "experiment_union_closure",
        "run_id": _RUN_ID,
        "sequence_no": index,
        "previous_event_hash": previous_hash,
        "actor_principal_id": "principal_union_closure",
        "issued_at": _ISSUED_AT,
        "decision": decision,
        "artifact": _artifact("6"),
        "supersedes_approval_event_id": supersedes,
        "approval_principal_id": "principal_union_closure",
        "approval_principal_kind": "experiment_approval_policy",
        "approval_policy_source_hash": None,
        "input_report_artifact_ids": [],
        "reason_code": f"approval_{decision}",
        "nonce_b64url": "A" * 22,
        "signing_key_id": "key_fixed_commit_runner",
        "signature_algorithm": "Ed25519",
        "signature_b64url": "A" * 86,
    }


def _revocation_tuple(
    before_head: EventRecord,
    *,
    from_state: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    revoked = _approval(
        before_head.event_hash,
        index=before_head.event.sequence_no + 1,
        decision="revoked",
        supersedes="019fe8f8-1400-7000-8000-000000000030",
    )
    revoked_record = _record(revoked)
    superseded = _common(
        "automarkov.artifact-superseded.v1",
        "ArtifactSuperseded",
        index=before_head.event.sequence_no + 2,
    ) | {
        "sequence_no": before_head.event.sequence_no + 2,
        "previous_event_hash": revoked_record.event_hash,
        "old_artifact": _artifact("6"),
        "new_artifact": _artifact("7"),
        "lineage_report": _artifact("8"),
        "supersession_reason_code": "approval_revoked",
    }
    superseded_record = _record(superseded)
    transition = _transition(
        superseded_record.event_hash,
        superseded_record,
        index=before_head.event.sequence_no + 3,
        from_state=from_state,
        to_state="TEXT_DRAFTED",
        reason_code="approval_revoked",
    )
    return revoked, superseded, transition


def test_nonterminal_approval_revocation_requires_atomic_rollback_tuple() -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(b"\x54" * 32)
    root_raw = {
        "schema_version": "automarkov.run-created.v1",
        "event_type": "RunCreated",
        "signing_domain": "AutoMarkov-Run-Created-v1",
        "event_id": "019fe8f8-1400-7000-8000-000000000030",
        "experiment_id": "experiment_union_closure",
        "run_id": _RUN_ID,
        "actor_principal_id": "principal_union_closure",
        "issued_at": _ISSUED_AT,
        "sequence_no": 0,
        "previous_event_hash": _PREVIOUS_HASH,
        "run_manifest_artifact_id": _artifact("9")["artifact_id"],
        "run_manifest_payload_hash": _artifact("9")["payload_hash"],
        "initial_state": "RECEIVED",
        "creation_principal_id": "principal_union_closure",
        "reason_code": "run_created",
        "nonce_b64url": "A" * 22,
        "signing_key_id": "key_fixed_commit_runner",
        "signature_algorithm": "Ed25519",
        "signature_b64url": "A" * 86,
    }
    root = _record(_sign_event(root_raw, private_key).model_dump(mode="json"))
    approved = _record(
        _approval(
            root.event_hash,
            index=1,
            decision="approved",
            supersedes=None,
        )
    )
    revoked = _approval(
        approved.event_hash,
        index=2,
        decision="revoked",
        supersedes=approved.event.event_id,
    )
    revoked_record = _record(revoked)

    with pytest.raises(InvalidRunTransitionError):
        project_records((root, approved, revoked_record))

    isolated_command = {
        "schema_version": "automarkov.lifecycle-command.v1",
        "command_type": "append_run_events",
        "command_id": "019fe8f8-1400-7000-8000-000000000031",
        "actor_principal_id": "principal_union_closure",
        "issued_at": _ISSUED_AT,
        "idempotency_key": "isolated-revocation",
        "run_id": _RUN_ID,
        "expected_state": "TEXT_LOCKED",
        "expected_head": {
            "run_id": _RUN_ID,
            "sequence_no": approved.event.sequence_no,
            "event_hash": approved.event_hash,
        },
        "events": [revoked],
    }
    with pytest.raises(ValueError, match="revocation"):
        AppendRunEventsCommand.model_validate(isolated_command, strict=True)

    rollback = deepcopy(isolated_command)
    rollback["events"] = list(_revocation_tuple(approved, from_state="TEXT_LOCKED"))
    AppendRunEventsCommand.model_validate(rollback, strict=True)

    frozen = deepcopy(rollback)
    frozen["expected_state"] = "SEALED_E2E_VALIDATING"
    transition = frozen["events"][2]
    assert isinstance(transition, dict)
    transition["from_state"] = "SEALED_E2E_VALIDATING"
    with pytest.raises(ValueError, match="replacement"):
        AppendRunEventsCommand.model_validate(frozen, strict=True)
