from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import Any, Literal, cast

import pytest
import rfc8785
from pydantic import ValidationError

import automarkov.adapters.repository._core as repository_core
import automarkov.public as public_module
from automarkov.adapters import InMemoryArtifactRepository, SqliteArtifactRepository
from automarkov.domain.canonical import (
    MAX_CANONICAL_DOCUMENT_BYTES,
    MAX_JSON_PAYLOAD_BYTES,
    CanonicalPayloadCodec,
)
from automarkov.domain.errors import AutoMarkovError
from automarkov.domain.models import (
    ArtifactId,
    Sha256Digest,
    StrictFrozenModel,
    TaskRequest,
)
from automarkov.public import (
    ArtifactPutRequest,
    ArtifactPutResult,
    validate_artifact_put_request,
)
from automarkov.repository import ArtifactSchemaRegistry

_CREATED_AT = "2026-08-10T09:00:00Z"
_CREATED_BY = "principal_contract_test"
_PAYLOAD_MEDIA_TYPE = "application/vnd.automarkov.canonical-payload+json"

ArtifactRepositoryAdapter = InMemoryArtifactRepository | SqliteArtifactRepository


class _ParentArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-parent-artifact.v1"]
    value: str


class _ChildArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-child-artifact.v1"]
    value: str


class _SharedStringArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-shared-artifact.v1"]
    value: str


class _SharedBooleanArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-shared-artifact.v1"]
    value: bool


