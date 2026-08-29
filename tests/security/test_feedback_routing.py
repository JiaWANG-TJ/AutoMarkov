from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from automarkov.contracts.validation import ValidationReport
from automarkov.public_validation import (
    UNIT_GATE_CHECKS,
    BoundPublicValidationReport,
    PublicCounterexample,
    PublicValidationBudgetLifecycleContext,
    PublicValidationLadder,
    PublicValidationPlan,
    PublicValidationRequest,
    PublicValidationRevisionLifecycleContext,
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


def _unit_binding() -> BoundPublicValidationReport:
    validation_report = ValidationReport.model_validate(
        {
            "schema_version": "automarkov.validation-report.v1",
            "report_kind": "validation_report",
            "subject_ref": _ref("2"),
            "level": "executable",
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
    report = UnitValidationReport.model_validate(
        {
            "schema_version": "automarkov.unit-validation-report.v1",
            "report_kind": "unit_validation",
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
                        "payload_hash": public_validation_payload_hash(
                            validation_report
                        ),
                    }
                ],
                "level": "executable",
                "scope": ["transition_kernel"],
                "passed": True,
            },
            "counterexample_refs": [],
            "official_api_validator": "gymnasium.utils.env_checker.check_env",
            "completed_checks": list(UNIT_GATE_CHECKS),
        },
        strict=True,
    )
    return BoundPublicValidationReport.model_validate(
        {
            "report_ref": _ref("a"),
            "report": report,
            "process_terminal_record": _derived_ref("1", "a"),
            "execution_attestation": _derived_ref("2", "a"),
        },
        strict=True,
    )


def _plan() -> PublicValidationPlan:
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
            "ablation_method_id": "automarkov",
            "omitted_gate_ids": [],
            "ablation_binding": None,
        },
        strict=True,
    )


def _counterexample(
    *, failure_class: str = "environment_implementation"
) -> PublicCounterexample:
    return PublicCounterexample.model_validate(
        {
            "schema_version": "automarkov.public-counterexample.v1",
            "counterexample_kind": "public_counterexample",
            "counterexample_ref": _ref("d"),
            "subject_ref": _ref("2"),
            "source_report_ref": _ref("a"),
            "provenance": {
                "provenance_kind": "independently_derived",
                "derivation_kind": "property",
                "failure_class": failure_class,
            },
            "observed_payload": _ref("b"),
            "expected_payload": None,
        },
        strict=True,
    )


def _failed_request(
    counterexample: PublicCounterexample,
    *,
    boundary: str = "public_dev",
    frozen: bool = False,
    prior: int = 0,
    requested: int = 1,
) -> PublicValidationRequest:
    unit = _unit_binding()
    payload: dict[str, Any] = {
        "schema_version": "automarkov.public-validation-request.v1",
        "plan": _plan(),
        "from_state": "UNIT_VALIDATING",
        "validation_target": {
            "required_level": "behavioral",
            "required_properties": ["transition_kernel"],
            "accepted_tolerances": [],
        },
        "prior_unit_gate": None,
        "reports": [unit],
        "omissions": [],
        "counterexamples": [],
        "prior_revision_count": 0,
        "requested_revision_count": 0,
        "feedback_boundary": "public_dev",
        "candidate_frozen": False,
    }
    failed_report = unit.report.model_copy(
        update={
            "validation_report": unit.report.validation_report.model_copy(
                update={
                    "status": "failed",
                    "covered_scope": (),
                    "uncovered_scope": ("transition_kernel",),
                }
            ),
            "validation_claim": None,
            "counterexample_refs": (counterexample.counterexample_ref,),
        }
    )
    # model_copy 会丢失可信来源，request ingress 必须重新验证。
    payload |= {
        "reports": [
            {
                "report_ref": _ref("a"),
                "report": failed_report,
                "process_terminal_record": _derived_ref("1", "a"),
                "execution_attestation": _derived_ref("2", "a"),
            }
        ],
        "counterexamples": [counterexample],
        "prior_revision_count": prior,
        "requested_revision_count": requested,
        "feedback_boundary": boundary,
        "candidate_frozen": frozen,
    }
    return PublicValidationRequest.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    ("failure_class", "expected"),
    [
        ("environment_implementation", "ENVIRONMENT_IMPLEMENTED"),
        ("decision_process", "FORMAL_DRAFTED"),
        ("semantic_assumption", "TEXT_DRAFTED"),
    ],
)
def test_independent_feedback_routes_to_the_nearest_authorized_layer(
    failure_class: str, expected: str
) -> None:
    outcome = PublicValidationLadder().evaluate(
        _failed_request(_counterexample(failure_class=failure_class))
    )
    assert outcome.outcome_kind == "revision_required"
    assert outcome.next_state == expected


