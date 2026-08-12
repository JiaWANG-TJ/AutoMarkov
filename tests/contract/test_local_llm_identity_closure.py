from __future__ import annotations

from base64 import urlsafe_b64encode
from copy import deepcopy

import pytest
from pydantic import ValidationError

from automarkov.domain import ArtifactId, Sha256Digest
from automarkov.llm_contracts import (
    REQUIRED_RUNTIME_ROUTE_POLICY_HASH,
    LlmCompletionResponseArtifact,
    LlmCompletionResult,
    LlmCompletionTrace,
    LlmProbeResult,
    LlmResponsePayload,
    LlmSampling,
    LlmUsage,
    LocalLlmRuntimeManifest,
    RuntimeArtifactReference,
    RuntimeHostAttestation,
    RuntimeModelSnapshotEvidence,
    RuntimePackageEvidence,
    RuntimeProbeEvidence,
    RuntimeProcessEvidence,
)

_MODEL_REVISION = "995ad96eacd98c81ed38be0c5b274b04031597b0"
_MODEL_ID = "Qwen/Qwen3.6-35B-A3B"
_LOCAL_CHECKPOINT_PATH = "/srv/automarkov/models/Qwen3.6-35B-A3B"
_OFFICIAL_WEIGHT_SHARD_HASHES = {
    "model-00001-of-00026.safetensors": "sha256:adee7bcb930aed22e0677e58d4873b48dadb1ed8001cb5c6a0487286eadb3478",
    "model-00002-of-00026.safetensors": "sha256:88f2dfd2b9e73e4b70be533dbf61bcfa3c9a0003758900fcbc9d9b96f5751d4b",
    "model-00003-of-00026.safetensors": "sha256:8f7d72178d3f4431864978e5bcfa4c6cb1c204bc00590644d90bb19d6d522eeb",
    "model-00004-of-00026.safetensors": "sha256:12d7db38689ba3c8af74b23ef8523eca41e0cd95db870583d0663a3ee8a6bd60",
    "model-00005-of-00026.safetensors": "sha256:a836047305d0f7a7b50f0815d09d5c03ec03d59ec2c763fcdc4bf7e9936bf902",
    "model-00006-of-00026.safetensors": "sha256:c9080d718e9c5f9e337443225aa417d4c24d00ae7995d76ee3f1cc296b557d15",
    "model-00007-of-00026.safetensors": "sha256:e8c05e23131b1dd45a455ec38cfac7db14667358268623c3938d00cf3e959a68",
    "model-00008-of-00026.safetensors": "sha256:4b6a6d495053089f4a80e7cbc82e848fba44e2c0c60122233d8fdff79fa7b296",
    "model-00009-of-00026.safetensors": "sha256:a31a954bb72d1c714e751bf0aabf2ff533f5a509693ebf7dd22ad6e90be46f67",
    "model-00010-of-00026.safetensors": "sha256:246560e66570fe746653b8443e245dc334c9b8b831ea43d2d9f1b7d98623994e",
    "model-00011-of-00026.safetensors": "sha256:7180392817fe3ecb3a27a1da43b7ff22c1a94806bac49975f9f122c3126df675",
    "model-00012-of-00026.safetensors": "sha256:043fb525f6625c2f2acb75e65a9959ee3fa7b6e3fdd2034b5cfe1859b01d3cfb",
    "model-00013-of-00026.safetensors": "sha256:33a20fb20a21379bf43c84a43105f9c0cc35bd50d740b1c302dcbe4b700f5425",
    "model-00014-of-00026.safetensors": "sha256:be823e33c5cb6120ad3769d081f34a2449dc2358041fca7c29d636c1ba19130d",
    "model-00015-of-00026.safetensors": "sha256:a89d547c6f9d0b535ee5ea2f2478f163089539f3f0dd330cb23d278a19d76123",
    "model-00016-of-00026.safetensors": "sha256:69fc3ae0316482288afdcdd0b9eb7d626703ae26f7567e89aa3fc8d1ffd4ff5b",
    "model-00017-of-00026.safetensors": "sha256:e356e3943cf3852b76bb8992e674f3256013e27d54b78e8250514151cdc29637",
    "model-00018-of-00026.safetensors": "sha256:9e5e63fd1cc7d6848330c1fa363dfcb661bbc2ac87e672d0e28b71c9cb7f3c7f",
    "model-00019-of-00026.safetensors": "sha256:708644ad34f1de727bf484f396944d8ec628645d52c183e9a992e65671685e21",
    "model-00020-of-00026.safetensors": "sha256:ca083a1d1aa64f8e8a785998f543a43374f13436dc85d396eee4e72c7a84e1ae",
    "model-00021-of-00026.safetensors": "sha256:ada4ae48f3d48fe01b4c53f2f82bce25e798a9631fd33959c881156fef2ccbce",
    "model-00022-of-00026.safetensors": "sha256:def207fb42d7db31efb512755557763c23233c6e4d4c433027cb5102a7bce2f7",
    "model-00023-of-00026.safetensors": "sha256:864d52ca7768a36f514069222e8de8626264ae124097ba8fcce5b5da2c6e2ed7",
    "model-00024-of-00026.safetensors": "sha256:391acd27420cdce5935ff18152423c70620d19dac3c39a5ef1a81d369f82d737",
    "model-00025-of-00026.safetensors": "sha256:778e7f76602f05042b69ba7f3ec91f1fdffef390540b16074041c258fb81d154",
    "model-00026-of-00026.safetensors": "sha256:1a97404220077ed3d4182e10385b152004cab608377f50cec9f54a6b8d28b613",
}


