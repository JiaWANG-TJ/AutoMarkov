from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from automarkov.contracts.remote_env import (
    RemoteEnvCertificateIdentity,
    RemoteEnvFrameHeader,
    RemoteEnvHandshake,
    RemoteEnvSessionTranscript,
)
from automarkov.remote_env import (
    RemoteEnvGrantVerifier,
    parse_remote_env_certificate_identity,
    remote_env_certificate_identity_uri,
    remote_env_session_id,
    sign_remote_env_grant,
    verify_remote_env_handshake,
    verify_remote_env_leaf_certificate,
)

_DIGEST = "sha256:" + "1" * 64
_SESSION = "sha256:" + "2" * 64
_NONCE = base64.urlsafe_b64encode(bytes(32)).decode("ascii").rstrip("=")


def _identity(*, server: bool = False) -> RemoteEnvCertificateIdentity:
    return RemoteEnvCertificateIdentity.model_validate(
        {
            "domain": "AutoMarkov-RemoteEnv-Certificate-Identity-v1",
            "experiment_id": "experiment_identity",
            "run_id": "run_identity",
            "process_execution_id": "execution_worker"
            if server
            else "execution_client",
            "profile_id": "env-cartpole" if server else "rllib-core",
            "principal_id": "principal_worker" if server else "principal_actor",
        },
        strict=True,
    )


def _grant_fields() -> dict[str, object]:
    return {
        "schema_version": "automarkov.remote-env-capability.v1",
        "signing_domain": "AutoMarkov-RemoteEnv-Capability-v1",
        "grant_id": "grant_identity",
        "experiment_id": "experiment_identity",
        "run_id": "run_identity",
        "session_id": _SESSION,
        "profile_graph_hash": _DIGEST,
        "source_process_execution_id": "execution_client",
        "source_profile_id": "rllib-core",
        "source_principal_id": "principal_actor",
        "target_process_execution_id": "execution_worker",
        "target_profile_id": "env-cartpole",
        "environment_id": "CartPole-v1",
        "role": "actor",
        "allowed_methods": ["Close", "Describe", "Reset", "Step"],
        "max_sequence": 10,
        "max_step": 10,
        "max_frame_bytes": 100_000,
        "max_tensor_bytes": 10_000,
        "not_before": "2026-08-12T00:00:00Z",
        "expires_at": "2026-08-13T00:00:00Z",
        "nonce_b64url": _NONCE,
        "signing_key_id": "key_runner",
    }


