from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from threading import Lock
from typing import Literal, Protocol, cast

from automarkov.canonical import canonical_json_bytes
from automarkov.environment_contracts import RuntimeImageStatus
from automarkov.multi_agent_suite_contracts import (
    AgentFlag,
    AgentInfo,
    AgentScalar,
    AgentVector,
    CityLearnAdapterManifest,
    CityLearnEnergyBalance,
    CityLearnParallelReset,
    CityLearnParallelTransition,
    CityLearnPhaseInfo,
    CityLearnTimeStepInfo,
    Mpe2ActorBatch,
    Mpe2ActorInput,
    Mpe2AdapterManifest,
    MultiAgentSuiteAdapterManifest,
    MultiAgentSuiteReadiness,
    Smacv2ActionToken,
    Smacv2ActorSnapshot,
    Smacv2AdapterManifest,
    Smacv2AgentObservation,
)
from automarkov.provenance import RuntimeProfileManifest
from automarkov.suite_adapters import (
    SuiteProfileContract,
    verify_official_suite_profile,
)


def _float_vector(value: object, *, label: str) -> tuple[float, ...]:
    if type(value) not in {list, tuple}:
        raise ValueError(f"{label} must be an exact float vector")
    vector = tuple(cast(Sequence[object], value))
    if any(type(item) is not float for item in vector):
        raise ValueError(f"{label} must be an exact float vector")
    return cast(tuple[float, ...], vector)


def _citylearn_info(
    value: object,
) -> tuple[CityLearnPhaseInfo | CityLearnTimeStepInfo, ...]:
    if type(value) is not dict:
        raise ValueError("CityLearn info must be an exact closed mapping")
    raw = cast(dict[object, object], value)
    if any(
        type(key) is not str
        or any(token in key.lower() for token in ("future", "forecast", "next"))
        for key in raw
    ):
        raise ValueError("CityLearn info contains a forbidden future field")
    if not set(raw).issubset({"phase", "time_step"}):
        raise ValueError("CityLearn info contains a non-allowlisted field")
    entries: list[CityLearnPhaseInfo | CityLearnTimeStepInfo] = []
    for key in sorted(cast(set[str], set(raw)), key=lambda item: item.encode("utf-8")):
        item = raw[key]
        if key == "phase":
            if item == "reset" and type(item) is str:
                entries.append(CityLearnPhaseInfo(key="phase", value="reset"))
            elif item == "step" and type(item) is str:
                entries.append(CityLearnPhaseInfo(key="phase", value="step"))
            else:
                raise ValueError("CityLearn info phase is invalid")
        else:
            if type(item) is not int or item < 0:
                raise ValueError("CityLearn info time_step is invalid")
            entries.append(CityLearnTimeStepInfo(key="time_step", value=item))
    return tuple(entries)


class _Mpe2Backend(Protocol):
    @property
    def possible_agents(self) -> tuple[str, ...]: ...

    def state(self) -> tuple[float, ...]: ...


class _Mpe2ActorAdapter:
    def __init__(
        self, *, possible_agents: tuple[str, ...], manifest: Mpe2AdapterManifest
    ) -> None:
        if type(manifest) is not Mpe2AdapterManifest:
            raise ValueError("MPE2 adapter requires the exact manifest")
        if possible_agents != manifest.possible_agents:
            raise ValueError("MPE2 backend possible-agent keyset drifted")
        self._manifest = manifest

    def _local_observations(
        self, observations: Mapping[str, tuple[float, ...]]
    ) -> dict[str, tuple[float, ...]]:
        if type(observations) is not dict or tuple(sorted(observations)) != tuple(
            sorted(self._manifest.possible_agents)
        ):
            raise ValueError("MPE2 actor observation keyset drifted")
        local = {
            agent_id: _float_vector(observations[agent_id], label="MPE2 observation")
            for agent_id in self._manifest.possible_agents
        }
        if any(
            len(vector) != self._manifest.local_observation_dimension
            for vector in local.values()
        ):
            raise ValueError("MPE2 local observation must contain 18 features")
        return local


