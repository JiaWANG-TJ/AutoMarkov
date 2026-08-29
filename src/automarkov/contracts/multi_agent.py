from __future__ import annotations

from math import isclose
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import AfterValidator, Field, model_validator

from automarkov.domain.canonical import (
    FrozenSequence,
    NonNegativeCanonicalFloat,
    NonNegativeSafeCanonicalInt,
    StrictCanonicalFloat,
    StrictTrue,
)
from automarkov.domain.models import StrictFrozenModel
from automarkov.lifecycle import ArtifactReference, Sha256Value

AgentId = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
ObservationName = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]

CITYLEARN_FORBIDDEN_FUTURE_OBSERVATION_NAMES = (
    "carbon_intensity_predicted_1",
    "electricity_pricing_predicted_1",
    "non_shiftable_load_predicted_1",
    "outdoor_dry_bulb_temperature_predicted_1",
    "solar_generation_predicted_1",
)


def _require_sorted_unique_nonempty(value: tuple[str, ...]) -> tuple[str, ...]:
    if not value or value != tuple(
        sorted(set(value), key=lambda item: item.encode("utf-8"))
    ):
        raise ValueError("values must be UTF-8 sorted, unique, and non-empty")
    return value


def _require_unique_nonempty(value: tuple[str, ...]) -> tuple[str, ...]:
    if not value or len(value) != len(set(value)):
        raise ValueError("values must be unique and non-empty")
    return value


CanonicalAgentIds = Annotated[
    FrozenSequence[AgentId], AfterValidator(_require_sorted_unique_nonempty)
]
OrderedObservationNames = Annotated[
    FrozenSequence[ObservationName], AfterValidator(_require_unique_nonempty)
]
CanonicalObservationNames = Annotated[
    FrozenSequence[ObservationName], AfterValidator(_require_sorted_unique_nonempty)
]
FloatVector = FrozenSequence[StrictCanonicalFloat]
BoolVector = FrozenSequence[bool]


class _SuiteAdapterManifestBase(StrictFrozenModel):
    environment_binding: ArtifactReference
    runtime_profile_manifest: ArtifactReference
    official_provenance: ArtifactReference
    protocol_version: Literal["automarkov.remote-env.v1"]
    space_contract_hash: Sha256Value
    adapter_source_hash: Sha256Value

    @model_validator(mode="after")
    def require_unique_direct_parents(self) -> Self:
        keys = (
            self.environment_binding.artifact_id,
            self.runtime_profile_manifest.artifact_id,
            self.official_provenance.artifact_id,
        )
        if len(set(keys)) != 3:
            raise ValueError("suite adapter direct parents must be unique")
        return self


class Mpe2InformationPolicy(StrictFrozenModel):
    policy_id: Literal["mpe2_shared_information_policy_v1"]
    actor_source: Literal["official_state", "native_local_observation"]
    critic_source: Literal["official_state"]
    fixed_actor_input_dimension: Literal[54]
    local_observation_dimension: Literal[18]


class Mpe2AdapterManifest(_SuiteAdapterManifestBase):
    schema_version: Literal["automarkov.mpe2-adapter-manifest.v1"]
    suite_id: Literal["mpe2_full_state_mg", "mpe2_native_local_posg"]
    condition: Literal["full_state", "native_local"]
    runtime_profile_id: Literal["env-mpe2"]
    package_version: Literal["1.1.0"]
    upstream_commit: Literal["7590d9d52791e321974d4fda6090fb18f34dbf49"]
    environment_id: Literal["simple_spread_v3"]
    possible_agents: CanonicalAgentIds
    local_observation_dimension: Literal[18]
    actor_input_dimension: Literal[54]
    information_policy: Mpe2InformationPolicy

    @model_validator(mode="after")
    def require_official_simple_spread_shape(self) -> Self:
        if self.possible_agents != ("agent_0", "agent_1", "agent_2"):
            raise ValueError(
                "MPE2 simple_spread_v3 requires its exact three-agent keyset"
            )
        expected_suite_id = (
            "mpe2_full_state_mg"
            if self.condition == "full_state"
            else "mpe2_native_local_posg"
        )
        if self.suite_id != expected_suite_id:
            raise ValueError("MPE2 condition and suite identity are inconsistent")
        expected_actor_source = (
            "official_state"
            if self.condition == "full_state"
            else "native_local_observation"
        )
        if self.information_policy.actor_source != expected_actor_source:
            raise ValueError("MPE2 actor information capability is inconsistent")
        return self


