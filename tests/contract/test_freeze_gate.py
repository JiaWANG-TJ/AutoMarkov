"""R09: Freeze gate contract tests — PredicateVerdict, FreezeGateReport,
ExperimentPreflightReport, FreezeGateChecker, check_freeze_gate,
preflight_experiment."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from automarkov.freeze_gate import (
    ExperimentPreflightReport,
    FreezeGateChecker,
    FreezeGateReport,
    PredicateKind,
    PredicateVerdict,
    check_freeze_gate,
    preflight_experiment,
)
from automarkov.lifecycle import ArtifactReference

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HX = "0" * 64
HASH = "sha256:" + HX
ART = "artifact_" + HX

ALL_PREDICATE_KINDS: tuple[PredicateKind, ...] = (
    "plan_closed",
    "source_commit_present",
    "profiles_present",
    "task_cards_present",
    "methods_present",
    "eligibility_present",
    "pair_seed_ledger_present",
    "design_power_sufficient",
    "calibrations_present",
    "keys_present",
    "sealed_handshake_consistent",
    "runner_dry_run_ready",
    "remote_env_vectors_present",
    "analysis_fixtures_present",
    "replacement_policy_present",
)


# ---------------------------------------------------------------------------
# Authorization stub — every field the checker reads via getattr
# ---------------------------------------------------------------------------
class _FullAuth:
    """Populated authorization object satisfying all 15 predicates."""

    def __init__(self) -> None:
        self.source_commit = "abc123"
        self.profile_id = "profile_main"
        self.method_id = "method_lamp_v1"
        self.suite_id = "taxi_mdp"
        self.variant_id = "v1_canonical"
        self.pair_id = "pair_001"
        self.generation_seed = 42
        self.track_id = "track_auto"
        self.runner_key_grant = SimpleNamespace(signing_key_id="key_main")
        self.launch_deadline = "2026-12-31T23:59:59Z"
        self.input_artifacts = (
            ArtifactReference(artifact_id=ART, payload_hash=HASH),
        )
        self.working_directory = "/tmp/workspace"


def _full_auth() -> _FullAuth:
    return _FullAuth()


# ---------------------------------------------------------------------------
# Minimal TaskContract — passes validate_task_contract_for_approval
# ---------------------------------------------------------------------------
def _minimal_contract_dict() -> dict:
    """Return a raw dict that satisfies TaskContract validation."""
    return {
        "schema_version": "automarkov.task-contract.v1",
        "contract_kind": "core_task",
        "task_identity": {
            "name": "test_task",
            "domain": "gym",
            "intended_use": "benchmarking",
            "excluded_uses": [],
        },
        "decision_structure": {
            "decision_makers": [
                {
                    "decision_maker_id": "agent_0",
                    "controlled_entity_ids": ["entity_0"],
                },
            ],
            "external_entity_ids": [],
            "coordination": "centralized",
            "decision_timing": {
                "timing": "simultaneous",
                "chance_turns": False,
                "environment_turns": False,
                "cycle_boundary": "episode",
            },
        },
        "objective": {
            "primary_objective": "maximise reward",
            "secondary_objectives": [],
            "success_criteria": ["positive reward"],
            "tradeoffs": [],
        },
        "information": {
            "observable_variables_by_decision_maker": {
                "agent_0": [
                    {
                        "name": "obs",
                        "domain": {
                            "kind": "vector",
                            "element_dtype": "float",
                            "shape": [{"dimension_kind": "fixed", "size": 4}],
                            "bounds": {
                                "binding_kind": "explicit",
                                "minimum": -1.0,
                                "maximum": 1.0,
                                "minimum_inclusive": True,
                                "maximum_inclusive": True,
                            },
                        },
                        "unit": None,
                        "semantic_definition": "observation vector",
                        "evidence_ids": [],
                    },
                ],
            },
            "latent_variables": [],
            "joint_observation_semantics": None,
            "history_access_by_decision_maker": {
                "agent_0": {
                    "observation_lags": [],
                    "action_lags": [],
                    "reward_lags": [],
                    "message_lags": [],
                    "recurrent_state_allowed": False,
                    "boundary_reset": "episode",
                },
            },
            "message_processes_by_recipient": {"agent_0": []},
        },
        "dynamics": {
            "exogenous_processes": [],
            "stochastic_assumptions": [],
            "intervention_effects": [],
            "reward_randomness": [],
            "time_step": "1",
            "horizon_binding": "1000",
        },
        "constraints": {
            "hard_constraints": [],
            "soft_constraints": [],
            "safety_constraints": [],
            "resource_limits": [],
        },
        "risks": {
            "failure_events": [],
            "risk_measures": [],
            "tolerances": [],
            "tail_or_worst_case_requirements": [],
        },
        "episode": {
            "reset_conditions": ["episode_start"],
            "termination_conditions": ["episode_end"],
            "truncation_conditions": ["time_limit"],
        },
        "evidence_and_assumptions": {
            "evidence_ids": [],
            "accepted_assumptions": [],
            "unresolved_questions": [],
        },
        "validation_target": {
            "required_level": "schema",
            "required_properties": ["deterministic"],
            "accepted_tolerances": [],
        },
    }


def _full_manifest_dict() -> dict:
    """Return a raw dict that satisfies the RunManifest v2 contract."""
    return {
        "schema_version": "automarkov.run-manifest.v2",
        "manifest_kind": "frozen_run",
        "run_id": "run_test_001",
        "experiment_id": "exp_001",
        "root_ordinal": 0,
        "task_request": {
            "artifact_id": ART,
            "payload_hash": HASH,
        },
        "event_security_context": {
            "schema_version": "automarkov.run-event-security-context.v1",
            "run_id": "run_test_001",
            "experiment_id": "exp_001",
            "root_ordinal": 0,
            "creation_policy": {
                "artifact_id": ART,
                "payload_hash": HASH,
            },
            "max_clock_skew_ms": 5000,
            "actor_capabilities": (
                {
                    "principal_id": "principal_candidate",
                    "process_execution_id": None,
                    "allowed_event_types": ("StageGatePassed",),
                },
            ),
            "signing_keys": (
                {
                    "signing_key_id": "key_candidate",
                    "principal_id": "principal_candidate",
                    "signature_algorithm": "Ed25519",
                    "public_key_b64url": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                    "not_before": "2025-01-01T00:00:00Z",
                    "not_after": "2027-01-01T00:00:00Z",
                    "revoked_at": None,
                },
                {
                    "signing_key_id": "key_comparator",
                    "principal_id": "principal_comparator",
                    "signature_algorithm": "Ed25519",
                    "public_key_b64url": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                    "not_before": "2025-01-01T00:00:00Z",
                    "not_after": "2027-01-01T00:00:00Z",
                    "revoked_at": None,
                },
                {
                    "signing_key_id": "key_coordinator",
                    "principal_id": "principal_coordinator",
                    "signature_algorithm": "Ed25519",
                    "public_key_b64url": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                    "not_before": "2025-01-01T00:00:00Z",
                    "not_after": "2027-01-01T00:00:00Z",
                    "revoked_at": None,
                },
                {
                    "signing_key_id": "key_evaluator",
                    "principal_id": "principal_evaluator",
                    "signature_algorithm": "Ed25519",
                    "public_key_b64url": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                    "not_before": "2025-01-01T00:00:00Z",
                    "not_after": "2027-01-01T00:00:00Z",
                    "revoked_at": None,
                },
                {
                    "signing_key_id": "key_gold",
                    "principal_id": "principal_gold",
                    "signature_algorithm": "Ed25519",
                    "public_key_b64url": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                    "not_before": "2025-01-01T00:00:00Z",
                    "not_after": "2027-01-01T00:00:00Z",
                    "revoked_at": None,
                },
            ),
            "run_creation": {
                "creation_principal_id": "principal_candidate",
                "signing_key_id": "key_candidate",
            },
            "approval": {
                "approval_principal_id": "principal_candidate",
                "approval_principal_kind": "interactive_user",
                "signing_key_id": "key_candidate",
                "policy_contract": {
                    "artifact_id": ART,
                    "payload_hash": HASH,
                },
                "policy_source_hash": None,
                "policy_image_hash": None,
                "policy_version": None,
                "revocation_authorities": (),
            },
        },
        "fixed_commit_authorization": {
            "artifact_id": ART,
            "payload_hash": HASH,
        },
        "sealed_e2e_signing_authorities": (
            {
                "principal_kind": "candidate_worker",
                "principal_id": "principal_candidate",
                "signing_key_id": "key_candidate",
            },
            {
                "principal_kind": "comparator",
                "principal_id": "principal_comparator",
                "signing_key_id": "key_comparator",
            },
            {
                "principal_kind": "coordinator",
                "principal_id": "principal_coordinator",
                "signing_key_id": "key_coordinator",
            },
            {
                "principal_kind": "evaluator",
                "principal_id": "principal_evaluator",
                "signing_key_id": "key_evaluator",
            },
            {
                "principal_kind": "gold_worker",
                "principal_id": "principal_gold",
                "signing_key_id": "key_gold",
            },
        ),
        "sealed_worker_authorizations": (
            {
                "worker_kind": "candidate",
                "principal_id": "principal_candidate",
                "job_manifest": {
                    "artifact_id": "artifact_0" + "0" * 62 + "1",
                    "payload_hash": HASH,
                },
                "fixed_commit_authorization": {
                    "artifact_id": "artifact_0" + "0" * 62 + "1",
                    "payload_hash": HASH,
                },
            },
            {
                "worker_kind": "comparator",
                "principal_id": "principal_comparator",
                "job_manifest": {
                    "artifact_id": "artifact_0" + "0" * 62 + "2",
                    "payload_hash": HASH,
                },
                "fixed_commit_authorization": {
                    "artifact_id": "artifact_0" + "0" * 62 + "2",
                    "payload_hash": HASH,
                },
            },
            {
                "worker_kind": "gold",
                "principal_id": "principal_gold",
                "job_manifest": {
                    "artifact_id": "artifact_0" + "0" * 62 + "3",
                    "payload_hash": HASH,
                },
                "fixed_commit_authorization": {
                    "artifact_id": "artifact_0" + "0" * 62 + "3",
                    "payload_hash": HASH,
                },
            },
        ),
        "created_at": "2026-01-01T00:00:00Z",
    }


def _build_contract():
    """Build a valid TaskContract from the raw dict."""
    from automarkov.contracts.task import validate_task_contract_for_approval

    return validate_task_contract_for_approval(_minimal_contract_dict())


def _build_manifest():
    """Build a valid RunManifest from the raw dict."""
    from automarkov.contracts.task import RunManifest

    return RunManifest.model_validate(_full_manifest_dict(), strict=True)


# ---------------------------------------------------------------------------
# PredicateVerdict tests
# ---------------------------------------------------------------------------

def test_predicate_verdict_valid_construction() -> None:
    v = PredicateVerdict(
        predicate_name="plan_closed",
        is_satisfied=True,
        detail="task contract is valid",
    )
    assert v.predicate_name == "plan_closed"
    assert v.is_satisfied is True
    assert v.detail == "task contract is valid"
    assert v.schema_version == "automarkov.predicate-verdict.v1"


def test_predicate_verdict_rejects_blank_detail() -> None:
    with pytest.raises(ValueError, match="nonblank"):
        PredicateVerdict(
            predicate_name="plan_closed",
            is_satisfied=True,
            detail="  ",
        )


def test_predicate_verdict_all_predicate_kinds() -> None:
    for kind in ALL_PREDICATE_KINDS:
        v = PredicateVerdict(
            predicate_name=kind,
            is_satisfied=False,
            detail=f"test {kind}",
        )
        assert v.predicate_name == kind


# ---------------------------------------------------------------------------
# FreezeGateReport tests
# ---------------------------------------------------------------------------

def test_freeze_gate_report_valid_construction() -> None:
    v = PredicateVerdict(
        predicate_name="plan_closed",
        is_satisfied=True,
        detail="ok",
    )
    report = FreezeGateReport(
        is_frozen=True,
        total_predicates=1,
        satisfied_count=1,
        missing_count=0,
        frozen_completeness=1.0,
        predicates=(v,),
        missing_fields=(),
        blocking_reasons=(),
    )
    assert report.is_frozen is True
    assert report.schema_version == "automarkov.freeze-gate-report.v1"


def test_freeze_gate_report_requires_nonempty_predicates() -> None:
    with pytest.raises(ValueError, match="at least one predicate"):
        FreezeGateReport(
            is_frozen=True,
            total_predicates=0,
            satisfied_count=0,
            missing_count=0,
            frozen_completeness=1.0,
            predicates=(),
            missing_fields=(),
            blocking_reasons=(),
        )


def test_freeze_gate_report_count_sum_mismatch() -> None:
    v = PredicateVerdict(
        predicate_name="plan_closed",
        is_satisfied=True,
        detail="ok",
    )
    with pytest.raises(ValueError, match="sum to total"):
        FreezeGateReport(
            is_frozen=True,
            total_predicates=1,
            satisfied_count=0,
            missing_count=0,
            frozen_completeness=0.0,
            predicates=(v,),
            missing_fields=(),
            blocking_reasons=(),
        )


def test_freeze_gate_report_completeness_out_of_range() -> None:
    v = PredicateVerdict(
        predicate_name="plan_closed",
        is_satisfied=True,
        detail="ok",
    )
    with pytest.raises(ValueError, match="completeness"):
        FreezeGateReport(
            is_frozen=True,
            total_predicates=1,
            satisfied_count=1,
            missing_count=0,
            frozen_completeness=1.5,
            predicates=(v,),
            missing_fields=(),
            blocking_reasons=(),
        )


def test_freeze_gate_report_is_frozen_false_when_missing() -> None:
    v = PredicateVerdict(
        predicate_name="plan_closed",
        is_satisfied=False,
        detail="not ok",
    )
    with pytest.raises(ValueError, match="is_frozen"):
        FreezeGateReport(
            is_frozen=True,
            total_predicates=1,
            satisfied_count=0,
            missing_count=1,
            frozen_completeness=0.0,
            predicates=(v,),
            missing_fields=("plan_closed",),
            blocking_reasons=("not ok",),
        )


def test_freeze_gate_report_nonunique_predicates() -> None:
    v = PredicateVerdict(
        predicate_name="plan_closed",
        is_satisfied=True,
        detail="ok",
    )
    with pytest.raises(ValueError, match="unique"):
        FreezeGateReport(
            is_frozen=True,
            total_predicates=2,
            satisfied_count=2,
            missing_count=0,
            frozen_completeness=1.0,
            predicates=(v, v),
            missing_fields=(),
            blocking_reasons=(),
        )


def test_freeze_gate_report_predicate_count_mismatch() -> None:
    v = PredicateVerdict(
        predicate_name="plan_closed",
        is_satisfied=True,
        detail="ok",
    )
    with pytest.raises(ValueError, match="predicate count"):
        FreezeGateReport(
            is_frozen=True,
            total_predicates=2,
            satisfied_count=2,
            missing_count=0,
            frozen_completeness=1.0,
            predicates=(v,),
            missing_fields=(),
            blocking_reasons=(),
        )


def test_freeze_gate_report_negative_counts_rejected() -> None:
    v = PredicateVerdict(
        predicate_name="plan_closed",
        is_satisfied=True,
        detail="ok",
    )
    with pytest.raises(ValueError, match="nonnegative"):
        FreezeGateReport(
            is_frozen=True,
            total_predicates=1,
            satisfied_count=-1,
            missing_count=0,
            frozen_completeness=0.0,
            predicates=(v,),
            missing_fields=(),
            blocking_reasons=(),
        )


def test_freeze_gate_report_frozen_completeness_cardinality() -> None:
    """All 15 predicates present with correct counts."""
    verdicts = tuple(
        PredicateVerdict(
            predicate_name=kind,
            is_satisfied=True,
            detail=f"ok {kind}",
        )
        for kind in ALL_PREDICATE_KINDS
    )
    report = FreezeGateReport(
        is_frozen=True,
        total_predicates=15,
        satisfied_count=15,
        missing_count=0,
        frozen_completeness=1.0,
        predicates=verdicts,
        missing_fields=(),
        blocking_reasons=(),
    )
    assert report.is_frozen is True
    assert len(report.predicates) == 15
    assert report.frozen_completeness == 1.0


# ---------------------------------------------------------------------------
# ExperimentPreflightReport tests
# ---------------------------------------------------------------------------

def test_preflight_report_valid_construction() -> None:
    verdicts = tuple(
        PredicateVerdict(
            predicate_name=kind,
            is_satisfied=True,
            detail=f"ok {kind}",
        )
        for kind in ALL_PREDICATE_KINDS
    )
    gate = FreezeGateReport(
        is_frozen=True,
        total_predicates=15,
        satisfied_count=15,
        missing_count=0,
        frozen_completeness=1.0,
        predicates=verdicts,
        missing_fields=(),
        blocking_reasons=(),
    )
    report = ExperimentPreflightReport(
        freeze_gate=gate,
        manifest_version_valid=True,
        e2e_authorities_consistent=True,
        all_signing_keys_bound=True,
        is_ready=True,
        blocking_reasons=(),
    )
    assert report.is_ready is True
    assert report.schema_version == "automarkov.experiment-preflight-report.v1"


def test_preflight_report_ready_false_when_gate_not_frozen() -> None:
    verdicts = tuple(
        PredicateVerdict(
            predicate_name=kind,
            is_satisfied=kind == "plan_closed",
            detail=f"ok {kind}",
        )
        for kind in ALL_PREDICATE_KINDS
    )
    gate = FreezeGateReport(
        is_frozen=False,
        total_predicates=15,
        satisfied_count=1,
        missing_count=14,
        frozen_completeness=1.0 / 15.0,
        predicates=verdicts,
        missing_fields=tuple(
            kind for kind in ALL_PREDICATE_KINDS if kind != "plan_closed"
        ),
        blocking_reasons=tuple(
            f"missing {kind}"
            for kind in ALL_PREDICATE_KINDS
            if kind != "plan_closed"
        ),
    )
    with pytest.raises(ValueError, match="is_ready"):
        ExperimentPreflightReport(
            freeze_gate=gate,
            manifest_version_valid=True,
            e2e_authorities_consistent=True,
            all_signing_keys_bound=True,
            is_ready=True,
            blocking_reasons=(),
        )


def test_preflight_report_ready_false_when_manifest_invalid() -> None:
    verdicts = tuple(
        PredicateVerdict(
            predicate_name=kind,
            is_satisfied=True,
            detail=f"ok {kind}",
        )
        for kind in ALL_PREDICATE_KINDS
    )
    gate = FreezeGateReport(
        is_frozen=True,
        total_predicates=15,
        satisfied_count=15,
        missing_count=0,
        frozen_completeness=1.0,
        predicates=verdicts,
        missing_fields=(),
        blocking_reasons=(),
    )
    with pytest.raises(ValueError, match="is_ready"):
        ExperimentPreflightReport(
            freeze_gate=gate,
            manifest_version_valid=False,
            e2e_authorities_consistent=True,
            all_signing_keys_bound=True,
            is_ready=True,
            blocking_reasons=(),
        )


def test_preflight_report_consistency() -> None:
    """is_ready must equal the conjunction of all sub-checks."""
    verdicts = tuple(
        PredicateVerdict(
            predicate_name=kind,
            is_satisfied=True,
            detail=f"ok {kind}",
        )
        for kind in ALL_PREDICATE_KINDS
    )
    gate = FreezeGateReport(
        is_frozen=True,
        total_predicates=15,
        satisfied_count=15,
        missing_count=0,
        frozen_completeness=1.0,
        predicates=verdicts,
        missing_fields=(),
        blocking_reasons=(),
    )
    # All sub-checks True but is_ready False -> should fail
    with pytest.raises(ValueError, match="is_ready"):
        ExperimentPreflightReport(
            freeze_gate=gate,
            manifest_version_valid=True,
            e2e_authorities_consistent=True,
            all_signing_keys_bound=True,
            is_ready=False,
            blocking_reasons=(),
        )


# ---------------------------------------------------------------------------
# FreezeGateChecker — _check_* methods
# ---------------------------------------------------------------------------

def test_check_plan_closed_valid() -> None:
    contract = _build_contract()
    manifest = _build_manifest()
    checker = FreezeGateChecker(contract, manifest, _full_auth())
    v = checker._check_plan_closed()
    assert v.predicate_name == "plan_closed"
    assert v.is_satisfied is True


def test_check_plan_closed_invalid_contract() -> None:
    from automarkov.contracts.task import TaskContract

    bad = _minimal_contract_dict()
    bad["evidence_and_assumptions"]["unresolved_questions"] = (
        {
            "question_id": "q_high",
            "severity": "high",
            "target_path": "/test",
            "question": "an open question",
        },
    )
    contract = TaskContract.model_validate(bad, strict=True)
    checker = FreezeGateChecker(contract, _build_manifest(), _full_auth())
    v = checker._check_plan_closed()
    assert v.is_satisfied is False


def test_check_source_commit_present_valid() -> None:
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    v = checker._check_source_commit_present()
    assert v.is_satisfied is True
    assert "present" in v.detail


def test_check_source_commit_present_missing() -> None:
    auth = SimpleNamespace(source_commit="")
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), auth,
    )
    v = checker._check_source_commit_present()
    assert v.is_satisfied is False


def test_check_profiles_present_valid() -> None:
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    v = checker._check_profiles_present()
    assert v.is_satisfied is True


def test_check_profiles_present_missing() -> None:
    auth = SimpleNamespace(profile_id="")
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), auth,
    )
    v = checker._check_profiles_present()
    assert v.is_satisfied is False


def test_check_task_cards_present_valid() -> None:
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    v = checker._check_task_cards_present()
    assert v.is_satisfied is True


def test_check_task_cards_present_missing_ref() -> None:
    """task_request with blank payload_hash should fail."""
    manifest_stub = SimpleNamespace(
        task_request=SimpleNamespace(artifact_id=ART, payload_hash=""),
    )
    checker = FreezeGateChecker(
        _build_contract(), manifest_stub, _full_auth(),  # type: ignore[arg-type]
    )
    v = checker._check_task_cards_present()
    assert v.is_satisfied is False


def test_check_methods_present_valid() -> None:
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    v = checker._check_methods_present()
    assert v.is_satisfied is True


def test_check_methods_present_missing() -> None:
    auth = SimpleNamespace(method_id="")
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), auth,
    )
    v = checker._check_methods_present()
    assert v.is_satisfied is False


def test_check_eligibility_present_valid() -> None:
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    v = checker._check_eligibility_present()
    assert v.is_satisfied is True


def test_check_eligibility_present_partial() -> None:
    auth = SimpleNamespace(suite_id="taxi_mdp", variant_id="")
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), auth,
    )
    v = checker._check_eligibility_present()
    assert v.is_satisfied is False


def test_check_pair_seed_ledger_present_valid() -> None:
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    v = checker._check_pair_seed_ledger_present()
    assert v.is_satisfied is True


def test_check_pair_seed_ledger_present_missing_pair() -> None:
    auth = SimpleNamespace(pair_id="", generation_seed=42)
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), auth,
    )
    v = checker._check_pair_seed_ledger_present()
    assert v.is_satisfied is False


def test_check_pair_seed_ledger_present_missing_seed() -> None:
    auth = SimpleNamespace(pair_id="pair_001", generation_seed=None)
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), auth,
    )
    v = checker._check_pair_seed_ledger_present()
    assert v.is_satisfied is False


def test_check_design_power_requires_typed_report() -> None:
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    v = checker._check_design_power_sufficient()
    assert v.is_satisfied is False
    assert "DesignPowerReport" in v.detail


def test_check_design_power_sufficient_unsupported_level() -> None:
    """An unsupported validation level string should fail."""
    from automarkov.contracts.task import TaskContract, TaskValidationTargetSpec

    contract = _build_contract()
    # Replace validation_target with a constructed one that has an
    # unsupported level, bypassing Literal validation.
    bad_target = TaskValidationTargetSpec.model_construct(
        required_level="unknown_level",
        required_properties=[],
        accepted_tolerances=[],
    )
    contract = TaskContract.model_construct(
        **{**contract.model_dump(), "validation_target": bad_target},
    )
    checker = FreezeGateChecker(
        contract, _build_manifest(), _full_auth(),
    )
    v = checker._check_design_power_sufficient()
    assert v.is_satisfied is False


def test_check_calibrations_require_typed_reports() -> None:
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    v = checker._check_calibrations_present()
    assert v.is_satisfied is False
    assert "GoldScoreCalibration" in v.detail


def test_check_calibrations_present_missing() -> None:
    auth = SimpleNamespace(track_id="")
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), auth,
    )
    v = checker._check_calibrations_present()
    assert v.is_satisfied is False


def test_check_keys_present_valid() -> None:
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    v = checker._check_keys_present()
    assert v.is_satisfied is True


def test_check_keys_present_missing_grant() -> None:
    auth = SimpleNamespace(runner_key_grant=None)
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), auth,
    )
    v = checker._check_keys_present()
    assert v.is_satisfied is False


def test_check_keys_present_blank_key() -> None:
    auth = SimpleNamespace(
        runner_key_grant=SimpleNamespace(signing_key_id=""),
    )
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), auth,
    )
    v = checker._check_keys_present()
    assert v.is_satisfied is False


def test_check_sealed_handshake_requires_verified_report() -> None:
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    v = checker._check_sealed_handshake_consistent()
    assert v.is_satisfied is False
    assert "handshake report" in v.detail


def test_check_sealed_handshake_consistent_no_authorities() -> None:
    """Empty signing authorities should cause the sealed handshake check to fail."""
    manifest_stub = SimpleNamespace(
        schema_version="automarkov.run-manifest.v2",
        manifest_kind="frozen_run",
        sealed_e2e_signing_authorities=(),
    )
    checker = FreezeGateChecker(
        _build_contract(), manifest_stub, _full_auth(),  # type: ignore[arg-type]
    )
    v = checker._check_sealed_handshake_consistent()
    assert v.is_satisfied is False


def test_check_runner_dry_run_ready_valid() -> None:
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    v = checker._check_runner_dry_run_ready()
    assert v.is_satisfied is True


def test_check_runner_dry_run_ready_wrong_schema() -> None:
    """Wrong schema_version on the manifest object should fail."""
    manifest_stub = SimpleNamespace(
        schema_version="automarkov.run-manifest.v1",
        manifest_kind="frozen_run",
        sealed_e2e_signing_authorities=(),
    )
    checker = FreezeGateChecker(
        _build_contract(), manifest_stub, _full_auth(),  # type: ignore[arg-type]
    )
    v = checker._check_runner_dry_run_ready()
    assert v.is_satisfied is False


def test_check_runner_dry_run_ready_wrong_kind() -> None:
    """Wrong manifest_kind on the manifest object should fail."""
    manifest_stub = SimpleNamespace(
        schema_version="automarkov.run-manifest.v2",
        manifest_kind="bootstrap",
        sealed_e2e_signing_authorities=(),
    )
    checker = FreezeGateChecker(
        _build_contract(), manifest_stub, _full_auth(),  # type: ignore[arg-type]
    )
    v = checker._check_runner_dry_run_ready()
    assert v.is_satisfied is False


def test_check_remote_env_vectors_require_verified_reports() -> None:
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    v = checker._check_remote_env_vectors_present()
    assert v.is_satisfied is False
    assert "RemoteEnv" in v.detail


def test_check_remote_env_vectors_present_missing() -> None:
    auth = SimpleNamespace(launch_deadline="")
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), auth,
    )
    v = checker._check_remote_env_vectors_present()
    assert v.is_satisfied is False


def test_check_remote_env_vectors_present_no_trailing_z() -> None:
    auth = SimpleNamespace(launch_deadline="2026-12-31T23:59:59")
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), auth,
    )
    v = checker._check_remote_env_vectors_present()
    assert v.is_satisfied is False


def test_check_analysis_fixtures_present_valid() -> None:
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    v = checker._check_analysis_fixtures_present()
    assert v.is_satisfied is True


def test_check_analysis_fixtures_present_empty() -> None:
    auth = SimpleNamespace(input_artifacts=())
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), auth,
    )
    v = checker._check_analysis_fixtures_present()
    assert v.is_satisfied is False


def test_check_replacement_policy_requires_typed_artifact() -> None:
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    v = checker._check_replacement_policy_present()
    assert v.is_satisfied is False
    assert "replacement-policy" in v.detail


def test_check_replacement_policy_present_missing() -> None:
    auth = SimpleNamespace(working_directory="")
    checker = FreezeGateChecker(
        _build_contract(), _build_manifest(), auth,
    )
    v = checker._check_replacement_policy_present()
    assert v.is_satisfied is False


# ---------------------------------------------------------------------------
# check_freeze_gate() integration
# ---------------------------------------------------------------------------

def test_check_freeze_gate_fails_closed_without_typed_evidence() -> None:
    report = check_freeze_gate(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    assert isinstance(report, FreezeGateReport)
    assert report.is_frozen is False
    assert report.total_predicates == 15
    assert report.satisfied_count == 10
    assert report.missing_count == 5
    assert report.frozen_completeness == 10 / 15
    assert len(report.predicates) == 15
    assert len(report.blocking_reasons) == 5
    assert len(report.missing_fields) == 5


def test_check_freeze_gate_partial_fail() -> None:
    """Empty authorization should fail most predicates."""
    auth = SimpleNamespace(
        source_commit="",
        profile_id="",
        method_id="",
        suite_id="",
        variant_id="",
        pair_id="",
        generation_seed=None,
        track_id="",
        runner_key_grant=None,
        launch_deadline="",
        input_artifacts=(),
        working_directory="",
    )
    report = check_freeze_gate(
        _build_contract(), _build_manifest(), auth,
    )
    assert report.is_frozen is False
    assert report.missing_count > 0
    assert report.satisfied_count + report.missing_count == report.total_predicates
    assert report.total_predicates == 15
    assert report.frozen_completeness < 1.0
    assert len(report.predicates) == 15
    assert len(report.blocking_reasons) > 0
    assert len(report.missing_fields) > 0


# ---------------------------------------------------------------------------
# preflight_experiment() integration
# ---------------------------------------------------------------------------

def test_preflight_experiment_fails_closed_without_typed_evidence() -> None:
    report = preflight_experiment(
        _build_contract(), _build_manifest(), _full_auth(),
    )
    assert isinstance(report, ExperimentPreflightReport)
    assert report.is_ready is False
    assert report.freeze_gate.is_frozen is False
    assert report.manifest_version_valid is True
    assert report.e2e_authorities_consistent is True
    assert report.all_signing_keys_bound is True
    assert len(report.blocking_reasons) == 5


def test_preflight_experiment_partial_fail() -> None:
    """Empty auth fields should cause partial failure with blocking reasons."""
    auth = SimpleNamespace(
        source_commit="",
        profile_id="",
        method_id="",
        suite_id="",
        variant_id="",
        pair_id="",
        generation_seed=None,
        track_id="",
        runner_key_grant=None,
        launch_deadline="",
        input_artifacts=(),
        working_directory="",
    )
    report = preflight_experiment(
        _build_contract(), _build_manifest(), auth,
    )
    assert report.is_ready is False
    assert len(report.blocking_reasons) > 0


def test_preflight_experiment_verbose() -> None:
    """Verbose: show all blocking_reasons for debugging."""
    auth = SimpleNamespace(
        source_commit="",
        profile_id="",
        method_id="",
        suite_id="",
        variant_id="",
        pair_id="",
        generation_seed=None,
        track_id="",
        runner_key_grant=None,
        launch_deadline="",
        input_artifact=(),
        working_directory="",
    )
    report = preflight_experiment(
        _build_contract(), _build_manifest(), auth,
    )
    assert report.is_ready is False
    # Gate blocking_reasons + additional preflight failures.
    assert len(report.blocking_reasons) >= 1
