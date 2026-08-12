from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from automarkov.domain import RunId, Sha256Digest, VerifiedEventHead
from automarkov.environment_contracts import (
    EnvironmentCandidateBundle,
    EnvironmentSandboxRequest,
    ImplementationPlan,
    ImplementationRoute,
    RuntimeImageStatus,
    RuntimeProfileResolution,
    SandboxLaunchReport,
    SandboxLaunchRequest,
    SandboxLimits,
    SandboxPolicy,
)
from automarkov.environment_sandbox import EnvironmentSandbox
from automarkov.lifecycle import ArtifactReference
from automarkov.provenance import RuntimeProfileId


def _ref(name: str, digit: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=f"artifact_{sha256(name.encode()).hexdigest()}",
        payload_hash=f"sha256:{digit * 64}",
    )


def _head() -> VerifiedEventHead:
    return VerifiedEventHead(
        run_id=RunId(root="run_environment_slice"),
        sequence_no=11,
        event_hash=Sha256Digest(root="sha256:" + "f" * 64),
    )


def _policy(route: ImplementationRoute = "reuse") -> SandboxPolicy:
    route_capability = {
        "reuse": (),
        "compose": ("registered_wrapper_compose",),
        "generate": ("registered_candidate_load",),
    }[route]
    return SandboxPolicy(
        schema_version="automarkov.environment-sandbox-policy.v1",
        policy_id=f"policy_cartpole_{route}",
        route=route,
        allowed_capabilities=tuple(
            sorted(
                (
                    "artifact_read",
                    "artifact_write",
                    "registered_environment_close",
                    "registered_environment_load",
                    "registered_environment_reset",
                    "registered_environment_step",
                    *route_capability,
                )
            )
        ),
        denied_capabilities=(
            "dynamic_import",
            "generation",
            "network",
            "pickle",
            "sealed_evaluation",
            "shell",
            "subprocess",
        ),
        network_access=False,
        sealed_access=False,
        pickle_allowed=False,
        shell_allowed=False,
        subprocess_allowed=False,
        dynamic_import_allowed=False,
    )


def _limits() -> SandboxLimits:
    return SandboxLimits(
        schema_version="automarkov.environment-sandbox-limits.v1",
        wall_time_seconds=30,
        cpu_time_seconds=20,
        memory_bytes=536_870_912,
        output_bytes=1_048_576,
        open_files=32,
        processes=1,
    )


def _plan(route: ImplementationRoute = "reuse") -> ImplementationPlan:
    return ImplementationPlan(
        schema_version="automarkov.implementation-plan.v1",
        route_request_id="route_request_cartpole",
        suite_id="suite_cartpole",
        task_contract=_ref("task", "1"),
        decision_process_spec=_ref("spec", "2"),
        classification_result=_ref("classification", "3"),
        signed_suite_manifest=_ref("suite", "4"),
        implementation_catalog_hash="sha256:" + "a" * 64,
        route=route,
        candidate_id="candidate_gymnasium_cartpole_v1",
        environment_id="CartPole-v1",
        backend="gymnasium",
        runtime_profile_id="rllib-core",
        wrappers=(),
        official_provenance=_ref("gymnasium_provenance", "5"),
    )


def _bundle(
    plan_ref: ArtifactReference, route: ImplementationRoute = "reuse"
) -> EnvironmentCandidateBundle:
    return EnvironmentCandidateBundle(
        schema_version="automarkov.environment-candidate-bundle.v1",
        implementation_plan=plan_ref,
        candidate_id="candidate_gymnasium_cartpole_v1",
        route=route,
        environment_id="CartPole-v1",
        backend="gymnasium",
        package_name="gymnasium",
        package_version="1.2.2",
        upstream_commit="a923da5d4415a1aa5195d99341069da5e16deed7",
        distribution_hash=(
            "sha256:f04ec362b1fdf73a8b327db5ef89384a3f2ba411e05d3521513414fbbb2199c8"
        ),
        runtime_profile_id="rllib-core",
        wrappers=(),
        materialized_files_hash="sha256:" + "b" * 64,
        official_provenance=_ref("gymnasium_provenance", "5"),
    )


