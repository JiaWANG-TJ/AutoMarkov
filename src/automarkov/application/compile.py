"""Compiler application orchestration service.

Implements a 16-stage state machine for the compile pipeline with
append-only event recovery and typed stage contracts.

The stage functions and their input/output models are imported from
:mod:`automarkov.application.stages` via the ``STAGE_REGISTRY``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from automarkov.application._common import (
    _ALL_STAGES,
    FailureCode,
    RecoveryStatus,
    StageId,
    StageResult,
    next_stage,
    stage_index,
)
from automarkov.application.stages import (
    STAGE_FUNCTIONS,
    STAGE_REGISTRY,
    ApprovalGateInput,
    ClaimEvidenceGraphInput,
    ClassifyProcessKindInput,
    CreateManifestInput,
    EnvironmentCandidateInput,
    FormalValidationInput,
    IdentifyAmbiguitiesInput,
    PackageCandidateInput,
    PlanEvidenceInput,
    ProposeFormalSpecInput,
    PublicTestsInput,
    RetrieveEvidenceInput,
    SelectRouteInput,
    TerminalCASInput,
    TextFormalCriticInput,
    ValidateIngressInput,
)

# ---------------------------------------------------------------------------
# Stage dispatch helpers
# ---------------------------------------------------------------------------


def stage_input_type(stage: StageId) -> type[Any]:
    return STAGE_REGISTRY[stage][0]


def stage_output_type(stage: StageId) -> type[Any]:
    return STAGE_REGISTRY[stage][1]


_StageIO = tuple[type[Any], type[Any]]

_STAGE_DISPATCH: dict[StageId, _StageIO] = {
    sid: (inp_model, out_model) for sid, (inp_model, out_model, _fn) in STAGE_REGISTRY.items()  # type: ignore[arg-type]
}

StageInput = (
    ValidateIngressInput | CreateManifestInput | PlanEvidenceInput
    | RetrieveEvidenceInput | ClaimEvidenceGraphInput | IdentifyAmbiguitiesInput
    | ClassifyProcessKindInput | ProposeFormalSpecInput | FormalValidationInput
    | TextFormalCriticInput | ApprovalGateInput | SelectRouteInput
    | EnvironmentCandidateInput | PublicTestsInput | PackageCandidateInput
    | TerminalCASInput
)


# ---------------------------------------------------------------------------
# CompileOrchestrator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompileOrchestrator:
    """Chains the 16 compile stages with recovery."""

    run_id: str
    current_stage: StageId
    event_head: object | None
    budget_snapshot_ref: object | None

    def current_stage_index(self) -> int:
        return stage_index(self.current_stage)

    def is_terminal(self) -> bool:
        return self.current_stage == "terminal_cas"

    def advance(self, stage_result: StageResult) -> CompileOrchestrator:
        """Advance to the next stage."""
        if stage_result.stage != self.current_stage:
            raise ValueError(
                f"stage mismatch: expected {self.current_stage}, "
                f"got {stage_result.stage}"
            )
        if stage_result.status == "failed":
            return CompileOrchestrator(
                run_id=self.run_id,
                current_stage=self.current_stage,
                event_head=stage_result.event_refs[-1]
                if stage_result.event_refs
                else self.event_head,
                budget_snapshot_ref=self.budget_snapshot_ref,
            )
        nxt = next_stage(self.current_stage)
        if nxt is None:
            return self
        return CompileOrchestrator(
            run_id=self.run_id,
            current_stage=nxt,
            event_head=stage_result.event_refs[-1]
            if stage_result.event_refs
            else self.event_head,
            budget_snapshot_ref=self.budget_snapshot_ref,
        )

    def recover_from_event_head(
        self,
        event_head: object | None,
    ) -> CompileOrchestrator:
        """Rebuild from verified event head."""
        if event_head is None:
            return CompileOrchestrator(
                run_id=self.run_id,
                current_stage="validate_ingress",
                event_head=None,
                budget_snapshot_ref=self.budget_snapshot_ref,
            )
        return CompileOrchestrator(
            run_id=self.run_id,
            current_stage=self.current_stage,
            event_head=event_head,
            budget_snapshot_ref=self.budget_snapshot_ref,
        )

    def chain(
        self,
        stage_inputs: dict[StageId, StageInput],
        *,
        recovery_heads: dict[StageId, object | None] | None = None,
    ) -> tuple[StageResult, ...]:
        """Execute all stages from current_stage onward."""
        results: list[StageResult] = []
        orch = self
        for stage_id in _ALL_STAGES:
            if stage_index(stage_id) < orch.current_stage_index():
                continue
            inp = stage_inputs.get(stage_id)
            if inp is None:
                break
            head = (recovery_heads or {}).get(stage_id)
            func = STAGE_FUNCTIONS[stage_id]
            result = func(inp, recovery_head=head)
            results.append(result)
            orch = orch.advance(result)
            if result.status == "failed":
                break
        return tuple(results)


__all__ = [
    "CompileOrchestrator",
    "FailureCode",
    "RecoveryStatus",
    "StageId",
    "StageResult",
    "next_stage",
    "stage_index",
    "stage_input_type",
    "stage_output_type",
]
