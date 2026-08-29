from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import (
    Annotated,
    Literal,
    Protocol,
    TypeAlias,
    cast,
    runtime_checkable,
)

from pydantic import (
    AfterValidator,
    Field,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)

from automarkov.contracts.evidence import (
    CrawlEvidenceRequest,
    EvidenceGatewayResult,
    ExtractEvidenceRequest,
    SearchEvidenceRequest,
)
from automarkov.contracts.llm import (
    LlmCompletionRequest,
    LlmCompletionResult,
    LlmProbeResult,
    LlmStartRequest,
)
from automarkov.domain.canonical import (
    MAX_CANONICAL_DOCUMENT_BYTES,
    MAX_JSON_NODES,
    MAX_JSON_PAYLOAD_BYTES,
    CanonicalJsonValue,
    StrictTrue,
    parse_json_payload,
    validate_and_measure_raw_json_tree,
)
from automarkov.domain.models import (
    ArtifactId,
    RunId,
    Sha256Digest,
    StrictFrozenModel,
    TaskRequest,
    VerifiedEventHead,
    validate_task_request_payload,
)
from automarkov.lifecycle import ArtifactReference, LifecycleCommitResult, RunProjection
from automarkov.provenance import RuntimeProfileId

LifecycleCommandInput: TypeAlias = dict[str, CanonicalJsonValue]

NonEmptyText = Annotated[str, Field(strict=True, min_length=1, max_length=100_000)]
EnvironmentHandleId = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^envhandle_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    ),
]


def _require_utc_timestamp(value: str) -> str:
    if not value.endswith("Z") or "+" in value or value.count("Z") != 1:
        raise ValueError("created_at must be a canonical UTC-Z timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("created_at must be a valid UTC timestamp") from error
    canonical = parsed.isoformat(timespec="microseconds").replace(".000000+00:00", "Z")
    if canonical.endswith("+00:00"):
        canonical = canonical.removesuffix("+00:00").rstrip("0").rstrip(".") + "Z"
    if value != canonical:
        raise ValueError("created_at must use the canonical UTC-Z representation")
    return value


def _require_sorted_unique_evidence_ids(value: tuple[str, ...]) -> tuple[str, ...]:
    expected = tuple(sorted(set(value), key=lambda item: item.encode("utf-8")))
    if value != expected:
        raise ValueError("source_evidence_ids must be sorted and unique")
    return value


PrincipalId = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^principal_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    ),
]
CanonicalUtcTimestamp = Annotated[
    str,
    Field(
        strict=True,
        pattern=(
            r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:"
            r"[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
        ),
    ),
    AfterValidator(_require_utc_timestamp),
]
AuthorityId = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^authority_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    ),
]
EvidenceId = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^E-[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    ),
]


@dataclass(frozen=True, slots=True)
class CommandPrincipalBinding:
    principal_id: str
    process_execution_id: str | None


@dataclass(frozen=True, slots=True)
class AuthenticatedCommandContext:
    principal_id: str
    process_execution_id: str | None
    received_at: str
    authority_id: str
    _issuer: object = field(repr=False, compare=False)


class CommandAuthority:
    """由可信 transport/control 层持有的 lifecycle command capability。"""

    def __init__(
        self,
        authority_id: str,
        bindings: tuple[CommandPrincipalBinding, ...],
    ) -> None:
        self.authority_id = TypeAdapter(AuthorityId).validate_python(
            authority_id,
            strict=True,
        )
        if type(bindings) is not tuple or not bindings:
            raise ValueError("command authority requires frozen principal bindings")
        normalized: set[tuple[str, str | None]] = set()
        for binding in bindings:
            if type(binding) is not CommandPrincipalBinding:
                raise TypeError("command principal binding must be exact")
            principal_id = TypeAdapter(PrincipalId).validate_python(
                binding.principal_id,
                strict=True,
            )
            process_id = binding.process_execution_id
            if process_id is not None and (
                type(process_id) is not str or not process_id
            ):
                raise ValueError("command process execution ID is invalid")
            normalized.add((principal_id, process_id))
        expected = tuple(
            sorted(
                normalized,
                key=lambda item: (
                    item[0].encode("utf-8"),
                    (item[1] or "").encode("utf-8"),
                ),
            )
        )
        if len(expected) != len(bindings):
            raise ValueError("command principal bindings must be unique")
        self._bindings = frozenset(expected)

    def issue(
        self,
        principal_id: str,
        process_execution_id: str | None,
        received_at: str,
    ) -> AuthenticatedCommandContext:
        principal = TypeAdapter(PrincipalId).validate_python(
            principal_id,
            strict=True,
        )
        timestamp = TypeAdapter(CanonicalUtcTimestamp).validate_python(
            received_at,
            strict=True,
        )
        if (principal, process_execution_id) not in self._bindings:
            raise ValueError("principal is outside the command authority")
        return AuthenticatedCommandContext(
            principal_id=principal,
            process_execution_id=process_execution_id,
            received_at=timestamp,
            authority_id=self.authority_id,
            _issuer=self,
        )

    def verifies(self, context: AuthenticatedCommandContext) -> bool:
        return (
            type(context) is AuthenticatedCommandContext
            and context._issuer is self
            and context.authority_id == self.authority_id
            and (context.principal_id, context.process_execution_id) in self._bindings
        )


