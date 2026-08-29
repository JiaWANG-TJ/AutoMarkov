"""Stage 11: approval gate — based on validation report and critic report,
determine if the spec is approvable and return structured decision.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from automarkov.application._common import StageResult, _artifact_ref
from automarkov.contracts.task import TaskContract, TextCriticReport
from automarkov.contracts.validation import ValidationReport
from automarkov.domain.models import StrictFrozenModel


class RejectionReason(StrictFrozenModel):
    """A single structured reason for rejection."""
    reason_id: str
    source: Literal["validation", "critic", "contract"]
    field_path: str
    severity: Literal["low", "medium", "high", "critical"]
    message: str


class ApprovalDecision(StrictFrozenModel):
    """Structured approval decision with reasons."""
    schema_version: Literal["compile.approval-decision.v1"]
    approved: bool = Field(strict=True)
    rejection_reasons: tuple[RejectionReason, ...]
    summary: str


class ApprovalGateInput(StrictFrozenModel):
    schema_version: Literal["compile.approval-gate-input.v1"]
    task_contract: TaskContract
    critic_report: TextCriticReport
    validation_report: ValidationReport
    manifest_ref: object


class ApprovalGateOutput(StrictFrozenModel):
    schema_version: Literal["compile.approval-gate-output.v1"]
    approved: bool = Field(strict=True)
    approval_decision: ApprovalDecision
    signed_approval_ref: object | None


def _collect_rejection_reasons(
    validation_report: ValidationReport,
    critic_report: TextCriticReport,
    contract: TaskContract,
) -> list[RejectionReason]:
    """Collect structured rejection reasons from all evidence sources."""
    reasons: list[RejectionReason] = []

    # From validation report
    if validation_report.status == "failed":
        for i, uncovered_item in enumerate(validation_report.uncovered_scope):
            reasons.append(RejectionReason(
                reason_id=f"val_rej_{i:03d}",
                source="validation",
                field_path=uncovered_item.split(":")[0] if ":" in uncovered_item
                           else uncovered_item,
                severity="high",
                message=uncovered_item,
            ))

    # From critic report
    for issue in critic_report.issues:
        if issue.disposition == "open" and issue.severity in ("high", "critical"):
            reasons.append(RejectionReason(
                reason_id=f"crit_rej_{issue.issue_id}",
                source="critic",
                field_path=issue.path,
                severity=issue.severity,
                message=f"{issue.reason}: {issue.consequence}",
            ))

    # From contract — unresolved questions
    for q in contract.evidence_and_assumptions.unresolved_questions:
        if q.severity in ("high", "critical"):
            reasons.append(RejectionReason(
                reason_id=f"ctr_rej_{q.question_id}",
                source="contract",
                field_path=q.target_path,
                severity=q.severity,
                message=f"Unresolved question: {q.question}",
            ))

    return reasons


def approval_gate_stage(
    inp: ApprovalGateInput,
    *,
    recovery_head: object | None = None,
) -> StageResult:
    """Stage 11: determine if spec is approvable.

    Decision rules:
    - If validation passes AND no unresolved critical/high critic issues →
      approve
    - Otherwise → reject with structured reasons

    Returns an ApprovalDecision with all rejection reasons enumerated.
    """
    validation_report = inp.validation_report
    critic_report = inp.critic_report
    contract = inp.task_contract

    rejection_reasons = _collect_rejection_reasons(
        validation_report, critic_report, contract
    )

    approved = len(rejection_reasons) == 0

    if approved:
        summary = "Approved: validation passed and no unresolved critical issues found."
    else:
        critical_count = sum(
            1 for r in rejection_reasons if r.severity == "critical"
        )
        high_count = sum(1 for r in rejection_reasons if r.severity == "high")
        summary = (
            f"Rejected: {critical_count} critical and {high_count} high-severity "
            f"issues found across validation, critic, and contract sources."
        )

    decision = ApprovalDecision(
        schema_version="compile.approval-decision.v1",
        approved=approved,
        rejection_reasons=tuple(rejection_reasons),
        summary=summary,
    )

    return StageResult(
        stage="approval_gate",
        status="ok",
        output_ref=ApprovalGateOutput(
            schema_version="compile.approval-gate-output.v1",
            approved=approved,
            approval_decision=decision,
            signed_approval_ref=_artifact_ref(
                decision.model_dump(mode="json", round_trip=True, warnings="error")
            ) if approved else None,
        ),
        failure_code=None,
        recovery_status="ok",
        event_refs=(),
        budget_consumed_ref=None,
    )
