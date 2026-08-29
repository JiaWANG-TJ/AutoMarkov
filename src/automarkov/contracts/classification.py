from __future__ import annotations

from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, field_validator, model_validator

from automarkov.contracts.evidence import (
    EvidenceLedgerBinding,
    EvidenceOmissionBinding,
)
from automarkov.domain.canonical import FrozenSequence, SafeCanonicalInt, StrictTrue
from automarkov.domain.models import StrictFrozenModel, validate_strict_frozen_payload
from automarkov.lifecycle import ArtifactReference

NonEmptyText = Annotated[str, Field(strict=True, min_length=1, max_length=8_192)]
PositiveSafeInt = Annotated[SafeCanonicalInt, Field(gt=0)]


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


def _require_unique_references(
    values: tuple[ArtifactReference, ...],
    *,
    label: str,
    required: bool = True,
) -> tuple[ArtifactReference, ...]:
    identities = tuple(item.artifact_id for item in values)
    if required and not identities:
        raise ValueError(f"{label} must be nonempty")
    if len(set(identities)) != len(identities):
        raise ValueError(f"{label} must be unique")
    return values


EvidenceBinding: TypeAlias = Annotated[
    EvidenceLedgerBinding | EvidenceOmissionBinding,
    Field(discriminator="binding_kind"),
]


