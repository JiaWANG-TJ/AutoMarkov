from __future__ import annotations

import base64
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Annotated, Literal, Protocol, Self, TypeAlias, TypeVar, cast
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import AfterValidator, Field, TypeAdapter, model_validator

from automarkov.contracts.task import FixedCommitRunAuthorization, RunManifest
from automarkov.domain.canonical import (
    CanonicalPayloadCodec,
    FrozenSequence,
    PositiveSafeCanonicalInt,
    canonical_json_bytes,
)
from automarkov.domain.ids import (
    NonEmptyId,
    PrincipalIdValue,
    RequestIdValue,
    RunIdValue,
    Sha256Value,
)
from automarkov.domain.models import (
    ArtifactId,
    CanonicalNonce,
    RunId,
    Sha256Digest,
    StrictFrozenModel,
    VerifiedEventHead,
)
from automarkov.fixed_commit_runner import (
    CapabilityDecisionLog,
    EgressDecisionLog,
    FixedCommitExecutionResult,
    FixedCommitJobManifest,
    MountAttestation,
    PhaseNetworkPolicy,
    RunnerArtifactReferencePayload,
    RunnerExecutionCheckpoint,
    RunnerInput,
    RunnerOutputBinding,
    RunnerReplayError,
    RunnerTerminalCommitReceipt,
    verify_execution_attestation_signature,
)
from automarkov.lifecycle import (
    RUN_PROJECTOR_HASH,
    RUN_PROJECTOR_VERSION,
    ArtifactReference,
    CanonicalTimestamp,
    ExecutionAttestation,
    LifecycleCommitReceipt,
    ManifestEventSigningKey,
    ProcessExecutionTerminalRecord,
    StageGatePassed,
    TerminalResult,
    ValidationFailed,
    _event_hash,
    validate_lifecycle_command,
)
from automarkov.public import ArtifactRepository, AuthenticatedCommandContext
from automarkov.security.provenance import RuntimeProfileManifest


