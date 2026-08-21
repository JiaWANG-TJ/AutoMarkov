from __future__ import annotations

from typing import Any, get_type_hints

import pytest
from pydantic import ValidationError

import automarkov.adapters as adapter_module
import automarkov.domain as domain_module
from automarkov.adapters import (
    AttachedLocalLlmRuntime,
    InMemoryArtifactRepository,
    InMemoryCompiler,
    InMemoryEnvironmentBinding,
    PrivilegedUnixRuntimeConnectionProvider,
    ScriptedEvidenceGateway,
    ScriptedExecutionSandbox,
    ScriptedLocalLlmRuntime,
    ScriptedRemoteEnv,
    ScriptedTrainingRunner,
)
from automarkov.canonical import MAX_CANONICAL_DOCUMENT_BYTES
from automarkov.domain import ArtifactId, RunId, Sha256Digest, VerifiedEventHead
from automarkov.errors import ArtifactSchemaError
from automarkov.lifecycle import ArtifactReference, LifecycleCommitResult, RunProjection
from automarkov.public import (
    ArtifactBytesResult,
    ArtifactPutRequest,
    ArtifactPutResult,
    ArtifactRepository,
    Compiler,
    EnvironmentBinding,
    EnvironmentHandle,
    EvidenceGateway,
    ExecutionSandbox,
    FixedCommitJobRequest,
    LlmProbeResult,
    LocalLlmRuntime,
    RemoteEnv,
    RuntimeProfileRef,
    TrainingRunner,
)


def _artifact_put_request() -> dict[str, object]:
    return {
        "schema_version": "automarkov.artifact-put-request.v2",
        "artifact_type": "task_request",
        "payload_bytes": b'{"request_id":"request_demo"}',
        "parent_artifact_ids": ["artifact_" + "a" * 64],
        "created_by": "principal_public_seam",
        "created_at": "2026-08-10T09:00:00Z",
        "source_evidence_ids": [],
    }


