# Decision D08

**Status**: OPEN
**Date Proposed**: 2026-08-25
**Decision Category**: Process / Review Governance

## Context

Task cards in the issue tracker currently lack an explicit reviewer assignment, which means implementation tickets can be merged without domain-specific scrutiny. For a project that blends reinforcement learning, Markov-game theory, and social simulation, blind spots in algorithmic correctness or experimental design are likely to slip through without a review gate. We need a reviewer policy that balances rigor with velocity.

## Recommended Option

Assign 2 independent domain reviewers to each task card. Reviewers are drawn from a rotating pool of contributors who have domain expertise in the ticket's primary area (RL, MAS, statistics, systems). Each reviewer performs one axis of the code-review skill (Standards or Spec) and signs off with a checklist entry. Both approvals are required before the ticket enters DONE.

## Alternatives Considered

1. **Single reviewer per card** — faster but exposes the project to single-point reviewer bias and misses domain-specific errors.
2. **Automated review only (CI + lint + tests)** — catches mechanical issues but cannot assess algorithmic soundness or experimental design quality.
3. **3 reviewers per card** — thorough but doubles review time and is unsustainable given the current contributor pool.

## Dependencies

This decision blocks R20 (review gate enforcement) and R22 (reviewer assignment automation). Both tickets must encode the 2-reviewer policy.

## Owner Action Required

Owner must approve the 2-reviewer policy, confirm the reviewer pool, and authorize R20 to enforce the gate in the tracker.