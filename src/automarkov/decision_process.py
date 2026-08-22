from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from types import MappingProxyType
from typing import Annotated, Any, Literal, Self, TypeAlias, cast

from pydantic import Field, TypeAdapter, model_validator

from automarkov.canonical import (
    CanonicalJsonValue,
    CanonicalPayloadCodec,
    ConfidenceCanonicalFloat,
    FrozenSequence,
    FrozenStringMapping,
    NonNegativeCanonicalFloat,
    NonNegativeSafeCanonicalInt,
    PositiveSafeCanonicalInt,
    ProbabilityCanonicalFloat,
    SafeCanonicalInt,
    StrictCanonicalFloat,
    StrictTrue,
    canonical_json_bytes,
    parse_json_payload,
    validate_and_measure_raw_json_tree,
)
from automarkov.domain import StrictFrozenModel
from automarkov.task_contracts import (
    AgentMessageSender,
    HistoryAccessSpec,
    MessageProcessSpec,
    VariableSpec,
)

DECISION_PROCESS_SCHEMA_VERSION = "automarkov.decision-process-spec.v1"


class DeterministicRewardSpec(StrictFrozenModel):
    mode: Literal["deterministic"]
    expression: str

    @model_validator(mode="after")
    def validate_reward_expression(self) -> Self:
        if not self.expression.strip():
            raise ValueError("deterministic reward expression must be nonempty")
        return self


class StochasticRewardSpec(StrictFrozenModel):
    mode: Literal["stochastic"]
    distribution_family: str
    parameters: FrozenStringMapping[str | SafeCanonicalInt | StrictCanonicalFloat]
    support: FrozenStringMapping[CanonicalJsonValue]
    conditional_on: FrozenSequence[str]
    correlation_group: str | None
    expectation_expression: str

    @model_validator(mode="after")
    def validate_reward_law(self) -> Self:
        if not self.distribution_family.strip() or not self.support:
            raise ValueError("stochastic reward requires a family and support")
        if not self.expectation_expression.strip():
            raise ValueError("stochastic reward expectation must be nonempty")
        if self.correlation_group == "":
            raise ValueError("independent reward uses null, not an empty group")
        if len(set(self.conditional_on)) != len(self.conditional_on):
            raise ValueError("reward conditioning variables must be unique")
        return self


RewardLaw: TypeAlias = Annotated[
    DeterministicRewardSpec | StochasticRewardSpec,
    Field(discriminator="mode"),
]


class ObjectiveSpec(StrictFrozenModel):
    objective_id: str
    owner_ids: FrozenSequence[str]
    direction: Literal["maximize", "minimize", "satisfice"]
    functional: str
    aggregation: Literal[
        "discounted_sum", "average", "terminal", "lexicographic", "pareto"
    ]
    priority: NonNegativeSafeCanonicalInt
    success_threshold: StrictCanonicalFloat | None

    @model_validator(mode="after")
    def validate_objective(self) -> Self:
        if not self.objective_id.strip() or not self.functional.strip():
            raise ValueError("objective identity and functional must be nonempty")
        if not self.owner_ids or any(
            not owner_id.strip() for owner_id in self.owner_ids
        ):
            raise ValueError("objective owners must be nonempty")
        if len(set(self.owner_ids)) != len(self.owner_ids):
            raise ValueError("objective owners must be unique")
        if self.priority < 0:
            raise ValueError("objective priority must be nonnegative")
        if (self.direction == "satisfice") != (self.success_threshold is not None):
            raise ValueError(
                "success_threshold is required exactly for satisfice objectives"
            )
        return self


