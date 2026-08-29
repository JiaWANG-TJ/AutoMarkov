"""Shared types, helpers, and the ``StageResult`` dataclass for compile stages.

Private module -- not part of the public application API.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, cast

from automarkov.contracts.task import (
    TaskContract,
    validate_task_contract_for_approval,
)
from automarkov.decision_process import (
    DecisionProcessValue,
    validate_decision_process_payload,
)
from automarkov.domain.canonical import canonical_json_bytes
from automarkov.domain.ids import ArtifactIdValue, Sha256Value
from automarkov.domain.models import StrictFrozenModel
from automarkov.lifecycle import ArtifactReference

# ---------------------------------------------------------------------------
# Stage identifiers
# ---------------------------------------------------------------------------

StageId = Literal[
    "validate_ingress", "create_manifest", "plan_evidence",
    "retrieve_evidence", "claim_evidence_graph", "identify_ambiguities",
    "classify_process_kind", "propose_formal_spec", "formal_validation",
    "text_formal_critic", "approval_gate", "select_route",
    "environment_candidate", "public_tests", "package_candidate",
    "terminal_cas",
]

_ALL_STAGES: tuple[StageId, ...] = (
    "validate_ingress", "create_manifest", "plan_evidence", "retrieve_evidence",
    "claim_evidence_graph", "identify_ambiguities", "classify_process_kind",
    "propose_formal_spec", "formal_validation", "text_formal_critic",
    "approval_gate", "select_route", "environment_candidate",
    "public_tests", "package_candidate", "terminal_cas",
)

FailureCode = Literal[
    "invalid_request", "manifest_creation_failed", "evidence_planning_failed",
    "evidence_retrieval_failed", "evidence_claim_failed",
    "ambiguity_analysis_failed", "classification_failed",
    "formal_proposal_failed", "formal_validation_failed",
    "critic_review_failed", "approval_rejected", "route_selection_failed",
    "environment_candidate_failed", "public_test_failed",
    "packaging_failed", "terminal_validation_failed",
    "budget_exhausted", "recovery_unavailable",
]

RecoveryStatus = Literal["ok", "recovered", "unrecoverable"]


# ---------------------------------------------------------------------------
# StageResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class StageResult:
    """Every stage function returns this value."""

    stage: StageId
    status: Literal["ok", "failed", "recovered"]
    output_ref: object | None
    failure_code: FailureCode | None
    recovery_status: RecoveryStatus
    event_refs: tuple[object, ...]
    budget_consumed_ref: object | None

    def __post_init__(self) -> None:
        if self.status == "failed" and self.failure_code is None:
            raise ValueError("failed stage must declare a failure_code")
        if self.status != "failed" and self.failure_code is not None:
            raise ValueError("non-failed stage must not declare a failure_code")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Canonical UTC-Z timestamp."""
    from datetime import UTC, datetime

    ms = int(datetime.now(tz=UTC).timestamp() * 1_000)
    ts = datetime.fromtimestamp(ms / 1_000, tz=UTC).isoformat(timespec="microseconds")
    return ts.removesuffix("+00:00").rstrip("0").rstrip(".") + "Z"


def _artifact_ref(
    data: dict[str, object] | StrictFrozenModel,
) -> dict[str, str]:
    """Compute a deterministic artifact reference (plain dict)."""
    if isinstance(data, StrictFrozenModel):
        data = data.model_dump(mode="json", round_trip=True, warnings="error")
    raw = _canonical_safe(data) if isinstance(data, dict) else canonical_json_bytes(data)
    h = sha256(raw).hexdigest()
    return {"artifact_id": f"artifact_{h}", "payload_hash": f"sha256:{h}"}  # type: ignore[return-value]


def _artifact_ref_as_typed(
    data: dict[str, object] | StrictFrozenModel,
) -> ArtifactReference:
    """Compute a deterministic artifact reference as ArtifactReference."""
    if isinstance(data, StrictFrozenModel):
        data = data.model_dump(mode="json", round_trip=True, warnings="error")
    raw = _canonical_safe(data) if isinstance(data, dict) else canonical_json_bytes(data)
    h = sha256(raw).hexdigest()
    return ArtifactReference(
        artifact_id=cast(ArtifactIdValue, f"artifact_{h}"),
        payload_hash=cast(Sha256Value, f"sha256:{h}"),
    )


def _classify_makers(coord: str, timing: str, msg_n: int) -> str:
    if coord == "centralized" and timing == "simultaneous" and msg_n == 0:
        return "IN_SCOPE_MDP"
    if coord == "centralized" and timing == "simultaneous":
        return "IN_SCOPE_MG"
    if coord in ("decentralized", "hybrid"):
        return "IN_SCOPE_POSG"
    return "IN_SCOPE_POMDP"


