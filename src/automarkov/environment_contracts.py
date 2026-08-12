from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import AfterValidator, Field, field_validator, model_validator

from automarkov.canonical import (
    FrozenSequence,
    PositiveSafeCanonicalInt,
    canonical_json_bytes,
)
from automarkov.domain import StrictFrozenModel, VerifiedEventHead
from automarkov.lifecycle import ArtifactReference, CanonicalTimestamp, Sha256Value
from automarkov.provenance import RuntimeProfileId

ImplementationRoute: TypeAlias = Literal["reuse", "compose", "generate"]
RuntimeImageStatus: TypeAlias = Literal[
    "built", "recipe_frozen", "attached_unverified", "restricted_disabled"
]
NonEmptyId = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]


def _require_canonical_string_tuple(value: tuple[str, ...]) -> tuple[str, ...]:
    if value != tuple(sorted(set(value), key=lambda item: item.encode("utf-8"))):
        raise ValueError("values must be sorted and unique")
    return value


CanonicalStringTuple = Annotated[
    FrozenSequence[NonEmptyId],
    AfterValidator(_require_canonical_string_tuple),
]


def _decode_base64url(value: str, *, expected_length: int) -> bytes:
    try:
        raw = urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, Base64Error) as error:
        raise ValueError("value must be canonical unpadded base64url") from error
    if (
        len(raw) != expected_length
        or urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value
    ):
        raise ValueError("value must be canonical unpadded base64url")
    return raw


class SignedRouteRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.signed-route-request.v1"]
    signing_domain: Literal["AutoMarkov-Signed-Route-Request-v1"]
    request_id: NonEmptyId
    suite_id: NonEmptyId
    task_contract: ArtifactReference
    decision_process_spec: ArtifactReference
    classification_result: ArtifactReference
    signed_suite_manifest: ArtifactReference
    implementation_catalog_hash: Sha256Value
    required_route: ImplementationRoute
    issued_at: CanonicalTimestamp
    nonce_b64url: Annotated[str, Field(strict=True, min_length=43, max_length=43)]
    signing_key_id: NonEmptyId
    signature_b64url: Annotated[str, Field(strict=True, max_length=86)]

    @field_validator("nonce_b64url")
    @classmethod
    def require_nonce(cls, value: str) -> str:
        _decode_base64url(value, expected_length=32)
        return value

    @field_validator("signature_b64url")
    @classmethod
    def require_signature(cls, value: str) -> str:
        if value:
            _decode_base64url(value, expected_length=64)
        return value

    def signing_bytes(self) -> bytes:
        payload = self.model_dump(mode="json", round_trip=True, warnings="error")
        payload.pop("signature_b64url")
        return canonical_json_bytes(payload)


class EnvironmentCandidate(StrictFrozenModel):
    candidate_id: NonEmptyId
    route: ImplementationRoute
    suite_id: NonEmptyId
    environment_id: NonEmptyId
    backend: Literal["gymnasium", "pettingzoo"]
    package_name: NonEmptyId
    package_version: NonEmptyId
    upstream_commit: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{40}$")]
    distribution_hash: Sha256Value
    runtime_profile_id: RuntimeProfileId
    wrappers: CanonicalStringTuple
    evidence_ids: CanonicalStringTuple
    official_provenance: ArtifactReference

    @model_validator(mode="after")
    def require_official_cartpole_reuse_contract(self) -> Self:
        if self.suite_id == "suite_cartpole" and (
            self.route != "reuse"
            or self.environment_id != "CartPole-v1"
            or self.backend != "gymnasium"
            or self.package_name != "gymnasium"
            or self.package_version != "1.2.2"
            or self.upstream_commit != "a923da5d4415a1aa5195d99341069da5e16deed7"
            or self.distribution_hash
            != "sha256:f04ec362b1fdf73a8b327db5ef89384a3f2ba411e05d3521513414fbbb2199c8"
            or self.runtime_profile_id != "rllib-core"
            or self.wrappers
        ):
            raise ValueError("CartPole must use the frozen official Reuse candidate")
        return self