def _manifest_payload() -> dict[str, object]:
    digest = "sha256:" + "a" * 64
    return {
        "schema_version": "automarkov.local-llm-runtime-manifest.v3",
        "runtime_id": "runtime_identity_closure",
        "lifecycle_mode": "ATTACHED",
        "profile_id": "llm-qwen36-vllm",
        "base_url": "http://127.0.0.1:8000/v1",
        "model_id": _MODEL_ID,
        "model_checkpoint_path": _LOCAL_CHECKPOINT_PATH,
        "tokenizer_checkpoint_path": _LOCAL_CHECKPOINT_PATH,
        "served_model_name": _MODEL_ID,
        "observed_at": "2026-08-12T12:00:00Z",
        "model_revision": _MODEL_REVISION,
        "tokenizer_revision": _MODEL_REVISION,
        "model_config_hash": "sha256:93a4693fa9d8392fbfccd4b3c9873f4bfdcb14fdede978b123d07d19675efe99",
        "weight_index_hash": "sha256:41b9356101ebf8e7519e150dc811f80c4226e727301fbb032b890f006ed0be83",
        "weight_shard_hashes": dict(_OFFICIAL_WEIGHT_SHARD_HASHES),
        "tokenizer_hash": "sha256:5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42",
        "tokenizer_config_hash": "sha256:5186f0defcd7f232382c7f0aebcd2252d073bb921ab240e407b7ae8745d2b29b",
        "chat_template_hash": "sha256:e84f32a23fdda27689f868aa4a1a5621f41133e51a48d7f3efcbea2839574259",
        "vllm_version": "0.25.1+cu129",
        "vllm_distribution_hash": "sha256:9e206f370c934a2d4b6b1f05d3d09708d344e05d80260189ef19f60755709431",
        "runtime_environment_hash": digest,
        "pytorch_version": "2.9.1",
        "cuda_version": "12.9",
        "container_digest": digest,
        "startup_args": [
            _LOCAL_CHECKPOINT_PATH,
            "--revision",
            _MODEL_REVISION,
            "--tokenizer",
            _LOCAL_CHECKPOINT_PATH,
            "--tokenizer-revision",
            _MODEL_REVISION,
            "--served-model-name",
            _MODEL_ID,
            "--max-model-len",
            "32768",
            "--reasoning-parser",
            "qwen3",
            "--tool-call-parser",
            "qwen3_coder",
            "--enable-auto-tool-choice",
            "--api-key",
            "[REDACTED]",
        ],
        "listener_identity_hash": digest,
        "process_identity_hash": digest,
        "relay_identity_hash": digest,
        "route_policy_hash": REQUIRED_RUNTIME_ROUTE_POLICY_HASH,
        "credential_id": "local-llm-server.v1",
        "credential_fingerprint": digest,
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


def _artifact_ref(character: str) -> RuntimeArtifactReference:
    return RuntimeArtifactReference(
        artifact_id=ArtifactId(root="artifact_" + character * 64),
        payload_hash="sha256:" + character * 64,
    )


def _runtime_evidence_payloads() -> tuple[
    RuntimeProcessEvidence,
    RuntimePackageEvidence,
    RuntimeModelSnapshotEvidence,
]:
    manifest = LocalLlmRuntimeManifest.model_validate(_manifest_payload(), strict=True)
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


def test_manifest_separates_public_model_identity_from_local_checkpoint_argv() -> None:
    manifest = LocalLlmRuntimeManifest.model_validate(_manifest_payload(), strict=True)

    assert manifest.model_id == _MODEL_ID
    assert manifest.model_checkpoint_path == _LOCAL_CHECKPOINT_PATH
    assert manifest.tokenizer_checkpoint_path == _LOCAL_CHECKPOINT_PATH
    assert manifest.startup_args[0] == manifest.model_checkpoint_path
    tokenizer_flag = manifest.startup_args.index("--tokenizer")
    assert manifest.startup_args[tokenizer_flag + 1] == (
        manifest.tokenizer_checkpoint_path
    )

    for position, replacement in (
        (0, _MODEL_ID),
        (tokenizer_flag + 1, _MODEL_ID),
    ):
        mismatched = _manifest_payload()
        raw_startup_args = mismatched["startup_args"]
        assert isinstance(raw_startup_args, list)
        startup_args = list(raw_startup_args)
        startup_args[position] = replacement
        mismatched["startup_args"] = startup_args
        with pytest.raises(ValidationError):
            LocalLlmRuntimeManifest.model_validate(mismatched, strict=True)


@pytest.mark.parametrize(
    "invalid_path",
    (
        "/",
        "relative/checkpoint",
        "//srv/automarkov/models/Qwen3.6-35B-A3B",
        "/srv/automarkov/models/Qwen3.6-35B-A3B/",
        "/srv/automarkov/./models/Qwen3.6-35B-A3B",
        "/srv/automarkov/../models/Qwen3.6-35B-A3B",
        "/srv/automarkov/models/Qwen3.6-35B-A3B\x00",
    ),
)
def test_manifest_rejects_noncanonical_local_checkpoint_paths(
    invalid_path: str,
) -> None:
    for field_name in ("model_checkpoint_path", "tokenizer_checkpoint_path"):
        payload = _manifest_payload()
        payload[field_name] = invalid_path
        with pytest.raises(ValidationError):
            LocalLlmRuntimeManifest.model_validate(payload, strict=True)


def test_manifest_freezes_exact_non_thinking_chat_template_policy() -> None:
    manifest = LocalLlmRuntimeManifest.model_validate(_manifest_payload(), strict=True)

    assert manifest.thinking_policy == "disabled"
    assert manifest.chat_template_policy == "enable_thinking=false"

    invalid_policies: tuple[tuple[str, object], ...] = (
        ("thinking_policy", "enabled"),
        ("chat_template_policy", "enable_thinking=true"),
        ("chat_template_policy", "preserve_thinking=true"),
    )
    for field_name, value in invalid_policies:
        payload = _manifest_payload()
        payload[field_name] = value
        with pytest.raises(ValidationError):
            LocalLlmRuntimeManifest.model_validate(payload, strict=True)


def test_host_attestation_uses_typed_refs_for_the_closed_runtime_evidence_dag() -> None:
    manifest = LocalLlmRuntimeManifest.model_validate(_manifest_payload(), strict=True)
    process, package, snapshot = _runtime_evidence_payloads()
    attestation = RuntimeHostAttestation(
        schema_version="automarkov.runtime-host-attestation.v3",
        signing_domain="AutoMarkov-Runtime-Host-Attestation-v3",
        attestation_id="runtimeatt_identity_closure",
        runtime_manifest_ref=_artifact_ref("a"),
        process_evidence_ref=RuntimeArtifactReference(
            artifact_id=_artifact_ref("b").artifact_id,
            payload_hash=process.payload_hash,
        ),
        package_evidence_ref=RuntimeArtifactReference(
            artifact_id=_artifact_ref("c").artifact_id,
            payload_hash=package.payload_hash,
        ),
        model_snapshot_evidence_ref=RuntimeArtifactReference(
            artifact_id=_artifact_ref("d").artifact_id,
            payload_hash=snapshot.payload_hash,
        ),
        observed_at=manifest.observed_at,
        nonce=urlsafe_b64encode(b"n" * 32).decode().rstrip("="),
        signature_algorithm="Ed25519",
        signing_key_id="key_runtime_host",
        signature="A" * 86,
    )

    assert attestation.runtime_manifest_ref == _artifact_ref("a")
    assert attestation.process_evidence_ref.payload_hash == process.payload_hash
    assert attestation.package_evidence_ref.payload_hash == package.payload_hash
    assert attestation.model_snapshot_evidence_ref.payload_hash == snapshot.payload_hash
    assert attestation.payload_hash.startswith("sha256:")

    legacy = attestation.model_dump(mode="json")
    del legacy["runtime_manifest_ref"]
    legacy["runtime_manifest_payload_hash"] = manifest.artifact_payload_hash
    with pytest.raises(ValidationError):
        RuntimeHostAttestation.model_validate(legacy, strict=True)

    noncanonical_nonce = attestation.model_dump(mode="json")
    noncanonical_nonce["nonce"] = "A" * 42 + "B"
    with pytest.raises(ValidationError, match="canonical 32-byte"):
        RuntimeHostAttestation.model_validate(noncanonical_nonce, strict=True)


def test_runtime_evidence_models_freeze_every_manifest_identity_group() -> None:
    manifest = LocalLlmRuntimeManifest.model_validate(_manifest_payload(), strict=True)
    process, package, snapshot = _runtime_evidence_payloads()

    assert process.startup_args == manifest.startup_args
    assert process.process_identity_hash == manifest.process_identity_hash
    assert package.runtime_environment_hash == manifest.runtime_environment_hash
    assert package.container_digest == manifest.container_digest
    assert snapshot.model_checkpoint_path == manifest.model_checkpoint_path
    assert snapshot.tokenizer_checkpoint_path == manifest.tokenizer_checkpoint_path
    assert snapshot.chat_template_policy == manifest.chat_template_policy
    assert dict(snapshot.weight_shard_hashes) == _OFFICIAL_WEIGHT_SHARD_HASHES

    tampered = snapshot.model_dump(mode="json")
    shard_hashes = dict(_OFFICIAL_WEIGHT_SHARD_HASHES)
    shard_hashes["model-00026-of-00026.safetensors"] = "sha256:" + "0" * 64
    tampered["weight_shard_hashes"] = shard_hashes
    with pytest.raises(ValidationError):
        RuntimeModelSnapshotEvidence.model_validate(tampered, strict=True)


def test_probe_evidence_binds_the_signed_host_attestation_artifact() -> None:
    probe = RuntimeProbeEvidence(
        schema_version="automarkov.runtime-probe-evidence.v3",
        runtime_manifest_ref=_artifact_ref("a"),
        runtime_host_attestation_ref=_artifact_ref("e"),
        served_model_name=_MODEL_ID,
        health_response_hash="sha256:" + "1" * 64,
        missing_auth_response_hash="sha256:" + "2" * 64,
        invalid_auth_response_hash="sha256:" + "3" * 64,
        models_response_hash="sha256:" + "4" * 64,
        canary_request_hash="sha256:" + "5" * 64,
        canary_response_hash="sha256:" + "6" * 64,
    )

    assert probe.runtime_host_attestation_ref == _artifact_ref("e")


def test_completion_artifact_and_trace_close_the_four_reference_dag() -> None:
    manifest_ref = _artifact_ref("a")
    probe_ref = _artifact_ref("b")
    prompt_ref = _artifact_ref("c")
    response_artifact_id = _artifact_ref("d").artifact_id
    trace_artifact_id = _artifact_ref("e").artifact_id
    response = LlmResponsePayload(
        schema_version="automarkov.llm-response.v1",
        content="canonical response",
        tool_calls=(),
        finish_reason="stop",
    )
    response_artifact = LlmCompletionResponseArtifact(
        schema_version="automarkov.llm-completion-response-artifact.v1",
        request_id="llmreq_identity_closure",
        runtime_manifest_ref=manifest_ref,
        runtime_probe_evidence_ref=probe_ref,
        prompt_ref=prompt_ref,
        response=response,
    )
    response_ref = RuntimeArtifactReference(
        artifact_id=response_artifact_id,
        payload_hash=response_artifact.payload_hash,
    )
    trace = LlmCompletionTrace(
        schema_version="automarkov.llm-completion-trace.v2",
        request_id="llmreq_identity_closure",
        model_id=_MODEL_ID,
        model_revision=_MODEL_REVISION,
        vllm_version="0.25.1+cu129",
        tokenizer_hash="sha256:5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42",
        chat_template_hash="sha256:e84f32a23fdda27689f868aa4a1a5621f41133e51a48d7f3efcbea2839574259",
        runtime_manifest_ref=manifest_ref,
        runtime_probe_evidence_ref=probe_ref,
        prompt_ref=prompt_ref,
        response_ref=response_ref,
        endpoint_identity_hash="sha256:" + "f" * 64,
        connection_evidence_hash="sha256:" + "9" * 64,
        sampling=LlmSampling(
            temperature="0",
            top_p="1",
            seed=0,
            max_tokens=64,
        ),
        usage=LlmUsage(
            prompt_tokens=8,
            completion_tokens=2,
            total_tokens=10,
        ),
        latency_ms=1,
        finish_reason="stop",
    )
    result = LlmCompletionResult(
        schema_version="automarkov.llm-completion-result.v3",
        response=response,
        trace=trace,
        response_payload_hash=Sha256Digest(root=response.payload_hash),
        trace_payload_hash=Sha256Digest(root=trace.payload_hash),
        response_artifact_id=response_artifact_id,
        trace_artifact_id=trace_artifact_id,
    )

    assert result.response_artifact_id == trace.response_ref.artifact_id
    assert result.trace_artifact_id != result.response_artifact_id
    assert result.trace.sampling.temperature_value == 0.0
    assert result.trace.sampling.top_p_value == 1.0

    changed_connection = trace.model_dump(mode="python")
    changed_connection["connection_evidence_hash"] = "sha256:" + "8" * 64
    assert (
        LlmCompletionTrace.model_validate(changed_connection, strict=True).payload_hash
        != trace.payload_hash
    )

    mismatched = result.model_dump(mode="python")
    mismatched["response_artifact_id"] = _artifact_ref("f").artifact_id
    with pytest.raises(ValidationError):
        LlmCompletionResult.model_validate(mismatched, strict=True)


def test_tool_call_arguments_remain_canonical_across_nested_revalidation() -> None:
    response = LlmResponsePayload.model_validate(
        {
            "schema_version": "automarkov.llm-response.v1",
            "content": "",
            "tool_calls": [
                {
                    "call_id": "call_reentrant",
                    "name": "inspect_public_evidence",
                    "arguments": {"filters": ["public"], "limit": 1},
                }
            ],
            "finish_reason": "tool_calls",
        },
        strict=True,
    )

    persisted = LlmCompletionResponseArtifact(
        schema_version="automarkov.llm-completion-response-artifact.v1",
        request_id="llmreq_tool_call_revalidation",
        runtime_manifest_ref=_artifact_ref("a"),
        runtime_probe_evidence_ref=_artifact_ref("b"),
        prompt_ref=_artifact_ref("c"),
        response=response,
    )

    assert persisted.response.model_dump(mode="json")["tool_calls"][0]["arguments"] == {
        "filters": ["public"],
        "limit": 1,
    }


@pytest.mark.parametrize(
    ("temperature", "top_p"),
    (
        ("0", "1"),
        ("0.25", "0.5"),
        ("1", "0.0001"),
        ("1.999", "0.999"),
        ("2", "1"),
    ),
)
def test_sampling_uses_canonical_decimal_string_wire_values(
    temperature: str,
    top_p: str,
) -> None:
    sampling = LlmSampling(
        temperature=temperature,
        top_p=top_p,
        seed=0,
        max_tokens=64,
    )

    assert sampling.temperature == temperature
    assert sampling.top_p == top_p
    assert sampling.temperature_value == float(temperature)
    assert sampling.top_p_value == float(top_p)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("temperature", "-0.1"),
        ("temperature", "2.1"),
        ("temperature", "01"),
        ("temperature", "0.0"),
        ("temperature", "1.50"),
        ("temperature", "1e-1"),
        ("top_p", "0"),
        ("top_p", "1.1"),
        ("top_p", ".5"),
        ("top_p", "0.0"),
        ("top_p", "0.50"),
        ("top_p", "1e-1"),
    ),
)
def test_sampling_rejects_out_of_range_or_noncanonical_wire_values(
    field_name: str,
    value: str,
) -> None:
    payload: dict[str, object] = {
        "temperature": "0",
        "top_p": "1",
        "seed": 0,
        "max_tokens": 64,
    }
    payload[field_name] = value

    with pytest.raises(ValidationError):
        LlmSampling.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("temperature", "0.10000000000000001"),
        ("temperature", "1.0000000000000001"),
        ("top_p", "0.99999999999999999"),
    ),
)
def test_sampling_rejects_decimal_strings_that_do_not_round_trip_to_wire_float(
    field_name: str,
    value: str,
) -> None:
    payload: dict[str, object] = {
        "temperature": "0",
        "top_p": "1",
        "seed": 0,
        "max_tokens": 64,
    }
    payload[field_name] = value

    with pytest.raises(ValidationError):
        LlmSampling.model_validate(payload, strict=True)


