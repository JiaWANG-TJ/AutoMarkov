from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, field_validator, model_validator

from automarkov.canonical import (
    FrozenSequence,
    FrozenStringMapping,
    NonNegativeSafeCanonicalInt,
    PositiveSafeCanonicalInt,
    SafeCanonicalInt,
    StrictCanonicalFloat,
)
from automarkov.domain import StrictFrozenModel, validate_strict_frozen_payload
from automarkov.lifecycle import (
    ArtifactReference,
    CanonicalTimestamp,
    ManifestEventSigningKey,
    NonEmptyId,
    PrincipalIdValue,
    RunEventSecurityContext,
    RunIdValue,
    Sha256Value,
)


class ValidationLevel(StrEnum):
    SCHEMA = "schema"
    STRUCTURAL = "structural"
    EXECUTABLE = "executable"
    BEHAVIORAL = "behavioral"
    ORACLE_EQUIVALENT = "oracle_equivalent"
    FORMALLY_VERIFIED = "formally_verified"


WireValidationLevel: TypeAlias = Literal[
    "schema",
    "structural",
    "executable",
    "behavioral",
    "oracle_equivalent",
    "formally_verified",
]


class ExplicitNumericBounds(StrictFrozenModel):
    binding_kind: Literal["explicit"]
    minimum: SafeCanonicalInt | StrictCanonicalFloat
    maximum: SafeCanonicalInt | StrictCanonicalFloat
    minimum_inclusive: bool = Field(strict=True)
    maximum_inclusive: bool = Field(strict=True)

    @model_validator(mode="after")
    def require_nonempty_interval(self) -> Self:
        if (
            self.minimum > self.maximum
            or self.minimum == self.maximum
            and not (self.minimum_inclusive and self.maximum_inclusive)
        ):
            raise ValueError("numeric bounds form an empty interval")
        return self


class SymbolicNumericBounds(StrictFrozenModel):
    binding_kind: Literal["symbolic"]
    symbol_id: str
    binding_expression: str
    evidence_ids: FrozenSequence[str]

    @model_validator(mode="after")
    def require_binding(self) -> Self:
        if not self.symbol_id.strip() or not self.binding_expression.strip():
            raise ValueError("symbolic bounds require identity and expression")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("symbolic-bound evidence IDs must be unique")
        return self


NumericBounds: TypeAlias = Annotated[
    ExplicitNumericBounds | SymbolicNumericBounds,
    Field(discriminator="binding_kind"),
]


class FixedDimension(StrictFrozenModel):
    dimension_kind: Literal["fixed"]
    size: PositiveSafeCanonicalInt

    @model_validator(mode="after")
    def require_positive_size(self) -> Self:
        if self.size < 1:
            raise ValueError("fixed dimension must be positive")
        return self


class SymbolicDimension(StrictFrozenModel):
    dimension_kind: Literal["symbolic"]
    symbol_id: str
    binding_expression: str
    evidence_ids: FrozenSequence[str]

    @model_validator(mode="after")
    def require_binding(self) -> Self:
        if not self.symbol_id.strip() or not self.binding_expression.strip():
            raise ValueError("symbolic dimension requires identity and expression")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("symbolic-dimension evidence IDs must be unique")
        return self


ShapeDimension: TypeAlias = Annotated[
    FixedDimension | SymbolicDimension,
    Field(discriminator="dimension_kind"),
]


def _validate_element_domain(
    element_dtype: Literal["bool", "int", "float"], bounds: NumericBounds | None
) -> None:
    if element_dtype == "bool":
        if bounds is not None:
            raise ValueError("bool elements cannot declare numeric bounds")
        return
    if bounds is None:
        raise ValueError("numeric elements require bounds")
    if isinstance(bounds, ExplicitNumericBounds):
        expected = int if element_dtype == "int" else float
        if type(bounds.minimum) is not expected or type(bounds.maximum) is not expected:
            raise ValueError("explicit bounds must match element dtype")


class ScalarDomain(StrictFrozenModel):
    kind: Literal["scalar"]
    element_dtype: Literal["int", "float"]
    bounds: NumericBounds

    @model_validator(mode="after")
    def validate_domain(self) -> Self:
        _validate_element_domain(self.element_dtype, self.bounds)
        return self


