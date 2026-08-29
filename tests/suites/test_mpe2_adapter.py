from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from automarkov.lifecycle import ArtifactReference
from automarkov.multi_agent_suite_adapters import (
    Mpe2NativeActorSuiteAdapter,
    Mpe2SuiteAdapter,
    build_multi_agent_suite_profile_contract,
    formal_multi_agent_suite_readiness,
)
from automarkov.security.provenance import RuntimeProfileManifest

ROOT = Path(__file__).resolve().parents[2]
from automarkov.contracts.multi_agent import (
    Mpe2AdapterManifest,
    suite_adapter_parent_references,
)


def _ref(name: str, digit: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=f"artifact_{sha256(name.encode()).hexdigest()}",
        payload_hash=f"sha256:{digit * 64}",
    )


def _manifest(condition: str) -> Mpe2AdapterManifest:
    return Mpe2AdapterManifest.model_validate(
        {
            "schema_version": "automarkov.mpe2-adapter-manifest.v1",
            "suite_id": (
                "mpe2_full_state_mg"
                if condition == "full_state"
                else "mpe2_native_local_posg"
            ),
            "condition": condition,
            "environment_binding": _ref("binding", "1").model_dump(),
            "runtime_profile_manifest": _ref("profile", "2").model_dump(),
            "official_provenance": _ref("mpe2", "3").model_dump(),
            "protocol_version": "automarkov.remote-env.v1",
            "space_contract_hash": "sha256:" + "4" * 64,
            "adapter_source_hash": "sha256:" + "5" * 64,
            "runtime_profile_id": "env-mpe2",
            "package_version": "1.1.0",
            "upstream_commit": "7590d9d52791e321974d4fda6090fb18f34dbf49",
            "environment_id": "simple_spread_v3",
            "possible_agents": ["agent_0", "agent_1", "agent_2"],
            "local_observation_dimension": 18,
            "actor_input_dimension": 54,
            "information_policy": {
                "policy_id": "mpe2_shared_information_policy_v1",
                "actor_source": (
                    "official_state"
                    if condition == "full_state"
                    else "native_local_observation"
                ),
                "critic_source": "official_state",
                "fixed_actor_input_dimension": 54,
                "local_observation_dimension": 18,
            },
        },
        strict=True,
    )


class _Mpe2Backend:
    possible_agents = ("agent_0", "agent_1", "agent_2")

    def __init__(self) -> None:
        self.state_calls = 0

    def state(self) -> tuple[float, ...]:
        self.state_calls += 1
        return tuple(float(index) for index in range(54))


def _local_observations() -> dict[str, tuple[float, ...]]:
    return {
        agent_id: tuple(float(agent_index * 100 + offset) for offset in range(18))
        for agent_index, agent_id in enumerate(("agent_0", "agent_1", "agent_2"))
    }


def test_full_state_actor_inputs_reuse_official_state_element_for_element() -> None:
    backend = _Mpe2Backend()
    adapter = Mpe2SuiteAdapter(backend=backend, manifest=_manifest("full_state"))

    batch = adapter.actor_inputs(_local_observations())

    expected = tuple(float(index) for index in range(54))
    assert backend.state_calls == 1
    assert tuple(item.agent_id for item in batch.inputs) == backend.possible_agents
    assert all(item.values == expected for item in batch.inputs)
    assert all(item.feature_availability_mask == (True,) * 54 for item in batch.inputs)
    assert all(item.source == "official_state" for item in batch.inputs)


def test_native_actor_never_calls_state_and_critic_uses_a_separate_seam() -> None:
    backend = _Mpe2Backend()
    adapter = Mpe2NativeActorSuiteAdapter(
        backend=backend,
        manifest=_manifest("native_local"),
    )

    batch = adapter.actor_inputs(_local_observations())

    assert backend.state_calls == 0
    assert batch.inputs[1].values == _local_observations()["agent_1"] + (0.0,) * 36
    assert batch.inputs[1].feature_availability_mask == (True,) * 18 + (False,) * 36
    assert batch.inputs[1].source == "native_local_observation"

    assert not hasattr(adapter, "critic_state")
    assert not hasattr(adapter, "_backend")
    assert "critic_state" not in batch.model_dump(mode="json")
    assert backend.state_calls == 0


