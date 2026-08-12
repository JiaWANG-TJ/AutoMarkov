from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType
from typing import Annotated, Any, Literal, Self, TypeAlias, TypeVar, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    RootModel,
    ValidationInfo,
    field_validator,
    model_validator,
)

from automarkov.canonical import (
    FrozenSequence,
    FrozenStringMapping,
    canonical_json_bytes,
    validate_and_measure_raw_json_tree,
)

RequestId = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^request_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    ),
]
PositiveSafeInt = Annotated[int, Field(strict=True, ge=1, le=9_007_199_254_740_991)]
NonNegativeSafeInt = Annotated[int, Field(strict=True, ge=0, le=9_007_199_254_740_991)]
_VALIDATED_PROVENANCE = object()
_RAW_INGRESS_VALIDATION_CONTEXT = object()


def _require_canonical_nonce(value: str) -> str:
    try:
        decoded = urlsafe_b64decode(value + "=")
    except ValueError as error:
        raise ValueError("nonce must be canonical unpadded base64url") from error
    if (
        len(decoded) != 32
        or urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value
    ):
        raise ValueError("nonce must be canonical 32-byte base64url data")
    return value


CanonicalNonce = Annotated[
    str,
    Field(strict=True, pattern=r"^[A-Za-z0-9_-]{43}$"),
    AfterValidator(_require_canonical_nonce),
]


def _exact_python_shape(value: object) -> object:
    """Capture the exact immutable Python graph accepted at trusted ingress."""

    if type(value) in {bool, bytes, float, int, str} or value is None:
        return type(value)
    if isinstance(value, Enum):
        return ("enum", type(value), _exact_python_shape(value.value))
    if isinstance(value, BaseModel):
        fields = type(value).model_fields
        if type(value.__dict__) is not dict or set(value.__dict__) != set(fields):
            raise ValueError("trusted model has an invalid field graph")
        if value.__pydantic_extra__ is not None:
            raise ValueError("trusted model has unexpected extra storage")
        return (
            "model",
            type(value),
            tuple(
                (field_name, _exact_python_shape(value.__dict__[field_name]))
                for field_name in fields
            ),
        )
    if type(value) is tuple:
        return ("tuple", tuple(_exact_python_shape(item) for item in value))
    if type(value) is MappingProxyType:
        mapping = cast(Mapping[str, object], value)
        if any(type(key) is not str for key in mapping):
            raise ValueError("trusted mapping has a non-string key")
        return (
            "mapping",
            tuple(
                (key, _exact_python_shape(mapping[key]))
                for key in sorted(mapping, key=lambda item: item.encode("utf-8"))
            ),
        )
    raise ValueError("trusted model contains an unsupported Python value")


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        validate_default=True,
        revalidate_instances="always",
    )
    _validation_provenance: object | None = PrivateAttr(default=None)
    _validation_canonical_bytes: bytes | None = PrivateAttr(default=None)
    _validation_python_shape: object | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def mark_validated_provenance(self, info: ValidationInfo) -> Self:
        private = self.__pydantic_private__
        if private is None:  # pragma: no cover - PrivateAttr guarantees storage.
            raise AssertionError("missing Pydantic private attribute storage")
        trusted_ingress = info.context is _RAW_INGRESS_VALIDATION_CONTEXT
        private["_validation_provenance"] = (
            _VALIDATED_PROVENANCE if trusted_ingress else None
        )
        private["_validation_canonical_bytes"] = (
            self._current_canonical_bytes() if trusted_ingress else None
        )
        private["_validation_python_shape"] = (
            _exact_python_shape(self) if trusted_ingress else None
        )
        return self

    def has_validated_provenance(self) -> bool:
        expected = self._validation_canonical_bytes
        expected_shape = self._validation_python_shape
        if (
            self._validation_provenance is not _VALIDATED_PROVENANCE
            or expected is None
            or expected_shape is None
        ):
            return False
        try:
            return (
                _exact_python_shape(self) == expected_shape
                and self._current_canonical_bytes() == expected
            )
        except (TypeError, ValueError):
            return False

    def _current_canonical_bytes(self) -> bytes:
        payload = self.model_dump(
            mode="json",
            round_trip=True,
            warnings="error",
        )
        if type(payload) is not dict:
            raise ValueError("trusted model must serialize to a JSON object")
        return canonical_json_bytes(payload)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        copied = super().model_copy(update=update, deep=deep)
        private = copied.__pydantic_private__
        if private is None:  # pragma: no cover - PrivateAttr guarantees storage.
            raise AssertionError("missing Pydantic private attribute storage")
        private["_validation_provenance"] = None
        private["_validation_canonical_bytes"] = None
        private["_validation_python_shape"] = None
        return copied

    def __copy__(self) -> Self:
        copied = super().__copy__()
        private = copied.__pydantic_private__
        if private is None:  # pragma: no cover - PrivateAttr guarantees storage.
            raise AssertionError("missing Pydantic private attribute storage")
        private["_validation_provenance"] = None
        private["_validation_canonical_bytes"] = None
        private["_validation_python_shape"] = None
        return copied

    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> Self:
        copied = super().__deepcopy__(memo)
        private = copied.__pydantic_private__
        if private is None:  # pragma: no cover - PrivateAttr guarantees storage.
            raise AssertionError("missing Pydantic private attribute storage")
        private["_validation_provenance"] = None
        private["_validation_canonical_bytes"] = None
        private["_validation_python_shape"] = None
        return copied


