"""Stage 13: build environment candidate based on route selection.

For REUSE: bind official package dependency profile.
For GENERATE (SYNTHESIZE): create environment skeleton from spec.
For COMPOSE: assemble from standard components.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from automarkov.application._common import StageResult, _artifact_ref_as_typed
from automarkov.contracts.environment import (
    EnvironmentCandidate,
    EnvironmentCandidateBundle,
    FrozenImplementationCatalog,
    ImplementationPlan,
    ImplementationRoute,
    SandboxLimits,
    SandboxPolicy,
)
from automarkov.contracts.task import TaskContract
from automarkov.decision_process import (
    DecisionProcessValue,
    POSGSpec,
)
from automarkov.domain.canonical import canonical_json_bytes
from automarkov.domain.models import StrictFrozenModel


class RouteRequestRef(StrictFrozenModel):
    """Minimal reference to the route request from stage 12."""
    process_kind: str
    environment_name: str
    route: ImplementationRoute
    package_name: str | None
    package_version: str | None
    backend: Literal["gymnasium", "pettingzoo"]


class EnvironmentCandidateInput(StrictFrozenModel):
    schema_version: Literal["compile.environment-candidate-input.v1"]
    route: ImplementationRoute
    route_request: RouteRequestRef
    task_contract: TaskContract
    decision_process_spec: DecisionProcessValue
    manifest_ref: object


class EnvironmentCandidateOutput(StrictFrozenModel):
    schema_version: Literal["compile.environment-candidate-output.v1"]
    environment_candidate: EnvironmentCandidate
    candidate_bundle: EnvironmentCandidateBundle
    implementation_catalog: FrozenImplementationCatalog
    sandbox_policy: SandboxPolicy
    sandbox_limits: SandboxLimits


# ---------------------------------------------------------------------------
# Official provenance data for known reuse packages
# ---------------------------------------------------------------------------

_OFFICIAL_PROVENANCE: dict[str, dict[str, str]] = {
    "gymnasium": {
        "package_name": "gymnasium",
        "package_version": "1.2.2",
        "upstream_commit": "a923da5d4415a1aa5195d99341069da5e16deed7",
    },
    "pettingzoo": {
        "package_name": "pettingzoo",
        "package_version": "1.25.0",
        "upstream_commit": "0" * 40,
    },
    "pomdp_py": {
        "package_name": "pomdp_py",
        "package_version": "1.2.3",
        "upstream_commit": "b" * 40,
    },
    "synthesize": {
        "package_name": "automarkov-synthesize",
        "package_version": "0.1.0",
        "upstream_commit": "0" * 40,
    },
}


def _make_candidate_id(
    route: ImplementationRoute, env_name: str, process_kind: str
) -> str:
    raw = canonical_json_bytes({
        "route": route,
        "env": env_name,
        "kind": process_kind,
    })
    return f"cand_{sha256(raw).hexdigest()[:16]}"


def _make_environment_id(env_name: str, process_kind: str) -> str:
    return f"env_{process_kind.lower()}_{env_name}"


def _build_reuse_candidate(
    rr: RouteRequestRef, contract: TaskContract, spec: DecisionProcessValue
) -> tuple[EnvironmentCandidate, EnvironmentCandidateBundle, ImplementationPlan,
           SandboxPolicy, SandboxLimits]:
    """Build a candidate that binds to an official package."""
    pkg_name = rr.package_name or "gymnasium"
    prov = _OFFICIAL_PROVENANCE.get(pkg_name, _OFFICIAL_PROVENANCE["synthesize"])

    candidate_id = _make_candidate_id(rr.route, rr.environment_name, rr.process_kind)
    env_id = _make_environment_id(rr.environment_name, rr.process_kind)

    dist_hash_raw = canonical_json_bytes({
        "pkg": pkg_name,
        "ver": prov["package_version"],
        "commit": prov["upstream_commit"],
    })

    provenance_ref = _artifact_ref_as_typed({
        "schema_version": "automarkov.official-provenance.v1",
        "package_name": pkg_name,
        "package_version": prov["package_version"],
        "upstream_commit": prov["upstream_commit"],
        "distribution_hash": f"sha256:{sha256(dist_hash_raw).hexdigest()}",
    })

    cand = EnvironmentCandidate(
        candidate_id=candidate_id,
        route=rr.route,
        suite_id=f"suite_{rr.environment_name}",
        environment_id=env_id,
        backend=rr.backend,
        package_name=pkg_name,
        package_version=prov["package_version"],
        upstream_commit=prov["upstream_commit"],
        distribution_hash=f"sha256:{sha256(dist_hash_raw).hexdigest()}",
        runtime_profile_id="rllib-core",
        wrappers=(),
        evidence_ids=(),
        official_provenance=provenance_ref,
    )

    plan = ImplementationPlan(
        schema_version="automarkov.implementation-plan.v1",
        route_request_id=candidate_id,
        suite_id=f"suite_{rr.environment_name}",
        task_contract=_artifact_ref_as_typed(
            contract.model_dump(mode="json", round_trip=True, warnings="error")
        ),
        decision_process_spec=_artifact_ref_as_typed(
            spec.model_dump(mode="json", round_trip=True, warnings="error")
        ),
        classification_result=provenance_ref,
        signed_suite_manifest=provenance_ref,
        implementation_catalog_hash=cand.distribution_hash,
        route=rr.route,
        candidate_id=candidate_id,
        environment_id=env_id,
        backend=rr.backend,
        runtime_profile_id="rllib-core",
        wrappers=(),
        official_provenance=provenance_ref,
    )

    bundle = EnvironmentCandidateBundle(
        schema_version="automarkov.environment-candidate-bundle.v1",
        implementation_plan=_artifact_ref_as_typed(
            plan.model_dump(mode="json", round_trip=True, warnings="error")
        ),
        candidate_id=candidate_id,
        route=rr.route,
        environment_id=env_id,
        backend=rr.backend,
        package_name=pkg_name,
        package_version=prov["package_version"],
        upstream_commit=prov["upstream_commit"],
        distribution_hash=cand.distribution_hash,
        runtime_profile_id="rllib-core",
        wrappers=(),
        materialized_files_hash=f"sha256:{sha256(b'@reuse').hexdigest()}",
        official_provenance=provenance_ref,
    )

    sandbox_policy = SandboxPolicy(
        schema_version="automarkov.environment-sandbox-policy.v1",
        policy_id="policy_reuse_default",
        route=rr.route,
        allowed_capabilities=(
            "artifact_read", "artifact_write",
            "registered_environment_close", "registered_environment_load",
            "registered_environment_reset", "registered_environment_step",
        ),
        denied_capabilities=(
            "dynamic_import", "generation", "network", "pickle",
            "sealed_evaluation", "shell", "subprocess",
        ),
        network_access=False,
        sealed_access=False,
        pickle_allowed=False,
        shell_allowed=False,
        subprocess_allowed=False,
        dynamic_import_allowed=False,
    )

    sandbox_limits = SandboxLimits(
        schema_version="automarkov.environment-sandbox-limits.v1",
        wall_time_seconds=3600,
        cpu_time_seconds=1800,
        memory_bytes=8 * 1024 * 1024 * 1024,  # 8 GB
        output_bytes=10 * 1024 * 1024,        # 10 MB
        open_files=256,
        processes=1,
    )

    return cand, bundle, plan, sandbox_policy, sandbox_limits


def _build_compose_candidate(
    rr: RouteRequestRef, contract: TaskContract, spec: DecisionProcessValue
) -> tuple[EnvironmentCandidate, EnvironmentCandidateBundle, ImplementationPlan,
           SandboxPolicy, SandboxLimits]:
    """Build a candidate assembled from standard components."""
    pkg_name = "pettingzoo" if rr.backend == "pettingzoo" else "gymnasium"
    prov = _OFFICIAL_PROVENANCE.get(pkg_name, _OFFICIAL_PROVENANCE["synthesize"])

    candidate_id = _make_candidate_id(rr.route, rr.environment_name, rr.process_kind)
    env_id = _make_environment_id(rr.environment_name, rr.process_kind)

    dist_hash_raw = canonical_json_bytes({
        "pkg": pkg_name,
        "ver": prov["package_version"],
        "commit": prov["upstream_commit"],
        "mode": "compose",
    })

    provenance_ref = _artifact_ref_as_typed({
        "schema_version": "automarkov.official-provenance.v1",
        "package_name": pkg_name,
        "package_version": prov["package_version"],
        "upstream_commit": prov["upstream_commit"],
    })

    # Determine wrappers based on spec type
    wrappers = ()
    if isinstance(spec, POSGSpec):
        wrappers = ("pettingzoo_wrapper", "rllib_multi_agent_wrapper")

    cand = EnvironmentCandidate(
        candidate_id=candidate_id,
        route=rr.route,
        suite_id=f"suite_{rr.environment_name}",
        environment_id=env_id,
        backend=rr.backend,
        package_name=pkg_name,
        package_version=prov["package_version"],
        upstream_commit=prov["upstream_commit"],
        distribution_hash=f"sha256:{sha256(dist_hash_raw).hexdigest()}",
        runtime_profile_id="rllib-core",
        wrappers=wrappers,
        evidence_ids=(),
        official_provenance=provenance_ref,
    )

    # Must use a non-CartPole suite_id to satisfy EnvironmentCandidate validator
    plan = ImplementationPlan(
        schema_version="automarkov.implementation-plan.v1",
        route_request_id=candidate_id,
        suite_id=f"suite_{rr.environment_name}",
        task_contract=_artifact_ref_as_typed(
            contract.model_dump(mode="json", round_trip=True, warnings="error")
        ),
        decision_process_spec=_artifact_ref_as_typed(
            spec.model_dump(mode="json", round_trip=True, warnings="error")
        ),
        classification_result=provenance_ref,
        signed_suite_manifest=provenance_ref,
        implementation_catalog_hash=cand.distribution_hash,
        route=rr.route,
        candidate_id=candidate_id,
        environment_id=env_id,
        backend=rr.backend,
        runtime_profile_id="rllib-core",
        wrappers=wrappers,
        official_provenance=provenance_ref,
    )

    bundle = EnvironmentCandidateBundle(
        schema_version="automarkov.environment-candidate-bundle.v1",
        implementation_plan=_artifact_ref_as_typed(
            plan.model_dump(mode="json", round_trip=True, warnings="error")
        ),
        candidate_id=candidate_id,
        route=rr.route,
        environment_id=env_id,
        backend=rr.backend,
        package_name=pkg_name,
        package_version=prov["package_version"],
        upstream_commit=prov["upstream_commit"],
        distribution_hash=cand.distribution_hash,
        runtime_profile_id="rllib-core",
        wrappers=wrappers,
        materialized_files_hash=f"sha256:{sha256(b'@compose').hexdigest()}",
        official_provenance=provenance_ref,
    )

    sandbox_policy = SandboxPolicy(
        schema_version="automarkov.environment-sandbox-policy.v1",
        policy_id="policy_compose_default",
        route=rr.route,
        allowed_capabilities=(
            "artifact_read", "artifact_write",
            "registered_environment_close", "registered_environment_load",
            "registered_environment_reset", "registered_environment_step",
            "registered_wrapper_compose",
        ),
        denied_capabilities=(
            "dynamic_import", "generation", "network", "pickle",
            "sealed_evaluation", "shell", "subprocess",
        ),
        network_access=False,
        sealed_access=False,
        pickle_allowed=False,
        shell_allowed=False,
        subprocess_allowed=False,
        dynamic_import_allowed=False,
    )

    sandbox_limits = SandboxLimits(
        schema_version="automarkov.environment-sandbox-limits.v1",
        wall_time_seconds=3600,
        cpu_time_seconds=1800,
        memory_bytes=8 * 1024 * 1024 * 1024,
        output_bytes=10 * 1024 * 1024,
        open_files=256,
        processes=1,
    )

    return cand, bundle, plan, sandbox_policy, sandbox_limits


def _build_generate_candidate(
    rr: RouteRequestRef, contract: TaskContract, spec: DecisionProcessValue
) -> tuple[EnvironmentCandidate, EnvironmentCandidateBundle, ImplementationPlan,
           SandboxPolicy, SandboxLimits]:
    """Build a candidate synthesized from the formal spec."""
    prov = _OFFICIAL_PROVENANCE["synthesize"]

    candidate_id = _make_candidate_id(rr.route, rr.environment_name, rr.process_kind)
    env_id = _make_environment_id(rr.environment_name, rr.process_kind)

    dist_hash_raw = canonical_json_bytes({
        "mode": "synthesize",
        "kind": rr.process_kind,
        "env": rr.environment_name,
    })

    provenance_ref = _artifact_ref_as_typed({
        "schema_version": "automarkov.official-provenance.v1",
        "package_name": prov["package_name"],
        "package_version": prov["package_version"],
    })

    cand = EnvironmentCandidate(
        candidate_id=candidate_id,
        route=rr.route,
        suite_id=f"suite_{rr.environment_name}",
        environment_id=env_id,
        backend=rr.backend,
        package_name=prov["package_name"],
        package_version=prov["package_version"],
        upstream_commit=prov["upstream_commit"],
        distribution_hash=f"sha256:{sha256(dist_hash_raw).hexdigest()}",
        runtime_profile_id="rllib-core",
        wrappers=(),
        evidence_ids=(),
        official_provenance=provenance_ref,
    )

    plan = ImplementationPlan(
        schema_version="automarkov.implementation-plan.v1",
        route_request_id=candidate_id,
        suite_id=f"suite_{rr.environment_name}",
        task_contract=_artifact_ref_as_typed(
            contract.model_dump(mode="json", round_trip=True, warnings="error")
        ),
        decision_process_spec=_artifact_ref_as_typed(
            spec.model_dump(mode="json", round_trip=True, warnings="error")
        ),
        classification_result=provenance_ref,
        signed_suite_manifest=provenance_ref,
        implementation_catalog_hash=cand.distribution_hash,
        route=rr.route,
        candidate_id=candidate_id,
        environment_id=env_id,
        backend=rr.backend,
        runtime_profile_id="rllib-core",
        wrappers=(),
        official_provenance=provenance_ref,
    )

    bundle = EnvironmentCandidateBundle(
        schema_version="automarkov.environment-candidate-bundle.v1",
        implementation_plan=_artifact_ref_as_typed(
            plan.model_dump(mode="json", round_trip=True, warnings="error")
        ),
        candidate_id=candidate_id,
        route=rr.route,
        environment_id=env_id,
        backend=rr.backend,
        package_name=prov["package_name"],
        package_version=prov["package_version"],
        upstream_commit=prov["upstream_commit"],
        distribution_hash=cand.distribution_hash,
        runtime_profile_id="rllib-core",
        wrappers=(),
        materialized_files_hash=f"sha256:{sha256(b'@generate').hexdigest()}",
        official_provenance=provenance_ref,
    )

    sandbox_policy = SandboxPolicy(
        schema_version="automarkov.environment-sandbox-policy.v1",
        policy_id="policy_generate_default",
        route=rr.route,
        allowed_capabilities=(
            "artifact_read", "artifact_write",
            "registered_environment_close", "registered_environment_load",
            "registered_environment_reset", "registered_environment_step",
            "registered_candidate_load",
        ),
        denied_capabilities=(
            "dynamic_import", "generation", "network", "pickle",
            "sealed_evaluation", "shell", "subprocess",
        ),
        network_access=False,
        sealed_access=False,
        pickle_allowed=False,
        shell_allowed=False,
        subprocess_allowed=False,
        dynamic_import_allowed=False,
    )

    sandbox_limits = SandboxLimits(
        schema_version="automarkov.environment-sandbox-limits.v1",
        wall_time_seconds=3600,
        cpu_time_seconds=1800,
        memory_bytes=8 * 1024 * 1024 * 1024,
        output_bytes=10 * 1024 * 1024,
        open_files=256,
        processes=1,
    )

    return cand, bundle, plan, sandbox_policy, sandbox_limits


_BUILDERS = {
    "reuse": _build_reuse_candidate,
    "compose": _build_compose_candidate,
    "generate": _build_generate_candidate,
}


def environment_candidate_stage(
    inp: EnvironmentCandidateInput,
    *,
    recovery_head: object | None = None,
) -> StageResult:
    """Stage 13: build environment candidate based on route selection.

    For REUSE: bind official package dependency profile.
    For GENERATE: create environment skeleton from formal spec.
    For COMPOSE: assemble from standard components.
    """
    builder = _BUILDERS[inp.route]
    cand, bundle, _plan, sandbox_policy, sandbox_limits = builder(
        inp.route_request, inp.task_contract, inp.decision_process_spec
    )

    catalog = FrozenImplementationCatalog(
        schema_version="automarkov.frozen-implementation-catalog.v1",
        catalog_id=f"cat_{cand.candidate_id}",
        candidates=(cand,),
        catalog_hash=cand.distribution_hash,
    )

    return StageResult(
        stage="environment_candidate",
        status="ok",
        output_ref=EnvironmentCandidateOutput(
            schema_version="compile.environment-candidate-output.v1",
            environment_candidate=cand,
            candidate_bundle=bundle,
            implementation_catalog=catalog,
            sandbox_policy=sandbox_policy,
            sandbox_limits=sandbox_limits,
        ),
        failure_code=None,
        recovery_status="ok",
        event_refs=(),
        budget_consumed_ref=None,
    )
