from __future__ import annotations

import base64
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Never, cast
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automarkov.canonical import (
    canonical_json_bytes,
    validate_and_measure_raw_json_tree,
)
from automarkov.domain import (
    ArtifactId,
    RequestBudget,
    RequestPermissions,
    RunId,
    Sha256Digest,
    StrictFrozenModel,
    TaskRequest,
    VerifiedEventHead,
)
from automarkov.errors import (
    CapabilityDeferredError,
    RunIdCollisionError,
    RunProjectionHeadError,
    UnknownRunError,
)
from automarkov.fixed_commit_runner import (
    FixedCommitExecutionRequest,
    FixedCommitRunner,
)
from automarkov.lifecycle import (
    RUN_PROJECTOR_HASH,
    RUN_PROJECTOR_VERSION,
    AppendRunEventsCommand,
    ArtifactReference,
    LifecycleCommand,
    LifecycleCommitReceipt,
    LifecycleCommitResult,
    ManifestEventSigningKey,
    RunApprovalSecurityBinding,
    RunCreated,
    RunCreationSecurityBinding,
    RunEventActorCapability,
    RunEventSecurityContext,
    RunProjection,
    RunState,
    event_signature_preimage,
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
    CommandAuthority,
    CommandPrincipalBinding,
    EnvironmentRef,
    EvidenceCrawlRequest,
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
    RuntimeProfileRef,
    SandboxRunRequest,
    SandboxTestRequest,
    TrainingRequest,
    TrainingResult,
)
from automarkov.repository import InMemoryArtifactRepository, SqliteArtifactRepository
from automarkov.task_contracts import (
    RunCreationPolicy,
    RunManifestBootstrap,
    TaskApprovalPolicy,
)

__all__ = [
    "AttachedLocalLlmRuntime",
    "FixedCommitExecutionSandbox",
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


_BOOTSTRAP_PRINCIPAL_ID = "principal_orchestrator"
_BOOTSTRAP_SIGNING_KEY_ID = "key_orchestrator"
_BOOTSTRAP_AUTHORITY_ID = "authority_compiler_bootstrap"


def _canonical_now() -> tuple[str, int]:
    milliseconds = int(datetime.now(tz=UTC).timestamp() * 1_000)
    issued_at = datetime.fromtimestamp(milliseconds / 1_000, tz=UTC).isoformat(
        timespec="microseconds"
    )
    issued_at = issued_at.removesuffix("+00:00").rstrip("0").rstrip(".") + "Z"
    return issued_at, milliseconds


def _uuid7_at(milliseconds: int) -> str:
    random_bytes = secrets.token_bytes(10)
    raw = bytearray(milliseconds.to_bytes(6, "big") + random_bytes)
    raw[6] = 0x70 | (raw[6] & 0x0F)
    raw[8] = 0x80 | (raw[8] & 0x3F)
    return str(UUID(bytes=bytes(raw)))


def _artifact_put(
    repository: ArtifactRepository,
    *,
    artifact_type: str,
    model: StrictFrozenModel,
    parents: tuple[ArtifactReference, ...],
    created_at: str,
) -> ArtifactReference:
    payload = model.model_dump(mode="json", round_trip=True, warnings="error")
    result = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": artifact_type,
            "payload_bytes": canonical_json_bytes(payload),
            "parent_artifact_ids": sorted(
                {parent.artifact_id for parent in parents},
                key=lambda item: item.encode("utf-8"),
            ),
            "created_by": _BOOTSTRAP_PRINCIPAL_ID,
            "created_at": created_at,
            "source_evidence_ids": [],
        }
    )
    return ArtifactReference(
        artifact_id=result.artifact_id.root,
        payload_hash=result.payload_hash.root,
    )


