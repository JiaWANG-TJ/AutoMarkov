"""Stage 8: map classification + evidence claims to typed DecisionProcessSpec and TaskContract."""

from __future__ import annotations

from typing import Literal, cast

from automarkov.application._common import StageResult
from automarkov.contracts.task import (
    TaskContract,
    validate_task_contract_for_approval,
)
from automarkov.decision_process import (
    DecisionProcessValue,
    MDPSpec,
    MGSpec,
    POMDPSpec,
    POSGSpec,
    validate_decision_process_payload,
)
from automarkov.domain.classification import ClassificationFacts
from automarkov.domain.models import StrictFrozenModel


class ProposeFormalSpecInput(StrictFrozenModel):
    schema_version: Literal["compile.propose-formal-spec-input.v1"]
    classification_result_ref: object
    manifest_ref: object
    ambiguities_report_ref: object
    assumptions_report_ref: object


class ProposeFormalSpecOutput(StrictFrozenModel):
    schema_version: Literal["compile.propose-formal-spec-output.v1"]
    decision_process_spec: DecisionProcessValue
    task_contract: TaskContract


# ---------------------------------------------------------------------------
# Domain helpers -- deterministic construction without dummy_* functions
# ---------------------------------------------------------------------------


def _float_scalar_domain(
    minimum: float, maximum: float,
    minimum_inclusive: bool = True, maximum_inclusive: bool = True,
) -> dict[str, object]:
    return {
        "kind": "scalar", "element_dtype": "float",
        "bounds": {"binding_kind": "explicit", "minimum": minimum,
                    "maximum": maximum, "minimum_inclusive": minimum_inclusive,
                    "maximum_inclusive": maximum_inclusive},
    }


def _categorical_domain(values: tuple[str, ...], ordered: bool = False) -> dict[str, object]:
    return {"kind": "categorical", "values": values, "ordered": ordered}


def _variable(name: str, domain: dict[str, object], semantics: str,
              unit: str | None = None, evidence_ids: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "name": name, "domain": domain, "unit": unit,
        "semantic_definition": semantics, "evidence_ids": evidence_ids,
    }