class VectorDomain(StrictFrozenModel):
    kind: Literal["vector"]
    element_dtype: Literal["int", "float"]
    shape: FrozenSequence[ShapeDimension]
    bounds: NumericBounds

    @model_validator(mode="after")
    def validate_domain(self) -> Self:
        if len(self.shape) != 1:
            raise ValueError("vector domain requires one dimension")
        _validate_element_domain(self.element_dtype, self.bounds)
        return self


class TensorDomain(StrictFrozenModel):
    kind: Literal["tensor"]
    element_dtype: Literal["bool", "int", "float"]
    shape: FrozenSequence[ShapeDimension]
    bounds: NumericBounds | None

    @model_validator(mode="after")
    def validate_domain(self) -> Self:
        if len(self.shape) < 2:
            raise ValueError("tensor domain requires rank >= 2")
        _validate_element_domain(self.element_dtype, self.bounds)
        return self


class CategoricalDomain(StrictFrozenModel):
    kind: Literal["categorical"]
    values: FrozenSequence[str]
    ordered: bool = Field(strict=True)

    @model_validator(mode="after")
    def validate_values(self) -> Self:
        if not self.values or any(not value.strip() for value in self.values):
            raise ValueError("categorical values must be nonblank")
        if len(set(self.values)) != len(self.values):
            raise ValueError("categorical values must be unique")
        return self


class TextDomain(StrictFrozenModel):
    kind: Literal["text"]
    encoding: Literal["utf-8"]
    max_length: ShapeDimension


class BinaryDomain(StrictFrozenModel):
    kind: Literal["binary"]
    shape: FrozenSequence[ShapeDimension]


VariableDomain: TypeAlias = Annotated[
    ScalarDomain
    | VectorDomain
    | TensorDomain
    | CategoricalDomain
    | TextDomain
    | BinaryDomain,
    Field(discriminator="kind"),
]


class VariableSpec(StrictFrozenModel):
    name: str
    domain: VariableDomain
    unit: str | None
    semantic_definition: str
    evidence_ids: FrozenSequence[str]

    @model_validator(mode="after")
    def validate_variable(self) -> Self:
        if not self.name.strip() or not self.semantic_definition.strip():
            raise ValueError("variable name and semantics must be nonblank")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("variable evidence IDs must be unique")
        if isinstance(self.domain, (CategoricalDomain, TextDomain, BinaryDomain)) and (
            self.unit is not None
        ):
            raise ValueError("categorical, text, and binary variables have no unit")
        return self


class HistoryAccessSpec(StrictFrozenModel):
    observation_lags: FrozenSequence[NonNegativeSafeCanonicalInt]
    action_lags: FrozenSequence[NonNegativeSafeCanonicalInt]
    reward_lags: FrozenSequence[NonNegativeSafeCanonicalInt]
    message_lags: FrozenSequence[NonNegativeSafeCanonicalInt]
    recurrent_state_allowed: bool = Field(strict=True)
    boundary_reset: Literal["episode", "life", "never"]

    @model_validator(mode="after")
    def require_unique_lags(self) -> Self:
        if any(
            lag < 0
            for lags in (
                self.observation_lags,
                self.action_lags,
                self.reward_lags,
                self.message_lags,
            )
            for lag in lags
        ):
            raise ValueError("history lags must be nonnegative")
        if any(
            len(set(lags)) != len(lags)
            for lags in (
                self.observation_lags,
                self.action_lags,
                self.reward_lags,
                self.message_lags,
            )
        ):
            raise ValueError("history lags must be unique")
        return self


class AgentMessageSender(StrictFrozenModel):
    sender_kind: Literal["agent"]
    agent_id: str


class EnvironmentMessageSender(StrictFrozenModel):
    sender_kind: Literal["environment"]
    process_id: str


class ExternalMessageSender(StrictFrozenModel):
    sender_kind: Literal["external"]
    source_id: str


MessageSender: TypeAlias = Annotated[
    AgentMessageSender | EnvironmentMessageSender | ExternalMessageSender,
    Field(discriminator="sender_kind"),
]


class DeterministicMessageDelay(StrictFrozenModel):
    delay_kind: Literal["deterministic"]
    steps: NonNegativeSafeCanonicalInt

    @model_validator(mode="after")
    def require_nonnegative_steps(self) -> Self:
        if self.steps < 0:
            raise ValueError("message delay must be nonnegative")
        return self


