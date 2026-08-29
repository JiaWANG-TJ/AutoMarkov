"""Stage 15: package the environment candidate into a distributable bundle.

Includes: environment code reference, dependency manifest, test report,
and a deterministic content hash for integrity verification.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from automarkov.application._common import StageResult, _now_iso
from automarkov.contracts.environment import (
    EnvironmentCandidate,
    EnvironmentCandidateBundle,
    ImplementationRoute,
    SandboxLimits,
    SandboxPolicy,
)
from automarkov.domain.canonical import canonical_json_bytes
from automarkov.domain.models import StrictFrozenModel

# ---------------------------------------------------------------------------
# Package models
# ---------------------------------------------------------------------------

class DependencySpec(StrictFrozenModel):
    """A single dependency specification."""
    package_name: str
    package_version: str
    constraint: str  # "==", ">=", etc.
    source: Literal["pypi", "git", "local", "official"]


class DependencyManifest(StrictFrozenModel):
    """The full dependency manifest for the package."""
    schema_version: Literal["compile.dependency-manifest.v1"]
    manifest_id: str
    dependencies: tuple[DependencySpec, ...]
    python_version: str
    platform: Literal["linux/amd64"]


class PackageArtifact(StrictFrozenModel):
    """The packaged artifact metadata."""
    schema_version: Literal["compile.package-artifact.v1"]
    package_id: str
    route: ImplementationRoute
    environment_candidate_id: str
    dependency_manifest: DependencyManifest
    test_report_id: str
    tests_passed: bool
    build_profile_id: str
    build_hash: str
    materialized_content_hash: str
    packaged_at: str


class PackageBundle(StrictFrozenModel):
    """The complete package bundle containing all artifacts."""
    schema_version: Literal["compile.package-bundle.v1"]
    bundle_id: str
    package: PackageArtifact
    environment_candidate: EnvironmentCandidate
    candidate_bundle: EnvironmentCandidateBundle
    sandbox_policy: SandboxPolicy
    sandbox_limits: SandboxLimits
    content_hash: str
    assembled_at: str


# ---------------------------------------------------------------------------
# Stage models
# ---------------------------------------------------------------------------

class PackageCandidateInput(StrictFrozenModel):
    schema_version: Literal["compile.package-candidate-input.v1"]
    route: ImplementationRoute
    environment_candidate: EnvironmentCandidate
    candidate_bundle: EnvironmentCandidateBundle
    sandbox_policy: SandboxPolicy
    sandbox_limits: SandboxLimits
    test_report_ref: object
    tests_passed: bool
    manifest_ref: object


class PackageCandidateOutput(StrictFrozenModel):
    schema_version: Literal["compile.package-candidate-output.v1"]
    package_bundle: PackageBundle
    package_hash: str


# ---------------------------------------------------------------------------
# Dependency resolution by route
# ---------------------------------------------------------------------------

def _resolve_dependencies(
    route: ImplementationRoute,
    candidate: EnvironmentCandidate,
) -> list[DependencySpec]:
    """Resolve the dependency list based on route and backend."""
    deps: list[DependencySpec] = []

    if route == "reuse":
        deps.append(DependencySpec(
            package_name=candidate.package_name,
            package_version=candidate.package_version,
            constraint="==",
            source="official",
        ))
        if candidate.backend == "gymnasium":
            deps.append(DependencySpec(
                package_name="gymnasium",
                package_version="1.2.2",
                constraint=">=",
                source="pypi",
            ))
        elif candidate.backend == "pettingzoo":
            deps.append(DependencySpec(
                package_name="pettingzoo",
                package_version="1.25.0",
                constraint=">=",
                source="pypi",
            ))

    elif route == "compose":
        deps.append(DependencySpec(
            package_name=candidate.package_name,
            package_version=candidate.package_version,
            constraint=">=",
            source="pypi",
        ))
        if "pettingzoo_wrapper" in candidate.wrappers:
            deps.append(DependencySpec(
                package_name="pettingzoo",
                package_version="1.25.0",
                constraint=">=",
                source="pypi",
            ))
        if "rllib_multi_agent_wrapper" in candidate.wrappers:
            deps.append(DependencySpec(
                package_name="ray",
                package_version="2.40.0",
                constraint=">=",
                source="pypi",
            ))

    elif route == "generate":
        deps.append(DependencySpec(
            package_name=candidate.package_name,
            package_version=candidate.package_version,
            constraint="==",
            source="local",
        ))
        deps.append(DependencySpec(
            package_name="gymnasium",
            package_version="1.2.2",
            constraint=">=",
            source="pypi",
        ))

    # Common runtime dependencies
    deps.append(DependencySpec(
        package_name="numpy",
        package_version="1.26.0",
        constraint=">=",
        source="pypi",
    ))
    deps.append(DependencySpec(
        package_name="ray",
        package_version="2.40.0",
        constraint=">=",
        source="pypi",
    ))

    return deps


_BACKEND_PYTHON_VERSIONS: dict[str, str] = {
    "gymnasium": ">=3.10",
    "pettingzoo": ">=3.9",
}


# ---------------------------------------------------------------------------
# Stage function
# ---------------------------------------------------------------------------

def package_candidate_stage(
    inp: PackageCandidateInput,
    *,
    recovery_head: object | None = None,
) -> StageResult:
    """Stage 15: package the environment candidate into a distributable bundle.

    Steps:
    1. Resolve dependency manifest from route and candidate
    2. Build PackageArtifact with integrity hash
    3. Assemble PackageBundle with all artifact references
    4. Calculate deterministic content hash for the full bundle
    """
    candidate = inp.candidate_bundle
    route = inp.route

    deps = _resolve_dependencies(route, inp.environment_candidate)

    # Compute manifest id deterministically
    manifest_raw = canonical_json_bytes({
        "deps": [(d.package_name, d.package_version) for d in deps],
    })
    manifest_id = f"dep_{sha256(manifest_raw).hexdigest()[:16]}"

    dep_manifest = DependencyManifest(
        schema_version="compile.dependency-manifest.v1",
        manifest_id=manifest_id,
        dependencies=tuple(deps),
        python_version=_BACKEND_PYTHON_VERSIONS.get(
            inp.environment_candidate.backend, ">=3.10"
        ),
        platform="linux/amd64",
    )

    # Extract test_report_id from the test_report_ref
    test_report_id = "tr_unknown"
    if isinstance(inp.test_report_ref, dict):
        test_report_id = inp.test_report_ref.get("report_id", test_report_id)
    elif hasattr(inp.test_report_ref, "report_id"):
        test_report_id = inp.test_report_ref.report_id  # type: ignore[union-attr]

    # Build package id deterministically
    pkg_raw = canonical_json_bytes({
        "route": route,
        "candidate_id": candidate.candidate_id,
        "env": candidate.environment_id,
        "deps": manifest_id,
    })
    package_id = f"pkg_{sha256(pkg_raw).hexdigest()[:16]}"

    # Materialized content hash: in a real impl, this would be the hash of
    # the actual environment code/weights. Here it's derived from the spec.
    content_raw = canonical_json_bytes({
        "route": route,
        "candidate": candidate.candidate_id,
        "env_id": candidate.environment_id,
        "wrappers": tuple(candidate.wrappers),
        "deps": manifest_id,
    })
    materialized_hash = f"sha256:{sha256(content_raw).hexdigest()}"

    # Build hash: hash of build environment + source + time
    build_raw = canonical_json_bytes({
        "profile": "rllib-core",
        "content": materialized_hash,
        "deps": manifest_id,
    })
    build_hash = f"sha256:{sha256(build_raw).hexdigest()}"

    now = _now_iso()
    package = PackageArtifact(
        schema_version="compile.package-artifact.v1",
        package_id=package_id,
        route=route,
        environment_candidate_id=candidate.candidate_id,
        dependency_manifest=dep_manifest,
        test_report_id=test_report_id,
        tests_passed=inp.tests_passed,
        build_profile_id="rllib-core",
        build_hash=build_hash,
        materialized_content_hash=materialized_hash,
        packaged_at=now,
    )

    # Full bundle content hash
    bundle_content_raw = canonical_json_bytes({
        "package": package.package_id,
        "candidate": candidate.candidate_id,
        "env": candidate.environment_id,
        "route": route,
        "sandbox": inp.sandbox_policy.policy_id,
    })
    content_hash = f"sha256:{sha256(bundle_content_raw).hexdigest()}"

    bundle_id = f"bdl_{sha256(bundle_content_raw).hexdigest()[:16]}"

    package_bundle = PackageBundle(
        schema_version="compile.package-bundle.v1",
        bundle_id=bundle_id,
        package=package,
        environment_candidate=inp.environment_candidate,
        candidate_bundle=inp.candidate_bundle,
        sandbox_policy=inp.sandbox_policy,
        sandbox_limits=inp.sandbox_limits,
        content_hash=content_hash,
        assembled_at=now,
    )

    return StageResult(
        stage="package_candidate",
        status="ok",
        output_ref=PackageCandidateOutput(
            schema_version="compile.package-candidate-output.v1",
            package_bundle=package_bundle,
            package_hash=content_hash,
        ),
        failure_code=None,
        recovery_status="ok",
        event_refs=(),
        budget_consumed_ref=None,
    )