class ConstraintSpec(StrictFrozenModel):
    constraint_id: str
    kind: Literal["hard", "soft", "chance", "budget", "safety"]
    predicate: str
    scope: Literal["state", "action", "transition", "trajectory", "population"]
    violation_response: Literal["mask", "reject", "terminate", "penalize", "report"]
    max_violation_probability: ProbabilityCanonicalFloat | None

    @model_validator(mode="after")
    def validate_constraint(self) -> Self:
        if not self.constraint_id.strip() or not self.predicate.strip():
            raise ValueError("constraint identity and predicate must be nonempty")
        if (self.kind == "chance") != (self.max_violation_probability is not None):
            raise ValueError(
                "max_violation_probability is required exactly for chance constraints"
            )
        return self


class RiskSpec(StrictFrozenModel):
    risk_id: str
    measure: Literal["failure_probability", "var", "cvar", "worst_case", "regret"]
    outcome_expression: str
    confidence_level: ConfidenceCanonicalFloat | None
    tolerance: NonNegativeCanonicalFloat
    evaluation_horizon: PositiveSafeCanonicalInt | None

    @model_validator(mode="after")
    def validate_risk(self) -> Self:
        if not self.risk_id.strip() or not self.outcome_expression.strip():
            raise ValueError("risk identity and outcome expression must be nonempty")
        if (self.measure in {"var", "cvar"}) != (self.confidence_level is not None):
            raise ValueError("confidence_level is required exactly for var and cvar")
        if self.tolerance < 0.0:
            raise ValueError("risk tolerance must be nonnegative")
        if self.measure == "failure_probability" and self.tolerance > 1.0:
            raise ValueError("failure-probability tolerance must be in [0, 1]")
        if self.evaluation_horizon is not None and self.evaluation_horizon < 1:
            raise ValueError("risk evaluation horizon must be positive")
        return self


class JointRewardDependencySpec(StrictFrozenModel):
    correlation_group: str
    member_agent_ids: FrozenSequence[str]
    joint_distribution_family: str
    parameters: FrozenStringMapping[str | SafeCanonicalInt | StrictCanonicalFloat]
    support: FrozenStringMapping[CanonicalJsonValue]
    conditional_on: FrozenSequence[str]
    joint_kernel: str
    marginal_laws_by_agent: FrozenStringMapping[str]

    @model_validator(mode="after")
    def validate_joint_reward_definition(self) -> Self:
        members = tuple(self.member_agent_ids)
        if (
            not self.correlation_group.strip()
            or not self.joint_distribution_family.strip()
        ):
            raise ValueError("joint reward group and distribution must be nonempty")
        if len(members) < 2 or len(set(members)) != len(members):
            raise ValueError("joint reward group requires unique multiple agents")
        if not self.support or not self.joint_kernel.strip():
            raise ValueError("joint reward support and kernel must be nonempty")
        if len(set(self.conditional_on)) != len(self.conditional_on):
            raise ValueError("joint reward conditioning variables must be unique")
        if set(self.marginal_laws_by_agent) != set(members) or any(
            not law.strip() for law in self.marginal_laws_by_agent.values()
        ):
            raise ValueError("joint reward marginals must match group members")
        return self


class JointObservationKernelSpec(StrictFrozenModel):
    joint_space: FrozenSequence[VariableSpec]
    kernel: str
    conditional_on: FrozenSequence[str]
    per_agent_projection: FrozenStringMapping[FrozenSequence[str]]
    cross_agent_correlations: FrozenSequence[str]

    @model_validator(mode="after")
    def validate_joint_observation(self) -> Self:
        names = tuple(variable.name for variable in self.joint_space)
        if not names or len(set(names)) != len(names):
            raise ValueError(
                "joint-observation variable names must be nonempty and unique"
            )
        if not self.kernel.strip():
            raise ValueError("joint-observation kernel must be nonempty")
        declared = set(names)
        for projection in self.per_agent_projection.values():
            if (
                len(set(projection)) != len(projection)
                or not set(projection) <= declared
            ):
                raise ValueError(
                    "agent projections must uniquely reference joint-space names"
                )
        return self