def _decode_canonical_b64url(value: str, expected_length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError as error:
        raise ValueError("value must be canonical unpadded base64url") from error
    if (
        len(decoded) != expected_length
        or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value
    ):
        raise ValueError("value must be canonical unpadded base64url")
    return decoded


def _require_signature(value: str) -> str:
    _decode_canonical_b64url(value, 64)
    return value


Ed25519Signature = Annotated[
    str,
    Field(strict=True, pattern=r"^[A-Za-z0-9_-]{86}$"),
    AfterValidator(_require_signature),
]


def _reference_key(reference: ArtifactReference) -> tuple[str, str]:
    return reference.artifact_id, reference.payload_hash


class _SignedE2EArtifact(StrictFrozenModel):
    issued_at: CanonicalTimestamp
    nonce_b64url: CanonicalNonce
    signature_algorithm: Literal["Ed25519"]
    signature_b64url: Ed25519Signature


class E2EGateEvaluationRequest(_SignedE2EArtifact):
    schema_version: Literal["automarkov.e2e-gate-evaluation-request.v1"]
    signing_domain: Literal["AutoMarkov-E2E-Gate-Evaluation-Request-v1"]
    request_id: RequestIdValue
    experiment_id: NonEmptyId
    run_id: RunIdValue
    run_manifest: ArtifactReference
    specified_event_head: VerifiedEventHead
    candidate_validation_freeze: ArtifactReference
    candidate_bundle: ArtifactReference
    task_contract: ArtifactReference
    decision_process_spec: ArtifactReference
    environment_binding: ArtifactReference
    candidate_worker_profile_id: NonEmptyId
    candidate_worker_profile_hash: Sha256Value
    evaluator_protocol_id: NonEmptyId
    evaluator_protocol_hash: Sha256Value
    evaluator_profile_id: NonEmptyId
    evaluator_profile_hash: Sha256Value
    evaluator_lock_hash: Sha256Value
    evaluator_image_hash: Sha256Value
    evaluator_schema_id: NonEmptyId
    evaluator_schema_hash: Sha256Value
    gold_worker_profile_id: NonEmptyId
    gold_worker_profile_hash: Sha256Value
    not_before: CanonicalTimestamp
    expires_at: CanonicalTimestamp
    coordinator_principal_id: PrincipalIdValue
    coordinator_key_id: NonEmptyId

    @model_validator(mode="after")
    def require_distinct_exact_subjects(self) -> Self:
        subjects = (
            self.run_manifest,
            self.candidate_validation_freeze,
            self.candidate_bundle,
            self.task_contract,
            self.decision_process_spec,
            self.environment_binding,
        )
        if len({_reference_key(subject) for subject in subjects}) != len(subjects):
            raise ValueError("E2E request subjects must be distinct exact references")
        issued_at = datetime.fromisoformat(self.issued_at)
        if (
            len(
                {
                    self.candidate_worker_profile_id,
                    self.gold_worker_profile_id,
                    self.evaluator_profile_id,
                }
            )
            != 3
            or self.specified_event_head.run_id.root != self.run_id
            or not datetime.fromisoformat(self.not_before)
            <= issued_at
            <= datetime.fromisoformat(self.expires_at)
        ):
            raise ValueError("E2E request profiles or validity window are invalid")
        return self


class E2EGateVerdict(_SignedE2EArtifact):
    schema_version: Literal["automarkov.e2e-gate-verdict.v1"]
    signing_domain: Literal["AutoMarkov-E2E-Gate-Verdict-v1"]
    verdict_id: NonEmptyId
    request_id: RequestIdValue
    request_payload_hash: Sha256Value
    run_id: RunIdValue
    run_manifest: ArtifactReference
    candidate_bundle: ArtifactReference
    task_contract: ArtifactReference
    decision_process_spec: ArtifactReference
    environment_binding: ArtifactReference
    text_passed: bool = Field(strict=True)
    formal_passed: bool = Field(strict=True)
    api_passed: bool = Field(strict=True)
    hidden_behavior_passed: bool = Field(strict=True)
    evaluator_principal_id: PrincipalIdValue
    evaluator_key_id: NonEmptyId

    @property
    def e2e_valid(self) -> bool:
        return all(
            (
                self.text_passed,
                self.formal_passed,
                self.api_passed,
                self.hidden_behavior_passed,
            )
        )


class E2EGateExecutionCommitInput(StrictFrozenModel):
    schema_version: Literal["automarkov.e2e-execution-commit-input.v1"]
    runner_fingerprint: Sha256Value
    process_execution_terminal_record: ArtifactReference


class E2EGateKeyPolicy(StrictFrozenModel):
    schema_version: Literal["automarkov.e2e-key-policy.v1"]
    key_id: NonEmptyId
    principal_id: PrincipalIdValue
    principal_kind: Literal[
        "coordinator",
        "evaluator",
        "candidate_worker",
        "gold_worker",
        "comparator",
    ]
    public_key_b64url: Annotated[
        str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{43}$")
    ]
    valid_from: CanonicalTimestamp
    valid_until: CanonicalTimestamp
    revoked_at: CanonicalTimestamp | None

    @model_validator(mode="after")
    def require_valid_key_interval(self) -> Self:
        _decode_canonical_b64url(self.public_key_b64url, 32)
        valid_from = datetime.fromisoformat(self.valid_from)
        valid_until = datetime.fromisoformat(self.valid_until)
        if valid_from >= valid_until or (
            self.revoked_at is not None
            and not valid_from <= datetime.fromisoformat(self.revoked_at) < valid_until
        ):
            raise ValueError("E2E key validity interval is invalid")
        return self

    def public_key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(
            _decode_canonical_b64url(self.public_key_b64url, 32)
        )


E2EPrincipalKind: TypeAlias = Literal[
    "coordinator",
    "evaluator",
    "candidate_worker",
    "gold_worker",
    "comparator",
]


class E2EKeyPolicyResolver(Protocol):
    def resolve(
        self,
        *,
        run_id: str,
        specified_event_head: VerifiedEventHead,
        run_manifest: ArtifactReference,
        key_id: str,
        principal_id: str,
        principal_kind: E2EPrincipalKind,
    ) -> E2EGateKeyPolicy: ...


class TrustedEvaluatorProtocol(StrictFrozenModel):
    schema_version: Literal["automarkov.trusted-evaluator-protocol.v1"]
    evaluator_protocol_id: NonEmptyId
    evaluator_protocol_hash: Sha256Value
    evaluator_profile_id: NonEmptyId
    evaluator_profile_hash: Sha256Value
    evaluator_lock_hash: Sha256Value
    evaluator_image_hash: Sha256Value
    evaluator_schema_id: NonEmptyId
    evaluator_schema_hash: Sha256Value
    candidate_worker_profile_id: NonEmptyId
    candidate_worker_profile_hash: Sha256Value
    gold_worker_profile_id: NonEmptyId
    gold_worker_profile_hash: Sha256Value


class E2EProtocolResolver(Protocol):
    def resolve(self, protocol_id: str) -> TrustedEvaluatorProtocol: ...


class E2ERunnerGrantResolver(Protocol):
    def resolve(
        self,
        *,
        run_id: str,
        specified_event_head: VerifiedEventHead,
        run_manifest: ArtifactReference,
        job_manifest: ArtifactReference,
        principal_id: str,
    ) -> ResolvedSealedWorkerExecution: ...


class ResolvedSealedWorkerExecution(StrictFrozenModel):
    """由 specified root 解析出的实际 job、profile 与输入身份。"""

    runner_key_grant: ManifestEventSigningKey
    job_manifest: ArtifactReference
    principal_id: PrincipalIdValue
    profile_id: NonEmptyId
    profile_hash: Sha256Value
    image_digest: Sha256Value
    input_sources: FrozenSequence[ArtifactReference]

    @model_validator(mode="after")
    def require_closed_input_sources(self) -> Self:
        keys = tuple(_reference_key(reference) for reference in self.input_sources)
        if keys != tuple(sorted(set(keys), key=lambda item: item[0].encode("utf-8"))):
            raise ValueError("resolved worker inputs must be sorted and unique")
        return self


class ArtifactRepositoryE2ERunnerGrantResolver:
    """从 exact root RunManifest v2 解析 FixedCommitRunner 独立签名 grant。"""

    def __init__(self, repository: ArtifactRepository) -> None:
        self._repository = repository

    def _load(
        self,
        reference: ArtifactReference,
        artifact_type: str,
        model_type: type[ModelT],
    ) -> ModelT:
        stored = self._repository.get(ArtifactId(root=reference.artifact_id))
        payload = stored.payload_document.model_dump(mode="json")["payload"]
        if (
            stored.envelope.artifact_type != artifact_type
            or stored.envelope.payload_hash != reference.payload_hash
            or type(payload) is not dict
        ):
            raise ValueError("runner authorization artifact binding is invalid")
        return model_type.model_validate(payload, strict=True)

    def resolve(
        self,
        *,
        run_id: str,
        specified_event_head: VerifiedEventHead,
        run_manifest: ArtifactReference,
        job_manifest: ArtifactReference,
        principal_id: str,
    ) -> ResolvedSealedWorkerExecution:
        projection = self._repository.project(
            RunId(root=run_id),
            specified_event_head,
            projector_version=RUN_PROJECTOR_VERSION,
            projector_hash=Sha256Digest(root=RUN_PROJECTOR_HASH),
        )
        if projection.run_manifest != run_manifest:
            raise ValueError("runner authorization is not from the specified root")
        manifest = self._load(run_manifest, "run_manifest", RunManifest)
        matches = tuple(
            authorization
            for authorization in manifest.sealed_worker_authorizations
            if authorization.principal_id == principal_id
            and authorization.job_manifest == job_manifest
        )
        if len(matches) != 1:
            raise ValueError("sealed worker authorization is unavailable")
        worker_authorization = matches[0]
        authorization = self._load(
            worker_authorization.fixed_commit_authorization,
            "fixed_commit_run_authorization",
            FixedCommitRunAuthorization,
        )
        grant = authorization.runner_key_grant
        job = self._load(
            job_manifest,
            "fixed_commit_job_manifest",
            FixedCommitJobManifest,
        )
        profile = self._load(
            job.profile_manifest,
            "runtime_profile_manifest",
            RuntimeProfileManifest,
        )
        input_sources = tuple(
            sorted(
                (
                    self._load(reference, "runner_input", RunnerInput).source_artifact
                    for reference in job.input_artifacts
                ),
                key=lambda item: item.artifact_id.encode("utf-8"),
            )
        )
        if (
            manifest.root_ordinal != 0
            or manifest.run_id != run_id
            or authorization.job_manifest != job_manifest
            or grant.principal_id != principal_id
            or grant
            != manifest.event_security_context.signing_key(grant.signing_key_id)
            or job.principal_id != principal_id
            or job.profile_id != profile.profile_id
            or job.profile_lock_hash != profile.lock_hash
            or job.image_digest != profile.image_digest
        ):
            raise ValueError("runner authorization is not rooted in the run manifest")
        return ResolvedSealedWorkerExecution(
            runner_key_grant=grant,
            job_manifest=job_manifest,
            principal_id=job.principal_id,
            profile_id=job.profile_id,
            profile_hash=profile.manifest_hash,
            image_digest=job.image_digest,
            input_sources=input_sources,
        )


class ArtifactRepositoryE2EKeyPolicyResolver:
    """从 specified root manifest 解析 sealed E2E 身份与公钥。"""

    def __init__(self, repository: ArtifactRepository) -> None:
        self._repository = repository

    def resolve(
        self,
        *,
        run_id: str,
        specified_event_head: VerifiedEventHead,
        run_manifest: ArtifactReference,
        key_id: str,
        principal_id: str,
        principal_kind: E2EPrincipalKind,
    ) -> E2EGateKeyPolicy:
        projection = self._repository.project(
            RunId(root=run_id),
            specified_event_head,
            projector_version=RUN_PROJECTOR_VERSION,
            projector_hash=Sha256Digest(root=RUN_PROJECTOR_HASH),
        )
        if projection.run_manifest != run_manifest:
            raise ValueError("E2E key policy is not from the specified root")
        stored = self._repository.get(ArtifactId(root=run_manifest.artifact_id))
        payload = stored.payload_document.model_dump(mode="json")["payload"]
        if (
            stored.envelope.artifact_type != "run_manifest"
            or stored.envelope.payload_hash != run_manifest.payload_hash
            or type(payload) is not dict
        ):
            raise ValueError("E2E root manifest binding is invalid")
        manifest = RunManifest.model_validate(payload, strict=True)
        authorities = tuple(
            authority
            for authority in manifest.sealed_e2e_signing_authorities
            if authority.principal_kind == principal_kind
            and authority.principal_id == principal_id
            and authority.signing_key_id == key_id
        )
        if (
            manifest.root_ordinal != 0
            or manifest.run_id != run_id
            or len(authorities) != 1
        ):
            raise ValueError("E2E signing authority is unavailable")
        key = manifest.event_security_context.signing_key(key_id)
        if key.principal_id != principal_id:
            raise ValueError("E2E signing authority is not rooted in the manifest")
        return E2EGateKeyPolicy(
            schema_version="automarkov.e2e-key-policy.v1",
            key_id=key.signing_key_id,
            principal_id=key.principal_id,
            principal_kind=principal_kind,
            public_key_b64url=key.public_key_b64url,
            valid_from=key.not_before,
            valid_until=key.not_after,
            revoked_at=key.revoked_at,
        )


class FrozenE2EProtocolRegistry:
    def __init__(self, protocols: tuple[TrustedEvaluatorProtocol, ...]) -> None:
        validated = tuple(
            _revalidate(protocol, TrustedEvaluatorProtocol) for protocol in protocols
        )
        identities = tuple(item.evaluator_protocol_id for item in validated)
        if not identities or len(identities) != len(set(identities)):
            raise ValueError("trusted E2E protocol identities must be unique")
        self._protocols = {item.evaluator_protocol_id: item for item in validated}

    def resolve(self, protocol_id: str) -> TrustedEvaluatorProtocol:
        try:
            return self._protocols[protocol_id]
        except KeyError as error:
            raise ValueError("E2E evaluator protocol is not trusted") from error


CandidateCapability = Literal["bounded_candidate_execute"]
GoldCapability = Literal["sealed_gold_execute"]
ComparatorCapability = Literal["sealed_compare"]
DeniedCapability = Literal[
    "arbitrary_ipc",
    "candidate_code",
    "generation",
    "network",
    "sealed_asset",
    "sealed_credential",
    "sealed_key",
    "sealed_locator",
    "training",
]


class WorkerIsolationPolicy(StrictFrozenModel):
    schema_version: Literal["automarkov.worker-isolation-policy.v1"]
    worker_kind: Literal["candidate", "gold", "comparator"]
    allowed_capabilities: FrozenSequence[
        CandidateCapability | GoldCapability | ComparatorCapability
    ]
    denied_capabilities: FrozenSequence[DeniedCapability]
    linux_denied_capabilities: FrozenSequence[str]
    network_access: bool = Field(strict=True)
    sealed_access: bool = Field(strict=True)
    candidate_code_access: bool = Field(strict=True)
    network_policy_hash: Sha256Value
    mount_table_hash: Sha256Value
    capability_decision_log_hash: Sha256Value
    egress_decision_log_hash: Sha256Value

    @model_validator(mode="after")
    def require_exact_worker_boundary(self) -> Self:
        if self.linux_denied_capabilities != ("capability:all",):
            raise ValueError("sealed workers must deny every Linux capability")
        if self.worker_kind == "candidate":
            if (
                tuple(self.allowed_capabilities) != ("bounded_candidate_execute",)
                or tuple(self.denied_capabilities)
                != (
                    "arbitrary_ipc",
                    "network",
                    "sealed_asset",
                    "sealed_credential",
                    "sealed_key",
                    "sealed_locator",
                )
                or self.network_access
                or self.sealed_access
                or not self.candidate_code_access
            ):
                raise ValueError("candidate worker isolation policy is not exact")
        elif self.worker_kind == "gold":
            if (
                tuple(self.allowed_capabilities) != ("sealed_gold_execute",)
                or tuple(self.denied_capabilities)
                != ("candidate_code", "generation", "network", "training")
                or self.network_access
                or not self.sealed_access
                or self.candidate_code_access
            ):
                raise ValueError("gold worker isolation policy is not exact")
        elif (
            tuple(self.allowed_capabilities) != ("sealed_compare",)
            or tuple(self.denied_capabilities)
            != ("candidate_code", "generation", "network", "training")
            or self.network_access
            or not self.sealed_access
            or self.candidate_code_access
        ):
            raise ValueError("comparator isolation policy is not exact")
        return self


WorkerOutputKind = Literal[
    "candidate_api",
    "candidate_behavior",
    "candidate_formal",
    "candidate_text",
    "gold_api",
    "gold_behavior",
    "gold_formal",
    "gold_text",
    "e2e_verdict",
]

_COMPARATOR_SUBJECT_OUTPUT_KINDS: tuple[WorkerOutputKind, ...] = (
    "candidate_api",
    "candidate_behavior",
    "candidate_formal",
    "candidate_text",
    "gold_api",
    "gold_behavior",
    "gold_formal",
    "gold_text",
)


class BoundedWorkerOutput(StrictFrozenModel):
    output_kind: WorkerOutputKind
    output_ref: ArtifactReference
    byte_length: PositiveSafeCanonicalInt

    @model_validator(mode="after")
    def require_bounded_output(self) -> Self:
        if self.byte_length > 8 * 1024 * 1024:
            raise ValueError("sealed worker output exceeds the bounded payload limit")
        return self


class SignedWorkerEvidence(_SignedE2EArtifact):
    schema_version: Literal["automarkov.signed-worker-evidence.v1"]
    signing_domain: Literal["AutoMarkov-Signed-Worker-Evidence-v1"]
    worker_id: NonEmptyId
    worker_kind: Literal["candidate", "gold", "comparator"]
    principal_id: PrincipalIdValue
    profile_id: NonEmptyId
    profile_hash: Sha256Value
    request_ref: ArtifactReference
    candidate_bundle: ArtifactReference
    task_contract: ArtifactReference
    decision_process_spec: ArtifactReference
    environment_binding: ArtifactReference
    job_manifest: ArtifactReference
    process_execution_id: NonEmptyId
    execution_attestation_ref: ArtifactReference
    isolation_policy_hash: Sha256Value
    subject_outputs: FrozenSequence[ArtifactReference]
    outputs: FrozenSequence[BoundedWorkerOutput]
    worker_key_id: NonEmptyId

    @model_validator(mode="after")
    def require_exact_typed_outputs(self) -> Self:
        kinds = tuple(output.output_kind for output in self.outputs)
        expected = {
            "candidate": (
                "candidate_api",
                "candidate_behavior",
                "candidate_formal",
                "candidate_text",
            ),
            "gold": ("gold_api", "gold_behavior", "gold_formal", "gold_text"),
            "comparator": ("e2e_verdict",),
        }[self.worker_kind]
        references = tuple(_reference_key(output.output_ref) for output in self.outputs)
        subject_references = tuple(
            _reference_key(reference) for reference in self.subject_outputs
        )
        subjects_are_exact = (
            len(subject_references) == len(_COMPARATOR_SUBJECT_OUTPUT_KINDS)
            and len(subject_references) == len(set(subject_references))
            if self.worker_kind == "comparator"
            else not subject_references
        )
        if (
            kinds != expected
            or len(references) != len(set(references))
            or not subjects_are_exact
        ):
            raise ValueError("worker outputs must be exact, typed, bounded, and unique")
        return self


class SealedWorkerBinding(StrictFrozenModel):
    worker_id: NonEmptyId
    principal_id: PrincipalIdValue
    profile_id: NonEmptyId
    profile_hash: Sha256Value
    process_execution_id: NonEmptyId
    job_manifest: ArtifactReference
    execution_attestation: ExecutionAttestation
    runner_output_refs: FrozenSequence[ArtifactReference]
    runner_outputs: FrozenSequence[RunnerOutputBinding]
    isolation_policy: WorkerIsolationPolicy
    network_policy: PhaseNetworkPolicy
    mount_attestation: MountAttestation
    capability_decision_log: CapabilityDecisionLog
    egress_decision_log: EgressDecisionLog
    evidence: SignedWorkerEvidence

    @model_validator(mode="after")
    def require_execution_attestation_binding(self) -> Self:
        attestation = self.execution_attestation
        policy = self.isolation_policy
        network_policy = self.network_policy
        mount_attestation = self.mount_attestation
        capability_log = self.capability_decision_log
        egress_log = self.egress_decision_log
        evidence = self.evidence
        if not (
            len(self.runner_output_refs)
            == len(self.runner_outputs)
            == len(evidence.outputs)
        ):
            raise ValueError("runner output wrappers do not match worker outputs")
        for persisted_ref, wrapper, bounded in zip(
            self.runner_output_refs,
            self.runner_outputs,
            evidence.outputs,
            strict=True,
        ):
            payload = RunnerArtifactReferencePayload.model_validate_json(
                wrapper.verified_content_bytes(), strict=True
            )
            expected_artifact_type = (
                "e2e_gate_verdict"
                if bounded.output_kind == "e2e_verdict"
                else bounded.output_kind
            )
            if (
                persisted_ref.payload_hash != e2e_payload_hash(wrapper)
                or payload.artifact_type != expected_artifact_type
                or payload.artifact != bounded.output_ref
            ):
                raise ValueError("runner output wrapper does not bind worker output")
        if (
            evidence.worker_id != self.worker_id
            or evidence.worker_kind != policy.worker_kind
            or evidence.principal_id != self.principal_id
            or evidence.profile_id != self.profile_id
            or evidence.profile_hash != self.profile_hash
            or evidence.job_manifest != self.job_manifest
            or evidence.process_execution_id != self.process_execution_id
            or evidence.execution_attestation_ref.payload_hash
            != e2e_payload_hash(attestation)
            or evidence.isolation_policy_hash != e2e_payload_hash(policy)
            or attestation.principal_id != self.principal_id
            or attestation.profile_id != self.profile_id
            or attestation.process_execution_id != self.process_execution_id
            or attestation.job_manifest != self.job_manifest
            or attestation.terminal_result is not None
            or tuple(attestation.payload_outputs)
            != tuple(
                sorted(
                    self.runner_output_refs,
                    key=lambda item: item.artifact_id.encode("utf-8"),
                )
            )
            or attestation.network_policy_hash != policy.network_policy_hash
            or attestation.mount_table_hash != policy.mount_table_hash
            or attestation.capability_decision_log_hash
            != policy.capability_decision_log_hash
            or attestation.egress_decision_log_hash != policy.egress_decision_log_hash
            or policy.network_policy_hash != e2e_payload_hash(network_policy)
            or policy.mount_table_hash != e2e_payload_hash(mount_attestation)
            or policy.capability_decision_log_hash != e2e_payload_hash(capability_log)
            or policy.egress_decision_log_hash != e2e_payload_hash(egress_log)
            or network_policy.egress_allowlist
            or network_policy.protocol_edges
            or network_policy.gateway_principal_id is not None
            or mount_attestation.job_manifest != self.job_manifest
            or capability_log.job_manifest != self.job_manifest
            or capability_log.denied_capabilities != policy.linux_denied_capabilities
            or egress_log.job_manifest != self.job_manifest
            or egress_log.decisions
            or attestation.actual_phase_transition.from_phase
            != f"{policy.worker_kind}_worker_started"
            or attestation.actual_phase_transition.to_phase
            != f"{policy.worker_kind}_outputs_committed"
        ):
            raise ValueError("worker execution attestation does not bind its policy")
        has_sealed_mount = any(
            mount.source_kind == "sealed_asset"
            for mount in mount_attestation.actual_mounts
        )
        if has_sealed_mount != policy.sealed_access:
            raise ValueError(
                "worker mount evidence does not match sealed access policy"
            )
        if policy.worker_kind == "comparator":
            mounted_subjects = tuple(
                (mount.target_path, mount.source_id)
                for mount in mount_attestation.actual_mounts
                if mount.source_kind == "input_artifact"
            )
            expected_subjects = tuple(
                (f"/mnt/automarkov/subjects/{kind}", subject.artifact_id)
                for kind, subject in zip(
                    _COMPARATOR_SUBJECT_OUTPUT_KINDS,
                    evidence.subject_outputs,
                    strict=True,
                )
            )
            if mounted_subjects != expected_subjects:
                raise ValueError(
                    "comparator mount evidence does not consume exact subject outputs"
                )
        return self


class SealedWorkerTopology(StrictFrozenModel):
    schema_version: Literal["automarkov.sealed-worker-topology.v1"]
    candidate: SealedWorkerBinding
    gold: SealedWorkerBinding
    comparator: SealedWorkerBinding

    @model_validator(mode="after")
    def require_independent_workers(self) -> Self:
        candidate = self.candidate
        gold = self.gold
        comparator = self.comparator
        if (
            candidate.isolation_policy.worker_kind != "candidate"
            or gold.isolation_policy.worker_kind != "gold"
            or comparator.isolation_policy.worker_kind != "comparator"
        ):
            raise ValueError("sealed worker roles are reversed")
        bindings = (candidate, gold, comparator)
        if any(
            len(values) != len(set(values))
            for values in (
                tuple(item.principal_id for item in bindings),
                tuple(item.profile_id for item in bindings),
                tuple(item.process_execution_id for item in bindings),
                tuple(_reference_key(item.job_manifest) for item in bindings),
            )
        ):
            raise ValueError(
                "candidate, gold, and comparator workers must be independent"
            )
        expected_subject_outputs = tuple(
            output.output_ref
            for binding in (candidate, gold)
            for output in binding.evidence.outputs
        )
        if tuple(comparator.evidence.subject_outputs) != expected_subject_outputs:
            raise ValueError(
                "comparator evidence does not bind exact candidate and gold outputs"
            )
        return self


class E2EGateDecision(StrictFrozenModel):
    schema_version: Literal["automarkov.e2e-gate-decision.v1"]
    next_state: Literal["TRAINING_SMOKE_TESTING", "PARTIAL", "FAILED"]
    terminal: bool = Field(strict=True)
    e2e_valid: bool = Field(strict=True)
    training_outcome_missing: bool = Field(strict=True)
    failure_class: Literal["gate_false", "integrity"] | None
    retry_permitted: Literal[False]

    @model_validator(mode="after")
    def require_closed_state_mapping(self) -> Self:
        expected = {
            "TRAINING_SMOKE_TESTING": (False, True, False, None),
            "PARTIAL": (True, False, True, "gate_false"),
            "FAILED": (True, False, True, "integrity"),
        }[self.next_state]
        if (
            self.terminal,
            self.e2e_valid,
            self.training_outcome_missing,
            self.failure_class,
        ) != expected:
            raise ValueError("E2E gate decision does not match its terminal mapping")
        return self


class E2EGateCommitCommand(StrictFrozenModel):
    schema_version: Literal["automarkov.e2e-gate-commit-command.v1"]
    request_ref: ArtifactReference
    verdict_ref: ArtifactReference
    request_id: RequestIdValue
    verdict_id: NonEmptyId
    request_nonce_b64url: CanonicalNonce
    verdict_nonce_b64url: CanonicalNonce
    coordinator_key_id: NonEmptyId
    evaluator_key_id: NonEmptyId
    run_id: RunIdValue
    run_manifest: ArtifactReference
    specified_event_head: VerifiedEventHead
    candidate_bundle: ArtifactReference
    topology_ref: ArtifactReference
    request_payload_hash: Sha256Value
    verdict_payload_hash: Sha256Value
    topology_payload_hash: Sha256Value
    candidate_worker_key_id: NonEmptyId | None = None
    gold_worker_key_id: NonEmptyId | None = None
    comparator_worker_key_id: NonEmptyId | None = None
    runner_fingerprint: Sha256Value | None = None
    process_execution_terminal_record: ArtifactReference | None = None
    decision: E2EGateDecision
    committed_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_materialized_payload_bindings(self) -> Self:
        if (
            self.request_ref.payload_hash != self.request_payload_hash
            or self.verdict_ref.payload_hash != self.verdict_payload_hash
            or self.topology_ref.payload_hash != self.topology_payload_hash
            or self.specified_event_head.run_id.root != self.run_id
        ):
            raise ValueError("E2E commit references must bind canonical payloads")
        if self.decision.terminal and (
            self.runner_fingerprint is None
            or self.process_execution_terminal_record is None
        ):
            raise ValueError("terminal E2E commits require an exact runner checkpoint")
        return self


class E2EGateCommitResult(StrictFrozenModel):
    schema_version: Literal["automarkov.e2e-gate-commit-result.v1"]
    materialization_id: Sha256Value
    command_fingerprint: Sha256Value
    request_ref: ArtifactReference
    verdict_ref: ArtifactReference
    candidate_bundle: ArtifactReference
    decision: E2EGateDecision
    terminal_reason_code: (
        Literal["sealed_e2e_gate_failed", "sealed_e2e_integrity_failed"] | None
    )
    outcome_e2e_valid: Literal[0, 1]
    training_outcome_missing: bool = Field(strict=True)
    materialization_backend: Literal["test_memory", "test_sqlite", "artifact_lifecycle"]
    atomic_receipt_id: Sha256Value
    process_execution_terminal_record: ArtifactReference | None = None
    terminal_result: ArtifactReference | None = None
    execution_attestation: ArtifactReference | None = None
    committed_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_atomic_terminal_materialization(self) -> Self:
        if self.decision.next_state == "PARTIAL":
            expected = ("sealed_e2e_gate_failed", 0, True)
        elif self.decision.next_state == "FAILED":
            expected = ("sealed_e2e_integrity_failed", 0, True)
        else:
            expected = (None, 1, False)
        if (
            self.terminal_reason_code,
            self.outcome_e2e_valid,
            self.training_outcome_missing,
        ) != expected:
            raise ValueError("E2E terminal materialization is not closed")
        if self.materialization_backend == "artifact_lifecycle" and (
            self.process_execution_terminal_record is None
            or self.execution_attestation is None
            or self.terminal != (self.terminal_result is not None)
        ):
            raise ValueError(
                "production terminal materialization requires runner evidence"
            )
        return self

    @property
    def next_state(self) -> Literal["TRAINING_SMOKE_TESTING", "PARTIAL", "FAILED"]:
        return self.decision.next_state

    @property
    def terminal(self) -> bool:
        return self.decision.terminal

    @property
    def e2e_valid(self) -> bool:
        return self.decision.e2e_valid

    @property
    def retry_permitted(self) -> Literal[False]:
        return self.decision.retry_permitted


class E2EGateCommitError(RuntimeError):
    pass


class E2EGateCommitter(Protocol):
    def commit(self, command: E2EGateCommitCommand) -> E2EGateCommitResult: ...


class ArtifactLifecycleAtomicReceipt(StrictFrozenModel):
    schema_version: Literal["automarkov.artifact-lifecycle-e2e-receipt.v1"]
    command_fingerprint: Sha256Value
    atomic_receipt_id: Sha256Value
    request_ref: ArtifactReference
    verdict_ref: ArtifactReference
    candidate_bundle: ArtifactReference
    topology_ref: ArtifactReference
    terminal_state: Literal["TRAINING_SMOKE_TESTING", "PARTIAL", "FAILED"]
    terminal_reason_code: (
        Literal["sealed_e2e_gate_failed", "sealed_e2e_integrity_failed"] | None
    )
    outcome_e2e_valid: Literal[0, 1]
    training_outcome_missing: bool = Field(strict=True)
    lifecycle_command_fingerprint: Sha256Value
    lifecycle_after_head_hash: Sha256Value
    process_execution_terminal_record: ArtifactReference | None
    terminal_result: ArtifactReference | None
    execution_attestation: ArtifactReference | None
    committed_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_runner_terminal_evidence(self) -> Self:
        terminal = self.terminal_state in {"PARTIAL", "FAILED"}
        if (
            self.process_execution_terminal_record is None
            or self.execution_attestation is None
            or terminal != (self.terminal_result is not None)
        ):
            raise ValueError("terminal E2E receipt requires complete runner evidence")
        return self


class E2EGateLifecycleMaterializer(Protocol):
    def materialize_atomically(
        self, command: E2EGateCommitCommand
    ) -> ArtifactLifecycleAtomicReceipt: ...


class E2EGateLifecyclePlanProvider(Protocol):
    def plan(
        self, command: E2EGateCommitCommand
    ) -> tuple[Mapping[str, object], AuthenticatedCommandContext]: ...

    def finalize(
        self,
        command: E2EGateCommitCommand,
        lifecycle_result: object,
    ) -> FixedCommitExecutionResult | None: ...


class E2EGateLifecyclePlanConfig(StrictFrozenModel):
    actor_principal_id: PrincipalIdValue
    process_execution_id: NonEmptyId
    budget_snapshot: ArtifactReference
    runner_fingerprint: Sha256Value | None = None
    process_execution_terminal_record: ArtifactReference | None = None

    @model_validator(mode="after")
    def require_paired_runner_checkpoint(self) -> Self:
        if (self.runner_fingerprint is None) != (
            self.process_execution_terminal_record is None
        ):
            raise ValueError("runner checkpoint configuration must be paired")
        return self


class E2ERunnerCheckpointResolver(Protocol):
    def checkpointed(
        self,
        *,
        job_id: str,
        process_execution_id: str,
        fingerprint: str,
        process_reference: ArtifactReference,
    ) -> RunnerExecutionCheckpoint: ...

    def finalize(
        self,
        *,
        fingerprint: str,
        checkpoint: RunnerExecutionCheckpoint,
        terminal_receipt: RunnerTerminalCommitReceipt,
        terminal_result: TerminalResult,
    ) -> FixedCommitExecutionResult: ...

    def finalize_nonterminal(
        self,
        *,
        fingerprint: str,
        checkpoint: RunnerExecutionCheckpoint,
        issued_at: str,
    ) -> FixedCommitExecutionResult: ...


def _e2e_lifecycle_uuid7(issued_at: str, seed: str) -> str:
    timestamp_ms = int(datetime.fromisoformat(issued_at).timestamp() * 1_000)
    random_bits = int.from_bytes(sha256(seed.encode("utf-8")).digest()[:10], "big")
    value = (
        timestamp_ms << 80
        | 7 << 76
        | (random_bits >> 64 & 0xFFF) << 64
        | 2 << 62
        | random_bits & ((1 << 62) - 1)
    )
    return str(UUID(int=value))


class ArtifactRepositoryE2EGateLifecyclePlan:
    """从 E2E command 与指定 head 机械生成唯一 lifecycle command。"""

    def __init__(
        self,
        repository: ArtifactRepository,
        config: E2EGateLifecyclePlanConfig,
        context_provider: Callable[[str, str, str], AuthenticatedCommandContext],
        runner_finalizer: E2ERunnerCheckpointResolver,
    ) -> None:
        self._repository = repository
        self._config = _revalidate(config, E2EGateLifecyclePlanConfig)
        self._context_provider = context_provider
        self._runner_finalizer = runner_finalizer

    def plan(
        self, command: E2EGateCommitCommand
    ) -> tuple[Mapping[str, object], AuthenticatedCommandContext]:
        command = _revalidate(command, E2EGateCommitCommand)
        config = self._config
        projection = self._repository.project(
            RunId(root=command.run_id),
            command.specified_event_head,
            projector_version=RUN_PROJECTOR_VERSION,
            projector_hash=Sha256Digest(root=RUN_PROJECTOR_HASH),
        )
        if (
            projection.state.value != "SEALED_E2E_VALIDATING"
            or projection.event_head.sequence_no
            != command.specified_event_head.sequence_no
            or projection.event_head.event_hash
            != command.specified_event_head.event_hash.root
            or projection.run_manifest != command.run_manifest
        ):
            raise ValueError("E2E lifecycle plan requires the exact sealed head")
        issued_at = command.committed_at
        fingerprint = _command_fingerprint(command)
        event_common = {
            "experiment_id": projection.experiment_id,
            "run_id": command.run_id,
            "actor_principal_id": config.actor_principal_id,
            "actor_process_execution_id": config.process_execution_id,
            "issued_at": issued_at,
        }
        cause_id = _e2e_lifecycle_uuid7(issued_at, fingerprint + ":cause")
        transition_id = _e2e_lifecycle_uuid7(issued_at, fingerprint + ":transition")
        if (
            command.runner_fingerprint is None
            or command.process_execution_terminal_record is None
            or config.runner_fingerprint != command.runner_fingerprint
            or config.process_execution_terminal_record
            != command.process_execution_terminal_record
        ):
            raise RunnerReplayError(
                "E2E command does not bind the configured runner checkpoint"
            )
        stored_process = self._repository.get(
            ArtifactId(root=command.process_execution_terminal_record.artifact_id)
        )
        if (
            stored_process.envelope.artifact_type != "process_execution_terminal_record"
            or stored_process.envelope.payload_hash
            != command.process_execution_terminal_record.payload_hash
        ):
            raise RunnerReplayError("E2E process artifact identity is invalid")
        process = ProcessExecutionTerminalRecord.model_validate(
            stored_process.payload_document.model_dump(mode="json")["payload"],
            strict=True,
        )
        checkpoint = self._runner_finalizer.checkpointed(
            job_id=process.job_id,
            process_execution_id=process.process_execution_id,
            fingerprint=command.runner_fingerprint,
            process_reference=command.process_execution_terminal_record,
        )
        stored_job = self._repository.get(
            ArtifactId(root=process.job_manifest.artifact_id)
        )
        if (
            stored_job.envelope.artifact_type != "fixed_commit_job_manifest"
            or stored_job.envelope.payload_hash != process.job_manifest.payload_hash
        ):
            raise RunnerReplayError("E2E runner job identity is invalid")
        job = FixedCommitJobManifest.model_validate(
            stored_job.payload_document.model_dump(mode="json")["payload"],
            strict=True,
        )
        stored_request = self._repository.get(
            ArtifactId(root=command.request_ref.artifact_id)
        )
        if stored_request.envelope.artifact_type != "e2e_gate_evaluation_request":
            raise RunnerReplayError("E2E request artifact identity is invalid")
        canonical_request_ref = ArtifactReference(
            artifact_id=command.request_ref.artifact_id,
            payload_hash=stored_request.envelope.payload_hash,
        )
        if (
            command.decision.next_state != "FAILED"
            and canonical_request_ref != command.request_ref
        ):
            raise RunnerReplayError("E2E request artifact identity is invalid")
        evaluation_request = E2EGateEvaluationRequest.model_validate(
            stored_request.payload_document.model_dump(mode="json")["payload"],
            strict=True,
        )
        stored_verdict = self._repository.get(
            ArtifactId(root=command.verdict_ref.artifact_id)
        )
        if stored_verdict.envelope.artifact_type != "e2e_gate_verdict":
            raise RunnerReplayError("E2E verdict artifact identity is invalid")
        canonical_verdict_ref = ArtifactReference(
            artifact_id=command.verdict_ref.artifact_id,
            payload_hash=stored_verdict.envelope.payload_hash,
        )
        if (
            command.decision.next_state != "FAILED"
            and canonical_verdict_ref != command.verdict_ref
        ):
            raise RunnerReplayError("E2E verdict artifact identity is invalid")
        stored_topology = self._repository.get(
            ArtifactId(root=command.topology_ref.artifact_id)
        )
        if stored_topology.envelope.artifact_type != "sealed_worker_topology":
            raise RunnerReplayError("E2E topology artifact identity is invalid")
        canonical_topology_ref = ArtifactReference(
            artifact_id=command.topology_ref.artifact_id,
            payload_hash=stored_topology.envelope.payload_hash,
        )
        if (
            command.decision.next_state != "FAILED"
            and canonical_topology_ref != command.topology_ref
        ):
            raise RunnerReplayError("E2E topology artifact identity is invalid")
        subjects = tuple(
            sorted(
                (
                    canonical_request_ref,
                    canonical_verdict_ref,
                    command.candidate_bundle,
                ),
                key=lambda item: item.artifact_id.encode("utf-8"),
            )
        )
        expected_input_sources = tuple(
            sorted(
                (
                    evaluation_request.candidate_validation_freeze,
                    command.candidate_bundle,
                ),
                key=lambda item: item.artifact_id.encode("utf-8"),
            )
        )
        actual_input_sources: list[ArtifactReference] = []
        for input_reference in job.input_artifacts:
            stored_input = self._repository.get(
                ArtifactId(root=input_reference.artifact_id)
            )
            if (
                stored_input.envelope.artifact_type != "runner_input"
                or stored_input.envelope.payload_hash != input_reference.payload_hash
            ):
                raise RunnerReplayError("E2E job input is not a runner input wrapper")
            runner_input = RunnerInput.model_validate(
                stored_input.payload_document.model_dump(mode="json")["payload"],
                strict=True,
            )
            source = self._repository.get(
                ArtifactId(root=runner_input.source_artifact.artifact_id)
            )
            if (
                source.envelope.artifact_type != runner_input.source_artifact_type
                or source.envelope.payload_hash
                != runner_input.source_artifact.payload_hash
            ):
                raise RunnerReplayError("E2E runner input source identity is invalid")
            actual_input_sources.append(runner_input.source_artifact)
        output_references: list[ArtifactReference] = []
        for output_reference in process.payload_outputs:
            stored_output = self._repository.get(
                ArtifactId(root=output_reference.artifact_id)
            )
            if (
                stored_output.envelope.artifact_type != "runner_output_binding"
                or stored_output.envelope.payload_hash != output_reference.payload_hash
            ):
                raise RunnerReplayError("E2E output is not a runner output wrapper")
            output = RunnerOutputBinding.model_validate(
                stored_output.payload_document.model_dump(mode="json")["payload"],
                strict=True,
            )
            artifact_output = RunnerArtifactReferencePayload.model_validate_json(
                output.verified_content_bytes(), strict=True
            )
            if artifact_output.artifact_type != "e2e_gate_verdict":
                raise RunnerReplayError(
                    "E2E output wrapper has the wrong artifact type"
                )
            output_references.append(artifact_output.artifact)
        if (
            checkpoint.process != process
            or process.run_id != command.run_id
            or process.experiment_id != projection.experiment_id
            or process.principal_id != config.actor_principal_id
            or process.process_execution_id != config.process_execution_id
            or process.status != "success"
            or process.exit_code != 0
            or tuple(
                sorted(
                    actual_input_sources,
                    key=lambda item: item.artifact_id.encode("utf-8"),
                )
            )
            != expected_input_sources
            or tuple(output_references) != (canonical_verdict_ref,)
        ):
            raise RunnerReplayError(
                "E2E checkpoint graph does not bind this evaluation"
            )
        if command.decision.next_state == "TRAINING_SMOKE_TESTING":
            cause = StageGatePassed.model_validate(
                event_common
                | {
                    "schema_version": "automarkov.stage-gate-passed.v1",
                    "event_type": "StageGatePassed",
                    "event_id": cause_id,
                    "sequence_no": projection.event_head.sequence_no + 1,
                    "previous_event_hash": projection.event_head.event_hash,
                    "gate_id": "SEALED_E2E",
                    "gate_version": "v1",
                    "gate_contract_hash": canonical_topology_ref.payload_hash,
                    "subject_artifact_references": subjects,
                    "gate_report": canonical_verdict_ref,
                    "from_state": "SEALED_E2E_VALIDATING",
                    "to_state": "TRAINING_SMOKE_TESTING",
                    "reason_code": "sealed_e2e_passed",
                    "result": "passed",
                },
                strict=True,
            )
            transition = event_common | {
                "schema_version": "automarkov.state-transitioned.v1",
                "event_type": "StateTransitioned",
                "event_id": transition_id,
                "sequence_no": cause.sequence_no + 1,
                "previous_event_hash": _event_hash(cause),
                "from_state": "SEALED_E2E_VALIDATING",
                "to_state": "TRAINING_SMOKE_TESTING",
                "trigger_event_id": cause.event_id,
                "trigger_event_hash": _event_hash(cause),
                "input_artifact_ids": tuple(
                    subject.artifact_id for subject in subjects
                ),
                "gate_report_artifact_id": canonical_verdict_ref.artifact_id,
                "gate_report_payload_hash": canonical_verdict_ref.payload_hash,
                "budget_snapshot_artifact_id": config.budget_snapshot.artifact_id,
                "budget_snapshot_payload_hash": config.budget_snapshot.payload_hash,
                "reason_code": "sealed_e2e_passed",
            }
            request: dict[str, object] = {
                "schema_version": "automarkov.lifecycle-command.v1",
                "command_type": "append_run_events",
                "command_id": _e2e_lifecycle_uuid7(issued_at, fingerprint + ":command"),
                "actor_principal_id": config.actor_principal_id,
                "issued_at": issued_at,
                "idempotency_key": f"e2e-gate:{fingerprint}",
                "run_id": command.run_id,
                "expected_state": "SEALED_E2E_VALIDATING",
                "expected_head": projection.event_head.model_dump(mode="json"),
                "events": [cause.model_dump(mode="json"), transition],
            }
        else:
            failure_code = cast(
                Literal["sealed_e2e_gate_failed", "sealed_e2e_integrity_failed"],
                (
                    "sealed_e2e_gate_failed"
                    if command.decision.next_state == "PARTIAL"
                    else "sealed_e2e_integrity_failed"
                ),
            )
            cause = ValidationFailed.model_validate(
                event_common
                | {
                    "schema_version": "automarkov.validation-failed.v1",
                    "event_type": "ValidationFailed",
                    "event_id": cause_id,
                    "sequence_no": projection.event_head.sequence_no + 1,
                    "previous_event_hash": projection.event_head.event_hash,
                    "subject": command.candidate_bundle,
                    "report": canonical_verdict_ref,
                    "validator_id": "sealed_e2e_gate",
                    "validator_version": "v1",
                    "validation_level": "terminal",
                    "validation_scope": "sealed_e2e",
                    "failure_code": failure_code,
                },
                strict=True,
            )
            transition = event_common | {
                "schema_version": "automarkov.state-transitioned.v1",
                "event_type": "StateTransitioned",
                "event_id": transition_id,
                "sequence_no": cause.sequence_no + 1,
                "previous_event_hash": _event_hash(cause),
                "from_state": "SEALED_E2E_VALIDATING",
                "to_state": command.decision.next_state,
                "trigger_event_id": cause.event_id,
                "trigger_event_hash": _event_hash(cause),
                "input_artifact_ids": tuple(
                    subject.artifact_id for subject in subjects
                ),
                "gate_report_artifact_id": canonical_verdict_ref.artifact_id,
                "gate_report_payload_hash": canonical_verdict_ref.payload_hash,
                "budget_snapshot_artifact_id": config.budget_snapshot.artifact_id,
                "budget_snapshot_payload_hash": config.budget_snapshot.payload_hash,
                "reason_code": failure_code,
            }
            request = {
                "schema_version": "automarkov.lifecycle-command.v1",
                "command_type": "commit_terminal",
                "command_id": _e2e_lifecycle_uuid7(issued_at, fingerprint + ":command"),
                "actor_principal_id": config.actor_principal_id,
                "issued_at": issued_at,
                "idempotency_key": f"e2e-gate:{fingerprint}",
                "run_id": command.run_id,
                "expected_state": "SEALED_E2E_VALIDATING",
                "expected_head": projection.event_head.model_dump(mode="json"),
                "events": [cause.model_dump(mode="json"), transition],
                "process_terminal_record": {
                    "schema_version": (
                        "automarkov.process-execution-terminal-record.v1"
                    ),
                    "signing_domain": ("AutoMarkov-ProcessExecutionTerminalRecord-v1"),
                    "experiment_id": projection.experiment_id,
                    "run_id": command.run_id,
                    **process.model_dump(mode="json"),
                },
                "fixed_commit_job_manifest": process.job_manifest,
                "terminal_time_approvals": [],
                "projector_version": RUN_PROJECTOR_VERSION,
                "projector_hash": RUN_PROJECTOR_HASH,
                "created_at": issued_at,
            }
        lifecycle_command = validate_lifecycle_command(
            TypeAdapter(dict[str, object]).dump_python(
                request,
                mode="json",
                warnings="error",
            )
        )
        context = self._context_provider(
            config.actor_principal_id,
            config.process_execution_id,
            issued_at,
        )
        return lifecycle_command.model_dump(mode="json"), context

    def finalize(
        self,
        command: E2EGateCommitCommand,
        lifecycle_result: object,
    ) -> FixedCommitExecutionResult | None:
        if not command.decision.terminal:
            if (
                command.runner_fingerprint is None
                or command.process_execution_terminal_record is None
                or not isinstance(lifecycle_result, LifecycleCommitReceipt)
            ):
                raise RunnerReplayError("E2E success checkpoint is unavailable")
            stored_process = self._repository.get(
                ArtifactId(root=command.process_execution_terminal_record.artifact_id)
            )
            process = ProcessExecutionTerminalRecord.model_validate(
                stored_process.payload_document.model_dump(mode="json")["payload"],
                strict=True,
            )
            checkpoint = self._runner_finalizer.checkpointed(
                job_id=process.job_id,
                process_execution_id=process.process_execution_id,
                fingerprint=command.runner_fingerprint,
                process_reference=command.process_execution_terminal_record,
            )
            return self._runner_finalizer.finalize_nonterminal(
                fingerprint=command.runner_fingerprint,
                checkpoint=checkpoint,
                issued_at=command.committed_at,
            )
        if not isinstance(lifecycle_result, LifecycleCommitReceipt):
            raise RunnerReplayError("E2E lifecycle terminal receipt is invalid")
        process_ref = lifecycle_result.process_execution_terminal_record
        terminal_ref = lifecycle_result.terminal_result
        if process_ref is None or terminal_ref is None:
            raise RunnerReplayError("E2E terminal artifacts are unavailable")
        process_result = self._repository.get(ArtifactId(root=process_ref.artifact_id))
        terminal_result = self._repository.get(
            ArtifactId(root=terminal_ref.artifact_id)
        )
        checkpoint = self._runner_finalizer.checkpointed(
            job_id=process_result.payload_document.model_dump(mode="json")["payload"][
                "job_id"
            ],
            process_execution_id=process_result.payload_document.model_dump(
                mode="json"
            )["payload"]["process_execution_id"],
            fingerprint=cast(str, command.runner_fingerprint),
            process_reference=process_ref,
        )
        terminal = TerminalResult.model_validate(
            terminal_result.payload_document.model_dump(mode="json")["payload"],
            strict=True,
        )
        return self._runner_finalizer.finalize(
            fingerprint=cast(str, command.runner_fingerprint),
            checkpoint=checkpoint,
            terminal_receipt=RunnerTerminalCommitReceipt(
                schema_version="automarkov.runner-terminal-commit-receipt.v1",
                process_terminal_record=process_ref,
                terminal_result=terminal_ref,
            ),
            terminal_result=terminal,
        )


class E2EAtomicArtifactRepository(Protocol):
    def materialize_e2e_gate_atomically(
        self,
        command: E2EGateCommitCommand,
        plan_provider: E2EGateLifecyclePlanProvider,
    ) -> ArtifactLifecycleAtomicReceipt: ...


class ArtifactRepositoryE2EGateLifecycleMaterializer:
    def __init__(
        self,
        repository: E2EAtomicArtifactRepository,
        plan_provider: E2EGateLifecyclePlanProvider,
    ) -> None:
        if not hasattr(repository, "materialize_e2e_gate_atomically") or not hasattr(
            plan_provider, "plan"
        ):
            raise TypeError("E2E materializer requires repository atomic CAS seams")
        self._repository = repository
        self._plan_provider = plan_provider

    def materialize_atomically(
        self, command: E2EGateCommitCommand
    ) -> ArtifactLifecycleAtomicReceipt:
        return self._repository.materialize_e2e_gate_atomically(
            command, self._plan_provider
        )


class ArtifactLifecycleE2EGateCommitter:
    """生产 adapter：仅接受 ArtifactRepository/lifecycle 返回的原子收据。"""

    def __init__(self, materializer: E2EGateLifecycleMaterializer) -> None:
        if not hasattr(materializer, "materialize_atomically"):
            raise TypeError(
                "production E2E committer requires a lifecycle materializer"
            )
        self._materializer = materializer

    def commit(self, command: E2EGateCommitCommand) -> E2EGateCommitResult:
        command = _revalidate(command, E2EGateCommitCommand)
        receipt = _revalidate(
            self._materializer.materialize_atomically(command),
            ArtifactLifecycleAtomicReceipt,
        )
        fingerprint = _command_fingerprint(command)
        decision = command.decision
        if decision.next_state == "PARTIAL":
            expected = ("sealed_e2e_gate_failed", 0, True)
        elif decision.next_state == "FAILED":
            expected = ("sealed_e2e_integrity_failed", 0, True)
        else:
            expected = (None, 1, False)
        if (
            receipt.command_fingerprint != fingerprint
            or receipt.request_ref != command.request_ref
            or receipt.verdict_ref != command.verdict_ref
            or receipt.candidate_bundle != command.candidate_bundle
            or receipt.topology_ref != command.topology_ref
            or receipt.terminal_state != decision.next_state
            or (
                receipt.terminal_reason_code,
                receipt.outcome_e2e_valid,
                receipt.training_outcome_missing,
            )
            != expected
        ):
            raise E2EGateCommitError("lifecycle atomic receipt does not bind command")
        return _materialize(
            command,
            backend="artifact_lifecycle",
            atomic_receipt_id=receipt.atomic_receipt_id,
            process_execution_terminal_record=(
                receipt.process_execution_terminal_record
            ),
            terminal_result=receipt.terminal_result,
            execution_attestation=receipt.execution_attestation,
            committed_at=receipt.committed_at,
        )


def _command_fingerprint(command: E2EGateCommitCommand) -> str:
    payload = command.model_dump(mode="json", round_trip=True, warnings="error")
    del payload["committed_at"]
    return "sha256:" + sha256(canonical_json_bytes(payload)).hexdigest()


def _claim_key(value: object) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def _command_claims(command: E2EGateCommitCommand) -> tuple[tuple[str, str], ...]:
    return (
        (
            "candidate",
            _claim_key(
                {
                    "run_id": command.run_id,
                    "artifact_id": command.candidate_bundle.artifact_id,
                    "payload_hash": command.candidate_bundle.payload_hash,
                }
            ),
        ),
        ("request_id", command.request_id),
        (
            "request_nonce",
            _claim_key(
                {
                    "key_id": command.coordinator_key_id,
                    "nonce": command.request_nonce_b64url,
                }
            ),
        ),
        (
            "request_subject",
            _claim_key(
                {"key_id": command.coordinator_key_id, "run_id": command.run_id}
            ),
        ),
        ("verdict_id", command.verdict_id),
        (
            "verdict_nonce",
            _claim_key(
                {
                    "key_id": command.evaluator_key_id,
                    "nonce": command.verdict_nonce_b64url,
                }
            ),
        ),
        (
            "verdict_subject",
            _claim_key({"key_id": command.evaluator_key_id, "run_id": command.run_id}),
        ),
        (
            "candidate_worker",
            _claim_key(
                {"key_id": command.candidate_worker_key_id, "run_id": command.run_id}
            ),
        ),
        (
            "gold_worker",
            _claim_key(
                {"key_id": command.gold_worker_key_id, "run_id": command.run_id}
            ),
        ),
        (
            "comparator_worker",
            _claim_key(
                {"key_id": command.comparator_worker_key_id, "run_id": command.run_id}
            ),
        ),
    )


def _materialize(
    command: E2EGateCommitCommand,
    *,
    backend: Literal["test_memory", "test_sqlite", "artifact_lifecycle"],
    atomic_receipt_id: str,
    process_execution_terminal_record: ArtifactReference | None = None,
    terminal_result: ArtifactReference | None = None,
    execution_attestation: ArtifactReference | None = None,
    committed_at: CanonicalTimestamp | None = None,
) -> E2EGateCommitResult:
    fingerprint = _command_fingerprint(command)
    decision = command.decision
    return E2EGateCommitResult(
        schema_version="automarkov.e2e-gate-commit-result.v1",
        materialization_id=(
            "sha256:"
            + sha256(
                canonical_json_bytes(
                    {
                        "domain": "AutoMarkov-E2E-Materialization-v1",
                        "fingerprint": fingerprint,
                    }
                )
            ).hexdigest()
        ),
        command_fingerprint=fingerprint,
        request_ref=command.request_ref,
        verdict_ref=command.verdict_ref,
        candidate_bundle=command.candidate_bundle,
        decision=decision,
        terminal_reason_code=(
            "sealed_e2e_gate_failed"
            if decision.next_state == "PARTIAL"
            else (
                "sealed_e2e_integrity_failed"
                if decision.next_state == "FAILED"
                else None
            )
        ),
        outcome_e2e_valid=1 if decision.e2e_valid else 0,
        training_outcome_missing=decision.training_outcome_missing,
        materialization_backend=backend,
        atomic_receipt_id=atomic_receipt_id,
        process_execution_terminal_record=process_execution_terminal_record,
        terminal_result=terminal_result,
        execution_attestation=execution_attestation,
        committed_at=command.committed_at if committed_at is None else committed_at,
    )


class InMemoryE2EGateCommitter:
    """仅供测试使用的进程内原子提交器。"""

    def __init__(self) -> None:
        self._claims: dict[tuple[str, str], str] = {}
        self._results: dict[str, bytes] = {}
        self._lock = RLock()

    def commit(self, command: E2EGateCommitCommand) -> E2EGateCommitResult:
        command = _revalidate(command, E2EGateCommitCommand)
        fingerprint = _command_fingerprint(command)
        with self._lock:
            existing = self._results.get(fingerprint)
            if existing is not None:
                return E2EGateCommitResult.model_validate_json(existing, strict=True)
            claims = _command_claims(command)
            conflicts = tuple(
                claimed
                for claimed in (self._claims.get(claim) for claim in claims)
                if claimed is not None and claimed != fingerprint
            )
            if conflicts:
                prior = self._results.get(conflicts[0])
                if prior is not None:
                    existing_result = E2EGateCommitResult.model_validate_json(
                        prior, strict=True
                    )
                    if existing_result.decision.next_state == "FAILED":
                        return existing_result
                command = command.model_copy(
                    update={"decision": SealedE2EGate._decision("FAILED")}
                )
                command = _revalidate(command, E2EGateCommitCommand)
                fingerprint = _command_fingerprint(command)
            receipt_id = (
                "sha256:"
                + sha256(
                    canonical_json_bytes(
                        {"backend": "test_memory", "fingerprint": fingerprint}
                    )
                ).hexdigest()
            )
            result = _materialize(
                command, backend="test_memory", atomic_receipt_id=receipt_id
            )
            canonical = canonical_json_bytes(
                result.model_dump(mode="json", round_trip=True, warnings="error")
            )
            for claim in claims:
                self._claims.setdefault(claim, fingerprint)
            self._results[fingerprint] = canonical
            return E2EGateCommitResult.model_validate_json(canonical, strict=True)


class SqliteE2EGateCommitter:
    """仅供测试使用的 SQLite durable 原子提交器。"""

    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path, isolation_level=None)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS e2e_materializations ("
            "fingerprint TEXT PRIMARY KEY, canonical_result BLOB NOT NULL) STRICT"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS e2e_replay_claims ("
            "claim_kind TEXT NOT NULL, claim_key TEXT NOT NULL, "
            "fingerprint TEXT NOT NULL, PRIMARY KEY (claim_kind, claim_key), "
            "FOREIGN KEY (fingerprint) REFERENCES e2e_materializations(fingerprint)) STRICT"
        )
        self._lock = RLock()

    def commit(self, command: E2EGateCommitCommand) -> E2EGateCommitResult:
        command = _revalidate(command, E2EGateCommitCommand)
        fingerprint = _command_fingerprint(command)
        claims = _command_claims(command)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT canonical_result FROM e2e_materializations WHERE fingerprint = ?",
                    (fingerprint,),
                ).fetchone()
                if row is not None:
                    self._connection.commit()
                    return E2EGateCommitResult.model_validate_json(
                        cast(bytes, row[0]), strict=True
                    )
                existing = tuple(
                    self._connection.execute(
                        "SELECT fingerprint FROM e2e_replay_claims "
                        "WHERE claim_kind = ? AND claim_key = ?",
                        claim,
                    ).fetchone()
                    for claim in claims
                )
                conflicts = tuple(
                    cast(str, row[0])
                    for row in existing
                    if row is not None and cast(str, row[0]) != fingerprint
                )
                if conflicts:
                    prior = self._connection.execute(
                        "SELECT canonical_result FROM e2e_materializations WHERE fingerprint = ?",
                        (conflicts[0],),
                    ).fetchone()
                    if prior is not None:
                        existing_result = E2EGateCommitResult.model_validate_json(
                            cast(bytes, prior[0]), strict=True
                        )
                        if existing_result.decision.next_state == "FAILED":
                            self._connection.commit()
                            return existing_result
                    command = command.model_copy(
                        update={"decision": SealedE2EGate._decision("FAILED")}
                    )
                    command = _revalidate(command, E2EGateCommitCommand)
                    fingerprint = _command_fingerprint(command)
                    replay_row = self._connection.execute(
                        "SELECT canonical_result FROM e2e_materializations WHERE fingerprint = ?",
                        (fingerprint,),
                    ).fetchone()
                    if replay_row is not None:
                        self._connection.commit()
                        return E2EGateCommitResult.model_validate_json(
                            cast(bytes, replay_row[0]), strict=True
                        )
                receipt_id = (
                    "sha256:"
                    + sha256(
                        canonical_json_bytes(
                            {"backend": "test_sqlite", "fingerprint": fingerprint}
                        )
                    ).hexdigest()
                )
                result = _materialize(
                    command, backend="test_sqlite", atomic_receipt_id=receipt_id
                )
                canonical = canonical_json_bytes(
                    result.model_dump(mode="json", round_trip=True, warnings="error")
                )
                self._connection.execute(
                    "INSERT INTO e2e_materializations(fingerprint, canonical_result) "
                    "VALUES (?, ?)",
                    (fingerprint, canonical),
                )
                self._connection.executemany(
                    "INSERT OR IGNORE INTO e2e_replay_claims(claim_kind, claim_key, fingerprint) "
                    "VALUES (?, ?, ?)",
                    [(*claim, fingerprint) for claim in claims],
                )
                self._connection.commit()
                return E2EGateCommitResult.model_validate_json(canonical, strict=True)
            except BaseException as error:
                self._connection.rollback()
                raise E2EGateCommitError(
                    "sealed E2E atomic materialization failed"
                ) from error

    def close(self) -> None:
        self._connection.close()


