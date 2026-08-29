from __future__ import annotations

from hashlib import sha256

import pytest
from pydantic import ValidationError

from automarkov.contracts.multi_agent import (
    CityLearnAdapterManifest,
    CityLearnEnergyBalance,
    PettingZooKeysetAudit,
)
from automarkov.lifecycle import ArtifactReference
from automarkov.multi_agent_suite_adapters import CityLearnSuiteAdapter


def _ref(name: str, digit: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=f"artifact_{sha256(name.encode()).hexdigest()}",
        payload_hash=f"sha256:{digit * 64}",
    )


def _manifest() -> CityLearnAdapterManifest:
    return CityLearnAdapterManifest.model_validate(
        {
            "schema_version": "automarkov.citylearn-adapter-manifest.v1",
            "suite_id": "citylearn_posg",
            "environment_binding": _ref("binding", "1").model_dump(),
            "runtime_profile_manifest": _ref("profile", "2").model_dump(),
            "official_provenance": _ref("citylearn", "3").model_dump(),
            "protocol_version": "automarkov.remote-env.v1",
            "space_contract_hash": "sha256:" + "5" * 64,
            "adapter_source_hash": "sha256:" + "6" * 64,
            "runtime_profile_id": "env-citylearn",
            "package_version": "2.5.0",
            "upstream_commit": "29062af6d077409e1c37a3e53a6cac30fd4d02bc",
            "environment_id": "CityLearnEnv",
            "challenge_schema": _ref("citylearn-challenge-schema", "4").model_dump(),
            "challenge_schema_hash": "sha256:" + "4" * 64,
            "agents": [
                {
                    "agent_id": "building_0",
                    "observation_names": [
                        "hour",
                        "net_electricity_consumption",
                        "outdoor_dry_bulb_temperature",
                    ],
                    "action_names": ["electrical_storage"],
                },
                {
                    "agent_id": "building_1",
                    "observation_names": [
                        "hour",
                        "net_electricity_consumption",
                        "outdoor_dry_bulb_temperature",
                    ],
                    "action_names": ["electrical_storage"],
                },
            ],
            "forbidden_future_observation_names": [
                "carbon_intensity_predicted_1",
                "electricity_pricing_predicted_1",
                "non_shiftable_load_predicted_1",
                "outdoor_dry_bulb_temperature_predicted_1",
                "solar_generation_predicted_1",
            ],
            "evaluation_period": {"start_time_step": 0, "end_time_step": 2},
            "parallel_api": True,
            "aec_conversion": "pettingzoo.parallel_to_aec",
        },
        strict=True,
    )


class _CityLearnBackend:
    central_agent = False
    challenge_schema_hash = "sha256:" + "4" * 64
    observation_names = (
        (
            "hour",
            "net_electricity_consumption",
            "outdoor_dry_bulb_temperature",
        ),
        (
            "hour",
            "net_electricity_consumption",
            "outdoor_dry_bulb_temperature",
        ),
    )

    def reset(
        self, *, seed: int, options: dict[str, object]
    ) -> tuple[tuple[tuple[float, ...], ...], dict[str, object]]:
        assert seed == 11
        assert options == {}
        return ((1.0, 2.0, 3.0), (4.0, 5.0, 6.0)), {
            "phase": "reset",
            "time_step": 0,
        }

    def step(
        self, actions: list[list[float]]
    ) -> tuple[
        tuple[tuple[float, ...], ...],
        tuple[float, ...],
        bool,
        bool,
        dict[str, object],
        dict[str, CityLearnEnergyBalance],
    ]:
        assert actions == [[0.25], [-0.5]]
        return (
            ((2.0, 3.0, 4.0), (5.0, 6.0, 7.0)),
            (-1.0, -2.0),
            False,
            False,
            {"phase": "step", "time_step": 1},
            {
                "building_0": _energy(),
                "building_1": _energy().model_copy(update={"agent_id": "building_1"}),
            },
        )