class StochasticMessageDelay(StrictFrozenModel):
    delay_kind: Literal["stochastic"]
    distribution_family: str
    parameters: FrozenStringMapping[str | SafeCanonicalInt | StrictCanonicalFloat]
    support_steps: FrozenSequence[NonNegativeSafeCanonicalInt]

    @model_validator(mode="after")
    def validate_delay(self) -> Self:
        if not self.distribution_family.strip() or not self.support_steps:
            raise ValueError("stochastic delay requires family and support")
        if len(set(self.support_steps)) != len(self.support_steps):
            raise ValueError("stochastic delay support must be unique")
        if any(step < 0 for step in self.support_steps):
            raise ValueError("stochastic delay support must be nonnegative")
        return self


MessageDelayLaw: TypeAlias = Annotated[
    DeterministicMessageDelay | StochasticMessageDelay,
    Field(discriminator="delay_kind"),
]


class MessageProcessSpec(StrictFrozenModel):
    message_process_id: str
    sender: MessageSender
    recipient_id: str
    channel_id: str
    space: FrozenSequence[VariableSpec]
    delivery_kernel: str
    delay_law: MessageDelayLaw

    @model_validator(mode="after")
    def validate_process(self) -> Self:
        if any(
            not value.strip()
            for value in (
                self.message_process_id,
                self.recipient_id,
                self.channel_id,
                self.delivery_kernel,
            )
        ):
            raise ValueError("message identity and kernel must be nonblank")
        names = tuple(variable.name for variable in self.space)
        if not names or len(set(names)) != len(names):
            raise ValueError("message space must be nonempty with unique variables")
        sender_id = getattr(
            self.sender,
            "agent_id",
            getattr(self.sender, "process_id", getattr(self.sender, "source_id", "")),
        )
        if not sender_id.strip():
            raise ValueError("message sender identity must be nonblank")
        return self


class TaskIdentitySpec(StrictFrozenModel):
    name: str
    domain: str
    intended_use: str
    excluded_uses: FrozenSequence[str]


class DecisionMakerSpec(StrictFrozenModel):
    decision_maker_id: str
    controlled_entity_ids: FrozenSequence[str]


class SimultaneousDecisionTiming(StrictFrozenModel):
    timing: Literal["simultaneous"]
    chance_turns: bool = Field(strict=True)
    environment_turns: bool = Field(strict=True)
    cycle_boundary: str


class SequentialDecisionTiming(StrictFrozenModel):
    timing: Literal["sequential"]
    turn_order: FrozenSequence[str]
    chance_turns: bool = Field(strict=True)
    environment_turns: bool = Field(strict=True)
    cycle_boundary: str


class EventDrivenDecisionTiming(StrictFrozenModel):
    timing: Literal["event_driven"]
    event_selection_rule: str
    chance_turns: bool = Field(strict=True)
    environment_turns: bool = Field(strict=True)
    cycle_boundary: str


DecisionTimingSpec: TypeAlias = Annotated[
    SimultaneousDecisionTiming | SequentialDecisionTiming | EventDrivenDecisionTiming,
    Field(discriminator="timing"),
]


class DecisionStructureSpec(StrictFrozenModel):
    decision_makers: FrozenSequence[DecisionMakerSpec]
    external_entity_ids: FrozenSequence[str]
    coordination: Literal["centralized", "decentralized", "hybrid"]
    decision_timing: DecisionTimingSpec


class TaskObjectiveSpec(StrictFrozenModel):
    primary_objective: str
    secondary_objectives: FrozenSequence[str]
    success_criteria: FrozenSequence[str]
    tradeoffs: FrozenSequence[str]


class TaskInformationSpec(StrictFrozenModel):
    observable_variables_by_decision_maker: FrozenStringMapping[
        FrozenSequence[VariableSpec]
    ]
    latent_variables: FrozenSequence[VariableSpec]
    joint_observation_semantics: str | None
    history_access_by_decision_maker: FrozenStringMapping[HistoryAccessSpec]
    message_processes_by_recipient: FrozenStringMapping[
        FrozenSequence[MessageProcessSpec]
    ]


class TaskDynamicsSpec(StrictFrozenModel):
    exogenous_processes: FrozenSequence[str]
    stochastic_assumptions: FrozenSequence[str]
    intervention_effects: FrozenSequence[str]
    reward_randomness: FrozenSequence[str]
    time_step: str
    horizon_binding: str


class TaskConstraintsSpec(StrictFrozenModel):
    hard_constraints: FrozenSequence[str]
    soft_constraints: FrozenSequence[str]
    safety_constraints: FrozenSequence[str]
    resource_limits: FrozenSequence[str]