def test_manifest_binds_every_official_qwen_weight_shard_hash() -> None:
    payload = _manifest_payload()
    manifest = LocalLlmRuntimeManifest.model_validate(payload, strict=True)
    assert dict(manifest.weight_shard_hashes) == _OFFICIAL_WEIGHT_SHARD_HASHES

    for shard_name in _OFFICIAL_WEIGHT_SHARD_HASHES:
        tampered = deepcopy(payload)
        shard_hashes = dict(_OFFICIAL_WEIGHT_SHARD_HASHES)
        shard_hashes[shard_name] = "sha256:" + "0" * 64
        tampered["weight_shard_hashes"] = shard_hashes
        with pytest.raises(ValidationError):
            LocalLlmRuntimeManifest.model_validate(tampered, strict=True)


def test_manifest_accepts_exact_redacted_api_key_startup_evidence() -> None:
    payload = _manifest_payload()

    manifest = LocalLlmRuntimeManifest.model_validate(payload, strict=True)

    assert manifest.startup_args[-2:] == ("--api-key", "[REDACTED]")


def test_manifest_requires_redacted_api_key_startup_evidence() -> None:
    payload = _manifest_payload()
    startup_args = payload["startup_args"]
    assert isinstance(startup_args, list)
    payload["startup_args"] = startup_args[:-2]

    with pytest.raises(ValidationError):
        LocalLlmRuntimeManifest.model_validate(payload, strict=True)