class AECTurnSpec(StrictFrozenModel):
    active_actor_function: str
    possible_turn_owners: FrozenSequence[str]
    chance_turns: bool = Field(strict=True)
    environment_turns: bool = Field(strict=True)
    cycle_boundary: str
    state_update_timing: Literal["after_each_turn", "end_of_cycle"]
    reward_accumulation: Literal["per_turn", "until_agent_next_acts", "end_of_cycle"]
    dead_agent_action: Literal["none_only"]

    @model_validator(mode="after")
    def validate_turn_contract(self) -> Self:
        if not self.active_actor_function.strip() or not self.cycle_boundary.strip():
            raise ValueError("AEC actor function and cycle boundary must be nonempty")
        if not self.possible_turn_owners or any(
            not owner_id.strip() for owner_id in self.possible_turn_owners
        ):
            raise ValueError("AEC turn owners must be nonempty")
        if len(set(self.possible_turn_owners)) != len(self.possible_turn_owners):
            raise ValueError("AEC turn owners must be unique")
        return self


def _validate_message_recipient(
    agent_ids: set[str],
    recipient_id: str,
    history_access: HistoryAccessSpec,
    processes: tuple[MessageProcessSpec, ...],
    seen_process_ids: set[str],
) -> None:
    if bool(processes) != bool(history_access.message_lags):
        raise ValueError(
            "message lags exist exactly for recipients with message processes"
        )
    for process in processes:
        if process.recipient_id != recipient_id:
            raise ValueError("message process must be stored under its exact recipient")
        if process.message_process_id in seen_process_ids:
            raise ValueError("message process IDs must be globally unique")
        seen_process_ids.add(process.message_process_id)
        if (
            isinstance(process.sender, AgentMessageSender)
            and process.sender.agent_id not in agent_ids
        ):
            raise ValueError(
                "agent message sender must belong to the declared agent set"
            )


def _validate_joint_reward_dependencies(
    agent_ids: tuple[str, ...],
    rewards_by_agent: Mapping[str, RewardLaw],
    dependencies: tuple[JointRewardDependencySpec, ...],
) -> None:
    group_names = tuple(group.correlation_group for group in dependencies)
    if len(set(group_names)) != len(group_names):
        raise ValueError("joint reward correlation groups must be unique")
    referenced: dict[str, set[str]] = {}
    for agent_id, reward in rewards_by_agent.items():
        if isinstance(reward, StochasticRewardSpec) and reward.correlation_group:
            referenced.setdefault(reward.correlation_group, set()).add(agent_id)
    defined = {group.correlation_group: group for group in dependencies}
    if set(defined) != set(referenced):
        raise ValueError("joint reward groups must be defined exactly once")
    known_agents = set(agent_ids)
    for group_name, group in defined.items():
        members = set(group.member_agent_ids)
        if not members <= known_agents or members != referenced[group_name]:
            raise ValueError("joint reward members must match tagged reward laws")


