from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

import pytest
from pydantic import ValidationError

from automarkov.contracts.multi_agent import Smacv2AdapterManifest
from automarkov.lifecycle import ArtifactReference
from automarkov.multi_agent_suite_adapters import Smacv2SuiteAdapter


def _ref(name: str, digit: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=f"artifact_{sha256(name.encode()).hexdigest()}",
        payload_hash=f"sha256:{digit * 64}",
    )


def _manifest() -> Smacv2AdapterManifest:
    return Smacv2AdapterManifest.model_validate(
        {
            "schema_version": "automarkov.smacv2-adapter-manifest.v1",
            "suite_id": "smacv2_posg",
            "environment_binding": _ref("binding", "1").model_dump(),
            "runtime_profile_manifest": _ref("profile", "2").model_dump(),
            "official_provenance": _ref("smacv2", "3").model_dump(),
            "protocol_version": "automarkov.remote-env.v1",
            "space_contract_hash": "sha256:" + "4" * 64,
            "adapter_source_hash": "sha256:" + "5" * 64,
            "runtime_profile_id": "env-smacv2",
            "upstream_commit": "577ab5a2cff2391f8df582da5731ea9cd6adf3c6",
            "scenario_id": "protoss_5_vs_5",
            "possible_agents": [f"agent_{index}" for index in range(5)],
            "capability_config": {
                "n_units": 5,
                "n_enemies": 5,
                "team_gen": {
                    "dist_type": "weighted_teams",
                    "unit_types": ["stalker", "zealot", "colossus"],
                    "exception_unit_types": ["colossus"],
                    "weights": [0.45, 0.45, 0.1],
                    "observe": True,
                },
                "start_positions": {
                    "dist_type": "surrounded_and_reflect",
                    "p": 0.5,
                    "n_enemies": 5,
                    "map_x": 32,
                    "map_y": 32,
                },
            },
            "train_config_ids": ["train_000", "train_001"],
            "test_config_ids": ["test_000"],
        },
        strict=True,
    )


@dataclass
class _Unit:
    health: float


class _SmacBackend:
    n_agents = 5
    n_actions = 4

    def __init__(self) -> None:
        self.health = [10.0, 0.0, 10.0, 10.0, 10.0]
        self.masks = (
            (0, 1, 1, 0),
            (1, 0, 0, 0),
            (0, 1, 0, 1),
            (0, 1, 1, 1),
            (0, 1, 0, 0),
        )
        self.reset_config: object | None = None
        self.state_calls = 0
        self.step_actions: list[tuple[int, ...]] = []

    def reset(self, episode_config: object) -> None:
        self.reset_config = episode_config

    def get_unit_by_id(self, agent_id: int) -> _Unit:
        return _Unit(self.health[agent_id])

    def get_obs_agent(self, agent_id: int) -> tuple[float, ...]:
        return (float(agent_id), float(agent_id + 1))

    def get_avail_agent_actions(self, agent_id: int) -> tuple[int, ...]:
        return self.masks[agent_id]

    def get_state(self) -> tuple[float, ...]:
        self.state_calls += 1
        return (9.0, 8.0, 7.0)

    def step(
        self, actions: tuple[int, ...]
    ) -> tuple[float, bool, bool, dict[str, object]]:
        self.step_actions.append(actions)
        return 1.0, False, False, {"battle_won": False}


def test_smacv2_snapshot_preserves_masks_and_dead_agent_noop_semantics() -> None:
    backend = _SmacBackend()
    adapter = Smacv2SuiteAdapter(backend=backend, manifest=_manifest())

    adapter.reset(partition="training", config_id="train_000")
    snapshot = adapter.actor_snapshot()

    assert backend.reset_config == {
        "capability_config": _manifest().capability_config.model_dump(mode="python"),
        "config_id": "train_000",
        "partition": "training",
    }
    assert backend.state_calls == 0
    assert tuple(item.agent_id for item in snapshot.active_agents) == (
        "agent_0",
        "agent_2",
        "agent_3",
        "agent_4",
    )
    assert snapshot.dead_agent_noop_actions == ("agent_1",)
    assert snapshot.active_agents[0].action_mask == (True, True, False)

    transition = adapter.step(
        {
            "agent_0": 0,
            "agent_2": 2,
            "agent_3": 1,
            "agent_4": 0,
        },
        token=snapshot.action_token,
    )
    assert backend.step_actions == [(1, 0, 3, 2, 1)]
    assert transition == (1.0, False, False, {"battle_won": False})
    assert adapter.critic_state() == (9.0, 8.0, 7.0)


