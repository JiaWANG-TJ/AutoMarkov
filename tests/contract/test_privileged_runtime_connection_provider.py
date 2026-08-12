from __future__ import annotations

import json
import os
import socket
import threading
from array import array
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automarkov.canonical import canonical_json_bytes
from automarkov.llm_contracts import (
    REQUIRED_RUNTIME_ROUTE_POLICY_HASH,
    RuntimeCurrentConnectionProof,
)
from automarkov.local_llm_runtime import (
    CurrentRuntimeConnectionIdentityError,
    PrivilegedUnixRuntimeConnectionProvider,
    RuntimeConnectionExpectation,
    RuntimeHttpRequestBinding,
)

_HOST_KEY = Ed25519PrivateKey.from_private_bytes(b"\x07" * 32)
_NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _digest(value: str) -> str:
    return "sha256:" + sha256(value.encode("utf-8")).hexdigest()


def _identity_hashes(server_port: int) -> tuple[str, str]:
    process = (
        "sha256:"
        + sha256(
            canonical_json_bytes(
                {
                    "domain": "AutoMarkov-Runtime-Process-Identity-v1",
                    "host_boot_id": "7539b5e6-fe36-4b8a-8ba2-ed6f0025de2b",
                    "network_namespace_inode": 101,
                    "owner_pid": 202,
                    "owner_start_ticks": 303,
                    "executable_identity_hash": _digest("executable"),
                    "startup_args_hash": _digest("argv"),
                }
            )
        ).hexdigest()
    )
    listener = (
        "sha256:"
        + sha256(
            canonical_json_bytes(
                {
                    "domain": "AutoMarkov-Runtime-Listener-Identity-v1",
                    "process_identity_hash": process,
                    "network_namespace_inode": 101,
                    "server_address": "127.0.0.1",
                    "server_port": server_port,
                    "listener_socket_inode": 404,
                }
            )
        ).hexdigest()
    )
    return listener, process


def _signed_proof(
    request: dict[str, object],
    connected: socket.socket,
    listener_hash: str,
    process_hash: str,
    *,
    observed_at: datetime = _NOW,
    accepted_socket_inode: int | None = None,
) -> RuntimeCurrentConnectionProof:
    client = connected.getsockname()
    server = connected.getpeername()
    payload: dict[str, object] = {
        "schema_version": "automarkov.runtime-current-connection-proof.v2",
        "signing_domain": "AutoMarkov-Runtime-Current-Connection-Proof-v2",
        "runtime_manifest_artifact_id": request["runtime_manifest_artifact_id"],
        "runtime_manifest_payload_hash": request["runtime_manifest_payload_hash"],
        "challenge": request["challenge"],
        "request_binding_hash": request["request_binding_hash"],
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "host_boot_id": "7539b5e6-fe36-4b8a-8ba2-ed6f0025de2b",
        "network_namespace_inode": 101,
        "listener_socket_inode": 404,
        "accepted_socket_inode": (
            os.fstat(connected.fileno()).st_ino
            if accepted_socket_inode is None
            else accepted_socket_inode
        ),
        "owner_pid": 202,
        "owner_start_ticks": 303,
        "executable_identity_hash": _digest("executable"),
        "startup_args_hash": _digest("argv"),
        "client_address": client[0],
        "client_port": client[1],
        "server_address": server[0],
        "server_port": server[1],
        "listener_identity_hash": listener_hash,
        "process_identity_hash": process_hash,
        "relay_identity_hash": request["relay_identity_hash"],
        "route_policy_hash": request["route_policy_hash"],
        "signature_algorithm": "Ed25519",
        "signing_key_id": "key_runtime_resolver",
        "signature": urlsafe_b64encode(b"\x00" * 64).decode().rstrip("="),
    }
    unsigned = RuntimeCurrentConnectionProof.model_validate(payload, strict=True)
    payload["signature"] = (
        urlsafe_b64encode(_HOST_KEY.sign(unsigned.signing_bytes())).decode().rstrip("=")
    )
    return RuntimeCurrentConnectionProof.model_validate(payload, strict=True)