def test_manifest_requires_a_current_route_allowlist_identity() -> None:
    baseline = LocalLlmRuntimeManifest.model_validate(_manifest_payload(), strict=True)
    assert baseline.route_policy_hash == REQUIRED_RUNTIME_ROUTE_POLICY_HASH

    for replacement in (None, "sha256:" + "f" * 64):
        payload = _manifest_payload()
        if replacement is None:
            del payload["route_policy_hash"]
        else:
            payload["route_policy_hash"] = replacement
        with pytest.raises(ValidationError):
            LocalLlmRuntimeManifest.model_validate(payload, strict=True)


def test_manifest_rejects_unregistered_credential_bearing_startup_flags() -> None:
    payload = _manifest_payload()
    payload["startup_args"] = [
        *payload["startup_args"],  # type: ignore[misc]
        "--hf-token",
        "PLAINTEXT_HF_SECRET",
    ]

    with pytest.raises(ValidationError, match="startup argument"):
        LocalLlmRuntimeManifest.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    "api_key_args",
    (
        ("--api-key", "actual-secret"),
        ("--api-key",),
        ("--api-key", "[REDACTED]", "--api-key", "[REDACTED]"),
        ("--api-key=[REDACTED]",),
        ("--api-key=actual-secret",),
    ),
)
def test_manifest_rejects_noncanonical_api_key_startup_evidence(
    api_key_args: tuple[str, ...],
) -> None:
    payload = _manifest_payload()
    startup_args = payload["startup_args"]
    assert isinstance(startup_args, list)
    payload["startup_args"] = [*startup_args[:-2], *api_key_args]

    with pytest.raises(ValidationError):
        LocalLlmRuntimeManifest.model_validate(payload, strict=True)


