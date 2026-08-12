from __future__ import annotations

import json
from hashlib import sha256

import pytest
from pydantic import ValidationError

from automarkov.adapters import (
    InMemoryArtifactRepository,
    InMemoryEnvironmentBinding,
    ScriptedExecutionSandbox,
)
from automarkov.domain import ArtifactId
from automarkov.environment_contracts import (
    EnvironmentBindingArtifact,
    EnvironmentCandidateBundle,
    ImplementationPlan,
    SandboxLimits,
    SandboxPolicy,
    candidate_bundle_parent_references,
    environment_binding_parent_references,
    implementation_plan_parent_references,
)
from automarkov.lifecycle import ArtifactReference
from automarkov.public import (
    EnvironmentRef,
    ExecutionResult,
    RuntimeProfileRef,
    SandboxRunRequest,
)


def _ref(name: str, digit: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=f"artifact_{sha256(name.encode()).hexdigest()}",
        payload_hash=f"sha256:{digit * 64}",
    )


def _plan() -> ImplementationPlan:
    return ImplementationPlan(
        schema_version="automarkov.implementation-plan.v1",
        route_request_id="route_request_cartpole",
        suite_id="suite_cartpole",
        task_contract=_ref("task", "1"),
        decision_process_spec=_ref("spec", "2"),
        classification_result=_ref("classification", "3"),
        signed_suite_manifest=_ref("suite", "4"),
        implementation_catalog_hash="sha256:" + "5" * 64,
        route="reuse",
        candidate_id="candidate_gymnasium_cartpole_v1",
        environment_id="CartPole-v1",
        backend="gymnasium",
        runtime_profile_id="rllib-core",
        wrappers=(),
        official_provenance=_ref("gymnasium_provenance", "6"),
    )


def _bundle() -> EnvironmentCandidateBundle:
    return EnvironmentCandidateBundle(
        schema_version="automarkov.environment-candidate-bundle.v1",
        implementation_plan=_ref("plan", "7"),
        candidate_id="candidate_gymnasium_cartpole_v1",
        route="reuse",
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
        materialized_files_hash="sha256:" + "8" * 64,
        official_provenance=_ref("gymnasium_provenance", "6"),
    )


def _binding() -> EnvironmentBindingArtifact:
    return EnvironmentBindingArtifact(
        schema_version="automarkov.environment-binding.v1",
        task_contract=_ref("task", "1"),
        decision_process_spec=_ref("spec", "2"),
        classification_result=_ref("classification", "3"),
        implementation_plan=_ref("plan", "7"),
        candidate_bundle=_ref("bundle", "8"),
        sandbox_policy=_ref("policy", "9"),
        sandbox_limits=_ref("limits", "a"),
        runtime_profile_manifest=_ref("profile", "b"),
        official_provenance=_ref("gymnasium_provenance", "6"),
        environment_id="CartPole-v1",
        backend="gymnasium",
        package_name="gymnasium",
        package_version="1.2.2",
        upstream_commit="a923da5d4415a1aa5195d99341069da5e16deed7",
        distribution_hash=(
            "sha256:f04ec362b1fdf73a8b327db5ef89384a3f2ba411e05d3521513414fbbb2199c8"
        ),
        runtime_profile_id="rllib-core",
        profile_lock_hash="sha256:" + "c" * 64,
        profile_image_digest="sha256:" + "d" * 64,
        profile_platform="linux/amd64",
        wrappers=(),
        protocol_version="automarkov.remote-env.v1",
        frame_schema_hash="sha256:" + "e" * 64,
        granted_capabilities=(
            "artifact_read",
            "artifact_write",
            "registered_environment_close",
            "registered_environment_load",
            "registered_environment_reset",
            "registered_environment_step",
        ),
    )