SignedE2EArtifact = E2EGateEvaluationRequest | E2EGateVerdict | SignedWorkerEvidence


def e2e_signature_preimage(value: SignedE2EArtifact) -> bytes:
    payload = value.model_dump(mode="json", round_trip=True, warnings="error")
    del payload["signature_b64url"]
    return canonical_json_bytes(payload)


def e2e_payload_hash(value: StrictFrozenModel) -> str:
    payload = value.model_dump(mode="json", round_trip=True, warnings="error")
    canonical = CanonicalPayloadCodec(type(value)).encode(payload)
    return "sha256:" + sha256(canonical).hexdigest()


ModelT = TypeVar("ModelT", bound=StrictFrozenModel)


def _revalidate(value: ModelT, model_type: type[ModelT]) -> ModelT:
    if type(value) is not model_type:
        raise ValueError("sealed E2E protocol requires exact closed contracts")
    return model_type.model_validate_json(
        canonical_json_bytes(
            value.model_dump(mode="json", round_trip=True, warnings="error")
        ),
        strict=True,
    )


def _sign_e2e(
    fields: Mapping[str, object],
    signing_key: Ed25519PrivateKey,
    model_type: type[E2EGateEvaluationRequest | E2EGateVerdict | SignedWorkerEvidence],
) -> E2EGateEvaluationRequest | E2EGateVerdict | SignedWorkerEvidence:
    if type(fields) is not dict or "signature_b64url" in fields:
        raise ValueError("E2E signer requires exact unsigned fields")
    if not isinstance(signing_key, Ed25519PrivateKey):
        raise TypeError("E2E signer requires an Ed25519 key")
    payload = dict(fields)
    payload["signature_b64url"] = (
        base64.urlsafe_b64encode(bytes(64)).decode().rstrip("=")
    )
    provisional = model_type.model_validate(payload, strict=True)
    payload["signature_b64url"] = (
        base64.urlsafe_b64encode(signing_key.sign(e2e_signature_preimage(provisional)))
        .decode("ascii")
        .rstrip("=")
    )
    return model_type.model_validate(payload, strict=True)


