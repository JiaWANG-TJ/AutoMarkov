from __future__ import annotations

import base64
import math
import os
import secrets
import socket
import ssl
import stat
import struct
from binascii import Error as Base64Error
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Lock
from typing import Literal, Protocol, cast

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import (
    ExtendedKeyUsageOID,
    ExtensionOID,
    SignatureAlgorithmOID,
)

from automarkov.contracts.remote_env import (
    BoxSpace,
    DiscreteSpace,
    MultiAgentStepResultPayload,
    RemoteEnvCapabilityGrant,
    RemoteEnvCertificateIdentity,
    RemoteEnvClientHello,
    RemoteEnvEnvelope,
    RemoteEnvFrameHeader,
    RemoteEnvHandshake,
    RemoteEnvRunnerGrantPolicy,
    RemoteEnvServerHello,
    RemoteEnvSessionTranscript,
    RemoteEnvTlsEndpoint,
    StepResultPayload,
    TensorDescriptor,
)
from automarkov.domain.canonical import (
    MAX_JSON_PAYLOAD_BYTES,
    canonical_json_bytes,
    parse_json_payload,
)
from automarkov.remote_env_codec import (
    decode_remote_env_frame,
    encode_remote_env_frame,
    make_tensor_descriptor,
    remote_env_transition_hash,
)

_CERTIFICATE_URI_PREFIX = "urn:automarkov:remote-env:v1:"
_CARTPOLE_ANGLE_OBSERVATION_LIMIT = 2 * (12 * 2 * math.pi / 360)
_LENGTH_PREFIX = struct.Struct(">Q")
_MAX_HANDSHAKE_BYTES = 1_048_576


class RemoteEnvRuntimeUnavailable(RuntimeError):
    """可信运行时材料或 TLS socket 暂不可用。"""

    state: Literal["WAITING_RUNTIME"] = "WAITING_RUNTIME"


class RemoteEnvSecurityError(ValueError):
    """RemoteEnv 的认证身份或 closed protocol 绑定无效。"""


def _digest(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


REMOTE_ENV_GRANT_SCHEMA_HASH = _digest(
    canonical_json_bytes(RemoteEnvCapabilityGrant.model_json_schema())
)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_b64url(value: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, Base64Error) as error:
        raise ValueError("value is not canonical unpadded base64url") from error
    if _b64url(decoded) != value:
        raise ValueError("value is not canonical unpadded base64url")
    return decoded


def remote_env_certificate_identity_uri(
    identity: RemoteEnvCertificateIdentity,
) -> str:
    payload = identity.model_dump(mode="json", round_trip=True, warnings="error")
    return _CERTIFICATE_URI_PREFIX + _b64url(canonical_json_bytes(payload))


def parse_remote_env_certificate_identity(
    uri: str,
    *,
    expected: RemoteEnvCertificateIdentity,
) -> RemoteEnvCertificateIdentity:
    if type(uri) is not str or not uri.startswith(_CERTIFICATE_URI_PREFIX):
        raise ValueError("certificate SAN does not contain the RemoteEnv identity URI")
    encoded = uri.removeprefix(_CERTIFICATE_URI_PREFIX)
    raw = _decode_b64url(encoded)
    parsed = parse_json_payload(raw)
    if type(parsed) is not dict or canonical_json_bytes(parsed) != raw:
        raise ValueError("certificate identity must use unique JCS bytes")
    identity = RemoteEnvCertificateIdentity.model_validate(parsed, strict=True)
    if identity != expected or remote_env_certificate_identity_uri(identity) != uri:
        raise ValueError("certificate identity does not match the frozen profile graph")
    return identity


def tls13_context(*, server: bool) -> ssl.SSLContext:
    protocol = ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT
    context = ssl.SSLContext(protocol)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = False
    return context


def verify_remote_env_leaf_certificate(
    certificate: x509.Certificate,
    issuer_certificate: x509.Certificate,
    *,
    expected_identity: RemoteEnvCertificateIdentity,
    peer_role: Literal["client", "server"],
    expected_fingerprint: str,
    expected_serial: int,
    revoked: bool,
    now: datetime,
) -> None:
    """按冻结的 Ed25519 leaf profile 验证 mTLS 身份，任何扩展漂移均拒绝。"""

    issuer_key = issuer_certificate.public_key()
    leaf_key = certificate.public_key()
    if not isinstance(issuer_key, Ed25519PublicKey) or not isinstance(
        leaf_key, Ed25519PublicKey
    ):
        raise ValueError(  # noqa: TRY004 - 安全合同统一 fail-closed 为 ValueError
            "RemoteEnv certificates require Ed25519 keys"
        )
    if (
        revoked
        or expected_serial.bit_length() != 159
        or certificate.serial_number != expected_serial
    ):
        raise ValueError("RemoteEnv certificate is revoked or has an unexpected serial")
    if (
        certificate.signature_algorithm_oid != SignatureAlgorithmOID.ED25519
        or issuer_certificate.signature_algorithm_oid != SignatureAlgorithmOID.ED25519
    ):
        raise ValueError("RemoteEnv certificates require Ed25519 signatures")
    if (
        certificate.subject != x509.Name([])
        or certificate.issuer != issuer_certificate.subject
    ):
        raise ValueError("RemoteEnv leaf subject or issuer is invalid")
    issuer_key.verify(certificate.signature, certificate.tbs_certificate_bytes)
    if _digest(certificate.public_bytes(Encoding.DER)) != expected_fingerprint:
        raise ValueError("RemoteEnv certificate fingerprint mismatch")
    expected_oids = {
        ExtensionOID.BASIC_CONSTRAINTS,
        ExtensionOID.KEY_USAGE,
        ExtensionOID.EXTENDED_KEY_USAGE,
        ExtensionOID.SUBJECT_KEY_IDENTIFIER,
        ExtensionOID.AUTHORITY_KEY_IDENTIFIER,
        ExtensionOID.SUBJECT_ALTERNATIVE_NAME,
    }
    if {extension.oid for extension in certificate.extensions} != expected_oids:
        raise ValueError("RemoteEnv leaf contains missing or unknown extensions")
    basic = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
    if not basic.critical or basic.value.ca or basic.value.path_length is not None:
        raise ValueError("RemoteEnv leaf BasicConstraints profile mismatch")
    usage = certificate.extensions.get_extension_for_class(x509.KeyUsage)
    usage_bits = (
        usage.value.digital_signature,
        usage.value.content_commitment,
        usage.value.key_encipherment,
        usage.value.data_encipherment,
        usage.value.key_agreement,
        usage.value.key_cert_sign,
        usage.value.crl_sign,
    )
    if not usage.critical or usage_bits != (
        True,
        False,
        False,
        False,
        False,
        False,
        False,
    ):
        raise ValueError("RemoteEnv leaf KeyUsage profile mismatch")
    eku = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    expected_eku = (
        ExtendedKeyUsageOID.CLIENT_AUTH
        if peer_role == "client"
        else ExtendedKeyUsageOID.SERVER_AUTH
    )
    if eku.critical or tuple(eku.value) != (expected_eku,):
        raise ValueError("RemoteEnv leaf ExtendedKeyUsage profile mismatch")
    ski = certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
    derived_ski = x509.SubjectKeyIdentifier.from_public_key(leaf_key)
    if ski.critical or ski.value.digest != derived_ski.digest:
        raise ValueError("RemoteEnv leaf SKI mismatch")
    issuer_ski_extension = issuer_certificate.extensions.get_extension_for_class(
        x509.SubjectKeyIdentifier
    )
    derived_issuer_ski = x509.SubjectKeyIdentifier.from_public_key(issuer_key)
    if issuer_ski_extension.value.digest != derived_issuer_ski.digest:
        raise ValueError("RemoteEnv issuer SKI mismatch")
    aki = certificate.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier)
    if (
        aki.critical
        or aki.value.key_identifier != derived_issuer_ski.digest
        or aki.value.authority_cert_issuer is not None
        or aki.value.authority_cert_serial_number is not None
    ):
        raise ValueError("RemoteEnv leaf AKI mismatch")
    san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    names = tuple(san.value)
    if (
        not san.critical
        or len(names) != 1
        or not isinstance(names[0], x509.UniformResourceIdentifier)
    ):
        raise ValueError("RemoteEnv leaf SAN must contain exactly one URI")
    parse_remote_env_certificate_identity(names[0].value, expected=expected_identity)
    if now.tzinfo is None:
        raise ValueError("RemoteEnv certificate verifier requires an aware UTC clock")
    moment = now.astimezone(UTC)
    if not (
        certificate.not_valid_before_utc <= moment < certificate.not_valid_after_utc
        and issuer_certificate.not_valid_before_utc
        <= moment
        < issuer_certificate.not_valid_after_utc
    ):
        raise ValueError("RemoteEnv certificate is outside its validity window")


