"""Tests for automarkov.classification_facts module.

Covers ClassificationFacts, ClassificationProof, ClassificationOodHandoff, and
derive_decision_process_kind with all five classification rules plus edge cases.
"""

from __future__ import annotations

from typing import Literal

import pytest

from automarkov.domain.classification import (
    ClassificationFacts,
    ClassificationOodHandoff,
    ClassificationProof,
    derive_decision_process_kind,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_facts(
    *,
    decision_maker_count: int = 1,
    has_strategic_other_agents: bool = False,
    simultaneous_or_sequential_actions: Literal[
        "simultaneous", "sequential", "mixed"
    ] = "simultaneous",
    state_sufficient_for_markov_property: bool = True,
    each_agent_observes_full_state: bool = True,
    observation_histories: bool = False,
    communication_processes: Literal[
        "none", "broadcast", "point_to_point", "mixed"
    ] = "none",
    chance_process: Literal[
        "none", "stochastic", "deterministic"
    ] = "none",
    continuous_time: bool = False,
    nonstationarity: bool = False,
    centralized_training_only_information: bool = False,
) -> ClassificationFacts:
    """Return a valid ClassificationFacts with sensible defaults."""
    return ClassificationFacts(
        decision_maker_count=decision_maker_count,
        has_strategic_other_agents=has_strategic_other_agents,
        simultaneous_or_sequential_actions=simultaneous_or_sequential_actions,
        state_sufficient_for_markov_property=state_sufficient_for_markov_property,
        each_agent_observes_full_state=each_agent_observes_full_state,
        observation_histories=observation_histories,
        communication_processes=communication_processes,
        chance_process=chance_process,
        continuous_time=continuous_time,
        nonstationarity=nonstationarity,
        centralized_training_only_information=centralized_training_only_information,
    )


def _make_proof(
    *,
    facts: ClassificationFacts | None = None,
    derived_kind: Literal[
        "MDP", "POMDP", "MG", "POSG", "CLARIFICATION_REQUIRED"
    ] = "MDP",
    rule_id: str = "test_rule",
    rule_description: str = "A test rule.",
) -> ClassificationProof:
    """Return a valid ClassificationProof with sensible defaults."""
    return ClassificationProof(
        facts=facts or _make_facts(),
        derived_kind=derived_kind,
        rule_id=rule_id,
        rule_description=rule_description,
    )


def _make_handoff(
    *,
    question: str = "Is the time continuous?",
    ood_type: Literal[
        "continuous_time", "pddl", "openspiel", "unknown"
    ] = "continuous_time",
    evidence_ids: tuple[str, ...] = (),
    todo: str = "Determine if continuous-time formalism applies.",
) -> ClassificationOodHandoff:
    """Return a valid ClassificationOodHandoff with sensible defaults."""
    return ClassificationOodHandoff(
        question=question,
        ood_type=ood_type,
        evidence_ids=evidence_ids,
        todo=todo,
    )


# ===========================================================================
# ClassificationFacts
# ===========================================================================


class TestClassificationFactsConstruction:
    """ClassificationFacts: valid construction and field validators."""

    def test_valid_construction_with_defaults(self) -> None:
        facts = _make_facts()
        assert facts.decision_maker_count == 1
        assert facts.has_strategic_other_agents is False
        assert facts.simultaneous_or_sequential_actions == "simultaneous"
        assert facts.state_sufficient_for_markov_property is True
        assert facts.each_agent_observes_full_state is True
        assert facts.observation_histories is False
        assert facts.communication_processes == "none"
        assert facts.chance_process == "none"
        assert facts.continuous_time is False
        assert facts.nonstationarity is False
        assert facts.centralized_training_only_information is False

    def test_valid_multi_agent(self) -> None:
        facts = _make_facts(decision_maker_count=3)
        assert facts.decision_maker_count == 3

    def test_valid_literal_simultaneous(self) -> None:
        facts = _make_facts(simultaneous_or_sequential_actions="simultaneous")
        assert facts.simultaneous_or_sequential_actions == "simultaneous"

    def test_valid_literal_sequential(self) -> None:
        facts = _make_facts(simultaneous_or_sequential_actions="sequential")
        assert facts.simultaneous_or_sequential_actions == "sequential"

    def test_valid_literal_mixed(self) -> None:
        facts = _make_facts(simultaneous_or_sequential_actions="mixed")
        assert facts.simultaneous_or_sequential_actions == "mixed"

    def test_valid_communication_broadcast(self) -> None:
        facts = _make_facts(communication_processes="broadcast")
        assert facts.communication_processes == "broadcast"

    def test_valid_communication_point_to_point(self) -> None:
        facts = _make_facts(communication_processes="point_to_point")
        assert facts.communication_processes == "point_to_point"

    def test_valid_communication_mixed(self) -> None:
        facts = _make_facts(communication_processes="mixed")
        assert facts.communication_processes == "mixed"

    def test_valid_chance_stochastic(self) -> None:
        facts = _make_facts(chance_process="stochastic")
        assert facts.chance_process == "stochastic"

    def test_valid_chance_deterministic(self) -> None:
        facts = _make_facts(chance_process="deterministic")
        assert facts.chance_process == "deterministic"

    # -- negative: field validators ----------------------------------------------

    def test_decision_maker_count_below_minimum_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_facts(decision_maker_count=0)

    def test_invalid_simultaneous_or_sequential_literal_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_facts(simultaneous_or_sequential_actions="invalid")  # type: ignore[arg-type]

    def test_invalid_communication_processes_literal_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_facts(communication_processes="invalid")  # type: ignore[arg-type]

    def test_invalid_chance_process_literal_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_facts(chance_process="invalid")  # type: ignore[arg-type]

    def test_extra_field_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            ClassificationFacts(
                **{
                    **_make_facts().model_dump(),
                    "unknown_field": "value",
                },
            )

    def test_frozen_after_construction(self) -> None:
        facts = _make_facts()
        with pytest.raises((ValueError, TypeError)):
            facts.decision_maker_count = 99  # type: ignore[misc]


# ===========================================================================
# ClassificationProof
# ===========================================================================


class TestClassificationProofConstruction:
    """ClassificationProof: valid construction and field constraints."""

    def test_valid_construction(self) -> None:
        proof = _make_proof()
        assert proof.derived_kind == "MDP"
        assert proof.rule_id == "test_rule"
        assert proof.rule_description == "A test rule."
        assert isinstance(proof.facts, ClassificationFacts)

    def test_valid_all_derived_kinds(self) -> None:
        for kind in ("MDP", "POMDP", "MG", "POSG", "CLARIFICATION_REQUIRED"):
            proof = _make_proof(derived_kind=kind)
            assert proof.derived_kind == kind

    def test_rule_id_min_length_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_proof(rule_id="")

    def test_rule_id_max_length_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_proof(rule_id="x" * 129)

    def test_rule_description_min_length_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_proof(rule_description="")

    def test_rule_description_max_length_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_proof(rule_description="x" * 513)

    def test_invalid_derived_kind_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_proof(derived_kind="INVALID")  # type: ignore[arg-type]

    def test_frozen_after_construction(self) -> None:
        proof = _make_proof()
        with pytest.raises((ValueError, TypeError)):
            proof.derived_kind = "POMDP"  # type: ignore[misc]


# ===========================================================================
# ClassificationOodHandoff
# ===========================================================================


class TestClassificationOodHandoffConstruction:
    """ClassificationOodHandoff: valid construction."""

    def test_valid_construction(self) -> None:
        handoff = _make_handoff()
        assert handoff.question == "Is the time continuous?"
        assert handoff.ood_type == "continuous_time"
        assert handoff.evidence_ids == ()
        assert handoff.todo == "Determine if continuous-time formalism applies."

    def test_valid_all_ood_types(self) -> None:
        for ood_type in ("continuous_time", "pddl", "openspiel", "unknown"):
            handoff = _make_handoff(ood_type=ood_type)
            assert handoff.ood_type == ood_type

    def test_valid_with_evidence_ids(self) -> None:
        handoff = _make_handoff(evidence_ids=("ev_1", "ev_2"))
        assert handoff.evidence_ids == ("ev_1", "ev_2")

    def test_question_min_length_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_handoff(question="")

    def test_question_max_length_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_handoff(question="x" * 2049)

    def test_todo_min_length_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_handoff(todo="")

    def test_todo_max_length_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_handoff(todo="x" * 2049)

    def test_invalid_ood_type_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_handoff(ood_type="invalid")  # type: ignore[arg-type]

    def test_frozen_after_construction(self) -> None:
        handoff = _make_handoff()
        with pytest.raises((ValueError, TypeError)):
            handoff.question = "changed"  # type: ignore[misc]


# ===========================================================================
# derive_decision_process_kind -- all 5 rules
# ===========================================================================


class TestDeriveDecisionProcessKind:
    """derive_decision_process_kind() with all five classification rules."""

    # -- Rule 1: MDP -------------------------------------------------------

    def test_rule1_single_state_sufficient_no_obs_history_yields_mdp(self) -> None:
        facts = _make_facts(
            decision_maker_count=1,
            state_sufficient_for_markov_property=True,
            observation_histories=False,
        )
        proof = derive_decision_process_kind(facts)

        assert proof.derived_kind == "MDP"
        assert proof.rule_id == "single_state_sufficient_no_obs_history"
        assert "MDP" in proof.rule_description

    def test_rule1_mdp_ignores_strategic_flag(self) -> None:
        """MDP rule fires regardless of has_strategic_other_agents for single agent."""
        facts = _make_facts(
            decision_maker_count=1,
            has_strategic_other_agents=True,
            state_sufficient_for_markov_property=True,
            observation_histories=False,
        )
        proof = derive_decision_process_kind(facts)
        assert proof.derived_kind == "MDP"

    # -- Rule 2: POMDP -----------------------------------------------------

    def test_rule2_single_not_state_sufficient_yields_pomdp(self) -> None:
        facts = _make_facts(
            decision_maker_count=1,
            state_sufficient_for_markov_property=False,
        )
        proof = derive_decision_process_kind(facts)

        assert proof.derived_kind == "POMDP"
        assert proof.rule_id == "single_not_state_sufficient"
        assert "POMDP" in proof.rule_description

    def test_rule2_pomdp_even_with_obs_history(self) -> None:
        """POMDP rule matches whenever state is insufficient, even with obs histories."""
        facts = _make_facts(
            decision_maker_count=1,
            state_sufficient_for_markov_property=False,
            observation_histories=True,
        )
        proof = derive_decision_process_kind(facts)
        assert proof.derived_kind == "POMDP"

    # -- Rule 3: MG -------------------------------------------------------

    def test_rule3_multi_non_strategic_full_state_yields_mg(self) -> None:
        facts = _make_facts(
            decision_maker_count=2,
            has_strategic_other_agents=False,
            each_agent_observes_full_state=True,
        )
        proof = derive_decision_process_kind(facts)

        assert proof.derived_kind == "MG"
        assert proof.rule_id == "multi_non_strategic_full_state"
        assert "Markov Game" in proof.rule_description

    # -- Rule 4: POSG ------------------------------------------------------

    def test_rule4_strategic_yields_posg(self) -> None:
        facts = _make_facts(
            decision_maker_count=2,
            has_strategic_other_agents=True,
            each_agent_observes_full_state=True,
        )
        proof = derive_decision_process_kind(facts)

        assert proof.derived_kind == "POSG"
        assert proof.rule_id == "multi_strategic_or_partial_obs"
        assert "POSG" in proof.rule_description

    def test_rule4_partial_obs_yields_posg(self) -> None:
        facts = _make_facts(
            decision_maker_count=2,
            has_strategic_other_agents=False,
            each_agent_observes_full_state=False,
        )
        proof = derive_decision_process_kind(facts)

        assert proof.derived_kind == "POSG"

    def test_rule4_strategic_and_partial_obs_yields_posg(self) -> None:
        facts = _make_facts(
            decision_maker_count=3,
            has_strategic_other_agents=True,
            each_agent_observes_full_state=False,
        )
        proof = derive_decision_process_kind(facts)
        assert proof.derived_kind == "POSG"

    # -- Rule 5: CLARIFICATION_REQUIRED -------------------------------------------

    def test_rule5_single_state_sufficient_with_obs_history_yields_clarification(self) -> None:
        """Single agent, state sufficient, BUT obs history present -> clarification."""
        facts = _make_facts(
            decision_maker_count=1,
            state_sufficient_for_markov_property=True,
            observation_histories=True,
        )
        proof = derive_decision_process_kind(facts)

        assert proof.derived_kind == "CLARIFICATION_REQUIRED"
        assert proof.rule_id == "insufficient_facts"
        assert "additional evidence" in proof.rule_description

    def test_rule5_multi_full_state_strategic_matches_rule4(self) -> None:
        """Multi-agent, strategic, full state -> Rule 4 POSG (not Rule 5)."""
        facts = _make_facts(
            decision_maker_count=2,
            has_strategic_other_agents=True,
            each_agent_observes_full_state=True,
            observation_histories=True,
            state_sufficient_for_markov_property=True,
        )
        proof = derive_decision_process_kind(facts)
        # Rule 4 matches first: multi AND strategic -> POSG.
        assert proof.derived_kind == "POSG"

    # -- Proof delegation check ------------------------------------------------

    def test_proof_carries_facts_with_equal_values(self) -> None:
        facts = _make_facts()
        proof = derive_decision_process_kind(facts)
        assert proof.facts == facts
        assert proof.facts.decision_maker_count == facts.decision_maker_count


# ===========================================================================
# Edge cases: contradictory / unusual fact combinations
# ===========================================================================


class TestEdgeCases:
    """Edge cases with contradictory or unusual fact combinations."""

    def test_single_agent_multi_count_edge_case(self) -> None:
        """decision_maker_count > 1 but other flags set for single-agent MDP path.

        Rule 1 requires single (=1) so it must NOT match.
        Rule 3/4 require multi (>1) so they DO match.
        With non_strategic + full_state -> Rule 3 -> MG.
        """
        facts = _make_facts(
            decision_maker_count=2,
            has_strategic_other_agents=False,
            state_sufficient_for_markov_property=True,
            observation_histories=False,
            each_agent_observes_full_state=True,
        )
        proof = derive_decision_process_kind(facts)
        assert proof.derived_kind == "MG"

    def test_obs_history_blocks_mdp_even_if_all_else_matches(self) -> None:
        """observation_histories=True prevents Rule 1 MDP even if everything else aligns."""
        facts = _make_facts(
            decision_maker_count=1,
            state_sufficient_for_markov_property=True,
            observation_histories=True,
        )
        proof = derive_decision_process_kind(facts)
        assert proof.derived_kind == "CLARIFICATION_REQUIRED"

    def test_single_agent_not_state_sufficient_takes_priority_over_mg_rule(self) -> None:
        """Single agent (count=1) with state not sufficient -> POMDP, not MG.

        Even though has_strategic_other_agents is False and full_state would be
        True, single-agent + not state sufficient -> Rule 2 -> POMDP.
        """
        facts = _make_facts(
            decision_maker_count=1,
            state_sufficient_for_markov_property=False,
            has_strategic_other_agents=False,
            each_agent_observes_full_state=True,
        )
        proof = derive_decision_process_kind(facts)
        assert proof.derived_kind == "POMDP"

    def test_high_decision_maker_count(self) -> None:
        """Large decision_maker_count still classifies correctly."""
        facts = _make_facts(
            decision_maker_count=100,
            has_strategic_other_agents=False,
            each_agent_observes_full_state=True,
        )
        proof = derive_decision_process_kind(facts)
        assert proof.derived_kind == "MG"

    def test_all_flags_true_single_agent(self) -> None:
        """Single agent with all boolean flags True -> CLARIFICATION_REQUIRED.

        Rule 1 fails (obs_history=True).
        Rule 2 fails (state_sufficient=True).
        Falls through to CLARIFICATION_REQUIRED.
        """
        facts = _make_facts(
            decision_maker_count=1,
            has_strategic_other_agents=True,
            state_sufficient_for_markov_property=True,
            each_agent_observes_full_state=True,
            observation_histories=True,
        )
        proof = derive_decision_process_kind(facts)
        assert proof.derived_kind == "CLARIFICATION_REQUIRED"

    def test_all_flags_false_multi_agent(self) -> None:
        """Multi agent, all booleans False -> Rule 4 POSG.

        Rule 3 needs full_state=True but each_agent_observes_full_state=False.
        Rule 4 fires: multi AND (not full_state) -> POSG.
        """
        facts = _make_facts(
            decision_maker_count=2,
            has_strategic_other_agents=False,
            each_agent_observes_full_state=False,
            observation_histories=False,
            state_sufficient_for_markov_property=False,
        )
        proof = derive_decision_process_kind(facts)
        assert proof.derived_kind == "POSG"

    def test_barren_handoff_defaults(self) -> None:
        """Default OodHandoff with minimal construction."""
        handoff = ClassificationOodHandoff(
            question="Q?",
            ood_type="unknown",
            evidence_ids=(),
            todo="Look into it.",
        )
        assert handoff.ood_type == "unknown"
        assert handoff.evidence_ids == ()

    def test_proof_dump_roundtrip(self) -> None:
        """Proof survives a JSON dump/load round-trip."""
        proof = _make_proof()
        dumped = proof.model_dump(mode="json", round_trip=True)
        restored = ClassificationProof.model_validate(dumped)
        assert restored.derived_kind == proof.derived_kind
        assert restored.rule_id == proof.rule_id
        assert restored.facts.decision_maker_count == proof.facts.decision_maker_count