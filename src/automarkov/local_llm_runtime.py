from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import stat
import struct
import subprocess
from array import array
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path, PurePath
from secrets import token_bytes
from threading import BoundedSemaphore, Lock, RLock
from time import monotonic_ns
from typing import Literal, Protocol, TypeAlias, cast
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from automarkov.canonical import canonical_json_bytes, parse_json_payload
from automarkov.domain import (
    ArtifactId,
    GenerationEvidenceView,
    Sha256Digest,
    validate_strict_frozen_payload,
)
from automarkov.errors import (
    AutoMarkovError,
    LocalLlmRuntimeCapacityError,
    LocalLlmRuntimeStateError,
)
from automarkov.llm_contracts import (
    LlmCompletionRequest,
    LlmCompletionResponseArtifact,
    LlmCompletionResult,
    LlmCompletionTrace,
    LlmProbeResult,
    LlmResponsePayload,
    LlmStartRequest,
    LlmToolCall,
    LlmUsage,
    LocalLlmRuntimeManifest,
    RuntimeArtifactReference,
    RuntimeCurrentConnectionProof,
    RuntimeHostAttestation,
    RuntimeModelSnapshotEvidence,
    RuntimePackageEvidence,
    RuntimeProbeEvidence,
    RuntimeProcessEvidence,
    validate_llm_completion_payload,
    validate_llm_start_payload,
)
from automarkov.public import ArtifactRepository, CloseResult

_MAX_HTTP_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_CREDENTIAL_BYTES = 4_096
_MAX_CONNECTION_PROOF_BYTES = 64 * 1024
_MAX_COMPLETION_REQUEST_CLAIMS = 100_000
_CANARY_PREFIX = "AUTOMARKOV_CANARY_"
_DISABLED_THINKING_MARKERS = ("<think>", "</think>")
_ProbeFailureCode: TypeAlias = Literal[
    "credential_invalid",
    "authentication_not_enforced",
    "health_failed",
    "identity_mismatch",
    "manifest_invalid",
    "models_failed",
    "completion_failed",
    "transport_failed",
]
_FinishReason: TypeAlias = Literal["stop", "length", "tool_calls"]


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes
    content_type: str | None


class GenerationEvidenceVerifier(Protocol):
    """在 prompt 进入模型前重验 generation evidence 能力。"""

    def verify_generation_view(
        self,
        view: GenerationEvidenceView,
    ) -> GenerationEvidenceView: ...


@dataclass(frozen=True, slots=True)
class RuntimeConnectionExpectation:
    runtime_manifest_artifact_id: str
    runtime_manifest_payload_hash: str
    listener_identity_hash: str
    process_identity_hash: str
    relay_identity_hash: str
    route_policy_hash: str


@dataclass(frozen=True, slots=True)
class RuntimeHttpRequestBinding:
    method: str
    url: str
    body_hash: str

    @property
    def binding_hash(self) -> str:
        return (
            "sha256:"
            + sha256(
                canonical_json_bytes(
                    {
                        "method": self.method,
                        "url": self.url,
                        "body_hash": self.body_hash,
                    }
                )
            ).hexdigest()
        )


@dataclass(frozen=True, slots=True)
class CurrentRuntimeConnectionEvidence:
    challenge: str
    request_binding_hash: str
    listener_identity_hash: str
    process_identity_hash: str
    relay_identity_hash: str
    route_policy_hash: str
    evidence_hash: str


@dataclass(frozen=True, slots=True)
class _VerifiedHttpResponse:
    response: HttpResponse
    connection_evidence_hash: str


class CurrentRuntimeConnectionIdentityError(ValueError):
    pass


