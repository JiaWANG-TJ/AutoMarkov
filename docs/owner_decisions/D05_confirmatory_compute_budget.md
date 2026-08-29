# Decision D05

**Status**: OPEN
**Date Proposed**: 2026-08-25
**Decision Category**: Infrastructure / Compute Budget

## Context

Confirmatory experiments (ablation sweeps, seeded evaluations, and bootstrap confidence intervals) require non-trivial compute resources. The project needs a defined budget so that ticket owners can plan experiment runs without exceeding available infrastructure and so that CI pipelines can enforce resource limits. The budget must cover the full confirmatory surface: seed count, grid resolution, and statistical bootstrap depth.

## Recommended Option

Establish a moderate compute budget of 10 seeds x 360 cells x 100k bootstrap draws. This yields 3.6M bootstrap samples per experimental configuration, which is sufficient for tight confidence intervals on the primary metrics while remaining tractable on a single GPU node over a multi-hour CI window. The budget applies per ablation variant, so a four-variant sweep costs roughly 14.4M total bootstrap draws.

## Alternatives Considered

1. **1 seed x 360 cells x 500k bootstrap** — equivalent statistical power per cell but non-parallelizable and produces a single-point failure risk.
2. **10 seeds x 360 cells x 1M bootstrap** — tighter intervals but ten times the compute cost, exceeding current CI resource quotas.
3. **5 seeds x 720 cells x 100k bootstrap** — finer grid but lower per-cell seed coverage, weakening variance estimation.

## Dependencies

This decision blocks R23 (ablation sweep execution) and R26 (bootstrap CI pipeline). Both tickets must reference this budget in their resource requests.

## Owner Action Required

Owner must approve the 10x360x100k budget and confirm the CI resource quota that will enforce it.