class Mpe2SuiteAdapter(_Mpe2ActorAdapter):
    """MPE2 full-state MG adapter；actor 与 critic 均有官方 state capability。"""

    def __init__(self, *, backend: _Mpe2Backend, manifest: Mpe2AdapterManifest) -> None:
        if (
            manifest.condition != "full_state"
            or manifest.suite_id != "mpe2_full_state_mg"
        ):
            raise ValueError("MPE2 full-state adapter rejects native actor manifests")
        super().__init__(
            possible_agents=tuple(backend.possible_agents), manifest=manifest
        )
        self._backend = backend

    def actor_inputs(
        self, observations: Mapping[str, tuple[float, ...]]
    ) -> Mpe2ActorBatch:
        self._local_observations(observations)
        state = self.critic_state()
        inputs = tuple(
            Mpe2ActorInput(
                agent_id=agent_id,
                role_index=index,
                values=state,
                feature_availability_mask=(True,) * len(state),
                source="official_state",
            )
            for index, agent_id in enumerate(self._manifest.possible_agents)
        )
        return Mpe2ActorBatch(
            schema_version="automarkov.mpe2-actor-batch.v1",
            condition="full_state",
            inputs=inputs,
        )

    def critic_state(self) -> tuple[float, ...]:
        state = _float_vector(self._backend.state(), label="MPE2 official state")
        if len(state) != self._manifest.actor_input_dimension:
            raise ValueError("MPE2 official state must contain exactly 54 features")
        return state


class Mpe2NativeActorSuiteAdapter(_Mpe2ActorAdapter):
    """MPE2 native-local actor view；此对象不具备 global critic-state capability。"""

    def __init__(self, *, backend: _Mpe2Backend, manifest: Mpe2AdapterManifest) -> None:
        if (
            manifest.condition != "native_local"
            or manifest.suite_id != "mpe2_native_local_posg"
        ):
            raise ValueError(
                "MPE2 native actor adapter requires its exact suite identity"
            )
        # Native actor 只复制稳定 keyset；不保留带 state() capability 的 backend。
        super().__init__(
            possible_agents=tuple(backend.possible_agents), manifest=manifest
        )

    def actor_inputs(
        self, observations: Mapping[str, tuple[float, ...]]
    ) -> Mpe2ActorBatch:
        local = self._local_observations(observations)
        unavailable = (False,) * (
            self._manifest.actor_input_dimension
            - self._manifest.local_observation_dimension
        )
        padding = (0.0,) * len(unavailable)
        inputs = tuple(
            Mpe2ActorInput(
                agent_id=agent_id,
                role_index=index,
                values=local[agent_id] + padding,
                feature_availability_mask=(True,) * len(local[agent_id]) + unavailable,
                source="native_local_observation",
            )
            for index, agent_id in enumerate(self._manifest.possible_agents)
        )
        return Mpe2ActorBatch(
            schema_version="automarkov.mpe2-actor-batch.v1",
            condition="native_local",
            inputs=inputs,
        )


class _Smacv2Unit(Protocol):
    health: float


class _Smacv2Backend(Protocol):
    n_agents: int
    n_actions: int

    def reset(self, episode_config: object) -> None: ...

    def get_unit_by_id(self, agent_id: int) -> _Smacv2Unit: ...

    def get_obs_agent(self, agent_id: int) -> tuple[float, ...]: ...

    def get_avail_agent_actions(self, agent_id: int) -> tuple[int, ...]: ...

    def get_state(self) -> tuple[float, ...]: ...

    def step(
        self, actions: tuple[int, ...]
    ) -> tuple[float, bool, bool, Mapping[str, object]]: ...