CanonicalEvidenceIds = Annotated[
    tuple[EvidenceId, ...],
    AfterValidator(_require_sorted_unique_evidence_ids),
]
ArtifactType = Annotated[
    str, Field(strict=True, min_length=1, pattern=r"^[a-z][a-z0-9_]{0,63}$")
]
PayloadSchemaVersion = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^automarkov\.[a-z0-9-]+\.v[1-9][0-9]*$",
    ),
]


def validate_task_request_json(raw: bytes) -> TaskRequest:
    """从 duplicate-aware 的原始 JSON bytes 构建任务请求。"""

    payload = parse_json_payload(raw)
    if type(payload) is not dict:
        raise ValueError("TaskRequest JSON root must be an object")
    return validate_task_request_payload(payload)


class ArtifactPutRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.artifact-put-request.v2"]
    artifact_type: ArtifactType
    payload_bytes: bytes = Field(strict=True, max_length=8 * 1024 * 1024)
    parent_artifact_ids: tuple[ArtifactId, ...]
    created_by: PrincipalId
    created_at: CanonicalUtcTimestamp
    source_evidence_ids: CanonicalEvidenceIds

    @field_validator("parent_artifact_ids")
    @classmethod
    def require_canonical_parents(
        cls, value: tuple[ArtifactId, ...]
    ) -> tuple[ArtifactId, ...]:
        expected = tuple(sorted(set(value), key=lambda item: item.root.encode("utf-8")))
        if value != expected:
            raise ValueError("parent_artifact_ids must be sorted and unique")
        return value


ArtifactPutInput = dict[str, object]
_ARTIFACT_PUT_KEYS = {
    "schema_version",
    "artifact_type",
    "payload_bytes",
    "parent_artifact_ids",
    "created_by",
    "created_at",
    "source_evidence_ids",
}


def validate_artifact_put_request(value: object) -> ArtifactPutRequest:
    """从未经构造的 exact-dict 命令建立可信写入请求。"""

    if type(value) is not dict:
        raise ValueError("ArtifactPutRequest ingress requires an exact dict")
    raw = cast(dict[object, object], value)
    if any(type(key) is not str for key in raw) or set(raw) != _ARTIFACT_PUT_KEYS:
        raise ValueError("ArtifactPutRequest ingress has an invalid keyset")
    payload_bytes = raw["payload_bytes"]
    if type(payload_bytes) is not bytes:
        raise ValueError("payload_bytes must be exact bytes")
    if len(payload_bytes) > MAX_JSON_PAYLOAD_BYTES:
        raise ValueError("payload_bytes exceeds byte limit")
    parent_ids = raw["parent_artifact_ids"]
    evidence_ids = raw["source_evidence_ids"]
    if type(parent_ids) is not list:
        raise ValueError("parent_artifact_ids must be a raw string list")
    if type(evidence_ids) is not list:
        raise ValueError("source_evidence_ids must be a raw string list")
    if len(parent_ids) + len(evidence_ids) + 13 > MAX_JSON_NODES:
        raise ValueError("ArtifactPutRequest metadata exceeds resource limits")
    metadata = {
        cast(str, key): item for key, item in raw.items() if key != "payload_bytes"
    }
    validate_and_measure_raw_json_tree(metadata)

    normalized = cast(dict[str, object], dict(raw))
    normalized["parent_artifact_ids"] = tuple(
        ArtifactId(root=cast(str, item)) for item in cast(list[object], parent_ids)
    )
    normalized["source_evidence_ids"] = tuple(cast(list[str], evidence_ids))
    return ArtifactPutRequest.model_validate(normalized, strict=True)


