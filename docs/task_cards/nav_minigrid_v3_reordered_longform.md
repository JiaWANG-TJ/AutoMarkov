---
suite_id: "nav_minigrid"
variant_id: "v3_reordered_longform"
task_description: "extended prose variant with sections rearranged"
allowed_sources: ["direct_api", "paper_spec"]
expected_gold: "fully specified MDP/POMDP with known parameters"
semantics_review_status: "PENDING"
---

## Semantics Contract

Canonical Navigation MiniGrid Environment task expressed in v3 reordered longform form.
All transition, observation, and reward semantics must match the reference specification exactly.

## Expected Evidence

- Primary source: official API or paper specification for nav_minigrid.
- Secondary source: reference implementation or test harness.
- Evidence must cover state space, action space, transition probabilities, observation model, and reward function.

## Gold Method Specification

Gold method for nav_minigrid (v3_reordered_longform): fully specified MDP/POMDP with known parameters.
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
