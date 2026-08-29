from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from automarkov.contracts.environment import RuntimeProfileResolution
from automarkov.contracts.remote_env import DiscreteSpace
from automarkov.domain.canonical import canonical_json_bytes
from automarkov.lifecycle import ArtifactReference, ExecutionAttestation
from automarkov.remote_env import RemoteEnvRuntimeUnavailable
from automarkov.security.provenance import RuntimeProfileManifest
from automarkov.suite_adapters import (
    SINGLE_AGENT_SUITE_ADAPTER,
    IntegerResetSeedContract,
    RemoteGymnasiumEnv,
    SingleAgentSuiteLifecycle,
    TaxiDenyMatrix,
    TaxiGeneratedBackend,
    TaxiRunnerBoundFactory,
    TaxiSuiteAdapterManifest,
    build_single_agent_suite_profile_contract,
    formal_single_agent_suite_readiness,
    resolve_profile_remote_env,
    suite_adapter_parent_references,
)

ROOT = Path(__file__).resolve().parents[2]


def _ref(name: str, digit: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=f"artifact_{sha256(name.encode()).hexdigest()}",
        payload_hash=f"sha256:{digit * 64}",
    )


def _manifest() -> TaxiSuiteAdapterManifest:
    return TaxiSuiteAdapterManifest(
        schema_version="automarkov.single-agent-suite-adapter.v1",
        suite_id="taxi_mdp",
        implementation_plan=_ref("plan", "1"),
        decision_process_spec=_ref("spec", "2"),
        signed_suite_manifest=_ref("suite", "3"),
        candidate_bundle=_ref("candidate", "4"),
        runtime_profile_manifest=_ref("profile", "5"),
        official_provenance=_ref("taxi-provenance", "7"),
        adapter_id="adapter_taxi_generated_v1",
        adapter_source_hash="sha256:" + "6" * 64,
        candidate_source_attestation=_ref("taxi-candidate-source", "6"),
        candidate_source_hash="sha256:" + "6" * 64,
        runtime_profile_id="rllib-taxi-synthesis",
        protocol_version="automarkov.remote-env.v1",
        frame_schema_hash="sha256:" + "7" * 64,
        space_adapter_registry_hash="sha256:" + "8" * 64,
        seed_contract=IntegerResetSeedContract(kind="integer_reset", seed=17),
        route="generate",
        source_mode="SYNTHESIS",
        environment_id="generated_taxi_candidate",
        materialization="candidate_bundle_only",
        observation_space=DiscreteSpace(kind="Discrete", n=500, start=0, dtype="int64"),
        action_space=DiscreteSpace(kind="Discrete", n=6, start=0, dtype="int64"),
    )


class _GeneratedTaxi:
    def __init__(self) -> None:
        self.state = 0
        self.close_count = 0

    def reset(
        self, *, seed: int, options: Mapping[str, object] | None
    ) -> tuple[object, Mapping[str, object]]:
        del options
        self.state = seed % 500
        return self.state, {"seed": seed}

    def step(
        self, action: object
    ) -> tuple[object, float, bool, bool, Mapping[str, object]]:
        assert type(action) is int
        self.state = (self.state + action) % 500
        return self.state, -1.0, False, False, {}

    def close(self) -> None:
        self.close_count += 1


