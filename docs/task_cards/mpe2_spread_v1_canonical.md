---
suite_id: "mpe2_spread"
variant_id: "v1_canonical"
task_description: "standard spread task with canonical text ordering"
allowed_sources: ["direct_api", "paper_spec"]
expected_gold: "fully specified MDP/POMDP with known parameters"
semantics_review_status: "PENDING"
---

## Semantics Contract

Canonical MPE2 Spread Communication Task task expressed in v1 canonical form.
All transition, observation, and reward semantics must match the reference specification exactly.

## Expected Evidence

- Primary source: official API or paper specification for mpe2_spread.
- Secondary source: reference implementation or test harness.
- Evidence must cover state space, action space, transition probabilities, observation model, and reward function.

## Gold Method Specification

Gold method for mpe2_spread (v1_canonical): fully specified MDP/POMDP with known parameters.
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
