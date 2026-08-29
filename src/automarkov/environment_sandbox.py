from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar, cast

from pydantic import BaseModel, TypeAdapter

from automarkov.contracts.environment import (
    EnvironmentCandidateBundle,
    EnvironmentSandboxRequest,
    EnvironmentSandboxResult,
    ImplementationPlan,
    RuntimeProfileResolution,
    SandboxLaunchReport,
    SandboxLaunchRequest,
    SandboxLimits,
    SandboxPolicy,
)
from automarkov.domain.models import VerifiedEventHead
from automarkov.lifecycle import ArtifactReference, CanonicalTimestamp
from automarkov.security.provenance import RuntimeProfileId

PayloadModel = TypeVar("PayloadModel", bound=BaseModel)


class EnvironmentArtifactReader(Protocol):
    def verify_event_head(self, head: VerifiedEventHead) -> None: ...

    def load(
        self,
        reference: ArtifactReference,
        model_type: type[BaseModel],
    ) -> BaseModel: ...


class RuntimeProfileResolver(Protocol):
    def resolve(
        self, profile_id: RuntimeProfileId, head: VerifiedEventHead
    ) -> RuntimeProfileResolution: ...


class EnvironmentLauncher(Protocol):
    def launch(self, request: SandboxLaunchRequest) -> SandboxLaunchReport: ...


class EnvironmentSandbox:
    """把冻结 candidate 限制在对应 route 的闭合 sandbox 中。"""

    def __init__(
        self,
        *,
        repository: EnvironmentArtifactReader,
        profile_resolver: RuntimeProfileResolver,
        launcher: EnvironmentLauncher,
        clock: Callable[[], str],
    ) -> None:
        self._repository = repository
        self._profile_resolver = profile_resolver
        self._launcher = launcher
        self._clock = clock

    def _load(
        self,
        reference: ArtifactReference,
        model_type: type[PayloadModel],
    ) -> PayloadModel:
        loaded = self._repository.load(reference, model_type)
        if type(loaded) is not model_type:
            raise ValueError("sandbox artifact payload type mismatch")
        return cast(PayloadModel, loaded)

    def run(self, request: EnvironmentSandboxRequest) -> EnvironmentSandboxResult:
        if type(request) is not EnvironmentSandboxRequest:
            raise ValueError("sandbox request must use the exact validated type")
        self._repository.verify_event_head(request.specified_event_head)
        plan = self._load(request.implementation_plan, ImplementationPlan)
        candidate = self._load(
            request.candidate_bundle,
            EnvironmentCandidateBundle,
        )
        policy = self._load(request.sandbox_policy, SandboxPolicy)
        self._load(request.sandbox_limits, SandboxLimits)
        completed_at = TypeAdapter(CanonicalTimestamp).validate_python(
            self._clock(), strict=True
        )
        self._require_closed_inputs(
            request=request,
            plan=plan,
            candidate=candidate,
            policy=policy,
        )
        profile = self._profile_resolver.resolve(
            plan.runtime_profile_id, request.specified_event_head
        )
        if type(profile) is not RuntimeProfileResolution:
            raise ValueError("runtime profile resolver returned an invalid payload")
        if profile.profile_id != plan.runtime_profile_id:
            raise ValueError("runtime profile identity does not match the plan")
        if profile.image_status != "built":
            return EnvironmentSandboxResult(
                schema_version="automarkov.environment-sandbox-result.v1",
                readiness_state="WAITING_RUNTIME",
                fixed_commit_job_manifest=request.fixed_commit_job_manifest,
                runtime_profile=profile,
                environment_binding=None,
                launch_report=None,
                failure_code="runtime_profile_unavailable",
                completed_at=completed_at,
            )
        return EnvironmentSandboxResult(
            schema_version="automarkov.environment-sandbox-result.v1",
            readiness_state="WAITING_RUNTIME",
            fixed_commit_job_manifest=request.fixed_commit_job_manifest,
            runtime_profile=profile,
            environment_binding=None,
            launch_report=None,
            failure_code="runtime_provenance_unverified",
            completed_at=completed_at,
        )

    @staticmethod
    def _require_closed_inputs(
        *,
        request: EnvironmentSandboxRequest,
        plan: ImplementationPlan,
        candidate: EnvironmentCandidateBundle,
        policy: SandboxPolicy,
    ) -> None:
        if (
            candidate.implementation_plan != request.implementation_plan
            or candidate.candidate_id != plan.candidate_id
            or candidate.route != plan.route
            or candidate.environment_id != plan.environment_id
            or candidate.backend != plan.backend
            or candidate.runtime_profile_id != plan.runtime_profile_id
            or candidate.wrappers != plan.wrappers
            or candidate.official_provenance != plan.official_provenance
            or policy.route != plan.route
        ):
            raise ValueError("sandbox input DAG does not match the implementation plan")


__all__ = [
    "EnvironmentArtifactReader",
    "EnvironmentLauncher",
    "EnvironmentSandbox",
    "RuntimeProfileResolver",
]