def sign_e2e_request(
    fields: Mapping[str, object], signing_key: Ed25519PrivateKey
) -> E2EGateEvaluationRequest:
    return cast(
        E2EGateEvaluationRequest,
        _sign_e2e(fields, signing_key, E2EGateEvaluationRequest),
    )


def sign_e2e_verdict(
    fields: Mapping[str, object], signing_key: Ed25519PrivateKey
) -> E2EGateVerdict:
    return cast(E2EGateVerdict, _sign_e2e(fields, signing_key, E2EGateVerdict))


def sign_worker_evidence(
    fields: Mapping[str, object], signing_key: Ed25519PrivateKey
) -> SignedWorkerEvidence:
    return cast(
        SignedWorkerEvidence,
        _sign_e2e(fields, signing_key, SignedWorkerEvidence),
    )


def _verify_signature(value: SignedE2EArtifact, public_key: Ed25519PublicKey) -> None:
    try:
        public_key.verify(
            _decode_canonical_b64url(value.signature_b64url, 64),
            e2e_signature_preimage(value),
        )
    except (InvalidSignature, ValueError) as error:
        raise ValueError("sealed E2E signature is invalid") from error


def _verify_execution_attestation(
    binding: SealedWorkerBinding, public_key: Ed25519PublicKey
) -> None:
    try:
        verify_execution_attestation_signature(
            binding.execution_attestation,
            public_key,
        )
    except (RunnerReplayError, TypeError, ValueError) as error:
        raise ValueError("worker execution attestation is invalid") from error


