"""Stage 12: map DecisionProcessKind and task_contract to an ImplementationRoute.

Route mapping:
  - MDP   → REUSE (if official package available) or SYNTHESIZE
  - POMDP → REUSE (if official package available) or COMPOSE
  - MG    → REUSE (if official package available) or COMPOSE
  - POSG  → REUSE (if official package available) or COMPOSE
"""

from __future__ import annotations

from typing import Literal

from automarkov.application._common import StageResult
from automarkov.contracts.environment import ImplementationRoute
from automarkov.contracts.task import TaskContract
from automarkov.decision_process import (
    DecisionProcessValue,
    MDPSpec,
    MGSpec,
    POMDPSpec,
    POSGSpec,
)
from automarkov.domain.models import StrictFrozenModel

# ---------------------------------------------------------------------------
# Known official environments that can be reused directly
# ---------------------------------------------------------------------------

_KNOWN_REUSE_ENVIRONMENTS: dict[str, frozenset[str]] = {
    "MDP": frozenset({
        "CartPole-v1", "MountainCar-v0", "Acrobot-v1",
        "LunarLander-v3", "FrozenLake-v1",
    }),
    "POMDP": frozenset({
        "Tiger-v0", "RockSampling-v0",
    }),
    "MG": frozenset({
        "simple_tag", "simple_spread", "simple_adversary",
    }),
    "POSG": frozenset({
        "simple_tag", "simple_spread", "simple_reference",
    }),
}


def _lookup_package_info(
    kind: str, env_name: str
) -> tuple[str, str, str] | None:
    """Return (package_name, package_version, upstream_commit) or None."""
    if kind == "MDP" and env_name in _KNOWN_REUSE_ENVIRONMENTS["MDP"]:
        return ("gymnasium", "1.2.2", "a923da5d4415a1aa5195d99341069da5e16deed7")
    if kind == "POMDP" and env_name in _KNOWN_REUSE_ENVIRONMENTS["POMDP"]:
        return ("pomdp_py", "1.2.3", "b" * 40)
    if kind in ("MG", "POSG") and env_name in _KNOWN_REUSE_ENVIRONMENTS[kind]:
        return ("pettingzoo", "1.25.0", "c" * 40)
    return None


class RouteRequest(StrictFrozenModel):
    """Deterministic route-request artifact recording the routing decision."""
    schema_version: Literal["compile.route-request.v1"]
    process_kind: str
    environment_name: str
    route: ImplementationRoute
    package_name: str | None
    package_version: str | None
    backend: Literal["gymnasium", "pettingzoo"]
    rationale: str


class SelectRouteInput(StrictFrozenModel):
    schema_version: Literal["compile.select-route-input.v1"]
    task_contract: TaskContract
    decision_process_spec: DecisionProcessValue
    manifest_ref: object


class SelectRouteOutput(StrictFrozenModel):
    schema_version: Literal["compile.select-route-output.v1"]
    route: ImplementationRoute
    route_request: RouteRequest


def _classify_route(
    spec: DecisionProcessValue, contract: TaskContract
) -> tuple[ImplementationRoute, str]:
    """Determine the implementation route and rationale."""
    kind = spec.kind
    env_name = contract.task_identity.name

    coord = contract.decision_structure.coordination
    n_makers = len(contract.decision_structure.decision_makers)

    # Check if a known official package exists for reuse
    pkg_info = _lookup_package_info(kind, env_name)

    if isinstance(spec, MDPSpec):
        if pkg_info is not None:
            return ("reuse", (
                f"MDP '{env_name}' matches known official package: "
                f"{pkg_info[0]}=={pkg_info[1]}"
            ))
        else:
            return ("generate", (
                f"MDP '{env_name}' not found in known packages; "
                "synthesize from spec"
            ))

    elif isinstance(spec, POMDPSpec):
        if pkg_info is not None:
            return ("reuse", (
                f"POMDP '{env_name}' matches known official package: "
                f"{pkg_info[0]}=={pkg_info[1]}"
            ))
        else:
            return ("compose", (
                f"POMDP '{env_name}' not found in known packages; "
                "compose from standard components"
            ))

    elif isinstance(spec, MGSpec):
        if pkg_info is not None:
            return ("reuse", (
                f"MG '{env_name}' matches known official package: "
                f"{pkg_info[0]}=={pkg_info[1]}"
            ))
        elif coord == "decentralized" and n_makers > 1:
            return ("compose", (
                f"MG '{env_name}' — multi-agent, compose from "
                "PettingZoo components"
            ))
        else:
            return ("compose", (
                f"MG '{env_name}' not found in known packages; "
                "compose from standard components"
            ))

    elif isinstance(spec, POSGSpec):
        if pkg_info is not None:
            return ("reuse", (
                f"POSG '{env_name}' matches known official package: "
                f"{pkg_info[0]}=={pkg_info[1]}"
            ))
        else:
            return ("compose", (
                f"POSG '{env_name}' not found in known packages; "
                "compose from standard components"
            ))

    # Fallback
    return ("generate", f"Unrecognized process kind '{kind}'; generate from spec")


def select_route_stage(
    inp: SelectRouteInput,
    *,
    recovery_head: object | None = None,
) -> StageResult:
    """Stage 12: map DecisionProcessKind + task_contract to ImplementationRoute.

    Determines whether to REUSE an official package, COMPOSE from standard
    components, or GENERATE/SYNTHESIZE from the formal spec.
    """
    spec = inp.decision_process_spec
    contract = inp.task_contract

    route, rationale = _classify_route(spec, contract)

    pkg_info = _lookup_package_info(spec.kind, contract.task_identity.name)
    package_name = pkg_info[0] if pkg_info else None
    package_version = pkg_info[1] if pkg_info else None

    if isinstance(spec, (MGSpec, POSGSpec)):
        backend: Literal["gymnasium", "pettingzoo"] = "pettingzoo"
    else:
        backend = "gymnasium"

    route_request = RouteRequest(
        schema_version="compile.route-request.v1",
        process_kind=spec.kind,
        environment_name=contract.task_identity.name,
        route=route,
        package_name=package_name,
        package_version=package_version,
        backend=backend,
        rationale=rationale,
    )

    return StageResult(
        stage="select_route",
        status="ok",
        output_ref=SelectRouteOutput(
            schema_version="compile.select-route-output.v1",
            route=route,
            route_request=route_request,
        ),
        failure_code=None,
        recovery_status="ok",
        event_refs=(),
        budget_consumed_ref=None,
    )
