from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import cache
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Literal, cast

from pydantic import BaseModel, RootModel, TypeAdapter

from automarkov.canonical import (
    MAX_CANONICAL_DOCUMENT_BYTES,
    MAX_JSON_PAYLOAD_BYTES,
    CanonicalPayloadCodec,
    canonical_json_bytes,
    parse_canonical_document,
    parse_json_payload,
    require_registered_model_number_contract,
)
from automarkov.domain import ArtifactId, RunId, RunView, Sha256Digest, TaskRequest
from automarkov.errors import (
    ArtifactCycleError,
    ArtifactIdentityConflictError,
    ArtifactIntegrityError,
    ArtifactParentContractError,
    ArtifactSchemaConflictError,
    ArtifactSchemaError,
    CanonicalPayloadError,
    CapabilityDeferredError,
    MissingArtifactParentError,
    UnknownArtifactError,
)
from automarkov.public import (
    ArtifactAppendRequest,
    ArtifactBytesResult,
    ArtifactEnvelope,
    ArtifactLineageResult,
    ArtifactPutInput,
    ArtifactPutResult,
    ArtifactType,
    PayloadSchemaVersion,
    validate_artifact_put_request,
)

PAYLOAD_MEDIA_TYPE = "application/vnd.automarkov.canonical-payload+json"
_ARTIFACT_TYPE_ADAPTER = TypeAdapter(ArtifactType)

_SQLITE_SCHEMA_VERSION = 1
_SQLITE_SCHEMA_STATEMENTS = (
    """CREATE TABLE payload_blobs (
    payload_hash TEXT PRIMARY KEY,
    payload_bytes BLOB NOT NULL
) STRICT""",
    """CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    envelope_bytes BLOB NOT NULL,
    payload_hash TEXT NOT NULL REFERENCES payload_blobs(payload_hash),
    artifact_type TEXT NOT NULL,
    schema_version TEXT NOT NULL
) STRICT""",
    """CREATE INDEX idx_artifacts_payload_hash_artifact_id
    ON artifacts(payload_hash, artifact_id)""",
    """CREATE TABLE artifact_schema_contracts (
    artifact_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    schema_id TEXT NOT NULL,
    direct_parent_artifact_types BLOB NOT NULL,
    PRIMARY KEY (artifact_type, schema_version)
) STRICT""",
    """CREATE TABLE artifact_parents (
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    position INTEGER NOT NULL CHECK (position >= 0),
    parent_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    PRIMARY KEY (artifact_id, position),
    UNIQUE (artifact_id, parent_id)
) STRICT""",
)


def _sqlite_schema_rows(
    connection: sqlite3.Connection,
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        ).fetchall()
    )


@cache
def _expected_sqlite_schema_rows() -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(":memory:") as connection:
        for statement in _SQLITE_SCHEMA_STATEMENTS:
            connection.execute(statement)
        return _sqlite_schema_rows(connection)


_PAYLOAD_SCHEMA_VERSION_ADAPTER = TypeAdapter(PayloadSchemaVersion)


@dataclass(frozen=True, slots=True)
class _RegisteredSchema:
    artifact_type: str
    schema_version: str
    codec: CanonicalPayloadCodec[BaseModel]
    direct_parent_artifact_types: tuple[str, ...]


def _require_exact_float_markers(schema: dict[str, object]) -> None:
    definitions = schema.get("$defs")
    definition_map = (
        cast(dict[str, object], definitions) if type(definitions) is dict else {}
    )
    pending: list[tuple[object, bool]] = [(schema, False)]
    visited_refs: set[tuple[str, bool]] = set()
    while pending:
        current, normalized_context = pending.pop()
        if type(current) is list:
            pending.extend((item, normalized_context) for item in current)
            continue
        if type(current) is not dict:
            continue
        node = cast(dict[str, object], current)
        number_kind = node.get("x-automarkov-number-kind")
        normalized = normalized_context or number_kind == "canonical-json-normalized"
        node_type = node.get("type")
        contains_number = node_type == "number" or (
            type(node_type) is list and "number" in node_type
        )
        if contains_number and not (normalized or number_kind == "exact-float"):
            raise ValueError("artifact float fields must use StrictCanonicalFloat")
        reference = node.get("$ref")
        if type(reference) is str and reference.startswith("#/$defs/"):
            visit_key = (reference, normalized)
            if visit_key not in visited_refs:
                visited_refs.add(visit_key)
                name = reference.removeprefix("#/$defs/")
                target = definition_map.get(name)
                if target is None:
                    raise ValueError("artifact schema contains an unresolved reference")
                pending.append((target, normalized))
        for key, value in node.items():
            if key not in {"$defs", "$ref"}:
                pending.append((value, normalized))


