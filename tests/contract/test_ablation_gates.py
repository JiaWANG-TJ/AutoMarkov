from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from automarkov.lifecycle import (
    EventReference,
    GateOmittedByDesign,
    encode_event_record,
    parse_event_bytes,
)
from automarkov.public_validation import (
    UNIT_GATE_CHECKS,
    BoundGateOmission,
    BoundPublicValidationReport,
    DifferentialTestReport,
    MetamorphicTestReport,
    PropertyTestReport,
    PublicDevLearningProbeReport,
    PublicValidationLadder,
    PublicValidationPlan,
    PublicValidationRequest,
    TrajectoryTestReport,
    UnitValidationReport,
    public_validation_payload_hash,
)
from automarkov.validation_contracts import ValidationReport


def _ref(digit: str) -> dict[str, str]:
    return {
        "artifact_id": f"artifact_{digit * 64}",
        "payload_hash": f"sha256:{digit * 64}",
    }


def _derived_ref(prefix: str, digit: str) -> dict[str, str]:
    value = prefix + digit * 63
    return {"artifact_id": f"artifact_{value}", "payload_hash": f"sha256:{value}"}


_REPORT_CLASSES = {
    "unit_validation": UnitValidationReport,
    "property_test": PropertyTestReport,
    "metamorphic_test": MetamorphicTestReport,
    "differential_test": DifferentialTestReport,
    "trajectory_test": TrajectoryTestReport,
    "public_dev_learning_probe": PublicDevLearningProbeReport,
}


def _bound(kind: str, digit: str) -> BoundPublicValidationReport:
    level = "executable" if kind == "unit_validation" else "behavioral"
    validation_report = ValidationReport.model_validate(
        {
            "schema_version": "automarkov.validation-report.v1",
            "report_kind": "validation_report",
            "subject_ref": _ref("2"),
            "level": level,
            "validator_id": "public_validator",
            "validator_version": "1.0.0",
            "status": "passed",
            "scope": ["transition_kernel"],
            "covered_scope": ["transition_kernel"],
            "uncovered_scope": [],
            "assumptions": [],
            "proof_refs": [],
            "formal_evidence": None,
        },
        strict=True,
    )
    payload: dict[str, object] = {
        "schema_version": f"automarkov.{kind.replace('_', '-')}-report.v1",
        "report_kind": kind,
        "subject_ref": _ref("2"),
        "fixed_job_manifest": _ref("8"),
        "validation_report": validation_report,
        "validation_claim": {
            "schema_version": "automarkov.validation-claim.v1",
            "claim_kind": "validation_claim",
            "subject_ref": _ref("2"),
            "report_refs": [
                {
                    **_ref("9"),
                    "payload_hash": public_validation_payload_hash(validation_report),
                }
            ],
            "level": level,
            "scope": ["transition_kernel"],
            "passed": True,
        },
        "counterexample_refs": [],
    }
    if kind == "unit_validation":
        payload["completed_checks"] = list(UNIT_GATE_CHECKS)
        payload["official_api_validator"] = "gymnasium.utils.env_checker.check_env"
    if kind == "property_test":
        payload["property_engine"] = "hypothesis"
    if kind == "public_dev_learning_probe":
        payload |= {
            "learner_backend": "ray.rllib.algorithms.ppo.PPOConfig",
            "diagnostic_predicates": ["action_effect", "finite_reward"],
            "uses_final_training_seed": False,
            "emits_policy_checkpoint": False,
        }
    report = _REPORT_CLASSES[kind].model_validate(payload, strict=True)
    return BoundPublicValidationReport.model_validate(
        {
            "report_ref": _ref(digit),
            "report": report,
            "process_terminal_record": _derived_ref("1", digit),
            "execution_attestation": _derived_ref("2", digit),
        },
        strict=True,
    )


def _plan(
    *, method: str = "automarkov", omissions: list[str] | None = None
) -> PublicValidationPlan:
    ablation_binding = (
        {
            "experiment_id": "experiment_ablation",
            "run_id": "run_ablation",
            "cell_id": "cell_1",
            "ablation_execution_plan": _ref("d"),
            "pair_binding_id": "pair_1",
            "task_card": _ref("0"),
        }
        if omissions
        else None
    )
    return PublicValidationPlan.model_validate(
        {
            "schema_version": "automarkov.public-validation-plan.v1",
            "track": "AUTO",
            "variant_id": "v1_canonical",
            "source_terminal_kind": "active",
            "run_manifest": _ref("1"),
            "task_contract": _ref("3"),
            "decision_process_spec": _ref("4"),
            "candidate_bundle": _ref("2"),
            "environment_binding": _ref("5"),
            "suite_adapter": _ref("6"),
            "runtime_profiles": [_ref("7")],
            "fixed_job_manifests": [_ref("8")],
            "seed_ids": ["public_seed_00"],
            "wall_time_budget_ms": 10_000,
            "step_budget": 100,
            "revision_budget": 2,
            "ablation_method_id": method,
            "omitted_gate_ids": omissions or [],
            "ablation_binding": ablation_binding,
        },
        strict=True,
    )