def _runner_factory(environment: _GeneratedTaxi) -> TaxiRunnerBoundFactory:
    signing_key = Ed25519PrivateKey.generate()
    denial_matrix = TaxiDenyMatrix(
        source_file_read_denial=_ref("taxi-source-denial", "9"),
        bytecode_read_denial=_ref("taxi-bytecode-denial", "a"),
        direct_import_denial=_ref("taxi-import-denial", "b"),
        find_spec_denial=_ref("taxi-find-spec-denial", "c"),
        resource_lookup_denial=_ref("taxi-resource-denial", "d"),
        wheel_read_denial=_ref("taxi-wheel-denial", "e"),
        sdist_read_denial=_ref("taxi-sdist-denial", "f"),
        package_cache_discovery_denial=_ref("taxi-cache-denial", "0"),
    )
    outputs = sorted(
        (
            _manifest().candidate_bundle,
            _manifest().candidate_source_attestation,
            *denial_matrix.references,
        ),
        key=lambda item: item.artifact_id.encode("utf-8"),
    )
    job_manifest = _ref("taxi-runner-job", "1")
    fields: dict[str, object] = {
        "schema_version": "automarkov.execution-attestation.v1",
        "signing_domain": "AutoMarkov-Execution-Attestation-v1",
        "experiment_id": "experiment_taxi",
        "run_id": "run_taxi",
        "job_id": "job_taxi_denial_preflight",
        "process_execution_id": "execution_taxi_denial_preflight",
        "profile_id": "rllib-taxi-synthesis",
        "principal_id": "principal_fixed_commit_runner",
        "job_manifest": job_manifest.model_dump(mode="json"),
        "process_terminal_record": _ref("taxi-process", "2").model_dump(mode="json"),
        "payload_outputs": [item.model_dump(mode="json") for item in outputs],
        "output_scan_report": None,
        "terminal_result": None,
        "network_policy_hash": "sha256:" + "3" * 64,
        "mount_table_hash": "sha256:" + "4" * 64,
        "capability_decision_log_hash": "sha256:" + "5" * 64,
        "actual_phase_transition": {
            "from_phase": "taxi_denial_preflight",
            "to_phase": "taxi_candidate_factory_ready",
            "transitioned_at": "2026-08-12T00:00:00Z",
        },
        "egress_decision_log_hash": "sha256:" + "0" * 64,
        "egress_revoked_at": "2026-08-12T00:00:00Z",
        "issued_at": "2026-08-12T00:00:00Z",
        "nonce_b64url": "A" * 21 + "w",
        "signing_key_id": "key_fixed_commit_runner",
        "signature_algorithm": "Ed25519",
    }
    fields["signature_b64url"] = (
        base64.urlsafe_b64encode(signing_key.sign(canonical_json_bytes(fields)))
        .decode("ascii")
        .rstrip("=")
    )
    attestation = ExecutionAttestation.model_validate(fields, strict=True)
    return TaxiRunnerBoundFactory.for_test(
        manifest=_manifest(),
        execution_attestation=attestation,
        trusted_runner_keys={"key_fixed_commit_runner": signing_key.public_key()},
        expected_runner_principal_id="principal_fixed_commit_runner",
        expected_job_manifest=job_manifest,
        denial_matrix=denial_matrix,
        environment_builder=lambda: environment,
    )


def test_taxi_manifest_is_generate_only_and_has_a_closed_parent_dag() -> None:
    manifest = _manifest()
    assert (
        type(
            SINGLE_AGENT_SUITE_ADAPTER.validate_python(
                manifest.model_dump(mode="json", round_trip=True, warnings="error"),
                strict=True,
            )
        )
        is TaxiSuiteAdapterManifest
    )
    assert manifest.observation_space.n == 500
    assert manifest.action_space.n == 6
    assert suite_adapter_parent_references(manifest) == tuple(
        sorted(
            (
                manifest.implementation_plan,
                manifest.decision_process_spec,
                manifest.signed_suite_manifest,
                manifest.candidate_bundle,
                manifest.runtime_profile_manifest,
                manifest.candidate_source_attestation,
                manifest.official_provenance,
            ),
            key=lambda item: item.artifact_id.encode("utf-8"),
        )
    )

    raw = manifest.model_dump(mode="json", round_trip=True, warnings="error")
    for forbidden_field, value in (
        ("official_taxi_environment", "Taxi-v4"),
        ("official_transition_table", [[0, 1]]),
        ("import_path", "gymnasium.envs.toy_text.taxi:TaxiEnv"),
        ("source_path", "gymnasium/envs/toy_text/taxi.py"),
    ):
        candidate = dict(raw)
        candidate[forbidden_field] = value
        with pytest.raises(ValidationError, match="Extra inputs"):
            TaxiSuiteAdapterManifest.model_validate(candidate, strict=True)

    assert formal_single_agent_suite_readiness(
        manifest, image_status="recipe_frozen"
    ) == (
        "WAITING_RUNTIME",
        "formal suite execution requires a built resolver-verified runtime profile",
    )