def test_privileged_provider_uses_the_resolver_supplied_socket_for_the_request(
    tmp_path: Path,
) -> None:
    http_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    http_listener.bind(("127.0.0.1", 0))
    http_listener.listen(1)
    server_port = http_listener.getsockname()[1]
    listener_hash, process_hash = _identity_hashes(server_port)
    control_path = tmp_path / "runtime-resolver.sock"
    control_listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    control_listener.bind(control_path.as_posix())
    control_path.chmod(0o600)
    control_listener.listen(1)
    errors: list[BaseException] = []

    def serve_http() -> None:
        try:
            accepted, _ = http_listener.accept()
            with accepted:
                request = accepted.recv(8_192)
                assert request.startswith(b"GET /health HTTP/1.1")
                accepted.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: 2\r\nConnection: close\r\n\r\n{}"
                )
        except Exception as error:  # noqa: BLE001  # pragma: no cover - 线程错误重抛
            errors.append(error)

    def serve_control() -> None:
        try:
            control, _ = control_listener.accept()
            with control:
                request = json.loads(control.recv(64 * 1024))
                connected = socket.create_connection(("127.0.0.1", server_port))
                try:
                    proof = _signed_proof(
                        request,
                        connected,
                        listener_hash,
                        process_hash,
                    )
                    descriptor = array("i", [connected.fileno()])
                    control.sendmsg(
                        [canonical_json_bytes(proof.model_dump(mode="json"))],
                        [(socket.SOL_SOCKET, socket.SCM_RIGHTS, descriptor)],
                    )
                finally:
                    connected.close()
        except Exception as error:  # noqa: BLE001  # pragma: no cover - 线程错误重抛
            errors.append(error)

    http_thread = threading.Thread(target=serve_http)
    control_thread = threading.Thread(target=serve_control)
    http_thread.start()
    control_thread.start()
    provider = PrivilegedUnixRuntimeConnectionProvider(
        resolver_socket_path=control_path,
        expected_resolver_uid=os.getuid(),
        trusted_resolver_keys={"key_runtime_resolver": _HOST_KEY.public_key()},
        clock=lambda: _NOW,
    )
    manifest_id = "artifact_" + "a" * 64
    manifest_hash = _digest("manifest")
    binding = RuntimeHttpRequestBinding(
        method="GET",
        url=f"http://127.0.0.1:{server_port}/health",
        body_hash="sha256:" + sha256(b"").hexdigest(),
    )

    verified = provider.open_verified(
        expectation=RuntimeConnectionExpectation(
            runtime_manifest_artifact_id=manifest_id,
            runtime_manifest_payload_hash=manifest_hash,
            listener_identity_hash=listener_hash,
            process_identity_hash=process_hash,
            relay_identity_hash=_digest("relay"),
            route_policy_hash=REQUIRED_RUNTIME_ROUTE_POLICY_HASH,
        ),
        binding=binding,
        challenge=urlsafe_b64encode(b"c" * 32).decode().rstrip("="),
    )
    try:
        with pytest.raises(ValueError, match="body"):
            verified.request(headers={}, body=b"different", timeout_seconds=5)
        response = verified.request(headers={}, body=None, timeout_seconds=5)
    finally:
        verified.close()
        http_listener.close()
        control_listener.close()
    http_thread.join(timeout=5)
    control_thread.join(timeout=5)

    assert errors == []
    assert response.status == 200
    assert response.body == b"{}"
    assert verified.evidence.listener_identity_hash == listener_hash