def _request(
    plan: PublicValidationPlan,
    reports: tuple[BoundPublicValidationReport, ...],
    *,
    state: str,
) -> PublicValidationRequest:
    prior_unit_gate = None
    if state == "SIMULATION_VALIDATING":
        prior_outcome = PublicValidationLadder().evaluate(
            _request(
                plan,
                (_bound("unit_validation", "f"),),
                state="UNIT_VALIDATING",
            )
        )
        prior_unit_gate = prior_outcome.gate_report
    return PublicValidationRequest.model_validate(
        {
            "schema_version": "automarkov.public-validation-request.v1",
            "plan": plan,
            "from_state": state,
            "validation_target": {
                "required_level": "behavioral",
                "required_properties": ["transition_kernel"],
                "accepted_tolerances": [],
            },
            "prior_unit_gate": prior_unit_gate,
            "reports": reports,
            "omissions": [],
            "counterexamples": [],
            "prior_revision_count": 0,
            "requested_revision_count": 0,
            "feedback_boundary": "public_dev",
            "candidate_frozen": False,
        },
        strict=True,
    )


def _event_id() -> str:
    timestamp_ms = int(datetime(2026, 8, 12, tzinfo=UTC).timestamp() * 1000)
    raw = timestamp_ms.to_bytes(6, "big") + bytes.fromhex("70008000000000000000")
    return str(UUID(bytes=raw))


def _omission(method: str, gate: str, kinds: list[str]) -> BoundGateOmission:
    event = GateOmittedByDesign.model_validate(
        {
            "schema_version": "automarkov.gate-omitted-event.v1",
            "event_type": "GateOmittedByDesign",
            "signing_domain": "AutoMarkov-Gate-Omitted-v1",
            "event_id": _event_id(),
            "experiment_id": "experiment_ablation",
            "run_id": "run_ablation",
            "issued_at": "2026-08-12T00:00:00Z",
            "nonce_b64url": "A" * 22,
            "signing_key_id": "runner_key",
            "signature_b64url": "A" * 86,
            "sequence_no": 8,
            "previous_event_hash": "sha256:" + "1" * 64,
            "track": "AUTO",
            "variant_id": "v1_canonical",
            "cell_id": "cell_1",
            "ablation_execution_plan_artifact_id": _ref("d")["artifact_id"],
            "ablation_execution_plan_hash": _ref("d")["payload_hash"],
            "pair_binding_id": "pair_1",
            "task_card_artifact_id": _ref("0")["artifact_id"],
            "subject_artifact_ids": [_ref("2")["artifact_id"]],
            "expected_missing_artifact_kinds": kinds,
            "output_artifact_ids": [],
            "reason": "controlled_ablation",
            "ablation_method_id": method,
            "omitted_gate_id": gate,
        },
        strict=True,
    )
    event_hash = parse_event_bytes(
        encode_event_record(event.model_dump(mode="json"))
    ).event_hash
    reference = EventReference.model_validate(
        {
            "event_id": event.event_id,
            "sequence_no": event.sequence_no,
            "event_hash": event_hash,
        },
        strict=True,
    )
    return BoundGateOmission.model_validate(
        {"event_ref": reference, "event": event}, strict=True
    )


def test_plan_accepts_only_the_two_exact_public_omission_projections() -> None:
    _plan(
        method="automarkov_no_simulation_tester",
        omissions=["PUBLIC_SIMULATION_TESTER"],
    )
    _plan(
        method="automarkov_no_training_feedback",
        omissions=["PUBLIC_DEV_LEARNING_PROBE_AND_ROLLBACK"],
    )

    payload = _plan().model_dump(mode="python")
    payload["omitted_gate_ids"] = ["UNIT_VALIDATION"]
    with pytest.raises((ValueError, ValidationError)):
        PublicValidationPlan.model_validate(payload, strict=True)

    payload = _plan().model_dump(mode="python")
    payload["ablation_method_id"] = "automarkov_no_simulation_tester"
    payload["omitted_gate_ids"] = [
        "PUBLIC_SIMULATION_TESTER",
        "PUBLIC_DEV_LEARNING_PROBE_AND_ROLLBACK",
    ]
    with pytest.raises((ValueError, ValidationError), match="exact"):
        PublicValidationPlan.model_validate(payload, strict=True)


