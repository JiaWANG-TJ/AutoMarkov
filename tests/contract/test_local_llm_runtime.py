from __future__ import annotations

import json
import os
import subprocess
from base64 import urlsafe_b64encode
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from automarkov.adapters import InMemoryArtifactRepository
from automarkov.canonical import canonical_json_bytes
from automarkov.domain import Sha256Digest
from automarkov.evidence_access import (
    EvidenceAccessController,
    EvidenceCapabilityGrant,
    EvidenceStoreRef,
    GenerationEvidenceView,
    validate_evidence_grant_payload,
)
from automarkov.llm_contracts import (
    OFFICIAL_QWEN_WEIGHT_SHARD_HASHES,
    REQUIRED_RUNTIME_ROUTE_POLICY_HASH,
    LlmCompletionRequest,
    LlmPromptArtifact,
    LlmResponsePayload,
    LlmSampling,
    LlmStartRequest,
    LlmToolCall,
    LocalLlmRuntimeManifest,
    RuntimeHostAttestation,
    RuntimeModelSnapshotEvidence,
    RuntimePackageEvidence,
    RuntimeProcessEvidence,
    UserChatMessage,
)
from automarkov.local_llm_runtime import (
    AttachedLocalLlmRuntime,
    CurrentRuntimeConnectionEvidence,
    HttpResponse,
    LocalLlmRuntimeStateError,
    RuntimeConnectionExpectation,
    RuntimeEvidenceResolver,
    RuntimeHttpRequestBinding,
    SignedRuntimeAttestationVerifier,
    VerifiedRuntimeConnection,
)
from automarkov.public import ArtifactPutInput, ArtifactPutResult

_HOST_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"\x01" * 32)
_EVIDENCE_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"\x02" * 32)


def _digest(value: str) -> str:
    return "sha256:" + sha256(value.encode()).hexdigest()


def _generation_evidence_context() -> tuple[
    EvidenceAccessController,
    GenerationEvidenceView,
]:
    store = EvidenceStoreRef(
        schema_version="automarkov.evidence-store-ref.v1",
        store_id="store_allowed_runtime_contract",
        tier="allowed_evidence",
        identity_hash=_digest("allowed-runtime-evidence"),
    )
    grant_payload: dict[str, object] = {
        "schema_version": "automarkov.evidence-capability-grant.v1",
        "signing_domain": "AutoMarkov-Evidence-Capability-Grant-v1",
        "capability_id": "capability_runtime_contract",
        "principal_id": "principal_runtime_contract",
        "principal_kind": "text_agent",
        "tiers": ["allowed_evidence"],
        "store_ids": [store.store_id],
        "store_identity_hashes": {store.store_id: store.identity_hash},
        "issuer_key_id": "key_runtime_evidence",
        "nonce": urlsafe_b64encode(b"e" * 32).decode().rstrip("="),
        "signature_algorithm": "Ed25519",
        "signature": urlsafe_b64encode(b"\x00" * 64).decode().rstrip("="),
    }
    unsigned = EvidenceCapabilityGrant.model_validate(grant_payload, strict=True)
    grant_payload["signature"] = (
        urlsafe_b64encode(_EVIDENCE_PRIVATE_KEY.sign(unsigned.signing_bytes()))
        .decode()
        .rstrip("=")
    )
    grant = validate_evidence_grant_payload(grant_payload)
    controller = EvidenceAccessController(
        authenticated_principal_id=grant.principal_id,
        trusted_issuer_keys={
            "key_runtime_evidence": _EVIDENCE_PRIVATE_KEY.public_key()
        },
        registered_stores={store.store_id: store},
    )
    return controller, controller.issue_generation_view(grant, (store,))


class _EvidenceResolver:
    def payload_hash(self, artifact_id: str) -> str:
        results = _manifest_repository(_manifest("resolver-token"))[3:]
        for result in results:
            if result.artifact_id.root == artifact_id:
                return result.payload_hash.root
        raise KeyError(artifact_id)


class _RecordingArtifactRepository(InMemoryArtifactRepository):
    def __init__(self) -> None:
        super().__init__()
        self.put_artifact_types: list[str] = []

    def put(self, request: ArtifactPutInput) -> ArtifactPutResult:
        artifact_type = request.get("artifact_type")
        if type(artifact_type) is str:
            self.put_artifact_types.append(artifact_type)
        return super().put(request)


def _attestation_verifier(
    *,
    evidence_resolver: RuntimeEvidenceResolver | None = None,
    clock: Callable[[], datetime] | None = None,
) -> SignedRuntimeAttestationVerifier:
    return SignedRuntimeAttestationVerifier(
        trusted_host_keys={"key_runtime_host": _HOST_PRIVATE_KEY.public_key()},
        evidence_resolver=evidence_resolver or _EvidenceResolver(),
        clock=clock or (lambda: datetime(2026, 8, 12, 12, 1, tzinfo=UTC)),
    )


def _host_attestation(
    manifest: LocalLlmRuntimeManifest,
    *,
    observed_at: str | None = None,
    nonce_byte: bytes = b"n",
) -> RuntimeHostAttestation:
    _, persisted, _, process, package, model = _manifest_repository(manifest)
    payload: dict[str, object] = {
        "schema_version": "automarkov.runtime-host-attestation.v3",
        "signing_domain": "AutoMarkov-Runtime-Host-Attestation-v3",
        "attestation_id": "runtimeatt_contract",
        "runtime_manifest_ref": _ref_payload(persisted),
        "process_evidence_ref": _ref_payload(process),
        "package_evidence_ref": _ref_payload(package),
        "model_snapshot_evidence_ref": _ref_payload(model),
        "observed_at": observed_at or manifest.observed_at,
        "nonce": urlsafe_b64encode(nonce_byte * 32).decode().rstrip("="),
        "signature_algorithm": "Ed25519",
        "signing_key_id": "key_runtime_host",
        "signature": urlsafe_b64encode(b"\x00" * 64).decode().rstrip("="),
    }
    unsigned = RuntimeHostAttestation.model_validate(payload, strict=True)
    payload["signature"] = (
        urlsafe_b64encode(_HOST_PRIVATE_KEY.sign(unsigned.signing_bytes()))
        .decode()
        .rstrip("=")
    )
    return RuntimeHostAttestation.model_validate(payload, strict=True)