_StrictModelT = TypeVar("_StrictModelT", bound=StrictFrozenModel)


def validate_strict_frozen_payload(
    model_type: type[_StrictModelT],
    value: object,
) -> _StrictModelT:
    """仅从受限原始 JSON object tree 构建带来源标记的严格模型。"""

    if type(value) is not dict:
        raise ValueError("strict model ingress requires a raw JSON object tree")
    validate_and_measure_raw_json_tree(value)
    return model_type.model_validate(
        value,
        strict=True,
        context=_RAW_INGRESS_VALIDATION_CONTEXT,
    )


EvidenceTier: TypeAlias = Literal[
    "allowed_evidence",
    "public_dev",
    "sealed_gold",
]
PrincipalKind: TypeAlias = Literal[
    "researcher",
    "text_agent",
    "formal_agent",
    "developer",
    "unit_tester",
    "simulation_tester",
    "training_analyst",
    "training_runner",
    "sealed_evaluator",
]

_PRINCIPAL_TIERS: dict[str, frozenset[str]] = {
    "researcher": frozenset({"allowed_evidence"}),
    "text_agent": frozenset({"allowed_evidence"}),
    "formal_agent": frozenset({"allowed_evidence"}),
    "developer": frozenset({"allowed_evidence", "public_dev"}),
    "unit_tester": frozenset({"allowed_evidence", "public_dev"}),
    "simulation_tester": frozenset({"allowed_evidence", "public_dev"}),
    "training_analyst": frozenset({"public_dev"}),
    "training_runner": frozenset({"public_dev"}),
    "sealed_evaluator": frozenset({"sealed_gold"}),
}