def _require_recursive_model_contract(model_type: type[BaseModel]) -> None:
    """递归拒绝开放、可变、非严格或带 alias 的 nested models。"""

    pending: list[object] = [TypeAdapter(model_type).core_schema]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if type(current) is list:
            pending.extend(current)
            continue
        if type(current) is not dict or id(current) in visited:
            continue
        visited.add(id(current))
        node = cast(dict[str, object], current)
        if node.get("type") == "model":
            candidate = node.get("cls")
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                config = candidate.model_config
                is_root_model = issubclass(candidate, RootModel)
                if (
                    config.get("strict") is not True
                    or config.get("frozen") is not True
                    or (not is_root_model and config.get("extra") != "forbid")
                ):
                    raise ValueError(
                        "artifact schemas must be recursively strict, frozen, and closed"
                    )
                if any(
                    field.alias is not None
                    or field.validation_alias is not None
                    or field.serialization_alias is not None
                    for field in candidate.model_fields.values()
                ):
                    raise ValueError("artifact schema field aliases are not supported")
                if any(
                    field.default_factory is not None
                    for field in candidate.model_fields.values()
                ):
                    raise ValueError(
                        "artifact schema fields cannot use default_factory"
                    )
                if any(
                    field.exclude is True or field.exclude_if is not None
                    for field in candidate.model_fields.values()
                ):
                    raise ValueError(
                        "artifact schema fields cannot alter serialization inclusion"
                    )
                decorators = candidate.__pydantic_decorators__
                if (
                    decorators.field_serializers
                    or decorators.model_serializers
                    or decorators.computed_fields
                ):
                    raise ValueError(
                        "custom artifact serializers and computed fields are not supported"
                    )
                if (
                    candidate.__get_pydantic_json_schema__.__func__
                    is not BaseModel.__get_pydantic_json_schema__.__func__
                ):
                    raise ValueError(
                        "artifact schema contains an unapproved JSON schema override"
                    )
        pending.extend(
            item
            for key, item in node.items()
            if key not in {"cls", "metadata", "serialization"}
        )


class ArtifactSchemaRegistry:
    """工件获得 identity 前使用的冻结 schema 注册表。"""

    def __init__(self) -> None:
        self._schemas: dict[tuple[str, str], _RegisteredSchema] = {}
        self._lock = RLock()
        self._frozen = False

    def register(
        self,
        artifact_type: str,
        schema_version: str,
        model_type: type[BaseModel],
        *,
        direct_parent_artifact_types: tuple[str, ...],
    ) -> str:
        artifact_type = _ARTIFACT_TYPE_ADAPTER.validate_python(
            artifact_type,
            strict=True,
        )
        schema_version = _PAYLOAD_SCHEMA_VERSION_ADAPTER.validate_python(
            schema_version,
            strict=True,
        )
        _require_recursive_model_contract(model_type)
        require_registered_model_number_contract(model_type)
        codec = CanonicalPayloadCodec(model_type)
        _require_exact_float_markers(codec.schema)
        if (
            codec.schema.get("type") != "object"
            or codec.schema.get("additionalProperties") is not False
        ):
            raise ValueError("artifact schemas must describe a closed JSON object")
        properties = codec.schema.get("properties")
        declared_version = (
            properties.get("schema_version", {}).get("const")
            if type(properties) is dict
            and type(properties.get("schema_version")) is dict
            else None
        )
        if declared_version != schema_version:
            raise ValueError("registered model schema_version literal does not match")
        key = (artifact_type, schema_version)
        if type(direct_parent_artifact_types) is not tuple:
            raise ValueError(
                "direct parent artifact types must be nonempty strings in canonical order"
            )
        validated_parent_types = tuple(
            _ARTIFACT_TYPE_ADAPTER.validate_python(item, strict=True)
            for item in direct_parent_artifact_types
        )
        expected_parent_types = tuple(
            sorted(validated_parent_types, key=lambda item: item.encode("utf-8"))
        )
        if validated_parent_types != expected_parent_types:
            raise ValueError(
                "direct parent artifact types must be nonempty strings in canonical order"
            )
        registered = _RegisteredSchema(
            artifact_type,
            schema_version,
            codec,
            expected_parent_types,
        )
        with self._lock:
            if self._frozen:
                raise RuntimeError("artifact schema registry is frozen")
            existing = self._schemas.get(key)
            if existing is not None:
                if (
                    existing.codec.schema_id != codec.schema_id
                    or existing.direct_parent_artifact_types
                    != registered.direct_parent_artifact_types
                ):
                    raise ValueError(
                        "artifact schema key is already registered differently"
                    )
                return existing.codec.schema_id
            self._schemas[key] = registered
        return codec.schema_id

    def freeze(self) -> None:
        with self._lock:
            registered_types = {
                registered.artifact_type for registered in self._schemas.values()
            }
            missing_parent_types = sorted(
                {
                    parent_type
                    for registered in self._schemas.values()
                    for parent_type in registered.direct_parent_artifact_types
                    if parent_type not in registered_types
                },
                key=lambda item: item.encode("utf-8"),
            )
            if missing_parent_types:
                raise ValueError(
                    "artifact schema registry references unregistered parent artifact "
                    f"types: {', '.join(missing_parent_types)}"
                )
            self._frozen = True

    def resolve(self, artifact_type: str, payload: object) -> _RegisteredSchema:
        if type(payload) is not dict:
            raise ArtifactSchemaError(artifact_type, None)
        version = cast(dict[str, object], payload).get("schema_version")
        if type(version) is not str:
            raise ArtifactSchemaError(artifact_type, None)
        with self._lock:
            registered = self._schemas.get((artifact_type, version))
        if registered is None:
            raise ArtifactSchemaError(artifact_type, version)
        return registered


