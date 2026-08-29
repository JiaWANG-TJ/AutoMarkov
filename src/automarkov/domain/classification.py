"""Deterministic decision-process classification rules.

Pure classification logic with no I/O and no LLM dependency.
All types are strict-frozen Pydantic models.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from automarkov.domain.models import StrictFrozenModel


class ClassificationFacts(StrictFrozenModel):
    """Observable facts about a decision process used for classification."""

    decision_maker_count: int = Field(ge=1, strict=True)
    has_strategic_other_agents: bool = Field(strict=True)
    simultaneous_or_sequential_actions: Literal[
        "simultaneous", "sequential", "mixed"
    ]
    state_sufficient_for_markov_property: bool = Field(strict=True)
    each_agent_observes_full_state: bool = Field(strict=True)
    observation_histories: bool = Field(strict=True)
    communication_processes: Literal["none", "broadcast", "point_to_point", "mixed"]
    chance_process: Literal["none", "stochastic", "deterministic"]
    continuous_time: bool = Field(strict=True)
    nonstationarity: bool = Field(strict=True)
    centralized_training_only_information: bool = Field(strict=True)


class ClassificationProof(StrictFrozenModel):
    """Proof that a classification rule matched."""

    facts: ClassificationFacts
    derived_kind: Literal["MDP", "POMDP", "MG", "POSG", "CLARIFICATION_REQUIRED"]
    rule_id: str = Field(strict=True, min_length=1, max_length=128)
    rule_description: str = Field(strict=True, min_length=1, max_length=512)


class ClassificationOodHandoff(StrictFrozenModel):
    """Handoff to the agent when classification requires out-of-domain routing."""

    question: str = Field(strict=True, min_length=1, max_length=2048)
    ood_type: Literal[
        "continuous_time", "pddl", "openspiel", "unknown"
    ]
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple, strict=True)
    todo: str = Field(strict=True, min_length=1, max_length=2048)


def derive_decision_process_kind(
    facts: ClassificationFacts,
) -> ClassificationProof:
    """Apply deterministic classification rules to observed facts.

    Rules (evaluated in order):
      1. Single agent + state sufficient + no observation histories  ->  MDP
      2. Single agent + state not sufficient               ->  POMDP
      3. Multi agent + not strategic + full state access  ->  MG
      4. Multi agent + strategic or not full state     ->  POSG
      5. Any missing-capability flags                 ->  CLARIFICATION_REQUIRED
    """

    single = facts.decision_maker_count == 1
    multi = facts.decision_maker_count > 1
    state_sufficient = facts.state_sufficient_for_markov_property
    no_obs_histories = not facts.observation_histories

    # Rule 1: classic single-agent fully-observed MDP
    if single and state_sufficient and no_obs_histories:
        return ClassificationProof(
            facts=facts,
            derived_kind="MDP",
            rule_id="single_state_sufficient_no_obs_history",
            rule_description=(
                "Single decision-maker with state sufficient for the Markov property "
                "and no observation histories yields an MDP."
            ),
        )

    # Rule 2: single agent with partial observability
    if single and not state_sufficient:
        return ClassificationProof(
            facts=facts,
            derived_kind="POMDP",
            rule_id="single_not_state_sufficient",
            rule_description=(
                "Single decision-maker whose state is not sufficient for the "
                "Markov property yields a POMDP."
            ),
        )

    # Rule 3: multi-agent, non-strategic, full state access
    if multi and not facts.has_strategic_other_agents and facts.each_agent_observes_full_state:
        return ClassificationProof(
            facts=facts,
            derived_kind="MG",
            rule_id="multi_non_strategic_full_state",
            rule_description=(
                "Multiple non-strategic agents each observing the full state "
                "yields a Markov Game."
                ),
            )

    # Rule 4: multi-agent with strategic interaction or partial observability
    if multi and (facts.has_strategic_other_agents or not facts.each_agent_observes_full_state):
        return ClassificationProof(
            facts=facts,
            derived_kind="POSG",
            rule_id="multi_strategic_or_partial_obs",
            rule_description=(
                "Multiple agents with strategic other-agent modeling or "
                "partial state observability yields a POSG."
            ),
        )

    # Rule 5: facts are insufficient for a definitive classification
    return ClassificationProof(
        facts=facts,
        derived_kind="CLARIFICATION_REQUIRED",
        rule_id="insufficient_facts",
        rule_description=(
            "Observed facts are insufficient to derive a unique decision-process "
            "kind; additional evidence is required."
        ),
    )


__all__ = [
    "ClassificationFacts",
    "ClassificationOodHandoff",
    "ClassificationProof",
    "derive_decision_process_kind",
]