class TaskRisksSpec(StrictFrozenModel):
    failure_events: FrozenSequence[str]
    risk_measures: FrozenSequence[str]
    tolerances: FrozenSequence[str]
    tail_or_worst_case_requirements: FrozenSequence[str]


class TaskEpisodeSpec(StrictFrozenModel):
    reset_conditions: FrozenSequence[str]
    termination_conditions: FrozenSequence[str]
    truncation_conditions: FrozenSequence[str]


class AcceptedAssumptionSpec(StrictFrozenModel):
    assumption_id: str
    statement: str
    evidence_ids: FrozenSequence[str]

    @model_validator(mode="after")
    def validate_assumption(self) -> Self:
        if not self.assumption_id.strip() or not self.statement.strip():
            raise ValueError("accepted assumption fields must be nonblank")
        if any(not item.strip() for item in self.evidence_ids):
            raise ValueError("accepted-assumption evidence IDs must be nonblank")
        return self


class UnresolvedQuestionSpec(StrictFrozenModel):
    question_id: str
    severity: Literal["low", "medium", "high", "critical"]
    target_path: str
    question: str

    @model_validator(mode="after")
    def validate_question(self) -> Self:
        if any(
            not item.strip()
            for item in (self.question_id, self.target_path, self.question)
        ):
            raise ValueError("unresolved question fields must be nonblank")
        return self


class TaskEvidenceSpec(StrictFrozenModel):
    evidence_ids: FrozenSequence[str]
    accepted_assumptions: FrozenSequence[AcceptedAssumptionSpec]
    unresolved_questions: FrozenSequence[UnresolvedQuestionSpec]


class TaskValidationTargetSpec(StrictFrozenModel):
    required_level: WireValidationLevel
    required_properties: FrozenSequence[str]
    accepted_tolerances: FrozenSequence[str]