def test_adapter_exports_preserve_t01_and_add_the_sqlite_repository() -> None:
    expected = {
        "AttachedLocalLlmRuntime",
        "FixedCommitExecutionSandbox",
        "InMemoryArtifactRepository",
        "InMemoryCompiler",
        "InMemoryEnvironmentBinding",
        "PrivilegedUnixRuntimeConnectionProvider",
        "ScriptedEvidenceGateway",
        "ScriptedExecutionSandbox",
        "ScriptedLocalLlmRuntime",
        "ScriptedRemoteEnv",
        "ScriptedTrainingRunner",
        "SqliteArtifactRepository",
    }

    assert set(adapter_module.__all__) == expected
    assert {
        name for name in adapter_module.__all__ if hasattr(adapter_module, name)
    } == (expected)
    assert adapter_module.AttachedLocalLlmRuntime is AttachedLocalLlmRuntime
    assert (
        adapter_module.PrivilegedUnixRuntimeConnectionProvider
        is PrivilegedUnixRuntimeConnectionProvider
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


def test_compiler_protocol_uses_lifecycle_commands_and_verified_heads() -> None:
    assert Compiler.dispatch.__annotations__ == {
        "request": "LifecycleCommandInput",
        "return": "LifecycleCommitResult",
    }
    assert Compiler.resume.__annotations__ == {
        "run_id": "RunId",
        "head": "VerifiedEventHead",
        "return": "RunProjection",
    }
    assert Compiler.package.__annotations__ == {
        "run_id": "RunId",
        "head": "VerifiedEventHead",
        "return": "PackageResult",
    }
    assert not hasattr(domain_module, "CompilerDispatchRequest")
    assert not hasattr(domain_module, "RunView")


def test_public_lifecycle_protocol_types_are_runtime_resolvable() -> None:
    compiler_dispatch = get_type_hints(Compiler.dispatch, include_extras=True)
    compiler_resume = get_type_hints(Compiler.resume, include_extras=True)
    repository_commit = get_type_hints(ArtifactRepository.commit, include_extras=True)
    repository_project = get_type_hints(ArtifactRepository.project, include_extras=True)

    assert compiler_dispatch["return"] == LifecycleCommitResult
    assert repository_commit["return"] == LifecycleCommitResult
    assert compiler_resume["return"] is RunProjection
    assert repository_project["return"] is RunProjection


@pytest.mark.parametrize(
    ("model_type", "valid_data", "coerced_data", "mutable_field", "replacement"),
    [
        pytest.param(
            ArtifactPutRequest,
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": "task_request",
                "payload_bytes": b"{}",
                "parent_artifact_ids": (ArtifactId(root="artifact_" + "a" * 64),),
                "created_by": "principal_public_seam",
                "created_at": "2026-08-10T09:00:00Z",
                "source_evidence_ids": (),
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
                "artifact_id": ArtifactId(root="artifact_" + "b" * 64),
                "payload_hash": Sha256Digest(root="sha256:" + "0" * 64),
            },
            {"artifact_id": "artifact_demo"},
            "payload_hash",
            Sha256Digest(root="sha256:" + "1" * 64),
            id="result",
        ),
        pytest.param(
            VerifiedEventHead,
            {
                "run_id": RunId(root="run_public_verified_head"),
                "sequence_no": 0,
                "event_hash": Sha256Digest(root="sha256:" + "0" * 64),
            },
            {"sequence_no": "0"},
            "event_hash",
            Sha256Digest(root="sha256:" + "1" * 64),
            id="verified-event-head",
        ),
    ],
)
def test_public_contract_models_are_strict_frozen_and_closed(
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


def test_fixed_commit_job_request_binds_verified_head_and_exact_manifest_reference() -> (
    None
):
    request = FixedCommitJobRequest(
        schema_version="automarkov.fixed-commit-job-request.v2",
        specified_event_head=VerifiedEventHead(
            run_id=RunId(root="run_public_fixed_commit"),
            sequence_no=1,
            event_hash=Sha256Digest(root="sha256:" + "c" * 64),
        ),
        job_manifest=ArtifactReference(
            artifact_id="artifact_" + "d" * 64,
            payload_hash="sha256:" + "d" * 64,
        ),
    )

    assert request.specified_event_head is not None
    assert request.job_manifest is not None
    assert request.specified_event_head.sequence_no == 1
    assert request.job_manifest.payload_hash == "sha256:" + "d" * 64
    assert FixedCommitJobRequest(
        schema_version="automarkov.fixed-commit-job-request.v1",
        job_manifest_artifact_id=ArtifactId(root="artifact_" + "e" * 64),
    ).job_manifest_artifact_id == ArtifactId(root="artifact_" + "e" * 64)

    with pytest.raises(ValidationError):
        FixedCommitJobRequest.model_validate(
            {
                "schema_version": "automarkov.fixed-commit-job-request.v2",
                "job_manifest_artifact_id": "artifact_" + "d" * 64,
            }
        )


def test_artifact_put_fails_closed_for_an_unregistered_payload_schema() -> None:
    repository = InMemoryArtifactRepository()

    with pytest.raises(ArtifactSchemaError) as raised:
        repository.put(_artifact_put_request())

    assert raised.value.artifact_type == "task_request"


def test_artifact_put_result_round_trips_through_its_public_json_schema() -> None:
    result = ArtifactPutResult(
        schema_version="automarkov.artifact-put-result.v1",
        artifact_id=ArtifactId(root="artifact_" + "c" * 64),
        payload_hash=Sha256Digest(root="sha256:" + "0" * 64),
    )

    assert ArtifactPutResult.model_validate_json(result.model_dump_json()) == result


def test_artifact_bytes_result_v2_declares_the_canonical_document_cap() -> None:
    schema = ArtifactBytesResult.model_json_schema()

    assert schema["properties"]["schema_version"]["const"] == (
        "automarkov.artifact-bytes-result.v2"
    )
    assert schema["properties"]["payload_bytes"]["maxLength"] == (
        MAX_CANONICAL_DOCUMENT_BYTES
    )


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
                "profile_id": "profiles/rllib-core",
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
