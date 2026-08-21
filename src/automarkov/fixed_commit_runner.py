from __future__ import annotations

import base64
import os
import re
import secrets
import signal
import socket
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha1, sha256
from ipaddress import ip_address
from pathlib import Path, PurePosixPath
from threading import Condition, Event, RLock, Thread
from types import MappingProxyType
from typing import Annotated, Literal, Protocol, Self, TypeAlias, cast
from urllib.parse import unquote, urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import Field, model_validator

from automarkov.canonical import (
    CanonicalJsonValue,
    FrozenSequence,
    FrozenStringMapping,
    NonNegativeSafeCanonicalInt,
    PositiveSafeCanonicalInt,
    StrictTrue,
    canonical_json_bytes,
    parse_json_payload,
)
from automarkov.domain import (
    ArtifactId,
    StrictFrozenModel,
    VerifiedEventHead,
)
from automarkov.domain import (
    RunId as DomainRunId,
)
from automarkov.domain import (
    Sha256Digest as DomainSha256Digest,
)
from automarkov.lifecycle import (
    RUN_PROJECTOR_HASH,
    RUN_PROJECTOR_VERSION,
    ArtifactReference,
    CanonicalTimestamp,
    CommitTerminalCommand,
    EventReference,
    ExecutionAttestation,
    ExecutionPhaseTransition,
    LifecycleCommitReceipt,
    ManifestEventSigningKey,
    ProcessExecutionTerminalRecord,
    TerminalResult,
    validate_lifecycle_command,
)
from automarkov.provenance import RuntimeProfileId, RuntimeProfileManifest
from automarkov.public import (
    ArtifactPutResult,
    ArtifactRepository,
    AuthenticatedCommandContext,
    LifecycleCommandInput,
)
from automarkov.task_contracts import FixedCommitRunAuthorization, RunManifest