class Smacv2SuiteAdapter:
    """保持 SMACv2 原生动作 mask、dead-agent no-op 与 critic state 接缝。"""

    def __init__(
        self, *, backend: _Smacv2Backend, manifest: Smacv2AdapterManifest
    ) -> None:
        if type(manifest) is not Smacv2AdapterManifest:
            raise ValueError("SMACv2 adapter requires the exact manifest")
        if backend.n_agents != len(manifest.possible_agents) or (
            type(backend.n_actions) is not int or backend.n_actions < 2
        ):
            raise ValueError("SMACv2 backend agent/action dimensions drifted")
        self._backend = backend
        self._manifest = manifest
        self._action_lock = Lock()
        self._step_id = 0
        self._episode_id = 0
        self._partition: Literal["training", "evaluation"] | None = None
        self._config_id: str | None = None
        self._latest_action_token: Smacv2ActionToken | None = None

    def reset(
        self,
        *,
        partition: Literal["training", "evaluation"],
        config_id: str,
    ) -> None:
        if type(config_id) is not str:
            raise ValueError("SMACv2 reset requires an exact configuration ID")
        allowed = (
            self._manifest.train_config_ids
            if partition == "training"
            else self._manifest.test_config_ids
        )
        if config_id not in allowed:
            raise ValueError("SMACv2 configuration does not belong to the partition")
        with self._action_lock:
            self._backend.reset(
                {
                    "capability_config": self._manifest.capability_config.model_dump(
                        mode="python", round_trip=True, warnings="error"
                    ),
                    "config_id": config_id,
                    "partition": partition,
                }
            )
            self._episode_id += 1
            self._step_id = 0
            self._partition = partition
            self._config_id = config_id
            self._latest_action_token = None

    def _read_actor_state(
        self,
    ) -> tuple[tuple[Smacv2AgentObservation, ...], tuple[str, ...], Smacv2ActionToken]:
        if self._partition is None or self._config_id is None:
            raise ValueError("SMACv2 actor snapshot requires a successful reset")
        active: list[Smacv2AgentObservation] = []
        dead: list[str] = []
        mask_bindings: list[dict[str, object]] = []
        for index, agent_id in enumerate(self._manifest.possible_agents):
            unit = self._backend.get_unit_by_id(index)
            health = unit.health
            if type(health) is not float:
                raise ValueError("SMACv2 unit health must be an exact float")
            raw_mask = tuple(self._backend.get_avail_agent_actions(index))
            if len(raw_mask) != self._backend.n_actions or any(
                type(item) is not int or item not in {0, 1} for item in raw_mask
            ):
                raise ValueError("SMACv2 action mask shape or values drifted")
            if health <= 0.0:
                if raw_mask != (1,) + (0,) * (self._backend.n_actions - 1):
                    raise ValueError("SMACv2 dead agent must expose only no-op")
                dead.append(agent_id)
                status = "dead"
            else:
                if raw_mask[0] != 0:
                    raise ValueError("SMACv2 live agent cannot expose dead-agent no-op")
                observation = _float_vector(
                    self._backend.get_obs_agent(index),
                    label="SMACv2 local observation",
                )
                active.append(
                    Smacv2AgentObservation(
                        agent_id=agent_id,
                        upstream_agent_index=index,
                        local_observation=observation,
                        action_mask=tuple(bool(item) for item in raw_mask[1:]),
                    )
                )
                status = "active"
            mask_bindings.append(
                {
                    "agent_id": agent_id,
                    "upstream_agent_index": index,
                    "status": status,
                    "upstream_action_mask": [bool(item) for item in raw_mask],
                }
            )
        action_mask_hash = (
            "sha256:"
            + sha256(
                canonical_json_bytes(
                    {
                        "domain": "AutoMarkov-SMACv2-ActionMask-v1",
                        "partition": self._partition,
                        "config_id": self._config_id,
                        "episode_id": self._episode_id,
                        "step_id": self._step_id,
                        "agents": mask_bindings,
                    }
                )
            ).hexdigest()
        )
        token = Smacv2ActionToken(
            schema_version="automarkov.smacv2-action-token.v1",
            partition=self._partition,
            config_id=self._config_id,
            episode_id=self._episode_id,
            step_id=self._step_id,
            action_mask_hash=action_mask_hash,
        )
        return tuple(active), tuple(dead), token

    def actor_snapshot(self) -> Smacv2ActorSnapshot:
        with self._action_lock:
            active, dead, token = self._read_actor_state()
            self._latest_action_token = token
            return Smacv2ActorSnapshot(
                schema_version="automarkov.smacv2-actor-snapshot.v1",
                action_token=token,
                active_agents=active,
                dead_agent_noop_actions=dead,
            )

    def step(
        self,
        actions: Mapping[str, int],
        *,
        token: Smacv2ActionToken,
    ) -> tuple[float, bool, bool, Mapping[str, object]]:
        if type(token) is not Smacv2ActionToken or type(actions) is not dict:
            raise ValueError("SMACv2 policy actions require an exact action token")
        with self._action_lock:
            if self._latest_action_token is None or token != self._latest_action_token:
                raise ValueError("SMACv2 action token is forged, expired, or replayed")
            current_active, _, current_token = self._read_actor_state()
            if current_token != token:
                self._latest_action_token = None
                raise ValueError(
                    "SMACv2 action token is stale after backend mask drift"
                )
            active = {item.agent_id: item for item in current_active}
            if set(actions) != set(active):
                raise ValueError("SMACv2 policy action keyset drifted")
            upstream = [0] * self._backend.n_agents
            for agent_id, item in active.items():
                action = actions[agent_id]
                if (
                    type(action) is not int
                    or action < 0
                    or action >= len(item.action_mask)
                    or not item.action_mask[action]
                ):
                    raise ValueError("SMACv2 policy action is masked or out of range")
                upstream[item.upstream_agent_index] = action + 1
            self._latest_action_token = None
            try:
                result = self._backend.step(tuple(upstream))
                if type(result) is not tuple or len(result) != 4:
                    raise ValueError("SMACv2 step result shape drifted")
                reward, terminated, truncated, info = result
                if (
                    type(reward) is not float
                    or type(terminated) is not bool
                    or type(truncated) is not bool
                    or not isinstance(info, Mapping)
                ):
                    raise ValueError("SMACv2 step result types drifted")
            except Exception:
                self._partition = None
                self._config_id = None
                raise
            self._step_id += 1
            return reward, terminated, truncated, dict(info)

    def critic_state(self) -> tuple[float, ...]:
        return _float_vector(self._backend.get_state(), label="SMACv2 critic state")