class Smacv2TeamGeneration(StrictFrozenModel):
    dist_type: Literal["weighted_teams"]
    unit_types: OrderedObservationNames
    exception_unit_types: OrderedObservationNames
    weights: FrozenSequence[StrictCanonicalFloat]
    observe: StrictTrue

    @model_validator(mode="after")
    def require_protoss_distribution(self) -> Self:
        if (
            self.unit_types != ("stalker", "zealot", "colossus")
            or self.exception_unit_types != ("colossus",)
            or self.weights != (0.45, 0.45, 0.1)
        ):
            raise ValueError("SMACv2 protoss_5_vs_5 weights or unit policy drifted")
        return self


class Smacv2StartPositions(StrictFrozenModel):
    dist_type: Literal["surrounded_and_reflect"]
    p: StrictCanonicalFloat
    n_enemies: Literal[5]
    map_x: Literal[32]
    map_y: Literal[32]

    @model_validator(mode="after")
    def require_frozen_position_distribution(self) -> Self:
        if self.p != 0.5:
            raise ValueError("SMACv2 start-position mixture must remain 0.5")
        return self


class Smacv2CapabilityConfig(StrictFrozenModel):
    n_units: Literal[5]
    n_enemies: Literal[5]
    team_gen: Smacv2TeamGeneration
    start_positions: Smacv2StartPositions


class Smacv2AdapterManifest(_SuiteAdapterManifestBase):
    schema_version: Literal["automarkov.smacv2-adapter-manifest.v1"]
    suite_id: Literal["smacv2_posg"]
    runtime_profile_id: Literal["env-smacv2"]
    upstream_commit: Literal["577ab5a2cff2391f8df582da5731ea9cd6adf3c6"]
    scenario_id: Literal["protoss_5_vs_5"]
    possible_agents: CanonicalAgentIds
    capability_config: Smacv2CapabilityConfig
    train_config_ids: CanonicalAgentIds
    test_config_ids: CanonicalAgentIds

    @model_validator(mode="after")
    def require_agent_count_and_split(self) -> Self:
        if self.possible_agents != tuple(f"agent_{index}" for index in range(5)):
            raise ValueError(
                "SMACv2 protoss_5_vs_5 requires five stable adapter agents"
            )
        if set(self.train_config_ids) & set(self.test_config_ids):
            raise ValueError("SMACv2 train and test configuration IDs must be disjoint")
        return self


class CityLearnAgentSchema(StrictFrozenModel):
    agent_id: AgentId
    observation_names: OrderedObservationNames
    action_names: OrderedObservationNames


class CityLearnEvaluationPeriod(StrictFrozenModel):
    start_time_step: NonNegativeSafeCanonicalInt
    end_time_step: NonNegativeSafeCanonicalInt

    @model_validator(mode="after")
    def require_nonempty_period(self) -> Self:
        if self.end_time_step <= self.start_time_step:
            raise ValueError("CityLearn evaluation period must be non-empty")
        return self


class CityLearnAdapterManifest(_SuiteAdapterManifestBase):
    schema_version: Literal["automarkov.citylearn-adapter-manifest.v1"]
    suite_id: Literal["citylearn_posg"]
    runtime_profile_id: Literal["env-citylearn"]
    package_version: Literal["2.5.0"]
    upstream_commit: Literal["29062af6d077409e1c37a3e53a6cac30fd4d02bc"]
    environment_id: Literal["CityLearnEnv"]
    challenge_schema: ArtifactReference
    challenge_schema_hash: Sha256Value
    agents: FrozenSequence[CityLearnAgentSchema]
    forbidden_future_observation_names: CanonicalObservationNames
    evaluation_period: CityLearnEvaluationPeriod
    parallel_api: StrictTrue
    aec_conversion: Literal["pettingzoo.parallel_to_aec"]

    @model_validator(mode="after")
    def require_posg_information_boundary(self) -> Self:
        agent_ids = tuple(item.agent_id for item in self.agents)
        if len(agent_ids) < 2 or agent_ids != tuple(
            sorted(set(agent_ids), key=lambda item: item.encode("utf-8"))
        ):
            raise ValueError("CityLearn agents must be sorted, unique, and multi-agent")
        forbidden = set(self.forbidden_future_observation_names)
        if (
            self.challenge_schema.payload_hash != self.challenge_schema_hash
            or self.forbidden_future_observation_names
            != CITYLEARN_FORBIDDEN_FUTURE_OBSERVATION_NAMES
        ):
            raise ValueError(
                "CityLearn challenge schema or central future policy is inconsistent"
            )
        observation_names = tuple(
            name for item in self.agents for name in item.observation_names
        )
        if any(forbidden & set(item.observation_names) for item in self.agents) or any(
            token in name.lower()
            for name in observation_names
            for token in ("future", "forecast", "next", "predicted")
        ):
            raise ValueError("CityLearn actor observation schema leaks future values")
        return self

    @property
    def possible_agents(self) -> tuple[str, ...]:
        return tuple(item.agent_id for item in self.agents)