class FrozenImplementationCatalog(StrictFrozenModel):
    schema_version: Literal["automarkov.frozen-implementation-catalog.v1"]
    catalog_id: NonEmptyId
    candidates: FrozenSequence[EnvironmentCandidate]
    catalog_hash: Sha256Value

    @model_validator(mode="after")
    def require_canonical_candidates(self) -> Self:
        keys = tuple(candidate.candidate_id for candidate in self.candidates)
        if not keys or keys != tuple(
            sorted(set(keys), key=lambda item: item.encode("utf-8"))
        ):
            raise ValueError("implementation candidates must be sorted and unique")
        return self


class ImplementationPlan(StrictFrozenModel):
    schema_version: Literal["automarkov.implementation-plan.v1"]
    route_request_id: NonEmptyId
    suite_id: NonEmptyId
    task_contract: ArtifactReference
    decision_process_spec: ArtifactReference
    classification_result: ArtifactReference
    signed_suite_manifest: ArtifactReference
    implementation_catalog_hash: Sha256Value
    route: ImplementationRoute
    candidate_id: NonEmptyId
    environment_id: NonEmptyId
    backend: Literal["gymnasium", "pettingzoo"]
    runtime_profile_id: RuntimeProfileId
    wrappers: CanonicalStringTuple
    official_provenance: ArtifactReference


class EnvironmentCandidateBundle(StrictFrozenModel):
    schema_version: Literal["automarkov.environment-candidate-bundle.v1"]
    implementation_plan: ArtifactReference
    candidate_id: NonEmptyId
    route: ImplementationRoute
    environment_id: NonEmptyId
    backend: Literal["gymnasium", "pettingzoo"]
    package_name: NonEmptyId
    package_version: NonEmptyId
    upstream_commit: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{40}$")]
    distribution_hash: Sha256Value
    runtime_profile_id: RuntimeProfileId
    wrappers: CanonicalStringTuple
    materialized_files_hash: Sha256Value
    official_provenance: ArtifactReference


class SandboxPolicy(StrictFrozenModel):
    schema_version: Literal["automarkov.environment-sandbox-policy.v1"]
    policy_id: NonEmptyId
    route: ImplementationRoute
    allowed_capabilities: CanonicalStringTuple
    denied_capabilities: CanonicalStringTuple
    network_access: bool = Field(strict=True)
    sealed_access: bool = Field(strict=True)
    pickle_allowed: bool = Field(strict=True)
    shell_allowed: bool = Field(strict=True)
    subprocess_allowed: bool = Field(strict=True)
    dynamic_import_allowed: bool = Field(strict=True)

    @model_validator(mode="after")
    def require_isolated_capabilities(self) -> Self:
        required_denials = {
            "dynamic_import",
            "generation",
            "network",
            "pickle",
            "sealed_evaluation",
            "shell",
            "subprocess",
        }
        base_allowlist = {
            "artifact_read",
            "artifact_write",
            "registered_environment_close",
            "registered_environment_load",
            "registered_environment_reset",
            "registered_environment_step",
        }
        route_allowlists = {
            "reuse": base_allowlist,
            "compose": base_allowlist | {"registered_wrapper_compose"},
            "generate": base_allowlist | {"registered_candidate_load"},
        }
        if set(self.allowed_capabilities) & set(self.denied_capabilities):
            raise ValueError("sandbox allowed and denied capabilities overlap")
        if not required_denials <= set(self.denied_capabilities):
            raise ValueError("sandbox policy omits mandatory denied capabilities")
        expected_allowlist = route_allowlists[self.route]
        if set(self.allowed_capabilities) != expected_allowlist:
            route_name = self.route.capitalize()
            raise ValueError(f"{route_name} sandbox capability allowlist is not exact")
        if any(
            (
                self.network_access,
                self.sealed_access,
                self.pickle_allowed,
                self.shell_allowed,
                self.subprocess_allowed,
                self.dynamic_import_allowed,
            )
        ):
            raise ValueError(
                "environment sandbox escape capabilities must remain disabled"
            )
        return self