def test_cartpole_reuse_artifacts_expose_a_closed_formal_parent_dag() -> None:
    plan = _plan()
    bundle = _bundle()
    binding = _binding()

    def reference_keys(
        references: tuple[ArtifactReference, ...],
    ) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted(
                ((item.artifact_id, item.payload_hash) for item in references),
                key=lambda item: item[0].encode("utf-8"),
            )
        )

    assert reference_keys(
        implementation_plan_parent_references(plan)
    ) == reference_keys(
        (
            plan.task_contract,
            plan.decision_process_spec,
            plan.classification_result,
            plan.signed_suite_manifest,
            plan.official_provenance,
        )
    )
    assert candidate_bundle_parent_references(bundle) == tuple(
        sorted(
            (bundle.implementation_plan, bundle.official_provenance),
            key=lambda item: item.artifact_id.encode("utf-8"),
        )
    )
    assert reference_keys(
        environment_binding_parent_references(binding)
    ) == reference_keys(
        (
            binding.task_contract,
            binding.decision_process_spec,
            binding.classification_result,
            binding.implementation_plan,
            binding.candidate_bundle,
            binding.sandbox_policy,
            binding.sandbox_limits,
            binding.runtime_profile_manifest,
            binding.official_provenance,
        )
    )


def test_formal_environment_binding_rejects_pilot_or_runtime_locator_fields() -> None:
    raw = _binding().model_dump(mode="json", round_trip=True, warnings="error")
    forbidden_fields = {
        "pilot_parent": "artifact_" + "1" * 64,
        "environment_object": "CartPoleEnv()",
        "import_path": "gymnasium.envs.classic_control:CartPoleEnv",
        "runtime_locator": "/tmp/worker.sock",
        "checkpoint_path": "/tmp/checkpoint.pkl",
    }
    for field_name, value in forbidden_fields.items():
        candidate = dict(raw)
        candidate[field_name] = value
        with pytest.raises(ValidationError, match="Extra inputs"):
            EnvironmentBindingArtifact.model_validate(candidate, strict=True)


def test_public_runtime_profile_reference_uses_the_t04_profile_identity() -> None:
    reference = RuntimeProfileRef(
        schema_version="automarkov.runtime-profile-ref.v1",
        profile_id="rllib-core",
    )
    assert reference.profile_id == "rllib-core"

    with pytest.raises(ValidationError):
        RuntimeProfileRef(
            schema_version="automarkov.runtime-profile-ref.v1",
            profile_id="profiles/rllib-core",
        )


def test_public_environment_seams_delegate_to_their_deep_implementations() -> None:
    execution_result = ExecutionResult(
        schema_version="automarkov.execution-result.v1",
        terminal_record_artifact_id=ArtifactId(root="artifact_" + "1" * 64),
    )
    sandbox = ScriptedExecutionSandbox(run_handler=lambda _: execution_result)
    run_request = SandboxRunRequest(
        schema_version="automarkov.sandbox-run-request.v1",
        bundle_artifact_id=ArtifactId(root="artifact_" + "2" * 64),
        limits_artifact_id=ArtifactId(root="artifact_" + "3" * 64),
    )
    remote_env = object()
    binding = InMemoryEnvironmentBinding(bind_handler=lambda _profile, _env: remote_env)

    assert sandbox.run(run_request) == execution_result
    assert (
        binding.bind(
            RuntimeProfileRef(
                schema_version="automarkov.runtime-profile-ref.v1",
                profile_id="rllib-core",
            ),
            EnvironmentRef(
                schema_version="automarkov.environment-ref.v1",
                environment_artifact_id=ArtifactId(root="artifact_" + "4" * 64),
            ),
        )
        is remote_env
    )


def test_default_repository_registers_closed_sandbox_policy_and_limits() -> None:
    repository = InMemoryArtifactRepository()
    policy = SandboxPolicy(
        schema_version="automarkov.environment-sandbox-policy.v1",
        policy_id="policy_cartpole_reuse",
        route="reuse",
        allowed_capabilities=(
            "artifact_read",
            "artifact_write",
            "registered_environment_close",
            "registered_environment_load",
            "registered_environment_reset",
            "registered_environment_step",
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
    limits = SandboxLimits(
        schema_version="automarkov.environment-sandbox-limits.v1",
        wall_time_seconds=30,
        cpu_time_seconds=20,
        memory_bytes=536_870_912,
        output_bytes=1_048_576,
        open_files=32,
        processes=1,
    )

    for artifact_type, model in (
        ("environment_sandbox_policy", policy),
        ("environment_sandbox_limits", limits),
    ):
        result = repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": artifact_type,
                "payload_bytes": json.dumps(model.model_dump(mode="json")).encode(),
                "parent_artifact_ids": [],
                "created_by": "principal_environment_test",
                "created_at": "2026-08-12T12:00:00Z",
                "source_evidence_ids": [],
            }
        )
        assert (
            repository.get(result.artifact_id).envelope.artifact_type == artifact_type
        )
