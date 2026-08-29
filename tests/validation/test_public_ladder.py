from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from automarkov.contracts.validation import ValidationReport
from automarkov.lifecycle import AppendRunEventsCommand, EventHead, StateTransitioned
from automarkov.public_validation import (
    UNIT_GATE_CHECKS,
    BoundPublicValidationReport,
    DifferentialTestReport,
    MetamorphicTestReport,
    PropertyTestReport,
    PublicDevLearningProbeReport,
    PublicValidationGateLifecycleContext,
    PublicValidationLadder,
    PublicValidationPlan,
    PublicValidationRequest,
    TrajectoryTestReport,
    UnitValidationReport,
    materialize_public_validation_lifecycle_events,
    public_validation_payload_hash,
)


def _ref(digit: str) -> dict[str, str]:
    return {
        "artifact_id": f"artifact_{digit * 64}",
        "payload_hash": f"sha256:{digit * 64}",
    }


def _derived_ref(prefix: str, digit: str) -> dict[str, str]:
    value = prefix + digit * 63
    return {"artifact_id": f"artifact_{value}", "payload_hash": f"sha256:{value}"}


def _uuid7(index: int) -> str:
    timestamp_ms = int(datetime(2026, 8, 12, tzinfo=UTC).timestamp() * 1_000)
    value = (timestamp_ms << 80) | (7 << 76) | (2 << 62) | index
    return str(UUID(int=value))


def _generic_report(*, level: str = "behavioral") -> dict[str, Any]:
    return {
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
    }


def _claim(*, level: str = "behavioral") -> dict[str, Any]:
    return {
        "schema_version": "automarkov.validation-claim.v1",
        "claim_kind": "validation_claim",
        "subject_ref": _ref("2"),
        "report_refs": [_ref("9")],
        "level": level,
        "scope": ["transition_kernel"],
        "passed": True,
    }


def _report_payload(kind: str) -> dict[str, Any]:
    level = "executable" if kind == "unit_validation" else "behavioral"
    validation_report = ValidationReport.model_validate(
        _generic_report(level=level), strict=True
    )
    validation_claim = _claim(level=level)
    validation_claim["report_refs"] = [
        {
            **_ref("9"),
            "payload_hash": public_validation_payload_hash(validation_report),
        }
    ]
    payload: dict[str, Any] = {
        "schema_version": f"automarkov.{kind.replace('_', '-')}-report.v1",
        "report_kind": kind,
        "subject_ref": _ref("2"),
        "fixed_job_manifest": _ref("8"),
        "validation_report": validation_report,
        "validation_claim": validation_claim,
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
    return payload


_REPORT_CLASSES = {
    "unit_validation": UnitValidationReport,
    "property_test": PropertyTestReport,
    "metamorphic_test": MetamorphicTestReport,
    "differential_test": DifferentialTestReport,
    "trajectory_test": TrajectoryTestReport,
    "public_dev_learning_probe": PublicDevLearningProbeReport,
}


def _bound(kind: str, digit: str) -> BoundPublicValidationReport:
    report = _REPORT_CLASSES[kind].model_validate(_report_payload(kind), strict=True)
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
    *,
    method: str = "automarkov",
    omissions: list[str] | None = None,
) -> PublicValidationPlan:
    payload = {
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
        "ablation_binding": None,
    }
    return PublicValidationPlan.model_validate(payload, strict=True)


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


def test_unit_gate_requires_the_complete_unmaskable_check_set() -> None:
    outcome = PublicValidationLadder().evaluate(
        _request(_plan(), (_bound("unit_validation", "a"),), state="UNIT_VALIDATING")
    )

    assert outcome.outcome_kind == "gate_passed"
    assert outcome.next_state == "SIMULATION_VALIDATING"
    assert outcome.gate_report is not None
    assert outcome.gate_report.public_validation_level == "executable"

    payload = _report_payload("unit_validation")
    payload["completed_checks"] = list(UNIT_GATE_CHECKS[:-1])
    with pytest.raises((ValueError, ValidationError), match="unit gate checks"):
        UnitValidationReport.model_validate(payload, strict=True)


def test_typed_report_claim_binds_the_canonical_validation_report_hash() -> None:
    payload = _report_payload("unit_validation")
    payload["validation_claim"]["report_refs"][0]["payload_hash"] = "sha256:" + "0" * 64

    with pytest.raises((ValueError, ValidationError), match="validation claim"):
        UnitValidationReport.model_validate(payload, strict=True)