_CapabilityId = Annotated[
    str,
    Field(strict=True, pattern=r"^capability_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
]
_PrincipalId = Annotated[
    str,
    Field(strict=True, pattern=r"^principal_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
]
_StoreId = Annotated[
    str,
    Field(strict=True, pattern=r"^store_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
]
_Digest = Annotated[str, Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]


def _require_canonical_signature(value: str) -> str:
    try:
        decoded = urlsafe_b64decode(value + "==")
    except ValueError as error:
        raise ValueError("signature must be canonical unpadded base64url") from error
    if (
        len(decoded) != 64
        or urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value
    ):
        raise ValueError("signature must be canonical 64-byte Ed25519 data")
    return value


class EvidenceStoreRef(StrictFrozenModel):
    schema_version: Literal["automarkov.evidence-store-ref.v1"]
    store_id: _StoreId
    tier: EvidenceTier
    identity_hash: _Digest


class EvidenceCapabilityGrant(StrictFrozenModel):
    schema_version: Literal["automarkov.evidence-capability-grant.v1"]
    signing_domain: Literal["AutoMarkov-Evidence-Capability-Grant-v1"]
    capability_id: _CapabilityId
    principal_id: _PrincipalId
    principal_kind: PrincipalKind
    tiers: FrozenSequence[EvidenceTier]
    store_ids: FrozenSequence[_StoreId]
    store_identity_hashes: FrozenStringMapping[_Digest]
    issuer_key_id: Annotated[
        str,
        Field(strict=True, pattern=r"^key_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
    ]
    nonce: CanonicalNonce
    signature_algorithm: Literal["Ed25519"]
    signature: Annotated[
        str,
        Field(strict=True, pattern=r"^[A-Za-z0-9_-]{86}$"),
    ]

    @model_validator(mode="after")
    def require_closed_capability_matrix(self) -> Self:
        if not self.tiers:
            raise ValueError("evidence capability requires at least one tier")
        expected = tuple(sorted(set(self.tiers), key=lambda item: item.encode("utf-8")))
        if self.tiers != expected:
            raise ValueError("evidence tiers must be sorted and unique")
        if not set(self.tiers).issubset(_PRINCIPAL_TIERS[self.principal_kind]):
            raise ValueError(
                "principal kind cannot receive the requested evidence tier"
            )
        expected_stores = tuple(
            sorted(set(self.store_ids), key=lambda item: item.encode("utf-8"))
        )
        if not self.store_ids or self.store_ids != expected_stores:
            raise ValueError("evidence capability stores must be sorted and unique")
        if tuple(self.store_identity_hashes) != self.store_ids:
            raise ValueError("evidence capability store identity keys do not match")
        _require_canonical_signature(self.signature)
        return self

    def signing_bytes(self) -> bytes:
        payload = self.model_dump(mode="json", round_trip=True, warnings="error")
        del payload["signature"]
        return canonical_json_bytes(payload)


class GenerationEvidenceView(StrictFrozenModel):
    schema_version: Literal["automarkov.generation-evidence-view.v1"]
    principal_id: _PrincipalId
    capability_grant: EvidenceCapabilityGrant
    stores: FrozenSequence[EvidenceStoreRef]

    @model_validator(mode="after")
    def bind_generation_capability(self) -> Self:
        if self.principal_id != self.capability_grant.principal_id:
            raise ValueError("generation evidence principal does not match its grant")
        if not self.stores:
            raise ValueError("generation evidence view requires at least one store")
        if self.capability_grant.principal_kind not in {
            "researcher",
            "text_agent",
            "formal_agent",
            "developer",
            "unit_tester",
            "simulation_tester",
            "training_analyst",
        }:
            raise ValueError("principal kind cannot receive a generation evidence view")
        if any(store.tier == "sealed_gold" for store in self.stores):
            raise ValueError("generation evidence view cannot include sealed gold")
        store_ids = tuple(store.store_id for store in self.stores)
        expected = tuple(sorted(set(store_ids), key=lambda item: item.encode("utf-8")))
        if store_ids != expected:
            raise ValueError("generation evidence stores must be sorted and unique")
        if any(
            store.store_id not in self.capability_grant.store_ids
            or store.tier not in self.capability_grant.tiers
            or self.capability_grant.store_identity_hashes.get(store.store_id)
            != store.identity_hash
            for store in self.stores
        ):
            raise ValueError("generation evidence stores do not match their grant")
        return self


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
                pattern=r"^artifact_[0-9a-f]{64}$",
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

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: Any | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        raise ValueError("TaskRequest JSON ingress must use validate_task_request_json")

    @field_validator("task_text")
    @classmethod
    def normalize_task_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("task_text must contain non-whitespace text")
        return normalized


def validate_task_request_payload(value: object) -> TaskRequest:
    """只从受边界保护的原始 JSON object tree 构建可信任务请求。"""

    if type(value) is not dict:
        raise ValueError("TaskRequest ingress requires a raw JSON object tree")
    validate_and_measure_raw_json_tree(value)
    return TaskRequest.model_validate(
        value,
        strict=True,
        context=_RAW_INGRESS_VALIDATION_CONTEXT,
    )


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


class VerifiedEventHead(StrictFrozenModel):
    run_id: RunId
    sequence_no: Annotated[
        int,
        Field(strict=True, ge=0, le=9_007_199_254_740_991),
    ]
    event_hash: Sha256Digest
