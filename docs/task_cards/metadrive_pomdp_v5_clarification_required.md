---
suite_id: "metadrive_pomdp"
variant_id: "v5_clarification_required"
task_description: "task requiring clarification of underspecified sensor model"
allowed_sources: ["direct_api", "paper_spec"]
expected_gold: "clarified specification resolving underspecified dynamics"
semantics_review_status: "PENDING"
---

## Semantics Contract

Canonical MetaDrive Partially Observable Driving task expressed in v5 clarification required form.
All transition, observation, and reward semantics must match the reference specification exactly.

## Expected Evidence

- Primary source: official API or paper specification for metadrive_pomdp.
- Secondary source: reference implementation or test harness.
- Evidence must cover state space, action space, transition probabilities, observation model, and reward function.

## Gold Method Specification

Gold method for metadrive_pomdp (v5_clarification_required): clarified specification resolving underspecified dynamics.
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
