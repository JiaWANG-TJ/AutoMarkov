from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from automarkov.task_contracts import (
    TaskContractTraceabilityReport,
    TextCriticReport,
    task_contract_claim_paths,
    validate_task_contract_for_approval,
    validate_task_contract_review_gate,
)


def _contract() -> dict[str, object]:
    return {
        "schema_version": "automarkov.task-contract.v1",
        "contract_kind": "core_task",
        "task_identity": {
            "name": "Inventory control",
            "domain": "operations research",
            "intended_use": "replenishment policy evaluation",
            "excluded_uses": [],
        },
        "decision_structure": {
            "decision_makers": [
                {
                    "decision_maker_id": "agent_0",
                    "controlled_entity_ids": ["entity_0"],
                }
            ],
            "external_entity_ids": [],
            "coordination": "centralized",
            "decision_timing": {
                "timing": "simultaneous",
                "chance_turns": True,
                "environment_turns": True,
                "cycle_boundary": "one step",
            },
        },
        "objective": {
            "primary_objective": "minimize total cost",
            "secondary_objectives": [],
            "success_criteria": ["mean cost <= 10"],
            "tradeoffs": [],
        },
        "information": {
            "observable_variables_by_decision_maker": {
                "agent_0": [
                    {
                        "name": "inventory",
                        "domain": {
                            "kind": "scalar",
                            "element_dtype": "int",
                            "bounds": {
                                "binding_kind": "explicit",
                                "minimum": 0,
                                "maximum": 100,
                                "minimum_inclusive": True,
                                "maximum_inclusive": True,
                            },
                        },
                        "unit": "items",
                        "semantic_definition": "on-hand inventory",
                        "evidence_ids": ["E-user-1"],
                    }
                ]
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
                }
            },
            "message_processes_by_recipient": {"agent_0": []},
        },
        "dynamics": {
            "exogenous_processes": [],
            "stochastic_assumptions": [],
            "intervention_effects": [],
            "reward_randomness": [],
            "time_step": "1 step",
            "horizon_binding": "10 steps",
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
            "reset_conditions": ["new episode"],
            "termination_conditions": ["horizon reached"],
            "truncation_conditions": [],
        },
        "evidence_and_assumptions": {
            "evidence_ids": ["E-user-1"],
            "accepted_assumptions": [],
            "unresolved_questions": [],
        },
        "validation_target": {
            "required_level": "behavioral",
            "required_properties": ["api_contract"],
            "accepted_tolerances": [],
        },
    }


def test_task_contract_is_strict_deep_frozen_and_approval_ready() -> None:
    contract = validate_task_contract_for_approval(_contract())

    assert contract.task_identity.name == "Inventory control"
    assert tuple(contract.information.observable_variables_by_decision_maker) == (
        "agent_0",
    )
    with pytest.raises(ValidationError, match="frozen"):
        contract.task_identity.name = "mutated"
    with pytest.raises(TypeError):
        contract.information.observable_variables_by_decision_maker["agent_1"] = ()


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (lambda value: value.pop("task_identity"), "Field required"),
        (
            lambda value: value["task_identity"].__setitem__("unexpected", True),
            "Extra inputs are not permitted",
        ),
        (
            lambda value: value["information"][
                "observable_variables_by_decision_maker"
            ].__setitem__("agent_1", []),
            "keyset",
        ),
        (
            lambda value: value["evidence_and_assumptions"][
                "unresolved_questions"
            ].append(
                {
                    "question_id": "q1",
                    "severity": "high",
                    "target_path": "/objective/primary_objective",
                    "question": "Which cost definition applies?",
                }
            ),
            "block",
        ),
    ],
)
def test_task_contract_rejects_unclosed_or_unapprovable_payloads(
    mutator: Callable[[dict[str, Any]], object],
    expected: str,
) -> None:
    payload = copy.deepcopy(_contract())
    mutator(payload)
    with pytest.raises((ValueError, ValidationError), match=expected):
        validate_task_contract_for_approval(payload)


def test_task_contract_review_gate_rejects_open_high_issues_or_trace_gaps() -> None:
    contract = validate_task_contract_for_approval(_contract())
    contract_ref = {
        "artifact_id": "artifact_" + "1" * 64,
        "payload_hash": "sha256:" + "2" * 64,
    }
    trace_ref = {
        "artifact_id": "artifact_" + "3" * 64,
        "payload_hash": "sha256:" + "4" * 64,
    }
    trace_payload = {
        "schema_version": "automarkov.task-contract-traceability-report.v1",
        "task_contract": contract_ref,
        "task_request": {
            "artifact_id": "artifact_" + "5" * 64,
            "payload_hash": "sha256:" + "6" * 64,
        },
        "entries": [
            {
                "target_path": target_path,
                "source_kind": "task_request",
                "source_ids": ["request_t06"],
            }
            for target_path in task_contract_claim_paths(contract)
        ],
        "uncovered_paths": [],
        "generated_at": "2026-08-12T00:00:00Z",
    }
    critic_payload = {
        "schema_version": "automarkov.text-critic-report.v1",
        "report_kind": "task_contract_review",
        "task_contract": contract_ref,
        "traceability_report": trace_ref,
        "critic_completion_trace": {
            "artifact_id": "artifact_" + "7" * 64,
            "payload_hash": "sha256:" + "8" * 64,
        },
        "previous_critic_report": None,
        "issues": [],
        "reviewed_at": "2026-08-12T00:00:00Z",
    }
    trace = TaskContractTraceabilityReport.model_validate(trace_payload, strict=True)
    critic = TextCriticReport.model_validate(critic_payload, strict=True)

    validate_task_contract_review_gate(contract, trace, critic)

    missing_path = "/episode/reset_conditions/0"
    gapped_trace = TaskContractTraceabilityReport.model_validate(
        trace_payload
        | {
            "entries": [
                entry
                for entry in trace_payload["entries"]
                if entry["target_path"] != missing_path
            ],
            "uncovered_paths": [missing_path],
        },
        strict=True,
    )
    open_issue = {
        "issue_id": "issue_1",
        "path": "/objective/primary_objective",
        "severity": "high",
        "type": "ambiguity",
        "reason": "cost is ambiguous",
        "consequence": "policies are incomparable",
        "question": "Which cost applies?",
        "evidence_ids": ["E-user-1"],
        "disposition": "open",
        "accepted_assumption_id": None,
    }
    blocking_critic = TextCriticReport.model_validate(
        critic_payload | {"issues": [open_issue]}, strict=True
    )

    with pytest.raises(ValueError, match="uncovered"):
        validate_task_contract_review_gate(contract, gapped_trace, critic)
    with pytest.raises(ValueError, match="block"):
        validate_task_contract_review_gate(contract, trace, blocking_critic)