def remote_env_session_id(transcript: RemoteEnvSessionTranscript) -> str:
    payload = transcript.model_dump(mode="json", round_trip=True, warnings="error")
    return _digest(canonical_json_bytes(payload))


def _session_peer_matches_identity(
    peer: object,
    identity: RemoteEnvCertificateIdentity,
) -> bool:
    return (
        getattr(peer, "process_execution_id", None) == identity.process_execution_id
        and getattr(peer, "profile_id", None) == identity.profile_id
        and getattr(peer, "principal_id", None) == identity.principal_id
    )


def verify_remote_env_handshake(
    handshake: RemoteEnvHandshake,
    transcript: RemoteEnvSessionTranscript,
    *,
    expected_client_identity: RemoteEnvCertificateIdentity,
    expected_server_identity: RemoteEnvCertificateIdentity,
    expected_profile_lock_hash: str,
    expected_image_digest: str,
    expected_environment_id: str,
) -> None:
    """闭合握手与已认证 transcript/profile graph，拒绝 caller 自报身份。"""

    fingerprints = handshake.peer_certificate_fingerprints
    if (
        expected_client_identity.experiment_id != expected_server_identity.experiment_id
        or expected_client_identity.run_id != expected_server_identity.run_id
        or transcript.experiment_id != expected_client_identity.experiment_id
        or transcript.run_id != expected_client_identity.run_id
        or not _session_peer_matches_identity(
            transcript.client, expected_client_identity
        )
        or not _session_peer_matches_identity(
            transcript.server, expected_server_identity
        )
        or handshake.session_id.root != remote_env_session_id(transcript)
        or handshake.run_id != transcript.run_id
        or handshake.profile_graph_hash != transcript.profile_graph_hash
        or handshake.process_execution_id
        != expected_server_identity.process_execution_id
        or handshake.profile_id != expected_server_identity.profile_id
        or handshake.principal_id != expected_server_identity.principal_id
        or handshake.run_id != expected_server_identity.run_id
        or handshake.profile_lock_hash.root != expected_profile_lock_hash
        or handshake.image_digest.root != expected_image_digest
        or handshake.environment_id != expected_environment_id
        or fingerprints["client"] != transcript.client.certificate_fingerprint
        or fingerprints["server"] != transcript.server.certificate_fingerprint
    ):
        raise ValueError(
            "RemoteEnv handshake does not match the authenticated profile graph"
        )


def remote_env_grant_preimage(grant: RemoteEnvCapabilityGrant) -> bytes:
    payload = grant.model_dump(mode="json", round_trip=True, warnings="error")
    del payload["signature_b64url"]
    return canonical_json_bytes(payload)


def sign_remote_env_grant(
    fields: Mapping[str, object],
    signing_key: Ed25519PrivateKey,
) -> RemoteEnvCapabilityGrant:
    payload = dict(fields)
    if "signature_b64url" in payload:
        raise ValueError("grant signer owns the signature field")
    payload["signature_b64url"] = _b64url(bytes(64))
    provisional = RemoteEnvCapabilityGrant.model_validate(payload, strict=True)
    payload["signature_b64url"] = _b64url(
        signing_key.sign(remote_env_grant_preimage(provisional))
    )
    return RemoteEnvCapabilityGrant.model_validate(payload, strict=True)


def verify_remote_env_grant_binding(
    grant: RemoteEnvCapabilityGrant,
    *,
    signing_key_id: str,
    signing_public_key: Ed25519PublicKey,
    session_id: str,
    source_identity: RemoteEnvCertificateIdentity,
    target_identity: RemoteEnvCertificateIdentity,
    environment_id: str,
    profile_graph_hash: str,
    expected_role: Literal["actor", "critic", "evaluator"],
    now: datetime,
    runner_policy: RemoteEnvRunnerGrantPolicy | None = None,
) -> None:
    """验证签名 grant 对双方证书身份与 session 的完整绑定。"""

    source_is_sealed = source_identity.profile_id.startswith("sealed-")
    target_is_sealed = target_identity.profile_id.startswith("sealed-")
    if expected_role == "evaluator":
        legal_topology = source_identity.profile_id.startswith(
            "sealed-evaluator-"
        ) and target_identity.profile_id.startswith("sealed-env-")
    else:
        legal_topology = not source_is_sealed and not target_is_sealed
    if not legal_topology:
        raise RemoteEnvSecurityError(
            "RemoteEnv grant topology is not a closed profile graph edge"
        )

    if runner_policy is not None:
        if (
            runner_policy.signing_key_id != signing_key_id
            or runner_policy.grant_schema_hash.root != REMOTE_ENV_GRANT_SCHEMA_HASH
            or runner_policy.key_revoked
        ):
            raise RemoteEnvSecurityError(
                "RemoteEnv runner key or grant schema policy mismatch"
            )
        if now.tzinfo is None:
            raise RemoteEnvSecurityError(
                "RemoteEnv verifier requires an aware UTC clock"
            )
        moment = now.astimezone(UTC)
        skew = timedelta(seconds=runner_policy.clock_skew_seconds)
        key_start = datetime.fromisoformat(runner_policy.key_not_before)
        key_end = datetime.fromisoformat(runner_policy.key_expires_at)
        run_start = datetime.fromisoformat(runner_policy.run_not_before)
        run_end = datetime.fromisoformat(runner_policy.run_expires_at)
        grant_start = datetime.fromisoformat(grant.not_before)
        grant_end = datetime.fromisoformat(grant.expires_at)
        if (
            grant_start < run_start
            or grant_end > run_end
            or not key_start <= moment + skew
            or not moment - skew < key_end
            or not run_start <= moment + skew
            or not moment - skew < run_end
        ):
            raise RemoteEnvSecurityError(
                "RemoteEnv runner key, run window, or grant window is invalid"
            )
    try:
        signature = _decode_b64url(grant.signature_b64url)
        if len(signature) != 64 or grant.signing_key_id != signing_key_id:
            raise RemoteEnvSecurityError("RemoteEnv grant signing identity mismatch")
        signing_public_key.verify(signature, remote_env_grant_preimage(grant))
    except (InvalidSignature, ValueError) as error:
        if isinstance(error, RemoteEnvSecurityError):
            raise
        raise RemoteEnvSecurityError("RemoteEnv grant signature is invalid") from error
    if (
        source_identity.experiment_id != target_identity.experiment_id
        or source_identity.run_id != target_identity.run_id
        or grant.experiment_id != source_identity.experiment_id
        or grant.run_id != source_identity.run_id
        or grant.session_id.root != session_id
        or grant.profile_graph_hash.root != profile_graph_hash
        or grant.environment_id != environment_id
        or grant.role != expected_role
        or grant.source_process_execution_id != source_identity.process_execution_id
        or grant.source_profile_id != source_identity.profile_id
        or grant.source_principal_id != source_identity.principal_id
        or grant.target_process_execution_id != target_identity.process_execution_id
        or grant.target_profile_id != target_identity.profile_id
    ):
        raise RemoteEnvSecurityError(
            "RemoteEnv grant, peer, profile, or session mismatch"
        )
    if now.tzinfo is None:
        raise RemoteEnvSecurityError("RemoteEnv verifier requires an aware UTC clock")
    moment = now.astimezone(UTC)
    not_before = datetime.fromisoformat(grant.not_before)
    expires_at = datetime.fromisoformat(grant.expires_at)
    if not not_before <= moment < expires_at:
        raise RemoteEnvSecurityError("RemoteEnv grant is outside its validity window")