class TaskContract(StrictFrozenModel):
    schema_version: Literal["automarkov.task-contract.v1"]
    contract_kind: Literal["core_task"]
    task_identity: TaskIdentitySpec
    decision_structure: DecisionStructureSpec
    objective: TaskObjectiveSpec
    information: TaskInformationSpec
    dynamics: TaskDynamicsSpec
    constraints: TaskConstraintsSpec
    risks: TaskRisksSpec
    episode: TaskEpisodeSpec
    evidence_and_assumptions: TaskEvidenceSpec
    validation_target: TaskValidationTargetSpec

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        identity = self.task_identity
        if any(
            not item.strip()
            for item in (identity.name, identity.domain, identity.intended_use)
        ):
            raise ValueError("task identity fields must be nonblank")
        makers = self.decision_structure.decision_makers
        maker_ids = tuple(maker.decision_maker_id for maker in makers)
        if not maker_ids or any(not item.strip() for item in maker_ids):
            raise ValueError("at least one nonblank decision maker is required")
        if len(set(maker_ids)) != len(maker_ids):
            raise ValueError("decision-maker IDs must be unique")
        controlled = tuple(
            entity for maker in makers for entity in maker.controlled_entity_ids
        )
        if (
            any(not maker.controlled_entity_ids for maker in makers)
            or any(not item.strip() for item in controlled)
            or len(set(controlled)) != len(controlled)
        ):
            raise ValueError("controlled entities require exactly one owner")
        external = self.decision_structure.external_entity_ids
        if (
            any(not item.strip() for item in external)
            or len(set(external)) != len(external)
            or set(external) & set(controlled)
        ):
            raise ValueError("external entities must be unique and disjoint")
        timing = self.decision_structure.decision_timing
        if not timing.cycle_boundary.strip():
            raise ValueError("decision timing requires a cycle boundary")
        if isinstance(timing, SequentialDecisionTiming) and (
            len(set(timing.turn_order)) != len(timing.turn_order)
            or set(timing.turn_order) != set(maker_ids)
        ):
            raise ValueError("sequential order must cover every decision maker")
        if isinstance(timing, EventDrivenDecisionTiming) and not (
            timing.event_selection_rule.strip()
        ):
            raise ValueError("event-driven timing requires a selection rule")

        info = self.information
        expected = set(maker_ids)
        if set(info.observable_variables_by_decision_maker) != expected:
            raise ValueError("observable-variable keyset must match decision makers")
        if set(info.history_access_by_decision_maker) != expected:
            raise ValueError("history-access keyset must match decision makers")
        if set(info.message_processes_by_recipient) != expected:
            raise ValueError("message-recipient keyset must match decision makers")
        seen_messages: set[str] = set()
        for maker_id in maker_ids:
            observations = info.observable_variables_by_decision_maker[maker_id]
            names = tuple(item.name for item in observations)
            if not names or len(set(names)) != len(names):
                raise ValueError("each maker requires uniquely named observations")
            history = info.history_access_by_decision_maker[maker_id]
            processes = info.message_processes_by_recipient[maker_id]
            if bool(processes) != bool(history.message_lags):
                raise ValueError("message processes and message lags must agree")
            for process in processes:
                if process.recipient_id != maker_id:
                    raise ValueError(
                        "message process must be stored under its recipient"
                    )
                if process.message_process_id in seen_messages:
                    raise ValueError("message process IDs must be globally unique")
                seen_messages.add(process.message_process_id)
                if isinstance(process.sender, AgentMessageSender) and (
                    process.sender.agent_id not in expected
                ):
                    raise ValueError("agent message sender must be a decision maker")
        latent_names = tuple(item.name for item in info.latent_variables)
        observed_names = {
            item.name
            for values in info.observable_variables_by_decision_maker.values()
            for item in values
        }
        if len(set(latent_names)) != len(latent_names) or observed_names & set(
            latent_names
        ):
            raise ValueError("latent variables must be unique and not observable")
        if len(maker_ids) > 1 and not (
            info.joint_observation_semantics
            and info.joint_observation_semantics.strip()
        ):
            raise ValueError("multi-maker tasks require joint observation semantics")

        if (
            not self.objective.primary_objective.strip()
            or not self.objective.success_criteria
            or any(not item.strip() for item in self.objective.success_criteria)
        ):
            raise ValueError("primary objective and success criteria are required")
        if (
            not self.dynamics.time_step.strip()
            or not self.dynamics.horizon_binding.strip()
        ):
            raise ValueError("time step and horizon binding are required")
        boundaries = (
            *self.episode.termination_conditions,
            *self.episode.truncation_conditions,
        )
        if (
            not self.episode.reset_conditions
            or any(not item.strip() for item in self.episode.reset_conditions)
            or not boundaries
            or any(not item.strip() for item in boundaries)
        ):
            raise ValueError("reset and episode boundary are required")
        if not self.validation_target.required_properties or any(
            not item.strip() for item in self.validation_target.required_properties
        ):
            raise ValueError("validation target requires at least one property")
        evidence = self.evidence_and_assumptions
        if len(set(evidence.evidence_ids)) != len(evidence.evidence_ids):
            raise ValueError("contract evidence IDs must be unique")
        assumption_ids = tuple(
            item.assumption_id for item in evidence.accepted_assumptions
        )
        question_ids = tuple(item.question_id for item in evidence.unresolved_questions)
        if len(set(assumption_ids)) != len(assumption_ids):
            raise ValueError("accepted-assumption IDs must be unique")
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("unresolved-question IDs must be unique")
        return self


def validate_task_contract_for_approval(value: object) -> TaskContract:
    contract = validate_strict_frozen_payload(TaskContract, value)
    if any(
        item.severity in {"high", "critical"}
        for item in contract.evidence_and_assumptions.unresolved_questions
    ):
        raise ValueError("high or critical unresolved questions block approval")
    return contract


class TaskContractAuthoringContext(StrictFrozenModel):
    schema_version: Literal["automarkov.task-contract-authoring-context.v1"]
    revision_index: NonNegativeSafeCanonicalInt
    task_request: ArtifactReference
    author_completion_trace: ArtifactReference
    previous_task_contract: ArtifactReference | None
    previous_critic_report: ArtifactReference | None

    @model_validator(mode="after")
    def require_revision_lineage(self) -> Self:
        if self.revision_index < 0:
            raise ValueError("revision index must be nonnegative")
        has_previous = self.previous_task_contract is not None
        if has_previous != (self.previous_critic_report is not None):
            raise ValueError("previous contract and critic references must be paired")
        if (self.revision_index == 0) == has_previous:
            raise ValueError("revision zero has no previous contract lineage")
        return self