class DecisionProcessBase(StrictFrozenModel):
    schema_version: Literal["automarkov.decision-process-spec.v1"]
    state_variables: FrozenSequence[VariableSpec]
    actions_by_agent: FrozenStringMapping[FrozenSequence[VariableSpec]]
    transition_kernel: str
    initial_distribution: str
    objectives: FrozenSequence[ObjectiveSpec]
    constraints: FrozenSequence[ConstraintSpec]
    risks: FrozenSequence[RiskSpec]
    horizon: PositiveSafeCanonicalInt | Literal["infinite"]
    discount: ProbabilityCanonicalFloat
    termination_predicates: FrozenSequence[str]
    truncation_predicates: FrozenSequence[str]

    @model_validator(mode="after")
    def validate_common_structure(self) -> Self:
        state_names = tuple(variable.name for variable in self.state_variables)
        if not state_names or len(set(state_names)) != len(state_names):
            raise ValueError("state-variable names must be nonempty and unique")
        if not self.actions_by_agent:
            raise ValueError("at least one action mapping is required")
        for agent_id, actions in self.actions_by_agent.items():
            action_names = tuple(variable.name for variable in actions)
            if (
                not agent_id.strip()
                or not actions
                or len(set(action_names)) != len(action_names)
            ):
                raise ValueError(
                    "action mappings require nonempty agents and unique action variables"
                )
        if not self.transition_kernel.strip() or not self.initial_distribution.strip():
            raise ValueError(
                "transition kernel and initial distribution must be nonempty"
            )
        if not self.objectives:
            raise ValueError("at least one objective is required")
        identity_groups = (
            tuple(objective.objective_id for objective in self.objectives),
            tuple(constraint.constraint_id for constraint in self.constraints),
            tuple(risk.risk_id for risk in self.risks),
        )
        if any(
            len(set(identities)) != len(identities) for identities in identity_groups
        ):
            raise ValueError("objective, constraint, and risk IDs must each be unique")
        terminal = tuple(self.termination_predicates)
        truncated = tuple(self.truncation_predicates)
        if any(not predicate.strip() for predicate in terminal + truncated):
            raise ValueError("termination and truncation predicates must be nonempty")
        if len(set(terminal)) != len(terminal) or len(set(truncated)) != len(truncated):
            raise ValueError("termination and truncation predicates must be unique")
        if set(terminal) & set(truncated):
            raise ValueError("termination and truncation predicates must be disjoint")
        if type(self.horizon) is int and self.horizon < 1:
            raise ValueError("finite horizon must be positive")
        if (
            self.horizon == "infinite"
            and any(
                objective.aggregation == "discounted_sum"
                for objective in self.objectives
            )
            and self.discount >= 1.0
        ):
            raise ValueError("infinite discounted-sum objectives require discount < 1")
        return self


def _validate_single_agent_structure(spec: DecisionProcessBase, agent_id: str) -> None:
    if not agent_id.strip():
        raise ValueError("single-agent decision process requires a nonempty agent ID")
    if set(spec.actions_by_agent) != {agent_id}:
        raise ValueError(
            "single-agent action keyset must equal the singleton agent set"
        )
    for objective in spec.objectives:
        if tuple(objective.owner_ids) != (agent_id,):
            raise ValueError("single-agent objective owner must equal the agent ID")


def _validate_single_agent_reward(reward: RewardLaw) -> None:
    if (
        isinstance(reward, StochasticRewardSpec)
        and reward.correlation_group is not None
    ):
        raise ValueError(
            "single-agent stochastic reward cannot declare a correlation group"
        )


class MDPSpec(DecisionProcessBase):
    kind: Literal["MDP"]
    agent_id: str
    state_is_observation: StrictTrue
    reward: RewardLaw

    @model_validator(mode="after")
    def validate_mdp_structure(self) -> Self:
        _validate_single_agent_structure(self, self.agent_id)
        _validate_single_agent_reward(self.reward)
        return self


class POMDPSpec(DecisionProcessBase):
    kind: Literal["POMDP"]
    agent_id: str
    observation_space: FrozenSequence[VariableSpec]
    observation_kernel: str
    history_access: HistoryAccessSpec
    message_processes_by_recipient: FrozenStringMapping[
        FrozenSequence[MessageProcessSpec]
    ]
    reward: RewardLaw

    @model_validator(mode="after")
    def validate_information_contract(self) -> Self:
        _validate_single_agent_structure(self, self.agent_id)
        _validate_single_agent_reward(self.reward)
        if set(self.message_processes_by_recipient) != {self.agent_id}:
            raise ValueError("POMDP message-recipient keyset must equal the agent set")
        observation_names = tuple(variable.name for variable in self.observation_space)
        if not observation_names or len(set(observation_names)) != len(
            observation_names
        ):
            raise ValueError("POMDP observation names must be nonempty and unique")
        if not self.observation_kernel.strip():
            raise ValueError("POMDP observation kernel must be nonempty")
        _validate_message_recipient(
            {self.agent_id},
            self.agent_id,
            self.history_access,
            tuple(self.message_processes_by_recipient[self.agent_id]),
            set(),
        )
        return self