MultiAgentSuiteAdapterManifest: TypeAlias = (
    Mpe2AdapterManifest | Smacv2AdapterManifest | CityLearnAdapterManifest
)


def suite_adapter_parent_references(
    manifest: MultiAgentSuiteAdapterManifest,
) -> tuple[ArtifactReference, ...]:
    if type(manifest) not in {
        Mpe2AdapterManifest,
        Smacv2AdapterManifest,
        CityLearnAdapterManifest,
    }:
        raise ValueError("suite adapter manifest must use an exact branch type")
    references = [
        manifest.environment_binding,
        manifest.runtime_profile_manifest,
        manifest.official_provenance,
    ]
    if type(manifest) is CityLearnAdapterManifest:
        references.append(manifest.challenge_schema)
    return tuple(
        sorted(
            references,
            key=lambda item: item.artifact_id.encode("utf-8"),
        )
    )


class Mpe2ActorInput(StrictFrozenModel):
    agent_id: AgentId
    role_index: NonNegativeSafeCanonicalInt
    values: FloatVector
    feature_availability_mask: BoolVector
    source: Literal["official_state", "native_local_observation"]

    @model_validator(mode="after")
    def require_aligned_feature_mask(self) -> Self:
        if len(self.values) != 54 or len(self.feature_availability_mask) != 54:
            raise ValueError("MPE2 actor inputs require 54 aligned features")
        return self


class Mpe2ActorBatch(StrictFrozenModel):
    schema_version: Literal["automarkov.mpe2-actor-batch.v1"]
    condition: Literal["full_state", "native_local"]
    inputs: FrozenSequence[Mpe2ActorInput]

    @model_validator(mode="after")
    def require_agent_order(self) -> Self:
        ids = tuple(item.agent_id for item in self.inputs)
        if ids != ("agent_0", "agent_1", "agent_2"):
            raise ValueError("MPE2 actor batch keyset drifted")
        if tuple(item.role_index for item in self.inputs) != (0, 1, 2):
            raise ValueError("MPE2 actor role indices drifted")
        return self


class Smacv2AgentObservation(StrictFrozenModel):
    agent_id: AgentId
    upstream_agent_index: NonNegativeSafeCanonicalInt
    local_observation: FloatVector
    action_mask: BoolVector

    @model_validator(mode="after")
    def require_available_policy_action(self) -> Self:
        if not self.action_mask or not any(self.action_mask):
            raise ValueError("live SMACv2 agents require an available policy action")
        return self


class Smacv2ActionToken(StrictFrozenModel):
    schema_version: Literal["automarkov.smacv2-action-token.v1"]
    partition: Literal["training", "evaluation"]
    config_id: AgentId
    episode_id: NonNegativeSafeCanonicalInt
    step_id: NonNegativeSafeCanonicalInt
    action_mask_hash: Sha256Value


class Smacv2ActorSnapshot(StrictFrozenModel):
    schema_version: Literal["automarkov.smacv2-actor-snapshot.v1"]
    action_token: Smacv2ActionToken
    active_agents: FrozenSequence[Smacv2AgentObservation]
    dead_agent_noop_actions: FrozenSequence[AgentId]

    @model_validator(mode="after")
    def require_disjoint_complete_agents(self) -> Self:
        active = tuple(item.agent_id for item in self.active_agents)
        dead = self.dead_agent_noop_actions
        if active != tuple(sorted(set(active), key=lambda item: item.encode("utf-8"))):
            raise ValueError("SMACv2 active agents must be canonical")
        if dead != tuple(sorted(set(dead), key=lambda item: item.encode("utf-8"))):
            raise ValueError("SMACv2 dead agents must be canonical")
        if set(active) & set(dead) or set(active) | set(dead) != {
            f"agent_{index}" for index in range(5)
        }:
            raise ValueError("SMACv2 active/dead agent partition is incomplete")
        return self