def test_official_reference_payload_is_confined_to_developer_tester() -> None:
    counterexample = PublicCounterexample.model_validate(
        {
            "schema_version": "automarkov.public-counterexample.v1",
            "counterexample_kind": "public_counterexample",
            "counterexample_ref": _ref("d"),
            "subject_ref": _ref("2"),
            "source_report_ref": _ref("a"),
            "provenance": {
                "provenance_kind": "official_reference_derived",
                "reference_value_kind": "expected_transition",
                "authorized_roles": ["Developer", "Tester"],
            },
            "observed_payload": _ref("b"),
            "expected_payload": _ref("c"),
        },
        strict=True,
    )

    outcome = PublicValidationLadder().evaluate(_failed_request(counterexample))
    assert outcome.next_state == "ENVIRONMENT_IMPLEMENTED"

    payload = counterexample.model_dump(mode="python")
    payload["provenance"]["reference_value_kind"] = "gold_trace"
    with pytest.raises((ValueError, ValidationError)):
        PublicCounterexample.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    ("boundary", "frozen"),
    [("sealed", False), ("post_freeze", False), ("public_dev", True)],
)
def test_sealed_or_post_freeze_feedback_is_rejected(
    boundary: str, frozen: bool
) -> None:
    with pytest.raises(ValueError, match="feedback boundary"):
        PublicValidationLadder().evaluate(
            _failed_request(_counterexample(), boundary=boundary, frozen=frozen)
        )


def test_revision_count_is_exactly_one_and_budget_exhaustion_is_terminal() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        PublicValidationLadder().evaluate(
            _failed_request(_counterexample(), prior=0, requested=2)
        )

    outcome = PublicValidationLadder().evaluate(
        _failed_request(_counterexample(), prior=2, requested=3)
    )
    assert outcome.outcome_kind == "budget_exhausted"
    assert outcome.next_state == "BUDGET_EXHAUSTED"
    assert outcome.revision_routes == ()


def test_revision_decision_materializes_exact_supersession_rollback_inputs() -> None:
    request = _failed_request(_counterexample(failure_class="formal_specification"))
    outcome = PublicValidationLadder().evaluate(request)
    context = PublicValidationRevisionLifecycleContext.model_validate(
        {
            "schema_version": "automarkov.public-validation-revision-lifecycle-context.v1",
            "context_kind": "revision",
            "experiment_id": None,
            "run_id": "run_t15_revision",
            "actor_principal_id": "principal_t15_runner",
            "actor_process_execution_id": "execution_t15_revision",
            "issued_at": "2026-08-12T00:00:00Z",
            "expected_head": {
                "run_id": "run_t15_revision",
                "sequence_no": 20,
                "event_hash": "sha256:" + "1" * 64,
            },
            "cause_event_id": _uuid7(21),
            "transition_event_id": _uuid7(22),
            "budget_snapshot": _ref("e"),
            "new_candidate_bundle": _ref("f"),
            "lineage_report": _ref("c"),
        },
        strict=True,
    )

    batch = materialize_public_validation_lifecycle_events(request, outcome, context)

    assert batch.cause_event.event_type == "ArtifactSuperseded"
    assert batch.transition_event.to_state.value == "FORMAL_DRAFTED"
    assert batch.transition_event.reason_code == "formal_revision_required"
    assert batch.transition_event.trigger_event_id == batch.cause_event.event_id


def test_budget_decision_materializes_exact_revision_exhaustion_inputs() -> None:
    request = _failed_request(_counterexample(), prior=2, requested=3)
    outcome = PublicValidationLadder().evaluate(request)
    context = PublicValidationBudgetLifecycleContext.model_validate(
        {
            "schema_version": "automarkov.public-validation-budget-lifecycle-context.v1",
            "context_kind": "budget_exhausted",
            "experiment_id": None,
            "run_id": "run_t15_budget",
            "actor_principal_id": "principal_t15_runner",
            "actor_process_execution_id": "execution_t15_budget",
            "issued_at": "2026-08-12T00:00:00Z",
            "expected_head": {
                "run_id": "run_t15_budget",
                "sequence_no": 30,
                "event_hash": "sha256:" + "1" * 64,
            },
            "cause_event_id": _uuid7(31),
            "transition_event_id": _uuid7(32),
            "budget_snapshot": _ref("e"),
            "budget_policy": _ref("f"),
            "cause_receipt": _ref("c"),
        },
        strict=True,
    )

    batch = materialize_public_validation_lifecycle_events(request, outcome, context)

    assert batch.cause_event.event_type == "BudgetExhausted"
    assert batch.cause_event.budget_kind == "revision"
    assert batch.cause_event.limit == 2
    assert batch.cause_event.consumed == 2
    assert batch.transition_event.to_state.value == "BUDGET_EXHAUSTED"
