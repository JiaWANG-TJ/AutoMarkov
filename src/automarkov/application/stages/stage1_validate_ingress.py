"""Stage 1: validate TaskRequest."""

from __future__ import annotations

from typing import Literal

from automarkov.application._common import StageResult
from automarkov.domain.models import StrictFrozenModel, TaskRequest, validate_strict_frozen_payload


class ValidateIngressInput(StrictFrozenModel):
    schema_version: Literal["compile.validate-ingress-input.v1"]
    task_request: TaskRequest
    budget_policy_ref: object


class ValidateIngressOutput(StrictFrozenModel):
    schema_version: Literal["compile.validate-ingress-output.v1"]
    validated_request: TaskRequest


def validate_ingress_stage(
    inp: ValidateIngressInput,
    *,
    recovery_head: object | None = None,
) -> StageResult:
    """Stage 1: validate TaskRequest."""
    validated = validate_strict_frozen_payload(
        TaskRequest,
        inp.task_request.model_dump(mode="json", round_trip=True, warnings="error"),
    )
    return StageResult(
        stage="validate_ingress", status="ok",
        output_ref=ValidateIngressOutput(
            schema_version="compile.validate-ingress-output.v1",
            validated_request=validated,
        ),
        failure_code=None, recovery_status="ok",
        event_refs=(), budget_consumed_ref=None,
    )