def test_no_simulation_requires_exact_signed_omission_and_does_not_upgrade_level() -> (
    None
):
    plan = _plan(
        method="automarkov_no_simulation_tester",
        omissions=["PUBLIC_SIMULATION_TESTER"],
    )
    request = _request(
        plan,
        (_bound("public_dev_learning_probe", "a"),),
        state="SIMULATION_VALIDATING",
    )
    payload = request.model_dump(mode="python")
    payload["omissions"] = [
        _omission(
            "automarkov_no_simulation_tester",
            "PUBLIC_SIMULATION_TESTER",
            [
                "PropertyTestReport",
                "MetamorphicTestReport",
                "DifferentialTestReport",
                "TrajectoryTestReport",
            ],
        )
    ]

    outcome = PublicValidationLadder().evaluate(
        type(request).model_validate(payload, strict=True)
    )
    assert outcome.outcome_kind == "candidate_frozen"
    assert outcome.candidate_freeze is not None
    assert outcome.candidate_freeze.public_validation_level == "executable"
    assert len(outcome.candidate_freeze.omission_event_refs) == 1


def test_no_training_feedback_requires_probe_absent_and_all_simulation_reports() -> (
    None
):
    plan = _plan(
        method="automarkov_no_training_feedback",
        omissions=["PUBLIC_DEV_LEARNING_PROBE_AND_ROLLBACK"],
    )
    reports = tuple(
        _bound(kind, digit)
        for kind, digit in (
            ("property_test", "a"),
            ("metamorphic_test", "b"),
            ("differential_test", "c"),
            ("trajectory_test", "d"),
        )
    )
    request = _request(plan, reports, state="SIMULATION_VALIDATING")
    payload = request.model_dump(mode="python")
    payload["omissions"] = [
        _omission(
            "automarkov_no_training_feedback",
            "PUBLIC_DEV_LEARNING_PROBE_AND_ROLLBACK",
            ["PublicDevLearningProbeReport"],
        )
    ]
    outcome = PublicValidationLadder().evaluate(
        type(request).model_validate(payload, strict=True)
    )
    assert outcome.outcome_kind == "candidate_frozen"
    assert outcome.candidate_freeze is not None
    assert outcome.candidate_freeze.public_validation_level == "behavioral"


def test_omission_is_not_a_pass_report_or_feedback_source() -> None:
    plan = _plan(
        method="automarkov_no_simulation_tester",
        omissions=["PUBLIC_SIMULATION_TESTER"],
    )
    request = _request(plan, (), state="SIMULATION_VALIDATING")
    with pytest.raises(ValueError, match="report kinds"):
        PublicValidationLadder().evaluate(request)


def test_signed_omission_binds_exact_event_hash_and_preregistered_cell() -> None:
    plan = _plan(
        method="automarkov_no_simulation_tester",
        omissions=["PUBLIC_SIMULATION_TESTER"],
    )
    request = _request(
        plan,
        (_bound("public_dev_learning_probe", "a"),),
        state="SIMULATION_VALIDATING",
    )
    omission = _omission(
        "automarkov_no_simulation_tester",
        "PUBLIC_SIMULATION_TESTER",
        [
            "PropertyTestReport",
            "MetamorphicTestReport",
            "DifferentialTestReport",
            "TrajectoryTestReport",
        ],
    )
    request_payload = request.model_dump(mode="python")
    request_payload["omissions"] = [omission]
    PublicValidationLadder().evaluate(
        PublicValidationRequest.model_validate(request_payload, strict=True)
    )

    event_payload = omission.event.model_dump(mode="json")
    event_payload["cell_id"] = "cell_other"
    substituted_event = GateOmittedByDesign.model_validate(event_payload, strict=True)
    substituted_hash = parse_event_bytes(
        encode_event_record(substituted_event.model_dump(mode="json"))
    ).event_hash
    request_payload["omissions"] = [
        {
            "event_ref": {
                "event_id": substituted_event.event_id,
                "sequence_no": substituted_event.sequence_no,
                "event_hash": substituted_hash,
            },
            "event": substituted_event,
        }
    ]
    with pytest.raises(ValueError, match="preregistered ablation binding"):
        PublicValidationLadder().evaluate(
            PublicValidationRequest.model_validate(request_payload, strict=True)
        )

    bad_reference = omission.model_dump(mode="python")
    bad_reference["event_ref"]["event_hash"] = "sha256:" + "e" * 64
    with pytest.raises((ValueError, ValidationError), match="event hash"):
        BoundGateOmission.model_validate(bad_reference, strict=True)
