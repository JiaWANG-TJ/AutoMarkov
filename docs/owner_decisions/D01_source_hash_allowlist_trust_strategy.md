# Decision D01

**Status**: OPEN
**Date Proposed**: 2026-08-25
**Decision Category**: Security / Trust Strategy

## Context

Provenance validation now operates through typed ingress combined with a Git-tree–based trust cache. The current source-hash allowlist mechanism was introduced as an interim integrity layer before typed ingress was available. With typed ingress policies in place, the allowlist overlaps partially with the typed policy checks, creating maintenance burden and duplicate verification logic. We need to decide whether the allowlist should be retired, retained as a read-through cache, or kept as a parallel enforcement.

## Recommended Option

Keep the current source-hash approach as an integrity cache and formalize ingress as the primary typed policy layer. Under this model, typed ingress performs canonical validation at the boundary, while the allowlist remains as a fast-path cache that short-circuits repeated hash lookups for already-trusted sources. This preserves the existing allowlist code path (avoiding a breaking change) while making typed ingress the single source of truth for new trust decisions.

## Alternatives Considered

1. **Remove allowlist entirely** — simplifies the trust surface but forces every ingress decision through the full typed-policy path, increasing latency for known-good sources and requiring a larger migration.
2. **Promote allowlist to primary trust authority** — degrades typed ingress to advisory status, which contradicts the current architecture and loses schema-validated trust decisions.
3. **Merge allowlist into typed ingress schema** — eliminates the parallel path but requires a schema migration and risks breaking existing callers that depend on the allowlist API.

## Dependencies

This decision blocks ticket R02 (trust cache integration). R02 must align its trust cache read/write path with whichever strategy is chosen here.

## Owner Action Required

Owner must confirm the dual-layer approach (typed ingress primary, allowlist cache) and approve the schema notation that will be used to bind the two layers together.