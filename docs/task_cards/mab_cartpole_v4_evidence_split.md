---
suite_id: "mab_cartpole"
variant_id: "v4_evidence_split"
task_description: "task split into evidence-bearing and judgment portions"
allowed_sources: ["direct_api", "paper_spec"]
expected_gold: "evidence-derived from fully specified reference"
semantics_review_status: "PENDING"
---

## Semantics Contract

Canonical Multi-Armed Bandit / CartPole task expressed in v4 evidence split form.
All transition, observation, and reward semantics must match the reference specification exactly.

## Expected Evidence

- Primary source: official API or paper specification for mab_cartpole.
- Secondary source: reference implementation or test harness.
- Evidence must cover state space, action space, transition probabilities, observation model, and reward function.

## Gold Method Specification

Gold method for mab_cartpole (v4_evidence_split): evidence-derived from fully specified reference.
Transitions are deterministic or stochastic per suite definition.
Observations follow the declared observation model.
Rewards follow the declared reward shaping.

## Review Status

| Field | Value |
|--------|--------|
| Semantics review | PENDING |
| Evidence coverage | PENDING |
| Gold alignment | PENDING |
| Clarification resolved | N/A |

---