class VerifiedRuntimeConnection(Protocol):
    @property
    def evidence(self) -> CurrentRuntimeConnectionEvidence: ...

    def request(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> HttpResponse: ...

    def close(self) -> None: ...


class CurrentRuntimeConnectionProvider(Protocol):
    """原子验证当前 listener/process，并在同一连接发送一次请求。"""

    def open_verified(
        self,
        *,
        expectation: RuntimeConnectionExpectation,
        binding: RuntimeHttpRequestBinding,
        challenge: str,
    ) -> VerifiedRuntimeConnection: ...


class RuntimeAttestationVerifier(Protocol):
    def verify(
        self,
        manifest: LocalLlmRuntimeManifest,
        attestation: RuntimeHostAttestation,
    ) -> None: ...


class RuntimeEvidenceResolver(Protocol):
    def payload_hash(self, artifact_id: str) -> str: ...


class SignedRuntimeAttestationVerifier:
    """用可信 host key 和已持久化证据引用验证现场 attestation。"""

    def __init__(
        self,
        *,
        trusted_host_keys: Mapping[str, Ed25519PublicKey],
        evidence_resolver: RuntimeEvidenceResolver,
        clock: Callable[[], datetime] | None = None,
        max_attestation_age_seconds: int = 300,
        max_future_clock_skew_seconds: int = 5,
    ) -> None:
        if type(trusted_host_keys) is not dict or not trusted_host_keys:
            raise ValueError("runtime attestation verifier requires trusted host keys")
        if (
            type(max_attestation_age_seconds) is not int
            or max_attestation_age_seconds <= 0
            or type(max_future_clock_skew_seconds) is not int
            or max_future_clock_skew_seconds < 0
        ):
            raise ValueError("runtime attestation freshness policy is invalid")
        self._trusted_host_keys = dict(trusted_host_keys)
        self._evidence_resolver = evidence_resolver
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_attestation_age = timedelta(seconds=max_attestation_age_seconds)
        self._max_future_clock_skew = timedelta(seconds=max_future_clock_skew_seconds)
        self._nonce_claims: dict[tuple[str, str], str] = {}

    def verify(
        self,
        manifest: LocalLlmRuntimeManifest,
        attestation: RuntimeHostAttestation,
    ) -> None:
        if type(manifest) is not LocalLlmRuntimeManifest:
            raise ValueError("runtime manifest type is invalid")
        if type(attestation) is not RuntimeHostAttestation:
            raise ValueError("runtime host attestation type is invalid")
        now = self._clock()
        if type(now) is not datetime or now.tzinfo is None:
            raise ValueError("runtime attestation clock must return an aware datetime")
        now = now.astimezone(UTC)
        observed_at = datetime.fromisoformat(
            attestation.observed_at.removesuffix("Z") + "+00:00"
        )
        if observed_at - now > self._max_future_clock_skew:
            raise ValueError("runtime host attestation is from the future")
        if now - observed_at > self._max_attestation_age:
            raise ValueError("runtime host attestation is stale")
        key = self._trusted_host_keys.get(attestation.signing_key_id)
        if key is None:
            raise ValueError("runtime host attestation key is not trusted")
        signature = urlsafe_b64decode(attestation.signature + "==")
        if (
            urlsafe_b64encode(signature).decode("ascii").rstrip("=")
            != attestation.signature
        ):
            raise ValueError("runtime host attestation signature is not canonical")
        try:
            key.verify(signature, attestation.signing_bytes())
        except InvalidSignature as error:
            raise ValueError("runtime host attestation signature is invalid") from error
        references = (
            (
                attestation.process_evidence_ref.artifact_id.root,
                attestation.process_evidence_ref.payload_hash,
            ),
            (
                attestation.package_evidence_ref.artifact_id.root,
                attestation.package_evidence_ref.payload_hash,
            ),
            (
                attestation.model_snapshot_evidence_ref.artifact_id.root,
                attestation.model_snapshot_evidence_ref.payload_hash,
            ),
        )
        for artifact_id, expected_hash in references:
            try:
                actual_hash = self._evidence_resolver.payload_hash(artifact_id)
            except (AutoMarkovError, LookupError, OSError, ValueError) as error:
                raise ValueError(
                    "runtime host evidence reference is unavailable"
                ) from error
            if actual_hash != expected_hash:
                raise ValueError("runtime host evidence reference failed integrity")
        if (
            attestation.runtime_manifest_ref.payload_hash
            != manifest.artifact_payload_hash
        ):
            raise ValueError("runtime host attestation does not bind the manifest")
        claim = "sha256:" + sha256(attestation.signing_bytes() + signature).hexdigest()
        nonce_slot = (attestation.signing_key_id, attestation.nonce)
        prior_claim = self._nonce_claims.get(nonce_slot)
        if prior_claim is not None and prior_claim != claim:
            raise ValueError("runtime host attestation nonce was replayed")
        self._nonce_claims[nonce_slot] = claim


class _ScmRightsVerifiedRuntimeConnection:
    """只允许在 resolver 传入的同一条 TCP socket 上发送一次请求。"""

    def __init__(
        self,
        *,
        connected_socket: socket.socket,
        binding: RuntimeHttpRequestBinding,
        evidence: CurrentRuntimeConnectionEvidence,
    ) -> None:
        self._socket = connected_socket
        self._binding = binding
        self._evidence = evidence
        self._used = False
        self._closed = False
        self._connection: http.client.HTTPConnection | None = None

    @property
    def evidence(self) -> CurrentRuntimeConnectionEvidence:
        return self._evidence

    def request(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> HttpResponse:
        if self._used or self._closed:
            raise ValueError("verified runtime connection is single-use")
        actual_body_hash = "sha256:" + sha256(body or b"").hexdigest()
        if actual_body_hash != self._binding.body_hash:
            raise ValueError("runtime request body does not match its signed binding")
        self._used = True
        parsed = urlsplit(self._binding.url)
        if parsed.hostname is None or parsed.port is None:
            raise ValueError("verified runtime request URL is invalid")
        self._socket.settimeout(timeout_seconds)
        connection = http.client.HTTPConnection(
            parsed.hostname,
            parsed.port,
            timeout=timeout_seconds,
        )
        connection.sock = self._socket
        self._connection = connection
        request_headers = dict(headers)
        request_headers["Connection"] = "close"
        connection.request(
            self._binding.method,
            parsed.path or "/",
            body=body,
            headers=request_headers,
        )
        response = connection.getresponse()
        response_body = response.read(_MAX_HTTP_RESPONSE_BYTES + 1)
        if len(response_body) > _MAX_HTTP_RESPONSE_BYTES:
            raise ValueError("runtime response exceeds the bounded ingress limit")
        raw_content_type = response.getheader("Content-Type")
        content_type = (
            raw_content_type.partition(";")[0].strip().lower()
            if raw_content_type is not None
            else None
        )
        return HttpResponse(
            status=response.status,
            body=response_body,
            content_type=content_type,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        connection = self._connection
        if connection is not None:
            connection.close()
            return
        self._socket.close()


class PrivilegedUnixRuntimeConnectionProvider:
    """从特权 resolver 原子接收签名证明与同一条已连接 TCP fd。"""

    def __init__(
        self,
        *,
        resolver_socket_path: Path,
        expected_resolver_uid: int,
        trusted_resolver_keys: Mapping[str, Ed25519PublicKey],
        clock: Callable[[], datetime] | None = None,
        max_proof_age_seconds: int = 5,
        max_replay_entries: int = 10_000,
        timeout_seconds: int = 5,
    ) -> None:
        if (
            type(expected_resolver_uid) is not int
            or expected_resolver_uid < 0
            or type(trusted_resolver_keys) is not dict
            or not trusted_resolver_keys
            or type(max_proof_age_seconds) is not int
            or max_proof_age_seconds < 1
            or max_proof_age_seconds > 5
            or type(max_replay_entries) is not int
            or max_replay_entries < 1
            or max_replay_entries > 1_000_000
            or type(timeout_seconds) is not int
            or timeout_seconds < 1
        ):
            raise ValueError("runtime resolver provider policy is invalid")
        self._resolver_socket_path = resolver_socket_path
        self._expected_resolver_uid = expected_resolver_uid
        self._trusted_resolver_keys = dict(trusted_resolver_keys)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_proof_age = timedelta(seconds=max_proof_age_seconds)
        self._max_replay_entries = max_replay_entries
        self._timeout_seconds = timeout_seconds
        self._seen_proofs: dict[str, datetime] = {}
        self._replay_lock = Lock()

    def open_verified(
        self,
        *,
        expectation: RuntimeConnectionExpectation,
        binding: RuntimeHttpRequestBinding,
        challenge: str,
    ) -> VerifiedRuntimeConnection:
        socket_metadata = self._verify_resolver_socket_path()
        request_bytes = canonical_json_bytes(
            {
                "schema_version": ("automarkov.runtime-current-connection-request.v2"),
                "runtime_manifest_artifact_id": (
                    expectation.runtime_manifest_artifact_id
                ),
                "runtime_manifest_payload_hash": (
                    expectation.runtime_manifest_payload_hash
                ),
                "listener_identity_hash": expectation.listener_identity_hash,
                "process_identity_hash": expectation.process_identity_hash,
                "relay_identity_hash": expectation.relay_identity_hash,
                "route_policy_hash": expectation.route_policy_hash,
                "method": binding.method,
                "url": binding.url,
                "body_hash": binding.body_hash,
                "request_binding_hash": binding.binding_hash,
                "challenge": challenge,
            }
        )
        control = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        received_fd: int | None = None
        try:
            control.settimeout(self._timeout_seconds)
            control.connect(self._resolver_socket_path.as_posix())
            self._verify_resolver_peer(control, socket_metadata)
            control.sendall(request_bytes)
            message, ancillary, flags, _ = control.recvmsg(
                _MAX_CONNECTION_PROOF_BYTES + 1,
                socket.CMSG_SPACE(2 * array("i").itemsize),
            )
            descriptors = array("i")
            unexpected_ancillary = False
            for level, kind, payload in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    descriptors.frombytes(
                        payload[: len(payload) - len(payload) % descriptors.itemsize]
                    )
                else:
                    unexpected_ancillary = True
            if (
                flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
                or not message
                or len(message) > _MAX_CONNECTION_PROOF_BYTES
            ):
                for descriptor in descriptors:
                    os.close(descriptor)
                raise CurrentRuntimeConnectionIdentityError(
                    "runtime resolver response is truncated or oversized"
                )
            if unexpected_ancillary:
                for descriptor in descriptors:
                    os.close(descriptor)
                raise CurrentRuntimeConnectionIdentityError(
                    "runtime resolver returned unexpected ancillary data"
                )
            if len(descriptors) != 1:
                for descriptor in descriptors:
                    os.close(descriptor)
                raise CurrentRuntimeConnectionIdentityError(
                    "runtime resolver must return exactly one socket fd"
                )
            received_fd = descriptors[0]
        finally:
            control.close()
        try:
            raw_proof = parse_json_payload(message)
            proof = validate_strict_frozen_payload(
                RuntimeCurrentConnectionProof,
                raw_proof,
            )
            self._verify_proof(proof, expectation, binding, challenge)
            connected = socket.socket(fileno=received_fd)
            received_fd = None
            try:
                os.set_inheritable(connected.fileno(), False)
                self._verify_connected_socket(connected, proof, binding)
            except BaseException:
                connected.close()
                raise
            evidence = CurrentRuntimeConnectionEvidence(
                challenge=proof.challenge,
                request_binding_hash=proof.request_binding_hash,
                listener_identity_hash=proof.listener_identity_hash,
                process_identity_hash=proof.process_identity_hash,
                relay_identity_hash=proof.relay_identity_hash,
                route_policy_hash=proof.route_policy_hash,
                evidence_hash=proof.payload_hash,
            )
            return _ScmRightsVerifiedRuntimeConnection(
                connected_socket=connected,
                binding=binding,
                evidence=evidence,
            )
        finally:
            if received_fd is not None:
                os.close(received_fd)

    def _verify_resolver_socket_path(self) -> os.stat_result:
        path = self._resolver_socket_path
        if (
            not path.is_absolute()
            or path.as_posix().startswith("//")
            or any(part in {"", ".", ".."} for part in path.parts[1:])
        ):
            raise ValueError("runtime resolver socket path is not canonical")
        current = Path("/")
        for part in path.parts[1:-1]:
            current /= part
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("runtime resolver socket parent is unsafe")
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISSOCK(metadata.st_mode)
            or metadata.st_uid != self._expected_resolver_uid
            or stat.S_IMODE(metadata.st_mode) not in {0o600, 0o660}
        ):
            raise ValueError("runtime resolver socket policy is invalid")
        return metadata

    def _verify_resolver_peer(
        self,
        control: socket.socket,
        before: os.stat_result,
    ) -> None:
        if not hasattr(socket, "SO_PEERCRED"):
            raise ValueError("runtime resolver requires Linux SO_PEERCRED")
        credentials = control.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        )
        _, peer_uid, _ = struct.unpack("3i", credentials)
        after = self._resolver_socket_path.lstat()
        if peer_uid != self._expected_resolver_uid or (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
        ) != (after.st_dev, after.st_ino, after.st_mode, after.st_uid):
            raise CurrentRuntimeConnectionIdentityError(
                "runtime resolver peer identity is invalid"
            )

    def _verify_proof(
        self,
        proof: RuntimeCurrentConnectionProof,
        expectation: RuntimeConnectionExpectation,
        binding: RuntimeHttpRequestBinding,
        challenge: str,
    ) -> None:
        now = self._clock()
        if type(now) is not datetime or now.tzinfo is None:
            raise ValueError("runtime resolver clock must return an aware datetime")
        observed_at = datetime.fromisoformat(
            proof.observed_at.removesuffix("Z") + "+00:00"
        )
        age = now.astimezone(UTC) - observed_at
        if age < timedelta(0) or age > self._max_proof_age:
            raise CurrentRuntimeConnectionIdentityError(
                "runtime current connection proof is not fresh"
            )
        if (
            proof.challenge != challenge
            or proof.request_binding_hash != binding.binding_hash
            or proof.runtime_manifest_artifact_id.root
            != expectation.runtime_manifest_artifact_id
            or proof.runtime_manifest_payload_hash
            != expectation.runtime_manifest_payload_hash
            or proof.listener_identity_hash != expectation.listener_identity_hash
            or proof.process_identity_hash != expectation.process_identity_hash
            or proof.relay_identity_hash != expectation.relay_identity_hash
            or proof.route_policy_hash != expectation.route_policy_hash
        ):
            raise CurrentRuntimeConnectionIdentityError(
                "runtime current connection proof binding is invalid"
            )
        key = self._trusted_resolver_keys.get(proof.signing_key_id)
        if key is None:
            raise CurrentRuntimeConnectionIdentityError(
                "runtime resolver signing key is not trusted"
            )
        signature = urlsafe_b64decode(proof.signature + "==")
        try:
            key.verify(signature, proof.signing_bytes())
        except InvalidSignature as error:
            raise CurrentRuntimeConnectionIdentityError(
                "runtime current connection proof signature is invalid"
            ) from error
        with self._replay_lock:
            expired = [
                proof_hash
                for proof_hash, accepted_at in self._seen_proofs.items()
                if now.astimezone(UTC) - accepted_at > self._max_proof_age
            ]
            for proof_hash in expired:
                del self._seen_proofs[proof_hash]
            if proof.payload_hash in self._seen_proofs:
                raise CurrentRuntimeConnectionIdentityError(
                    "runtime current connection proof was replayed"
                )
            if len(self._seen_proofs) >= self._max_replay_entries:
                raise CurrentRuntimeConnectionIdentityError(
                    "runtime current connection proof cache is exhausted"
                )
            self._seen_proofs[proof.payload_hash] = now.astimezone(UTC)

    @staticmethod
    def _verify_connected_socket(
        connected: socket.socket,
        proof: RuntimeCurrentConnectionProof,
        binding: RuntimeHttpRequestBinding,
    ) -> None:
        if (
            connected.family not in {socket.AF_INET, socket.AF_INET6}
            or connected.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
            != socket.SOCK_STREAM
        ):
            raise CurrentRuntimeConnectionIdentityError(
                "runtime resolver fd is not a TCP stream socket"
            )
        client = connected.getsockname()
        server = connected.getpeername()
        socket_inode = os.fstat(connected.fileno()).st_ino
        client_address = ipaddress.ip_address(client[0]).compressed
        server_address = ipaddress.ip_address(server[0]).compressed
        parsed = urlsplit(binding.url)
        if parsed.hostname is None or parsed.port is None:
            raise ValueError("runtime request endpoint is invalid")
        if (
            client_address != proof.client_address
            or client[1] != proof.client_port
            or server_address != proof.server_address
            or server[1] != proof.server_port
            or socket_inode != proof.accepted_socket_inode
            or server_address != ipaddress.ip_address(parsed.hostname).compressed
            or server[1] != parsed.port
        ):
            raise CurrentRuntimeConnectionIdentityError(
                "runtime resolver fd does not match the signed tuple"
            )


class _ProbeFailure(Exception):
    def __init__(self, code: _ProbeFailureCode) -> None:
        self.code: _ProbeFailureCode = code


def _credential_fingerprint(token: str) -> str:
    payload = b"automarkov.vllm-credential-fingerprint.v1\x00" + token.encode("utf-8")
    return "sha256:" + sha256(payload).hexdigest()


def _response_hash(response: HttpResponse) -> str:
    payload = canonical_json_bytes(
        {
            "status": response.status,
            "content_type": response.content_type,
            "body_hash": "sha256:" + sha256(response.body).hexdigest(),
        }
    )
    return "sha256:" + sha256(payload).hexdigest()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _read_owner_only_file(path: Path) -> bytes:
    if not path.is_absolute() or any(
        part in {"", ".", ".."} for part in path.parts[1:]
    ):
        raise ValueError("credential locator must be a normalized absolute path")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open("/", directory_flags)
    try:
        for component in path.parts[1:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        file_descriptor = os.open(path.name, file_flags, dir_fd=descriptor)
        try:
            metadata = os.fstat(file_descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("credential locator must resolve to a regular file")
            if (
                metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise ValueError("credential file must be owner-only mode 0600")
            if metadata.st_size < 1 or metadata.st_size > _MAX_CREDENTIAL_BYTES:
                raise ValueError("credential file size is invalid")
            chunks: list[bytes] = []
            remaining = _MAX_CREDENTIAL_BYTES + 1
            while remaining:
                chunk = os.read(file_descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) != metadata.st_size or len(payload) > _MAX_CREDENTIAL_BYTES:
                raise ValueError("credential file changed during the bounded read")
            after = os.fstat(file_descriptor)
            stable_fields = (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_uid",
                "st_size",
                "st_mtime_ns",
            )
            if any(
                getattr(after, field) != getattr(metadata, field)
                for field in stable_fields
            ):
                raise ValueError("credential file changed during the bounded read")
            return payload
        finally:
            os.close(file_descriptor)
    finally:
        os.close(descriptor)


def _credential_path(repository_root: Path) -> Path:
    raw_locator = os.environ.get("AUTOMARKOV_VLLM_API_KEY_FILE")
    if raw_locator is None or not raw_locator:
        raise ValueError("local vLLM credential file is not configured")
    if "\x00" in raw_locator:
        raise ValueError("local vLLM credential locator is invalid")
    candidate = Path(raw_locator)
    if (
        raw_locator.startswith("//")
        or candidate.as_posix() != raw_locator
        or any(part == ".." for part in PurePath(raw_locator).parts)
    ):
        raise ValueError("local vLLM credential locator contains traversal")
    path = candidate if candidate.is_absolute() else repository_root / candidate
    path = Path(os.path.abspath(path))
    try:
        repository = repository_root.resolve(strict=True)
        canonical_path = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("credential path identity cannot be resolved") from error
    if not repository.is_dir():
        raise ValueError("repository root must resolve to a directory")
    if _is_inside(canonical_path, repository):
        relative = canonical_path.relative_to(repository)
        if not relative.parts or relative.parts[0] != "secrets":
            raise ValueError("worktree credentials are allowed only below secrets")
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "--", relative.as_posix()],
            cwd=repository,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
        if ignored.returncode != 0:
            raise ValueError("worktree credential file is not ignored")
    return path


def _load_credential(repository_root: Path) -> str:
    raw = _read_owner_only_file(_credential_path(repository_root))
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    try:
        token = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("credential file is not ASCII") from error
    if not token or any(
        ord(character) < 0x21 or ord(character) > 0x7E for character in token
    ):
        raise ValueError("credential file contains an invalid bearer token")
    return token


def _object(value: object, subject: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(f"{subject} must be a JSON object")
    return cast(dict[str, object], value)


def _sequence(value: object, subject: str) -> list[object]:
    if type(value) is not list:
        raise ValueError(f"{subject} must be a JSON array")
    return cast(list[object], value)


def _exact_string(value: object, subject: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{subject} must be a string")
    return value


def _exact_nonnegative_int(value: object, subject: str) -> int:
    if type(value) is not int or value < 0 or value > 9_007_199_254_740_991:
        raise ValueError(f"{subject} must be a safe nonnegative integer")
    return value


class AttachedLocalLlmRuntime:
    """附着到既有 vLLM；该适配器从不拥有服务生命周期。"""

    def __init__(
        self,
        *,
        repository_root: Path,
        artifact_repository: ArtifactRepository,
        attestation_verifier: RuntimeAttestationVerifier,
        connection_provider: CurrentRuntimeConnectionProvider,
        evidence_access_controller: GenerationEvidenceVerifier,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository_root = repository_root
        self._artifact_repository = artifact_repository
        self._connection_provider = connection_provider
        self._evidence_access_controller = evidence_access_controller
        self._attestation_verifier = attestation_verifier
        self._clock = clock or (lambda: datetime.now(UTC))
        self._manifest: LocalLlmRuntimeManifest | None = None
        self._manifest_payload_hash: str | None = None
        self._manifest_artifact_id: ArtifactId | None = None
        self._attestation: RuntimeHostAttestation | None = None
        self._attestation_artifact_id: ArtifactId | None = None
        self._attestation_payload_hash: str | None = None
        self._probe_evidence_artifact_id: ArtifactId | None = None
        self._probe_evidence_payload_hash: str | None = None
        self._ready = False
        self._closed = False
        self._capacity: BoundedSemaphore | None = None
        self._state_lock = RLock()
        self._active_completions = 0
        self._completion_request_claims: set[str] = set()

    def start(self, request: LlmStartRequest) -> LlmProbeResult:
        with self._state_lock:
            if self._active_completions:
                raise LocalLlmRuntimeStateError("BUSY")
            return self._start_locked(request)

    def _start_locked(self, request: LlmStartRequest) -> LlmProbeResult:
        if type(request) is not LlmStartRequest:
            raise ValueError("LLM start requires the exact request type")
        request = validate_llm_start_payload(
            request.model_dump(mode="json", round_trip=True, warnings="error")
        )
        self._ready = False
        self._attestation = None
        self._attestation_artifact_id = None
        self._attestation_payload_hash = None
        self._probe_evidence_artifact_id = None
        self._probe_evidence_payload_hash = None
        self._manifest = request.runtime_manifest
        self._manifest_payload_hash = request.runtime_manifest_payload_hash.root
        self._manifest_artifact_id = request.runtime_manifest_artifact_id
        self._capacity = BoundedSemaphore(request.runtime_manifest.max_concurrency)
        self._closed = False
        try:
            self._verify_runtime_manifest_artifact(request)
            self._verify_host_evidence_artifacts(request)
            self._attestation_verifier.verify(
                request.runtime_manifest,
                request.host_attestation,
            )
            (
                self._attestation_artifact_id,
                self._attestation_payload_hash,
            ) = self._persist_host_attestation(request)
        except (AutoMarkovError, OSError, ValueError):
            result = self._failed_probe("manifest_invalid")
            self._manifest = None
            self._manifest_payload_hash = None
            self._manifest_artifact_id = None
            self._attestation = None
            self._attestation_artifact_id = None
            self._attestation_payload_hash = None
            self._probe_evidence_artifact_id = None
            self._probe_evidence_payload_hash = None
            self._capacity = None
            return result
        self._attestation = request.host_attestation
        return self._run_probe()

    def _failed_probe(self, code: _ProbeFailureCode) -> LlmProbeResult:
        manifest = self._require_manifest()
        self._ready = False
        self._probe_evidence_artifact_id = None
        self._probe_evidence_payload_hash = None
        return LlmProbeResult(
            schema_version="automarkov.llm-probe-result.v3",
            runtime_id=manifest.runtime_id,
            readiness_state="WAITING_RUNTIME",
            ready=False,
            runtime_manifest_payload_hash=self._require_manifest_payload_hash(),
            health_passed=False,
            authentication_enforced_passed=False,
            authenticated_models_passed=False,
            authenticated_completion_passed=False,
            served_model_name=None,
            health_response_hash=None,
            missing_auth_response_hash=None,
            invalid_auth_response_hash=None,
            models_response_hash=None,
            canary_request_hash=None,
            canary_response_hash=None,
            failure_code=code,
            probe_evidence_artifact_id=None,
            probe_evidence_payload_hash=None,
        )

    def probe(self) -> LlmProbeResult:
        with self._state_lock:
            if self._active_completions:
                raise LocalLlmRuntimeStateError("BUSY")
            return self._probe_locked()

    def _probe_locked(self) -> LlmProbeResult:
        if self._manifest is None:
            raise LocalLlmRuntimeStateError("NOT_STARTED")
        if self._closed:
            raise LocalLlmRuntimeStateError("CLOSED")
        try:
            self._verify_attestation()
        except (OSError, ValueError):
            return self._failed_probe("manifest_invalid")
        return self._run_probe()

    def _run_probe(self) -> LlmProbeResult:
        manifest = self._require_manifest()
        manifest_ref = self._manifest_ref()
        health_passed = False
        models_passed = False
        authentication_enforced = False
        completion_passed = False
        served_model_name: str | None = None
        health_response_hash: str | None = None
        missing_auth_response_hash: str | None = None
        invalid_auth_response_hash: str | None = None
        models_response_hash: str | None = None
        canary_request_hash: str | None = None
        canary_response_hash: str | None = None
        failure_code: _ProbeFailureCode | None = None
        probe_evidence_artifact_id: ArtifactId | None = None
        probe_evidence_payload_hash: Sha256Digest | None = None
        self._ready = False
        try:
            if manifest.lifecycle_mode != "ATTACHED":
                raise _ProbeFailure("manifest_invalid")
            configured_base_url = os.environ.get("AUTOMARKOV_VLLM_BASE_URL")
            configured_model = os.environ.get("AUTOMARKOV_VLLM_MODEL")
            configured_timeout = os.environ.get("AUTOMARKOV_VLLM_TIMEOUT_SECONDS")
            if (
                configured_base_url != manifest.base_url
                or configured_model != manifest.model_id
                or configured_timeout != str(manifest.request_timeout_seconds)
            ):
                raise _ProbeFailure("manifest_invalid")
            health = self._request(
                "GET",
                self._health_url(manifest),
                manifest=manifest,
                manifest_ref=manifest_ref,
                credential=None,
            ).response
            if health.status != 200:
                raise _ProbeFailure("health_failed")
            health_response_hash = _response_hash(health)
            health_passed = True
            missing_credential = self._request(
                "GET",
                manifest.base_url + "/models",
                manifest=manifest,
                manifest_ref=manifest_ref,
                credential=None,
            ).response
            invalid_token = (
                "automarkov-invalid-"
                + sha256(manifest.identity_hash.encode("ascii")).hexdigest()
            )
            if (
                _credential_fingerprint(invalid_token)
                == manifest.credential_fingerprint
            ):
                invalid_token += "-different"
            invalid_credential = self._request(
                "GET",
                manifest.base_url + "/models",
                manifest=manifest,
                manifest_ref=manifest_ref,
                credential=lambda: invalid_token,
            ).response
            missing_auth_response_hash = _response_hash(missing_credential)
            invalid_auth_response_hash = _response_hash(invalid_credential)
            if missing_credential.status not in {
                401,
                403,
            } or invalid_credential.status not in {401, 403}:
                raise _ProbeFailure("authentication_not_enforced")
            authentication_enforced = True
            models = self._request(
                "GET",
                manifest.base_url + "/models",
                manifest=manifest,
                manifest_ref=manifest_ref,
                credential=lambda: self._probe_credential(manifest),
            ).response
            if models.status in {401, 403}:
                raise _ProbeFailure("credential_invalid")
            if models.status != 200:
                raise _ProbeFailure("models_failed")
            if models.content_type != "application/json":
                raise _ProbeFailure("models_failed")
            models_response_hash = _response_hash(models)
            try:
                served_model_name = self._parse_models(models.body, manifest)
            except ValueError:
                raise _ProbeFailure("models_failed") from None
            if served_model_name != manifest.served_model_name:
                raise _ProbeFailure("identity_mismatch")
            models_passed = True
            canary = (
                _CANARY_PREFIX
                + sha256(manifest.identity_hash.encode("ascii")).hexdigest()[:24]
            )
            canary_messages = [
                {
                    "role": "user",
                    "content": "Return exactly this token with no other text: "
                    + canary,
                }
            ]
            canary_max_tokens = min(64, manifest.max_completion_tokens)
            canary_prompt_tokens = self._count_rendered_prompt_tokens(
                canary_messages,
                manifest,
                manifest_ref,
            )
            if (
                canary_prompt_tokens > manifest.max_prompt_tokens
                or canary_prompt_tokens + canary_max_tokens > manifest.max_model_len
            ):
                raise _ProbeFailure("manifest_invalid")
            canary_body = canonical_json_bytes(
                {
                    "model": manifest.served_model_name,
                    "messages": canary_messages,
                    "temperature": 0.0,
                    "top_p": 1.0,
                    "seed": 0,
                    "max_tokens": canary_max_tokens,
                    "chat_template_kwargs": {"enable_thinking": False},
                }
            )
            canary_request_hash = "sha256:" + sha256(canary_body).hexdigest()
            capacity = self._capacity
            if capacity is None or not capacity.acquire(blocking=False):
                raise _ProbeFailure("completion_failed")
            try:
                completion = self._request(
                    "POST",
                    manifest.base_url + "/chat/completions",
                    manifest=manifest,
                    manifest_ref=manifest_ref,
                    credential=lambda: self._probe_credential(manifest),
                    body=canary_body,
                ).response
            finally:
                capacity.release()
            if completion.status in {401, 403}:
                raise _ProbeFailure("credential_invalid")
            if completion.status != 200:
                raise _ProbeFailure("completion_failed")
            if completion.content_type != "application/json":
                raise _ProbeFailure("completion_failed")
            canary_response_hash = _response_hash(completion)
            try:
                response, canary_usage = self._parse_completion(
                    completion.body,
                    manifest,
                )
            except ValueError:
                raise _ProbeFailure("completion_failed") from None
            if (
                response.content != canary
                or response.tool_calls
                or response.finish_reason != "stop"
                or canary_usage.prompt_tokens != canary_prompt_tokens
                or canary_usage.completion_tokens > canary_max_tokens
                or canary_usage.total_tokens > manifest.max_model_len
            ):
                raise _ProbeFailure("completion_failed")
            completion_passed = True
            try:
                (
                    probe_evidence_artifact_id,
                    probe_evidence_payload_hash,
                ) = self._persist_probe_evidence(
                    manifest=manifest,
                    served_model_name=served_model_name,
                    health_response_hash=health_response_hash,
                    missing_auth_response_hash=missing_auth_response_hash,
                    invalid_auth_response_hash=invalid_auth_response_hash,
                    models_response_hash=models_response_hash,
                    canary_request_hash=canary_request_hash,
                    canary_response_hash=canary_response_hash,
                )
                self._probe_evidence_artifact_id = probe_evidence_artifact_id
                self._probe_evidence_payload_hash = probe_evidence_payload_hash.root
            except (AutoMarkovError, OSError, ValueError):
                raise _ProbeFailure("manifest_invalid") from None
            self._ready = True
        except _ProbeFailure as error:
            failure_code = error.code
        except CurrentRuntimeConnectionIdentityError:
            failure_code = "identity_mismatch"
        except (http.client.HTTPException, OSError, TimeoutError, ValueError):
            failure_code = "transport_failed"
        return LlmProbeResult(
            schema_version="automarkov.llm-probe-result.v3",
            runtime_id=manifest.runtime_id,
            readiness_state="READY" if self._ready else "WAITING_RUNTIME",
            ready=self._ready,
            runtime_manifest_payload_hash=self._require_manifest_payload_hash(),
            health_passed=health_passed,
            authenticated_models_passed=models_passed,
            authentication_enforced_passed=authentication_enforced,
            authenticated_completion_passed=completion_passed,
            served_model_name=served_model_name,
            health_response_hash=health_response_hash,
            missing_auth_response_hash=missing_auth_response_hash,
            invalid_auth_response_hash=invalid_auth_response_hash,
            models_response_hash=models_response_hash,
            canary_request_hash=canary_request_hash,
            canary_response_hash=canary_response_hash,
            probe_evidence_artifact_id=probe_evidence_artifact_id,
            probe_evidence_payload_hash=probe_evidence_payload_hash,
            failure_code=failure_code,
        )

    def complete(self, request: LlmCompletionRequest) -> LlmCompletionResult:
        if type(request) is not LlmCompletionRequest:
            raise ValueError("LLM completion requires the exact request type")
        request = validate_llm_completion_payload(
            request.model_dump(mode="json", round_trip=True, warnings="error")
        )
        with self._state_lock:
            manifest = self._require_manifest()
            if self._closed or not self._ready:
                raise LocalLlmRuntimeStateError("WAITING_RUNTIME")
            try:
                self._verify_attestation()
            except (OSError, ValueError) as error:
                self._ready = False
                raise LocalLlmRuntimeStateError("IDENTITY_DRIFT") from error
            if (
                request.runtime_manifest_payload_hash.root
                != self._require_manifest_payload_hash()
            ):
                self._ready = False
                raise LocalLlmRuntimeStateError("IDENTITY_DRIFT")
            self._verify_prompt_artifact(request)
            request_id = str(request.request_id)
            if request_id in self._completion_request_claims:
                raise LocalLlmRuntimeStateError("REPLAY")
            if len(self._completion_request_claims) >= _MAX_COMPLETION_REQUEST_CLAIMS:
                raise LocalLlmRuntimeStateError("REPLAY_REGISTRY_EXHAUSTED")
            if request.sampling.max_tokens > manifest.max_completion_tokens:
                raise ValueError("completion request exceeds the runtime token ceiling")
            manifest_ref = self._manifest_ref()
            probe_ref = self._probe_ref()
            capacity = self._capacity
            if capacity is None:
                raise LocalLlmRuntimeStateError("WAITING_RUNTIME")
            self._completion_request_claims.add(request_id)
            self._active_completions += 1
        try:
            return self._complete_snapshot(
                request=request,
                manifest=manifest,
                manifest_ref=manifest_ref,
                probe_ref=probe_ref,
                capacity=capacity,
            )
        finally:
            with self._state_lock:
                self._active_completions -= 1

    def _complete_snapshot(
        self,
        *,
        request: LlmCompletionRequest,
        manifest: LocalLlmRuntimeManifest,
        manifest_ref: RuntimeArtifactReference,
        probe_ref: RuntimeArtifactReference,
        capacity: BoundedSemaphore,
    ) -> LlmCompletionResult:
        try:
            verified_prompt_tokens = self._count_prompt_tokens(
                request,
                manifest,
                manifest_ref,
            )
        except CurrentRuntimeConnectionIdentityError as error:
            self._ready = False
            raise LocalLlmRuntimeStateError("IDENTITY_DRIFT") from error
        except (
            http.client.HTTPException,
            OSError,
            TimeoutError,
            ValueError,
        ) as error:
            self._ready = False
            raise LocalLlmRuntimeStateError("TOKENIZER_DRIFT") from error
        if (
            verified_prompt_tokens > manifest.max_prompt_tokens
            or verified_prompt_tokens + request.sampling.max_tokens
            > manifest.max_model_len
        ):
            raise ValueError("completion request exceeds the prompt token ceiling")
        if not capacity.acquire(blocking=False):
            raise LocalLlmRuntimeCapacityError(manifest.runtime_id)
        started = monotonic_ns()
        try:
            body = canonical_json_bytes(
                {
                    "model": manifest.served_model_name,
                    "messages": [
                        message.model_dump(mode="json", warnings="error")
                        for message in request.prompt.messages
                    ],
                    "temperature": request.sampling.temperature_value,
                    "top_p": request.sampling.top_p_value,
                    "seed": request.sampling.seed,
                    "max_tokens": request.sampling.max_tokens,
                    "chat_template_kwargs": {"enable_thinking": False},
                }
            )
            try:
                verified_response = self._request(
                    "POST",
                    manifest.base_url + "/chat/completions",
                    manifest=manifest,
                    manifest_ref=manifest_ref,
                    credential=lambda: self._load_manifest_credential(manifest),
                    body=body,
                )
            except CurrentRuntimeConnectionIdentityError as error:
                self._ready = False
                raise LocalLlmRuntimeStateError("IDENTITY_DRIFT") from error
            except (
                http.client.HTTPException,
                OSError,
                subprocess.SubprocessError,
                TimeoutError,
                ValueError,
            ):
                self._ready = False
                raise LocalLlmRuntimeStateError("DEGRADED") from None
            response = verified_response.response
            if response.status != 200:
                self._ready = False
                raise LocalLlmRuntimeStateError("DEGRADED")
            if response.content_type != "application/json":
                self._ready = False
                raise LocalLlmRuntimeStateError("DEGRADED")
            connection_evidence_hash = verified_response.connection_evidence_hash
            try:
                response_payload, usage = self._parse_completion(
                    response.body, manifest
                )
            except ValueError:
                self._ready = False
                raise LocalLlmRuntimeStateError("DEGRADED") from None
            if (
                usage.prompt_tokens != verified_prompt_tokens
                or usage.completion_tokens > request.sampling.max_tokens
                or usage.completion_tokens > manifest.max_completion_tokens
                or usage.total_tokens > manifest.max_model_len
            ):
                self._ready = False
                raise LocalLlmRuntimeStateError("TOKEN_BUDGET_DRIFT")
            latency_ms = max(0, (monotonic_ns() - started) // 1_000_000)
            prompt_ref = RuntimeArtifactReference(
                artifact_id=request.prompt_artifact_id,
                payload_hash=request.prompt_payload_hash.root,
            )
            response_artifact = LlmCompletionResponseArtifact(
                schema_version="automarkov.llm-completion-response-artifact.v1",
                request_id=request.request_id,
                runtime_manifest_ref=manifest_ref,
                runtime_probe_evidence_ref=probe_ref,
                prompt_ref=prompt_ref,
                response=response_payload,
            )
            completed_at = self._creation_timestamp()
            try:
                response_artifact_id, response_artifact_hash = (
                    self._persist_completion_response(
                        response_artifact,
                        created_at=completed_at,
                    )
                )
            except (AutoMarkovError, OSError, ValueError) as error:
                self._ready = False
                raise LocalLlmRuntimeStateError(
                    "ARTIFACT_PERSISTENCE_FAILED"
                ) from error
            trace = LlmCompletionTrace(
                schema_version="automarkov.llm-completion-trace.v2",
                request_id=request.request_id,
                model_id=manifest.model_id,
                model_revision=manifest.model_revision,
                vllm_version=manifest.vllm_version,
                tokenizer_hash=manifest.tokenizer_hash,
                chat_template_hash=manifest.chat_template_hash,
                runtime_manifest_ref=manifest_ref,
                runtime_probe_evidence_ref=probe_ref,
                prompt_ref=prompt_ref,
                response_ref=RuntimeArtifactReference(
                    artifact_id=response_artifact_id,
                    payload_hash=response_artifact_hash.root,
                ),
                endpoint_identity_hash=manifest.listener_identity_hash,
                connection_evidence_hash=connection_evidence_hash,
                sampling=request.sampling,
                usage=usage,
                latency_ms=latency_ms,
                finish_reason=response_payload.finish_reason,
            )
            try:
                trace_artifact_id, trace_artifact_hash = self._persist_completion_trace(
                    trace,
                    created_at=completed_at,
                )
            except (AutoMarkovError, OSError, ValueError) as error:
                self._ready = False
                raise LocalLlmRuntimeStateError(
                    "ARTIFACT_PERSISTENCE_FAILED"
                ) from error
            return LlmCompletionResult(
                schema_version="automarkov.llm-completion-result.v3",
                response=response_payload,
                trace=trace,
                response_payload_hash=Sha256Digest(root=response_payload.payload_hash),
                trace_payload_hash=trace_artifact_hash,
                response_artifact_id=response_artifact_id,
                trace_artifact_id=trace_artifact_id,
            )
        finally:
            capacity.release()

    def close(self) -> CloseResult:
        with self._state_lock:
            if self._active_completions:
                raise LocalLlmRuntimeStateError("BUSY")
            return self._close_locked()

    def _close_locked(self) -> CloseResult:
        self._ready = False
        self._attestation = None
        self._attestation_artifact_id = None
        self._attestation_payload_hash = None
        self._probe_evidence_artifact_id = None
        self._probe_evidence_payload_hash = None
        self._manifest_payload_hash = None
        self._manifest_artifact_id = None
        self._closed = True
        return CloseResult(schema_version="automarkov.close-result.v1", closed=True)

    def _verify_runtime_manifest_artifact(self, request: LlmStartRequest) -> None:
        artifact = self._artifact_repository.get(request.runtime_manifest_artifact_id)
        actual_document = artifact.payload_document.model_dump(
            mode="json",
            round_trip=True,
            warnings="error",
        )
        expected_payload = request.runtime_manifest.model_dump(
            mode="json",
            round_trip=True,
            warnings="error",
        )
        if (
            artifact.envelope.artifact_type != "local_llm_runtime_manifest"
            or artifact.envelope.schema_version
            != "automarkov.local-llm-runtime-manifest.v3"
            or artifact.envelope.payload_hash
            != request.runtime_manifest_payload_hash.root
            or actual_document["payload"] != expected_payload
        ):
            raise ValueError("runtime manifest artifact binding is invalid")

    def _verify_prompt_artifact(self, request: LlmCompletionRequest) -> None:
        try:
            artifact = self._artifact_repository.get(request.prompt_artifact_id)
        except AutoMarkovError as error:
            raise ValueError("prompt artifact binding is unavailable") from error
        actual_document = artifact.payload_document.model_dump(
            mode="json",
            round_trip=True,
            warnings="error",
        )
        expected_payload = request.prompt.model_dump(
            mode="json",
            round_trip=True,
            warnings="error",
        )
        if (
            artifact.envelope.artifact_type != "llm_prompt"
            or artifact.envelope.schema_version != "automarkov.llm-prompt.v3"
            or artifact.envelope.payload_hash != request.prompt_payload_hash.root
            or actual_document["payload"] != expected_payload
        ):
            raise ValueError("prompt artifact binding is invalid")
        self._evidence_access_controller.verify_generation_view(
            request.prompt.generation_evidence_view
        )

    def _verify_host_evidence_artifacts(self, request: LlmStartRequest) -> None:
        manifest = request.runtime_manifest
        expected: tuple[tuple[RuntimeArtifactReference, str, object], ...] = (
            (
                request.host_attestation.process_evidence_ref,
                "runtime_process_evidence",
                RuntimeProcessEvidence(
                    schema_version="automarkov.runtime-process-evidence.v2",
                    runtime_id=manifest.runtime_id,
                    observed_at=manifest.observed_at,
                    lifecycle_mode=manifest.lifecycle_mode,
                    listener_identity_hash=manifest.listener_identity_hash,
                    process_identity_hash=manifest.process_identity_hash,
                    relay_identity_hash=manifest.relay_identity_hash,
                    route_policy_hash=manifest.route_policy_hash,
                    startup_args=manifest.startup_args,
                ),
            ),
            (
                request.host_attestation.package_evidence_ref,
                "runtime_package_evidence",
                RuntimePackageEvidence(
                    schema_version="automarkov.runtime-package-evidence.v1",
                    runtime_id=manifest.runtime_id,
                    observed_at=manifest.observed_at,
                    vllm_version=manifest.vllm_version,
                    vllm_distribution_hash=manifest.vllm_distribution_hash,
                    runtime_environment_hash=manifest.runtime_environment_hash,
                    pytorch_version=manifest.pytorch_version,
                    cuda_version=manifest.cuda_version,
                    container_digest=manifest.container_digest,
                ),
            ),
            (
                request.host_attestation.model_snapshot_evidence_ref,
                "runtime_model_snapshot_evidence",
                RuntimeModelSnapshotEvidence(
                    schema_version="automarkov.runtime-model-snapshot-evidence.v1",
                    runtime_id=manifest.runtime_id,
                    observed_at=manifest.observed_at,
                    model_id=manifest.model_id,
                    model_checkpoint_path=manifest.model_checkpoint_path,
                    tokenizer_checkpoint_path=manifest.tokenizer_checkpoint_path,
                    model_revision=manifest.model_revision,
                    tokenizer_revision=manifest.tokenizer_revision,
                    model_config_hash=manifest.model_config_hash,
                    weight_index_hash=manifest.weight_index_hash,
                    weight_shard_hashes=manifest.weight_shard_hashes,
                    tokenizer_hash=manifest.tokenizer_hash,
                    tokenizer_config_hash=manifest.tokenizer_config_hash,
                    chat_template_hash=manifest.chat_template_hash,
                    thinking_policy=manifest.thinking_policy,
                    chat_template_policy=manifest.chat_template_policy,
                ),
            ),
        )
        for reference, artifact_type, expected_model in expected:
            artifact = self._artifact_repository.get(reference.artifact_id)
            if (
                artifact.envelope.artifact_type != artifact_type
                or artifact.envelope.payload_hash != reference.payload_hash
                or artifact.payload_document.model_dump(mode="json")["payload"]
                != expected_model.model_dump(
                    mode="json", round_trip=True, warnings="error"
                )
            ):
                raise ValueError("runtime host evidence artifact binding is invalid")

    def _persist_host_attestation(
        self, request: LlmStartRequest
    ) -> tuple[ArtifactId, str]:
        attestation = request.host_attestation
        parent_ids = tuple(
            sorted(
                (
                    attestation.runtime_manifest_ref.artifact_id,
                    attestation.process_evidence_ref.artifact_id,
                    attestation.package_evidence_ref.artifact_id,
                    attestation.model_snapshot_evidence_ref.artifact_id,
                ),
                key=lambda item: item.root.encode("utf-8"),
            )
        )
        result = self._artifact_repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": "runtime_host_attestation",
                "payload_bytes": canonical_json_bytes(
                    attestation.model_dump(
                        mode="json", round_trip=True, warnings="error"
                    )
                ),
                "parent_artifact_ids": [item.root for item in parent_ids],
                "created_by": "principal_local_llm_runtime",
                "created_at": attestation.observed_at,
                "source_evidence_ids": [],
            }
        )
        persisted = self._artifact_repository.get(result.artifact_id)
        if (
            persisted.envelope.artifact_type != "runtime_host_attestation"
            or persisted.envelope.schema_version
            != "automarkov.runtime-host-attestation.v3"
            or persisted.envelope.payload_hash != result.payload_hash.root
            or persisted.envelope.parent_artifact_ids != parent_ids
            or result.payload_hash.root != attestation.payload_hash
            or persisted.payload_document.model_dump(mode="json")["payload"]
            != attestation.model_dump(mode="json", round_trip=True, warnings="error")
        ):
            raise ValueError("persisted runtime attestation failed revalidation")
        return result.artifact_id, result.payload_hash.root

    def _persist_probe_evidence(
        self,
        *,
        manifest: LocalLlmRuntimeManifest,
        served_model_name: str | None,
        health_response_hash: str | None,
        missing_auth_response_hash: str | None,
        invalid_auth_response_hash: str | None,
        models_response_hash: str | None,
        canary_request_hash: str | None,
        canary_response_hash: str | None,
    ) -> tuple[ArtifactId, Sha256Digest]:
        manifest_artifact_id = self._manifest_artifact_id
        attestation_artifact_id = self._attestation_artifact_id
        attestation_payload_hash = self._attestation_payload_hash
        values = (
            served_model_name,
            health_response_hash,
            missing_auth_response_hash,
            invalid_auth_response_hash,
            models_response_hash,
            canary_request_hash,
            canary_response_hash,
        )
        if (
            manifest_artifact_id is None
            or attestation_artifact_id is None
            or attestation_payload_hash is None
            or any(value is None for value in values)
        ):
            raise ValueError("probe evidence is incomplete")
        evidence = RuntimeProbeEvidence(
            schema_version="automarkov.runtime-probe-evidence.v3",
            runtime_manifest_ref=RuntimeArtifactReference(
                artifact_id=manifest_artifact_id,
                payload_hash=self._require_manifest_payload_hash(),
            ),
            runtime_host_attestation_ref=RuntimeArtifactReference(
                artifact_id=attestation_artifact_id,
                payload_hash=attestation_payload_hash,
            ),
            served_model_name=cast(str, served_model_name),
            health_response_hash=cast(str, health_response_hash),
            missing_auth_response_hash=cast(str, missing_auth_response_hash),
            invalid_auth_response_hash=cast(str, invalid_auth_response_hash),
            models_response_hash=cast(str, models_response_hash),
            canary_request_hash=cast(str, canary_request_hash),
            canary_response_hash=cast(str, canary_response_hash),
        )
        result = self._artifact_repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": "runtime_probe_evidence",
                "payload_bytes": canonical_json_bytes(
                    evidence.model_dump(mode="json", round_trip=True, warnings="error")
                ),
                "parent_artifact_ids": sorted(
                    [manifest_artifact_id.root, attestation_artifact_id.root]
                ),
                "created_by": "principal_local_llm_runtime",
                "created_at": self._creation_timestamp(),
                "source_evidence_ids": [],
            }
        )
        persisted = self._artifact_repository.get(result.artifact_id)
        if (
            persisted.envelope.artifact_type != "runtime_probe_evidence"
            or persisted.artifact_id != result.artifact_id
            or persisted.envelope.schema_version
            != "automarkov.runtime-probe-evidence.v3"
            or persisted.envelope.payload_hash != result.payload_hash.root
            or persisted.envelope.parent_artifact_ids
            != tuple(
                sorted(
                    (manifest_artifact_id, attestation_artifact_id),
                    key=lambda item: item.root.encode("utf-8"),
                )
            )
            or result.payload_hash.root != evidence.payload_hash
            or persisted.payload_document.model_dump(mode="json")["payload"]
            != evidence.model_dump(mode="json", round_trip=True, warnings="error")
        ):
            raise ValueError("persisted probe evidence failed revalidation")
        return result.artifact_id, result.payload_hash

    def _manifest_ref(self) -> RuntimeArtifactReference:
        artifact_id = self._manifest_artifact_id
        if artifact_id is None:
            raise LocalLlmRuntimeStateError("NOT_STARTED")
        return RuntimeArtifactReference(
            artifact_id=artifact_id,
            payload_hash=self._require_manifest_payload_hash(),
        )

    def _creation_timestamp(self) -> str:
        observed = self._clock()
        if (
            type(observed) is not datetime
            or observed.tzinfo is None
            or observed.utcoffset() is None
        ):
            raise ValueError("runtime clock must return an aware datetime")
        utc_value = observed.astimezone(UTC)
        whole, fraction = (
            utc_value.isoformat(timespec="microseconds")
            .removesuffix("+00:00")
            .split(".")
        )
        canonical_fraction = fraction.rstrip("0")
        return f"{whole}.{canonical_fraction}Z" if canonical_fraction else f"{whole}Z"

    def _probe_ref(self) -> RuntimeArtifactReference:
        artifact_id = self._probe_evidence_artifact_id
        payload_hash = self._probe_evidence_payload_hash
        if artifact_id is None or payload_hash is None:
            raise LocalLlmRuntimeStateError("WAITING_RUNTIME")
        return RuntimeArtifactReference(
            artifact_id=artifact_id,
            payload_hash=payload_hash,
        )

    def _persist_completion_response(
        self,
        response: LlmCompletionResponseArtifact,
        *,
        created_at: str,
    ) -> tuple[ArtifactId, Sha256Digest]:
        parents = tuple(
            sorted(
                (
                    response.runtime_manifest_ref.artifact_id,
                    response.runtime_probe_evidence_ref.artifact_id,
                    response.prompt_ref.artifact_id,
                ),
                key=lambda item: item.root.encode("utf-8"),
            )
        )
        result = self._artifact_repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": "llm_completion_response",
                "payload_bytes": canonical_json_bytes(
                    response.model_dump(mode="json", round_trip=True, warnings="error")
                ),
                "parent_artifact_ids": [item.root for item in parents],
                "created_by": "principal_local_llm_runtime",
                "created_at": created_at,
                "source_evidence_ids": [],
            }
        )
        persisted = self._artifact_repository.get(result.artifact_id)
        if (
            persisted.envelope.artifact_type != "llm_completion_response"
            or persisted.envelope.schema_version
            != "automarkov.llm-completion-response-artifact.v1"
            or persisted.envelope.payload_hash != result.payload_hash.root
            or persisted.envelope.parent_artifact_ids != parents
            or result.payload_hash.root != response.payload_hash
            or persisted.payload_document.model_dump(mode="json")["payload"]
            != response.model_dump(mode="json", round_trip=True, warnings="error")
        ):
            raise ValueError("persisted LLM response failed revalidation")
        return result.artifact_id, result.payload_hash

    def _persist_completion_trace(
        self,
        trace: LlmCompletionTrace,
        *,
        created_at: str,
    ) -> tuple[ArtifactId, Sha256Digest]:
        parents = tuple(
            sorted(
                (
                    trace.runtime_manifest_ref.artifact_id,
                    trace.runtime_probe_evidence_ref.artifact_id,
                    trace.prompt_ref.artifact_id,
                    trace.response_ref.artifact_id,
                ),
                key=lambda item: item.root.encode("utf-8"),
            )
        )
        result = self._artifact_repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": "llm_completion_trace",
                "payload_bytes": canonical_json_bytes(
                    trace.model_dump(mode="json", round_trip=True, warnings="error")
                ),
                "parent_artifact_ids": [item.root for item in parents],
                "created_by": "principal_local_llm_runtime",
                "created_at": created_at,
                "source_evidence_ids": [],
            }
        )
        persisted = self._artifact_repository.get(result.artifact_id)
        if (
            persisted.envelope.artifact_type != "llm_completion_trace"
            or persisted.envelope.schema_version != "automarkov.llm-completion-trace.v2"
            or persisted.envelope.payload_hash != result.payload_hash.root
            or persisted.envelope.parent_artifact_ids != parents
            or result.payload_hash.root != trace.payload_hash
            or persisted.payload_document.model_dump(mode="json")["payload"]
            != trace.model_dump(mode="json", round_trip=True, warnings="error")
        ):
            raise ValueError("persisted LLM trace failed revalidation")
        return result.artifact_id, result.payload_hash

    def _count_prompt_tokens(
        self,
        request: LlmCompletionRequest,
        manifest: LocalLlmRuntimeManifest,
        manifest_ref: RuntimeArtifactReference,
    ) -> int:
        messages = [
            message.model_dump(mode="json", warnings="error")
            for message in request.prompt.messages
        ]
        return self._count_rendered_prompt_tokens(messages, manifest, manifest_ref)

    def _count_rendered_prompt_tokens(
        self,
        messages: Sequence[Mapping[str, object]],
        manifest: LocalLlmRuntimeManifest,
        manifest_ref: RuntimeArtifactReference,
    ) -> int:
        body = canonical_json_bytes(
            {
                "model": manifest.served_model_name,
                "messages": messages,
                "add_generation_prompt": True,
                "continue_final_message": False,
                "add_special_tokens": False,
                "chat_template_kwargs": {"enable_thinking": False},
            }
        )
        response = self._request(
            "POST",
            self._health_url(manifest).removesuffix("/health") + "/tokenize",
            manifest=manifest,
            manifest_ref=manifest_ref,
            credential=None,
            body=body,
        ).response
        if response.status != 200 or response.content_type != "application/json":
            raise ValueError("runtime tokenizer request failed")
        payload = _object(parse_json_payload(response.body), "tokenizer response")
        count = _exact_nonnegative_int(payload.get("count"), "tokenizer count")
        max_model_len = _exact_nonnegative_int(
            payload.get("max_model_len"), "tokenizer max_model_len"
        )
        tokens = _sequence(payload.get("tokens"), "tokenizer tokens")
        if (
            max_model_len != manifest.max_model_len
            or len(tokens) != count
            or any(_exact_nonnegative_int(token, "token ID") < 0 for token in tokens)
        ):
            raise ValueError("runtime tokenizer identity drifted")
        return count

    def _request(
        self,
        method: str,
        url: str,
        *,
        manifest: LocalLlmRuntimeManifest,
        manifest_ref: RuntimeArtifactReference,
        credential: Callable[[], str] | None,
        body: bytes | None = None,
    ) -> _VerifiedHttpResponse:
        headers: dict[str, str] = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        binding = RuntimeHttpRequestBinding(
            method=method,
            url=url,
            body_hash="sha256:" + sha256(body or b"").hexdigest(),
        )
        challenge = urlsafe_b64encode(token_bytes(32)).decode("ascii").rstrip("=")
        verified = self._connection_provider.open_verified(
            expectation=RuntimeConnectionExpectation(
                runtime_manifest_artifact_id=manifest_ref.artifact_id.root,
                runtime_manifest_payload_hash=manifest_ref.payload_hash,
                listener_identity_hash=manifest.listener_identity_hash,
                process_identity_hash=manifest.process_identity_hash,
                relay_identity_hash=manifest.relay_identity_hash,
                route_policy_hash=manifest.route_policy_hash,
            ),
            binding=binding,
            challenge=challenge,
        )
        try:
            evidence = verified.evidence
            if type(evidence) is not CurrentRuntimeConnectionEvidence:
                raise CurrentRuntimeConnectionIdentityError(
                    "runtime connection evidence has an invalid type"
                )
            if (
                evidence.challenge != challenge
                or evidence.request_binding_hash != binding.binding_hash
                or evidence.listener_identity_hash != manifest.listener_identity_hash
                or evidence.process_identity_hash != manifest.process_identity_hash
                or evidence.relay_identity_hash != manifest.relay_identity_hash
                or evidence.route_policy_hash != manifest.route_policy_hash
                or len(evidence.evidence_hash) != 71
                or not evidence.evidence_hash.startswith("sha256:")
                or any(
                    character not in "0123456789abcdef"
                    for character in evidence.evidence_hash.removeprefix("sha256:")
                )
            ):
                raise CurrentRuntimeConnectionIdentityError(
                    "current runtime connection identity is invalid"
                )
            if credential is not None:
                headers["Authorization"] = "Bearer " + credential()
            response = verified.request(
                headers=headers,
                body=body,
                timeout_seconds=manifest.request_timeout_seconds,
            )
        finally:
            verified.close()
        if (
            type(response) is not HttpResponse
            or type(response.status) is not int
            or response.status < 100
            or response.status > 599
            or type(response.body) is not bytes
            or len(response.body) > _MAX_HTTP_RESPONSE_BYTES
            or (
                response.content_type is not None
                and type(response.content_type) is not str
            )
        ):
            raise ValueError("runtime transport returned an invalid bounded response")
        return _VerifiedHttpResponse(
            response=response,
            connection_evidence_hash=evidence.evidence_hash,
        )

    def _parse_models(
        self,
        body: bytes,
        manifest: LocalLlmRuntimeManifest,
    ) -> str:
        del manifest
        payload = _object(parse_json_payload(body), "models response")
        if payload.get("object") != "list":
            raise ValueError("models response object kind is invalid")
        models = _sequence(payload.get("data"), "models data")
        identifiers: list[str] = []
        for raw_model in models:
            model = _object(raw_model, "model entry")
            if model.get("object") != "model":
                raise ValueError("model card object kind is invalid")
            _exact_nonnegative_int(model.get("created"), "model created")
            _exact_string(model.get("owned_by"), "model owner")
            identifiers.append(_exact_string(model.get("id"), "model ID"))
        if len(identifiers) != 1:
            raise ValueError("models response must contain exactly one served identity")
        return identifiers[0]

    def _parse_completion(
        self,
        body: bytes,
        manifest: LocalLlmRuntimeManifest,
    ) -> tuple[LlmResponsePayload, LlmUsage]:
        payload = _object(parse_json_payload(body), "completion response")
        if payload.get("object") != "chat.completion":
            raise ValueError("completion response object kind is invalid")
        _exact_string(payload.get("id"), "completion ID")
        _exact_nonnegative_int(payload.get("created"), "completion created")
        if (
            _exact_string(payload.get("model"), "completion model")
            != manifest.served_model_name
        ):
            raise ValueError("completion response model identity drifted")
        choices = _sequence(payload.get("choices"), "completion choices")
        if len(choices) != 1:
            raise ValueError("completion response must contain exactly one choice")
        choice = _object(choices[0], "completion choice")
        if _exact_nonnegative_int(choice.get("index"), "choice index") != 0:
            raise ValueError("completion choice index must be zero")
        message = _object(choice.get("message"), "completion message")
        if message.get("role") != "assistant":
            raise ValueError("completion message role is invalid")
        raw_content = message.get("content")
        content = "" if raw_content is None else _exact_string(raw_content, "content")
        lowered_content = content.lower()
        if any(marker in lowered_content for marker in _DISABLED_THINKING_MARKERS):
            raise ValueError("completion content contains disabled thinking markup")
        raw_tool_calls = message.get("tool_calls", [])
        tool_calls: list[LlmToolCall] = []
        for raw_call in _sequence(raw_tool_calls, "tool calls"):
            call = _object(raw_call, "tool call")
            if call.get("type") != "function":
                raise ValueError("tool call type is invalid")
            function = _object(call.get("function"), "tool function")
            arguments_text = _exact_string(function.get("arguments"), "tool arguments")
            arguments = parse_json_payload(arguments_text.encode("utf-8"))
            if type(arguments) is not dict:
                raise ValueError("tool arguments must be a JSON object")
            tool_calls.append(
                LlmToolCall(
                    call_id=_exact_string(call.get("id"), "tool call ID"),
                    name=_exact_string(function.get("name"), "tool name"),
                    arguments=arguments,
                )
            )
        finish_reason = _exact_string(choice.get("finish_reason"), "finish reason")
        if finish_reason not in {"stop", "length", "tool_calls"}:
            raise ValueError("completion finish reason is not allowed")
        response = LlmResponsePayload(
            schema_version="automarkov.llm-response.v1",
            content=content,
            tool_calls=tuple(tool_calls),
            finish_reason=cast(_FinishReason, finish_reason),
        )
        raw_usage = _object(payload.get("usage"), "completion usage")
        usage = LlmUsage(
            prompt_tokens=_exact_nonnegative_int(
                raw_usage.get("prompt_tokens"), "prompt tokens"
            ),
            completion_tokens=_exact_nonnegative_int(
                raw_usage.get("completion_tokens"), "completion tokens"
            ),
            total_tokens=_exact_nonnegative_int(
                raw_usage.get("total_tokens"), "total tokens"
            ),
        )
        return response, usage

    def _require_manifest(self) -> LocalLlmRuntimeManifest:
        if self._manifest is None:
            raise LocalLlmRuntimeStateError("NOT_STARTED")
        return self._manifest

    def _require_manifest_payload_hash(self) -> str:
        payload_hash = self._manifest_payload_hash
        if payload_hash is None:
            raise LocalLlmRuntimeStateError("NOT_STARTED")
        return payload_hash

    def _verify_attestation(self) -> None:
        manifest = self._require_manifest()
        attestation = self._attestation
        if attestation is None:
            raise ValueError("runtime host attestation is unavailable")
        self._attestation_verifier.verify(manifest, attestation)

    def _load_manifest_credential(self, manifest: LocalLlmRuntimeManifest) -> str:
        token = _load_credential(self._repository_root)
        if _credential_fingerprint(token) != manifest.credential_fingerprint:
            raise ValueError("runtime credential fingerprint does not match")
        return token

    def _probe_credential(self, manifest: LocalLlmRuntimeManifest) -> str:
        try:
            return self._load_manifest_credential(manifest)
        except (OSError, subprocess.SubprocessError, ValueError):
            raise _ProbeFailure("credential_invalid") from None

    @staticmethod
    def _health_url(manifest: LocalLlmRuntimeManifest) -> str:
        parsed = urlsplit(manifest.base_url)
        return f"{parsed.scheme}://{parsed.netloc}/health"


__all__ = [
    "AttachedLocalLlmRuntime",
    "CurrentRuntimeConnectionEvidence",
    "CurrentRuntimeConnectionIdentityError",
    "CurrentRuntimeConnectionProvider",
    "HttpResponse",
    "LocalLlmRuntimeCapacityError",
    "LocalLlmRuntimeStateError",
    "PrivilegedUnixRuntimeConnectionProvider",
    "RuntimeAttestationVerifier",
    "RuntimeConnectionExpectation",
    "RuntimeEvidenceResolver",
    "RuntimeHttpRequestBinding",
    "SignedRuntimeAttestationVerifier",
    "VerifiedRuntimeConnection",
]