def _ref_payload(result: ArtifactPutResult) -> dict[str, str]:
    return {
        "artifact_id": result.artifact_id.root,
        "payload_hash": result.payload_hash.root,
    }


def _credential_fingerprint(token: str) -> str:
    payload = b"automarkov.vllm-credential-fingerprint.v1\x00" + token.encode()
    return "sha256:" + sha256(payload).hexdigest()


def _manifest(token: str, **updates: object) -> LocalLlmRuntimeManifest:
    payload: dict[str, object] = {
        "schema_version": "automarkov.local-llm-runtime-manifest.v3",
        "runtime_id": "runtime_qwen_local",
        "lifecycle_mode": "ATTACHED",
        "profile_id": "llm-qwen36-vllm",
        "base_url": "http://127.0.0.1:8000/v1",
        "model_id": "Qwen/Qwen3.6-35B-A3B",
        "model_checkpoint_path": "/mnt/automarkov/models/qwen36",
        "tokenizer_checkpoint_path": "/mnt/automarkov/models/qwen36",
        "served_model_name": "Qwen/Qwen3.6-35B-A3B",
        "observed_at": "2026-08-12T12:00:00Z",
        "model_revision": "995ad96eacd98c81ed38be0c5b274b04031597b0",
        "tokenizer_revision": "995ad96eacd98c81ed38be0c5b274b04031597b0",
        "model_config_hash": "sha256:93a4693fa9d8392fbfccd4b3c9873f4bfdcb14fdede978b123d07d19675efe99",
        "weight_index_hash": "sha256:41b9356101ebf8e7519e150dc811f80c4226e727301fbb032b890f006ed0be83",
        "weight_shard_hashes": dict(OFFICIAL_QWEN_WEIGHT_SHARD_HASHES),
        "tokenizer_hash": "sha256:5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42",
        "tokenizer_config_hash": "sha256:5186f0defcd7f232382c7f0aebcd2252d073bb921ab240e407b7ae8745d2b29b",
        "chat_template_hash": "sha256:e84f32a23fdda27689f868aa4a1a5621f41133e51a48d7f3efcbea2839574259",
        "vllm_version": "0.25.1+cu129",
        "vllm_distribution_hash": "sha256:9e206f370c934a2d4b6b1f05d3d09708d344e05d80260189ef19f60755709431",
        "runtime_environment_hash": _digest("runtime-environment"),
        "pytorch_version": "2.9.1",
        "cuda_version": "12.9",
        "container_digest": _digest("container"),
        "startup_args": (
            "/mnt/automarkov/models/qwen36",
            "--revision",
            "995ad96eacd98c81ed38be0c5b274b04031597b0",
            "--tokenizer",
            "/mnt/automarkov/models/qwen36",
            "--tokenizer-revision",
            "995ad96eacd98c81ed38be0c5b274b04031597b0",
            "--served-model-name",
            "Qwen/Qwen3.6-35B-A3B",
            "--max-model-len",
            "32768",
            "--reasoning-parser",
            "qwen3",
            "--tool-call-parser",
            "qwen3_coder",
            "--enable-auto-tool-choice",
            "--api-key",
            "[REDACTED]",
        ),
        "listener_identity_hash": _digest("listener"),
        "process_identity_hash": _digest("process"),
        "relay_identity_hash": _digest("relay"),
        "route_policy_hash": REQUIRED_RUNTIME_ROUTE_POLICY_HASH,
        "credential_id": "local-llm-server.v1",
        "credential_fingerprint": _credential_fingerprint(token),
        "max_model_len": 32_768,
        "max_concurrency": 2,
        "request_timeout_seconds": 30,
        "max_prompt_tokens": 8_192,
        "max_completion_tokens": 2_048,
        "reasoning_parser": "qwen3",
        "tool_call_parser": "qwen3_coder",
        "thinking_policy": "disabled",
        "chat_template_policy": "enable_thinking=false",
    }
    payload.update(updates)
    return LocalLlmRuntimeManifest.model_validate(payload, strict=True)


def _manifest_repository(
    manifest: LocalLlmRuntimeManifest,
    *,
    repository: InMemoryArtifactRepository | None = None,
) -> tuple[
    InMemoryArtifactRepository,
    ArtifactPutResult,
    ArtifactPutResult,
    ArtifactPutResult,
    ArtifactPutResult,
    ArtifactPutResult,
]:
    repository = repository or InMemoryArtifactRepository()
    persisted = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "local_llm_runtime_manifest",
            "payload_bytes": canonical_json_bytes(
                manifest.model_dump(mode="json", round_trip=True, warnings="error")
            ),
            "parent_artifact_ids": [],
            "created_by": "principal_t05_contract",
            "created_at": "2026-08-12T12:00:00Z",
            "source_evidence_ids": [],
        }
    )
    evidence_payloads = (
        (
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
    evidence_results = tuple(
        repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": artifact_type,
                "payload_bytes": canonical_json_bytes(
                    payload.model_dump(mode="json", round_trip=True, warnings="error")
                ),
                "parent_artifact_ids": [],
                "created_by": "principal_t05_contract",
                "created_at": "2026-08-12T12:00:00Z",
                "source_evidence_ids": [],
            }
        )
        for artifact_type, payload in evidence_payloads
    )
    prompt = LlmPromptArtifact(
        schema_version="automarkov.llm-prompt.v3",
        generation_evidence_view=_generation_evidence_context()[1],
        messages=(UserChatMessage(role="user", content="Return the exact result."),),
    )
    persisted_prompt = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "llm_prompt",
            "payload_bytes": canonical_json_bytes(
                prompt.model_dump(mode="json", round_trip=True, warnings="error")
            ),
            "parent_artifact_ids": [],
            "created_by": "principal_t05_contract",
            "created_at": "2026-08-12T12:00:00Z",
            "source_evidence_ids": [],
        }
    )
    return (
        repository,
        persisted,
        persisted_prompt,
        evidence_results[0],
        evidence_results[1],
        evidence_results[2],
    )


