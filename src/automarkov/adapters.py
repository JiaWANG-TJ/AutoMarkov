from __future__ import annotations

from collections.abc import Callable
from typing import Never
from uuid import uuid4

from automarkov.canonical import validate_and_measure_raw_json_tree
from automarkov.domain import (
    RequestBudget,
    RequestPermissions,
    RunId,
    Sha256Digest,
    TaskRequest,
    VerifiedEventHead,
)
from automarkov.errors import (
    CapabilityDeferredError,
    RunIdCollisionError,
    RunProjectionHeadError,
    UnknownRunError,
)
from automarkov.lifecycle import (
    RUN_PROJECTOR_HASH,
    RUN_PROJECTOR_VERSION,
    AppendRunEventsCommand,
    LifecycleCommand,
    LifecycleCommitResult,
    RunProjection,
    validate_lifecycle_command,
)
from automarkov.local_llm_runtime import (
    AttachedLocalLlmRuntime,
    PrivilegedUnixRuntimeConnectionProvider,
)
from automarkov.public import (
    ArtifactRepository,
    AuthenticatedCommandContext,
    CloseResult,
    EnvironmentRef,
    EvidenceCrawlRequest,
    EvidenceResolveRequest,
    EvidenceResult,
    EvidenceSearchRequest,
    EvidenceUrlsRequest,
    ExecutionResult,
    FixedCommitJobRequest,
    LifecycleCommandInput,
    LlmCompletionRequest,
    LlmCompletionResult,
    LlmProbeResult,
    LlmStartRequest,
    PackageResult,
    PolicyEvaluationRequest,
    PolicyEvaluationResult,
    RemoteEnvResetRequest,
    RemoteEnvResult,
    RemoteEnvSpacesResult,
    RemoteEnvStepRequest,
    RuntimeProfileRef,
    SandboxRunRequest,
    SandboxTestRequest,
    TrainingRequest,
    TrainingResult,
)
from automarkov.repository import InMemoryArtifactRepository, SqliteArtifactRepository

__all__ = [
    "AttachedLocalLlmRuntime",
    "InMemoryArtifactRepository",
    "InMemoryCompiler",
    "InMemoryEnvironmentBinding",
    "PrivilegedUnixRuntimeConnectionProvider",
    "ScriptedEvidenceGateway",
    "ScriptedExecutionSandbox",
    "ScriptedLocalLlmRuntime",
    "ScriptedRemoteEnv",
    "ScriptedTrainingRunner",
    "SqliteArtifactRepository",
]


def _deferred(capability: str, owner_ticket: str) -> Never:
    raise CapabilityDeferredError(capability, owner_ticket)


