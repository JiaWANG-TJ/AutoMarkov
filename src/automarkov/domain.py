from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

RequestId = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^request_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    ),
]
PositiveSafeInt = Annotated[int, Field(strict=True, ge=1, le=9_007_199_254_740_991)]
NonNegativeSafeInt = Annotated[int, Field(strict=True, ge=0, le=9_007_199_254_740_991)]


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        validate_default=True,
    )


class RunId(
    RootModel[
        Annotated[
            str,
            Field(
                strict=True,
                pattern=r"^run_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
            ),
        ]
    ]
):
    model_config = ConfigDict(strict=True, frozen=True)


class ArtifactId(
    RootModel[
        Annotated[
            str,
            Field(
                strict=True,
                pattern=r"^artifact_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
            ),
        ]
    ]
):
    model_config = ConfigDict(strict=True, frozen=True)


class Sha256Digest(
    RootModel[Annotated[str, Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]]
):
    model_config = ConfigDict(strict=True, frozen=True)


class RequestBudget(StrictFrozenModel):
    schema_version: Literal["automarkov.request-budget.v1"]
    wall_time_seconds: PositiveSafeInt
    llm_token_limit: NonNegativeSafeInt
    tool_call_limit: NonNegativeSafeInt


class RequestPermissions(StrictFrozenModel):
    schema_version: Literal["automarkov.request-permissions.v1"]
    allow_retrieval: bool = Field(strict=True)
    allow_clarification: bool = Field(strict=True)
    allow_code_execution: bool = Field(strict=True)


class TaskRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.task-request.v1"]
    request_id: RequestId
    task_text: Annotated[str, Field(strict=True, min_length=1, max_length=100_000)]
    budget: RequestBudget
    permissions: RequestPermissions

    @field_validator("task_text")
    @classmethod
    def normalize_task_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("task_text must contain non-whitespace text")
        return normalized


class RunState(str, Enum):
    RECEIVED = "RECEIVED"
    RESEARCHING = "RESEARCHING"
    TEXT_DRAFTED = "TEXT_DRAFTED"
    TEXT_REVIEWED = "TEXT_REVIEWED"
    WAITING_TEXT_CONFIRMATION = "WAITING_TEXT_CONFIRMATION"
    TEXT_LOCKED = "TEXT_LOCKED"
    CLASSIFIED = "CLASSIFIED"
    REDUCTION_PROPOSAL_DRAFTING = "REDUCTION_PROPOSAL_DRAFTING"
    WAITING_REDUCTION_CONFIRMATION = "WAITING_REDUCTION_CONFIRMATION"
    OOD_HANDOFF_BUILDING = "OOD_HANDOFF_BUILDING"
    OOD_HANDOFF_VALIDATING = "OOD_HANDOFF_VALIDATING"
    FORMAL_DRAFTED = "FORMAL_DRAFTED"
    FORMAL_REVIEWED = "FORMAL_REVIEWED"
    WAITING_FORMAL_CONFIRMATION = "WAITING_FORMAL_CONFIRMATION"
    FORMAL_LOCKED = "FORMAL_LOCKED"
    IMPLEMENTATION_SELECTED = "IMPLEMENTATION_SELECTED"
    ENVIRONMENT_IMPLEMENTED = "ENVIRONMENT_IMPLEMENTED"
    UNIT_VALIDATING = "UNIT_VALIDATING"
    SIMULATION_VALIDATING = "SIMULATION_VALIDATING"
    SEALED_E2E_VALIDATING = "SEALED_E2E_VALIDATING"
    TRAINING_SMOKE_TESTING = "TRAINING_SMOKE_TESTING"
    POLICY_TRAINING = "POLICY_TRAINING"
    FINAL_EVALUATING = "FINAL_EVALUATING"
    PACKAGING = "PACKAGING"
    WAITING_RUNTIME = "WAITING_RUNTIME"
    WAITING_EVIDENCE = "WAITING_EVIDENCE"
    WAITING_ASSET = "WAITING_ASSET"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    OOD_PACKAGED = "OOD_PACKAGED"
    PARTIAL = "PARTIAL"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RunView(StrictFrozenModel):
    schema_version: Literal["automarkov.run-view.v1"]
    run_id: RunId
    task_request_id: RequestId
    state: RunState


class CompilerDispatchRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.compiler-dispatch-request.v1"]
    run_id: RunId
    event_id: Annotated[
        str,
        Field(
            strict=True,
            pattern=r"^event_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
        ),
    ]
