from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automarkov.contracts.remote_env import (
    RemoteEnvCertificateIdentity,
    RemoteEnvFrameHeader,
)
from automarkov.remote_env import (
    REMOTE_ENV_GRANT_SCHEMA_HASH,
    RemoteEnvGrantVerifier,
    sign_remote_env_grant,
)

_DIGEST = "sha256:" + "1" * 64
_SESSION = "sha256:" + "2" * 64
_NONCE = base64.urlsafe_b64encode(bytes(32)).decode().rstrip("=")
_NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


def _identity(*, server: bool, sealed: bool = False) -> RemoteEnvCertificateIdentity:
    return RemoteEnvCertificateIdentity.model_validate(
        {
            "domain": "AutoMarkov-RemoteEnv-Certificate-Identity-v1",
            "experiment_id": "experiment_security",
            "run_id": "run_security",
            "process_execution_id": "execution_worker"
            if server
            else "execution_client",
            "profile_id": (
                "sealed-env-taxi-gold"
                if server and sealed
                else "sealed-evaluator-rllib"
                if sealed
                else "env-cartpole"
                if server
                else "rllib-core"
            ),
            "principal_id": (
                "principal_sealed_worker"
                if server and sealed
                else "principal_sealed_evaluator"
                if sealed
                else "principal_worker"
                if server
                else "principal_actor"
            ),
        },
        strict=True,
    )


def _grant(signing_key: Ed25519PrivateKey) -> object:
    return sign_remote_env_grant(
        {
            "schema_version": "automarkov.remote-env-capability.v1",
            "signing_domain": "AutoMarkov-RemoteEnv-Capability-v1",
            "grant_id": "grant_security",
            "experiment_id": "experiment_security",
            "run_id": "run_security",
            "session_id": _SESSION,
            "profile_graph_hash": _DIGEST,
            "source_process_execution_id": "execution_client",
            "source_profile_id": "rllib-core",
            "source_principal_id": "principal_actor",
            "target_process_execution_id": "execution_worker",
            "target_profile_id": "env-cartpole",
            "environment_id": "CartPole-v1",
            "role": "actor",
            "allowed_methods": ["Describe"],
            "max_sequence": 4,
            "max_step": 1,
            "max_frame_bytes": 100_000,
            "max_tensor_bytes": 10_000,
            "not_before": "2026-08-12T11:00:00Z",
            "expires_at": "2026-08-12T13:00:00Z",
            "nonce_b64url": _NONCE,
            "signing_key_id": "key_runner",
        },
        signing_key,
    )


def _header(grant: object, sequence: int) -> RemoteEnvFrameHeader:
    return RemoteEnvFrameHeader.model_validate(
        {
            "codec_version": "automarkov.remote-env-frame.v1",
            "message_kind": "Describe",
            "envelope": {
                "protocol_version": "automarkov.remote-env.v1",
                "run_id": "run_security",
                "session_id": _SESSION,
                "source_process_execution_id": "execution_client",
                "source_profile_id": "rllib-core",
                "source_principal_id": "principal_actor",
                "target_process_execution_id": "execution_worker",
                "target_profile_id": "env-cartpole",
                "sequence": sequence,
                "step_id": 0,
                "grant": grant,
            },
            "payload": {},
            "tensors": [],
        },
        strict=True,
    )


def _verifier(
    signing_key: Ed25519PrivateKey,
    *,
    key_revoked: bool = False,
    schema_hash: str = REMOTE_ENV_GRANT_SCHEMA_HASH,
) -> RemoteEnvGrantVerifier:
    return RemoteEnvGrantVerifier(
        signing_key_id="key_runner",
        signing_public_key=signing_key.public_key(),
        session_id=_SESSION,
        source_identity=_identity(server=False),
        target_identity=_identity(server=True),
        environment_id="CartPole-v1",
        profile_graph_hash=_DIGEST,
        expected_role="actor",
        topology_kind="trainer_environment",
        runner_key_not_before=_NOW - timedelta(hours=2),
        runner_key_expires_at=_NOW + timedelta(hours=2),
        runner_key_revoked=key_revoked,
        run_not_before=_NOW - timedelta(hours=1),
        run_expires_at=_NOW + timedelta(hours=1),
        clock_skew_seconds=5,
        expected_grant_schema_hash=schema_hash,
    )


def test_runner_key_run_window_schema_and_exact_replay_key_fail_closed() -> None:
    signing_key = Ed25519PrivateKey.generate()
    grant = _grant(signing_key)
    verifier = _verifier(signing_key)

    verifier.verify_and_consume(_header(grant, 1), now=_NOW)
    verifier.verify_and_consume(_header(grant, 2), now=_NOW)
    with pytest.raises(ValueError, match="sequence"):
        verifier.verify_and_consume(_header(grant, 1), now=_NOW)
    assert verifier.revoked is True

    with pytest.raises(ValueError, match="runner signing key"):
        _verifier(signing_key, key_revoked=True).verify_and_consume(
            _header(grant, 1), now=_NOW
        )
    with pytest.raises(ValueError, match="schema hash"):
        _verifier(signing_key, schema_hash="sha256:" + "9" * 64)


def test_only_trainer_worker_and_sealed_evaluator_worker_topologies_are_legal() -> None:
    signing_key = Ed25519PrivateKey.generate()
    with pytest.raises(ValueError, match="topology"):
        RemoteEnvGrantVerifier(
            signing_key_id="key_runner",
            signing_public_key=signing_key.public_key(),
            session_id=_SESSION,
            source_identity=_identity(server=False),
            target_identity=_identity(server=True, sealed=True),
            environment_id="CartPole-v1",
            profile_graph_hash=_DIGEST,
            expected_role="actor",
            topology_kind="trainer_environment",
        )
