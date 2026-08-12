from __future__ import annotations

from base64 import urlsafe_b64encode
from hashlib import sha256
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from automarkov.adapters import InMemoryArtifactRepository
from automarkov.canonical import canonical_json_bytes
from automarkov.domain import ArtifactId, Sha256Digest
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
    AssistantChatMessage,
    LlmCompletionRequest,
    LlmCompletionResponseArtifact,
    LlmCompletionTrace,
    LlmPromptArtifact,
    LlmPromptToolCall,
    LlmPromptToolFunction,
    LlmResponsePayload,
    LlmSampling,
    LlmStartRequest,
    LlmUsage,
    LocalLlmRuntimeManifest,
    RuntimeArtifactReference,
    RuntimeHostAttestation,
    RuntimeModelSnapshotEvidence,
    RuntimePackageEvidence,
    RuntimeProbeEvidence,
    RuntimeProcessEvidence,
    ToolChatMessage,
    UserChatMessage,
)
from automarkov.local_llm_runtime import (
    AttachedLocalLlmRuntime,
    RuntimeConnectionExpectation,
    RuntimeHttpRequestBinding,
    VerifiedRuntimeConnection,
)
from automarkov.public import ArtifactPutResult
from automarkov.repository import ArtifactParentContractError

_CREATED_AT = "2026-08-12T12:00:00Z"
_CREATED_BY = "principal_t05_contract"
_EVIDENCE_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"\x03" * 32)


def _digest(value: str) -> str:
    return "sha256:" + sha256(value.encode("utf-8")).hexdigest()


def _generation_evidence_context() -> tuple[
    EvidenceAccessController,
    GenerationEvidenceView,
]:
    store = EvidenceStoreRef(
        schema_version="automarkov.evidence-store-ref.v1",
        store_id="store_allowed_artifact_contract",
        tier="allowed_evidence",
        identity_hash=_digest("allowed-artifact-evidence"),
    )
    payload: dict[str, object] = {
        "schema_version": "automarkov.evidence-capability-grant.v1",
        "signing_domain": "AutoMarkov-Evidence-Capability-Grant-v1",
        "capability_id": "capability_artifact_contract",
        "principal_id": "principal_artifact_contract",
        "principal_kind": "text_agent",
        "tiers": ["allowed_evidence"],
        "store_ids": [store.store_id],
        "store_identity_hashes": {store.store_id: store.identity_hash},
        "issuer_key_id": "key_artifact_evidence",
        "nonce": urlsafe_b64encode(b"g" * 32).decode().rstrip("="),
        "signature_algorithm": "Ed25519",
        "signature": urlsafe_b64encode(b"\x00" * 64).decode().rstrip("="),
    }
    unsigned = EvidenceCapabilityGrant.model_validate(payload, strict=True)
    payload["signature"] = (
        urlsafe_b64encode(_EVIDENCE_PRIVATE_KEY.sign(unsigned.signing_bytes()))
        .decode()
        .rstrip("=")
    )
    grant = validate_evidence_grant_payload(payload)
    controller = EvidenceAccessController(
        authenticated_principal_id=grant.principal_id,
        trusted_issuer_keys={
            "key_artifact_evidence": _EVIDENCE_PRIVATE_KEY.public_key()
        },
        registered_stores={store.store_id: store},
    )
    return controller, controller.issue_generation_view(grant, (store,))