NonEmptyId = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
Sha256Digest = Annotated[str, Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]
GitCommit = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{40}$")]
RunId = Annotated[
    str,
    Field(strict=True, pattern=r"^run_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
]
PrincipalId = Annotated[
    str,
    Field(strict=True, pattern=r"^principal_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
]
ExecutionPhase = Literal[
    "authoring",
    "retrieval",
    "training",
    "sealed_evaluation",
    "analysis",
    "export",
]
SealedWorkerKind: TypeAlias = Literal["candidate", "gold", "comparator"]
ProtocolEdge: TypeAlias = Literal["EvidenceGateway", "LocalLlmRuntime", "RemoteEnv"]

_PHASE_PROTOCOL_EDGE_MATRIX: dict[ExecutionPhase, tuple[ProtocolEdge, ...]] = {
    "authoring": ("LocalLlmRuntime",),
    "retrieval": ("EvidenceGateway",),
    "training": ("RemoteEnv",),
    "sealed_evaluation": (),
    "analysis": (),
    "export": (),
}

_CREDENTIAL_ARG_MARKERS = (
    "--api-key",
    "--api_key",
    "--access-token",
    "--access_token",
    "--auth-token",
    "--auth_token",
    "--authorization",
    "--authorization-header",
    "--authorization_header",
    "--client-secret",
    "--client_secret",
    "--credential",
    "--credentials",
    "--hf-token",
    "--hf_token",
    "--password",
    "--passwd",
    "--private-key",
    "--private_key",
    "--refresh-token",
    "--refresh_token",
    "--secret",
    "--token",
)
_SHELL_EXECUTABLE_NAMES = frozenset(
    {"ash", "bash", "csh", "dash", "fish", "ksh", "sh", "tcsh", "zsh"}
)

_RUNNER_OUTPUT_SCANNER_RULES = {
    "credential_locator_keys": [
        "credential_locator",
        "credentials_path",
        "secret_locator",
    ],
    "credential_locator_markers": [".env", "credential://", "secret://"],
    "gold_marker_keys": [
        "gold_answer",
        "gold_marker",
        "hidden_test",
        "reference_answer",
    ],
    "gold_value_markers": ["gold://", "sealed-gold://"],
    "secret_keys": [
        "access_token",
        "api_secret",
        "api_key",
        "auth_token",
        "authorization",
        "authorization_header",
        "bearer_token",
        "client_secret",
        "credential",
        "credentials",
        "id_token",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "session_token",
        "token",
    ],
    "secret_value_markers": ["-----begin private key-----"],
    "secret_value_patterns": [
        r"(?i)(?:^|[^A-Za-z0-9])bearer[ \t]+[A-Za-z0-9._~+/-]{20,}(?:$|[^A-Za-z0-9._~+/-])",
        r"(?:^|[^A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?:$|[^A-Z0-9])",
        r"(?:^|[^A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{36,255}(?:$|[^A-Za-z0-9])",
        r"(?:^|[^A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{20,}(?:$|[^A-Za-z0-9_-])",
    ],
}
RUNNER_OUTPUT_SCANNER_RULES_HASH = (
    "sha256:" + sha256(canonical_json_bytes(_RUNNER_OUTPUT_SCANNER_RULES)).hexdigest()
)
PositiveSafeInt = PositiveSafeCanonicalInt


def _normalize_scanner_key(value: str) -> str:
    """将 snake/kebab/camel/Pascal key 归一到唯一 scanner 词形。"""

    words = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", words)
    return re.sub(r"[^A-Za-z0-9]+", "_", words).strip("_").casefold()


def _apparmor_profile_name(process_execution_id: str) -> str:
    """为每个持久化 execution 派生唯一且可复算的 profile 名。"""

    return "automarkov-" + sha256(process_execution_id.encode("utf-8")).hexdigest()[:32]


def _require_canonical_https_repository(value: str) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("repository URL is invalid") from error
    if (
        parsed.scheme != "https"
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or not parsed.path.strip("/")
        or parsed.path != unquote(parsed.path)
        or hostname.endswith(".")
        or any(part in {"", ".", ".."} for part in PurePosixPath(parsed.path).parts[1:])
    ):
        raise ValueError("repository must be a canonical HTTPS URL")
    try:
        ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("repository hostname must not be an IP literal")
    if hostname in {"localhost"} or hostname.endswith(".localhost"):
        raise ValueError("repository hostname must not be local")
    canonical_netloc = hostname if port is None else f"{hostname}:443"
    if parsed.netloc != canonical_netloc:
        raise ValueError("repository URL authority is not canonical")
    return value


def _resolve_public_repository_addresses(hostname: str, port: int) -> tuple[str, ...]:
    """解析并冻结 Git HTTPS 目标；本地、私网或特殊地址一律拒绝。"""

    try:
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise RunnerWaitingRuntimeError("public repository DNS resolution") from error
    addresses = tuple(
        sorted(
            {str(ip_address(record[4][0])) for record in records},
            key=lambda item: item.encode("ascii"),
        )
    )
    if not addresses or any(not ip_address(address).is_global for address in addresses):
        raise RunnerPreflightError(
            "repository hostname resolves outside the public network"
        )
    return addresses


def _require_relative_directory(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or value.endswith("/")
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise ValueError("working directory must be a canonical relative path")
    return value


class FixedCommitResourceLimits(StrictFrozenModel):
    schema_version: Literal["automarkov.fixed-commit-resource-limits.v1"]
    phase: ExecutionPhase
    cpu_millis: PositiveSafeInt
    memory_bytes: PositiveSafeInt
    pids: PositiveSafeInt
    io_bytes: PositiveSafeInt
    disk_bytes: PositiveSafeInt
    wall_time_ms: PositiveSafeInt
    gpu_devices: FrozenSequence[
        Annotated[str, Field(strict=True, pattern=r"^cuda:[0-9]+$")]
    ]

    @model_validator(mode="after")
    def require_phase_resource_contract(self) -> Self:
        expected = tuple(
            sorted(set(self.gpu_devices), key=lambda item: item.encode("utf-8"))
        )
        if self.gpu_devices != expected:
            raise ValueError("GPU devices must be sorted and unique")
        if self.phase not in {"training", "sealed_evaluation"} and self.gpu_devices:
            raise ValueError("CPU-only phase cannot request GPU devices")
        return self


class PhaseNetworkPolicy(StrictFrozenModel):
    schema_version: Literal["automarkov.phase-network-policy.v1"]
    phase: ExecutionPhase
    egress_allowlist: FrozenSequence[
        Annotated[
            str,
            Field(
                strict=True,
                pattern=r"^[A-Za-z0-9.-]+:[1-9][0-9]{0,4}$",
                max_length=256,
            ),
        ]
    ]
    protocol_edges: FrozenSequence[ProtocolEdge]
    gateway_principal_id: PrincipalId | None
    deny_ip_literals: StrictTrue
    deny_redirect_egress: StrictTrue
    revoke_before_output_scan: StrictTrue

    @model_validator(mode="after")
    def require_phase_egress_matrix(self) -> Self:
        expected_endpoints = tuple(
            sorted(set(self.egress_allowlist), key=lambda item: item.encode("utf-8"))
        )
        expected_edges = tuple(
            sorted(set(self.protocol_edges), key=lambda item: item.encode("utf-8"))
        )
        if (
            self.egress_allowlist != expected_endpoints
            or self.protocol_edges != expected_edges
        ):
            raise ValueError("network grants must be sorted and unique")
        if self.protocol_edges != _PHASE_PROTOCOL_EDGE_MATRIX[self.phase]:
            raise ValueError("phase protocol edges do not match the closed matrix")
        for endpoint in self.egress_allowlist:
            hostname = endpoint.rsplit(":", 1)[0]
            try:
                ip_address(hostname)
            except ValueError:
                pass
            else:
                raise ValueError("network policy forbids IP literals")
        if self.phase == "retrieval":
            if (
                self.egress_allowlist != ("api.tavily.com:443",)
                or self.protocol_edges != ("EvidenceGateway",)
                or self.gateway_principal_id != "principal_retrieval-tavily"
            ):
                raise ValueError("retrieval uses only the authenticated Tavily gateway")
        elif self.egress_allowlist:
            raise ValueError("non-retrieval phases cannot use direct internet egress")
        elif self.gateway_principal_id is not None:
            raise ValueError("default-deny phases do not bind an egress gateway")
        return self


MountSourceKind: TypeAlias = Literal[
    "checkout",
    "input_artifact",
    "output_root",
    "sealed_asset",
]


class ExecutionMount(StrictFrozenModel):
    source_kind: MountSourceKind
    source_id: NonEmptyId
    target_path: Annotated[
        str,
        Field(strict=True, pattern=r"^/mnt/automarkov(?:/[A-Za-z0-9._-]+)+$"),
    ]
    access: Literal["read_only", "write_only"]

    @model_validator(mode="after")
    def require_mount_access(self) -> Self:
        if (self.source_kind == "output_root") != (self.access == "write_only"):
            raise ValueError("only the isolated output root may be writable")
        forbidden = ("docker.sock", "site-packages", "credential", "/.env")
        if any(fragment in self.target_path.lower() for fragment in forbidden):
            raise ValueError("mount target crosses a forbidden host boundary")
        path = PurePosixPath(self.target_path)
        if (
            any(part in {".", ".."} for part in path.parts)
            or str(path) != self.target_path
        ):
            raise ValueError("mount target must be an absolute canonical path")
        return self


class ExecutionMountPolicy(StrictFrozenModel):
    schema_version: Literal["automarkov.execution-mount-policy.v1"]
    candidate_worker: bool = Field(strict=True)
    mounts: FrozenSequence[ExecutionMount]

    @model_validator(mode="after")
    def require_mount_subset(self) -> Self:
        targets = tuple(mount.target_path for mount in self.mounts)
        if targets != tuple(
            sorted(set(targets), key=lambda item: item.encode("utf-8"))
        ):
            raise ValueError("mount targets must be sorted and unique")
        if self.candidate_worker and any(
            mount.source_kind == "sealed_asset" for mount in self.mounts
        ):
            raise ValueError("candidate worker cannot mount sealed assets")
        return self


class ExecutionCapabilityPolicy(StrictFrozenModel):
    schema_version: Literal["automarkov.execution-capability-policy.v1"]
    drop_all_capabilities: StrictTrue
    allowed_capabilities: FrozenSequence[str]
    no_new_privileges: StrictTrue
    read_only_rootfs: StrictTrue
    non_root: StrictTrue
    seccomp_profile_hash: Sha256Digest
    apparmor_profile_hash: Sha256Digest
    apparmor_profile_name: Annotated[
        str,
        Field(strict=True, pattern=r"^automarkov-[a-z0-9][a-z0-9_-]{0,62}$"),
    ]

    @model_validator(mode="after")
    def require_empty_capability_set(self) -> Self:
        if self.allowed_capabilities:
            raise ValueError("fixed-commit workers must drop all capabilities")
        return self


class FixedCommitJobManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.fixed-commit-job-manifest.v1"]
    job_id: NonEmptyId
    process_execution_id: NonEmptyId
    experiment_id: NonEmptyId
    run_id: RunId
    principal_id: PrincipalId
    repository_url: Annotated[
        str,
        Field(strict=True, min_length=9, max_length=2_048),
    ]
    source_commit: GitCommit
    profile_manifest: ArtifactReference
    profile_id: RuntimeProfileId
    profile_lock_hash: Sha256Digest
    target_platform: Literal["linux/amd64"]
    image_digest: Sha256Digest
    input_artifacts: FrozenSequence[ArtifactReference]
    suite_id: NonEmptyId
    variant_id: NonEmptyId
    track_id: NonEmptyId
    method_id: NonEmptyId
    pair_id: NonEmptyId
    generation_seed: NonNegativeSafeCanonicalInt
    rl_seed: NonNegativeSafeCanonicalInt
    phase: ExecutionPhase
    argv: FrozenSequence[
        Annotated[str, Field(strict=True, min_length=1, max_length=32_768)]
    ]
    working_directory: Annotated[
        str, Field(strict=True, min_length=1, max_length=1_024)
    ]
    resource_limits: ArtifactReference
    network_policy: ArtifactReference
    mount_policy: ArtifactReference
    capability_policy: ArtifactReference
    output_contract: ArtifactReference
    scanner_policy: ArtifactReference
    from_phase: NonEmptyId
    to_phase: NonEmptyId
    launch_deadline: CanonicalTimestamp

    @model_validator(mode="after")
    def require_frozen_launch(self) -> Self:
        _require_canonical_https_repository(self.repository_url)
        _require_relative_directory(self.working_directory)
        input_keys = tuple(
            (reference.artifact_id, reference.payload_hash)
            for reference in self.input_artifacts
        )
        if not input_keys or input_keys != tuple(
            sorted(set(input_keys), key=lambda item: item[0].encode("utf-8"))
        ):
            raise ValueError("input artifacts must be sorted, unique, and nonempty")
        if not self.argv:
            raise ValueError("argv must be nonempty")
        executable = self.argv[0]
        if (
            not executable.startswith("/")
            or any(character.isspace() for character in executable)
            or "\x00" in executable
            or PurePosixPath(executable).name.lower() in _SHELL_EXECUTABLE_NAMES
        ):
            raise ValueError("argv must name an absolute executable without a shell")
        if any("\x00" in argument for argument in self.argv):
            raise ValueError("argv contains a NUL byte")
        for argument in self.argv:
            lowered = argument.casefold()
            flag = lowered.split("=", 1)[0]
            if flag in _CREDENTIAL_ARG_MARKERS or lowered.startswith("bearer "):
                raise ValueError("argv must not contain credential-bearing arguments")
        if self.from_phase == self.to_phase:
            raise ValueError("job phase transition must change phase")
        return self


def _job_authorization(
    manifest: FixedCommitJobManifest,
    job_reference: ArtifactReference,
    *,
    runner_key_grant: ManifestEventSigningKey,
) -> FixedCommitRunAuthorization:
    return FixedCommitRunAuthorization(
        schema_version="automarkov.fixed-commit-run-authorization.v1",
        job_manifest=job_reference,
        repository_url=manifest.repository_url,
        source_commit=manifest.source_commit,
        profile_manifest=manifest.profile_manifest,
        profile_id=manifest.profile_id,
        image_digest=manifest.image_digest,
        input_artifacts=manifest.input_artifacts,
        resource_limits=manifest.resource_limits,
        network_policy=manifest.network_policy,
        mount_policy=manifest.mount_policy,
        capability_policy=manifest.capability_policy,
        output_contract=manifest.output_contract,
        scanner_policy=manifest.scanner_policy,
        suite_id=manifest.suite_id,
        variant_id=manifest.variant_id,
        track_id=manifest.track_id,
        method_id=manifest.method_id,
        pair_id=manifest.pair_id,
        generation_seed=manifest.generation_seed,
        rl_seed=manifest.rl_seed,
        phase=manifest.phase,
        argv=manifest.argv,
        working_directory=manifest.working_directory,
        from_phase=manifest.from_phase,
        to_phase=manifest.to_phase,
        launch_deadline=manifest.launch_deadline,
        runner_key_grant=runner_key_grant,
    )


class RunnerResultPayload(StrictFrozenModel):
    schema_version: Literal["automarkov.runner-output.v1"]
    status: Literal["ok"]


class RunnerArtifactReferencePayload(StrictFrozenModel):
    schema_version: Literal["automarkov.runner-artifact-reference-output.v1"]
    artifact_type: NonEmptyId
    artifact: ArtifactReference
    artifact_payload_b64url: (
        Annotated[str, Field(strict=True, min_length=1, max_length=11 * 1024 * 1024)]
        | None
    ) = None

    @model_validator(mode="after")
    def require_canonical_embedded_payload(self) -> Self:
        if self.artifact_payload_b64url is not None:
            try:
                payload = base64.urlsafe_b64decode(
                    self.artifact_payload_b64url
                    + "=" * (-len(self.artifact_payload_b64url) % 4)
                )
            except ValueError as error:
                raise ValueError("embedded artifact payload is invalid") from error
            if (
                not payload
                or base64.urlsafe_b64encode(payload).decode().rstrip("=")
                != self.artifact_payload_b64url
            ):
                raise ValueError("embedded artifact payload is not canonical base64url")
        return self

    def embedded_payload_bytes(self) -> bytes | None:
        if self.artifact_payload_b64url is None:
            return None
        return base64.urlsafe_b64decode(
            self.artifact_payload_b64url
            + "=" * (-len(self.artifact_payload_b64url) % 4)
        )


class SealedSubjectRecord(StrictFrozenModel):
    record_id: NonEmptyId
    value: CanonicalJsonValue


SealedSubjectRecords = Annotated[
    FrozenSequence[SealedSubjectRecord], Field(min_length=1, max_length=4096)
]


class _SealedSubjectOutput(StrictFrozenModel):
    job_manifest: ArtifactReference
    records: SealedSubjectRecords

    @model_validator(mode="after")
    def require_canonical_records(self) -> Self:
        record_ids = tuple(record.record_id for record in self.records)
        if record_ids != tuple(
            sorted(set(record_ids), key=lambda item: item.encode("utf-8"))
        ):
            raise ValueError("sealed subject records must be sorted and unique")
        return self


class CandidateApiOutput(_SealedSubjectOutput):
    schema_version: Literal["automarkov.candidate-api-output.v1"]


class CandidateBehaviorOutput(_SealedSubjectOutput):
    schema_version: Literal["automarkov.candidate-behavior-output.v1"]


class CandidateFormalOutput(_SealedSubjectOutput):
    schema_version: Literal["automarkov.candidate-formal-output.v1"]


class CandidateTextOutput(_SealedSubjectOutput):
    schema_version: Literal["automarkov.candidate-text-output.v1"]


class GoldApiOutput(_SealedSubjectOutput):
    schema_version: Literal["automarkov.gold-api-output.v1"]


class GoldBehaviorOutput(_SealedSubjectOutput):
    schema_version: Literal["automarkov.gold-behavior-output.v1"]


class GoldFormalOutput(_SealedSubjectOutput):
    schema_version: Literal["automarkov.gold-formal-output.v1"]


class GoldTextOutput(_SealedSubjectOutput):
    schema_version: Literal["automarkov.gold-text-output.v1"]


@dataclass(frozen=True, slots=True)
class SealedSubjectArtifactContract:
    schema_version: str
    model_type: type[_SealedSubjectOutput]


SEALED_SUBJECT_ARTIFACT_CONTRACTS: Mapping[str, SealedSubjectArtifactContract] = (
    MappingProxyType(
        {
            "candidate_api": SealedSubjectArtifactContract(
                "automarkov.candidate-api-output.v1", CandidateApiOutput
            ),
            "candidate_behavior": SealedSubjectArtifactContract(
                "automarkov.candidate-behavior-output.v1", CandidateBehaviorOutput
            ),
            "candidate_formal": SealedSubjectArtifactContract(
                "automarkov.candidate-formal-output.v1", CandidateFormalOutput
            ),
            "candidate_text": SealedSubjectArtifactContract(
                "automarkov.candidate-text-output.v1", CandidateTextOutput
            ),
            "gold_api": SealedSubjectArtifactContract(
                "automarkov.gold-api-output.v1", GoldApiOutput
            ),
            "gold_behavior": SealedSubjectArtifactContract(
                "automarkov.gold-behavior-output.v1", GoldBehaviorOutput
            ),
            "gold_formal": SealedSubjectArtifactContract(
                "automarkov.gold-formal-output.v1", GoldFormalOutput
            ),
            "gold_text": SealedSubjectArtifactContract(
                "automarkov.gold-text-output.v1", GoldTextOutput
            ),
        }
    )
)


def _output_schema_identity(model: type[StrictFrozenModel]) -> str:
    return (
        "sha256:"
        + sha256(
            canonical_json_bytes(model.model_json_schema(mode="validation"))
        ).hexdigest()
    )


RUNNER_RESULT_PAYLOAD_SCHEMA_HASH = _output_schema_identity(RunnerResultPayload)
RUNNER_ARTIFACT_REFERENCE_PAYLOAD_SCHEMA_HASH = _output_schema_identity(
    RunnerArtifactReferencePayload
)


class OutputSchemaBinding(StrictFrozenModel):
    path: Annotated[
        str, Field(strict=True, pattern=r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
    ]
    schema_version: NonEmptyId
    schema_identity_hash: Sha256Digest


_CLOSED_OUTPUT_SCHEMA_REGISTRY: dict[
    tuple[str, str], tuple[type[StrictFrozenModel], str]
] = {
    ("result.json", "automarkov.runner-output.v1"): (
        RunnerResultPayload,
        RUNNER_RESULT_PAYLOAD_SCHEMA_HASH,
    ),
    (
        "artifact-reference.json",
        "automarkov.runner-artifact-reference-output.v1",
    ): (
        RunnerArtifactReferencePayload,
        RUNNER_ARTIFACT_REFERENCE_PAYLOAD_SCHEMA_HASH,
    ),
    **{
        (
            path,
            "automarkov.runner-artifact-reference-output.v1",
        ): (
            RunnerArtifactReferencePayload,
            RUNNER_ARTIFACT_REFERENCE_PAYLOAD_SCHEMA_HASH,
        )
        for path in (
            "candidate_api.json",
            "candidate_behavior.json",
            "candidate_formal.json",
            "candidate_text.json",
            "e2e_verdict.json",
            "gold_api.json",
            "gold_behavior.json",
            "gold_formal.json",
            "gold_text.json",
        )
    },
}

_TYPED_ARTIFACT_REFERENCE_OUTPUT_TYPES = {
    "candidate_api.json": "candidate_api",
    "candidate_behavior.json": "candidate_behavior",
    "candidate_formal.json": "candidate_formal",
    "candidate_text.json": "candidate_text",
    "e2e_verdict.json": "e2e_gate_verdict",
    "gold_api.json": "gold_api",
    "gold_behavior.json": "gold_behavior",
    "gold_formal.json": "gold_formal",
    "gold_text.json": "gold_text",
}


class ExecutionOutputContract(StrictFrozenModel):
    schema_version: Literal["automarkov.execution-output-contract.v1"]
    allowed_paths: FrozenSequence[
        Annotated[
            str, Field(strict=True, pattern=r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
        ]
    ]
    output_schemas: FrozenSequence[OutputSchemaBinding]
    maximum_total_bytes: PositiveSafeInt
    require_regular_files: StrictTrue
    forbid_symlinks: StrictTrue
    forbid_extra_outputs: StrictTrue

    @model_validator(mode="after")
    def require_closed_paths(self) -> Self:
        expected = tuple(
            sorted(set(self.allowed_paths), key=lambda item: item.encode("utf-8"))
        )
        schema_paths = tuple(binding.path for binding in self.output_schemas)
        if (
            not expected
            or self.allowed_paths != expected
            or schema_paths != expected
            or len(set(schema_paths)) != len(schema_paths)
            or any(
                (
                    registered := _CLOSED_OUTPUT_SCHEMA_REGISTRY.get(
                        (binding.path, binding.schema_version)
                    )
                )
                is None
                or binding.schema_identity_hash != registered[1]
                for binding in self.output_schemas
            )
        ):
            raise ValueError(
                "allowed output paths require one sorted unique schema binding"
            )
        return self


class OutputScannerPolicy(StrictFrozenModel):
    schema_version: Literal["automarkov.output-scanner-policy.v1"]
    scanner_id: NonEmptyId
    scanner_version: NonEmptyId
    scanner_rules_hash: Sha256Digest
    reject_secrets: StrictTrue
    reject_gold_markers: StrictTrue
    reject_credential_locators: StrictTrue

    @model_validator(mode="after")
    def require_central_scanner_identity(self) -> Self:
        if self.scanner_rules_hash != RUNNER_OUTPUT_SCANNER_RULES_HASH:
            raise ValueError("scanner policy does not bind the central rules identity")
        return self


class RunnerInput(StrictFrozenModel):
    schema_version: Literal["automarkov.runner-input.v1"]
    input_index: Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]
    source_artifact: ArtifactReference
    source_artifact_type: NonEmptyId
    source_commitment: Sha256Digest

    @model_validator(mode="after")
    def require_exact_source_commitment(self) -> Self:
        if self.source_commitment != self.source_artifact.payload_hash:
            raise ValueError("runner input commitment does not match source artifact")
        return self


class RuntimeProfileArtifactPayload(StrictFrozenModel):
    """ArtifactRepository wire schema；语义仍由 T04 RuntimeProfileManifest 重验。"""

    schema_version: Literal["automarkov.runtime-profile-manifest.v2"]
    profile_id: str
    python_version: str
    lockfile_path: str
    lock_hash: Sha256Digest
    containerfile_path: str
    build_context_files: FrozenSequence[str]
    build_context_hash: Sha256Digest
    target_platform: str
    image_status: str
    image_digest: Sha256Digest | None
    platform: str | None
    libc_version: str | None
    openssl_version: str | None
    ca_bundle_hash: Sha256Digest | None
    build_attestation_id: str | None
    build_attestation_hash: Sha256Digest | None
    import_smoke_attestation_id: str | None
    import_smoke_attestation_hash: Sha256Digest | None
    sbom_path: str
    sbom_hash: Sha256Digest
    license_manifest_path: str
    license_manifest_hash: Sha256Digest
    smoke_contract_path: str
    smoke_contract_hash: Sha256Digest
    package_versions: FrozenStringMapping[str]
    repository_commits: FrozenStringMapping[str]
    dataset_revisions: FrozenStringMapping[str]
    model_revisions: FrozenStringMapping[str]
    hardware_contract: str
    capabilities: FrozenSequence[str]
    conflict_groups: FrozenSequence[str]
    egress_allowlist: FrozenSequence[str]
    credential_ids: FrozenSequence[str]
    read_mounts: FrozenSequence[str]
    write_mounts: FrozenSequence[str]
    protocol_edges: FrozenSequence[str]
    restricted: bool = Field(strict=True)
    build_enabled: bool = Field(strict=True)
    publishable: bool = Field(strict=True)


class RunnerRuntimeAttestation(StrictFrozenModel):
    schema_version: Literal["automarkov.runner-runtime-attestation.v1"]
    signing_domain: Literal["AutoMarkov-Runner-Runtime-Attestation-v1"]
    attestation_kind: Literal["build", "import_smoke"]
    issuer_id: NonEmptyId
    signing_key_id: NonEmptyId
    profile_id: RuntimeProfileId
    image_digest: Sha256Digest
    observed_at: CanonicalTimestamp
    nonce_b64url: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{22}$")]
    evidence_refs: FrozenSequence[ArtifactReference]
    signature_algorithm: Literal["Ed25519"]
    signature_b64url: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{86}$")]

    @model_validator(mode="after")
    def require_canonical_signed_attestation(self) -> Self:
        refs = tuple(
            (item.artifact_id, item.payload_hash) for item in self.evidence_refs
        )
        if not refs or refs != tuple(sorted(set(refs))):
            raise ValueError("runtime evidence references must be sorted and unique")
        try:
            nonce = base64.urlsafe_b64decode(self.nonce_b64url + "==")
            signature = base64.urlsafe_b64decode(self.signature_b64url + "==")
        except ValueError as error:
            raise ValueError("runtime attestation uses invalid base64url") from error
        if (
            len(nonce) != 16
            or base64.urlsafe_b64encode(nonce).decode().rstrip("=") != self.nonce_b64url
            or len(signature) != 64
            or base64.urlsafe_b64encode(signature).decode().rstrip("=")
            != self.signature_b64url
        ):
            raise ValueError("runtime attestation signature or nonce is noncanonical")
        return self

    def signing_bytes(self) -> bytes:
        payload = self.model_dump(mode="json", round_trip=True, warnings="error")
        del payload["signature_b64url"]
        return canonical_json_bytes(payload)


class RunnerRuntimeEvidence(StrictFrozenModel):
    schema_version: Literal["automarkov.runner-runtime-evidence.v1"]
    evidence_kind: Literal["build", "import_smoke"]
    image_digest: Sha256Digest


@dataclass(frozen=True, slots=True)
class RuntimeAttestationKeyPolicy:
    signing_key_id: str
    issuer_id: str
    public_key: Ed25519PublicKey
    not_before: str
    not_after: str
    allowed_profile_ids: frozenset[str]
    allowed_kinds: frozenset[str]


def _sign_runtime_attestation(
    fields: Mapping[str, object],
    signing_key: Ed25519PrivateKey,
) -> RunnerRuntimeAttestation:
    payload = dict(fields)
    payload["signature_b64url"] = "A" * 86
    provisional = RunnerRuntimeAttestation.model_validate(payload, strict=True)
    payload["signature_b64url"] = (
        base64.urlsafe_b64encode(signing_key.sign(provisional.signing_bytes()))
        .decode()
        .rstrip("=")
    )
    return RunnerRuntimeAttestation.model_validate(payload, strict=True)


class RunnerOutputBinding(StrictFrozenModel):
    schema_version: Literal["automarkov.runner-output-binding.v2"]
    path: Annotated[
        str,
        Field(strict=True, pattern=r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$"),
    ]
    byte_size: NonNegativeSafeCanonicalInt
    media_type: Annotated[str, Field(strict=True, min_length=1, max_length=256)]
    content_hash: Sha256Digest
    content_schema_version: NonEmptyId
    content_b64url: Annotated[
        str, Field(strict=True, min_length=1, max_length=11 * 1024 * 1024)
    ]
    schema_valid: StrictTrue

    @model_validator(mode="after")
    def require_actual_content_identity(self) -> Self:
        try:
            content_bytes = base64.urlsafe_b64decode(
                self.content_b64url + "=" * (-len(self.content_b64url) % 4)
            )
        except ValueError as error:
            raise ValueError("output content encoding is invalid") from error
        if (
            not content_bytes
            or base64.urlsafe_b64encode(content_bytes).decode().rstrip("=")
            != self.content_b64url
            or self.byte_size != len(content_bytes)
            or self.content_hash != "sha256:" + sha256(content_bytes).hexdigest()
            or self.media_type != "application/json"
        ):
            raise ValueError("output binding must hash actual immutable bytes")
        try:
            payload = parse_json_payload(content_bytes)
        except ValueError as error:
            raise ValueError(
                "output content must satisfy its JSON schema contract"
            ) from error
        if (
            type(payload) is not dict
            or cast(dict[str, object], payload).get("schema_version")
            != self.content_schema_version
        ):
            raise ValueError("output content schema identity is invalid")
        return self

    def verified_content_bytes(self) -> bytes:
        content = base64.urlsafe_b64decode(
            self.content_b64url + "=" * (-len(self.content_b64url) % 4)
        )
        if (
            len(content) != self.byte_size
            or "sha256:" + sha256(content).hexdigest() != self.content_hash
            or self.media_type != "application/json"
        ):
            raise RunnerPreflightError("output content media/hash/size changed")
        payload = parse_json_payload(content)
        if (
            type(payload) is not dict
            or cast(dict[str, object], payload).get("schema_version")
            != self.content_schema_version
        ):
            raise RunnerPreflightError("output content schema changed")
        return content


class ExecutionResourceUsage(StrictFrozenModel):
    schema_version: Literal["automarkov.execution-resource-usage.v1"]
    job_manifest: ArtifactReference
    limits_policy: ArtifactReference
    cpu_time_ms: NonNegativeSafeCanonicalInt
    peak_memory_bytes: NonNegativeSafeCanonicalInt
    peak_pids: NonNegativeSafeCanonicalInt
    io_read_bytes: NonNegativeSafeCanonicalInt
    io_write_bytes: NonNegativeSafeCanonicalInt
    peak_disk_bytes: NonNegativeSafeCanonicalInt
    wall_time_ms: NonNegativeSafeCanonicalInt
    gpu_devices: FrozenSequence[str]

    @model_validator(mode="after")
    def require_canonical_gpu_usage(self) -> Self:
        if self.gpu_devices != tuple(
            sorted(set(self.gpu_devices), key=lambda item: item.encode("utf-8"))
        ):
            raise ValueError("used GPU devices must be sorted and unique")
        return self


class RunnerNetworkDecision(StrictFrozenModel):
    decision_kind: Literal["direct_egress", "control_edge"]
    endpoint: Annotated[str, Field(strict=True, min_length=1, max_length=256)] | None
    protocol_edge: (
        Annotated[str, Field(strict=True, min_length=1, max_length=128)] | None
    )
    decision: Literal["allowed", "denied"]
    reason_code: Annotated[str, Field(strict=True, pattern=r"^[a-z][a-z0-9_]{0,127}$")]

    @model_validator(mode="after")
    def require_closed_decision_kind(self) -> Self:
        if self.decision_kind == "direct_egress":
            if self.endpoint is None or self.protocol_edge is not None:
                raise ValueError("direct egress requires only an endpoint")
        elif self.endpoint is not None or self.protocol_edge is None:
            raise ValueError("control edge requires only a protocol edge")
        return self


class NetworkDecisionLog(StrictFrozenModel):
    schema_version: Literal["automarkov.network-decision-log.v1"]
    job_manifest: ArtifactReference
    network_policy: ArtifactReference
    decisions: FrozenSequence[RunnerNetworkDecision]

    @model_validator(mode="after")
    def require_canonical_decisions(self) -> Self:
        keys = tuple(
            (
                item.decision_kind,
                item.endpoint or "",
                item.protocol_edge or "",
                item.decision,
                item.reason_code,
            )
            for item in self.decisions
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("network decisions must be sorted and unique")
        return self


class MountAttestation(StrictFrozenModel):
    schema_version: Literal["automarkov.mount-attestation.v1"]
    job_manifest: ArtifactReference
    mount_policy: ArtifactReference
    actual_mounts: FrozenSequence[ExecutionMount]

    @model_validator(mode="after")
    def require_canonical_actual_mounts(self) -> Self:
        keys = tuple(
            (item.target_path, item.source_kind, item.source_id, item.access)
            for item in self.actual_mounts
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("actual mounts must be sorted and unique")
        return self


class CapabilityDecisionLog(StrictFrozenModel):
    schema_version: Literal["automarkov.capability-decision-log.v1"]
    job_manifest: ArtifactReference
    capability_policy: ArtifactReference
    denied_capabilities: FrozenSequence[str]
    effective_uid: Annotated[int, Field(strict=True, ge=1, le=2**31 - 1)]
    no_new_privileges: StrictTrue
    read_only_rootfs: StrictTrue
    dropped_capabilities: FrozenSequence[str]
    seccomp_profile_hash: Sha256Digest
    apparmor_profile_hash: Sha256Digest

    @model_validator(mode="after")
    def require_canonical_effective_boundary(self) -> Self:
        if self.denied_capabilities != tuple(
            sorted(set(self.denied_capabilities), key=lambda item: item.encode("utf-8"))
        ) or self.dropped_capabilities != tuple(
            sorted(
                set(self.dropped_capabilities), key=lambda item: item.encode("utf-8")
            )
        ):
            raise ValueError("capability evidence must be sorted and unique")
        return self


class EgressDecisionLog(StrictFrozenModel):
    schema_version: Literal["automarkov.egress-decision-log.v1"]
    job_manifest: ArtifactReference
    network_policy: ArtifactReference
    decisions: FrozenSequence[RunnerNetworkDecision]
    revoked_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_canonical_egress_decisions(self) -> Self:
        keys = tuple(
            (
                item.decision_kind,
                item.endpoint or "",
                item.protocol_edge or "",
                item.decision,
                item.reason_code,
            )
            for item in self.decisions
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("egress decisions must be sorted and unique")
        return self


class OutputScanReport(StrictFrozenModel):
    schema_version: Literal["automarkov.output-scan-report.v1"]
    job_manifest: ArtifactReference
    scanner_policy: ArtifactReference
    output_contract: ArtifactReference
    scanner_rules_hash: Sha256Digest
    scanned_outputs: FrozenSequence[ArtifactReference]
    scanned_paths: FrozenSequence[str]
    total_bytes: NonNegativeSafeCanonicalInt
    schema_valid: StrictTrue
    scan_passed: StrictTrue
    scanned_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_canonical_scan_set(self) -> Self:
        output_keys = tuple(
            (item.artifact_id, item.payload_hash) for item in self.scanned_outputs
        )
        if output_keys != tuple(
            sorted(set(output_keys), key=lambda item: item[0].encode("utf-8"))
        ):
            raise ValueError("scanned outputs must be sorted and unique")
        if self.scanned_paths != tuple(
            sorted(set(self.scanned_paths), key=lambda item: item.encode("utf-8"))
        ):
            raise ValueError("scanned paths must be sorted and unique")
        return self


class RawExecutionEvidence(StrictFrozenModel):
    schema_version: Literal["automarkov.raw-execution-evidence.v1"]
    job_id: NonEmptyId
    process_execution_id: NonEmptyId
    source_commit: GitCommit
    profile_id: RuntimeProfileId
    image_digest: Sha256Digest
    status: Literal["success", "terminal_failure"]
    exit_code: Annotated[int, Field(strict=True, ge=0, le=255)]
    reason_code: Annotated[
        str,
        Field(strict=True, pattern=r"^[a-z][a-z0-9_]{0,127}$"),
    ]

    started_at: CanonicalTimestamp
    finished_at: CanonicalTimestamp
    stdout_hash: Sha256Digest
    stderr_hash: Sha256Digest
    payload_outputs: FrozenSequence[ArtifactReference]
    resource_usage: ArtifactReference
    network_log: ArtifactReference
    mount_attestation: ArtifactReference
    capability_decision_log: ArtifactReference
    egress_decision_log: ArtifactReference
    output_scan_report: ArtifactReference
    egress_revoked_at: CanonicalTimestamp

    @model_validator(mode="after")
    def require_closed_evidence(self) -> Self:
        output_keys = tuple(
            (reference.artifact_id, reference.payload_hash)
            for reference in self.payload_outputs
        )
        controls = (
            self.network_log,
            self.mount_attestation,
            self.capability_decision_log,
            self.egress_decision_log,
            self.output_scan_report,
        )
        if output_keys != tuple(
            sorted(set(output_keys), key=lambda item: item[0].encode("utf-8"))
        ):
            raise ValueError("execution outputs must be sorted and unique")
        role_references = (*controls, self.resource_usage)
        if len(set(role_references)) != len(role_references):
            raise ValueError("execution evidence roles must use distinct artifacts")
        if set(role_references) & set(self.payload_outputs):
            raise ValueError("payload and control artifacts must be distinct")
        if (self.status == "success") != (self.exit_code == 0):
            raise ValueError("execution status and exit code are inconsistent")
        finished_at = datetime.fromisoformat(self.finished_at)
        if (
            datetime.fromisoformat(self.started_at) > finished_at
            or datetime.fromisoformat(self.egress_revoked_at) > finished_at
        ):
            raise ValueError("execution evidence timestamps are inconsistent")
        return self


class FixedCommitExecutionRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.fixed-commit-execution-request.v1"]
    specified_event_head: VerifiedEventHead
    job_manifest: ArtifactReference


class FixedCommitExecutionResult(StrictFrozenModel):
    schema_version: Literal["automarkov.fixed-commit-execution-result.v1"]
    process_terminal_record: ArtifactReference
    execution_attestation: ArtifactReference
    terminal_result: ArtifactReference | None


class RunnerPreflightError(ValueError):
    """固定提交 job 在任何执行动作前未通过冻结身份检查。"""


class RunnerReplayError(ValueError):
    """同一 job、execution 或签名 nonce 被不同内容重放。"""


class RunnerWaitingRuntimeError(RuntimeError):
    def __init__(self, capability: str) -> None:
        self.state = "WAITING_RUNTIME"
        self.capability = capability
        super().__init__(f"WAITING_RUNTIME: missing {capability}")


class _RuntimeProfileMountMaximum(Protocol):
    @property
    def read_mounts(self) -> tuple[str, ...]: ...

    @property
    def write_mounts(self) -> tuple[str, ...]: ...


def validate_mount_profile_policy(
    profile: _RuntimeProfileMountMaximum,
    policy: ExecutionMountPolicy,
    manifest: FixedCommitJobManifest,
) -> None:
    frozen_input_ids = {reference.artifact_id for reference in manifest.input_artifacts}
    for mount in policy.mounts:
        maximum = (
            profile.read_mounts if mount.access == "read_only" else profile.write_mounts
        )
        if mount.target_path not in maximum:
            raise RunnerPreflightError(
                "mount direction exceeds runtime profile maximum"
            )
        source_is_frozen = {
            "checkout": mount.source_id == manifest.source_commit,
            "input_artifact": mount.source_id in frozen_input_ids,
            "output_root": mount.source_id == manifest.process_execution_id,
            "sealed_asset": (
                manifest.phase == "sealed_evaluation"
                and mount.source_id in frozen_input_ids
            ),
        }[mount.source_kind]
        if not source_is_frozen:
            raise RunnerPreflightError("mount source is outside the frozen job graph")


def validate_network_decisions(
    policy: PhaseNetworkPolicy,
    decisions: tuple[RunnerNetworkDecision, ...],
) -> None:
    for item in decisions:
        if item.decision != "allowed":
            continue
        if item.decision_kind == "direct_egress":
            if item.endpoint not in policy.egress_allowlist:
                raise RunnerPreflightError(
                    "direct egress endpoint is outside the allowlist"
                )
        elif item.protocol_edge not in policy.protocol_edges:
            raise RunnerPreflightError(
                "control edge is outside the frozen protocol graph"
            )


def validate_worker_launch_policy(
    worker_kind: SealedWorkerKind | None,
    mount_policy: ExecutionMountPolicy,
    network_policy: PhaseNetworkPolicy,
) -> None:
    if worker_kind != "candidate":
        return
    if (
        not mount_policy.candidate_worker
        or any(mount.source_kind == "sealed_asset" for mount in mount_policy.mounts)
        or network_policy.egress_allowlist
        or network_policy.protocol_edges
    ):
        raise RunnerPreflightError(
            "candidate worker launch policy crosses the sealed boundary"
        )


class FixedCommitExecutor(Protocol):
    def execute(self, manifest: FixedCommitJobManifest) -> RawExecutionEvidence: ...


class MemoryFixedCommitExecutor:
    """仅用于合同测试的显式 fake，不表示 OCI runtime 已就绪。"""

    def __init__(self, evidence: RawExecutionEvidence) -> None:
        self._evidence = evidence
        self.call_count = 0

    def execute(self, manifest: FixedCommitJobManifest) -> RawExecutionEvidence:
        self.call_count += 1
        return self._evidence


@dataclass(frozen=True, slots=True)
class OciCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class OciResourceObservation(StrictFrozenModel):
    schema_version: Literal["automarkov.oci-resource-observation.v1"]
    cpu_time_ms: NonNegativeSafeCanonicalInt
    peak_memory_bytes: NonNegativeSafeCanonicalInt
    peak_pids: NonNegativeSafeCanonicalInt
    io_read_bytes: NonNegativeSafeCanonicalInt
    io_write_bytes: NonNegativeSafeCanonicalInt
    peak_disk_bytes: NonNegativeSafeCanonicalInt
    wall_time_ms: NonNegativeSafeCanonicalInt
    gpu_devices: FrozenSequence[str]
    timed_out: bool = Field(strict=True)
    limit_exceeded: bool = Field(strict=True)


class OciResourceCollector(Protocol):
    def collect(
        self,
        *,
        container_pid: int,
        output_root: Path,
        wait_for_exit: Callable[[], OciCommandResult],
        terminate: Callable[[], None],
        limits: FixedCommitResourceLimits,
        execution_started_monotonic: float,
    ) -> tuple[OciResourceObservation, OciCommandResult]: ...


class LinuxCgroupV2ResourceCollector:
    """从本机 cgroup v2 对同一容器生命周期采集不可回退的资源证据。"""

    def __init__(
        self,
        *,
        proc_root: Path = Path("/proc"),
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval_seconds: float = 0.02,
    ) -> None:
        if not 0.001 <= poll_interval_seconds <= 0.25:
            raise ValueError("cgroup polling interval is outside the closed range")
        self._proc_root = proc_root
        self._cgroup_root = cgroup_root
        self._monotonic = monotonic
        self._sleep = sleep
        self._poll_interval = poll_interval_seconds

    def monotonic(self) -> float:
        return self._monotonic()

    @staticmethod
    def _read_nonnegative(path: Path) -> int:
        raw = path.read_text(encoding="ascii").strip()
        if not raw.isascii() or not raw.isdecimal():
            raise RunnerWaitingRuntimeError("canonical cgroup v2 numeric evidence")
        return int(raw)

    @staticmethod
    def _cpu_usage_usec(path: Path) -> int:
        fields = {
            key: value
            for key, value in (
                line.split(" ", 1)
                for line in path.read_text(encoding="ascii").splitlines()
                if " " in line
            )
        }
        value = fields.get("usage_usec")
        if value is None or not value.isdecimal():
            raise RunnerWaitingRuntimeError("canonical cgroup v2 CPU evidence")
        return int(value)

    @staticmethod
    def _io_bytes(path: Path) -> tuple[int, int]:
        read_bytes = 0
        write_bytes = 0
        for line in path.read_text(encoding="ascii").splitlines():
            fields = line.split()
            if not fields or ":" not in fields[0]:
                raise RunnerWaitingRuntimeError("canonical cgroup v2 IO evidence")
            values: dict[str, int] = {}
            for field in fields[1:]:
                key, separator, value = field.partition("=")
                if not separator or not value.isdecimal():
                    raise RunnerWaitingRuntimeError("canonical cgroup v2 IO evidence")
                values[key] = int(value)
            read_bytes += values.get("rbytes", 0)
            write_bytes += values.get("wbytes", 0)
        return read_bytes, write_bytes

    def _cgroup_path(self, container_pid: int) -> Path:
        if type(container_pid) is not int or container_pid <= 0:
            raise RunnerWaitingRuntimeError("running OCI container PID")
        lines = (
            (self._proc_root / str(container_pid) / "cgroup")
            .read_text(encoding="ascii")
            .splitlines()
        )
        matches = [line[3:] for line in lines if line.startswith("0::/")]
        if len(matches) != 1:
            raise RunnerWaitingRuntimeError("unique cgroup v2 container membership")
        observed = matches[0]
        if not observed.startswith("/") or observed.startswith("//"):
            raise RunnerWaitingRuntimeError("canonical cgroup v2 container path")
        relative = PurePosixPath(observed.removeprefix("/"))
        if not relative.parts or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            raise RunnerWaitingRuntimeError("canonical cgroup v2 container path")
        root = self._cgroup_root.resolve(strict=True)
        path = root.joinpath(*relative.parts).resolve(strict=True)
        if path == root or not path.is_relative_to(root):
            raise RunnerWaitingRuntimeError("isolated cgroup v2 container path")
        return path

    @staticmethod
    def _output_bytes(root: Path) -> int:
        total = 0
        for path in root.rglob("*"):
            if path.is_symlink():
                raise RunnerPreflightError("OCI output tree contains a symlink")
            if path.is_file():
                total += path.stat().st_size
        return total

    def collect(
        self,
        *,
        container_pid: int,
        output_root: Path,
        wait_for_exit: Callable[[], OciCommandResult],
        terminate: Callable[[], None],
        limits: FixedCommitResourceLimits,
        execution_started_monotonic: float,
    ) -> tuple[OciResourceObservation, OciCommandResult]:
        if type(limits) is not FixedCommitResourceLimits:
            raise TypeError("resource collector requires frozen exact limits")
        cgroup = self._cgroup_path(container_pid)
        complete = Event()
        result: list[OciCommandResult] = []
        failures: list[Exception] = []

        def wait() -> None:
            try:
                result.append(wait_for_exit())
            except (OSError, RuntimeError, ValueError) as error:
                failures.append(error)
            finally:
                complete.set()

        waiter = Thread(target=wait, name="automarkov-oci-wait", daemon=True)
        if (
            type(execution_started_monotonic) is not float
            or not execution_started_monotonic >= 0
        ):
            raise ValueError("execution monotonic start must be a nonnegative float")
        started = execution_started_monotonic
        waiter.start()
        cpu_time_ms = 0
        peak_memory = 0
        peak_pids = 0
        io_read = 0
        io_write = 0
        sampled = False
        deadline = started + limits.wall_time_ms / 1_000
        timed_out = False
        resource_exceeded = False
        termination_requested = False
        while True:
            try:
                cpu_time_ms = max(
                    cpu_time_ms,
                    (self._cpu_usage_usec(cgroup / "cpu.stat") + 999) // 1_000,
                )
                peak_memory = max(
                    peak_memory, self._read_nonnegative(cgroup / "memory.peak")
                )
                peak_pids = max(peak_pids, self._read_nonnegative(cgroup / "pids.peak"))
                observed_read, observed_write = self._io_bytes(cgroup / "io.stat")
                io_read = max(io_read, observed_read)
                io_write = max(io_write, observed_write)
                sampled = True
            except FileNotFoundError:
                if not complete.is_set() or not sampled:
                    raise RunnerWaitingRuntimeError(
                        "persistent cgroup v2 execution evidence"
                    ) from None
            resource_exceeded = resource_exceeded or (
                peak_memory > limits.memory_bytes
                or peak_pids > limits.pids
                or io_read + io_write > limits.io_bytes
                or self._output_bytes(output_root) > limits.disk_bytes
            )
            if complete.is_set():
                break
            if resource_exceeded and not termination_requested:
                terminate()
                termination_requested = True
                deadline = self._monotonic() + 5.0
                continue
            if self._monotonic() >= deadline and not termination_requested:
                terminate()
                timed_out = True
                termination_requested = True
                deadline = self._monotonic() + 5.0
            elif self._monotonic() >= deadline:
                raise RunnerWaitingRuntimeError("bounded OCI container termination")
            self._sleep(self._poll_interval)
        waiter.join(timeout=1)
        if failures:
            raise failures[0]
        if len(result) != 1 or not sampled:
            raise RunnerWaitingRuntimeError("complete cgroup v2 execution evidence")
        elapsed_ms = max(0, int((self._monotonic() - started) * 1_000))
        return (
            OciResourceObservation(
                schema_version="automarkov.oci-resource-observation.v1",
                cpu_time_ms=cpu_time_ms,
                peak_memory_bytes=peak_memory,
                peak_pids=peak_pids,
                io_read_bytes=io_read,
                io_write_bytes=io_write,
                peak_disk_bytes=self._output_bytes(output_root),
                wall_time_ms=elapsed_ms,
                gpu_devices=(),
                timed_out=timed_out,
                limit_exceeded=resource_exceeded,
            ),
            result[0],
        )


class RunnerArtifactWriter(Protocol):
    def write(
        self,
        artifact_type: str,
        value: StrictFrozenModel,
        *,
        parents: tuple[ArtifactReference, ...],
        created_by: str,
        created_at: str,
    ) -> ArtifactReference: ...


class ArtifactRepositoryRunnerArtifactWriter:
    """把 executor 生成的模型写入正式 immutable ArtifactRepository。"""

    def __init__(self, repository: ArtifactRepository) -> None:
        if not hasattr(repository, "put"):
            raise TypeError("runner artifact writer requires ArtifactRepository.put")
        self._repository = repository

    def write(
        self,
        artifact_type: str,
        value: StrictFrozenModel,
        *,
        parents: tuple[ArtifactReference, ...],
        created_by: str,
        created_at: str,
    ) -> ArtifactReference:
        parent_ids = sorted(
            {parent.artifact_id for parent in parents},
            key=lambda item: item.encode("utf-8"),
        )
        result: ArtifactPutResult = self._repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": artifact_type,
                "payload_bytes": canonical_json_bytes(
                    value.model_dump(
                        mode="json",
                        round_trip=True,
                        warnings="error",
                        exclude_computed_fields=True,
                    )
                ),
                "parent_artifact_ids": parent_ids,
                "created_by": created_by,
                "created_at": created_at,
                "source_evidence_ids": [],
            }
        )
        return ArtifactReference(
            artifact_id=result.artifact_id.root,
            payload_hash=result.payload_hash.root,
        )


class MemoryRunnerArtifactWriter:
    """测试 writer；写入同一个 MemoryTrustedRunnerArtifactResolver。"""

    def __init__(self, resolver: MemoryTrustedRunnerArtifactResolver) -> None:
        self._resolver = resolver

    def write(
        self,
        artifact_type: str,
        value: StrictFrozenModel,
        *,
        parents: tuple[ArtifactReference, ...],
        created_by: str,
        created_at: str,
    ) -> ArtifactReference:
        del created_by, created_at
        return self._resolver.register(
            artifact_type,
            value,
            parent_artifact_ids=tuple(
                sorted(
                    {parent.artifact_id for parent in parents},
                    key=lambda item: item.encode("utf-8"),
                )
            ),
        )


class OciFixedCommitExecutor:
    """在干净 exact-commit checkout 上运行受限 Docker/OCI job。

    该实现只接受本机 Docker、cgroup v2、无直连网络且无 GPU 的闭合子集；
    需要 RemoteEnv/EvidenceGateway/GPU 的 job 在专用 collector 部署前保持
    ``WAITING_RUNTIME``，不会降级成不受验证的执行。
    """

    def __init__(
        self,
        *,
        resolver: TrustedRunnerArtifactResolver,
        specified_event_head: VerifiedEventHead,
        job_manifest: ArtifactReference,
        seccomp_profile_path: Path,
        apparmor_profile_path: Path,
        artifact_writer: RunnerArtifactWriter,
        resource_collector: OciResourceCollector | None = None,
        clock: Callable[[], str] = lambda: (
            datetime.now(UTC).isoformat().replace("+00:00", "Z")
        ),
        docker_executable: str = "docker",
        apparmor_parser_executable: str = "apparmor_parser",
        process_profile_reader: Callable[[int], str] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        command_runner: Callable[[tuple[str, ...], float], object] | None = None,
        repository_host_resolver: Callable[[str, int], tuple[str, ...]] = (
            _resolve_public_repository_addresses
        ),
    ) -> None:
        if (
            not hasattr(resolver, "resolve")
            or not hasattr(artifact_writer, "write")
            or not docker_executable
            or not apparmor_parser_executable
        ):
            raise TypeError("OCI executor dependencies do not implement closed seams")
        self._resolver = resolver
        self._head = specified_event_head
        self._job_manifest = job_manifest
        self._seccomp_profile_path = seccomp_profile_path
        self._apparmor_profile_path = apparmor_profile_path
        self._writer = artifact_writer
        self._resource_collector = (
            resource_collector or LinuxCgroupV2ResourceCollector()
        )
        self._clock = clock
        self._docker = docker_executable
        self._apparmor_parser = apparmor_parser_executable
        self._process_profile_reader = (
            process_profile_reader or self._default_process_profile_reader
        )
        self._monotonic = monotonic
        self._command_runner = command_runner or self._default_command_runner
        self._repository_host_resolver = repository_host_resolver
        self._execution_started = False
        self._last_terminal_evidence: RawExecutionEvidence | None = None
        self._last_manifest: FixedCommitJobManifest | None = None

    @property
    def execution_started(self) -> bool:
        return self._execution_started

    def terminal_failure_evidence(self, error: BaseException) -> RawExecutionEvidence:
        del error
        manifest = self._last_manifest
        if manifest is None:
            raise RunnerExecutionFailed(
                "started OCI execution lost its frozen manifest"
            )
        evidence = self._last_terminal_evidence
        if evidence is None:
            return self._synthesize_terminal_failure_evidence(manifest)
        if evidence.status == "terminal_failure":
            return evidence
        scan = cast(
            OutputScanReport,
            self._resolve_model(
                evidence.output_scan_report,
                "output_scan_report",
                OutputScanReport,
            ),
        ).model_copy(
            update={
                "scanned_outputs": (),
                "scanned_paths": (),
                "total_bytes": 0,
                "scanned_at": self._clock(),
            }
        )
        scan_reference = self._write_artifact(
            "output_scan_report",
            scan,
            (
                self._job_manifest,
                manifest.output_contract,
                manifest.scanner_policy,
            ),
            manifest,
            scan.scanned_at,
        )
        return evidence.model_copy(
            update={
                "status": "terminal_failure",
                "exit_code": 126,
                "reason_code": "fixed_commit_post_start_failure",
                "finished_at": self._clock(),
                "payload_outputs": (),
                "output_scan_report": scan_reference,
            }
        )

    def _synthesize_terminal_failure_evidence(
        self, manifest: FixedCommitJobManifest
    ) -> RawExecutionEvidence:
        now = self._clock()
        mount_policy = cast(
            ExecutionMountPolicy,
            self._resolve_model(
                manifest.mount_policy, "execution_mount_policy", ExecutionMountPolicy
            ),
        )
        scanner_policy = cast(
            OutputScannerPolicy,
            self._resolve_model(
                manifest.scanner_policy, "output_scanner_policy", OutputScannerPolicy
            ),
        )
        capability_policy = cast(
            ExecutionCapabilityPolicy,
            self._resolve_model(
                manifest.capability_policy,
                "execution_capability_policy",
                ExecutionCapabilityPolicy,
            ),
        )
        scan = OutputScanReport(
            schema_version="automarkov.output-scan-report.v1",
            job_manifest=self._job_manifest,
            scanner_policy=manifest.scanner_policy,
            output_contract=manifest.output_contract,
            scanner_rules_hash=scanner_policy.scanner_rules_hash,
            scanned_outputs=(),
            scanned_paths=(),
            total_bytes=0,
            schema_valid=True,
            scan_passed=True,
            scanned_at=now,
        )
        scan_ref = self._write_artifact(
            "output_scan_report",
            scan,
            (self._job_manifest, manifest.output_contract, manifest.scanner_policy),
            manifest,
            now,
        )
        network_log = NetworkDecisionLog(
            schema_version="automarkov.network-decision-log.v1",
            job_manifest=self._job_manifest,
            network_policy=manifest.network_policy,
            decisions=(),
        )
        network_ref = self._write_artifact(
            "network_decision_log",
            network_log,
            (self._job_manifest, manifest.network_policy),
            manifest,
            now,
        )
        mount_attestation = MountAttestation(
            schema_version="automarkov.mount-attestation.v1",
            job_manifest=self._job_manifest,
            mount_policy=manifest.mount_policy,
            actual_mounts=mount_policy.mounts,
        )
        mount_ref = self._write_artifact(
            "mount_attestation",
            mount_attestation,
            (self._job_manifest, manifest.mount_policy),
            manifest,
            now,
        )
        capability_log = CapabilityDecisionLog(
            schema_version="automarkov.capability-decision-log.v1",
            job_manifest=self._job_manifest,
            capability_policy=manifest.capability_policy,
            denied_capabilities=("capability:all",),
            effective_uid=65532,
            no_new_privileges=True,
            read_only_rootfs=True,
            dropped_capabilities=("ALL",),
            seccomp_profile_hash=capability_policy.seccomp_profile_hash,
            apparmor_profile_hash=capability_policy.apparmor_profile_hash,
        )
        capability_ref = self._write_artifact(
            "capability_decision_log",
            capability_log,
            (self._job_manifest, manifest.capability_policy),
            manifest,
            now,
        )
        egress_log = EgressDecisionLog(
            schema_version="automarkov.egress-decision-log.v1",
            job_manifest=self._job_manifest,
            network_policy=manifest.network_policy,
            decisions=(),
            revoked_at=now,
        )
        egress_ref = self._write_artifact(
            "egress_decision_log",
            egress_log,
            (self._job_manifest, manifest.network_policy),
            manifest,
            now,
        )
        usage = ExecutionResourceUsage(
            schema_version="automarkov.execution-resource-usage.v1",
            job_manifest=self._job_manifest,
            limits_policy=manifest.resource_limits,
            cpu_time_ms=0,
            peak_memory_bytes=0,
            peak_pids=0,
            io_read_bytes=0,
            io_write_bytes=0,
            peak_disk_bytes=0,
            wall_time_ms=0,
            gpu_devices=(),
        )
        usage_ref = self._write_artifact(
            "execution_resource_usage",
            usage,
            (self._job_manifest, manifest.resource_limits),
            manifest,
            now,
        )
        return RawExecutionEvidence(
            schema_version="automarkov.raw-execution-evidence.v1",
            job_id=manifest.job_id,
            process_execution_id=manifest.process_execution_id,
            source_commit=manifest.source_commit,
            profile_id=manifest.profile_id,
            image_digest=manifest.image_digest,
            status="terminal_failure",
            exit_code=126,
            reason_code="fixed_commit_post_start_failure",
            started_at=now,
            finished_at=now,
            stdout_hash="sha256:" + "0" * 64,
            stderr_hash="sha256:" + "0" * 64,
            payload_outputs=(),
            resource_usage=usage_ref,
            network_log=network_ref,
            mount_attestation=mount_ref,
            capability_decision_log=capability_ref,
            egress_decision_log=egress_ref,
            output_scan_report=scan_ref,
            egress_revoked_at=now,
        )

    @staticmethod
    def _git_command(*arguments: str) -> tuple[str, ...]:
        return (
            "env",
            "--ignore-environment",
            "PATH=/usr/bin:/bin",
            "HOME=/nonexistent",
            "XDG_CONFIG_HOME=/nonexistent",
            "LC_ALL=C",
            "GIT_CONFIG_NOSYSTEM=1",
            "GIT_CONFIG_GLOBAL=/dev/null",
            "GIT_CONFIG_SYSTEM=/dev/null",
            "GIT_ATTR_NOSYSTEM=1",
            "GIT_TERMINAL_PROMPT=0",
            "GIT_ASKPASS=",
            "GIT_LFS_SKIP_SMUDGE=1",
            "git",
            *arguments,
        )

    @staticmethod
    def _default_command_runner(
        command: tuple[str, ...], timeout_seconds: float
    ) -> object:
        maximum_bytes = 16 * 1024 * 1024
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        streams = (process.stdout, process.stderr)
        buffers = (bytearray(), bytearray())
        exceeded = Event()

        def read_bounded(index: int) -> None:
            stream = streams[index]
            assert stream is not None
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                buffers[index].extend(chunk)
                if len(buffers[index]) > maximum_bytes:
                    exceeded.set()
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    return

        readers = tuple(
            Thread(target=read_bounded, args=(index,), daemon=True)
            for index in range(2)
        )
        for reader in readers:
            reader.start()
        try:
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            raise
        finally:
            for reader in readers:
                reader.join(timeout=5)
        if exceeded.is_set() or any(reader.is_alive() for reader in readers):
            raise OSError("Docker CLI output exceeded its bounded stream contract")
        return OciCommandResult(
            returncode=returncode,
            stdout=bytes(buffers[0]),
            stderr=bytes(buffers[1]),
        )

    @staticmethod
    def _default_process_profile_reader(process_id: int) -> str:
        return (
            Path(f"/proc/{process_id}/attr/current").read_text(encoding="ascii").strip()
        )

    def _run(
        self, command: tuple[str, ...], timeout_seconds: float
    ) -> OciCommandResult:
        if (
            type(command) is not tuple
            or not command
            or any(type(item) is not str or "\x00" in item for item in command)
            or type(timeout_seconds) is not float
            or timeout_seconds <= 0
        ):
            raise RunnerPreflightError("OCI command contract is invalid")
        try:
            raw = self._command_runner(command, timeout_seconds)
        except (OSError, subprocess.SubprocessError) as error:
            raise RunnerWaitingRuntimeError("bounded local Docker CLI") from error
        returncode = getattr(raw, "returncode", None)
        stdout = getattr(raw, "stdout", None)
        stderr = getattr(raw, "stderr", None)
        if type(returncode) is not int or returncode < 0:
            raise RunnerWaitingRuntimeError("canonical Docker CLI result")
        if type(stdout) is str:
            stdout = stdout.encode("utf-8")
        if type(stderr) is str:
            stderr = stderr.encode("utf-8")
        if type(stdout) is not bytes or type(stderr) is not bytes:
            raise RunnerWaitingRuntimeError("bounded Docker CLI byte streams")
        if len(stdout) > 16 * 1024 * 1024 or len(stderr) > 16 * 1024 * 1024:
            raise RunnerWaitingRuntimeError("bounded Docker CLI byte streams")
        return OciCommandResult(returncode=returncode, stdout=stdout, stderr=stderr)

    def _unload_apparmor(self, policy_path: Path) -> None:
        unloaded = self._run(
            (self._apparmor_parser, "--remove", str(policy_path)), 30.0
        )
        if unloaded.returncode != 0:
            raise RunnerWaitingRuntimeError("verified AppArmor profile cleanup")

    @staticmethod
    def _require_success(result: OciCommandResult, operation: str) -> bytes:
        if result.returncode != 0:
            raise RunnerWaitingRuntimeError(f"successful Docker {operation}")
        return result.stdout.strip()

    @staticmethod
    def _read_exact_file(descriptor: int, byte_size: int) -> bytes:
        content = bytearray()
        while len(content) <= byte_size:
            chunk = os.read(descriptor, min(1024 * 1024, byte_size + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        return bytes(content)

    @staticmethod
    def _read_policy_file(path: Path, expected_hash: str) -> tuple[Path, bytes]:
        if path.is_symlink():
            raise RunnerPreflightError("OCI policy path must not be a symlink")
        resolved = path.resolve(strict=True)
        descriptor = os.open(resolved, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 8 * 1024 * 1024:
                raise RunnerPreflightError("OCI policy must be a bounded regular file")
            content = OciFixedCommitExecutor._read_exact_file(
                descriptor, metadata.st_size
            )
        finally:
            os.close(descriptor)
        if (
            len(content) != metadata.st_size
            or "sha256:" + sha256(content).hexdigest() != expected_hash
        ):
            raise RunnerPreflightError("OCI policy bytes differ from the frozen hash")
        return resolved, content

    @staticmethod
    def _materialize_policy_copy(path: Path, content: bytes) -> Path:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o400,
        )
        try:
            offset = 0
            while offset < len(content):
                offset += os.write(descriptor, content[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if path.is_symlink() or path.read_bytes() != content:
            raise RunnerPreflightError("materialized OCI policy bytes changed")
        return path.resolve(strict=True)

    def _materialize_checkout(
        self, manifest: FixedCommitJobManifest, scratch: Path
    ) -> Path:
        """从冻结 remote+commit 构造不含 Git 元数据的独立只读快照。"""

        hostname = urlsplit(manifest.repository_url).hostname
        if hostname is None:
            raise RunnerPreflightError("repository hostname is missing")
        addresses = self._repository_host_resolver(hostname, 443)
        if (
            type(addresses) is not tuple
            or not addresses
            or addresses
            != tuple(sorted(set(addresses), key=lambda item: item.encode("ascii")))
        ):
            raise RunnerPreflightError("repository DNS result is not canonical")
        parsed_addresses = tuple(ip_address(address) for address in addresses)
        if any(not address.is_global for address in parsed_addresses):
            raise RunnerPreflightError(
                "repository hostname resolves outside the public network"
            )
        git_version_result = self._run(self._git_command("--version"), 10.0)
        if git_version_result.returncode != 0:
            raise RunnerWaitingRuntimeError("Git with DNS pin support")
        try:
            git_version_output = git_version_result.stdout.decode("ascii").strip()
        except UnicodeDecodeError as error:
            raise RunnerWaitingRuntimeError("Git with DNS pin support") from error
        git_version_match = re.fullmatch(
            r"git version ([0-9]+)\.([0-9]+)(?:\.[0-9]+)?(?:\.[0-9]+)?(?:[-+].*)?",
            git_version_output,
        )
        if git_version_match is None or tuple(
            int(component) for component in git_version_match.groups()
        ) < (2, 37):
            raise RunnerWaitingRuntimeError("Git with DNS pin support")
        curl_addresses = ",".join(
            f"[{address}]" if address.version == 6 else str(address)
            for address in parsed_addresses
        )
        fetch_transport = (
            "-c",
            "http.followRedirects=false",
            "-c",
            f"http.curloptResolve={hostname}:443:{curl_addresses}",
        )

        git_directory = scratch / "source.git"
        work_tree = scratch / "checkout"
        work_tree.mkdir(mode=0o700)
        git_dir_arg = f"--git-dir={git_directory}"
        operations: tuple[tuple[tuple[str, ...], float, str], ...] = (
            (
                self._git_command("init", "--bare", str(git_directory)),
                30.0,
                "repository init",
            ),
            (
                self._git_command(
                    git_dir_arg,
                    "remote",
                    "add",
                    "origin",
                    manifest.repository_url,
                ),
                10.0,
                "remote binding",
            ),
            (
                self._git_command(
                    git_dir_arg,
                    *fetch_transport,
                    "-c",
                    "protocol.file.allow=never",
                    "fetch",
                    "--no-tags",
                    "--depth=1",
                    "origin",
                    manifest.source_commit,
                ),
                300.0,
                "exact commit fetch",
            ),
        )
        for command, timeout, operation in operations:
            self._require_success(self._run(command, timeout), operation)
        fetched = self._require_success(
            self._run(
                self._git_command(git_dir_arg, "rev-parse", "FETCH_HEAD^{commit}"),
                10.0,
            ),
            "fetched commit identity",
        )
        if fetched != manifest.source_commit.encode("ascii"):
            raise RunnerPreflightError("remote fetch differs from frozen commit")
        tree = self._require_success(
            self._run(
                self._git_command(
                    git_dir_arg,
                    "ls-tree",
                    "-rz",
                    "-r",
                    "--full-tree",
                    "FETCH_HEAD",
                ),
                30.0,
            ),
            "exact tree inventory",
        )
        inventory: list[tuple[str, str, str]] = []
        for entry in tree.split(b"\0"):
            if not entry:
                continue
            metadata, separator, raw_path = entry.partition(b"\t")
            fields = metadata.split(b" ")
            if not separator or len(fields) != 3:
                raise RunnerPreflightError("fixed-commit tree inventory is invalid")
            raw_mode, raw_type, raw_object_id = fields
            if raw_mode == b"160000":
                raise RunnerPreflightError(
                    "fixed-commit snapshot requires a frozen submodule-free tree"
                )
            try:
                path = raw_path.decode("utf-8")
                mode = raw_mode.decode("ascii")
                object_id = raw_object_id.decode("ascii")
            except UnicodeDecodeError as error:
                raise RunnerPreflightError(
                    "fixed-commit tree inventory is not canonical UTF-8"
                ) from error
            relative = PurePosixPath(path)
            if (
                raw_type != b"blob"
                or mode not in {"100644", "100755", "120000"}
                or re.fullmatch(r"[0-9a-f]{40}", object_id) is None
                or not relative.parts
                or path.startswith("/")
                or "\\" in path
                or any(part in {"", ".", ".."} for part in relative.parts)
                or relative.as_posix() != path
            ):
                raise RunnerPreflightError("fixed-commit tree inventory is invalid")
            inventory.append((path, mode, object_id))
        paths = tuple(path for path, _, _ in inventory)
        if len(paths) != len(set(paths)):
            raise RunnerPreflightError("fixed-commit tree inventory is not unique")
        work_tree.chmod(0o700)
        for path, mode, object_id in inventory:
            blob_result = self._run(
                self._git_command(git_dir_arg, "cat-file", "blob", object_id),
                120.0,
            )
            if blob_result.returncode != 0:
                raise RunnerWaitingRuntimeError("successful Git exact blob read")
            blob = blob_result.stdout
            actual_object_id = sha1(
                b"blob " + str(len(blob)).encode("ascii") + b"\0" + blob
            ).hexdigest()
            if actual_object_id != object_id:
                raise RunnerPreflightError(
                    "materialized Git blob differs from frozen tree"
                )
            destination = work_tree.joinpath(*PurePosixPath(path).parts)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if mode == "120000":
                if b"\0" in blob:
                    raise RunnerPreflightError("Git symlink target is invalid")
                os.symlink(blob, os.fsencode(destination))
            else:
                destination.write_bytes(blob)
                destination.chmod(0o555 if mode == "100755" else 0o444)
        if work_tree.joinpath(".git").exists():
            raise RunnerPreflightError("detached snapshot exposes Git control metadata")
        for path in sorted(
            work_tree.rglob("*"),
            key=lambda item: len(item.relative_to(work_tree).parts),
            reverse=True,
        ):
            if path.is_symlink():
                continue
            if path.is_dir():
                path.chmod(0o555)
            elif path.is_file():
                path.chmod(0o444)
            else:
                raise RunnerPreflightError("snapshot contains a non-publishable node")
        work_tree.chmod(0o555)
        return work_tree

    def _resolve_model(
        self,
        reference: ArtifactReference,
        artifact_type: str,
        model_type: type[StrictFrozenModel],
    ) -> StrictFrozenModel:
        value = _resolve_typed_artifact(
            self._resolver, self._head, reference, artifact_type
        )
        if type(value) is not model_type:
            raise RunnerPreflightError("OCI policy artifact has the wrong schema")
        return value

    def _verify_image(self, manifest: FixedCommitJobManifest) -> str:
        image = manifest.image_digest
        payload = self._require_success(
            self._run(
                (
                    self._docker,
                    "image",
                    "inspect",
                    "--format={{json .}}",
                    image,
                ),
                30.0,
            ),
            "image inspect",
        )
        try:
            document = parse_json_payload(payload)
        except ValueError as error:
            raise RunnerPreflightError(
                "Docker image inspect is not canonical JSON"
            ) from error
        if type(document) is not dict:
            raise RunnerPreflightError("Docker image inspect must be an object")
        image_data = cast(dict[str, object], document)
        repo_digests = image_data.get("RepoDigests", [])
        if type(repo_digests) is not list or any(
            type(item) is not str for item in repo_digests
        ):
            raise RunnerPreflightError("Docker image digest list is invalid")
        image_id = image_data.get("Id")
        if (
            image_data.get("Os") != "linux"
            or image_data.get("Architecture") != "amd64"
            or not (
                image_id == image
                or any(cast(str, item).endswith("@" + image) for item in repo_digests)
            )
        ):
            raise RunnerPreflightError("local OCI image differs from frozen identity")
        return cast(str, image_id)

    def _materialize_input(
        self, reference: ArtifactReference, destination: Path
    ) -> None:
        wrapper = cast(
            RunnerInput,
            self._resolve_model(reference, "runner_input", RunnerInput),
        )
        source = self._resolver.resolve(self._head, wrapper.source_artifact)
        if (
            source.reference != wrapper.source_artifact
            or not _resolved_identity_matches(source, wrapper.source_artifact)
            or source.artifact_type != wrapper.source_artifact_type
        ):
            raise RunnerPreflightError("OCI input source identity is invalid")
        destination.mkdir(mode=0o755)
        metadata = destination / "runner-input.json"
        payload = destination / "payload.json"
        metadata.write_bytes(
            canonical_json_bytes(wrapper.model_dump(mode="json", round_trip=True))
        )
        payload.write_bytes(source.payload_bytes)
        metadata.chmod(0o444)
        payload.chmod(0o444)
        destination.chmod(0o555)

    def _mount_sources(
        self,
        manifest: FixedCommitJobManifest,
        policy: ExecutionMountPolicy,
        checkout: Path,
        scratch: Path,
    ) -> tuple[dict[str, Path], Path]:
        sources: dict[str, Path] = {}
        output_root = scratch / "outputs"
        output_root.mkdir(mode=0o733)
        for mount in policy.mounts:
            if mount.source_kind == "checkout":
                source = checkout
            elif mount.source_kind == "output_root":
                source = output_root
            else:
                reference = next(
                    (
                        item
                        for item in manifest.input_artifacts
                        if item.artifact_id == mount.source_id
                    ),
                    None,
                )
                if reference is None:
                    raise RunnerPreflightError(
                        "OCI mount input is outside frozen inputs"
                    )
                source = scratch / "inputs" / reference.artifact_id
                source.parent.mkdir(mode=0o755, exist_ok=True)
                if not source.exists():
                    self._materialize_input(reference, source)
            if "," in str(source):
                raise RunnerPreflightError("OCI mount source cannot contain a comma")
            sources[mount.target_path] = source
        if not any(mount.source_kind == "output_root" for mount in policy.mounts):
            raise RunnerPreflightError("OCI job requires a frozen output-root mount")
        workdir = "/mnt/automarkov/" + manifest.working_directory
        if not any(
            workdir == target or workdir.startswith(target.rstrip("/") + "/")
            for target in sources
        ):
            raise RunnerPreflightError("OCI working directory is outside frozen mounts")
        return sources, output_root

    def _create_command(
        self,
        manifest: FixedCommitJobManifest,
        resources: FixedCommitResourceLimits,
        mounts: ExecutionMountPolicy,
        mount_sources: Mapping[str, Path],
        seccomp_path: Path,
        apparmor_profile_name: str,
    ) -> tuple[str, ...]:
        name = (
            "automarkov-"
            + re.sub(r"[^a-z0-9_.-]", "-", manifest.process_execution_id.casefold())[
                :48
            ]
            + "-"
            + secrets.token_hex(6)
        )
        command: list[str] = [
            self._docker,
            "container",
            "create",
            "--name",
            name,
            "--platform",
            manifest.target_platform,
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--security-opt",
            f"seccomp={seccomp_path}",
            "--security-opt",
            f"apparmor={apparmor_profile_name}",
            "--user",
            "65532:65532",
            "--pids-limit",
            str(resources.pids),
            "--cpu-period",
            "100000",
            "--cpu-quota",
            str(resources.cpu_millis * 100),
            "--memory",
            str(resources.memory_bytes),
            "--memory-swap",
            str(resources.memory_bytes),
            "--restart",
            "no",
            "--log-driver",
            "json-file",
            "--log-opt",
            "max-size=16m",
            "--log-opt",
            "max-file=1",
            "--no-healthcheck",
            "--workdir",
            "/mnt/automarkov/" + manifest.working_directory,
            "--entrypoint",
            manifest.argv[0],
            "--label",
            f"automarkov.job_id={manifest.job_id}",
            "--label",
            f"automarkov.process_execution_id={manifest.process_execution_id}",
        ]
        by_target = {mount.target_path: mount for mount in mounts.mounts}
        for target in sorted(mount_sources, key=lambda item: item.encode("utf-8")):
            source = mount_sources[target]
            read_only = ",readonly" if by_target[target].access == "read_only" else ""
            command.extend(
                (
                    "--mount",
                    f"type=bind,src={source},dst={target}{read_only}",
                )
            )
        command.extend((manifest.image_digest, *manifest.argv[1:]))
        return tuple(command)

    @staticmethod
    def _container_document(payload: bytes) -> dict[str, object]:
        try:
            document = parse_json_payload(payload)
        except ValueError as error:
            raise RunnerPreflightError(
                "Docker container inspect is invalid JSON"
            ) from error
        if type(document) is not dict:
            raise RunnerPreflightError("Docker container inspect must be an object")
        return cast(dict[str, object], document)

    def _verify_running_container(
        self,
        document: Mapping[str, object],
        manifest: FixedCommitJobManifest,
        resources: FixedCommitResourceLimits,
        mounts: ExecutionMountPolicy,
        mount_sources: Mapping[str, Path],
        seccomp_path: Path,
        apparmor_profile_name: str,
        image_id: str,
    ) -> int:
        host = document.get("HostConfig")
        config = document.get("Config")
        state = document.get("State")
        actual_mounts = document.get("Mounts")
        if (
            not all(type(item) is dict for item in (host, config, state))
            or type(actual_mounts) is not list
        ):
            raise RunnerPreflightError("Docker inspect omits frozen runtime fields")
        host_data = cast(dict[str, object], host)
        config_data = cast(dict[str, object], config)
        state_data = cast(dict[str, object], state)
        security_options = host_data.get("SecurityOpt")
        cap_add = host_data.get("CapAdd")
        cap_drop = host_data.get("CapDrop")
        destinations = {
            cast(str, item.get("Destination")): (
                cast(str, item.get("Source")),
                bool(item.get("RW")),
                cast(str, item.get("Type")),
            )
            for item in actual_mounts
            if type(item) is dict
            and type(cast(dict[str, object], item).get("Destination")) is str
            and type(cast(dict[str, object], item).get("Source")) is str
            and type(cast(dict[str, object], item).get("Type")) is str
        }
        expected_mounts = {
            mount.target_path: (
                str(mount_sources[mount.target_path].resolve(strict=True)),
                mount.access == "write_only",
                "bind",
            )
            for mount in mounts.mounts
        }
        expected_security = {
            "no-new-privileges=true",
            f"seccomp={seccomp_path}",
            f"apparmor={apparmor_profile_name}",
        }
        pid = state_data.get("Pid")
        labels = config_data.get("Labels")
        restart_policy = host_data.get("RestartPolicy")
        log_config = host_data.get("LogConfig")
        tmpfs = host_data.get("Tmpfs")
        if (
            document.get("Image") != image_id
            or config_data.get("User") != "65532:65532"
            or config_data.get("Entrypoint") != [manifest.argv[0]]
            or config_data.get("Cmd") != list(manifest.argv[1:])
            or config_data.get("WorkingDir")
            != "/mnt/automarkov/" + manifest.working_directory
            or type(labels) is not dict
            or cast(dict[str, object], labels).get("automarkov.job_id")
            != manifest.job_id
            or cast(dict[str, object], labels).get("automarkov.process_execution_id")
            != manifest.process_execution_id
            or host_data.get("NetworkMode") != "none"
            or host_data.get("ReadonlyRootfs") is not True
            or host_data.get("Privileged") is not False
            or cap_add not in (None, [])
            or type(cap_drop) is not list
            or {str(item).upper() for item in cap_drop} != {"ALL"}
            or type(security_options) is not list
            or len(security_options) != len(expected_security)
            or {str(item) for item in security_options} != expected_security
            or host_data.get("PidsLimit") != resources.pids
            or host_data.get("CpuPeriod") != 100_000
            or host_data.get("CpuQuota") != resources.cpu_millis * 100
            or host_data.get("Memory") != resources.memory_bytes
            or host_data.get("MemorySwap") != resources.memory_bytes
            or type(restart_policy) is not dict
            or cast(dict[str, object], restart_policy).get("Name") != "no"
            or log_config
            != {
                "Type": "json-file",
                "Config": {"max-file": "1", "max-size": "16m"},
            }
            or tmpfs not in (None, {})
            or destinations != expected_mounts
            or type(pid) is not int
            or not (
                (state_data.get("Running") is True and pid > 0)
                or (
                    state_data.get("Running") is False
                    and state_data.get("Status") == "exited"
                    and pid == 0
                    and type(state_data.get("ExitCode")) is int
                    and 0 <= cast(int, state_data["ExitCode"]) <= 255
                )
            )
        ):
            raise RunnerPreflightError(
                "running OCI container differs from frozen policy"
            )
        return pid

    @staticmethod
    def _read_outputs(
        output_root: Path, contract: ExecutionOutputContract
    ) -> tuple[RunnerOutputBinding, ...]:
        observed: dict[str, bytes] = {}
        observed_size = 0
        for path in output_root.rglob("*"):
            if path.is_symlink():
                raise RunnerPreflightError("OCI output contains a symlink")
            if path.is_dir():
                continue
            if not path.is_file():
                raise RunnerPreflightError("OCI output is not a regular file")
            relative = path.relative_to(output_root).as_posix()
            if relative not in contract.allowed_paths:
                raise RunnerPreflightError("OCI output path is outside frozen contract")
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise RunnerPreflightError("OCI output is not a regular file")
                remaining = contract.maximum_total_bytes - observed_size
                if metadata.st_size > remaining:
                    raise RunnerPreflightError(
                        "OCI output exceeds the frozen byte limit"
                    )
                content = OciFixedCommitExecutor._read_exact_file(
                    descriptor, metadata.st_size
                )
            finally:
                os.close(descriptor)
            if len(content) != metadata.st_size:
                raise RunnerPreflightError("OCI output changed while being collected")
            observed[relative] = content
            observed_size += len(content)
        if (
            tuple(sorted(observed, key=lambda item: item.encode("utf-8")))
            != (contract.allowed_paths)
            or sum(len(content) for content in observed.values())
            > contract.maximum_total_bytes
        ):
            raise RunnerPreflightError("OCI output set differs from frozen contract")
        schemas = {binding.path: binding for binding in contract.output_schemas}
        return tuple(
            RunnerOutputBinding(
                schema_version="automarkov.runner-output-binding.v2",
                path=path,
                byte_size=len(observed[path]),
                media_type="application/json",
                content_hash="sha256:" + sha256(observed[path]).hexdigest(),
                content_schema_version=schemas[path].schema_version,
                content_b64url=base64.urlsafe_b64encode(observed[path])
                .decode()
                .rstrip("="),
                schema_valid=True,
            )
            for path in contract.allowed_paths
        )

    def _write_artifact(
        self,
        artifact_type: str,
        value: StrictFrozenModel,
        parents: tuple[ArtifactReference, ...],
        manifest: FixedCommitJobManifest,
        created_at: str,
    ) -> ArtifactReference:
        return self._writer.write(
            artifact_type,
            value,
            parents=parents,
            created_by=manifest.principal_id,
            created_at=created_at,
        )

    def _persist_scanned_outputs(
        self,
        outputs: tuple[RunnerOutputBinding, ...],
        contract: ExecutionOutputContract,
        manifest: FixedCommitJobManifest,
        created_at: str,
        *,
        require_complete: bool,
    ) -> tuple[ArtifactReference, ...]:
        actual_bytes = tuple(output.verified_content_bytes() for output in outputs)
        FixedCommitRunner._validate_actual_output_bytes(
            outputs,
            actual_bytes,
            contract,
            require_complete=require_complete,
        )
        persisted: list[ArtifactReference] = []
        for output, content in zip(outputs, actual_bytes, strict=True):
            if (
                output.content_schema_version
                == "automarkov.runner-artifact-reference-output.v1"
            ):
                reference_payload = RunnerArtifactReferencePayload.model_validate_json(
                    content, strict=True
                )
                embedded = reference_payload.embedded_payload_bytes()
                if embedded is not None:
                    expected_type = _TYPED_ARTIFACT_REFERENCE_OUTPUT_TYPES.get(
                        output.path
                    )
                    if expected_type != reference_payload.artifact_type:
                        raise RunnerPreflightError(
                            "embedded output artifact type does not match frozen path"
                        )
                    expected_reference = runner_payload_reference(
                        reference_payload.artifact_type, embedded
                    )
                    if expected_reference != reference_payload.artifact:
                        raise RunnerPreflightError(
                            "embedded output artifact reference does not match bytes"
                        )
                    FixedCommitRunner._scan_actual_payload_bytes(embedded)
                    if reference_payload.artifact_type in (
                        SEALED_SUBJECT_ARTIFACT_CONTRACTS
                    ):
                        contract_spec = SEALED_SUBJECT_ARTIFACT_CONTRACTS[
                            reference_payload.artifact_type
                        ]
                        value = contract_spec.model_type.model_validate_json(
                            embedded, strict=True
                        )
                        canonical = canonical_json_bytes(value.model_dump(mode="json"))
                        if (
                            canonical != embedded
                            or value.job_manifest != self._job_manifest
                        ):
                            raise RunnerPreflightError(
                                "embedded sealed subject does not bind the worker job"
                            )
                        artifact_parents = (self._job_manifest,)
                    elif reference_payload.artifact_type == "e2e_gate_verdict":
                        from automarkov.sealed_evaluation import E2EGateVerdict

                        value = E2EGateVerdict.model_validate_json(
                            embedded, strict=True
                        )
                        canonical = canonical_json_bytes(value.model_dump(mode="json"))
                        if canonical != embedded:
                            raise RunnerPreflightError(
                                "embedded E2E verdict is not canonical"
                            )
                        artifact_parents = (
                            value.run_manifest,
                            value.candidate_bundle,
                            value.task_contract,
                            value.decision_process_spec,
                            value.environment_binding,
                        )
                    else:
                        raise RunnerPreflightError(
                            "embedded output artifact type is not approved"
                        )
                    actual_reference = self._write_artifact(
                        reference_payload.artifact_type,
                        value,
                        artifact_parents,
                        manifest,
                        created_at,
                    )
                    if actual_reference != reference_payload.artifact:
                        reference_payload = reference_payload.model_copy(
                            update={"artifact": actual_reference}
                        )
                        rebound_content = canonical_json_bytes(
                            reference_payload.model_dump(
                                mode="json",
                                round_trip=True,
                                warnings="error",
                                exclude_computed_fields=True,
                            )
                        )
                        output = RunnerOutputBinding.model_validate(
                            output.model_dump(mode="json")
                            | {
                                "byte_size": len(rebound_content),
                                "content_hash": "sha256:"
                                + sha256(rebound_content).hexdigest(),
                                "content_b64url": base64.urlsafe_b64encode(
                                    rebound_content
                                )
                                .decode("ascii")
                                .rstrip("="),
                            },
                            strict=True,
                        )
            persisted.append(
                self._write_artifact(
                    "runner_output_binding",
                    output,
                    (),
                    manifest,
                    created_at,
                )
            )
        return tuple(
            sorted(persisted, key=lambda item: item.artifact_id.encode("utf-8"))
        )

    def execute(self, manifest: FixedCommitJobManifest) -> RawExecutionEvidence:
        self._execution_started = False
        self._last_terminal_evidence = None
        self._last_manifest = manifest
        resolved_manifest = self._resolve_model(
            self._job_manifest, "fixed_commit_job_manifest", FixedCommitJobManifest
        )
        if resolved_manifest != manifest:
            raise RunnerPreflightError(
                "OCI executor manifest differs from frozen artifact"
            )
        resources = cast(
            FixedCommitResourceLimits,
            self._resolve_model(
                manifest.resource_limits,
                "fixed_commit_resource_limits",
                FixedCommitResourceLimits,
            ),
        )
        network = cast(
            PhaseNetworkPolicy,
            self._resolve_model(
                manifest.network_policy, "phase_network_policy", PhaseNetworkPolicy
            ),
        )
        mounts = cast(
            ExecutionMountPolicy,
            self._resolve_model(
                manifest.mount_policy, "execution_mount_policy", ExecutionMountPolicy
            ),
        )
        capabilities = cast(
            ExecutionCapabilityPolicy,
            self._resolve_model(
                manifest.capability_policy,
                "execution_capability_policy",
                ExecutionCapabilityPolicy,
            ),
        )
        output_contract = cast(
            ExecutionOutputContract,
            self._resolve_model(
                manifest.output_contract,
                "execution_output_contract",
                ExecutionOutputContract,
            ),
        )
        scanner = cast(
            OutputScannerPolicy,
            self._resolve_model(
                manifest.scanner_policy,
                "output_scanner_policy",
                OutputScannerPolicy,
            ),
        )
        if network.egress_allowlist or network.protocol_edges:
            raise RunnerWaitingRuntimeError("verified OCI network/control-edge adapter")
        if resources.gpu_devices:
            raise RunnerWaitingRuntimeError("verified OCI GPU accounting")
        _, seccomp_bytes = self._read_policy_file(
            self._seccomp_profile_path, capabilities.seccomp_profile_hash
        )
        _, apparmor_bytes = self._read_policy_file(
            self._apparmor_profile_path, capabilities.apparmor_profile_hash
        )
        apparmor_profile_name = _apparmor_profile_name(manifest.process_execution_id)
        if capabilities.apparmor_profile_name != apparmor_profile_name:
            raise RunnerPreflightError(
                "AppArmor profile name is not bound to the process execution"
            )
        image_id = self._verify_image(manifest)
        container_id: str | None = None
        with tempfile.TemporaryDirectory(
            prefix="automarkov-fixed-commit-"
        ) as temporary:
            scratch = Path(temporary)
            scratch.chmod(0o700)
            checkout = self._materialize_checkout(manifest, scratch)
            policy_root = scratch / "policies"
            policy_root.mkdir(mode=0o700)
            seccomp = self._materialize_policy_copy(
                policy_root / "seccomp.json", seccomp_bytes
            )
            apparmor = self._materialize_policy_copy(
                policy_root / "apparmor.profile", apparmor_bytes
            )
            declared_profiles = re.findall(
                rb"(?:^|\n)\s*profile\s+([A-Za-z0-9_-]+)(?:\s|\{)",
                apparmor_bytes,
            )
            if declared_profiles != [apparmor_profile_name.encode("ascii")]:
                raise RunnerPreflightError(
                    "AppArmor bytes must define exactly the frozen profile"
                )
            self._require_success(
                self._run(
                    (
                        self._apparmor_parser,
                        "--replace",
                        "--warn=all",
                        str(apparmor),
                    ),
                    30.0,
                ),
                "AppArmor profile load",
            )
            try:
                mount_sources, output_root = self._mount_sources(
                    manifest, mounts, checkout, scratch
                )
                create = self._create_command(
                    manifest,
                    resources,
                    mounts,
                    mount_sources,
                    seccomp,
                    apparmor_profile_name,
                )
            except BaseException:
                self._unload_apparmor(apparmor)
                raise
            try:
                created = self._require_success(
                    self._run(create, 60.0), "container create"
                )
                try:
                    container_id = created.decode("ascii")
                except UnicodeDecodeError as error:
                    raise RunnerPreflightError("container ID is not ASCII") from error
                if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
                    raise RunnerPreflightError(
                        "Docker returned a noncanonical container ID"
                    )
                started_at = self._clock()
                execution_started_monotonic = self._monotonic()
                self._require_success(
                    self._run((self._docker, "container", "start", container_id), 30.0),
                    "container start",
                )
                self._execution_started = True
                running = self._container_document(
                    self._require_success(
                        self._run(
                            (
                                self._docker,
                                "container",
                                "inspect",
                                "--format={{json .}}",
                                container_id,
                            ),
                            30.0,
                        ),
                        "running container inspect",
                    )
                )
                container_pid = self._verify_running_container(
                    running,
                    manifest,
                    resources,
                    mounts,
                    mount_sources,
                    seccomp,
                    apparmor_profile_name,
                    image_id,
                )
                if container_pid > 0 and self._process_profile_reader(
                    container_pid
                ) != (apparmor_profile_name + " (enforce)"):
                    raise RunnerPreflightError(
                        "running container lacks the frozen AppArmor profile"
                    )

                def wait_for_exit() -> OciCommandResult:
                    return self._run(
                        (self._docker, "container", "wait", container_id),
                        resources.wall_time_ms / 1_000 + 10.0,
                    )

                def terminate() -> None:
                    self._run((self._docker, "container", "kill", container_id), 10.0)

                post_start_error: Exception | None = None
                if container_pid == 0:
                    waited = wait_for_exit()
                    observation = OciResourceObservation(
                        schema_version="automarkov.oci-resource-observation.v1",
                        cpu_time_ms=0,
                        peak_memory_bytes=0,
                        peak_pids=0,
                        io_read_bytes=0,
                        io_write_bytes=0,
                        peak_disk_bytes=0,
                        wall_time_ms=max(
                            0,
                            int(
                                (self._monotonic() - execution_started_monotonic)
                                * 1_000
                            ),
                        ),
                        gpu_devices=(),
                        timed_out=False,
                        limit_exceeded=False,
                    )
                    post_start_error = RunnerWaitingRuntimeError(
                        "complete cgroup evidence for an already-exited container"
                    )
                else:
                    observation, waited = self._resource_collector.collect(
                        container_pid=container_pid,
                        output_root=output_root,
                        wait_for_exit=wait_for_exit,
                        terminate=terminate,
                        limits=resources,
                        execution_started_monotonic=execution_started_monotonic,
                    )
                wait_stdout = self._require_success(waited, "container wait")
                try:
                    exit_code = int(wait_stdout.decode("ascii"))
                except (UnicodeDecodeError, ValueError) as error:
                    raise RunnerPreflightError(
                        "container exit code is invalid"
                    ) from error
                if not 0 <= exit_code <= 255:
                    raise RunnerPreflightError("container exit code is outside uint8")
                if observation.timed_out:
                    exit_code = 124
                elif observation.limit_exceeded:
                    exit_code = 125
                elif container_pid == 0:
                    exit_code = 126
                try:
                    logs = self._run(
                        (self._docker, "container", "logs", container_id), 30.0
                    )
                    self._require_success(logs, "container logs")
                # 进程启动后，任意普通 Python 异常都必须闭合为 terminal evidence。
                except Exception as error:  # noqa: BLE001
                    if post_start_error is None:
                        post_start_error = error
                    logs = OciCommandResult(returncode=126, stdout=b"", stderr=b"")
                    exit_code = 126
                finished_at = self._clock()
                try:
                    output_models = (
                        self._read_outputs(output_root, output_contract)
                        if exit_code == 0
                        else ()
                    )
                    output_references = self._persist_scanned_outputs(
                        output_models,
                        output_contract,
                        manifest,
                        finished_at,
                        require_complete=exit_code == 0,
                    )
                # 输出收集失败同样属于已启动 execution 的终态，而不是重试入口。
                except Exception as error:  # noqa: BLE001
                    post_start_error = error
                    exit_code = 126
                    output_models = ()
                    output_references = ()
                usage = ExecutionResourceUsage(
                    schema_version="automarkov.execution-resource-usage.v1",
                    job_manifest=self._job_manifest,
                    limits_policy=manifest.resource_limits,
                    cpu_time_ms=observation.cpu_time_ms,
                    peak_memory_bytes=observation.peak_memory_bytes,
                    peak_pids=observation.peak_pids,
                    io_read_bytes=observation.io_read_bytes,
                    io_write_bytes=observation.io_write_bytes,
                    peak_disk_bytes=observation.peak_disk_bytes,
                    wall_time_ms=observation.wall_time_ms,
                    gpu_devices=observation.gpu_devices,
                )
                usage_ref = self._write_artifact(
                    "execution_resource_usage",
                    usage,
                    (self._job_manifest, manifest.resource_limits),
                    manifest,
                    finished_at,
                )
                network_log = NetworkDecisionLog(
                    schema_version="automarkov.network-decision-log.v1",
                    job_manifest=self._job_manifest,
                    network_policy=manifest.network_policy,
                    decisions=(),
                )
                network_ref = self._write_artifact(
                    "network_decision_log",
                    network_log,
                    (self._job_manifest, manifest.network_policy),
                    manifest,
                    finished_at,
                )
                mount_attestation = MountAttestation(
                    schema_version="automarkov.mount-attestation.v1",
                    job_manifest=self._job_manifest,
                    mount_policy=manifest.mount_policy,
                    actual_mounts=mounts.mounts,
                )
                mount_ref = self._write_artifact(
                    "mount_attestation",
                    mount_attestation,
                    (self._job_manifest, manifest.mount_policy),
                    manifest,
                    finished_at,
                )
                capability_log = CapabilityDecisionLog(
                    schema_version="automarkov.capability-decision-log.v1",
                    job_manifest=self._job_manifest,
                    capability_policy=manifest.capability_policy,
                    denied_capabilities=("capability:all",),
                    effective_uid=65532,
                    no_new_privileges=True,
                    read_only_rootfs=True,
                    dropped_capabilities=("ALL",),
                    seccomp_profile_hash=capabilities.seccomp_profile_hash,
                    apparmor_profile_hash=capabilities.apparmor_profile_hash,
                )
                capability_ref = self._write_artifact(
                    "capability_decision_log",
                    capability_log,
                    (self._job_manifest, manifest.capability_policy),
                    manifest,
                    finished_at,
                )
                egress_log = EgressDecisionLog(
                    schema_version="automarkov.egress-decision-log.v1",
                    job_manifest=self._job_manifest,
                    network_policy=manifest.network_policy,
                    decisions=(),
                    revoked_at=finished_at,
                )
                egress_ref = self._write_artifact(
                    "egress_decision_log",
                    egress_log,
                    (self._job_manifest, manifest.network_policy),
                    manifest,
                    finished_at,
                )
                scan = OutputScanReport(
                    schema_version="automarkov.output-scan-report.v1",
                    job_manifest=self._job_manifest,
                    scanner_policy=manifest.scanner_policy,
                    output_contract=manifest.output_contract,
                    scanner_rules_hash=scanner.scanner_rules_hash,
                    scanned_outputs=output_references,
                    scanned_paths=tuple(output.path for output in output_models),
                    total_bytes=sum(output.byte_size for output in output_models),
                    schema_valid=True,
                    scan_passed=True,
                    scanned_at=finished_at,
                )
                scan_ref = self._write_artifact(
                    "output_scan_report",
                    scan,
                    (
                        self._job_manifest,
                        manifest.output_contract,
                        manifest.scanner_policy,
                        *output_references,
                    ),
                    manifest,
                    finished_at,
                )
                terminal_evidence = RawExecutionEvidence(
                    schema_version="automarkov.raw-execution-evidence.v1",
                    job_id=manifest.job_id,
                    process_execution_id=manifest.process_execution_id,
                    source_commit=manifest.source_commit,
                    profile_id=manifest.profile_id,
                    image_digest=manifest.image_digest,
                    status="success" if exit_code == 0 else "terminal_failure",
                    exit_code=exit_code,
                    reason_code=(
                        "fixed_commit_completed"
                        if exit_code == 0
                        else (
                            "fixed_commit_post_start_failure"
                            if post_start_error is not None
                            else (
                                "fixed_commit_timeout"
                                if observation.timed_out
                                else (
                                    "fixed_commit_resource_limit"
                                    if observation.limit_exceeded
                                    else "fixed_commit_process_failed"
                                )
                            )
                        )
                    ),
                    started_at=started_at,
                    finished_at=finished_at,
                    stdout_hash="sha256:" + sha256(logs.stdout).hexdigest(),
                    stderr_hash="sha256:" + sha256(logs.stderr).hexdigest(),
                    payload_outputs=output_references,
                    resource_usage=usage_ref,
                    network_log=network_ref,
                    mount_attestation=mount_ref,
                    capability_decision_log=capability_ref,
                    egress_decision_log=egress_ref,
                    output_scan_report=scan_ref,
                    egress_revoked_at=finished_at,
                )
                self._last_terminal_evidence = terminal_evidence
                return terminal_evidence
            finally:
                try:
                    if container_id is not None:
                        removed = self._run(
                            (
                                self._docker,
                                "container",
                                "rm",
                                "--force",
                                "--volumes",
                                container_id,
                            ),
                            30.0,
                        )
                        if removed.returncode != 0:
                            raise RunnerWaitingRuntimeError(
                                "verified OCI container cleanup"
                            )
                finally:
                    self._unload_apparmor(apparmor)


def execution_attestation_signing_bytes(value: ExecutionAttestation) -> bytes:
    payload = value.model_dump(mode="json", round_trip=True, warnings="error")
    del payload["signature_b64url"]
    if payload.get("output_scan_report") is None:
        payload.pop("output_scan_report", None)
    return canonical_json_bytes(payload)


def execution_attestation_signature_bytes(value: ExecutionAttestation) -> bytes:
    return base64.urlsafe_b64decode(value.signature_b64url + "==")


def verify_execution_attestation_signature(
    value: ExecutionAttestation,
    public_key: Ed25519PublicKey,
) -> None:
    if not isinstance(public_key, Ed25519PublicKey):
        raise TypeError("runner public key must be Ed25519")
    try:
        public_key.verify(
            execution_attestation_signature_bytes(value),
            execution_attestation_signing_bytes(value),
        )
    except InvalidSignature as error:
        raise RunnerReplayError("execution attestation signature is invalid") from error


def sign_execution_attestation(
    *,
    manifest: FixedCommitJobManifest,
    job_manifest: ArtifactReference,
    evidence: RawExecutionEvidence,
    process_outputs: tuple[ArtifactReference, ...],
    process_reference: ArtifactReference,
    terminal_result: ArtifactReference | None,
    issued_at: str,
    nonce_b64url: str,
    signing_key_id: str,
    signing_key: Ed25519PrivateKey,
) -> ExecutionAttestation:
    fields: dict[str, object] = {
        "schema_version": "automarkov.execution-attestation.v1",
        "signing_domain": "AutoMarkov-Execution-Attestation-v1",
        "experiment_id": manifest.experiment_id,
        "run_id": manifest.run_id,
        "job_id": manifest.job_id,
        "process_execution_id": manifest.process_execution_id,
        "profile_id": manifest.profile_id,
        "principal_id": manifest.principal_id,
        "job_manifest": job_manifest,
        "process_terminal_record": process_reference,
        "payload_outputs": process_outputs,
        "output_scan_report": evidence.output_scan_report,
        "terminal_result": terminal_result,
        "network_policy_hash": manifest.network_policy.payload_hash,
        "mount_table_hash": evidence.mount_attestation.payload_hash,
        "capability_decision_log_hash": evidence.capability_decision_log.payload_hash,
        "actual_phase_transition": ExecutionPhaseTransition(
            from_phase=manifest.from_phase,
            to_phase=manifest.to_phase,
            transitioned_at=evidence.finished_at,
        ),
        "egress_decision_log_hash": evidence.egress_decision_log.payload_hash,
        "egress_revoked_at": evidence.egress_revoked_at,
        "issued_at": issued_at,
        "nonce_b64url": nonce_b64url,
        "signing_key_id": signing_key_id,
        "signature_algorithm": "Ed25519",
        "signature_b64url": "A" * 86,
    }
    provisional = ExecutionAttestation.model_validate(fields, strict=True)
    fields["signature_b64url"] = (
        base64.urlsafe_b64encode(
            signing_key.sign(execution_attestation_signing_bytes(provisional))
        )
        .decode("ascii")
        .rstrip("=")
    )
    return ExecutionAttestation.model_validate(fields, strict=True)


def _content_reference(
    domain: str,
    value: StrictFrozenModel,
) -> ArtifactReference:
    payload = canonical_json_bytes(
        value.model_dump(mode="json", round_trip=True, warnings="error")
    )
    payload_digest = sha256(payload).hexdigest()
    artifact_digest = sha256(domain.encode("ascii") + b"\x00" + payload).hexdigest()
    return ArtifactReference(
        artifact_id=f"artifact_{artifact_digest}",
        payload_hash=f"sha256:{payload_digest}",
    )


def runner_artifact_reference(
    artifact_type: str,
    value: StrictFrozenModel,
) -> ArtifactReference:
    if not artifact_type or not artifact_type.replace("_", "").isalnum():
        raise ValueError("runner artifact type is invalid")
    payload_bytes = canonical_json_bytes(
        value.model_dump(
            mode="json",
            round_trip=True,
            warnings="error",
            exclude_computed_fields=True,
        )
    )
    return runner_payload_reference(artifact_type, payload_bytes)


def runner_payload_reference(
    artifact_type: str,
    payload_bytes: bytes,
) -> ArtifactReference:
    if type(payload_bytes) is not bytes or not payload_bytes:
        raise ValueError("runner payload must be nonempty exact bytes")
    payload_digest = sha256(payload_bytes).hexdigest()
    artifact_digest = sha256(
        f"AutoMarkov-{artifact_type}-Artifact-v1".encode("ascii")
        + b"\x00"
        + payload_bytes
    ).hexdigest()
    return ArtifactReference(
        artifact_id=f"artifact_{artifact_digest}",
        payload_hash=f"sha256:{payload_digest}",
    )


class ResolvedRunnerArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.resolved-runner-artifact.v1"]
    reference: ArtifactReference
    artifact_type: NonEmptyId
    payload_schema_version: NonEmptyId
    payload_bytes: bytes = Field(strict=True, max_length=8 * 1024 * 1024)
    parent_artifact_ids: FrozenSequence[str]
    identity_scheme: Literal["runner_payload_v1", "artifact_repository_v2"] = (
        "runner_payload_v1"
    )
    repository_payload_document_bytes: bytes | None = None
    repository_envelope_bytes: bytes | None = None


class TrustedRunnerArtifactResolver(Protocol):
    def resolve(
        self,
        head: VerifiedEventHead,
        reference: ArtifactReference,
    ) -> ResolvedRunnerArtifact: ...

    def runtime_attestation_key_policies(
        self,
    ) -> Mapping[str, RuntimeAttestationKeyPolicy]: ...

    def runner_signing_key_grant(
        self, job_reference: ArtifactReference | None = None
    ) -> ManifestEventSigningKey: ...

    def worker_kind_for_job(
        self, job_reference: ArtifactReference
    ) -> SealedWorkerKind | None: ...

    def validate_job_authorization(
        self,
        head: VerifiedEventHead,
        job_reference: ArtifactReference,
        manifest: FixedCommitJobManifest,
        runner_key_grant: ManifestEventSigningKey,
    ) -> None: ...


class MemoryTrustedRunnerArtifactResolver:
    """按 caller 指定 head 提供 canonical bytes，并由 runner 重验身份。"""

    def __init__(self, head: VerifiedEventHead) -> None:
        self._head = head
        self._artifacts: dict[str, ResolvedRunnerArtifact] = {}
        self._runtime_signing_key = Ed25519PrivateKey.generate()
        self._runtime_key_policy = RuntimeAttestationKeyPolicy(
            signing_key_id="key_memory_runtime",
            issuer_id="issuer_memory_runtime",
            public_key=self._runtime_signing_key.public_key(),
            not_before="2026-08-01T00:00:00Z",
            not_after="2026-09-01T00:00:00Z",
            allowed_profile_ids=frozenset({"runner-control"}),
            allowed_kinds=frozenset({"build", "import_smoke"}),
        )
        self._runner_signing_key = Ed25519PrivateKey.generate()
        self._runner_key_grant = ManifestEventSigningKey(
            signing_key_id="key_runner",
            principal_id="principal_runner",
            signature_algorithm="Ed25519",
            public_key_b64url=base64.urlsafe_b64encode(
                self._runner_signing_key.public_key().public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw,
                )
            )
            .decode()
            .rstrip("="),
            not_before="2026-08-01T00:00:00Z",
            not_after="2026-09-01T00:00:00Z",
            revoked_at=None,
        )
        self._authorized_jobs: dict[str, FixedCommitRunAuthorization] = {}

    @property
    def runner_signing_key(self) -> Ed25519PrivateKey:
        return self._runner_signing_key

    def runner_signing_key_grant(
        self, job_reference: ArtifactReference | None = None
    ) -> ManifestEventSigningKey:
        if (
            job_reference is not None
            and job_reference.artifact_id not in self._authorized_jobs
        ):
            raise RunnerPreflightError("job has no frozen runner signing grant")
        return self._runner_key_grant

    def worker_kind_for_job(
        self, job_reference: ArtifactReference
    ) -> SealedWorkerKind | None:
        del job_reference
        return None

    def runtime_attestation_key_policies(
        self,
    ) -> Mapping[str, RuntimeAttestationKeyPolicy]:
        return {self._runtime_key_policy.signing_key_id: self._runtime_key_policy}

    def register(
        self,
        artifact_type: str,
        value: StrictFrozenModel,
        *,
        parent_artifact_ids: tuple[str, ...] = (),
    ) -> ArtifactReference:
        reference = runner_artifact_reference(artifact_type, value)
        payload_bytes = canonical_json_bytes(
            value.model_dump(
                mode="json",
                round_trip=True,
                warnings="error",
                exclude_computed_fields=True,
            )
        )
        self._artifacts[reference.artifact_id] = ResolvedRunnerArtifact(
            schema_version="automarkov.resolved-runner-artifact.v1",
            reference=reference,
            artifact_type=artifact_type,
            payload_schema_version=cast(
                str, value.model_dump(mode="json")["schema_version"]
            ),
            payload_bytes=payload_bytes,
            parent_artifact_ids=parent_artifact_ids,
        )
        return reference

    def register_payload(
        self,
        artifact_type: str,
        payload_schema_version: str,
        payload_bytes: bytes,
    ) -> ArtifactReference:
        reference = runner_payload_reference(artifact_type, payload_bytes)
        self._artifacts[reference.artifact_id] = ResolvedRunnerArtifact(
            schema_version="automarkov.resolved-runner-artifact.v1",
            reference=reference,
            artifact_type=artifact_type,
            payload_schema_version=payload_schema_version,
            payload_bytes=payload_bytes,
            parent_artifact_ids=(),
        )
        return reference

    def inject_corrupt_payload(
        self,
        reference: ArtifactReference,
        artifact_type: str,
        value: StrictFrozenModel,
    ) -> None:
        self._artifacts[reference.artifact_id] = ResolvedRunnerArtifact(
            schema_version="automarkov.resolved-runner-artifact.v1",
            reference=reference,
            artifact_type=artifact_type,
            payload_schema_version=cast(
                str, value.model_dump(mode="json")["schema_version"]
            ),
            payload_bytes=canonical_json_bytes(value.model_dump(mode="json")),
            parent_artifact_ids=(),
        )

    def inject_corrupt_bytes(
        self,
        reference: ArtifactReference,
        artifact_type: str,
        payload_bytes: bytes,
        *,
        payload_schema_version: str = "automarkov.runner-input.v1",
    ) -> None:
        self._artifacts[reference.artifact_id] = ResolvedRunnerArtifact(
            schema_version="automarkov.resolved-runner-artifact.v1",
            reference=reference,
            artifact_type=artifact_type,
            payload_schema_version=payload_schema_version,
            payload_bytes=payload_bytes,
            parent_artifact_ids=(),
        )

    def resolve(
        self,
        head: VerifiedEventHead,
        reference: ArtifactReference,
    ) -> ResolvedRunnerArtifact:
        if head != self._head:
            raise RunnerPreflightError(
                "resolver head does not match caller-specified head"
            )
        try:
            return self._artifacts[reference.artifact_id]
        except KeyError as error:
            raise RunnerPreflightError(
                "artifact is unavailable at specified head"
            ) from error

    def freeze_job(
        self,
        draft: FixedCommitJobManifest,
        *,
        build_evidence_kind: Literal["build", "import_smoke"] = "build",
        output_paths: tuple[str, ...] | None = None,
        mounts: tuple[ExecutionMount, ...] = (),
        seccomp_profile_hash: str = "sha256:" + "a" * 64,
        apparmor_profile_hash: str = "sha256:" + "b" * 64,
    ) -> tuple[FixedCommitJobManifest, ArtifactReference]:
        build_evidence = self.register(
            "runner_runtime_evidence",
            RunnerRuntimeEvidence(
                schema_version="automarkov.runner-runtime-evidence.v1",
                evidence_kind=build_evidence_kind,
                image_digest=draft.image_digest,
            ),
        )
        import_evidence = self.register(
            "runner_runtime_evidence",
            RunnerRuntimeEvidence(
                schema_version="automarkov.runner-runtime-evidence.v1",
                evidence_kind="import_smoke",
                image_digest=draft.image_digest,
            ),
        )
        common_attestation_fields: dict[str, object] = {
            "schema_version": "automarkov.runner-runtime-attestation.v1",
            "signing_domain": "AutoMarkov-Runner-Runtime-Attestation-v1",
            "issuer_id": self._runtime_key_policy.issuer_id,
            "signing_key_id": self._runtime_key_policy.signing_key_id,
            "profile_id": draft.profile_id,
            "image_digest": draft.image_digest,
            "observed_at": "2026-08-12T10:00:00Z",
            "signature_algorithm": "Ed25519",
        }
        build_attestation = self.register(
            "runner_runtime_attestation",
            _sign_runtime_attestation(
                {
                    **common_attestation_fields,
                    "attestation_kind": "build",
                    "nonce_b64url": base64.urlsafe_b64encode(secrets.token_bytes(16))
                    .decode()
                    .rstrip("="),
                    "evidence_refs": (build_evidence,),
                },
                self._runtime_signing_key,
            ),
        )
        import_attestation = self.register(
            "runner_runtime_attestation",
            _sign_runtime_attestation(
                {
                    **common_attestation_fields,
                    "attestation_kind": "import_smoke",
                    "nonce_b64url": base64.urlsafe_b64encode(secrets.token_bytes(16))
                    .decode()
                    .rstrip("="),
                    "evidence_refs": (import_evidence,),
                },
                self._runtime_signing_key,
            ),
        )
        profile_model = RuntimeProfileManifest.model_validate(
            {
                "schema_version": "automarkov.runtime-profile-manifest.v2",
                "profile_id": draft.profile_id,
                "python_version": "3.12.11",
                "lockfile_path": "uv.lock",
                "lock_hash": draft.profile_lock_hash,
                "containerfile_path": "Containerfile",
                "build_context_files": [
                    ".dockerignore",
                    "Containerfile",
                    "pyproject.toml",
                    "uv.lock",
                ],
                "build_context_hash": "sha256:" + "9" * 64,
                "target_platform": draft.target_platform,
                "image_status": "built",
                "image_digest": draft.image_digest,
                "platform": draft.target_platform,
                "libc_version": "glibc-2.36",
                "openssl_version": "OpenSSL-3.0.17",
                "ca_bundle_hash": "sha256:" + "8" * 64,
                "build_attestation_id": build_attestation.artifact_id,
                "build_attestation_hash": build_attestation.payload_hash,
                "import_smoke_attestation_id": import_attestation.artifact_id,
                "import_smoke_attestation_hash": import_attestation.payload_hash,
                "sbom_path": "sbom.spdx.json",
                "sbom_hash": "sha256:" + "7" * 64,
                "license_manifest_path": "license-manifest.json",
                "license_manifest_hash": "sha256:" + "6" * 64,
                "smoke_contract_path": "smoke.json",
                "smoke_contract_hash": "sha256:" + "5" * 64,
                "package_versions": {},
                "repository_commits": {},
                "dataset_revisions": {},
                "model_revisions": {},
                "hardware_contract": "cpu",
                "capabilities": [],
                "conflict_groups": [],
                "egress_allowlist": [],
                "credential_ids": ["fixed-commit-signing.v1"],
                "read_mounts": ["/mnt/automarkov/artifacts/control"],
                "write_mounts": ["/mnt/automarkov/artifacts/attestations"],
                "protocol_edges": ["FixedCommitRunner", "RemoteEnv"],
                "restricted": False,
                "build_enabled": True,
                "publishable": True,
            },
            strict=True,
        )
        profile = self.register(
            "runtime_profile_manifest",
            profile_model,
            parent_artifact_ids=tuple(
                sorted(
                    (
                        build_attestation.artifact_id,
                        import_attestation.artifact_id,
                    )
                )
            ),
        )
        inputs: list[ArtifactReference] = []
        for index, reference in enumerate(draft.input_artifacts):
            source = self.register_payload(
                "runner_input_source",
                "automarkov.runner-input-source.v1",
                canonical_json_bytes(reference.model_dump(mode="json")),
            )
            inputs.append(
                self.register(
                    "runner_input",
                    RunnerInput(
                        schema_version="automarkov.runner-input.v1",
                        input_index=index,
                        source_artifact=source,
                        source_artifact_type="runner_input_source",
                        source_commitment=source.payload_hash,
                    ),
                )
            )
        frozen_inputs = tuple(inputs)
        resource = self.register(
            "fixed_commit_resource_limits",
            FixedCommitResourceLimits(
                schema_version="automarkov.fixed-commit-resource-limits.v1",
                phase=draft.phase,
                cpu_millis=1000,
                memory_bytes=1024 * 1024,
                pids=16,
                io_bytes=1024 * 1024,
                disk_bytes=1024 * 1024,
                wall_time_ms=60_000,
                gpu_devices=(),
            ),
        )
        network = self.register(
            "phase_network_policy",
            PhaseNetworkPolicy(
                schema_version="automarkov.phase-network-policy.v1",
                phase=draft.phase,
                egress_allowlist=("api.tavily.com:443",)
                if draft.phase == "retrieval"
                else (),
                protocol_edges=_PHASE_PROTOCOL_EDGE_MATRIX[draft.phase],
                gateway_principal_id="principal_retrieval-tavily"
                if draft.phase == "retrieval"
                else None,
                deny_ip_literals=True,
                deny_redirect_egress=True,
                revoke_before_output_scan=True,
            ),
        )
        mount = self.register(
            "execution_mount_policy",
            ExecutionMountPolicy(
                schema_version="automarkov.execution-mount-policy.v1",
                candidate_worker=True,
                mounts=mounts,
            ),
        )
        capability = self.register(
            "execution_capability_policy",
            ExecutionCapabilityPolicy(
                schema_version="automarkov.execution-capability-policy.v1",
                drop_all_capabilities=True,
                allowed_capabilities=(),
                no_new_privileges=True,
                read_only_rootfs=True,
                non_root=True,
                seccomp_profile_hash=seccomp_profile_hash,
                apparmor_profile_hash=apparmor_profile_hash,
                apparmor_profile_name=_apparmor_profile_name(
                    draft.process_execution_id
                ),
            ),
        )
        frozen_output_paths = output_paths or ("result.json",)
        output_schemas = tuple(
            OutputSchemaBinding(
                path=path,
                schema_version=(
                    "automarkov.runner-output.v1"
                    if path == "result.json"
                    else "automarkov.runner-artifact-reference-output.v1"
                ),
                schema_identity_hash=(
                    RUNNER_RESULT_PAYLOAD_SCHEMA_HASH
                    if path == "result.json"
                    else RUNNER_ARTIFACT_REFERENCE_PAYLOAD_SCHEMA_HASH
                ),
            )
            for path in frozen_output_paths
        )
        output_contract = self.register(
            "execution_output_contract",
            ExecutionOutputContract(
                schema_version="automarkov.execution-output-contract.v1",
                allowed_paths=frozen_output_paths,
                output_schemas=output_schemas,
                maximum_total_bytes=1024 * 1024,
                require_regular_files=True,
                forbid_symlinks=True,
                forbid_extra_outputs=True,
            ),
        )
        scanner = self.register(
            "output_scanner_policy",
            OutputScannerPolicy(
                schema_version="automarkov.output-scanner-policy.v1",
                scanner_id="scanner_test",
                scanner_version="1.0.0",
                scanner_rules_hash=RUNNER_OUTPUT_SCANNER_RULES_HASH,
                reject_secrets=True,
                reject_gold_markers=True,
                reject_credential_locators=True,
            ),
        )
        manifest = FixedCommitJobManifest.model_validate(
            draft.model_copy(
                update={
                    "profile_manifest": profile,
                    "input_artifacts": frozen_inputs,
                    "resource_limits": resource,
                    "network_policy": network,
                    "mount_policy": mount,
                    "capability_policy": capability,
                    "output_contract": output_contract,
                    "scanner_policy": scanner,
                }
            ).model_dump(),
            strict=True,
        )
        parents = tuple(
            sorted(
                {
                    profile.artifact_id,
                    *(item.artifact_id for item in frozen_inputs),
                    resource.artifact_id,
                    network.artifact_id,
                    mount.artifact_id,
                    capability.artifact_id,
                    output_contract.artifact_id,
                    scanner.artifact_id,
                }
            )
        )
        reference = self.register(
            "fixed_commit_job_manifest",
            manifest,
            parent_artifact_ids=parents,
        )
        self._authorized_jobs[reference.artifact_id] = _job_authorization(
            manifest,
            reference,
            runner_key_grant=self._runner_key_grant,
        )
        return manifest, reference

    def validate_job_authorization(
        self,
        head: VerifiedEventHead,
        job_reference: ArtifactReference,
        manifest: FixedCommitJobManifest,
        runner_key_grant: ManifestEventSigningKey,
    ) -> None:
        if head != self._head:
            raise RunnerPreflightError("authorization head differs from specified head")
        expected = _job_authorization(
            manifest,
            job_reference,
            runner_key_grant=runner_key_grant,
        )
        if self._authorized_jobs.get(job_reference.artifact_id) != expected:
            raise RunnerPreflightError("job is not frozen by the trusted run graph")

    def freeze_execution_evidence(
        self,
        manifest: FixedCommitJobManifest,
        manifest_reference: ArtifactReference,
    ) -> RawExecutionEvidence:
        capability_policy = cast(
            ExecutionCapabilityPolicy,
            _resolve_typed_artifact(
                self,
                self._head,
                manifest.capability_policy,
                "execution_capability_policy",
            ),
        )
        output = self.register(
            "runner_output_binding",
            RunnerOutputBinding(
                schema_version="automarkov.runner-output-binding.v2",
                path="result.json",
                byte_size=62,
                media_type="application/json",
                content_hash=(
                    "sha256:"
                    + sha256(
                        b'{"schema_version":"automarkov.runner-output.v1","status":"ok"}'
                    ).hexdigest()
                ),
                content_schema_version="automarkov.runner-output.v1",
                content_b64url=base64.urlsafe_b64encode(
                    b'{"schema_version":"automarkov.runner-output.v1","status":"ok"}'
                )
                .decode()
                .rstrip("="),
                schema_valid=True,
            ),
        )
        resource = self.register(
            "execution_resource_usage",
            ExecutionResourceUsage(
                schema_version="automarkov.execution-resource-usage.v1",
                job_manifest=manifest_reference,
                limits_policy=manifest.resource_limits,
                cpu_time_ms=100,
                peak_memory_bytes=4096,
                peak_pids=2,
                io_read_bytes=64,
                io_write_bytes=64,
                peak_disk_bytes=128,
                wall_time_ms=60_000,
                gpu_devices=(),
            ),
        )
        network = self.register(
            "network_decision_log",
            NetworkDecisionLog(
                schema_version="automarkov.network-decision-log.v1",
                job_manifest=manifest_reference,
                network_policy=manifest.network_policy,
                decisions=(),
            ),
        )
        mount = self.register(
            "mount_attestation",
            MountAttestation(
                schema_version="automarkov.mount-attestation.v1",
                job_manifest=manifest_reference,
                mount_policy=manifest.mount_policy,
                actual_mounts=(),
            ),
        )
        capability = self.register(
            "capability_decision_log",
            CapabilityDecisionLog(
                schema_version="automarkov.capability-decision-log.v1",
                job_manifest=manifest_reference,
                capability_policy=manifest.capability_policy,
                denied_capabilities=("capability:all",),
                effective_uid=65532,
                no_new_privileges=True,
                read_only_rootfs=True,
                dropped_capabilities=("ALL",),
                seccomp_profile_hash=capability_policy.seccomp_profile_hash,
                apparmor_profile_hash=capability_policy.apparmor_profile_hash,
            ),
        )
        egress = self.register(
            "egress_decision_log",
            EgressDecisionLog(
                schema_version="automarkov.egress-decision-log.v1",
                job_manifest=manifest_reference,
                network_policy=manifest.network_policy,
                decisions=(),
                revoked_at="2026-08-12T11:00:59Z",
            ),
        )
        scanner_policy = cast(
            OutputScannerPolicy,
            _resolve_typed_artifact(
                self,
                self._head,
                manifest.scanner_policy,
                "output_scanner_policy",
            ),
        )
        scan = self.register(
            "output_scan_report",
            OutputScanReport(
                schema_version="automarkov.output-scan-report.v1",
                job_manifest=manifest_reference,
                scanner_policy=manifest.scanner_policy,
                output_contract=manifest.output_contract,
                scanner_rules_hash=scanner_policy.scanner_rules_hash,
                scanned_outputs=(output,),
                scanned_paths=("result.json",),
                total_bytes=62,
                schema_valid=True,
                scan_passed=True,
                scanned_at="2026-08-12T11:00:59Z",
            ),
            parent_artifact_ids=tuple(
                sorted(
                    {
                        manifest_reference.artifact_id,
                        manifest.scanner_policy.artifact_id,
                        manifest.output_contract.artifact_id,
                        output.artifact_id,
                    }
                )
            ),
        )
        return RawExecutionEvidence(
            schema_version="automarkov.raw-execution-evidence.v1",
            job_id=manifest.job_id,
            process_execution_id=manifest.process_execution_id,
            source_commit=manifest.source_commit,
            profile_id=manifest.profile_id,
            image_digest=manifest.image_digest,
            status="success",
            exit_code=0,
            reason_code="completed",
            started_at="2026-08-12T11:00:00Z",
            finished_at="2026-08-12T11:01:00Z",
            stdout_hash="sha256:" + "1" * 64,
            stderr_hash="sha256:" + "2" * 64,
            payload_outputs=(output,),
            resource_usage=resource,
            network_log=network,
            mount_attestation=mount,
            capability_decision_log=capability,
            egress_decision_log=egress,
            output_scan_report=scan,
            egress_revoked_at="2026-08-12T11:00:59Z",
        )


class ArtifactRepositoryTrustedRunnerArtifactResolver:
    """从 immutable repository 与 caller-specified head 重算工件 identity。"""

    def __init__(
        self,
        repository: ArtifactRepository,
        specified_event_head: VerifiedEventHead,
        *,
        trusted_runtime_attestation_keys: Mapping[str, RuntimeAttestationKeyPolicy]
        | None = None,
    ) -> None:
        self._repository = repository
        self._head = specified_event_head
        self._runtime_attestation_keys = dict(trusted_runtime_attestation_keys or {})

    def runtime_attestation_key_policies(
        self,
    ) -> Mapping[str, RuntimeAttestationKeyPolicy]:
        return dict(self._runtime_attestation_keys)

    def _projected_run_manifest(self) -> RunManifest:
        projection = self._repository.project(
            DomainRunId(root=self._head.run_id.root),
            self._head,
            projector_version=RUN_PROJECTOR_VERSION,
            projector_hash=DomainSha256Digest(root=RUN_PROJECTOR_HASH),
        )
        if projection.run_manifest is None:
            raise RunnerPreflightError(
                "specified head has no frozen run manifest reference"
            )
        return cast(
            RunManifest,
            _resolve_typed_artifact(
                self,
                self._head,
                projection.run_manifest,
                "run_manifest",
            ),
        )

    def _authorization_for_job(
        self, job_reference: ArtifactReference | None
    ) -> FixedCommitRunAuthorization:
        run_manifest = self._projected_run_manifest()
        authorization_reference = run_manifest.fixed_commit_authorization
        if job_reference is not None:
            sealed_matches = tuple(
                item
                for item in run_manifest.sealed_worker_authorizations
                if item.job_manifest == job_reference
            )
            if len(sealed_matches) > 1:
                raise RunnerPreflightError(
                    "job has multiple sealed worker authorizations"
                )
            if sealed_matches:
                authorization_reference = sealed_matches[0].fixed_commit_authorization
        return cast(
            FixedCommitRunAuthorization,
            _resolve_typed_artifact(
                self,
                self._head,
                authorization_reference,
                "fixed_commit_run_authorization",
            ),
        )

    def runner_signing_key_grant(
        self, job_reference: ArtifactReference | None = None
    ) -> ManifestEventSigningKey:
        return self._authorization_for_job(job_reference).runner_key_grant

    def worker_kind_for_job(
        self, job_reference: ArtifactReference
    ) -> SealedWorkerKind | None:
        matches = tuple(
            item.worker_kind
            for item in self._projected_run_manifest().sealed_worker_authorizations
            if item.job_manifest == job_reference
        )
        if len(matches) > 1:
            raise RunnerPreflightError("job has multiple sealed worker roles")
        return cast(SealedWorkerKind, matches[0]) if matches else None

    def validate_job_authorization(
        self,
        head: VerifiedEventHead,
        job_reference: ArtifactReference,
        manifest: FixedCommitJobManifest,
        runner_key_grant: ManifestEventSigningKey,
    ) -> None:
        if head != self._head:
            raise RunnerPreflightError("authorization head differs from specified head")
        projection = self._repository.project(
            DomainRunId(root=head.run_id.root),
            head,
            projector_version=RUN_PROJECTOR_VERSION,
            projector_hash=DomainSha256Digest(root=RUN_PROJECTOR_HASH),
        )
        if projection.run_manifest is None:
            raise RunnerPreflightError(
                "specified head has no frozen run manifest reference"
            )
        run_manifest = cast(
            RunManifest,
            _resolve_typed_artifact(
                self, head, projection.run_manifest, "run_manifest"
            ),
        )
        authorization = self._authorization_for_job(job_reference)
        if (
            run_manifest.run_id != manifest.run_id
            or run_manifest.experiment_id != manifest.experiment_id
            or authorization
            != _job_authorization(
                manifest,
                job_reference,
                runner_key_grant=runner_key_grant,
            )
        ):
            raise RunnerPreflightError(
                "job is not frozen by the projected run manifest"
            )

    def resolve(
        self,
        head: VerifiedEventHead,
        reference: ArtifactReference,
    ) -> ResolvedRunnerArtifact:
        if head != self._head:
            raise RunnerPreflightError(
                "repository resolver head differs from caller-specified head"
            )
        self._repository.project(
            DomainRunId(root=head.run_id.root),
            head,
            projector_version=RUN_PROJECTOR_VERSION,
            projector_hash=DomainSha256Digest(root=RUN_PROJECTOR_HASH),
        )
        result = self._repository.get(ArtifactId(root=reference.artifact_id))
        envelope_bytes = canonical_json_bytes(
            result.envelope.model_dump(mode="json", round_trip=True, warnings="error")
        )
        payload_document_bytes = bytes(result.payload_bytes)
        envelope = result.envelope
        if (
            result.artifact_id.root != reference.artifact_id
            or envelope.payload_hash != reference.payload_hash
            or "artifact_" + sha256(envelope_bytes).hexdigest() != reference.artifact_id
            or "sha256:" + sha256(payload_document_bytes).hexdigest()
            != reference.payload_hash
        ):
            raise RunnerPreflightError("repository artifact identity is invalid")
        document = parse_json_payload(payload_document_bytes)
        if type(document) is not dict or "payload" not in document:
            raise RunnerPreflightError("repository payload document is invalid")
        payload_bytes = canonical_json_bytes(
            cast(dict[str, object], document)["payload"]
        )
        payload = parse_json_payload(payload_bytes)
        if type(payload) is not dict:
            raise RunnerPreflightError("runner repository payload must be an object")
        schema_version = cast(dict[str, object], payload).get("schema_version")
        if type(schema_version) is not str or schema_version != envelope.schema_version:
            raise RunnerPreflightError("repository payload schema identity is invalid")
        return ResolvedRunnerArtifact(
            schema_version="automarkov.resolved-runner-artifact.v1",
            reference=reference,
            artifact_type=envelope.artifact_type,
            payload_schema_version=schema_version,
            payload_bytes=payload_bytes,
            parent_artifact_ids=tuple(
                item.root for item in envelope.parent_artifact_ids
            ),
            identity_scheme="artifact_repository_v2",
            repository_payload_document_bytes=payload_document_bytes,
            repository_envelope_bytes=envelope_bytes,
        )


_RUNNER_ARTIFACT_MODELS: dict[str, type[StrictFrozenModel]] = {
    "fixed_commit_job_manifest": FixedCommitJobManifest,
    "fixed_commit_run_authorization": FixedCommitRunAuthorization,
    "run_manifest": RunManifest,
    "runner_input": RunnerInput,
    "runtime_profile_manifest": RuntimeProfileManifest,
    "runner_runtime_attestation": RunnerRuntimeAttestation,
    "runner_runtime_evidence": RunnerRuntimeEvidence,
    "runner_output_binding": RunnerOutputBinding,
    "fixed_commit_resource_limits": FixedCommitResourceLimits,
    "phase_network_policy": PhaseNetworkPolicy,
    "execution_mount_policy": ExecutionMountPolicy,
    "execution_capability_policy": ExecutionCapabilityPolicy,
    "execution_output_contract": ExecutionOutputContract,
    "output_scanner_policy": OutputScannerPolicy,
    "execution_resource_usage": ExecutionResourceUsage,
    "network_decision_log": NetworkDecisionLog,
    "mount_attestation": MountAttestation,
    "capability_decision_log": CapabilityDecisionLog,
    "egress_decision_log": EgressDecisionLog,
    "output_scan_report": OutputScanReport,
    **{
        artifact_type: contract.model_type
        for artifact_type, contract in SEALED_SUBJECT_ARTIFACT_CONTRACTS.items()
    },
}


def _resolved_identity_matches(
    resolved: ResolvedRunnerArtifact,
    reference: ArtifactReference,
) -> bool:
    if resolved.identity_scheme == "runner_payload_v1":
        return (
            runner_payload_reference(resolved.artifact_type, resolved.payload_bytes)
            == reference
        )
    document_bytes = resolved.repository_payload_document_bytes
    envelope_bytes = resolved.repository_envelope_bytes
    if document_bytes is None or envelope_bytes is None:
        return False
    try:
        document = parse_json_payload(document_bytes)
        envelope = parse_json_payload(envelope_bytes)
        payload = parse_json_payload(resolved.payload_bytes)
    except ValueError:
        return False
    if type(document) is not dict or type(envelope) is not dict:
        return False
    envelope_object = cast(dict[str, object], envelope)
    return (
        "artifact_" + sha256(envelope_bytes).hexdigest() == reference.artifact_id
        and "sha256:" + sha256(document_bytes).hexdigest() == reference.payload_hash
        and cast(dict[str, object], document).get("payload") == payload
        and envelope_object.get("artifact_type") == resolved.artifact_type
        and envelope_object.get("schema_version") == resolved.payload_schema_version
        and envelope_object.get("payload_hash") == reference.payload_hash
        and envelope_object.get("parent_artifact_ids")
        == list(resolved.parent_artifact_ids)
    )


def _resolve_payload_artifact(
    resolver: TrustedRunnerArtifactResolver,
    head: VerifiedEventHead,
    reference: ArtifactReference,
    artifact_type: str,
) -> ResolvedRunnerArtifact:
    resolved = resolver.resolve(head, reference)
    if resolved.reference != reference:
        raise RunnerPreflightError("resolved payload reference does not match")
    if resolved.artifact_type != artifact_type:
        raise RunnerPreflightError("resolved artifact type does not match")
    subject_contract = SEALED_SUBJECT_ARTIFACT_CONTRACTS.get(artifact_type)
    if (
        subject_contract is not None
        and resolved.payload_schema_version != subject_contract.schema_version
    ):
        raise RunnerPreflightError("resolved artifact schema does not match")
    if not _resolved_identity_matches(resolved, reference):
        raise RunnerPreflightError("resolved payload content identity does not match")
    return resolved


def _parse_sealed_subject_artifact(
    resolved: ResolvedRunnerArtifact,
    expected_job_manifest: ArtifactReference,
) -> _SealedSubjectOutput:
    contract = SEALED_SUBJECT_ARTIFACT_CONTRACTS[resolved.artifact_type]
    try:
        subject = contract.model_type.model_validate_json(
            resolved.payload_bytes, strict=True
        )
    except ValueError as error:
        raise RunnerPreflightError(
            "resolved sealed subject artifact schema is invalid"
        ) from error
    canonical_payload = canonical_json_bytes(
        subject.model_dump(
            mode="json", round_trip=True, warnings="error", exclude_unset=True
        )
    )
    if (
        canonical_payload != resolved.payload_bytes
        or subject.job_manifest != expected_job_manifest
    ):
        raise RunnerPreflightError(
            "resolved sealed subject artifact binding is invalid"
        )
    return subject


def _resolve_typed_artifact(
    resolver: TrustedRunnerArtifactResolver,
    head: VerifiedEventHead,
    reference: ArtifactReference,
    artifact_type: str,
) -> StrictFrozenModel:
    resolved = resolver.resolve(head, reference)
    model_type = _RUNNER_ARTIFACT_MODELS[artifact_type]
    if resolved.reference != reference or resolved.artifact_type != artifact_type:
        raise RunnerPreflightError("resolved artifact identity or type does not match")
    try:
        value = model_type.model_validate_json(resolved.payload_bytes, strict=True)
    except ValueError as error:
        raise RunnerPreflightError("resolved artifact payload is invalid") from error
    if value.model_dump(mode="json").get(
        "schema_version"
    ) != resolved.payload_schema_version or not _resolved_identity_matches(
        resolved, reference
    ):
        raise RunnerPreflightError("resolved artifact content identity does not match")
    return value


class RunnerExecutionFailed(RuntimeError):
    pass


class RunnerExecutionCheckpoint(StrictFrozenModel):
    schema_version: Literal["automarkov.runner-execution-checkpoint.v1"]
    process: ProcessExecutionTerminalRecord
    process_reference: ArtifactReference
    evidence: RawExecutionEvidence

    @model_validator(mode="after")
    def require_scanner_provenance(self) -> Self:
        if (
            self.process.payload_outputs != self.evidence.payload_outputs
            or self.process.resource_usage != self.evidence.resource_usage
            or self.process.network_log_hash != self.evidence.network_log.payload_hash
            or self.process.mount_attestation_hash
            != self.evidence.mount_attestation.payload_hash
            or self.process.capability_decision_hash
            != self.evidence.capability_decision_log.payload_hash
            or self.process.egress_log_hash
            != self.evidence.egress_decision_log.payload_hash
        ):
            raise ValueError("runner checkpoint must bind its exact execution evidence")
        return self


class RunnerArtifactStore(Protocol):
    def replay(self, fingerprint: str) -> FixedCommitExecutionResult | None: ...

    def reserve(
        self, job_id: str, process_execution_id: str, fingerprint: str
    ) -> FixedCommitExecutionResult | RunnerExecutionCheckpoint | None: ...

    def checkpoint(
        self,
        job_id: str,
        fingerprint: str,
        checkpoint: RunnerExecutionCheckpoint,
    ) -> RunnerExecutionCheckpoint: ...

    def fail(self, job_id: str, fingerprint: str, error: BaseException) -> None: ...

    def release(self, job_id: str, fingerprint: str) -> None: ...

    def commit(
        self,
        *,
        fingerprint: str,
        process: ProcessExecutionTerminalRecord,
        process_reference: ArtifactReference,
        attestation: ExecutionAttestation,
        resolved_evidence: Mapping[str, tuple[ArtifactReference, StrictFrozenModel]],
        terminal_result: TerminalResult | None = None,
        terminal_reference: ArtifactReference | None = None,
    ) -> FixedCommitExecutionResult: ...


class _Reservation:
    def __init__(self, fingerprint: str, process_execution_id: str) -> None:
        self.fingerprint = fingerprint
        self.process_execution_id = process_execution_id
        self.state: Literal["executing", "checkpointed", "completed", "failed"] = (
            "executing"
        )
        self.result: FixedCommitExecutionResult | None = None
        self.failure: str | None = None
        self.checkpoint: RunnerExecutionCheckpoint | None = None
        self.active = True


class RunnerTerminalCommitReceipt(StrictFrozenModel):
    schema_version: Literal["automarkov.runner-terminal-commit-receipt.v1"]
    process_terminal_record: ArtifactReference
    terminal_result: ArtifactReference


class RunnerTerminalCommitter(Protocol):
    def commit_terminal(
        self,
        process: ProcessExecutionTerminalRecord,
    ) -> tuple[RunnerTerminalCommitReceipt, TerminalResult]: ...


class MemoryRunnerArtifactStore:
    """执行前 reservation 与 runner artifact 的可回读原子内存合同。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._reservations: dict[str, _Reservation] = {}
        self._process_owners: dict[str, str] = {}
        self._models: dict[str, StrictFrozenModel] = {}
        self._model_types: dict[str, str] = {}
        self._references: dict[str, ArtifactReference] = {}
        self._attestation_nonces: dict[tuple[str, str], str] = {}
        self._parents: dict[str, tuple[str, ...]] = {}

    def replay(self, fingerprint: str) -> FixedCommitExecutionResult | None:
        with self._lock:
            matches = tuple(
                reservation.result
                for reservation in self._reservations.values()
                if reservation.fingerprint == fingerprint
                and reservation.state == "completed"
            )
            if len(matches) > 1:
                raise RunnerReplayError("runner fingerprint has multiple results")
            return matches[0] if matches else None

    def reserve(
        self,
        job_id: str,
        process_execution_id: str,
        fingerprint: str,
    ) -> FixedCommitExecutionResult | RunnerExecutionCheckpoint | None:
        with self._condition:
            owner = self._process_owners.get(process_execution_id)
            if owner is not None and owner != job_id:
                raise RunnerReplayError("process execution ID is already reserved")
            reservation = self._reservations.get(job_id)
            if reservation is None:
                self._process_owners[process_execution_id] = job_id
                self._reservations[job_id] = _Reservation(
                    fingerprint, process_execution_id
                )
                return None
            if (
                reservation.fingerprint != fingerprint
                or reservation.process_execution_id != process_execution_id
            ):
                raise RunnerReplayError("job ID was replayed with conflicting bytes")
            while reservation.state == "executing" or (
                reservation.state == "checkpointed" and reservation.active
            ):
                self._condition.wait()
            if reservation.state == "completed":
                return cast(FixedCommitExecutionResult, reservation.result)
            if reservation.state == "failed":
                raise RunnerExecutionFailed(cast(str, reservation.failure))
            if reservation.checkpoint is not None:
                reservation.active = True
                return reservation.checkpoint
            return cast(FixedCommitExecutionResult, reservation.result)

    def checkpoint(
        self,
        job_id: str,
        fingerprint: str,
        checkpoint: RunnerExecutionCheckpoint,
    ) -> RunnerExecutionCheckpoint:
        with self._condition:
            reservation = self._reservations[job_id]
            if reservation.fingerprint != fingerprint:
                raise RunnerReplayError("checkpoint does not bind reservation")
            if reservation.state == "checkpointed":
                if reservation.checkpoint != checkpoint:
                    raise RunnerReplayError("checkpoint conflicts with persisted bytes")
                return checkpoint
            if reservation.state != "executing" or reservation.checkpoint is not None:
                raise RunnerReplayError(
                    "checkpoint does not bind executing reservation"
                )
            reservation.checkpoint = checkpoint
            reservation.state = "checkpointed"
            self._condition.notify_all()
            return checkpoint

    def fail(self, job_id: str, fingerprint: str, error: BaseException) -> None:
        with self._condition:
            reservation = self._reservations[job_id]
            if reservation.fingerprint != fingerprint or reservation.state not in {
                "executing",
                "checkpointed",
            }:
                raise RunnerReplayError("failure does not bind active reservation")
            reservation.state = "failed"
            reservation.active = False
            reservation.failure = f"{type(error).__name__}: {error}"
            self._condition.notify_all()

    def release(self, job_id: str, fingerprint: str) -> None:
        with self._condition:
            reservation = self._reservations[job_id]
            if reservation.fingerprint != fingerprint:
                raise RunnerReplayError("release does not bind checkpoint")
            if reservation.state == "executing":
                del self._reservations[job_id]
                del self._process_owners[reservation.process_execution_id]
                self._condition.notify_all()
                return
            if reservation.state != "checkpointed":
                raise RunnerReplayError("release does not bind recoverable execution")
            reservation.active = False
            self._condition.notify_all()

    def commit(
        self,
        *,
        fingerprint: str,
        process: ProcessExecutionTerminalRecord,
        process_reference: ArtifactReference,
        attestation: ExecutionAttestation,
        resolved_evidence: Mapping[str, tuple[ArtifactReference, StrictFrozenModel]],
        terminal_result: TerminalResult | None = None,
        terminal_reference: ArtifactReference | None = None,
    ) -> FixedCommitExecutionResult:
        if attestation.process_terminal_record != process_reference:
            raise RunnerReplayError("attestation does not bind exact process record")
        if (terminal_result is None) != (terminal_reference is None):
            raise RunnerReplayError("terminal result model/reference must be paired")
        if attestation.terminal_result != terminal_reference:
            raise RunnerReplayError("attestation does not bind exact terminal result")
        attestation_reference = _content_reference(
            "AutoMarkov-ExecutionAttestation-Artifact-v1", attestation
        )
        process_parents = tuple(
            sorted(
                {
                    process.job_manifest.artifact_id,
                    process.resource_usage.artifact_id,
                    *(item.artifact_id for item in process.payload_outputs),
                },
                key=lambda item: item.encode("utf-8"),
            )
        )
        attestation_parents = tuple(
            sorted(
                {
                    attestation.job_manifest.artifact_id,
                    attestation.process_terminal_record.artifact_id,
                    *(
                        (attestation.output_scan_report.artifact_id,)
                        if attestation.output_scan_report is not None
                        else ()
                    ),
                    *(item.artifact_id for item in attestation.payload_outputs),
                    *(
                        (attestation.terminal_result.artifact_id,)
                        if attestation.terminal_result is not None
                        else ()
                    ),
                },
                key=lambda item: item.encode("utf-8"),
            )
        )
        result = FixedCommitExecutionResult(
            schema_version="automarkov.fixed-commit-execution-result.v1",
            process_terminal_record=process_reference,
            execution_attestation=attestation_reference,
            terminal_result=terminal_reference,
        )
        attestation_fingerprint = sha256(
            canonical_json_bytes(attestation.model_dump(mode="json"))
        ).hexdigest()
        nonce_key = (attestation.signing_key_id, attestation.nonce_b64url)
        with self._condition:
            reservation = self._reservations[process.job_id]
            if reservation.fingerprint != fingerprint:
                raise RunnerReplayError("commit does not bind active reservation")
            existing_nonce = self._attestation_nonces.get(nonce_key)
            if existing_nonce not in {None, attestation_fingerprint}:
                raise RunnerReplayError("attestation signing nonce was replayed")
            for artifact_type, (reference, model) in resolved_evidence.items():
                registered_type = (
                    "runner_output_binding"
                    if artifact_type.startswith("runner_output_binding_")
                    else artifact_type
                )
                if runner_artifact_reference(registered_type, model) != reference:
                    raise RunnerReplayError("resolved evidence identity changed")
                self._models[reference.artifact_id] = model
                self._model_types[reference.artifact_id] = registered_type
                self._references[reference.artifact_id] = reference
            self._models[process_reference.artifact_id] = process
            self._model_types[process_reference.artifact_id] = "process_terminal_record"
            self._references[process_reference.artifact_id] = process_reference
            self._models[attestation_reference.artifact_id] = attestation
            self._model_types[attestation_reference.artifact_id] = (
                "execution_attestation"
            )
            self._references[attestation_reference.artifact_id] = attestation_reference
            if terminal_result is not None and terminal_reference is not None:
                self._models[terminal_reference.artifact_id] = terminal_result
                self._model_types[terminal_reference.artifact_id] = "terminal_result"
                self._references[terminal_reference.artifact_id] = terminal_reference
                self._parents[terminal_reference.artifact_id] = tuple(
                    sorted(
                        {
                            terminal_result.fixed_commit_job_manifest.artifact_id,
                            terminal_result.process_execution_terminal_record.artifact_id,
                            *(
                                item.artifact_id
                                for item in terminal_result.payload_outputs
                            ),
                        }
                    )
                )
            self._attestation_nonces[nonce_key] = attestation_fingerprint
            self._parents[process_reference.artifact_id] = process_parents
            self._parents[attestation_reference.artifact_id] = attestation_parents
            reservation.result = result
            reservation.state = "completed"
            reservation.active = False
            self._condition.notify_all()
        return result

    def get(
        self,
        reference: ArtifactReference,
        expected_type: type[StrictFrozenModel],
    ) -> StrictFrozenModel:
        with self._lock:
            try:
                value = self._models[reference.artifact_id]
                artifact_type = self._model_types[reference.artifact_id]
                stored_reference = self._references[reference.artifact_id]
            except KeyError as error:
                raise KeyError("unknown runner artifact") from error
        if type(value) is not expected_type:
            raise RunnerReplayError("stored runner artifact has wrong schema")
        expected_reference = {
            "process_terminal_record": lambda: _content_reference(
                "AutoMarkov-ProcessExecutionTerminalRecord-Artifact-v1", value
            ),
            "execution_attestation": lambda: _content_reference(
                "AutoMarkov-ExecutionAttestation-Artifact-v1", value
            ),
            "terminal_result": lambda: _content_reference(
                "AutoMarkov-TerminalResult-Artifact-v1", value
            ),
        }.get(artifact_type, lambda: runner_artifact_reference(artifact_type, value))()
        if stored_reference != reference or (
            artifact_type not in {"process_terminal_record", "terminal_result"}
            and expected_reference != reference
        ):
            raise RunnerReplayError("stored runner artifact identity is corrupted")
        return value

    def execution_attestation(
        self, reference: ArtifactReference
    ) -> ExecutionAttestation:
        return cast(ExecutionAttestation, self.get(reference, ExecutionAttestation))

    def process_terminal_record(
        self, reference: ArtifactReference
    ) -> ProcessExecutionTerminalRecord:
        return cast(
            ProcessExecutionTerminalRecord,
            self.get(reference, ProcessExecutionTerminalRecord),
        )

    def parents(self, reference: ArtifactReference) -> tuple[str, ...]:
        with self._lock:
            return self._parents[reference.artifact_id]


class _RunnerPersistenceRepository(ArtifactRepository, Protocol):
    def replay_runner_execution(
        self, fingerprint: str
    ) -> FixedCommitExecutionResult | None: ...

    def reserve_runner_execution(
        self, job_id: str, process_execution_id: str, fingerprint: str
    ) -> FixedCommitExecutionResult | RunnerExecutionCheckpoint | None: ...

    def checkpoint_runner_execution(
        self,
        job_id: str,
        fingerprint: str,
        checkpoint: RunnerExecutionCheckpoint,
    ) -> RunnerExecutionCheckpoint: ...

    def resolve_runner_checkpoint(
        self,
        job_id: str,
        process_execution_id: str,
        fingerprint: str,
        process_reference: ArtifactReference,
    ) -> RunnerExecutionCheckpoint: ...

    def finalize_runner_execution(
        self,
        job_id: str,
        fingerprint: str,
        attestation: ExecutionAttestation,
    ) -> FixedCommitExecutionResult: ...

    def fail_runner_execution(
        self, job_id: str, fingerprint: str, failure: str
    ) -> None: ...

    def release_runner_execution(self, job_id: str, fingerprint: str) -> None: ...


class ArtifactRepositoryRunnerStore:
    """把 reservation/checkpoint/finalize 委托给既有 repository 原子事务。"""

    def __init__(self, repository: _RunnerPersistenceRepository) -> None:
        self._repository = repository

    def replay(self, fingerprint: str) -> FixedCommitExecutionResult | None:
        return self._repository.replay_runner_execution(fingerprint)

    def reserve(
        self, job_id: str, process_execution_id: str, fingerprint: str
    ) -> FixedCommitExecutionResult | RunnerExecutionCheckpoint | None:
        return self._repository.reserve_runner_execution(
            job_id, process_execution_id, fingerprint
        )

    def checkpoint(
        self,
        job_id: str,
        fingerprint: str,
        checkpoint: RunnerExecutionCheckpoint,
    ) -> RunnerExecutionCheckpoint:
        return self._repository.checkpoint_runner_execution(
            job_id, fingerprint, checkpoint
        )

    def checkpointed(
        self,
        job_id: str,
        process_execution_id: str,
        fingerprint: str,
        process_reference: ArtifactReference,
    ) -> RunnerExecutionCheckpoint:
        return self._repository.resolve_runner_checkpoint(
            job_id,
            process_execution_id,
            fingerprint,
            process_reference,
        )

    def fail(self, job_id: str, fingerprint: str, error: BaseException) -> None:
        self._repository.fail_runner_execution(
            job_id, fingerprint, f"{type(error).__name__}: {error}"
        )

    def release(self, job_id: str, fingerprint: str) -> None:
        self._repository.release_runner_execution(job_id, fingerprint)

    def commit(
        self,
        *,
        fingerprint: str,
        process: ProcessExecutionTerminalRecord,
        process_reference: ArtifactReference,
        attestation: ExecutionAttestation,
        resolved_evidence: Mapping[str, tuple[ArtifactReference, StrictFrozenModel]],
        terminal_result: TerminalResult | None = None,
        terminal_reference: ArtifactReference | None = None,
    ) -> FixedCommitExecutionResult:
        del resolved_evidence
        if (
            attestation.process_terminal_record != process_reference
            or attestation.terminal_result != terminal_reference
            or (terminal_result is None) != (terminal_reference is None)
        ):
            raise RunnerReplayError("repository finalize binding is invalid")
        return self._repository.finalize_runner_execution(
            process.job_id,
            fingerprint,
            attestation,
        )

    def execution_attestation(
        self, reference: ArtifactReference
    ) -> ExecutionAttestation:
        result = self._repository.get(ArtifactId(root=reference.artifact_id))
        if (
            result.envelope.artifact_type != "execution_attestation"
            or result.envelope.payload_hash != reference.payload_hash
        ):
            raise RunnerReplayError("repository attestation identity is invalid")
        return ExecutionAttestation.model_validate(
            result.payload_document.model_dump(mode="json")["payload"], strict=True
        )


class ArtifactRepositoryRunnerCheckpointFinalizer:
    """用既有 runner checkpoint/finalize CAS 签发 terminal attestation。"""

    def __init__(
        self,
        repository: _RunnerPersistenceRepository,
        *,
        signing_key_id: str,
        signing_key: Ed25519PrivateKey,
    ) -> None:
        if not signing_key_id or not isinstance(signing_key, Ed25519PrivateKey):
            raise TypeError("runner finalizer requires an Ed25519 signing identity")
        self._repository = repository
        self._store = ArtifactRepositoryRunnerStore(repository)
        self._signing_key_id = signing_key_id
        self._signing_key = signing_key

    def checkpointed(
        self,
        *,
        job_id: str,
        process_execution_id: str,
        fingerprint: str,
        process_reference: ArtifactReference,
    ) -> RunnerExecutionCheckpoint:
        return self._store.checkpointed(
            job_id,
            process_execution_id,
            fingerprint,
            process_reference,
        )

    def finalize(
        self,
        *,
        fingerprint: str,
        checkpoint: RunnerExecutionCheckpoint,
        terminal_receipt: RunnerTerminalCommitReceipt,
        terminal_result: TerminalResult,
    ) -> FixedCommitExecutionResult:
        process = checkpoint.process
        persisted = self.checkpointed(
            job_id=process.job_id,
            process_execution_id=process.process_execution_id,
            fingerprint=fingerprint,
            process_reference=checkpoint.process_reference,
        )
        if (
            persisted != checkpoint
            or process.status != "success"
            or process.exit_code != 0
            or terminal_receipt.process_terminal_record != checkpoint.process_reference
            or terminal_result.process_execution_terminal_record
            != checkpoint.process_reference
            or terminal_result.fixed_commit_job_manifest != process.job_manifest
            or terminal_result.process_execution_id != process.process_execution_id
            or terminal_result.payload_outputs != process.payload_outputs
        ):
            raise RunnerReplayError("terminal finalize does not bind runner checkpoint")
        stored_terminal = self._repository.get(
            ArtifactId(root=terminal_receipt.terminal_result.artifact_id)
        )
        if (
            stored_terminal.envelope.artifact_type != "terminal_result"
            or stored_terminal.envelope.payload_hash
            != terminal_receipt.terminal_result.payload_hash
            or TerminalResult.model_validate(
                stored_terminal.payload_document.model_dump(mode="json")["payload"],
                strict=True,
            )
            != terminal_result
        ):
            raise RunnerReplayError("terminal result artifact is invalid")
        return self._finalize_checkpoint(
            fingerprint=fingerprint,
            checkpoint=checkpoint,
            terminal_result=terminal_result,
            terminal_reference=terminal_receipt.terminal_result,
            issued_at=terminal_result.created_at,
        )

    def finalize_nonterminal(
        self,
        *,
        fingerprint: str,
        checkpoint: RunnerExecutionCheckpoint,
        issued_at: str,
    ) -> FixedCommitExecutionResult:
        process = checkpoint.process
        if (
            self.checkpointed(
                job_id=process.job_id,
                process_execution_id=process.process_execution_id,
                fingerprint=fingerprint,
                process_reference=checkpoint.process_reference,
            )
            != checkpoint
            or process.status != "success"
            or process.exit_code != 0
        ):
            raise RunnerReplayError("nonterminal finalize does not bind checkpoint")
        return self._finalize_checkpoint(
            fingerprint=fingerprint,
            checkpoint=checkpoint,
            terminal_result=None,
            terminal_reference=None,
            issued_at=issued_at,
        )

    def _finalize_checkpoint(
        self,
        *,
        fingerprint: str,
        checkpoint: RunnerExecutionCheckpoint,
        terminal_result: TerminalResult | None,
        terminal_reference: ArtifactReference | None,
        issued_at: str,
    ) -> FixedCommitExecutionResult:
        process = checkpoint.process
        stored_manifest = self._repository.get(
            ArtifactId(root=process.job_manifest.artifact_id)
        )
        if (
            stored_manifest.envelope.artifact_type != "fixed_commit_job_manifest"
            or stored_manifest.envelope.payload_hash
            != process.job_manifest.payload_hash
        ):
            raise RunnerReplayError("runner job manifest identity is invalid")
        manifest = FixedCommitJobManifest.model_validate(
            stored_manifest.payload_document.model_dump(mode="json")["payload"],
            strict=True,
        )
        nonce = (
            base64.urlsafe_b64encode(
                sha256(
                    canonical_json_bytes(
                        {
                            "domain": "AutoMarkov-E2E-Execution-Attestation-Nonce-v1",
                            "process_terminal_record": checkpoint.process_reference.model_dump(
                                mode="json"
                            ),
                            "terminal_result": (
                                terminal_reference.model_dump(mode="json")
                                if terminal_reference is not None
                                else None
                            ),
                        }
                    )
                ).digest()[:16]
            )
            .decode("ascii")
            .rstrip("=")
        )
        attestation = sign_execution_attestation(
            manifest=manifest,
            job_manifest=process.job_manifest,
            evidence=checkpoint.evidence,
            process_outputs=process.payload_outputs,
            process_reference=checkpoint.process_reference,
            terminal_result=terminal_reference,
            issued_at=issued_at,
            nonce_b64url=nonce,
            signing_key_id=self._signing_key_id,
            signing_key=self._signing_key,
        )
        result = self._store.commit(
            fingerprint=fingerprint,
            process=process,
            process_reference=checkpoint.process_reference,
            attestation=attestation,
            resolved_evidence={},
            terminal_result=terminal_result,
            terminal_reference=terminal_reference,
        )
        if (
            result.process_terminal_record != checkpoint.process_reference
            or result.terminal_result != terminal_reference
            or self._store.execution_attestation(result.execution_attestation)
            != attestation
        ):
            raise RunnerReplayError("runner terminal finalize receipt is invalid")
        return result


class MemoryRunnerTerminalCommitter:
    """测试用 terminal CAS 适配器；生产实现必须委托 ArtifactRepository。"""

    def __init__(
        self,
        *,
        terminal_event: EventReference,
        terminal_head: VerifiedEventHead,
        terminal_state: Literal["COMPLETED", "FAILED"],
        terminal_reason_code: str,
    ) -> None:
        self._event = terminal_event
        self._head = terminal_head
        self._state: Literal["COMPLETED", "FAILED"] = terminal_state
        self._reason = terminal_reason_code
        self.call_count = 0

    def commit_terminal(
        self,
        process: ProcessExecutionTerminalRecord,
    ) -> tuple[RunnerTerminalCommitReceipt, TerminalResult]:
        self.call_count += 1
        process_reference = _content_reference(
            "AutoMarkov-ProcessExecutionTerminalRecord-Artifact-v1", process
        )
        terminal = TerminalResult(
            schema_version="automarkov.terminal-result.v1",
            signing_domain="AutoMarkov-TerminalResult-v1",
            run_id=process.run_id,
            experiment_id=process.experiment_id,
            fixed_commit_job_manifest=process.job_manifest,
            process_execution_terminal_record=process_reference,
            process_execution_id=process.process_execution_id,
            terminal_event=self._event,
            terminal_snapshot_event_head=self._head,
            terminal_state=self._state,
            terminal_reason_code=self._reason,
            payload_outputs=process.payload_outputs,
            terminal_time_approvals=(),
            projector_version="automarkov.run-projector.v1",
            projector_hash=RUN_PROJECTOR_HASH,
            created_at=process.finished_at,
        )
        terminal_reference = _content_reference(
            "AutoMarkov-TerminalResult-Artifact-v1", terminal
        )
        return (
            RunnerTerminalCommitReceipt(
                schema_version="automarkov.runner-terminal-commit-receipt.v1",
                process_terminal_record=process_reference,
                terminal_result=terminal_reference,
            ),
            terminal,
        )


class ArtifactRepositoryTerminalCommitter:
    """将 runner terminal 写入委托给现有 Memory/SQLite 原子 CAS。"""

    def __init__(
        self,
        *,
        repository: ArtifactRepository,
        context: AuthenticatedCommandContext,
        specified_event_head: VerifiedEventHead,
        command_builder: Callable[
            [ProcessExecutionTerminalRecord], Mapping[str, object]
        ],
    ) -> None:
        self._repository = repository
        self._context = context
        self._head = specified_event_head
        self._command_builder = command_builder

    def commit_terminal(
        self,
        process: ProcessExecutionTerminalRecord,
    ) -> tuple[RunnerTerminalCommitReceipt, TerminalResult]:
        command = validate_lifecycle_command(self._command_builder(process))
        if (
            not isinstance(command, CommitTerminalCommand)
            or command.process_terminal_record != process
            or command.run_id != self._head.run_id.root
            or command.expected_head.run_id != self._head.run_id.root
            or command.expected_head.sequence_no != self._head.sequence_no
            or command.expected_head.event_hash != self._head.event_hash.root
        ):
            raise RunnerPreflightError(
                "terminal command does not bind process and specified head"
            )
        receipt = self._repository.commit(
            cast(LifecycleCommandInput, command.model_dump(mode="json")),
            context=self._context,
        )
        if (
            not isinstance(receipt, LifecycleCommitReceipt)
            or receipt.process_execution_terminal_record is None
            or receipt.terminal_result is None
            or receipt.before_head is None
            or receipt.before_head.run_id != self._head.run_id.root
            or receipt.before_head.sequence_no != self._head.sequence_no
            or receipt.before_head.event_hash != self._head.event_hash.root
        ):
            raise RunnerReplayError(
                "repository returned an invalid terminal CAS receipt"
            )
        stored = self._repository.get(
            ArtifactId(root=receipt.terminal_result.artifact_id)
        )
        stored_process = self._repository.get(
            ArtifactId(root=receipt.process_execution_terminal_record.artifact_id)
        )
        process_payload = stored_process.payload_document.model_dump(mode="json")[
            "payload"
        ]
        if type(process_payload) is not dict:
            raise RunnerReplayError("repository process payload is not an object")
        persisted_process = ProcessExecutionTerminalRecord.model_validate_json(
            canonical_json_bytes(process_payload)
        )
        if persisted_process != process:
            raise RunnerReplayError("repository process record changed canonical bytes")
        payload = stored.payload_document.model_dump(mode="json")["payload"]
        if type(payload) is not dict:
            raise RunnerReplayError("repository terminal payload is not an object")
        terminal = TerminalResult.model_validate_json(canonical_json_bytes(payload))
        if (
            terminal.process_execution_terminal_record
            != receipt.process_execution_terminal_record
            or terminal.fixed_commit_job_manifest != process.job_manifest
            or terminal.payload_outputs != process.payload_outputs
        ):
            raise RunnerReplayError("repository terminal result does not bind process")
        return (
            RunnerTerminalCommitReceipt(
                schema_version="automarkov.runner-terminal-commit-receipt.v1",
                process_terminal_record=receipt.process_execution_terminal_record,
                terminal_result=receipt.terminal_result,
            ),
            terminal,
        )


class FixedCommitRunner:
    def __init__(
        self,
        *,
        artifact_store: RunnerArtifactStore,
        resolver: TrustedRunnerArtifactResolver,
        executor: FixedCommitExecutor,
        signing_key_id: str,
        signing_key: Ed25519PrivateKey,
        clock: Callable[[], str],
        terminal_committer: RunnerTerminalCommitter | None = None,
        trusted_runtime_attestation_keys: (
            Mapping[str, RuntimeAttestationKeyPolicy] | None
        ) = None,
    ) -> None:
        if not isinstance(signing_key, Ed25519PrivateKey):
            raise TypeError("runner signing key must be Ed25519")
        if not signing_key_id:
            raise ValueError("runner signing key ID is required")
        public_key_bytes = signing_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        self._artifact_store = artifact_store
        self._resolver = resolver
        self._executor = executor
        self._signing_key_id = signing_key_id
        self._signing_key = signing_key
        self._runner_key_grant = resolver.runner_signing_key_grant()
        self._runner_public_key_bytes = public_key_bytes
        self._clock = clock
        self._terminal_committer = terminal_committer
        inherited_policies = resolver.runtime_attestation_key_policies()
        self._runtime_attestation_keys = dict(
            trusted_runtime_attestation_keys
            if trusted_runtime_attestation_keys is not None
            else inherited_policies
        )

    def run_at_commit(
        self, request: FixedCommitExecutionRequest
    ) -> FixedCommitExecutionResult:
        fingerprint = (
            "sha256:"
            + sha256(canonical_json_bytes(request.model_dump(mode="json"))).hexdigest()
        )
        completed = self._artifact_store.replay(fingerprint)
        if completed is not None:
            return completed
        manifest = cast(
            FixedCommitJobManifest,
            _resolve_typed_artifact(
                self._resolver,
                request.specified_event_head,
                request.job_manifest,
                "fixed_commit_job_manifest",
            ),
        )
        runner_key_grant = self._resolver.runner_signing_key_grant(request.job_manifest)
        if (
            runner_key_grant.signing_key_id != self._signing_key_id
            or runner_key_grant.public_key_bytes() != self._runner_public_key_bytes
        ):
            raise RunnerPreflightError(
                "runner private key does not match frozen job signing grant"
            )
        self._runner_key_grant = runner_key_grant
        replay = self._artifact_store.reserve(
            manifest.job_id, manifest.process_execution_id, fingerprint
        )
        if isinstance(replay, FixedCommitExecutionResult):
            return replay
        checkpointed = isinstance(replay, RunnerExecutionCheckpoint)
        try:
            self._preflight(
                request,
                manifest,
                enforce_launch_deadline=replay is None,
            )
            if isinstance(replay, RunnerExecutionCheckpoint):
                evidence = replay.evidence
                process = replay.process
                process_reference = replay.process_reference
                resolved_evidence = self._resolve_execution_evidence(
                    request, manifest, evidence
                )
            else:
                evidence = self._executor.execute(manifest)
                self._verify_evidence(manifest, evidence)
                resolved_evidence = self._resolve_execution_evidence(
                    request, manifest, evidence
                )
                process = self._process_record(request, manifest, evidence)
                process_reference = _content_reference(
                    "AutoMarkov-ProcessExecutionTerminalRecord-Artifact-v1", process
                )
            process_outputs = process.payload_outputs
            if not checkpointed:
                persisted_checkpoint = self._artifact_store.checkpoint(
                    manifest.job_id,
                    fingerprint,
                    RunnerExecutionCheckpoint(
                        schema_version="automarkov.runner-execution-checkpoint.v1",
                        process=process,
                        process_reference=process_reference,
                        evidence=evidence,
                    ),
                )
                process_reference = persisted_checkpoint.process_reference
                checkpointed = True
            terminal_result: TerminalResult | None = None
            terminal_reference: ArtifactReference | None = None
            if self._terminal_committer is not None:
                receipt, terminal_result = self._terminal_committer.commit_terminal(
                    process
                )
                process_reference = receipt.process_terminal_record
                terminal_reference = receipt.terminal_result
            attestation = self._signed_attestation(
                manifest=manifest,
                job_manifest=request.job_manifest,
                evidence=evidence,
                process_outputs=process_outputs,
                process_reference=process_reference,
                terminal_result=terminal_reference,
            )
            return self._artifact_store.commit(
                fingerprint=fingerprint,
                process=process,
                process_reference=process_reference,
                attestation=attestation,
                resolved_evidence=resolved_evidence,
                terminal_result=terminal_result,
                terminal_reference=terminal_reference,
            )
        except RunnerWaitingRuntimeError as error:
            if checkpointed:
                self._artifact_store.release(manifest.job_id, fingerprint)
                raise
            if getattr(self._executor, "execution_started", False) is True:
                try:
                    return self._record_started_failure(
                        request=request,
                        manifest=manifest,
                        fingerprint=fingerprint,
                        error=error,
                    )
                except Exception as record_error:
                    self._artifact_store.fail(manifest.job_id, fingerprint, error)
                    raise RunnerExecutionFailed(
                        f"{type(error).__name__}: {error}"
                    ) from record_error
            self._artifact_store.release(manifest.job_id, fingerprint)
            raise
        except BaseException as error:
            if checkpointed:
                self._artifact_store.release(manifest.job_id, fingerprint)
            elif (
                isinstance(error, Exception)
                and getattr(self._executor, "execution_started", False) is True
            ):
                try:
                    return self._record_started_failure(
                        request=request,
                        manifest=manifest,
                        fingerprint=fingerprint,
                        error=error,
                    )
                except Exception as record_error:
                    self._artifact_store.fail(manifest.job_id, fingerprint, error)
                    raise RunnerExecutionFailed(
                        f"{type(error).__name__}: {error}"
                    ) from record_error
            else:
                self._artifact_store.fail(manifest.job_id, fingerprint, error)
            raise

    def _record_started_failure(
        self,
        *,
        request: FixedCommitExecutionRequest,
        manifest: FixedCommitJobManifest,
        fingerprint: str,
        error: BaseException,
    ) -> FixedCommitExecutionResult:
        provider = getattr(self._executor, "terminal_failure_evidence", None)
        if not callable(provider):
            raise RunnerExecutionFailed(
                "started executor does not expose terminal failure evidence"
            )
        evidence = cast(RawExecutionEvidence, provider(error))
        if evidence.status != "terminal_failure" or evidence.exit_code == 0:
            raise RunnerExecutionFailed(
                "started executor returned nonterminal failure evidence"
            )
        self._verify_evidence(manifest, evidence)
        resolved_evidence = self._resolve_execution_evidence(
            request, manifest, evidence
        )
        process = self._process_record(request, manifest, evidence)
        process_reference = _content_reference(
            "AutoMarkov-ProcessExecutionTerminalRecord-Artifact-v1", process
        )
        persisted = self._artifact_store.checkpoint(
            manifest.job_id,
            fingerprint,
            RunnerExecutionCheckpoint(
                schema_version="automarkov.runner-execution-checkpoint.v1",
                process=process,
                process_reference=process_reference,
                evidence=evidence,
            ),
        )
        process_reference = persisted.process_reference
        terminal_result: TerminalResult | None = None
        terminal_reference: ArtifactReference | None = None
        if self._terminal_committer is not None:
            receipt, terminal_result = self._terminal_committer.commit_terminal(process)
            process_reference = receipt.process_terminal_record
            terminal_reference = receipt.terminal_result
        attestation = self._signed_attestation(
            manifest=manifest,
            job_manifest=request.job_manifest,
            evidence=evidence,
            process_outputs=process.payload_outputs,
            process_reference=process_reference,
            terminal_result=terminal_reference,
        )
        return self._artifact_store.commit(
            fingerprint=fingerprint,
            process=process,
            process_reference=process_reference,
            attestation=attestation,
            resolved_evidence=resolved_evidence,
            terminal_result=terminal_result,
            terminal_reference=terminal_reference,
        )

    @staticmethod
    def _process_record(
        request: FixedCommitExecutionRequest,
        manifest: FixedCommitJobManifest,
        evidence: RawExecutionEvidence,
    ) -> ProcessExecutionTerminalRecord:
        return ProcessExecutionTerminalRecord(
            schema_version="automarkov.process-execution-terminal-record.v1",
            signing_domain="AutoMarkov-ProcessExecutionTerminalRecord-v1",
            experiment_id=manifest.experiment_id,
            run_id=manifest.run_id,
            job_id=manifest.job_id,
            process_execution_id=manifest.process_execution_id,
            profile_id=manifest.profile_id,
            principal_id=manifest.principal_id,
            job_manifest=request.job_manifest,
            status=evidence.status,
            exit_code=evidence.exit_code,
            reason_code=evidence.reason_code,
            started_at=evidence.started_at,
            finished_at=evidence.finished_at,
            stdout_hash=evidence.stdout_hash,
            stderr_hash=evidence.stderr_hash,
            payload_outputs=evidence.payload_outputs,
            resource_usage=evidence.resource_usage,
            network_log_hash=evidence.network_log.payload_hash,
            mount_attestation_hash=evidence.mount_attestation.payload_hash,
            capability_decision_hash=evidence.capability_decision_log.payload_hash,
            egress_log_hash=evidence.egress_decision_log.payload_hash,
            created_at=evidence.finished_at,
        )

    def _preflight(
        self,
        request: FixedCommitExecutionRequest,
        manifest: FixedCommitJobManifest,
        *,
        enforce_launch_deadline: bool,
    ) -> None:
        head = request.specified_event_head
        if head.run_id.root != manifest.run_id:
            raise RunnerPreflightError("specified head belongs to another run")
        self._resolver.validate_job_authorization(
            head,
            request.job_manifest,
            manifest,
            self._runner_key_grant,
        )
        now = datetime.fromisoformat(self._clock())
        if (
            self._runner_key_grant.principal_id != manifest.principal_id
            or now < datetime.fromisoformat(self._runner_key_grant.not_before)
            or now >= datetime.fromisoformat(self._runner_key_grant.not_after)
            or self._runner_key_grant.revoked_at is not None
            and now >= datetime.fromisoformat(self._runner_key_grant.revoked_at)
        ):
            raise RunnerPreflightError("runner signing grant is not active")
        if enforce_launch_deadline and now > datetime.fromisoformat(
            manifest.launch_deadline
        ):
            raise RunnerPreflightError("job launch deadline has expired")
        profile = cast(
            RuntimeProfileManifest,
            _resolve_typed_artifact(
                self._resolver,
                head,
                manifest.profile_manifest,
                "runtime_profile_manifest",
            ),
        )
        if (
            profile.profile_id != manifest.profile_id
            or profile.lock_hash != manifest.profile_lock_hash
            or profile.target_platform != manifest.target_platform
        ):
            raise RunnerPreflightError("runtime profile binding does not match job")
        if (
            profile.image_status != "built"
            or profile.build_attestation_id is None
            or profile.build_attestation_hash is None
            or profile.import_smoke_attestation_id is None
            or profile.import_smoke_attestation_hash is None
        ):
            raise RunnerWaitingRuntimeError(
                "built runtime profile with build/import attestations"
            )
        if profile.image_digest != manifest.image_digest:
            raise RunnerPreflightError(
                "runtime profile image digest does not match job"
            )
        runtime_nonces: set[tuple[str, str]] = set()
        for reference, expected_kind in (
            (
                ArtifactReference(
                    artifact_id=profile.build_attestation_id,
                    payload_hash=profile.build_attestation_hash,
                ),
                "build",
            ),
            (
                ArtifactReference(
                    artifact_id=profile.import_smoke_attestation_id,
                    payload_hash=profile.import_smoke_attestation_hash,
                ),
                "import_smoke",
            ),
        ):
            attestation = cast(
                RunnerRuntimeAttestation,
                _resolve_typed_artifact(
                    self._resolver,
                    head,
                    reference,
                    "runner_runtime_attestation",
                ),
            )
            if (
                attestation.attestation_kind != expected_kind
                or attestation.profile_id != profile.profile_id
                or attestation.image_digest != profile.image_digest
            ):
                raise RunnerPreflightError("runtime attestation does not match profile")
            nonce_key = (attestation.signing_key_id, attestation.nonce_b64url)
            if nonce_key in runtime_nonces:
                raise RunnerPreflightError("runtime attestation nonce was replayed")
            runtime_nonces.add(nonce_key)
            self._verify_runtime_attestation(head, attestation)
        for reference in manifest.input_artifacts:
            runner_input = cast(
                RunnerInput,
                _resolve_typed_artifact(
                    self._resolver,
                    head,
                    reference,
                    "runner_input",
                ),
            )
            source = self._resolver.resolve(head, runner_input.source_artifact)
            if (
                source.artifact_type != runner_input.source_artifact_type
                or source.reference != runner_input.source_artifact
                or runner_input.source_commitment
                != runner_input.source_artifact.payload_hash
                or not _resolved_identity_matches(source, runner_input.source_artifact)
            ):
                raise RunnerPreflightError(
                    "runner input source payload content identity is invalid"
                )
        resource_limits = cast(
            FixedCommitResourceLimits,
            _resolve_typed_artifact(
                self._resolver,
                head,
                manifest.resource_limits,
                "fixed_commit_resource_limits",
            ),
        )
        network_policy = cast(
            PhaseNetworkPolicy,
            _resolve_typed_artifact(
                self._resolver,
                head,
                manifest.network_policy,
                "phase_network_policy",
            ),
        )
        mount_policy = cast(
            ExecutionMountPolicy,
            _resolve_typed_artifact(
                self._resolver,
                head,
                manifest.mount_policy,
                "execution_mount_policy",
            ),
        )
        for reference, artifact_type in (
            (manifest.capability_policy, "execution_capability_policy"),
            (manifest.output_contract, "execution_output_contract"),
            (manifest.scanner_policy, "output_scanner_policy"),
        ):
            _resolve_typed_artifact(self._resolver, head, reference, artifact_type)
        if (
            resource_limits.phase != manifest.phase
            or network_policy.phase != manifest.phase
            or not set(network_policy.egress_allowlist).issubset(
                profile.egress_allowlist
            )
            or not set(network_policy.protocol_edges).issubset(profile.protocol_edges)
        ):
            raise RunnerPreflightError("phase policy exceeds runtime profile maxima")
        validate_worker_launch_policy(
            self._resolver.worker_kind_for_job(request.job_manifest),
            mount_policy,
            network_policy,
        )
        validate_mount_profile_policy(profile, mount_policy, manifest)
        if manifest.phase == "retrieval" and (
            manifest.principal_id != network_policy.gateway_principal_id
        ):
            raise RunnerPreflightError(
                "retrieval execution principal is not the frozen gateway"
            )
        resolved_job = self._resolver.resolve(head, request.job_manifest)
        expected_parents = tuple(
            sorted(
                {
                    manifest.profile_manifest.artifact_id,
                    *(item.artifact_id for item in manifest.input_artifacts),
                    manifest.resource_limits.artifact_id,
                    manifest.network_policy.artifact_id,
                    manifest.mount_policy.artifact_id,
                    manifest.capability_policy.artifact_id,
                    manifest.output_contract.artifact_id,
                    manifest.scanner_policy.artifact_id,
                },
                key=lambda item: item.encode("utf-8"),
            )
        )
        if resolved_job.parent_artifact_ids != expected_parents:
            raise RunnerPreflightError("job manifest exact parent DAG does not match")

    def _verify_runtime_attestation(
        self,
        head: VerifiedEventHead,
        attestation: RunnerRuntimeAttestation,
    ) -> None:
        policy = self._runtime_attestation_keys.get(attestation.signing_key_id)
        if policy is None:
            raise RunnerWaitingRuntimeError("trusted runtime attestation issuer key")
        observed_at = datetime.fromisoformat(attestation.observed_at)
        if (
            policy.issuer_id != attestation.issuer_id
            or policy.signing_key_id != attestation.signing_key_id
            or attestation.profile_id not in policy.allowed_profile_ids
            or attestation.attestation_kind not in policy.allowed_kinds
            or observed_at < datetime.fromisoformat(policy.not_before)
            or observed_at >= datetime.fromisoformat(policy.not_after)
            or observed_at > datetime.fromisoformat(self._clock())
        ):
            raise RunnerPreflightError(
                "runtime attestation key policy does not allow claim"
            )
        for reference in attestation.evidence_refs:
            evidence = cast(
                RunnerRuntimeEvidence,
                _resolve_typed_artifact(
                    self._resolver,
                    head,
                    reference,
                    "runner_runtime_evidence",
                ),
            )
            if (
                evidence.evidence_kind != attestation.attestation_kind
                or evidence.image_digest != attestation.image_digest
            ):
                raise RunnerPreflightError(
                    "runtime evidence does not match signed attestation"
                )
        try:
            policy.public_key.verify(
                base64.urlsafe_b64decode(attestation.signature_b64url + "=="),
                attestation.signing_bytes(),
            )
        except InvalidSignature as error:
            raise RunnerPreflightError(
                "runtime attestation signature is invalid"
            ) from error

    def _resolve_execution_evidence(
        self,
        request: FixedCommitExecutionRequest,
        manifest: FixedCommitJobManifest,
        evidence: RawExecutionEvidence,
    ) -> dict[str, tuple[ArtifactReference, StrictFrozenModel]]:
        head = request.specified_event_head
        output_models = tuple(
            cast(
                RunnerOutputBinding,
                _resolve_typed_artifact(
                    self._resolver, head, reference, "runner_output_binding"
                ),
            )
            for reference in evidence.payload_outputs
        )
        actual_output_bytes = tuple(
            item.verified_content_bytes() for item in output_models
        )
        resolved: dict[str, tuple[ArtifactReference, StrictFrozenModel]] = {}
        role_specs = (
            (
                "execution_resource_usage",
                evidence.resource_usage,
                ExecutionResourceUsage,
            ),
            ("network_decision_log", evidence.network_log, NetworkDecisionLog),
            ("mount_attestation", evidence.mount_attestation, MountAttestation),
            (
                "capability_decision_log",
                evidence.capability_decision_log,
                CapabilityDecisionLog,
            ),
            ("egress_decision_log", evidence.egress_decision_log, EgressDecisionLog),
            ("output_scan_report", evidence.output_scan_report, OutputScanReport),
        )
        for artifact_type, reference, model_type in role_specs:
            value = _resolve_typed_artifact(
                self._resolver, head, reference, artifact_type
            )
            if type(value) is not model_type:
                raise RunnerPreflightError("execution evidence role has wrong schema")
            resolved[artifact_type] = (reference, value)
        resource_usage = cast(
            ExecutionResourceUsage, resolved["execution_resource_usage"][1]
        )
        network_log = cast(NetworkDecisionLog, resolved["network_decision_log"][1])
        mount = cast(MountAttestation, resolved["mount_attestation"][1])
        capability = cast(CapabilityDecisionLog, resolved["capability_decision_log"][1])
        egress = cast(EgressDecisionLog, resolved["egress_decision_log"][1])
        scan = cast(OutputScanReport, resolved["output_scan_report"][1])
        scan_resolved = self._resolver.resolve(head, evidence.output_scan_report)
        scanner_policy = cast(
            OutputScannerPolicy,
            _resolve_typed_artifact(
                self._resolver,
                head,
                manifest.scanner_policy,
                "output_scanner_policy",
            ),
        )
        output_contract = cast(
            ExecutionOutputContract,
            _resolve_typed_artifact(
                self._resolver,
                head,
                manifest.output_contract,
                "execution_output_contract",
            ),
        )
        self._validate_actual_output_bytes(
            output_models,
            actual_output_bytes,
            output_contract,
            require_complete=evidence.status == "success",
        )
        for output, content in zip(output_models, actual_output_bytes, strict=True):
            if (
                output.content_schema_version
                != "automarkov.runner-artifact-reference-output.v1"
            ):
                continue
            expected_artifact_type = _TYPED_ARTIFACT_REFERENCE_OUTPUT_TYPES.get(
                output.path
            )
            try:
                reference_payload = RunnerArtifactReferencePayload.model_validate_json(
                    content, strict=True
                )
            except ValueError as error:
                raise RunnerPreflightError(
                    "typed output does not contain an artifact reference"
                ) from error
            if (
                expected_artifact_type is not None
                and reference_payload.artifact_type != expected_artifact_type
            ):
                raise RunnerPreflightError(
                    "referenced output artifact type does not match frozen path"
                )
            referenced_artifact = _resolve_payload_artifact(
                self._resolver,
                head,
                reference_payload.artifact,
                reference_payload.artifact_type,
            )
            if reference_payload.artifact_type in SEALED_SUBJECT_ARTIFACT_CONTRACTS:
                _parse_sealed_subject_artifact(
                    referenced_artifact, request.job_manifest
                )
            self._scan_actual_payload_bytes(referenced_artifact.payload_bytes)
        resource_limits = cast(
            FixedCommitResourceLimits,
            _resolve_typed_artifact(
                self._resolver,
                head,
                manifest.resource_limits,
                "fixed_commit_resource_limits",
            ),
        )
        network_policy = cast(
            PhaseNetworkPolicy,
            _resolve_typed_artifact(
                self._resolver,
                head,
                manifest.network_policy,
                "phase_network_policy",
            ),
        )
        mount_policy = cast(
            ExecutionMountPolicy,
            _resolve_typed_artifact(
                self._resolver,
                head,
                manifest.mount_policy,
                "execution_mount_policy",
            ),
        )
        capability_policy = cast(
            ExecutionCapabilityPolicy,
            _resolve_typed_artifact(
                self._resolver,
                head,
                manifest.capability_policy,
                "execution_capability_policy",
            ),
        )
        validate_network_decisions(network_policy, network_log.decisions)
        if (
            resource_usage.job_manifest != request.job_manifest
            or resource_usage.limits_policy != manifest.resource_limits
            or network_log.job_manifest != request.job_manifest
            or network_log.network_policy != manifest.network_policy
            or mount.job_manifest != request.job_manifest
            or mount.mount_policy != manifest.mount_policy
            or capability.job_manifest != request.job_manifest
            or capability.capability_policy != manifest.capability_policy
            or capability.denied_capabilities
            != tuple(
                sorted(
                    {
                        "capability:" + item
                        for item in capability_policy.allowed_capabilities
                    }
                    | {"capability:all"},
                    key=lambda item: item.encode("utf-8"),
                )
            )
            or capability.dropped_capabilities != ("ALL",)
            or capability.seccomp_profile_hash != capability_policy.seccomp_profile_hash
            or capability.apparmor_profile_hash
            != capability_policy.apparmor_profile_hash
            or egress.job_manifest != request.job_manifest
            or egress.network_policy != manifest.network_policy
            or egress.revoked_at != evidence.egress_revoked_at
            or scan.job_manifest != request.job_manifest
            or scan.scanner_policy != manifest.scanner_policy
            or scan.output_contract != manifest.output_contract
            or scan.scanner_rules_hash != scanner_policy.scanner_rules_hash
            or scan.scanned_outputs != evidence.payload_outputs
            or scan_resolved.parent_artifact_ids
            != tuple(
                sorted(
                    {
                        request.job_manifest.artifact_id,
                        manifest.scanner_policy.artifact_id,
                        manifest.output_contract.artifact_id,
                        *(item.artifact_id for item in evidence.payload_outputs),
                    }
                )
            )
            or scan.scanned_paths
            != tuple(
                sorted(
                    (item.path for item in output_models),
                    key=lambda item: item.encode("utf-8"),
                )
            )
            or scan.total_bytes != sum(len(item) for item in actual_output_bytes)
            or scan.total_bytes > output_contract.maximum_total_bytes
            or not set(scan.scanned_paths).issubset(output_contract.allowed_paths)
            or evidence.status == "success"
            and (
                resource_usage.peak_memory_bytes > resource_limits.memory_bytes
                or resource_usage.peak_pids > resource_limits.pids
                or resource_usage.io_read_bytes + resource_usage.io_write_bytes
                > resource_limits.io_bytes
                or resource_usage.peak_disk_bytes > resource_limits.disk_bytes
                or resource_usage.wall_time_ms > resource_limits.wall_time_ms
            )
            or not set(resource_usage.gpu_devices).issubset(resource_limits.gpu_devices)
            or mount.actual_mounts != mount_policy.mounts
            or network_log.decisions != egress.decisions
            or datetime.fromisoformat(evidence.egress_revoked_at)
            > datetime.fromisoformat(scan.scanned_at)
            or datetime.fromisoformat(scan.scanned_at)
            > datetime.fromisoformat(evidence.finished_at)
        ):
            raise RunnerPreflightError("offline execution evidence binding is invalid")
        for index, (reference, model) in enumerate(
            zip(evidence.payload_outputs, output_models, strict=True)
        ):
            resolved[f"runner_output_binding_{index}"] = (reference, model)
        return resolved

    @staticmethod
    def _validate_actual_output_bytes(
        outputs: tuple[RunnerOutputBinding, ...],
        actual_output_bytes: tuple[bytes, ...],
        contract: ExecutionOutputContract,
        *,
        require_complete: bool,
    ) -> None:
        bindings = {binding.path: binding for binding in contract.output_schemas}
        output_paths = tuple(output.path for output in outputs)
        if (
            require_complete
            and tuple(sorted(output_paths, key=lambda item: item.encode("utf-8")))
            != contract.allowed_paths
        ) or (not require_complete and output_paths):
            raise RunnerPreflightError(
                "actual output paths do not match the frozen schema set"
            )
        for output, content in zip(outputs, actual_output_bytes, strict=True):
            binding = bindings[output.path]
            registered = _CLOSED_OUTPUT_SCHEMA_REGISTRY.get(
                (output.path, binding.schema_version)
            )
            if (
                registered is None
                or output.content_schema_version != binding.schema_version
                or binding.schema_identity_hash != registered[1]
                or _output_schema_identity(registered[0]) != registered[1]
            ):
                raise RunnerPreflightError(
                    "actual output schema is not in the trusted closed registry"
                )
            try:
                payload_model = registered[0].model_validate_json(content, strict=True)
            except ValueError as error:
                raise RunnerPreflightError(
                    "actual output does not satisfy canonical JSON schema"
                ) from error
            payload = payload_model.model_dump(
                mode="json",
                round_trip=True,
                warnings="error",
                exclude_unset=True,
            )
            if (
                canonical_json_bytes(payload) != content
                or payload.get("schema_version") != binding.schema_version
            ):
                raise RunnerPreflightError(
                    "actual output does not satisfy canonical JSON schema"
                )
            FixedCommitRunner._scan_payload_tree(payload)

    @staticmethod
    def _scan_actual_payload_bytes(content: bytes) -> None:
        try:
            payload = parse_json_payload(content)
        except ValueError as error:
            raise RunnerPreflightError(
                "referenced output payload is not valid JSON"
            ) from error
        FixedCommitRunner._scan_payload_tree(payload)

    @staticmethod
    def _scan_payload_tree(payload: object) -> None:
        secret_keys = frozenset(_RUNNER_OUTPUT_SCANNER_RULES["secret_keys"])
        credential_keys = frozenset(
            _RUNNER_OUTPUT_SCANNER_RULES["credential_locator_keys"]
        )
        gold_keys = frozenset(_RUNNER_OUTPUT_SCANNER_RULES["gold_marker_keys"])
        value_markers = tuple(
            marker
            for rule in (
                "secret_value_markers",
                "credential_locator_markers",
                "gold_value_markers",
            )
            for marker in _RUNNER_OUTPUT_SCANNER_RULES[rule]
        )
        value_patterns = tuple(
            re.compile(pattern)
            for pattern in _RUNNER_OUTPUT_SCANNER_RULES["secret_value_patterns"]
        )
        pending: list[object] = [payload]
        while pending:
            value = pending.pop()
            if type(value) is dict:
                for key, item in cast(dict[str, object], value).items():
                    normalized_key = _normalize_scanner_key(key)
                    if normalized_key in secret_keys:
                        raise RunnerPreflightError(
                            "actual output contains a secret field"
                        )
                    if normalized_key in credential_keys:
                        raise RunnerPreflightError(
                            "actual output contains a credential locator"
                        )
                    if normalized_key in gold_keys:
                        raise RunnerPreflightError(
                            "actual output contains a gold marker"
                        )
                    pending.append(item)
            elif type(value) is list:
                pending.extend(cast(list[object], value))
            elif type(value) is str:
                if any(marker in value.casefold() for marker in value_markers):
                    raise RunnerPreflightError(
                        "actual output contains a forbidden scanner marker"
                    )
                if any(pattern.search(value) is not None for pattern in value_patterns):
                    raise RunnerPreflightError(
                        "actual output contains a high-confidence credential value"
                    )

    @staticmethod
    def _verify_evidence(
        manifest: FixedCommitJobManifest,
        evidence: RawExecutionEvidence,
    ) -> None:
        if (
            evidence.job_id != manifest.job_id
            or evidence.process_execution_id != manifest.process_execution_id
            or evidence.source_commit != manifest.source_commit
            or evidence.profile_id != manifest.profile_id
            or evidence.image_digest != manifest.image_digest
        ):
            raise RunnerPreflightError("executor evidence does not match frozen job")

    def _signed_attestation(
        self,
        *,
        manifest: FixedCommitJobManifest,
        job_manifest: ArtifactReference,
        evidence: RawExecutionEvidence,
        process_outputs: tuple[ArtifactReference, ...],
        process_reference: ArtifactReference,
        terminal_result: ArtifactReference | None,
    ) -> ExecutionAttestation:
        return sign_execution_attestation(
            manifest=manifest,
            job_manifest=job_manifest,
            evidence=evidence,
            process_outputs=process_outputs,
            process_reference=process_reference,
            terminal_result=terminal_result,
            issued_at=evidence.finished_at,
            nonce_b64url=base64.urlsafe_b64encode(secrets.token_bytes(16))
            .decode("ascii")
            .rstrip("="),
            signing_key_id=self._signing_key_id,
            signing_key=self._signing_key,
        )
