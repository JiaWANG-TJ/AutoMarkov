from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from automarkov.canonical import canonical_json_bytes
from automarkov.lifecycle import ArtifactReference
from automarkov.suite_adapters import (
    MetaDriveScenarioBackend,
    MetaDriveSuiteAdapterManifest,
    ScenarioEpisodeSeedContract,
    ScenarioPartitionAttestation,
    ScenarioPartitionManifestAttestation,
    formal_single_agent_suite_readiness,
    require_non_overlapping_scenario_partitions,
    sign_scenario_partition_attestation,
    sign_scenario_partition_manifest_attestation,
    suite_adapter_parent_references,
    verify_scenario_partition_attestation,
    verify_scenario_partition_manifest_attestation,
)


def _ref(name: str, digit: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=f"artifact_{sha256(name.encode()).hexdigest()}",
        payload_hash=f"sha256:{digit * 64}",
    )


def _signed_partition(
    *,
    key: Ed25519PrivateKey,
    kind: str,
    scenario_ids: list[str],
) -> ScenarioPartitionAttestation:
    attestation = sign_scenario_partition_attestation(
        {
            "schema_version": "automarkov.scenario-partition-attestation.v1",
            "signing_domain": "AutoMarkov-Scenario-Partition-Attestation-v1",
            "partition_id": f"partition_metadrive_{kind}_v1",
            "partition_kind": kind,
            "dataset_revision_hash": "sha256:" + "a" * 64,
            "scenario_ids": scenario_ids,
            "issued_at": "2026-08-12T00:00:00Z",
            "signing_key_id": "key_scenario_partition",
        },
        key,
    )
    return attestation


def _partition() -> tuple[ScenarioPartitionAttestation, Ed25519PrivateKey]:
    key = Ed25519PrivateKey.generate()
    return (
        _signed_partition(
            key=key,
            kind="evaluation",
            scenario_ids=["scenario_001", "scenario_002"],
        ),
        key,
    )


def _partition_manifest() -> tuple[
    ScenarioPartitionManifestAttestation, Ed25519PrivateKey
]:
    key = Ed25519PrivateKey.generate()
    attestation = sign_scenario_partition_manifest_attestation(
        {
            "schema_version": "automarkov.scenario-partition-manifest-attestation.v1",
            "signing_domain": "AutoMarkov-Scenario-Partition-Manifest-Attestation-v1",
            "manifest_id": "partition_manifest_metadrive_v1",
            "dataset_revision_hash": "sha256:" + "a" * 64,
            "training_partition": _signed_partition(
                key=key, kind="training", scenario_ids=["scenario_101"]
            ).model_dump(mode="json"),
            "validation_partition": _signed_partition(
                key=key, kind="validation", scenario_ids=["scenario_201"]
            ).model_dump(mode="json"),
            "evaluation_partition": _signed_partition(
                key=key,
                kind="evaluation",
                scenario_ids=["scenario_001", "scenario_002"],
            ).model_dump(mode="json"),
            "issued_at": "2026-08-12T00:00:00Z",
            "signing_key_id": "key_scenario_partition",
        },
        key,
    )
    return attestation, key


def _partition_manifest_ref(
    attestation: ScenarioPartitionManifestAttestation,
) -> ArtifactReference:
    digest = sha256(
        canonical_json_bytes(
            attestation.model_dump(mode="json", round_trip=True, warnings="error")
        )
    ).hexdigest()
    return ArtifactReference(
        artifact_id=f"artifact_{digest}", payload_hash=f"sha256:{digest}"
    )


def _manifest(
    partition_manifest: ScenarioPartitionManifestAttestation | None = None,
) -> MetaDriveSuiteAdapterManifest:
    return MetaDriveSuiteAdapterManifest(
        schema_version="automarkov.single-agent-suite-adapter.v1",
        suite_id="metadrive_pomdp",
        implementation_plan=_ref("plan", "1"),
        decision_process_spec=_ref("spec", "2"),
        signed_suite_manifest=_ref("suite", "3"),
        candidate_bundle=_ref("candidate", "4"),
        runtime_profile_manifest=_ref("profile", "5"),
        official_provenance=_ref("metadrive-provenance", "0"),
        scenario_partition_manifest_attestation=(
            _partition_manifest_ref(partition_manifest)
            if partition_manifest is not None
            else _ref("partition-manifest", "6")
        ),
        adapter_id="adapter_metadrive_scenario_v1",
        adapter_source_hash="sha256:" + "7" * 64,
        runtime_profile_id="env-metadrive",
        protocol_version="automarkov.remote-env.v1",
        frame_schema_hash="sha256:" + "8" * 64,
        space_adapter_registry_hash="sha256:" + "9" * 64,
        seed_contract=ScenarioEpisodeSeedContract(
            kind="scenario_episode", scenario_id="scenario_001", seed=23
        ),
        route="compose",
        environment_id="ScenarioEnv",
        package_name="metadrive-simulator",
        package_version="0.4.3",
        upstream_commit="5bf8ea8909c4643a4099a250e6f5fb89c695d8b4",
        scenarionet_commit="d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170",
        physics_policy="official_unmodified",
        traffic_policy="scenario_replay_in_environment_process",
        selected_partition="evaluation",
    )