def test_smacv2_rejects_dead_or_live_mask_drift() -> None:
    backend = _SmacBackend()
    adapter = Smacv2SuiteAdapter(backend=backend, manifest=_manifest())
    adapter.reset(partition="training", config_id="train_000")
    backend.masks = (
        backend.masks[0],
        (0, 1, 0, 0),
        *backend.masks[2:],
    )

    with pytest.raises(ValueError, match="dead agent"):
        adapter.actor_snapshot()

    backend = _SmacBackend()
    backend.masks = ((1, 1, 0, 0), *backend.masks[1:])
    adapter = Smacv2SuiteAdapter(backend=backend, manifest=_manifest())
    adapter.reset(partition="training", config_id="train_000")
    with pytest.raises(ValueError, match="live agent"):
        adapter.actor_snapshot()


def test_smacv2_rejects_config_drift_or_train_test_overlap() -> None:
    raw = _manifest().model_dump(mode="json", round_trip=True, warnings="error")
    raw["capability_config"]["team_gen"]["weights"] = [0.5, 0.4, 0.1]
    with pytest.raises(ValidationError, match="weights"):
        Smacv2AdapterManifest.model_validate(raw, strict=True)

    raw = _manifest().model_dump(mode="json", round_trip=True, warnings="error")
    raw["test_config_ids"] = ["train_001"]
    with pytest.raises(ValidationError, match="disjoint"):
        Smacv2AdapterManifest.model_validate(raw, strict=True)


def test_smacv2_rejects_policy_action_missing_or_blocked_by_mask() -> None:
    adapter = Smacv2SuiteAdapter(backend=_SmacBackend(), manifest=_manifest())
    adapter.reset(partition="training", config_id="train_000")
    snapshot = adapter.actor_snapshot()

    with pytest.raises(ValueError, match="keyset"):
        adapter.step({"agent_0": 0}, token=snapshot.action_token)
    with pytest.raises(ValueError, match="masked"):
        adapter.step(
            {
                "agent_0": 2,
                "agent_2": 2,
                "agent_3": 1,
                "agent_4": 0,
            },
            token=snapshot.action_token,
        )


def test_smacv2_rejects_forged_stale_or_replayed_action_token() -> None:
    backend = _SmacBackend()
    adapter = Smacv2SuiteAdapter(backend=backend, manifest=_manifest())
    adapter.reset(partition="training", config_id="train_000")
    snapshot = adapter.actor_snapshot()
    actions = {
        "agent_0": 0,
        "agent_2": 2,
        "agent_3": 1,
        "agent_4": 0,
    }

    forged = snapshot.action_token.model_copy(update={"step_id": 999})
    with pytest.raises(ValueError, match="token"):
        adapter.step(actions, token=forged)

    backend.masks = (
        (0, 1, 0, 1),
        *backend.masks[1:],
    )
    with pytest.raises(ValueError, match="stale"):
        adapter.step(actions, token=snapshot.action_token)

    backend = _SmacBackend()
    adapter = Smacv2SuiteAdapter(backend=backend, manifest=_manifest())
    adapter.reset(partition="training", config_id="train_000")
    snapshot = adapter.actor_snapshot()
    adapter.step(actions, token=snapshot.action_token)
    with pytest.raises(ValueError, match="token"):
        adapter.step(actions, token=snapshot.action_token)


def test_smacv2_binds_train_test_config_to_episode_and_step_tokens() -> None:
    backend = _SmacBackend()
    adapter = Smacv2SuiteAdapter(backend=backend, manifest=_manifest())

    with pytest.raises(ValueError, match="reset"):
        adapter.actor_snapshot()
    with pytest.raises(ValueError, match="partition"):
        adapter.reset(partition="evaluation", config_id="train_000")

    adapter.reset(partition="evaluation", config_id="test_000")
    snapshot = adapter.actor_snapshot()
    assert snapshot.action_token.partition == "evaluation"
    assert snapshot.action_token.config_id == "test_000"
    assert snapshot.action_token.episode_id == 1

    adapter.reset(partition="training", config_id="train_001")
    with pytest.raises(ValueError, match="expired"):
        adapter.step(
            {
                "agent_0": 0,
                "agent_2": 2,
                "agent_3": 1,
                "agent_4": 0,
            },
            token=snapshot.action_token,
        )
