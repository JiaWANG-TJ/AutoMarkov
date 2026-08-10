from __future__ import annotations

from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import Field, ValidationInfo, field_validator

from automarkov.domain import (
    ArtifactId,
    CompilerDispatchRequest,
    RunId,
    RunView,
    Sha256Digest,
    StrictFrozenModel,
    TaskRequest,
)

NonEmptyText = Annotated[str, Field(strict=True, min_length=1, max_length=100_000)]
RuntimeId = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^runtime_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    ),
]
ProfileId = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^profile_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    ),
]
EnvironmentHandleId = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^envhandle_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    ),
]


class ArtifactPutRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.artifact-put-request.v1"]
    artifact_type: Annotated[
        str, Field(strict=True, min_length=1, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    ]
    payload_bytes: bytes = Field(strict=True, max_length=8 * 1024 * 1024)
    parent_artifact_ids: tuple[ArtifactId, ...]


class ArtifactPutResult(StrictFrozenModel):
    schema_version: Literal["automarkov.artifact-put-result.v1"]
    artifact_id: ArtifactId
    payload_hash: Sha256Digest

    @field_validator("artifact_id", mode="before")
    @classmethod
    def require_artifact_id(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "json" and type(value) is str:
            return value
        if type(value) is not ArtifactId:
            raise ValueError("artifact_id must be an ArtifactId")
        return value

    @field_validator("payload_hash", mode="before")
    @classmethod
    def require_payload_hash(cls, value: object, info: ValidationInfo) -> object:
        if info.mode == "json" and type(value) is str:
            return value
        if type(value) is not Sha256Digest:
            raise ValueError("payload_hash must be a Sha256Digest")
        return value


class ArtifactAppendRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.artifact-append-request.v1"]
    run_id: RunId
    event_bytes: bytes = Field(strict=True, max_length=8 * 1024 * 1024)


class ArtifactBytesResult(StrictFrozenModel):
    schema_version: Literal["automarkov.artifact-bytes-result.v1"]
    artifact_id: ArtifactId
    payload_bytes: bytes = Field(strict=True, max_length=8 * 1024 * 1024)


class ArtifactLineageResult(StrictFrozenModel):
    schema_version: Literal["automarkov.artifact-lineage-result.v1"]
    artifact_ids: tuple[ArtifactId, ...]


class PackageResult(StrictFrozenModel):
    schema_version: Literal["automarkov.package-result.v1"]
    run_id: RunId
    package_artifact_id: ArtifactId


class LlmStartRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.llm-start-request.v1"]
    runtime_manifest_artifact_id: ArtifactId


class LlmProbeResult(StrictFrozenModel):
    schema_version: Literal["automarkov.llm-probe-result.v1"]
    runtime_id: RuntimeId
    ready: bool = Field(strict=True)


class LlmCompletionRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.llm-completion-request.v1"]
    prompt_artifact_id: ArtifactId


class LlmCompletionResult(StrictFrozenModel):
    schema_version: Literal["automarkov.llm-completion-result.v1"]
    response_artifact_id: ArtifactId


class EvidenceSearchRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.evidence-search-request.v1"]
    query: NonEmptyText


class EvidenceUrlsRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.evidence-urls-request.v1"]
    urls: tuple[NonEmptyText, ...]


class EvidenceCrawlRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.evidence-crawl-request.v1"]
    root_url: NonEmptyText


class EvidenceResolveRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.evidence-resolve-request.v1"]
    claim_artifact_id: ArtifactId


class EvidenceResult(StrictFrozenModel):
    schema_version: Literal["automarkov.evidence-result.v1"]
    evidence_artifact_id: ArtifactId


class SandboxRunRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.sandbox-run-request.v1"]
    bundle_artifact_id: ArtifactId
    limits_artifact_id: ArtifactId


class SandboxTestRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.sandbox-test-request.v1"]
    bundle_artifact_id: ArtifactId
    test_plan_artifact_id: ArtifactId


class FixedCommitJobRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.fixed-commit-job-request.v1"]
    job_manifest_artifact_id: ArtifactId


class ExecutionResult(StrictFrozenModel):
    schema_version: Literal["automarkov.execution-result.v1"]
    terminal_record_artifact_id: ArtifactId


class RuntimeProfileRef(StrictFrozenModel):
    schema_version: Literal["automarkov.runtime-profile-ref.v1"]
    profile_id: ProfileId


class EnvironmentRef(StrictFrozenModel):
    schema_version: Literal["automarkov.environment-ref.v1"]
    environment_artifact_id: ArtifactId


class EnvironmentHandle(StrictFrozenModel):
    schema_version: Literal["automarkov.environment-handle.v1"]
    handle_id: EnvironmentHandleId


class RemoteEnvResetRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.remote-env-reset-request.v1"]
    seed: Annotated[int, Field(strict=True, ge=0, le=9_007_199_254_740_991)]


class RemoteEnvStepRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.remote-env-step-request.v1"]
    action_artifact_id: ArtifactId


class RemoteEnvResult(StrictFrozenModel):
    schema_version: Literal["automarkov.remote-env-result.v1"]
    frame_artifact_id: ArtifactId


class RemoteEnvSpacesResult(StrictFrozenModel):
    schema_version: Literal["automarkov.remote-env-spaces-result.v1"]
    spaces_artifact_id: ArtifactId


class CloseResult(StrictFrozenModel):
    schema_version: Literal["automarkov.close-result.v1"]
    closed: Literal[True]

    @field_validator("closed", mode="before")
    @classmethod
    def require_exact_true(cls, value: object) -> object:
        if type(value) is not bool or value is not True:
            raise ValueError("closed must be the boolean true")
        return value


class TrainingRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.training-request.v1"]
    training_plan_artifact_id: ArtifactId


class TrainingResult(StrictFrozenModel):
    schema_version: Literal["automarkov.training-result.v1"]
    terminal_record_artifact_id: ArtifactId


class PolicyEvaluationRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.policy-evaluation-request-ref.v1"]
    request_artifact_id: ArtifactId


class PolicyEvaluationResult(StrictFrozenModel):
    schema_version: Literal["automarkov.policy-evaluation-result-ref.v1"]
    result_artifact_id: ArtifactId


@runtime_checkable
class Compiler(Protocol):
    def start(self, request: TaskRequest) -> RunId: ...
    def dispatch(self, request: CompilerDispatchRequest) -> RunView: ...
    def resume(self, run_id: RunId) -> RunView: ...
    def package(self, run_id: RunId) -> PackageResult: ...


@runtime_checkable
class ArtifactRepository(Protocol):
    def put(self, request: ArtifactPutRequest) -> ArtifactPutResult: ...
    def get(self, artifact_id: ArtifactId) -> ArtifactBytesResult: ...
    def append(self, request: ArtifactAppendRequest) -> RunView: ...
    def lineage(self, artifact_id: ArtifactId) -> ArtifactLineageResult: ...
    def project(self, run_id: RunId) -> RunView: ...


@runtime_checkable
class LocalLlmRuntime(Protocol):
    def start(self, request: LlmStartRequest) -> LlmProbeResult: ...
    def probe(self) -> LlmProbeResult: ...
    def complete(self, request: LlmCompletionRequest) -> LlmCompletionResult: ...
    def close(self) -> CloseResult: ...


@runtime_checkable
class EvidenceGateway(Protocol):
    def search(self, request: EvidenceSearchRequest) -> EvidenceResult: ...
    def extract(self, request: EvidenceUrlsRequest) -> EvidenceResult: ...
    def crawl(self, request: EvidenceCrawlRequest) -> EvidenceResult: ...
    def resolve(self, request: EvidenceResolveRequest) -> EvidenceResult: ...


@runtime_checkable
class ExecutionSandbox(Protocol):
    def run(self, request: SandboxRunRequest) -> ExecutionResult: ...
    def test(self, request: SandboxTestRequest) -> ExecutionResult: ...
    def run_at_commit(self, request: FixedCommitJobRequest) -> ExecutionResult: ...


@runtime_checkable
class RemoteEnv(Protocol):
    def reset(self, request: RemoteEnvResetRequest) -> RemoteEnvResult: ...
    def step(self, request: RemoteEnvStepRequest) -> RemoteEnvResult: ...
    def spaces(self) -> RemoteEnvSpacesResult: ...
    def close(self) -> CloseResult: ...


@runtime_checkable
class EnvironmentBinding(Protocol):
    def bind(
        self, profile: RuntimeProfileRef, env_ref: EnvironmentRef
    ) -> RemoteEnv: ...


@runtime_checkable
class TrainingRunner(Protocol):
    def train(self, request: TrainingRequest) -> TrainingResult: ...
    def evaluate(self, request: PolicyEvaluationRequest) -> PolicyEvaluationResult: ...
