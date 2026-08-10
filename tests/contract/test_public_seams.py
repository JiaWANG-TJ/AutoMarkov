from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from automarkov.adapters import (
    InMemoryArtifactRepository,
    InMemoryCompiler,
    InMemoryEnvironmentBinding,
    ScriptedEvidenceGateway,
    ScriptedExecutionSandbox,
    ScriptedLocalLlmRuntime,
    ScriptedRemoteEnv,
    ScriptedTrainingRunner,
)
from automarkov.domain import ArtifactId, Sha256Digest
from automarkov.errors import CapabilityDeferredError
from automarkov.public import (
    ArtifactPutRequest,
    ArtifactPutResult,
    ArtifactRepository,
    Compiler,
    EnvironmentBinding,
    EnvironmentHandle,
    EvidenceGateway,
    ExecutionSandbox,
    LlmProbeResult,
    LocalLlmRuntime,
    RemoteEnv,
    RuntimeProfileRef,
    TrainingRunner,
)


def _artifact_put_request() -> ArtifactPutRequest:
    return ArtifactPutRequest(
        schema_version="automarkov.artifact-put-request.v1",
        artifact_type="task_request",
        payload_bytes=b'{"request_id":"request_demo"}',
        parent_artifact_ids=(ArtifactId(root="artifact_parent"),),
    )


@pytest.mark.parametrize(
    ("adapter_type", "protocol_type"),
    [
        pytest.param(InMemoryCompiler, Compiler, id="compiler"),
        pytest.param(
            InMemoryArtifactRepository,
            ArtifactRepository,
            id="artifact-repository",
        ),
        pytest.param(
            ScriptedLocalLlmRuntime,
            LocalLlmRuntime,
            id="local-llm-runtime",
        ),
        pytest.param(
            ScriptedEvidenceGateway,
            EvidenceGateway,
            id="evidence-gateway",
        ),
        pytest.param(
            ScriptedExecutionSandbox,
            ExecutionSandbox,
            id="execution-sandbox",
        ),
        pytest.param(
            InMemoryEnvironmentBinding,
            EnvironmentBinding,
            id="environment-binding",
        ),
        pytest.param(ScriptedRemoteEnv, RemoteEnv, id="remote-env"),
        pytest.param(
            ScriptedTrainingRunner,
            TrainingRunner,
            id="training-runner",
        ),
    ],
)
def test_public_protocols_accept_their_structural_adapters(
    adapter_type: type[Any],
    protocol_type: type[Any],
) -> None:
    assert isinstance(adapter_type(), protocol_type)
    assert not isinstance(object(), protocol_type)


@pytest.mark.parametrize(
    ("model_type", "valid_data", "coerced_data", "mutable_field", "replacement"),
    [
        pytest.param(
            ArtifactPutRequest,
            {
                "schema_version": "automarkov.artifact-put-request.v1",
                "artifact_type": "task_request",
                "payload_bytes": b"{}",
                "parent_artifact_ids": (ArtifactId(root="artifact_parent"),),
            },
            {"payload_bytes": "{}"},
            "artifact_type",
            "mutated_type",
            id="request",
        ),
        pytest.param(
            ArtifactPutResult,
            {
                "schema_version": "automarkov.artifact-put-result.v1",
                "artifact_id": ArtifactId(root="artifact_demo"),
                "payload_hash": Sha256Digest(root="sha256:" + "0" * 64),
            },
            {"artifact_id": "artifact_demo"},
            "payload_hash",
            Sha256Digest(root="sha256:" + "1" * 64),
            id="result",
        ),
    ],
)
def test_public_request_and_result_models_are_strict_frozen_and_closed(
    model_type: type[Any],
    valid_data: dict[str, object],
    coerced_data: dict[str, object],
    mutable_field: str,
    replacement: object,
) -> None:
    model = model_type(**valid_data)

    with pytest.raises(ValidationError):
        model_type(**valid_data, unexpected="not-public")

    with pytest.raises(ValidationError):
        model_type(**(valid_data | coerced_data))

    with pytest.raises(ValidationError):
        setattr(model, mutable_field, replacement)


def test_deferred_artifact_put_fails_with_typed_capability_owner() -> None:
    repository = InMemoryArtifactRepository()

    with pytest.raises(CapabilityDeferredError) as raised:
        repository.put(_artifact_put_request())

    assert raised.value.capability == "artifact.put"
    assert raised.value.owner_ticket == "T02"


def test_artifact_put_result_round_trips_through_its_public_json_schema() -> None:
    result = ArtifactPutResult(
        schema_version="automarkov.artifact-put-result.v1",
        artifact_id=ArtifactId(root="artifact_roundtrip"),
        payload_hash=Sha256Digest(root="sha256:" + "0" * 64),
    )

    assert ArtifactPutResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    ("model_type", "payload"),
    [
        (
            LlmProbeResult,
            {
                "schema_version": "automarkov.llm-probe-result.v1",
                "runtime_id": "artifact_wrong_namespace",
                "ready": True,
            },
        ),
        (
            RuntimeProfileRef,
            {
                "schema_version": "automarkov.runtime-profile-ref.v1",
                "profile_id": "request_wrong_namespace",
            },
        ),
        (
            EnvironmentHandle,
            {
                "schema_version": "automarkov.environment-handle.v1",
                "handle_id": "run_wrong_namespace",
            },
        ),
    ],
)
def test_protocol_identity_fields_reject_cross_namespace_ids(
    model_type: type[Any], payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        model_type.model_validate(payload)