def _validate_multi_agent_structure(
    spec: DecisionProcessBase,
    agent_ids: tuple[str, ...],
    required_mappings: tuple[Mapping[str, object], ...],
) -> set[str]:
    agent_set = set(agent_ids)
    if len(agent_ids) < 2 or any(not agent_id.strip() for agent_id in agent_ids):
        raise ValueError(
            "multi-agent decision process requires multiple nonempty agents"
        )
    if len(agent_set) != len(agent_ids):
        raise ValueError("multi-agent IDs must be unique")
    if set(spec.actions_by_agent) != agent_set:
        raise ValueError("multi-agent action keyset must equal the agent set")
    if any(set(mapping) != agent_set for mapping in required_mappings):
        raise ValueError("every required per-agent keyset must equal the agent set")
    covered_owners: set[str] = set()
    for objective in spec.objectives:
        owners = set(objective.owner_ids)
        if not owners <= agent_set:
            raise ValueError("objective owners must belong to the agent set")
        covered_owners.update(owners)
    if covered_owners != agent_set:
        raise ValueError("objective owners must collectively cover every agent")
    return agent_set


def _validate_action_timing(
    agent_set: set[str],
    action_timing: Literal["simultaneous", "aec"],
    aec_turn: AECTurnSpec | None,
) -> None:
    if action_timing == "simultaneous":
        if aec_turn is not None:
            raise ValueError("simultaneous action timing forbids an AEC turn spec")
        return
    if aec_turn is None:
        raise ValueError("AEC action timing requires an AEC turn spec")
    if set(aec_turn.possible_turn_owners) != agent_set:
        raise ValueError("AEC turn owners must equal the agent set")


class MGSpec(DecisionProcessBase):
    kind: Literal["MG"]
    agent_ids: FrozenSequence[str]
    full_state_access_by_agent: FrozenStringMapping[FrozenSequence[str]]
    joint_action_kernel: str
    rewards_by_agent: FrozenStringMapping[RewardLaw]
    joint_reward_dependencies: FrozenSequence[JointRewardDependencySpec]
    game_form: Literal["cooperative", "zero_sum", "general_sum"]
    solution_concept: str
    action_timing: Literal["simultaneous", "aec"]
    aec_turn: AECTurnSpec | None

    @model_validator(mode="after")
    def validate_mg_structure(self) -> Self:
        agent_ids = tuple(self.agent_ids)
        agent_set = _validate_multi_agent_structure(
            self,
            agent_ids,
            (self.full_state_access_by_agent, self.rewards_by_agent),
        )
        state_names = {variable.name for variable in self.state_variables}
        for projection in self.full_state_access_by_agent.values():
            if (
                len(set(projection)) != len(projection)
                or set(projection) != state_names
            ):
                raise ValueError(
                    "MG actors must receive each full-state variable exactly once"
                )
        if not self.joint_action_kernel.strip() or not self.solution_concept.strip():
            raise ValueError(
                "MG joint-action kernel and solution concept must be nonempty"
            )
        _validate_action_timing(agent_set, self.action_timing, self.aec_turn)
        _validate_joint_reward_dependencies(
            agent_ids,
            self.rewards_by_agent,
            tuple(self.joint_reward_dependencies),
        )
        return self


class StateTrainingFieldRef(StrictFrozenModel):
    field_kind: Literal["state"]
    variable_name: str


class ObservationTrainingFieldRef(StrictFrozenModel):
    field_kind: Literal["observation"]
    agent_id: str
    variable_name: str


class ActionHistoryTrainingFieldRef(StrictFrozenModel):
    field_kind: Literal["action_history"]
    agent_id: str
    variable_name: str


class RewardHistoryTrainingFieldRef(StrictFrozenModel):
    field_kind: Literal["reward_history"]
    agent_id: str


class MessageHistoryTrainingFieldRef(StrictFrozenModel):
    field_kind: Literal["message_history"]
    agent_id: str
    message_process_id: str