def test_privileged_provider_closes_received_fd_before_rejecting_truncated_message(
    tmp_path: Path,
) -> None:
    control_path = tmp_path / "runtime-resolver.sock"
    control_listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    control_listener.bind(control_path.as_posix())
    control_path.chmod(0o600)
    control_listener.listen(1)
    sent_fd = os.open("/dev/null", os.O_RDONLY)
    errors: list[BaseException] = []

    def serve_control() -> None:
        try:
            control, _ = control_listener.accept()
            with control:
                control.recv(64 * 1024)
                descriptor = array("i", [sent_fd])
                control.sendmsg(
                    [b"x" * (64 * 1024 + 2)],
                    [(socket.SOL_SOCKET, socket.SCM_RIGHTS, descriptor)],
                )
        except Exception as error:  # noqa: BLE001  # pragma: no cover - 线程错误重抛
            errors.append(error)

    thread = threading.Thread(target=serve_control)
    thread.start()
    provider = PrivilegedUnixRuntimeConnectionProvider(
        resolver_socket_path=control_path,
        expected_resolver_uid=os.getuid(),
        trusted_resolver_keys={"key_runtime_resolver": _HOST_KEY.public_key()},
        clock=lambda: _NOW,
    )
    fd_count_before = len(tuple(Path("/proc/self/fd").iterdir()))
    try:
        with pytest.raises(ValueError, match="truncated or oversized"):
            provider.open_verified(
                expectation=RuntimeConnectionExpectation(
                    runtime_manifest_artifact_id="artifact_" + "a" * 64,
                    runtime_manifest_payload_hash=_digest("manifest"),
                    listener_identity_hash=_digest("listener"),
                    process_identity_hash=_digest("process"),
                    relay_identity_hash=_digest("relay"),
                    route_policy_hash=REQUIRED_RUNTIME_ROUTE_POLICY_HASH,
                ),
                binding=RuntimeHttpRequestBinding(
                    method="GET",
                    url="http://127.0.0.1:8000/health",
                    body_hash="sha256:" + sha256(b"").hexdigest(),
                ),
                challenge=urlsafe_b64encode(b"c" * 32).decode().rstrip("="),
            )
    finally:
        thread.join(timeout=5)
        os.close(sent_fd)
        control_listener.close()

    assert errors == []
    assert len(tuple(Path("/proc/self/fd").iterdir())) == fd_count_before - 2


def test_privileged_provider_fails_closed_when_the_resolver_is_absent(
    tmp_path: Path,
) -> None:
    provider = PrivilegedUnixRuntimeConnectionProvider(
        resolver_socket_path=tmp_path / "missing.sock",
        expected_resolver_uid=os.getuid(),
        trusted_resolver_keys={"key_runtime_resolver": _HOST_KEY.public_key()},
        clock=lambda: _NOW,
    )

    with pytest.raises(OSError):
        provider.open_verified(
            expectation=RuntimeConnectionExpectation(
                runtime_manifest_artifact_id="artifact_" + "a" * 64,
                runtime_manifest_payload_hash=_digest("manifest"),
                listener_identity_hash=_digest("listener"),
                process_identity_hash=_digest("process"),
                relay_identity_hash=_digest("relay"),
                route_policy_hash=REQUIRED_RUNTIME_ROUTE_POLICY_HASH,
            ),
            binding=RuntimeHttpRequestBinding(
                method="GET",
                url="http://127.0.0.1:8000/health",
                body_hash="sha256:" + sha256(b"").hexdigest(),
            ),
            challenge=urlsafe_b64encode(b"c" * 32).decode().rstrip("="),
        )