class InMemoryCompiler:
    def __init__(
        self,
        run_id_factory: Callable[[], RunId] | None = None,
        repository: ArtifactRepository | None = None,
        command_context_provider: (
            Callable[[LifecycleCommand], AuthenticatedCommandContext] | None
        ) = None,
        command_authority: CommandAuthority | None = None,
        run_creation_signing_key: Ed25519PrivateKey | None = None,
    ) -> None:
        self._run_id_factory = run_id_factory or (
            lambda: RunId(root=f"run_{uuid4().hex}")
        )
        self._command_authority = command_authority or CommandAuthority(
            _BOOTSTRAP_AUTHORITY_ID,
            (CommandPrincipalBinding(_BOOTSTRAP_PRINCIPAL_ID, None),),
        )
        self._repository = repository or InMemoryArtifactRepository(
            command_authority=self._command_authority
        )
        self._command_context_provider = command_context_provider or (
            lambda command: self._command_authority.issue(
                command.actor_principal_id,
                None,
                command.issued_at,
            )
        )
        self._run_creation_signing_key = (
            run_creation_signing_key or Ed25519PrivateKey.generate()
        )
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
        issued_at, issued_at_ms = _canonical_now()
        request_reference = _artifact_put(
            self._repository,
            artifact_type="task_request",
            model=validated_request,
            parents=(),
            created_at=issued_at,
        )
        creation_policy_reference = _artifact_put(
            self._repository,
            artifact_type="run_creation_policy",
            model=RunCreationPolicy(
                schema_version="automarkov.run-creation-policy.v1",
                policy_version="v1",
                creation_principal_id=_BOOTSTRAP_PRINCIPAL_ID,
                signing_key_id=_BOOTSTRAP_SIGNING_KEY_ID,
                max_clock_skew_ms=5_000,
            ),
            parents=(),
            created_at=issued_at,
        )
        approval_policy_reference = _artifact_put(
            self._repository,
            artifact_type="task_approval_policy",
            model=TaskApprovalPolicy(
                schema_version="automarkov.task-approval-policy.v1",
                policy_kind="interactive_user",
                policy_version="v1",
                approval_principal_id=_BOOTSTRAP_PRINCIPAL_ID,
                signing_key_id=_BOOTSTRAP_SIGNING_KEY_ID,
                policy_source_hash=None,
                policy_image_hash=None,
                allowed_artifact_type="task_contract",
                required_report_artifact_types=(
                    "task_contract_traceability_report",
                    "text_critic_report",
                ),
                approved_reason_code="text_approved",
                rejected_reason_code="text_rejected",
            ),
            parents=(),
            created_at=issued_at,
        )
        public_key = self._run_creation_signing_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        public_key_b64url = base64.urlsafe_b64encode(public_key).decode().rstrip("=")
        security_context = RunEventSecurityContext(
            schema_version="automarkov.run-event-security-context.v1",
            run_id=run_id.root,
            experiment_id=None,
            root_ordinal=0,
            creation_policy=creation_policy_reference,
            max_clock_skew_ms=5_000,
            actor_capabilities=(
                RunEventActorCapability(
                    principal_id=_BOOTSTRAP_PRINCIPAL_ID,
                    process_execution_id=None,
                    allowed_event_types=("RunCreated", "SignedApprovalEvent"),
                ),
            ),
            signing_keys=(
                ManifestEventSigningKey(
                    signing_key_id=_BOOTSTRAP_SIGNING_KEY_ID,
                    principal_id=_BOOTSTRAP_PRINCIPAL_ID,
                    signature_algorithm="Ed25519",
                    public_key_b64url=public_key_b64url,
                    not_before="1970-01-01T00:00:00Z",
                    not_after="9999-12-31T23:59:59Z",
                    revoked_at=None,
                ),
            ),
            run_creation=RunCreationSecurityBinding(
                creation_principal_id=_BOOTSTRAP_PRINCIPAL_ID,
                signing_key_id=_BOOTSTRAP_SIGNING_KEY_ID,
            ),
            approval=RunApprovalSecurityBinding(
                approval_principal_id=_BOOTSTRAP_PRINCIPAL_ID,
                approval_principal_kind="interactive_user",
                signing_key_id=_BOOTSTRAP_SIGNING_KEY_ID,
                policy_contract=approval_policy_reference,
                policy_source_hash=None,
                policy_image_hash=None,
                policy_version="v1",
                revocation_authorities=(),
            ),
        )
        manifest_reference = _artifact_put(
            self._repository,
            artifact_type="run_manifest",
            model=RunManifestBootstrap(
                schema_version="automarkov.run-manifest-bootstrap.v1",
                manifest_kind="bootstrap",
                run_id=run_id.root,
                experiment_id=None,
                root_ordinal=0,
                task_request=request_reference,
                event_security_context=security_context,
                created_at=issued_at,
            ),
            parents=(
                approval_policy_reference,
                creation_policy_reference,
                request_reference,
            ),
            created_at=issued_at,
        )
        event_payload: dict[str, object] = {
            "schema_version": "automarkov.run-created.v1",
            "event_type": "RunCreated",
            "signing_domain": "AutoMarkov-Run-Created-v1",
            "event_id": _uuid7_at(issued_at_ms),
            "experiment_id": None,
            "run_id": run_id.root,
            "sequence_no": 0,
            "previous_event_hash": "sha256:" + "0" * 64,
            "actor_principal_id": _BOOTSTRAP_PRINCIPAL_ID,
            "issued_at": issued_at,
            "run_manifest_artifact_id": manifest_reference.artifact_id,
            "run_manifest_payload_hash": manifest_reference.payload_hash,
            "initial_state": RunState.RECEIVED.value,
            "creation_principal_id": _BOOTSTRAP_PRINCIPAL_ID,
            "reason_code": "run_created",
            "nonce_b64url": base64.urlsafe_b64encode(secrets.token_bytes(16))
            .decode()
            .rstrip("="),
            "signing_key_id": _BOOTSTRAP_SIGNING_KEY_ID,
            "signature_algorithm": "Ed25519",
            "signature_b64url": "A" * 86,
        }
        unsigned_event = RunCreated.model_validate(event_payload, strict=True)
        event_payload["signature_b64url"] = (
            base64.urlsafe_b64encode(
                self._run_creation_signing_key.sign(
                    event_signature_preimage(unsigned_event)
                )
            )
            .decode()
            .rstrip("=")
        )
        command_payload = {
            "schema_version": "automarkov.lifecycle-command.v1",
            "command_type": "append_run_events",
            "command_id": _uuid7_at(issued_at_ms),
            "actor_principal_id": _BOOTSTRAP_PRINCIPAL_ID,
            "issued_at": issued_at,
            "idempotency_key": f"bootstrap_{run_id.root}",
            "run_id": run_id.root,
            "expected_state": None,
            "expected_head": None,
            "events": [event_payload],
        }
        command = validate_lifecycle_command(command_payload)
        context = self._command_context_provider(command)
        if type(context) is not AuthenticatedCommandContext:
            raise ValueError("command context provider returned an invalid context")
        receipt = self._repository.commit(command_payload, context=context)
        if (
            not isinstance(receipt, LifecycleCommitReceipt)
            or receipt.before_head is not None
            or receipt.run_id != run_id.root
            or receipt.after_head.sequence_no != 0
            or receipt.run_view.state is not RunState.RECEIVED
            or len(receipt.event_records) != 1
            or receipt.event_records[0].event_hash != receipt.after_head.event_hash
            or not isinstance(receipt.event_records[0].event, RunCreated)
            or receipt.event_records[0].event.run_manifest_artifact_id
            != manifest_reference.artifact_id
            or receipt.event_records[0].event.run_manifest_payload_hash
            != manifest_reference.payload_hash
        ):
            raise ValueError("repository returned an invalid run bootstrap receipt")
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
    def search(
        self,
        request: EvidenceSearchRequest,
        *,
        context: AuthenticatedCommandContext,
    ) -> EvidenceResult:
        _deferred("evidence.search", "T10")

    def extract(
        self,
        request: EvidenceUrlsRequest,
        *,
        context: AuthenticatedCommandContext,
    ) -> EvidenceResult:
        _deferred("evidence.extract", "T10")

    def crawl(
        self,
        request: EvidenceCrawlRequest,
        *,
        context: AuthenticatedCommandContext,
    ) -> EvidenceResult:
        _deferred("evidence.crawl", "T10")


