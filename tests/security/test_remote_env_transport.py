from __future__ import annotations

import base64
import inspect
import socket
import ssl
import struct
import threading
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from automarkov.contracts.remote_env import (
    RemoteEnvCertificateIdentity,
    RemoteEnvFrameHeader,
    RemoteEnvHandshake,
    RemoteEnvRunnerGrantPolicy,
    RemoteEnvSessionTranscript,
    RemoteEnvTlsEndpoint,
)
from automarkov.domain.canonical import parse_json_payload
from automarkov.domain.models import Sha256Digest
from automarkov.remote_env import (
    REMOTE_ENV_GRANT_SCHEMA_HASH,
    RemoteEnvRuntimeUnavailable,
    RemoteEnvSecurityError,
    TlsSocketRemoteEnv,
    TlsSocketRemoteEnvServer,
    formal_remote_env_readiness,
    remote_env_certificate_identity_uri,
    remote_env_session_id,
    sign_remote_env_grant,
)
from automarkov.remote_env_codec import decode_remote_env_frame, encode_remote_env_frame

_DIGEST = "sha256:" + "1" * 64
_NONCE = base64.urlsafe_b64encode(bytes(32)).decode("ascii").rstrip("=")
_NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


def _identity(*, server: bool) -> RemoteEnvCertificateIdentity:
    return RemoteEnvCertificateIdentity.model_validate(
        {
            "domain": "AutoMarkov-RemoteEnv-Certificate-Identity-v1",
            "experiment_id": "experiment_tls",
            "run_id": "run_tls",
            "process_execution_id": "execution_worker"
            if server
            else "execution_client",
            "profile_id": "env-cartpole" if server else "rllib-core",
            "principal_id": "principal_worker" if server else "principal_actor",
        },
        strict=True,
    )


