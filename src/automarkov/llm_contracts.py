from __future__ import annotations

import ipaddress
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Annotated, Literal, Self, TypeAlias
from urllib.parse import urlsplit

from pydantic import AfterValidator, Field, model_validator

from automarkov.canonical import (
    CanonicalJsonValue,
    CanonicalPayloadCodec,
    FrozenSequence,
    FrozenStringMapping,
    canonical_json_bytes,
)
from automarkov.domain import (
    ArtifactId,
    CanonicalNonce,
    GenerationEvidenceView,
    NonNegativeSafeInt,
    PositiveSafeInt,
    Sha256Digest,
    StrictFrozenModel,
    validate_strict_frozen_payload,
)


def _require_unicode(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("text must not contain a lone surrogate") from error
    return value


def _require_utc_timestamp(value: str) -> str:
    if not value.endswith("Z") or "+" in value or value.count("Z") != 1:
        raise ValueError("observed_at must be a canonical UTC-Z timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("observed_at must be a valid UTC timestamp") from error
    canonical = parsed.isoformat(timespec="microseconds").replace(".000000+00:00", "Z")
    if canonical.endswith("+00:00"):
        canonical = canonical.removesuffix("+00:00").rstrip("0").rstrip(".") + "Z"
    if canonical != value:
        raise ValueError("observed_at must use canonical UTC-Z representation")
    return value


def _require_loopback_v1_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("vLLM base URL is invalid") from error
    if (
        parsed.scheme != "http"
        or host is None
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/v1"
        or (parsed.netloc != f"[{host}]:{port}" and parsed.netloc != f"{host}:{port}")
    ):
        raise ValueError("vLLM base URL must be an exact loopback HTTP /v1 URL")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ValueError("vLLM base URL must use a literal loopback address") from error
    if not address.is_loopback:
        raise ValueError("vLLM base URL must use a literal loopback address")
    return value


def _require_loopback_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError("runtime connection address must be a literal IP") from error
    if not address.is_loopback or address.compressed != value:
        raise ValueError("runtime connection address must be canonical loopback")
    return value


def _require_canonical_local_path(value: str) -> str:
    _require_unicode(value)
    if (
        "\x00" in value
        or not value.startswith("/")
        or value == "/"
        or value.startswith("//")
        or value.endswith("/")
        or any(segment in {"", ".", ".."} for segment in value.split("/")[1:])
    ):
        raise ValueError(
            "local checkpoint path must be a canonical non-root absolute POSIX path"
        )
    return value


def _require_canonical_float_wire(value: str) -> str:
    numeric_value = float(value)
    if canonical_json_bytes(numeric_value).decode("ascii") != value:
        raise ValueError("sampling value must exactly round-trip to its JSON float")
    return value


CanonicalModelId = Literal["Qwen/Qwen3.6-35B-A3B"]
CanonicalLocalPath = Annotated[
    str,
    Field(strict=True, min_length=2, max_length=4096),
    AfterValidator(_require_canonical_local_path),
]
ServedModelName = Annotated[
    str,
    Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$"),
]
DigestValue = Annotated[str, Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]
REQUIRED_RUNTIME_ROUTE_POLICY_HASH = (
    "sha256:b305ee7c32e0cff9c69911f3dffdb7af5e0351f39d0a4cc64930c683cd63c1dd"
)
RequiredRuntimeRoutePolicyHash = Literal[
    "sha256:b305ee7c32e0cff9c69911f3dffdb7af5e0351f39d0a4cc64930c683cd63c1dd"
]
CanonicalRevision = Literal["995ad96eacd98c81ed38be0c5b274b04031597b0"]
OfficialModelConfigHash = Literal[
    "sha256:93a4693fa9d8392fbfccd4b3c9873f4bfdcb14fdede978b123d07d19675efe99"
]
OfficialWeightIndexHash = Literal[
    "sha256:41b9356101ebf8e7519e150dc811f80c4226e727301fbb032b890f006ed0be83"
]
OfficialTokenizerHash = Literal[
    "sha256:5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42"
]
OfficialTokenizerConfigHash = Literal[
    "sha256:5186f0defcd7f232382c7f0aebcd2252d073bb921ab240e407b7ae8745d2b29b"
]
OfficialChatTemplateHash = Literal[
    "sha256:e84f32a23fdda27689f868aa4a1a5621f41133e51a48d7f3efcbea2839574259"
]
OfficialVllmDistributionHash = Literal[
    "sha256:9e206f370c934a2d4b6b1f05d3d09708d344e05d80260189ef19f60755709431"
]
TemperatureWire = Annotated[
    str,
    Field(
        strict=True,
        max_length=64,
        pattern=r"^(?:0|1|2|0\.[0-9]*[1-9]|1\.[0-9]*[1-9])$",
    ),
    AfterValidator(_require_canonical_float_wire),
]
TopPWire = Annotated[
    str,
    Field(
        strict=True,
        max_length=64,
        pattern=r"^(?:1|0\.[0-9]*[1-9])$",
    ),
    AfterValidator(_require_canonical_float_wire),
]

OFFICIAL_QWEN_WEIGHT_SHARD_HASHES = MappingProxyType(
    {
        "model-00001-of-00026.safetensors": "sha256:adee7bcb930aed22e0677e58d4873b48dadb1ed8001cb5c6a0487286eadb3478",
        "model-00002-of-00026.safetensors": "sha256:88f2dfd2b9e73e4b70be533dbf61bcfa3c9a0003758900fcbc9d9b96f5751d4b",
        "model-00003-of-00026.safetensors": "sha256:8f7d72178d3f4431864978e5bcfa4c6cb1c204bc00590644d90bb19d6d522eeb",
        "model-00004-of-00026.safetensors": "sha256:12d7db38689ba3c8af74b23ef8523eca41e0cd95db870583d0663a3ee8a6bd60",
        "model-00005-of-00026.safetensors": "sha256:a836047305d0f7a7b50f0815d09d5c03ec03d59ec2c763fcdc4bf7e9936bf902",
        "model-00006-of-00026.safetensors": "sha256:c9080d718e9c5f9e337443225aa417d4c24d00ae7995d76ee3f1cc296b557d15",
        "model-00007-of-00026.safetensors": "sha256:e8c05e23131b1dd45a455ec38cfac7db14667358268623c3938d00cf3e959a68",
        "model-00008-of-00026.safetensors": "sha256:4b6a6d495053089f4a80e7cbc82e848fba44e2c0c60122233d8fdff79fa7b296",
        "model-00009-of-00026.safetensors": "sha256:a31a954bb72d1c714e751bf0aabf2ff533f5a509693ebf7dd22ad6e90be46f67",
        "model-00010-of-00026.safetensors": "sha256:246560e66570fe746653b8443e245dc334c9b8b831ea43d2d9f1b7d98623994e",
        "model-00011-of-00026.safetensors": "sha256:7180392817fe3ecb3a27a1da43b7ff22c1a94806bac49975f9f122c3126df675",
        "model-00012-of-00026.safetensors": "sha256:043fb525f6625c2f2acb75e65a9959ee3fa7b6e3fdd2034b5cfe1859b01d3cfb",
        "model-00013-of-00026.safetensors": "sha256:33a20fb20a21379bf43c84a43105f9c0cc35bd50d740b1c302dcbe4b700f5425",
        "model-00014-of-00026.safetensors": "sha256:be823e33c5cb6120ad3769d081f34a2449dc2358041fca7c29d636c1ba19130d",
        "model-00015-of-00026.safetensors": "sha256:a89d547c6f9d0b535ee5ea2f2478f163089539f3f0dd330cb23d278a19d76123",
        "model-00016-of-00026.safetensors": "sha256:69fc3ae0316482288afdcdd0b9eb7d626703ae26f7567e89aa3fc8d1ffd4ff5b",
        "model-00017-of-00026.safetensors": "sha256:e356e3943cf3852b76bb8992e674f3256013e27d54b78e8250514151cdc29637",
        "model-00018-of-00026.safetensors": "sha256:9e5e63fd1cc7d6848330c1fa363dfcb661bbc2ac87e672d0e28b71c9cb7f3c7f",
        "model-00019-of-00026.safetensors": "sha256:708644ad34f1de727bf484f396944d8ec628645d52c183e9a992e65671685e21",
        "model-00020-of-00026.safetensors": "sha256:ca083a1d1aa64f8e8a785998f543a43374f13436dc85d396eee4e72c7a84e1ae",
        "model-00021-of-00026.safetensors": "sha256:ada4ae48f3d48fe01b4c53f2f82bce25e798a9631fd33959c881156fef2ccbce",
        "model-00022-of-00026.safetensors": "sha256:def207fb42d7db31efb512755557763c23233c6e4d4c433027cb5102a7bce2f7",
        "model-00023-of-00026.safetensors": "sha256:864d52ca7768a36f514069222e8de8626264ae124097ba8fcce5b5da2c6e2ed7",
        "model-00024-of-00026.safetensors": "sha256:391acd27420cdce5935ff18152423c70620d19dac3c39a5ef1a81d369f82d737",
        "model-00025-of-00026.safetensors": "sha256:778e7f76602f05042b69ba7f3ec91f1fdffef390540b16074041c258fb81d154",
        "model-00026-of-00026.safetensors": "sha256:1a97404220077ed3d4182e10385b152004cab608377f50cec9f54a6b8d28b613",
    }
)
NonEmptyText = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=100_000),
    AfterValidator(_require_unicode),
]
RuntimeRequestId = Annotated[
    str,
    Field(strict=True, pattern=r"^llmreq_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
]
SignatureValue = Annotated[
    str,
    Field(strict=True, pattern=r"^[A-Za-z0-9_-]{86}$"),
]


def _require_canonical_signature(value: str) -> str:
    try:
        decoded = urlsafe_b64decode(value + "==")
    except ValueError as error:
        raise ValueError("signature must be canonical unpadded base64url") from error
    if (
        len(decoded) != 64
        or urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value
    ):
        raise ValueError("signature must be canonical 64-byte Ed25519 data")
    return value


class RuntimeArtifactReference(StrictFrozenModel):
    artifact_id: ArtifactId
    payload_hash: DigestValue


class LocalLlmRuntimeManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.local-llm-runtime-manifest.v3"]
    runtime_id: Annotated[
        str,
        Field(strict=True, pattern=r"^runtime_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
    ]
    lifecycle_mode: Literal["ATTACHED", "MANAGED"]
    profile_id: Literal["llm-qwen36-vllm"]
    base_url: Annotated[
        str,
        Field(strict=True, max_length=512),
        AfterValidator(_require_loopback_v1_url),
    ]
    model_id: CanonicalModelId
    model_checkpoint_path: CanonicalLocalPath
    tokenizer_checkpoint_path: CanonicalLocalPath
    served_model_name: ServedModelName
    observed_at: Annotated[
        str,
        Field(strict=True, max_length=32),
        AfterValidator(_require_utc_timestamp),
    ]
    model_revision: CanonicalRevision
    tokenizer_revision: CanonicalRevision
    model_config_hash: OfficialModelConfigHash
    weight_index_hash: OfficialWeightIndexHash
    weight_shard_hashes: FrozenStringMapping[DigestValue]
    tokenizer_hash: OfficialTokenizerHash
    tokenizer_config_hash: OfficialTokenizerConfigHash
    chat_template_hash: OfficialChatTemplateHash
    vllm_version: Literal["0.25.1+cu129"]
    vllm_distribution_hash: OfficialVllmDistributionHash
    runtime_environment_hash: DigestValue
    pytorch_version: NonEmptyText
    cuda_version: NonEmptyText
    container_digest: DigestValue
    startup_args: FrozenSequence[NonEmptyText]
    listener_identity_hash: DigestValue
    process_identity_hash: DigestValue
    relay_identity_hash: DigestValue
    route_policy_hash: RequiredRuntimeRoutePolicyHash
    credential_id: Literal["local-llm-server.v1"]
    credential_fingerprint: DigestValue
    max_model_len: PositiveSafeInt
    max_concurrency: PositiveSafeInt
    request_timeout_seconds: PositiveSafeInt
    max_prompt_tokens: PositiveSafeInt
    max_completion_tokens: PositiveSafeInt
    reasoning_parser: Literal["qwen3"]
    tool_call_parser: Literal["qwen3_coder"]
    thinking_policy: Literal["disabled"]
    chat_template_policy: Literal["enable_thinking=false"]

    @model_validator(mode="after")
    def require_closed_attached_identity(self) -> Self:
        expected_shards = tuple(
            f"model-{index:05d}-of-00026.safetensors" for index in range(1, 27)
        )
        shard_names = tuple(self.weight_shard_hashes)
        if shard_names != expected_shards:
            raise ValueError(
                "runtime manifest requires the exact 26-shard Qwen snapshot"
            )
        if dict(self.weight_shard_hashes) != dict(OFFICIAL_QWEN_WEIGHT_SHARD_HASHES):
            raise ValueError("runtime manifest weight shard hashes do not match Qwen")
        if not self.startup_args:
            raise ValueError("runtime manifest requires frozen startup arguments")
        if self.startup_args[0] != self.model_checkpoint_path:
            raise ValueError(
                "runtime startup arguments must bind the model positionally"
            )
        api_key_positions = tuple(
            index
            for index, argument in enumerate(self.startup_args)
            if argument == "--api-key"
        )
        if any(argument.startswith("--api-key=") for argument in self.startup_args) or (
            len(api_key_positions) != 1
            or self.startup_args.count("[REDACTED]") != 1
            or api_key_positions[0] + 1 >= len(self.startup_args)
            or self.startup_args[api_key_positions[0] + 1] != "[REDACTED]"
        ):
            raise ValueError(
                "runtime manifest requires exact redacted API-key evidence"
            )
        required_pairs = (
            ("--revision", self.model_revision),
            ("--tokenizer", self.tokenizer_checkpoint_path),
            ("--tokenizer-revision", self.tokenizer_revision),
            ("--served-model-name", self.served_model_name),
            ("--max-model-len", str(self.max_model_len)),
            ("--reasoning-parser", self.reasoning_parser),
            ("--tool-call-parser", self.tool_call_parser),
        )
        protected_flags = {flag for flag, _ in required_pairs} | {
            "--model",
            "--chat-template",
            "--enable-auto-tool-choice",
        }
        if any(
            any(argument.startswith(flag + "=") for flag in protected_flags)
            for argument in self.startup_args
        ):
            raise ValueError("runtime startup arguments use a forbidden override form")
        if any(
            argument in {"--model", "--chat-template"} for argument in self.startup_args
        ):
            raise ValueError("runtime startup arguments override frozen identity")
        for flag, expected in required_pairs:
            positions = tuple(
                index
                for index, argument in enumerate(self.startup_args)
                if argument == flag
            )
            if (
                len(positions) != 1
                or positions[0] + 1 >= len(self.startup_args)
                or self.startup_args[positions[0] + 1] != expected
            ):
                raise ValueError(
                    "runtime startup arguments do not bind the parser identity"
                )
        if self.startup_args.count("--enable-auto-tool-choice") != 1:
            raise ValueError(
                "runtime startup arguments must enable the frozen tool policy"
            )
        value_flags = {
            "--api-key",
            "--cpu-offload-gb",
            "--dtype",
            "--generation-config",
            "--gpu-memory-utilization",
            "--host",
            "--max-model-len",
            "--max-num-batched-tokens",
            "--max-num-seqs",
            "--port",
            "--reasoning-parser",
            "--revision",
            "--seed",
            "--served-model-name",
            "--structured-outputs-config",
            "--tensor-parallel-size",
            "--tokenizer",
            "--tokenizer-revision",
            "--tool-call-parser",
        }
        switch_flags = {
            "--disable-uvicorn-access-log",
            "--enable-auto-tool-choice",
            "--enable-chunked-prefill",
            "--enforce-eager",
            "--language-model-only",
            "--no-enable-log-requests",
        }
        seen_flags: set[str] = set()
        index = 1
        while index < len(self.startup_args):
            argument = self.startup_args[index]
            if argument in seen_flags or argument not in value_flags | switch_flags:
                raise ValueError("runtime startup argument is not in the closed policy")
            seen_flags.add(argument)
            if argument in switch_flags:
                index += 1
                continue
            if index + 1 >= len(self.startup_args) or self.startup_args[
                index + 1
            ].startswith("--"):
                raise ValueError("runtime startup argument value is missing")
            index += 2
        if self.max_prompt_tokens + self.max_completion_tokens > self.max_model_len:
            raise ValueError("prompt and completion ceilings exceed max_model_len")
        return self

    @property
    def identity_hash(self) -> str:
        payload = self.model_dump(mode="json", round_trip=True, warnings="error")
        return "sha256:" + sha256(canonical_json_bytes(payload)).hexdigest()

    @property
    def artifact_payload_hash(self) -> str:
        return _canonical_artifact_payload_hash(self)


class RuntimeProcessEvidence(StrictFrozenModel):
    schema_version: Literal["automarkov.runtime-process-evidence.v2"]
    runtime_id: Annotated[
        str,
        Field(strict=True, pattern=r"^runtime_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
    ]
    observed_at: Annotated[
        str,
        Field(strict=True, max_length=32),
        AfterValidator(_require_utc_timestamp),
    ]
    lifecycle_mode: Literal["ATTACHED", "MANAGED"]
    listener_identity_hash: DigestValue
    process_identity_hash: DigestValue
    relay_identity_hash: DigestValue
    route_policy_hash: RequiredRuntimeRoutePolicyHash
    startup_args: FrozenSequence[NonEmptyText]

    @property
    def payload_hash(self) -> str:
        return _canonical_artifact_payload_hash(self)


class RuntimePackageEvidence(StrictFrozenModel):
    schema_version: Literal["automarkov.runtime-package-evidence.v1"]
    runtime_id: Annotated[
        str,
        Field(strict=True, pattern=r"^runtime_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
    ]
    observed_at: Annotated[
        str,
        Field(strict=True, max_length=32),
        AfterValidator(_require_utc_timestamp),
    ]
    vllm_version: Literal["0.25.1+cu129"]
    vllm_distribution_hash: OfficialVllmDistributionHash
    runtime_environment_hash: DigestValue
    pytorch_version: NonEmptyText
    cuda_version: NonEmptyText
    container_digest: DigestValue

    @property
    def payload_hash(self) -> str:
        return _canonical_artifact_payload_hash(self)


class RuntimeModelSnapshotEvidence(StrictFrozenModel):
    schema_version: Literal["automarkov.runtime-model-snapshot-evidence.v1"]
    runtime_id: Annotated[
        str,
        Field(strict=True, pattern=r"^runtime_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
    ]
    observed_at: Annotated[
        str,
        Field(strict=True, max_length=32),
        AfterValidator(_require_utc_timestamp),
    ]
    model_id: CanonicalModelId
    model_checkpoint_path: CanonicalLocalPath
    tokenizer_checkpoint_path: CanonicalLocalPath
    model_revision: CanonicalRevision
    tokenizer_revision: CanonicalRevision
    model_config_hash: OfficialModelConfigHash
    weight_index_hash: OfficialWeightIndexHash
    weight_shard_hashes: FrozenStringMapping[DigestValue]
    tokenizer_hash: OfficialTokenizerHash
    tokenizer_config_hash: OfficialTokenizerConfigHash
    chat_template_hash: OfficialChatTemplateHash
    thinking_policy: Literal["disabled"]
    chat_template_policy: Literal["enable_thinking=false"]

    @model_validator(mode="after")
    def require_official_snapshot(self) -> Self:
        expected_shards = tuple(
            f"model-{index:05d}-of-00026.safetensors" for index in range(1, 27)
        )
        if tuple(self.weight_shard_hashes) != expected_shards or dict(
            self.weight_shard_hashes
        ) != dict(OFFICIAL_QWEN_WEIGHT_SHARD_HASHES):
            raise ValueError("runtime model evidence requires the exact Qwen snapshot")
        return self

    @property
    def payload_hash(self) -> str:
        return _canonical_artifact_payload_hash(self)


class RuntimeHostAttestation(StrictFrozenModel):
    schema_version: Literal["automarkov.runtime-host-attestation.v3"]
    signing_domain: Literal["AutoMarkov-Runtime-Host-Attestation-v3"]
    attestation_id: Annotated[
        str,
        Field(strict=True, pattern=r"^runtimeatt_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
    ]
    runtime_manifest_ref: RuntimeArtifactReference
    process_evidence_ref: RuntimeArtifactReference
    package_evidence_ref: RuntimeArtifactReference
    model_snapshot_evidence_ref: RuntimeArtifactReference
    observed_at: Annotated[
        str,
        Field(strict=True, max_length=32),
        AfterValidator(_require_utc_timestamp),
    ]
    nonce: CanonicalNonce
    signature_algorithm: Literal["Ed25519"]
    signing_key_id: Annotated[
        str,
        Field(strict=True, pattern=r"^key_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
    ]
    signature: Annotated[SignatureValue, AfterValidator(_require_canonical_signature)]

    def signing_bytes(self) -> bytes:
        payload = self.model_dump(mode="json", round_trip=True, warnings="error")
        del payload["signature"]
        return canonical_json_bytes(payload)

    @property
    def payload_hash(self) -> str:
        return _canonical_artifact_payload_hash(self)


class RuntimeCurrentConnectionProof(StrictFrozenModel):
    """特权 host resolver 对同一条已连接 socket 签发的短期证明。"""

    schema_version: Literal["automarkov.runtime-current-connection-proof.v2"]
    signing_domain: Literal["AutoMarkov-Runtime-Current-Connection-Proof-v2"]
    runtime_manifest_artifact_id: ArtifactId
    runtime_manifest_payload_hash: DigestValue
    challenge: CanonicalNonce
    request_binding_hash: DigestValue
    observed_at: Annotated[
        str,
        Field(strict=True, max_length=32),
        AfterValidator(_require_utc_timestamp),
    ]
    host_boot_id: Annotated[
        str,
        Field(
            strict=True,
            pattern=(
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}$"
            ),
        ),
    ]
    network_namespace_inode: PositiveSafeInt
    listener_socket_inode: PositiveSafeInt
    accepted_socket_inode: PositiveSafeInt
    owner_pid: PositiveSafeInt
    owner_start_ticks: PositiveSafeInt
    executable_identity_hash: DigestValue
    startup_args_hash: DigestValue
    client_address: Annotated[
        str,
        Field(strict=True, max_length=64),
        AfterValidator(_require_loopback_address),
    ]
    client_port: Annotated[int, Field(strict=True, ge=1, le=65_535)]
    server_address: Annotated[
        str,
        Field(strict=True, max_length=64),
        AfterValidator(_require_loopback_address),
    ]
    server_port: Annotated[int, Field(strict=True, ge=1, le=65_535)]
    listener_identity_hash: DigestValue
    process_identity_hash: DigestValue
    relay_identity_hash: DigestValue
    route_policy_hash: RequiredRuntimeRoutePolicyHash
    signature_algorithm: Literal["Ed25519"]
    signing_key_id: Annotated[
        str,
        Field(strict=True, pattern=r"^key_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
    ]
    signature: Annotated[SignatureValue, AfterValidator(_require_canonical_signature)]

    @property
    def expected_process_identity_hash(self) -> str:
        payload = {
            "domain": "AutoMarkov-Runtime-Process-Identity-v1",
            "host_boot_id": self.host_boot_id,
            "network_namespace_inode": self.network_namespace_inode,
            "owner_pid": self.owner_pid,
            "owner_start_ticks": self.owner_start_ticks,
            "executable_identity_hash": self.executable_identity_hash,
            "startup_args_hash": self.startup_args_hash,
        }
        return "sha256:" + sha256(canonical_json_bytes(payload)).hexdigest()

    @property
    def expected_listener_identity_hash(self) -> str:
        payload = {
            "domain": "AutoMarkov-Runtime-Listener-Identity-v1",
            "process_identity_hash": self.process_identity_hash,
            "network_namespace_inode": self.network_namespace_inode,
            "server_address": self.server_address,
            "server_port": self.server_port,
            "listener_socket_inode": self.listener_socket_inode,
        }
        return "sha256:" + sha256(canonical_json_bytes(payload)).hexdigest()

    @model_validator(mode="after")
    def bind_identity_preimages(self) -> Self:
        if (
            self.process_identity_hash != self.expected_process_identity_hash
            or self.listener_identity_hash != self.expected_listener_identity_hash
        ):
            raise ValueError("current connection proof identity preimage mismatch")
        return self

    def signing_bytes(self) -> bytes:
        payload = self.model_dump(mode="json", round_trip=True, warnings="error")
        del payload["signature"]
        return canonical_json_bytes(payload)

    @property
    def payload_hash(self) -> str:
        payload = self.model_dump(mode="json", round_trip=True, warnings="error")
        return "sha256:" + sha256(canonical_json_bytes(payload)).hexdigest()


class LlmStartRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.llm-start-request.v4"]
    runtime_manifest_artifact_id: ArtifactId
    runtime_manifest_payload_hash: Sha256Digest
    runtime_manifest: LocalLlmRuntimeManifest
    host_attestation: RuntimeHostAttestation

    @model_validator(mode="after")
    def bind_manifest_hash(self) -> Self:
        manifest_observed_at = datetime.fromisoformat(self.runtime_manifest.observed_at)
        attestation_observed_at = datetime.fromisoformat(
            self.host_attestation.observed_at
        )
        if (
            self.host_attestation.runtime_manifest_ref.artifact_id
            != self.runtime_manifest_artifact_id
            or self.host_attestation.runtime_manifest_ref.payload_hash
            != self.runtime_manifest_payload_hash.root
            or self.runtime_manifest_payload_hash.root
            != self.runtime_manifest.artifact_payload_hash
            or attestation_observed_at < manifest_observed_at
        ):
            raise ValueError("runtime host attestation does not bind the manifest")
        return self


class LlmPromptToolFunction(StrictFrozenModel):
    name: NonEmptyText
    arguments: Annotated[str, Field(strict=True, max_length=1_000_000)]


class LlmPromptToolCall(StrictFrozenModel):
    id: NonEmptyText
    type: Literal["function"]
    function: LlmPromptToolFunction


class SystemChatMessage(StrictFrozenModel):
    role: Literal["system"]
    content: NonEmptyText


class UserChatMessage(StrictFrozenModel):
    role: Literal["user"]
    content: NonEmptyText


class AssistantChatMessage(StrictFrozenModel):
    role: Literal["assistant"]
    content: Annotated[str, Field(strict=True, max_length=1_000_000)]
    tool_calls: FrozenSequence[LlmPromptToolCall]

    @model_validator(mode="after")
    def require_content_or_tool_call(self) -> Self:
        _require_unicode(self.content)
        if not self.content and not self.tool_calls:
            raise ValueError("assistant message requires content or a tool call")
        return self


class ToolChatMessage(StrictFrozenModel):
    role: Literal["tool"]
    content: NonEmptyText
    tool_call_id: NonEmptyText


ChatMessage: TypeAlias = Annotated[
    SystemChatMessage | UserChatMessage | AssistantChatMessage | ToolChatMessage,
    Field(discriminator="role"),
]


class LlmPromptArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.llm-prompt.v3"]
    generation_evidence_view: GenerationEvidenceView
    messages: FrozenSequence[ChatMessage]

    @model_validator(mode="after")
    def require_nonempty_messages(self) -> Self:
        if not self.messages:
            raise ValueError("LLM prompt requires at least one message")
        pending_tool_calls: set[str] = set()
        seen_tool_calls: set[str] = set()
        for message in self.messages:
            if isinstance(message, AssistantChatMessage):
                if pending_tool_calls:
                    raise ValueError(
                        "assistant tool calls require matching tool responses first"
                    )
                for tool_call in message.tool_calls:
                    if tool_call.id in seen_tool_calls:
                        raise ValueError("assistant tool-call IDs must be unique")
                    seen_tool_calls.add(tool_call.id)
                    pending_tool_calls.add(tool_call.id)
                continue
            if isinstance(message, ToolChatMessage):
                if message.tool_call_id not in pending_tool_calls:
                    raise ValueError(
                        "tool message must reference a pending assistant tool call"
                    )
                pending_tool_calls.remove(message.tool_call_id)
                continue
            if pending_tool_calls:
                raise ValueError(
                    "assistant tool calls require matching tool responses first"
                )
        if pending_tool_calls:
            raise ValueError("assistant tool calls require matching tool responses")
        return self

    @property
    def payload_hash(self) -> str:
        return _canonical_artifact_payload_hash(self)


class LlmSampling(StrictFrozenModel):
    temperature: TemperatureWire
    top_p: TopPWire
    seed: NonNegativeSafeInt
    max_tokens: PositiveSafeInt

    @property
    def temperature_value(self) -> float:
        return float(self.temperature)

    @property
    def top_p_value(self) -> float:
        return float(self.top_p)


class LlmCompletionRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.llm-completion-request.v4"]
    request_id: RuntimeRequestId
    runtime_manifest_payload_hash: Sha256Digest
    prompt_artifact_id: ArtifactId
    prompt_payload_hash: Sha256Digest
    prompt: LlmPromptArtifact
    sampling: LlmSampling

    @model_validator(mode="after")
    def bind_prompt_hash(self) -> Self:
        if self.prompt_payload_hash.root != self.prompt.payload_hash:
            raise ValueError("prompt payload hash mismatch")
        return self


class LlmToolCall(StrictFrozenModel):
    call_id: NonEmptyText
    name: NonEmptyText
    arguments: CanonicalJsonValue


class LlmResponsePayload(StrictFrozenModel):
    schema_version: Literal["automarkov.llm-response.v1"]
    content: str = Field(strict=True, max_length=1_000_000)
    tool_calls: FrozenSequence[LlmToolCall]
    finish_reason: Literal["stop", "length", "tool_calls"]

    @model_validator(mode="after")
    def require_content_or_tool_call(self) -> Self:
        _require_unicode(self.content)
        if not self.content and not self.tool_calls:
            raise ValueError("LLM response requires content or a tool call")
        call_ids = tuple(call.call_id for call in self.tool_calls)
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("LLM response tool call IDs must be unique")
        if (self.finish_reason == "tool_calls") != bool(self.tool_calls):
            raise ValueError("LLM response finish reason must match its tool calls")
        return self

    @property
    def payload_hash(self) -> str:
        return _canonical_artifact_payload_hash(self)


class LlmCompletionResponseArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.llm-completion-response-artifact.v1"]
    request_id: RuntimeRequestId
    runtime_manifest_ref: RuntimeArtifactReference
    runtime_probe_evidence_ref: RuntimeArtifactReference
    prompt_ref: RuntimeArtifactReference
    response: LlmResponsePayload

    @model_validator(mode="after")
    def require_distinct_upstream_artifacts(self) -> Self:
        artifact_ids = (
            self.runtime_manifest_ref.artifact_id,
            self.runtime_probe_evidence_ref.artifact_id,
            self.prompt_ref.artifact_id,
        )
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ValueError("completion response requires distinct upstream artifacts")
        return self

    @property
    def payload_hash(self) -> str:
        return _canonical_artifact_payload_hash(self)


class LlmUsage(StrictFrozenModel):
    prompt_tokens: NonNegativeSafeInt
    completion_tokens: NonNegativeSafeInt
    total_tokens: NonNegativeSafeInt

    @model_validator(mode="after")
    def require_exact_total(self) -> Self:
        if self.prompt_tokens + self.completion_tokens != self.total_tokens:
            raise ValueError("LLM usage total is inconsistent")
        return self


class LlmCompletionTrace(StrictFrozenModel):
    schema_version: Literal["automarkov.llm-completion-trace.v2"]
    request_id: RuntimeRequestId
    model_id: CanonicalModelId
    model_revision: CanonicalRevision
    vllm_version: Literal["0.25.1+cu129"]
    tokenizer_hash: OfficialTokenizerHash
    chat_template_hash: OfficialChatTemplateHash
    runtime_manifest_ref: RuntimeArtifactReference
    runtime_probe_evidence_ref: RuntimeArtifactReference
    prompt_ref: RuntimeArtifactReference
    response_ref: RuntimeArtifactReference
    endpoint_identity_hash: DigestValue
    connection_evidence_hash: DigestValue
    sampling: LlmSampling
    usage: LlmUsage
    latency_ms: NonNegativeSafeInt
    finish_reason: Literal["stop", "length", "tool_calls"]

    @model_validator(mode="after")
    def require_distinct_artifact_dag(self) -> Self:
        artifact_ids = (
            self.runtime_manifest_ref.artifact_id,
            self.runtime_probe_evidence_ref.artifact_id,
            self.prompt_ref.artifact_id,
            self.response_ref.artifact_id,
        )
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ValueError("completion trace requires four distinct artifact refs")
        return self

    @property
    def payload_hash(self) -> str:
        return _canonical_artifact_payload_hash(self)


class RuntimeProbeEvidence(StrictFrozenModel):
    schema_version: Literal["automarkov.runtime-probe-evidence.v3"]
    runtime_manifest_ref: RuntimeArtifactReference
    runtime_host_attestation_ref: RuntimeArtifactReference
    served_model_name: ServedModelName
    health_response_hash: DigestValue
    missing_auth_response_hash: DigestValue
    invalid_auth_response_hash: DigestValue
    models_response_hash: DigestValue
    canary_request_hash: DigestValue
    canary_response_hash: DigestValue

    @property
    def payload_hash(self) -> str:
        return _canonical_artifact_payload_hash(self)


class LlmProbeResult(StrictFrozenModel):
    schema_version: Literal["automarkov.llm-probe-result.v3"]
    runtime_id: Annotated[
        str,
        Field(strict=True, pattern=r"^runtime_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
    ]
    readiness_state: Literal["READY", "WAITING_RUNTIME"]
    ready: bool = Field(strict=True)
    runtime_manifest_payload_hash: DigestValue
    health_passed: bool = Field(strict=True)
    authenticated_models_passed: bool = Field(strict=True)
    authentication_enforced_passed: bool = Field(strict=True)
    authenticated_completion_passed: bool = Field(strict=True)
    served_model_name: str | None = Field(default=None, strict=True, max_length=512)
    health_response_hash: DigestValue | None
    missing_auth_response_hash: DigestValue | None
    invalid_auth_response_hash: DigestValue | None
    models_response_hash: DigestValue | None
    canary_request_hash: DigestValue | None
    canary_response_hash: DigestValue | None
    probe_evidence_artifact_id: ArtifactId | None = None
    probe_evidence_payload_hash: Sha256Digest | None = None
    failure_code: (
        Literal[
            "credential_invalid",
            "authentication_not_enforced",
            "health_failed",
            "identity_mismatch",
            "manifest_invalid",
            "models_failed",
            "completion_failed",
            "transport_failed",
        ]
        | None
    )

    @model_validator(mode="after")
    def require_consistent_readiness(self) -> Self:
        all_passed = (
            self.health_passed
            and self.authentication_enforced_passed
            and self.authenticated_models_passed
            and self.authenticated_completion_passed
            and self.probe_evidence_artifact_id is not None
            and self.probe_evidence_payload_hash is not None
        )
        if self.ready != all_passed or (
            self.ready != (self.readiness_state == "READY")
        ):
            raise ValueError("LLM readiness flags are inconsistent")
        if self.ready != (self.failure_code is None):
            raise ValueError("LLM failure code is inconsistent")
        if self.ready and (
            self.served_model_name is None
            or any(
                value is None
                for value in (
                    self.health_response_hash,
                    self.missing_auth_response_hash,
                    self.invalid_auth_response_hash,
                    self.models_response_hash,
                    self.canary_request_hash,
                    self.canary_response_hash,
                    self.probe_evidence_artifact_id,
                    self.probe_evidence_payload_hash,
                )
            )
        ):
            raise ValueError("READY requires complete probe identity evidence")
        if not self.ready and (
            self.probe_evidence_artifact_id is not None
            or self.probe_evidence_payload_hash is not None
        ):
            raise ValueError("WAITING_RUNTIME cannot publish READY probe evidence")
        return self


def _canonical_artifact_payload_hash(model: StrictFrozenModel) -> str:
    codec = CanonicalPayloadCodec(type(model))
    raw = model.model_dump(mode="json", round_trip=True, warnings="error")
    return "sha256:" + sha256(codec.encode(raw)).hexdigest()


def validate_llm_start_payload(value: object) -> LlmStartRequest:
    return validate_strict_frozen_payload(LlmStartRequest, value)


def validate_llm_completion_payload(value: object) -> LlmCompletionRequest:
    return validate_strict_frozen_payload(LlmCompletionRequest, value)


class LlmCompletionResult(StrictFrozenModel):
    schema_version: Literal["automarkov.llm-completion-result.v3"]
    response: LlmResponsePayload
    trace: LlmCompletionTrace
    response_payload_hash: Sha256Digest
    trace_payload_hash: Sha256Digest
    response_artifact_id: ArtifactId
    trace_artifact_id: ArtifactId

    @model_validator(mode="after")
    def bind_payload_hashes(self) -> Self:
        if self.response_payload_hash.root != self.response.payload_hash:
            raise ValueError("response payload hash mismatch")
        if self.trace_payload_hash.root != self.trace.payload_hash:
            raise ValueError("trace payload hash mismatch")
        response_artifact = LlmCompletionResponseArtifact(
            schema_version="automarkov.llm-completion-response-artifact.v1",
            request_id=self.trace.request_id,
            runtime_manifest_ref=self.trace.runtime_manifest_ref,
            runtime_probe_evidence_ref=self.trace.runtime_probe_evidence_ref,
            prompt_ref=self.trace.prompt_ref,
            response=self.response,
        )
        if (
            self.response_artifact_id != self.trace.response_ref.artifact_id
            or response_artifact.payload_hash != self.trace.response_ref.payload_hash
            or self.trace.finish_reason != self.response.finish_reason
            or self.trace_artifact_id.root
            in {
                self.trace.runtime_manifest_ref.artifact_id.root,
                self.trace.runtime_probe_evidence_ref.artifact_id.root,
                self.trace.prompt_ref.artifact_id.root,
                self.response_artifact_id.root,
            }
        ):
            raise ValueError("completion result artifact DAG mismatch")
        return self


__all__ = [
    "OFFICIAL_QWEN_WEIGHT_SHARD_HASHES",
    "REQUIRED_RUNTIME_ROUTE_POLICY_HASH",
    "AssistantChatMessage",
    "ChatMessage",
    "LlmCompletionRequest",
    "LlmCompletionResponseArtifact",
    "LlmCompletionResult",
    "LlmCompletionTrace",
    "LlmProbeResult",
    "LlmPromptArtifact",
    "LlmPromptToolCall",
    "LlmPromptToolFunction",
    "LlmResponsePayload",
    "LlmSampling",
    "LlmStartRequest",
    "LlmToolCall",
    "LlmUsage",
    "LocalLlmRuntimeManifest",
    "RuntimeArtifactReference",
    "RuntimeCurrentConnectionProof",
    "RuntimeHostAttestation",
    "RuntimeModelSnapshotEvidence",
    "RuntimePackageEvidence",
    "RuntimeProbeEvidence",
    "RuntimeProcessEvidence",
    "SystemChatMessage",
    "ToolChatMessage",
    "UserChatMessage",
    "validate_llm_completion_payload",
    "validate_llm_start_payload",
]