class ArtifactEnvelope(StrictFrozenModel):
    artifact_type: ArtifactType
    schema_version: PayloadSchemaVersion
    schema_id: Annotated[str, Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]
    payload_media_type: Literal["application/vnd.automarkov.canonical-payload+json"]
    payload_hash: Annotated[str, Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]
    parent_artifact_ids: tuple[ArtifactId, ...]
    created_by: PrincipalId
    created_at: CanonicalUtcTimestamp
    source_evidence_ids: CanonicalEvidenceIds

    @field_validator("parent_artifact_ids")
    @classmethod
    def require_canonical_parents(
        cls, value: tuple[ArtifactId, ...]
    ) -> tuple[ArtifactId, ...]:
        expected = tuple(sorted(set(value), key=lambda item: item.root.encode("utf-8")))
        if value != expected:
            raise ValueError("parent_artifact_ids must be sorted and unique")
        return value


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


class CanonicalPayloadDocument(StrictFrozenModel):
    schema_id: Annotated[str, Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]
    exact_float_paths: tuple[str, ...]
    payload: CanonicalJsonValue


class ArtifactBytesResult(StrictFrozenModel):
    schema_version: Literal["automarkov.artifact-bytes-result.v2"]
    artifact_id: ArtifactId
    envelope: ArtifactEnvelope
    payload_bytes: bytes = Field(
        strict=True,
        max_length=MAX_CANONICAL_DOCUMENT_BYTES,
    )
    payload_document: CanonicalPayloadDocument


class ArtifactLineageResult(StrictFrozenModel):
    schema_version: Literal["automarkov.artifact-lineage-result.v1"]
    artifact_ids: tuple[ArtifactId, ...]


class PackageResult(StrictFrozenModel):
    schema_version: Literal["automarkov.package-result.v1"]
    run_id: RunId
    package_artifact_id: ArtifactId


EvidenceSearchRequest = SearchEvidenceRequest
EvidenceUrlsRequest = ExtractEvidenceRequest
EvidenceCrawlRequest = CrawlEvidenceRequest
EvidenceResult = EvidenceGatewayResult


class SandboxRunRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.sandbox-run-request.v1"]
    bundle_artifact_id: ArtifactId
    limits_artifact_id: ArtifactId


class SandboxTestRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.sandbox-test-request.v1"]
    bundle_artifact_id: ArtifactId
    test_plan_artifact_id: ArtifactId


class FixedCommitJobRequest(StrictFrozenModel):
    schema_version: Literal[
        "automarkov.fixed-commit-job-request.v1",
        "automarkov.fixed-commit-job-request.v2",
    ]
    job_manifest_artifact_id: ArtifactId | None = None
    specified_event_head: VerifiedEventHead | None = None
    job_manifest: ArtifactReference | None = None

    @model_validator(mode="after")
    def validate_versioned_reference_binding(self) -> FixedCommitJobRequest:
        legacy_version = self.schema_version.endswith(".v1")
        if legacy_version:
            if self.job_manifest_artifact_id is None:
                raise ValueError("v1 requires job_manifest_artifact_id")
            if self.specified_event_head is not None or self.job_manifest is not None:
                raise ValueError("v1 forbids v2 reference-binding fields")
            return self
        if self.job_manifest_artifact_id is not None:
            raise ValueError("v2 forbids job_manifest_artifact_id")
        if self.specified_event_head is None or self.job_manifest is None:
            raise ValueError("v2 requires specified_event_head and job_manifest")
        return self


