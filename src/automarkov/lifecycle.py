from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from threading import RLock
from types import MappingProxyType
from typing import Annotated, Literal, TypeAlias, cast
from uuid import RFC_4122, UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import (
    AfterValidator,
    Field,
    SerializeAsAny,
    TypeAdapter,
    field_validator,
    model_validator,
)

from automarkov.canonical import (
    MAX_JSON_PAYLOAD_BYTES,
    FrozenSequence,
    SafeCanonicalInt,
    canonical_json_bytes,
    parse_json_payload,
    validate_and_measure_raw_json_tree,
)
from automarkov.domain import RunState, StrictFrozenModel, VerifiedEventHead
from automarkov.errors import (
    BudgetContractError,
    EventHeadConflictError,
    EventIntegrityError,
    EventSchemaError,
    EventSequenceConflictError,
    InvalidRunTransitionError,
    RunProjectionHeadError,
    RunResumeContractError,
    RunTerminalError,
    TerminalCommitRequiredError,
)

ZERO_EVENT_HASH = "sha256:" + "0" * 64
EVENT_HASH_DOMAIN = "AutoMarkov-RunEventHash-v1"

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
SequenceNo = Annotated[int, Field(strict=True, ge=0, le=9_007_199_254_740_991)]
NonNegativeSafeInt = Annotated[SafeCanonicalInt, Field(ge=0)]
PositiveSafeInt = Annotated[SafeCanonicalInt, Field(ge=1)]


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


def _parse_run_state(value: object) -> RunState:
    if type(value) is not str:
        raise ValueError("run state must be an exact string")
    try:
        return RunState(value)
    except ValueError as error:
        raise ValueError("unknown run state") from error


RunStateValue = Annotated[RunState, AfterValidator(lambda value: value)]


class ArtifactReference(StrictFrozenModel):
    artifact_id: ArtifactIdValue
    payload_hash: Sha256Value


class EventReference(StrictFrozenModel):
    event_id: EventId
    sequence_no: SequenceNo
    event_hash: Sha256Value


BudgetMetric = Literal[
    "wall_time_ms",
    "llm_tokens",
    "tool_calls",
    "provider_credits",
    "cost_microunits",
    "stage_revisions",
]


class BudgetCounter(StrictFrozenModel):
    metric: BudgetMetric
    consumed: SafeCanonicalInt
    limit: SafeCanonicalInt

    @model_validator(mode="after")
    def require_within_limit(self) -> BudgetCounter:
        if self.consumed > self.limit:
            raise ValueError("budget consumption cannot exceed its frozen limit")
        return self


class BudgetSnapshot(StrictFrozenModel):
    schema_version: Literal["automarkov.budget-snapshot.v1"]
    contract_hash: Sha256Value
    counters: FrozenSequence[BudgetCounter]

    @field_validator("counters")
    @classmethod
    def require_closed_metric_set(
        cls, value: tuple[BudgetCounter, ...]
    ) -> tuple[BudgetCounter, ...]:
        names = tuple(counter.metric for counter in value)
        expected = tuple(sorted(set(names), key=lambda item: item.encode("utf-8")))
        if not value or names != expected:
            raise ValueError("budget counters must be nonempty, sorted, and unique")
        return value


class EventHead(StrictFrozenModel):
    run_id: RunIdValue
    sequence_no: SequenceNo
    event_hash: Sha256Value


class EmptyEventHead(StrictFrozenModel):
    head_type: Literal["empty"]


class PresentEventHead(StrictFrozenModel):
    head_type: Literal["present"]
    run_id: RunIdValue
    sequence_no: SequenceNo
    event_hash: Sha256Value


ExpectedEventHead: TypeAlias = Annotated[
    EmptyEventHead | PresentEventHead,
    Field(discriminator="head_type"),
]


class _RunEventBase(StrictFrozenModel):
    event_id: EventId
    experiment_id: NonEmptyId | None
    run_id: RunIdValue
    actor_principal_id: PrincipalIdValue
    issued_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_uuid7_time_matches_issued_at(self) -> _RunEventBase:
        event_time_ms = int.from_bytes(UUID(self.event_id).bytes[:6], "big")
        issued_time_ms = int(datetime.fromisoformat(self.issued_at).timestamp() * 1000)
        if event_time_ms != issued_time_ms:
            raise ValueError("event UUIDv7 timestamp must match issued_at")
        return self


class RunCreated(_RunEventBase):
    schema_version: Literal["automarkov.run-created.v1"]
    event_type: Literal["RunCreated"]
    signing_domain: Literal["AutoMarkov-Run-Created-v1"]
    sequence_no: Literal[0]
    previous_event_hash: Literal[
        "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    ]
    run_manifest_artifact_id: ArtifactIdValue
    run_manifest_payload_hash: Sha256Value
    initial_state: Literal[RunState.RECEIVED]
    creation_principal_id: PrincipalIdValue
    reason_code: Literal["run_created"]
    nonce_b64url: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{22}$")]
    signing_key_id: NonEmptyId
    signature_algorithm: Literal["Ed25519"]
    signature_b64url: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{86}$")]

    @model_validator(mode="after")
    def require_canonical_nonce(self) -> RunCreated:
        try:
            nonce = base64.urlsafe_b64decode(self.nonce_b64url + "==")
        except ValueError as error:
            raise ValueError("nonce must be canonical base64url") from error
        if (
            len(nonce) != 16
            or base64.urlsafe_b64encode(nonce).decode().rstrip("=") != self.nonce_b64url
        ):
            raise ValueError("nonce must contain exactly 128 canonical bits")
        if self.creation_principal_id != self.actor_principal_id:
            raise ValueError("run creation principal must be the authenticated actor")
        return self


class _NonRootRunEvent(_RunEventBase):
    sequence_no: SequenceNo
    previous_event_hash: Sha256Value


class _UnsignedNonRootRunEvent(_NonRootRunEvent):
    actor_process_execution_id: NonEmptyId | None


class StateTransitioned(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.state-transitioned.v1"]
    event_type: Literal["StateTransitioned"]
    from_state: RunState
    to_state: RunState
    trigger_event_id: EventId
    trigger_event_hash: Sha256Value
    input_artifact_ids: FrozenSequence[ArtifactIdValue]
    gate_report_artifact_id: ArtifactIdValue | None
    gate_report_payload_hash: Sha256Value | None
    budget_snapshot_artifact_id: ArtifactIdValue
    budget_snapshot_payload_hash: Sha256Value
    reason_code: ReasonCode

    @field_validator("from_state", "to_state", mode="before")
    @classmethod
    def parse_state(cls, value: object) -> RunState:
        return _parse_run_state(value)

    @field_validator("input_artifact_ids")
    @classmethod
    def require_canonical_inputs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value), key=lambda item: item.encode("utf-8"))):
            raise ValueError("input_artifact_ids must be sorted and unique")
        return value

    @model_validator(mode="after")
    def require_gate_reference_pair(self) -> StateTransitioned:
        if (self.gate_report_artifact_id is None) != (
            self.gate_report_payload_hash is None
        ):
            raise ValueError("gate report artifact ID/hash must be paired")
        return self