def _dummy_decision_process() -> DecisionProcessValue:
    """Build and validate a minimal MDP decision-process."""
    dom = {
        "kind": "scalar", "element_dtype": "float",
        "bounds": {"binding_kind": "explicit", "minimum": 0.0,
                    "maximum": 1.0, "minimum_inclusive": True,
                    "maximum_inclusive": True},
    }
    state_var = {"name": "s0", "domain": dom, "unit": None,
                  "semantic_definition": "stub", "evidence_ids": []}
    action_var = {"name": "a0", "domain": dom, "unit": None,
                   "semantic_definition": "stub", "evidence_ids": []}
    return validate_decision_process_payload({
        "schema_version": "automarkov.decision-process-spec.v1",
        "kind": "MDP",
        "state_variables": [state_var],
        "actions_by_agent": {"agent": [action_var]},
        "transition_kernel": "stub",
        "initial_distribution": "stub",
        "objectives": [{
            "objective_id": "stub_obj", "owner_ids": ["agent"],
            "direction": "maximize", "functional": "stub",
            "aggregation": "terminal", "priority": 0,
            "success_threshold": None,
        }],
        "constraints": [], "risks": [],
        "horizon": 1, "discount": 1.0,
        "termination_predicates": ["stub"],
        "truncation_predicates": [],
        "agent_id": "agent",
        "state_is_observation": True,
        "reward": {"mode": "deterministic", "expression": "stub"},
    })


_dummy_obs = {
    "name": "stub_obs", "unit": None,
    "semantic_definition": "stub", "evidence_ids": [],
    "domain": {"kind": "scalar", "element_dtype": "float",
               "bounds": {"binding_kind": "explicit", "minimum": 0.0,
                           "maximum": 1.0, "minimum_inclusive": True,
                           "maximum_inclusive": True}},
}

_dummy_hist = {
    "observation_lags": [], "action_lags": [], "reward_lags": [],
    "message_lags": [], "recurrent_state_allowed": False,
    "boundary_reset": "episode",
}


def _dummy_task_contract() -> TaskContract:
    """Build and validate a minimal task-contract."""
    return validate_task_contract_for_approval({
        "schema_version": "automarkov.task-contract.v1",
        "contract_kind": "core_task",
        "task_identity": {"name": "stub_task", "domain": "stub_domain",
                          "intended_use": "stub", "excluded_uses": []},
        "decision_structure": {"decision_makers": [
            {"decision_maker_id": "agent",
             "controlled_entity_ids": ["entity_0"]}],
            "external_entity_ids": [], "coordination": "centralized",
            "decision_timing": {"timing": "simultaneous",
                                "chance_turns": False, "environment_turns": False,
                                "cycle_boundary": "stub_boundary"}},
        "objective": {"primary_objective": "stub_objective",
                      "secondary_objectives": [], "success_criteria": ["stub_criterion"],
                      "tradeoffs": []},
        "information": {"observable_variables_by_decision_maker": {"agent": [_dummy_obs]},
                        "latent_variables": [], "joint_observation_semantics": None,
                        "history_access_by_decision_maker": {"agent": _dummy_hist},
                        "message_processes_by_recipient": {"agent": []}},
        "dynamics": {"exogenous_processes": [],
                     "stochastic_assumptions": [], "intervention_effects": [],
                     "reward_randomness": [], "time_step": "stub_step",
                     "horizon_binding": "stub_binding"},
        "constraints": {"hard_constraints": [], "soft_constraints": [],
                        "safety_constraints": [], "resource_limits": []},
        "risks": {"failure_events": [], "risk_measures": [],
                  "tolerances": [], "tail_or_worst_case_requirements": []},
        "episode": {"reset_conditions": ["stub_reset"],
                    "termination_conditions": ["stub_termination"],
                    "truncation_conditions": ["stub_truncation"]},
        "evidence_and_assumptions": {"evidence_ids": [],
                                     "accepted_assumptions": [], "unresolved_questions": []},
        "validation_target": {"required_level": "schema",
                              "required_properties": ["stub_property"],
                              "accepted_tolerances": []},
    })


def stage_index(stage: StageId) -> int:
    return _ALL_STAGES.index(stage)


def next_stage(stage: StageId) -> StageId | None:
    idx = stage_index(stage)
    if idx + 1 >= len(_ALL_STAGES):
        return None
    return _ALL_STAGES[idx + 1]


def _thaw_for_canonical(value: object) -> object:
    """Recursively convert tuples to lists so canonical_json_bytes accepts the dict."""
    if isinstance(value, tuple):
        return [_thaw_for_canonical(item) for item in value]
    if isinstance(value, dict):
        return {key: _thaw_for_canonical(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_thaw_for_canonical(item) for item in value]
    return value


def _canonical_safe(value: dict[str, object]) -> bytes:
    """canonical_json_bytes wrapper that first thaws tuples to lists."""
    return canonical_json_bytes(cast(dict[str, object], _thaw_for_canonical(value)))