class InMemoryCompiler:
    def __init__(
        self,
        run_id_factory: Callable[[], RunId] | None = None,
        repository: ArtifactRepository | None = None,
        command_context_provider: (
            Callable[[LifecycleCommand], AuthenticatedCommandContext] | None
        ) = None,
    ) -> None:
        self._run_id_factory = run_id_factory or (
            lambda: RunId(root=f"run_{uuid4().hex}")
        )
        self._repository = repository or InMemoryArtifactRepository()
        self._command_context_provider = command_context_provider
        self._runs: dict[str, str] = {}

    def start(self, request: TaskRequest) -> RunId:
        if type(request) is not TaskRequest or not request.has_validated_provenance():
            raise ValueError("compiler start requires a validated exact TaskRequest")
        raw_request = dict(request.__dict__)
        for field_name, model_type in (
            ("budget", RequestBudget),
            ("permissions", RequestPermissions),
        ):
            nested = raw_request.get(field_name)
            if type(nested) is not model_type or not nested.has_validated_provenance():
                raise ValueError(f"TaskRequest.{field_name} has invalid provenance")
            raw_request[field_name] = nested.model_dump(mode="python", warnings="error")
        validated_request = TaskRequest.model_validate(
            raw_request,
            strict=True,
        )
        run_id = self._run_id_factory()
        if run_id.root in self._runs:
            raise RunIdCollisionError(run_id.root)
        self._runs[run_id.root] = validated_request.request_id
        return run_id

    def dispatch(self, request: LifecycleCommandInput) -> LifecycleCommitResult:
        if type(request) is not dict:
            raise ValueError("compiler dispatch requires an exact raw command object")
        validate_and_measure_raw_json_tree(request)
        command = validate_lifecycle_command(request)
        if (
            isinstance(command, AppendRunEventsCommand)
            and command.expected_head is None
            and command.run_id not in self._runs
        ):
            raise UnknownRunError(command.run_id)
        if self._command_context_provider is None:
            raise ValueError("compiler dispatch requires a command context provider")
        context = self._command_context_provider(command)
        if type(context) is not AuthenticatedCommandContext:
            raise ValueError("command context provider returned an invalid context")
        return self._repository.commit(request, context=context)

    def resume(self, run_id: RunId, head: VerifiedEventHead) -> RunProjection:
        if type(run_id) is not RunId or type(head) is not VerifiedEventHead:
            raise ValueError("compiler resume requires exact run and verified head IDs")
        if head.run_id != run_id:
            raise RunProjectionHeadError(run_id.root)
        try:
            return self._repository.project(
                run_id,
                head,
                projector_version=RUN_PROJECTOR_VERSION,
                projector_hash=Sha256Digest(root=RUN_PROJECTOR_HASH),
            )
        except UnknownRunError as error:
            if run_id.root not in self._runs:
                raise
            raise RunProjectionHeadError(run_id.root) from error

    def package(self, run_id: RunId, head: VerifiedEventHead) -> PackageResult:
        if type(run_id) is not RunId or type(head) is not VerifiedEventHead:
            raise ValueError(
                "compiler package requires exact run and verified head IDs"
            )
        if run_id.root not in self._runs:
            raise UnknownRunError(run_id.root)
        if head.run_id != run_id:
            raise RunProjectionHeadError(run_id.root)
        _deferred("compiler.package", "T24")


class ScriptedLocalLlmRuntime:
    def start(self, request: LlmStartRequest) -> LlmProbeResult:
        _deferred("llm.start", "T05")

    def probe(self) -> LlmProbeResult:
        _deferred("llm.probe", "T05")

    def complete(self, request: LlmCompletionRequest) -> LlmCompletionResult:
        _deferred("llm.complete", "T05")

    def close(self) -> CloseResult:
        _deferred("llm.close", "T05")


class ScriptedEvidenceGateway:
    def search(self, request: EvidenceSearchRequest) -> EvidenceResult:
        _deferred("evidence.search", "T10")

    def extract(self, request: EvidenceUrlsRequest) -> EvidenceResult:
        _deferred("evidence.extract", "T10")

    def crawl(self, request: EvidenceCrawlRequest) -> EvidenceResult:
        _deferred("evidence.crawl", "T10")

    def resolve(self, request: EvidenceResolveRequest) -> EvidenceResult:
        _deferred("evidence.resolve", "T10")


class ScriptedExecutionSandbox:
    def run(self, request: SandboxRunRequest) -> ExecutionResult:
        _deferred("sandbox.run", "T11")

    def test(self, request: SandboxTestRequest) -> ExecutionResult:
        _deferred("sandbox.test", "T15")

    def run_at_commit(self, request: FixedCommitJobRequest) -> ExecutionResult:
        _deferred("sandbox.run_at_commit", "T17")


class ScriptedRemoteEnv:
    def reset(self, request: RemoteEnvResetRequest) -> RemoteEnvResult:
        _deferred("remote_env.reset", "T12")

    def step(self, request: RemoteEnvStepRequest) -> RemoteEnvResult:
        _deferred("remote_env.step", "T12")

    def spaces(self) -> RemoteEnvSpacesResult:
        _deferred("remote_env.spaces", "T12")

    def close(self) -> CloseResult:
        _deferred("remote_env.close", "T12")


class InMemoryEnvironmentBinding:
    def bind(
        self, profile: RuntimeProfileRef, env_ref: EnvironmentRef
    ) -> ScriptedRemoteEnv:
        _deferred("environment.bind", "T11")


class ScriptedTrainingRunner:
    def train(self, request: TrainingRequest) -> TrainingResult:
        _deferred("training.train", "T18")

    def evaluate(self, request: PolicyEvaluationRequest) -> PolicyEvaluationResult:
        _deferred("training.evaluate", "T19")