def test_mpe2_information_policy_is_shared_except_for_actor_capability() -> None:
    full = _manifest("full_state").information_policy
    native = _manifest("native_local").information_policy

    assert full.model_copy(update={"actor_source": native.actor_source}) == native
    assert full.critic_source == native.critic_source == "official_state"

    raw = _manifest("native_local").model_dump(
        mode="json", round_trip=True, warnings="error"
    )
    raw["information_policy"]["actor_source"] = "official_state"
    with pytest.raises(ValidationError, match="actor information"):
        Mpe2AdapterManifest.model_validate(raw, strict=True)


def test_mpe2_manifest_has_a_closed_direct_parent_dag() -> None:
    manifest = _manifest("full_state")

    assert suite_adapter_parent_references(manifest) == tuple(
        sorted(
            (
                manifest.environment_binding,
                manifest.official_provenance,
                manifest.runtime_profile_manifest,
            ),
            key=lambda item: item.artifact_id.encode("utf-8"),
        )
    )


def test_mpe2_adapter_rejects_agent_keyset_or_state_shape_drift() -> None:
    backend = _Mpe2Backend()
    adapter = Mpe2SuiteAdapter(backend=backend, manifest=_manifest("full_state"))
    observations = _local_observations()
    observations.pop("agent_2")

    with pytest.raises(ValueError, match="keyset"):
        adapter.actor_inputs(observations)

    backend.state = lambda: (0.0,) * 53  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="54"):
        adapter.actor_inputs(_local_observations())


def test_mpe2_suite_identity_and_actor_critic_capability_are_separate() -> None:
    native = _manifest("native_local").model_dump(
        mode="json", round_trip=True, warnings="error"
    )
    native["suite_id"] = "mpe2_full_state_mg"
    with pytest.raises(ValidationError, match="suite identity"):
        Mpe2AdapterManifest.model_validate(native, strict=True)

    full = _manifest("full_state").model_dump(
        mode="json", round_trip=True, warnings="error"
    )
    full["suite_id"] = "mpe2_native_local_posg"
    with pytest.raises(ValidationError, match="suite identity"):
        Mpe2AdapterManifest.model_validate(full, strict=True)

    with pytest.raises(ValueError, match="full-state"):
        Mpe2SuiteAdapter(backend=_Mpe2Backend(), manifest=_manifest("native_local"))


def test_formal_multi_agent_execution_fails_closed_until_runtime_and_runner_exist() -> (
    None
):
    manifest = _manifest("full_state")

    frozen = formal_multi_agent_suite_readiness(manifest, image_status="recipe_frozen")
    built = formal_multi_agent_suite_readiness(manifest, image_status="built")

    assert (frozen.state, frozen.reason_code) == (
        "WAITING_RUNTIME",
        "runtime_profile_unavailable",
    )
    assert (built.state, built.reason_code) == (
        "WAITING_RUNTIME",
        "fixed_commit_remote_env_unavailable",
    )


def test_mpe2_production_profile_contract_binds_official_runtime_identity() -> None:
    profile = RuntimeProfileManifest.model_validate(
        json.loads((ROOT / "profiles/env-mpe2/profile.json").read_text()),
        strict=True,
    )
    profile_ref = ArtifactReference(
        artifact_id=f"artifact_{profile.manifest_hash.removeprefix('sha256:')}",
        payload_hash=profile.manifest_hash,
    )
    manifest = _manifest("full_state").model_copy(
        update={"runtime_profile_manifest": profile_ref}
    )
    contract = build_multi_agent_suite_profile_contract(manifest, profile=profile)

    assert contract.expectation.package_identities[1].package_name == "mpe2"
    assert contract.expectation.framework_contract == (
        "pettingzoo.parallel.remote-env.rllib-multi-agent.v1"
    )

    drifted = profile.model_copy(
        update={"package_versions": {**dict(profile.package_versions), "mpe2": "1.2.0"}}
    )
    with pytest.raises(ValueError, match="package/version/commit"):
        build_multi_agent_suite_profile_contract(manifest, profile=drifted)
