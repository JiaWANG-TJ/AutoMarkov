from __future__ import annotations

from types import MappingProxyType

import pytest

from automarkov.decision_process import (
    CARTPOLE_GYMNASIUM_PROVENANCE,
    MDPSpec,
    load_official_gymnasium_spec,
    validate_decision_process_json,
    validate_decision_process_payload,
)


def test_cartpole_allowlisted_fixture_is_strict_deeply_frozen_mdp() -> None:
    spec = load_official_gymnasium_spec()

    assert isinstance(spec, MDPSpec)
    assert spec.kind == "MDP"
    assert spec.agent_id == "agent"
    assert spec.state_is_observation is True
    assert type(spec.state_variables) is tuple
    assert type(spec.actions_by_agent) is MappingProxyType
    assert spec.actions_by_agent["agent"][0].domain.values == ("left", "right")
    assert spec.termination_predicates == (
        (
            "after_state_update: cart_position < -2.4 or cart_position > 2.4 "
            "or pole_angle_rad < -0.20943951023931953 or "
            "pole_angle_rad > 0.20943951023931953"
        ),
    )
    assert spec.truncation_predicates == ("elapsed_steps >= 500",)
    assert CARTPOLE_GYMNASIUM_PROVENANCE["gymnasium_version"] == "1.2.2"
    assert CARTPOLE_GYMNASIUM_PROVENANCE["upstream_commit"] == (
        "a923da5d4415a1aa5195d99341069da5e16deed7"
    )
    assert CARTPOLE_GYMNASIUM_PROVENANCE["wheel_sha256"] == (
        "f04ec362b1fdf73a8b327db5ef89384a3f2ba411e05d3521513414fbbb2199c8"
    )


def test_decision_process_ingress_copies_caller_containers_and_rejects_models() -> None:
    raw = load_official_gymnasium_spec().model_dump(mode="json", round_trip=True)
    parsed = validate_decision_process_payload(raw)
    raw["state_variables"][0]["name"] = "mutated"

    assert parsed.state_variables[0].name == "cart_position"
    with pytest.raises(ValueError, match="raw JSON object"):
        validate_decision_process_payload(parsed)
    with pytest.raises(ValueError, match="exact bytes"):
        validate_decision_process_json(bytearray(b"{}"))  # type: ignore[arg-type]