class _CityLearnBackend(Protocol):
    central_agent: bool
    challenge_schema_hash: str
    observation_names: tuple[tuple[str, ...], ...]

    def reset(
        self, *, seed: int, options: dict[str, object]
    ) -> tuple[tuple[tuple[float, ...], ...], Mapping[str, object]]: ...

    def step(
        self, actions: list[list[float]]
    ) -> tuple[
        tuple[tuple[float, ...], ...],
        tuple[float, ...],
        bool,
        bool,
        Mapping[str, object],
        Mapping[str, CityLearnEnergyBalance],
    ]: ...


class CityLearnSuiteAdapter:
    """把 CityLearn 分散控制输出映射为稳定的 PettingZoo Parallel keyset。"""

    def __init__(
        self,
        *,
        backend: _CityLearnBackend,
        manifest: CityLearnAdapterManifest,
    ) -> None:
        if type(manifest) is not CityLearnAdapterManifest:
            raise ValueError("CityLearn adapter requires the exact manifest")
        expected_observations = tuple(
            item.observation_names for item in manifest.agents
        )
        if (
            backend.central_agent is not False
            or backend.challenge_schema_hash != manifest.challenge_schema_hash
            or (
                tuple(tuple(item) for item in backend.observation_names)
                != expected_observations
            )
        ):
            raise ValueError("CityLearn backend observation schema drifted")
        self._backend = backend
        self._manifest = manifest
        self._time_step: int | None = None

    def _observations(self, values: object) -> tuple[AgentVector, ...]:
        if type(values) not in {list, tuple} or len(
            cast(Sequence[object], values)
        ) != len(self._manifest.agents):
            raise ValueError("CityLearn observation agent keyset drifted")
        observations: list[AgentVector] = []
        for schema, value in zip(
            self._manifest.agents, cast(Sequence[object], values), strict=True
        ):
            vector = _float_vector(value, label="CityLearn observation")
            if len(vector) != len(schema.observation_names):
                raise ValueError("CityLearn observation shape drifted")
            observations.append(AgentVector(agent_id=schema.agent_id, values=vector))
        return tuple(observations)

    def reset(self, *, seed: int) -> CityLearnParallelReset:
        if type(seed) is not int or seed < 0:
            raise ValueError("CityLearn reset requires a nonnegative integer seed")
        observations, info = self._backend.reset(seed=seed, options={})
        values = self._observations(observations)
        sanitized_info = _citylearn_info(info)
        time_step = next(
            (
                item.value
                for item in sanitized_info
                if type(item) is CityLearnTimeStepInfo
            ),
            None,
        )
        if time_step != self._manifest.evaluation_period.start_time_step:
            raise ValueError("CityLearn reset is outside the frozen evaluation period")
        self._time_step = time_step
        infos = tuple(
            AgentInfo(agent_id=item.agent_id, info=sanitized_info) for item in values
        )
        return CityLearnParallelReset(
            schema_version="automarkov.citylearn-parallel-reset.v1",
            observations=values,
            infos=infos,
        )

    def step(
        self, actions: Mapping[str, tuple[float, ...]]
    ) -> CityLearnParallelTransition:
        if type(actions) is not dict or set(actions) != set(
            self._manifest.possible_agents
        ):
            raise ValueError("CityLearn action keyset drifted")
        upstream: list[list[float]] = []
        for schema in self._manifest.agents:
            vector = _float_vector(actions[schema.agent_id], label="CityLearn action")
            if len(vector) != len(schema.action_names):
                raise ValueError("CityLearn action shape drifted")
            upstream.append(list(vector))
        if self._time_step is None:
            raise ValueError("CityLearn step requires a successful reset")
        result = self._backend.step(upstream)
        if type(result) is not tuple or len(result) != 6:
            raise ValueError("CityLearn backend step snapshot is incomplete")
        observations, rewards, terminated, truncated, info, balances = result
        if type(terminated) is not bool or type(truncated) is not bool:
            raise ValueError("CityLearn termination flags must be exact booleans")
        observation_values = self._observations(observations)
        sanitized_info = _citylearn_info(info)
        time_step = next(
            (
                item.value
                for item in sanitized_info
                if type(item) is CityLearnTimeStepInfo
            ),
            None,
        )
        if (
            type(time_step) is not int
            or time_step != self._time_step + 1
            or time_step > self._manifest.evaluation_period.end_time_step
        ):
            raise ValueError("CityLearn step is outside the frozen evaluation period")
        at_end = time_step == self._manifest.evaluation_period.end_time_step
        if at_end != (terminated or truncated):
            raise ValueError("CityLearn terminal flag does not match evaluation period")
        reward_values = _float_vector(rewards, label="CityLearn rewards")
        if len(reward_values) != len(self._manifest.agents):
            raise ValueError("CityLearn reward keyset drifted")
        if type(balances) is not dict or set(balances) != set(
            self._manifest.possible_agents
        ):
            raise ValueError("CityLearn energy-balance keyset drifted")
        ordered_balances = tuple(
            balances[agent_id] for agent_id in self._manifest.possible_agents
        )
        if any(
            type(item) is not CityLearnEnergyBalance or item.agent_id != agent_id
            for item, agent_id in zip(
                ordered_balances, self._manifest.possible_agents, strict=True
            )
        ):
            raise ValueError("CityLearn energy-balance identity drifted")
        self._time_step = time_step
        return CityLearnParallelTransition(
            schema_version="automarkov.citylearn-parallel-transition.v1",
            observations=observation_values,
            rewards=tuple(
                AgentScalar(agent_id=agent_id, value=value)
                for agent_id, value in zip(
                    self._manifest.possible_agents, reward_values, strict=True
                )
            ),
            terminations=tuple(
                AgentFlag(agent_id=agent_id, value=terminated)
                for agent_id in self._manifest.possible_agents
            ),
            truncations=tuple(
                AgentFlag(agent_id=agent_id, value=truncated)
                for agent_id in self._manifest.possible_agents
            ),
            infos=tuple(
                AgentInfo(agent_id=agent_id, info=sanitized_info)
                for agent_id in self._manifest.possible_agents
            ),
            energy_balances=ordered_balances,
        )