class SealedE2EGate:
    """验证 sealed 四门结果并给出不含诊断信息的唯一状态映射。"""

    def __init__(
        self,
        *,
        key_policy_resolver: E2EKeyPolicyResolver,
        protocol_resolver: E2EProtocolResolver,
        runner_grant_resolver: E2ERunnerGrantResolver,
        committer: E2EGateCommitter,
        clock: Callable[[], str],
        maximum_clock_skew_ms: int,
    ) -> None:
        if (
            not hasattr(key_policy_resolver, "resolve")
            or not hasattr(protocol_resolver, "resolve")
            or not hasattr(runner_grant_resolver, "resolve")
            or not hasattr(committer, "commit")
        ):
            raise TypeError("sealed E2E dependencies do not implement their seams")
        if (
            type(maximum_clock_skew_ms) is not int
            or not 0 <= maximum_clock_skew_ms <= 300_000
        ):
            raise ValueError("E2E clock skew must be a bounded exact integer")
        self._key_policy_resolver = key_policy_resolver
        self._protocol_resolver = protocol_resolver
        self._runner_grant_resolver = runner_grant_resolver
        self._committer = committer
        self._clock = clock
        self._maximum_clock_skew = timedelta(milliseconds=maximum_clock_skew_ms)

    def evaluate(
        self,
        *,
        request_ref: ArtifactReference,
        request: E2EGateEvaluationRequest,
        verdict_ref: ArtifactReference,
        verdict: E2EGateVerdict,
        topology: SealedWorkerTopology,
        topology_ref: ArtifactReference,
        execution: E2EGateExecutionCommitInput,
    ) -> E2EGateCommitResult:
        request_ref = _revalidate(request_ref, ArtifactReference)
        verdict_ref = _revalidate(verdict_ref, ArtifactReference)
        request = _revalidate(request, E2EGateEvaluationRequest)
        verdict = _revalidate(verdict, E2EGateVerdict)
        topology_ref = _revalidate(topology_ref, ArtifactReference)
        execution = _revalidate(execution, E2EGateExecutionCommitInput)
        topology_hash = topology_ref.payload_hash
        try:
            topology = _revalidate(topology, SealedWorkerTopology)
            if topology_hash != e2e_payload_hash(topology):
                raise ValueError("sealed topology reference does not match payload")
            now = datetime.fromisoformat(self._clock())
            self._verify_closed_protocol(
                request_ref=request_ref,
                request=request,
                verdict_ref=verdict_ref,
                verdict=verdict,
                topology=topology,
                now=now,
            )
            decision = self._decision(
                "TRAINING_SMOKE_TESTING" if verdict.e2e_valid else "PARTIAL"
            )
        except (KeyError, TypeError, ValueError, RuntimeError):
            decision = self._decision("FAILED")
        command = E2EGateCommitCommand(
            schema_version="automarkov.e2e-gate-commit-command.v1",
            request_ref=request_ref,
            verdict_ref=verdict_ref,
            request_id=request.request_id,
            verdict_id=verdict.verdict_id,
            request_nonce_b64url=request.nonce_b64url,
            verdict_nonce_b64url=verdict.nonce_b64url,
            coordinator_key_id=request.coordinator_key_id,
            evaluator_key_id=verdict.evaluator_key_id,
            candidate_worker_key_id=topology.candidate.evidence.worker_key_id,
            gold_worker_key_id=topology.gold.evidence.worker_key_id,
            comparator_worker_key_id=topology.comparator.evidence.worker_key_id,
            run_id=request.run_id,
            run_manifest=request.run_manifest,
            specified_event_head=request.specified_event_head,
            candidate_bundle=request.candidate_bundle,
            topology_ref=topology_ref,
            request_payload_hash=request_ref.payload_hash,
            verdict_payload_hash=verdict_ref.payload_hash,
            topology_payload_hash=topology_hash,
            runner_fingerprint=execution.runner_fingerprint,
            process_execution_terminal_record=(
                execution.process_execution_terminal_record
            ),
            decision=decision,
            committed_at=cast(CanonicalTimestamp, self._clock()),
        )
        return self._committer.commit(command)

    def _verify_closed_protocol(
        self,
        *,
        request_ref: ArtifactReference,
        request: E2EGateEvaluationRequest,
        verdict_ref: ArtifactReference,
        verdict: E2EGateVerdict,
        topology: SealedWorkerTopology,
        now: datetime,
    ) -> None:
        request_hash = e2e_payload_hash(request)
        verdict_hash = e2e_payload_hash(verdict)
        if (
            request_ref.payload_hash != request_hash
            or verdict_ref.payload_hash != verdict_hash
        ):
            raise ValueError("E2E artifact reference does not bind canonical payload")
        self._verify_time_window(request, verdict, now)
        coordinator_policy = self._require_key(
            request=request,
            key_id=request.coordinator_key_id,
            principal_id=request.coordinator_principal_id,
            principal_kind="coordinator",
            issued_at=request.issued_at,
            now=now,
        )
        evaluator_policy = self._require_key(
            request=request,
            key_id=verdict.evaluator_key_id,
            principal_id=verdict.evaluator_principal_id,
            principal_kind="evaluator",
            issued_at=verdict.issued_at,
            now=now,
        )
        _verify_signature(request, coordinator_policy.public_key())
        _verify_signature(verdict, evaluator_policy.public_key())
        trusted_protocol = _revalidate(
            self._protocol_resolver.resolve(request.evaluator_protocol_id),
            TrustedEvaluatorProtocol,
        )
        self._verify_trusted_protocol(request, trusted_protocol)
        for binding in (topology.candidate, topology.gold, topology.comparator):
            evidence = binding.evidence
            evidence_policy = self._require_key(
                request=request,
                key_id=evidence.worker_key_id,
                principal_id=binding.principal_id,
                principal_kind=cast(
                    Literal["candidate_worker", "gold_worker", "comparator"],
                    (
                        "candidate_worker"
                        if evidence.worker_kind == "candidate"
                        else (
                            "gold_worker"
                            if evidence.worker_kind == "gold"
                            else "comparator"
                        )
                    ),
                ),
                issued_at=evidence.issued_at,
                now=now,
            )
            resolved_execution = _revalidate(
                self._runner_grant_resolver.resolve(
                    run_id=request.run_id,
                    specified_event_head=request.specified_event_head,
                    run_manifest=request.run_manifest,
                    job_manifest=binding.job_manifest,
                    principal_id=binding.principal_id,
                ),
                ResolvedSealedWorkerExecution,
            )
            grant = resolved_execution.runner_key_grant
            attestation_issued = datetime.fromisoformat(
                binding.execution_attestation.issued_at
            )
            if (
                grant.signing_key_id == evidence.worker_key_id
                or grant.public_key_bytes()
                == evidence_policy.public_key().public_bytes_raw()
                or binding.execution_attestation.signing_key_id != grant.signing_key_id
                or attestation_issued < datetime.fromisoformat(grant.not_before)
                or attestation_issued >= datetime.fromisoformat(grant.not_after)
                or grant.revoked_at is not None
                and attestation_issued >= datetime.fromisoformat(grant.revoked_at)
            ):
                raise ValueError("runner attestation grant is inactive or substituted")
            _verify_signature(evidence, evidence_policy.public_key())
            _verify_execution_attestation(
                binding,
                Ed25519PublicKey.from_public_bytes(grant.public_key_bytes()),
            )
            self._verify_worker_subjects(
                binding=binding,
                request_ref=request_ref,
                request=request,
                verdict_ref=verdict_ref,
                verdict=verdict,
                resolved_execution=resolved_execution,
            )
            self._verify_worker_time_windows(
                topology=topology, request=request, now=now
            )
        if (
            topology.candidate.profile_id != request.candidate_worker_profile_id
            or topology.candidate.profile_hash != request.candidate_worker_profile_hash
            or topology.gold.profile_id != request.gold_worker_profile_id
            or topology.gold.profile_hash != request.gold_worker_profile_hash
            or topology.comparator.profile_id != request.evaluator_profile_id
            or topology.comparator.profile_hash != request.evaluator_profile_hash
            or topology.candidate.execution_attestation.run_id != request.run_id
            or topology.gold.execution_attestation.run_id != request.run_id
            or topology.comparator.execution_attestation.run_id != request.run_id
            or topology.candidate.execution_attestation.experiment_id
            != request.experiment_id
            or topology.gold.execution_attestation.experiment_id
            != request.experiment_id
            or topology.comparator.execution_attestation.experiment_id
            != request.experiment_id
        ):
            raise ValueError("worker topology does not match frozen profiles")
        pairs = (
            (verdict.request_id, request.request_id),
            (verdict.request_payload_hash, e2e_payload_hash(request)),
            (verdict.run_id, request.run_id),
            (verdict.run_manifest, request.run_manifest),
            (verdict.candidate_bundle, request.candidate_bundle),
            (verdict.task_contract, request.task_contract),
            (verdict.decision_process_spec, request.decision_process_spec),
            (verdict.environment_binding, request.environment_binding),
        )
        if any(actual != expected for actual, expected in pairs) or (
            datetime.fromisoformat(verdict.issued_at)
            < datetime.fromisoformat(request.issued_at)
        ):
            raise ValueError("sealed E2E verdict binding is invalid")

    def _require_key(
        self,
        *,
        request: E2EGateEvaluationRequest,
        key_id: str,
        principal_id: str,
        principal_kind: E2EPrincipalKind,
        issued_at: str,
        now: datetime,
    ) -> E2EGateKeyPolicy:
        policy = _revalidate(
            self._key_policy_resolver.resolve(
                run_id=request.run_id,
                specified_event_head=request.specified_event_head,
                run_manifest=request.run_manifest,
                key_id=key_id,
                principal_id=principal_id,
                principal_kind=principal_kind,
            ),
            E2EGateKeyPolicy,
        )
        issued = datetime.fromisoformat(issued_at)
        if (
            policy.key_id != key_id
            or policy.principal_id != principal_id
            or policy.principal_kind != principal_kind
            or not datetime.fromisoformat(policy.valid_from)
            <= issued
            < datetime.fromisoformat(policy.valid_until)
            or (
                policy.revoked_at is not None
                and datetime.fromisoformat(policy.revoked_at) <= now
            )
        ):
            raise ValueError("E2E signing key policy is inactive or mismatched")
        return policy

    def _verify_time_window(
        self,
        request: E2EGateEvaluationRequest,
        verdict: E2EGateVerdict,
        now: datetime,
    ) -> None:
        issued = datetime.fromisoformat(request.issued_at)
        not_before = datetime.fromisoformat(request.not_before)
        expires = datetime.fromisoformat(request.expires_at)
        verdict_issued = datetime.fromisoformat(verdict.issued_at)
        if (
            now < not_before - self._maximum_clock_skew
            or now > expires + self._maximum_clock_skew
            or issued > now + self._maximum_clock_skew
            or verdict_issued > now + self._maximum_clock_skew
            or not issued <= verdict_issued <= expires
        ):
            raise ValueError("sealed E2E artifact is stale or from the future")

    def _verify_worker_time_windows(
        self,
        *,
        topology: SealedWorkerTopology,
        request: E2EGateEvaluationRequest,
        now: datetime,
    ) -> None:
        expires = datetime.fromisoformat(request.expires_at)
        for binding in (
            topology.candidate,
            topology.gold,
            topology.comparator,
        ):
            attestation_issued = datetime.fromisoformat(
                binding.execution_attestation.issued_at
            )
            evidence_issued = datetime.fromisoformat(binding.evidence.issued_at)
            if (
                attestation_issued > now + self._maximum_clock_skew
                or evidence_issued > now + self._maximum_clock_skew
                or attestation_issued > expires + self._maximum_clock_skew
                or evidence_issued > expires + self._maximum_clock_skew
            ):
                raise ValueError(
                    "worker attestation or evidence is outside the request window"
                )

    @staticmethod
    def _verify_trusted_protocol(
        request: E2EGateEvaluationRequest,
        trusted: TrustedEvaluatorProtocol,
    ) -> None:
        names = (
            "evaluator_protocol_id",
            "evaluator_protocol_hash",
            "evaluator_profile_id",
            "evaluator_profile_hash",
            "evaluator_lock_hash",
            "evaluator_image_hash",
            "evaluator_schema_id",
            "evaluator_schema_hash",
            "candidate_worker_profile_id",
            "candidate_worker_profile_hash",
            "gold_worker_profile_id",
            "gold_worker_profile_hash",
        )
        if any(getattr(request, name) != getattr(trusted, name) for name in names):
            raise ValueError("request protocol evidence is not registry-authenticated")

    @staticmethod
    def _verify_worker_subjects(
        *,
        binding: SealedWorkerBinding,
        request_ref: ArtifactReference,
        request: E2EGateEvaluationRequest,
        verdict_ref: ArtifactReference,
        verdict: E2EGateVerdict,
        resolved_execution: ResolvedSealedWorkerExecution,
    ) -> None:
        evidence = binding.evidence
        pairs = (
            (evidence.request_ref, request_ref),
            (evidence.candidate_bundle, request.candidate_bundle),
            (evidence.task_contract, request.task_contract),
            (evidence.decision_process_spec, request.decision_process_spec),
            (evidence.environment_binding, request.environment_binding),
        )
        attestation = binding.execution_attestation
        request_issued = datetime.fromisoformat(request.issued_at)
        verdict_issued = datetime.fromisoformat(verdict.issued_at)
        attestation_issued = datetime.fromisoformat(attestation.issued_at)
        evidence_issued = datetime.fromisoformat(evidence.issued_at)
        request_subjects = tuple(
            sorted(
                (
                    request.candidate_bundle,
                    request.task_contract,
                    request.decision_process_spec,
                    request.environment_binding,
                ),
                key=lambda item: item.artifact_id.encode("utf-8"),
            )
        )
        mounted_inputs = tuple(
            sorted(
                (
                    mount.source_id
                    for mount in binding.mount_attestation.actual_mounts
                    if mount.source_kind == "input_artifact"
                ),
                key=lambda item: item.encode("utf-8"),
            )
        )
        expected_profile_hash = {
            "candidate": request.candidate_worker_profile_hash,
            "gold": request.gold_worker_profile_hash,
            "comparator": request.evaluator_profile_hash,
        }[evidence.worker_kind]
        expected_profile_id = {
            "candidate": request.candidate_worker_profile_id,
            "gold": request.gold_worker_profile_id,
            "comparator": request.evaluator_profile_id,
        }[evidence.worker_kind]
        ordering_is_valid = (
            request_issued <= verdict_issued <= attestation_issued <= evidence_issued
            if evidence.worker_kind == "comparator"
            else request_issued
            <= attestation_issued
            <= evidence_issued
            <= verdict_issued
        )
        if (
            any(actual != expected for actual, expected in pairs)
            or not ordering_is_valid
            or resolved_execution.job_manifest != binding.job_manifest
            or resolved_execution.principal_id != binding.principal_id
            or resolved_execution.profile_id != expected_profile_id
            or resolved_execution.profile_hash != expected_profile_hash
        ):
            raise ValueError("worker evidence subjects or ordering are invalid")
        if evidence.worker_kind == "candidate":
            if tuple(
                resolved_execution.input_sources
            ) != request_subjects or mounted_inputs != tuple(
                sorted(
                    (subject.artifact_id for subject in request_subjects),
                    key=lambda item: item.encode("utf-8"),
                )
            ):
                raise ValueError(
                    "candidate execution does not bind exact request subjects"
                )
            candidate_output_kinds = {output.output_kind for output in evidence.outputs}
            if not {
                "candidate_text",
                "candidate_formal",
                "candidate_api",
                "candidate_behavior",
            }.issubset(candidate_output_kinds):
                raise ValueError(
                    "candidate worker must produce all four typed output kinds"
                )
        if evidence.worker_kind == "gold":
            candidate_subject_ids = {item.artifact_id for item in request_subjects}
            candidate_subject_hashes = {item.payload_hash for item in request_subjects}
            if any(
                source.payload_hash in candidate_subject_hashes
                for source in resolved_execution.input_sources
            ) or any(
                source_id in candidate_subject_ids for source_id in mounted_inputs
            ):
                raise ValueError("gold execution mounts candidate-side subjects")
        if evidence.worker_kind == "comparator" and (
            tuple(resolved_execution.input_sources)
            != tuple(
                sorted(
                    evidence.subject_outputs,
                    key=lambda item: item.artifact_id.encode("utf-8"),
                )
            )
            or resolved_execution.image_digest != request.evaluator_image_hash
        ):
            raise ValueError("comparator execution identity is not frozen")
        if evidence.worker_kind == "comparator" and (
            evidence.outputs[0].output_ref != verdict_ref
        ):
            raise ValueError("comparator output does not bind the exact verdict")

    @staticmethod
    def _decision(
        state: Literal["TRAINING_SMOKE_TESTING", "PARTIAL", "FAILED"],
    ) -> E2EGateDecision:
        fields = {
            "TRAINING_SMOKE_TESTING": (False, True, False, None),
            "PARTIAL": (True, False, True, "gate_false"),
            "FAILED": (True, False, True, "integrity"),
        }[state]
        return E2EGateDecision(
            schema_version="automarkov.e2e-gate-decision.v1",
            next_state=state,
            terminal=fields[0],
            e2e_valid=fields[1],
            training_outcome_missing=fields[2],
            failure_class=fields[3],
            retry_permitted=False,
        )