class RemoteEnvGrantVerifier:
    def __init__(
        self,
        *,
        signing_key_id: str,
        signing_public_key: Ed25519PublicKey,
        session_id: str,
        source_identity: RemoteEnvCertificateIdentity,
        target_identity: RemoteEnvCertificateIdentity,
        environment_id: str,
        profile_graph_hash: str,
        expected_role: Literal["actor", "critic", "evaluator"],
        topology_kind: Literal["trainer_environment", "sealed_evaluator_environment"]
        | None = None,
        runner_key_not_before: datetime | None = None,
        runner_key_expires_at: datetime | None = None,
        runner_key_revoked: bool = False,
        run_not_before: datetime | None = None,
        run_expires_at: datetime | None = None,
        clock_skew_seconds: int = 0,
        expected_grant_schema_hash: str = REMOTE_ENV_GRANT_SCHEMA_HASH,
    ) -> None:
        resolved_topology = topology_kind or (
            "sealed_evaluator_environment"
            if expected_role == "evaluator"
            else "trainer_environment"
        )
        source_is_sealed = source_identity.profile_id.startswith("sealed-")
        target_is_sealed = target_identity.profile_id.startswith("sealed-")
        legal_trainer = (
            resolved_topology == "trainer_environment"
            and expected_role in {"actor", "critic"}
            and not source_is_sealed
            and not target_is_sealed
        )
        legal_sealed = (
            resolved_topology == "sealed_evaluator_environment"
            and expected_role == "evaluator"
            and source_identity.profile_id.startswith("sealed-evaluator-")
            and target_identity.profile_id.startswith("sealed-env-")
        )
        if not (legal_trainer or legal_sealed):
            raise RemoteEnvSecurityError(
                "RemoteEnv topology is not one of the two closed profile graph edges"
            )
        if (
            type(clock_skew_seconds) is not int
            or clock_skew_seconds < 0
            or expected_grant_schema_hash != REMOTE_ENV_GRANT_SCHEMA_HASH
        ):
            raise RemoteEnvSecurityError(
                "RemoteEnv grant schema hash or clock-skew policy mismatch"
            )
        for boundary in (
            runner_key_not_before,
            runner_key_expires_at,
            run_not_before,
            run_expires_at,
        ):
            if boundary is not None and boundary.tzinfo is None:
                raise RemoteEnvSecurityError(
                    "RemoteEnv key and run windows require aware UTC timestamps"
                )
        if (
            runner_key_not_before is not None
            and runner_key_expires_at is not None
            and runner_key_not_before >= runner_key_expires_at
        ) or (
            run_not_before is not None
            and run_expires_at is not None
            and run_not_before >= run_expires_at
        ):
            raise RemoteEnvSecurityError("RemoteEnv key or run window is invalid")
        self._signing_key_id = signing_key_id
        self._signing_public_key = signing_public_key
        self._session_id = session_id
        self._source = source_identity
        self._target = target_identity
        self._environment_id = environment_id
        self._profile_graph_hash = profile_graph_hash
        self._expected_role: Literal["actor", "critic", "evaluator"] = expected_role
        self._runner_key_not_before = runner_key_not_before
        self._runner_key_expires_at = runner_key_expires_at
        self._runner_key_revoked = runner_key_revoked
        self._run_not_before = run_not_before
        self._run_expires_at = run_expires_at
        self._clock_skew = timedelta(seconds=clock_skew_seconds)
        self._last_sequence = 0
        self._consumed: set[tuple[str, int]] = set()
        self._revoked = False
        self._consume_lock = Lock()

    @property
    def revoked(self) -> bool:
        return self._revoked

    def verify_and_consume(
        self,
        header: RemoteEnvFrameHeader,
        *,
        now: datetime,
    ) -> None:
        if not self._consume_lock.acquire(blocking=False):
            self._revoked = True
            raise ValueError(
                "concurrent RemoteEnv grant consumption revoked the session"
            )
        try:
            self._verify_and_consume_locked(header, now=now)
        finally:
            self._consume_lock.release()

    def _verify_and_consume_locked(
        self,
        header: RemoteEnvFrameHeader,
        *,
        now: datetime,
    ) -> None:
        try:
            if self._revoked:
                raise ValueError("RemoteEnv session is revoked")
            grant = header.envelope.grant
            moment = now.astimezone(UTC) if now.tzinfo is not None else now
            grant_not_before = datetime.fromisoformat(grant.not_before)
            grant_expires_at = datetime.fromisoformat(grant.expires_at)
            key_not_before = self._runner_key_not_before or grant_not_before
            key_expires_at = self._runner_key_expires_at or grant_expires_at
            run_not_before = self._run_not_before or grant_not_before
            run_expires_at = self._run_expires_at or grant_expires_at
            if (
                now.tzinfo is None
                or self._runner_key_revoked
                or not key_not_before.astimezone(UTC) <= moment + self._clock_skew
                or not moment - self._clock_skew < key_expires_at.astimezone(UTC)
            ):
                raise ValueError(
                    "RemoteEnv runner signing key is revoked or outside validity"
                )
            if grant_not_before < run_not_before.astimezone(
                UTC
            ) or grant_expires_at > run_expires_at.astimezone(UTC):
                raise ValueError("RemoteEnv grant exceeds the frozen run window")
            verify_remote_env_grant_binding(
                grant,
                signing_key_id=self._signing_key_id,
                signing_public_key=self._signing_public_key,
                session_id=self._session_id,
                source_identity=self._source,
                target_identity=self._target,
                environment_id=self._environment_id,
                profile_graph_hash=self._profile_graph_hash,
                expected_role=self._expected_role,
                now=now,
            )
            envelope = header.envelope
            replay_key = (grant.grant_id, envelope.sequence)
            if (
                envelope.session_id.root != self._session_id
                or envelope.run_id != grant.run_id
                or envelope.source_process_execution_id
                != grant.source_process_execution_id
                or envelope.source_profile_id != grant.source_profile_id
                or envelope.source_principal_id != grant.source_principal_id
                or envelope.target_process_execution_id
                != grant.target_process_execution_id
                or envelope.target_profile_id != grant.target_profile_id
            ):
                raise ValueError("RemoteEnv grant, peer, profile, or session mismatch")
            if (
                header.message_kind not in grant.allowed_methods
                or replay_key in self._consumed
                or envelope.sequence != self._last_sequence + 1
                or envelope.sequence > grant.max_sequence
                or envelope.step_id > grant.max_step
            ):
                raise ValueError("RemoteEnv method, sequence, or step is unauthorized")
        except Exception as error:
            self._revoked = True
            if isinstance(error, ValueError):
                raise
            raise ValueError("RemoteEnv grant verification failed") from error
        self._last_sequence = header.envelope.sequence
        self._consumed.add((header.envelope.grant.grant_id, header.envelope.sequence))

    def revoke(self) -> None:
        self._revoked = True


def _open_trusted_material(locator: str, *, private_key: bool) -> int:
    if type(locator) is not str or not locator:
        raise RemoteEnvSecurityError("RemoteEnv material locator must be canonical")
    absolute = os.path.abspath(locator)
    real = os.path.realpath(locator)
    if locator != absolute or locator != real:
        raise RemoteEnvSecurityError("RemoteEnv material locator must be canonical")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(locator, flags)
    except OSError as error:
        raise RemoteEnvRuntimeUnavailable(
            "WAITING_RUNTIME: trusted RemoteEnv material is unavailable"
        ) from error
    try:
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if not stat.S_ISREG(metadata.st_mode):
            raise RemoteEnvSecurityError(
                "RemoteEnv material locator must resolve to a regular file"
            )
        if private_key and mode != 0o600:
            raise RemoteEnvSecurityError(
                "RemoteEnv private key material must have mode 0600"
            )
        if not private_key and mode & 0o022:
            raise RemoteEnvSecurityError(
                "RemoteEnv certificate material must not be group/world writable"
            )
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _read_public_certificate(descriptor: int) -> x509.Certificate:
    os.lseek(descriptor, 0, os.SEEK_SET)
    payload = bytearray()
    while len(payload) <= _MAX_HANDSHAKE_BYTES:
        chunk = os.read(
            descriptor, min(65_536, _MAX_HANDSHAKE_BYTES + 1 - len(payload))
        )
        if not chunk:
            break
        payload.extend(chunk)
    if not payload or len(payload) > _MAX_HANDSHAKE_BYTES:
        raise RemoteEnvSecurityError("RemoteEnv certificate material is invalid")
    try:
        return x509.load_pem_x509_certificate(bytes(payload))
    except ValueError as error:
        raise RemoteEnvSecurityError(
            "RemoteEnv certificate material is invalid"
        ) from error