class SandboxLimits(StrictFrozenModel):
    schema_version: Literal["automarkov.environment-sandbox-limits.v1"]
    wall_time_seconds: PositiveSafeCanonicalInt
    cpu_time_seconds: PositiveSafeCanonicalInt
    memory_bytes: PositiveSafeCanonicalInt
    output_bytes: PositiveSafeCanonicalInt
    open_files: PositiveSafeCanonicalInt
    processes: PositiveSafeCanonicalInt

    @model_validator(mode="after")
    def require_cpu_within_wall_budget(self) -> Self:
        if self.cpu_time_seconds > self.wall_time_seconds:
            raise ValueError("CPU time cannot exceed wall time")
        if self.processes != 1:
            raise ValueError("environment sandbox must run exactly one process")
        return self


class EnvironmentBindingArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.environment-binding.v1"]
    task_contract: ArtifactReference
    decision_process_spec: ArtifactReference
    classification_result: ArtifactReference
    implementation_plan: ArtifactReference
    candidate_bundle: ArtifactReference
    sandbox_policy: ArtifactReference
    sandbox_limits: ArtifactReference
    runtime_profile_manifest: ArtifactReference
    official_provenance: ArtifactReference
    environment_id: NonEmptyId
    backend: Literal["gymnasium", "pettingzoo"]
    package_name: NonEmptyId
    package_version: NonEmptyId
    upstream_commit: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{40}$")]
    distribution_hash: Sha256Value
    runtime_profile_id: RuntimeProfileId
    profile_lock_hash: Sha256Value
    profile_image_digest: Sha256Value
    profile_platform: Literal["linux/amd64"]
    wrappers: CanonicalStringTuple
    protocol_version: Literal["automarkov.remote-env.v1"]
    frame_schema_hash: Sha256Value
    granted_capabilities: CanonicalStringTuple


class RuntimeProfileResolution(StrictFrozenModel):
    schema_version: Literal["automarkov.runtime-profile-resolution.v1"]
    profile_id: RuntimeProfileId
    profile_manifest: ArtifactReference
    lock_hash: Sha256Value
    image_status: RuntimeImageStatus
    image_digest: Sha256Value | None
    platform: Literal["linux/amd64"] | None
    build_attestation: ArtifactReference | None
    import_smoke_attestation: ArtifactReference | None

    @model_validator(mode="after")
    def require_built_evidence(self) -> Self:
        evidence = (
            self.image_digest,
            self.platform,
            self.build_attestation,
            self.import_smoke_attestation,
        )
        if self.image_status == "built" and any(item is None for item in evidence):
            raise ValueError(
                "built runtime profile requires complete attestation evidence"
            )
        if self.image_status != "built" and any(item is not None for item in evidence):
            raise ValueError("unbuilt runtime profile cannot claim runtime evidence")
        return self


class EnvironmentSandboxRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.environment-sandbox-request.v1"]
    specified_event_head: VerifiedEventHead
    implementation_plan: ArtifactReference
    candidate_bundle: ArtifactReference
    sandbox_policy: ArtifactReference
    sandbox_limits: ArtifactReference
    fixed_commit_job_manifest: ArtifactReference


class SandboxLaunchRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.sandbox-launch-request.v1"]
    environment_id: NonEmptyId
    backend: Literal["gymnasium", "pettingzoo"]
    package_name: NonEmptyId
    package_version: NonEmptyId
    upstream_commit: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{40}$")]
    distribution_hash: Sha256Value
    runtime_profile_id: RuntimeProfileId
    image_digest: Sha256Value
    platform: Literal["linux/amd64"]
    wrappers: CanonicalStringTuple
    granted_capabilities: CanonicalStringTuple
    network_access: bool = Field(strict=True)
    sealed_access: bool = Field(strict=True)
    pickle_allowed: bool = Field(strict=True)
    shell_allowed: bool = Field(strict=True)
    subprocess_allowed: bool = Field(strict=True)
    dynamic_import_allowed: bool = Field(strict=True)
    limits: SandboxLimits

    @model_validator(mode="after")
    def require_closed_launcher_capabilities(self) -> Self:
        if any(
            (
                self.network_access,
                self.sealed_access,
                self.pickle_allowed,
                self.shell_allowed,
                self.subprocess_allowed,
                self.dynamic_import_allowed,
            )
        ):
            raise ValueError(
                "sandbox launcher escape capabilities must remain disabled"
            )
        return self