def _leaf(
    *,
    ca: x509.Certificate,
    ca_key: Ed25519PrivateKey,
    ca_ski: x509.SubjectKeyIdentifier,
    identity: RemoteEnvCertificateIdentity,
    server: bool,
    serial: int,
) -> tuple[Ed25519PrivateKey, x509.Certificate]:
    key = Ed25519PrivateKey.generate()
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([]))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(serial)
        .not_valid_before(_NOW - timedelta(days=1))
        .not_valid_after(_NOW + timedelta(days=1))
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
            x509.ExtendedKeyUsage(
                [
                    ExtendedKeyUsageOID.SERVER_AUTH
                    if server
                    else ExtendedKeyUsageOID.CLIENT_AUTH
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
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
    return key, certificate


def _write_material(path: Path, payload: bytes, *, private_key: bool = False) -> str:
    path.write_bytes(payload)
    path.chmod(0o600 if private_key else 0o644)
    return str(path.resolve())


def _receive_frame(channel: ssl.SSLSocket) -> bytes:
    prefix = channel.recv(8)
    assert len(prefix) == 8
    header_length = struct.unpack(">Q", prefix)[0]
    header = bytearray()
    while len(header) < header_length:
        header.extend(channel.recv(header_length - len(header)))
    parsed = parse_json_payload(bytes(header))
    assert isinstance(parsed, dict)
    validated = RemoteEnvFrameHeader.model_validate(parsed, strict=True)
    tensor_length = sum(item.nbytes for item in validated.tensors)
    tensors = bytearray()
    while len(tensors) < tensor_length:
        tensors.extend(channel.recv(tensor_length - len(tensors)))
    return prefix + bytes(header) + bytes(tensors)


def test_tls_socket_remote_env_authenticates_before_exchanging_frames(
    tmp_path: Path,
) -> None:
    ca_key = Ed25519PrivateKey.generate()
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "runner CA")])
    ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(1)
        .not_valid_before(_NOW - timedelta(days=1))
        .not_valid_after(_NOW + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(ca_ski, critical=False)
        .sign(ca_key, algorithm=None)
    )
    client_identity = _identity(server=False)
    server_identity = _identity(server=True)
    client_serial = (1 << 158) + 2
    server_serial = (1 << 158) + 3
    client_key, client_certificate = _leaf(
        ca=ca,
        ca_key=ca_key,
        ca_ski=ca_ski,
        identity=client_identity,
        server=False,
        serial=client_serial,
    )
    server_key, server_certificate = _leaf(
        ca=ca,
        ca_key=ca_key,
        ca_ski=ca_ski,
        identity=server_identity,
        server=True,
        serial=server_serial,
    )
    ca_path = _write_material(tmp_path / "ca.pem", ca.public_bytes(Encoding.PEM))
    client_certificate_path = _write_material(
        tmp_path / "client.pem", client_certificate.public_bytes(Encoding.PEM)
    )
    client_key_path = _write_material(
        tmp_path / "client.key",
        client_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()),
        private_key=True,
    )
    server_certificate_path = _write_material(
        tmp_path / "server.pem", server_certificate.public_bytes(Encoding.PEM)
    )
    server_key_path = _write_material(
        tmp_path / "server.key",
        server_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()),
        private_key=True,
    )
    client_fingerprint = (
        "sha256:" + sha256(client_certificate.public_bytes(Encoding.DER)).hexdigest()
    )
    server_fingerprint = (
        "sha256:" + sha256(server_certificate.public_bytes(Encoding.DER)).hexdigest()
    )
    signing_key = Ed25519PrivateKey.generate()
    runner_policy = RemoteEnvRunnerGrantPolicy(
        schema_version="automarkov.remote-env-runner-grant-policy.v1",
        signing_key_id="key_runner",
        key_not_before="2026-08-11T00:00:00Z",
        key_expires_at="2026-08-14T00:00:00Z",
        key_revoked=False,
        run_not_before="2026-08-12T00:00:00Z",
        run_expires_at="2026-08-13T00:00:00Z",
        clock_skew_seconds=5,
        grant_schema_hash=Sha256Digest(root=REMOTE_ENV_GRANT_SCHEMA_HASH),
    )

    def issue_grant(session_id: str):
        return sign_remote_env_grant(
            {
                "schema_version": "automarkov.remote-env-capability.v1",
                "signing_domain": "AutoMarkov-RemoteEnv-Capability-v1",
                "grant_id": "grant_tls",
                "experiment_id": "experiment_tls",
                "run_id": "run_tls",
                "session_id": session_id,
                "profile_graph_hash": _DIGEST,
                "source_process_execution_id": client_identity.process_execution_id,
                "source_profile_id": client_identity.profile_id,
                "source_principal_id": client_identity.principal_id,
                "target_process_execution_id": server_identity.process_execution_id,
                "target_profile_id": server_identity.profile_id,
                "environment_id": "CartPole-v1",
                "role": "actor",
                "allowed_methods": ["Describe"],
                "max_sequence": 2,
                "max_step": 1,
                "max_frame_bytes": 100_000,
                "max_tensor_bytes": 10_000,
                "not_before": "2026-08-12T00:00:00Z",
                "expires_at": "2026-08-13T00:00:00Z",
                "nonce_b64url": _NONCE,
                "signing_key_id": "key_runner",
            },
            signing_key,
        )

    def build_handshake(transcript: RemoteEnvSessionTranscript) -> RemoteEnvHandshake:
        return RemoteEnvHandshake.model_validate(
            {
                "protocol_version": "automarkov.remote-env.v1",
                "run_id": "run_tls",
                "session_id": remote_env_session_id(transcript),
                "process_execution_id": server_identity.process_execution_id,
                "profile_id": server_identity.profile_id,
                "principal_id": server_identity.principal_id,
                "profile_lock_hash": "sha256:" + "4" * 64,
                "image_digest": "sha256:" + "5" * 64,
                "peer_certificate_fingerprints": {
                    "client": client_fingerprint,
                    "server": server_fingerprint,
                },
                "profile_graph_hash": _DIGEST,
                "environment_id": "CartPole-v1",
                "environment_repository_commit": "4f74260de0413812cab0680921163083a948a4f2",
                "observation_spaces": {},
                "action_spaces": {},
                "supports_parallel": False,
                "supports_aec": False,
                "supports_state": False,
                "seed_contract": {"kind": "integer_reset", "seed": 0},
            },
            strict=True,
        )

    class EchoWorker:
        def __init__(self) -> None:
            self.call_count = 0

        def exchange(self, canonical_frame: bytes) -> bytes:
            self.call_count += 1
            return canonical_frame

    echo_worker = EchoWorker()
    server = TlsSocketRemoteEnvServer(
        runner_ca_path=ca_path,
        server_certificate_path=server_certificate_path,
        server_private_key_path=server_key_path,
        expected_client_identity=client_identity,
        expected_server_identity=server_identity,
        expected_client_fingerprint=client_fingerprint,
        expected_client_serial=client_serial,
        client_certificate_revoked=False,
        expected_server_fingerprint=server_fingerprint,
        expected_server_serial=server_serial,
        server_certificate_revoked=False,
        profile_graph_hash=_DIGEST,
        expected_profile_lock_hash="sha256:" + "4" * 64,
        expected_image_digest="sha256:" + "5" * 64,
        expected_environment_id="CartPole-v1",
        grant_signing_key_id="key_runner",
        grant_signing_public_key=signing_key.public_key(),
        runner_grant_policy=runner_policy,
        expected_role="actor",
        grant_issuer=issue_grant,
        handshake_builder=build_handshake,
        worker_factory=lambda _grant, _transcript: echo_worker,
        now_provider=lambda: _NOW,
    )
    grant_for_request: list[object] = []

    def request_for(grant: object) -> bytes:
        request_header = RemoteEnvFrameHeader.model_validate(
            {
                "codec_version": "automarkov.remote-env-frame.v1",
                "message_kind": "Describe",
                "envelope": {
                    "protocol_version": "automarkov.remote-env.v1",
                    "run_id": "run_tls",
                    "session_id": grant.session_id,  # type: ignore[attr-defined]
                    "source_process_execution_id": client_identity.process_execution_id,
                    "source_profile_id": client_identity.profile_id,
                    "source_principal_id": client_identity.principal_id,
                    "target_process_execution_id": server_identity.process_execution_id,
                    "target_profile_id": server_identity.profile_id,
                    "sequence": 1,
                    "step_id": 0,
                    "grant": grant,
                },
                "payload": {},
                "tensors": [],
            },
            strict=True,
        )
        return encode_remote_env_frame(request_header, {})

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    server_errors: list[BaseException] = []

    def serve() -> None:
        try:
            raw, _ = listener.accept()
            server.serve_connection(raw)
        except (AssertionError, OSError, ssl.SSLError, ValueError) as error:
            server_errors.append(error)
        finally:
            listener.close()

    thread = threading.Thread(target=serve)
    thread.start()
    transport = TlsSocketRemoteEnv(
        endpoint=RemoteEnvTlsEndpoint(
            schema_version="automarkov.remote-env-tls-endpoint.v1",
            host="127.0.0.1",
            port=port,
            server_name=None,
        ),
        runner_ca_path=ca_path,
        client_certificate_path=client_certificate_path,
        client_private_key_path=client_key_path,
        expected_client_identity=client_identity,
        expected_server_identity=server_identity,
        profile_graph_hash=_DIGEST,
        expected_environment_id="CartPole-v1",
        grant_signing_key_id="key_runner",
        grant_signing_public_key=signing_key.public_key(),
        runner_grant_policy=runner_policy,
        expected_role="actor",
        expected_client_fingerprint=client_fingerprint,
        expected_client_serial=client_serial,
        client_certificate_revoked=False,
        expected_server_fingerprint=server_fingerprint,
        expected_server_serial=server_serial,
        server_certificate_revoked=False,
        expected_profile_lock_hash="sha256:" + "4" * 64,
        expected_image_digest="sha256:" + "5" * 64,
        now_provider=lambda: _NOW,
    )

    transport.connect()
    assert transport.grant is not None
    grant_for_request.append(transport.grant)
    request = request_for(transport.grant)
    assert transport.exchange(request) == request
    assert formal_remote_env_readiness("built", transport=transport)[0] == "READY"
    with pytest.raises(RemoteEnvSecurityError):
        transport.exchange(request)
    assert echo_worker.call_count == 1
    with pytest.raises(ValueError, match="terminal"):
        transport.connect()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(server_errors) == 1
    assert isinstance(server_errors[0], RemoteEnvSecurityError)
    assert "sequence" in str(server_errors[0])

    class WrongSequenceWorker:
        def exchange(self, canonical_frame: bytes) -> bytes:
            decoded = decode_remote_env_frame(
                canonical_frame, max_frame_bytes=100_000, max_tensor_bytes=10_000
            )
            payload = decoded.header.model_dump(mode="json")
            payload["envelope"]["sequence"] += 1
            return encode_remote_env_frame(
                RemoteEnvFrameHeader.model_validate(payload, strict=True), {}
            )

    bad_server = TlsSocketRemoteEnvServer(
        runner_ca_path=ca_path,
        server_certificate_path=server_certificate_path,
        server_private_key_path=server_key_path,
        expected_client_identity=client_identity,
        expected_server_identity=server_identity,
        expected_client_fingerprint=client_fingerprint,
        expected_client_serial=client_serial,
        client_certificate_revoked=False,
        expected_server_fingerprint=server_fingerprint,
        expected_server_serial=server_serial,
        server_certificate_revoked=False,
        profile_graph_hash=_DIGEST,
        expected_profile_lock_hash="sha256:" + "4" * 64,
        expected_image_digest="sha256:" + "5" * 64,
        expected_environment_id="CartPole-v1",
        grant_signing_key_id="key_runner",
        grant_signing_public_key=signing_key.public_key(),
        runner_grant_policy=runner_policy,
        expected_role="actor",
        grant_issuer=issue_grant,
        handshake_builder=build_handshake,
        worker_factory=lambda _grant, _transcript: WrongSequenceWorker(),
        now_provider=lambda: _NOW,
    )
    bad_listener = socket.socket()
    bad_listener.bind(("127.0.0.1", 0))
    bad_listener.listen(1)
    bad_port = bad_listener.getsockname()[1]
    bad_server_errors: list[BaseException] = []

    def serve_bad_response() -> None:
        try:
            raw, _ = bad_listener.accept()
            bad_server.serve_connection(raw)
        except BaseException as error:  # noqa: BLE001 - thread evidence is asserted
            bad_server_errors.append(error)
        finally:
            bad_listener.close()

    bad_thread = threading.Thread(target=serve_bad_response)
    bad_thread.start()
    bad_transport = TlsSocketRemoteEnv(
        endpoint=RemoteEnvTlsEndpoint(
            schema_version="automarkov.remote-env-tls-endpoint.v1",
            host="127.0.0.1",
            port=bad_port,
            server_name=None,
        ),
        runner_ca_path=ca_path,
        client_certificate_path=client_certificate_path,
        client_private_key_path=client_key_path,
        expected_client_identity=client_identity,
        expected_server_identity=server_identity,
        profile_graph_hash=_DIGEST,
        expected_environment_id="CartPole-v1",
        grant_signing_key_id="key_runner",
        grant_signing_public_key=signing_key.public_key(),
        runner_grant_policy=runner_policy,
        expected_role="actor",
        expected_client_fingerprint=client_fingerprint,
        expected_client_serial=client_serial,
        client_certificate_revoked=False,
        expected_server_fingerprint=server_fingerprint,
        expected_server_serial=server_serial,
        server_certificate_revoked=False,
        expected_profile_lock_hash="sha256:" + "4" * 64,
        expected_image_digest="sha256:" + "5" * 64,
        now_provider=lambda: _NOW,
    )
    bad_transport.connect()
    assert bad_transport.grant is not None
    with pytest.raises(RemoteEnvSecurityError):
        bad_transport.exchange(request_for(bad_transport.grant))
    with pytest.raises(RemoteEnvSecurityError, match="terminal"):
        bad_transport.connect()
    bad_thread.join(timeout=5)
    assert not bad_thread.is_alive()
    assert len(bad_server_errors) == 1
    assert isinstance(bad_server_errors[0], RemoteEnvSecurityError)

    with pytest.raises(RemoteEnvRuntimeUnavailable) as error:
        TlsSocketRemoteEnv(
            endpoint=RemoteEnvTlsEndpoint(
                schema_version="automarkov.remote-env-tls-endpoint.v1",
                host="127.0.0.1",
                port=port,
                server_name=None,
            ),
            runner_ca_path=str((tmp_path / "missing-ca.pem").resolve()),
            client_certificate_path=client_certificate_path,
            client_private_key_path=client_key_path,
            expected_client_identity=client_identity,
            expected_server_identity=server_identity,
            profile_graph_hash=_DIGEST,
            expected_environment_id="CartPole-v1",
            grant_signing_key_id="key_runner",
            grant_signing_public_key=signing_key.public_key(),
            runner_grant_policy=runner_policy,
            expected_role="actor",
            expected_client_fingerprint=client_fingerprint,
            expected_client_serial=client_serial,
            client_certificate_revoked=False,
            expected_server_fingerprint=server_fingerprint,
            expected_server_serial=server_serial,
            server_certificate_revoked=False,
            expected_profile_lock_hash="sha256:" + "4" * 64,
            expected_image_digest="sha256:" + "5" * 64,
            now_provider=lambda: _NOW,
        )
    assert error.value.state == "WAITING_RUNTIME"
    assert "transcript" not in inspect.signature(TlsSocketRemoteEnv).parameters
    assert "nonce_provider" not in inspect.signature(TlsSocketRemoteEnv).parameters