def _receive_exact(channel: ssl.SSLSocket, count: int) -> bytes:
    output = bytearray()
    while len(output) < count:
        chunk = channel.recv(count - len(output))
        if not chunk:
            raise RemoteEnvSecurityError("RemoteEnv TLS stream ended mid-message")
        output.extend(chunk)
    return bytes(output)


def _receive_handshake(channel: ssl.SSLSocket) -> RemoteEnvHandshake:
    prefix = _receive_exact(channel, _LENGTH_PREFIX.size)
    length = _LENGTH_PREFIX.unpack(prefix)[0]
    if length < 2 or length > _MAX_HANDSHAKE_BYTES:
        raise RemoteEnvSecurityError("RemoteEnv handshake exceeds its byte ceiling")
    payload = _receive_exact(channel, length)
    parsed = parse_json_payload(payload)
    if type(parsed) is not dict or canonical_json_bytes(parsed) != payload:
        raise RemoteEnvSecurityError("RemoteEnv handshake must use unique JCS bytes")
    try:
        return RemoteEnvHandshake.model_validate(parsed, strict=True)
    except ValueError as error:
        raise RemoteEnvSecurityError("RemoteEnv handshake schema is invalid") from error


def _send_canonical_message(
    channel: ssl.SSLSocket,
    message: RemoteEnvClientHello | RemoteEnvServerHello,
) -> None:
    payload = canonical_json_bytes(
        message.model_dump(mode="json", round_trip=True, warnings="error")
    )
    if len(payload) > _MAX_HANDSHAKE_BYTES:
        raise RemoteEnvSecurityError("RemoteEnv session message exceeds byte ceiling")
    channel.sendall(_LENGTH_PREFIX.pack(len(payload)) + payload)


def _receive_typed_message(
    channel: ssl.SSLSocket,
    model: type[RemoteEnvClientHello | RemoteEnvServerHello],
) -> RemoteEnvClientHello | RemoteEnvServerHello:
    prefix = _receive_exact(channel, _LENGTH_PREFIX.size)
    length = _LENGTH_PREFIX.unpack(prefix)[0]
    if length < 2 or length > _MAX_HANDSHAKE_BYTES:
        raise RemoteEnvSecurityError("RemoteEnv session message exceeds byte ceiling")
    payload = _receive_exact(channel, length)
    parsed = parse_json_payload(payload)
    if type(parsed) is not dict or canonical_json_bytes(parsed) != payload:
        raise RemoteEnvSecurityError("RemoteEnv session message is not canonical")
    try:
        return model.model_validate(parsed, strict=True)
    except ValueError as error:
        raise RemoteEnvSecurityError(
            "RemoteEnv session message schema is invalid"
        ) from error


def _session_transcript(
    *,
    source: RemoteEnvCertificateIdentity,
    target: RemoteEnvCertificateIdentity,
    profile_graph_hash: str,
    client_fingerprint: str,
    server_fingerprint: str,
    client_nonce_b64url: str,
    server_nonce_b64url: str,
) -> RemoteEnvSessionTranscript:
    return RemoteEnvSessionTranscript.model_validate(
        {
            "domain": "AutoMarkov-RemoteEnv-Session-v1",
            "protocol_version": "automarkov.remote-env.v1",
            "experiment_id": source.experiment_id,
            "run_id": source.run_id,
            "profile_graph_hash": profile_graph_hash,
            "client": {
                "process_execution_id": source.process_execution_id,
                "profile_id": source.profile_id,
                "principal_id": source.principal_id,
                "certificate_fingerprint": client_fingerprint,
                "nonce_b64url": client_nonce_b64url,
            },
            "server": {
                "process_execution_id": target.process_execution_id,
                "profile_id": target.profile_id,
                "principal_id": target.principal_id,
                "certificate_fingerprint": server_fingerprint,
                "nonce_b64url": server_nonce_b64url,
            },
        },
        strict=True,
    )


def _receive_frame(
    channel: ssl.SSLSocket,
    *,
    max_frame_bytes: int,
    max_tensor_bytes: int,
) -> bytes:
    prefix = _receive_exact(channel, _LENGTH_PREFIX.size)
    header_length = _LENGTH_PREFIX.unpack(prefix)[0]
    if (
        header_length > MAX_JSON_PAYLOAD_BYTES
        or header_length + _LENGTH_PREFIX.size > max_frame_bytes
    ):
        raise RemoteEnvSecurityError(
            "RemoteEnv response header exceeds its byte ceiling"
        )
    header_bytes = _receive_exact(channel, header_length)
    parsed = parse_json_payload(header_bytes)
    if type(parsed) is not dict or canonical_json_bytes(parsed) != header_bytes:
        raise RemoteEnvSecurityError("RemoteEnv response header is not canonical")
    try:
        header = RemoteEnvFrameHeader.model_validate(parsed, strict=True)
    except ValueError as error:
        raise RemoteEnvSecurityError("RemoteEnv response header is invalid") from error
    tensor_bytes = sum(item.nbytes for item in header.tensors)
    if tensor_bytes > max_frame_bytes - _LENGTH_PREFIX.size - header_length:
        raise RemoteEnvSecurityError("RemoteEnv response exceeds its byte ceiling")
    frame = prefix + header_bytes + _receive_exact(channel, tensor_bytes)
    decode_remote_env_frame(
        frame,
        max_frame_bytes=max_frame_bytes,
        max_tensor_bytes=max_tensor_bytes,
    )
    return frame


def _receive_frame_or_eof(
    channel: ssl.SSLSocket,
    *,
    max_frame_bytes: int,
    max_tensor_bytes: int,
) -> bytes | None:
    first = channel.recv(_LENGTH_PREFIX.size)
    if not first:
        return None
    prefix = first + _receive_exact(channel, _LENGTH_PREFIX.size - len(first))
    header_length = _LENGTH_PREFIX.unpack(prefix)[0]
    if (
        header_length > MAX_JSON_PAYLOAD_BYTES
        or header_length + _LENGTH_PREFIX.size > max_frame_bytes
    ):
        raise RemoteEnvSecurityError(
            "RemoteEnv request header exceeds its byte ceiling"
        )
    header_bytes = _receive_exact(channel, header_length)
    parsed = parse_json_payload(header_bytes)
    if type(parsed) is not dict or canonical_json_bytes(parsed) != header_bytes:
        raise RemoteEnvSecurityError("RemoteEnv request header is not canonical")
    try:
        header = RemoteEnvFrameHeader.model_validate(parsed, strict=True)
    except ValueError as error:
        raise RemoteEnvSecurityError("RemoteEnv request header is invalid") from error
    tensor_bytes = sum(item.nbytes for item in header.tensors)
    if tensor_bytes > max_frame_bytes - _LENGTH_PREFIX.size - header_length:
        raise RemoteEnvSecurityError("RemoteEnv request exceeds its byte ceiling")
    frame = prefix + header_bytes + _receive_exact(channel, tensor_bytes)
    decode_remote_env_frame(
        frame,
        max_frame_bytes=max_frame_bytes,
        max_tensor_bytes=max_tensor_bytes,
    )
    return frame


class RemoteEnvByteWorker(Protocol):
    def exchange(self, canonical_frame: bytes) -> bytes: ...


