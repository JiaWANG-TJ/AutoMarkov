from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from datetime import datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    AfterValidator,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from automarkov.domain.canonical import (
    CanonicalJsonValue,
    FrozenSequence,
    FrozenStringMapping,
    NonNegativeSafeCanonicalInt,
    PositiveSafeCanonicalInt,
    StrictTrue,
)
from automarkov.domain.models import RunId, Sha256Digest, StrictFrozenModel
from automarkov.lifecycle import ArtifactReference, CanonicalTimestamp
from automarkov.provenance import RuntimeProfileId

ProtocolMethod: TypeAlias = Literal[
    "Describe",
    "Reset",
    "Step",
    "Observe",
    "State",
    "Snapshot",
    "Close",
]
RemoteEnvRole: TypeAlias = Literal["actor", "critic", "evaluator"]
TensorDtype: TypeAlias = Literal[
    "bool",
    "uint8",
    "int8",
    "uint16",
    "int16",
    "uint32",
    "int32",
    "uint64",
    "int64",
    "float16",
    "float32",
    "float64",
]
IntegerTensorDtype: TypeAlias = Literal[
    "uint8",
    "int8",
    "uint16",
    "int16",
    "uint32",
    "int32",
    "uint64",
    "int64",
]

ExperimentId = Annotated[
    str, Field(strict=True, pattern=r"^experiment_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
]
ProcessExecutionId = Annotated[
    str, Field(strict=True, pattern=r"^execution_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
]
PrincipalId = Annotated[
    str, Field(strict=True, pattern=r"^principal_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
]
EnvironmentId = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
GrantId = Annotated[
    str, Field(strict=True, pattern=r"^grant_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
]
SigningKeyId = Annotated[
    str, Field(strict=True, pattern=r"^key_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
]
TensorId = Annotated[
    str, Field(strict=True, pattern=r"^tensor_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
]
CertificateFingerprint = Sha256Digest
SessionId = Sha256Digest


def _require_nonce(value: str) -> str:
    try:
        decoded = urlsafe_b64decode(value + "=")
    except (ValueError, Base64Error) as error:
        raise ValueError("nonce must be canonical unpadded base64url") from error
    if (
        len(decoded) != 32
        or urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value
    ):
        raise ValueError("nonce must encode exactly 32 bytes")
    return value


NonceB64Url = Annotated[
    str,
    Field(strict=True, pattern=r"^[A-Za-z0-9_-]{43}$"),
    AfterValidator(_require_nonce),
]


def _require_signature(value: str) -> str:
    try:
        decoded = urlsafe_b64decode(value + "==")
    except (ValueError, Base64Error) as error:
        raise ValueError("signature must be canonical unpadded base64url") from error
    if (
        len(decoded) != 64
        or urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value
    ):
        raise ValueError("signature must encode exactly 64 bytes")
    return value


SignatureB64Url = Annotated[
    str,
    Field(strict=True, pattern=r"^[A-Za-z0-9_-]{86}$"),
    AfterValidator(_require_signature),
]


def _require_sorted_unique_strings(value: tuple[str, ...]) -> tuple[str, ...]:
    if value != tuple(sorted(set(value), key=lambda item: item.encode("utf-8"))):
        raise ValueError("values must be UTF-8 byte sorted and unique")
    return value


class TensorDescriptor(StrictFrozenModel):
    tensor_id: TensorId
    dtype: TensorDtype
    shape: FrozenSequence[NonNegativeSafeCanonicalInt]
    offset: NonNegativeSafeCanonicalInt
    nbytes: NonNegativeSafeCanonicalInt
    sha256: Sha256Digest


class DiscreteSpace(StrictFrozenModel):
    kind: Literal["Discrete"]
    n: PositiveSafeCanonicalInt
    start: int
    dtype: IntegerTensorDtype


class BoxSpace(StrictFrozenModel):
    kind: Literal["Box"]
    shape: FrozenSequence[NonNegativeSafeCanonicalInt]
    dtype: TensorDtype
    low_tensor_id: TensorId
    high_tensor_id: TensorId


class MultiDiscreteSpace(StrictFrozenModel):
    kind: Literal["MultiDiscrete"]
    nvec_tensor_id: TensorId
    start_tensor_id: TensorId
    dtype: IntegerTensorDtype


class MultiBinarySpace(StrictFrozenModel):
    kind: Literal["MultiBinary"]
    n: PositiveSafeCanonicalInt
    dtype: Literal["int8"]


class TextSpace(StrictFrozenModel):
    kind: Literal["Text"]
    min_length: NonNegativeSafeCanonicalInt
    max_length: PositiveSafeCanonicalInt
    charset: str

    @model_validator(mode="after")
    def validate_lengths(self) -> TextSpace:
        if self.min_length > self.max_length:
            raise ValueError("Text min_length must not exceed max_length")
        return self


class FiniteTextSpace(StrictFrozenModel):
    kind: Literal["FiniteText"]
    values: FrozenSequence[str]

    @field_validator("values")
    @classmethod
    def validate_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("FiniteText values must not be empty")
        return _require_sorted_unique_strings(value)


class DictEntry(StrictFrozenModel):
    key: str
    space: Space


class TupleSpace(StrictFrozenModel):
    kind: Literal["Tuple"]
    items: FrozenSequence[Space]


class DictSpace(StrictFrozenModel):
    kind: Literal["Dict"]
    entries: FrozenSequence[DictEntry]

    @field_validator("entries")
    @classmethod
    def validate_entries(cls, value: tuple[DictEntry, ...]) -> tuple[DictEntry, ...]:
        keys = tuple(entry.key for entry in value)
        _require_sorted_unique_strings(keys)
        return value


Space: TypeAlias = Annotated[
    DiscreteSpace
    | BoxSpace
    | MultiDiscreteSpace
    | MultiBinarySpace
    | TextSpace
    | FiniteTextSpace
    | TupleSpace
    | DictSpace,
    Field(discriminator="kind"),
]


class EmptyMethodPayload(StrictFrozenModel):
    pass


class IntegerResetSeedContract(StrictFrozenModel):
    kind: Literal["integer_reset"]
    seed: NonNegativeSafeCanonicalInt


class ScenarioEpisodeSeedContract(StrictFrozenModel):
    kind: Literal["scenario_episode"]
    scenario_id: Annotated[
        str,
        Field(
            strict=True,
            pattern=r"^scenario_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
        ),
    ]
    seed: NonNegativeSafeCanonicalInt


SeedContract: TypeAlias = Annotated[
    IntegerResetSeedContract | ScenarioEpisodeSeedContract,
    Field(discriminator="kind"),
]


class DescribeResultPayload(StrictFrozenModel):
    environment_repository_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    environment_spec: str
    observation_space: Space
    action_space: Space
    seed_contract: SeedContract
    supports_aec: bool
    supports_parallel: bool
    supports_state: bool


class ResetRequestPayload(StrictFrozenModel):
    seed: NonNegativeSafeCanonicalInt
    options: FrozenStringMapping[CanonicalJsonValue]


class ObservationPayload(StrictFrozenModel):
    observation_tensor_id: TensorId
    info: FrozenStringMapping[CanonicalJsonValue]


class StepRequestPayload(StrictFrozenModel):
    action: CanonicalJsonValue


class StepResultPayload(StrictFrozenModel):
    observation_tensor_id: TensorId
    reward: CanonicalJsonValue
    termination: bool
    truncation: bool
    info: FrozenStringMapping[CanonicalJsonValue]
    active_aec_agent: str | None
    cycle_index: NonNegativeSafeCanonicalInt
    transition_hash: Sha256Digest


class MultiAgentStepResultPayload(StrictFrozenModel):
    observations: FrozenStringMapping[TensorId]
    rewards: FrozenStringMapping[CanonicalJsonValue]
    terminations: FrozenStringMapping[bool]
    truncations: FrozenStringMapping[bool]
    infos: FrozenStringMapping[FrozenStringMapping[CanonicalJsonValue]]
    active_aec_agent: str | None
    cycle_index: NonNegativeSafeCanonicalInt
    transition_hash: Sha256Digest

    @model_validator(mode="after")
    def validate_agent_sets(self) -> MultiAgentStepResultPayload:
        agents = set(self.observations)
        if (
            not agents
            or set(self.rewards) != agents
            or set(self.terminations) != agents
            or set(self.truncations) != agents
            or set(self.infos) != agents
            or (
                self.active_aec_agent is not None
                and self.active_aec_agent not in agents
            )
        ):
            raise ValueError(
                "multi-agent Step fields must bind one exact non-empty agent set"
            )
        return self


class CloseResultPayload(StrictFrozenModel):
    closed: StrictTrue


RemoteEnvPayload: TypeAlias = (
    EmptyMethodPayload
    | DescribeResultPayload
    | ResetRequestPayload
    | ObservationPayload
    | StepRequestPayload
    | StepResultPayload
    | MultiAgentStepResultPayload
    | CloseResultPayload
)


class RemoteEnvCertificateIdentity(StrictFrozenModel):
    domain: Literal["AutoMarkov-RemoteEnv-Certificate-Identity-v1"]
    experiment_id: ExperimentId
    run_id: RunId
    process_execution_id: ProcessExecutionId
    profile_id: RuntimeProfileId
    principal_id: PrincipalId


class RemoteEnvSessionPeer(StrictFrozenModel):
    process_execution_id: ProcessExecutionId
    profile_id: RuntimeProfileId
    principal_id: PrincipalId
    certificate_fingerprint: CertificateFingerprint
    nonce_b64url: NonceB64Url


class RemoteEnvSessionTranscript(StrictFrozenModel):
    domain: Literal["AutoMarkov-RemoteEnv-Session-v1"]
    protocol_version: Literal["automarkov.remote-env.v1"]
    experiment_id: ExperimentId
    run_id: RunId
    profile_graph_hash: Sha256Digest
    client: RemoteEnvSessionPeer
    server: RemoteEnvSessionPeer


class RemoteEnvClientHello(StrictFrozenModel):
    domain: Literal["AutoMarkov-RemoteEnv-Client-Hello-v1"]
    protocol_version: Literal["automarkov.remote-env.v1"]
    experiment_id: ExperimentId
    run_id: RunId
    profile_graph_hash: Sha256Digest
    process_execution_id: ProcessExecutionId
    profile_id: RuntimeProfileId
    principal_id: PrincipalId
    certificate_fingerprint: CertificateFingerprint
    nonce_b64url: NonceB64Url


class RemoteEnvServerHello(StrictFrozenModel):
    domain: Literal["AutoMarkov-RemoteEnv-Server-Hello-v1"]
    protocol_version: Literal["automarkov.remote-env.v1"]
    server_nonce_b64url: NonceB64Url
    handshake: RemoteEnvHandshake
    grant: RemoteEnvCapabilityGrant


class RemoteEnvCapabilityGrant(StrictFrozenModel):
    schema_version: Literal["automarkov.remote-env-capability.v1"]
    signing_domain: Literal["AutoMarkov-RemoteEnv-Capability-v1"]
    grant_id: GrantId
    experiment_id: ExperimentId
    run_id: RunId
    session_id: SessionId
    profile_graph_hash: Sha256Digest
    source_process_execution_id: ProcessExecutionId
    source_profile_id: RuntimeProfileId
    source_principal_id: PrincipalId
    target_process_execution_id: ProcessExecutionId
    target_profile_id: RuntimeProfileId
    environment_id: EnvironmentId
    role: RemoteEnvRole
    allowed_methods: FrozenSequence[ProtocolMethod]
    max_sequence: PositiveSafeCanonicalInt
    max_step: PositiveSafeCanonicalInt
    max_frame_bytes: PositiveSafeCanonicalInt
    max_tensor_bytes: PositiveSafeCanonicalInt
    not_before: CanonicalTimestamp
    expires_at: CanonicalTimestamp
    nonce_b64url: NonceB64Url
    signing_key_id: SigningKeyId
    signature_b64url: SignatureB64Url

    @model_validator(mode="after")
    def validate_closed_authority(self) -> RemoteEnvCapabilityGrant:
        expected = tuple(
            sorted(set(self.allowed_methods), key=lambda item: item.encode("ascii"))
        )
        if self.allowed_methods != expected or not expected:
            raise ValueError(
                "allowed_methods must be ASCII sorted, unique, and non-empty"
            )
        if self.role == "actor" and ({"State", "Snapshot"} & set(expected)):
            raise ValueError("actor grants cannot authorize State or Snapshot")
        if self.max_tensor_bytes > self.max_frame_bytes:
            raise ValueError("tensor byte ceiling cannot exceed frame byte ceiling")
        try:
            not_before = datetime.fromisoformat(self.not_before)
            expires_at = datetime.fromisoformat(self.expires_at)
        except ValueError as error:
            raise ValueError("grant timestamps must be RFC 3339 timestamps") from error
        if not self.not_before.endswith("Z") or not self.expires_at.endswith("Z"):
            raise ValueError("grant timestamps must use UTC Z")
        if not_before >= expires_at:
            raise ValueError("grant validity window must be positive")
        return self


class RemoteEnvRunnerGrantPolicy(StrictFrozenModel):
    schema_version: Literal["automarkov.remote-env-runner-grant-policy.v1"]
    signing_key_id: SigningKeyId
    key_not_before: CanonicalTimestamp
    key_expires_at: CanonicalTimestamp
    key_revoked: bool
    run_not_before: CanonicalTimestamp
    run_expires_at: CanonicalTimestamp
    clock_skew_seconds: NonNegativeSafeCanonicalInt
    grant_schema_hash: Sha256Digest

    @model_validator(mode="after")
    def validate_windows(self) -> RemoteEnvRunnerGrantPolicy:
        key_start = datetime.fromisoformat(self.key_not_before)
        key_end = datetime.fromisoformat(self.key_expires_at)
        run_start = datetime.fromisoformat(self.run_not_before)
        run_end = datetime.fromisoformat(self.run_expires_at)
        if key_start >= key_end or run_start >= run_end:
            raise ValueError("runner key and run windows must be positive")
        return self


class RemoteEnvEnvelope(StrictFrozenModel):
    protocol_version: Literal["automarkov.remote-env.v1"]
    run_id: RunId
    session_id: SessionId
    source_process_execution_id: ProcessExecutionId
    source_profile_id: RuntimeProfileId
    source_principal_id: PrincipalId
    target_process_execution_id: ProcessExecutionId
    target_profile_id: RuntimeProfileId
    sequence: PositiveSafeCanonicalInt
    step_id: NonNegativeSafeCanonicalInt
    grant: RemoteEnvCapabilityGrant


class RemoteEnvFrameHeader(StrictFrozenModel):
    codec_version: Literal["automarkov.remote-env-frame.v1"]
    message_kind: ProtocolMethod
    envelope: RemoteEnvEnvelope
    payload: RemoteEnvPayload
    tensors: FrozenSequence[TensorDescriptor]

    @model_validator(mode="after")
    def validate_tensor_order(self) -> RemoteEnvFrameHeader:
        ids = tuple(item.tensor_id for item in self.tensors)
        if ids != tuple(sorted(set(ids), key=lambda item: item.encode("utf-8"))):
            raise ValueError("tensor descriptors must be UTF-8 sorted and unique")
        offset = 0
        for descriptor in self.tensors:
            if descriptor.offset != offset:
                raise ValueError(
                    "tensor descriptors must be contiguous from offset zero"
                )
            offset += descriptor.nbytes
        if self.message_kind not in self.envelope.grant.allowed_methods:
            raise ValueError("message method is not granted")
        payload_types: dict[str, tuple[type[StrictFrozenModel], ...]] = {
            "Describe": (EmptyMethodPayload, DescribeResultPayload),
            "Reset": (ResetRequestPayload, ObservationPayload),
            "Step": (
                StepRequestPayload,
                StepResultPayload,
                MultiAgentStepResultPayload,
            ),
            "Close": (EmptyMethodPayload, CloseResultPayload),
            "Observe": (ObservationPayload,),
            "State": (ObservationPayload,),
            "Snapshot": (ObservationPayload,),
        }
        if type(self.payload) not in payload_types[self.message_kind]:
            raise ValueError("payload schema does not match the protocol method")
        return self


class RemoteEnvHandshake(StrictFrozenModel):
    protocol_version: Literal["automarkov.remote-env.v1"]
    run_id: RunId
    session_id: SessionId
    process_execution_id: ProcessExecutionId
    profile_id: RuntimeProfileId
    principal_id: PrincipalId
    profile_lock_hash: Sha256Digest
    image_digest: Sha256Digest
    peer_certificate_fingerprints: FrozenStringMapping[CertificateFingerprint]
    profile_graph_hash: Sha256Digest
    environment_id: EnvironmentId
    environment_repository_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    observation_spaces: FrozenStringMapping[Space]
    action_spaces: FrozenStringMapping[Space]
    supports_parallel: bool
    supports_aec: bool
    supports_state: bool
    seed_contract: SeedContract

    @field_validator("peer_certificate_fingerprints")
    @classmethod
    def validate_peer_roles(
        cls, value: dict[str, CertificateFingerprint]
    ) -> dict[str, CertificateFingerprint]:
        if set(value) != {"client", "server"}:
            raise ValueError(
                "peer certificate fingerprints must bind client and server"
            )
        return value


class RemoteEnvTlsEndpoint(StrictFrozenModel):
    schema_version: Literal["automarkov.remote-env-tls-endpoint.v1"]
    host: Annotated[
        str,
        Field(
            strict=True,
            min_length=1,
            max_length=253,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$",
        ),
    ]
    port: Annotated[int, Field(strict=True, ge=1, le=65535)]
    server_name: (
        Annotated[
            str,
            Field(
                strict=True,
                min_length=1,
                max_length=253,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$",
            ),
        ]
        | None
    )


class EnvironmentHandle(StrictFrozenModel):
    schema_version: Literal["automarkov.environment-handle.v2"]
    implementation_plan: ArtifactReference
    environment_binding: ArtifactReference
    run_id: RunId
    session_id: SessionId
    profile_graph_hash: Sha256Digest
    source_identity: RemoteEnvCertificateIdentity
    target_identity: RemoteEnvCertificateIdentity


SPACE_ADAPTER = TypeAdapter(Space)

DictEntry.model_rebuild()
TupleSpace.model_rebuild()
DictSpace.model_rebuild()
RemoteEnvServerHello.model_rebuild()
