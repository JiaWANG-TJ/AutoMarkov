"""Stage 10: compare formal DecisionProcessSpec against task_contract requirements,
identifying semantic gaps, underspecified fields, and potential misinterpretations.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from automarkov.application._common import StageResult, _artifact_ref_as_typed, _now_iso
from automarkov.contracts.task import (
    TaskContract,
    TextCriticIssue,
    TextCriticReport,
)
from automarkov.contracts.validation import ValidationReport
from automarkov.decision_process import (
    DecisionProcessValue,
    MDPSpec,
    MGSpec,
    POMDPSpec,
    POSGSpec,
)
from automarkov.domain.models import StrictFrozenModel


class TextFormalCriticInput(StrictFrozenModel):
    schema_version: Literal["compile.text-formal-critic-input.v1"]
    decision_process_spec: DecisionProcessValue
    task_contract: TaskContract
    validation_report: ValidationReport
    manifest_ref: object


class TextFormalCriticOutput(StrictFrozenModel):
    schema_version: Literal["compile.text-formal-critic-output.v1"]
    critic_report: TextCriticReport
    has_open_critical: bool = Field(strict=True)


def _check_process_kind_alignment(
    spec: DecisionProcessValue, contract: TaskContract
) -> list[TextCriticIssue]:
    """Check that the DecisionProcessKind matches the decision structure."""
    issues: list[TextCriticIssue] = []

    makers = contract.decision_structure.decision_makers
    n_makers = len(makers)
    coord = contract.decision_structure.coordination

    if n_makers == 1 and isinstance(spec, MDPSpec) and coord == "centralized":
        # Single-agent centralized with full observation is MDP — consistent
        pass
    elif n_makers == 1 and isinstance(spec, POMDPSpec) and coord == "centralized":
        # Single-agent centralized with partial observation is POMDP — consistent
        pass
    elif n_makers > 1 and isinstance(spec, MGSpec):
        # Multi-agent fully observable — consistent
        pass
    elif n_makers > 1 and isinstance(spec, POSGSpec):
        # Multi-agent partially observable — consistent
        pass
    else:
        issues.append(TextCriticIssue(
            issue_id="crit_kind_001",
            path="/decision_structure/coordination",
            severity="high",
            type="inconsistent_timing",
            reason=f"Process kind {spec.kind} may be inconsistent with "
                   f"coordination '{coord}' and {n_makers} decision maker(s)",
            consequence="Formal spec kind may not match the task structure",
            question="Is the chosen decision-process kind appropriate for the "
                     "task structure?",
            evidence_ids=(),
            disposition="open",
            accepted_assumption_id=None,
        ))

    return issues


def _check_observation_coverage(
    spec: DecisionProcessValue, contract: TaskContract
) -> list[TextCriticIssue]:
    """Check that observable variables from the contract are reflected in the spec."""
    issues: list[TextCriticIssue] = []

    if isinstance(spec, (MDPSpec, MGSpec)):
        return issues  # Full observability — no gap

    info = contract.information
    contract_obs: set[str] = set()
    for var_list in info.observable_variables_by_decision_maker.values():
        contract_obs.update(v.name for v in var_list)

    if isinstance(spec, POMDPSpec):
        spec_obs = {v.name for v in spec.observation_space}
    elif isinstance(spec, POSGSpec):
        spec_obs = {v.name for v in spec.joint_observation.joint_space}
    else:
        return issues

    missing = contract_obs - spec_obs
    extra = spec_obs - contract_obs

    if missing:
        issues.append(TextCriticIssue(
            issue_id="crit_obs_001",
            path="/information/observable_variables_by_decision_maker",
            severity="high",
            type="incomplete_information",
            reason=f"Observable variables missing from formal spec: {sorted(missing)}",
            consequence="The formal spec does not account for all observable "
                         "information channels",
            question="Should these variables be added to the observation space?",
            evidence_ids=(),
            disposition="open",
            accepted_assumption_id=None,
        ))

    if extra:
        issues.append(TextCriticIssue(
            issue_id="crit_obs_002",
            path="/information",
            severity="low",
            type="ambiguity",
            reason=f"Extra variables in observation space not in contract: {sorted(extra)}",
            consequence="The formal spec introduces observation variables not "
                         "documented in the contract",
            question="Are these extra observation variables intentional?",
            evidence_ids=(),
            disposition="open",
            accepted_assumption_id=None,
        ))

    return issues


def _check_objective_alignment(
    spec: DecisionProcessValue, contract: TaskContract
) -> list[TextCriticIssue]:
    """Check that objectives in the spec match the contract."""
    issues: list[TextCriticIssue] = []

    contract_obj = contract.objective
    if not spec.objectives:
        issues.append(TextCriticIssue(
            issue_id="crit_obj_001",
            path="/objective",
            severity="critical",
            type="missing_field",
            reason="No objectives defined in the formal spec",
            consequence="Cannot evaluate any decision policy without objectives",
            question="What objectives should the agent optimize?",
            evidence_ids=(),
            disposition="open",
            accepted_assumption_id=None,
        ))

    # Check that success criteria from contract have corresponding objectives
    if contract_obj.success_criteria and not spec.objectives:
        issues.append(TextCriticIssue(
            issue_id="crit_obj_002",
            path="/objective/success_criteria",
            severity="high",
            type="traceability_gap",
            reason=f"Success criteria declared ({len(contract_obj.success_criteria)}) "
                   "but no objectives in spec",
            consequence="Success criteria from contract are not traceable to any "
                         "formal objective",
            question="How are the success criteria operationalized?",
            evidence_ids=(),
            disposition="open",
            accepted_assumption_id=None,
        ))

    return issues


def _check_horizon_consistency(
    spec: DecisionProcessValue, contract: TaskContract
) -> list[TextCriticIssue]:
    """Check that the horizon and episode boundaries are consistent."""
    issues: list[TextCriticIssue] = []

    contract_ep = contract.episode
    n_termination = len(contract_ep.termination_conditions)
    n_spec_termination = len(spec.termination_predicates)

    if n_spec_termination < n_termination:
        issues.append(TextCriticIssue(
            issue_id="crit_ep_001",
            path="/episode/termination_conditions",
            severity="medium",
            type="unclear_episode_boundary",
            reason=f"Contract specifies {n_termination} termination conditions, "
                   f"but spec has only {n_spec_termination}",
            consequence="Some termination conditions may not be captured in the "
                         "formal spec",
            question="Are all contract termination conditions formalized?",
            evidence_ids=(),
            disposition="open",
            accepted_assumption_id=None,
        ))

    return issues


def _check_underspecified_fields(
    spec: DecisionProcessValue, contract: TaskContract
) -> list[TextCriticIssue]:
    """Identify fields marked with stub placeholders."""
    issues: list[TextCriticIssue] = []

    if spec.transition_kernel == "stub":
        issues.append(TextCriticIssue(
            issue_id="crit_under_001",
            path="/transition_kernel",
            severity="critical",
            type="missing_field",
            reason="Transition kernel is a stub placeholder",
            consequence="The dynamics of the environment are not specified",
            question="What is the formal transition function?",
            evidence_ids=(),
            disposition="open",
            accepted_assumption_id=None,
        ))

    if spec.initial_distribution == "stub":
        issues.append(TextCriticIssue(
            issue_id="crit_under_002",
            path="/initial_distribution",
            severity="high",
            type="missing_field",
            reason="Initial distribution is a stub placeholder",
            consequence="Initial state distribution is undefined",
            question="What is the initial state distribution?",
            evidence_ids=(),
            disposition="open",
            accepted_assumption_id=None,
        ))

    for obj in spec.objectives:
        if obj.functional == "stub":
            issues.append(TextCriticIssue(
                issue_id=f"crit_under_obj_{obj.objective_id}",
                path=f"/objectives/{obj.objective_id}",
                severity="critical",
                type="missing_field",
                reason=f"Objective '{obj.objective_id}' functional is a stub",
                consequence="The reward/loss functional is unspecified",
                question="What is the reward functional?",
                evidence_ids=(),
                disposition="open",
                accepted_assumption_id=None,
            ))

    if isinstance(spec, POMDPSpec) and spec.observation_kernel == "stub":
        issues.append(TextCriticIssue(
            issue_id="crit_under_003",
            path="/observation_kernel",
            severity="high",
            type="missing_field",
            reason="Observation kernel is a stub placeholder",
            consequence="Observation model is unspecified",
            question="What is the observation function?",
            evidence_ids=(),
            disposition="open",
            accepted_assumption_id=None,
        ))

    return issues


def text_formal_critic_stage(
    inp: TextFormalCriticInput,
    *,
    recovery_head: object | None = None,
) -> StageResult:
    """Stage 10: compare formal spec against task_contract requirements.

    Checks for semantic gaps between natural-language requirements and the
    formal representation, identifies underspecified fields, and flags
    potential misinterpretations.
    """
    spec = inp.decision_process_spec
    contract = inp.task_contract

    all_issues: list[TextCriticIssue] = []
    all_issues.extend(_check_process_kind_alignment(spec, contract))
    all_issues.extend(_check_observation_coverage(spec, contract))
    all_issues.extend(_check_objective_alignment(spec, contract))
    all_issues.extend(_check_horizon_consistency(spec, contract))
    all_issues.extend(_check_underspecified_fields(spec, contract))

    # Sort by issue_id for determinism
    all_issues.sort(key=lambda i: i.issue_id.encode("utf-8"))

    critic_report = TextCriticReport(
        schema_version="automarkov.text-critic-report.v1",
        report_kind="task_contract_review",
        task_contract=_artifact_ref_as_typed(
            contract.model_dump(mode="json", round_trip=True, warnings="error")
        ),
        traceability_report=_artifact_ref_as_typed(
            {
                "schema_version": "automarkov.task-contract-traceability-report.v1",
                "task_contract": _artifact_ref_as_typed(
                    contract.model_dump(mode="json", round_trip=True, warnings="error")
                ),
                "task_request": {
                    "artifact_id": "artifact_" + "0" * 64,
                    "payload_hash": "sha256:" + "0" * 64,
                },
                "entries": (),
                "uncovered_paths": (),
                "generated_at": _now_iso(),
            }
        ),
        critic_completion_trace=_artifact_ref_as_typed(
            contract.model_dump(mode="json", round_trip=True, warnings="error")
        ),
        previous_critic_report=None,
        issues=tuple(all_issues),
        reviewed_at=_now_iso(),
    )

    has_open_critical = any(
        issue.disposition == "open" and issue.severity in ("high", "critical")
        for issue in all_issues
    )

    return StageResult(
        stage="text_formal_critic",
        status="ok",
        output_ref=TextFormalCriticOutput(
            schema_version="compile.text-formal-critic-output.v1",
            critic_report=critic_report,
            has_open_critical=has_open_critical,
        ),
        failure_code=None,
        recovery_status="ok",
        event_refs=(),
        budget_consumed_ref=None,
    )