class TaskTraceabilityEntry(StrictFrozenModel):
    target_path: str
    source_kind: Literal["task_request", "evidence", "accepted_assumption", "symbolic"]
    source_ids: FrozenSequence[str]

    @model_validator(mode="after")
    def require_sources(self) -> Self:
        if not self.target_path.strip() or not self.source_ids:
            raise ValueError("traceability entries require target and source")
        expected = tuple(sorted(set(self.source_ids), key=lambda item: item.encode()))
        if self.source_ids != expected or any(
            not item.strip() for item in self.source_ids
        ):
            raise ValueError(
                "traceability source IDs must be sorted, unique, and nonblank"
            )
        return self


class TaskContractTraceabilityReport(StrictFrozenModel):
    schema_version: Literal["automarkov.task-contract-traceability-report.v1"]
    task_contract: ArtifactReference
    task_request: ArtifactReference
    entries: FrozenSequence[TaskTraceabilityEntry]
    uncovered_paths: FrozenSequence[str]
    generated_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_unique_paths(self) -> Self:
        paths = tuple(item.target_path for item in self.entries)
        if len(set(paths)) != len(paths):
            raise ValueError("traceability target paths must be unique")
        return self


class TextCriticIssue(StrictFrozenModel):
    issue_id: str
    path: str
    severity: Literal["low", "medium", "high", "critical"]
    type: Literal[
        "ambiguity",
        "missing_field",
        "contradiction",
        "unsupported_assumption",
        "inconsistent_timing",
        "incomplete_information",
        "unclear_episode_boundary",
        "traceability_gap",
    ]
    reason: str
    consequence: str
    question: str
    evidence_ids: FrozenSequence[str]
    disposition: Literal["open", "resolved", "converted_to_explicit_assumption"]
    accepted_assumption_id: str | None

    @model_validator(mode="after")
    def require_disposition(self) -> Self:
        converted = self.disposition == "converted_to_explicit_assumption"
        if converted != (self.accepted_assumption_id is not None):
            raise ValueError("converted issue requires one accepted assumption")
        if converted and self.severity != "high":
            raise ValueError("only high-severity issues may become assumptions")
        if any(
            not item.strip()
            for item in (
                self.issue_id,
                self.path,
                self.reason,
                self.consequence,
                self.question,
            )
        ):
            raise ValueError("critic issue fields must be nonblank")
        return self


class TextCriticReport(StrictFrozenModel):
    schema_version: Literal["automarkov.text-critic-report.v1"]
    report_kind: Literal["task_contract_review"]
    task_contract: ArtifactReference
    traceability_report: ArtifactReference
    critic_completion_trace: ArtifactReference
    previous_critic_report: ArtifactReference | None
    issues: FrozenSequence[TextCriticIssue]
    reviewed_at: CanonicalTimestamp

    @field_validator("issues")
    @classmethod
    def require_canonical_issues(
        cls, value: tuple[TextCriticIssue, ...]
    ) -> tuple[TextCriticIssue, ...]:
        ids = tuple(item.issue_id for item in value)
        if ids != tuple(sorted(set(ids), key=lambda item: item.encode())):
            raise ValueError("critic issues must be sorted and unique")
        return value

    def require_approval_ready(self) -> None:
        if any(
            item.disposition == "open" and item.severity in {"high", "critical"}
            for item in self.issues
        ):
            raise ValueError("open high or critical critic issues block approval")


def validate_task_contract_review_gate(
    contract: TaskContract,
    traceability: TaskContractTraceabilityReport,
    critic: TextCriticReport,
) -> None:
    """对同一 immutable contract 机械执行文本审批 gate。"""

    required_paths = task_contract_claim_paths(contract)
    entry_paths = tuple(entry.target_path for entry in traceability.entries)
    uncovered_paths = traceability.uncovered_paths
    if (
        tuple(sorted(uncovered_paths, key=lambda item: item.encode()))
        != uncovered_paths
        or set(entry_paths) & set(uncovered_paths)
        or set(entry_paths) | set(uncovered_paths) != set(required_paths)
    ):
        raise ValueError(
            "TaskContract traceability coverage does not match claim leaves"
        )
    if uncovered_paths:
        raise ValueError("TaskContract traceability contains uncovered paths")
    if critic.task_contract != traceability.task_contract:
        raise ValueError("critic and traceability reports target different contracts")
    evidence = contract.evidence_and_assumptions
    evidence_ids = set(evidence.evidence_ids)
    assumption_ids = {item.assumption_id for item in evidence.accepted_assumptions}
    for entry in traceability.entries:
        if entry.source_kind == "evidence" and not set(entry.source_ids).issubset(
            evidence_ids
        ):
            raise ValueError("traceability cites evidence outside the TaskContract")
        if entry.source_kind == "accepted_assumption" and not set(
            entry.source_ids
        ).issubset(assumption_ids):
            raise ValueError("traceability cites an unknown accepted assumption")
    critic.require_approval_ready()
    for issue in critic.issues:
        if (
            issue.disposition == "converted_to_explicit_assumption"
            and issue.accepted_assumption_id not in assumption_ids
        ):
            raise ValueError(
                "converted critic issue does not bind a current assumption"
            )