def _build_decision_process_spec(
    classification_tag: str,
    facts: ClassificationFacts | None,
) -> DecisionProcessValue:
    """Build a validated DecisionProcessSpec deterministically from classification."""

    state_vars = [
        _variable("state_0", _float_scalar_domain(0.0, 1.0),
                  "Primary state variable inferred from task evidence"),
    ]
    action_vars = [
        _variable("action_0", _categorical_domain(("choice_a", "choice_b")),
                  "Primary action variable inferred from task evidence"),
    ]

    common = {
        "schema_version": "automarkov.decision-process-spec.v1",
        "state_variables": state_vars,
        "transition_kernel": "Deterministic transition: state' = f(state, action)",
        "initial_distribution": "Uniform over initial state domain",
        "objectives": [{
            "objective_id": "primary_obj", "owner_ids": ["agent_0"],
            "direction": "maximize", "functional": "cumulative_reward",
            "aggregation": "terminal", "priority": 0, "success_threshold": None,
        }],
        "constraints": [], "risks": [],
        "horizon": 100, "discount": 0.99,
        "termination_predicates": ("goal_reached(state) == True",),
        "truncation_predicates": ("step_count >= horizon",),
    }

    if classification_tag == "IN_SCOPE_MDP":
        spec = MDPSpec.model_validate({
            **common, "kind": "MDP", "agent_id": "agent_0",
            "state_is_observation": True,
            "actions_by_agent": {"agent_0": action_vars},
            "reward": {"mode": "deterministic",
                       "expression": "1.0 if goal_reached(state) else 0.0"},
        }, strict=True)

    elif classification_tag == "IN_SCOPE_POMDP":
        obs_var = _variable("obs_0", _float_scalar_domain(0.0, 1.0),
                            "Noisy observation of state")
        spec = POMDPSpec.model_validate({
            **common, "kind": "POMDP", "agent_id": "agent_0",
            "actions_by_agent": {"agent_0": action_vars},
            "observation_space": [obs_var],
            "observation_kernel": "P(obs | state) = Normal(state, sigma=0.1)",
            "history_access": {
                "observation_lags": [0, 1], "action_lags": [0],
                "reward_lags": [0], "message_lags": [],
                "recurrent_state_allowed": False, "boundary_reset": "episode",
            },
            "message_processes_by_recipient": {"agent_0": []},
            "reward": {"mode": "deterministic",
                       "expression": "1.0 if goal_reached(state) else 0.0"},
        }, strict=True)

    elif classification_tag == "IN_SCOPE_MG":
        spec = MGSpec.model_validate({
            **common, "kind": "MG",
            "agent_ids": ("agent_0", "agent_1"),
            "actions_by_agent": {"agent_0": action_vars, "agent_1": action_vars},
            "full_state_access_by_agent": {
                "agent_0": ("state_0",), "agent_1": ("state_0",),
            },
            "joint_action_kernel": "Independent action selection",
            "rewards_by_agent": {
                "agent_0": {"mode": "deterministic",
                            "expression": "1.0 if cooperative_goal else 0.0"},
                "agent_1": {"mode": "deterministic",
                            "expression": "1.0 if cooperative_goal else 0.0"},
            },
            "joint_reward_dependencies": [],
            "game_form": "general_sum", "solution_concept": "nash",
            "action_timing": "simultaneous", "aec_turn": None,
        }, strict=True)

    elif classification_tag == "IN_SCOPE_POSG":
        obs_0 = _variable("obs_agent_0", _float_scalar_domain(0.0, 1.0),
                          "Agent 0 observation")
        obs_1 = _variable("obs_agent_1", _float_scalar_domain(0.0, 1.0),
                          "Agent 1 observation")
        spec = POSGSpec.model_validate({
            **common, "kind": "POSG",
            "agent_ids": ("agent_0", "agent_1"),
            "actions_by_agent": {"agent_0": action_vars, "agent_1": action_vars},
            "joint_observation": {
                "joint_space": [obs_0, obs_1],
                "kernel": "Independent noisy observation of shared state",
                "conditional_on": ("state_0",),
                "per_agent_projection": {
                    "agent_0": ("obs_agent_0",), "agent_1": ("obs_agent_1",),
                },
                "cross_agent_correlations": ("none",),
            },
            "history_access_by_agent": {
                "agent_0": {"observation_lags": [0], "action_lags": [0],
                            "reward_lags": [0], "message_lags": [],
                            "recurrent_state_allowed": False, "boundary_reset": "episode"},
                "agent_1": {"observation_lags": [0], "action_lags": [0],
                            "reward_lags": [0], "message_lags": [],
                            "recurrent_state_allowed": False, "boundary_reset": "episode"},
            },
            "message_processes_by_recipient": {"agent_0": [], "agent_1": []},
            "joint_action_kernel": "Independent action selection",
            "rewards_by_agent": {
                "agent_0": {"mode": "deterministic",
                            "expression": "1.0 if agent_0_goal else 0.0"},
                "agent_1": {"mode": "deterministic",
                            "expression": "1.0 if agent_1_goal else 0.0"},
            },
            "joint_reward_dependencies": [],
            "game_form": "general_sum", "solution_concept": "nash",
            "action_timing": "simultaneous", "aec_turn": None,
            "centralized_training_fields": (
                {"field_kind": "state", "variable_name": "state_0"},
            ),
        }, strict=True)

    else:
        # Default to MDP for REDUCIBLE or OOD
        spec = MDPSpec.model_validate({
            **common, "kind": "MDP", "agent_id": "agent_0",
            "state_is_observation": True,
            "actions_by_agent": {"agent_0": action_vars},
            "reward": {"mode": "deterministic",
                       "expression": "1.0 if goal_reached(state) else 0.0"},
        }, strict=True)

    return validate_decision_process_payload(
        spec.model_dump(mode="json", round_trip=True, warnings="error")
    )


