---
suite_id: "smacv2_posg"
variant_id: "v2_paraphrased"
task_description: "reworded POSG task preserving identical semantics"
allowed_sources: ["direct_api", "paper_spec"]
expected_gold: "fully specified POSG with known parameters"
semantics_review_status: "PENDING"
---

## Semantics Contract

Canonical SMACv2 POSG battle task expressed in v2 paraphrased form.
All transition, observation, and reward semantics must match the reference specification exactly.

## Expected Evidence

- Primary source: official API or paper specification for smacv2_posg.
- Secondary source: reference implementation or test harness.
- Evidence must cover state space, action space, transition probabilities, observation model, and reward function.

## Gold Method Specification

Gold method for smacv2_posg (v2_paraphrased): fully specified POSG with known parameters.
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
