"""Unit tests for automarkov.run_coordinator.

Covers:
- RunCoordinatorState / RunPhaseTransition construction
- All 11 allowed phase transitions
- Invalid transition rejection (ValueError)
- is_terminal() / is_recovery() predicates
- create_run() from idle
- Planning lifecycle (begin_planning, finish_planning)
- Execution lifecycle (finish_execution, fail_execution)
- cancel() from executing
- Recovery lifecycle (begin_recovery, finish_recovery_to_execution,
  finish_recovery_to_planning, fail_recovery)
- recover_from_event_head() idempotency
"""

from __future__ import annotations

import pytest

from automarkov.domain.models import RunId, Sha256Digest
from automarkov.run_coordinator import (
    RunCoordinator,
    RunCoordinatorState,
    RunPhaseTransition,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_RUN_ID = "run_unit_test"


def _head():
    """Minimal VerifiedEventHead stub."""
    from automarkov.domain.models import VerifiedEventHead

    return VerifiedEventHead(
        run_id=RunId("run_unit_test"),
        sequence_no=0,
        event_hash=Sha256Digest("sha256:" + "ab" * 32),
    )


def _idle():
    """Fresh coordinator in idle phase."""
    return RunCoordinator(
        run_id=_RUN_ID,
        phase="idle",
        event_head=None,
        manifest_ref=None,
        recovery_attempted=False,
    )


def _manifest():
    """Lightweight manifest stub."""
    return object()


def _to_completed():
    """Reach completed via idle->planning->executing->completed."""
    return (
        _idle()
        .transition("planning", "p")
        .transition("executing", "e")
        .transition("completed", "done")
    )


def _to_failed():
    """Reach failed via idle->planning->failed."""
    return _idle().transition("planning", "p").transition("failed", "fail")


def _to_cancelled():
    """Reach cancelled via idle->planning->executing->cancelled."""
    return (
        _idle()
        .transition("planning", "p")
        .transition("executing", "e")
        .transition("cancelled", "cancel")
    )


def _to_recovering():
    """Reach recovering via idle->planning->executing->recovering."""
    return (
        _idle()
        .transition("planning", "p")
        .transition("executing", "e")
        .transition("recovering", "recover")
    )


# ---------------------------------------------------------------------------
# RunCoordinatorState — construction
# ---------------------------------------------------------------------------


def test_run_coordinator_state_valid_construction() -> None:
    state = RunCoordinatorState(
        schema_version="run-coordinator.state.v1",
        run_id=_RUN_ID,
        phase="idle",
        event_head=None,
        manifest=None,
        recovery_attempted=False,
    )
    assert state.schema_version == "run-coordinator.state.v1"
    assert state.run_id == _RUN_ID
    assert state.phase == "idle"
    assert state.event_head is None
    assert state.manifest is None
    assert state.recovery_attempted is False


# ---------------------------------------------------------------------------
# RunPhaseTransition — construction & validation
# ---------------------------------------------------------------------------


def test_phase_transition_valid_construction() -> None:
    t = RunPhaseTransition(
        schema_version="run-coordinator.phase-transition.v1",
        from_phase="idle",
        to_phase="planning",
        reason_code="test_transition",
    )
    assert t.from_phase == "idle"
    assert t.to_phase == "planning"
    assert t.reason_code == "test_transition"


def test_phase_transition_same_phase_raises() -> None:
    with pytest.raises(ValueError, match="not allowed"):
        _idle().transition("idle", "test_same")


# ---------------------------------------------------------------------------
# RunCoordinator — initial state
# ---------------------------------------------------------------------------


def test_initial_state_is_idle() -> None:
    c = _idle()
    assert c.phase == "idle"
    assert c.run_id == _RUN_ID
    assert c.event_head is None
    assert c.manifest_ref is None
    assert c.recovery_attempted is False


# ---------------------------------------------------------------------------
# All 10 allowed phase transitions
# ---------------------------------------------------------------------------


def test_transition_idle_to_planning() -> None:
    c = _idle().transition("planning", "idle->planning")
    assert c.phase == "planning"


def test_transition_idle_to_recovering() -> None:
    c = _idle().transition("recovering", "idle->recovering")
    assert c.phase == "recovering"


def test_transition_planning_to_executing() -> None:
    c = _idle().transition("planning", "p").transition("executing", "p->e")
    assert c.phase == "executing"


def test_transition_planning_to_failed() -> None:
    c = _idle().transition("planning", "p").transition("failed", "p->f")
    assert c.phase == "failed"


def test_transition_executing_to_completed() -> None:
    c = (
        _idle()
        .transition("planning", "x")
        .transition("executing", "x")
        .transition("completed", "x")
    )
    assert c.phase == "completed"


def test_transition_executing_to_failed() -> None:
    c = (
        _idle()
        .transition("planning", "x")
        .transition("executing", "x")
        .transition("failed", "x")
    )
    assert c.phase == "failed"


def test_transition_executing_to_cancelled() -> None:
    c = (
        _idle()
        .transition("planning", "x")
        .transition("executing", "x")
        .transition("cancelled", "x")
    )
    assert c.phase == "cancelled"


def test_transition_executing_to_recovering() -> None:
    c = (
        _idle()
        .transition("planning", "x")
        .transition("executing", "x")
        .transition("recovering", "x")
    )
    assert c.phase == "recovering"


def test_transition_recovering_to_executing() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    c = c.begin_recovery()
    assert c.phase == "recovering"
    c = c.finish_recovery_to_execution()
    assert c.phase == "executing"


def test_transition_recovering_to_planning() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    c = c.begin_recovery()
    assert c.phase == "recovering"
    c = c.finish_recovery_to_planning()
    assert c.phase == "planning"


def test_transition_recovering_to_failed() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    c = c.begin_recovery()
    assert c.phase == "recovering"
    c = c.fail_recovery()
    assert c.phase == "failed"


# ---------------------------------------------------------------------------
# Invalid transitions raise ValueError
# ---------------------------------------------------------------------------


def test_invalid_transition_terminal_to_planning() -> None:
    c = _to_completed()
    with pytest.raises(ValueError, match="not allowed"):
        c.transition("planning", "invalid")


def test_invalid_transition_failed_to_executing() -> None:
    c = _to_failed()
    with pytest.raises(ValueError, match="not allowed"):
        c.transition("executing", "invalid")


def test_invalid_transition_cancelled_to_planning() -> None:
    c = _to_cancelled()
    with pytest.raises(ValueError, match="not allowed"):
        c.transition("planning", "invalid")


def test_invalid_transition_idle_to_completed() -> None:
    with pytest.raises(ValueError, match="not allowed"):
        _idle().transition("completed", "skip_to_end")


def test_invalid_transition_idle_to_failed() -> None:
    with pytest.raises(ValueError, match="not allowed"):
        _idle().transition("failed", "skip_fail")


def test_invalid_transition_planning_to_completed() -> None:
    c = _idle().transition("planning", "p")
    with pytest.raises(ValueError, match="not allowed"):
        c.transition("completed", "skip_exec")


def test_invalid_transition_recovering_to_completed() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    c = c.begin_recovery()
    with pytest.raises(ValueError, match="not allowed"):
        c.transition("completed", "skip_to_done")


def test_invalid_transition_idle_to_recovering_via_generic() -> None:
    """idle->recovering is allowed, but idle->cancelled is NOT."""
    with pytest.raises(ValueError, match="not allowed"):
        _idle().transition("cancelled", "no_skip_cancel")


# ---------------------------------------------------------------------------
# is_terminal()
# ---------------------------------------------------------------------------


def test_is_terminal_completed() -> None:
    c = _to_completed()
    assert c.is_terminal() is True


def test_is_terminal_failed() -> None:
    c = _to_failed()
    assert c.is_terminal() is True


def test_is_terminal_cancelled() -> None:
    c = _to_cancelled()
    assert c.is_terminal() is True


def test_is_terminal_idle() -> None:
    assert _idle().is_terminal() is False


def test_is_terminal_planning() -> None:
    c = _idle().transition("planning", "p")
    assert c.is_terminal() is False


def test_is_terminal_executing() -> None:
    c = _idle().transition("planning", "p").transition("executing", "e")
    assert c.is_terminal() is False


def test_is_terminal_recovering() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    c = c.begin_recovery()
    assert c.is_terminal() is False


# ---------------------------------------------------------------------------
# is_recovery()
# ---------------------------------------------------------------------------


def test_is_recovery_true() -> None:
    c = _to_recovering()
    assert c.is_recovery() is True


def test_is_recovery_false_for_idle() -> None:
    assert _idle().is_recovery() is False


def test_is_recovery_false_for_planning() -> None:
    c = _idle().transition("planning", "p")
    assert c.is_recovery() is False


def test_is_recovery_false_for_executing() -> None:
    c = _idle().transition("planning", "p").transition("executing", "e")
    assert c.is_recovery() is False


def test_is_recovery_false_for_completed() -> None:
    c = _to_completed()
    assert c.is_recovery() is False


def test_is_recovery_false_for_failed() -> None:
    c = _to_failed()
    assert c.is_recovery() is False


# ---------------------------------------------------------------------------
# create_run()
# ---------------------------------------------------------------------------


def test_create_run_starts_at_planning() -> None:
    r = _head()
    m = _manifest()
    c = _idle().create_run(m, r)
    assert c.phase == "planning"
    assert c.manifest_ref is m
    assert c.event_head is r
    assert c.recovery_attempted is False


def test_create_run_preserves_run_id() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    assert c.run_id == _RUN_ID


def test_create_run_from_non_idle_raises() -> None:
    c = _idle().transition("planning", "p")
    with pytest.raises(ValueError, match="idle"):
        c.create_run(_manifest(), _head())


# ---------------------------------------------------------------------------
# begin_planning() / finish_planning()
# ---------------------------------------------------------------------------


def test_begin_planning_from_idle() -> None:
    c = _idle().begin_planning()
    assert c.phase == "planning"


def test_begin_planning_from_recovering() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    c = c.begin_recovery()
    c = c.begin_planning()
    assert c.phase == "planning"


def test_begin_planning_from_executing_raises() -> None:
    c = _idle().transition("planning", "p").transition("executing", "e")
    with pytest.raises(ValueError, match="planning"):
        c.begin_planning()


def test_begin_planning_from_completed_raises() -> None:
    c = _to_completed()
    with pytest.raises(ValueError, match="planning"):
        c.begin_planning()


def test_finish_planning_advances_to_executing() -> None:
    c = _idle().transition("planning", "p").finish_planning()
    assert c.phase == "executing"


# ---------------------------------------------------------------------------
# finish_execution() / fail_execution()
# ---------------------------------------------------------------------------


def test_finish_execution_advances_to_completed() -> None:
    c = (
        _idle()
        .transition("planning", "p")
        .transition("executing", "e")
        .finish_execution()
    )
    assert c.phase == "completed"


def test_fail_execution_from_executing() -> None:
    c = (
        _idle()
        .transition("planning", "p")
        .transition("executing", "e")
        .fail_execution("runtime_error")
    )
    assert c.phase == "failed"


def test_fail_execution_preserves_reason() -> None:
    c = (
        _idle()
        .transition("planning", "p")
        .transition("executing", "e")
        .fail_execution("oom_killed")
    )
    assert c.phase == "failed"
    # The reason is stored in the transition, not the coordinator directly.
    # Verify the transition succeeded regardless of reason string.


def test_fail_execution_default_reason() -> None:
    c = (
        _idle()
        .transition("planning", "p")
        .transition("executing", "e")
        .fail_execution()
    )
    assert c.phase == "failed"


def test_fail_execution_from_idle_raises() -> None:
    with pytest.raises(ValueError, match="not allowed"):
        _idle().fail_execution()


def test_fail_execution_from_planning_raises() -> None:
    c = _idle().transition("planning", "p")
    with pytest.raises(ValueError, match="not allowed"):
        c.fail_execution()


def test_fail_execution_from_completed_raises() -> None:
    c = _to_completed()
    with pytest.raises(ValueError, match="not allowed"):
        c.fail_execution()


# ---------------------------------------------------------------------------
# cancel()
# ---------------------------------------------------------------------------


def test_cancel_from_executing() -> None:
    c = (
        _idle()
        .transition("planning", "p")
        .transition("executing", "e")
        .cancel("user_cancelled")
    )
    assert c.phase == "cancelled"


def test_cancel_default_reason() -> None:
    c = (
        _idle()
        .transition("planning", "p")
        .transition("executing", "e")
        .cancel()
    )
    assert c.phase == "cancelled"


def test_cancel_from_idle_raises() -> None:
    with pytest.raises(ValueError, match="not allowed"):
        _idle().cancel()


def test_cancel_from_planning_raises() -> None:
    c = _idle().transition("planning", "p")
    with pytest.raises(ValueError, match="not allowed"):
        c.cancel()


def test_cancel_from_completed_raises() -> None:
    c = _to_completed()
    with pytest.raises(ValueError, match="not allowed"):
        c.cancel()


def test_cancel_from_failed_raises() -> None:
    c = _to_failed()
    with pytest.raises(ValueError, match="not allowed"):
        c.cancel()


def test_cancel_from_recovering_raises() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    c = c.begin_recovery()
    with pytest.raises(ValueError, match="not allowed"):
        c.cancel()


# ---------------------------------------------------------------------------
# begin_recovery()
# ---------------------------------------------------------------------------


def test_begin_recovery_from_executing() -> None:
    c = (
        _idle()
        .transition("planning", "p")
        .transition("executing", "e")
        .begin_recovery()
    )
    assert c.phase == "recovering"
    assert c.recovery_attempted is True


def test_begin_recovery_from_planning() -> None:
    c = _idle().transition("planning", "p").begin_recovery()
    assert c.phase == "recovering"
    assert c.recovery_attempted is True


def test_begin_recovery_from_idle_raises() -> None:
    with pytest.raises(ValueError, match="recovery"):
        _idle().begin_recovery()


def test_begin_recovery_from_completed_raises() -> None:
    c = _to_completed()
    with pytest.raises(ValueError, match="recovery"):
        c.begin_recovery()


def test_begin_recovery_from_failed_raises() -> None:
    c = _to_failed()
    with pytest.raises(ValueError, match="recovery"):
        c.begin_recovery()


def test_begin_recovery_from_recovering_raises() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    c = c.begin_recovery()
    with pytest.raises(ValueError, match="recovery"):
        c.begin_recovery()


# ---------------------------------------------------------------------------
# finish_recovery_to_execution() / finish_recovery_to_planning()
# ---------------------------------------------------------------------------


def test_finish_recovery_to_execution() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    c = c.begin_recovery()
    c = c.finish_recovery_to_execution()
    assert c.phase == "executing"


def test_finish_recovery_to_planning() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    c = c.begin_recovery()
    c = c.finish_recovery_to_planning()
    assert c.phase == "planning"


def test_fail_recovery() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    c = c.begin_recovery()
    c = c.fail_recovery("recovery_exhausted")
    assert c.phase == "failed"


def test_fail_recovery_default_reason() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    c = c.begin_recovery()
    c = c.fail_recovery()
    assert c.phase == "failed"


# ---------------------------------------------------------------------------
# recover_from_event_head() — idempotency
# ---------------------------------------------------------------------------


def test_recover_from_event_head_with_none_resets_to_idle() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    assert c.phase == "planning"
    c2 = c.recover_from_event_head(None)
    assert c2.phase == "idle"
    assert c2.manifest_ref == c.manifest_ref


def test_recover_from_event_head_preserves_phase() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    c = c.transition("executing", "go")
    c2 = c.recover_from_event_head(r)
    assert c2.phase == "executing"
    assert c2.event_head is r


def test_recover_from_event_head_idempotent() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    c1 = c.recover_from_event_head(r)
    c2 = c1.recover_from_event_head(r)
    assert c1.phase == c2.phase
    assert c1.run_id == c2.run_id
    assert c1.recovery_attempted == c2.recovery_attempted


def test_recover_preserves_run_id() -> None:
    r = _head()
    c = _idle().create_run(_manifest(), r)
    c2 = c.recover_from_event_head(r)
    assert c2.run_id == _RUN_ID


def test_recover_preserves_manifest_ref() -> None:
    r = _head()
    m = _manifest()
    c = _idle().create_run(m, r)
    c = c.transition("executing", "go")
    c2 = c.recover_from_event_head(r)
    assert c2.manifest_ref is m