class ArtifactSuperseded(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.artifact-superseded.v1"]
    event_type: Literal["ArtifactSuperseded"]
    old_artifact: ArtifactReference
    new_artifact: ArtifactReference
    lineage_report: ArtifactReference
    supersession_reason_code: Literal[
        "approval_revoked",
        "revision_replaced",
        "validation_failed",
        "upstream_invalidated",
        "reduction_approved",
    ]

    @model_validator(mode="after")
    def require_changed_artifact(self) -> ArtifactSuperseded:
        if self.old_artifact == self.new_artifact:
            raise ValueError("artifact supersession requires a new artifact identity")
        return self


_STAGE_GATE_CONTRACTS: Mapping[
    tuple[RunState, RunState],
    tuple[str, str],
] = MappingProxyType(
    {
        (RunState.RECEIVED, RunState.RESEARCHING): (
            "INTAKE_SCHEMA_BUDGET_AUTHORITY",
            "intake_accepted",
        ),
        (RunState.RESEARCHING, RunState.TEXT_DRAFTED): (
            "EVIDENCE_LEDGER_CLOSURE",
            "research_completed",
        ),
        (RunState.TEXT_DRAFTED, RunState.TEXT_REVIEWED): (
            "TEXT_SCHEMA",
            "text_schema_passed",
        ),
        (RunState.TEXT_REVIEWED, RunState.WAITING_TEXT_CONFIRMATION): (
            "TEXT_CRITIC_REVIEW",
            "text_review_passed",
        ),
        (RunState.TEXT_LOCKED, RunState.CLASSIFIED): (
            "CLASSIFICATION_BINDING",
            "classification_passed",
        ),
        (RunState.CLASSIFIED, RunState.FORMAL_DRAFTED): (
            "CLASSIFICATION_IN_SCOPE",
            "in_scope_classification_selected",
        ),
        (RunState.CLASSIFIED, RunState.REDUCTION_PROPOSAL_DRAFTING): (
            "CLASSIFICATION_REDUCTION",
            "reduction_required",
        ),
        (RunState.CLASSIFIED, RunState.OOD_HANDOFF_BUILDING): (
            "CLASSIFICATION_OOD",
            "ood_classification_selected",
        ),
        (
            RunState.REDUCTION_PROPOSAL_DRAFTING,
            RunState.WAITING_REDUCTION_CONFIRMATION,
        ): ("REDUCTION_PROPOSAL", "reduction_proposal_ready"),
        (RunState.OOD_HANDOFF_BUILDING, RunState.OOD_HANDOFF_VALIDATING): (
            "OOD_HANDOFF_BUILD",
            "ood_handoff_built",
        ),
        (RunState.FORMAL_DRAFTED, RunState.FORMAL_REVIEWED): (
            "FORMAL_SCHEMA_STRUCTURAL",
            "formal_schema_passed",
        ),
        (RunState.FORMAL_REVIEWED, RunState.WAITING_FORMAL_CONFIRMATION): (
            "FORMAL_CRITIC_REVIEW",
            "formal_review_passed",
        ),
        (RunState.FORMAL_LOCKED, RunState.IMPLEMENTATION_SELECTED): (
            "FORMAL_LOCK_CLOSURE",
            "formal_contract_locked",
        ),
        (RunState.IMPLEMENTATION_SELECTED, RunState.ENVIRONMENT_IMPLEMENTED): (
            "IMPLEMENTATION_ROUTE_SELECTION",
            "implementation_completed",
        ),
        (RunState.ENVIRONMENT_IMPLEMENTED, RunState.UNIT_VALIDATING): (
            "ENVIRONMENT_ARTIFACT_FREEZE",
            "environment_artifacts_frozen",
        ),
        (RunState.UNIT_VALIDATING, RunState.SIMULATION_VALIDATING): (
            "UNIT_VALIDATION",
            "unit_validation_passed",
        ),
        (RunState.SIMULATION_VALIDATING, RunState.SEALED_E2E_VALIDATING): (
            "PUBLIC_SIMULATION_TESTER",
            "public_simulation_passed",
        ),
        (RunState.SEALED_E2E_VALIDATING, RunState.TRAINING_SMOKE_TESTING): (
            "SEALED_E2E",
            "sealed_e2e_passed",
        ),
        (RunState.TRAINING_SMOKE_TESTING, RunState.POLICY_TRAINING): (
            "TRAINING_SMOKE",
            "training_smoke_passed",
        ),
        (RunState.POLICY_TRAINING, RunState.FINAL_EVALUATING): (
            "POLICY_TRAINING",
            "policy_training_completed",
        ),
        (RunState.FINAL_EVALUATING, RunState.PACKAGING): (
            "FINAL_EVALUATION",
            "final_evaluation_completed",
        ),
    }
)


class StageGatePassed(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.stage-gate-passed.v1"]
    event_type: Literal["StageGatePassed"]
    gate_id: NonEmptyId
    gate_version: NonEmptyId
    gate_contract_hash: Sha256Value
    subject_artifact_references: FrozenSequence[ArtifactReference]
    gate_report: ArtifactReference
    from_state: RunState
    to_state: RunState
    reason_code: ReasonCode
    result: Literal["passed"]

    @field_validator("from_state", "to_state", mode="before")
    @classmethod
    def parse_state(cls, value: object) -> RunState:
        return _parse_run_state(value)

    @field_validator("subject_artifact_references")
    @classmethod
    def require_canonical_subjects(
        cls,
        value: tuple[ArtifactReference, ...],
    ) -> tuple[ArtifactReference, ...]:
        keys = tuple((item.artifact_id, item.payload_hash) for item in value)
        if keys != tuple(sorted(set(keys), key=lambda item: item[0].encode("utf-8"))):
            raise ValueError("stage gate subjects must be sorted and unique")
        return value

    @model_validator(mode="after")
    def require_frozen_gate_contract(self) -> StageGatePassed:
        contract = _STAGE_GATE_CONTRACTS.get((self.from_state, self.to_state))
        if contract != (self.gate_id, self.reason_code):
            raise ValueError("stage gate does not match the frozen state-table edge")
        return self

    def matches_transition(self, transition: StateTransitioned) -> bool:
        subject_ids = tuple(
            reference.artifact_id for reference in self.subject_artifact_references
        )
        return (
            transition.from_state is self.from_state
            and transition.to_state is self.to_state
            and transition.reason_code == self.reason_code
            and transition.input_artifact_ids == subject_ids
            and transition.gate_report_artifact_id == self.gate_report.artifact_id
            and transition.gate_report_payload_hash == self.gate_report.payload_hash
        )


class RuntimeReady(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.runtime-ready.v1"]
    event_type: Literal["RuntimeReady"]
    dependency_kind: Literal["local_llm", "runtime_profile", "remote_service"]
    dependency_identity_hash: Sha256Value
    profile_id: NonEmptyId | None
    process_execution_id: NonEmptyId | None
    protocol_edge_id: NonEmptyId | None
    readiness_report: ArtifactReference
    passed_gate_id: NonEmptyId

    @model_validator(mode="after")
    def require_dependency_identity(self) -> RuntimeReady:
        if (
            self.dependency_kind == "local_llm"
            and (self.profile_id is None or self.process_execution_id is None)
            or self.dependency_kind == "runtime_profile"
            and self.profile_id is None
            or self.dependency_kind == "remote_service"
            and self.protocol_edge_id is None
        ):
            raise ValueError("runtime readiness identity is incomplete")
        return self


class LlmRuntimeDegraded(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.llm-runtime-degraded.v1"]
    event_type: Literal["LlmRuntimeDegraded"]
    dependency_identity_hash: Sha256Value
    failed_gate_id: NonEmptyId
    failure_report: ArtifactReference
    affected_state: RunState

    @field_validator("affected_state", mode="before")
    @classmethod
    def parse_state(cls, value: object) -> RunState:
        return _parse_run_state(value)


class EvidenceSlotStateCounts(StrictFrozenModel):
    available: NonNegativeSafeInt
    leased: NonNegativeSafeInt
    cooldown: NonNegativeSafeInt
    invalid_credential: NonNegativeSafeInt


class EvidenceTemporarilyUnavailable(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.evidence-temporarily-unavailable.v1"]
    event_type: Literal["EvidenceTemporarilyUnavailable"]
    lease_pool_artifact_id: ArtifactIdValue
    lease_pool_payload_hash: Sha256Value
    lease_snapshot_artifact_id: ArtifactIdValue
    lease_snapshot_payload_hash: Sha256Value
    availability_probe_artifact_id: ArtifactIdValue
    availability_probe_payload_hash: Sha256Value
    slot_state_counts: EvidenceSlotStateCounts
    earliest_availability: CanonicalTimestamp

    @model_validator(mode="after")
    def require_future_availability(self) -> EvidenceTemporarilyUnavailable:
        if datetime.fromisoformat(self.earliest_availability) < datetime.fromisoformat(
            self.issued_at
        ):
            raise ValueError("earliest evidence availability precedes the probe")
        return self


class EvidenceAuthorityRequired(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.evidence-authority-required.v1"]
    event_type: Literal["EvidenceAuthorityRequired"]
    lease_pool_artifact_id: ArtifactIdValue
    lease_pool_payload_hash: Sha256Value
    lease_snapshot_artifact_id: ArtifactIdValue
    lease_snapshot_payload_hash: Sha256Value
    slot_state_counts: EvidenceSlotStateCounts
    external_authority_principal_id: PrincipalIdValue
    resolution_condition_hash: Sha256Value
    failure_report_artifact_id: ArtifactIdValue
    failure_report_payload_hash: Sha256Value
    reason_code: Literal["evidence_authority_required"]


class WaitingRuntime(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.waiting-runtime.v1"]
    event_type: Literal["WaitingRuntime"]
    resume_state: RunState
    wait_reason_code: Literal[
        "local_llm_unavailable",
        "runtime_profile_unavailable",
        "remote_service_unavailable",
    ]
    trigger_event_id: EventId
    trigger_event_hash: Sha256Value
    failure_report_artifact_id: ArtifactIdValue
    failure_report_payload_hash: Sha256Value
    recovery_gate_id: NonEmptyId
    recovery_condition_hash: Sha256Value
    entered_at: CanonicalTimestamp
    dependency_kind: Literal["local_llm", "runtime_profile", "remote_service"]
    profile_id: NonEmptyId | None
    process_execution_id: NonEmptyId | None
    protocol_edge_id: NonEmptyId | None
    dependency_identity_hash: Sha256Value
    failed_readiness_gate_id: NonEmptyId

    @field_validator("resume_state", mode="before")
    @classmethod
    def parse_state(cls, value: object) -> RunState:
        return _parse_run_state(value)

    @model_validator(mode="after")
    def require_exact_runtime_wait(self) -> WaitingRuntime:
        expected_reason = {
            "local_llm": "local_llm_unavailable",
            "runtime_profile": "runtime_profile_unavailable",
            "remote_service": "remote_service_unavailable",
        }[self.dependency_kind]
        if (
            self.wait_reason_code != expected_reason
            or self.recovery_gate_id != self.failed_readiness_gate_id
            or self.entered_at != self.issued_at
        ):
            raise ValueError("runtime wait reason, gate, or entry time is inconsistent")
        return self


class WaitingEvidence(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.waiting-evidence.v1"]
    event_type: Literal["WaitingEvidence"]
    resume_state: RunState
    wait_reason_code: Literal[
        "evidence_pool_cooldown",
        "evidence_pool_leased",
        "evidence_temporarily_unavailable",
    ]
    trigger_event_id: EventId
    trigger_event_hash: Sha256Value
    failure_report_artifact_id: ArtifactIdValue
    failure_report_payload_hash: Sha256Value
    recovery_gate_id: NonEmptyId
    recovery_condition_hash: Sha256Value
    entered_at: CanonicalTimestamp
    lease_pool_artifact_id: ArtifactIdValue
    lease_pool_payload_hash: Sha256Value
    lease_snapshot_artifact_id: ArtifactIdValue
    lease_snapshot_payload_hash: Sha256Value
    lease_identity_hash: Sha256Value
    earliest_availability: CanonicalTimestamp

    @field_validator("resume_state", mode="before")
    @classmethod
    def parse_state(cls, value: object) -> RunState:
        return _parse_run_state(value)

    @model_validator(mode="after")
    def require_exact_evidence_wait(self) -> WaitingEvidence:
        if self.entered_at != self.issued_at or datetime.fromisoformat(
            self.earliest_availability
        ) < datetime.fromisoformat(self.entered_at):
            raise ValueError("evidence wait timestamps are inconsistent")
        return self


class WaitingAsset(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.waiting-asset.v1"]
    event_type: Literal["WaitingAsset"]
    resume_state: RunState
    wait_reason_code: Literal[
        "asset_unavailable",
        "license_pending",
        "provisioning_pending",
    ]
    trigger_event_id: EventId
    trigger_event_hash: Sha256Value
    failure_report_artifact_id: ArtifactIdValue
    failure_report_payload_hash: Sha256Value
    recovery_gate_id: NonEmptyId
    recovery_condition_hash: Sha256Value
    entered_at: CanonicalTimestamp
    asset_identity_hash: Sha256Value
    license_identity_hash: Sha256Value
    provisioning_authority_principal_id: PrincipalIdValue

    @field_validator("resume_state", mode="before")
    @classmethod
    def parse_state(cls, value: object) -> RunState:
        return _parse_run_state(value)

    @model_validator(mode="after")
    def require_exact_asset_wait(self) -> WaitingAsset:
        if self.entered_at != self.issued_at:
            raise ValueError("asset wait entry time must match event issuance")
        return self


class Blocked(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.blocked.v1"]
    event_type: Literal["Blocked"]
    resume_state: RunState
    block_reason_code: Literal[
        "credential_required",
        "license_authority_required",
        "provisioning_authority_required",
        "user_decision_required",
        "evidence_authority_required",
    ]
    external_authority_kind: Literal[
        "credential",
        "license",
        "provisioning",
        "user_decision",
        "evidence_authority",
    ]
    external_authority_principal_id: PrincipalIdValue
    resolution_condition_hash: Sha256Value
    failure_report_artifact_id: ArtifactIdValue
    failure_report_payload_hash: Sha256Value
    recheck_gate_id: NonEmptyId
    entered_at: CanonicalTimestamp

    @field_validator("resume_state", mode="before")
    @classmethod
    def parse_state(cls, value: object) -> RunState:
        return _parse_run_state(value)

    @model_validator(mode="after")
    def require_exact_block(self) -> Blocked:
        expected_kind = {
            "credential_required": "credential",
            "license_authority_required": "license",
            "provisioning_authority_required": "provisioning",
            "user_decision_required": "user_decision",
            "evidence_authority_required": "evidence_authority",
        }[self.block_reason_code]
        if (
            self.external_authority_kind != expected_kind
            or self.entered_at != self.issued_at
        ):
            raise ValueError("block reason, authority, or entry time is inconsistent")
        return self


RunBlocked = Blocked


class WaitResolved(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.wait-resolved.v1"]
    event_type: Literal["WaitResolved"]
    wait_kind: Literal["runtime", "evidence", "asset"]
    waiting_event_id: EventId
    waiting_event_hash: Sha256Value
    resume_state: RunState
    recovery_gate_id: NonEmptyId
    recovery_report_artifact_id: ArtifactIdValue
    recovery_report_payload_hash: Sha256Value
    identity_hash: Sha256Value
    resolved_at: CanonicalTimestamp

    @field_validator("resume_state", mode="before")
    @classmethod
    def parse_state(cls, value: object) -> RunState:
        return _parse_run_state(value)

    @model_validator(mode="after")
    def require_resolution_time(self) -> WaitResolved:
        if self.resolved_at != self.issued_at:
            raise ValueError("wait resolution time must match event issuance")
        return self


class BlockResolved(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.block-resolved.v1"]
    event_type: Literal["BlockResolved"]
    blocked_event_id: EventId
    blocked_event_hash: Sha256Value
    authority_principal_id: PrincipalIdValue
    resolution_evidence_artifact_id: ArtifactIdValue
    resolution_evidence_payload_hash: Sha256Value
    revalidation_report_artifact_id: ArtifactIdValue
    revalidation_report_payload_hash: Sha256Value


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


class _BudgetExhaustionBase(_UnsignedNonRootRunEvent):
    budget_kind: BudgetKind
    budget_policy_artifact_id: ArtifactIdValue
    budget_policy_payload_hash: Sha256Value
    budget_snapshot_artifact_id: ArtifactIdValue
    budget_snapshot_payload_hash: Sha256Value
    canonical_unit: BudgetUnit
    limit: NonNegativeSafeInt
    consumed: NonNegativeSafeInt
    reserved: NonNegativeSafeInt
    cause_receipt_artifact_id: ArtifactIdValue
    cause_receipt_payload_hash: Sha256Value
    phase: BudgetPhase
    reason_code: Literal["budget_exhausted"]
    exhausted_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_reached_limit(self) -> _BudgetExhaustionBase:
        expected_unit = {
            "revision": "revisions",
            "token": "tokens",
            "tool_call": "calls",
            "provider_credit": "credits",
            "wall_time": "milliseconds",
            "global_cost": "microunits",
        }[self.budget_kind]
        if (
            self.canonical_unit != expected_unit
            or self.consumed + self.reserved < self.limit
            or self.exhausted_at != self.issued_at
        ):
            raise ValueError("budget proof does not establish exact exhaustion")
        return self


class BudgetExhausted(_BudgetExhaustionBase):
    schema_version: Literal["automarkov.budget-exhausted.v1"]
    event_type: Literal["BudgetExhausted"]


class EvidenceBudgetExhausted(_BudgetExhaustionBase):
    schema_version: Literal["automarkov.evidence-budget-exhausted.v1"]
    event_type: Literal["EvidenceBudgetExhausted"]
    registered_account_receipts: FrozenSequence[ArtifactReference]

    @field_validator("registered_account_receipts")
    @classmethod
    def require_closed_receipt_set(
        cls, value: tuple[ArtifactReference, ...]
    ) -> tuple[ArtifactReference, ...]:
        keys = tuple((item.artifact_id, item.payload_hash) for item in value)
        if not value or keys != tuple(
            sorted(set(keys), key=lambda item: item[0].encode("utf-8"))
        ):
            raise ValueError("provider receipts must be nonempty, sorted, and unique")
        return value


class ClarificationRequested(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.clarification-requested.v1"]
    event_type: Literal["ClarificationRequested"]
    task: ArtifactReference
    review: ArtifactReference
    result: ArtifactReference
    gap_ids: FrozenSequence[NonEmptyId]
    clarification_policy: ArtifactReference
    reason_code: Literal["clarification_required"]

    @field_validator("gap_ids")
    @classmethod
    def require_closed_gap_set(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or value != tuple(
            sorted(set(value), key=lambda item: item.encode("utf-8"))
        ):
            raise ValueError("clarification gaps must be nonempty, sorted, and unique")
        return value


class ValidationClaimed(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.validation-claimed.v1"]
    event_type: Literal["ValidationClaimed"]
    claim: ArtifactReference
    subject: ArtifactReference
    report: ArtifactReference
    validator_id: NonEmptyId
    validator_version: NonEmptyId
    validation_level: Literal["terminal"]
    validation_scope: Literal["ood_handoff", "package"]


class ValidationFailed(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.validation-failed.v1"]
    event_type: Literal["ValidationFailed"]
    subject: ArtifactReference
    report: ArtifactReference
    validator_id: NonEmptyId
    validator_version: NonEmptyId
    validation_level: Literal["terminal"]
    validation_scope: Literal[
        "approval",
        "classification",
        "formalization",
        "ood_handoff",
        "sealed_e2e",
        "training_smoke",
        "policy_training",
        "final_evaluation",
        "packaging",
        "internal",
    ]
    failure_code: Literal[
        "invalid_signature_or_binding",
        "artifact_identity_or_parent_mismatch",
        "unrecoverable_internal_error",
        "protocol_integrity_violation",
        "sealed_e2e_gate_failed",
        "training_smoke_failed",
        "required_evaluation_result_missing",
        "required_package_artifact_missing",
        "secret_or_license_violation",
    ]


class RunTerminationRequested(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.run-termination-requested.v1"]
    event_type: Literal["RunTerminationRequested"]
    requested_terminal_state: Literal[RunState.PARTIAL, RunState.CANCELLED]
    requesting_authority_principal_id: PrincipalIdValue
    request_evidence: ArtifactReference | None
    reason_code: Literal[
        "user_cancelled",
        "continuation_declined",
        "asset_unavailable",
        "partial_accepted",
    ]

    @field_validator("requested_terminal_state", mode="before")
    @classmethod
    def parse_terminal_state(cls, value: object) -> RunState:
        return _parse_run_state(value)

    @model_validator(mode="after")
    def require_termination_reason(self) -> RunTerminationRequested:
        if (self.reason_code == "user_cancelled") != (
            self.requested_terminal_state is RunState.CANCELLED
        ):
            raise ValueError("termination reason does not match requested state")
        return self


class ArtifactAccessRevoked(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.artifact-access-revoked.v1"]
    event_type: Literal["ArtifactAccessRevoked"]
    subject: ArtifactReference
    governance_policy: ArtifactReference
    revocation_authority_principal_id: PrincipalIdValue
    reason_code: Literal[
        "privacy_request",
        "legal_requirement",
        "retention_policy",
        "access_policy_revoked",
    ]
    effective_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_effective_time(self) -> ArtifactAccessRevoked:
        if datetime.fromisoformat(self.effective_at) < datetime.fromisoformat(
            self.issued_at
        ):
            raise ValueError("artifact revocation predates its event")
        return self


class SpecificationConflictDetected(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.specification-conflict-detected.v1"]
    event_type: Literal["SpecificationConflictDetected"]
    specification: ArtifactReference
    first_conflict_locus_id: NonEmptyId
    first_conflict_locus_hash: Sha256Value
    second_conflict_locus_id: NonEmptyId
    second_conflict_locus_hash: Sha256Value
    affected_contract_ids: FrozenSequence[NonEmptyId]
    conflict_code: Literal[
        "normative_example_conflict",
        "cross_section_contract_conflict",
        "schema_contract_conflict",
    ]

    @field_validator("affected_contract_ids")
    @classmethod
    def require_canonical_contracts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or value != tuple(
            sorted(set(value), key=lambda item: item.encode("utf-8"))
        ):
            raise ValueError("affected contract IDs must be nonempty and canonical")
        return value

    @model_validator(mode="after")
    def require_distinct_conflict_loci(self) -> SpecificationConflictDetected:
        if self.first_conflict_locus_id == self.second_conflict_locus_id:
            raise ValueError("specification conflict loci must be distinct")
        return self


class ClarificationEvaluationRequested(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.clarification-evaluation-requested.v1"]
    event_type: Literal["ClarificationEvaluationRequested"]
    evaluation_request: ArtifactReference
    terminal_result: ArtifactReference
    terminal_event: EventReference
    terminal_snapshot_event_head_hash: Sha256Value

    @model_validator(mode="after")
    def require_terminal_snapshot(self) -> ClarificationEvaluationRequested:
        if self.terminal_snapshot_event_head_hash != self.terminal_event.event_hash:
            raise ValueError("clarification request terminal snapshot is inconsistent")
        return self


class ClarificationEvaluationRecorded(_UnsignedNonRootRunEvent):
    schema_version: Literal["automarkov.clarification-evaluation-recorded.v1"]
    event_type: Literal["ClarificationEvaluationRecorded"]
    evaluation_request: ArtifactReference
    evaluation_verdict: ArtifactReference
    terminal_result: ArtifactReference
    terminal_event: EventReference
    terminal_snapshot_event_head_hash: Sha256Value

    @model_validator(mode="after")
    def require_terminal_snapshot(self) -> ClarificationEvaluationRecorded:
        if self.terminal_snapshot_event_head_hash != self.terminal_event.event_hash:
            raise ValueError("clarification verdict terminal snapshot is inconsistent")
        return self


class SignedApprovalEvent(_NonRootRunEvent):
    schema_version: Literal["automarkov.approval-event.v1"]
    signing_domain: Literal["AutoMarkov-Approval-v1"]
    event_type: Literal["SignedApprovalEvent"]
    experiment_id: NonEmptyId | None
    decision: Literal["approved", "rejected", "revoked"]
    artifact: ArtifactReference
    supersedes_approval_event_id: EventId | None
    approval_principal_id: PrincipalIdValue
    approval_principal_kind: Literal[
        "interactive_user",
        "experiment_approval_policy",
        "registered_revocation_policy",
    ]
    approval_policy_source_hash: Sha256Value | None
    input_report_artifact_ids: FrozenSequence[ArtifactIdValue]
    reason_code: ReasonCode
    nonce_b64url: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{22}$")]
    signing_key_id: NonEmptyId
    signature_algorithm: Literal["Ed25519"]
    signature_b64url: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{86}$")]

    @model_validator(mode="after")
    def require_decision_branch(self) -> SignedApprovalEvent:
        if (self.decision == "revoked") != (
            self.supersedes_approval_event_id is not None
        ):
            raise ValueError("approval supersession field does not match decision")
        if self.approval_principal_id != self.actor_principal_id:
            raise ValueError("approval principal must be the authenticated actor")
        reports = self.input_report_artifact_ids
        if reports != tuple(
            sorted(set(reports), key=lambda item: item.encode("utf-8"))
        ):
            raise ValueError("input report artifact IDs must be sorted and unique")
        try:
            nonce = base64.urlsafe_b64decode(self.nonce_b64url + "==")
        except ValueError as error:
            raise ValueError("nonce must be canonical base64url") from error
        if (
            len(nonce) != 16
            or base64.urlsafe_b64encode(nonce).decode().rstrip("=") != self.nonce_b64url
        ):
            raise ValueError("nonce must contain exactly 128 canonical bits")
        return self


class _SignedRunEventBase(StrictFrozenModel):
    event_id: EventId
    experiment_id: NonEmptyId | None
    run_id: RunIdValue
    issued_at: CanonicalTimestamp
    nonce_b64url: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{22}$")]
    signing_key_id: NonEmptyId
    signature_b64url: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{86}$")]

    @model_validator(mode="after")
    def require_signed_event_identity(self) -> _SignedRunEventBase:
        event_time_ms = int.from_bytes(UUID(self.event_id).bytes[:6], "big")
        issued_time_ms = int(datetime.fromisoformat(self.issued_at).timestamp() * 1000)
        if event_time_ms != issued_time_ms:
            raise ValueError("event UUIDv7 timestamp must match issued_at")
        try:
            nonce = base64.urlsafe_b64decode(self.nonce_b64url + "==")
        except ValueError as error:
            raise ValueError("nonce must be canonical base64url") from error
        if (
            len(nonce) != 16
            or base64.urlsafe_b64encode(nonce).decode().rstrip("=") != self.nonce_b64url
        ):
            raise ValueError("nonce must contain exactly 128 canonical bits")
        try:
            signature = base64.urlsafe_b64decode(self.signature_b64url + "==")
        except ValueError as error:
            raise ValueError("signature must be canonical Ed25519 base64url") from error
        if (
            len(signature) != 64
            or base64.urlsafe_b64encode(signature).decode().rstrip("=")
            != self.signature_b64url
        ):
            raise ValueError("signature must be canonical Ed25519 base64url")
        return self


class RunSuperseded(_SignedRunEventBase):
    schema_version: Literal["automarkov.run-superseded.v1"]
    event_type: Literal["RunSuperseded"]
    signing_domain: Literal["AutoMarkov-Run-Superseded-v1"]
    sequence_no: SequenceNo
    previous_event_hash: Sha256Value
    supersession_cause: Literal[
        "runtime_identity_replacement",
        "approval_revocation",
    ]
    child_run_id: RunIdValue
    replacement_ordinal: PositiveSafeInt
    old_run_manifest_artifact_id: ArtifactIdValue
    old_run_manifest_payload_hash: Sha256Value
    child_run_manifest_artifact_id: ArtifactIdValue
    child_run_manifest_payload_hash: Sha256Value
    replacement_policy_artifact_id: ArtifactIdValue
    replacement_policy_payload_hash: Sha256Value
    replacement_eligibility: Literal[
        "confirmatory_slot_reused",
        "new_nonconfirmatory_slot",
        "slot_terminal_failure",
    ]
    replacement_authority_principal_id: PrincipalIdValue
    reason_code: Literal[
        "runtime_identity_replacement",
        "approval_revocation",
    ]
    failed_waiting_event_id: EventId | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    failed_readiness_gate_id: NonEmptyId | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    old_dependency_identity_hash: Sha256Value | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    new_dependency_identity_hash: Sha256Value | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    revocation_event_id: EventId | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    revoked_approval_event_id: EventId | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    artifact_id: ArtifactIdValue | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    artifact_payload_hash: Sha256Value | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def require_exact_cause_branch(self) -> RunSuperseded:
        runtime_fields = {
            "failed_waiting_event_id",
            "failed_readiness_gate_id",
            "old_dependency_identity_hash",
            "new_dependency_identity_hash",
        }
        approval_fields = {
            "revocation_event_id",
            "revoked_approval_event_id",
            "artifact_id",
            "artifact_payload_hash",
        }
        required = (
            runtime_fields
            if self.supersession_cause == "runtime_identity_replacement"
            else approval_fields
        )
        forbidden = approval_fields if required is runtime_fields else runtime_fields
        if (
            not required.issubset(self.model_fields_set)
            or forbidden.intersection(self.model_fields_set)
            or self.reason_code != self.supersession_cause
            or self.child_run_id == self.run_id
        ):
            raise ValueError("run supersession cause branch is invalid")
        if (
            self.supersession_cause == "runtime_identity_replacement"
            and self.old_dependency_identity_hash == self.new_dependency_identity_hash
        ):
            raise ValueError("replacement dependency identity must change")
        return self


class ReplacementRunCreated(_SignedRunEventBase):
    schema_version: Literal["automarkov.replacement-run-created.v1"]
    event_type: Literal["ReplacementRunCreated"]
    signing_domain: Literal["AutoMarkov-Replacement-Run-Created-v1"]
    sequence_no: Literal[0]
    previous_event_hash: Literal[
        "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    ]
    run_manifest_artifact_id: ArtifactIdValue
    run_manifest_payload_hash: Sha256Value
    parent_run_id: RunIdValue
    parent_run_superseded_event_id: EventId
    supersession_cause: Literal[
        "runtime_identity_replacement",
        "approval_revocation",
    ]
    replacement_ordinal: PositiveSafeInt
    replacement_policy_artifact_id: ArtifactIdValue
    replacement_policy_payload_hash: Sha256Value
    replacement_authority_principal_id: PrincipalIdValue

    @model_validator(mode="after")
    def require_distinct_parent(self) -> ReplacementRunCreated:
        if self.parent_run_id == self.run_id:
            raise ValueError("replacement child must use a distinct run identity")
        return self


class ClarificationChildRunCreated(_SignedRunEventBase):
    schema_version: Literal["automarkov.clarification-child-run-created.v1"]
    event_type: Literal["ClarificationChildRunCreated"]
    signing_domain: Literal["AutoMarkov-Clarification-Child-Run-Created-v1"]
    sequence_no: Literal[0]
    previous_event_hash: Literal[
        "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    ]
    run_manifest_artifact_id: ArtifactIdValue
    run_manifest_payload_hash: Sha256Value
    parent_run_id: RunIdValue
    parent_clarification_result_artifact_id: ArtifactIdValue
    parent_clarification_result_payload_hash: Sha256Value
    parent_terminal_result_artifact_id: ArtifactIdValue
    parent_terminal_result_payload_hash: Sha256Value
    parent_terminal_snapshot_event_head_hash: Sha256Value
    signed_answer_bundle_artifact_id: ArtifactIdValue
    signed_answer_bundle_payload_hash: Sha256Value
    continuation_policy_artifact_id: ArtifactIdValue
    continuation_policy_payload_hash: Sha256Value
    clarification_continuation_ordinal: PositiveSafeInt
    continuation_authority_principal_id: PrincipalIdValue
    reason_code: Literal["clarification_answer_received"]

    @model_validator(mode="after")
    def require_distinct_parent(self) -> ClarificationChildRunCreated:
        if self.parent_run_id == self.run_id:
            raise ValueError("clarification child must use a distinct run identity")
        return self


class GateOmittedByDesign(_SignedRunEventBase):
    schema_version: Literal["automarkov.gate-omitted-event.v1"]
    event_type: Literal["GateOmittedByDesign"]
    signing_domain: Literal["AutoMarkov-Gate-Omitted-v1"]
    sequence_no: SequenceNo
    previous_event_hash: Sha256Value
    track: Literal["AUTO"]
    variant_id: Literal[
        "v1_canonical",
        "v2_paraphrased",
        "v3_reordered_longform",
        "v4_evidence_split",
    ]
    cell_id: NonEmptyId
    ablation_execution_plan_artifact_id: ArtifactIdValue
    ablation_execution_plan_hash: Sha256Value
    pair_binding_id: NonEmptyId
    task_card_artifact_id: ArtifactIdValue
    subject_artifact_ids: FrozenSequence[ArtifactIdValue]
    expected_missing_artifact_kinds: FrozenSequence[NonEmptyId]
    output_artifact_ids: FrozenSequence[ArtifactIdValue]
    reason: Literal["controlled_ablation"]
    ablation_method_id: Literal[
        "automarkov_no_evidence",
        "automarkov_no_text_critic",
        "automarkov_no_formal_critic",
        "automarkov_no_simulation_tester",
        "automarkov_no_training_feedback",
    ]
    omitted_gate_id: Literal[
        "EVIDENCE_LEDGER_CLOSURE",
        "TEXT_CRITIC_REVIEW",
        "FORMAL_CRITIC_REVIEW",
        "PUBLIC_SIMULATION_TESTER",
        "PUBLIC_DEV_LEARNING_PROBE_AND_ROLLBACK",
    ]

    @model_validator(mode="after")
    def require_exact_omission_branch(self) -> GateOmittedByDesign:
        contracts = {
            "EVIDENCE_LEDGER_CLOSURE": (
                "automarkov_no_evidence",
                0,
                ("EvidenceLedger",),
                1,
            ),
            "TEXT_CRITIC_REVIEW": (
                "automarkov_no_text_critic",
                1,
                ("TextCriticReport",),
                0,
            ),
            "FORMAL_CRITIC_REVIEW": (
                "automarkov_no_formal_critic",
                1,
                ("FormalCriticReport",),
                0,
            ),
            "PUBLIC_SIMULATION_TESTER": (
                "automarkov_no_simulation_tester",
                1,
                (
                    "PropertyTestReport",
                    "MetamorphicTestReport",
                    "DifferentialTestReport",
                    "TrajectoryTestReport",
                ),
                0,
            ),
            "PUBLIC_DEV_LEARNING_PROBE_AND_ROLLBACK": (
                "automarkov_no_training_feedback",
                1,
                ("PublicDevLearningProbeReport",),
                0,
            ),
        }
        method, subject_count, missing_kinds, output_count = contracts[
            self.omitted_gate_id
        ]
        canonical_subjects = tuple(
            sorted(
                set(self.subject_artifact_ids), key=lambda item: item.encode("utf-8")
            )
        )
        canonical_outputs = tuple(
            sorted(set(self.output_artifact_ids), key=lambda item: item.encode("utf-8"))
        )
        if (
            self.experiment_id is None
            or self.ablation_method_id != method
            or len(self.subject_artifact_ids) != subject_count
            or self.subject_artifact_ids != canonical_subjects
            or self.expected_missing_artifact_kinds != missing_kinds
            or len(self.output_artifact_ids) != output_count
            or self.output_artifact_ids != canonical_outputs
        ):
            raise ValueError("gate omission branch binding is invalid")
        return self


class ExecutionTopologySubstituted(_SignedRunEventBase):
    schema_version: Literal["automarkov.execution-topology-substituted.v1"]
    event_type: Literal["ExecutionTopologySubstituted"]
    signing_domain: Literal["AutoMarkov-Execution-Topology-Substituted-v1"]
    sequence_no: SequenceNo
    previous_event_hash: Sha256Value
    ablation_execution_plan_artifact_id: ArtifactIdValue
    ablation_execution_plan_hash: Sha256Value
    ablation_method_id: Literal["automarkov_single_agent_workflow"]
    cell_id: NonEmptyId
    from_topology: Literal["multi_role"]
    to_topology: Literal["single_qwen_sequential"]
    role_order: FrozenSequence[NonEmptyId]
    prompt_hashes: FrozenSequence[Sha256Value]
    model_identity_hash: Sha256Value

    @model_validator(mode="after")
    def require_frozen_topology(self) -> ExecutionTopologySubstituted:
        if (
            self.experiment_id is None
            or not self.role_order
            or len(set(self.role_order)) != len(self.role_order)
            or len(self.prompt_hashes) != len(self.role_order)
        ):
            raise ValueError("execution topology substitution binding is invalid")
        return self


OrdinaryAppendEvent: TypeAlias = Annotated[
    StateTransitioned
    | ArtifactSuperseded
    | StageGatePassed
    | RuntimeReady
    | LlmRuntimeDegraded
    | EvidenceTemporarilyUnavailable
    | EvidenceAuthorityRequired
    | WaitingRuntime
    | WaitingEvidence
    | WaitingAsset
    | WaitResolved
    | Blocked
    | BlockResolved
    | ArtifactAccessRevoked
    | SpecificationConflictDetected
    | SignedApprovalEvent
    | GateOmittedByDesign
    | ExecutionTopologySubstituted,
    Field(discriminator="event_type"),
]

TerminalCauseEvent: TypeAlias = Annotated[
    ClarificationRequested
    | ValidationClaimed
    | ValidationFailed
    | RunTerminationRequested
    | BudgetExhausted
    | EvidenceBudgetExhausted,
    Field(discriminator="event_type"),
]

PostTerminalEvent: TypeAlias = Annotated[
    ArtifactAccessRevoked
    | SpecificationConflictDetected
    | ClarificationEvaluationRequested
    | ClarificationEvaluationRecorded
    | SignedApprovalEvent,
    Field(discriminator="event_type"),
]

RunEvent: TypeAlias = Annotated[
    RunCreated
    | StateTransitioned
    | LlmRuntimeDegraded
    | EvidenceTemporarilyUnavailable
    | EvidenceAuthorityRequired
    | WaitingRuntime
    | WaitingEvidence
    | WaitingAsset
    | WaitResolved
    | Blocked
    | BlockResolved
    | ClarificationRequested
    | ValidationClaimed
    | ValidationFailed
    | RunTerminationRequested
    | BudgetExhausted
    | EvidenceBudgetExhausted
    | ArtifactAccessRevoked
    | SignedApprovalEvent
    | ArtifactSuperseded
    | StageGatePassed
    | RuntimeReady
    | SpecificationConflictDetected
    | ClarificationEvaluationRequested
    | ClarificationEvaluationRecorded
    | GateOmittedByDesign
    | ExecutionTopologySubstituted
    | RunSuperseded
    | ReplacementRunCreated
    | ClarificationChildRunCreated,
    Field(discriminator="event_type"),
]

_RUN_EVENT_ADAPTER = TypeAdapter(RunEvent)


class EventRecord(StrictFrozenModel):
    schema_version: Literal["automarkov.event-record.v1"]
    event: SerializeAsAny[RunEvent]
    event_hash: Sha256Value


class EventSchemaRegistry:
    """首次使用即冻结的精确事件类型与版本合同 registry。"""

    def __init__(self) -> None:
        self._models: dict[tuple[str, str], type[StrictFrozenModel]] = {}
        self._schema_ids: dict[tuple[str, str], str] = {}
        self._frozen = False
        self._lock = RLock()

    def register(
        self,
        event_type: str,
        schema_version: str,
        model_type: type[StrictFrozenModel],
    ) -> str:
        if type(event_type) is not str or type(schema_version) is not str:
            raise TypeError("event registry keys must be exact strings")
        adapter = TypeAdapter(model_type)
        schema_id = (
            f"sha256:{sha256(canonical_json_bytes(adapter.json_schema())).hexdigest()}"
        )
        key = (event_type, schema_version)
        with self._lock:
            if self._frozen:
                raise RuntimeError("event schema registry is frozen")
            existing = self._models.get(key)
            if existing is not None and (
                existing is not model_type or self._schema_ids[key] != schema_id
            ):
                raise ValueError("event schema key is already registered differently")
            self._models[key] = model_type
            self._schema_ids[key] = schema_id
        return schema_id

    def freeze(self) -> None:
        with self._lock:
            self._frozen = True

    def snapshot(self) -> tuple[tuple[str, str, str], ...]:
        with self._lock:
            return tuple(
                (event_type, schema_version, self._schema_ids[key])
                for key in sorted(self._models)
                for event_type, schema_version in (key,)
            )

    def decode(self, raw: object) -> RunEvent:
        if type(raw) is not dict:
            raise EventSchemaError("event root must be an exact object")
        event_type = raw.get("event_type")
        schema_version = raw.get("schema_version")
        if type(event_type) is not str or type(schema_version) is not str:
            raise EventSchemaError("event type and schema version are required")
        with self._lock:
            model_type = self._models.get((event_type, schema_version))
        if model_type is None:
            raise EventSchemaError(
                f"unknown event contract: {event_type}@{schema_version}"
            )
        try:
            return cast(
                RunEvent, TypeAdapter(model_type).validate_python(raw, strict=True)
            )
        except (TypeError, ValueError) as error:
            raise EventSchemaError(f"{event_type}@{schema_version}") from error


_CORE_EVENTS = (
    RunCreated,
    StateTransitioned,
    LlmRuntimeDegraded,
    EvidenceTemporarilyUnavailable,
    EvidenceAuthorityRequired,
    WaitingRuntime,
    WaitingEvidence,
    WaitingAsset,
    WaitResolved,
    Blocked,
    BlockResolved,
    ClarificationRequested,
    ValidationClaimed,
    ValidationFailed,
    RunTerminationRequested,
    BudgetExhausted,
    EvidenceBudgetExhausted,
    ArtifactAccessRevoked,
    SignedApprovalEvent,
    ArtifactSuperseded,
    StageGatePassed,
    RuntimeReady,
    SpecificationConflictDetected,
    ClarificationEvaluationRequested,
    ClarificationEvaluationRecorded,
    GateOmittedByDesign,
    ExecutionTopologySubstituted,
    RunSuperseded,
    ReplacementRunCreated,
    ClarificationChildRunCreated,
)


def default_event_schema_registry() -> EventSchemaRegistry:
    registry = EventSchemaRegistry()
    for model_type in _CORE_EVENTS:
        schema = model_type.model_json_schema()
        event_type = cast(str, schema["properties"]["event_type"]["const"])
        version = cast(str, schema["properties"]["schema_version"]["const"])
        registry.register(event_type, version, model_type)
    registry.freeze()
    return registry


def parse_event_body_bytes(
    raw: bytes,
    registry: EventSchemaRegistry | None = None,
) -> RunEvent:
    if type(raw) is not bytes or len(raw) > MAX_JSON_PAYLOAD_BYTES:
        raise EventSchemaError("event bytes exceed the bounded ingress contract")
    try:
        parsed = parse_json_payload(raw)
    except ValueError as error:
        raise EventSchemaError("event JSON is invalid") from error
    return (registry or default_event_schema_registry()).decode(parsed)


def event_bytes(event: StrictFrozenModel) -> bytes:
    return canonical_json_bytes(
        event.model_dump(mode="json", round_trip=True, warnings="error")
    )


def _event_hash(event: StrictFrozenModel) -> str:
    preimage = {
        "domain": EVENT_HASH_DOMAIN,
        "event": parse_json_payload(event_bytes(event)),
    }
    return f"sha256:{sha256(canonical_json_bytes(preimage)).hexdigest()}"


def _encode_typed_event_record(event: StrictFrozenModel) -> bytes:
    record = {
        "schema_version": "automarkov.event-record.v1",
        "event": parse_json_payload(event_bytes(event)),
        "event_hash": _event_hash(event),
    }
    return canonical_json_bytes(record)


def encode_event_record(
    raw_event: object,
    registry: EventSchemaRegistry | None = None,
) -> bytes:
    """验证精确原始事件 mapping，并生成 canonical record bytes。"""

    if type(raw_event) is not dict:
        raise EventSchemaError("event ingress requires an exact raw mapping")
    event = (registry or default_event_schema_registry()).decode(raw_event)
    return _encode_typed_event_record(event)


def parse_event_bytes(
    raw: bytes,
    registry: EventSchemaRegistry | None = None,
) -> EventRecord:
    try:
        parsed = parse_json_payload(raw)
        if type(parsed) is not dict or set(parsed) != {
            "schema_version",
            "event",
            "event_hash",
        }:
            raise ValueError("invalid record keyset")
        event = (registry or default_event_schema_registry()).decode(parsed["event"])
        record = EventRecord.model_validate(parsed, strict=True)
        if (
            record.event_hash != _event_hash(record.event)
            or _encode_typed_event_record(event) != raw
        ):
            raise ValueError("record hash or canonical bytes mismatch")
        return record
    except (EventSchemaError, TypeError, ValueError) as error:
        raise EventIntegrityError("event-record") from error


parse_event_record = parse_event_bytes


def event_signature_preimage(
    event: RunCreated
    | SignedApprovalEvent
    | RunSuperseded
    | ReplacementRunCreated
    | ClarificationChildRunCreated
    | GateOmittedByDesign
    | ExecutionTopologySubstituted,
) -> bytes:
    payload = cast(dict[str, object], event.model_dump(mode="json", warnings="error"))
    del payload["signature_b64url"]
    return canonical_json_bytes(payload)


approval_signature_preimage = event_signature_preimage


class ManifestEventSigningKey(StrictFrozenModel):
    signing_key_id: NonEmptyId
    principal_id: PrincipalIdValue
    signature_algorithm: Literal["Ed25519"]
    public_key_b64url: Annotated[
        str,
        Field(strict=True, pattern=r"^[A-Za-z0-9_-]{43}$"),
    ]
    not_before: CanonicalTimestamp
    not_after: CanonicalTimestamp
    revoked_at: CanonicalTimestamp | None

    @model_validator(mode="after")
    def require_canonical_active_key(self) -> ManifestEventSigningKey:
        try:
            public_key = base64.urlsafe_b64decode(self.public_key_b64url + "=")
        except ValueError as error:
            raise ValueError("manifest event key is not canonical base64url") from error
        if (
            len(public_key) != 32
            or base64.urlsafe_b64encode(public_key).decode().rstrip("=")
            != self.public_key_b64url
        ):
            raise ValueError("manifest event key must contain 32 canonical bytes")
        not_before = datetime.fromisoformat(self.not_before)
        not_after = datetime.fromisoformat(self.not_after)
        if not_before >= not_after:
            raise ValueError("manifest event key validity interval is empty")
        if self.revoked_at is not None and not (
            not_before <= datetime.fromisoformat(self.revoked_at) < not_after
        ):
            raise ValueError("manifest event key revocation is outside its validity")
        return self

    def public_key_bytes(self) -> bytes:
        return base64.urlsafe_b64decode(self.public_key_b64url + "=")


class RunEventActorCapability(StrictFrozenModel):
    principal_id: PrincipalIdValue
    process_execution_id: NonEmptyId | None
    allowed_event_types: FrozenSequence[NonEmptyId]

    @field_validator("allowed_event_types")
    @classmethod
    def require_closed_event_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        known = frozenset(
            cast(str, model.model_json_schema()["properties"]["event_type"]["const"])
            for model in _CORE_EVENTS
        )
        if (
            not value
            or value != tuple(sorted(set(value), key=lambda item: item.encode("utf-8")))
            or any(item not in known for item in value)
        ):
            raise ValueError("actor event capabilities must be closed and canonical")
        return value


class RunCreationSecurityBinding(StrictFrozenModel):
    creation_principal_id: PrincipalIdValue
    signing_key_id: NonEmptyId


class RunEventRevocationAuthority(StrictFrozenModel):
    principal_id: PrincipalIdValue
    principal_kind: Literal["registered_revocation_policy"]
    signing_key_id: NonEmptyId


class RunApprovalSecurityBinding(StrictFrozenModel):
    approval_principal_id: PrincipalIdValue
    approval_principal_kind: Literal[
        "interactive_user",
        "experiment_approval_policy",
    ]
    signing_key_id: NonEmptyId
    policy_contract: ArtifactReference
    policy_source_hash: Sha256Value | None
    policy_image_hash: Sha256Value | None
    policy_version: NonEmptyId | None
    revocation_authorities: FrozenSequence[RunEventRevocationAuthority]

    @field_validator("revocation_authorities")
    @classmethod
    def require_canonical_authorities(
        cls,
        value: tuple[RunEventRevocationAuthority, ...],
    ) -> tuple[RunEventRevocationAuthority, ...]:
        keys = tuple(
            (authority.principal_id, authority.signing_key_id) for authority in value
        )
        if keys != tuple(sorted(set(keys), key=lambda item: item[0].encode("utf-8"))):
            raise ValueError("revocation authorities must be sorted and unique")
        return value


class RunEventSecurityContext(StrictFrozenModel):
    schema_version: Literal["automarkov.run-event-security-context.v1"]
    run_id: RunIdValue
    experiment_id: NonEmptyId | None
    root_ordinal: SafeCanonicalInt
    creation_policy: ArtifactReference
    max_clock_skew_ms: SafeCanonicalInt
    actor_capabilities: FrozenSequence[RunEventActorCapability]
    signing_keys: FrozenSequence[ManifestEventSigningKey]
    run_creation: RunCreationSecurityBinding
    approval: RunApprovalSecurityBinding

    @model_validator(mode="after")
    def require_closed_security_graph(self) -> RunEventSecurityContext:
        capability_keys = tuple(
            (capability.principal_id, capability.process_execution_id or "")
            for capability in self.actor_capabilities
        )
        signing_key_ids = tuple(key.signing_key_id for key in self.signing_keys)
        signing_keys = {key.signing_key_id: key for key in self.signing_keys}
        capability_principals = {
            capability.principal_id for capability in self.actor_capabilities
        }
        if (
            self.root_ordinal < 0
            or self.max_clock_skew_ms < 0
            or not self.actor_capabilities
            or capability_keys
            != tuple(
                sorted(
                    set(capability_keys),
                    key=lambda item: (item[0].encode("utf-8"), item[1].encode("utf-8")),
                )
            )
            or not self.signing_keys
            or signing_key_ids
            != tuple(
                sorted(set(signing_key_ids), key=lambda item: item.encode("utf-8"))
            )
            or self.run_creation.creation_principal_id not in capability_principals
            or self.approval.approval_principal_id not in capability_principals
        ):
            raise ValueError("run event security context is not canonical")
        creation_key = signing_keys.get(self.run_creation.signing_key_id)
        approval_key = signing_keys.get(self.approval.signing_key_id)
        if (
            creation_key is None
            or creation_key.principal_id != self.run_creation.creation_principal_id
            or approval_key is None
            or approval_key.principal_id != self.approval.approval_principal_id
            or any(
                signing_keys.get(authority.signing_key_id) is None
                or signing_keys[authority.signing_key_id].principal_id
                != authority.principal_id
                for authority in self.approval.revocation_authorities
            )
        ):
            raise ValueError("run event signing bindings are inconsistent")
        return self

    def signing_key(self, signing_key_id: str) -> ManifestEventSigningKey:
        matches = tuple(
            key for key in self.signing_keys if key.signing_key_id == signing_key_id
        )
        if len(matches) != 1:
            raise EventSchemaError("manifest event signing key is unavailable")
        return matches[0]


@dataclass(frozen=True, slots=True)
class EventSigningKey:
    signing_key_id: str
    principal_id: str
    run_id: str
    public_key_bytes: bytes
    not_before: str
    not_after: str
    revoked_at: str | None = None

    def __post_init__(self) -> None:
        TypeAdapter(NonEmptyId).validate_python(self.signing_key_id, strict=True)
        TypeAdapter(PrincipalIdValue).validate_python(self.principal_id, strict=True)
        TypeAdapter(RunIdValue).validate_python(self.run_id, strict=True)
        TypeAdapter(CanonicalTimestamp).validate_python(self.not_before, strict=True)
        TypeAdapter(CanonicalTimestamp).validate_python(self.not_after, strict=True)
        if self.revoked_at is not None:
            TypeAdapter(CanonicalTimestamp).validate_python(
                self.revoked_at,
                strict=True,
            )
        if type(self.public_key_bytes) is not bytes or len(self.public_key_bytes) != 32:
            raise ValueError("event signing key must contain 32 Ed25519 public bytes")
        not_before = datetime.fromisoformat(self.not_before)
        not_after = datetime.fromisoformat(self.not_after)
        if not_before >= not_after:
            raise ValueError("event signing key validity interval is empty")
        if self.revoked_at is not None and not (
            not_before <= datetime.fromisoformat(self.revoked_at) < not_after
        ):
            raise ValueError("event key revocation must fall within its validity")


class EventAuthenticator:
    """签名生命周期 ingress 的冻结 Ed25519 key/principal policy。"""

    def __init__(self, keys: tuple[EventSigningKey, ...] = ()) -> None:
        if type(keys) is not tuple:
            raise TypeError("event signing keys must be a frozen tuple")
        mutable_keys: dict[str, EventSigningKey] = {}
        for key in keys:
            if type(key) is not EventSigningKey or key.signing_key_id in mutable_keys:
                raise ValueError("event signing key registry is invalid")
            mutable_keys[key.signing_key_id] = key
        self._keys: Mapping[str, EventSigningKey] = MappingProxyType(mutable_keys)

    def authenticate(
        self,
        event: RunEvent,
        security_context: RunEventSecurityContext | None = None,
    ) -> None:
        runner_audit = isinstance(
            event,
            (GateOmittedByDesign, ExecutionTopologySubstituted),
        )
        manifest_signing_key = (
            security_context.signing_key(event.signing_key_id)
            if security_context is not None and runner_audit
            else None
        )
        cached_signing_key = (
            self._keys.get(event.signing_key_id)
            if security_context is None and runner_audit
            else None
        )
        actor_principal_id = (
            event.replacement_authority_principal_id
            if isinstance(event, (RunSuperseded, ReplacementRunCreated))
            else event.continuation_authority_principal_id
            if isinstance(event, ClarificationChildRunCreated)
            else manifest_signing_key.principal_id
            if manifest_signing_key is not None
            else cached_signing_key.principal_id
            if cached_signing_key is not None
            else cast(_RunEventBase, event).actor_principal_id
        )
        if security_context is not None:
            if (
                security_context.run_id != event.run_id
                or security_context.experiment_id != event.experiment_id
            ):
                raise EventSchemaError("event does not match its run manifest")
            process_execution_id = getattr(
                event,
                "actor_process_execution_id",
                None,
            )
            if not any(
                capability.principal_id == actor_principal_id
                and capability.process_execution_id == process_execution_id
                and event.event_type in capability.allowed_event_types
                for capability in security_context.actor_capabilities
            ):
                raise EventSchemaError("event actor capability is unavailable")
            if isinstance(event, RunCreated):
                if (
                    security_context.root_ordinal != 0
                    or event.creation_principal_id
                    != security_context.run_creation.creation_principal_id
                    or event.signing_key_id
                    != security_context.run_creation.signing_key_id
                ):
                    raise EventSchemaError(
                        "run creation binding or root ordinal does not match manifest"
                    )
            elif isinstance(event, ReplacementRunCreated):
                if security_context.root_ordinal != event.replacement_ordinal:
                    raise EventSchemaError(
                        "replacement child ordinal does not match manifest"
                    )
            elif isinstance(event, ClarificationChildRunCreated):
                if (
                    security_context.root_ordinal
                    != event.clarification_continuation_ordinal
                ):
                    raise EventSchemaError(
                        "clarification child ordinal does not match manifest"
                    )
            elif isinstance(event, SignedApprovalEvent):
                approval = security_context.approval
                authority = any(
                    item.principal_id == event.approval_principal_id
                    and item.signing_key_id == event.signing_key_id
                    for item in approval.revocation_authorities
                )
                expected_approval = (
                    event.approval_principal_id == approval.approval_principal_id
                    and event.approval_principal_kind
                    == approval.approval_principal_kind
                    and event.signing_key_id == approval.signing_key_id
                    and event.approval_policy_source_hash == approval.policy_source_hash
                )
                if not expected_approval and not (
                    event.decision == "revoked" and authority
                ):
                    raise EventSchemaError("approval binding does not match manifest")
        if not isinstance(
            event,
            (
                RunCreated,
                SignedApprovalEvent,
                RunSuperseded,
                ReplacementRunCreated,
                ClarificationChildRunCreated,
                GateOmittedByDesign,
                ExecutionTopologySubstituted,
            ),
        ):
            return
        principal_id = (
            event.creation_principal_id
            if isinstance(event, RunCreated)
            else event.approval_principal_id
            if isinstance(event, SignedApprovalEvent)
            else actor_principal_id
        )
        if security_context is None:
            key = self._keys.get(event.signing_key_id)
        else:
            manifest_key = security_context.signing_key(event.signing_key_id)
            key = EventSigningKey(
                signing_key_id=manifest_key.signing_key_id,
                principal_id=manifest_key.principal_id,
                run_id=security_context.run_id,
                public_key_bytes=manifest_key.public_key_bytes(),
                not_before=manifest_key.not_before,
                not_after=manifest_key.not_after,
                revoked_at=manifest_key.revoked_at,
            )
            cached_key = self._keys.get(event.signing_key_id)
            if cached_key is not None and cached_key != key:
                raise EventSchemaError("event signing key cache differs from manifest")
        if (
            key is None
            or key.principal_id != principal_id
            or key.run_id != event.run_id
            or datetime.fromisoformat(event.issued_at)
            < datetime.fromisoformat(key.not_before)
            or datetime.fromisoformat(event.issued_at)
            >= datetime.fromisoformat(key.not_after)
            or key.revoked_at is not None
            and datetime.fromisoformat(event.issued_at)
            >= datetime.fromisoformat(key.revoked_at)
        ):
            raise EventSchemaError("event signing key is unavailable or inactive")
        try:
            signature = base64.urlsafe_b64decode(event.signature_b64url + "==")
            if (
                len(signature) != 64
                or base64.urlsafe_b64encode(signature).decode().rstrip("=")
                != event.signature_b64url
            ):
                raise ValueError("signature is not canonical Ed25519 base64url")
            Ed25519PublicKey.from_public_bytes(key.public_key_bytes).verify(
                signature,
                event_signature_preimage(event),
            )
        except (InvalidSignature, ValueError) as error:
            raise EventSchemaError("event signature is invalid") from error


def verify_approval_signature(
    event: SignedApprovalEvent,
    public_keys: Mapping[str, bytes],
) -> None:
    key_bytes = public_keys.get(event.signing_key_id)
    if type(key_bytes) is not bytes or len(key_bytes) != 32:
        raise EventSchemaError("approval signing key is unavailable")
    EventAuthenticator(
        (
            EventSigningKey(
                signing_key_id=event.signing_key_id,
                principal_id=event.approval_principal_id,
                run_id=event.run_id,
                public_key_bytes=key_bytes,
                not_before="1970-01-01T00:00:00Z",
                not_after="9999-12-31T23:59:59Z",
            ),
        )
    ).authenticate(event)


TERMINAL_STATES = frozenset(
    {
        RunState.COMPLETED,
        RunState.CLARIFICATION_REQUIRED,
        RunState.OOD_PACKAGED,
        RunState.PARTIAL,
        RunState.BUDGET_EXHAUSTED,
        RunState.FAILED,
        RunState.CANCELLED,
    }
)
WAITING_STATES = frozenset(
    {
        RunState.WAITING_RUNTIME,
        RunState.WAITING_EVIDENCE,
        RunState.WAITING_ASSET,
        RunState.BLOCKED,
    }
)

_FORWARD_TRANSITIONS = {
    (RunState.RECEIVED, RunState.RESEARCHING),
    (RunState.RESEARCHING, RunState.TEXT_DRAFTED),
    (RunState.TEXT_DRAFTED, RunState.TEXT_REVIEWED),
    (RunState.TEXT_REVIEWED, RunState.WAITING_TEXT_CONFIRMATION),
    (RunState.WAITING_TEXT_CONFIRMATION, RunState.TEXT_LOCKED),
    (RunState.TEXT_LOCKED, RunState.CLASSIFIED),
    (RunState.CLASSIFIED, RunState.FORMAL_DRAFTED),
    (RunState.CLASSIFIED, RunState.REDUCTION_PROPOSAL_DRAFTING),
    (RunState.CLASSIFIED, RunState.OOD_HANDOFF_BUILDING),
    (RunState.REDUCTION_PROPOSAL_DRAFTING, RunState.WAITING_REDUCTION_CONFIRMATION),
    (RunState.WAITING_REDUCTION_CONFIRMATION, RunState.TEXT_DRAFTED),
    (RunState.WAITING_REDUCTION_CONFIRMATION, RunState.OOD_HANDOFF_BUILDING),
    (RunState.OOD_HANDOFF_BUILDING, RunState.OOD_HANDOFF_VALIDATING),
    (RunState.OOD_HANDOFF_VALIDATING, RunState.OOD_PACKAGED),
    (RunState.FORMAL_DRAFTED, RunState.FORMAL_REVIEWED),
    (RunState.FORMAL_REVIEWED, RunState.WAITING_FORMAL_CONFIRMATION),
    (RunState.WAITING_FORMAL_CONFIRMATION, RunState.FORMAL_LOCKED),
    (RunState.FORMAL_LOCKED, RunState.IMPLEMENTATION_SELECTED),
    (RunState.IMPLEMENTATION_SELECTED, RunState.ENVIRONMENT_IMPLEMENTED),
    (RunState.ENVIRONMENT_IMPLEMENTED, RunState.UNIT_VALIDATING),
    (RunState.UNIT_VALIDATING, RunState.SIMULATION_VALIDATING),
    (RunState.SIMULATION_VALIDATING, RunState.SEALED_E2E_VALIDATING),
    (RunState.SEALED_E2E_VALIDATING, RunState.TRAINING_SMOKE_TESTING),
    (RunState.TRAINING_SMOKE_TESTING, RunState.POLICY_TRAINING),
    (RunState.POLICY_TRAINING, RunState.FINAL_EVALUATING),
    (RunState.FINAL_EVALUATING, RunState.PACKAGING),
    (RunState.PACKAGING, RunState.COMPLETED),
}
_REVISION_TRANSITIONS = {
    (RunState.TEXT_REVIEWED, RunState.TEXT_DRAFTED),
    (RunState.WAITING_TEXT_CONFIRMATION, RunState.TEXT_DRAFTED),
    (RunState.FORMAL_DRAFTED, RunState.TEXT_DRAFTED),
    (RunState.FORMAL_REVIEWED, RunState.FORMAL_DRAFTED),
    (RunState.FORMAL_REVIEWED, RunState.TEXT_DRAFTED),
    (RunState.WAITING_FORMAL_CONFIRMATION, RunState.FORMAL_DRAFTED),
    (RunState.UNIT_VALIDATING, RunState.ENVIRONMENT_IMPLEMENTED),
    (RunState.SIMULATION_VALIDATING, RunState.ENVIRONMENT_IMPLEMENTED),
}

_TRANSITION_NAMES: dict[str, frozenset[str]] = {
    "RECEIVED": frozenset({"RESEARCHING", "BLOCKED"}),
    "RESEARCHING": frozenset(
        {"TEXT_DRAFTED", "WAITING_EVIDENCE", "BLOCKED", "WAITING_RUNTIME"}
    ),
    "TEXT_DRAFTED": frozenset({"TEXT_REVIEWED"}),
    "TEXT_REVIEWED": frozenset(
        {"WAITING_TEXT_CONFIRMATION", "CLARIFICATION_REQUIRED", "TEXT_DRAFTED"}
    ),
    "WAITING_TEXT_CONFIRMATION": frozenset({"TEXT_LOCKED", "TEXT_DRAFTED", "BLOCKED"}),
    "TEXT_LOCKED": frozenset({"CLASSIFIED"}),
    "CLASSIFIED": frozenset(
        {"FORMAL_DRAFTED", "REDUCTION_PROPOSAL_DRAFTING", "OOD_HANDOFF_BUILDING"}
    ),
    "REDUCTION_PROPOSAL_DRAFTING": frozenset({"WAITING_REDUCTION_CONFIRMATION"}),
    "WAITING_REDUCTION_CONFIRMATION": frozenset(
        {"TEXT_DRAFTED", "OOD_HANDOFF_BUILDING", "BLOCKED"}
    ),
    "OOD_HANDOFF_BUILDING": frozenset(
        {"OOD_HANDOFF_VALIDATING", "WAITING_ASSET", "BLOCKED"}
    ),
    "OOD_HANDOFF_VALIDATING": frozenset(
        {"OOD_PACKAGED", "OOD_HANDOFF_BUILDING", "WAITING_ASSET"}
    ),
    "FORMAL_DRAFTED": frozenset({"FORMAL_REVIEWED", "TEXT_DRAFTED"}),
    "FORMAL_REVIEWED": frozenset(
        {"WAITING_FORMAL_CONFIRMATION", "FORMAL_DRAFTED", "TEXT_DRAFTED"}
    ),
    "WAITING_FORMAL_CONFIRMATION": frozenset(
        {"FORMAL_LOCKED", "FORMAL_DRAFTED", "BLOCKED"}
    ),
    "FORMAL_LOCKED": frozenset({"IMPLEMENTATION_SELECTED"}),
    "IMPLEMENTATION_SELECTED": frozenset(
        {"ENVIRONMENT_IMPLEMENTED", "WAITING_RUNTIME", "WAITING_ASSET", "BLOCKED"}
    ),
    "ENVIRONMENT_IMPLEMENTED": frozenset({"UNIT_VALIDATING"}),
    "UNIT_VALIDATING": frozenset(
        {
            "SIMULATION_VALIDATING",
            "WAITING_RUNTIME",
            "ENVIRONMENT_IMPLEMENTED",
            "FORMAL_DRAFTED",
            "TEXT_DRAFTED",
        }
    ),
    "SIMULATION_VALIDATING": frozenset(
        {
            "SEALED_E2E_VALIDATING",
            "WAITING_RUNTIME",
            "ENVIRONMENT_IMPLEMENTED",
            "FORMAL_DRAFTED",
            "TEXT_DRAFTED",
        }
    ),
    "SEALED_E2E_VALIDATING": frozenset(
        {"TRAINING_SMOKE_TESTING", "WAITING_RUNTIME", "PARTIAL"}
    ),
    "TRAINING_SMOKE_TESTING": frozenset(
        {"POLICY_TRAINING", "WAITING_RUNTIME", "WAITING_ASSET", "PARTIAL"}
    ),
    "POLICY_TRAINING": frozenset({"FINAL_EVALUATING", "WAITING_RUNTIME"}),
    "FINAL_EVALUATING": frozenset({"PACKAGING", "WAITING_RUNTIME", "PARTIAL"}),
    "PACKAGING": frozenset({"COMPLETED", "PARTIAL"}),
    "WAITING_RUNTIME": frozenset(
        {
            "RESEARCHING",
            "IMPLEMENTATION_SELECTED",
            "UNIT_VALIDATING",
            "SIMULATION_VALIDATING",
            "SEALED_E2E_VALIDATING",
            "TRAINING_SMOKE_TESTING",
            "POLICY_TRAINING",
            "FINAL_EVALUATING",
            "PARTIAL",
        }
    ),
    "WAITING_EVIDENCE": frozenset({"RESEARCHING", "BLOCKED"}),
    "WAITING_ASSET": frozenset(
        {
            "OOD_HANDOFF_BUILDING",
            "OOD_HANDOFF_VALIDATING",
            "IMPLEMENTATION_SELECTED",
            "TRAINING_SMOKE_TESTING",
            "PARTIAL",
        }
    ),
    "BLOCKED": frozenset(
        {
            "RECEIVED",
            "RESEARCHING",
            "WAITING_TEXT_CONFIRMATION",
            "WAITING_REDUCTION_CONFIRMATION",
            "OOD_HANDOFF_BUILDING",
            "WAITING_FORMAL_CONFIRMATION",
            "IMPLEMENTATION_SELECTED",
            "PARTIAL",
        }
    ),
}
_APPROVAL_REVOCATION_TARGETS: Mapping[RunState, frozenset[RunState]] = MappingProxyType(
    {
        RunState.TEXT_LOCKED: frozenset({RunState.TEXT_DRAFTED}),
        RunState.CLASSIFIED: frozenset({RunState.TEXT_DRAFTED}),
        RunState.REDUCTION_PROPOSAL_DRAFTING: frozenset({RunState.TEXT_DRAFTED}),
        RunState.WAITING_REDUCTION_CONFIRMATION: frozenset({RunState.TEXT_DRAFTED}),
        RunState.OOD_HANDOFF_BUILDING: frozenset({RunState.TEXT_DRAFTED}),
        RunState.OOD_HANDOFF_VALIDATING: frozenset({RunState.TEXT_DRAFTED}),
        RunState.FORMAL_DRAFTED: frozenset({RunState.TEXT_DRAFTED}),
        RunState.FORMAL_REVIEWED: frozenset({RunState.TEXT_DRAFTED}),
        RunState.WAITING_FORMAL_CONFIRMATION: frozenset({RunState.TEXT_DRAFTED}),
        RunState.FORMAL_LOCKED: frozenset(
            {RunState.TEXT_DRAFTED, RunState.FORMAL_DRAFTED}
        ),
        RunState.IMPLEMENTATION_SELECTED: frozenset(
            {RunState.TEXT_DRAFTED, RunState.FORMAL_DRAFTED}
        ),
        RunState.ENVIRONMENT_IMPLEMENTED: frozenset(
            {RunState.TEXT_DRAFTED, RunState.FORMAL_DRAFTED}
        ),
        RunState.UNIT_VALIDATING: frozenset(
            {RunState.TEXT_DRAFTED, RunState.FORMAL_DRAFTED}
        ),
        RunState.SIMULATION_VALIDATING: frozenset(
            {RunState.TEXT_DRAFTED, RunState.FORMAL_DRAFTED}
        ),
    }
)
for _source, _targets in _APPROVAL_REVOCATION_TARGETS.items():
    _TRANSITION_NAMES[_source.value] = frozenset(
        _TRANSITION_NAMES[_source.value] | {target.value for target in _targets}
    )
_ALLOWED_TRANSITIONS = frozenset(
    (RunState(source), RunState(target))
    for source, targets in _TRANSITION_NAMES.items()
    for target in targets
)
_FAILED_PREDECESSORS = frozenset(
    {
        RunState.RESEARCHING,
        RunState.WAITING_TEXT_CONFIRMATION,
        RunState.TEXT_LOCKED,
        RunState.CLASSIFIED,
        RunState.REDUCTION_PROPOSAL_DRAFTING,
        RunState.OOD_HANDOFF_VALIDATING,
        RunState.WAITING_FORMAL_CONFIRMATION,
        RunState.FORMAL_LOCKED,
        RunState.SEALED_E2E_VALIDATING,
        RunState.TRAINING_SMOKE_TESTING,
        RunState.POLICY_TRAINING,
        RunState.FINAL_EVALUATING,
        RunState.PACKAGING,
    }
)
_CANCELLED_PREDECESSORS = frozenset(
    {
        RunState.RECEIVED,
        RunState.WAITING_RUNTIME,
        RunState.WAITING_EVIDENCE,
        RunState.BLOCKED,
    }
)
_EXACT_TERMINAL_TRANSITIONS = frozenset(
    {(state, RunState.FAILED) for state in _FAILED_PREDECESSORS}
    | {(state, RunState.CANCELLED) for state in _CANCELLED_PREDECESSORS}
)
_UNIVERSAL_TERMINAL_TRANSITIONS = frozenset({RunState.BUDGET_EXHAUSTED})
RUN_PROJECTOR_VERSION = "automarkov.run-projector.v1"


def allowed_transition(from_state: RunState, to_state: RunState) -> bool:
    if from_state in TERMINAL_STATES:
        return False
    return (
        (
            from_state,
            to_state,
        )
        in _ALLOWED_TRANSITIONS
        or (
            from_state,
            to_state,
        )
        in _EXACT_TERMINAL_TRANSITIONS
        or to_state in _UNIVERSAL_TERMINAL_TRANSITIONS
    )


class WaitingBinding(StrictFrozenModel):
    waiting_state: RunState
    wait_kind: Literal["runtime", "evidence", "asset", "blocked"]
    resume_state: RunState
    event: EventReference
    dependency_identity_hash: Sha256Value | None
    gate_id: NonEmptyId
    authority_principal_id: PrincipalIdValue | None
    entered_at: CanonicalTimestamp


class ApprovalEventSnapshot(StrictFrozenModel):
    event: EventReference
    validity: Literal["valid", "revoked"]


class RunProjection(StrictFrozenModel):
    schema_version: Literal["automarkov.run-view.v2"]
    run_id: RunIdValue
    experiment_id: NonEmptyId | None
    projector_version: Literal["automarkov.run-projector.v1"]
    projector_hash: Sha256Value
    state: RunState
    event_head: EventHead
    budget_snapshot: ArtifactReference | None
    waiting: WaitingBinding | None
    terminal_event: EventReference | None
    terminal_snapshot_head: EventHead | None
    current_approval_snapshots: FrozenSequence[ApprovalEventSnapshot]
    post_terminal_audit_event_references: FrozenSequence[EventReference]
    terminal_result: ArtifactReference | None = None
    run_audit_projection: ArtifactReference | None = None


def _budget_is_monotonic(
    previous: BudgetSnapshot,
    current: BudgetSnapshot,
) -> bool:
    if previous.contract_hash != current.contract_hash:
        return False
    left = {counter.metric: counter for counter in previous.counters}
    right = {counter.metric: counter for counter in current.counters}
    return left.keys() == right.keys() and all(
        left[name].limit == right[name].limit
        and left[name].consumed <= right[name].consumed
        for name in left
    )


def _require_trigger(
    event: StateTransitioned,
    records: Mapping[str, EventRecord],
) -> RunEvent:
    record = records.get(event.trigger_event_id)
    if (
        record is None
        or record.event_hash != event.trigger_event_hash
        or record.event.sequence_no != event.sequence_no - 1
    ):
        raise InvalidRunTransitionError(event.from_state.value, event.to_state.value)
    return record.event


_APPROVAL_TRANSITION_CONTRACTS: Mapping[
    tuple[RunState, RunState],
    tuple[str, str],
] = MappingProxyType(
    {
        (RunState.WAITING_TEXT_CONFIRMATION, RunState.TEXT_LOCKED): (
            "approved",
            "text_approved",
        ),
        (RunState.WAITING_TEXT_CONFIRMATION, RunState.TEXT_DRAFTED): (
            "rejected",
            "text_rejected",
        ),
        (RunState.WAITING_REDUCTION_CONFIRMATION, RunState.OOD_HANDOFF_BUILDING): (
            "rejected",
            "reduction_rejected",
        ),
        (RunState.WAITING_FORMAL_CONFIRMATION, RunState.FORMAL_LOCKED): (
            "approved",
            "formal_approved",
        ),
        (RunState.WAITING_FORMAL_CONFIRMATION, RunState.FORMAL_DRAFTED): (
            "rejected",
            "formal_rejected",
        ),
    }
)
_REVISION_REASON_BY_EDGE: Mapping[tuple[RunState, RunState], str] = MappingProxyType(
    {
        (RunState.TEXT_REVIEWED, RunState.TEXT_DRAFTED): "text_revision_required",
        (RunState.FORMAL_DRAFTED, RunState.TEXT_DRAFTED): (
            "semantic_revision_required"
        ),
        (RunState.FORMAL_REVIEWED, RunState.FORMAL_DRAFTED): (
            "formal_revision_required"
        ),
        (RunState.FORMAL_REVIEWED, RunState.TEXT_DRAFTED): (
            "semantic_revision_required"
        ),
        (RunState.OOD_HANDOFF_VALIDATING, RunState.OOD_HANDOFF_BUILDING): (
            "ood_handoff_revision_required"
        ),
        (RunState.UNIT_VALIDATING, RunState.ENVIRONMENT_IMPLEMENTED): (
            "implementation_revision_required"
        ),
        (RunState.UNIT_VALIDATING, RunState.FORMAL_DRAFTED): (
            "formal_revision_required"
        ),
        (RunState.UNIT_VALIDATING, RunState.TEXT_DRAFTED): (
            "semantic_revision_required"
        ),
        (RunState.SIMULATION_VALIDATING, RunState.ENVIRONMENT_IMPLEMENTED): (
            "implementation_revision_required"
        ),
        (RunState.SIMULATION_VALIDATING, RunState.FORMAL_DRAFTED): (
            "formal_revision_required"
        ),
        (RunState.SIMULATION_VALIDATING, RunState.TEXT_DRAFTED): (
            "semantic_revision_required"
        ),
    }
)
_NONTERMINAL_TRANSITIONS = frozenset(
    pair for pair in _ALLOWED_TRANSITIONS if pair[1] not in TERMINAL_STATES
)
_WAIT_TRANSITIONS = frozenset(
    pair
    for pair in _NONTERMINAL_TRANSITIONS
    if pair[0] in WAITING_STATES or pair[1] in WAITING_STATES
)
_WAIT_ENTRY_MODELS: Mapping[RunState, type[StrictFrozenModel]] = MappingProxyType(
    {
        RunState.WAITING_RUNTIME: WaitingRuntime,
        RunState.WAITING_EVIDENCE: WaitingEvidence,
        RunState.WAITING_ASSET: WaitingAsset,
        RunState.BLOCKED: Blocked,
    }
)
_WAIT_RESOLUTION_MODELS: Mapping[RunState, type[StrictFrozenModel]] = MappingProxyType(
    {
        RunState.WAITING_RUNTIME: WaitResolved,
        RunState.WAITING_EVIDENCE: WaitResolved,
        RunState.WAITING_ASSET: WaitResolved,
        RunState.BLOCKED: BlockResolved,
    }
)
_REVOCATION_TRANSITIONS = frozenset(
    (source, target)
    for source, targets in _APPROVAL_REVOCATION_TARGETS.items()
    for target in targets
)
_CLOSED_CAUSE_TRANSITIONS = frozenset(
    {
        *_STAGE_GATE_CONTRACTS,
        *_APPROVAL_TRANSITION_CONTRACTS,
        *_REVISION_REASON_BY_EDGE,
        *_WAIT_TRANSITIONS,
        *_REVOCATION_TRANSITIONS,
        (
            RunState.WAITING_REDUCTION_CONFIRMATION,
            RunState.TEXT_DRAFTED,
        ),
    }
)
if _CLOSED_CAUSE_TRANSITIONS != _NONTERMINAL_TRANSITIONS:
    raise RuntimeError("ordinary transition cause table is not closed")


def _ordinary_transition_cause_is_valid(
    transition: StateTransitioned,
    trigger: RunEvent,
) -> bool:
    pair = (transition.from_state, transition.to_state)
    if transition.to_state in TERMINAL_STATES:
        return True
    if pair in _STAGE_GATE_CONTRACTS:
        return isinstance(trigger, StageGatePassed) and trigger.matches_transition(
            transition
        )
    if transition.to_state in WAITING_STATES:
        expected_trigger = _WAIT_ENTRY_MODELS[transition.to_state]
        return isinstance(trigger, expected_trigger)
    if transition.from_state in WAITING_STATES:
        return isinstance(trigger, _WAIT_RESOLUTION_MODELS[transition.from_state])
    approval_contract = _APPROVAL_TRANSITION_CONTRACTS.get(pair)
    if approval_contract is not None:
        return (
            isinstance(trigger, SignedApprovalEvent)
            and (trigger.decision, transition.reason_code) == approval_contract
        )
    if pair == (
        RunState.WAITING_REDUCTION_CONFIRMATION,
        RunState.TEXT_DRAFTED,
    ):
        return (
            isinstance(trigger, ArtifactSuperseded)
            and trigger.supersession_reason_code == "reduction_approved"
            and transition.reason_code == "reduction_approved"
        )
    revision_reason = _REVISION_REASON_BY_EDGE.get(pair)
    return (
        isinstance(trigger, ArtifactSuperseded)
        and trigger.supersession_reason_code == "approval_revoked"
        and transition.to_state
        in _APPROVAL_REVOCATION_TARGETS.get(
            transition.from_state,
            frozenset(),
        )
        and transition.reason_code == "approval_revoked"
        or revision_reason is not None
        and isinstance(trigger, ArtifactSuperseded)
        and trigger.supersession_reason_code
        in {"revision_replaced", "validation_failed", "upstream_invalidated"}
        and transition.reason_code == revision_reason
    )


def _revocation_rollback_is_valid(
    revoked: SignedApprovalEvent,
    superseded: RunEvent | None,
    superseded_hash: str | None,
    transition: RunEvent | None,
    current_state: RunState,
) -> bool:
    return (
        isinstance(superseded, ArtifactSuperseded)
        and isinstance(transition, StateTransitioned)
        and superseded.old_artifact == revoked.artifact
        and superseded.supersession_reason_code == "approval_revoked"
        and transition.from_state is current_state
        and transition.to_state
        in _APPROVAL_REVOCATION_TARGETS.get(current_state, frozenset())
        and transition.reason_code == "approval_revoked"
        and transition.trigger_event_id == superseded.event_id
        and transition.trigger_event_hash == superseded_hash
    )


def _waiting_binding(
    trigger: RunEvent,
    record: EventRecord,
    records: Mapping[str, EventRecord],
) -> WaitingBinding:
    if isinstance(trigger, WaitingRuntime | WaitingEvidence | WaitingAsset):
        cause = records.get(trigger.trigger_event_id)
        if (
            cause is None
            or cause.event_hash != trigger.trigger_event_hash
            or cause.event.sequence_no != trigger.sequence_no - 1
            or datetime.fromisoformat(cause.event.issued_at)
            > datetime.fromisoformat(trigger.entered_at)
        ):
            raise RunResumeContractError(trigger.run_id)
        if (
            isinstance(trigger, WaitingRuntime)
            and trigger.dependency_kind == "local_llm"
            and (
                not isinstance(cause.event, LlmRuntimeDegraded)
                or cause.event.dependency_identity_hash
                != trigger.dependency_identity_hash
                or cause.event.failed_gate_id != trigger.recovery_gate_id
                or cause.event.failure_report.artifact_id
                != trigger.failure_report_artifact_id
                or cause.event.failure_report.payload_hash
                != trigger.failure_report_payload_hash
                or cause.event.affected_state != trigger.resume_state
            )
        ):
            raise RunResumeContractError(trigger.run_id)
        if isinstance(trigger, WaitingEvidence) and (
            not isinstance(cause.event, EvidenceTemporarilyUnavailable)
            or cause.event.lease_pool_artifact_id != trigger.lease_pool_artifact_id
            or cause.event.lease_pool_payload_hash != trigger.lease_pool_payload_hash
            or cause.event.lease_snapshot_artifact_id
            != trigger.lease_snapshot_artifact_id
            or cause.event.lease_snapshot_payload_hash
            != trigger.lease_snapshot_payload_hash
            or cause.event.earliest_availability != trigger.earliest_availability
        ):
            raise RunResumeContractError(trigger.run_id)
    if isinstance(trigger, WaitingRuntime):
        return WaitingBinding(
            waiting_state=RunState.WAITING_RUNTIME,
            wait_kind="runtime",
            resume_state=trigger.resume_state,
            event=EventReference(
                event_id=trigger.event_id,
                sequence_no=trigger.sequence_no,
                event_hash=record.event_hash,
            ),
            dependency_identity_hash=trigger.dependency_identity_hash,
            gate_id=trigger.recovery_gate_id,
            authority_principal_id=None,
            entered_at=trigger.entered_at,
        )
    if isinstance(trigger, WaitingEvidence):
        return WaitingBinding(
            waiting_state=RunState.WAITING_EVIDENCE,
            wait_kind="evidence",
            resume_state=trigger.resume_state,
            event=EventReference(
                event_id=trigger.event_id,
                sequence_no=trigger.sequence_no,
                event_hash=record.event_hash,
            ),
            dependency_identity_hash=trigger.lease_identity_hash,
            gate_id=trigger.recovery_gate_id,
            authority_principal_id=None,
            entered_at=trigger.entered_at,
        )
    if isinstance(trigger, WaitingAsset):
        return WaitingBinding(
            waiting_state=RunState.WAITING_ASSET,
            wait_kind="asset",
            resume_state=trigger.resume_state,
            event=EventReference(
                event_id=trigger.event_id,
                sequence_no=trigger.sequence_no,
                event_hash=record.event_hash,
            ),
            dependency_identity_hash=trigger.asset_identity_hash,
            gate_id=trigger.recovery_gate_id,
            authority_principal_id=None,
            entered_at=trigger.entered_at,
        )
    if isinstance(trigger, Blocked):
        if trigger.block_reason_code == "evidence_authority_required":
            cause = next(
                (
                    candidate.event
                    for candidate in records.values()
                    if candidate.event.sequence_no == trigger.sequence_no - 1
                ),
                None,
            )
            if (
                not isinstance(cause, EvidenceAuthorityRequired)
                or cause.external_authority_principal_id
                != trigger.external_authority_principal_id
                or cause.resolution_condition_hash != trigger.resolution_condition_hash
                or cause.failure_report_artifact_id
                != trigger.failure_report_artifact_id
                or cause.failure_report_payload_hash
                != trigger.failure_report_payload_hash
            ):
                raise RunResumeContractError(trigger.run_id)
        return WaitingBinding(
            waiting_state=RunState.BLOCKED,
            wait_kind="blocked",
            resume_state=trigger.resume_state,
            event=EventReference(
                event_id=trigger.event_id,
                sequence_no=trigger.sequence_no,
                event_hash=record.event_hash,
            ),
            dependency_identity_hash=None,
            gate_id=trigger.recheck_gate_id,
            authority_principal_id=trigger.external_authority_principal_id,
            entered_at=trigger.entered_at,
        )
    raise InvalidRunTransitionError("unknown", "waiting")


def _validate_resume(
    projection: RunProjection,
    trigger: RunEvent,
) -> None:
    binding = projection.waiting
    if binding is None:
        raise RunResumeContractError(projection.run_id)
    if isinstance(trigger, WaitResolved):
        valid = (
            binding.wait_kind != "blocked"
            and trigger.wait_kind == binding.wait_kind
            and trigger.waiting_event_id == binding.event.event_id
            and trigger.waiting_event_hash == binding.event.event_hash
            and trigger.resume_state == binding.resume_state
            and trigger.recovery_gate_id == binding.gate_id
            and trigger.identity_hash == binding.dependency_identity_hash
        )
    elif isinstance(trigger, BlockResolved):
        valid = (
            binding.wait_kind == "blocked"
            and trigger.blocked_event_id == binding.event.event_id
            and trigger.blocked_event_hash == binding.event.event_hash
            and trigger.authority_principal_id == binding.authority_principal_id
        )
    else:
        valid = False
    if not valid or datetime.fromisoformat(trigger.issued_at) < datetime.fromisoformat(
        binding.entered_at
    ):
        raise RunResumeContractError(projection.run_id)


_TERMINAL_CAUSE_MODELS = (
    ClarificationRequested,
    ValidationClaimed,
    ValidationFailed,
    RunTerminationRequested,
    BudgetExhausted,
    EvidenceBudgetExhausted,
)
_BUDGET_METRIC_BY_KIND: Mapping[str, BudgetMetric] = MappingProxyType(
    {
        "revision": "stage_revisions",
        "token": "llm_tokens",
        "tool_call": "tool_calls",
        "provider_credit": "provider_credits",
        "wall_time": "wall_time_ms",
        "global_cost": "cost_microunits",
    }
)


def _waiting_event_is_allowed(state: RunState, event: RunEvent) -> bool:
    if isinstance(event, RunSuperseded):
        return (
            state is RunState.WAITING_RUNTIME
            and event.supersession_cause == "runtime_identity_replacement"
        )
    if isinstance(event, StateTransitioned | RunTerminationRequested):
        return True
    if isinstance(event, BudgetExhausted | EvidenceBudgetExhausted):
        return True
    if state is RunState.BLOCKED:
        return isinstance(event, BlockResolved)
    if state is RunState.WAITING_EVIDENCE and isinstance(
        event, EvidenceAuthorityRequired | Blocked
    ):
        return True
    return isinstance(event, WaitResolved) and event.wait_kind == {
        RunState.WAITING_RUNTIME: "runtime",
        RunState.WAITING_EVIDENCE: "evidence",
        RunState.WAITING_ASSET: "asset",
    }.get(state)


def _is_run_supersession_transition(
    superseded: RunSuperseded,
    superseded_hash: str,
    transition: RunEvent,
    current_state: RunState,
) -> bool:
    return (
        isinstance(transition, StateTransitioned)
        and transition.run_id == superseded.run_id
        and transition.experiment_id == superseded.experiment_id
        and transition.actor_principal_id
        == superseded.replacement_authority_principal_id
        and transition.actor_process_execution_id is not None
        and transition.sequence_no == superseded.sequence_no + 1
        and transition.previous_event_hash == superseded_hash
        and transition.trigger_event_id == superseded.event_id
        and transition.trigger_event_hash == superseded_hash
        and transition.from_state is current_state
        and transition.to_state is RunState.CANCELLED
        and transition.reason_code == "run_superseded"
        and (
            superseded.supersession_cause != "runtime_identity_replacement"
            or current_state is RunState.WAITING_RUNTIME
        )
    )


def _record_reference(record: EventRecord) -> EventReference:
    return EventReference(
        event_id=record.event.event_id,
        sequence_no=record.event.sequence_no,
        event_hash=record.event_hash,
    )


def _approval_snapshots_after(
    snapshots: tuple[ApprovalEventSnapshot, ...],
    record: EventRecord,
) -> tuple[ApprovalEventSnapshot, ...]:
    event = record.event
    if not isinstance(event, SignedApprovalEvent) or event.decision == "rejected":
        return snapshots
    if event.decision == "approved":
        return tuple(
            sorted(
                (
                    *snapshots,
                    ApprovalEventSnapshot(
                        event=_record_reference(record),
                        validity="valid",
                    ),
                ),
                key=lambda item: item.event.event_id.encode("utf-8"),
            )
        )
    return tuple(
        snapshot.model_copy(
            update={
                "validity": (
                    "revoked"
                    if snapshot.event.event_id == event.supersedes_approval_event_id
                    else snapshot.validity
                )
            }
        )
        for snapshot in snapshots
    )


def project_records(
    records: Iterable[EventRecord],
    *,
    as_of_head: EventHead | None = None,
    budget_snapshots: Mapping[str, object] | None = None,
) -> RunProjection:
    ordered = tuple(records)
    if not ordered or not isinstance(
        ordered[0].event,
        (RunCreated, ReplacementRunCreated, ClarificationChildRunCreated),
    ):
        raise EventIntegrityError("missing-run-created")
    root = ordered[0].event
    projection = RunProjection(
        schema_version="automarkov.run-view.v2",
        run_id=root.run_id,
        experiment_id=root.experiment_id,
        projector_version=RUN_PROJECTOR_VERSION,
        projector_hash=RUN_PROJECTOR_HASH,
        state=RunState.RECEIVED,
        event_head=EventHead(
            run_id=root.run_id,
            sequence_no=0,
            event_hash=ordered[0].event_hash,
        ),
        budget_snapshot=None,
        waiting=None,
        terminal_event=None,
        terminal_snapshot_head=None,
        current_approval_snapshots=(),
        post_terminal_audit_event_references=(),
    )
    if as_of_head == projection.event_head:
        return projection
    by_id: dict[str, EventRecord] = {root.event_id: ordered[0]}
    previous_hash = ordered[0].event_hash
    previous_budget: BudgetSnapshot | None = None
    if root.previous_event_hash != ZERO_EVENT_HASH:
        raise EventIntegrityError(root.event_id)
    for expected_sequence, record in enumerate(ordered[1:], start=1):
        event = record.event
        if (
            event.run_id != root.run_id
            or event.sequence_no != expected_sequence
            or event.previous_event_hash != previous_hash
            or event.event_id in by_id
        ):
            raise EventIntegrityError(event.event_id)
        if isinstance(event, RunSuperseded) and (
            expected_sequence + 1 >= len(ordered)
            or not _is_run_supersession_transition(
                event,
                record.event_hash,
                ordered[expected_sequence + 1].event,
                projection.state,
            )
        ):
            raise InvalidRunTransitionError(
                projection.state.value,
                RunState.CANCELLED.value,
            )
        if isinstance(event, StageGatePassed) and (
            expected_sequence + 1 >= len(ordered)
            or not isinstance(
                ordered[expected_sequence + 1].event,
                StateTransitioned,
            )
            or not event.matches_transition(
                cast(
                    StateTransitioned,
                    ordered[expected_sequence + 1].event,
                )
            )
        ):
            raise InvalidRunTransitionError(
                event.from_state.value,
                event.to_state.value,
            )
        if projection.state in WAITING_STATES and not _waiting_event_is_allowed(
            projection.state, event
        ):
            raise RunResumeContractError(root.run_id)
        if projection.state in TERMINAL_STATES and not (
            isinstance(
                event,
                (
                    ArtifactAccessRevoked,
                    SpecificationConflictDetected,
                    ClarificationEvaluationRequested,
                    ClarificationEvaluationRecorded,
                ),
            )
            or isinstance(event, SignedApprovalEvent)
            and event.decision == "revoked"
        ):
            raise RunTerminalError(root.run_id)
        if isinstance(event, SignedApprovalEvent) and event.decision == "revoked":
            superseded_id = event.supersedes_approval_event_id
            superseded = by_id.get(superseded_id) if superseded_id is not None else None
            already_revoked = any(
                isinstance(previous.event, SignedApprovalEvent)
                and previous.event.decision == "revoked"
                and previous.event.supersedes_approval_event_id == superseded_id
                for previous in by_id.values()
            )
            if (
                superseded is None
                or not isinstance(superseded.event, SignedApprovalEvent)
                or superseded.event.decision != "approved"
                or superseded.event.artifact != event.artifact
                or already_revoked
            ):
                raise EventIntegrityError(event.event_id)
            if projection.state not in TERMINAL_STATES:
                next_record = (
                    ordered[expected_sequence + 1]
                    if expected_sequence + 1 < len(ordered)
                    else None
                )
                transition_record = (
                    ordered[expected_sequence + 2]
                    if expected_sequence + 2 < len(ordered)
                    else None
                )
                if not _revocation_rollback_is_valid(
                    event,
                    next_record.event if next_record is not None else None,
                    next_record.event_hash if next_record is not None else None,
                    (
                        transition_record.event
                        if transition_record is not None
                        else None
                    ),
                    projection.state,
                ):
                    raise InvalidRunTransitionError(
                        projection.state.value,
                        "approval_revoked",
                    )
        if isinstance(event, StateTransitioned):
            if event.from_state != projection.state or not allowed_transition(
                event.from_state, event.to_state
            ):
                raise InvalidRunTransitionError(
                    event.from_state.value,
                    event.to_state.value,
                )
            trigger = _require_trigger(event, by_id)
            if not _ordinary_transition_cause_is_valid(event, trigger):
                raise InvalidRunTransitionError(
                    event.from_state.value,
                    event.to_state.value,
                )
            if budget_snapshots is not None:
                raw_budget = budget_snapshots.get(event.budget_snapshot_artifact_id)
                if raw_budget is None:
                    raise BudgetContractError(root.run_id)
                try:
                    current_budget = BudgetSnapshot.model_validate_json(
                        canonical_json_bytes(raw_budget)
                    )
                except (TypeError, ValueError) as error:
                    raise BudgetContractError(root.run_id) from error
                if previous_budget is not None and not _budget_is_monotonic(
                    previous_budget, current_budget
                ):
                    raise BudgetContractError(root.run_id)
                exhausted = tuple(
                    counter.metric
                    for counter in current_budget.counters
                    if counter.consumed == counter.limit
                )
                if exhausted and event.to_state != RunState.BUDGET_EXHAUSTED:
                    raise BudgetContractError(root.run_id)
                if event.to_state == RunState.BUDGET_EXHAUSTED and (
                    not isinstance(trigger, BudgetExhausted | EvidenceBudgetExhausted)
                    or trigger.budget_snapshot_artifact_id
                    != event.budget_snapshot_artifact_id
                    or trigger.budget_snapshot_payload_hash
                    != event.budget_snapshot_payload_hash
                    or _BUDGET_METRIC_BY_KIND[trigger.budget_kind] not in exhausted
                    or not any(
                        counter.metric == _BUDGET_METRIC_BY_KIND[trigger.budget_kind]
                        and counter.limit == trigger.limit
                        and counter.consumed == trigger.consumed
                        for counter in current_budget.counters
                    )
                ):
                    raise BudgetContractError(root.run_id)
                previous_budget = current_budget
            waiting: WaitingBinding | None = None
            if event.to_state in WAITING_STATES:
                waiting = _waiting_binding(
                    trigger,
                    by_id[trigger.event_id],
                    by_id,
                )
                if (
                    waiting.waiting_state != event.to_state
                    or waiting.resume_state != event.from_state
                ):
                    raise RunResumeContractError(root.run_id)
            elif (
                event.from_state in WAITING_STATES
                and event.to_state not in TERMINAL_STATES
            ):
                _validate_resume(projection, trigger)
                if (
                    projection.waiting is None
                    or event.to_state != projection.waiting.resume_state
                ):
                    raise RunResumeContractError(root.run_id)
            if event.to_state in TERMINAL_STATES:
                if isinstance(trigger, RunSuperseded):
                    if not _is_run_supersession_transition(
                        trigger,
                        by_id[trigger.event_id].event_hash,
                        event,
                        projection.state,
                    ):
                        raise InvalidRunTransitionError(
                            event.from_state.value,
                            event.to_state.value,
                        )
                elif not isinstance(trigger, _TERMINAL_CAUSE_MODELS):
                    raise InvalidRunTransitionError(
                        event.from_state.value,
                        event.to_state.value,
                    )
                else:
                    cause = cast(TerminalCauseEvent, trigger)
                    if (
                        _terminal_target(cause) != event.to_state
                        or _terminal_reason(cause) != event.reason_code
                        or not _terminal_cause_matches_predecessor(
                            cause,
                            event.from_state,
                        )
                    ):
                        raise InvalidRunTransitionError(
                            event.from_state.value,
                            event.to_state.value,
                        )
                if event.to_state is RunState.BUDGET_EXHAUSTED and not isinstance(
                    trigger,
                    BudgetExhausted | EvidenceBudgetExhausted,
                ):
                    raise BudgetContractError(root.run_id)
            terminal_reference = (
                EventReference(
                    event_id=event.event_id,
                    sequence_no=event.sequence_no,
                    event_hash=record.event_hash,
                )
                if event.to_state in TERMINAL_STATES
                else None
            )
            projection = RunProjection(
                schema_version="automarkov.run-view.v2",
                run_id=root.run_id,
                experiment_id=root.experiment_id,
                projector_version=projection.projector_version,
                projector_hash=projection.projector_hash,
                state=event.to_state,
                event_head=EventHead(
                    run_id=root.run_id,
                    sequence_no=event.sequence_no,
                    event_hash=record.event_hash,
                ),
                budget_snapshot=ArtifactReference(
                    artifact_id=event.budget_snapshot_artifact_id,
                    payload_hash=event.budget_snapshot_payload_hash,
                ),
                waiting=waiting,
                terminal_event=terminal_reference,
                terminal_snapshot_head=(
                    EventHead(
                        run_id=root.run_id,
                        sequence_no=event.sequence_no,
                        event_hash=record.event_hash,
                    )
                    if terminal_reference is not None
                    else None
                ),
                current_approval_snapshots=projection.current_approval_snapshots,
                post_terminal_audit_event_references=(
                    projection.post_terminal_audit_event_references
                ),
            )
        else:
            was_terminal = projection.state in TERMINAL_STATES
            projection = projection.model_copy(
                update={
                    "event_head": EventHead(
                        run_id=root.run_id,
                        sequence_no=event.sequence_no,
                        event_hash=record.event_hash,
                    ),
                    "current_approval_snapshots": _approval_snapshots_after(
                        projection.current_approval_snapshots,
                        record,
                    ),
                    "post_terminal_audit_event_references": (
                        *projection.post_terminal_audit_event_references,
                        _record_reference(record),
                    )
                    if was_terminal
                    else projection.post_terminal_audit_event_references,
                }
            )
        by_id[event.event_id] = record
        previous_hash = record.event_hash
        if as_of_head is not None and (
            event.sequence_no == as_of_head.sequence_no
            and record.event_hash == as_of_head.event_hash
        ):
            return projection
    if as_of_head is not None and projection.event_head != as_of_head:
        raise RunProjectionHeadError(root.run_id)
    return projection


AppendRunEvent: TypeAlias = RunCreated | OrdinaryAppendEvent | PostTerminalEvent


class AppendRunEventsCommand(StrictFrozenModel):
    schema_version: Literal["automarkov.lifecycle-command.v1"]
    command_type: Literal["append_run_events"]
    command_id: EventId
    actor_principal_id: PrincipalIdValue
    issued_at: CanonicalTimestamp
    idempotency_key: NonEmptyId
    run_id: RunIdValue
    expected_state: RunState | None
    expected_head: EventHead | None
    events: FrozenSequence[AppendRunEvent]

    @field_validator("expected_state", mode="before")
    @classmethod
    def parse_expected_state(cls, value: object) -> RunState | None:
        return None if value is None else _parse_run_state(value)

    @field_validator("events")
    @classmethod
    def require_nonempty_events(
        cls, value: tuple[AppendRunEvent, ...]
    ) -> tuple[AppendRunEvent, ...]:
        if not value:
            raise ValueError("append command requires at least one event")
        return value

    @model_validator(mode="after")
    def require_command_scope(self) -> AppendRunEventsCommand:
        command_time_ms = int.from_bytes(UUID(self.command_id).bytes[:6], "big")
        issued_time_ms = int(datetime.fromisoformat(self.issued_at).timestamp() * 1000)
        if command_time_ms != issued_time_ms:
            raise ValueError("command UUIDv7 timestamp must match issued_at")
        if any(
            getattr(event, "actor_principal_id", self.actor_principal_id)
            != self.actor_principal_id
            for event in self.events
        ):
            raise ValueError("every event actor must match the authenticated command")
        if self.expected_head is None:
            if (
                self.expected_state is not None
                or len(self.events) != 1
                or not isinstance(self.events[0], RunCreated)
            ):
                raise ValueError("empty-head append must create exactly one root event")
        elif self.expected_state is None or any(
            isinstance(event, RunCreated) for event in self.events
        ):
            raise ValueError("existing-run append requires state and non-root events")
        elif self.expected_state in TERMINAL_STATES:
            if any(
                not (
                    isinstance(
                        event,
                        (
                            ArtifactAccessRevoked,
                            SpecificationConflictDetected,
                            ClarificationEvaluationRequested,
                            ClarificationEvaluationRecorded,
                        ),
                    )
                    or isinstance(event, SignedApprovalEvent)
                    and event.decision == "revoked"
                )
                for event in self.events
            ):
                raise ValueError("terminal run accepts only post-terminal events")
        elif any(
            isinstance(event, StateTransitioned) and event.to_state in TERMINAL_STATES
            for event in self.events
        ):
            raise ValueError("terminal transitions require a terminal commit")
        if self.expected_head is not None:
            first = self.events[0]
            if (
                first.sequence_no != self.expected_head.sequence_no + 1
                or first.previous_event_hash != self.expected_head.event_hash
                or any(
                    current.sequence_no != previous.sequence_no + 1
                    or current.previous_event_hash != _event_hash(previous)
                    for previous, current in zip(
                        self.events,
                        self.events[1:],
                        strict=False,
                    )
                )
            ):
                raise ValueError("append events do not extend the expected head")
        for index, event in enumerate(self.events):
            previous = self.events[index - 1] if index > 0 else None
            following = self.events[index + 1] if index + 1 < len(self.events) else None
            after_following = (
                self.events[index + 2] if index + 2 < len(self.events) else None
            )
            if (
                isinstance(event, SignedApprovalEvent)
                and event.decision == "revoked"
                and self.expected_state not in TERMINAL_STATES
            ):
                if self.expected_state is None or not _revocation_rollback_is_valid(
                    event,
                    following,
                    _event_hash(following) if following is not None else None,
                    after_following,
                    self.expected_state,
                ):
                    raise ValueError(
                        "nonterminal approval revocation requires rollback or replacement"
                    )
            elif isinstance(event, WaitingRuntime | WaitingEvidence | WaitingAsset):
                waiting_state = {
                    WaitingRuntime: RunState.WAITING_RUNTIME,
                    WaitingEvidence: RunState.WAITING_EVIDENCE,
                    WaitingAsset: RunState.WAITING_ASSET,
                }[type(event)]
                if (
                    previous is None
                    or following is None
                    or not isinstance(following, StateTransitioned)
                    or event.trigger_event_id != previous.event_id
                    or event.trigger_event_hash != _event_hash(previous)
                    or following.trigger_event_id != event.event_id
                    or following.trigger_event_hash != _event_hash(event)
                    or following.from_state != event.resume_state
                    or following.to_state != waiting_state
                    or isinstance(event, WaitingRuntime)
                    and event.dependency_kind == "local_llm"
                    and not isinstance(previous, LlmRuntimeDegraded)
                    or isinstance(event, WaitingEvidence)
                    and not isinstance(previous, EvidenceTemporarilyUnavailable)
                ):
                    raise ValueError("waiting entry requires an adjacent causal tuple")
            elif isinstance(event, Blocked):
                if (
                    previous is None
                    or following is None
                    or not isinstance(following, StateTransitioned)
                    or following.trigger_event_id != event.event_id
                    or following.trigger_event_hash != _event_hash(event)
                    or following.from_state != event.resume_state
                    or following.to_state is not RunState.BLOCKED
                    or event.block_reason_code == "evidence_authority_required"
                    and not isinstance(previous, EvidenceAuthorityRequired)
                ):
                    raise ValueError("blocked entry requires an adjacent causal tuple")
            elif isinstance(event, WaitResolved):
                waiting_state = {
                    "runtime": RunState.WAITING_RUNTIME,
                    "evidence": RunState.WAITING_EVIDENCE,
                    "asset": RunState.WAITING_ASSET,
                }[event.wait_kind]
                if (
                    following is None
                    or not isinstance(following, StateTransitioned)
                    or following.trigger_event_id != event.event_id
                    or following.trigger_event_hash != _event_hash(event)
                    or following.from_state != waiting_state
                    or following.to_state != event.resume_state
                ):
                    raise ValueError("wait resolution requires an adjacent transition")
            elif isinstance(event, BlockResolved) and (
                following is None
                or not isinstance(following, StateTransitioned)
                or following.trigger_event_id != event.event_id
                or following.trigger_event_hash != _event_hash(event)
                or following.from_state is not RunState.BLOCKED
            ):
                raise ValueError("block resolution requires an adjacent transition")
            elif isinstance(event, StageGatePassed) and (
                following is None
                or not isinstance(following, StateTransitioned)
                or following.trigger_event_id != event.event_id
                or following.trigger_event_hash != _event_hash(event)
                or not event.matches_transition(following)
            ):
                raise ValueError("stage gate requires an adjacent bound transition")
        return self


class RunProjectionRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.run-projection-request.v1"]
    run_id: RunIdValue
    as_of_sequence_no: SequenceNo
    as_of_event_head_hash: Sha256Value
    projector_version: Literal["automarkov.run-projector.v1"]
    projector_hash: Sha256Value


class RunAppendStep(StrictFrozenModel):
    schema_version: Literal["automarkov.run-append-step.v1"]
    event_record: EventRecord
    run_view: RunProjection
    idempotent: bool = Field(strict=True)


class LifecycleCommitReceipt(StrictFrozenModel):
    schema_version: Literal["automarkov.lifecycle-commit-receipt.v1"]
    command_id: EventId
    idempotency_key: NonEmptyId
    command_fingerprint: Sha256Value
    run_id: RunIdValue
    before_head: EventHead | None
    after_head: EventHead
    event_records: FrozenSequence[EventRecord]
    artifact_references: FrozenSequence[ArtifactReference]
    run_view: RunProjection
    process_execution_terminal_record: ArtifactReference | None = None
    terminal_result: ArtifactReference | None = None

    @model_validator(mode="after")
    def require_closed_receipt(self) -> LifecycleCommitReceipt:
        first = self.event_records[0] if self.event_records else None
        expected_first_sequence = (
            self.before_head.sequence_no + 1 if self.before_head is not None else 0
        )
        expected_first_hash = (
            self.before_head.event_hash
            if self.before_head is not None
            else ZERO_EVENT_HASH
        )
        linked_records = all(
            current.event.sequence_no == previous.event.sequence_no + 1
            and current.event.previous_event_hash == previous.event_hash
            for previous, current in zip(
                self.event_records,
                self.event_records[1:],
                strict=False,
            )
        )
        named_references = tuple(
            reference
            for reference in (
                self.process_execution_terminal_record,
                self.terminal_result,
            )
            if reference is not None
        )
        required_references = tuple(
            reference
            for reference in (
                *named_references,
                self.run_view.run_audit_projection,
            )
            if reference is not None
        )
        if (
            first is None
            or self.run_id != self.run_view.run_id
            or self.before_head is not None
            and self.before_head.run_id != self.run_id
            or self.after_head.run_id != self.run_id
            or self.after_head != self.run_view.event_head
            or any(record.event.run_id != self.run_id for record in self.event_records)
            or first.event.sequence_no != expected_first_sequence
            or first.event.previous_event_hash != expected_first_hash
            or not linked_records
            or self.event_records[-1].event.sequence_no != self.after_head.sequence_no
            or self.event_records[-1].event_hash != self.after_head.event_hash
            or len({reference.artifact_id for reference in self.artifact_references})
            != len(self.artifact_references)
            or any(
                reference not in self.artifact_references
                for reference in required_references
            )
            or self.terminal_result is not None
            and self.terminal_result != self.run_view.terminal_result
        ):
            raise ValueError("lifecycle receipt binding is invalid")
        return self

    @property
    def event_record(self) -> EventRecord:
        return self.event_records[-1]


class CrossRunLifecycleCommitReceipt(StrictFrozenModel):
    schema_version: Literal["automarkov.cross-run-lifecycle-commit-receipt.v1"]
    command_type: Literal[
        "create_replacement_run",
        "create_clarification_child_run",
    ]
    command_id: EventId
    idempotency_key: NonEmptyId
    command_fingerprint: Sha256Value
    parent_run_id: RunIdValue
    child_run_id: RunIdValue
    parent_before_head: EventHead
    parent_after_head: EventHead
    child_after_head: EventHead
    parent_event_records: FrozenSequence[EventRecord]
    child_event_records: FrozenSequence[EventRecord]
    artifact_references: FrozenSequence[ArtifactReference]
    parent_run_view: RunProjection
    child_run_view: RunProjection
    process_execution_terminal_record: ArtifactReference | None
    terminal_result: ArtifactReference | None
    run_audit_projection: ArtifactReference | None
    execution_attestation: ArtifactReference | None

    @model_validator(mode="after")
    def require_closed_cross_run_receipt(self) -> CrossRunLifecycleCommitReceipt:
        child_record = (
            self.child_event_records[0] if len(self.child_event_records) == 1 else None
        )
        named_artifacts = tuple(
            reference
            for reference in (
                self.process_execution_terminal_record,
                self.terminal_result,
                self.run_audit_projection,
                self.execution_attestation,
            )
            if reference is not None
        )
        expected_artifacts = tuple(
            sorted(
                set(named_artifacts),
                key=lambda reference: reference.artifact_id.encode("utf-8"),
            )
        )
        if (
            self.parent_run_id == self.child_run_id
            or self.parent_before_head.run_id != self.parent_run_id
            or self.parent_after_head.run_id != self.parent_run_id
            or self.child_after_head.run_id != self.child_run_id
            or self.parent_run_view.run_id != self.parent_run_id
            or self.child_run_view.run_id != self.child_run_id
            or self.parent_run_view.event_head != self.parent_after_head
            or self.child_run_view.event_head != self.child_after_head
            or self.parent_run_view.experiment_id != self.child_run_view.experiment_id
            or child_record is None
            or child_record.event.run_id != self.child_run_id
            or child_record.event.sequence_no != 0
            or child_record.event.previous_event_hash != ZERO_EVENT_HASH
            or child_record.event_hash != self.child_after_head.event_hash
            or self.artifact_references != expected_artifacts
        ):
            raise ValueError("cross-run receipt identity binding is invalid")
        if self.command_type == "create_replacement_run":
            if not self._is_replacement_receipt(child_record):
                raise ValueError("replacement receipt cardinality is invalid")
        elif not self._is_clarification_receipt(child_record):
            raise ValueError("clarification receipt cardinality is invalid")
        return self

    def _is_replacement_receipt(self, child_record: EventRecord) -> bool:
        if (
            len(self.parent_event_records) != 2
            or not isinstance(self.parent_event_records[0].event, RunSuperseded)
            or not isinstance(
                self.parent_event_records[1].event,
                StateTransitioned,
            )
            or not isinstance(child_record.event, ReplacementRunCreated)
            or any(
                reference is None
                for reference in (
                    self.process_execution_terminal_record,
                    self.terminal_result,
                    self.run_audit_projection,
                    self.execution_attestation,
                )
            )
            or len(self.artifact_references) != 4
            or self.parent_run_view.state is not RunState.CANCELLED
            or self.child_run_view.state is not RunState.RECEIVED
            or self.parent_run_view.terminal_result != self.terminal_result
            or self.parent_run_view.run_audit_projection != self.run_audit_projection
            or self.child_run_view.terminal_result is not None
            or self.child_run_view.run_audit_projection is not None
        ):
            return False
        superseded_record, transition_record = self.parent_event_records
        superseded = cast(RunSuperseded, superseded_record.event)
        transition = cast(StateTransitioned, transition_record.event)
        child_created = cast(ReplacementRunCreated, child_record.event)
        return (
            superseded.run_id == self.parent_run_id
            and superseded.child_run_id == self.child_run_id
            and superseded.sequence_no == self.parent_before_head.sequence_no + 1
            and superseded.previous_event_hash == self.parent_before_head.event_hash
            and superseded_record.event_hash == transition.previous_event_hash
            and transition.sequence_no == superseded.sequence_no + 1
            and transition.trigger_event_id == superseded.event_id
            and transition.trigger_event_hash == superseded_record.event_hash
            and transition.to_state is RunState.CANCELLED
            and transition_record.event_hash == self.parent_after_head.event_hash
            and transition.sequence_no == self.parent_after_head.sequence_no
            and child_created.parent_run_id == self.parent_run_id
            and child_created.parent_run_superseded_event_id == superseded.event_id
            and child_created.supersession_cause == superseded.supersession_cause
            and child_created.replacement_ordinal == superseded.replacement_ordinal
        )

    def _is_clarification_receipt(self, child_record: EventRecord) -> bool:
        return (
            not self.parent_event_records
            and self.parent_before_head == self.parent_after_head
            and isinstance(child_record.event, ClarificationChildRunCreated)
            and child_record.event.parent_run_id == self.parent_run_id
            and not self.artifact_references
            and self.process_execution_terminal_record is None
            and self.terminal_result is None
            and self.run_audit_projection is None
            and self.execution_attestation is None
            and self.parent_run_view.state is RunState.CLARIFICATION_REQUIRED
            and self.child_run_view.state is RunState.RECEIVED
            and self.child_run_view.terminal_result is None
            and self.child_run_view.run_audit_projection is None
        )


LifecycleCommitResult: TypeAlias = Annotated[
    LifecycleCommitReceipt | CrossRunLifecycleCommitReceipt,
    Field(discriminator="schema_version"),
]


def _run_append_step(
    record: EventRecord,
    view: RunProjection,
    *,
    idempotent: bool,
) -> RunAppendStep:
    return RunAppendStep.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": "automarkov.run-append-step.v1",
                "event_record": record.model_dump(
                    mode="json", round_trip=True, warnings="error"
                ),
                "run_view": view.model_dump(
                    mode="json", round_trip=True, warnings="error"
                ),
                "idempotent": idempotent,
            }
        )
    )


class ProcessExecutionTerminalRecord(StrictFrozenModel):
    schema_version: Literal["automarkov.process-execution-terminal-record.v1"]
    signing_domain: Literal["AutoMarkov-ProcessExecutionTerminalRecord-v1"]
    experiment_id: NonEmptyId | None
    run_id: RunIdValue
    job_id: NonEmptyId
    process_execution_id: NonEmptyId
    profile_id: NonEmptyId
    principal_id: PrincipalIdValue
    job_manifest: ArtifactReference
    status: Literal["success", "terminal_failure"]
    exit_code: SafeCanonicalInt
    reason_code: ReasonCode
    started_at: CanonicalTimestamp
    finished_at: CanonicalTimestamp
    stdout_hash: Sha256Value
    stderr_hash: Sha256Value
    payload_outputs: FrozenSequence[ArtifactReference]
    resource_usage: ArtifactReference
    network_log_hash: Sha256Value
    mount_attestation_hash: Sha256Value
    capability_decision_hash: Sha256Value
    egress_log_hash: Sha256Value
    created_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_exit_code_range(self) -> ProcessExecutionTerminalRecord:
        output_keys = tuple(
            (output.artifact_id, output.payload_hash) for output in self.payload_outputs
        )
        if not 0 <= self.exit_code <= 255:
            raise ValueError("exit_code must be between 0 and 255")
        if (self.status == "success") != (self.exit_code == 0):
            raise ValueError("process status and exit code are inconsistent")
        if output_keys != tuple(
            sorted(set(output_keys), key=lambda item: item[0].encode("utf-8"))
        ):
            raise ValueError("payload outputs must be sorted and unique")
        if datetime.fromisoformat(self.started_at) > datetime.fromisoformat(
            self.finished_at
        ):
            raise ValueError("process execution timestamps are reversed")
        return self


class ExecutionPhaseTransition(StrictFrozenModel):
    from_phase: NonEmptyId
    to_phase: NonEmptyId
    transitioned_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_state_change(self) -> ExecutionPhaseTransition:
        if self.from_phase == self.to_phase:
            raise ValueError("execution phase transition must change phase")
        return self


class ExecutionAttestation(StrictFrozenModel):
    schema_version: Literal["automarkov.execution-attestation.v1"]
    signing_domain: Literal["AutoMarkov-Execution-Attestation-v1"]
    experiment_id: NonEmptyId | None
    run_id: RunIdValue
    job_id: NonEmptyId
    process_execution_id: NonEmptyId
    profile_id: NonEmptyId
    principal_id: PrincipalIdValue
    job_manifest: ArtifactReference
    process_terminal_record: ArtifactReference
    payload_outputs: FrozenSequence[ArtifactReference]
    terminal_result: ArtifactReference | None
    network_policy_hash: Sha256Value
    mount_table_hash: Sha256Value
    capability_decision_log_hash: Sha256Value
    actual_phase_transition: ExecutionPhaseTransition
    egress_decision_log_hash: Sha256Value
    egress_revoked_at: CanonicalTimestamp
    issued_at: CanonicalTimestamp
    nonce_b64url: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{22}$")]
    signing_key_id: NonEmptyId
    signature_algorithm: Literal["Ed25519"]
    signature_b64url: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{86}$")]

    @model_validator(mode="after")
    def require_closed_attestation(self) -> ExecutionAttestation:
        output_keys = tuple(
            (output.artifact_id, output.payload_hash) for output in self.payload_outputs
        )
        if output_keys != tuple(
            sorted(set(output_keys), key=lambda item: item[0].encode("utf-8"))
        ):
            raise ValueError("attested payload outputs must be sorted and unique")
        issued_at = datetime.fromisoformat(self.issued_at)
        if (
            datetime.fromisoformat(self.actual_phase_transition.transitioned_at)
            > issued_at
            or datetime.fromisoformat(self.egress_revoked_at) > issued_at
        ):
            raise ValueError("attestation cannot precede execution evidence")
        try:
            nonce = base64.urlsafe_b64decode(self.nonce_b64url + "==")
        except ValueError as error:
            raise ValueError("nonce must be canonical base64url") from error
        if (
            len(nonce) != 16
            or base64.urlsafe_b64encode(nonce).decode().rstrip("=") != self.nonce_b64url
        ):
            raise ValueError("nonce must contain exactly 128 canonical bits")
        try:
            signature = base64.urlsafe_b64decode(self.signature_b64url + "==")
        except ValueError as error:
            raise ValueError("signature must be canonical Ed25519 base64url") from error
        if (
            len(signature) != 64
            or base64.urlsafe_b64encode(signature).decode().rstrip("=")
            != self.signature_b64url
        ):
            raise ValueError("signature must be canonical Ed25519 base64url")
        return self


class RunOutcomeMask(StrictFrozenModel):
    e2e_valid: SafeCanonicalInt
    gold_policy_evaluation_valid: SafeCanonicalInt
    q_gate: SafeCanonicalInt

    @model_validator(mode="after")
    def require_binary_values(self) -> RunOutcomeMask:
        if any(
            value not in {0, 1}
            for value in (
                self.e2e_valid,
                self.gold_policy_evaluation_valid,
                self.q_gate,
            )
        ):
            raise ValueError("outcome mask values must be binary")
        return self


def run_audit_projection_id(payload: Mapping[str, object]) -> str:
    if type(payload) is not dict or "projection_id" in payload:
        raise ValueError("projection ID preimage must exclude projection_id")
    return (
        "sha256:"
        + sha256(
            canonical_json_bytes(
                {
                    "domain": "AutoMarkov-RunAuditProjection-ID-v1",
                    "projection": payload,
                }
            )
        ).hexdigest()
    )


class TerminalResult(StrictFrozenModel):
    schema_version: Literal["automarkov.terminal-result.v1"]
    signing_domain: Literal["AutoMarkov-TerminalResult-v1"]
    run_id: RunIdValue
    experiment_id: NonEmptyId | None
    fixed_commit_job_manifest: ArtifactReference
    process_execution_terminal_record: ArtifactReference
    process_execution_id: NonEmptyId
    terminal_event: EventReference
    terminal_snapshot_event_head: VerifiedEventHead
    terminal_state: Literal[
        "COMPLETED",
        "CLARIFICATION_REQUIRED",
        "OOD_PACKAGED",
        "PARTIAL",
        "BUDGET_EXHAUSTED",
        "FAILED",
        "CANCELLED",
    ]
    terminal_reason_code: ReasonCode
    payload_outputs: FrozenSequence[ArtifactReference]
    terminal_time_approvals: FrozenSequence[ApprovalEventSnapshot]
    projector_version: Literal["automarkov.run-projector.v1"]
    projector_hash: Sha256Value
    created_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_terminal_bindings(self) -> TerminalResult:
        output_keys = tuple(
            (output.artifact_id, output.payload_hash) for output in self.payload_outputs
        )
        approval_keys = tuple(
            (
                approval.event.event_id,
                approval.event.sequence_no,
                approval.event.event_hash,
            )
            for approval in self.terminal_time_approvals
        )
        if (
            self.terminal_event.sequence_no
            != self.terminal_snapshot_event_head.sequence_no
            or self.terminal_event.event_hash
            != self.terminal_snapshot_event_head.event_hash.root
            or self.terminal_snapshot_event_head.run_id.root != self.run_id
            or output_keys
            != tuple(sorted(set(output_keys), key=lambda item: item[0].encode("utf-8")))
            or approval_keys
            != tuple(
                sorted(
                    set(approval_keys),
                    key=lambda item: item[0].encode("utf-8"),
                )
            )
            or any(
                approval.validity != "valid"
                for approval in self.terminal_time_approvals
            )
            or self.projector_hash != RUN_PROJECTOR_HASH
        ):
            raise ValueError("terminal result binding is invalid")
        return self


class RunAuditProjection(StrictFrozenModel):
    schema_version: Literal["automarkov.run-audit-projection.v1"]
    signing_domain: Literal["AutoMarkov-RunAuditProjection-v1"]
    projection_id: Sha256Value
    run_id: RunIdValue
    experiment_id: NonEmptyId | None
    projector_version: Literal["automarkov.run-projector.v1"]
    projector_hash: Sha256Value
    as_of_event_head: VerifiedEventHead
    previous_projection: ArtifactReference | None
    terminal_result: ArtifactReference
    current_approval_snapshots: FrozenSequence[ApprovalEventSnapshot]
    post_terminal_audit_event_references: FrozenSequence[EventReference]
    signed_deviations: FrozenSequence[ArtifactReference]
    outcome_mask: RunOutcomeMask

    @model_validator(mode="after")
    def require_closed_projection(self) -> RunAuditProjection:
        approval_keys = tuple(
            (
                snapshot.event.event_id,
                snapshot.event.sequence_no,
                snapshot.event.event_hash,
            )
            for snapshot in self.current_approval_snapshots
        )
        audit_keys = tuple(
            (event.event_id, event.sequence_no, event.event_hash)
            for event in self.post_terminal_audit_event_references
        )
        deviation_keys = tuple(
            (reference.artifact_id, reference.payload_hash)
            for reference in self.signed_deviations
        )
        payload = cast(
            dict[str, object],
            self.model_dump(mode="json", round_trip=True, warnings="error"),
        )
        del payload["projection_id"]
        if (
            self.as_of_event_head.run_id.root != self.run_id
            or self.projector_hash != RUN_PROJECTOR_HASH
            or approval_keys
            != tuple(
                sorted(set(approval_keys), key=lambda item: item[0].encode("utf-8"))
            )
            or audit_keys != tuple(sorted(set(audit_keys), key=lambda item: item[1]))
            or deviation_keys
            != tuple(
                sorted(set(deviation_keys), key=lambda item: item[0].encode("utf-8"))
            )
            or self.projection_id != run_audit_projection_id(payload)
        ):
            raise ValueError("audit projection binding is invalid")
        return self


_PARTIAL_FAILURE_CODES = frozenset(
    {
        "sealed_e2e_gate_failed",
        "training_smoke_failed",
        "required_evaluation_result_missing",
        "required_package_artifact_missing",
    }
)
_VALIDATION_SCOPE_PREDECESSORS: Mapping[str, frozenset[RunState]] = MappingProxyType(
    {
        "approval": frozenset(
            {
                RunState.WAITING_TEXT_CONFIRMATION,
                RunState.WAITING_FORMAL_CONFIRMATION,
            }
        ),
        "classification": frozenset({RunState.TEXT_LOCKED, RunState.CLASSIFIED}),
        "formalization": frozenset({RunState.FORMAL_LOCKED}),
        "ood_handoff": frozenset({RunState.OOD_HANDOFF_VALIDATING}),
        "sealed_e2e": frozenset({RunState.SEALED_E2E_VALIDATING}),
        "training_smoke": frozenset({RunState.TRAINING_SMOKE_TESTING}),
        "policy_training": frozenset({RunState.POLICY_TRAINING}),
        "final_evaluation": frozenset({RunState.FINAL_EVALUATING}),
        "packaging": frozenset({RunState.PACKAGING}),
        "internal": _FAILED_PREDECESSORS,
    }
)
_VALIDATION_CLAIM_CONTRACTS: Mapping[str, tuple[RunState, str, RunState]] = (
    MappingProxyType(
        {
            "ood_handoff": (
                RunState.OOD_PACKAGED,
                "ood_handoff_packaged",
                RunState.OOD_HANDOFF_VALIDATING,
            ),
            "package": (
                RunState.COMPLETED,
                "run_completed",
                RunState.PACKAGING,
            ),
        }
    )
)
_TERMINATION_REASON_TARGETS: Mapping[str, RunState] = MappingProxyType(
    {
        "user_cancelled": RunState.CANCELLED,
        "continuation_declined": RunState.PARTIAL,
        "asset_unavailable": RunState.PARTIAL,
        "partial_accepted": RunState.PARTIAL,
    }
)


def _edge_key(row: list[str]) -> tuple[bytes, ...]:
    return tuple(value.encode("utf-8") for value in row)


def _run_projector_contract_preimage() -> dict[str, object]:
    stage_gate = [
        [source.value, target.value, gate_id, reason]
        for (source, target), (gate_id, reason) in _STAGE_GATE_CONTRACTS.items()
    ]
    approval = [
        [source.value, target.value, decision, reason]
        for (source, target), (decision, reason) in (
            _APPROVAL_TRANSITION_CONTRACTS.items()
        )
    ]
    revision = [
        [source.value, target.value, reason]
        for (source, target), reason in _REVISION_REASON_BY_EDGE.items()
    ] + [
        [
            RunState.WAITING_REDUCTION_CONFIRMATION.value,
            RunState.TEXT_DRAFTED.value,
            "reduction_approved",
        ]
    ]
    revocation = [
        [source.value, target.value, "approval_revoked"]
        for source, targets in _APPROVAL_REVOCATION_TARGETS.items()
        for target in targets
    ]
    waiting = [
        [
            source.value,
            target.value,
            (
                _WAIT_ENTRY_MODELS[target].__name__
                if target in WAITING_STATES
                else _WAIT_RESOLUTION_MODELS[source].__name__
            ),
        ]
        for source, target in _WAIT_TRANSITIONS
    ]
    terminal = [
        [
            "ClarificationRequested",
            RunState.CLARIFICATION_REQUIRED.value,
            "clarification_required",
            RunState.TEXT_REVIEWED.value,
        ],
        *[
            [
                f"ValidationClaimed:{scope}",
                target.value,
                reason,
                predecessor.value,
            ]
            for scope, (target, reason, predecessor) in (
                _VALIDATION_CLAIM_CONTRACTS.items()
            )
        ],
        [
            "ValidationFailed:partial",
            RunState.PARTIAL.value,
            *sorted(_PARTIAL_FAILURE_CODES, key=lambda item: item.encode("utf-8")),
        ],
        ["ValidationFailed:other", RunState.FAILED.value],
        *[
            [f"RunTerminationRequested:{reason}", target.value, reason]
            for reason, target in _TERMINATION_REASON_TARGETS.items()
        ],
        ["BudgetExhausted", RunState.BUDGET_EXHAUSTED.value, "budget_exhausted"],
        [
            "EvidenceBudgetExhausted",
            RunState.BUDGET_EXHAUSTED.value,
            "budget_exhausted",
        ],
        ["RunSuperseded", RunState.CANCELLED.value, "run_superseded"],
    ]
    return {
        "domain": "AutoMarkov-RunProjector-Contract-v1",
        "version": RUN_PROJECTOR_VERSION,
        "transitions": {
            source: sorted(targets, key=lambda item: item.encode("utf-8"))
            for source, targets in sorted(_TRANSITION_NAMES.items())
        },
        "event_schemas": [
            list(contract) for contract in default_event_schema_registry().snapshot()
        ],
        "terminal_states": sorted(
            (state.value for state in TERMINAL_STATES),
            key=lambda item: item.encode("utf-8"),
        ),
        "exact_terminal_transitions": sorted(
            (
                [source.value, target.value]
                for source, target in _EXACT_TERMINAL_TRANSITIONS
            ),
            key=_edge_key,
        ),
        "waiting_states": sorted(
            (state.value for state in WAITING_STATES),
            key=lambda item: item.encode("utf-8"),
        ),
        "causal_contracts": {
            "stage_gate": sorted(stage_gate, key=_edge_key),
            "approval": sorted(approval, key=_edge_key),
            "revision": sorted(revision, key=_edge_key),
            "revocation": sorted(revocation, key=_edge_key),
            "waiting": sorted(waiting, key=_edge_key),
            "terminal": sorted(terminal, key=_edge_key),
            "budget_metric_by_kind": sorted(
                ([kind, metric] for kind, metric in _BUDGET_METRIC_BY_KIND.items()),
                key=_edge_key,
            ),
            "partial_failure_codes": sorted(
                _PARTIAL_FAILURE_CODES,
                key=lambda item: item.encode("utf-8"),
            ),
            "validation_scope_predecessors": [
                [
                    scope,
                    *sorted(
                        (state.value for state in predecessors),
                        key=lambda item: item.encode("utf-8"),
                    ),
                ]
                for scope, predecessors in sorted(
                    _VALIDATION_SCOPE_PREDECESSORS.items()
                )
            ],
        },
        "rules": [
            "approval-revocation-adjacency-v1",
            "budget-monotonicity-v1",
            "immediate-transition-trigger-v1",
            "post-terminal-audit-only-v1",
            "projection-approval-snapshot-v1",
            "projection-post-terminal-audit-reference-v1",
            "specified-head-only-v1",
            "stage-gate-full-binding-v1",
            "waiting-identity-gate-authority-v1",
        ],
    }


RUN_PROJECTOR_HASH = (
    "sha256:"
    + sha256(canonical_json_bytes(_run_projector_contract_preimage())).hexdigest()
)


def _terminal_target(cause: TerminalCauseEvent) -> RunState:
    if isinstance(cause, ClarificationRequested):
        return RunState.CLARIFICATION_REQUIRED
    if isinstance(cause, ValidationClaimed):
        return _VALIDATION_CLAIM_CONTRACTS[cause.validation_scope][0]
    if isinstance(cause, ValidationFailed):
        return (
            RunState.PARTIAL
            if cause.failure_code in _PARTIAL_FAILURE_CODES
            else RunState.FAILED
        )
    if isinstance(cause, RunTerminationRequested):
        return cause.requested_terminal_state
    return RunState.BUDGET_EXHAUSTED


def _terminal_reason(cause: TerminalCauseEvent) -> str:
    if isinstance(cause, ClarificationRequested | RunTerminationRequested):
        return cause.reason_code
    if isinstance(cause, ValidationClaimed):
        return _VALIDATION_CLAIM_CONTRACTS[cause.validation_scope][1]
    if isinstance(cause, ValidationFailed):
        return cause.failure_code
    return cause.reason_code


def _terminal_cause_matches_predecessor(
    cause: TerminalCauseEvent,
    predecessor: RunState,
) -> bool:
    if isinstance(cause, ClarificationRequested):
        return predecessor is RunState.TEXT_REVIEWED
    if isinstance(cause, ValidationClaimed):
        return predecessor is _VALIDATION_CLAIM_CONTRACTS[cause.validation_scope][2]
    if isinstance(cause, ValidationFailed):
        return predecessor in _VALIDATION_SCOPE_PREDECESSORS[cause.validation_scope]
    return allowed_transition(predecessor, _terminal_target(cause))


class CommitTerminalCommand(StrictFrozenModel):
    schema_version: Literal["automarkov.lifecycle-command.v1"]
    command_type: Literal["commit_terminal"]
    command_id: EventId
    actor_principal_id: PrincipalIdValue
    issued_at: CanonicalTimestamp
    idempotency_key: NonEmptyId
    run_id: RunIdValue
    expected_state: RunState
    expected_head: EventHead
    events: FrozenSequence[TerminalCauseEvent | StateTransitioned]
    process_terminal_record: ProcessExecutionTerminalRecord
    fixed_commit_job_manifest: ArtifactReference
    terminal_time_approvals: FrozenSequence[ApprovalEventSnapshot]
    projector_version: NonEmptyId
    projector_hash: Sha256Value
    created_at: CanonicalTimestamp

    @field_validator("expected_state", mode="before")
    @classmethod
    def parse_expected_state(cls, value: object) -> RunState:
        return _parse_run_state(value)

    @model_validator(mode="after")
    def require_command_identity(self) -> CommitTerminalCommand:
        command_time_ms = int.from_bytes(UUID(self.command_id).bytes[:6], "big")
        issued_time_ms = int(datetime.fromisoformat(self.issued_at).timestamp() * 1000)
        if command_time_ms != issued_time_ms:
            raise ValueError("command UUIDv7 timestamp must match issued_at")
        if self.process_terminal_record.principal_id != self.actor_principal_id:
            raise ValueError("terminal process principal must match command actor")
        if (
            self.projector_version != RUN_PROJECTOR_VERSION
            or self.projector_hash != RUN_PROJECTOR_HASH
        ):
            raise ValueError("terminal command projector identity is unavailable")
        approval_keys = tuple(
            (reference.event.event_id, reference.event.event_hash)
            for reference in self.terminal_time_approvals
        )
        if approval_keys != tuple(
            sorted(set(approval_keys), key=lambda item: item[0].encode("utf-8"))
        ):
            raise ValueError("terminal approval references must be sorted and unique")
        if (
            len(self.events) != 2
            or not isinstance(
                self.events[0],
                (
                    ClarificationRequested,
                    ValidationClaimed,
                    ValidationFailed,
                    RunTerminationRequested,
                    BudgetExhausted,
                    EvidenceBudgetExhausted,
                ),
            )
            or not isinstance(self.events[1], StateTransitioned)
        ):
            raise ValueError("terminal commit requires one cause and one transition")
        cause = self.events[0]
        transition = self.events[1]
        terminal_state = _terminal_target(cause)
        if (
            cause.run_id != self.run_id
            or transition.run_id != self.run_id
            or cause.actor_principal_id != self.actor_principal_id
            or transition.actor_principal_id != self.actor_principal_id
            or cause.actor_process_execution_id
            != self.process_terminal_record.process_execution_id
            or transition.actor_process_execution_id
            != self.process_terminal_record.process_execution_id
            or cause.sequence_no != self.expected_head.sequence_no + 1
            or cause.previous_event_hash != self.expected_head.event_hash
            or transition.sequence_no != cause.sequence_no + 1
            or transition.previous_event_hash != _event_hash(cause)
            or transition.trigger_event_id != cause.event_id
            or transition.trigger_event_hash != _event_hash(cause)
            or transition.from_state != self.expected_state
            or transition.to_state != terminal_state
            or transition.to_state not in TERMINAL_STATES
            or transition.reason_code != _terminal_reason(cause)
            or not _terminal_cause_matches_predecessor(cause, self.expected_state)
        ):
            raise ValueError("terminal cause/transition binding is invalid")
        return self


class RuntimeReplacementPrerequisite(StrictFrozenModel):
    prerequisite_type: Literal["runtime_identity_replacement"]
    failed_waiting_event: EventReference
    failed_readiness_gate_id: NonEmptyId
    old_dependency_identity_hash: Sha256Value
    new_dependency_identity_hash: Sha256Value

    @model_validator(mode="after")
    def require_changed_identity(self) -> RuntimeReplacementPrerequisite:
        if self.old_dependency_identity_hash == self.new_dependency_identity_hash:
            raise ValueError("replacement dependency identity must change")
        return self

    def binds_parent_head(
        self,
        *,
        failed_waiting_event_id: str | None,
        expected_parent_head_sequence_no: int,
    ) -> bool:
        """验证 active waiting 引用位于随后落库的转换 head 之前。"""

        return (
            failed_waiting_event_id == self.failed_waiting_event.event_id
            and self.failed_waiting_event.sequence_no < expected_parent_head_sequence_no
        )


class ApprovalRevocationPrerequisite(StrictFrozenModel):
    prerequisite_type: Literal["approval_revocation"]
    revocation_event: EventReference
    revoked_approval_event: EventReference
    artifact: ArtifactReference

    @model_validator(mode="after")
    def require_distinct_events(self) -> ApprovalRevocationPrerequisite:
        if self.revocation_event.event_id == self.revoked_approval_event.event_id:
            raise ValueError("revocation and revoked approval events must differ")
        return self


ReplacementPrerequisite: TypeAlias = Annotated[
    RuntimeReplacementPrerequisite | ApprovalRevocationPrerequisite,
    Field(discriminator="prerequisite_type"),
]


class CreateReplacementRunCommand(StrictFrozenModel):
    schema_version: Literal["automarkov.lifecycle-command.v1"]
    command_type: Literal["create_replacement_run"]
    command_id: EventId
    actor_principal_id: PrincipalIdValue
    issued_at: CanonicalTimestamp
    idempotency_key: NonEmptyId
    parent_run_id: RunIdValue
    child_run_id: RunIdValue
    expected_parent_state: RunState
    expected_parent_head: VerifiedEventHead
    expected_child_head: None
    old_run_manifest: ArtifactReference
    child_run_manifest: ArtifactReference
    replacement_policy: ArtifactReference
    cause_prerequisite: ReplacementPrerequisite
    slot_decision: ArtifactReference
    replacement_eligibility: Literal[
        "confirmatory_slot_reused",
        "new_nonconfirmatory_slot",
        "slot_terminal_failure",
    ]
    fixed_commit_job_manifest: ArtifactReference
    process_terminal_record: ProcessExecutionTerminalRecord
    run_superseded_event: RunSuperseded
    parent_terminal_transition: StateTransitioned
    replacement_run_created_event: ReplacementRunCreated
    execution_attestation: ExecutionAttestation
    projector_version: NonEmptyId
    projector_hash: Sha256Value

    @field_validator("expected_parent_state", mode="before")
    @classmethod
    def parse_expected_parent_state(cls, value: object) -> RunState:
        return _parse_run_state(value)

    @model_validator(mode="after")
    def require_cross_run_bindings(self) -> CreateReplacementRunCommand:
        command_time_ms = int.from_bytes(UUID(self.command_id).bytes[:6], "big")
        issued_time_ms = int(datetime.fromisoformat(self.issued_at).timestamp() * 1000)
        superseded = self.run_superseded_event
        transition = self.parent_terminal_transition
        child_created = self.replacement_run_created_event
        process = self.process_terminal_record
        attestation = self.execution_attestation
        if (
            command_time_ms != issued_time_ms
            or self.parent_run_id == self.child_run_id
            or self.expected_parent_head.run_id.root != self.parent_run_id
            or self.expected_parent_state in TERMINAL_STATES
            or self.projector_version != RUN_PROJECTOR_VERSION
            or self.projector_hash != RUN_PROJECTOR_HASH
        ):
            raise ValueError("replacement command identity or CAS binding is invalid")
        if (
            superseded.run_id != self.parent_run_id
            or superseded.child_run_id != self.child_run_id
            or superseded.sequence_no != self.expected_parent_head.sequence_no + 1
            or superseded.previous_event_hash
            != self.expected_parent_head.event_hash.root
            or superseded.replacement_authority_principal_id != self.actor_principal_id
            or superseded.replacement_eligibility != self.replacement_eligibility
            or superseded.old_run_manifest_artifact_id
            != self.old_run_manifest.artifact_id
            or superseded.old_run_manifest_payload_hash
            != self.old_run_manifest.payload_hash
            or superseded.child_run_manifest_artifact_id
            != self.child_run_manifest.artifact_id
            or superseded.child_run_manifest_payload_hash
            != self.child_run_manifest.payload_hash
            or superseded.replacement_policy_artifact_id
            != self.replacement_policy.artifact_id
            or superseded.replacement_policy_payload_hash
            != self.replacement_policy.payload_hash
        ):
            raise ValueError("run supersession event does not match its command")
        superseded_hash = _event_hash(superseded)
        if (
            transition.run_id != self.parent_run_id
            or transition.experiment_id != superseded.experiment_id
            or transition.actor_principal_id != self.actor_principal_id
            or transition.actor_process_execution_id != process.process_execution_id
            or transition.sequence_no != superseded.sequence_no + 1
            or transition.previous_event_hash != superseded_hash
            or transition.trigger_event_id != superseded.event_id
            or transition.trigger_event_hash != superseded_hash
            or transition.from_state != self.expected_parent_state
            or transition.to_state is not RunState.CANCELLED
            or transition.reason_code != "run_superseded"
        ):
            raise ValueError("replacement terminal transition binding is invalid")
        if (
            child_created.run_id != self.child_run_id
            or child_created.parent_run_id != self.parent_run_id
            or child_created.experiment_id != superseded.experiment_id
            or child_created.parent_run_superseded_event_id != superseded.event_id
            or child_created.supersession_cause != superseded.supersession_cause
            or child_created.replacement_ordinal != superseded.replacement_ordinal
            or child_created.replacement_authority_principal_id
            != self.actor_principal_id
            or child_created.run_manifest_artifact_id
            != self.child_run_manifest.artifact_id
            or child_created.run_manifest_payload_hash
            != self.child_run_manifest.payload_hash
            or child_created.replacement_policy_artifact_id
            != self.replacement_policy.artifact_id
            or child_created.replacement_policy_payload_hash
            != self.replacement_policy.payload_hash
            or child_created.signing_key_id != superseded.signing_key_id
            or child_created.nonce_b64url == superseded.nonce_b64url
        ):
            raise ValueError("replacement child bootstrap binding is invalid")
        if (
            process.run_id != self.parent_run_id
            or process.experiment_id != superseded.experiment_id
            or process.principal_id != self.actor_principal_id
            or process.job_manifest != self.fixed_commit_job_manifest
            or process.status != "success"
            or attestation.run_id != self.parent_run_id
            or attestation.experiment_id != process.experiment_id
            or attestation.job_id != process.job_id
            or attestation.process_execution_id != process.process_execution_id
            or attestation.profile_id != process.profile_id
            or attestation.principal_id != process.principal_id
            or attestation.job_manifest != self.fixed_commit_job_manifest
            or attestation.payload_outputs != process.payload_outputs
            or attestation.terminal_result is None
        ):
            raise ValueError("replacement process provenance binding is invalid")
        prerequisite = self.cause_prerequisite
        if isinstance(prerequisite, RuntimeReplacementPrerequisite):
            if (
                superseded.supersession_cause != "runtime_identity_replacement"
                or self.expected_parent_state is not RunState.WAITING_RUNTIME
                or not prerequisite.binds_parent_head(
                    failed_waiting_event_id=superseded.failed_waiting_event_id,
                    expected_parent_head_sequence_no=(
                        self.expected_parent_head.sequence_no
                    ),
                )
                or prerequisite.failed_readiness_gate_id
                != superseded.failed_readiness_gate_id
                or prerequisite.old_dependency_identity_hash
                != superseded.old_dependency_identity_hash
                or prerequisite.new_dependency_identity_hash
                != superseded.new_dependency_identity_hash
            ):
                raise ValueError("runtime replacement prerequisite is invalid")
        elif (
            superseded.supersession_cause != "approval_revocation"
            or prerequisite.revocation_event.event_id != superseded.revocation_event_id
            or prerequisite.revoked_approval_event.event_id
            != superseded.revoked_approval_event_id
            or prerequisite.artifact.artifact_id != superseded.artifact_id
            or prerequisite.artifact.payload_hash != superseded.artifact_payload_hash
        ):
            raise ValueError("approval replacement prerequisite is invalid")
        return self


class CreateClarificationChildRunCommand(StrictFrozenModel):
    schema_version: Literal["automarkov.lifecycle-command.v1"]
    command_type: Literal["create_clarification_child_run"]
    command_id: EventId
    actor_principal_id: PrincipalIdValue
    issued_at: CanonicalTimestamp
    idempotency_key: NonEmptyId
    parent_run_id: RunIdValue
    child_run_id: RunIdValue
    expected_parent_head: VerifiedEventHead
    expected_child_head: None
    parent_clarification_result: ArtifactReference
    parent_terminal_result: ArtifactReference
    parent_terminal_snapshot_event_head: VerifiedEventHead
    signed_answer_bundle: ArtifactReference
    continuation_policy: ArtifactReference
    child_run_manifest: ArtifactReference
    clarification_child_run_created_event: ClarificationChildRunCreated

    @model_validator(mode="after")
    def require_continuation_bindings(self) -> CreateClarificationChildRunCommand:
        command_time_ms = int.from_bytes(UUID(self.command_id).bytes[:6], "big")
        issued_time_ms = int(datetime.fromisoformat(self.issued_at).timestamp() * 1000)
        snapshot = self.parent_terminal_snapshot_event_head
        expected = self.expected_parent_head
        event = self.clarification_child_run_created_event
        if (
            command_time_ms != issued_time_ms
            or self.parent_run_id == self.child_run_id
            or expected.run_id.root != self.parent_run_id
            or snapshot.run_id.root != self.parent_run_id
            or snapshot.sequence_no > expected.sequence_no
            or snapshot.sequence_no == expected.sequence_no
            and snapshot.event_hash != expected.event_hash
        ):
            raise ValueError("clarification command identity or CAS binding is invalid")
        if (
            event.run_id != self.child_run_id
            or event.parent_run_id != self.parent_run_id
            or event.continuation_authority_principal_id != self.actor_principal_id
            or event.run_manifest_artifact_id != self.child_run_manifest.artifact_id
            or event.run_manifest_payload_hash != self.child_run_manifest.payload_hash
            or event.parent_clarification_result_artifact_id
            != self.parent_clarification_result.artifact_id
            or event.parent_clarification_result_payload_hash
            != self.parent_clarification_result.payload_hash
            or event.parent_terminal_result_artifact_id
            != self.parent_terminal_result.artifact_id
            or event.parent_terminal_result_payload_hash
            != self.parent_terminal_result.payload_hash
            or event.parent_terminal_snapshot_event_head_hash
            != snapshot.event_hash.root
            or event.signed_answer_bundle_artifact_id
            != self.signed_answer_bundle.artifact_id
            or event.signed_answer_bundle_payload_hash
            != self.signed_answer_bundle.payload_hash
            or event.continuation_policy_artifact_id
            != self.continuation_policy.artifact_id
            or event.continuation_policy_payload_hash
            != self.continuation_policy.payload_hash
        ):
            raise ValueError("clarification child bootstrap binding is invalid")
        return self


LifecycleCommand: TypeAlias = Annotated[
    AppendRunEventsCommand
    | CommitTerminalCommand
    | CreateReplacementRunCommand
    | CreateClarificationChildRunCommand,
    Field(discriminator="command_type"),
]


def validate_lifecycle_command(value: object) -> LifecycleCommand:
    if type(value) is not dict:
        raise EventSchemaError("lifecycle command must be an exact raw object")
    try:
        validate_and_measure_raw_json_tree(value)
        raw = cast(dict[str, object], value)
        if raw.get("command_type") in {"append_run_events", "commit_terminal"}:
            events = raw.get("events")
            if type(events) is not list:
                raise ValueError("events must be an exact raw list")
            registry = default_event_schema_registry()
            normalized = dict(raw)
            normalized["events"] = [
                registry.decode(event).model_dump(
                    mode="json",
                    round_trip=True,
                    warnings="error",
                )
                for event in events
            ]
            if raw.get("command_type") == "append_run_events":
                return AppendRunEventsCommand.model_validate(normalized, strict=True)
            return CommitTerminalCommand.model_validate(normalized, strict=True)
        if raw.get("command_type") == "create_replacement_run":
            return CreateReplacementRunCommand.model_validate(raw, strict=True)
        if raw.get("command_type") == "create_clarification_child_run":
            return CreateClarificationChildRunCommand.model_validate(raw, strict=True)
        raise ValueError("unknown lifecycle command type")
    except (TypeError, ValueError) as error:
        raise EventSchemaError("lifecycle command is invalid") from error


def validate_projection_request(value: object) -> RunProjectionRequest:
    if type(value) is not dict:
        raise EventSchemaError("projection request must be an exact raw object")
    try:
        validate_and_measure_raw_json_tree(value)
        return RunProjectionRequest.model_validate(value, strict=True)
    except (TypeError, ValueError) as error:
        raise EventSchemaError("projection request is invalid") from error


def require_expected_head(
    run_id: str,
    expected: EventHead | None,
    actual: EventHead | None,
) -> None:
    if expected is None:
        if actual is not None:
            raise EventHeadConflictError(run_id)
        return
    if (
        expected.run_id != run_id
        or actual is None
        or (
            expected.sequence_no != actual.sequence_no
            or expected.event_hash != actual.event_hash
        )
    ):
        raise EventHeadConflictError(run_id)


def append_record(
    existing: tuple[EventRecord, ...],
    event: RunEvent,
    *,
    expected_head: EventHead | None,
    allow_terminal: bool,
    budget_snapshots: Mapping[str, object] | None = None,
) -> tuple[tuple[EventRecord, ...], RunAppendStep]:
    actual_head = (
        EventHead(
            run_id=existing[-1].event.run_id,
            sequence_no=existing[-1].event.sequence_no,
            event_hash=existing[-1].event_hash,
        )
        if existing
        else None
    )
    if existing and event.sequence_no < len(existing):
        candidate = parse_event_record(_encode_typed_event_record(event))
        stored = existing[event.sequence_no]
        if stored == candidate and event.sequence_no == len(existing) - 1:
            view = project_records(existing, budget_snapshots=budget_snapshots)
            return existing, _run_append_step(stored, view, idempotent=True)
        require_expected_head(event.run_id, expected_head, actual_head)
        raise EventSequenceConflictError(event.run_id, event.sequence_no)
    require_expected_head(event.run_id, expected_head, actual_head)
    if event.sequence_no != len(existing):
        raise EventSequenceConflictError(event.run_id, event.sequence_no)
    if event.previous_event_hash != (
        actual_head.event_hash if actual_head is not None else ZERO_EVENT_HASH
    ):
        raise EventSequenceConflictError(event.run_id, event.sequence_no)
    if not allow_terminal and (
        isinstance(event, _TERMINAL_CAUSE_MODELS)
        or isinstance(event, StateTransitioned)
        and event.to_state in TERMINAL_STATES
    ):
        raise TerminalCommitRequiredError(event.run_id)
    if (
        existing
        and project_records(existing, budget_snapshots=budget_snapshots).state
        in TERMINAL_STATES
        and not (
            isinstance(event, ArtifactAccessRevoked)
            or isinstance(event, SignedApprovalEvent)
            and event.decision == "revoked"
        )
    ):
        raise RunTerminalError(event.run_id)
    record = parse_event_record(_encode_typed_event_record(event))
    candidate_records = existing + (record,)
    view = project_records(candidate_records, budget_snapshots=budget_snapshots)
    return candidate_records, _run_append_step(record, view, idempotent=False)