@pytest.fixture(params=("memory", "sqlite"))
def artifact_repository(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> Iterator[ArtifactRepositoryAdapter]:
    adapter_name = cast(str, request.param)
    repository: ArtifactRepositoryAdapter
    if adapter_name == "memory":
        repository = InMemoryArtifactRepository()
    else:
        repository = SqliteArtifactRepository(tmp_path / "artifacts.sqlite")
    try:
        yield repository
    finally:
        if isinstance(repository, SqliteArtifactRepository):
            repository.close()


def _payload(request_id: str = "request_artifact_alpha") -> dict[str, object]:
    return {
        "schema_version": "automarkov.task-request.v1",
        "request_id": request_id,
        "task_text": "Model a finite-horizon inventory replenishment process.",
        "budget": {
            "schema_version": "automarkov.request-budget.v1",
            "wall_time_seconds": 60,
            "llm_token_limit": 0,
            "tool_call_limit": 0,
        },
        "permissions": {
            "schema_version": "automarkov.request-permissions.v1",
            "allow_retrieval": False,
            "allow_clarification": False,
            "allow_code_execution": False,
        },
    }


def _payload_bytes(request_id: str = "request_artifact_alpha") -> bytes:
    return json.dumps(
        _payload(request_id),
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")


def _put_request(
    request_id: str = "request_artifact_alpha",
    *,
    parent_artifact_ids: tuple[ArtifactId, ...] = (),
    artifact_type: str = "task_request",
    payload_bytes: bytes | None = None,
    created_by: str = _CREATED_BY,
) -> dict[str, object]:
    return {
        "schema_version": "automarkov.artifact-put-request.v2",
        "artifact_type": artifact_type,
        "payload_bytes": (
            payload_bytes if payload_bytes is not None else _payload_bytes(request_id)
        ),
        "parent_artifact_ids": [item.root for item in parent_artifact_ids],
        "created_by": created_by,
        "created_at": _CREATED_AT,
        "source_evidence_ids": ["E-alpha", "E-beta"],
    }


def _schema_id() -> str:
    schema_bytes = rfc8785.dumps(TaskRequest.model_json_schema())
    return f"sha256:{hashlib.sha256(schema_bytes).hexdigest()}"


def _canonical_document_bytes(
    request_id: str = "request_artifact_alpha",
) -> bytes:
    return rfc8785.dumps(
        cast(
            Any,
            {
                "schema_id": _schema_id(),
                "exact_float_paths": [],
                "payload": _payload(request_id),
            },
        )
    )


def _assert_error_code(
    operation: Callable[[], object],
    expected_code: str,
) -> AutoMarkovError:
    with pytest.raises(AutoMarkovError) as raised:
        operation()
    assert raised.value.code == expected_code
    return raised.value


def _lineage_registry() -> ArtifactSchemaRegistry:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_parent",
        "automarkov.test-parent-artifact.v1",
        _ParentArtifact,
        direct_parent_artifact_types=(),
    )
    registry.register(
        "test_child",
        "automarkov.test-child-artifact.v1",
        _ChildArtifact,
        direct_parent_artifact_types=("test_parent",),
    )
    registry.register(
        "tampered_parent",
        "automarkov.test-parent-artifact.v1",
        _ParentArtifact,
        direct_parent_artifact_types=(),
    )
    registry.register(
        "test_forged_child",
        "automarkov.test-child-artifact.v1",
        _ChildArtifact,
        direct_parent_artifact_types=("tampered_parent",),
    )
    return registry


def _lineage_request(
    artifact_type: str,
    schema_version: str,
    value: str,
    *,
    parent_artifact_ids: tuple[ArtifactId, ...] = (),
) -> dict[str, object]:
    return _put_request(
        artifact_type=artifact_type,
        payload_bytes=json.dumps(
            {"schema_version": schema_version, "value": value}
        ).encode("utf-8"),
        parent_artifact_ids=parent_artifact_ids,
    )


def test_put_derives_hashes_from_canonical_document_and_full_envelope() -> None:
    request = _put_request()
    repository = InMemoryArtifactRepository()
    result = repository.put(request)
    fetched = repository.get(result.artifact_id)

    expected_document = _canonical_document_bytes()
    expected_payload_hash = Sha256Digest(
        root=f"sha256:{hashlib.sha256(expected_document).hexdigest()}"
    )
    assert result.payload_hash == expected_payload_hash

    envelope_bytes = rfc8785.dumps(cast(Any, fetched.envelope.model_dump(mode="json")))
    assert envelope_bytes == rfc8785.dumps(json.loads(envelope_bytes))
    envelope = json.loads(envelope_bytes)
    assert envelope["artifact_type"] == "task_request"
    assert envelope["schema_version"] == "automarkov.task-request.v1"
    assert envelope["payload_media_type"] == _PAYLOAD_MEDIA_TYPE
    assert envelope["payload_hash"] == expected_payload_hash.root
    assert envelope["parent_artifact_ids"] == []
    assert envelope["created_by"] == _CREATED_BY
    assert envelope["created_at"] == _CREATED_AT
    assert envelope["source_evidence_ids"] == ["E-alpha", "E-beta"]

    assert result.artifact_id == ArtifactId(
        root=f"artifact_{hashlib.sha256(envelope_bytes).hexdigest()}"
    )
    assert fetched.artifact_id == result.artifact_id
    assert fetched.payload_bytes == expected_document

    decoded = json.loads(fetched.payload_bytes)
    assert set(decoded) == {"schema_id", "exact_float_paths", "payload"}
    decoded["payload"]["task_text"] = "mutated outside the repository"
    assert repository.get(result.artifact_id).payload_bytes == expected_document


def test_semantically_identical_json_put_is_idempotent(
    artifact_repository: ArtifactRepositoryAdapter,
) -> None:
    compact_request = _put_request(
        payload_bytes=rfc8785.dumps(cast(Any, _payload())),
    )
    pretty_request = _put_request()

    compact_result = artifact_repository.put(compact_request)
    pretty_result = artifact_repository.put(pretty_request)

    assert compact_result == pretty_result
    assert artifact_repository.get(compact_result.artifact_id).payload_bytes == (
        _canonical_document_bytes()
    )


@pytest.mark.parametrize(
    "forbidden_field",
    ["artifact_id", "schema_id", "exact_float_paths", "payload_hash"],
)
def test_put_request_is_closed_to_repository_derived_identity_fields(
    forbidden_field: str,
) -> None:
    data = {
        "schema_version": "automarkov.artifact-put-request.v2",
        "artifact_type": "task_request",
        "payload_bytes": _payload_bytes(),
        "parent_artifact_ids": [],
        "created_by": _CREATED_BY,
        "created_at": _CREATED_AT,
        "source_evidence_ids": [],
        forbidden_field: "caller-controlled",
    }

    with pytest.raises((ValueError, ValidationError)):
        validate_artifact_put_request(data)


@pytest.mark.parametrize(
    "invalid_update",
    [
        {"created_by": "not-a-principal"},
        {"created_at": "2026-08-10T09:00:00+00:00"},
        {"source_evidence_ids": ["E-beta", "E-alpha"]},
        {"source_evidence_ids": ["E-alpha", "E-alpha"]},
    ],
)
def test_put_request_requires_canonical_creation_metadata(
    invalid_update: dict[str, object],
) -> None:
    valid_data = _put_request()

    with pytest.raises(ValidationError):
        validate_artifact_put_request(valid_data | invalid_update)


@pytest.mark.parametrize(
    ("artifact_type", "payload_bytes"),
    [
        (
            "task_request",
            b'{"schema_version":"automarkov.task-request.v999"}',
        ),
        (
            "test_report",
            _payload_bytes(),
        ),
        (
            "task_request",
            rfc8785.dumps(cast(Any, _payload() | {"unexpected": "not closed"})),
        ),
    ],
    ids=["unknown-schema", "type-schema-mismatch", "payload-extra-field"],
)
def test_put_fails_closed_on_unknown_mismatched_or_open_payload_schema(
    artifact_type: str,
    payload_bytes: bytes,
    artifact_repository: ArtifactRepositoryAdapter,
) -> None:
    with pytest.raises(AutoMarkovError):
        artifact_repository.put(
            _put_request(
                artifact_type=artifact_type,
                payload_bytes=payload_bytes,
            )
        )


def test_parent_ids_must_be_sorted_and_unique() -> None:
    first = ArtifactId(root="artifact_" + "1" * 64)
    second = ArtifactId(root="artifact_" + "2" * 64)

    with pytest.raises(ValidationError):
        validate_artifact_put_request(_put_request(parent_artifact_ids=(second, first)))
    with pytest.raises(ValidationError):
        validate_artifact_put_request(_put_request(parent_artifact_ids=(first, first)))


def test_repository_rejects_a_missing_parent(
    artifact_repository: ArtifactRepositoryAdapter,
) -> None:
    missing = ArtifactId(root="artifact_" + "1" * 64)

    _assert_error_code(
        lambda: artifact_repository.put(_put_request(parent_artifact_ids=(missing,))),
        "missing_artifact_parent",
    )


def test_task_request_artifact_must_be_a_root(
    artifact_repository: ArtifactRepositoryAdapter,
) -> None:
    parent = artifact_repository.put(_put_request("request_parent_contract_root"))

    with pytest.raises(AutoMarkovError):
        artifact_repository.put(
            _put_request(
                "request_parent_contract_child",
                parent_artifact_ids=(parent.artifact_id,),
            )
        )


def test_root_lineage_and_unknown_ids_follow_the_same_contract(
    artifact_repository: ArtifactRepositoryAdapter,
) -> None:
    artifact = artifact_repository.put(_put_request("request_lineage_root"))
    assert artifact_repository.lineage(artifact.artifact_id).artifact_ids == ()

    unknown = ArtifactId(root="artifact_" + "0" * 64)
    _assert_error_code(
        lambda: artifact_repository.get(unknown),
        "unknown_artifact",
    )
    _assert_error_code(
        lambda: artifact_repository.lineage(unknown),
        "unknown_artifact",
    )


def test_identity_collision_is_idempotent_only_for_identical_canonical_bytes(
    artifact_repository: ArtifactRepositoryAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forced_id = ArtifactId(root="artifact_" + "d" * 64)
    original_request = _put_request("request_substitution_original")

    with monkeypatch.context() as patch:
        patch.setattr(
            repository_core,
            "_default_artifact_id",
            lambda _: forced_id,
        )
        original = artifact_repository.put(original_request)

        assert artifact_repository.put(original_request) == original
        _assert_error_code(
            lambda: artifact_repository.put(
                _put_request("request_substitution_attempt")
            ),
            "artifact_identity_conflict",
        )
        assert artifact_repository.get(forced_id).payload_bytes == (
            _canonical_document_bytes("request_substitution_original")
        )


def test_in_memory_concurrent_put_is_atomic_for_idempotent_writers() -> None:
    repository = InMemoryArtifactRepository()
    request = _put_request("request_concurrent_idempotent")
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: repository.put(request), range(32)))

    assert results == [results[0]] * 32
    assert repository.get(results[0].artifact_id).payload_bytes == (
        _canonical_document_bytes("request_concurrent_idempotent")
    )