def test_full_simulation_ladder_freezes_only_after_every_typed_report_passes() -> None:
    reports = tuple(
        _bound(kind, digit)
        for kind, digit in (
            ("property_test", "a"),
            ("metamorphic_test", "b"),
            ("differential_test", "c"),
            ("trajectory_test", "d"),
            ("public_dev_learning_probe", "e"),
        )
    )

    outcome = PublicValidationLadder().evaluate(
        _request(_plan(), reports, state="SIMULATION_VALIDATING")
    )

    assert outcome.outcome_kind == "candidate_frozen"
    assert outcome.next_state == "SEALED_E2E_VALIDATING"
    assert outcome.candidate_freeze is not None
    assert outcome.candidate_freeze.public_validation_level == "behavioral"
    assert len(outcome.candidate_freeze.report_refs) == 6


def test_simulation_cannot_bypass_the_prior_unit_gate() -> None:
    request = _request(
        _plan(),
        (_bound("public_dev_learning_probe", "a"),),
        state="SIMULATION_VALIDATING",
    )
    payload = request.model_dump(mode="python")
    payload["prior_unit_gate"] = None
    bypass = PublicValidationRequest.model_validate(payload, strict=True)

    with pytest.raises(ValueError, match="prior unit gate"):
        PublicValidationLadder().evaluate(bypass)


def test_learning_probe_cannot_use_final_seeds_or_emit_a_policy_checkpoint() -> None:
    payload = _report_payload("public_dev_learning_probe")
    payload["uses_final_training_seed"] = True
    with pytest.raises((ValueError, ValidationError), match="probe isolation"):
        PublicDevLearningProbeReport.model_validate(payload, strict=True)


def test_unit_decision_materializes_the_existing_reducer_gate_tuple() -> None:
    request = _request(
        _plan(), (_bound("unit_validation", "a"),), state="UNIT_VALIDATING"
    )
    outcome = PublicValidationLadder().evaluate(request)
    assert outcome.gate_report is not None
    gate_report_ref = {
        "artifact_id": _ref("e")["artifact_id"],
        "payload_hash": public_validation_payload_hash(outcome.gate_report),
    }
    head = EventHead.model_validate(
        {
            "run_id": "run_t15_gate",
            "sequence_no": 10,
            "event_hash": "sha256:" + "1" * 64,
        },
        strict=True,
    )
    context = PublicValidationGateLifecycleContext.model_validate(
        {
            "schema_version": "automarkov.public-validation-gate-lifecycle-context.v1",
            "context_kind": "stage_gate",
            "experiment_id": None,
            "run_id": "run_t15_gate",
            "actor_principal_id": "principal_t15_runner",
            "actor_process_execution_id": "execution_t15_gate",
            "issued_at": "2026-08-12T00:00:00Z",
            "expected_head": head,
            "cause_event_id": _uuid7(11),
            "transition_event_id": _uuid7(12),
            "budget_snapshot": _ref("b"),
            "gate_report": gate_report_ref,
            "gate_version": "t15-v1",
            "gate_contract_hash": "sha256:" + "c" * 64,
        },
        strict=True,
    )

    batch = materialize_public_validation_lifecycle_events(request, outcome, context)

    command_payload = {
        "schema_version": "automarkov.lifecycle-command.v1",
        "command_type": "append_run_events",
        "command_id": _uuid7(13),
        "actor_principal_id": "principal_t15_runner",
        "issued_at": "2026-08-12T00:00:00Z",
        "idempotency_key": "t15-unit-gate",
        "run_id": "run_t15_gate",
        "expected_state": "UNIT_VALIDATING",
        "expected_head": head,
        "events": batch.event_payloads,
    }
    AppendRunEventsCommand.model_validate(command_payload, strict=True)
    assert batch.transition_event.to_state.value == "SIMULATION_VALIDATING"

    transition_payload = batch.transition_event.model_dump(mode="json")
    transition_payload["gate_report_artifact_id"] = _ref("f")["artifact_id"]
    mismatched = StateTransitioned.model_validate(transition_payload, strict=True)
    command_payload["events"] = (
        batch.cause_event.model_dump(mode="json"),
        mismatched.model_dump(mode="json"),
    )
    with pytest.raises((ValueError, ValidationError), match="stage gate"):
        AppendRunEventsCommand.model_validate(command_payload, strict=True)