class _ScenarioFactory:
    def __init__(self) -> None:
        self.requests: list[tuple[str, int]] = []

    def create(self, *, scenario_id: str, seed: int) -> _ScenarioEnv:
        self.requests.append((scenario_id, seed))
        return _ScenarioEnv(scenario_id)


class _ScenarioEnv:
    def __init__(self, scenario_id: str) -> None:
        self.scenario_id = scenario_id

    def reset(
        self, *, seed: int, options: Mapping[str, object] | None
    ) -> tuple[object, Mapping[str, object]]:
        del options
        return {"lidar": [0.5], "scenario_id": self.scenario_id}, {"seed": seed}

    def step(
        self, action: object
    ) -> tuple[object, float, bool, bool, Mapping[str, object]]:
        del action
        return {"lidar": [0.4], "scenario_id": self.scenario_id}, 1.0, False, False, {}

    def close(self) -> None:
        return None


def test_metadrive_partition_is_signed_and_manifest_has_no_runtime_locator() -> None:
    attestation, key = _partition()
    verify_scenario_partition_attestation(
        attestation,
        trusted_keys={"key_scenario_partition": key.public_key()},
    )
    assert attestation.scenario_ids == ("scenario_001", "scenario_002")

    manifest = _manifest()
    assert suite_adapter_parent_references(manifest)[-1] in (
        manifest.scenario_partition_manifest_attestation,
        manifest.signed_suite_manifest,
    )
    raw = manifest.model_dump(mode="json", round_trip=True, warnings="error")
    for forbidden_field, value in (
        ("scenario_path", "/datasets/waymo"),
        ("scenario_index", 1),
        ("scenario_import", "scenarionet.converter"),
        ("raw_dataset_path", "/raw"),
    ):
        candidate = dict(raw)
        candidate[forbidden_field] = value
        with pytest.raises(ValidationError, match="Extra inputs"):
            MetaDriveSuiteAdapterManifest.model_validate(candidate, strict=True)

    assert formal_single_agent_suite_readiness(
        manifest, image_status="recipe_frozen", partition_attested=False
    ) == (
        "WAITING_ASSET",
        "MetaDrive requires a signed scenario partition",
    )


def test_metadrive_partition_rejects_signature_tampering_and_overlap() -> None:
    attestation, key = _partition()
    tampered = attestation.model_copy(
        update={"dataset_revision_hash": "sha256:" + "b" * 64}
    )
    with pytest.raises(ValueError, match="signature is invalid"):
        verify_scenario_partition_attestation(
            tampered,
            trusted_keys={"key_scenario_partition": key.public_key()},
        )

    second = attestation.model_copy(
        update={"partition_id": "partition_metadrive_train_v1"}
    )
    with pytest.raises(ValueError, match="must not overlap"):
        require_non_overlapping_scenario_partitions(attestation, second)


def test_metadrive_backend_uses_only_attested_scenario_identity_and_episode_seed() -> (
    None
):
    attestation, key = _partition_manifest()
    factory = _ScenarioFactory()
    backend = MetaDriveScenarioBackend(
        manifest=_manifest(attestation),
        partition_manifest_attestation=attestation,
        trusted_partition_keys={"key_scenario_partition": key.public_key()},
        environment_factory=factory,
    )

    observation, info = backend.reset(
        seed_contract=ScenarioEpisodeSeedContract(
            kind="scenario_episode", scenario_id="scenario_001", seed=23
        ),
        options={},
    )
    assert factory.requests == [("scenario_001", 23)]
    assert observation == {"lidar": [0.5], "scenario_id": "scenario_001"}
    assert info == {"seed": 23}

    with pytest.raises(ValueError, match="attested partition"):
        backend.reset(
            seed_contract=ScenarioEpisodeSeedContract(
                kind="scenario_episode", scenario_id="scenario_999", seed=23
            ),
            options={},
        )


def test_metadrive_partition_manifest_binds_all_disjoint_partitions() -> None:
    attestation, key = _partition_manifest()
    verify_scenario_partition_manifest_attestation(
        attestation,
        trusted_keys={"key_scenario_partition": key.public_key()},
    )
    assert attestation.training_partition.scenario_ids == ("scenario_101",)
    assert attestation.validation_partition.scenario_ids == ("scenario_201",)
    assert attestation.evaluation_partition.scenario_ids == (
        "scenario_001",
        "scenario_002",
    )

    overlapping = attestation.model_copy(
        update={"validation_partition": attestation.training_partition}
    )
    with pytest.raises(ValueError, match="partition kinds|overlap"):
        ScenarioPartitionManifestAttestation.model_validate(
            overlapping.model_dump(mode="json", round_trip=True, warnings="error"),
            strict=True,
        )

    tampered_manifest = _manifest(attestation).model_copy(
        update={"scenario_partition_manifest_attestation": _ref("wrong", "f")}
    )
    with pytest.raises(ValueError, match="manifest artifact"):
        MetaDriveScenarioBackend(
            manifest=tampered_manifest,
            partition_manifest_attestation=attestation,
            trusted_partition_keys={"key_scenario_partition": key.public_key()},
            environment_factory=_ScenarioFactory(),
        )