def test_manifest_rejects_equal_form_and_duplicate_startup_overrides() -> None:
    protected_overrides = (
        f"--revision={_MODEL_REVISION}",
        "--max-model-len=1",
        "--reasoning-parser=qwen3",
        ("--model", "evil/model"),
        ("--tokenizer", "evil/tokenizer"),
        ("--chat-template", "/tmp/evil.jinja"),
        ("--served-model-name", _MODEL_ID),
    )
    for override in protected_overrides:
        payload = _manifest_payload()
        raw_startup_args = payload["startup_args"]
        assert isinstance(raw_startup_args, list)
        startup_args = list(raw_startup_args)
        if isinstance(override, tuple):
            startup_args.extend(override)
        else:
            startup_args.append(override)
        payload["startup_args"] = startup_args
        with pytest.raises(ValidationError):
            LocalLlmRuntimeManifest.model_validate(payload, strict=True)


def test_ready_probe_requires_served_model_and_every_probe_hash() -> None:
    digest = "sha256:" + "a" * 64
    payload: dict[str, object] = {
        "schema_version": "automarkov.llm-probe-result.v3",
        "runtime_id": "runtime_identity_closure",
        "readiness_state": "READY",
        "ready": True,
        "runtime_manifest_payload_hash": digest,
        "health_passed": True,
        "authenticated_models_passed": True,
        "authentication_enforced_passed": True,
        "authenticated_completion_passed": True,
        "served_model_name": _MODEL_ID,
        "health_response_hash": digest,
        "missing_auth_response_hash": digest,
        "invalid_auth_response_hash": digest,
        "models_response_hash": digest,
        "canary_request_hash": digest,
        "canary_response_hash": digest,
        "failure_code": None,
    }
    required_ready_evidence = (
        "served_model_name",
        "health_response_hash",
        "missing_auth_response_hash",
        "invalid_auth_response_hash",
        "models_response_hash",
        "canary_request_hash",
        "canary_response_hash",
    )
    for field_name in required_ready_evidence:
        incomplete = dict(payload)
        incomplete[field_name] = None
        with pytest.raises(ValidationError):
            LlmProbeResult.model_validate(incomplete, strict=True)