def task_contract_claim_paths(contract: TaskContract) -> tuple[str, ...]:
    """返回需要来源绑定的 deterministic canonical TaskContract leaf paths。"""

    payload = contract.model_dump(mode="json", round_trip=True, warnings="error")
    paths: list[str] = []
    pending: list[tuple[str, object]] = [("", payload)]
    while pending:
        path, value = pending.pop()
        if type(value) is dict:
            for key, item in value.items():
                if path == "" and key in {"schema_version", "contract_kind"}:
                    continue
                escaped = key.replace("~", "~0").replace("/", "~1")
                pending.append((f"{path}/{escaped}", item))
            continue
        if type(value) is list:
            pending.extend(
                (f"{path}/{index}", item) for index, item in enumerate(value)
            )
            continue
        paths.append(path)
    return tuple(sorted(paths, key=lambda item: item.encode()))


class RunCreationPolicy(StrictFrozenModel):
    schema_version: Literal["automarkov.run-creation-policy.v1"]
    policy_version: NonEmptyId
    creation_principal_id: PrincipalIdValue
    signing_key_id: NonEmptyId
    max_clock_skew_ms: NonNegativeSafeCanonicalInt

    @model_validator(mode="after")
    def require_nonnegative_skew(self) -> Self:
        if self.max_clock_skew_ms < 0:
            raise ValueError("clock skew must be nonnegative")
        return self


class TaskApprovalPolicy(StrictFrozenModel):
    schema_version: Literal["automarkov.task-approval-policy.v1"]
    policy_kind: Literal["interactive_user", "experiment_approval_policy"]
    policy_version: NonEmptyId
    approval_principal_id: PrincipalIdValue
    signing_key_id: NonEmptyId
    policy_source_hash: Sha256Value | None
    policy_image_hash: Sha256Value | None
    allowed_artifact_type: Literal["task_contract"]
    required_report_artifact_types: FrozenSequence[
        Literal["task_contract_traceability_report", "text_critic_report"]
    ]
    approved_reason_code: Literal["text_approved"]
    rejected_reason_code: Literal["text_rejected"]

    @model_validator(mode="after")
    def require_policy_branch(self) -> Self:
        expected = ("task_contract_traceability_report", "text_critic_report")
        if self.required_report_artifact_types != expected:
            raise ValueError("task approval requires the exact report set")
        hashes_present = (
            self.policy_source_hash is not None and self.policy_image_hash is not None
        )
        if (self.policy_kind == "experiment_approval_policy") != hashes_present:
            raise ValueError("experiment policy hashes must be paired and exact")
        return self


class RunManifestBootstrap(StrictFrozenModel):
    schema_version: Literal["automarkov.run-manifest-bootstrap.v1"]
    manifest_kind: Literal["bootstrap"]
    run_id: RunIdValue
    experiment_id: NonEmptyId | None
    root_ordinal: SafeCanonicalInt
    task_request: ArtifactReference
    event_security_context: RunEventSecurityContext
    created_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_bootstrap_root(self) -> Self:
        if self.root_ordinal != 0:
            raise ValueError("bootstrap manifest root ordinal must be zero")
        return self


class FixedCommitRunAuthorization(StrictFrozenModel):
    """由 root RunManifest 冻结的 fixed-commit launch 授权。"""

    schema_version: Literal["automarkov.fixed-commit-run-authorization.v1"]
    job_manifest: ArtifactReference
    repository_url: str
    source_commit: str
    profile_manifest: ArtifactReference
    profile_id: str
    image_digest: Sha256Value
    input_artifacts: FrozenSequence[ArtifactReference]
    resource_limits: ArtifactReference
    network_policy: ArtifactReference
    mount_policy: ArtifactReference
    capability_policy: ArtifactReference
    output_contract: ArtifactReference
    scanner_policy: ArtifactReference
    suite_id: NonEmptyId
    variant_id: NonEmptyId
    track_id: NonEmptyId
    method_id: NonEmptyId
    pair_id: NonEmptyId
    generation_seed: SafeCanonicalInt
    rl_seed: SafeCanonicalInt
    phase: NonEmptyId
    argv: FrozenSequence[str]
    working_directory: str
    from_phase: NonEmptyId
    to_phase: NonEmptyId
    launch_deadline: CanonicalTimestamp
    runner_key_grant: ManifestEventSigningKey