def test_privileged_provider_replay_cache_is_freshness_bounded() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    connected = socket.create_connection(listener.getsockname())
    accepted, _ = listener.accept()
    now = [_NOW]
    try:
        server_port = listener.getsockname()[1]
        listener_hash, process_hash = _identity_hashes(server_port)
        manifest_id = "artifact_" + "a" * 64
        manifest_hash = _digest("manifest")
        binding = RuntimeHttpRequestBinding(
            method="GET",
            url=f"http://127.0.0.1:{server_port}/health",
            body_hash="sha256:" + sha256(b"").hexdigest(),
        )
        expectation = RuntimeConnectionExpectation(
            runtime_manifest_artifact_id=manifest_id,
            runtime_manifest_payload_hash=manifest_hash,
            listener_identity_hash=listener_hash,
            process_identity_hash=process_hash,
            relay_identity_hash=_digest("relay"),
            route_policy_hash=REQUIRED_RUNTIME_ROUTE_POLICY_HASH,
        )
        provider = PrivilegedUnixRuntimeConnectionProvider(
            resolver_socket_path=Path("/run/automarkov/resolver.sock"),
            expected_resolver_uid=os.getuid(),
            trusted_resolver_keys={"key_runtime_resolver": _HOST_KEY.public_key()},
            clock=lambda: now[0],
            max_replay_entries=1,
        )

        def proof(challenge_byte: bytes) -> tuple[str, RuntimeCurrentConnectionProof]:
            challenge = urlsafe_b64encode(challenge_byte * 32).decode().rstrip("=")
            request: dict[str, object] = {
                "runtime_manifest_artifact_id": manifest_id,
                "runtime_manifest_payload_hash": manifest_hash,
                "request_binding_hash": binding.binding_hash,
                "challenge": challenge,
                "relay_identity_hash": _digest("relay"),
                "route_policy_hash": REQUIRED_RUNTIME_ROUTE_POLICY_HASH,
            }
            return challenge, _signed_proof(
                request,
                connected,
                listener_hash,
                process_hash,
                observed_at=now[0],
            )

        first_challenge, first = proof(b"a")
        with pytest.raises(
            CurrentRuntimeConnectionIdentityError,
            match="proof binding",
        ):
            provider._verify_proof(
                first,
                RuntimeConnectionExpectation(
                    runtime_manifest_artifact_id=manifest_id,
                    runtime_manifest_payload_hash=manifest_hash,
                    listener_identity_hash=listener_hash,
                    process_identity_hash=_digest("wrong-process"),
                    relay_identity_hash=_digest("relay"),
                    route_policy_hash=REQUIRED_RUNTIME_ROUTE_POLICY_HASH,
                ),
                binding,
                first_challenge,
            )
        provider._verify_proof(first, expectation, binding, first_challenge)
        with pytest.raises(ValueError, match="replayed"):
            provider._verify_proof(first, expectation, binding, first_challenge)

        second_challenge, second = proof(b"b")
        with pytest.raises(ValueError, match="cache is exhausted"):
            provider._verify_proof(second, expectation, binding, second_challenge)

        now[0] += timedelta(seconds=6)
        third_challenge, third = proof(b"c")
        provider._verify_proof(third, expectation, binding, third_challenge)
        assert tuple(provider._seen_proofs) == (third.payload_hash,)
    finally:
        connected.close()
        accepted.close()
        listener.close()


def test_privileged_provider_rejects_signed_socket_inode_substitution() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    connected = socket.create_connection(listener.getsockname())
    accepted, _ = listener.accept()
    try:
        server_port = listener.getsockname()[1]
        listener_hash, process_hash = _identity_hashes(server_port)
        binding = RuntimeHttpRequestBinding(
            method="GET",
            url=f"http://127.0.0.1:{server_port}/health",
            body_hash="sha256:" + sha256(b"").hexdigest(),
        )
        request: dict[str, object] = {
            "runtime_manifest_artifact_id": "artifact_" + "a" * 64,
            "runtime_manifest_payload_hash": _digest("manifest"),
            "request_binding_hash": binding.binding_hash,
            "challenge": urlsafe_b64encode(b"i" * 32).decode().rstrip("="),
            "relay_identity_hash": _digest("relay"),
            "route_policy_hash": REQUIRED_RUNTIME_ROUTE_POLICY_HASH,
        }
        actual_inode = os.fstat(connected.fileno()).st_ino
        proof = _signed_proof(
            request,
            connected,
            listener_hash,
            process_hash,
            accepted_socket_inode=actual_inode + 1,
        )

        with pytest.raises(
            CurrentRuntimeConnectionIdentityError,
            match="signed tuple",
        ):
            PrivilegedUnixRuntimeConnectionProvider._verify_connected_socket(
                connected,
                proof,
                binding,
            )
    finally:
        connected.close()
        accepted.close()
        listener.close()
