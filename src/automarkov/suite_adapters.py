from __future__ import annotations

import base64
from binascii import Error as Base64Error
from collections.abc import Callable, Mapping
from hashlib import sha256
from types import MappingProxyType
from typing import Annotated, Literal, Protocol, Self, TypeAlias, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import (
    AfterValidator,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from automarkov.canonical import (
    FrozenSequence,
    canonical_json_bytes,
)
from automarkov.domain import StrictFrozenModel
from automarkov.environment_contracts import (
    RuntimeImageStatus,
    RuntimeProfileResolution,
)
from automarkov.lifecycle import (
    ArtifactReference,
    CanonicalTimestamp,
    ExecutionAttestation,
    NonEmptyId,
    Sha256Value,
)
from automarkov.provenance import RuntimeProfileId, RuntimeProfileManifest
from automarkov.remote_env import (
    RemoteEnvRuntimeUnavailable,
    TlsSocketRemoteEnv,
)
from automarkov.remote_env_contracts import (
    BoxSpace,
    DictEntry,
    DictSpace,
    DiscreteSpace,
    FiniteTextSpace,
    IntegerResetSeedContract,
    ScenarioEpisodeSeedContract,
    SeedContract,
)

_MINIGRID_MISSION = "go to the matching object at the end of the hallway"


def _canonical_nonempty_strings(value: tuple[str, ...]) -> tuple[str, ...]:
    if not value or value != tuple(
        sorted(set(value), key=lambda item: item.encode("utf-8"))
    ):
        raise ValueError("values must be UTF-8 sorted, unique, and non-empty")
    return value


CanonicalNonEmptyIds = Annotated[
    FrozenSequence[NonEmptyId], AfterValidator(_canonical_nonempty_strings)
]
CanonicalNonEmptyStrings = Annotated[
    FrozenSequence[Annotated[str, Field(strict=True, min_length=1, max_length=1024)]],
    AfterValidator(_canonical_nonempty_strings),
]


SingleAgentSeedContract: TypeAlias = SeedContract


class SuitePackageIdentity(StrictFrozenModel):
    package_name: NonEmptyId
    package_version: NonEmptyId


class SuiteRepositoryIdentity(StrictFrozenModel):
    repository_name: NonEmptyId
    repository_commit: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{40}$")]


class OfficialSuiteProfileExpectation(StrictFrozenModel):
    suite_id: NonEmptyId
    runtime_profile_id: RuntimeProfileId
    environment_id: NonEmptyId
    framework_contract: Literal[
        "gymnasium.remote-env.rllib.v1",
        "pettingzoo.parallel.remote-env.rllib-multi-agent.v1",
    ]
    package_identities: FrozenSequence[SuitePackageIdentity]
    repository_identities: FrozenSequence[SuiteRepositoryIdentity]

    @model_validator(mode="after")
    def require_canonical_identities(self) -> Self:
        for values, attribute in (
            (self.package_identities, "package_name"),
            (self.repository_identities, "repository_name"),
        ):
            names = tuple(getattr(item, attribute) for item in values)
            if names != tuple(
                sorted(set(names), key=lambda item: item.encode("utf-8"))
            ):
                raise ValueError("suite runtime identities must be canonical")
        return self


_OFFICIAL_SUITE_PROFILES: Mapping[str, dict[str, object]] = {
    "taxi_mdp": {
        "runtime_profile_id": "rllib-taxi-synthesis",
        "environment_id": "generated_taxi_candidate",
        "framework_contract": "gymnasium.remote-env.rllib.v1",
        "packages": {
            "gymnasium": "1.2.2",
            "minigrid": "3.1.0",
            "mpe2": "1.1.0",
            "pettingzoo": "1.26.1",
            "ray": "2.56.1",
            "safetensors": "0.8.0",
            "torch": "2.13.0",
        },
        "repositories": {"ray": "936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a"},
    },
    "memory_pomdp": {
        "runtime_profile_id": "env-minigrid",
        "environment_id": "MiniGrid-MemoryS17Random-v0",
        "framework_contract": "gymnasium.remote-env.rllib.v1",
        "packages": {"gymnasium": "1.2.2", "minigrid": "3.1.0"},
        "repositories": {"minigrid": "90928729376741a41222a257911343b97103b548"},
    },
    "metadrive_pomdp": {
        "runtime_profile_id": "env-metadrive",
        "environment_id": "ScenarioEnv",
        "framework_contract": "gymnasium.remote-env.rllib.v1",
        "packages": {"metadrive-simulator": "0.4.3", "setuptools": "84.0.0"},
        "repositories": {
            "metadrive": "5bf8ea8909c4643a4099a250e6f5fb89c695d8b4",
            "scenarionet": "d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170",
        },
    },
    "mpe2_full_state_mg": {
        "runtime_profile_id": "env-mpe2",
        "environment_id": "simple_spread_v3",
        "framework_contract": "pettingzoo.parallel.remote-env.rllib-multi-agent.v1",
        "packages": {
            "gymnasium": "1.2.2",
            "mpe2": "1.1.0",
            "pettingzoo": "1.26.1",
        },
        "repositories": {
            "mpe2": "7590d9d52791e321974d4fda6090fb18f34dbf49",
            "pettingzoo": "1756a4d7494b532651f0024ff7087ef4945432a6",
        },
    },
    "mpe2_native_local_posg": {
        "runtime_profile_id": "env-mpe2",
        "environment_id": "simple_spread_v3",
        "framework_contract": "pettingzoo.parallel.remote-env.rllib-multi-agent.v1",
        "packages": {
            "gymnasium": "1.2.2",
            "mpe2": "1.1.0",
            "pettingzoo": "1.26.1",
        },
        "repositories": {
            "mpe2": "7590d9d52791e321974d4fda6090fb18f34dbf49",
            "pettingzoo": "1756a4d7494b532651f0024ff7087ef4945432a6",
        },
    },
    "smacv2_posg": {
        "runtime_profile_id": "env-smacv2",
        "environment_id": "protoss_5_vs_5",
        "framework_contract": "pettingzoo.parallel.remote-env.rllib-multi-agent.v1",
        "packages": {
            "protobuf": "3.20.1",
            "pysc2": "4.0.0",
            "setuptools": "84.0.0",
        },
        "repositories": {"smacv2": "577ab5a2cff2391f8df582da5731ea9cd6adf3c6"},
    },
    "citylearn_posg": {
        "runtime_profile_id": "env-citylearn",
        "environment_id": "CityLearnEnv",
        "framework_contract": "pettingzoo.parallel.remote-env.rllib-multi-agent.v1",
        "packages": {
            "citylearn": "2.5.0",
            "gymnasium": "0.28.1",
            "numpy": "1.26.4",
            "setuptools": "84.0.0",
        },
        "repositories": {"citylearn": "29062af6d077409e1c37a3e53a6cac30fd4d02bc"},
    },
}


def official_suite_profile_expectation(
    suite_id: str,
) -> OfficialSuiteProfileExpectation:
    raw = _OFFICIAL_SUITE_PROFILES.get(suite_id)
    if raw is None:
        raise ValueError("suite has no frozen official profile expectation")
    packages = cast(dict[str, str], raw["packages"])
    repositories = cast(dict[str, str], raw["repositories"])
    return OfficialSuiteProfileExpectation(
        suite_id=suite_id,
        runtime_profile_id=cast(RuntimeProfileId, raw["runtime_profile_id"]),
        environment_id=cast(str, raw["environment_id"]),
        framework_contract=cast(
            Literal[
                "gymnasium.remote-env.rllib.v1",
                "pettingzoo.parallel.remote-env.rllib-multi-agent.v1",
            ],
            raw["framework_contract"],
        ),
        package_identities=tuple(
            SuitePackageIdentity(package_name=name, package_version=version)
            for name, version in sorted(packages.items())
        ),
        repository_identities=tuple(
            SuiteRepositoryIdentity(repository_name=name, repository_commit=commit)
            for name, commit in sorted(repositories.items())
        ),
    )


def verify_official_suite_profile(
    suite_id: str, profile: RuntimeProfileManifest
) -> OfficialSuiteProfileExpectation:
    if type(profile) is not RuntimeProfileManifest:
        raise ValueError("suite profile must use the exact runtime manifest")
    expectation = official_suite_profile_expectation(suite_id)
    expected_packages = {
        item.package_name: item.package_version
        for item in expectation.package_identities
    }
    expected_repositories = {
        item.repository_name: item.repository_commit
        for item in expectation.repository_identities
    }
    if (
        profile.profile_id != expectation.runtime_profile_id
        or profile.python_version != "3.11.13"
        or dict(profile.package_versions) != expected_packages
        or dict(profile.repository_commits) != expected_repositories
        or "RemoteEnv" not in profile.protocol_edges
    ):
        raise ValueError(
            "runtime profile does not match frozen official package/version/commit identity"
        )
    return expectation


class SuiteProfileContract(StrictFrozenModel):
    schema_version: Literal["automarkov.suite-profile-contract.v1"]
    expectation: OfficialSuiteProfileExpectation
    runtime_profile_manifest: ArtifactReference
    official_provenance: ArtifactReference
    environment_space_hash: Sha256Value
    adapter_source_hash: Sha256Value
    protocol_version: Literal["automarkov.remote-env.v1"]


class SuiteProfileWorkerAttestation(StrictFrozenModel):
    schema_version: Literal["automarkov.suite-profile-worker-attestation.v1"]
    signing_domain: Literal["AutoMarkov-Suite-Profile-Worker-Attestation-v1"]
    contract: SuiteProfileContract
    profile_lock_hash: Sha256Value
    image_digest: Sha256Value
    platform: Literal["linux/amd64"]
    worker_process_execution_id: NonEmptyId
    worker_principal_id: NonEmptyId
    issued_at: CanonicalTimestamp
    signing_key_id: NonEmptyId
    signature_b64url: Annotated[str, Field(strict=True, min_length=86, max_length=86)]

    @field_validator("signature_b64url")
    @classmethod
    def require_signature(cls, value: str) -> str:
        try:
            decoded = base64.urlsafe_b64decode(value + "==")
        except (ValueError, Base64Error) as error:
            raise ValueError(
                "signature must be canonical unpadded base64url"
            ) from error
        if (
            len(decoded) != 64
            or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value
        ):
            raise ValueError("signature must be canonical 64-byte Ed25519 data")
        return value

    def signing_bytes(self) -> bytes:
        payload = self.model_dump(mode="json", round_trip=True, warnings="error")
        del payload["signature_b64url"]
        return canonical_json_bytes(payload)


def sign_suite_profile_worker_attestation(
    fields: Mapping[str, object], signing_key: Ed25519PrivateKey
) -> SuiteProfileWorkerAttestation:
    if type(fields) is not dict or "signature_b64url" in fields:
        raise ValueError("suite worker signer requires exact unsigned fields")
    if not isinstance(signing_key, Ed25519PrivateKey):
        raise TypeError("suite worker signer requires an Ed25519 key")
    payload = dict(fields)
    payload["signature_b64url"] = (
        base64.urlsafe_b64encode(bytes(64)).decode().rstrip("=")
    )
    provisional = SuiteProfileWorkerAttestation.model_validate(payload, strict=True)
    payload["signature_b64url"] = (
        base64.urlsafe_b64encode(signing_key.sign(provisional.signing_bytes()))
        .decode("ascii")
        .rstrip("=")
    )
    return SuiteProfileWorkerAttestation.model_validate(payload, strict=True)


def build_single_agent_suite_profile_contract(
    manifest: SingleAgentSuiteAdapterManifest,
    *,
    profile: RuntimeProfileManifest,
) -> SuiteProfileContract:
    if type(manifest) not in {
        TaxiSuiteAdapterManifest,
        MiniGridMemorySuiteAdapterManifest,
        MetaDriveSuiteAdapterManifest,
    }:
        raise ValueError("single-agent suite profile requires an exact manifest")
    expectation = verify_official_suite_profile(manifest.suite_id, profile)
    if (
        manifest.runtime_profile_id != expectation.runtime_profile_id
        or manifest.environment_id != expectation.environment_id
        or manifest.runtime_profile_manifest.payload_hash != profile.manifest_hash
    ):
        raise ValueError("suite manifest does not bind the frozen official profile")
    space_payload: dict[str, object] = {
        "domain": "AutoMarkov-Single-Agent-Suite-Space-v1",
        "environment_id": manifest.environment_id,
        "frame_schema_hash": manifest.frame_schema_hash,
        "space_adapter_registry_hash": manifest.space_adapter_registry_hash,
    }
    observation_space = getattr(manifest, "observation_space", None)
    if observation_space is not None:
        space_payload["observation_space"] = observation_space.model_dump(
            mode="json", round_trip=True, warnings="error"
        )
    action_space = getattr(manifest, "action_space", None)
    if action_space is not None:
        space_payload["action_space"] = action_space.model_dump(
            mode="json", round_trip=True, warnings="error"
        )
    return SuiteProfileContract(
        schema_version="automarkov.suite-profile-contract.v1",
        expectation=expectation,
        runtime_profile_manifest=manifest.runtime_profile_manifest,
        official_provenance=manifest.official_provenance,
        environment_space_hash="sha256:"
        + sha256(canonical_json_bytes(space_payload)).hexdigest(),
        adapter_source_hash=manifest.adapter_source_hash,
        protocol_version=manifest.protocol_version,
    )


class VerifiedProfileRemoteEnv:
    def __init__(
        self,
        *,
        contract: SuiteProfileContract,
        transport: TlsSocketRemoteEnv,
        _verified_token: object,
    ) -> None:
        if _verified_token is not _PROFILE_REMOTE_ENV_TOKEN:
            raise ValueError("profile RemoteEnv must be created by the verifier")
        self.contract = contract
        self._transport = transport

    def exchange(self, canonical_frame: bytes) -> bytes:
        if type(canonical_frame) is not bytes:
            raise ValueError("profile RemoteEnv accepts canonical bytes only")
        return self._transport.exchange(canonical_frame)

    def close(self) -> None:
        self._transport.close()


_PROFILE_REMOTE_ENV_TOKEN = object()


def resolve_profile_remote_env(
    contract: SuiteProfileContract,
    *,
    profile: RuntimeProfileManifest,
    resolution: RuntimeProfileResolution,
    worker_attestation: SuiteProfileWorkerAttestation | None,
    trusted_worker_keys: Mapping[str, Ed25519PublicKey],
    transport: TlsSocketRemoteEnv | None,
) -> VerifiedProfileRemoteEnv:
    if transport is not None and type(transport) is not TlsSocketRemoteEnv:
        raise ValueError("production suite composition requires real TLS RemoteEnv")
    verify_official_suite_profile(contract.expectation.suite_id, profile)
    if (
        contract.runtime_profile_manifest.payload_hash != profile.manifest_hash
        or resolution.profile_id != profile.profile_id
        or resolution.profile_manifest != contract.runtime_profile_manifest
        or resolution.lock_hash != profile.lock_hash
    ):
        raise ValueError("resolver output does not bind the suite runtime profile")
    if profile.image_status != "built" or resolution.image_status != "built":
        raise RemoteEnvRuntimeUnavailable(
            "WAITING_RUNTIME: suite profile remains recipe_frozen or unverified"
        )
    if worker_attestation is None or transport is None:
        raise RemoteEnvRuntimeUnavailable(
            "WAITING_RUNTIME: suite profile worker or RemoteEnv transport is unavailable"
        )
    if type(worker_attestation) is not SuiteProfileWorkerAttestation:
        raise ValueError("suite worker attestation must use the exact contract")
    key = trusted_worker_keys.get(worker_attestation.signing_key_id)
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError("suite worker signing key is not trusted")
    try:
        key.verify(
            base64.urlsafe_b64decode(worker_attestation.signature_b64url + "=="),
            worker_attestation.signing_bytes(),
        )
    except InvalidSignature:
        raise ValueError("suite worker attestation signature is invalid") from None
    if (
        worker_attestation.contract != contract
        or worker_attestation.profile_lock_hash != profile.lock_hash
        or worker_attestation.image_digest != profile.image_digest
        or worker_attestation.platform != profile.platform
        or resolution.image_digest != profile.image_digest
        or resolution.platform != profile.platform
    ):
        raise ValueError("suite worker attestation identity is inconsistent")
    return VerifiedProfileRemoteEnv(
        contract=contract,
        transport=transport,
        _verified_token=_PROFILE_REMOTE_ENV_TOKEN,
    )


class _SingleAgentSuiteAdapterManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.single-agent-suite-adapter.v1"]
    implementation_plan: ArtifactReference
    decision_process_spec: ArtifactReference
    signed_suite_manifest: ArtifactReference
    candidate_bundle: ArtifactReference
    runtime_profile_manifest: ArtifactReference
    official_provenance: ArtifactReference
    adapter_id: NonEmptyId
    adapter_source_hash: Sha256Value
    runtime_profile_id: RuntimeProfileId
    protocol_version: Literal["automarkov.remote-env.v1"]
    frame_schema_hash: Sha256Value
    space_adapter_registry_hash: Sha256Value
    seed_contract: SingleAgentSeedContract


class TaxiSuiteAdapterManifest(_SingleAgentSuiteAdapterManifest):
    suite_id: Literal["taxi_mdp"]
    route: Literal["generate"]
    source_mode: Literal["SYNTHESIS"]
    environment_id: Literal["generated_taxi_candidate"]
    materialization: Literal["candidate_bundle_only"]
    candidate_source_attestation: ArtifactReference
    candidate_source_hash: Sha256Value
    observation_space: DiscreteSpace
    action_space: DiscreteSpace

    @model_validator(mode="after")
    def require_synthesis_boundary(self) -> Self:
        if (
            self.runtime_profile_id != "rllib-taxi-synthesis"
            or type(self.seed_contract) is not IntegerResetSeedContract
            or self.observation_space
            != DiscreteSpace(kind="Discrete", n=500, start=0, dtype="int64")
            or self.action_space
            != DiscreteSpace(kind="Discrete", n=6, start=0, dtype="int64")
            or self.candidate_source_attestation.payload_hash
            != self.candidate_source_hash
        ):
            raise ValueError(
                "Taxi must use the frozen synthesis profile and Discrete(500)/Discrete(6) contract"
            )
        return self


class MiniGridMemorySuiteAdapterManifest(_SingleAgentSuiteAdapterManifest):
    suite_id: Literal["memory_pomdp"]
    route: Literal["compose"]
    environment_id: Literal["MiniGrid-MemoryS17Random-v0"]
    package_name: Literal["minigrid"]
    package_version: Literal["3.1.0"]
    upstream_commit: Literal["90928729376741a41222a257911343b97103b548"]
    mission_values: CanonicalNonEmptyStrings
    observation_keys: CanonicalNonEmptyStrings
    observation_space: DictSpace
    action_space: DiscreteSpace
    observation_policy: Literal["partial_image_direction_mission"]
    history_policy: Literal["actor_recurrent_state_only"]

    @model_validator(mode="after")
    def require_memory_pomdp_boundary(self) -> Self:
        if (
            self.runtime_profile_id != "env-minigrid"
            or type(self.seed_contract) is not IntegerResetSeedContract
            or self.mission_values != (_MINIGRID_MISSION,)
            or self.observation_keys != ("direction", "image", "mission")
            or self.observation_space
            != DictSpace(
                kind="Dict",
                entries=(
                    DictEntry(
                        key="direction",
                        space=DiscreteSpace(
                            kind="Discrete", n=4, start=0, dtype="int64"
                        ),
                    ),
                    DictEntry(
                        key="image",
                        space=BoxSpace(
                            kind="Box",
                            shape=(7, 7, 3),
                            dtype="uint8",
                            low_tensor_id="tensor_minigrid_image_low",
                            high_tensor_id="tensor_minigrid_image_high",
                        ),
                    ),
                    DictEntry(
                        key="mission",
                        space=FiniteTextSpace(
                            kind="FiniteText", values=(_MINIGRID_MISSION,)
                        ),
                    ),
                ),
            )
            or self.action_space
            != DiscreteSpace(kind="Discrete", n=7, start=0, dtype="int64")
        ):
            raise ValueError(
                "MiniGrid Memory must preserve its exact partial observation and mission contract"
            )
        return self


class MetaDriveSuiteAdapterManifest(_SingleAgentSuiteAdapterManifest):
    suite_id: Literal["metadrive_pomdp"]
    route: Literal["compose"]
    scenario_partition_manifest_attestation: ArtifactReference
    environment_id: Literal["ScenarioEnv"]
    package_name: Literal["metadrive-simulator"]
    package_version: Literal["0.4.3"]
    upstream_commit: Literal["5bf8ea8909c4643a4099a250e6f5fb89c695d8b4"]
    scenarionet_commit: Literal["d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170"]
    physics_policy: Literal["official_unmodified"]
    traffic_policy: Literal["scenario_replay_in_environment_process"]
    selected_partition: Literal["training", "validation", "evaluation"]

    @model_validator(mode="after")
    def require_scenario_profile(self) -> Self:
        if (
            self.runtime_profile_id != "env-metadrive"
            or type(self.seed_contract) is not ScenarioEpisodeSeedContract
        ):
            raise ValueError(
                "MetaDrive must use its isolated profile and scenario episode seed"
            )
        return self


SingleAgentSuiteAdapterManifest: TypeAlias = Annotated[
    TaxiSuiteAdapterManifest
    | MiniGridMemorySuiteAdapterManifest
    | MetaDriveSuiteAdapterManifest,
    Field(discriminator="suite_id"),
]
SINGLE_AGENT_SUITE_ADAPTER = TypeAdapter(SingleAgentSuiteAdapterManifest)


class ScenarioPartitionAttestation(StrictFrozenModel):
    schema_version: Literal["automarkov.scenario-partition-attestation.v1"]
    signing_domain: Literal["AutoMarkov-Scenario-Partition-Attestation-v1"]
    partition_id: NonEmptyId
    partition_kind: Literal["training", "validation", "evaluation"]
    dataset_revision_hash: Sha256Value
    scenario_ids: CanonicalNonEmptyIds
    issued_at: CanonicalTimestamp
    signing_key_id: NonEmptyId
    signature_b64url: Annotated[str, Field(strict=True, min_length=86, max_length=86)]

    @field_validator("signature_b64url")
    @classmethod
    def require_signature(cls, value: str) -> str:
        try:
            decoded = base64.urlsafe_b64decode(value + "==")
        except (ValueError, Base64Error) as error:
            raise ValueError(
                "signature must be canonical unpadded base64url"
            ) from error
        if (
            len(decoded) != 64
            or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value
        ):
            raise ValueError("signature must be canonical 64-byte Ed25519 data")
        return value

    def signing_bytes(self) -> bytes:
        payload = self.model_dump(mode="json", round_trip=True, warnings="error")
        del payload["signature_b64url"]
        return canonical_json_bytes(payload)


class ScenarioPartitionManifestAttestation(StrictFrozenModel):
    schema_version: Literal["automarkov.scenario-partition-manifest-attestation.v1"]
    signing_domain: Literal["AutoMarkov-Scenario-Partition-Manifest-Attestation-v1"]
    manifest_id: NonEmptyId
    dataset_revision_hash: Sha256Value
    training_partition: ScenarioPartitionAttestation
    validation_partition: ScenarioPartitionAttestation
    evaluation_partition: ScenarioPartitionAttestation
    issued_at: CanonicalTimestamp
    signing_key_id: NonEmptyId
    signature_b64url: Annotated[str, Field(strict=True, min_length=86, max_length=86)]

    @field_validator("signature_b64url")
    @classmethod
    def require_signature(cls, value: str) -> str:
        try:
            decoded = base64.urlsafe_b64decode(value + "==")
        except (ValueError, Base64Error) as error:
            raise ValueError(
                "signature must be canonical unpadded base64url"
            ) from error
        if (
            len(decoded) != 64
            or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value
        ):
            raise ValueError("signature must be canonical 64-byte Ed25519 data")
        return value

    @model_validator(mode="after")
    def require_complete_disjoint_partition_set(self) -> Self:
        partitions = (
            self.training_partition,
            self.validation_partition,
            self.evaluation_partition,
        )
        if tuple(item.partition_kind for item in partitions) != (
            "training",
            "validation",
            "evaluation",
        ):
            raise ValueError("MetaDrive partition kinds are incomplete or drifted")
        if any(
            item.dataset_revision_hash != self.dataset_revision_hash
            for item in partitions
        ):
            raise ValueError("MetaDrive partitions must share one dataset revision")
        require_non_overlapping_scenario_partitions(*partitions)
        return self

    def signing_bytes(self) -> bytes:
        payload = self.model_dump(mode="json", round_trip=True, warnings="error")
        del payload["signature_b64url"]
        return canonical_json_bytes(payload)


def sign_scenario_partition_attestation(
    fields: Mapping[str, object],
    signing_key: Ed25519PrivateKey,
) -> ScenarioPartitionAttestation:
    if type(fields) is not dict or "signature_b64url" in fields:
        raise ValueError("partition signer requires exact unsigned fields")
    if not isinstance(signing_key, Ed25519PrivateKey):
        raise TypeError("partition signer requires an Ed25519 key")
    payload = dict(fields)
    payload["signature_b64url"] = (
        base64.urlsafe_b64encode(bytes(64)).decode().rstrip("=")
    )
    provisional = ScenarioPartitionAttestation.model_validate(payload, strict=True)
    payload["signature_b64url"] = (
        base64.urlsafe_b64encode(signing_key.sign(provisional.signing_bytes()))
        .decode("ascii")
        .rstrip("=")
    )
    return ScenarioPartitionAttestation.model_validate(payload, strict=True)


def sign_scenario_partition_manifest_attestation(
    fields: Mapping[str, object],
    signing_key: Ed25519PrivateKey,
) -> ScenarioPartitionManifestAttestation:
    if type(fields) is not dict or "signature_b64url" in fields:
        raise ValueError("partition manifest signer requires exact unsigned fields")
    if not isinstance(signing_key, Ed25519PrivateKey):
        raise TypeError("partition manifest signer requires an Ed25519 key")
    payload = dict(fields)
    payload["signature_b64url"] = (
        base64.urlsafe_b64encode(bytes(64)).decode().rstrip("=")
    )
    provisional = ScenarioPartitionManifestAttestation.model_validate(
        payload, strict=True
    )
    payload["signature_b64url"] = (
        base64.urlsafe_b64encode(signing_key.sign(provisional.signing_bytes()))
        .decode("ascii")
        .rstrip("=")
    )
    return ScenarioPartitionManifestAttestation.model_validate(payload, strict=True)


def verify_scenario_partition_attestation(
    attestation: ScenarioPartitionAttestation,
    *,
    trusted_keys: Mapping[str, Ed25519PublicKey],
) -> None:
    if type(attestation) is not ScenarioPartitionAttestation:
        raise ValueError("partition attestation must use the exact closed contract")
    if type(trusted_keys) is not dict:
        raise ValueError("trusted partition keys must be a caller snapshot")
    key = trusted_keys.get(attestation.signing_key_id)
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError("partition signing key is not trusted")
    try:
        key.verify(
            base64.urlsafe_b64decode(attestation.signature_b64url + "=="),
            attestation.signing_bytes(),
        )
    except InvalidSignature:
        raise ValueError("partition attestation signature is invalid") from None


def verify_scenario_partition_manifest_attestation(
    attestation: ScenarioPartitionManifestAttestation,
    *,
    trusted_keys: Mapping[str, Ed25519PublicKey],
) -> None:
    if type(attestation) is not ScenarioPartitionManifestAttestation:
        raise ValueError("partition manifest must use the exact closed contract")
    if type(trusted_keys) is not dict:
        raise ValueError("trusted partition keys must be a caller snapshot")
    key = trusted_keys.get(attestation.signing_key_id)
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError("partition manifest signing key is not trusted")
    try:
        key.verify(
            base64.urlsafe_b64decode(attestation.signature_b64url + "=="),
            attestation.signing_bytes(),
        )
    except InvalidSignature:
        raise ValueError("partition manifest signature is invalid") from None
    for partition in (
        attestation.training_partition,
        attestation.validation_partition,
        attestation.evaluation_partition,
    ):
        verify_scenario_partition_attestation(partition, trusted_keys=trusted_keys)


def _partition_manifest_payload_hash(
    attestation: ScenarioPartitionManifestAttestation,
) -> str:
    return (
        "sha256:"
        + sha256(
            canonical_json_bytes(
                attestation.model_dump(mode="json", round_trip=True, warnings="error")
            )
        ).hexdigest()
    )


def require_non_overlapping_scenario_partitions(
    *attestations: ScenarioPartitionAttestation,
) -> None:
    seen: dict[str, str] = {}
    partition_ids: set[str] = set()
    for attestation in attestations:
        if type(attestation) is not ScenarioPartitionAttestation:
            raise ValueError("partitions must use exact signed attestation contracts")
        if attestation.partition_id in partition_ids:
            raise ValueError("scenario partition identities must be unique")
        partition_ids.add(attestation.partition_id)
        for scenario_id in attestation.scenario_ids:
            if scenario_id in seen:
                raise ValueError("scenario partitions must not overlap")
            seen[scenario_id] = attestation.partition_id


def suite_adapter_parent_references(
    manifest: SingleAgentSuiteAdapterManifest,
) -> tuple[ArtifactReference, ...]:
    if type(manifest) not in {
        TaxiSuiteAdapterManifest,
        MiniGridMemorySuiteAdapterManifest,
        MetaDriveSuiteAdapterManifest,
    }:
        raise ValueError("suite adapter manifest must use an exact branch type")
    references = [
        manifest.implementation_plan,
        manifest.decision_process_spec,
        manifest.signed_suite_manifest,
        manifest.candidate_bundle,
        manifest.runtime_profile_manifest,
        manifest.official_provenance,
    ]
    if type(manifest) is TaxiSuiteAdapterManifest:
        references.append(manifest.candidate_source_attestation)
    if type(manifest) is MetaDriveSuiteAdapterManifest:
        references.append(manifest.scenario_partition_manifest_attestation)
    keys = tuple((item.artifact_id, item.payload_hash) for item in references)
    if len(keys) != len(set(keys)):
        raise ValueError("suite adapter parents must be unique")
    return tuple(sorted(references, key=lambda item: item.artifact_id.encode("utf-8")))


class _GymnasiumEnvironment(Protocol):
    def reset(
        self, *, seed: int, options: Mapping[str, object] | None
    ) -> tuple[object, Mapping[str, object]]: ...

    def step(
        self, action: object
    ) -> tuple[object, float, bool, bool, Mapping[str, object]]: ...

    def close(self) -> None: ...


class _ScenarioEnvironmentFactory(Protocol):
    def create(self, *, scenario_id: str, seed: int) -> _GymnasiumEnvironment: ...


class TaxiDenyMatrix(StrictFrozenModel):
    source_file_read_denial: ArtifactReference
    bytecode_read_denial: ArtifactReference
    direct_import_denial: ArtifactReference
    find_spec_denial: ArtifactReference
    resource_lookup_denial: ArtifactReference
    wheel_read_denial: ArtifactReference
    sdist_read_denial: ArtifactReference
    package_cache_discovery_denial: ArtifactReference

    @model_validator(mode="after")
    def require_unique_evidence(self) -> Self:
        references = self.references
        identities = tuple((item.artifact_id, item.payload_hash) for item in references)
        if len(set(identities)) != len(identities):
            raise ValueError("Taxi denial evidence references must be unique")
        return self

    @property
    def references(self) -> tuple[ArtifactReference, ...]:
        return (
            self.source_file_read_denial,
            self.bytecode_read_denial,
            self.direct_import_denial,
            self.find_spec_denial,
            self.resource_lookup_denial,
            self.wheel_read_denial,
            self.sdist_read_denial,
            self.package_cache_discovery_denial,
        )


_TAXI_TEST_FACTORY_TOKEN = object()


class TaxiRunnerBoundFactory:
    """仅在 runner 证明 Taxi 源码 import/read 均被拒绝后创建候选环境。"""

    def __init__(
        self,
        *,
        manifest: TaxiSuiteAdapterManifest,
        execution_attestation: ExecutionAttestation,
        trusted_runner_keys: Mapping[str, Ed25519PublicKey],
        expected_runner_principal_id: str,
        expected_job_manifest: ArtifactReference,
        denial_matrix: TaxiDenyMatrix,
        environment_builder: Callable[[], _GymnasiumEnvironment],
        _test_factory_token: object,
    ) -> None:
        if (
            type(manifest) is not TaxiSuiteAdapterManifest
            or type(execution_attestation) is not ExecutionAttestation
            or type(trusted_runner_keys) is not dict
            or type(expected_runner_principal_id) is not str
            or type(expected_job_manifest) is not ArtifactReference
            or type(denial_matrix) is not TaxiDenyMatrix
            or not callable(environment_builder)
            or _test_factory_token is not _TAXI_TEST_FACTORY_TOKEN
        ):
            raise ValueError(
                "Taxi fake factory is restricted to the explicit test seam"
            )
        key = trusted_runner_keys.get(execution_attestation.signing_key_id)
        if not isinstance(key, Ed25519PublicKey):
            raise TypeError("Taxi runner signing key is unavailable")
        unsigned = execution_attestation.model_dump(
            mode="json", round_trip=True, warnings="error"
        )
        del unsigned["signature_b64url"]
        try:
            key.verify(
                base64.urlsafe_b64decode(execution_attestation.signature_b64url + "=="),
                canonical_json_bytes(unsigned),
            )
        except (InvalidSignature, ValueError):
            raise ValueError("Taxi runner execution attestation is invalid") from None
        payload_outputs = tuple(
            sorted(
                (
                    manifest.candidate_bundle,
                    manifest.candidate_source_attestation,
                    *denial_matrix.references,
                ),
                key=lambda item: item.artifact_id.encode("utf-8"),
            )
        )
        if (
            execution_attestation.profile_id != manifest.runtime_profile_id
            or execution_attestation.principal_id != expected_runner_principal_id
            or execution_attestation.job_manifest != expected_job_manifest
            or execution_attestation.payload_outputs != payload_outputs
            or execution_attestation.terminal_result is not None
            or execution_attestation.actual_phase_transition.from_phase
            != "taxi_denial_preflight"
            or execution_attestation.actual_phase_transition.to_phase
            != "taxi_candidate_factory_ready"
        ):
            raise ValueError("Taxi runner denial attestation binding is invalid")
        self.manifest = manifest
        self.denial_matrix = denial_matrix
        self._environment_builder = environment_builder

    @classmethod
    def for_test(
        cls,
        *,
        manifest: TaxiSuiteAdapterManifest,
        execution_attestation: ExecutionAttestation,
        trusted_runner_keys: Mapping[str, Ed25519PublicKey],
        expected_runner_principal_id: str,
        expected_job_manifest: ArtifactReference,
        denial_matrix: TaxiDenyMatrix,
        environment_builder: Callable[[], _GymnasiumEnvironment],
    ) -> TaxiRunnerBoundFactory:
        return cls(
            manifest=manifest,
            execution_attestation=execution_attestation,
            trusted_runner_keys=trusted_runner_keys,
            expected_runner_principal_id=expected_runner_principal_id,
            expected_job_manifest=expected_job_manifest,
            denial_matrix=denial_matrix,
            environment_builder=environment_builder,
            _test_factory_token=_TAXI_TEST_FACTORY_TOKEN,
        )

    def create(self) -> _GymnasiumEnvironment:
        environment = self._environment_builder()
        unwrapped = getattr(environment, "unwrapped", environment)
        if type(unwrapped).__module__ == "gymnasium.envs.toy_text.taxi":
            raise ValueError("official Gymnasium Taxi objects are denied")
        if any(
            not callable(getattr(environment, method, None))
            for method in ("reset", "step", "close")
        ):
            raise ValueError("generated Taxi candidate does not implement Gymnasium")
        return environment


def _validate_transition(
    value: tuple[object, float, bool, bool, Mapping[str, object]],
) -> tuple[object, float, bool, bool, Mapping[str, object]]:
    if type(value) is not tuple or len(value) != 5:
        raise ValueError("Gymnasium step must return the exact five-value contract")
    observation, reward, terminated, truncated, info = value
    if (
        type(reward) is not float
        or type(terminated) is not bool
        or type(truncated) is not bool
        or not isinstance(info, Mapping)
    ):
        raise ValueError("Gymnasium step types do not match the frozen contract")
    return observation, reward, terminated, truncated, dict(info)


class TaxiGeneratedBackend:
    def __init__(
        self,
        *,
        manifest: TaxiSuiteAdapterManifest,
        factory: TaxiRunnerBoundFactory,
    ) -> None:
        if (
            type(manifest) is not TaxiSuiteAdapterManifest
            or type(factory) is not TaxiRunnerBoundFactory
            or factory.manifest != manifest
        ):
            raise ValueError("Taxi backend requires the exact synthesis manifest")
        self.manifest = manifest
        self._environment = factory.create()

    def reset(
        self,
        *,
        seed_contract: SingleAgentSeedContract,
        options: Mapping[str, object] | None,
    ) -> tuple[int, Mapping[str, object]]:
        if type(seed_contract) is not IntegerResetSeedContract:
            raise ValueError("Taxi requires an integer reset seed")
        observation, info = self._environment.reset(
            seed=seed_contract.seed, options=options
        )
        if type(observation) is not int or not 0 <= observation < 500:
            raise ValueError("Taxi observation must be an exact Discrete(500) value")
        return observation, dict(info)

    def step(
        self, action: object
    ) -> tuple[int, float, bool, bool, Mapping[str, object]]:
        if type(action) is not int or not 0 <= action < 6:
            raise ValueError("Taxi action must be an exact Discrete(6) value")
        result = _validate_transition(self._environment.step(action))
        if type(result[0]) is not int or not 0 <= result[0] < 500:
            raise ValueError("Taxi observation must be an exact Discrete(500) value")
        return cast(tuple[int, float, bool, bool, Mapping[str, object]], result)

    def close(self) -> None:
        self._environment.close()


class MiniGridMemoryBackend:
    def __init__(
        self,
        *,
        manifest: MiniGridMemorySuiteAdapterManifest,
        environment: _GymnasiumEnvironment,
    ) -> None:
        if type(manifest) is not MiniGridMemorySuiteAdapterManifest:
            raise ValueError("MiniGrid backend requires the exact Memory manifest")
        self.manifest = manifest
        self._environment = environment

    def _observation(self, value: object) -> dict[str, object]:
        if not isinstance(value, Mapping) or set(value) != {
            "image",
            "direction",
            "mission",
        }:
            raise ValueError(
                "MiniGrid observation must exactly preserve image, direction, and mission"
            )
        if type(value["direction"]) is not int or not 0 <= value["direction"] < 4:
            raise ValueError("MiniGrid direction must be an exact integer in [0, 4)")
        self._require_image(value["image"])
        if value["mission"] not in self.manifest.mission_values:
            raise ValueError(
                "MiniGrid mission is outside the audited finite mission set"
            )
        return dict(value)

    @staticmethod
    def _require_image(value: object) -> None:
        shape = getattr(value, "shape", None)
        dtype = getattr(value, "dtype", None)
        if shape is not None and tuple(shape) == (7, 7, 3) and str(dtype) == "uint8":
            return
        if type(value) is not list or len(value) != 7:
            raise ValueError("MiniGrid image must have exact uint8 shape (7, 7, 3)")
        for row in value:
            if type(row) is not list or len(row) != 7:
                raise ValueError("MiniGrid image must have exact uint8 shape (7, 7, 3)")
            for pixel in row:
                if (
                    type(pixel) is not list
                    or len(pixel) != 3
                    or any(
                        type(item) is not int or not 0 <= item <= 255 for item in pixel
                    )
                ):
                    raise ValueError(
                        "MiniGrid image must have exact uint8 shape (7, 7, 3)"
                    )

    def reset(
        self,
        *,
        seed_contract: SingleAgentSeedContract,
        options: Mapping[str, object] | None,
    ) -> tuple[dict[str, object], Mapping[str, object]]:
        if type(seed_contract) is not IntegerResetSeedContract:
            raise ValueError("MiniGrid requires an integer reset seed")
        observation, info = self._environment.reset(
            seed=seed_contract.seed, options=options
        )
        return self._observation(observation), dict(info)

    def step(
        self, action: object
    ) -> tuple[dict[str, object], float, bool, bool, Mapping[str, object]]:
        if type(action) is not int or not 0 <= action < 7:
            raise ValueError("MiniGrid action must be an exact Discrete(7) value")
        observation, reward, terminated, truncated, info = _validate_transition(
            self._environment.step(action)
        )
        if terminated and "correct_exit" not in info:
            info = dict(info)
            info["correct_exit"] = reward > 0.0
        if "correct_exit" in info and type(info["correct_exit"]) is not bool:
            raise ValueError("MiniGrid correct_exit must be an exact boolean")
        return self._observation(observation), reward, terminated, truncated, info

    def close(self) -> None:
        self._environment.close()


class MetaDriveScenarioBackend:
    def __init__(
        self,
        *,
        manifest: MetaDriveSuiteAdapterManifest,
        partition_manifest_attestation: ScenarioPartitionManifestAttestation,
        trusted_partition_keys: Mapping[str, Ed25519PublicKey],
        environment_factory: _ScenarioEnvironmentFactory,
    ) -> None:
        if type(manifest) is not MetaDriveSuiteAdapterManifest:
            raise ValueError(
                "MetaDrive backend requires the exact ScenarioEnv manifest"
            )
        verify_scenario_partition_manifest_attestation(
            partition_manifest_attestation, trusted_keys=trusted_partition_keys
        )
        if (
            manifest.scenario_partition_manifest_attestation.payload_hash
            != _partition_manifest_payload_hash(partition_manifest_attestation)
        ):
            raise ValueError("MetaDrive partition manifest artifact binding is invalid")
        self.manifest = manifest
        self._partition = {
            "training": partition_manifest_attestation.training_partition,
            "validation": partition_manifest_attestation.validation_partition,
            "evaluation": partition_manifest_attestation.evaluation_partition,
        }[manifest.selected_partition]
        self._factory = environment_factory
        self._environment: _GymnasiumEnvironment | None = None

    def reset(
        self,
        *,
        seed_contract: SingleAgentSeedContract,
        options: Mapping[str, object] | None,
    ) -> tuple[object, Mapping[str, object]]:
        if type(seed_contract) is not ScenarioEpisodeSeedContract:
            raise ValueError("MetaDrive requires a scenario episode seed")
        if seed_contract.scenario_id not in self._partition.scenario_ids:
            raise ValueError("scenario identity is outside the attested partition")
        sanitized_options = dict(options or {})
        selected_scenario = sanitized_options.pop(
            "scenario_id", seed_contract.scenario_id
        )
        if selected_scenario != seed_contract.scenario_id:
            raise ValueError("reset options cannot substitute the attested scenario")
        if self._environment is not None:
            self._environment.close()
        self._environment = self._factory.create(
            scenario_id=seed_contract.scenario_id, seed=seed_contract.seed
        )
        observation, info = self._environment.reset(
            seed=seed_contract.seed, options=sanitized_options
        )
        return observation, dict(info)

    def step(
        self, action: object
    ) -> tuple[object, float, bool, bool, Mapping[str, object]]:
        if self._environment is None:
            raise ValueError("MetaDrive step requires a successful scenario reset")
        return _validate_transition(self._environment.step(action))

    def close(self) -> None:
        if self._environment is not None:
            self._environment.close()
            self._environment = None


SingleAgentBackend: TypeAlias = (
    TaxiGeneratedBackend | MiniGridMemoryBackend | MetaDriveScenarioBackend
)


class SingleAgentSuiteLifecycle:
    """集中执行单主体 suite 的 typed lifecycle；它不冒充 bytes RemoteEnv。"""

    def __init__(self, *, backend: SingleAgentBackend) -> None:
        if type(backend) not in {
            TaxiGeneratedBackend,
            MiniGridMemoryBackend,
            MetaDriveScenarioBackend,
        }:
            raise ValueError("worker requires a registered single-agent backend")
        self._backend = backend
        self._phase: Literal["new", "described", "reset", "closed"] = "new"
        self._step_id = 0

    def describe(self) -> SingleAgentSuiteAdapterManifest:
        if self._phase == "closed":
            raise ValueError("closed worker cannot be described")
        if self._phase == "new":
            self._phase = "described"
        return self._backend.manifest

    def reset(
        self,
        *,
        seed_contract: SingleAgentSeedContract,
        options: Mapping[str, object] | None,
    ) -> tuple[object, Mapping[str, object]]:
        if self._phase not in {"described", "reset"}:
            raise ValueError("reset requires Describe and an open worker")
        if seed_contract != self._backend.manifest.seed_contract:
            raise ValueError("reset seed must equal the manifest-bound seed contract")
        result = self._backend.reset(seed_contract=seed_contract, options=options)
        self._phase = "reset"
        self._step_id = 0
        return result

    def step(
        self, action: object
    ) -> tuple[object, float, bool, bool, Mapping[str, object]]:
        if self._phase != "reset":
            raise ValueError("step requires a successful reset")
        result = self._backend.step(action)
        self._step_id += 1
        return result

    def close(self) -> None:
        if self._phase != "closed":
            self._backend.close()
            self._phase = "closed"


class RemoteGymnasiumEnv:
    """RLlib/Gymnasium 形状的唯一单主体 adapter；transport 由 RemoteEnv seam 提供。"""

    metadata: Mapping[str, object] = MappingProxyType({})
    render_mode = None

    def __init__(self, *, lifecycle: SingleAgentSuiteLifecycle) -> None:
        if type(lifecycle) is not SingleAgentSuiteLifecycle:
            raise ValueError("RemoteGymnasiumEnv requires the exact suite lifecycle")
        self._lifecycle = lifecycle
        self._manifest = lifecycle.describe()
        self.observation_space = getattr(self._manifest, "observation_space", None)
        self.action_space = getattr(self._manifest, "action_space", None)

    def reset(
        self,
        *,
        seed: int | None = None,
        options: Mapping[str, object] | None = None,
    ) -> tuple[object, Mapping[str, object]]:
        if type(seed) is not int or seed < 0:
            raise ValueError("RemoteGymnasiumEnv requires an explicit nonnegative seed")
        frozen = self._manifest.seed_contract
        if type(frozen) is IntegerResetSeedContract:
            contract: SingleAgentSeedContract = IntegerResetSeedContract(
                kind="integer_reset", seed=seed
            )
        else:
            scenario_id = None if options is None else options.get("scenario_id")
            if type(scenario_id) is not str:
                raise ValueError("MetaDrive reset options require a scenario_id")
            contract = ScenarioEpisodeSeedContract(
                kind="scenario_episode", scenario_id=scenario_id, seed=seed
            )
        return self._lifecycle.reset(seed_contract=contract, options=options)

    def step(
        self, action: object
    ) -> tuple[object, float, bool, bool, Mapping[str, object]]:
        return self._lifecycle.step(action)

    def close(self) -> None:
        self._lifecycle.close()


def formal_single_agent_suite_readiness(
    manifest: SingleAgentSuiteAdapterManifest,
    *,
    image_status: RuntimeImageStatus,
    partition_attested: bool = False,
) -> tuple[Literal["WAITING_RUNTIME", "WAITING_ASSET"], str]:
    if type(manifest) is MetaDriveSuiteAdapterManifest and not partition_attested:
        return "WAITING_ASSET", "MetaDrive requires a signed scenario partition"
    if image_status != "built":
        return (
            "WAITING_RUNTIME",
            "formal suite execution requires a built resolver-verified runtime profile",
        )
    return (
        "WAITING_RUNTIME",
        "formal suite execution awaits the fixed-commit RemoteEnv runner gate",
    )


__all__ = [
    "SINGLE_AGENT_SUITE_ADAPTER",
    "IntegerResetSeedContract",
    "MetaDriveScenarioBackend",
    "MetaDriveSuiteAdapterManifest",
    "MiniGridMemoryBackend",
    "MiniGridMemorySuiteAdapterManifest",
    "OfficialSuiteProfileExpectation",
    "RemoteGymnasiumEnv",
    "ScenarioEpisodeSeedContract",
    "ScenarioPartitionAttestation",
    "ScenarioPartitionManifestAttestation",
    "SingleAgentSeedContract",
    "SingleAgentSuiteAdapterManifest",
    "SingleAgentSuiteLifecycle",
    "SuitePackageIdentity",
    "SuiteProfileContract",
    "SuiteProfileWorkerAttestation",
    "SuiteRepositoryIdentity",
    "TaxiDenyMatrix",
    "TaxiGeneratedBackend",
    "TaxiRunnerBoundFactory",
    "TaxiSuiteAdapterManifest",
    "VerifiedProfileRemoteEnv",
    "build_single_agent_suite_profile_contract",
    "formal_single_agent_suite_readiness",
    "official_suite_profile_expectation",
    "require_non_overlapping_scenario_partitions",
    "resolve_profile_remote_env",
    "sign_scenario_partition_attestation",
    "sign_scenario_partition_manifest_attestation",
    "sign_suite_profile_worker_attestation",
    "suite_adapter_parent_references",
    "verify_official_suite_profile",
    "verify_scenario_partition_attestation",
    "verify_scenario_partition_manifest_attestation",
]