def _default_schema_registry() -> ArtifactSchemaRegistry:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "task_request",
        "automarkov.task-request.v1",
        TaskRequest,
        direct_parent_artifact_types=(),
    )
    registry.freeze()
    return registry


@dataclass(frozen=True, slots=True)
class _StoredArtifact:
    artifact_id: ArtifactId
    envelope_bytes: bytes
    payload_bytes: bytes
    payload_hash: Sha256Digest
    parent_artifact_ids: tuple[ArtifactId, ...]
    artifact_type: str
    schema_version: str


@dataclass(frozen=True, slots=True)
class _PreparedArtifact:
    envelope_bytes: bytes
    payload_bytes: bytes
    payload_hash: Sha256Digest
    artifact_type: str
    schema_version: str
    schema_id: str
    parent_artifact_ids: tuple[ArtifactId, ...]
    direct_parent_artifact_types: tuple[str, ...]


def _sha256_digest(value: bytes) -> Sha256Digest:
    return Sha256Digest(root=f"sha256:{sha256(value).hexdigest()}")


def _default_artifact_id(envelope_bytes: bytes) -> ArtifactId:
    return ArtifactId(root=f"artifact_{sha256(envelope_bytes).hexdigest()}")


def _typed_envelope(value: object) -> ArtifactEnvelope:
    if type(value) is not dict or set(value) != {
        "artifact_type",
        "schema_version",
        "schema_id",
        "payload_media_type",
        "payload_hash",
        "parent_artifact_ids",
        "created_by",
        "created_at",
        "source_evidence_ids",
    }:
        raise ValueError("artifact envelope has an invalid keyset")
    raw = cast(dict[str, object], value)
    parents = raw["parent_artifact_ids"]
    evidence_ids = raw["source_evidence_ids"]
    scalar_keys = (
        "artifact_type",
        "schema_version",
        "schema_id",
        "payload_media_type",
        "payload_hash",
        "created_by",
        "created_at",
    )
    if (
        type(parents) is not list
        or any(type(item) is not str for item in parents)
        or type(evidence_ids) is not list
        or any(type(item) is not str for item in evidence_ids)
        or any(type(raw[key]) is not str for key in scalar_keys)
    ):
        raise ValueError("artifact envelope contains invalid repeated fields")
    return ArtifactEnvelope(
        artifact_type=cast(str, raw["artifact_type"]),
        schema_version=cast(str, raw["schema_version"]),
        schema_id=cast(str, raw["schema_id"]),
        payload_media_type=cast(
            Literal["application/vnd.automarkov.canonical-payload+json"],
            raw["payload_media_type"],
        ),
        payload_hash=cast(str, raw["payload_hash"]),
        parent_artifact_ids=tuple(
            ArtifactId(root=item) for item in cast(list[str], parents)
        ),
        created_by=cast(str, raw["created_by"]),
        created_at=cast(str, raw["created_at"]),
        source_evidence_ids=tuple(cast(list[str], evidence_ids)),
    )


