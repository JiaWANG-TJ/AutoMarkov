from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, TypeAlias
from uuid import RFC_4122, UUID

from pydantic import AfterValidator, Field

RunIdValue = Annotated[
    str,
    Field(strict=True, pattern=r"^run_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
]
RequestIdValue = Annotated[
    str,
    Field(strict=True, pattern=r"^request_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
]
ArtifactIdValue = Annotated[
    str,
    Field(strict=True, pattern=r"^artifact_[0-9a-f]{64}$"),
]
Sha256Value = Annotated[
    str,
    Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$"),
]
PrincipalIdValue = Annotated[
    str,
    Field(strict=True, pattern=r"^principal_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
]
NonEmptyId = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
ReasonCode = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$"),
]
ValidationLevelValue: TypeAlias = Literal[
    "schema",
    "structural",
    "executable",
    "behavioral",
    "oracle_equivalent",
    "formally_verified",
]
SequenceNo = Annotated[int, Field(strict=True, ge=0, le=9_007_199_254_740_991)]


def _require_uuid7(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError("event_id must be a canonical UUIDv7") from error
    if str(parsed) != value or parsed.version != 7 or parsed.variant != RFC_4122:
        raise ValueError("event_id must be a canonical UUIDv7")
    return value


EventId = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    ),
    AfterValidator(_require_uuid7),
]


def _require_utc_timestamp(value: str) -> str:
    if not value.endswith("Z") or value.count("Z") != 1:
        raise ValueError("timestamp must use canonical UTC-Z representation")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("timestamp must be valid ISO-8601") from error
    canonical = parsed.isoformat(timespec="microseconds").replace(".000000+00:00", "Z")
    if canonical.endswith("+00:00"):
        canonical = canonical.removesuffix("+00:00").rstrip("0").rstrip(".") + "Z"
    if canonical != value:
        raise ValueError("timestamp must use canonical UTC-Z representation")
    return value


CanonicalTimestamp = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    ),
    AfterValidator(_require_utc_timestamp),
]

BudgetMetric = Literal[
    "wall_time_ms",
    "llm_tokens",
    "tool_calls",
    "provider_credits",
    "cost_microunits",
    "stage_revisions",
]
BudgetKind = Literal[
    "revision",
    "token",
    "tool_call",
    "provider_credit",
    "wall_time",
    "global_cost",
]
BudgetUnit = Literal[
    "revisions",
    "tokens",
    "calls",
    "credits",
    "milliseconds",
    "microunits",
]
BudgetPhase = Literal[
    "research",
    "text_specification",
    "formalization",
    "implementation",
    "validation",
    "training",
    "final_evaluation",
    "packaging",
]

__all__ = [
    "ArtifactIdValue",
    "BudgetKind",
    "BudgetMetric",
    "BudgetPhase",
    "BudgetUnit",
    "CanonicalTimestamp",
    "EventId",
    "NonEmptyId",
    "PrincipalIdValue",
    "ReasonCode",
    "RequestIdValue",
    "RunIdValue",
    "SequenceNo",
    "Sha256Value",
    "ValidationLevelValue",
]