def formal_multi_agent_suite_readiness(
    manifest: MultiAgentSuiteAdapterManifest,
    *,
    image_status: RuntimeImageStatus,
) -> MultiAgentSuiteReadiness:
    if type(manifest) not in {
        Mpe2AdapterManifest,
        Smacv2AdapterManifest,
        CityLearnAdapterManifest,
    }:
        raise ValueError("multi-agent readiness requires an exact suite manifest")
    reason = (
        "runtime_profile_unavailable"
        if image_status != "built"
        else "fixed_commit_remote_env_unavailable"
    )
    return MultiAgentSuiteReadiness(
        schema_version="automarkov.multi-agent-suite-readiness.v1",
        runtime_profile_id=manifest.runtime_profile_id,
        state="WAITING_RUNTIME",
        reason_code=reason,
    )


def build_multi_agent_suite_profile_contract(
    manifest: MultiAgentSuiteAdapterManifest,
    *,
    profile: RuntimeProfileManifest,
) -> SuiteProfileContract:
    if type(manifest) not in {
        Mpe2AdapterManifest,
        Smacv2AdapterManifest,
        CityLearnAdapterManifest,
    }:
        raise ValueError("multi-agent suite profile requires an exact manifest")
    expectation = verify_official_suite_profile(manifest.suite_id, profile)
    if type(manifest) is Smacv2AdapterManifest:
        environment_id = manifest.scenario_id
    elif type(manifest) in {Mpe2AdapterManifest, CityLearnAdapterManifest}:
        environment_id = cast(
            Mpe2AdapterManifest | CityLearnAdapterManifest, manifest
        ).environment_id
    else:  # pragma: no cover - exact branch set checked above
        raise ValueError("multi-agent suite profile branch is unavailable")
    if (
        manifest.runtime_profile_id != expectation.runtime_profile_id
        or environment_id != expectation.environment_id
        or manifest.runtime_profile_manifest.payload_hash != profile.manifest_hash
    ):
        raise ValueError("suite manifest does not bind the frozen official profile")
    return SuiteProfileContract(
        schema_version="automarkov.suite-profile-contract.v1",
        expectation=expectation,
        runtime_profile_manifest=manifest.runtime_profile_manifest,
        official_provenance=manifest.official_provenance,
        environment_space_hash=manifest.space_contract_hash,
        adapter_source_hash=manifest.adapter_source_hash,
        protocol_version=manifest.protocol_version,
    )


__all__ = [
    "CityLearnSuiteAdapter",
    "Mpe2NativeActorSuiteAdapter",
    "Mpe2SuiteAdapter",
    "Smacv2SuiteAdapter",
    "build_multi_agent_suite_profile_contract",
    "formal_multi_agent_suite_readiness",
]