class _ArtifactRepositoryCore:
    """两种存储共用的 canonical codec 与完整性核心。"""

    def __init__(
        self,
        schema_registry: ArtifactSchemaRegistry | None = None,
    ) -> None:
        self._schemas = schema_registry or _default_schema_registry()
        self._schemas.freeze()
        self._lock = RLock()

    def _prepare(self, request_input: ArtifactPutInput) -> _PreparedArtifact:
        request = validate_artifact_put_request(request_input)
        try:
            raw_payload = parse_json_payload(request.payload_bytes)
        except ValueError as error:
            raise CanonicalPayloadError(request.artifact_type, None) from error
        registered = self._schemas.resolve(request.artifact_type, raw_payload)
        try:
            payload_bytes = registered.codec.encode(raw_payload)
        except ValueError as error:
            raise CanonicalPayloadError(
                request.artifact_type, registered.schema_version
            ) from error
        payload_hash = _sha256_digest(payload_bytes)
        envelope = ArtifactEnvelope(
            artifact_type=request.artifact_type,
            schema_version=registered.schema_version,
            schema_id=registered.codec.schema_id,
            payload_media_type=PAYLOAD_MEDIA_TYPE,
            payload_hash=payload_hash.root,
            parent_artifact_ids=request.parent_artifact_ids,
            created_by=request.created_by,
            created_at=request.created_at,
            source_evidence_ids=request.source_evidence_ids,
        )
        envelope_bytes = canonical_json_bytes(envelope.model_dump(mode="json"))
        return _PreparedArtifact(
            envelope_bytes=envelope_bytes,
            payload_bytes=payload_bytes,
            payload_hash=payload_hash,
            artifact_type=request.artifact_type,
            schema_version=registered.schema_version,
            schema_id=registered.codec.schema_id,
            parent_artifact_ids=request.parent_artifact_ids,
            direct_parent_artifact_types=registered.direct_parent_artifact_types,
        )

    def append(self, request: ArtifactAppendRequest) -> RunView:
        raise CapabilityDeferredError("artifact.append", "T03")

    def project(self, run_id: RunId) -> RunView:
        raise CapabilityDeferredError("artifact.project", "T03")

    @staticmethod
    def _validate_parent_contract(
        artifact_type: str,
        expected_types: tuple[str, ...],
        actual_types: tuple[str, ...],
    ) -> None:
        canonical_actual = tuple(
            sorted(actual_types, key=lambda item: item.encode("utf-8"))
        )
        if canonical_actual != expected_types:
            raise ArtifactParentContractError(
                artifact_type,
                expected_types,
                canonical_actual,
            )

    def _verify(
        self,
        stored: _StoredArtifact,
        direct_parent_artifact_types: tuple[str, ...],
    ) -> None:
        try:
            if _default_artifact_id(stored.envelope_bytes) != stored.artifact_id:
                raise ValueError("artifact identity mismatch")
            raw_envelope = parse_json_payload(stored.envelope_bytes)
            if canonical_json_bytes(raw_envelope) != stored.envelope_bytes:
                raise ValueError("noncanonical envelope")
            envelope = _typed_envelope(raw_envelope)
            if _sha256_digest(stored.payload_bytes) != stored.payload_hash:
                raise ValueError("payload hash mismatch")
            registered = self._schemas.resolve(
                envelope.artifact_type,
                {"schema_version": envelope.schema_version},
            )
            if (
                envelope.artifact_type != stored.artifact_type
                or envelope.schema_version != stored.schema_version
                or envelope.schema_id != registered.codec.schema_id
                or envelope.payload_hash != stored.payload_hash.root
                or envelope.parent_artifact_ids != stored.parent_artifact_ids
            ):
                raise ValueError("stored schema mismatch")
            self._validate_parent_contract(
                stored.artifact_type,
                registered.direct_parent_artifact_types,
                direct_parent_artifact_types,
            )
            registered.codec.decode(stored.payload_bytes)
        except (
            ArtifactParentContractError,
            ArtifactSchemaError,
            TypeError,
            ValueError,
        ) as error:
            raise ArtifactIntegrityError(stored.artifact_id.root) from error

    def _read_result(
        self,
        stored: _StoredArtifact,
        direct_parent_artifact_types: tuple[str, ...],
    ) -> ArtifactBytesResult:
        self._verify(stored, direct_parent_artifact_types)
        document = parse_canonical_document(stored.payload_bytes)
        if type(document) is not dict:
            raise ArtifactIntegrityError(stored.artifact_id.root)
        document_object = cast(dict[str, object], document)
        schema_id = document_object.get("schema_id")
        float_paths = document_object.get("exact_float_paths")
        payload = document_object.get("payload")
        if (
            type(schema_id) is not str
            or type(float_paths) is not list
            or any(type(path) is not str for path in float_paths)
        ):
            raise ArtifactIntegrityError(stored.artifact_id.root)
        return ArtifactBytesResult.model_validate(
            {
                "schema_version": "automarkov.artifact-bytes-result.v2",
                "artifact_id": stored.artifact_id,
                "envelope": _typed_envelope(parse_json_payload(stored.envelope_bytes)),
                "payload_bytes": bytes(stored.payload_bytes),
                "payload_document": {
                    "schema_id": schema_id,
                    "exact_float_paths": tuple(cast(list[str], float_paths)),
                    "payload": payload,
                },
            },
            strict=True,
        )

    @staticmethod
    def _result(stored: _StoredArtifact) -> ArtifactPutResult:
        return ArtifactPutResult(
            schema_version="automarkov.artifact-put-result.v1",
            artifact_id=stored.artifact_id,
            payload_hash=stored.payload_hash,
        )