def _start_request(
    manifest: LocalLlmRuntimeManifest,
    *,
    attestation_observed_at: str | None = None,
    nonce_byte: bytes = b"n",
) -> LlmStartRequest:
    _, persisted, _, _, _, _ = _manifest_repository(manifest)
    return LlmStartRequest(
        schema_version="automarkov.llm-start-request.v4",
        runtime_manifest_artifact_id=persisted.artifact_id,
        runtime_manifest_payload_hash=persisted.payload_hash,
        runtime_manifest=manifest,
        host_attestation=_host_attestation(
            manifest,
            observed_at=attestation_observed_at,
            nonce_byte=nonce_byte,
        ),
    )


def _completion_request(
    manifest: LocalLlmRuntimeManifest,
    *,
    request_id: str = "llmreq_contract",
) -> LlmCompletionRequest:
    prompt = LlmPromptArtifact(
        schema_version="automarkov.llm-prompt.v3",
        generation_evidence_view=_generation_evidence_context()[1],
        messages=(UserChatMessage(role="user", content="Return the exact result."),),
    )
    _, _, persisted_prompt, _, _, _ = _manifest_repository(manifest)
    return LlmCompletionRequest(
        schema_version="automarkov.llm-completion-request.v4",
        request_id=request_id,
        runtime_manifest_payload_hash=Sha256Digest(root=manifest.artifact_payload_hash),
        prompt_artifact_id=persisted_prompt.artifact_id,
        prompt_payload_hash=Sha256Digest(root=prompt.payload_hash),
        prompt=prompt,
        sampling=LlmSampling(
            temperature="0",
            top_p="1",
            seed=7,
            max_tokens=64,
        ),
    )


class _ScriptedTransport:
    def __init__(
        self,
        served_model: str = "Qwen/Qwen3.6-35B-A3B",
        *,
        expected_token: str | None = None,
        enforce_authentication: bool = True,
        tokenizer_count: int = 4,
        reported_completion_tokens: int | None = None,
        invalid_identity_on_open: int | None = None,
        completion_content: str = "final answer",
        canary_has_tool_call: bool = False,
        canary_finish_reason: str = "stop",
    ) -> None:
        self.served_model = served_model
        self.expected_token = expected_token
        self.enforce_authentication = enforce_authentication
        self.tokenizer_count = tokenizer_count
        self.reported_completion_tokens = reported_completion_tokens
        self.invalid_identity_on_open = invalid_identity_on_open
        self.completion_content = completion_content
        self.canary_has_tool_call = canary_has_tool_call
        self.canary_finish_reason = canary_finish_reason
        self.open_count = 0
        self.close_count = 0
        self.opened_connections: list[tuple[str, str]] = []
        self.reenter_before_response: Callable[[], object] | None = None
        self.reenter_path: str | None = None
        self.requests: list[tuple[str, str, Mapping[str, str], bytes | None]] = []

    def open_verified(
        self,
        *,
        expectation: RuntimeConnectionExpectation,
        binding: RuntimeHttpRequestBinding,
        challenge: str,
    ) -> VerifiedRuntimeConnection:
        del expectation
        self.open_count += 1
        return _ScriptedConnection(
            self,
            binding,
            challenge,
            invalid_identity=self.open_count == self.invalid_identity_on_open,
        )