class CityLearnEnergyBalance(StrictFrozenModel):
    agent_id: AgentId
    net_electricity_consumption_kwh: StrictCanonicalFloat
    cooling_electricity_consumption_kwh: StrictCanonicalFloat
    heating_electricity_consumption_kwh: StrictCanonicalFloat
    dhw_electricity_consumption_kwh: StrictCanonicalFloat
    non_shiftable_load_electricity_consumption_kwh: StrictCanonicalFloat
    electrical_storage_electricity_consumption_kwh: StrictCanonicalFloat
    electrical_storage_charge_power_kw: NonNegativeCanonicalFloat
    electrical_storage_discharge_power_kw: NonNegativeCanonicalFloat
    electrical_storage_max_charge_power_kw: NonNegativeCanonicalFloat
    electrical_storage_max_discharge_power_kw: NonNegativeCanonicalFloat
    step_duration_hours: StrictCanonicalFloat
    solar_generation_kwh: StrictCanonicalFloat
    charger_electricity_consumption_kwh: StrictCanonicalFloat
    washing_machine_electricity_consumption_kwh: StrictCanonicalFloat
    power_outage: bool = Field(strict=True)
    state_of_charge_kwh: NonNegativeCanonicalFloat
    storage_capacity_kwh: NonNegativeCanonicalFloat
    electricity_price_per_kwh: NonNegativeCanonicalFloat
    carbon_intensity_kg_per_kwh: NonNegativeCanonicalFloat
    electricity_cost: StrictCanonicalFloat
    carbon_emissions_kg: StrictCanonicalFloat

    @model_validator(mode="after")
    def require_energy_conservation_and_soc_bounds(self) -> Self:
        if self.state_of_charge_kwh > self.storage_capacity_kwh:
            raise ValueError("CityLearn state of charge exceeds storage capacity")
        if (
            self.step_duration_hours <= 0.0
            or self.electrical_storage_charge_power_kw
            > self.electrical_storage_max_charge_power_kw
            or self.electrical_storage_discharge_power_kw
            > self.electrical_storage_max_discharge_power_kw
        ):
            raise ValueError("CityLearn storage power limit invariant failed")
        if (
            self.electrical_storage_charge_power_kw > 0.0
            and self.electrical_storage_discharge_power_kw > 0.0
        ):
            raise ValueError("CityLearn storage cannot charge and discharge together")
        expected_storage = (
            self.electrical_storage_charge_power_kw
            - self.electrical_storage_discharge_power_kw
        ) * self.step_duration_hours
        if not isclose(
            self.electrical_storage_electricity_consumption_kwh,
            expected_storage,
            rel_tol=0.0,
            abs_tol=1e-6,
        ):
            raise ValueError("CityLearn storage energy and power invariant failed")
        expected = (
            0.0
            if self.power_outage
            else sum(
                (
                    self.cooling_electricity_consumption_kwh,
                    self.heating_electricity_consumption_kwh,
                    self.dhw_electricity_consumption_kwh,
                    self.non_shiftable_load_electricity_consumption_kwh,
                    self.electrical_storage_electricity_consumption_kwh,
                    self.solar_generation_kwh,
                    self.charger_electricity_consumption_kwh,
                    self.washing_machine_electricity_consumption_kwh,
                )
            )
        )
        if not isclose(
            self.net_electricity_consumption_kwh,
            expected,
            rel_tol=0.0,
            abs_tol=1e-6,
        ):
            raise ValueError("CityLearn energy conservation invariant failed")
        expected_cost = (
            self.net_electricity_consumption_kwh * self.electricity_price_per_kwh
        )
        expected_carbon = (
            self.net_electricity_consumption_kwh * self.carbon_intensity_kg_per_kwh
        )
        if not isclose(
            self.electricity_cost, expected_cost, rel_tol=0.0, abs_tol=1e-6
        ) or not isclose(
            self.carbon_emissions_kg,
            expected_carbon,
            rel_tol=0.0,
            abs_tol=1e-6,
        ):
            raise ValueError("CityLearn official cost or carbon invariant failed")
        return self


class AgentVector(StrictFrozenModel):
    agent_id: AgentId
    values: FloatVector


class AgentScalar(StrictFrozenModel):
    agent_id: AgentId
    value: StrictCanonicalFloat


class AgentFlag(StrictFrozenModel):
    agent_id: AgentId
    value: bool = Field(strict=True)


class CityLearnPhaseInfo(StrictFrozenModel):
    key: Literal["phase"]
    value: Literal["reset", "step"]


class CityLearnTimeStepInfo(StrictFrozenModel):
    key: Literal["time_step"]
    value: NonNegativeSafeCanonicalInt


CityLearnInfoEntry: TypeAlias = Annotated[
    CityLearnPhaseInfo | CityLearnTimeStepInfo,
    Field(discriminator="key"),
]


class AgentInfo(StrictFrozenModel):
    agent_id: AgentId
    info: FrozenSequence[CityLearnInfoEntry]