class InMemoryArtifactRepository(_ArtifactRepositoryCore):
    """供合同测试与单进程执行使用的原子不可变工件仓库。"""

    def __init__(
        self,
        schema_registry: ArtifactSchemaRegistry | None = None,
    ) -> None:
        super().__init__(schema_registry)
        self._artifacts: dict[str, _StoredArtifact] = {}

    def put(self, request: ArtifactPutInput) -> ArtifactPutResult:
        prepared = self._prepare(request)
        artifact_id = _default_artifact_id(prepared.envelope_bytes)

        with self._lock:
            if artifact_id in prepared.parent_artifact_ids:
                raise ArtifactCycleError(artifact_id.root)
            parent_types = self._verified_parent_types(
                prepared.parent_artifact_ids,
                integrity_subject=None,
            )
            for parent_id in prepared.parent_artifact_ids:
                if self._has_ancestor(parent_id, artifact_id):
                    raise ArtifactCycleError(artifact_id.root)
            self._validate_parent_contract(
                prepared.artifact_type,
                prepared.direct_parent_artifact_types,
                parent_types,
            )
            candidate = _StoredArtifact(
                artifact_id=artifact_id,
                envelope_bytes=prepared.envelope_bytes,
                payload_bytes=prepared.payload_bytes,
                payload_hash=prepared.payload_hash,
                parent_artifact_ids=prepared.parent_artifact_ids,
                artifact_type=prepared.artifact_type,
                schema_version=prepared.schema_version,
            )
            existing = self._artifacts.get(artifact_id.root)
            if existing is not None:
                self._verify(
                    existing,
                    parent_types,
                )
                if (
                    existing.envelope_bytes != candidate.envelope_bytes
                    or existing.payload_bytes != candidate.payload_bytes
                ):
                    raise ArtifactIdentityConflictError(artifact_id.root)
                return self._result(existing)
            self._artifacts[artifact_id.root] = candidate
            return self._result(candidate)

    def get(self, artifact_id: ArtifactId) -> ArtifactBytesResult:
        with self._lock:
            stored = self._require_artifact(artifact_id)
            parent_types = self._verified_parent_types(
                stored.parent_artifact_ids,
                integrity_subject=stored.artifact_id.root,
            )
            return self._read_result(stored, parent_types)

    def lineage(self, artifact_id: ArtifactId) -> ArtifactLineageResult:
        with self._lock:
            stored = self._require_artifact(artifact_id)
            parent_types = self._verified_parent_types(
                stored.parent_artifact_ids,
                integrity_subject=stored.artifact_id.root,
            )
            self._verify(stored, parent_types)
            return ArtifactLineageResult(
                schema_version="automarkov.artifact-lineage-result.v1",
                artifact_ids=stored.parent_artifact_ids,
            )

    def _require_artifact(self, artifact_id: ArtifactId) -> _StoredArtifact:
        try:
            return self._artifacts[artifact_id.root]
        except KeyError as error:
            raise UnknownArtifactError(artifact_id.root) from error

    def _verified_parent_types(
        self,
        parent_artifact_ids: tuple[ArtifactId, ...],
        *,
        integrity_subject: str | None,
    ) -> tuple[str, ...]:
        verified_types: dict[str, str] = {}
        active: set[str] = set()
        pending = [(parent.root, False) for parent in reversed(parent_artifact_ids)]
        while pending:
            current_id, leaving = pending.pop()
            if current_id in verified_types:
                continue
            stored = self._artifacts.get(current_id)
            if stored is None:
                if integrity_subject is None:
                    raise MissingArtifactParentError(current_id)
                raise ArtifactIntegrityError(integrity_subject)
            if leaving:
                try:
                    parent_types = tuple(
                        verified_types[parent.root]
                        for parent in stored.parent_artifact_ids
                    )
                    self._verify(stored, parent_types)
                except (ArtifactIntegrityError, KeyError) as error:
                    subject = integrity_subject or stored.artifact_id.root
                    raise ArtifactIntegrityError(subject) from error
                verified_types[current_id] = stored.artifact_type
                active.remove(current_id)
                continue
            if current_id in active:
                subject = integrity_subject or current_id
                raise ArtifactIntegrityError(subject)
            active.add(current_id)
            pending.append((current_id, True))
            pending.extend(
                (parent.root, False)
                for parent in reversed(stored.parent_artifact_ids)
                if parent.root not in verified_types
            )
        return tuple(verified_types[parent.root] for parent in parent_artifact_ids)

    def _has_ancestor(self, start: ArtifactId, target: ArtifactId) -> bool:
        pending = [start]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == target:
                return True
            if current.root in visited:
                continue
            visited.add(current.root)
            stored = self._artifacts.get(current.root)
            if stored is not None:
                pending.extend(stored.parent_artifact_ids)
        return False