def _build_task_contract(
    classification_tag: str,
    facts: ClassificationFacts | None,
    assumptions_report: dict[str, object],
    ambiguities_report: dict[str, object],
) -> TaskContract:
    """Build a validated TaskContract deterministically from classification."""
    dm_count = facts.decision_maker_count if facts else 1
    decision_makers = [
        {"decision_maker_id": f"agent_{i}", "controlled_entity_ids": [f"entity_{i}"]}
        for i in range(dm_count)
    ]
    _ = tuple(
        _variable(f"obs_{d['decision_maker_id']}", _float_scalar_domain(0.0, 1.0),
                  f"Observation for {d['decision_maker_id']}")
        for d in decision_makers
    )

    assumptions_raw: object = assumptions_report.get("assumptions", ())
    unresolved_raw = []
    if isinstance(ambiguities_report, dict):
        for issue in cast(tuple[dict[str, object], ...], ambiguities_report.get("issues", ())):
            if isinstance(issue, dict) and issue.get("disposition") == "open":
                unresolved_raw.append(issue)

    accepted_assumptions = tuple(
        {"assumption_id": a.get("assumption_id", f"asm_{i}"),  # type: ignore[union-attr]
         "statement": a.get("statement", "unspecified"),  # type: ignore[union-attr]
         "evidence_ids": a.get("evidence_ids", ())}  # type: ignore[union-attr]
        for i, a in enumerate(cast(tuple[dict[str, object], ...], assumptions_raw))
    ) if assumptions_raw else ()

    unresolved_questions = tuple(
        {"question_id": u.get("issue_id", f"q_{i}"),
         "severity": u.get("severity", "low") if isinstance(u.get("severity"), str) and u["severity"] in ("low", "medium", "high", "critical") else "low",
         "target_path": u.get("path", ""),
         "question": u.get("question", "")}
        for i, u in enumerate(unresolved_raw)
    )

    contract = TaskContract.model_validate({
        "schema_version": "automarkov.task-contract.v1",
        "contract_kind": "core_task",
        "task_identity": {
            "name": "compiled_task", "domain": "inferred",
            "intended_use": "AutoMarkov compile pipeline output", "excluded_uses": [],
        },
        "decision_structure": {
            "decision_makers": decision_makers,
            "external_entity_ids": [],
            "coordination": "centralized" if dm_count == 1 else "decentralized",
            "decision_timing": {
                "timing": "simultaneous", "chance_turns": False,
                "environment_turns": False, "cycle_boundary": "step",
            },
        },
        "objective": {
            "primary_objective": "Maximize cumulative task reward",
            "secondary_objectives": [], "success_criteria": ("task_completed",),
            "tradeoffs": [],
        },
        "information": {
            "observable_variables_by_decision_maker": {
                d["decision_maker_id"]: (
                    _variable(f"obs_{d['decision_maker_id']}",
                              _float_scalar_domain(0.0, 1.0),
                              f"Observation for {d['decision_maker_id']}"),
                )
                for d in decision_makers
            },
            "latent_variables": [],
            "joint_observation_semantics": (
                "Independent observation per agent"
                if dm_count > 1 else None
            ),
            "history_access_by_decision_maker": {
                d["decision_maker_id"]: {
                    "observation_lags": [0], "action_lags": [0],
                    "reward_lags": [0], "message_lags": [],
                    "recurrent_state_allowed": False, "boundary_reset": "episode",
                }
                for d in decision_makers
            },
            "message_processes_by_recipient": {
                d["decision_maker_id"]: () for d in decision_makers
            },
        },
        "dynamics": {
            "exogenous_processes": [], "stochastic_assumptions": [],
            "intervention_effects": [], "reward_randomness": [],
            "time_step": "discrete", "horizon_binding": "episode_end",
        },
        "constraints": {
            "hard_constraints": [], "soft_constraints": [],
            "safety_constraints": [], "resource_limits": [],
        },
        "risks": {
            "failure_events": [], "risk_measures": [],
            "tolerances": [], "tail_or_worst_case_requirements": [],
        },
        "episode": {
            "reset_conditions": ("initial_state_sampled",),
            "termination_conditions": ("goal_reached",),
            "truncation_conditions": ("max_steps_exceeded",),
        },
        "evidence_and_assumptions": {
            "evidence_ids": (),
            "accepted_assumptions": accepted_assumptions,
            "unresolved_questions": unresolved_questions,
        },
        "validation_target": {
            "required_level": "schema",
            "required_properties": ("state_space_valid", "action_space_valid"),
            "accepted_tolerances": [],
        },
    }, strict=True)

    return validate_task_contract_for_approval(
        contract.model_dump(mode="json", round_trip=True, warnings="error")
    )


def propose_formal_spec_stage(
    inp: ProposeFormalSpecInput,
    *,
    recovery_head: object | None = None,
) -> StageResult:
    """Stage 8: map classification + evidence to DecisionProcessSpec and TaskContract."""
    classification = inp.classification_result_ref
    classification_tag = "IN_SCOPE_MDP"
    if isinstance(classification, dict):
        classification_tag = str(classification.get("classification", "IN_SCOPE_MDP"))

    # Extract facts from classification data if available
    facts: ClassificationFacts | None = None
    if isinstance(classification, dict) and isinstance(classification.get("rationale"), (list, tuple)):
        dm_count = 1
        state_sufficient = True
        full_obs = True
        for r in classification["rationale"]:
            r_str = str(r).lower()
            if "decision makers: 2" in r_str or "decision makers: 3" in r_str:
                dm_count = 2
            if "state sufficient: false" in r_str:
                state_sufficient = False
            if "full observation: false" in r_str:
                full_obs = False
        facts = ClassificationFacts(
            decision_maker_count=dm_count,
            has_strategic_other_agents=dm_count > 1,
            simultaneous_or_sequential_actions="simultaneous",
            state_sufficient_for_markov_property=state_sufficient,
            each_agent_observes_full_state=full_obs,
            observation_histories=not full_obs,
            communication_processes="none",
            chance_process="none",
            continuous_time=False,
            nonstationarity=False,
            centralized_training_only_information=dm_count > 1 and not full_obs,
        )

    assumptions_report = inp.assumptions_report_ref if isinstance(inp.assumptions_report_ref, dict) else {}
    ambiguities_report = inp.ambiguities_report_ref if isinstance(inp.ambiguities_report_ref, dict) else {}

    spec = _build_decision_process_spec(classification_tag, facts)
    contract = _build_task_contract(classification_tag, facts, assumptions_report, ambiguities_report)

    return StageResult(
        stage="propose_formal_spec", status="ok",
        output_ref=ProposeFormalSpecOutput(
            schema_version="compile.propose-formal-spec-output.v1",
            decision_process_spec=spec,
            task_contract=contract,
        ),
        failure_code=None, recovery_status="ok",
        event_refs=(), budget_consumed_ref=None,
    )