class _Repository:
    def __init__(self, values: dict[str, BaseModel]) -> None:
        self._values = values
        self.verified_heads: list[VerifiedEventHead] = []

    def verify_event_head(self, head: VerifiedEventHead) -> None:
        self.verified_heads.append(head)

    def load(
        self, reference: ArtifactReference, model_type: type[BaseModel]
    ) -> BaseModel:
        value = self._values[reference.artifact_id]
        if type(value) is not model_type:
            raise ValueError("artifact payload type mismatch")
        return value


class _Resolver:
    def __init__(self, profile: RuntimeProfileResolution) -> None:
        self.profile = profile
        self.resolved_heads: list[VerifiedEventHead] = []

    def resolve(
        self, profile_id: RuntimeProfileId, head: VerifiedEventHead
    ) -> RuntimeProfileResolution:
        assert profile_id == "rllib-core"
        self.resolved_heads.append(head)
        return self.profile


class _Launcher:
    def __init__(self) -> None:
        self.calls = 0

    def launch(self, request: SandboxLaunchRequest) -> SandboxLaunchReport:
        self.calls += 1
        assert not hasattr(request, "import_path")
        assert not hasattr(request, "locator")
        assert request.network_access is False
        assert request.environment_id == "CartPole-v1"
        return SandboxLaunchReport(
            schema_version="automarkov.sandbox-launch-report.v1",
            status="success",
            started_at="2026-08-12T12:00:00Z",
            finished_at="2026-08-12T12:00:01Z",
            stdout_hash="sha256:" + "1" * 64,
            stderr_hash="sha256:" + "2" * 64,
            network_log_hash="sha256:" + "3" * 64,
            mount_attestation_hash="sha256:" + "4" * 64,
            capability_decision_hash="sha256:" + "5" * 64,
            egress_log_hash="sha256:" + "6" * 64,
            frame_schema_hash="sha256:" + "7" * 64,
        )


def _profile(image_status: RuntimeImageStatus) -> RuntimeProfileResolution:
    built = image_status == "built"
    return RuntimeProfileResolution(
        schema_version="automarkov.runtime-profile-resolution.v1",
        profile_id="rllib-core",
        profile_manifest=_ref("profile", "8"),
        lock_hash="sha256:" + "9" * 64,
        image_status=image_status,
        image_digest="sha256:" + "a" * 64 if built else None,
        platform="linux/amd64" if built else None,
        build_attestation=_ref("build_attestation", "b") if built else None,
        import_smoke_attestation=_ref("import_attestation", "c") if built else None,
    )


def _sandbox(
    *,
    profile_status: RuntimeImageStatus = "built",
    route: ImplementationRoute = "reuse",
) -> tuple[EnvironmentSandbox, _Launcher, EnvironmentSandboxRequest, _Repository]:
    plan_ref = _ref("plan", "d")
    bundle_ref = _ref("bundle", "e")
    policy_ref = _ref("policy", "f")
    limits_ref = _ref("limits", "0")
    launcher = _Launcher()
    repository = _Repository(
        {
            plan_ref.artifact_id: _plan(route),
            bundle_ref.artifact_id: _bundle(plan_ref, route),
            policy_ref.artifact_id: _policy(route),
            limits_ref.artifact_id: _limits(),
        }
    )
    sandbox = EnvironmentSandbox(
        repository=repository,
        profile_resolver=_Resolver(_profile(profile_status)),
        launcher=launcher,
        clock=cast(Callable[[], str], lambda: "2026-08-12T12:00:02Z"),
    )
    request = EnvironmentSandboxRequest(
        schema_version="automarkov.environment-sandbox-request.v1",
        specified_event_head=_head(),
        implementation_plan=plan_ref,
        candidate_bundle=bundle_ref,
        sandbox_policy=policy_ref,
        sandbox_limits=limits_ref,
        fixed_commit_job_manifest=_ref("job", "1"),
    )
    return sandbox, launcher, request, repository


