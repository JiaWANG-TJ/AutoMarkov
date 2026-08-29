"""Run lifecycle coordination module.

Handles run creation, planning, recovery, and resume via
a compact state machine with idempotent recovery from event head.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from automarkov.domain.models import (
    StrictFrozenModel,
    VerifiedEventHead,
)
from automarkov.lifecycle import RunIdValue

# ---------------------------------------------------------------------------
# Run phase identifiers
# ---------------------------------------------------------------------------

RunPhase = Literal[
    "idle",
    "planning",
    "executing",
    "recovering",
    "completed",
    "failed",
    "cancelled",
]

_RUN_PHASE_ORDER: tuple[RunPhase, ...] = (
    "idle",
    "planning",
    "executing",
    "recovering",
    "completed",
    "failed",
    "cancelled",
)

_TERMINAL_PHASES: frozenset[RunPhase] = frozenset(
    {"completed", "failed", "cancelled"}
)

_RECOVERY_PHASES: frozenset[RunPhase] = frozenset(
    {"recovering"}
)


# ---------------------------------------------------------------------------
# Typed contracts for phase transitions
# ---------------------------------------------------------------------------


class RunPhaseTransition(StrictFrozenModel):
    """Validated phase transition record."""

    schema_version: Literal["run-coordinator.phase-transition.v1"]
    from_phase: RunPhase
    to_phase: RunPhase
    reason_code: str


class RunCoordinatorState(StrictFrozenModel):
    """Durable coordinator state for a single run."""

    schema_version: Literal["run-coordinator.state.v1"]
    run_id: RunIdValue
    phase: RunPhase
    event_head: VerifiedEventHead | None
    manifest: object | None
    recovery_attempted: bool = Field(strict=True)


# ---------------------------------------------------------------------------
# Phase transition rules
# ---------------------------------------------------------------------------

_ALLOWED_PHASE_TRANSITIONS: frozenset[tuple[RunPhase, RunPhase]] = frozenset(
    {
        ("idle", "planning"),
        ("idle", "recovering"),
        ("planning", "executing"),
        ("planning", "failed"),
        ("executing", "completed"),
        ("executing", "failed"),
        ("executing", "cancelled"),
        ("executing", "recovering"),
        ("recovering", "executing"),
        ("recovering", "planning"),
        ("recovering", "failed"),
    }
)


def _phase_index(phase: RunPhase) -> int:
    return _RUN_PHASE_ORDER.index(phase)


def _is_allowed_transition(
    from_phase: RunPhase,
    to_phase: RunPhase,
) -> bool:
    if from_phase in _TERMINAL_PHASES:
        return False
    return (from_phase, to_phase) in _ALLOWED_PHASE_TRANSITIONS


# ---------------------------------------------------------------------------
# RunCoordinator state machine
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunCoordinator:
    """Compact run-lifecycle coordinator.

    Manages creation, planning, execution, recovery, and resume
    for a single run via an idempotent state machine.
    """

    run_id: str
    phase: RunPhase
    event_head: VerifiedEventHead | None
    manifest_ref: object | None
    recovery_attempted: bool

    # -- phase transitions --------------------------------------------------

    def transition(self, to_phase: RunPhase, reason: str) -> RunCoordinator:
        """Advance to a new phase with a validated transition."""
        if not _is_allowed_transition(self.phase, to_phase):
            raise ValueError(
                f"phase transition not allowed: {self.phase} -> {to_phase}"
            )
        return RunCoordinator(
            run_id=self.run_id,
            phase=to_phase,
            event_head=self.event_head,
            manifest_ref=self.manifest_ref,
            recovery_attempted=self.recovery_attempted,
        )

    def is_terminal(self) -> bool:
        return self.phase in _TERMINAL_PHASES

    def is_recovery(self) -> bool:
        return self.phase in _RECOVERY_PHASES

    # -- creation -----------------------------------------------------------

    def create_run(
        self,
        manifest: object,
        event_head: VerifiedEventHead,
    ) -> RunCoordinator:
        """Transition from idle to planning with a fresh manifest."""
        if self.phase != "idle":
            raise ValueError("run creation requires idle phase")
        return RunCoordinator(
            run_id=self.run_id,
            phase="planning",
            event_head=event_head,
            manifest_ref=manifest,
            recovery_attempted=False,
        )

    # -- planning -----------------------------------------------------------

    def begin_planning(self) -> RunCoordinator:
        """Explicit entry into planning (from idle via recovery or fresh)."""
        if self.phase not in {"idle", "recovering"}:
            raise ValueError(
                f"planning entry requires idle or recovering, got {self.phase}"
            )
        return RunCoordinator(
            run_id=self.run_id,
            phase="planning",
            event_head=self.event_head,
            manifest_ref=self.manifest_ref,
            recovery_attempted=self.recovery_attempted,
        )

    def finish_planning(self) -> RunCoordinator:
        """Advance from planning to executing."""
        return self.transition("executing", "planning_complete")

    # -- execution ----------------------------------------------------------

    def finish_execution(self) -> RunCoordinator:
        """Advance from executing to completed."""
        return self.transition("completed", "execution_complete")

    def fail_execution(self, reason: str = "execution_failed") -> RunCoordinator:
        """Advance from executing to failed."""
        if self.phase != "executing":
            raise ValueError(
                f"fail_execution not allowed from phase {self.phase}"
            )
        return self.transition("failed", reason)

    def cancel(self, reason: str = "user_cancelled") -> RunCoordinator:
        """Advance from executing to cancelled."""
        if self.phase in _TERMINAL_PHASES:
            raise ValueError(
                f"cancel not allowed from terminal phase {self.phase}"
            )
        return self.transition("cancelled", reason)

    # -- recovery -----------------------------------------------------------

    def begin_recovery(self) -> RunCoordinator:
        """Transition into recovery phase."""
        if self.phase not in {"executing", "planning"}:
            raise ValueError(
                f"recovery requires executing or planning, got {self.phase}"
            )
        return RunCoordinator(
            run_id=self.run_id,
            phase="recovering",
            event_head=self.event_head,
            manifest_ref=self.manifest_ref,
            recovery_attempted=True,
        )

    def finish_recovery_to_execution(self) -> RunCoordinator:
        """Resume execution after successful recovery."""
        return self.transition("executing", "recovery_resumed")

    def finish_recovery_to_planning(self) -> RunCoordinator:
        """Fall back to planning after recovery."""
        return self.transition("planning", "recovery_fallback")

    def fail_recovery(self, reason: str = "recovery_failed") -> RunCoordinator:
        """Abort: recovery did not succeed."""
        return self.transition("failed", reason)

    # -- idempotent recovery from event head ------------------------------------

    def recover_from_event_head(
        self,
        event_head: VerifiedEventHead | None,
    ) -> RunCoordinator:
        """Rebuild coordinator state from a verified event head.

        The caller MUST verify the event head against the append-only
        event log before calling this method.
        """
        if event_head is None:
            return RunCoordinator(
                run_id=self.run_id,
                phase="idle",
                event_head=None,
                manifest_ref=self.manifest_ref,
                recovery_attempted=False,
            )
        return RunCoordinator(
            run_id=self.run_id,
            phase=self.phase,
            event_head=event_head,
            manifest_ref=self.manifest_ref,
            recovery_attempted=self.recovery_attempted,
        )


__all__ = [
    "RunCoordinator",
    "RunCoordinatorState",
    "RunPhase",
    "RunPhaseTransition",
]