def test_default_repository_accepts_typed_runtime_process_evidence_root() -> None:
    repository = InMemoryArtifactRepository()
    evidence = RuntimeProcessEvidence(
        schema_version="automarkov.runtime-process-evidence.v2",
        runtime_id="runtime_artifact_binding",
        observed_at=_CREATED_AT,
        lifecycle_mode="ATTACHED",
        listener_identity_hash=_digest("listener"),
        process_identity_hash=_digest("process"),
        relay_identity_hash=_digest("relay"),
        route_policy_hash=REQUIRED_RUNTIME_ROUTE_POLICY_HASH,
        startup_args=(
            "/models/Qwen3.6-35B-A3B",
            "--api-key",
            "[REDACTED]",
        ),
    )

    persisted = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "runtime_process_evidence",
            "payload_bytes": canonical_json_bytes(
                evidence.model_dump(
                    mode="json",
                    round_trip=True,
                    warnings="error",
                )
            ),
            "parent_artifact_ids": [],
            "created_by": _CREATED_BY,
            "created_at": _CREATED_AT,
            "source_evidence_ids": [],
        }
    )

    assert repository.get(persisted.artifact_id).envelope.parent_artifact_ids == ()


def _put(
    repository: InMemoryArtifactRepository,
    artifact_type: str,
    payload: (
        LlmPromptArtifact
        | LocalLlmRuntimeManifest
        | RuntimeProcessEvidence
        | RuntimePackageEvidence
        | RuntimeModelSnapshotEvidence
        | RuntimeHostAttestation
        | RuntimeProbeEvidence
        | LlmCompletionResponseArtifact
        | LlmCompletionTrace
    ),
    *,
    parents: tuple[ArtifactId, ...] = (),
) -> ArtifactPutResult:
    return repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": artifact_type,
            "payload_bytes": canonical_json_bytes(
                payload.model_dump(mode="json", round_trip=True, warnings="error")
            ),
            "parent_artifact_ids": [
                parent.root
                for parent in sorted(parents, key=lambda item: item.root.encode())
            ],
            "created_by": _CREATED_BY,
            "created_at": _CREATED_AT,
            "source_evidence_ids": [],
        }
    )


def _prompt() -> LlmPromptArtifact:
    return LlmPromptArtifact(
        schema_version="automarkov.llm-prompt.v3",
        generation_evidence_view=_generation_evidence_context()[1],
        messages=(
            UserChatMessage(
                role="user",
                content="Return the canonical artifact-bound response.",
            ),
        ),
    )