CentralizedTrainingFieldRef: TypeAlias = Annotated[
    StateTrainingFieldRef
    | ObservationTrainingFieldRef
    | ActionHistoryTrainingFieldRef
    | RewardHistoryTrainingFieldRef
    | MessageHistoryTrainingFieldRef,
    Field(discriminator="field_kind"),
]


class POSGSpec(DecisionProcessBase):
    kind: Literal["POSG"]
    agent_ids: FrozenSequence[str]
    joint_observation: JointObservationKernelSpec
    history_access_by_agent: FrozenStringMapping[HistoryAccessSpec]
    message_processes_by_recipient: FrozenStringMapping[
        FrozenSequence[MessageProcessSpec]
    ]
    joint_action_kernel: str
    rewards_by_agent: FrozenStringMapping[RewardLaw]
    joint_reward_dependencies: FrozenSequence[JointRewardDependencySpec]
    game_form: Literal["cooperative", "zero_sum", "general_sum"]
    solution_concept: str
    action_timing: Literal["simultaneous", "aec"]
    aec_turn: AECTurnSpec | None
    centralized_training_fields: FrozenSequence[CentralizedTrainingFieldRef]

    @model_validator(mode="after")
    def validate_posg_contract(self) -> Self:
        agent_ids = tuple(self.agent_ids)
        agent_set = _validate_multi_agent_structure(
            self,
            agent_ids,
            (
                self.rewards_by_agent,
                self.history_access_by_agent,
                self.message_processes_by_recipient,
                self.joint_observation.per_agent_projection,
            ),
        )
        if not self.joint_action_kernel.strip() or not self.solution_concept.strip():
            raise ValueError(
                "POSG joint-action kernel and solution concept must be nonempty"
            )
        if any(
            not projection
            for projection in self.joint_observation.per_agent_projection.values()
        ):
            raise ValueError(
                "each POSG actor requires a nonempty observation projection"
            )
        _validate_action_timing(agent_set, self.action_timing, self.aec_turn)
        seen_process_ids: set[str] = set()
        for recipient_id in agent_ids:
            _validate_message_recipient(
                agent_set,
                recipient_id,
                self.history_access_by_agent[recipient_id],
                tuple(self.message_processes_by_recipient[recipient_id]),
                seen_process_ids,
            )
        actor_fields, valid_fields = self._training_field_sets(agent_ids)
        centralized_fields = {
            _centralized_field_key(field) for field in self.centralized_training_fields
        }
        if len(centralized_fields) != len(self.centralized_training_fields):
            raise ValueError("centralized-training fields must be unique")
        if not centralized_fields <= valid_fields:
            raise ValueError(
                "centralized-training fields must reference declared inputs"
            )
        if actor_fields & centralized_fields:
            raise ValueError("centralized-only fields must not overlap actor inputs")
        _validate_joint_reward_dependencies(
            agent_ids,
            self.rewards_by_agent,
            tuple(self.joint_reward_dependencies),
        )
        return self

    def _training_field_sets(
        self, agent_ids: tuple[str, ...]
    ) -> tuple[set[tuple[str, ...]], set[tuple[str, ...]]]:
        state_names = {variable.name for variable in self.state_variables}
        actor_fields: set[tuple[str, ...]] = set()
        valid_fields: set[tuple[str, ...]] = {
            ("state", variable_name) for variable_name in state_names
        }
        message_processes = {
            (recipient_id, process.message_process_id)
            for recipient_id, processes in self.message_processes_by_recipient.items()
            for process in processes
        }
        for agent_id in agent_ids:
            history = self.history_access_by_agent[agent_id]
            observation_names = set(
                self.joint_observation.per_agent_projection[agent_id]
            )
            action_names = {
                variable.name for variable in self.actions_by_agent[agent_id]
            }
            actor_fields.update(
                ("observation", agent_id, name) for name in observation_names
            )
            valid_fields.update(
                ("observation", agent_id, name) for name in observation_names
            )
            valid_fields.update(
                ("action_history", agent_id, name) for name in action_names
            )
            if history.action_lags:
                actor_fields.update(
                    ("action_history", agent_id, name) for name in action_names
                )
            valid_fields.add(("reward_history", agent_id))
            if history.reward_lags:
                actor_fields.add(("reward_history", agent_id))
            for recipient_id, process_id in message_processes:
                if recipient_id == agent_id:
                    field_key = ("message_history", recipient_id, process_id)
                    valid_fields.add(field_key)
                    if history.message_lags:
                        actor_fields.add(field_key)
        return actor_fields, valid_fields