class ClassificationResult(StrictFrozenModel):
    schema_version: Literal["automarkov.classification-result.v1"]
    result_kind: Literal["classification"]
    source_task_ref: ArtifactReference
    evidence_binding: EvidenceBinding
    classification: Literal[
        "IN_SCOPE_MDP",
        "IN_SCOPE_POMDP",
        "IN_SCOPE_MG",
        "IN_SCOPE_POSG",
        "REDUCIBLE",
        "OOD",
    ]
    rationale: FrozenSequence[NonEmptyText]

    @field_validator("rationale")
    @classmethod
    def require_rationale(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_nonblank_unique(value, label="classification rationale")


class ReductionAssumption(StrictFrozenModel):
    assumption_id: Annotated[
        str,
        Field(
            strict=True,
            min_length=1,
            max_length=160,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        ),
    ]
    kind: Literal[
        "finite_state",
        "discretization",
        "rewardization",
        "horizon",
        "chance",
        "information_structure",
        "other",
    ]
    statement: NonEmptyText
    semantic_loss: NonEmptyText
    evidence_ids: FrozenSequence[NonEmptyText]

    @model_validator(mode="after")
    def require_explicit_semantic_loss(self) -> Self:
        if not self.statement.strip() or not self.semantic_loss.strip():
            raise ValueError(
                "reduction assumptions require statement and semantic loss"
            )
        _require_nonblank_unique(
            tuple(self.evidence_ids),
            label="reduction evidence IDs",
            required=False,
        )
        return self


class ReductionProposal(StrictFrozenModel):
    schema_version: Literal["automarkov.reduction-proposal.v1"]
    proposal_kind: Literal["decision_process_reduction"]
    source_task_ref: ArtifactReference
    classification_ref: ArtifactReference
    target_kind: Literal["MDP", "POMDP", "MG", "POSG"]
    assumptions: FrozenSequence[ReductionAssumption]
    preserved_properties: FrozenSequence[NonEmptyText]
    lost_properties: FrozenSequence[NonEmptyText]
    supersedes_proposal_ref: ArtifactReference | None
    trigger_classification_ref: ArtifactReference | None
    approval_required: StrictTrue

    @model_validator(mode="after")
    def require_closed_reduction_lineage(self) -> Self:
        if not self.assumptions:
            raise ValueError("reduction proposal requires at least one assumption")
        assumption_ids = tuple(item.assumption_id for item in self.assumptions)
        if len(set(assumption_ids)) != len(assumption_ids):
            raise ValueError("reduction assumption IDs must be unique")
        _require_nonblank_unique(
            tuple(self.preserved_properties),
            label="preserved properties",
            required=False,
        )
        _require_nonblank_unique(
            tuple(self.lost_properties),
            label="lost properties",
            required=False,
        )
        if (self.supersedes_proposal_ref is None) != (
            self.trigger_classification_ref is None
        ):
            raise ValueError(
                "reduction revisions require both superseded and trigger references"
            )
        return self


class GenericReferralRoute(StrictFrozenModel):
    route_kind: Literal["GENERIC_REFERRAL"]
    capability: Literal["referral_only"]
    recommended_backend: Literal[
        "causal_inference",
        "continuous_time_control",
        "mathematical_programming",
        "custom",
    ]
    unsupported_features: FrozenSequence[NonEmptyText]
    license_and_asset_requirements: FrozenSequence[NonEmptyText]
    recipient_acceptance_checks: FrozenSequence[NonEmptyText]

    @model_validator(mode="after")
    def require_referral_acceptance_contract(self) -> Self:
        _require_nonblank_unique(
            tuple(self.unsupported_features),
            label="unsupported features",
        )
        _require_nonblank_unique(
            tuple(self.license_and_asset_requirements),
            label="license and asset requirements",
        )
        _require_nonblank_unique(
            tuple(self.recipient_acceptance_checks),
            label="recipient acceptance checks",
        )
        return self


class OpenSpielRoute(StrictFrozenModel):
    route_kind: Literal["OPEN_SPIEL"]
    capability: Literal["executable"]
    upstream_provenance_ref: ArtifactReference
    runtime_profile_ref: ArtifactReference
    players: FrozenSequence[NonEmptyText]
    dynamics: Literal["sequential", "simultaneous"]
    chance_mode: Literal["deterministic", "explicit_stochastic"]
    information_model: Literal[
        "perfect_information",
        "imperfect_information",
    ]
    utility_type: Literal[
        "zero_sum",
        "constant_sum",
        "general_sum",
        "identical",
    ]
    reward_model: Literal["rewards", "terminal"]
    min_players: PositiveSafeInt
    max_players: PositiveSafeInt
    selected_game_or_adapter: NonEmptyText
    requested_algorithms: FrozenSequence[NonEmptyText]
    metric: NonEmptyText

    @model_validator(mode="after")
    def require_game_contract(self) -> Self:
        _require_nonblank_unique(tuple(self.players), label="OpenSpiel players")
        _require_nonblank_unique(
            tuple(self.requested_algorithms),
            label="OpenSpiel algorithms",
        )
        if self.min_players > self.max_players:
            raise ValueError("OpenSpiel player bounds are inverted")
        if not self.selected_game_or_adapter.strip() or not self.metric.strip():
            raise ValueError("OpenSpiel game and metric must be nonblank")
        return self


class PddlRoute(StrictFrozenModel):
    route_kind: Literal["PDDL"]
    capability: Literal["executable"]
    domain_source_ref: ArtifactReference
    problem_source_ref: ArtifactReference
    upstream_provenance_ref: ArtifactReference
    runtime_profile_ref: ArtifactReference
    requirements: FrozenSequence[NonEmptyText]
    objects_and_types: FrozenSequence[NonEmptyText]
    fluents: FrozenSequence[NonEmptyText]
    actions: FrozenSequence[NonEmptyText]
    goals: FrozenSequence[NonEmptyText]
    metrics: FrozenSequence[NonEmptyText]
    selected_compiler_kinds: FrozenSequence[NonEmptyText]
    planner_engine: NonEmptyText
    unsupported_features: FrozenSequence[NonEmptyText]

    @model_validator(mode="after")
    def require_planning_contract(self) -> Self:
        for label, values in (
            ("PDDL requirements", self.requirements),
            ("PDDL objects and types", self.objects_and_types),
            ("PDDL fluents", self.fluents),
            ("PDDL actions", self.actions),
            ("PDDL goals", self.goals),
            ("PDDL compiler kinds", self.selected_compiler_kinds),
        ):
            _require_nonblank_unique(tuple(values), label=label)
        _require_nonblank_unique(
            tuple(self.metrics),
            label="PDDL metrics",
            required=False,
        )
        _require_nonblank_unique(
            tuple(self.unsupported_features),
            label="PDDL unsupported features",
            required=False,
        )
        if not self.planner_engine.strip():
            raise ValueError("PDDL planner engine must be nonblank")
        return self


OODRoute: TypeAlias = Annotated[
    GenericReferralRoute | OpenSpielRoute | PddlRoute,
    Field(discriminator="route_kind"),
]


class OODHandoffSpec(StrictFrozenModel):
    schema_version: Literal["automarkov.ood-handoff.v1"]
    handoff_kind: Literal["ood_handoff"]
    source_task_ref: ArtifactReference
    classification_ref: ArtifactReference
    authority_refs: FrozenSequence[ArtifactReference]
    classification_reason: NonEmptyText
    traceability: FrozenSequence[NonEmptyText]
    assumptions: FrozenSequence[NonEmptyText]
    required_inputs: FrozenSequence[NonEmptyText]
    required_outputs: FrozenSequence[NonEmptyText]
    route: OODRoute

    @model_validator(mode="after")
    def require_closed_handoff(self) -> Self:
        _require_unique_references(tuple(self.authority_refs), label="OOD authorities")
        for label, values in (
            ("OOD traceability", self.traceability),
            ("OOD assumptions", self.assumptions),
            ("OOD required inputs", self.required_inputs),
            ("OOD required outputs", self.required_outputs),
        ):
            _require_nonblank_unique(tuple(values), label=label)
        if not self.classification_reason.strip():
            raise ValueError("OOD classification reason must be nonblank")
        return self


class OODRuntimeReadiness(StrictFrozenModel):
    schema_version: Literal["automarkov.ood-runtime-readiness.v1"]
    status: Literal["READY", "WAITING"]
    route_kind: Literal["GENERIC_REFERRAL", "OPEN_SPIEL", "PDDL"]
    required_profile_ref: ArtifactReference | None
    reason_code: Literal["referral_only", "profile_unavailable"]


def evaluate_ood_runtime_readiness(
    handoff: OODHandoffSpec,
) -> OODRuntimeReadiness:
    """在可信运行时解析器落地前，仅允许 referral 路由声明 READY。"""

    route = handoff.route
    if isinstance(route, GenericReferralRoute):
        return OODRuntimeReadiness(
            schema_version="automarkov.ood-runtime-readiness.v1",
            status="READY",
            route_kind=route.route_kind,
            required_profile_ref=None,
            reason_code="referral_only",
        )
    return OODRuntimeReadiness(
        schema_version="automarkov.ood-runtime-readiness.v1",
        status="WAITING",
        route_kind=route.route_kind,
        required_profile_ref=route.runtime_profile_ref,
        reason_code="profile_unavailable",
    )


def validate_classification_payload(value: object) -> ClassificationResult:
    return validate_strict_frozen_payload(ClassificationResult, value)


def validate_reduction_proposal_payload(value: object) -> ReductionProposal:
    return validate_strict_frozen_payload(ReductionProposal, value)


def validate_ood_handoff_payload(value: object) -> OODHandoffSpec:
    return validate_strict_frozen_payload(OODHandoffSpec, value)


__all__ = [
    "ClassificationResult",
    "EvidenceBinding",
    "GenericReferralRoute",
    "OODHandoffSpec",
    "OODRuntimeReadiness",
    "OpenSpielRoute",
    "PddlRoute",
    "ReductionAssumption",
    "ReductionProposal",
    "evaluate_ood_runtime_readiness",
    "validate_classification_payload",
    "validate_ood_handoff_payload",
    "validate_reduction_proposal_payload",
]