class ScriptedExecutionSandbox:
    def __init__(
        self,
        *,
        run_handler: Callable[[SandboxRunRequest], ExecutionResult] | None = None,
    ) -> None:
        self._run_handler = run_handler

    def run(self, request: SandboxRunRequest) -> ExecutionResult:
        if self._run_handler is None:
            raise ValueError("sandbox run requires a configured deep implementation")
        return self._run_handler(request)

    def test(self, request: SandboxTestRequest) -> ExecutionResult:
        _deferred("sandbox.test", "T15")

    def run_at_commit(self, request: FixedCommitJobRequest) -> ExecutionResult:
        raise ValueError("fixed-commit execution requires FixedCommitExecutionSandbox")


class FixedCommitExecutionSandbox:
    def __init__(self, runner: FixedCommitRunner) -> None:
        self._runner = runner

    def run(self, request: SandboxRunRequest) -> ExecutionResult:
        raise ValueError("fixed-commit adapter only supports run_at_commit")

    def test(self, request: SandboxTestRequest) -> ExecutionResult:
        raise ValueError("fixed-commit adapter only supports run_at_commit")

    def run_at_commit(self, request: FixedCommitJobRequest) -> ExecutionResult:
        if request.schema_version != "automarkov.fixed-commit-job-request.v2":
            raise ValueError("fixed-commit execution requires request schema v2")
        specified_event_head = request.specified_event_head
        job_manifest = request.job_manifest
        if specified_event_head is None or job_manifest is None:
            raise ValueError("fixed-commit request v2 reference binding is incomplete")
        result = self._runner.run_at_commit(
            FixedCommitExecutionRequest(
                schema_version="automarkov.fixed-commit-execution-request.v1",
                specified_event_head=specified_event_head,
                job_manifest=job_manifest,
            )
        )
        return ExecutionResult(
            schema_version="automarkov.execution-result.v2",
            terminal_record_artifact_id=ArtifactId(
                root=result.process_terminal_record.artifact_id
            ),
            process_terminal_record=result.process_terminal_record,
            execution_attestation=result.execution_attestation,
            terminal_result=result.terminal_result,
        )


class ScriptedRemoteEnv:
    def exchange(self, canonical_frame: bytes) -> bytes:
        _deferred("remote_env.exchange", "T12")

    def close(self) -> CloseResult:
        _deferred("remote_env.close", "T12")


class InMemoryEnvironmentBinding:
    def __init__(
        self,
        *,
        bind_handler: (
            Callable[[RuntimeProfileRef, EnvironmentRef], object] | None
        ) = None,
    ) -> None:
        self._bind_handler = bind_handler

    def bind(
        self, profile: RuntimeProfileRef, env_ref: EnvironmentRef
    ) -> ScriptedRemoteEnv:
        if self._bind_handler is None:
            raise ValueError(
                "environment bind requires a configured deep implementation"
            )
        return cast(ScriptedRemoteEnv, self._bind_handler(profile, env_ref))


class ScriptedTrainingRunner:
    def train(self, request: TrainingRequest) -> TrainingResult:
        _deferred("training.train", "T18")

    def evaluate(self, request: PolicyEvaluationRequest) -> PolicyEvaluationResult:
        _deferred("training.evaluate", "T19")