def test_taxi_worker_and_single_remote_gymnasium_adapter_are_seed_reproducible() -> (
    None
):
    environment = _GeneratedTaxi()
    factory = _runner_factory(environment)
    backend = TaxiGeneratedBackend(manifest=_manifest(), factory=factory)
    lifecycle = SingleAgentSuiteLifecycle(backend=backend)
    assert not hasattr(lifecycle, "exchange")
    adapter = RemoteGymnasiumEnv(lifecycle=lifecycle)

    first, first_info = adapter.reset(seed=17)
    adapter.step(2)
    second, second_info = adapter.reset(seed=17)

    assert first == second == 17
    assert first_info == second_info == {"seed": 17}
    assert adapter.action_space == DiscreteSpace(
        kind="Discrete", n=6, start=0, dtype="int64"
    )

    adapter.close()
    adapter.close()
    assert environment.close_count == 1


def test_taxi_rejects_direct_unattested_environment_injection() -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'environment'"):
        TaxiGeneratedBackend(
            manifest=_manifest(),
            environment=_GeneratedTaxi(),  # pyright: ignore[reportCallIssue]
        )


def test_taxi_denial_matrix_and_candidate_source_are_exactly_attested() -> None:
    environment = _GeneratedTaxi()
    factory = _runner_factory(environment)
    assert factory.manifest.candidate_source_attestation.payload_hash == (
        factory.manifest.candidate_source_hash
    )

    with pytest.raises(TypeError, match="unexpected keyword argument|missing"):
        TaxiRunnerBoundFactory(  # pyright: ignore[reportCallIssue]
            manifest=_manifest(),
            environment_builder=lambda: environment,
        )

    raw = factory.denial_matrix.model_dump(
        mode="json", round_trip=True, warnings="error"
    )
    raw["package_cache_discovery_denial"] = raw["source_file_read_denial"]
    with pytest.raises(ValidationError, match="unique"):
        TaxiDenyMatrix.model_validate(raw, strict=True)


def test_taxi_production_profile_composition_rejects_fakes_and_waits_for_build() -> (
    None
):
    profile = RuntimeProfileManifest.model_validate(
        json.loads((ROOT / "profiles/rllib-taxi-synthesis/profile.json").read_text()),
        strict=True,
    )
    profile_ref = ArtifactReference(
        artifact_id=f"artifact_{profile.manifest_hash.removeprefix('sha256:')}",
        payload_hash=profile.manifest_hash,
    )
    manifest = _manifest().model_copy(update={"runtime_profile_manifest": profile_ref})
    contract = build_single_agent_suite_profile_contract(manifest, profile=profile)
    resolution = RuntimeProfileResolution(
        schema_version="automarkov.runtime-profile-resolution.v1",
        profile_id=profile.profile_id,
        profile_manifest=profile_ref,
        lock_hash=profile.lock_hash,
        image_status="recipe_frozen",
        image_digest=None,
        platform=None,
        build_attestation=None,
        import_smoke_attestation=None,
    )

    with pytest.raises(ValueError, match="real TLS RemoteEnv"):
        resolve_profile_remote_env(
            contract,
            profile=profile,
            resolution=resolution,
            worker_attestation=None,
            trusted_worker_keys={},
            transport=object(),  # pyright: ignore[reportArgumentType]
        )
    with pytest.raises(RemoteEnvRuntimeUnavailable, match="WAITING_RUNTIME"):
        resolve_profile_remote_env(
            contract,
            profile=profile,
            resolution=resolution,
            worker_attestation=None,
            trusted_worker_keys={},
            transport=None,
        )