class _ScriptedConnection:
    def __init__(
        self,
        owner: _ScriptedTransport,
        binding: RuntimeHttpRequestBinding,
        challenge: str,
        *,
        invalid_identity: bool,
    ) -> None:
        self._owner = owner
        self._binding = binding
        evidence_payload = {
            "challenge": challenge,
            "request_binding_hash": binding.binding_hash,
            "listener_identity_hash": _digest(
                "wrong-listener" if invalid_identity else "listener"
            ),
            "process_identity_hash": _digest("process"),
            "relay_identity_hash": _digest("relay"),
            "route_policy_hash": REQUIRED_RUNTIME_ROUTE_POLICY_HASH,
        }
        self.evidence = CurrentRuntimeConnectionEvidence(
            **evidence_payload,
            evidence_hash="sha256:"
            + sha256(canonical_json_bytes(evidence_payload)).hexdigest(),
        )
        owner.opened_connections.append((binding.url, self.evidence.evidence_hash))

    def request(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> HttpResponse:
        del timeout_seconds
        owner = self._owner
        method = self._binding.method
        url = self._binding.url
        if (
            owner.reenter_before_response is not None
            and owner.reenter_path is not None
            and url.endswith(owner.reenter_path)
        ):
            callback = owner.reenter_before_response
            owner.reenter_before_response = None
            owner.reenter_path = None
            callback()
        owner.requests.append((method, url, headers, body))
        response: HttpResponse
        if url.endswith("/health"):
            response = HttpResponse(status=200, body=b"", content_type="text/plain")
        elif url.endswith("/models"):
            if owner.enforce_authentication and headers.get("Authorization") != (
                f"Bearer {owner.expected_token}"
            ):
                response = HttpResponse(
                    status=401,
                    body=b'{"error":"Unauthorized"}',
                    content_type="application/json",
                )
            else:
                response = HttpResponse(
                    status=200,
                    body=json.dumps(
                        {
                            "object": "list",
                            "data": [
                                {
                                    "id": owner.served_model,
                                    "object": "model",
                                    "created": 1,
                                    "owned_by": "vllm",
                                }
                            ],
                        }
                    ).encode(),
                    content_type="application/json",
                )
        elif url.endswith("/tokenize"):
            response = HttpResponse(
                status=200,
                body=json.dumps(
                    {
                        "count": owner.tokenizer_count,
                        "max_model_len": 32_768,
                        "tokens": list(range(owner.tokenizer_count)),
                    }
                ).encode(),
                content_type="application/json",
            )
        else:
            assert url.endswith("/chat/completions")
            if owner.enforce_authentication and headers.get("Authorization") != (
                f"Bearer {owner.expected_token}"
            ):
                response = HttpResponse(
                    status=401,
                    body=b'{"error":"Unauthorized"}',
                    content_type="application/json",
                )
            else:
                request = json.loads(body or b"{}")
                prompt_content = request["messages"][-1]["content"]
                canary = "AUTOMARKOV_CANARY_" in prompt_content
                content = (
                    prompt_content.rsplit(" ", 1)[-1]
                    if canary
                    else owner.completion_content
                )
                completion_tokens = (
                    min(2, request["max_tokens"])
                    if owner.reported_completion_tokens is None
                    else owner.reported_completion_tokens
                )
                message: dict[str, object] = {
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": "must never enter the trace",
                }
                if canary and owner.canary_has_tool_call:
                    message["tool_calls"] = [
                        {
                            "id": "call_unexpected_canary_tool",
                            "type": "function",
                            "function": {
                                "name": "unexpected_canary_tool",
                                "arguments": "{}",
                            },
                        }
                    ]
                response = HttpResponse(
                    status=200,
                    body=json.dumps(
                        {
                            "id": "chatcmpl-contract",
                            "created": 1,
                            "object": "chat.completion",
                            "model": owner.served_model,
                            "choices": [
                                {
                                    "index": 0,
                                    "message": message,
                                    "finish_reason": (
                                        owner.canary_finish_reason if canary else "stop"
                                    ),
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 4,
                                "completion_tokens": completion_tokens,
                                "total_tokens": 4 + completion_tokens,
                            },
                        }
                    ).encode(),
                    content_type="application/json",
                )
        return response

    def close(self) -> None:
        self._owner.close_count += 1


def _runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    token: str,
    transport: _ScriptedTransport,
    manifest: LocalLlmRuntimeManifest | None = None,
    clock: Callable[[], datetime] | None = None,
    repository: InMemoryArtifactRepository | None = None,
    attestation_clock: Callable[[], datetime] | None = None,
) -> AttachedLocalLlmRuntime:
    credential = tmp_path / "vllm-api-key"
    credential.write_text(token + "\n", encoding="utf-8")
    credential.chmod(0o600)
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    monkeypatch.setenv("AUTOMARKOV_VLLM_API_KEY_FILE", str(credential))
    monkeypatch.setenv("AUTOMARKOV_VLLM_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("AUTOMARKOV_VLLM_MODEL", "Qwen/Qwen3.6-35B-A3B")
    monkeypatch.setenv("AUTOMARKOV_VLLM_TIMEOUT_SECONDS", "30")
    return AttachedLocalLlmRuntime(
        repository_root=repository_root,
        artifact_repository=repository
        or _manifest_repository(manifest or _manifest(token))[0],
        attestation_verifier=_attestation_verifier(clock=attestation_clock),
        connection_provider=transport,
        evidence_access_controller=_generation_evidence_context()[0],
        clock=clock,
    )


def test_host_attestation_can_refresh_without_changing_manifest_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "refresh-attestation-token"
    manifest = _manifest(token)
    now = [datetime(2026, 8, 12, 12, 1, tzinfo=UTC)]
    runtime = _runtime(
        tmp_path,
        monkeypatch,
        token,
        _ScriptedTransport(expected_token=token),
        manifest,
        clock=lambda: now[0],
        attestation_clock=lambda: now[0],
    )
    first = runtime.start(_start_request(manifest))
    now[0] = datetime(2026, 8, 12, 12, 6, tzinfo=UTC)

    refreshed = runtime.start(
        _start_request(
            manifest,
            attestation_observed_at="2026-08-12T12:06:00Z",
            nonce_byte=b"r",
        )
    )

    assert first.ready is True
    assert refreshed.ready is True
    assert refreshed.runtime_manifest_payload_hash == manifest.artifact_payload_hash


def test_completion_parse_failure_does_not_chain_raw_model_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "redacted-error-token"
    sensitive_output = "SENSITIVE_REASONING_MARKER_" * 40_001
    transport = _ScriptedTransport(
        expected_token=token,
        completion_content=sensitive_output,
    )
    manifest = _manifest(token)
    runtime = _runtime(tmp_path, monkeypatch, token, transport, manifest)
    assert runtime.start(_start_request(manifest)).ready is True

    with pytest.raises(LocalLlmRuntimeStateError) as error:
        runtime.complete(_completion_request(manifest))

    assert error.value.state == "DEGRADED"
    assert error.value.__cause__ is None
    assert "SENSITIVE_REASONING_MARKER" not in repr(error.value)


def test_probe_and_completion_artifacts_use_their_actual_creation_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "artifact-time-token"
    manifest = _manifest(token)
    repository = _manifest_repository(manifest)[0]
    now = [datetime(2026, 8, 12, 12, 10, tzinfo=UTC)]
    runtime = _runtime(
        tmp_path,
        monkeypatch,
        token,
        _ScriptedTransport(expected_token=token),
        manifest,
        clock=lambda: now[0],
        repository=repository,
    )

    probe = runtime.start(_start_request(manifest))
    assert probe.probe_evidence_artifact_id is not None
    assert (
        repository.get(probe.probe_evidence_artifact_id).envelope.created_at
        == "2026-08-12T12:10:00Z"
    )

    now[0] = datetime(2026, 8, 12, 12, 11, tzinfo=UTC)
    result = runtime.complete(_completion_request(manifest))

    assert (
        repository.get(result.response_artifact_id).envelope.created_at
        == "2026-08-12T12:11:00Z"
    )
    assert (
        repository.get(result.trace_artifact_id).envelope.created_at
        == "2026-08-12T12:11:00Z"
    )


def test_attached_runtime_requires_all_three_readiness_probes_and_keeps_auth_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "contract-token"
    transport = _ScriptedTransport(expected_token=token)
    manifest = _manifest(token)
    runtime = _runtime(tmp_path, monkeypatch, token, transport, manifest)

    probe = runtime.start(_start_request(manifest))

    assert probe.ready is True
    assert probe.readiness_state == "READY"
    assert [request[1] for request in transport.requests] == [
        "http://127.0.0.1:8000/health",
        "http://127.0.0.1:8000/v1/models",
        "http://127.0.0.1:8000/v1/models",
        "http://127.0.0.1:8000/v1/models",
        "http://127.0.0.1:8000/tokenize",
        "http://127.0.0.1:8000/v1/chat/completions",
    ]
    assert "Authorization" not in transport.requests[0][2]
    assert "Authorization" not in transport.requests[1][2]
    assert transport.requests[2][2]["Authorization"] != f"Bearer {token}"
    assert transport.requests[3][2]["Authorization"] == f"Bearer {token}"
    assert "Authorization" not in transport.requests[4][2]
    assert transport.requests[5][2]["Authorization"] == f"Bearer {token}"
    assert probe.probe_evidence_artifact_id is not None
    assert probe.probe_evidence_payload_hash is not None


@pytest.mark.parametrize(
    ("canary_has_tool_call", "canary_finish_reason"),
    [
        (True, "stop"),
        (False, "tool_calls"),
    ],
    ids=["nonempty-tool-calls", "tool-call-finish-reason"],
)
def test_readiness_canary_rejects_tool_call_control_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    canary_has_tool_call: bool,
    canary_finish_reason: str,
) -> None:
    token = "canary-tool-control-token"
    transport = _ScriptedTransport(
        expected_token=token,
        canary_has_tool_call=canary_has_tool_call,
        canary_finish_reason=canary_finish_reason,
    )
    manifest = _manifest(token)
    runtime = _runtime(tmp_path, monkeypatch, token, transport, manifest)

    probe = runtime.start(_start_request(manifest))

    assert probe.ready is False
    assert probe.readiness_state == "WAITING_RUNTIME"
    assert probe.authenticated_completion_passed is False
    assert probe.failure_code == "completion_failed"


def test_completion_trace_excludes_credentials_endpoint_and_reasoning_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "trace-secret-token"
    transport = _ScriptedTransport(expected_token=token)
    manifest = _manifest(token)
    runtime = _runtime(tmp_path, monkeypatch, token, transport)
    assert runtime.start(_start_request(manifest)).ready

    result = runtime.complete(_completion_request(manifest))
    encoded = result.model_dump_json()

    assert result.response.content == "final answer"
    assert result.trace.response_ref.artifact_id == result.response_artifact_id
    assert result.response_payload_hash.root == result.response.payload_hash
    assert token not in encoded
    assert "reasoning_content" not in encoded
    assert manifest.base_url not in encoded
    assert "AUTOMARKOV_VLLM_API_KEY_FILE" not in encoded


def test_disabled_thinking_rejects_reasoning_markup_before_public_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "disabled-thinking-markup-token"
    manifest = _manifest(token)
    repository = _RecordingArtifactRepository()
    _manifest_repository(manifest, repository=repository)
    transport = _ScriptedTransport(
        expected_token=token,
        completion_content="<think>private reasoning</think>\nfinal answer",
    )
    runtime = _runtime(
        tmp_path,
        monkeypatch,
        token,
        transport,
        manifest,
        repository=repository,
    )
    assert runtime.start(_start_request(manifest)).ready
    repository.put_artifact_types.clear()

    with pytest.raises(LocalLlmRuntimeStateError) as error:
        runtime.complete(_completion_request(manifest))

    assert error.value.state == "DEGRADED"
    assert error.value.__cause__ is None
    assert "llm_completion_response" not in repository.put_artifact_types
    assert "llm_completion_trace" not in repository.put_artifact_types


def test_completion_trace_keeps_its_connection_evidence_when_probe_is_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "reentrant-proof-token"
    transport = _ScriptedTransport(expected_token=token)
    manifest = _manifest(token)
    runtime = _runtime(tmp_path, monkeypatch, token, transport)
    assert runtime.start(_start_request(manifest)).ready
    opened_before = len(transport.opened_connections)
    rejected_states: list[str] = []

    def attempt_probe() -> None:
        try:
            runtime.probe()
        except LocalLlmRuntimeStateError as error:
            rejected_states.append(error.state)

    transport.reenter_before_response = attempt_probe
    transport.reenter_path = "/v1/chat/completions"

    result = runtime.complete(_completion_request(manifest))

    completion_proofs = [
        evidence_hash
        for url, evidence_hash in transport.opened_connections[opened_before:]
        if url.endswith("/v1/chat/completions")
    ]
    assert rejected_states == ["BUSY"]
    assert len(completion_proofs) == 1
    assert result.trace.connection_evidence_hash == completion_proofs[0]


def test_runtime_rebind_is_rejected_while_a_completion_is_in_flight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "runtime-rebind-token"
    transport = _ScriptedTransport(expected_token=token)
    manifest = _manifest(token)
    runtime = _runtime(tmp_path, monkeypatch, token, transport)
    start_request = _start_request(manifest)
    assert runtime.start(start_request).ready
    rejected_states: list[str] = []

    def attempt_rebind() -> None:
        try:
            runtime.start(start_request)
        except LocalLlmRuntimeStateError as error:
            rejected_states.append(error.state)

    transport.reenter_before_response = attempt_rebind
    transport.reenter_path = "/v1/chat/completions"

    result = runtime.complete(_completion_request(manifest))

    assert rejected_states == ["BUSY"]
    assert (
        result.trace.runtime_manifest_ref
        == start_request.host_attestation.runtime_manifest_ref
    )


def test_readiness_canary_uses_the_completion_concurrency_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "canary-capacity-token"
    transport = _ScriptedTransport(expected_token=token)
    manifest = _manifest(token, max_concurrency=1)
    runtime = _runtime(tmp_path, monkeypatch, token, transport, manifest)
    assert runtime.start(_start_request(manifest)).ready
    rejected_states: list[str] = []

    def attempt_probe() -> None:
        try:
            runtime.probe()
        except LocalLlmRuntimeStateError as error:
            rejected_states.append(error.state)

    transport.reenter_before_response = attempt_probe
    transport.reenter_path = "/v1/chat/completions"
    opened_before = len(transport.opened_connections)

    runtime.complete(_completion_request(manifest))

    generated = [
        url
        for url, _ in transport.opened_connections[opened_before:]
        if url.endswith("/v1/chat/completions")
    ]
    assert generated == ["http://127.0.0.1:8000/v1/chat/completions"]
    assert rejected_states == ["BUSY"]
    assert (
        runtime.complete(
            _completion_request(manifest, request_id="llmreq_after_busy_probe")
        ).response.content
        == "final answer"
    )


def test_completion_request_id_replay_is_rejected_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "completion-replay-token"
    transport = _ScriptedTransport(expected_token=token)
    manifest = _manifest(token)
    runtime = _runtime(tmp_path, monkeypatch, token, transport)
    assert runtime.start(_start_request(manifest)).ready
    request = _completion_request(manifest)
    runtime.complete(request)
    requests_after_first_completion = len(transport.requests)

    with pytest.raises(LocalLlmRuntimeStateError) as raised:
        runtime.complete(request)

    assert raised.value.state == "REPLAY"
    assert len(transport.requests) == requests_after_first_completion


def test_prompt_token_ceiling_is_enforced_before_completion_is_sent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "prompt-ceiling-token"
    transport = _ScriptedTransport(
        expected_token=token,
    )
    manifest = _manifest(token)
    runtime = _runtime(tmp_path, monkeypatch, token, transport)
    assert runtime.start(_start_request(manifest)).ready
    transport.tokenizer_count = 8_193
    request_count = len(transport.requests)

    with pytest.raises(ValueError, match="prompt token ceiling"):
        runtime.complete(_completion_request(manifest))

    assert len(transport.requests) == request_count + 1
    assert transport.requests[-1][1] == "http://127.0.0.1:8000/tokenize"


def test_completion_usage_cannot_exceed_the_request_token_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "request-budget-token"
    transport = _ScriptedTransport(
        expected_token=token,
        reported_completion_tokens=2,
    )
    manifest = _manifest(token)
    runtime = _runtime(tmp_path, monkeypatch, token, transport)
    assert runtime.start(_start_request(manifest)).ready
    request = _completion_request(manifest).model_copy(
        update={
            "sampling": LlmSampling(
                temperature="0",
                top_p="1",
                seed=7,
                max_tokens=1,
            )
        }
    )

    with pytest.raises(LocalLlmRuntimeStateError) as error:
        runtime.complete(request)

    assert error.value.state == "TOKEN_BUDGET_DRIFT"


def test_readiness_canary_uses_token_admission_before_authenticated_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "canary-admission-token"
    transport = _ScriptedTransport(expected_token=token)
    manifest = _manifest(token, max_completion_tokens=1)
    runtime = _runtime(tmp_path, monkeypatch, token, transport, manifest)

    probe = runtime.start(_start_request(manifest))

    assert probe.ready is True
    canary_index = next(
        index
        for index, (_, url, _, _) in enumerate(transport.requests)
        if url.endswith("/v1/chat/completions")
    )
    assert transport.requests[canary_index - 1][1].endswith("/tokenize")
    canary_body = json.loads(transport.requests[canary_index][3] or b"{}")
    assert canary_body["max_tokens"] == 1


def test_identity_drift_returns_waiting_runtime_without_a_hosted_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "drift-token"
    transport = _ScriptedTransport(served_model="wrong-model", expected_token=token)
    runtime = _runtime(tmp_path, monkeypatch, token, transport)

    probe = runtime.start(_start_request(_manifest(token)))

    assert probe.ready is False
    assert probe.readiness_state == "WAITING_RUNTIME"
    assert probe.failure_code == "identity_mismatch"
    assert len(transport.requests) == 4
    with pytest.raises(LocalLlmRuntimeStateError):
        runtime.complete(_completion_request(_manifest(token)))


@pytest.mark.parametrize(
    "updates",
    [
        {"base_url": "https://127.0.0.1:8000/v1"},
        {"base_url": "http://localhost:8000/v1"},
        {"base_url": "http://127.0.0.1:8000/v1/"},
        {"model_id": "Qwen/Qwen3-32B"},
        {"profile_id": "authoring"},
        {"reasoning_parser": "deepseek_r1"},
        {"tool_call_parser": "hermes"},
    ],
)
def test_manifest_rejects_noncanonical_runtime_identity(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _manifest("token", **updates)


def test_runtime_manifest_round_trips_with_byte_identical_canonical_identity() -> None:
    manifest = _manifest("token")

    restored = LocalLlmRuntimeManifest.model_validate_json(manifest.model_dump_json())

    assert restored == manifest
    assert restored.identity_hash == manifest.identity_hash
    assert tuple(restored.weight_shard_hashes) == tuple(manifest.weight_shard_hashes)


def test_valid_function_tool_call_response_is_parsed_without_degradation() -> None:
    manifest = _manifest("token")
    body = canonical_json_bytes(
        {
            "id": "completion_tool_call",
            "created": 1,
            "object": "chat.completion",
            "model": manifest.served_model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_exact",
                                "type": "function",
                                "function": {
                                    "name": "inspect_public_evidence",
                                    "arguments": '{"filters":["public"],"limit":1}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {
                "prompt_tokens": 8,
                "completion_tokens": 3,
                "total_tokens": 11,
            },
        }
    )

    response, usage = AttachedLocalLlmRuntime._parse_completion(
        object.__new__(AttachedLocalLlmRuntime),
        body,
        manifest,
    )

    assert response.model_dump(mode="json")["tool_calls"] == [
        {
            "call_id": "call_exact",
            "name": "inspect_public_evidence",
            "arguments": {"filters": ["public"], "limit": 1},
        }
    ]
    assert usage.total_tokens == 11


@pytest.mark.parametrize(
    ("tool_calls", "finish_reason"),
    [
        ((), "tool_calls"),
        (
            (
                LlmToolCall(call_id="call_dup", name="inspect", arguments={}),
                LlmToolCall(call_id="call_dup", name="inspect", arguments={}),
            ),
            "tool_calls",
        ),
    ],
    ids=["missing-tool-call", "duplicate-call-id"],
)
def test_response_rejects_inconsistent_tool_call_control_flow(
    tool_calls: tuple[LlmToolCall, ...],
    finish_reason: str,
) -> None:
    with pytest.raises(ValidationError):
        LlmResponsePayload.model_validate(
            {
                "schema_version": "automarkov.llm-response.v1",
                "content": "final",
                "tool_calls": tool_calls,
                "finish_reason": finish_reason,
            },
            strict=True,
        )


def test_credential_file_must_be_owner_only_regular_and_match_the_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "permission-token"
    runtime = _runtime(
        tmp_path,
        monkeypatch,
        token,
        _ScriptedTransport(expected_token=token),
    )
    credential = Path(os.environ["AUTOMARKOV_VLLM_API_KEY_FILE"])
    credential.chmod(0o644)

    probe = runtime.start(_start_request(_manifest(token)))

    assert probe.ready is False
    assert probe.failure_code == "credential_invalid"


def test_current_connection_identity_is_verified_before_credential_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "proof-before-credential-token"
    transport = _ScriptedTransport(
        expected_token=token,
        invalid_identity_on_open=4,
    )
    runtime = _runtime(tmp_path, monkeypatch, token, transport)
    Path(os.environ["AUTOMARKOV_VLLM_API_KEY_FILE"]).unlink()

    probe = runtime.start(_start_request(_manifest(token)))

    assert probe.ready is False
    assert probe.failure_code == "identity_mismatch"
    assert transport.open_count == 4
    assert transport.close_count == 4
    assert len(transport.requests) == 3
    assert token not in probe.model_dump_json()


def test_credential_symlink_is_rejected_without_following_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "symlink-token"
    target = tmp_path / "target"
    target.write_text(token, encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "credential-link"
    link.symlink_to(target)
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setenv("AUTOMARKOV_VLLM_API_KEY_FILE", str(link))
    monkeypatch.setenv("AUTOMARKOV_VLLM_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("AUTOMARKOV_VLLM_MODEL", "Qwen/Qwen3.6-35B-A3B")
    monkeypatch.setenv("AUTOMARKOV_VLLM_TIMEOUT_SECONDS", "30")
    runtime = AttachedLocalLlmRuntime(
        repository_root=repository,
        artifact_repository=_manifest_repository(_manifest(token))[0],
        attestation_verifier=_attestation_verifier(),
        connection_provider=_ScriptedTransport(expected_token=token),
        evidence_access_controller=_generation_evidence_context()[0],
    )

    probe = runtime.start(_start_request(_manifest(token)))

    assert probe.ready is False
    assert probe.failure_code == "credential_invalid"


def test_symlinked_repository_root_cannot_hide_a_worktree_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "repository-alias-token"
    repository = tmp_path / "real-repository"
    repository.mkdir()
    credential = repository / "not-secrets.key"
    credential.write_text(token, encoding="utf-8")
    credential.chmod(0o600)
    repository_alias = tmp_path / "repository-alias"
    repository_alias.symlink_to(repository, target_is_directory=True)
    monkeypatch.setenv("AUTOMARKOV_VLLM_API_KEY_FILE", str(credential))
    monkeypatch.setenv("AUTOMARKOV_VLLM_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("AUTOMARKOV_VLLM_MODEL", "Qwen/Qwen3.6-35B-A3B")
    monkeypatch.setenv("AUTOMARKOV_VLLM_TIMEOUT_SECONDS", "30")
    runtime = AttachedLocalLlmRuntime(
        repository_root=repository_alias,
        artifact_repository=_manifest_repository(_manifest(token))[0],
        attestation_verifier=_attestation_verifier(),
        connection_provider=_ScriptedTransport(expected_token=token),
        evidence_access_controller=_generation_evidence_context()[0],
    )

    probe = runtime.start(_start_request(_manifest(token)))

    assert probe.ready is False
    assert probe.failure_code == "credential_invalid"


def test_credential_failures_never_leak_the_locator_through_an_exception_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "credential-cause-token"
    transport = _ScriptedTransport(expected_token=token)
    manifest = _manifest(token)
    runtime = _runtime(tmp_path, monkeypatch, token, transport)
    assert runtime.start(_start_request(manifest)).ready
    credential = Path(os.environ["AUTOMARKOV_VLLM_API_KEY_FILE"])
    credential.unlink()

    with pytest.raises(LocalLlmRuntimeStateError) as error:
        runtime.complete(_completion_request(manifest))

    assert error.value.state == "DEGRADED"
    assert error.value.__cause__ is None
    assert str(credential) not in repr(error.value)


def test_git_ignore_timeout_is_normalized_without_leaking_the_credential_locator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "credential-timeout-token"
    repository = tmp_path / "repository"
    secrets = repository / "secrets"
    secrets.mkdir(parents=True)
    credential = secrets / "vllm-api-key"
    credential.write_text(token + "\n", encoding="utf-8")
    credential.chmod(0o600)
    monkeypatch.setenv("AUTOMARKOV_VLLM_API_KEY_FILE", str(credential))
    monkeypatch.setenv("AUTOMARKOV_VLLM_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("AUTOMARKOV_VLLM_MODEL", "Qwen/Qwen3.6-35B-A3B")
    monkeypatch.setenv("AUTOMARKOV_VLLM_TIMEOUT_SECONDS", "30")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("git check-ignore", timeout=5)
        ),
    )
    manifest = _manifest(token)
    runtime = AttachedLocalLlmRuntime(
        repository_root=repository,
        artifact_repository=_manifest_repository(manifest)[0],
        attestation_verifier=_attestation_verifier(),
        connection_provider=_ScriptedTransport(expected_token=token),
        evidence_access_controller=_generation_evidence_context()[0],
    )

    probe = runtime.start(_start_request(manifest))

    assert probe.ready is False
    assert probe.failure_code == "credential_invalid"
    assert str(credential) not in probe.model_dump_json()


def test_attached_close_never_calls_a_remote_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "close-token"
    transport = _ScriptedTransport(expected_token=token)
    runtime = _runtime(tmp_path, monkeypatch, token, transport)
    assert runtime.start(_start_request(_manifest(token))).ready
    request_count = len(transport.requests)

    result = runtime.close()

    assert result.closed is True
    assert len(transport.requests) == request_count


def test_forged_start_request_is_revalidated_before_runtime_state_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "forged-token"
    runtime = _runtime(
        tmp_path,
        monkeypatch,
        token,
        _ScriptedTransport(expected_token=token),
    )
    forged = LlmStartRequest.model_construct(
        schema_version="automarkov.llm-start-request.v4"
    )

    with pytest.raises(ValidationError):
        runtime.start(forged)


def test_service_without_authentication_middleware_never_becomes_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "authentication-contract-token"
    transport = _ScriptedTransport(
        expected_token=token,
        enforce_authentication=False,
    )
    runtime = _runtime(tmp_path, monkeypatch, token, transport)

    probe = runtime.start(_start_request(_manifest(token)))

    assert probe.ready is False
    assert probe.authentication_enforced_passed is False
    assert probe.failure_code == "authentication_not_enforced"
    assert len(transport.requests) == 3


def test_rejected_host_attestation_cannot_be_bypassed_by_a_later_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "rejected-attestation-token"
    transport = _ScriptedTransport(expected_token=token)
    runtime = _runtime(tmp_path, monkeypatch, token, transport)
    request = _start_request(_manifest(token))
    rejected_attestation = request.host_attestation.model_copy(
        update={"signature": urlsafe_b64encode(b"\x00" * 64).decode().rstrip("=")}
    )

    result = runtime.start(
        request.model_copy(update={"host_attestation": rejected_attestation})
    )

    assert result.ready is False
    assert result.failure_code == "manifest_invalid"
    assert transport.requests == []
    with pytest.raises(LocalLlmRuntimeStateError) as error:
        runtime.probe()
    assert error.value.state == "NOT_STARTED"


def test_missing_host_evidence_fails_closed_as_waiting_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "missing-host-evidence-token"
    transport = _ScriptedTransport(expected_token=token)

    class _MissingEvidenceResolver:
        def payload_hash(self, artifact_id: str) -> str:
            raise KeyError(artifact_id)

    credential = tmp_path / "vllm-api-key"
    credential.write_text(token + "\n", encoding="utf-8")
    credential.chmod(0o600)
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setenv("AUTOMARKOV_VLLM_API_KEY_FILE", str(credential))
    monkeypatch.setenv("AUTOMARKOV_VLLM_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("AUTOMARKOV_VLLM_MODEL", "Qwen/Qwen3.6-35B-A3B")
    monkeypatch.setenv("AUTOMARKOV_VLLM_TIMEOUT_SECONDS", "30")
    runtime = AttachedLocalLlmRuntime(
        repository_root=repository,
        artifact_repository=_manifest_repository(_manifest(token))[0],
        attestation_verifier=_attestation_verifier(
            evidence_resolver=_MissingEvidenceResolver()
        ),
        connection_provider=transport,
        evidence_access_controller=_generation_evidence_context()[0],
    )

    result = runtime.start(_start_request(_manifest(token)))

    assert result.ready is False
    assert result.failure_code == "manifest_invalid"
    assert transport.requests == []


def test_probe_and_completion_reject_a_stale_host_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "stale-host-attestation-token"
    now = [datetime(2026, 8, 12, 12, 1, tzinfo=UTC)]
    transport = _ScriptedTransport(expected_token=token)
    credential = tmp_path / "vllm-api-key"
    credential.write_text(token + "\n", encoding="utf-8")
    credential.chmod(0o600)
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setenv("AUTOMARKOV_VLLM_API_KEY_FILE", str(credential))
    monkeypatch.setenv("AUTOMARKOV_VLLM_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("AUTOMARKOV_VLLM_MODEL", "Qwen/Qwen3.6-35B-A3B")
    monkeypatch.setenv("AUTOMARKOV_VLLM_TIMEOUT_SECONDS", "30")
    runtime = AttachedLocalLlmRuntime(
        repository_root=repository,
        artifact_repository=_manifest_repository(_manifest(token))[0],
        attestation_verifier=_attestation_verifier(clock=lambda: now[0]),
        connection_provider=transport,
        evidence_access_controller=_generation_evidence_context()[0],
    )
    manifest = _manifest(token)
    assert runtime.start(_start_request(manifest)).ready is True

    now[0] += timedelta(minutes=6)
    probe = runtime.probe()

    assert probe.ready is False
    assert probe.failure_code == "manifest_invalid"
    with pytest.raises(LocalLlmRuntimeStateError) as error:
        runtime.complete(_completion_request(manifest))
    assert error.value.state == "WAITING_RUNTIME"