def test_sandbox_request_requires_a_caller_specified_verified_event_head() -> None:
    raw = _sandbox()[2].model_dump(mode="json", round_trip=True, warnings="error")
    del raw["specified_event_head"]

    with pytest.raises(ValidationError, match="specified_event_head"):
        EnvironmentSandboxRequest.model_validate(raw, strict=True)


def test_sandbox_verifies_the_caller_specified_event_head_before_resolution() -> None:
    sandbox, launcher, request, repository = _sandbox()

    sandbox.run(request)

    assert repository.verified_heads == [request.specified_event_head]
    assert launcher.calls == 0


def test_self_reported_built_profile_stays_waiting_without_verified_runtime_chain() -> (
    None
):
    sandbox, launcher, request, _ = _sandbox()

    result = sandbox.run(request)

    assert result.readiness_state == "WAITING_RUNTIME"
    assert result.environment_binding is None
    assert result.failure_code == "runtime_provenance_unverified"
    assert launcher.calls == 0


def test_recipe_frozen_profile_fails_closed_to_waiting_runtime_without_launch() -> None:
    sandbox, launcher, request, _ = _sandbox(profile_status="recipe_frozen")

    result = sandbox.run(request)

    assert result.readiness_state == "WAITING_RUNTIME"
    assert result.environment_binding is None
    assert result.failure_code == "runtime_profile_unavailable"
    assert launcher.calls == 0


def test_compose_route_can_execute_only_its_registered_wrapper_bundle() -> None:
    sandbox, launcher, request, _ = _sandbox(route="compose")

    result = sandbox.run(request)

    assert result.readiness_state == "WAITING_RUNTIME"
    assert result.failure_code == "runtime_provenance_unverified"
    assert launcher.calls == 0


def test_reuse_policy_cannot_enable_generation_network_pickle_or_dynamic_import() -> (
    None
):
    payload = _policy().model_dump(mode="json", round_trip=True, warnings="error")
    payload["network_access"] = True
    with pytest.raises(ValidationError):
        SandboxPolicy.model_validate(payload, strict=True)

    payload = _policy().model_dump(mode="json", round_trip=True, warnings="error")
    payload["allowed_capabilities"] = sorted(
        [*payload["allowed_capabilities"], "generation"]
    )
    with pytest.raises(ValidationError, match="allowlist|overlap"):
        SandboxPolicy.model_validate(payload, strict=True)


def test_compose_policy_requires_its_exact_registered_wrapper_capability() -> None:
    payload = _policy().model_dump(mode="json", round_trip=True, warnings="error")
    payload["policy_id"] = "policy_minigrid_compose"
    payload["route"] = "compose"
    payload["allowed_capabilities"] = sorted(
        [*payload["allowed_capabilities"], "registered_wrapper_compose"]
    )

    policy = SandboxPolicy.model_validate(payload, strict=True)
    assert policy.route == "compose"

    payload["allowed_capabilities"] = sorted(
        [*payload["allowed_capabilities"], "unregistered_capability"]
    )
    with pytest.raises(ValidationError, match="Compose.*allowlist"):
        SandboxPolicy.model_validate(payload, strict=True)


def test_generate_policy_requires_only_registered_candidate_loading() -> None:
    payload = _policy().model_dump(mode="json", round_trip=True, warnings="error")
    payload["policy_id"] = "policy_taxi_generate"
    payload["route"] = "generate"
    payload["allowed_capabilities"] = sorted(
        [*payload["allowed_capabilities"], "registered_candidate_load"]
    )

    policy = SandboxPolicy.model_validate(payload, strict=True)
    assert policy.route == "generate"

    payload["allowed_capabilities"] = sorted(
        [*payload["allowed_capabilities"], "generation"]
    )
    with pytest.raises(ValidationError, match="overlap|Generate.*allowlist"):
        SandboxPolicy.model_validate(payload, strict=True)