__all__ = [
    "ArtifactLifecycleAtomicReceipt",
    "ArtifactLifecycleE2EGateCommitter",
    "ArtifactRepositoryE2EGateLifecycleMaterializer",
    "ArtifactRepositoryE2EKeyPolicyResolver",
    "ArtifactRepositoryE2ERunnerGrantResolver",
    "BoundedWorkerOutput",
    "E2EGateCommitCommand",
    "E2EGateCommitError",
    "E2EGateCommitResult",
    "E2EGateCommitter",
    "E2EGateDecision",
    "E2EGateEvaluationRequest",
    "E2EGateExecutionCommitInput",
    "E2EGateKeyPolicy",
    "E2EGateLifecycleMaterializer",
    "E2EGateLifecyclePlanProvider",
    "E2EGateVerdict",
    "E2EKeyPolicyResolver",
    "E2ERunnerGrantResolver",
    "FrozenE2EProtocolRegistry",
    "InMemoryE2EGateCommitter",
    "SealedE2EGate",
    "SealedWorkerBinding",
    "SealedWorkerTopology",
    "SignedWorkerEvidence",
    "SqliteE2EGateCommitter",
    "TrustedEvaluatorProtocol",
    "WorkerIsolationPolicy",
    "e2e_payload_hash",
    "e2e_signature_preimage",
    "sign_e2e_request",
    "sign_e2e_verdict",
    "sign_worker_evidence",
]