class CityLearnParallelReset(StrictFrozenModel):
    schema_version: Literal["automarkov.citylearn-parallel-reset.v1"]
    observations: FrozenSequence[AgentVector]
    infos: FrozenSequence[AgentInfo]

    @model_validator(mode="after")
    def require_keysets(self) -> Self:
        if tuple(item.agent_id for item in self.observations) != tuple(
            item.agent_id for item in self.infos
        ):
            raise ValueError("CityLearn reset keysets must match")
        return self

    @property
    def agent_ids(self) -> tuple[str, ...]:
        return tuple(item.agent_id for item in self.observations)

    def observation(self, agent_id: str) -> tuple[float, ...]:
        return next(
            item.values for item in self.observations if item.agent_id == agent_id
        )


class CityLearnParallelTransition(StrictFrozenModel):
    schema_version: Literal["automarkov.citylearn-parallel-transition.v1"]
    observations: FrozenSequence[AgentVector]
    rewards: FrozenSequence[AgentScalar]
    terminations: FrozenSequence[AgentFlag]
    truncations: FrozenSequence[AgentFlag]
    infos: FrozenSequence[AgentInfo]
    energy_balances: FrozenSequence[CityLearnEnergyBalance]

    @model_validator(mode="after")
    def require_exact_parallel_keysets(self) -> Self:
        sequences = (
            self.observations,
            self.rewards,
            self.terminations,
            self.truncations,
            self.infos,
            self.energy_balances,
        )
        keysets = tuple(tuple(item.agent_id for item in values) for values in sequences)
        if not keysets[0] or any(keys != keysets[0] for keys in keysets[1:]):
            raise ValueError("CityLearn Parallel API keysets must match exactly")
        return self

    @property
    def agent_ids(self) -> tuple[str, ...]:
        return tuple(item.agent_id for item in self.observations)

    def reward(self, agent_id: str) -> float:
        return next(item.value for item in self.rewards if item.agent_id == agent_id)

    def termination(self, agent_id: str) -> bool:
        return next(
            item.value for item in self.terminations if item.agent_id == agent_id
        )

    def truncation(self, agent_id: str) -> bool:
        return next(
            item.value for item in self.truncations if item.agent_id == agent_id
        )


class PettingZooKeysetAudit(StrictFrozenModel):
    possible_agents: CanonicalAgentIds
    parallel_observation_keys: CanonicalAgentIds
    parallel_reward_keys: CanonicalAgentIds
    parallel_termination_keys: CanonicalAgentIds
    parallel_truncation_keys: CanonicalAgentIds
    parallel_info_keys: CanonicalAgentIds
    aec_agents: CanonicalAgentIds
    active_aec_agent: AgentId

    @model_validator(mode="after")
    def require_parallel_and_aec_keysets(self) -> Self:
        parallel_keysets = (
            self.parallel_observation_keys,
            self.parallel_reward_keys,
            self.parallel_termination_keys,
            self.parallel_truncation_keys,
            self.parallel_info_keys,
        )
        if any(keys != self.possible_agents for keys in parallel_keysets):
            raise ValueError("PettingZoo Parallel API keysets do not match")
        if self.aec_agents != self.possible_agents or self.active_aec_agent not in set(
            self.aec_agents
        ):
            raise ValueError("PettingZoo AEC agent keyset or active agent is invalid")
        return self


class MultiAgentSuiteReadiness(StrictFrozenModel):
    schema_version: Literal["automarkov.multi-agent-suite-readiness.v1"]
    runtime_profile_id: Literal["env-mpe2", "env-smacv2", "env-citylearn"]
    state: Literal["WAITING_RUNTIME", "READY"]
    reason_code: Literal[
        "runtime_profile_unavailable",
        "fixed_commit_remote_env_unavailable",
        "suite_adapter_ready",
    ]


__all__ = [
    "AgentFlag",
    "AgentInfo",
    "AgentScalar",
    "AgentVector",
    "CityLearnAdapterManifest",
    "CityLearnEnergyBalance",
    "CityLearnEvaluationPeriod",
    "CityLearnInfoEntry",
    "CityLearnParallelReset",
    "CityLearnParallelTransition",
    "Mpe2ActorBatch",
    "Mpe2ActorInput",
    "Mpe2AdapterManifest",
    "Mpe2InformationPolicy",
    "MultiAgentSuiteAdapterManifest",
    "MultiAgentSuiteReadiness",
    "PettingZooKeysetAudit",
    "Smacv2ActionToken",
    "Smacv2ActorSnapshot",
    "Smacv2AdapterManifest",
    "Smacv2AgentObservation",
    "suite_adapter_parent_references",
]