def _centralized_field_key(
    field: CentralizedTrainingFieldRef,
) -> tuple[str, ...]:
    if isinstance(field, StateTrainingFieldRef):
        return field.field_kind, field.variable_name
    if isinstance(field, RewardHistoryTrainingFieldRef):
        return field.field_kind, field.agent_id
    if isinstance(field, MessageHistoryTrainingFieldRef):
        return field.field_kind, field.agent_id, field.message_process_id
    return field.field_kind, field.agent_id, field.variable_name


DecisionProcessSpec: TypeAlias = Annotated[
    MDPSpec | POMDPSpec | MGSpec | POSGSpec,
    Field(discriminator="kind"),
]
DecisionProcessValue: TypeAlias = MDPSpec | POMDPSpec | MGSpec | POSGSpec

decision_process_adapter: TypeAdapter[DecisionProcessValue] = TypeAdapter(
    DecisionProcessSpec
)
decision_process_codec: CanonicalPayloadCodec[DecisionProcessValue] = (
    CanonicalPayloadCodec(decision_process_adapter)
)
_DECISION_PROCESS_SCHEMA_BYTES = canonical_json_bytes(
    decision_process_adapter.json_schema()
)


def validate_decision_process_payload(value: object) -> DecisionProcessValue:
    """从 exact raw dict 构建唯一四分支 DecisionProcessSpec。"""

    if type(value) is not dict:
        raise ValueError("public DecisionProcess ingress requires a raw JSON object")
    validate_and_measure_raw_json_tree(value)
    return decision_process_adapter.validate_python(value, strict=True)


def validate_decision_process_json(raw: bytes) -> DecisionProcessValue:
    """从 bounded duplicate-aware JSON bytes 构建 DecisionProcessSpec。"""

    if type(raw) is not bytes:
        raise ValueError("DecisionProcess JSON ingress requires exact bytes")
    return validate_decision_process_payload(parse_json_payload(raw))


def decision_process_json_schema() -> dict[str, object]:
    """返回调用方可安全修改的完整四分支 JSON Schema 快照。"""

    schema = parse_json_payload(_DECISION_PROCESS_SCHEMA_BYTES)
    if type(schema) is not dict:
        raise AssertionError("DecisionProcess schema root must be an object")
    return cast(dict[str, object], schema)