def _manifest() -> LocalLlmRuntimeManifest:
    revision = "995ad96eacd98c81ed38be0c5b274b04031597b0"
    checkpoint = "/models/Qwen3.6-35B-A3B"
    return LocalLlmRuntimeManifest.model_validate(
        {
            "schema_version": "automarkov.local-llm-runtime-manifest.v3",
            "runtime_id": "runtime_artifact_binding",
            "lifecycle_mode": "ATTACHED",
            "profile_id": "llm-qwen36-vllm",
            "base_url": "http://127.0.0.1:8000/v1",
            "model_id": "Qwen/Qwen3.6-35B-A3B",
            "model_checkpoint_path": checkpoint,
            "tokenizer_checkpoint_path": checkpoint,
            "served_model_name": "Qwen/Qwen3.6-35B-A3B",
            "observed_at": _CREATED_AT,
            "model_revision": revision,
            "tokenizer_revision": revision,
            "model_config_hash": (
                "sha256:93a4693fa9d8392fbfccd4b3c9873f4bfdcb14fdede978b123d07d19675efe99"
            ),
            "weight_index_hash": (
                "sha256:41b9356101ebf8e7519e150dc811f80c4226e727301fbb032b890f006ed0be83"
            ),
            "weight_shard_hashes": dict(OFFICIAL_QWEN_WEIGHT_SHARD_HASHES),
            "tokenizer_hash": (
                "sha256:5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42"
            ),
            "tokenizer_config_hash": (
                "sha256:5186f0defcd7f232382c7f0aebcd2252d073bb921ab240e407b7ae8745d2b29b"
            ),
            "chat_template_hash": (
                "sha256:e84f32a23fdda27689f868aa4a1a5621f41133e51a48d7f3efcbea2839574259"
            ),
            "vllm_version": "0.25.1+cu129",
            "vllm_distribution_hash": (
                "sha256:9e206f370c934a2d4b6b1f05d3d09708d344e05d80260189ef19f60755709431"
            ),
            "runtime_environment_hash": _digest("runtime-environment"),
            "pytorch_version": "2.9.1",
            "cuda_version": "12.9",
            "container_digest": _digest("container"),
            "startup_args": (
                checkpoint,
                "--revision",
                revision,
                "--tokenizer",
                checkpoint,
                "--tokenizer-revision",
                revision,
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
            "credential_fingerprint": _digest("credential"),
            "max_model_len": 32_768,
            "max_concurrency": 2,
            "request_timeout_seconds": 30,
            "max_prompt_tokens": 8_192,
            "max_completion_tokens": 2_048,
            "reasoning_parser": "qwen3",
            "tool_call_parser": "qwen3_coder",
            "thinking_policy": "disabled",
            "chat_template_policy": "enable_thinking=false",
        },
        strict=True,
    )


def _reference(result: ArtifactPutResult) -> RuntimeArtifactReference:
    return RuntimeArtifactReference(
        artifact_id=result.artifact_id,
        payload_hash=result.payload_hash.root,
    )


def _host_attestation(
    manifest_artifact: ArtifactPutResult,
    *,
    process_evidence: ArtifactPutResult | None = None,
    package_evidence: ArtifactPutResult | None = None,
    model_snapshot_evidence: ArtifactPutResult | None = None,
) -> RuntimeHostAttestation:
    process_ref = (
        _reference(process_evidence)
        if process_evidence is not None
        else RuntimeArtifactReference(
            artifact_id=ArtifactId(root="artifact_" + "c" * 64),
            payload_hash=_digest("process-evidence"),
        )
    )
    package_ref = (
        _reference(package_evidence)
        if package_evidence is not None
        else RuntimeArtifactReference(
            artifact_id=ArtifactId(root="artifact_" + "d" * 64),
            payload_hash=_digest("package-evidence"),
        )
    )
    snapshot_ref = (
        _reference(model_snapshot_evidence)
        if model_snapshot_evidence is not None
        else RuntimeArtifactReference(
            artifact_id=ArtifactId(root="artifact_" + "e" * 64),
            payload_hash=_digest("model-evidence"),
        )
    )
    return RuntimeHostAttestation(
        schema_version="automarkov.runtime-host-attestation.v3",
        signing_domain="AutoMarkov-Runtime-Host-Attestation-v3",
        attestation_id="runtimeatt_artifact_binding",
        runtime_manifest_ref=_reference(manifest_artifact),
        process_evidence_ref=process_ref,
        package_evidence_ref=package_ref,
        model_snapshot_evidence_ref=snapshot_ref,
        observed_at=_CREATED_AT,
        nonce=urlsafe_b64encode(b"n" * 32).decode("ascii").rstrip("="),
        signature_algorithm="Ed25519",
        signing_key_id="key_runtime_host",
        signature=urlsafe_b64encode(b"s" * 64).decode("ascii").rstrip("="),
    )


def _runtime_evidence(
    manifest: LocalLlmRuntimeManifest,
) -> tuple[
    RuntimeProcessEvidence, RuntimePackageEvidence, RuntimeModelSnapshotEvidence
]:
    process = RuntimeProcessEvidence(
        schema_version="automarkov.runtime-process-evidence.v2",
        runtime_id=manifest.runtime_id,
        observed_at=manifest.observed_at,
        lifecycle_mode=manifest.lifecycle_mode,
        listener_identity_hash=manifest.listener_identity_hash,
        process_identity_hash=manifest.process_identity_hash,
        relay_identity_hash=manifest.relay_identity_hash,
        route_policy_hash=manifest.route_policy_hash,
        startup_args=manifest.startup_args,
    )
    package = RuntimePackageEvidence(
        schema_version="automarkov.runtime-package-evidence.v1",
        runtime_id=manifest.runtime_id,
        observed_at=manifest.observed_at,
        vllm_version=manifest.vllm_version,
        vllm_distribution_hash=manifest.vllm_distribution_hash,
        runtime_environment_hash=manifest.runtime_environment_hash,
        pytorch_version=manifest.pytorch_version,
        cuda_version=manifest.cuda_version,
        container_digest=manifest.container_digest,
    )
    snapshot = RuntimeModelSnapshotEvidence(
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
    )
    return process, package, snapshot


def test_host_attestation_payload_can_bind_the_exact_runtime_manifest_artifact() -> (
    None
):
    repository = InMemoryArtifactRepository()
    manifest = _manifest()
    persisted = _put(repository, "local_llm_runtime_manifest", manifest)
    attestation = _host_attestation(persisted)

    assert attestation.runtime_manifest_ref == _reference(persisted)


def test_default_repository_persists_the_complete_local_llm_artifact_dag() -> None:
    repository = InMemoryArtifactRepository()
    manifest = _manifest()
    manifest_result = _put(repository, "local_llm_runtime_manifest", manifest)
    process, package, snapshot = _runtime_evidence(manifest)
    process_result = _put(repository, "runtime_process_evidence", process)
    package_result = _put(repository, "runtime_package_evidence", package)
    snapshot_result = _put(repository, "runtime_model_snapshot_evidence", snapshot)
    attestation = _host_attestation(
        manifest_result,
        process_evidence=process_result,
        package_evidence=package_result,
        model_snapshot_evidence=snapshot_result,
    )
    attestation_parents = (
        manifest_result.artifact_id,
        process_result.artifact_id,
        package_result.artifact_id,
        snapshot_result.artifact_id,
    )
    attestation_result = _put(
        repository,
        "runtime_host_attestation",
        attestation,
        parents=attestation_parents,
    )
    digest = _digest("probe")
    probe = RuntimeProbeEvidence(
        schema_version="automarkov.runtime-probe-evidence.v3",
        runtime_manifest_ref=_reference(manifest_result),
        runtime_host_attestation_ref=_reference(attestation_result),
        served_model_name=manifest.served_model_name,
        health_response_hash=digest,
        missing_auth_response_hash=digest,
        invalid_auth_response_hash=digest,
        models_response_hash=digest,
        canary_request_hash=digest,
        canary_response_hash=digest,
    )
    probe_result = _put(
        repository,
        "runtime_probe_evidence",
        probe,
        parents=(manifest_result.artifact_id, attestation_result.artifact_id),
    )
    prompt_result = _put(repository, "llm_prompt", _prompt())
    response_payload = LlmResponsePayload(
        schema_version="automarkov.llm-response.v1",
        content="artifact-bound response",
        tool_calls=(),
        finish_reason="stop",
    )
    response = LlmCompletionResponseArtifact(
        schema_version="automarkov.llm-completion-response-artifact.v1",
        request_id="llmreq_artifact_dag",
        runtime_manifest_ref=_reference(manifest_result),
        runtime_probe_evidence_ref=_reference(probe_result),
        prompt_ref=_reference(prompt_result),
        response=response_payload,
    )
    response_parents = (
        manifest_result.artifact_id,
        probe_result.artifact_id,
        prompt_result.artifact_id,
    )
    response_result = _put(
        repository,
        "llm_completion_response",
        response,
        parents=response_parents,
    )
    trace = LlmCompletionTrace(
        schema_version="automarkov.llm-completion-trace.v2",
        request_id="llmreq_artifact_dag",
        model_id=manifest.model_id,
        model_revision=manifest.model_revision,
        vllm_version=manifest.vllm_version,
        tokenizer_hash=manifest.tokenizer_hash,
        chat_template_hash=manifest.chat_template_hash,
        runtime_manifest_ref=_reference(manifest_result),
        runtime_probe_evidence_ref=_reference(probe_result),
        prompt_ref=_reference(prompt_result),
        response_ref=_reference(response_result),
        endpoint_identity_hash=manifest.listener_identity_hash,
        connection_evidence_hash=_digest("connection-evidence"),
        sampling=LlmSampling(
            temperature="0",
            top_p="1",
            seed=0,
            max_tokens=64,
        ),
        usage=LlmUsage(prompt_tokens=4, completion_tokens=2, total_tokens=6),
        latency_ms=1,
        finish_reason="stop",
    )
    trace_parents = response_parents + (response_result.artifact_id,)
    trace_result = _put(
        repository,
        "llm_completion_trace",
        trace,
        parents=trace_parents,
    )

    assert {
        parent.root
        for parent in repository.get(
            attestation_result.artifact_id
        ).envelope.parent_artifact_ids
    } == {parent.root for parent in attestation_parents}
    assert {
        parent.root
        for parent in repository.get(
            probe_result.artifact_id
        ).envelope.parent_artifact_ids
    } == {manifest_result.artifact_id.root, attestation_result.artifact_id.root}
    assert {
        parent.root
        for parent in repository.get(
            response_result.artifact_id
        ).envelope.parent_artifact_ids
    } == {parent.root for parent in response_parents}
    assert {
        parent.root
        for parent in repository.get(
            trace_result.artifact_id
        ).envelope.parent_artifact_ids
    } == {parent.root for parent in trace_parents}

    with pytest.raises(ArtifactParentContractError):
        _put(
            repository,
            "llm_completion_trace",
            trace,
            parents=response_parents,
        )


class _AcceptingAttestationVerifier:
    def verify(
        self,
        manifest: LocalLlmRuntimeManifest,
        attestation: RuntimeHostAttestation,
    ) -> None:
        del manifest, attestation


class _UnexpectedConnectionProvider:
    def open_verified(
        self,
        *,
        expectation: RuntimeConnectionExpectation,
        binding: RuntimeHttpRequestBinding,
        challenge: str,
    ) -> VerifiedRuntimeConnection:
        del expectation, binding, challenge
        raise AssertionError("invalid artifact binding must fail before HTTP transport")


def test_completion_prompt_hash_comes_from_the_artifact_repository() -> None:
    repository = InMemoryArtifactRepository()
    prompt = _prompt()
    persisted = _put(repository, "llm_prompt", prompt)

    request = LlmCompletionRequest(
        schema_version="automarkov.llm-completion-request.v4",
        request_id="llmreq_artifact_binding",
        runtime_manifest_payload_hash=Sha256Digest(root=_digest("runtime-manifest")),
        prompt_artifact_id=persisted.artifact_id,
        prompt_payload_hash=persisted.payload_hash,
        prompt=prompt,
        sampling=LlmSampling(
            temperature="0",
            top_p="1",
            seed=0,
            max_tokens=64,
        ),
    )

    assert request.prompt_payload_hash == persisted.payload_hash


def test_prompt_requires_a_generation_evidence_capability_view() -> None:
    with pytest.raises(ValidationError, match="generation_evidence_view"):
        LlmPromptArtifact.model_validate(
            {
                "schema_version": "automarkov.llm-prompt.v3",
                "messages": [
                    {
                        "role": "user",
                        "content": "Return only from authorized evidence.",
                    }
                ],
            },
            strict=True,
        )


def test_tool_role_prompt_requires_its_tool_call_identity() -> None:
    with pytest.raises(ValidationError, match="tool_call_id"):
        ToolChatMessage.model_validate(
            {"role": "tool", "content": "tool result"},
            strict=True,
        )


def test_prompt_preserves_a_closed_assistant_tool_call_exchange() -> None:
    prompt = LlmPromptArtifact(
        schema_version="automarkov.llm-prompt.v3",
        generation_evidence_view=_generation_evidence_context()[1],
        messages=(
            AssistantChatMessage(
                role="assistant",
                content="",
                tool_calls=(
                    LlmPromptToolCall(
                        id="call_weather",
                        type="function",
                        function=LlmPromptToolFunction(
                            name="get_weather",
                            arguments='{"city":"Shanghai"}',
                        ),
                    ),
                ),
            ),
            ToolChatMessage(
                role="tool",
                content="sunny",
                tool_call_id="call_weather",
            ),
        ),
    )

    payload = prompt.model_dump(mode="json", round_trip=True, warnings="error")
    assert payload["messages"][0]["tool_calls"][0]["id"] == "call_weather"
    assert payload["messages"][1]["tool_call_id"] == "call_weather"


@pytest.mark.parametrize("mismatch", ("missing", "hash", "schema"))
def test_runtime_never_becomes_ready_with_an_invalid_manifest_artifact_binding(
    mismatch: str,
    tmp_path: Path,
) -> None:
    repository = InMemoryArtifactRepository()
    manifest = _manifest()
    manifest_artifact = _put(repository, "local_llm_runtime_manifest", manifest)
    prompt_artifact = _put(repository, "llm_prompt", _prompt())
    artifact_id = manifest_artifact.artifact_id
    payload_hash = manifest_artifact.payload_hash
    if mismatch == "missing":
        artifact_id = ArtifactId(root="artifact_" + "f" * 64)
    elif mismatch == "hash":
        payload_hash = Sha256Digest(root="sha256:" + "f" * 64)
    else:
        artifact_id = prompt_artifact.artifact_id
        payload_hash = prompt_artifact.payload_hash
    claimed_manifest = ArtifactPutResult(
        schema_version="automarkov.artifact-put-result.v1",
        artifact_id=artifact_id,
        payload_hash=payload_hash,
    )
    if mismatch != "missing":
        with pytest.raises(ValidationError):
            LlmStartRequest(
                schema_version="automarkov.llm-start-request.v4",
                runtime_manifest_artifact_id=artifact_id,
                runtime_manifest_payload_hash=payload_hash,
                runtime_manifest=manifest,
                host_attestation=_host_attestation(claimed_manifest),
            )
        return
    request = LlmStartRequest(
        schema_version="automarkov.llm-start-request.v4",
        runtime_manifest_artifact_id=artifact_id,
        runtime_manifest_payload_hash=payload_hash,
        runtime_manifest=manifest,
        host_attestation=_host_attestation(claimed_manifest),
    )
    runtime = AttachedLocalLlmRuntime(
        repository_root=tmp_path,
        artifact_repository=repository,
        attestation_verifier=_AcceptingAttestationVerifier(),
        connection_provider=_UnexpectedConnectionProvider(),
        evidence_access_controller=_generation_evidence_context()[0],
    )

    result = runtime.start(request)

    assert result.ready is False
    assert result.readiness_state == "WAITING_RUNTIME"
    assert result.failure_code == "manifest_invalid"


def test_ready_probe_persists_and_revalidates_immutable_probe_evidence(
    tmp_path: Path,
) -> None:
    repository = InMemoryArtifactRepository()
    manifest = _manifest()
    manifest_artifact = _put(repository, "local_llm_runtime_manifest", manifest)
    request = LlmStartRequest(
        schema_version="automarkov.llm-start-request.v4",
        runtime_manifest_artifact_id=manifest_artifact.artifact_id,
        runtime_manifest_payload_hash=manifest_artifact.payload_hash,
        runtime_manifest=manifest,
        host_attestation=_host_attestation(manifest_artifact),
    )
    runtime = AttachedLocalLlmRuntime(
        repository_root=tmp_path,
        artifact_repository=repository,
        attestation_verifier=_AcceptingAttestationVerifier(),
        connection_provider=_UnexpectedConnectionProvider(),
        evidence_access_controller=_generation_evidence_context()[0],
    )

    result = runtime.start(request)

    assert result.ready is False
    assert result.probe_evidence_artifact_id is None
    assert result.probe_evidence_payload_hash is None
