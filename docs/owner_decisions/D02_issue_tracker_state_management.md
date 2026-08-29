# Decision D02

**Status**: OPEN
**Date Proposed**: 2026-08-25
**Decision Category**: Process / Issue Tracker State Management

## Context

Tickets T18 through T27 were closed without acceptance evidence in the issue tracker. This violates the project's completion contract, which requires measurable evidence (test passage, CI green, or owner sign-off) before a ticket transitions to CLOSED. The absence of evidence suggests either an upstream tracker misconfiguration or a lack of enforcement in the issue lifecycle. We need to establish a governance layer that prevents tickets from reaching CLOSED without evidence.

## Recommended Option

Create a Recovery milestone that captures the affected tickets and enforces a re-verification gate. Each ticket in the Recovery milestone must pass a fresh evidence check (trailing CI status or explicit owner acknowledgement) before it is re-closed. The milestone itself tracks aggregate progress and serves as the authoritative record of what was recovered and when.

## Alternatives Considered

1. **Retroactively add acceptance comments to T18–T27** — addresses the record gap but does not prevent recurrence and may obscure the fact that evidence was missing at close time.
2. **Implement a CI webhook that blocks ticket closure** — stronger enforcement but requires tracker API integration that is out of scope for this recovery pass.
3. **Do nothing, treat T18–T27 as accepted** — normalizes the evidence gap and weakens the completion contract for future work.

## Dependencies

This decision blocks R01 (tracker state recovery). R01 must reference this decision's milestone ID and evidence format.

## Owner Action Required

Owner must create the Recovery milestone in the tracker, assign T18–T27 to it, and define the re-verification evidence format (test output, CI link, or owner comment).