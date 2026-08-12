from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256

import pytest

from automarkov.lifecycle import ArtifactReference
from automarkov.remote_env_contracts import (
    BoxSpace,
    DictEntry,
    DictSpace,
    DiscreteSpace,
    FiniteTextSpace,
)
from automarkov.suite_adapters import (
    IntegerResetSeedContract,
    MiniGridMemoryBackend,
    MiniGridMemorySuiteAdapterManifest,
)

MISSION = "go to the matching object at the end of the hallway"


def _ref(name: str, digit: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=f"artifact_{sha256(name.encode()).hexdigest()}",
        payload_hash=f"sha256:{digit * 64}",
    )


def _manifest() -> MiniGridMemorySuiteAdapterManifest:
    return MiniGridMemorySuiteAdapterManifest(
        schema_version="automarkov.single-agent-suite-adapter.v1",
        suite_id="memory_pomdp",
        implementation_plan=_ref("plan", "1"),
        decision_process_spec=_ref("spec", "2"),
        signed_suite_manifest=_ref("suite", "3"),
        candidate_bundle=_ref("candidate", "4"),
        runtime_profile_manifest=_ref("profile", "5"),
        official_provenance=_ref("minigrid-provenance", "9"),
        adapter_id="adapter_minigrid_memory_v1",
        adapter_source_hash="sha256:" + "6" * 64,
        runtime_profile_id="env-minigrid",
        protocol_version="automarkov.remote-env.v1",
        frame_schema_hash="sha256:" + "7" * 64,
        space_adapter_registry_hash="sha256:" + "8" * 64,
        seed_contract=IntegerResetSeedContract(kind="integer_reset", seed=19),
        route="compose",
        environment_id="MiniGrid-MemoryS17Random-v0",
        package_name="minigrid",
        package_version="3.1.0",
        upstream_commit="90928729376741a41222a257911343b97103b548",
        mission_values=(MISSION,),
        observation_keys=("direction", "image", "mission"),
        observation_space=DictSpace(
            kind="Dict",
            entries=(
                DictEntry(
                    key="direction",
                    space=DiscreteSpace(kind="Discrete", n=4, start=0, dtype="int64"),
                ),
                DictEntry(
                    key="image",
                    space=BoxSpace(
                        kind="Box",
                        shape=(7, 7, 3),
                        dtype="uint8",
                        low_tensor_id="tensor_minigrid_image_low",
                        high_tensor_id="tensor_minigrid_image_high",
                    ),
                ),
                DictEntry(
                    key="mission",
                    space=FiniteTextSpace(kind="FiniteText", values=(MISSION,)),
                ),
            ),
        ),
        action_space=DiscreteSpace(kind="Discrete", n=7, start=0, dtype="int64"),
        observation_policy="partial_image_direction_mission",
        history_policy="actor_recurrent_state_only",
    )


class _MemoryEnv:
    def __init__(self) -> None:
        self.observation = {
            "image": [[[1, 2, 0] for _ in range(7)] for _ in range(7)],
            "direction": 0,
            "mission": MISSION,
        }

    def reset(
        self, *, seed: int, options: Mapping[str, object] | None
    ) -> tuple[object, Mapping[str, object]]:
        del seed, options
        return self.observation, {}

    def step(
        self, action: object
    ) -> tuple[object, float, bool, bool, Mapping[str, object]]:
        del action
        return self.observation, 0.0, True, False, {"correct_exit": False}

    def close(self) -> None:
        return None


def test_minigrid_memory_preserves_partial_observation_mission_and_exit_semantics() -> (
    None
):
    backend = MiniGridMemoryBackend(manifest=_manifest(), environment=_MemoryEnv())

    observation, _ = backend.reset(
        seed_contract=IntegerResetSeedContract(kind="integer_reset", seed=19),
        options={},
    )
    next_observation, reward, terminated, truncated, info = backend.step(2)

    assert observation == next_observation == _MemoryEnv().observation
    assert (reward, terminated, truncated, info) == (
        0.0,
        True,
        False,
        {"correct_exit": False},
    )


def test_minigrid_memory_rejects_full_observation_or_history_leakage() -> None:
    environment = _MemoryEnv()
    backend = MiniGridMemoryBackend(manifest=_manifest(), environment=environment)

    environment.observation = {
        "image": [[[1, 2, 0] for _ in range(7)] for _ in range(7)],
        "direction": 0,
        "mission": MISSION,
        "full_grid": [[[1, 2, 0]]],
    }
    with pytest.raises(
        ValueError, match="exactly preserve image, direction, and mission"
    ):
        backend.reset(
            seed_contract=IntegerResetSeedContract(kind="integer_reset", seed=19),
            options={},
        )

    environment.observation = {
        "image": [[[1, 2, 0] for _ in range(7)] for _ in range(7)],
        "direction": 0,
        "mission": MISSION,
        "previous_cue": "key",
    }
    with pytest.raises(
        ValueError, match="exactly preserve image, direction, and mission"
    ):
        backend.reset(
            seed_contract=IntegerResetSeedContract(kind="integer_reset", seed=19),
            options={},
        )
