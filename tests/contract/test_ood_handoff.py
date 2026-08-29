from __future__ import annotations

from inspect import signature

import pytest
from pydantic import ValidationError

from automarkov.contracts.classification import (
    OpenSpielRoute,
    evaluate_ood_runtime_readiness,
    validate_ood_handoff_payload,
)


def _ref(digit: str) -> dict[str, str]:
    return {
        "artifact_id": f"artifact_{digit * 64}",
        "payload_hash": f"sha256:{digit * 64}",
    }


def _base(route: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "automarkov.ood-handoff.v1",
        "handoff_kind": "ood_handoff",
        "source_task_ref": _ref("1"),
        "classification_ref": _ref("2"),
        "authority_refs": [_ref("3")],
        "classification_reason": "The task is a classical planning problem.",
        "traceability": ["goal -> PDDL goal"],
        "assumptions": ["The world is deterministic."],
        "required_inputs": ["domain.pddl", "problem.pddl"],
        "required_outputs": ["validated plan"],
        "route": route,
    }


def test_generic_ood_route_can_only_claim_referral_capability() -> None:
    handoff = validate_ood_handoff_payload(
        _base(
            {
                "route_kind": "GENERIC_REFERRAL",
                "capability": "referral_only",
                "recommended_backend": "mathematical_programming",
                "unsupported_features": ["integer solver execution"],
                "license_and_asset_requirements": ["recipient selects solver"],
                "recipient_acceptance_checks": ["confirm solver license"],
            }
        )
    )
    assert handoff.route.capability == "referral_only"
    assert evaluate_ood_runtime_readiness(handoff).status == "READY"


def test_openspiel_route_is_typed_but_waits_for_its_isolated_runtime() -> None:
    payload = _base(
        {
            "route_kind": "OPEN_SPIEL",
            "capability": "executable",
            "upstream_provenance_ref": _ref("4"),
            "runtime_profile_ref": _ref("5"),
            "players": ["row", "column"],
            "dynamics": "simultaneous",
            "chance_mode": "deterministic",
            "information_model": "perfect_information",
            "utility_type": "zero_sum",
            "reward_model": "terminal",
            "min_players": 2,
            "max_players": 2,
            "selected_game_or_adapter": "matrix_rps",
            "requested_algorithms": ["nash"],
            "metric": "exploitability",
        }
    )
    handoff = validate_ood_handoff_payload(payload)
    assert isinstance(handoff.route, OpenSpielRoute)

    waiting = evaluate_ood_runtime_readiness(handoff)
    assert waiting.status == "WAITING"
    assert waiting.required_profile_ref is not None
    assert waiting.required_profile_ref.model_dump(
        mode="json"
    ) == handoff.route.runtime_profile_ref.model_dump(mode="json")
    assert (
        "available_profile_artifact_ids"
        not in signature(evaluate_ood_runtime_readiness).parameters
    )


def test_pddl_route_cannot_claim_executable_without_provenance_and_profile() -> None:
    route = {
        "route_kind": "PDDL",
        "capability": "executable",
        "domain_source_ref": _ref("4"),
        "problem_source_ref": _ref("5"),
        "upstream_provenance_ref": _ref("6"),
        "runtime_profile_ref": _ref("7"),
        "requirements": [":strips", ":typing"],
        "objects_and_types": ["truck - vehicle"],
        "fluents": ["at(truck, location)"],
        "actions": ["drive"],
        "goals": ["at(truck, depot)"],
        "metrics": ["minimize total-time"],
        "selected_compiler_kinds": ["GROUNDING"],
        "planner_engine": "aries",
        "unsupported_features": ["continuous effects"],
    }
    assert validate_ood_handoff_payload(_base(route)).route.route_kind == "PDDL"

    del route["runtime_profile_ref"]
    with pytest.raises((ValueError, ValidationError)):
        validate_ood_handoff_payload(_base(route))