def _energy(net: float = 8.0) -> CityLearnEnergyBalance:
    return CityLearnEnergyBalance(
        agent_id="building_0",
        net_electricity_consumption_kwh=net,
        cooling_electricity_consumption_kwh=1.0,
        heating_electricity_consumption_kwh=1.0,
        dhw_electricity_consumption_kwh=1.0,
        non_shiftable_load_electricity_consumption_kwh=4.0,
        electrical_storage_electricity_consumption_kwh=1.0,
        electrical_storage_charge_power_kw=1.0,
        electrical_storage_discharge_power_kw=0.0,
        electrical_storage_max_charge_power_kw=2.0,
        electrical_storage_max_discharge_power_kw=2.0,
        step_duration_hours=1.0,
        solar_generation_kwh=-1.0,
        charger_electricity_consumption_kwh=0.5,
        washing_machine_electricity_consumption_kwh=0.5,
        power_outage=False,
        state_of_charge_kwh=2.0,
        storage_capacity_kwh=4.0,
        electricity_price_per_kwh=0.2,
        carbon_intensity_kg_per_kwh=0.4,
        electricity_cost=1.6,
        carbon_emissions_kg=3.2,
    )


def test_citylearn_parallel_adapter_preserves_exact_agent_keysets() -> None:
    adapter = CityLearnSuiteAdapter(
        backend=_CityLearnBackend(),
        manifest=_manifest(),
    )

    reset = adapter.reset(seed=11)
    transition = adapter.step({"building_0": (0.25,), "building_1": (-0.5,)})

    assert reset.agent_ids == ("building_0", "building_1")
    assert reset.observation("building_1") == (4.0, 5.0, 6.0)
    assert transition.agent_ids == reset.agent_ids
    assert transition.reward("building_0") == -1.0
    assert transition.termination("building_0") is False
    assert transition.truncation("building_1") is False
    assert (
        tuple(item.agent_id for item in transition.energy_balances) == reset.agent_ids
    )

    audit = PettingZooKeysetAudit(
        possible_agents=reset.agent_ids,
        parallel_observation_keys=transition.agent_ids,
        parallel_reward_keys=transition.agent_ids,
        parallel_termination_keys=transition.agent_ids,
        parallel_truncation_keys=transition.agent_ids,
        parallel_info_keys=transition.agent_ids,
        aec_agents=reset.agent_ids,
        active_aec_agent="building_0",
    )
    assert audit.active_aec_agent == "building_0"


def test_citylearn_manifest_rejects_future_observation_leakage() -> None:
    raw = _manifest().model_dump(mode="json", round_trip=True, warnings="error")
    raw["agents"][0]["observation_names"].append(
        "outdoor_dry_bulb_temperature_predicted_1"
    )

    with pytest.raises(ValidationError, match="future"):
        CityLearnAdapterManifest.model_validate(raw, strict=True)

    backend = _CityLearnBackend()
    backend.observation_names = (  # type: ignore[assignment]
        (*backend.observation_names[0], "electricity_pricing_predicted_1"),
        backend.observation_names[1],
    )
    with pytest.raises(ValueError, match="observation schema"):
        CityLearnSuiteAdapter(
            backend=backend,
            manifest=_manifest(),
        )


def test_citylearn_rejects_observation_vector_length_drift() -> None:
    backend = _CityLearnBackend()
    backend.reset = lambda **_: (  # type: ignore[method-assign]
        ((1.0, 2.0), (4.0, 5.0, 6.0)),
        {"phase": "reset"},
    )
    adapter = CityLearnSuiteAdapter(
        backend=backend,
        manifest=_manifest(),
    )

    with pytest.raises(ValueError, match="observation shape"):
        adapter.reset(seed=11)


@pytest.mark.parametrize(
    "hostile_info",
    [
        {"outdoor_temperature_forecast": 20.0},
        {"next_price": 0.3},
        {"future_load": 10.0},
        {"phase": object()},
        {"unknown": "value"},
    ],
)
def test_citylearn_info_is_closed_typed_and_future_safe(
    hostile_info: dict[str, object],
) -> None:
    backend = _CityLearnBackend()
    backend.reset = lambda **_: (  # type: ignore[method-assign]
        ((1.0, 2.0, 3.0), (4.0, 5.0, 6.0)),
        hostile_info,
    )
    adapter = CityLearnSuiteAdapter(
        backend=backend,
        manifest=_manifest(),
    )

    with pytest.raises(ValueError, match="CityLearn info"):
        adapter.reset(seed=11)