def _request_header(grant: object, *, sequence: int) -> RemoteEnvFrameHeader:
    return RemoteEnvFrameHeader.model_validate(
        {
            "codec_version": "automarkov.remote-env-frame.v1",
            "message_kind": "Describe",
            "envelope": {
                "protocol_version": "automarkov.remote-env.v1",
                "run_id": "run_identity",
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


def test_certificate_identity_and_session_transcript_are_canonical() -> None:
    client = _identity()
    server = _identity(server=True)
    uri = remote_env_certificate_identity_uri(client)

    assert parse_remote_env_certificate_identity(uri, expected=client) == client
    with pytest.raises(ValueError, match="canonical"):
        parse_remote_env_certificate_identity(uri + "=", expected=client)

    transcript = RemoteEnvSessionTranscript.model_validate(
        {
            "domain": "AutoMarkov-RemoteEnv-Session-v1",
            "protocol_version": "automarkov.remote-env.v1",
            "experiment_id": "experiment_identity",
            "run_id": "run_identity",
            "profile_graph_hash": _DIGEST,
            "client": {
                "process_execution_id": client.process_execution_id,
                "profile_id": client.profile_id,
                "principal_id": client.principal_id,
                "certificate_fingerprint": _DIGEST,
                "nonce_b64url": _NONCE,
            },
            "server": {
                "process_execution_id": server.process_execution_id,
                "profile_id": server.profile_id,
                "principal_id": server.principal_id,
                "certificate_fingerprint": "sha256:" + "3" * 64,
                "nonce_b64url": base64.urlsafe_b64encode(bytes([1]) * 32)
                .decode("ascii")
                .rstrip("="),
            },
        },
        strict=True,
    )

    assert remote_env_session_id(transcript).startswith("sha256:")
    assert remote_env_session_id(transcript) == remote_env_session_id(transcript)
    handshake = RemoteEnvHandshake.model_validate(
        {
            "protocol_version": "automarkov.remote-env.v1",
            "run_id": "run_identity",
            "session_id": remote_env_session_id(transcript),
            "process_execution_id": server.process_execution_id,
            "profile_id": server.profile_id,
            "principal_id": server.principal_id,
            "profile_lock_hash": "sha256:" + "4" * 64,
            "image_digest": "sha256:" + "5" * 64,
            "peer_certificate_fingerprints": {
                "client": transcript.client.certificate_fingerprint,
                "server": transcript.server.certificate_fingerprint,
            },
            "profile_graph_hash": _DIGEST,
            "environment_id": "CartPole-v1",
            "environment_repository_commit": "4f74260de0413812cab0680921163083a948a4f2",
            "observation_spaces": {
                "agent": {
                    "kind": "Box",
                    "shape": [4],
                    "dtype": "float32",
                    "low_tensor_id": "tensor_low",
                    "high_tensor_id": "tensor_high",
                }
            },
            "action_spaces": {
                "agent": {"kind": "Discrete", "n": 2, "start": 0, "dtype": "int64"}
            },
            "supports_parallel": False,
            "supports_aec": False,
            "supports_state": False,
            "seed_contract": {"kind": "integer_reset", "seed": 0},
        },
        strict=True,
    )
    verify_remote_env_handshake(
        handshake,
        transcript,
        expected_client_identity=client,
        expected_server_identity=server,
        expected_profile_lock_hash="sha256:" + "4" * 64,
        expected_image_digest="sha256:" + "5" * 64,
        expected_environment_id="CartPole-v1",
    )

    mismatched_transcript = RemoteEnvSessionTranscript.model_validate(
        {
            **transcript.model_dump(mode="json"),
            "client": {
                **transcript.client.model_dump(mode="json"),
                "principal_id": "principal_wrong_client",
            },
        },
        strict=True,
    )
    with pytest.raises(ValueError, match="authenticated profile graph"):
        verify_remote_env_handshake(
            handshake,
            mismatched_transcript,
            expected_client_identity=client,
            expected_server_identity=server,
            expected_profile_lock_hash="sha256:" + "4" * 64,
            expected_image_digest="sha256:" + "5" * 64,
            expected_environment_id="CartPole-v1",
        )


def test_signed_grant_replay_revokes_the_session() -> None:
    signing_key = Ed25519PrivateKey.generate()
    grant = sign_remote_env_grant(_grant_fields(), signing_key)
    verifier = RemoteEnvGrantVerifier(
        signing_key_id="key_runner",
        signing_public_key=signing_key.public_key(),
        session_id=_SESSION,
        source_identity=_identity(),
        target_identity=_identity(server=True),
        environment_id="CartPole-v1",
        profile_graph_hash=_DIGEST,
        expected_role="actor",
    )
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)

    verifier.verify_and_consume(_request_header(grant, sequence=1), now=now)
    with pytest.raises(ValueError, match="sequence"):
        verifier.verify_and_consume(_request_header(grant, sequence=1), now=now)

    assert verifier.revoked is True
    with pytest.raises(ValueError, match="revoked"):
        verifier.verify_and_consume(_request_header(grant, sequence=2), now=now)


def test_signed_grant_binds_the_exact_experiment_of_both_certificate_peers() -> None:
    signing_key = Ed25519PrivateKey.generate()
    fields = _grant_fields()
    fields["experiment_id"] = "experiment_other"
    grant = sign_remote_env_grant(fields, signing_key)
    verifier = RemoteEnvGrantVerifier(
        signing_key_id="key_runner",
        signing_public_key=signing_key.public_key(),
        session_id=_SESSION,
        source_identity=_identity(),
        target_identity=_identity(server=True),
        environment_id="CartPole-v1",
        profile_graph_hash=_DIGEST,
        expected_role="actor",
    )

    with pytest.raises(ValueError, match="peer, profile, or session"):
        verifier.verify_and_consume(
            _request_header(grant, sequence=1),
            now=datetime(2026, 8, 12, 12, tzinfo=UTC),
        )


def test_ed25519_leaf_profile_binds_the_exact_san_identity() -> None:
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    ca_key = Ed25519PrivateKey.generate()
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "AutoMarkov runner CA")]
    )
    ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(ca_ski, critical=False)
        .sign(ca_key, algorithm=None)
    )
    leaf_key = Ed25519PrivateKey.generate()
    identity = _identity()
    leaf_serial = (1 << 158) + 2
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([]))
        .issuer_name(ca.subject)
        .public_key(leaf_key.public_key())
        .serial_number(leaf_serial)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,  # type: ignore[arg-type]
                decipher_only=None,  # type: ignore[arg-type]
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.UniformResourceIdentifier(
                        remote_env_certificate_identity_uri(identity)
                    )
                ]
            ),
            critical=True,
        )
        .sign(ca_key, algorithm=None)
    )
    fingerprint = (
        "sha256:"
        + __import__("hashlib").sha256(leaf.public_bytes(Encoding.DER)).hexdigest()
    )

    verify_remote_env_leaf_certificate(
        leaf,
        ca,
        expected_identity=identity,
        peer_role="client",
        expected_fingerprint=fingerprint,
        expected_serial=leaf_serial,
        revoked=False,
        now=now,
    )

    with pytest.raises(ValueError, match="identity"):
        verify_remote_env_leaf_certificate(
            leaf,
            ca,
            expected_identity=_identity(server=True),
            peer_role="client",
            expected_fingerprint=fingerprint,
            expected_serial=leaf_serial,
            revoked=False,
            now=now,
        )
