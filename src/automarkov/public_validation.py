from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Literal, Self, TypeAlias, cast

from pydantic import AfterValidator, Field, model_validator

from automarkov.contracts.task import TaskValidationTargetSpec
from automarkov.contracts.validation import ValidationClaim, ValidationReport
from automarkov.domain.canonical import (
    CanonicalPayloadCodec,
    FrozenSequence,
    NonNegativeSafeCanonicalInt,
    PositiveSafeCanonicalInt,
    StrictFalse,
    StrictTrue,
)
from automarkov.domain.models import StrictFrozenModel
from automarkov.lifecycle import (
    ArtifactReference,
    ArtifactSuperseded,
    BudgetExhausted,
    CanonicalTimestamp,
    EventHead,
    EventId,
    EventReference,
    GateOmittedByDesign,
    PrincipalIdValue,
    RunIdValue,
    Sha256Value,
    StageGatePassed,
    StateTransitioned,
    encode_event_record,
    parse_event_bytes,
)

PublicReportKind: TypeAlias = Literal[
    "unit_validation",
    "property_test",
    "metamorphic_test",
    "differential_test",
    "trajectory_test",
    "public_dev_learning_probe",
]
PublicValidationState: TypeAlias = Literal["UNIT_VALIDATING", "SIMULATION_VALIDATING"]
RevisionState: TypeAlias = Literal[
    "ENVIRONMENT_IMPLEMENTED", "FORMAL_DRAFTED", "TEXT_DRAFTED"
]
AblationMethodId: TypeAlias = Literal[
    "automarkov",
    "automarkov_no_evidence",
    "automarkov_no_text_critic",
    "automarkov_no_formal_critic",
    "automarkov_single_agent_workflow",
    "automarkov_no_simulation_tester",
    "automarkov_no_training_feedback",
]
PublicOmittedGateId: TypeAlias = Literal[
    "PUBLIC_SIMULATION_TESTER", "PUBLIC_DEV_LEARNING_PROBE_AND_ROLLBACK"
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


def public_validation_payload_hash(value: StrictFrozenModel) -> str:
    """按 repository 的 canonical payload document 合同计算 payload hash。"""

    codec = CanonicalPayloadCodec(type(value))
    payload = value.model_dump(mode="json", round_trip=True, warnings="error")
    canonical = codec.encode(payload)
    return f"sha256:{sha256(canonical).hexdigest()}"


def _event_hash(value: StrictFrozenModel) -> str:
    record = parse_event_bytes(
        encode_event_record(
            value.model_dump(mode="json", round_trip=True, warnings="error")
        )
    )
    return record.event_hash


class PublicValidationGateLifecycleContext(StrictFrozenModel):
    schema_version: Literal["automarkov.public-validation-gate-lifecycle-context.v1"]
    context_kind: Literal["stage_gate"]
    experiment_id: NonEmptyId | None
    run_id: RunIdValue
    actor_principal_id: PrincipalIdValue
    actor_process_execution_id: NonEmptyId | None
    issued_at: CanonicalTimestamp
    expected_head: EventHead
    cause_event_id: EventId
    transition_event_id: EventId
    budget_snapshot: ArtifactReference
    gate_report: ArtifactReference
    gate_version: NonEmptyId
    gate_contract_hash: Sha256Value

    @model_validator(mode="after")
    def require_head_run(self) -> Self:
        if self.expected_head.run_id != self.run_id:
            raise ValueError("public validation lifecycle head belongs to another run")
        return self


class PublicValidationRevisionLifecycleContext(StrictFrozenModel):
    schema_version: Literal[
        "automarkov.public-validation-revision-lifecycle-context.v1"
    ]
    context_kind: Literal["revision"]
    experiment_id: NonEmptyId | None
    run_id: RunIdValue
    actor_principal_id: PrincipalIdValue
    actor_process_execution_id: NonEmptyId | None
    issued_at: CanonicalTimestamp
    expected_head: EventHead
    cause_event_id: EventId
    transition_event_id: EventId
    budget_snapshot: ArtifactReference
    new_candidate_bundle: ArtifactReference
    lineage_report: ArtifactReference

    @model_validator(mode="after")
    def require_revision_identities(self) -> Self:
        if self.expected_head.run_id != self.run_id:
            raise ValueError("public validation lifecycle head belongs to another run")
        if _reference_key(self.new_candidate_bundle) == _reference_key(
            self.lineage_report
        ):
            raise ValueError("replacement candidate and lineage report must differ")
        return self


class PublicValidationBudgetLifecycleContext(StrictFrozenModel):
    schema_version: Literal["automarkov.public-validation-budget-lifecycle-context.v1"]
    context_kind: Literal["budget_exhausted"]
    experiment_id: NonEmptyId | None
    run_id: RunIdValue
    actor_principal_id: PrincipalIdValue
    actor_process_execution_id: NonEmptyId | None
    issued_at: CanonicalTimestamp
    expected_head: EventHead
    cause_event_id: EventId
    transition_event_id: EventId
    budget_snapshot: ArtifactReference
    budget_policy: ArtifactReference
    cause_receipt: ArtifactReference

    @model_validator(mode="after")
    def require_head_run(self) -> Self:
        if self.expected_head.run_id != self.run_id:
            raise ValueError("public validation lifecycle head belongs to another run")
        return self


PublicValidationLifecycleContext: TypeAlias = Annotated[
    PublicValidationGateLifecycleContext
    | PublicValidationRevisionLifecycleContext
    | PublicValidationBudgetLifecycleContext,
    Field(discriminator="context_kind"),
]

UNIT_GATE_CHECKS: tuple[str, ...] = (
    "action_mask",
    "api",
    "bounds",
    "core_invariant",
    "dtype",
    "import",
    "minimal_runtime",
    "schema",
    "seed_reproducibility",
    "shape",
    "static",
)
SIMULATION_REPORT_KINDS: tuple[PublicReportKind, ...] = (
    "property_test",
    "metamorphic_test",
    "differential_test",
    "trajectory_test",
)
PROBE_REPORT_KIND: PublicReportKind = "public_dev_learning_probe"

_PUBLIC_OMISSION_PROJECTION: dict[str, tuple[str, ...]] = {
    "automarkov_no_simulation_tester": ("PUBLIC_SIMULATION_TESTER",),
    "automarkov_no_training_feedback": ("PUBLIC_DEV_LEARNING_PROBE_AND_ROLLBACK",),
}
_OMISSION_MISSING_KINDS: dict[str, tuple[str, ...]] = {
    "PUBLIC_SIMULATION_TESTER": (
        "PropertyTestReport",
        "MetamorphicTestReport",
        "DifferentialTestReport",
        "TrajectoryTestReport",
    ),
    "PUBLIC_DEV_LEARNING_PROBE_AND_ROLLBACK": ("PublicDevLearningProbeReport",),
}


def _reference_key(reference: ArtifactReference) -> tuple[str, str]:
    return reference.artifact_id, reference.payload_hash


def _require_unique_references(
    values: tuple[ArtifactReference, ...], *, label: str, required: bool = True
) -> tuple[ArtifactReference, ...]:
    keys = tuple(_reference_key(value) for value in values)
    if required and not keys:
        raise ValueError(f"{label} must be nonempty")
    if len(set(keys)) != len(keys):
        raise ValueError(f"{label} must be unique")
    return values


def _require_sorted_unique_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    expected = tuple(sorted(set(values), key=lambda item: item.encode("utf-8")))
    if not values or values != expected:
        raise ValueError("values must be nonempty, sorted, and unique")
    return values


CanonicalIds = Annotated[
    FrozenSequence[NonEmptyId], AfterValidator(_require_sorted_unique_ids)
]


class PublicAblationBinding(StrictFrozenModel):
    experiment_id: NonEmptyId
    run_id: RunIdValue
    cell_id: NonEmptyId
    ablation_execution_plan: ArtifactReference
    pair_binding_id: NonEmptyId
    task_card: ArtifactReference


class PublicValidationPlan(StrictFrozenModel):
    schema_version: Literal["automarkov.public-validation-plan.v1"]
    track: Literal["AUTO"]
    variant_id: Literal[
        "v1_canonical",
        "v2_paraphrased",
        "v3_reordered_longform",
        "v4_evidence_split",
    ]
    source_terminal_kind: Literal["active"]
    run_manifest: ArtifactReference
    task_contract: ArtifactReference
    decision_process_spec: ArtifactReference
    candidate_bundle: ArtifactReference
    environment_binding: ArtifactReference
    suite_adapter: ArtifactReference
    runtime_profiles: FrozenSequence[ArtifactReference]
    fixed_job_manifests: FrozenSequence[ArtifactReference]
    seed_ids: CanonicalIds
    wall_time_budget_ms: PositiveSafeCanonicalInt
    step_budget: PositiveSafeCanonicalInt
    revision_budget: NonNegativeSafeCanonicalInt
    ablation_method_id: AblationMethodId
    omitted_gate_ids: FrozenSequence[PublicOmittedGateId]
    ablation_binding: PublicAblationBinding | None

    @model_validator(mode="after")
    def require_closed_plan(self) -> Self:
        _require_unique_references(
            tuple(self.runtime_profiles), label="runtime profile references"
        )
        _require_unique_references(
            tuple(self.fixed_job_manifests), label="fixed job manifest references"
        )
        expected = _PUBLIC_OMISSION_PROJECTION.get(self.ablation_method_id, ())
        if tuple(self.omitted_gate_ids) != expected:
            raise ValueError(
                "ablation method requires its exact public gate projection"
            )
        if bool(expected) != (self.ablation_binding is not None):
            raise ValueError(
                "public gate omission requires its preregistered ablation binding"
            )
        return self


class _PublicValidationReportBase(StrictFrozenModel):
    subject_ref: ArtifactReference
    fixed_job_manifest: ArtifactReference
    validation_report: ValidationReport
    validation_claim: ValidationClaim | None
    counterexample_refs: FrozenSequence[ArtifactReference]

    def _require_common_report_contract(
        self, *, expected_level: Literal["executable", "behavioral"]
    ) -> None:
        report = self.validation_report
        claim = self.validation_claim
        _require_unique_references(
            tuple(self.counterexample_refs),
            label="counterexample references",
            required=False,
        )
        if _reference_key(report.subject_ref) != _reference_key(self.subject_ref):
            raise ValueError("typed report must bind its validation-report subject")
        if report.level != expected_level:
            raise ValueError("typed report uses the wrong validation level")
        if report.status == "passed":
            if self.counterexample_refs or claim is None:
                raise ValueError("passed reports require a claim and no counterexample")
            report_references = tuple(claim.report_refs)
            if (
                len(report_references) != 1
                or report_references[0].payload_hash
                != public_validation_payload_hash(report)
                or _reference_key(claim.subject_ref) != _reference_key(self.subject_ref)
                or claim.level != report.level
                or tuple(claim.scope) != tuple(report.scope)
                or report.uncovered_scope
            ):
                raise ValueError("validation claim does not close its typed report")
        elif claim is not None or not self.counterexample_refs:
            raise ValueError(
                "failed reports require counterexamples and cannot claim pass"
            )


class UnitValidationReport(_PublicValidationReportBase):
    schema_version: Literal["automarkov.unit-validation-report.v1"]
    report_kind: Literal["unit_validation"]
    official_api_validator: Literal[
        "gymnasium.utils.env_checker.check_env", "pettingzoo.test.api_test"
    ]
    completed_checks: FrozenSequence[
        Literal[
            "action_mask",
            "api",
            "bounds",
            "core_invariant",
            "dtype",
            "import",
            "minimal_runtime",
            "schema",
            "seed_reproducibility",
            "shape",
            "static",
        ]
    ]

    @model_validator(mode="after")
    def require_complete_unit_gate(self) -> Self:
        self._require_common_report_contract(expected_level="executable")
        if tuple(self.completed_checks) != UNIT_GATE_CHECKS:
            raise ValueError("unit gate checks must be exact and complete")
        return self


class PropertyTestReport(_PublicValidationReportBase):
    schema_version: Literal["automarkov.property-test-report.v1"]
    report_kind: Literal["property_test"]
    property_engine: Literal["hypothesis"]

    @model_validator(mode="after")
    def require_report(self) -> Self:
        self._require_common_report_contract(expected_level="behavioral")
        return self


class MetamorphicTestReport(_PublicValidationReportBase):
    schema_version: Literal["automarkov.metamorphic-test-report.v1"]
    report_kind: Literal["metamorphic_test"]

    @model_validator(mode="after")
    def require_report(self) -> Self:
        self._require_common_report_contract(expected_level="behavioral")
        return self


class DifferentialTestReport(_PublicValidationReportBase):
    schema_version: Literal["automarkov.differential-test-report.v1"]
    report_kind: Literal["differential_test"]

    @model_validator(mode="after")
    def require_report(self) -> Self:
        self._require_common_report_contract(expected_level="behavioral")
        return self


class TrajectoryTestReport(_PublicValidationReportBase):
    schema_version: Literal["automarkov.trajectory-test-report.v1"]
    report_kind: Literal["trajectory_test"]

    @model_validator(mode="after")
    def require_report(self) -> Self:
        self._require_common_report_contract(expected_level="behavioral")
        return self


class PublicDevLearningProbeReport(_PublicValidationReportBase):
    schema_version: Literal["automarkov.public-dev-learning-probe-report.v1"]
    report_kind: Literal["public_dev_learning_probe"]
    learner_backend: Literal["ray.rllib.algorithms.ppo.PPOConfig"]
    diagnostic_predicates: CanonicalIds
    uses_final_training_seed: bool = Field(strict=True)
    emits_policy_checkpoint: bool = Field(strict=True)

    @model_validator(mode="after")
    def require_isolated_probe(self) -> Self:
        self._require_common_report_contract(expected_level="behavioral")
        if self.uses_final_training_seed or self.emits_policy_checkpoint:
            raise ValueError("public learning probe isolation is mandatory")
        return self


PublicValidationReport: TypeAlias = Annotated[
    UnitValidationReport
    | PropertyTestReport
    | MetamorphicTestReport
    | DifferentialTestReport
    | TrajectoryTestReport
    | PublicDevLearningProbeReport,
    Field(discriminator="report_kind"),
]


class BoundPublicValidationReport(StrictFrozenModel):
    report_ref: ArtifactReference
    report: PublicValidationReport
    process_terminal_record: ArtifactReference
    execution_attestation: ArtifactReference

    @model_validator(mode="after")
    def require_forward_terminal_dag(self) -> Self:
        identities = {
            _reference_key(self.report_ref),
            _reference_key(self.process_terminal_record),
            _reference_key(self.execution_attestation),
        }
        if len(identities) != 3:
            raise ValueError(
                "report, process terminal, and attestation must be distinct"
            )
        return self


IndependentFailureClass: TypeAlias = Literal[
    "environment_implementation",
    "api_contract",
    "deterministic_core",
    "public_trajectory",
    "learning_algorithm",
    "runtime",
    "decision_process",
    "formal_specification",
    "observability",
    "reward",
    "task_contract",
    "semantic_assumption",
]


class IndependentlyDerivedProvenance(StrictFrozenModel):
    provenance_kind: Literal["independently_derived"]
    derivation_kind: Literal["property", "core", "probe"]
    failure_class: IndependentFailureClass


class OfficialReferenceDerivedProvenance(StrictFrozenModel):
    provenance_kind: Literal["official_reference_derived"]
    reference_value_kind: Literal[
        "expected_transition",
        "expected_reward",
        "expected_trajectory",
        "expected_state",
        "expected_value",
    ]
    authorized_roles: FrozenSequence[Literal["Developer", "Tester"]]

    @model_validator(mode="after")
    def confine_reference_payload(self) -> Self:
        if tuple(self.authorized_roles) != ("Developer", "Tester"):
            raise ValueError("official reference payload is Developer/Tester only")
        return self


CounterexampleProvenance: TypeAlias = Annotated[
    IndependentlyDerivedProvenance | OfficialReferenceDerivedProvenance,
    Field(discriminator="provenance_kind"),
]


class PublicCounterexample(StrictFrozenModel):
    schema_version: Literal["automarkov.public-counterexample.v1"]
    counterexample_kind: Literal["public_counterexample"]
    counterexample_ref: ArtifactReference
    subject_ref: ArtifactReference
    source_report_ref: ArtifactReference
    provenance: CounterexampleProvenance
    observed_payload: ArtifactReference
    expected_payload: ArtifactReference | None

    @model_validator(mode="after")
    def require_provenance_payload(self) -> Self:
        official = isinstance(self.provenance, OfficialReferenceDerivedProvenance)
        if official != (self.expected_payload is not None):
            raise ValueError(
                "only official reference counterexamples carry expected payloads"
            )
        return self


class BoundGateOmission(StrictFrozenModel):
    event_ref: EventReference
    event: GateOmittedByDesign

    @model_validator(mode="after")
    def bind_event_reference(self) -> Self:
        if (
            self.event_ref.event_id != self.event.event_id
            or self.event_ref.sequence_no != self.event.sequence_no
            or self.event_ref.event_hash != _event_hash(self.event)
        ):
            raise ValueError("omission event reference does not bind its event hash")
        return self


class PublicValidationRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.public-validation-request.v1"]
    plan: PublicValidationPlan
    from_state: PublicValidationState
    validation_target: TaskValidationTargetSpec
    prior_unit_gate: PublicValidationGateReport | None
    reports: FrozenSequence[BoundPublicValidationReport]
    omissions: FrozenSequence[BoundGateOmission]
    counterexamples: FrozenSequence[PublicCounterexample]
    prior_revision_count: NonNegativeSafeCanonicalInt
    requested_revision_count: NonNegativeSafeCanonicalInt
    feedback_boundary: Literal["public_dev", "sealed", "post_freeze"]
    candidate_frozen: bool = Field(strict=True)


class PublicValidationGateReport(StrictFrozenModel):
    schema_version: Literal["automarkov.public-validation-gate-report.v1"]
    report_kind: Literal["public_validation_gate_report"]
    plan_bindings: FrozenSequence[ArtifactReference]
    current_subject: ArtifactReference
    from_state: PublicValidationState
    next_state: Literal["SIMULATION_VALIDATING", "SEALED_E2E_VALIDATING"]
    report_kinds: FrozenSequence[PublicReportKind]
    report_refs: FrozenSequence[ArtifactReference]
    process_terminal_refs: FrozenSequence[ArtifactReference]
    execution_attestation_refs: FrozenSequence[ArtifactReference]
    omission_event_refs: FrozenSequence[EventReference]
    covered_scope: FrozenSequence[NonEmptyId]
    public_validation_level: Literal["executable", "behavioral"]
    status: Literal["passed"]

    @model_validator(mode="after")
    def require_parent_cardinality(self) -> Self:
        _require_unique_references(
            tuple(self.plan_bindings), label="gate plan bindings"
        )
        _require_unique_references(
            tuple(self.report_refs), label="gate report references"
        )
        _require_unique_references(
            tuple(self.process_terminal_refs),
            label="process terminal references",
        )
        _require_unique_references(
            tuple(self.execution_attestation_refs),
            label="execution attestation references",
        )
        if len(self.report_kinds) != len(self.report_refs):
            raise ValueError("gate report kinds and references must align")
        if not (
            len(self.report_refs)
            == len(self.process_terminal_refs)
            == len(self.execution_attestation_refs)
        ):
            raise ValueError("every report requires one terminal and one attestation")
        event_ids = tuple(item.event_id for item in self.omission_event_refs)
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("gate omission references must be unique")
        return self


class CandidateValidationFreeze(StrictFrozenModel):
    schema_version: Literal["automarkov.candidate-validation-freeze.v1"]
    freeze_kind: Literal["candidate_validation_freeze"]
    candidate_bundle: ArtifactReference
    environment_binding: ArtifactReference
    unit_gate_report: PublicValidationGateReport
    gate_report: PublicValidationGateReport
    report_refs: FrozenSequence[ArtifactReference]
    omission_event_refs: FrozenSequence[EventReference]
    public_validation_level: Literal["executable", "behavioral"]
    frozen: StrictTrue
    permits_public_repair: StrictFalse

    @model_validator(mode="after")
    def require_gate_closure(self) -> Self:
        if (
            self.unit_gate_report.status != "passed"
            or self.unit_gate_report.from_state != "UNIT_VALIDATING"
            or self.unit_gate_report.next_state != "SIMULATION_VALIDATING"
            or self.gate_report.status != "passed"
            or self.gate_report.next_state != "SEALED_E2E_VALIDATING"
            or tuple(self.report_refs)
            != (
                *self.unit_gate_report.report_refs,
                *self.gate_report.report_refs,
            )
            or tuple(self.omission_event_refs)
            != tuple(self.gate_report.omission_event_refs)
            or self.public_validation_level != self.gate_report.public_validation_level
        ):
            raise ValueError("candidate freeze requires a closed public gate")
        return self


class RevisionRoute(StrictFrozenModel):
    counterexample: PublicCounterexample
    target_state: RevisionState
    authorized_roles: FrozenSequence[
        Literal["Author", "Developer", "Formalizer", "Tester"]
    ]


class PublicValidationOutcome(StrictFrozenModel):
    schema_version: Literal["automarkov.public-validation-outcome.v1"]
    outcome_kind: Literal[
        "gate_passed", "candidate_frozen", "revision_required", "budget_exhausted"
    ]
    next_state: Literal[
        "SIMULATION_VALIDATING",
        "SEALED_E2E_VALIDATING",
        "ENVIRONMENT_IMPLEMENTED",
        "FORMAL_DRAFTED",
        "TEXT_DRAFTED",
        "BUDGET_EXHAUSTED",
    ]
    gate_report: PublicValidationGateReport | None
    candidate_freeze: CandidateValidationFreeze | None
    revision_routes: FrozenSequence[RevisionRoute]
    revision_count: NonNegativeSafeCanonicalInt

    @model_validator(mode="after")
    def require_exact_outcome_shape(self) -> Self:
        gate = self.gate_report is not None
        freeze = self.candidate_freeze is not None
        routes = bool(self.revision_routes)
        shapes = {
            "gate_passed": (True, False, False, "SIMULATION_VALIDATING"),
            "candidate_frozen": (True, True, False, "SEALED_E2E_VALIDATING"),
            "revision_required": (False, False, True, self.next_state),
            "budget_exhausted": (False, False, False, "BUDGET_EXHAUSTED"),
        }
        expected = shapes[self.outcome_kind]
        if (gate, freeze, routes, self.next_state) != expected:
            raise ValueError("public validation outcome shape is inconsistent")
        return self


PublicValidationCauseEvent: TypeAlias = Annotated[
    StageGatePassed | ArtifactSuperseded | BudgetExhausted,
    Field(discriminator="event_type"),
]


class PublicValidationLifecycleBatch(StrictFrozenModel):
    schema_version: Literal["automarkov.public-validation-lifecycle-batch.v1"]
    expected_head: EventHead
    omission_events: FrozenSequence[GateOmittedByDesign]
    cause_event: PublicValidationCauseEvent
    transition_event: StateTransitioned

    @property
    def events(
        self,
    ) -> tuple[
        GateOmittedByDesign
        | StageGatePassed
        | ArtifactSuperseded
        | BudgetExhausted
        | StateTransitioned,
        ...,
    ]:
        return (*self.omission_events, self.cause_event, self.transition_event)

    @property
    def event_payloads(self) -> tuple[dict[str, object], ...]:
        """返回可直接送入 lifecycle command ingress 的 JSON-shaped events。"""

        return tuple(
            event.model_dump(mode="json", round_trip=True, warnings="error")
            for event in self.events
        )

    @model_validator(mode="after")
    def require_closed_causal_tuple(self) -> Self:
        expected_sequence = self.expected_head.sequence_no + 1
        previous_hash = self.expected_head.event_hash
        for event in self.events:
            if (
                event.run_id != self.expected_head.run_id
                or event.sequence_no != expected_sequence
                or event.previous_event_hash != previous_hash
            ):
                raise ValueError(
                    "public validation events do not extend the exact event head"
                )
            expected_sequence += 1
            previous_hash = _event_hash(event)
        if (
            self.transition_event.trigger_event_id != self.cause_event.event_id
            or self.transition_event.trigger_event_hash != _event_hash(self.cause_event)
        ):
            raise ValueError("public validation transition does not bind its cause")
        if isinstance(self.cause_event, StageGatePassed) and not (
            self.cause_event.matches_transition(self.transition_event)
        ):
            raise ValueError(
                "public validation stage gate does not bind its transition"
            )
        return self


_FAILURE_ROUTES: dict[str, RevisionState] = {
    "environment_implementation": "ENVIRONMENT_IMPLEMENTED",
    "api_contract": "ENVIRONMENT_IMPLEMENTED",
    "deterministic_core": "ENVIRONMENT_IMPLEMENTED",
    "public_trajectory": "ENVIRONMENT_IMPLEMENTED",
    "learning_algorithm": "ENVIRONMENT_IMPLEMENTED",
    "runtime": "ENVIRONMENT_IMPLEMENTED",
    "decision_process": "FORMAL_DRAFTED",
    "formal_specification": "FORMAL_DRAFTED",
    "observability": "FORMAL_DRAFTED",
    "reward": "FORMAL_DRAFTED",
    "task_contract": "TEXT_DRAFTED",
    "semantic_assumption": "TEXT_DRAFTED",
}
_REVISION_PRIORITY: dict[RevisionState, int] = {
    "ENVIRONMENT_IMPLEMENTED": 0,
    "FORMAL_DRAFTED": 1,
    "TEXT_DRAFTED": 2,
}


class PublicValidationLadder:
    """在 public-dev 边界内判定 T15 门禁；本类不执行持久化。"""

    def evaluate(self, request: PublicValidationRequest) -> PublicValidationOutcome:
        self._verify_prior_unit_gate(request)
        expected_kinds = self._expected_report_kinds(request)
        actual_kinds = tuple(binding.report.report_kind for binding in request.reports)
        if actual_kinds != expected_kinds:
            raise ValueError(
                "public validation report kinds do not match the exact gate"
            )
        self._verify_report_bindings(request)
        self._verify_omissions(request)

        failed = tuple(
            binding
            for binding in request.reports
            if binding.report.validation_report.status == "failed"
        )
        if failed:
            return self._route_revision(request, failed)
        if request.counterexamples:
            raise ValueError("passed public gates cannot carry counterexamples")

        level: Literal["executable", "behavioral"] = (
            "executable"
            if request.from_state == "UNIT_VALIDATING"
            or "PUBLIC_SIMULATION_TESTER" in request.plan.omitted_gate_ids
            else "behavioral"
        )
        next_state: Literal["SIMULATION_VALIDATING", "SEALED_E2E_VALIDATING"] = (
            "SIMULATION_VALIDATING"
            if request.from_state == "UNIT_VALIDATING"
            else "SEALED_E2E_VALIDATING"
        )
        report_refs = tuple(
            sorted(
                (binding.report_ref for binding in request.reports),
                key=lambda item: item.artifact_id.encode("utf-8"),
            )
        )
        omission_refs = tuple(
            sorted(
                (binding.event_ref for binding in request.omissions),
                key=lambda item: (item.sequence_no, item.event_id),
            )
        )
        terminal_refs = self._unique_sorted_references(
            tuple(binding.process_terminal_record for binding in request.reports)
        )
        attestation_refs = self._unique_sorted_references(
            tuple(binding.execution_attestation for binding in request.reports)
        )
        gate = PublicValidationGateReport.model_validate(
            {
                "schema_version": "automarkov.public-validation-gate-report.v1",
                "report_kind": "public_validation_gate_report",
                "plan_bindings": self._plan_bindings(request.plan),
                "current_subject": request.plan.candidate_bundle,
                "from_state": request.from_state,
                "next_state": next_state,
                "report_kinds": actual_kinds,
                "report_refs": report_refs,
                "process_terminal_refs": terminal_refs,
                "execution_attestation_refs": attestation_refs,
                "omission_event_refs": omission_refs,
                "covered_scope": request.validation_target.required_properties,
                "public_validation_level": level,
                "status": "passed",
            },
            strict=True,
        )
        if request.from_state == "UNIT_VALIDATING":
            return PublicValidationOutcome.model_validate(
                {
                    "schema_version": "automarkov.public-validation-outcome.v1",
                    "outcome_kind": "gate_passed",
                    "next_state": next_state,
                    "gate_report": gate,
                    "candidate_freeze": None,
                    "revision_routes": [],
                    "revision_count": request.prior_revision_count,
                },
                strict=True,
            )
        freeze = CandidateValidationFreeze.model_validate(
            {
                "schema_version": "automarkov.candidate-validation-freeze.v1",
                "freeze_kind": "candidate_validation_freeze",
                "candidate_bundle": request.plan.candidate_bundle,
                "environment_binding": request.plan.environment_binding,
                "unit_gate_report": request.prior_unit_gate,
                "gate_report": gate,
                "report_refs": (
                    *cast(
                        PublicValidationGateReport, request.prior_unit_gate
                    ).report_refs,
                    *report_refs,
                ),
                "omission_event_refs": omission_refs,
                "public_validation_level": level,
                "frozen": True,
                "permits_public_repair": False,
            },
            strict=True,
        )
        return PublicValidationOutcome.model_validate(
            {
                "schema_version": "automarkov.public-validation-outcome.v1",
                "outcome_kind": "candidate_frozen",
                "next_state": next_state,
                "gate_report": gate,
                "candidate_freeze": freeze,
                "revision_routes": [],
                "revision_count": request.prior_revision_count,
            },
            strict=True,
        )

    @staticmethod
    def _expected_report_kinds(
        request: PublicValidationRequest,
    ) -> tuple[PublicReportKind, ...]:
        if request.from_state == "UNIT_VALIDATING":
            return ("unit_validation",)
        omitted = set(request.plan.omitted_gate_ids)
        simulation = (
            () if "PUBLIC_SIMULATION_TESTER" in omitted else SIMULATION_REPORT_KINDS
        )
        probe = (
            ()
            if "PUBLIC_DEV_LEARNING_PROBE_AND_ROLLBACK" in omitted
            else (PROBE_REPORT_KIND,)
        )
        return (*simulation, *probe)

    @staticmethod
    def _verify_report_bindings(request: PublicValidationRequest) -> None:
        refs = tuple(binding.report_ref for binding in request.reports)
        _require_unique_references(refs, label="bound public reports")
        subject = _reference_key(request.plan.candidate_bundle)
        required_scope = tuple(request.validation_target.required_properties)
        fixed_job_manifests = {
            _reference_key(reference) for reference in request.plan.fixed_job_manifests
        }
        for binding in request.reports:
            report = binding.report
            if _reference_key(report.subject_ref) != subject:
                raise ValueError("public report does not bind the current candidate")
            if tuple(report.validation_report.scope) != required_scope:
                raise ValueError(
                    "public report does not cover the task validation target"
                )
            if _reference_key(report.fixed_job_manifest) not in fixed_job_manifests:
                raise ValueError("public report is not bound to a fixed job manifest")

    @staticmethod
    def _verify_prior_unit_gate(request: PublicValidationRequest) -> None:
        gate = request.prior_unit_gate
        if request.from_state == "UNIT_VALIDATING":
            if gate is not None:
                raise ValueError("unit validation cannot supply a prior unit gate")
            return
        if (
            gate is None
            or gate.status != "passed"
            or gate.from_state != "UNIT_VALIDATING"
            or gate.next_state != "SIMULATION_VALIDATING"
            or _reference_key(gate.current_subject)
            != _reference_key(request.plan.candidate_bundle)
            or tuple(gate.plan_bindings)
            != PublicValidationLadder._plan_bindings(request.plan)
            or gate.public_validation_level != "executable"
            or tuple(gate.report_kinds) != ("unit_validation",)
            or gate.omission_event_refs
        ):
            raise ValueError(
                "simulation validation requires the closed prior unit gate"
            )

    @staticmethod
    def _unique_sorted_references(
        references: tuple[ArtifactReference, ...],
    ) -> tuple[ArtifactReference, ...]:
        unique = {_reference_key(reference): reference for reference in references}
        return tuple(
            sorted(unique.values(), key=lambda item: item.artifact_id.encode("utf-8"))
        )

    @staticmethod
    def _verify_omissions(request: PublicValidationRequest) -> None:
        expected = (
            ()
            if request.from_state == "UNIT_VALIDATING"
            else tuple(request.plan.omitted_gate_ids)
        )
        actual = tuple(binding.event.omitted_gate_id for binding in request.omissions)
        if actual != expected:
            raise ValueError(
                "public gate omissions do not match the exact signed projection"
            )
        ablation = request.plan.ablation_binding
        if actual and ablation is None:
            raise ValueError("public omission lacks its preregistered ablation binding")
        for binding in request.omissions:
            event = binding.event
            if (
                ablation is None
                or event.experiment_id != ablation.experiment_id
                or event.run_id != ablation.run_id
                or event.cell_id != ablation.cell_id
                or event.ablation_execution_plan_artifact_id
                != ablation.ablation_execution_plan.artifact_id
                or event.ablation_execution_plan_hash
                != ablation.ablation_execution_plan.payload_hash
                or event.pair_binding_id != ablation.pair_binding_id
                or event.task_card_artifact_id != ablation.task_card.artifact_id
                or event.ablation_method_id != request.plan.ablation_method_id
                or event.track != request.plan.track
                or event.variant_id != request.plan.variant_id
                or tuple(event.expected_missing_artifact_kinds)
                != _OMISSION_MISSING_KINDS[event.omitted_gate_id]
                or tuple(event.subject_artifact_ids)
                != (request.plan.candidate_bundle.artifact_id,)
                or event.output_artifact_ids
            ):
                raise ValueError(
                    "signed public omission does not bind the preregistered ablation binding"
                )

    @staticmethod
    def _plan_bindings(plan: PublicValidationPlan) -> tuple[ArtifactReference, ...]:
        ablation_refs: tuple[ArtifactReference, ...] = (
            ()
            if plan.ablation_binding is None
            else (
                plan.ablation_binding.ablation_execution_plan,
                plan.ablation_binding.task_card,
            )
        )
        refs = (
            plan.run_manifest,
            plan.task_contract,
            plan.decision_process_spec,
            plan.candidate_bundle,
            plan.environment_binding,
            plan.suite_adapter,
            *plan.runtime_profiles,
            *plan.fixed_job_manifests,
            *ablation_refs,
        )
        unique = {_reference_key(reference): reference for reference in refs}
        if len(unique) != len(refs):
            raise ValueError("public validation plan bindings must be distinct")
        return tuple(
            sorted(unique.values(), key=lambda item: item.artifact_id.encode("utf-8"))
        )

    @staticmethod
    def _route_revision(
        request: PublicValidationRequest,
        failed: tuple[BoundPublicValidationReport, ...],
    ) -> PublicValidationOutcome:
        if request.feedback_boundary != "public_dev" or request.candidate_frozen:
            raise ValueError("feedback boundary forbids sealed or post-freeze repair")
        if request.requested_revision_count != request.prior_revision_count + 1:
            raise ValueError("revision count must advance by exactly one")
        if request.requested_revision_count > request.plan.revision_budget:
            return PublicValidationOutcome.model_validate(
                {
                    "schema_version": "automarkov.public-validation-outcome.v1",
                    "outcome_kind": "budget_exhausted",
                    "next_state": "BUDGET_EXHAUSTED",
                    "gate_report": None,
                    "candidate_freeze": None,
                    "revision_routes": [],
                    "revision_count": request.prior_revision_count,
                },
                strict=True,
            )

        failed_refs = {_reference_key(binding.report_ref) for binding in failed}
        all_report_refs = {
            _reference_key(binding.report_ref): binding for binding in request.reports
        }
        if not request.counterexamples:
            raise ValueError("failed public reports require counterexample feedback")
        counterexample_refs = tuple(
            _reference_key(counterexample.counterexample_ref)
            for counterexample in request.counterexamples
        )
        if len(set(counterexample_refs)) != len(counterexample_refs):
            raise ValueError("public counterexample references must be unique")
        routes: list[RevisionRoute] = []
        covered_failed_refs: set[tuple[str, str]] = set()
        for counterexample in request.counterexamples:
            source = _reference_key(counterexample.source_report_ref)
            if source not in failed_refs or source not in all_report_refs:
                raise ValueError(
                    "counterexample must originate from a failed public report"
                )
            if _reference_key(counterexample.subject_ref) != _reference_key(
                request.plan.candidate_bundle
            ):
                raise ValueError("counterexample does not bind the current candidate")
            covered_failed_refs.add(source)
            provenance = counterexample.provenance
            if isinstance(provenance, OfficialReferenceDerivedProvenance):
                target: RevisionState = "ENVIRONMENT_IMPLEMENTED"
                roles: tuple[str, ...] = ("Developer", "Tester")
            else:
                target = _FAILURE_ROUTES[provenance.failure_class]
                role_contract: dict[RevisionState, tuple[str, ...]] = {
                    "ENVIRONMENT_IMPLEMENTED": ("Developer", "Tester"),
                    "FORMAL_DRAFTED": ("Formalizer",),
                    "TEXT_DRAFTED": ("Author",),
                }
                roles = role_contract[target]
            routes.append(
                RevisionRoute.model_validate(
                    {
                        "counterexample": counterexample,
                        "target_state": target,
                        "authorized_roles": roles,
                    },
                    strict=True,
                )
            )
        if covered_failed_refs != failed_refs:
            raise ValueError("every failed public report requires routed feedback")
        for binding in failed:
            expected = {
                _reference_key(counterexample.counterexample_ref)
                for counterexample in request.counterexamples
                if _reference_key(counterexample.source_report_ref)
                == _reference_key(binding.report_ref)
            }
            actual = {
                _reference_key(reference)
                for reference in binding.report.counterexample_refs
            }
            if actual != expected:
                raise ValueError(
                    "failed report counterexample references must close exactly"
                )
        next_state = max(
            (route.target_state for route in routes),
            key=lambda state: _REVISION_PRIORITY[cast(RevisionState, state)],
        )
        return PublicValidationOutcome.model_validate(
            {
                "schema_version": "automarkov.public-validation-outcome.v1",
                "outcome_kind": "revision_required",
                "next_state": next_state,
                "gate_report": None,
                "candidate_freeze": None,
                "revision_routes": tuple(routes),
                "revision_count": request.requested_revision_count,
            },
            strict=True,
        )


def materialize_public_validation_lifecycle_events(
    request: PublicValidationRequest,
    outcome: PublicValidationOutcome,
    context: PublicValidationLifecycleContext,
) -> PublicValidationLifecycleBatch:
    """把已复核判定转换为现有 reducer 的精确因果 tuple。"""

    verified_outcome = PublicValidationLadder().evaluate(request)
    if verified_outcome != outcome:
        raise ValueError("public validation outcome is not the result of this request")
    omission_events = tuple(binding.event for binding in request.omissions)
    previous_hash = context.expected_head.event_hash
    sequence_no = context.expected_head.sequence_no + 1
    for omission in omission_events:
        if (
            omission.run_id != context.run_id
            or omission.experiment_id != context.experiment_id
            or omission.sequence_no != sequence_no
            or omission.previous_event_hash != previous_hash
        ):
            raise ValueError("signed omission does not extend the lifecycle head")
        sequence_no += 1
        previous_hash = _event_hash(omission)

    common = {
        "event_id": context.cause_event_id,
        "experiment_id": context.experiment_id,
        "run_id": context.run_id,
        "actor_principal_id": context.actor_principal_id,
        "actor_process_execution_id": context.actor_process_execution_id,
        "issued_at": context.issued_at,
        "sequence_no": sequence_no,
        "previous_event_hash": previous_hash,
    }
    from_state = request.from_state
    gate_report_artifact_id: str | None = None
    gate_report_payload_hash: str | None = None
    input_artifact_ids: tuple[str, ...]
    reason_code: str
    to_state: str
    cause: PublicValidationCauseEvent
    if outcome.outcome_kind in {"gate_passed", "candidate_frozen"}:
        if not isinstance(context, PublicValidationGateLifecycleContext):
            raise ValueError("gate outcome requires a stage-gate lifecycle context")
        gate = outcome.gate_report
        if gate is None:
            raise ValueError("stage-gate outcome is missing its gate report")
        if public_validation_payload_hash(gate) != context.gate_report.payload_hash:
            raise ValueError(
                "gate report reference does not bind the evaluated payload"
            )
        reason_code = (
            "unit_validation_passed"
            if gate.from_state == "UNIT_VALIDATING"
            else "public_simulation_passed"
        )
        cause = StageGatePassed.model_validate(
            common
            | {
                "schema_version": "automarkov.stage-gate-passed.v1",
                "event_type": "StageGatePassed",
                "gate_id": (
                    "UNIT_VALIDATION"
                    if gate.from_state == "UNIT_VALIDATING"
                    else "PUBLIC_SIMULATION_TESTER"
                ),
                "gate_version": context.gate_version,
                "gate_contract_hash": context.gate_contract_hash,
                "subject_artifact_references": (gate.current_subject,),
                "gate_report": context.gate_report,
                "from_state": gate.from_state,
                "to_state": gate.next_state,
                "reason_code": reason_code,
                "result": "passed",
            },
            strict=True,
        )
        to_state = gate.next_state
        input_artifact_ids = (gate.current_subject.artifact_id,)
        gate_report_artifact_id = context.gate_report.artifact_id
        gate_report_payload_hash = context.gate_report.payload_hash
    elif outcome.outcome_kind == "revision_required":
        if not isinstance(context, PublicValidationRevisionLifecycleContext):
            raise ValueError("revision outcome requires a revision lifecycle context")
        old_candidate = request.plan.candidate_bundle
        identities = {
            _reference_key(old_candidate),
            _reference_key(context.new_candidate_bundle),
            _reference_key(context.lineage_report),
        }
        if len(identities) != 3:
            raise ValueError("revision lifecycle artifacts must be distinct")
        cause = ArtifactSuperseded.model_validate(
            common
            | {
                "schema_version": "automarkov.artifact-superseded.v1",
                "event_type": "ArtifactSuperseded",
                "old_artifact": old_candidate,
                "new_artifact": context.new_candidate_bundle,
                "lineage_report": context.lineage_report,
                "supersession_reason_code": "validation_failed",
            },
            strict=True,
        )
        to_state = outcome.next_state
        reason_code = {
            "ENVIRONMENT_IMPLEMENTED": "implementation_revision_required",
            "FORMAL_DRAFTED": "formal_revision_required",
            "TEXT_DRAFTED": "semantic_revision_required",
        }[to_state]
        input_artifact_ids = tuple(
            sorted(
                (
                    old_candidate.artifact_id,
                    context.new_candidate_bundle.artifact_id,
                    context.lineage_report.artifact_id,
                ),
                key=lambda item: item.encode("utf-8"),
            )
        )
    else:
        if not isinstance(context, PublicValidationBudgetLifecycleContext):
            raise ValueError("budget outcome requires a budget lifecycle context")
        cause = BudgetExhausted.model_validate(
            common
            | {
                "schema_version": "automarkov.budget-exhausted.v1",
                "event_type": "BudgetExhausted",
                "budget_kind": "revision",
                "budget_policy_artifact_id": context.budget_policy.artifact_id,
                "budget_policy_payload_hash": context.budget_policy.payload_hash,
                "budget_snapshot_artifact_id": context.budget_snapshot.artifact_id,
                "budget_snapshot_payload_hash": context.budget_snapshot.payload_hash,
                "canonical_unit": "revisions",
                "limit": request.plan.revision_budget,
                "consumed": request.plan.revision_budget,
                "reserved": 0,
                "cause_receipt_artifact_id": context.cause_receipt.artifact_id,
                "cause_receipt_payload_hash": context.cause_receipt.payload_hash,
                "phase": "validation",
                "reason_code": "budget_exhausted",
                "exhausted_at": context.issued_at,
            },
            strict=True,
        )
        to_state = "BUDGET_EXHAUSTED"
        reason_code = "budget_exhausted"
        input_artifact_ids = tuple(
            sorted(
                {
                    request.plan.candidate_bundle.artifact_id,
                    context.budget_policy.artifact_id,
                    context.budget_snapshot.artifact_id,
                    context.cause_receipt.artifact_id,
                },
                key=lambda item: item.encode("utf-8"),
            )
        )

    cause_hash = _event_hash(cause)
    transition = StateTransitioned.model_validate(
        {
            "schema_version": "automarkov.state-transitioned.v1",
            "event_type": "StateTransitioned",
            "event_id": context.transition_event_id,
            "experiment_id": context.experiment_id,
            "run_id": context.run_id,
            "actor_principal_id": context.actor_principal_id,
            "actor_process_execution_id": context.actor_process_execution_id,
            "issued_at": context.issued_at,
            "sequence_no": sequence_no + 1,
            "previous_event_hash": cause_hash,
            "from_state": from_state,
            "to_state": to_state,
            "trigger_event_id": cause.event_id,
            "trigger_event_hash": cause_hash,
            "input_artifact_ids": input_artifact_ids,
            "gate_report_artifact_id": gate_report_artifact_id,
            "gate_report_payload_hash": gate_report_payload_hash,
            "budget_snapshot_artifact_id": context.budget_snapshot.artifact_id,
            "budget_snapshot_payload_hash": context.budget_snapshot.payload_hash,
            "reason_code": reason_code,
        },
        strict=True,
    )
    return PublicValidationLifecycleBatch.model_validate(
        {
            "schema_version": "automarkov.public-validation-lifecycle-batch.v1",
            "expected_head": context.expected_head.model_dump(mode="json"),
            "omission_events": [
                event.model_dump(mode="json") for event in omission_events
            ],
            "cause_event": cause.model_dump(mode="json"),
            "transition_event": transition.model_dump(mode="json"),
        },
        strict=True,
    )


__all__ = [
    "PROBE_REPORT_KIND",
    "SIMULATION_REPORT_KINDS",
    "UNIT_GATE_CHECKS",
    "BoundGateOmission",
    "BoundPublicValidationReport",
    "CandidateValidationFreeze",
    "DifferentialTestReport",
    "IndependentlyDerivedProvenance",
    "MetamorphicTestReport",
    "OfficialReferenceDerivedProvenance",
    "PropertyTestReport",
    "PublicCounterexample",
    "PublicDevLearningProbeReport",
    "PublicValidationBudgetLifecycleContext",
    "PublicValidationGateLifecycleContext",
    "PublicValidationGateReport",
    "PublicValidationLadder",
    "PublicValidationLifecycleBatch",
    "PublicValidationLifecycleContext",
    "PublicValidationOutcome",
    "PublicValidationPlan",
    "PublicValidationRequest",
    "PublicValidationRevisionLifecycleContext",
    "RevisionRoute",
    "TrajectoryTestReport",
    "UnitValidationReport",
    "materialize_public_validation_lifecycle_events",
    "public_validation_payload_hash",
]