def test_citylearn_info_is_sanitized_to_closed_entries() -> None:
    adapter = CityLearnSuiteAdapter(
        backend=_CityLearnBackend(),
        manifest=_manifest(),
    )

    reset = adapter.reset(seed=11)

    assert tuple(item.model_dump(mode="json") for item in reset.infos[0].info) == (
        {"key": "phase", "value": "reset"},
        {"key": "time_step", "value": 0},
    )


def test_citylearn_energy_balance_and_soc_fail_closed() -> None:
    with pytest.raises(ValidationError, match="energy conservation"):
        _energy(net=8.1)

    raw = _energy().model_dump(mode="json", round_trip=True, warnings="error")
    raw["state_of_charge_kwh"] = 5.0
    with pytest.raises(ValidationError, match="state of charge"):
        CityLearnEnergyBalance.model_validate(raw, strict=True)

    raw = _energy().model_dump(mode="json", round_trip=True, warnings="error")
    raw["electrical_storage_discharge_power_kw"] = 0.5
    with pytest.raises(ValidationError, match="charge and discharge"):
        CityLearnEnergyBalance.model_validate(raw, strict=True)

    raw = _energy().model_dump(mode="json", round_trip=True, warnings="error")
    raw["electricity_cost"] = 1.61
    with pytest.raises(ValidationError, match="cost or carbon"):
        CityLearnEnergyBalance.model_validate(raw, strict=True)


def test_citylearn_schema_and_central_future_policy_are_not_self_reported() -> None:
    raw = _manifest().model_dump(mode="json", round_trip=True, warnings="error")
    raw["challenge_schema_hash"] = "sha256:" + "5" * 64
    with pytest.raises(ValidationError, match="challenge schema"):
        CityLearnAdapterManifest.model_validate(raw, strict=True)

    raw = _manifest().model_dump(mode="json", round_trip=True, warnings="error")
    raw["forbidden_future_observation_names"] = ["electricity_pricing_predicted_1"]
    with pytest.raises(ValidationError, match="central future"):
        CityLearnAdapterManifest.model_validate(raw, strict=True)

    raw = _manifest().model_dump(mode="json", round_trip=True, warnings="error")
    raw["agents"][0]["observation_names"].append("carbon_intensity_forecast_6h")
    with pytest.raises(ValidationError, match="future"):
        CityLearnAdapterManifest.model_validate(raw, strict=True)


def test_citylearn_energy_snapshot_is_bound_to_the_same_backend_step() -> None:
    backend = _CityLearnBackend()
    backend.step = lambda _: (  # type: ignore[method-assign]
        ((2.0, 3.0, 4.0), (5.0, 6.0, 7.0)),
        (-1.0, -2.0),
        False,
        False,
        {"phase": "step", "time_step": 1},
        {"building_0": _energy()},
    )
    adapter = CityLearnSuiteAdapter(backend=backend, manifest=_manifest())
    adapter.reset(seed=11)

    with pytest.raises(ValueError, match="energy-balance keyset"):
        adapter.step({"building_0": (0.25,), "building_1": (-0.5,)})


def test_citylearn_keyset_audit_rejects_parallel_or_aec_drift() -> None:
    keys = ("building_0", "building_1")
    with pytest.raises(ValidationError, match="keysets"):
        PettingZooKeysetAudit(
            possible_agents=keys,
            parallel_observation_keys=("building_0",),
            parallel_reward_keys=keys,
            parallel_termination_keys=keys,
            parallel_truncation_keys=keys,
            parallel_info_keys=keys,
            aec_agents=keys,
            active_aec_agent="building_0",
        )

    with pytest.raises(ValidationError, match="AEC"):
        PettingZooKeysetAudit(
            possible_agents=keys,
            parallel_observation_keys=keys,
            parallel_reward_keys=keys,
            parallel_termination_keys=keys,
            parallel_truncation_keys=keys,
            parallel_info_keys=keys,
            aec_agents=keys,
            active_aec_agent="building_2",
        )