class SqliteArtifactRepository(_ArtifactRepositoryCore):
    """使用事务 CAS 的文件型生产工件仓库。"""

    def __init__(
        self,
        database_path: str | Path,
        schema_registry: ArtifactSchemaRegistry | None = None,
    ) -> None:
        super().__init__(schema_registry)
        self._database_path = str(database_path)
        self._connection = sqlite3.connect(
            self._database_path,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        try:
            self._initialize_schema()
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
        except BaseException as error:
            self._connection.close()
            if not isinstance(error, Exception) or isinstance(
                error,
                ArtifactIntegrityError,
            ):
                raise
            raise ArtifactIntegrityError(f"sqlite:{self._database_path}") from error

    def _initialize_schema(self) -> None:
        self._connection.execute("BEGIN EXCLUSIVE")
        try:
            actual_rows = _sqlite_schema_rows(self._connection)
            if not actual_rows:
                for statement in _SQLITE_SCHEMA_STATEMENTS:
                    self._connection.execute(statement)
                self._connection.execute(
                    f"PRAGMA user_version = {_SQLITE_SCHEMA_VERSION}"
                )
                actual_rows = _sqlite_schema_rows(self._connection)

            self._require_schema_integrity(actual_rows)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def put(self, request: ArtifactPutInput) -> ArtifactPutResult:
        prepared = self._prepare(request)
        artifact_id = _default_artifact_id(prepared.envelope_bytes)
        if artifact_id in prepared.parent_artifact_ids:
            raise ArtifactCycleError(artifact_id.root)

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_schema_integrity()
                self._ensure_schema_contract(prepared)
                parent_types = self._verified_parent_types(
                    prepared.parent_artifact_ids,
                    integrity_subject=None,
                )
                for parent in prepared.parent_artifact_ids:
                    if self._sqlite_has_ancestor(parent.root, artifact_id.root):
                        raise ArtifactCycleError(artifact_id.root)
                self._validate_parent_contract(
                    prepared.artifact_type,
                    prepared.direct_parent_artifact_types,
                    parent_types,
                )

                try:
                    existing = self._fetch_stored(artifact_id)
                except (TypeError, ValueError) as error:
                    raise ArtifactIntegrityError(artifact_id.root) from error
                if existing is not None:
                    self._verify(existing, parent_types)
                    if (
                        existing.envelope_bytes != prepared.envelope_bytes
                        or existing.payload_bytes != prepared.payload_bytes
                    ):
                        raise ArtifactIdentityConflictError(artifact_id.root)
                    self._connection.commit()
                    return self._result(existing)

                blob = self._connection.execute(
                    "SELECT rowid FROM payload_blobs WHERE payload_hash = ?",
                    (prepared.payload_hash.root,),
                ).fetchone()
                if blob is None:
                    missing_blob_reference = self._connection.execute(
                        "SELECT artifact_id FROM artifacts WHERE payload_hash = ? "
                        "ORDER BY artifact_id LIMIT 1",
                        (prepared.payload_hash.root,),
                    ).fetchone()
                    if missing_blob_reference is not None:
                        raise ArtifactIntegrityError(str(missing_blob_reference[0]))
                if blob is not None:
                    persisted_payload = self._read_bounded_blob(
                        "payload_blobs",
                        "payload_bytes",
                        blob[0],
                        MAX_CANONICAL_DOCUMENT_BYTES,
                        artifact_id.root,
                    )
                    if persisted_payload != prepared.payload_bytes:
                        raise ArtifactIntegrityError(artifact_id.root)
                self._connection.execute(
                    "INSERT OR IGNORE INTO payload_blobs(payload_hash, payload_bytes) "
                    "VALUES (?, ?)",
                    (prepared.payload_hash.root, prepared.payload_bytes),
                )

                self._connection.execute(
                    "INSERT INTO artifacts(artifact_id, envelope_bytes, payload_hash, "
                    "artifact_type, schema_version) VALUES (?, ?, ?, ?, ?)",
                    (
                        artifact_id.root,
                        prepared.envelope_bytes,
                        prepared.payload_hash.root,
                        prepared.artifact_type,
                        prepared.schema_version,
                    ),
                )
                self._connection.executemany(
                    "INSERT INTO artifact_parents(artifact_id, position, parent_id) "
                    "VALUES (?, ?, ?)",
                    [
                        (artifact_id.root, position, parent.root)
                        for position, parent in enumerate(prepared.parent_artifact_ids)
                    ],
                )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        return self._result(
            _StoredArtifact(
                artifact_id=artifact_id,
                envelope_bytes=prepared.envelope_bytes,
                payload_bytes=prepared.payload_bytes,
                payload_hash=prepared.payload_hash,
                parent_artifact_ids=prepared.parent_artifact_ids,
                artifact_type=prepared.artifact_type,
                schema_version=prepared.schema_version,
            )
        )

    def _require_schema_integrity(
        self,
        actual_rows: tuple[tuple[object, ...], ...] | None = None,
    ) -> None:
        user_version = int(
            self._connection.execute("PRAGMA user_version").fetchone()[0]
        )
        if actual_rows is None:
            actual_rows = _sqlite_schema_rows(self._connection)
        if (
            user_version != _SQLITE_SCHEMA_VERSION
            or actual_rows != _expected_sqlite_schema_rows()
        ):
            raise ArtifactIntegrityError(f"sqlite:{self._database_path}")

    def _ensure_schema_contract(self, prepared: _PreparedArtifact) -> None:
        parent_contract_bytes = canonical_json_bytes(
            list(prepared.direct_parent_artifact_types)
        )
        existing = self._read_schema_contract(
            prepared.artifact_type,
            prepared.schema_version,
            f"schema:{prepared.artifact_type}:{prepared.schema_version}",
        )
        if existing is None:
            existing_artifact = self._connection.execute(
                "SELECT artifact_id FROM artifacts "
                "WHERE artifact_type = ? AND schema_version = ? LIMIT 1",
                (prepared.artifact_type, prepared.schema_version),
            ).fetchone()
            if existing_artifact is not None:
                raise ArtifactIntegrityError(str(existing_artifact[0]))
            self._connection.execute(
                "INSERT INTO artifact_schema_contracts(artifact_type, schema_version, "
                "schema_id, direct_parent_artifact_types) VALUES (?, ?, ?, ?)",
                (
                    prepared.artifact_type,
                    prepared.schema_version,
                    prepared.schema_id,
                    parent_contract_bytes,
                ),
            )
            return
        if (
            existing[0] != prepared.schema_id
            or existing[1] != parent_contract_bytes
        ):
            raise ArtifactSchemaConflictError(
                prepared.artifact_type,
                prepared.schema_version,
            )

    def _verify_persisted_schema_contract(
        self,
        artifact_id: ArtifactId,
        artifact_type: str,
        schema_version: str,
    ) -> None:
        try:
            registered = self._schemas.resolve(
                artifact_type,
                {"schema_version": schema_version},
            )
        except ArtifactSchemaError as error:
            raise ArtifactIntegrityError(artifact_id.root) from error
        expected_parents = canonical_json_bytes(
            list(registered.direct_parent_artifact_types)
        )
        row = self._read_schema_contract(
            artifact_type,
            schema_version,
            artifact_id.root,
        )
        if row is None or (
            row[0] != registered.codec.schema_id or row[1] != expected_parents
        ):
            raise ArtifactIntegrityError(artifact_id.root)

    def _read_schema_contract(
        self,
        artifact_type: str,
        schema_version: str,
        integrity_subject: str,
    ) -> tuple[str, bytes] | None:
        row = self._connection.execute(
            "SELECT rowid, schema_id FROM artifact_schema_contracts "
            "WHERE artifact_type = ? AND schema_version = ?",
            (artifact_type, schema_version),
        ).fetchone()
        if row is None:
            return None
        parent_contract_bytes = self._read_bounded_blob(
            "artifact_schema_contracts",
            "direct_parent_artifact_types",
            row[0],
            MAX_JSON_PAYLOAD_BYTES,
            integrity_subject,
        )
        return str(row[1]), parent_contract_bytes

    def get(self, artifact_id: ArtifactId) -> ArtifactBytesResult:
        with self._lock:
            self._require_schema_integrity()
            try:
                stored = self._fetch_stored(artifact_id)
            except (TypeError, ValueError) as error:
                raise ArtifactIntegrityError(artifact_id.root) from error
            if stored is None:
                raise UnknownArtifactError(artifact_id.root)
            parent_types = self._verified_parent_types(
                stored.parent_artifact_ids,
                integrity_subject=stored.artifact_id.root,
            )
            return self._read_result(stored, parent_types)

    def lineage(self, artifact_id: ArtifactId) -> ArtifactLineageResult:
        with self._lock:
            self._require_schema_integrity()
            try:
                stored = self._fetch_stored(artifact_id)
            except (TypeError, ValueError) as error:
                raise ArtifactIntegrityError(artifact_id.root) from error
            if stored is None:
                raise UnknownArtifactError(artifact_id.root)
            parent_types = self._verified_parent_types(
                stored.parent_artifact_ids,
                integrity_subject=stored.artifact_id.root,
            )
            self._verify(stored, parent_types)
            return ArtifactLineageResult(
                schema_version="automarkov.artifact-lineage-result.v1",
                artifact_ids=stored.parent_artifact_ids,
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _verified_parent_types(
        self,
        parent_artifact_ids: tuple[ArtifactId, ...],
        *,
        integrity_subject: str | None,
    ) -> tuple[str, ...]:
        verified_types: dict[str, str] = {}
        stored_by_id: dict[str, _StoredArtifact] = {}
        active: set[str] = set()
        pending = [(parent.root, False) for parent in reversed(parent_artifact_ids)]
        while pending:
            current_id, leaving = pending.pop()
            if current_id in verified_types:
                continue
            if leaving:
                stored = stored_by_id[current_id]
                try:
                    parent_types = tuple(
                        verified_types[parent.root]
                        for parent in stored.parent_artifact_ids
                    )
                    self._verify(stored, parent_types)
                except (ArtifactIntegrityError, KeyError) as error:
                    subject = integrity_subject or stored.artifact_id.root
                    raise ArtifactIntegrityError(subject) from error
                verified_types[current_id] = stored.artifact_type
                active.remove(current_id)
                continue
            if current_id in active:
                subject = integrity_subject or current_id
                raise ArtifactIntegrityError(subject)
            try:
                stored = self._fetch_stored(ArtifactId(root=current_id))
            except (ArtifactIntegrityError, TypeError, ValueError) as error:
                subject = integrity_subject or current_id
                raise ArtifactIntegrityError(subject) from error
            if stored is None:
                if integrity_subject is None:
                    raise MissingArtifactParentError(current_id)
                raise ArtifactIntegrityError(integrity_subject)
            stored_by_id[current_id] = stored
            active.add(current_id)
            pending.append((current_id, True))
            pending.extend(
                (parent.root, False)
                for parent in reversed(stored.parent_artifact_ids)
                if parent.root not in verified_types
            )
        return tuple(verified_types[parent.root] for parent in parent_artifact_ids)

    def _sqlite_has_ancestor(self, start: str, target: str) -> bool:
        return (
            self._connection.execute(
                """
                WITH RECURSIVE ancestors(artifact_id) AS (
                    SELECT ?
                    UNION
                    SELECT parent_id
                    FROM artifact_parents
                    JOIN ancestors USING (artifact_id)
                )
                SELECT 1 FROM ancestors WHERE artifact_id = ? LIMIT 1
                """,
                (start, target),
            ).fetchone()
            is not None
        )

    def _read_bounded_blob(
        self,
        table: str,
        column: str,
        row_id: object,
        maximum_bytes: int,
        integrity_subject: str,
    ) -> bytes:
        if type(row_id) is not int:
            raise ArtifactIntegrityError(integrity_subject)
        try:
            with self._connection.blobopen(
                table,
                column,
                row_id,
                readonly=True,
            ) as blob:
                if len(blob) > maximum_bytes:
                    raise ArtifactIntegrityError(integrity_subject)
                return blob.read()
        except ArtifactIntegrityError:
            raise
        except (OverflowError, sqlite3.Error, TypeError, ValueError) as error:
            raise ArtifactIntegrityError(integrity_subject) from error

    def _fetch_stored(self, artifact_id: ArtifactId) -> _StoredArtifact | None:
        row = self._connection.execute(
            """
            SELECT a.rowid, b.rowid, a.payload_hash,
                   a.artifact_type, a.schema_version
            FROM artifacts AS a
            LEFT JOIN payload_blobs AS b USING (payload_hash)
            WHERE a.artifact_id = ?
            """,
            (artifact_id.root,),
        ).fetchone()
        if row is None:
            return None
        if row[1] is None:
            raise ArtifactIntegrityError(artifact_id.root)
        self._verify_persisted_schema_contract(
            artifact_id,
            str(row[3]),
            str(row[4]),
        )
        envelope_bytes = self._read_bounded_blob(
            "artifacts",
            "envelope_bytes",
            row[0],
            MAX_JSON_PAYLOAD_BYTES,
            artifact_id.root,
        )
        payload_bytes = self._read_bounded_blob(
            "payload_blobs",
            "payload_bytes",
            row[1],
            MAX_CANONICAL_DOCUMENT_BYTES,
            artifact_id.root,
        )
        parents = tuple(
            ArtifactId(root=parent[0])
            for parent in self._connection.execute(
                "SELECT parent_id FROM artifact_parents "
                "WHERE artifact_id = ? ORDER BY position",
                (artifact_id.root,),
            ).fetchall()
        )
        return _StoredArtifact(
            artifact_id=artifact_id,
            envelope_bytes=envelope_bytes,
            payload_bytes=payload_bytes,
            payload_hash=Sha256Digest(root=row[2]),
            parent_artifact_ids=parents,
            artifact_type=row[3],
            schema_version=row[4],
        )