def test_get_returns_a_fresh_deeply_frozen_document_view(
    artifact_repository: ArtifactRepositoryAdapter,
) -> None:
    artifact = artifact_repository.put(_put_request("request_fresh_read"))

    first = artifact_repository.get(artifact.artifact_id)
    second = artifact_repository.get(artifact.artifact_id)

    assert first == second
    assert first is not second
    assert first.payload_document is not second.payload_document
    assert first.payload_document.payload is not second.payload_document.payload
    with pytest.raises(TypeError):
        cast(dict[str, object], first.payload_document.payload)["task_text"] = "mutated"


def test_sqlite_repository_persists_the_same_contract_across_reopen(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "artifacts.sqlite")
    repository = SqliteArtifactRepository(database_path)
    artifact = repository.put(_put_request("request_sqlite_reopen"))
    expected = repository.get(artifact.artifact_id)
    repository.close()

    reopened = SqliteArtifactRepository(database_path)
    assert reopened.get(artifact.artifact_id) == expected
    assert reopened.lineage(artifact.artifact_id).artifact_ids == ()
    reopened.close()


def test_sqlite_repository_rejects_an_incompatible_preexisting_schema(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "incompatible.sqlite")
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE payload_blobs(payload_hash, payload_bytes);
            CREATE TABLE artifacts(
                artifact_id,
                envelope_bytes,
                payload_hash,
                artifact_type,
                schema_version
            );
            CREATE TABLE artifact_schema_contracts(
                artifact_type,
                schema_version,
                schema_id,
                direct_parent_artifact_types
            );
            CREATE TABLE artifact_parents(artifact_id, position, parent_id);
            """
        )

    _assert_error_code(
        lambda: SqliteArtifactRepository(database_path),
        "artifact_integrity_error",
    )


@pytest.mark.parametrize("signal_type", [KeyboardInterrupt, SystemExit])
def test_sqlite_open_preserves_control_flow_exceptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signal_type: type[BaseException],
) -> None:
    def interrupt_open(_: SqliteArtifactRepository) -> None:
        raise signal_type()

    monkeypatch.setattr(
        SqliteArtifactRepository,
        "_initialize_schema",
        interrupt_open,
    )

    with pytest.raises(signal_type):
        SqliteArtifactRepository(tmp_path / "cancelled-open.sqlite")


def test_sqlite_payload_reference_lookup_has_a_covering_index(tmp_path: Path) -> None:
    repository = SqliteArtifactRepository(tmp_path / "indexed.sqlite")

    index_rows = repository._connection.execute(
        "PRAGMA index_list('artifacts')"
    ).fetchall()
    index_name = next(
        str(row[1])
        for row in index_rows
        if str(row[1]) == "idx_artifacts_payload_hash_artifact_id"
    )
    indexed_columns = repository._connection.execute(
        f"PRAGMA index_info('{index_name}')"
    ).fetchall()

    assert tuple(str(row[2]) for row in indexed_columns) == (
        "payload_hash",
        "artifact_id",
    )
    repository.close()


def test_sqlite_repository_rejects_unexpected_schema_triggers(tmp_path: Path) -> None:
    database_path = str(tmp_path / "trigger.sqlite")
    repository = SqliteArtifactRepository(database_path)
    repository.close()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TRIGGER discard_artifact AFTER INSERT ON artifacts "
            "BEGIN DELETE FROM artifacts WHERE artifact_id = NEW.artifact_id; END"
        )

    _assert_error_code(
        lambda: SqliteArtifactRepository(database_path),
        "artifact_integrity_error",
    )


def test_sqlite_put_rejects_a_trigger_added_after_open(tmp_path: Path) -> None:
    database_path = str(tmp_path / "live-trigger.sqlite")
    repository = SqliteArtifactRepository(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TRIGGER discard_artifact AFTER INSERT ON artifacts "
            "BEGIN DELETE FROM artifacts WHERE artifact_id = NEW.artifact_id; END"
        )

    _assert_error_code(
        lambda: repository.put(_put_request("request_live_trigger")),
        "artifact_integrity_error",
    )
    repository.close()


@pytest.mark.parametrize("operation", ["get", "lineage"])
def test_sqlite_reads_reject_a_trigger_added_after_open(
    tmp_path: Path,
    operation: str,
) -> None:
    database_path = str(tmp_path / f"live-read-trigger-{operation}.sqlite")
    repository = SqliteArtifactRepository(database_path)
    artifact = repository.put(_put_request(f"request_live_read_{operation}"))
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TRIGGER discard_artifact AFTER INSERT ON artifacts "
            "BEGIN DELETE FROM artifacts WHERE artifact_id = NEW.artifact_id; END"
        )

    if operation == "get":
        action = lambda: repository.get(artifact.artifact_id)
    else:
        action = lambda: repository.lineage(artifact.artifact_id)
    _assert_error_code(action, "artifact_integrity_error")
    repository.close()


def test_sqlite_repository_serializes_concurrent_first_open(tmp_path: Path) -> None:
    database_path = str(tmp_path / "first-open.sqlite")
    worker_count = 16
    barrier = Barrier(worker_count)

    def open_and_close(_: int) -> None:
        barrier.wait()
        repository = SqliteArtifactRepository(database_path)
        repository.close()

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        list(executor.map(open_and_close, range(worker_count)))


def test_sqlite_repository_serializes_cross_connection_puts(tmp_path: Path) -> None:
    database_path = str(tmp_path / "concurrent.sqlite")
    left = SqliteArtifactRepository(database_path)
    right = SqliteArtifactRepository(database_path)
    request = _put_request("request_sqlite_concurrent")
    barrier = Barrier(2)

    def put(repository: SqliteArtifactRepository) -> ArtifactPutResult:
        barrier.wait()
        return repository.put(request)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(put, (left, right)))

    assert results[0] == results[1]
    assert left.get(results[0].artifact_id) == right.get(results[0].artifact_id)
    left.close()
    right.close()


def test_sqlite_persists_one_schema_contract_across_runtime_registries(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "schema-contract.sqlite")
    string_registry = ArtifactSchemaRegistry()
    string_registry.register(
        "test_shared",
        "automarkov.test-shared-artifact.v1",
        _SharedStringArtifact,
        direct_parent_artifact_types=(),
    )
    boolean_registry = ArtifactSchemaRegistry()
    boolean_registry.register(
        "test_shared",
        "automarkov.test-shared-artifact.v1",
        _SharedBooleanArtifact,
        direct_parent_artifact_types=(),
    )
    string_repository = SqliteArtifactRepository(database_path, string_registry)
    boolean_repository = SqliteArtifactRepository(database_path, boolean_registry)
    string_repository.put(
        _put_request(
            artifact_type="test_shared",
            payload_bytes=json.dumps(
                {
                    "schema_version": "automarkov.test-shared-artifact.v1",
                    "value": "accepted",
                }
            ).encode("utf-8"),
        )
    )

    _assert_error_code(
        lambda: boolean_repository.put(
            _put_request(
                artifact_type="test_shared",
                payload_bytes=json.dumps(
                    {
                        "schema_version": "automarkov.test-shared-artifact.v1",
                        "value": True,
                    }
                ).encode("utf-8"),
            )
        ),
        "artifact_schema_conflict",
    )
    string_repository.close()
    boolean_repository.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("schema_id", "sha256:" + "0" * 64),
        ("direct_parent_artifact_types", b'["forged_parent"]'),
    ],
)
def test_sqlite_detects_persisted_schema_contract_tampering(
    tmp_path: Path,
    column: str,
    value: str | bytes,
) -> None:
    database_path = str(tmp_path / f"schema-contract-{column}.sqlite")
    repository = SqliteArtifactRepository(database_path)
    artifact = repository.put(_put_request(f"request_schema_contract_{column}"))
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            f"UPDATE artifact_schema_contracts SET {column} = ?",
            (value,),
        )
        connection.commit()

    _assert_error_code(
        lambda: repository.get(artifact.artifact_id),
        "artifact_integrity_error",
    )
    repository.close()


def test_sqlite_bounds_schema_contract_blob_before_materializing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = str(tmp_path / "oversized-schema-contract.sqlite")
    repository = SqliteArtifactRepository(database_path)
    request = _put_request("request_oversized_schema_contract")
    artifact = repository.put(request)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE artifact_schema_contracts "
            "SET direct_parent_artifact_types = zeroblob(?)",
            (MAX_JSON_PAYLOAD_BYTES + 1,),
        )
        connection.commit()

    bounded_reads: list[tuple[str, str, int]] = []
    original_read = repository._read_bounded_blob

    def observe_bounded_read(
        table: str,
        column: str,
        row_id: object,
        maximum_bytes: int,
        integrity_subject: str,
    ) -> bytes:
        bounded_reads.append((table, column, maximum_bytes))
        return original_read(
            table,
            column,
            row_id,
            maximum_bytes,
            integrity_subject,
        )

    monkeypatch.setattr(repository, "_read_bounded_blob", observe_bounded_read)
    for operation in (
        lambda: repository.get(artifact.artifact_id),
        lambda: repository.put(request),
    ):
        bounded_reads.clear()
        _assert_error_code(operation, "artifact_integrity_error")
        assert bounded_reads == [
            (
                "artifact_schema_contracts",
                "direct_parent_artifact_types",
                MAX_JSON_PAYLOAD_BYTES,
            )
        ]
    repository.close()


def test_sqlite_does_not_recreate_a_missing_schema_contract(tmp_path: Path) -> None:
    database_path = str(tmp_path / "missing-schema-contract.sqlite")
    repository = SqliteArtifactRepository(database_path)
    request = _put_request("request_missing_schema_contract")
    artifact = repository.put(request)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM artifact_schema_contracts")
        connection.commit()

    _assert_error_code(lambda: repository.put(request), "artifact_integrity_error")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM artifact_schema_contracts"
        ).fetchone() == (0,)
    _assert_error_code(
        lambda: repository.get(artifact.artifact_id),
        "artifact_integrity_error",
    )
    repository.close()


def test_sqlite_repository_detects_at_rest_payload_substitution(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "tampered.sqlite")
    repository = SqliteArtifactRepository(database_path)
    artifact = repository.put(_put_request("request_sqlite_tamper"))
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE payload_blobs SET payload_bytes = ?",
            (b"{}",),
        )
        connection.commit()

    _assert_error_code(
        lambda: repository.get(artifact.artifact_id),
        "artifact_integrity_error",
    )
    repository.close()


def test_sqlite_rejects_identity_substitution_before_payload_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = str(tmp_path / "identity-substitution.sqlite")
    repository = SqliteArtifactRepository(database_path)
    original = repository.put(_put_request("request_identity_original"))
    substitute = repository.put(_put_request("request_identity_substitute"))
    with sqlite3.connect(database_path) as connection:
        envelope_bytes, payload_hash, artifact_type, schema_version = (
            connection.execute(
                "SELECT envelope_bytes, payload_hash, artifact_type, schema_version "
                "FROM artifacts WHERE artifact_id = ?",
                (substitute.artifact_id.root,),
            ).fetchone()
        )
        connection.execute(
            "UPDATE artifacts SET envelope_bytes = ?, payload_hash = ?, "
            "artifact_type = ?, schema_version = ? WHERE artifact_id = ?",
            (
                envelope_bytes,
                payload_hash,
                artifact_type,
                schema_version,
                original.artifact_id.root,
            ),
        )
        connection.commit()

    def fail_if_decoded(*_: object) -> object:
        raise AssertionError("payload decoded before artifact identity validation")

    monkeypatch.setattr(CanonicalPayloadCodec, "decode", fail_if_decoded)
    _assert_error_code(
        lambda: repository.get(original.artifact_id),
        "artifact_integrity_error",
    )
    repository.close()


@pytest.mark.parametrize("operation", ["get", "lineage", "idempotent-put"])
def test_sqlite_missing_payload_blob_is_integrity_failure_without_repair(
    tmp_path: Path,
    operation: str,
) -> None:
    database_path = str(tmp_path / f"missing-blob-{operation}.sqlite")
    repository = SqliteArtifactRepository(database_path)
    request = _put_request(f"request_missing_blob_{operation.replace('-', '_')}")
    artifact = repository.put(request)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "DELETE FROM payload_blobs WHERE payload_hash = ?",
            (artifact.payload_hash.root,),
        )
        connection.commit()
        assert connection.execute(
            "SELECT COUNT(*) FROM artifacts WHERE artifact_id = ?",
            (artifact.artifact_id.root,),
        ).fetchone() == (1,)

    if operation == "get":
        action = lambda: repository.get(artifact.artifact_id)
    elif operation == "lineage":
        action = lambda: repository.lineage(artifact.artifact_id)
    else:
        action = lambda: repository.put(request)

    _assert_error_code(action, "artifact_integrity_error")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM payload_blobs WHERE payload_hash = ?",
            (artifact.payload_hash.root,),
        ).fetchone() == (0,)
    repository.close()


def test_sqlite_new_artifact_cannot_recreate_a_missing_shared_blob(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "missing-shared-blob.sqlite")
    repository = SqliteArtifactRepository(database_path)
    original_request = _put_request("request_missing_shared_blob")
    original = repository.put(original_request)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "DELETE FROM payload_blobs WHERE payload_hash = ?",
            (original.payload_hash.root,),
        )
        connection.commit()

    revised_metadata = original_request | {"created_by": "principal_second_writer"}
    _assert_error_code(
        lambda: repository.put(revised_metadata),
        "artifact_integrity_error",
    )
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM payload_blobs WHERE payload_hash = ?",
            (original.payload_hash.root,),
        ).fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone() == (1,)
    repository.close()


@pytest.mark.parametrize("column", ["artifact_type", "schema_version"])
def test_sqlite_idempotent_put_rejects_tampered_record_metadata(
    tmp_path: Path,
    column: str,
) -> None:
    database_path = str(tmp_path / f"tampered-{column}.sqlite")
    repository = SqliteArtifactRepository(database_path)
    request = _put_request(f"request_tampered_{column}")
    artifact = repository.put(request)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            f"UPDATE artifacts SET {column} = ? WHERE artifact_id = ?",
            ("tampered", artifact.artifact_id.root),
        )
        connection.commit()

    _assert_error_code(lambda: repository.put(request), "artifact_integrity_error")
    _assert_error_code(
        lambda: repository.get(artifact.artifact_id), "artifact_integrity_error"
    )
    repository.close()


def test_sqlite_idempotent_put_maps_malformed_payload_hash_to_integrity_error(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "tampered-payload-hash.sqlite")
    repository = SqliteArtifactRepository(database_path)
    request = _put_request("request_tampered_payload_hash")
    artifact = repository.put(request)
    with sqlite3.connect(database_path) as connection:
        payload_bytes = connection.execute(
            "SELECT payload_bytes FROM payload_blobs WHERE payload_hash = ?",
            (artifact.payload_hash.root,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO payload_blobs(payload_hash, payload_bytes) VALUES (?, ?)",
            ("malformed", payload_bytes),
        )
        connection.execute(
            "UPDATE artifacts SET payload_hash = ? WHERE artifact_id = ?",
            ("malformed", artifact.artifact_id.root),
        )
        connection.commit()

    _assert_error_code(lambda: repository.put(request), "artifact_integrity_error")
    repository.close()


@pytest.mark.parametrize("blob_kind", ["envelope", "payload"])
def test_sqlite_rejects_oversized_record_before_buffering_the_blob(
    tmp_path: Path,
    blob_kind: str,
) -> None:
    database_path = str(tmp_path / f"oversized-{blob_kind}.sqlite")
    repository = SqliteArtifactRepository(database_path)
    artifact = repository.put(_put_request(f"request_oversized_{blob_kind}"))
    with sqlite3.connect(database_path) as connection:
        if blob_kind == "envelope":
            connection.execute(
                "UPDATE artifacts SET envelope_bytes = zeroblob(?) "
                "WHERE artifact_id = ?",
                (MAX_JSON_PAYLOAD_BYTES + 1, artifact.artifact_id.root),
            )
        else:
            connection.execute(
                "UPDATE payload_blobs SET payload_bytes = zeroblob(?) "
                "WHERE payload_hash = ?",
                (MAX_CANONICAL_DOCUMENT_BYTES + 1, artifact.payload_hash.root),
            )
        connection.commit()

    previous_limit = repository._connection.setlimit(
        sqlite3.SQLITE_LIMIT_LENGTH,
        64 * 1024,
    )
    try:
        _assert_error_code(
            lambda: repository.get(artifact.artifact_id),
            "artifact_integrity_error",
        )
    finally:
        repository._connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, previous_limit)
        repository.close()


def test_sqlite_put_rolls_back_when_the_writer_is_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SqliteArtifactRepository(tmp_path / "cancelled-write.sqlite")
    request = _put_request("request_cancelled_write")
    original = repository._ensure_schema_contract

    def cancel_write(_: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(repository, "_ensure_schema_contract", cancel_write)
    with pytest.raises(KeyboardInterrupt):
        repository.put(request)

    assert not repository._connection.in_transaction
    monkeypatch.setattr(repository, "_ensure_schema_contract", original)
    repository.put(request)
    repository.close()


@pytest.mark.parametrize(
    "tampered_field",
    ["artifact_type", "envelope", "payload"],
)
def test_sqlite_rejects_child_use_after_any_parent_record_tampering(
    tmp_path: Path,
    tampered_field: str,
) -> None:
    database_path = str(tmp_path / f"parent-{tampered_field}.sqlite")
    repository = SqliteArtifactRepository(database_path, _lineage_registry())
    parent = repository.put(
        _lineage_request(
            "test_parent",
            "automarkov.test-parent-artifact.v1",
            "parent",
        )
    )
    existing_child = repository.put(
        _lineage_request(
            "test_child",
            "automarkov.test-child-artifact.v1",
            "existing-child",
            parent_artifact_ids=(parent.artifact_id,),
        )
    )

    with sqlite3.connect(database_path) as connection:
        if tampered_field == "artifact_type":
            connection.execute(
                "UPDATE artifacts SET artifact_type = ? WHERE artifact_id = ?",
                ("tampered_parent", parent.artifact_id.root),
            )
            new_child_type = "test_forged_child"
        elif tampered_field == "envelope":
            connection.execute(
                "UPDATE artifacts SET envelope_bytes = ? WHERE artifact_id = ?",
                (b"{}", parent.artifact_id.root),
            )
            new_child_type = "test_child"
        else:
            connection.execute(
                "UPDATE payload_blobs SET payload_bytes = ? WHERE payload_hash = ?",
                (b"{}", parent.payload_hash.root),
            )
            new_child_type = "test_child"
        connection.commit()

    _assert_error_code(
        lambda: repository.get(existing_child.artifact_id),
        "artifact_integrity_error",
    )
    _assert_error_code(
        lambda: repository.put(
            _lineage_request(
                new_child_type,
                "automarkov.test-child-artifact.v1",
                "new-child",
                parent_artifact_ids=(parent.artifact_id,),
            )
        ),
        "artifact_integrity_error",
    )
    repository.close()


def test_in_memory_rejects_child_use_after_parent_payload_tampering() -> None:
    repository = InMemoryArtifactRepository(_lineage_registry())
    parent = repository.put(
        _lineage_request(
            "test_parent",
            "automarkov.test-parent-artifact.v1",
            "parent",
        )
    )
    child = repository.put(
        _lineage_request(
            "test_child",
            "automarkov.test-child-artifact.v1",
            "child",
            parent_artifact_ids=(parent.artifact_id,),
        )
    )
    stored = repository._artifacts[parent.artifact_id.root]
    repository._artifacts[parent.artifact_id.root] = replace(
        stored,
        payload_bytes=b"{}",
    )

    _assert_error_code(
        lambda: repository.get(child.artifact_id),
        "artifact_integrity_error",
    )
    _assert_error_code(
        lambda: repository.put(
            _lineage_request(
                "test_child",
                "automarkov.test-child-artifact.v1",
                "new-child",
                parent_artifact_ids=(parent.artifact_id,),
            )
        ),
        "artifact_integrity_error",
    )


def test_model_construct_forged_put_request_is_rejected_by_both_adapters(
    artifact_repository: ArtifactRepositoryAdapter,
) -> None:
    valid = _put_request("request_forged_put_request")
    validated = validate_artifact_put_request(valid)
    forged_data = validated.model_dump(mode="python")
    forged_data["artifact_id"] = "caller-controlled"
    forged = ArtifactPutRequest.model_construct(**forged_data)

    with pytest.raises((AutoMarkovError, ValidationError, ValueError)):
        artifact_repository.put(cast(dict[str, object], forged))

    artifact_repository.put(valid)


def test_repository_normalizes_huge_exponent_to_a_canonical_payload_error(
    artifact_repository: ArtifactRepositoryAdapter,
) -> None:
    error = _assert_error_code(
        lambda: artifact_repository.put(
            _put_request(payload_bytes=b'{"value":1e999999999999999999999999999}')
        ),
        "canonical_payload_rejected",
    )

    assert error.__cause__ is not None
    assert isinstance(error.__cause__, ValueError)


def test_repository_preflights_command_resources_before_model_construction(
    artifact_repository: ArtifactRepositoryAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oversized = _put_request(payload_bytes=b" " * (MAX_JSON_PAYLOAD_BYTES + 1))
    oversized["parent_artifact_ids"] = ["invalid-parent-id"]
    with pytest.raises(ValueError, match="payload_bytes exceeds byte limit"):
        artifact_repository.put(oversized)

    monkeypatch.setattr(public_module, "MAX_JSON_NODES", 16)
    excessive_metadata = _put_request()
    excessive_metadata["parent_artifact_ids"] = ["invalid-a", "invalid-b"]
    with pytest.raises(ValueError, match="metadata exceeds resource limits"):
        artifact_repository.put(excessive_metadata)
