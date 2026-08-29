from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from automarkov.contracts.task import WireValidationLevel
from automarkov.domain.canonical import FrozenSequence, StrictTrue
from automarkov.domain.models import StrictFrozenModel, validate_strict_frozen_payload
from automarkov.lifecycle import ArtifactReference

NonEmptyText = Annotated[str, Field(strict=True, min_length=1, max_length=8_192)]
ValidatorId = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]

VALIDATION_LEVELS: tuple[str, ...] = (
    "schema",
    "structural",
    "executable",
    "behavioral",
    "oracle_equivalent",
    "formally_verified",
)


def _require_nonblank_unique(
    values: tuple[str, ...],
    *,
    label: str,
    required: bool = True,
) -> tuple[str, ...]:
    if required and not values:
        raise ValueError(f"{label} must be nonempty")
    if any(not item.strip() for item in values):
        raise ValueError(f"{label} cannot contain blank values")
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")
    return values


def _require_unique_refs(
    values: tuple[ArtifactReference, ...],
    *,
    label: str,
    required: bool,
) -> tuple[ArtifactReference, ...]:
    identities = tuple(item.artifact_id for item in values)
    if required and not identities:
        raise ValueError(f"{label} must be nonempty")
    if len(set(identities)) != len(identities):
        raise ValueError(f"{label} must be unique")
    return values


def _reference_key(reference: ArtifactReference) -> tuple[str, str]:
    return reference.artifact_id, reference.payload_hash


class FormalVerificationEvidence(StrictFrozenModel):
    property_id: ValidatorId
    model_id: ValidatorId
    tool_id: ValidatorId


class ValidationReport(StrictFrozenModel):
    schema_version: Literal["automarkov.validation-report.v1"]
    report_kind: Literal["validation_report"]
    subject_ref: ArtifactReference
    level: WireValidationLevel
    validator_id: ValidatorId
    validator_version: ValidatorId
    status: Literal["passed", "failed"]
    scope: FrozenSequence[NonEmptyText]
    covered_scope: FrozenSequence[NonEmptyText]
    uncovered_scope: FrozenSequence[NonEmptyText]
    assumptions: FrozenSequence[NonEmptyText]
    proof_refs: FrozenSequence[ArtifactReference]
    formal_evidence: FormalVerificationEvidence | None

    @model_validator(mode="after")
    def require_closed_report(self) -> Self:
        scope = _require_nonblank_unique(tuple(self.scope), label="validation scope")
        covered = _require_nonblank_unique(
            tuple(self.covered_scope),
            label="covered validation scope",
            required=False,
        )
        uncovered = _require_nonblank_unique(
            tuple(self.uncovered_scope),
            label="uncovered validation scope",
            required=False,
        )
        _require_nonblank_unique(
            tuple(self.assumptions),
            label="validation assumptions",
            required=False,
        )
        _require_unique_refs(
            tuple(self.proof_refs),
            label="proof references",
            required=False,
        )
        if set(covered) & set(uncovered) or set(covered) | set(uncovered) != set(scope):
            raise ValueError("covered and uncovered scope must partition report scope")
        formal = self.level == "formally_verified"
        if formal != (self.formal_evidence is not None and bool(self.proof_refs)):
            raise ValueError(
                "formal verification requires property, model, tool, and proof evidence"
            )
        if not formal and (self.formal_evidence is not None or self.proof_refs):
            raise ValueError("non-formal reports cannot attach formal proof evidence")
        return self


class ValidationClaim(StrictFrozenModel):
    schema_version: Literal["automarkov.validation-claim.v1"]
    claim_kind: Literal["validation_claim"]
    subject_ref: ArtifactReference
    report_refs: FrozenSequence[ArtifactReference]
    level: WireValidationLevel
    scope: FrozenSequence[NonEmptyText]
    passed: StrictTrue

    @model_validator(mode="after")
    def require_closed_claim(self) -> Self:
        _require_unique_refs(
            tuple(self.report_refs),
            label="validation report references",
            required=True,
        )
        _require_nonblank_unique(tuple(self.scope), label="validation claim scope")
        return self


def validate_claim_report_chain(
    claim: ValidationClaim,
    bound_reports: tuple[tuple[ArtifactReference, ValidationReport], ...],
) -> None:
    """验证 claim 引用的报告闭包；repository 在 identity 固定后调用同一合同。"""

    actual_refs = tuple(_reference_key(reference) for reference, _ in bound_reports)
    expected_refs = tuple(_reference_key(reference) for reference in claim.report_refs)
    if set(actual_refs) != set(expected_refs) or len(actual_refs) != len(expected_refs):
        raise ValueError("claim report references do not match supplied reports")
    target_index = VALIDATION_LEVELS.index(claim.level)
    required_levels = set(VALIDATION_LEVELS[: target_index + 1])
    passed_levels: set[str] = set()
    for reference, report in bound_reports:
        if _reference_key(reference) not in expected_refs:
            raise ValueError("claim includes an unbound validation report")
        if _reference_key(report.subject_ref) != _reference_key(claim.subject_ref):
            raise ValueError("validation reports must bind the claim subject")
        if report.status != "passed":
            raise ValueError("only passed reports can support a claim")
        if tuple(report.scope) != tuple(claim.scope) or report.uncovered_scope:
            raise ValueError("validation reports must fully cover the claim scope")
        passed_levels.add(report.level)
    missing = required_levels - passed_levels
    if missing:
        raise ValueError(
            "validation prerequisite reports are missing: "
            + ", ".join(sorted(missing, key=VALIDATION_LEVELS.index))
        )


def validate_validation_report_payload(value: object) -> ValidationReport:
    return validate_strict_frozen_payload(ValidationReport, value)


def validate_validation_claim_payload(value: object) -> ValidationClaim:
    return validate_strict_frozen_payload(ValidationClaim, value)


__all__ = [
    "VALIDATION_LEVELS",
    "FormalVerificationEvidence",
    "ValidationClaim",
    "ValidationReport",
    "validate_claim_report_chain",
    "validate_validation_claim_payload",
    "validate_validation_report_payload",
]
