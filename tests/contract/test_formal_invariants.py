from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

import pytest
from pydantic import ValidationError

from automarkov.decision_process import (
    MDPSpec,
    MGSpec,
    POMDPSpec,
    POSGSpec,
    decision_process_codec,
    decision_process_json_schema,
    load_official_gymnasium_spec,
    validate_decision_process_payload,
)
from automarkov.domain.canonical import canonical_json_bytes, parse_canonical_document


def _mdp() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        load_official_gymnasium_spec().model_dump(mode="json", round_trip=True),
    )


def _variable(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "domain": {
            "kind": "categorical",
            "values": ["low", "high"],
            "ordered": False,
        },
        "unit": None,
        "semantic_definition": f"Observable variable {name}.",
        "evidence_ids": ["fixture"],
    }


def _history(*, message_lags: list[int] | None = None) -> dict[str, Any]:
    return {
        "observation_lags": [0],
        "action_lags": [],
        "reward_lags": [],
        "message_lags": [] if message_lags is None else message_lags,
        "recurrent_state_allowed": False,
        "boundary_reset": "episode",
    }


def _pomdp() -> dict[str, Any]:
    raw = _mdp()
    raw["kind"] = "POMDP"
    raw.pop("state_is_observation")
    raw["observation_space"] = [_variable("sensor")]
    raw["observation_kernel"] = "sensor ~ O(. | next_state)"
    raw["history_access"] = _history()
    raw["message_processes_by_recipient"] = {"agent": []}
    return raw


def _mg() -> dict[str, Any]:
    raw = _mdp()
    raw["kind"] = "MG"
    raw.pop("agent_id")
    raw.pop("state_is_observation")
    reward = raw.pop("reward")
    raw["agent_ids"] = ["agent_a", "agent_b"]
    raw["actions_by_agent"] = {
        "agent_a": [_variable("action_a")],
        "agent_b": [_variable("action_b")],
    }
    raw["objectives"][0]["owner_ids"] = ["agent_a", "agent_b"]
    state_names = [item["name"] for item in raw["state_variables"]]
    raw["full_state_access_by_agent"] = {
        "agent_a": state_names,
        "agent_b": state_names,
    }
    raw["joint_action_kernel"] = "simultaneous product action kernel"
    raw["rewards_by_agent"] = {"agent_a": reward, "agent_b": deepcopy(reward)}
    raw["joint_reward_dependencies"] = []
    raw["game_form"] = "cooperative"
    raw["solution_concept"] = "team-optimal policy"
    raw["action_timing"] = "simultaneous"
    raw["aec_turn"] = None
    return raw


def _posg() -> dict[str, Any]:
    raw = _mg()
    raw["kind"] = "POSG"
    raw.pop("full_state_access_by_agent")
    raw["joint_observation"] = {
        "joint_space": [_variable("sensor_a"), _variable("sensor_b")],
        "kernel": "joint observation conditional on next state",
        "conditional_on": ["next_state"],
        "per_agent_projection": {
            "agent_a": ["sensor_a"],
            "agent_b": ["sensor_b"],
        },
        "cross_agent_correlations": [],
    }
    raw["history_access_by_agent"] = {
        "agent_a": _history(),
        "agent_b": _history(),
    }
    raw["message_processes_by_recipient"] = {
        "agent_a": [],
        "agent_b": [],
    }
    raw["centralized_training_fields"] = [
        {"field_kind": "state", "variable_name": "cart_velocity"}
    ]
    return raw


@pytest.mark.parametrize(
    ("fixture", "expected_type"),
    [(_pomdp, POMDPSpec), (_mg, MGSpec), (_posg, POSGSpec)],
)
def test_complete_union_accepts_each_non_mdp_branch(fixture, expected_type) -> None:
    assert isinstance(validate_decision_process_payload(fixture()), expected_type)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: raw.update({"kind": "UNKNOWN"}),
        lambda raw: raw.update({"schema_version": "v2"}),
        lambda raw: raw.update({"state_is_observation": False}),
        lambda raw: raw.update({"agent_ids": ["agent"]}),
        lambda raw: raw.update({"discount": 1}),
    ],
)
def test_mdp_rejects_unknown_branch_mixing_and_scalar_coercion(mutation) -> None:
    raw = _mdp()
    mutation(raw)
    with pytest.raises(ValidationError):
        validate_decision_process_payload(raw)


def test_pomdp_closes_message_recipient_and_history_contract() -> None:
    raw = _pomdp()
    raw["history_access"]["message_lags"] = [0]

    with pytest.raises(ValidationError, match="message lags exist exactly"):
        validate_decision_process_payload(raw)


def test_mg_closes_agent_keysets_full_state_and_aec_contract() -> None:
    raw = _mg()
    raw["full_state_access_by_agent"]["agent_a"] = ["cart_position"]
    with pytest.raises(ValidationError, match="full-state variable"):
        validate_decision_process_payload(raw)

    raw = _mg()
    raw["action_timing"] = "aec"
    with pytest.raises(ValidationError, match="requires an AEC"):
        validate_decision_process_payload(raw)


def test_posg_closes_joint_observation_and_centralized_leakage() -> None:
    raw = _posg()
    raw["centralized_training_fields"] = [
        {
            "field_kind": "observation",
            "agent_id": "agent_a",
            "variable_name": "sensor_a",
        }
    ]

    with pytest.raises(ValidationError, match="overlap actor inputs"):
        validate_decision_process_payload(raw)