class TlsSocketRemoteEnvServer:
    """在 worker 调用前完成真实 mTLS peer 与现场 session 的服务端 transport。"""

    def __init__(
        self,
        *,
        runner_ca_path: str,
        server_certificate_path: str,
        server_private_key_path: str,
        expected_client_identity: RemoteEnvCertificateIdentity,
        expected_server_identity: RemoteEnvCertificateIdentity,
        expected_client_fingerprint: str,
        expected_client_serial: int,
        client_certificate_revoked: bool,
        expected_server_fingerprint: str,
        expected_server_serial: int,
        server_certificate_revoked: bool,
        profile_graph_hash: str,
        expected_profile_lock_hash: str,
        expected_image_digest: str,
        expected_environment_id: str,
        grant_signing_key_id: str,
        grant_signing_public_key: Ed25519PublicKey,
        runner_grant_policy: RemoteEnvRunnerGrantPolicy,
        expected_role: Literal["actor", "critic", "evaluator"],
        grant_issuer: Callable[[str], RemoteEnvCapabilityGrant],
        handshake_builder: Callable[[RemoteEnvSessionTranscript], RemoteEnvHandshake],
        worker_factory: Callable[
            [RemoteEnvCapabilityGrant, RemoteEnvSessionTranscript],
            RemoteEnvByteWorker,
        ],
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._source = expected_client_identity
        self._target = expected_server_identity
        self._expected_client_fingerprint = expected_client_fingerprint
        self._expected_client_serial = expected_client_serial
        self._client_certificate_revoked = client_certificate_revoked
        self._expected_server_fingerprint = expected_server_fingerprint
        self._expected_server_serial = expected_server_serial
        self._server_certificate_revoked = server_certificate_revoked
        self._profile_graph_hash = profile_graph_hash
        self._expected_profile_lock_hash = expected_profile_lock_hash
        self._expected_image_digest = expected_image_digest
        self._expected_environment_id = expected_environment_id
        self._grant_signing_key_id = grant_signing_key_id
        self._grant_signing_public_key = grant_signing_public_key
        self._runner_grant_policy = runner_grant_policy
        self._expected_role: Literal["actor", "critic", "evaluator"] = expected_role
        self._grant_issuer = grant_issuer
        self._handshake_builder = handshake_builder
        self._worker_factory = worker_factory
        self._now_provider = now_provider or (lambda: datetime.now(tz=UTC))
        self._seen_client_nonces: set[str] = set()
        self._terminal_sessions: set[str] = set()
        self._state_lock = Lock()
        descriptors: list[int] = []
        try:
            ca_fd = _open_trusted_material(runner_ca_path, private_key=False)
            descriptors.append(ca_fd)
            cert_fd = _open_trusted_material(server_certificate_path, private_key=False)
            descriptors.append(cert_fd)
            key_fd = _open_trusted_material(server_private_key_path, private_key=True)
            descriptors.append(key_fd)
            self._context = tls13_context(server=True)
            self._context.load_verify_locations(cafile=f"/proc/self/fd/{ca_fd}")
            self._context.load_cert_chain(
                certfile=f"/proc/self/fd/{cert_fd}", keyfile=f"/proc/self/fd/{key_fd}"
            )
            self._issuer = _read_public_certificate(ca_fd)
            self._server_certificate = _read_public_certificate(cert_fd)
            verify_remote_env_leaf_certificate(
                self._server_certificate,
                self._issuer,
                expected_identity=self._target,
                peer_role="server",
                expected_fingerprint=self._expected_server_fingerprint,
                expected_serial=self._expected_server_serial,
                revoked=self._server_certificate_revoked,
                now=self._now_provider(),
            )
        except (OSError, ssl.SSLError, ValueError) as error:
            if isinstance(error, (RemoteEnvRuntimeUnavailable, RemoteEnvSecurityError)):
                raise
            raise RemoteEnvSecurityError(
                "RemoteEnv server TLS material is invalid"
            ) from error
        finally:
            for descriptor in descriptors:
                os.close(descriptor)

    def serve_connection(self, raw_socket: socket.socket) -> None:
        session_id: str | None = None
        try:
            with self._context.wrap_socket(raw_socket, server_side=True) as channel:
                if channel.version() != "TLSv1.3":
                    raise RemoteEnvSecurityError(
                        "RemoteEnv requires negotiated TLS 1.3"
                    )
                peer_der = channel.getpeercert(binary_form=True)
                if type(peer_der) is not bytes or not peer_der:
                    raise RemoteEnvSecurityError(
                        "RemoteEnv client certificate is missing"
                    )
                try:
                    peer_certificate = x509.load_der_x509_certificate(peer_der)
                except ValueError as error:
                    raise RemoteEnvSecurityError(
                        "RemoteEnv client certificate is invalid"
                    ) from error
                verify_remote_env_leaf_certificate(
                    peer_certificate,
                    self._issuer,
                    expected_identity=self._source,
                    peer_role="client",
                    expected_fingerprint=self._expected_client_fingerprint,
                    expected_serial=self._expected_client_serial,
                    revoked=self._client_certificate_revoked,
                    now=self._now_provider(),
                )
                received = _receive_typed_message(channel, RemoteEnvClientHello)
                if type(received) is not RemoteEnvClientHello or (
                    received.experiment_id != self._source.experiment_id
                    or received.run_id != self._source.run_id
                    or received.profile_graph_hash.root != self._profile_graph_hash
                    or received.process_execution_id
                    != self._source.process_execution_id
                    or received.profile_id != self._source.profile_id
                    or received.principal_id != self._source.principal_id
                    or received.certificate_fingerprint.root
                    != self._expected_client_fingerprint
                ):
                    raise RemoteEnvSecurityError(
                        "RemoteEnv client hello does not match its TLS certificate"
                    )
                server_nonce = secrets.token_bytes(32)
                with self._state_lock:
                    if received.nonce_b64url in self._seen_client_nonces:
                        raise RemoteEnvSecurityError(
                            "RemoteEnv client nonce replay is forbidden"
                        )
                    self._seen_client_nonces.add(received.nonce_b64url)
                transcript = _session_transcript(
                    source=self._source,
                    target=self._target,
                    profile_graph_hash=self._profile_graph_hash,
                    client_fingerprint=self._expected_client_fingerprint,
                    server_fingerprint=self._expected_server_fingerprint,
                    client_nonce_b64url=received.nonce_b64url,
                    server_nonce_b64url=_b64url(server_nonce),
                )
                session_id = remote_env_session_id(transcript)
                with self._state_lock:
                    if session_id in self._terminal_sessions:
                        raise RemoteEnvSecurityError(
                            "RemoteEnv terminal session cannot be reused"
                        )
                grant = self._grant_issuer(session_id)
                verify_remote_env_grant_binding(
                    grant,
                    signing_key_id=self._grant_signing_key_id,
                    signing_public_key=self._grant_signing_public_key,
                    session_id=session_id,
                    source_identity=self._source,
                    target_identity=self._target,
                    environment_id=self._expected_environment_id,
                    profile_graph_hash=self._profile_graph_hash,
                    expected_role=self._expected_role,
                    now=self._now_provider(),
                    runner_policy=self._runner_grant_policy,
                )
                handshake = self._handshake_builder(transcript)
                verify_remote_env_handshake(
                    handshake,
                    transcript,
                    expected_client_identity=self._source,
                    expected_server_identity=self._target,
                    expected_profile_lock_hash=self._expected_profile_lock_hash,
                    expected_image_digest=self._expected_image_digest,
                    expected_environment_id=self._expected_environment_id,
                )
                _send_canonical_message(
                    channel,
                    RemoteEnvServerHello(
                        domain="AutoMarkov-RemoteEnv-Server-Hello-v1",
                        protocol_version="automarkov.remote-env.v1",
                        server_nonce_b64url=_b64url(server_nonce),
                        handshake=handshake,
                        grant=grant,
                    ),
                )
                request_verifier = RemoteEnvGrantVerifier(
                    signing_key_id=self._grant_signing_key_id,
                    signing_public_key=self._grant_signing_public_key,
                    session_id=session_id,
                    source_identity=self._source,
                    target_identity=self._target,
                    environment_id=self._expected_environment_id,
                    profile_graph_hash=self._profile_graph_hash,
                    expected_role=self._expected_role,
                    topology_kind=(
                        "sealed_evaluator_environment"
                        if self._expected_role == "evaluator"
                        else "trainer_environment"
                    ),
                    runner_key_not_before=datetime.fromisoformat(
                        self._runner_grant_policy.key_not_before
                    ),
                    runner_key_expires_at=datetime.fromisoformat(
                        self._runner_grant_policy.key_expires_at
                    ),
                    runner_key_revoked=self._runner_grant_policy.key_revoked,
                    run_not_before=datetime.fromisoformat(
                        self._runner_grant_policy.run_not_before
                    ),
                    run_expires_at=datetime.fromisoformat(
                        self._runner_grant_policy.run_expires_at
                    ),
                    clock_skew_seconds=self._runner_grant_policy.clock_skew_seconds,
                    expected_grant_schema_hash=(
                        self._runner_grant_policy.grant_schema_hash.root
                    ),
                )
                worker = self._worker_factory(grant, transcript)
                while True:
                    request = _receive_frame_or_eof(
                        channel,
                        max_frame_bytes=grant.max_frame_bytes,
                        max_tensor_bytes=grant.max_tensor_bytes,
                    )
                    if request is None:
                        return
                    request_decoded = decode_remote_env_frame(
                        request,
                        max_frame_bytes=grant.max_frame_bytes,
                        max_tensor_bytes=grant.max_tensor_bytes,
                    )
                    if request_decoded.header.envelope.grant != grant:
                        raise RemoteEnvSecurityError(
                            "RemoteEnv request does not carry the negotiated grant"
                        )
                    try:
                        request_verifier.verify_and_consume(
                            request_decoded.header,
                            now=self._now_provider(),
                        )
                    except ValueError as error:
                        raise RemoteEnvSecurityError(str(error)) from error
                    response = worker.exchange(request)
                    response_decoded = decode_remote_env_frame(
                        response,
                        max_frame_bytes=grant.max_frame_bytes,
                        max_tensor_bytes=grant.max_tensor_bytes,
                    )
                    if (
                        response_decoded.header.envelope.grant != grant
                        or response_decoded.header.message_kind
                        != request_decoded.header.message_kind
                        or response_decoded.header.envelope.sequence
                        != request_decoded.header.envelope.sequence
                        or response_decoded.header.envelope.step_id
                        != request_decoded.header.envelope.step_id
                    ):
                        raise RemoteEnvSecurityError(
                            "RemoteEnv response does not bind its exact request"
                        )
                    if response_decoded.header.message_kind == "Step" and (
                        not isinstance(
                            response_decoded.header.payload,
                            (StepResultPayload, MultiAgentStepResultPayload),
                        )
                        or cast(
                            StepResultPayload | MultiAgentStepResultPayload,
                            response_decoded.header.payload,
                        ).transition_hash.root
                        != remote_env_transition_hash(request, response)
                    ):
                        raise RemoteEnvSecurityError(
                            "RemoteEnv Step response transition hash mismatch"
                        )
                    channel.sendall(response)
        finally:
            if session_id is not None:
                with self._state_lock:
                    self._terminal_sessions.add(session_id)


class TlsSocketRemoteEnv:
    """由 runner 材料认证的 TLS 1.3 `exchange(bytes) -> bytes` transport。"""

    def __init__(
        self,
        *,
        endpoint: RemoteEnvTlsEndpoint,
        runner_ca_path: str,
        client_certificate_path: str,
        client_private_key_path: str,
        expected_client_identity: RemoteEnvCertificateIdentity,
        expected_server_identity: RemoteEnvCertificateIdentity,
        profile_graph_hash: str,
        expected_environment_id: str,
        grant_signing_key_id: str,
        grant_signing_public_key: Ed25519PublicKey,
        runner_grant_policy: RemoteEnvRunnerGrantPolicy,
        expected_role: Literal["actor", "critic", "evaluator"],
        expected_client_fingerprint: str,
        expected_client_serial: int,
        client_certificate_revoked: bool,
        expected_server_fingerprint: str,
        expected_server_serial: int,
        server_certificate_revoked: bool,
        expected_profile_lock_hash: str,
        expected_image_digest: str,
        connect_timeout_seconds: float = 10.0,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        if type(endpoint) is not RemoteEnvTlsEndpoint:
            raise RemoteEnvSecurityError("RemoteEnv TLS endpoint is not frozen")
        if (
            type(connect_timeout_seconds) not in {int, float}
            or not math.isfinite(connect_timeout_seconds)
            or connect_timeout_seconds <= 0
        ):
            raise RemoteEnvSecurityError("RemoteEnv connect timeout must be positive")
        self._endpoint = endpoint
        self._source = expected_client_identity
        self._target = expected_server_identity
        self._profile_graph_hash = profile_graph_hash
        self._expected_environment_id = expected_environment_id
        self._transcript: RemoteEnvSessionTranscript | None = None
        self._grant: RemoteEnvCapabilityGrant | None = None
        self._grant_signing_key_id = grant_signing_key_id
        self._grant_signing_public_key = grant_signing_public_key
        self._runner_grant_policy = runner_grant_policy
        self._expected_role: Literal["actor", "critic", "evaluator"] = expected_role
        self._expected_client_fingerprint = expected_client_fingerprint
        self._expected_client_serial = expected_client_serial
        self._client_certificate_revoked = client_certificate_revoked
        self._expected_server_fingerprint = expected_server_fingerprint
        self._expected_server_serial = expected_server_serial
        self._server_certificate_revoked = server_certificate_revoked
        self._expected_profile_lock_hash = expected_profile_lock_hash
        self._expected_image_digest = expected_image_digest
        self._connect_timeout_seconds = float(connect_timeout_seconds)
        self._now_provider = now_provider or (lambda: datetime.now(tz=UTC))
        self._lock = Lock()
        self._channel: ssl.SSLSocket | None = None
        self._terminal = False
        self._context, self._issuer, self._client_certificate = self._load_materials(
            runner_ca_path=runner_ca_path,
            client_certificate_path=client_certificate_path,
            client_private_key_path=client_private_key_path,
        )
        self._verify_frozen_bindings()

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._channel is not None

    @property
    def grant(self) -> RemoteEnvCapabilityGrant | None:
        with self._lock:
            return self._grant

    def _load_materials(
        self,
        *,
        runner_ca_path: str,
        client_certificate_path: str,
        client_private_key_path: str,
    ) -> tuple[ssl.SSLContext, x509.Certificate, x509.Certificate]:
        descriptors: list[int] = []
        try:
            ca_fd = _open_trusted_material(runner_ca_path, private_key=False)
            descriptors.append(ca_fd)
            certificate_fd = _open_trusted_material(
                client_certificate_path, private_key=False
            )
            descriptors.append(certificate_fd)
            key_fd = _open_trusted_material(client_private_key_path, private_key=True)
            descriptors.append(key_fd)
            context = tls13_context(server=False)
            context.load_verify_locations(cafile=f"/proc/self/fd/{ca_fd}")
            context.load_cert_chain(
                certfile=f"/proc/self/fd/{certificate_fd}",
                keyfile=f"/proc/self/fd/{key_fd}",
            )
            issuer = _read_public_certificate(ca_fd)
            client_certificate = _read_public_certificate(certificate_fd)
            return context, issuer, client_certificate
        except RemoteEnvRuntimeUnavailable:
            raise
        except RemoteEnvSecurityError:
            raise
        except (OSError, ssl.SSLError, ValueError) as error:
            raise RemoteEnvSecurityError(
                "RemoteEnv trusted TLS material is invalid"
            ) from error
        finally:
            for descriptor in descriptors:
                os.close(descriptor)

    def _verify_frozen_bindings(self) -> None:
        now = self._now_provider()
        if (
            self._source.experiment_id != self._target.experiment_id
            or self._source.run_id != self._target.run_id
        ):
            raise RemoteEnvSecurityError(
                "RemoteEnv certificate peers do not share one frozen run"
            )
        try:
            verify_remote_env_leaf_certificate(
                self._client_certificate,
                self._issuer,
                expected_identity=self._source,
                peer_role="client",
                expected_fingerprint=self._expected_client_fingerprint,
                expected_serial=self._expected_client_serial,
                revoked=self._client_certificate_revoked,
                now=now,
            )
        except RemoteEnvSecurityError:
            raise
        except ValueError as error:
            raise RemoteEnvSecurityError(
                "RemoteEnv frozen certificate or grant binding is invalid"
            ) from error

    def connect(self) -> None:
        with self._lock:
            self._connect_locked()

    def _connect_locked(self) -> None:
        if self._terminal:
            raise RemoteEnvSecurityError(
                "RemoteEnv session is terminal and cannot be reconnected"
            )
        if self._channel is not None:
            return
        self._verify_frozen_bindings()
        raw_socket: socket.socket | None = None
        tls_socket: ssl.SSLSocket | None = None
        try:
            raw_socket = socket.create_connection(
                (self._endpoint.host, self._endpoint.port),
                timeout=self._connect_timeout_seconds,
            )
            tls_socket = self._context.wrap_socket(
                raw_socket,
                server_hostname=self._endpoint.server_name,
            )
            raw_socket = None
            if tls_socket.version() != "TLSv1.3":
                raise RemoteEnvSecurityError("RemoteEnv requires negotiated TLS 1.3")
            peer_der = tls_socket.getpeercert(binary_form=True)
            if type(peer_der) is not bytes or not peer_der:
                raise RemoteEnvSecurityError("RemoteEnv server certificate is missing")
            try:
                peer_certificate = x509.load_der_x509_certificate(peer_der)
            except ValueError as error:
                raise RemoteEnvSecurityError(
                    "RemoteEnv server certificate is invalid"
                ) from error
            verify_remote_env_leaf_certificate(
                peer_certificate,
                self._issuer,
                expected_identity=self._target,
                peer_role="server",
                expected_fingerprint=self._expected_server_fingerprint,
                expected_serial=self._expected_server_serial,
                revoked=self._server_certificate_revoked,
                now=self._now_provider(),
            )
            client_nonce = secrets.token_bytes(32)
            client_hello = RemoteEnvClientHello.model_validate(
                {
                    "domain": "AutoMarkov-RemoteEnv-Client-Hello-v1",
                    "protocol_version": "automarkov.remote-env.v1",
                    "experiment_id": self._source.experiment_id,
                    "run_id": self._source.run_id,
                    "profile_graph_hash": self._profile_graph_hash,
                    "process_execution_id": self._source.process_execution_id,
                    "profile_id": self._source.profile_id,
                    "principal_id": self._source.principal_id,
                    "certificate_fingerprint": self._expected_client_fingerprint,
                    "nonce_b64url": _b64url(client_nonce),
                },
                strict=True,
            )
            _send_canonical_message(tls_socket, client_hello)
            received = _receive_typed_message(tls_socket, RemoteEnvServerHello)
            if type(received) is not RemoteEnvServerHello:
                raise RemoteEnvSecurityError("RemoteEnv server hello type mismatch")
            transcript = _session_transcript(
                source=self._source,
                target=self._target,
                profile_graph_hash=self._profile_graph_hash,
                client_fingerprint=self._expected_client_fingerprint,
                server_fingerprint=self._expected_server_fingerprint,
                client_nonce_b64url=client_hello.nonce_b64url,
                server_nonce_b64url=received.server_nonce_b64url,
            )
            handshake = received.handshake
            verify_remote_env_handshake(
                handshake,
                transcript,
                expected_client_identity=self._source,
                expected_server_identity=self._target,
                expected_profile_lock_hash=self._expected_profile_lock_hash,
                expected_image_digest=self._expected_image_digest,
                expected_environment_id=self._expected_environment_id,
            )
            verify_remote_env_grant_binding(
                received.grant,
                signing_key_id=self._grant_signing_key_id,
                signing_public_key=self._grant_signing_public_key,
                session_id=remote_env_session_id(transcript),
                source_identity=self._source,
                target_identity=self._target,
                environment_id=self._expected_environment_id,
                profile_graph_hash=self._profile_graph_hash,
                expected_role=self._expected_role,
                now=self._now_provider(),
                runner_policy=self._runner_grant_policy,
            )
            self._transcript = transcript
            self._grant = received.grant
            self._channel = tls_socket
        except RemoteEnvSecurityError:
            self._terminal = True
            if tls_socket is not None:
                tls_socket.close()
            if raw_socket is not None:
                raw_socket.close()
            raise
        except ssl.SSLCertVerificationError as error:
            self._terminal = True
            if tls_socket is not None:
                tls_socket.close()
            if raw_socket is not None:
                raw_socket.close()
            raise RemoteEnvSecurityError(
                "RemoteEnv TLS certificate verification failed"
            ) from error
        except ValueError as error:
            self._terminal = True
            if tls_socket is not None:
                tls_socket.close()
            if raw_socket is not None:
                raw_socket.close()
            raise RemoteEnvSecurityError(
                "RemoteEnv TLS peer identity or handshake is invalid"
            ) from error
        except (OSError, ssl.SSLError, TimeoutError) as error:
            self._terminal = True
            if tls_socket is not None:
                tls_socket.close()
            if raw_socket is not None:
                raw_socket.close()
            raise RemoteEnvRuntimeUnavailable(
                "WAITING_RUNTIME: RemoteEnv TLS endpoint is unavailable"
            ) from error

    def exchange(self, canonical_frame: bytes) -> bytes:
        with self._lock:
            self._connect_locked()
            channel = self._channel
            grant = self._grant
            if channel is None or grant is None:
                raise RemoteEnvRuntimeUnavailable("WAITING_RUNTIME")
            try:
                self._verify_frozen_bindings()
                decoded = decode_remote_env_frame(
                    canonical_frame,
                    max_frame_bytes=grant.max_frame_bytes,
                    max_tensor_bytes=grant.max_tensor_bytes,
                )
                if (
                    decoded.header.envelope.grant != grant
                    or not self._envelope_is_bound(decoded.header.envelope)
                ):
                    raise RemoteEnvSecurityError(
                        "RemoteEnv frame does not carry the authenticated grant"
                    )
                channel.sendall(canonical_frame)
                response = _receive_frame(
                    channel,
                    max_frame_bytes=grant.max_frame_bytes,
                    max_tensor_bytes=grant.max_tensor_bytes,
                )
                response_decoded = decode_remote_env_frame(
                    response,
                    max_frame_bytes=grant.max_frame_bytes,
                    max_tensor_bytes=grant.max_tensor_bytes,
                )
                response_header = response_decoded.header
                if (
                    response_header.envelope.grant != grant
                    or not self._envelope_is_bound(response_header.envelope)
                    or response_header.message_kind != decoded.header.message_kind
                    or response_header.envelope.sequence
                    != decoded.header.envelope.sequence
                    or response_header.envelope.step_id
                    != decoded.header.envelope.step_id
                ):
                    raise RemoteEnvSecurityError(
                        "RemoteEnv response does not carry the authenticated grant"
                    )
                if response_header.message_kind == "Step" and (
                    not isinstance(
                        response_header.payload,
                        (StepResultPayload, MultiAgentStepResultPayload),
                    )
                    or cast(
                        StepResultPayload | MultiAgentStepResultPayload,
                        response_header.payload,
                    ).transition_hash.root
                    != remote_env_transition_hash(canonical_frame, response)
                ):
                    raise RemoteEnvSecurityError(
                        "RemoteEnv Step response transition hash mismatch"
                    )
                return response
            except RemoteEnvSecurityError:
                self._abort_locked()
                raise
            except ValueError as error:
                self._abort_locked()
                raise RemoteEnvSecurityError(
                    "RemoteEnv frame failed closed validation"
                ) from error
            except (OSError, ssl.SSLError, TimeoutError) as error:
                self._abort_locked()
                raise RemoteEnvRuntimeUnavailable(
                    "WAITING_RUNTIME: RemoteEnv TLS exchange failed"
                ) from error

    def _envelope_is_bound(self, envelope: RemoteEnvEnvelope) -> bool:
        grant = self._grant
        if grant is None:
            return False
        return (
            envelope.run_id == grant.run_id
            and envelope.session_id == grant.session_id
            and envelope.source_process_execution_id
            == grant.source_process_execution_id
            and envelope.source_profile_id == grant.source_profile_id
            and envelope.source_principal_id == grant.source_principal_id
            and envelope.target_process_execution_id
            == grant.target_process_execution_id
            and envelope.target_profile_id == grant.target_profile_id
        )

    def close(self) -> None:
        with self._lock:
            self._terminal = True
            self._close_locked()

    def _abort_locked(self) -> None:
        self._terminal = True
        self._close_locked()

    def _close_locked(self) -> None:
        channel = self._channel
        self._channel = None
        if channel is not None:
            try:
                channel.close()
            except OSError:
                pass


class CartPoleBackend(Protocol):
    def reset(
        self, *, seed: int, options: Mapping[str, object] | None
    ) -> tuple[tuple[float, float, float, float], Mapping[str, object]]: ...

    def step(
        self, action: int
    ) -> tuple[
        tuple[float, float, float, float],
        float,
        bool,
        bool,
        Mapping[str, object],
    ]: ...

    def close(self) -> None: ...


class SingleAgentRemoteEnvWorker:
    """单主体 bytes RemoteEnv worker；当前冻结 tracer profile 是 CartPole。"""

    def __init__(
        self,
        *,
        backend: CartPoleBackend,
        verifier: RemoteEnvGrantVerifier,
        response_envelope: RemoteEnvEnvelope,
        repository_commit: str,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._backend = backend
        self._verifier = verifier
        self._response_envelope = response_envelope
        self._repository_commit = repository_commit
        self._now_provider = now_provider or (lambda: datetime.now(tz=UTC))
        self._phase: Literal["new", "described", "reset", "closed"] = "new"
        self._closed_response: bytes | None = None
        self._last_step = 0
        self._current_request_envelope = response_envelope

    def exchange(self, request_frame: bytes) -> bytes:
        decoded = decode_remote_env_frame(
            request_frame,
            max_frame_bytes=self._response_envelope.grant.max_frame_bytes,
            max_tensor_bytes=self._response_envelope.grant.max_tensor_bytes,
        )
        self._verifier.verify_and_consume(decoded.header, now=self._now_provider())
        self._current_request_envelope = decoded.header.envelope
        try:
            method = decoded.header.message_kind
            payload = decoded.header.payload.model_dump(
                mode="json", round_trip=True, warnings="error"
            )
            if method == "Describe":
                if self._phase != "new":
                    raise ValueError("Describe must be the first CartPole method")
                self._phase = "described"
                return self._describe_response()
            if method == "Reset":
                if self._phase not in {"described", "reset"}:
                    raise ValueError("Reset requires a successful Describe")
                seed = payload.get("seed")
                options = payload.get("options")
                if type(seed) is not int or not isinstance(options, Mapping):
                    raise ValueError(
                        "Reset requires an integer seed and object options"
                    )
                observation, info = self._backend.reset(seed=seed, options=options)
                self._phase = "reset"
                self._last_step = 0
                return self._observation_response("Reset", observation, info)
            if method == "Step":
                if self._phase != "reset":
                    raise ValueError("Step requires a successful Reset")
                action = payload.get("action")
                if type(action) is not int or action not in {0, 1}:
                    raise ValueError("CartPole action must be exact integer 0 or 1")
                if decoded.header.envelope.step_id != self._last_step + 1:
                    raise ValueError("CartPole step_id must be strictly monotonic")
                observation, reward, terminated, truncated, info = self._backend.step(
                    action
                )
                self._last_step = decoded.header.envelope.step_id
                return self._observation_response(
                    "Step",
                    request_frame,
                    observation,
                    info,
                    reward=reward,
                    terminated=terminated,
                    truncated=truncated,
                )
            if method == "Close":
                if self._closed_response is None:
                    self._backend.close()
                    self._phase = "closed"
                    self._closed_response = self._json_response(
                        "Close", {"closed": True}
                    )
                return self._closed_response
            raise ValueError(
                "CartPole tracer only supports Describe, Reset, Step, and Close"
            )
        except Exception:
            self._verifier.revoke()
            raise

    def _describe_response(self) -> bytes:
        high, high_bytes = make_tensor_descriptor(
            tensor_id="tensor_cartpole_high",
            dtype="float32",
            shape=(4,),
            offset=0,
            data=struct.pack(
                "<ffff",
                4.8,
                float("inf"),
                _CARTPOLE_ANGLE_OBSERVATION_LIMIT,
                float("inf"),
            ),
            allow_infinity=True,
        )
        low, low_bytes = make_tensor_descriptor(
            tensor_id="tensor_cartpole_low",
            dtype="float32",
            shape=(4,),
            offset=len(high_bytes),
            data=struct.pack(
                "<ffff",
                -4.8,
                float("-inf"),
                -_CARTPOLE_ANGLE_OBSERVATION_LIMIT,
                float("-inf"),
            ),
            allow_infinity=True,
        )
        observation_space = BoxSpace(
            kind="Box",
            shape=(4,),
            dtype="float32",
            low_tensor_id=low.tensor_id,
            high_tensor_id=high.tensor_id,
        )
        action_space = DiscreteSpace(kind="Discrete", n=2, start=0, dtype="int64")
        return self._response(
            "Describe",
            {
                "environment_repository_commit": self._repository_commit,
                "environment_spec": "CartPole-v1",
                "observation_space": observation_space.model_dump(mode="json"),
                "action_space": action_space.model_dump(mode="json"),
                "seed_contract": {"kind": "integer_reset", "seed": 0},
                "supports_aec": False,
                "supports_parallel": False,
                "supports_state": False,
            },
            (high, low),
            {high.tensor_id: high_bytes, low.tensor_id: low_bytes},
        )

    def _observation_response(
        self,
        method: Literal["Reset", "Step"],
        request_frame_or_observation: bytes | tuple[float, float, float, float],
        observation_or_info: tuple[float, float, float, float] | Mapping[str, object],
        info: Mapping[str, object] | None = None,
        *,
        reward: float | None = None,
        terminated: bool | None = None,
        truncated: bool | None = None,
    ) -> bytes:
        if method == "Step":
            if (
                type(request_frame_or_observation) is not bytes
                or not isinstance(observation_or_info, tuple)
                or info is None
            ):
                raise ValueError("Step response requires its canonical request frame")
            request_frame = request_frame_or_observation
            observation = observation_or_info
        else:
            if not isinstance(request_frame_or_observation, tuple) or not isinstance(
                observation_or_info, Mapping
            ):
                raise ValueError("Reset response requires observation and info")
            request_frame = None
            observation = request_frame_or_observation
            info = observation_or_info
        descriptor, data = make_tensor_descriptor(
            tensor_id="tensor_cartpole_observation",
            dtype="float32",
            shape=(4,),
            offset=0,
            data=struct.pack("<ffff", *observation),
        )
        payload: dict[str, object] = {
            "observation_tensor_id": descriptor.tensor_id,
            "info": dict(info),
        }
        if method == "Step":
            payload.update(
                {
                    "reward": reward,
                    "termination": terminated,
                    "truncation": truncated,
                    "active_aec_agent": None,
                    "cycle_index": self._current_request_envelope.step_id,
                    "transition_hash": "sha256:" + "0" * 64,
                }
            )
        provisional = self._response(
            method, payload, (descriptor,), {descriptor.tensor_id: data}
        )
        if request_frame is None:
            return provisional
        payload["transition_hash"] = remote_env_transition_hash(
            request_frame, provisional
        )
        return self._response(
            method, payload, (descriptor,), {descriptor.tensor_id: data}
        )

    def _json_response(
        self, method: Literal["Close"], payload: dict[str, object]
    ) -> bytes:
        return self._response(method, payload, (), {})

    def _response(
        self,
        method: Literal["Describe", "Reset", "Step", "Close"],
        payload: dict[str, object],
        descriptors: tuple[TensorDescriptor, ...],
        tensors: Mapping[str, bytes],
    ) -> bytes:
        header = RemoteEnvFrameHeader.model_validate(
            {
                "codec_version": "automarkov.remote-env-frame.v1",
                "message_kind": method,
                "envelope": self._current_request_envelope.model_dump(mode="json"),
                "payload": payload,
                "tensors": [item.model_dump(mode="json") for item in descriptors],
            },
            strict=True,
        )
        return encode_remote_env_frame(header, tensors)


def formal_remote_env_readiness(
    image_status: str,
    *,
    transport: TlsSocketRemoteEnv | None = None,
) -> tuple[Literal["WAITING_RUNTIME", "READY"], str]:
    if image_status != "built":
        return (
            "WAITING_RUNTIME",
            "formal RemoteEnv execution requires a built, resolver-verified runtime profile",
        )
    if transport is not None and transport.ready:
        return "READY", "runner-provisioned mTLS RemoteEnv transport is authenticated"
    return (
        "WAITING_RUNTIME",
        "formal RemoteEnv requires runner CA, leaf/key, and an authenticated TLS session",
    )


# T12 的公开兼容名；唯一实现仍是满足 exchange(bytes) -> bytes 的通用单主体 seam。
CartPoleRemoteEnvWorker = SingleAgentRemoteEnvWorker


__all__ = [
    "REMOTE_ENV_GRANT_SCHEMA_HASH",
    "CartPoleRemoteEnvWorker",
    "RemoteEnvGrantVerifier",
    "RemoteEnvRuntimeUnavailable",
    "RemoteEnvSecurityError",
    "SingleAgentRemoteEnvWorker",
    "TlsSocketRemoteEnv",
    "TlsSocketRemoteEnvServer",
    "formal_remote_env_readiness",
    "parse_remote_env_certificate_identity",
    "remote_env_certificate_identity_uri",
    "remote_env_grant_preimage",
    "remote_env_session_id",
    "sign_remote_env_grant",
    "verify_remote_env_grant_binding",
    "verify_remote_env_handshake",
    "verify_remote_env_leaf_certificate",
]