class SealedWorkerRunAuthorization(StrictFrozenModel):
    worker_kind: Literal["candidate", "gold", "comparator"]
    principal_id: PrincipalIdValue
    job_manifest: ArtifactReference
    fixed_commit_authorization: ArtifactReference


class SealedE2ESigningAuthority(StrictFrozenModel):
    """由 root manifest 冻结的 sealed E2E 签名角色。"""

    principal_kind: Literal[
        "candidate_worker",
        "comparator",
        "coordinator",
        "evaluator",
        "gold_worker",
    ]
    principal_id: PrincipalIdValue
    signing_key_id: NonEmptyId


class RunManifest(StrictFrozenModel):
    """完整运行图 manifest；bootstrap v1 保持兼容。"""

    schema_version: Literal["automarkov.run-manifest.v2"]
    manifest_kind: Literal["frozen_run"]
    run_id: RunIdValue
    experiment_id: NonEmptyId
    root_ordinal: SafeCanonicalInt
    task_request: ArtifactReference
    event_security_context: RunEventSecurityContext
    fixed_commit_authorization: ArtifactReference
    sealed_e2e_signing_authorities: FrozenSequence[SealedE2ESigningAuthority]
    sealed_worker_authorizations: FrozenSequence[SealedWorkerRunAuthorization]
    created_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_root_identity(self) -> Self:
        worker_kinds = tuple(
            authorization.worker_kind
            for authorization in self.sealed_worker_authorizations
        )
        principals = tuple(
            authorization.principal_id
            for authorization in self.sealed_worker_authorizations
        )
        jobs = tuple(
            (
                authorization.job_manifest.artifact_id,
                authorization.job_manifest.payload_hash,
            )
            for authorization in self.sealed_worker_authorizations
        )
        authorizations = tuple(
            (
                authorization.fixed_commit_authorization.artifact_id,
                authorization.fixed_commit_authorization.payload_hash,
            )
            for authorization in self.sealed_worker_authorizations
        )
        authority_kinds = tuple(
            authority.principal_kind
            for authority in self.sealed_e2e_signing_authorities
        )
        authority_principals = tuple(
            authority.principal_id for authority in self.sealed_e2e_signing_authorities
        )
        authority_keys = tuple(
            authority.signing_key_id
            for authority in self.sealed_e2e_signing_authorities
        )
        manifest_signing_keys = {
            key.signing_key_id: key for key in self.event_security_context.signing_keys
        }
        worker_principals = {
            authorization.worker_kind: authorization.principal_id
            for authorization in self.sealed_worker_authorizations
        }
        authority_principal_by_kind = {
            authority.principal_kind: authority.principal_id
            for authority in self.sealed_e2e_signing_authorities
        }
        if (
            self.root_ordinal != 0
            or authority_kinds
            != (
                "candidate_worker",
                "comparator",
                "coordinator",
                "evaluator",
                "gold_worker",
            )
            or len(authority_principals) != len(set(authority_principals))
            or len(authority_keys) != len(set(authority_keys))
            or any(
                manifest_signing_keys.get(authority.signing_key_id) is None
                or manifest_signing_keys[authority.signing_key_id].principal_id
                != authority.principal_id
                for authority in self.sealed_e2e_signing_authorities
            )
            or worker_kinds != ("candidate", "comparator", "gold")
            or worker_principals.get("candidate")
            != authority_principal_by_kind.get("candidate_worker")
            or worker_principals.get("comparator")
            != authority_principal_by_kind.get("comparator")
            or worker_principals.get("gold")
            != authority_principal_by_kind.get("gold_worker")
            or len(principals) != len(set(principals))
            or len(jobs) != len(set(jobs))
            or len(authorizations) != len(set(authorizations))
        ):
            raise ValueError("frozen root manifest ordinal must be zero")
        return self