def test_decision_process_schema_is_one_closed_four_branch_union() -> None:
    schema = cast(dict[str, Any], decision_process_json_schema())

    assert schema["discriminator"]["propertyName"] == "kind"
    assert set(schema["discriminator"]["mapping"]) == {"MDP", "POMDP", "MG", "POSG"}
    assert len(schema["oneOf"]) == 4
    seen_kind_constants: set[str] = set()
    for reference in schema["oneOf"]:
        branch = schema["$defs"][reference["$ref"].removeprefix("#/$defs/")]
        assert branch["additionalProperties"] is False
        assert "kind" in branch["required"]
        assert branch["properties"]["schema_version"]["const"] == (
            "automarkov.decision-process-spec.v1"
        )
        seen_kind_constants.add(branch["properties"]["kind"]["const"])
    assert seen_kind_constants == {"MDP", "POMDP", "MG", "POSG"}

    mdp_schema = schema["$defs"]["MDPSpec"]
    assert mdp_schema["properties"]["discount"]["minimum"] == 0.0
    assert mdp_schema["properties"]["discount"]["maximum"] == 1.0
    assert schema["$defs"]["FixedDimension"]["properties"]["size"]["minimum"] == 1
    lag_schema_ref = schema["$defs"]["HistoryAccessSpec"]["properties"]["action_lags"][
        "$ref"
    ]
    lag_schema = schema["$defs"][lag_schema_ref.removeprefix("#/$defs/")]
    assert lag_schema["items"]["minimum"] == 0


@pytest.mark.parametrize("fixture", [_mdp, _pomdp, _mg, _posg])
def test_canonical_union_round_trip_is_byte_identical(fixture) -> None:
    encoded = decision_process_codec.encode(fixture())
    restored = decision_process_codec.decode(encoded)

    assert (
        decision_process_codec.encode(restored.model_dump(mode="json", round_trip=True))
        == encoded
    )


def test_canonical_union_rejects_exact_float_map_tampering() -> None:
    encoded = decision_process_codec.encode(_mdp())
    document = cast(dict[str, Any], parse_canonical_document(encoded))
    assert type(document) is dict
    document["exact_float_paths"].pop()

    with pytest.raises(ValueError, match="exact-float map"):
        decision_process_codec.decode(canonical_json_bytes(document))


# ── Negative / defensive tests ──────────────────────────────────────


def _mutate(raw: dict[str, Any], **kw: object) -> dict[str, Any]:
    clone = cast(dict[str, Any], deepcopy(raw))
    clone.update(kw)
    return clone


class TestMDPNegativeInvariants:
    """Deceptive counter-examples that must be rejected by MDP contracts."""

    def test_empty_state_variables(self) -> None:
        raw = _mutate(_mdp(), state_variables=[])
        with pytest.raises(ValidationError, match="nonempty"):
            validate_decision_process_payload(raw)

    def test_duplicate_state_variable_names(self) -> None:
        raw = _mdp()
        dup_var = raw["state_variables"][0].copy()
        raw["state_variables"] = [dup_var, dup_var]
        with pytest.raises(ValidationError, match="unique"):
            validate_decision_process_payload(raw)

    def test_empty_actions_by_agent(self) -> None:
        raw = _mutate(_mdp(), actions_by_agent={})
        with pytest.raises(ValidationError, match="action mapping"):
            validate_decision_process_payload(raw)

    def test_unknown_kind_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            validate_decision_process_payload({"kind": "NOVEL_MDP", "schema_version": "v0"})


class TestPOMDPNegativeInvariants:
    """POMDP-specific negative contract tests."""

    def test_duplicate_observation_names(self) -> None:
        raw = _pomdp()
        raw["observation_space"] = [_variable("dup"), _variable("dup")]
        with pytest.raises(ValidationError, match="nonempty and unique"):
            validate_decision_process_payload(raw)

    def test_missing_message_recipient(self) -> None:
        raw = _pomdp()
        raw["message_processes_by_recipient"] = {}
        with pytest.raises(ValidationError):
            validate_decision_process_payload(raw)


class TestMultiAgentNegativeInvariants:
    """MG/POSG negative contract tests."""

    def test_duplicate_agent_ids(self) -> None:
        raw = _mg()
        raw["agent_ids"] = ["agent_a", "agent_a"]
        with pytest.raises(ValidationError, match="unique"):
            validate_decision_process_payload(raw)

    def test_mg_inverted_player_bounds(self) -> None:
        raw = _mg()
        raw["actions_by_agent"]["agent_a"] = [_variable("act_a")]
        raw["actions_by_agent"]["agent_b"] = [_variable("act_b")]
        raw["full_state_access_by_agent"]["agent_a"] = [
            v["name"] for v in raw["state_variables"]
        ]
        raw["full_state_access_by_agent"]["agent_b"] = [
            v["name"] for v in raw["state_variables"]
        ]
        raw["rewards_by_agent"]["agent_a"] = {
            "mode": "deterministic", "expression": "1.0"
        }
        raw["rewards_by_agent"]["agent_b"] = {
            "mode": "deterministic", "expression": "1.0"
        }
        raw["agent_ids"] = ["only_one"]
        with pytest.raises(ValidationError):
            validate_decision_process_payload(raw)


class TestDiscountBoundary:
    """Boundary value tests for discount parameter."""

    def test_zero_discount_mdp_is_valid(self) -> None:
        raw = _mdp()
        raw["discount"] = 0.0
        result = validate_decision_process_payload(raw)
        assert isinstance(result, MDPSpec)

    def test_unit_discount_rejected_as_max(self) -> None:
        raw = _mdp()
        raw["objectives"] = [{"kind": "M",
                            "aggregation": "discounted_sum",
                            "expression": "x", "weight": 1.0}]
        raw["discount"] = 1.0
        raw["horizon"] = "infinite"
        with pytest.raises(ValidationError):
            validate_decision_process_payload(raw)