class ExecutionResult(StrictFrozenModel):
    schema_version: Literal[
        "automarkov.execution-result.v1",
        "automarkov.execution-result.v2",
    ]
    terminal_record_artifact_id: ArtifactId
    process_terminal_record: ArtifactReference | None = None
    execution_attestation: ArtifactReference | None = None
    terminal_result: ArtifactReference | None = None

    @model_validator(mode="after")
    def validate_versioned_fixed_commit_references(self) -> ExecutionResult:
        exact_references = (
            self.process_terminal_record,
            self.execution_attestation,
            self.terminal_result,
        )
        legacy_version = self.schema_version.endswith(".v1")
        if legacy_version:
            if any(reference is not None for reference in exact_references):
                raise ValueError("v1 forbids v2 fixed-commit references")
            return self
        if self.process_terminal_record is None or self.execution_attestation is None:
            raise ValueError(
                "v2 requires process_terminal_record and execution_attestation"
            )
        if (
            self.process_terminal_record.artifact_id
            != self.terminal_record_artifact_id.root
        ):
            raise ValueError(
                "terminal_record_artifact_id must match process_terminal_record"
            )
        return self


class RuntimeProfileRef(StrictFrozenModel):
    schema_version: Literal["automarkov.runtime-profile-ref.v1"]
    profile_id: RuntimeProfileId


class EnvironmentRef(StrictFrozenModel):
    schema_version: Literal["automarkov.environment-ref.v1"]
    environment_artifact_id: ArtifactId


class EnvironmentHandle(StrictFrozenModel):
    schema_version: Literal["automarkov.environment-handle.v1"]
    handle_id: EnvironmentHandleId


class CloseResult(StrictFrozenModel):
    schema_version: Literal["automarkov.close-result.v1"]
    closed: StrictTrue


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
    def dispatch(self, request: LifecycleCommandInput) -> LifecycleCommitResult: ...
    def resume(self, run_id: RunId, head: VerifiedEventHead) -> RunProjection: ...
    def package(self, run_id: RunId, head: VerifiedEventHead) -> PackageResult: ...


@runtime_checkable
class ArtifactRepository(Protocol):
    def put(self, request: ArtifactPutInput) -> ArtifactPutResult: ...
    def get(self, artifact_id: ArtifactId) -> ArtifactBytesResult: ...
    def commit(
        self,
        request: LifecycleCommandInput,
        *,
        context: AuthenticatedCommandContext,
    ) -> LifecycleCommitResult: ...
    def lineage(self, artifact_id: ArtifactId) -> ArtifactLineageResult: ...
    def project(
        self,
        run_id: RunId,
        as_of: VerifiedEventHead,
        *,
        projector_version: str,
        projector_hash: Sha256Digest,
    ) -> RunProjection: ...


@runtime_checkable
class LocalLlmRuntime(Protocol):
    def start(self, request: LlmStartRequest) -> LlmProbeResult: ...
    def probe(self) -> LlmProbeResult: ...
    def complete(self, request: LlmCompletionRequest) -> LlmCompletionResult: ...
    def close(self) -> CloseResult: ...


@runtime_checkable
class EvidenceGateway(Protocol):
    def search(
        self,
        request: SearchEvidenceRequest,
        *,
        context: AuthenticatedCommandContext,
    ) -> EvidenceGatewayResult: ...

    def extract(
        self,
        request: ExtractEvidenceRequest,
        *,
        context: AuthenticatedCommandContext,
    ) -> EvidenceGatewayResult: ...

    def crawl(
        self,
        request: CrawlEvidenceRequest,
        *,
        context: AuthenticatedCommandContext,
    ) -> EvidenceGatewayResult: ...


@runtime_checkable
class ExecutionSandbox(Protocol):
    def run(self, request: SandboxRunRequest) -> ExecutionResult: ...
    def test(self, request: SandboxTestRequest) -> ExecutionResult: ...
    def run_at_commit(self, request: FixedCommitJobRequest) -> ExecutionResult: ...


@runtime_checkable
class RemoteEnv(Protocol):
    def exchange(self, canonical_frame: bytes) -> bytes: ...


@runtime_checkable
class EnvironmentBinding(Protocol):
    def bind(
        self, profile: RuntimeProfileRef, env_ref: EnvironmentRef
    ) -> RemoteEnv: ...


@runtime_checkable
class TrainingRunner(Protocol):
    def train(self, request: TrainingRequest) -> TrainingResult: ...
    def evaluate(self, request: PolicyEvaluationRequest) -> PolicyEvaluationResult: ...