def _cartpole_raw_fixture() -> dict[str, Any]:
    evidence = ["gymnasium-v1.2.2-cartpole"]

    def float_domain(minimum: float, maximum: float) -> dict[str, Any]:
        return {
            "kind": "scalar",
            "element_dtype": "float",
            "bounds": {
                "binding_kind": "explicit",
                "minimum": minimum,
                "maximum": maximum,
                "minimum_inclusive": True,
                "maximum_inclusive": True,
            },
        }

    def symbolic_float_domain(symbol_id: str, expression: str) -> dict[str, Any]:
        return {
            "kind": "scalar",
            "element_dtype": "float",
            "bounds": {
                "binding_kind": "symbolic",
                "symbol_id": symbol_id,
                "binding_expression": expression,
                "evidence_ids": evidence,
            },
        }

    return {
        "schema_version": DECISION_PROCESS_SCHEMA_VERSION,
        "kind": "MDP",
        "state_variables": [
            {
                "name": "cart_position",
                "domain": float_domain(-4.8, 4.8),
                "unit": "m",
                "semantic_definition": "Cart position along the one-dimensional track.",
                "evidence_ids": evidence,
            },
            {
                "name": "cart_velocity",
                "domain": symbolic_float_domain(
                    "cart_velocity_unbounded",
                    "all finite IEEE-754 values accepted by the runtime",
                ),
                "unit": "m/s",
                "semantic_definition": "Cart velocity along the track.",
                "evidence_ids": evidence,
            },
            {
                "name": "pole_angle_rad",
                "domain": float_domain(-0.41887902047863906, 0.41887902047863906),
                "unit": "rad",
                "semantic_definition": "Pole angle measured from upright.",
                "evidence_ids": evidence,
            },
            {
                "name": "pole_angular_velocity",
                "domain": symbolic_float_domain(
                    "pole_angular_velocity_unbounded",
                    "all finite IEEE-754 values accepted by the runtime",
                ),
                "unit": "rad/s",
                "semantic_definition": "Angular velocity of the pole.",
                "evidence_ids": evidence,
            },
        ],
        "actions_by_agent": {
            "agent": [
                {
                    "name": "force_direction",
                    "domain": {
                        "kind": "categorical",
                        "values": ["left", "right"],
                        "ordered": False,
                    },
                    "unit": None,
                    "semantic_definition": (
                        "Discrete(2): push the cart left or right; scalar action."
                    ),
                    "evidence_ids": evidence,
                }
            ]
        },
        "transition_kernel": (
            "Gymnasium CartPoleEnv v1.2.2 equations with tau=0.02 seconds and "
            "kinematics_integrator='euler'"
        ),
        "initial_distribution": (
            "independent uniform(-0.05, 0.05) for each of the four state variables"
        ),
        "objectives": [
            {
                "objective_id": "registry_reward_threshold",
                "owner_ids": ["agent"],
                "direction": "satisfice",
                "functional": "undiscounted episode return",
                "aggregation": "terminal",
                "priority": 0,
                "success_threshold": 475.0,
            }
        ],
        "constraints": [],
        "risks": [],
        "horizon": 500,
        "discount": 1.0,
        "termination_predicates": [
            (
                "after_state_update: cart_position < -2.4 or cart_position > 2.4 "
                "or pole_angle_rad < -0.20943951023931953 or "
                "pole_angle_rad > 0.20943951023931953"
            )
        ],
        "truncation_predicates": ["elapsed_steps >= 500"],
        "agent_id": "agent",
        "state_is_observation": True,
        "reward": {
            "mode": "deterministic",
            "expression": "1.0 on every step, including the first terminating step",
        },
    }


_CARTPOLE_FIXTURE_BYTES = json.dumps(
    _cartpole_raw_fixture(),
    ensure_ascii=False,
    allow_nan=False,
    separators=(",", ":"),
).encode("utf-8")
CARTPOLE_GYMNASIUM_PROVENANCE: Mapping[str, str] = MappingProxyType(
    {
        "environment_id": "CartPole-v1",
        "gymnasium_version": "1.2.2",
        "upstream_commit": "a923da5d4415a1aa5195d99341069da5e16deed7",
        "wheel_sha256": (
            "f04ec362b1fdf73a8b327db5ef89384a3f2ba411e05d3521513414fbbb2199c8"
        ),
        "fixture_sha256": sha256(_CARTPOLE_FIXTURE_BYTES).hexdigest(),
    }
)


def load_official_gymnasium_spec(
    environment_id: Literal["CartPole-v1"] = "CartPole-v1",
    *,
    gymnasium_version: Literal["1.2.2"] = "1.2.2",
) -> MDPSpec:
    """加载唯一 allowlisted、已审计的 Gymnasium CartPole fixture。"""

    if environment_id != "CartPole-v1" or gymnasium_version != "1.2.2":
        raise ValueError("unsupported Gymnasium decision-process fixture")
    return cast(MDPSpec, validate_decision_process_json(_CARTPOLE_FIXTURE_BYTES))