class SandboxLaunchReport(StrictFrozenModel):
    schema_version: Literal["automarkov.sandbox-launch-report.v1"]
    status: Literal["success"]
    started_at: CanonicalTimestamp
    finished_at: CanonicalTimestamp
    stdout_hash: Sha256Value
    stderr_hash: Sha256Value
    network_log_hash: Sha256Value
    mount_attestation_hash: Sha256Value
    capability_decision_hash: Sha256Value
    egress_log_hash: Sha256Value
    frame_schema_hash: Sha256Value

    @model_validator(mode="after")
    def require_ordered_timestamps(self) -> Self:
        if self.started_at > self.finished_at:
            raise ValueError("sandbox launch timestamps are reversed")
        return self


class EnvironmentSandboxResult(StrictFrozenModel):
    schema_version: Literal["automarkov.environment-sandbox-result.v1"]
    readiness_state: Literal["ENVIRONMENT_IMPLEMENTED", "WAITING_RUNTIME"]
    fixed_commit_job_manifest: ArtifactReference
    runtime_profile: RuntimeProfileResolution
    environment_binding: EnvironmentBindingArtifact | None
    launch_report: SandboxLaunchReport | None
    failure_code: (
        Literal["runtime_profile_unavailable", "runtime_provenance_unverified"] | None
    )
    completed_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_closed_outcome(self) -> Self:
        succeeded = self.readiness_state == "ENVIRONMENT_IMPLEMENTED"
        if succeeded != (self.environment_binding is not None):
            raise ValueError("sandbox readiness and binding presence disagree")
        if succeeded != (self.launch_report is not None):
            raise ValueError("sandbox readiness and launch report presence disagree")
        if succeeded == (self.failure_code is not None):
            raise ValueError("sandbox readiness and failure code disagree")
        return self


def _canonical_references(
    references: tuple[ArtifactReference, ...],
) -> tuple[ArtifactReference, ...]:
    keys = tuple((item.artifact_id, item.payload_hash) for item in references)
    if len(keys) != len(set(keys)):
        raise ValueError("artifact parent references must be unique")
    return tuple(sorted(references, key=lambda item: item.artifact_id.encode("utf-8")))


def implementation_plan_parent_references(
    plan: ImplementationPlan,
) -> tuple[ArtifactReference, ...]:
    """返回 ImplementationPlan 的闭合 direct-parent DAG。"""

    if type(plan) is not ImplementationPlan:
        raise ValueError("implementation plan must use the exact validated type")
    return _canonical_references(
        (
            plan.task_contract,
            plan.decision_process_spec,
            plan.classification_result,
            plan.signed_suite_manifest,
            plan.official_provenance,
        )
    )


def candidate_bundle_parent_references(
    bundle: EnvironmentCandidateBundle,
) -> tuple[ArtifactReference, ...]:
    """返回 candidate bundle 的闭合 direct-parent DAG。"""

    if type(bundle) is not EnvironmentCandidateBundle:
        raise ValueError("candidate bundle must use the exact validated type")
    return _canonical_references(
        (bundle.implementation_plan, bundle.official_provenance)
    )


def environment_binding_parent_references(
    binding: EnvironmentBindingArtifact,
) -> tuple[ArtifactReference, ...]:
    """返回 binding 的闭合 direct-parent DAG，不允许 pilot/locator 旁路。"""

    if type(binding) is not EnvironmentBindingArtifact:
        raise ValueError("environment binding must use the exact validated type")
    return _canonical_references(
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


__all__ = [
    "EnvironmentBindingArtifact",
    "EnvironmentCandidate",
    "EnvironmentCandidateBundle",
    "EnvironmentSandboxRequest",
    "EnvironmentSandboxResult",
    "FrozenImplementationCatalog",
    "ImplementationPlan",
    "ImplementationRoute",
    "RuntimeImageStatus",
    "RuntimeProfileResolution",
    "SandboxLaunchReport",
    "SandboxLaunchRequest",
    "SandboxLimits",
    "SandboxPolicy",
    "SignedRouteRequest",
    "candidate_bundle_parent_references",
    "environment_binding_parent_references",
    "implementation_plan_parent_references",
]
