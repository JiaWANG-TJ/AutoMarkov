from __future__ import annotations

import base64
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Literal, TypeAlias, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import (
    BaseModel,
    RootModel,
    TypeAdapter,
    field_validator,
    model_validator,
)

from automarkov.canonical import (
    MAX_CANONICAL_DOCUMENT_BYTES,
    MAX_JSON_PAYLOAD_BYTES,
    CanonicalPayloadCodec,
    canonical_json_bytes,
    parse_canonical_document,
    parse_json_payload,
    require_registered_model_number_contract,
)
from automarkov.domain import (
    ArtifactId,
    RunId,
    Sha256Digest,
    StrictFrozenModel,
    TaskRequest,
    VerifiedEventHead,
)
from automarkov.errors import (
    ArtifactCycleError,
    ArtifactIdentityConflictError,
    ArtifactIntegrityError,
    ArtifactParentContractError,
    ArtifactSchemaConflictError,
    ArtifactSchemaError,
    ArtifactWriteAuthorityError,
    CanonicalPayloadError,
    CommandAuthenticationError,
    EventHeadConflictError,
    EventReplayConflictError,
    EventSequenceConflictError,
    MissingArtifactParentError,
    RunProjectorIdentityError,
    TerminalProvenanceError,
    UnknownArtifactError,
    UnknownRunError,
)
from automarkov.lifecycle import (
    RUN_PROJECTOR_HASH,
    RUN_PROJECTOR_VERSION,
    TERMINAL_STATES,
    AppendRunEventsCommand,
    ApprovalEventSnapshot,
    ArtifactReference,
    ClarificationChildRunCreated,
    ClarificationRequested,
    CommitTerminalCommand,
    CreateClarificationChildRunCommand,
    CreateReplacementRunCommand,
    CrossRunLifecycleCommitReceipt,
    EventAuthenticator,
    EventHead,
    EventRecord,
    EventReference,
    ExecutionAttestation,
    ExecutionTopologySubstituted,
    GateOmittedByDesign,
    LifecycleCommand,
    LifecycleCommitReceipt,
    LifecycleCommitResult,
    ReplacementRunCreated,
    RunAppendStep,
    RunAuditProjection,
    RunCreated,
    RunEvent,
    RunEventSecurityContext,
    RunProjection,
    RunProjectionRequest,
    RunSuperseded,
    RuntimeReplacementPrerequisite,
    SignedApprovalEvent,
    StateTransitioned,
    TerminalResult,
    WaitingRuntime,
    _event_hash,
    append_record,
    default_event_schema_registry,
    parse_event_record,
    project_records,
    require_expected_head,
    run_audit_projection_id,
    validate_lifecycle_command,
    validate_projection_request,
)
from automarkov.public import (
    ArtifactBytesResult,
    ArtifactEnvelope,
    ArtifactLineageResult,
    ArtifactPutInput,
    ArtifactPutResult,
    ArtifactType,
    AuthenticatedCommandContext,
    CommandAuthority,
    PayloadSchemaVersion,
    validate_artifact_put_request,
)

PAYLOAD_MEDIA_TYPE = "application/vnd.automarkov.canonical-payload+json"
_ARTIFACT_TYPE_ADAPTER = TypeAdapter(ArtifactType)
_LIFECYCLE_DERIVED_ARTIFACT_TYPES = frozenset(
    {
        "execution_attestation",
        "process_execution_terminal_record",
        "run_audit_projection",
        "terminal_result",
    }
)
_CANDIDATE_FROZEN_STATES = frozenset(
    {
        "SEALED_E2E_VALIDATING",
        "TRAINING_SMOKE_TESTING",
        "POLICY_TRAINING",
        "FINAL_EVALUATING",
        "PACKAGING",
    }
)
_SIGNED_EVENT_TYPES = (
    RunCreated,
    SignedApprovalEvent,
    RunSuperseded,
    ReplacementRunCreated,
    ClarificationChildRunCreated,
    GateOmittedByDesign,
    ExecutionTopologySubstituted,
)

_SQLITE_SCHEMA_VERSION = 8
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
    """CREATE TABLE event_schema_contracts (
    event_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    schema_id TEXT NOT NULL,
    PRIMARY KEY (event_type, schema_version)
) STRICT""",
    """CREATE TABLE artifact_parents (
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    position INTEGER NOT NULL CHECK (position >= 0),
    parent_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    PRIMARY KEY (artifact_id, position),
    UNIQUE (artifact_id, parent_id)
) STRICT""",
    """CREATE TABLE run_events (
    run_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    event_id TEXT NOT NULL UNIQUE,
    event_hash TEXT NOT NULL,
    record_bytes BLOB NOT NULL,
    PRIMARY KEY (run_id, sequence_no),
    UNIQUE (run_id, event_hash)
) STRICT""",
    """CREATE TABLE run_heads (
    run_id TEXT PRIMARY KEY,
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    event_hash TEXT NOT NULL,
    FOREIGN KEY (run_id, sequence_no) REFERENCES run_events(run_id, sequence_no)
) STRICT""",
    """CREATE TABLE signed_event_nonces (
    signing_key_id TEXT NOT NULL,
    nonce_b64url TEXT NOT NULL,
    run_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    event_id TEXT NOT NULL UNIQUE REFERENCES run_events(event_id),
    PRIMARY KEY (nonce_b64url),
    UNIQUE (signing_key_id, run_id, sequence_no)
) STRICT""",
    """CREATE TABLE lifecycle_commands (
    command_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    command_fingerprint TEXT NOT NULL,
    result_bytes BLOB NOT NULL
) STRICT""",
    """CREATE TABLE run_terminal_results (
    run_id TEXT PRIMARY KEY,
    terminal_sequence_no INTEGER NOT NULL CHECK (terminal_sequence_no >= 0),
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    payload_hash TEXT NOT NULL,
    FOREIGN KEY (run_id, terminal_sequence_no)
        REFERENCES run_events(run_id, sequence_no)
) STRICT""",
    """CREATE TABLE run_audit_projections (
    run_id TEXT NOT NULL,
    as_of_sequence_no INTEGER NOT NULL CHECK (as_of_sequence_no >= 0),
    projector_hash TEXT NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    payload_hash TEXT NOT NULL,
    PRIMARY KEY (run_id, as_of_sequence_no, projector_hash),
    FOREIGN KEY (run_id, as_of_sequence_no)
        REFERENCES run_events(run_id, sequence_no)
) STRICT""",
    """CREATE TABLE run_replacements (
    parent_run_id TEXT PRIMARY KEY REFERENCES run_heads(run_id),
    child_run_id TEXT NOT NULL UNIQUE REFERENCES run_heads(run_id),
    supersession_event_id TEXT NOT NULL UNIQUE REFERENCES run_events(event_id),
    prerequisite_event_id TEXT NOT NULL UNIQUE REFERENCES run_events(event_id),
    replacement_ordinal INTEGER NOT NULL CHECK (replacement_ordinal > 0),
    replacement_policy_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    process_terminal_artifact_id TEXT NOT NULL UNIQUE REFERENCES artifacts(artifact_id),
    terminal_result_artifact_id TEXT NOT NULL UNIQUE REFERENCES artifacts(artifact_id),
    execution_attestation_artifact_id TEXT NOT NULL UNIQUE
        REFERENCES artifacts(artifact_id)
) STRICT""",
    """CREATE TABLE run_clarification_continuations (
    parent_run_id TEXT PRIMARY KEY REFERENCES run_heads(run_id),
    child_run_id TEXT NOT NULL UNIQUE REFERENCES run_heads(run_id),
    child_event_id TEXT NOT NULL UNIQUE REFERENCES run_events(event_id),
    signed_answer_bundle_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    continuation_policy_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id)
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


ParentCardinality: TypeAlias = Literal["one", "optional", "many"]


class ParentBinding(StrictFrozenModel):
    """将 payload 中一组 ID/hash 引用冻结到允许的父工件类型。"""

    artifact_id_path: str
    payload_hash_path: str
    allowed_artifact_types: tuple[str, ...]
    cardinality: ParentCardinality

    @field_validator("artifact_id_path", "payload_hash_path")
    @classmethod
    def require_closed_json_path(cls, value: str) -> str:
        segments = value.split(".")
        if not value or any(
            not segment
            or (
                segment != "*"
                and (
                    not segment.replace("_", "a").isalnum()
                    or not (segment[0].isalpha() or segment[0] == "_")
                )
            )
            for segment in segments
        ):
            raise ValueError("parent binding paths must be closed JSON paths")
        return value

    @field_validator("allowed_artifact_types")
    @classmethod
    def require_canonical_allowed_types(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        validated = tuple(
            _ARTIFACT_TYPE_ADAPTER.validate_python(item, strict=True) for item in value
        )
        canonical = tuple(sorted(set(validated), key=lambda item: item.encode("utf-8")))
        if not validated or validated != canonical:
            raise ValueError(
                "allowed artifact types must be nonempty, sorted, and unique"
            )
        return validated

    @model_validator(mode="after")
    def require_paired_path_shape(self) -> ParentBinding:
        id_segments = self.artifact_id_path.split(".")
        hash_segments = self.payload_hash_path.split(".")
        if id_segments[-1] != "artifact_id" or hash_segments[-1] != "payload_hash":
            raise ValueError(
                "parent binding paths must address artifact ID/hash fields"
            )
        id_wildcards = tuple(
            index for index, segment in enumerate(id_segments) if segment == "*"
        )
        hash_wildcards = tuple(
            index for index, segment in enumerate(hash_segments) if segment == "*"
        )
        if id_wildcards != hash_wildcards:
            raise ValueError("parent binding ID/hash paths must have paired wildcards")
        if (self.cardinality == "many") != bool(id_wildcards):
            raise ValueError("many bindings, and only many bindings, must use wildcard")
        return self


class ExactParentContract(StrictFrozenModel):
    contract_kind: Literal["exact"] = "exact"
    direct_parent_artifact_types: tuple[str, ...]


class PayloadBoundParentContract(StrictFrozenModel):
    contract_kind: Literal["payload_bound"] = "payload_bound"
    bindings: tuple[ParentBinding, ...]


ParentContract: TypeAlias = ExactParentContract | PayloadBoundParentContract


@dataclass(frozen=True, slots=True)
class _RegisteredSchema:
    artifact_type: str
    schema_version: str
    codec: CanonicalPayloadCodec[BaseModel]
    parent_contract: ParentContract

    @property
    def direct_parent_artifact_types(self) -> tuple[str, ...]:
        if isinstance(self.parent_contract, ExactParentContract):
            return self.parent_contract.direct_parent_artifact_types
        return ()

    @property
    def payload_parent_bindings(self) -> tuple[ParentBinding, ...]:
        if isinstance(self.parent_contract, PayloadBoundParentContract):
            return self.parent_contract.bindings
        return ()


@dataclass(frozen=True, slots=True)
class _PathOccurrence:
    location: tuple[str | int, ...]
    value: object
    exists: bool


@dataclass(frozen=True, slots=True)
class _ExpectedParentReference:
    artifact_id: ArtifactId
    payload_hash: Sha256Digest
    allowed_artifact_types: tuple[str, ...]


def _extract_path_occurrences(
    payload: object,
    path: str,
) -> dict[tuple[int, ...], _PathOccurrence]:
    occurrences: dict[tuple[int, ...], _PathOccurrence] = {}
    segments = tuple(path.split("."))

    def visit(
        current: object,
        remaining: tuple[str, ...],
        coordinate: tuple[int, ...],
        location: tuple[str | int, ...],
    ) -> None:
        if not remaining:
            occurrences[coordinate] = _PathOccurrence(location, current, True)
            return
        if current is None:
            occurrences[coordinate] = _PathOccurrence(location, None, False)
            return
        segment, *remaining_items = remaining
        tail = tuple(remaining_items)
        if segment == "*":
            if type(current) is not list:
                raise ValueError("payload-bound wildcard must address an array")
            for index, item in enumerate(current):
                visit(item, tail, coordinate + (index,), location + (index,))
            return
        if type(current) is not dict:
            raise ValueError("payload-bound path must address an object")
        mapping = cast(dict[str, object], current)
        if segment not in mapping:
            raise ValueError(f"payload-bound parent path is missing: {segment}")
        visit(mapping[segment], tail, coordinate, location + (segment,))

    visit(payload, segments, (), ())
    return occurrences


def _payload_artifact_reference_locations(
    payload: object,
) -> set[tuple[tuple[str | int, ...], tuple[str | int, ...]]]:
    locations: set[tuple[tuple[str | int, ...], tuple[str | int, ...]]] = set()

    def visit(current: object, location: tuple[str | int, ...]) -> None:
        if type(current) is list:
            for index, item in enumerate(current):
                visit(item, location + (index,))
            return
        if type(current) is not dict:
            return
        mapping = cast(dict[str, object], current)
        has_id = "artifact_id" in mapping
        has_hash = "payload_hash" in mapping
        if has_id != has_hash:
            raise ValueError("payload artifact ID/hash fields must be paired")
        if has_id:
            locations.add(
                (
                    location + ("artifact_id",),
                    location + ("payload_hash",),
                )
            )
        for key, value in mapping.items():
            visit(value, location + (key,))

    visit(payload, ())
    return locations


def _extract_payload_parent_references(
    payload: object,
    bindings: tuple[ParentBinding, ...],
) -> tuple[_ExpectedParentReference, ...]:
    references: list[_ExpectedParentReference] = []
    claimed_locations: set[tuple[tuple[str | int, ...], tuple[str | int, ...]]] = set()

    for binding in bindings:
        id_occurrences = _extract_path_occurrences(
            payload,
            binding.artifact_id_path,
        )
        hash_occurrences = _extract_path_occurrences(
            payload,
            binding.payload_hash_path,
        )
        if id_occurrences.keys() != hash_occurrences.keys():
            raise ValueError("payload-bound ID/hash branches are not synchronized")
        binding_count = 0
        for coordinate in id_occurrences:
            id_occurrence = id_occurrences[coordinate]
            hash_occurrence = hash_occurrences[coordinate]
            if id_occurrence.exists != hash_occurrence.exists:
                raise ValueError("payload-bound ID/hash branches are not synchronized")
            if not id_occurrence.exists:
                continue
            location_pair = (id_occurrence.location, hash_occurrence.location)
            if location_pair in claimed_locations:
                raise ValueError("payload artifact reference is bound more than once")
            claimed_locations.add(location_pair)
            artifact_id_value = id_occurrence.value
            payload_hash_value = hash_occurrence.value
            if (artifact_id_value is None) != (payload_hash_value is None):
                raise ValueError("payload artifact ID/hash fields must be paired")
            if artifact_id_value is None:
                if binding.cardinality != "optional":
                    raise ValueError(
                        "only optional payload parent bindings may contain a null pair"
                    )
                continue
            if (
                type(artifact_id_value) is not str
                or type(payload_hash_value) is not str
            ):
                raise ValueError("payload artifact ID/hash values must be strings")
            references.append(
                _ExpectedParentReference(
                    artifact_id=ArtifactId(root=artifact_id_value),
                    payload_hash=Sha256Digest(root=payload_hash_value),
                    allowed_artifact_types=binding.allowed_artifact_types,
                )
            )
            binding_count += 1
        if binding.cardinality == "one" and binding_count != 1:
            raise ValueError("payload parent binding requires exactly one reference")
        if binding.cardinality == "optional" and binding_count > 1:
            raise ValueError(
                "optional payload parent binding permits at most one reference"
            )

    if claimed_locations != _payload_artifact_reference_locations(payload):
        raise ValueError("payload contains an undeclared artifact reference")
    parent_ids = tuple(reference.artifact_id.root for reference in references)
    if len(set(parent_ids)) != len(parent_ids):
        raise ValueError("payload-bound parent references must be unique")
    return tuple(
        sorted(references, key=lambda item: item.artifact_id.root.encode("utf-8"))
    )


def _parent_contract_bytes(
    parent_contract: ParentContract,
) -> bytes:
    if isinstance(parent_contract, ExactParentContract):
        contract: dict[str, object] = {
            "contract_kind": "exact",
            "direct_parent_artifact_types": list(
                parent_contract.direct_parent_artifact_types
            ),
        }
    else:
        contract = {
            "contract_kind": "payload_bound",
            "bindings": [
                binding.model_dump(mode="json") for binding in parent_contract.bindings
            ],
        }
    return canonical_json_bytes(contract)


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
        direct_parent_artifact_types: tuple[str, ...] | None = None,
        payload_parent_bindings: tuple[ParentBinding, ...] | None = None,
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
        if (direct_parent_artifact_types is None) == (payload_parent_bindings is None):
            raise TypeError(
                "exactly one of direct_parent_artifact_types or "
                "payload_parent_bindings is required"
            )
        parent_contract: ParentContract
        if direct_parent_artifact_types is not None:
            if type(direct_parent_artifact_types) is not tuple:
                raise ValueError(
                    "direct parent artifact types must be nonempty strings in "
                    "canonical order"
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
                    "direct parent artifact types must be nonempty strings in "
                    "canonical order"
                )
            parent_contract = ExactParentContract(
                direct_parent_artifact_types=expected_parent_types
            )
        else:
            if type(payload_parent_bindings) is not tuple or any(
                type(binding) is not ParentBinding
                for binding in payload_parent_bindings
            ):
                raise ValueError(
                    "payload parent bindings must be a tuple of ParentBinding"
                )
            canonical_bindings = tuple(
                sorted(
                    set(payload_parent_bindings),
                    key=lambda binding: (
                        binding.artifact_id_path.encode("utf-8"),
                        binding.payload_hash_path.encode("utf-8"),
                    ),
                )
            )
            if (
                not payload_parent_bindings
                or payload_parent_bindings != canonical_bindings
            ):
                raise ValueError("payload parent bindings must be sorted and unique")
            id_paths = tuple(
                binding.artifact_id_path for binding in payload_parent_bindings
            )
            hash_paths = tuple(
                binding.payload_hash_path for binding in payload_parent_bindings
            )
            if len(set(id_paths)) != len(id_paths) or len(set(hash_paths)) != len(
                hash_paths
            ):
                raise ValueError("payload parent binding paths must be unique")
            parent_contract = PayloadBoundParentContract(
                bindings=payload_parent_bindings
            )
        registered = _RegisteredSchema(
            artifact_type,
            schema_version,
            codec,
            parent_contract,
        )
        with self._lock:
            if self._frozen:
                raise RuntimeError("artifact schema registry is frozen")
            existing = self._schemas.get(key)
            if existing is not None:
                if (
                    existing.codec.schema_id != codec.schema_id
                    or existing.parent_contract != registered.parent_contract
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
                    for parent_type in (
                        registered.direct_parent_artifact_types
                        if isinstance(registered.parent_contract, ExactParentContract)
                        else tuple(
                            allowed_type
                            for binding in registered.payload_parent_bindings
                            for allowed_type in binding.allowed_artifact_types
                        )
                    )
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
    parent_contract: ParentContract
    expected_parent_references: tuple[_ExpectedParentReference, ...] | None


@dataclass(frozen=True, slots=True)
class _VerifiedParent:
    artifact_id: ArtifactId
    artifact_type: str
    payload_hash: Sha256Digest


def _sha256_digest(value: bytes) -> Sha256Digest:
    return Sha256Digest(root=f"sha256:{sha256(value).hexdigest()}")


def _default_artifact_id(envelope_bytes: bytes) -> ArtifactId:
    return ArtifactId(root=f"artifact_{sha256(envelope_bytes).hexdigest()}")


def _record_bytes(record: EventRecord) -> bytes:
    return canonical_json_bytes(
        record.model_dump(mode="json", round_trip=True, warnings="error")
    )


def _record_for_event(event: RunEvent) -> EventRecord:
    return parse_event_record(
        canonical_json_bytes(
            {
                "schema_version": "automarkov.event-record.v1",
                "event": event.model_dump(
                    mode="json",
                    round_trip=True,
                    warnings="error",
                ),
                "event_hash": _event_hash(event),
            }
        )
    )


def _event_head_from_verified(head: VerifiedEventHead) -> EventHead:
    return EventHead(
        run_id=head.run_id.root,
        sequence_no=head.sequence_no,
        event_hash=head.event_hash.root,
    )


def _closed_append_step(record: EventRecord, view: RunProjection) -> RunAppendStep:
    return RunAppendStep.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": "automarkov.run-append-step.v1",
                "event_record": record.model_dump(mode="json"),
                "run_view": view.model_dump(mode="json"),
                "idempotent": False,
            }
        )
    )


def _append_closed_event_batch(
    existing: tuple[EventRecord, ...],
    events: tuple[RunEvent, ...],
    *,
    expected_head: EventHead | None,
    budget_snapshots: Mapping[str, object],
) -> tuple[tuple[EventRecord, ...], RunAppendStep]:
    """一次投影完整因果批次，避免在合法半批次状态上误判。"""

    actual_head = (
        EventHead(
            run_id=existing[-1].event.run_id,
            sequence_no=existing[-1].event.sequence_no,
            event_hash=existing[-1].event_hash,
        )
        if existing
        else None
    )
    run_id = events[0].run_id
    require_expected_head(run_id, expected_head, actual_head)
    records = list(existing)
    previous_hash = (
        actual_head.event_hash if actual_head is not None else "sha256:" + "0" * 64
    )
    for event in events:
        if (
            event.run_id != run_id
            or event.sequence_no != len(records)
            or event.previous_event_hash != previous_hash
        ):
            raise EventSequenceConflictError(run_id, event.sequence_no)
        record = _record_for_event(event)
        records.append(record)
        previous_hash = record.event_hash
    updated = tuple(records)
    view = project_records(updated, budget_snapshots=budget_snapshots)
    return updated, _closed_append_step(updated[-1], view)


def _lifecycle_command_fingerprint(
    command: LifecycleCommand,
) -> str:
    payload = command.model_dump(
        mode="json",
        round_trip=True,
        warnings="error",
    )
    return f"sha256:{sha256(canonical_json_bytes(payload)).hexdigest()}"


def _fresh_commit_receipt(
    result: LifecycleCommitResult,
) -> LifecycleCommitResult:
    return TypeAdapter(LifecycleCommitResult).validate_json(
        result.model_dump_json(warnings="error"),
        strict=True,
    )


def _with_commit_records(
    result: RunAppendStep,
    command: AppendRunEventsCommand,
    *,
    before_head: EventHead | None,
    records: tuple[EventRecord, ...],
    artifact_references: tuple[ArtifactReference, ...] = (),
) -> LifecycleCommitReceipt:
    return LifecycleCommitReceipt.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": "automarkov.lifecycle-commit-receipt.v1",
                "command_id": command.command_id,
                "idempotency_key": command.idempotency_key,
                "command_fingerprint": _lifecycle_command_fingerprint(command),
                "run_id": command.run_id,
                "before_head": (
                    before_head.model_dump(mode="json")
                    if before_head is not None
                    else None
                ),
                "after_head": result.run_view.event_head.model_dump(mode="json"),
                "event_records": [record.model_dump(mode="json") for record in records],
                "artifact_references": [
                    reference.model_dump(mode="json")
                    for reference in artifact_references
                ],
                "run_view": result.run_view.model_dump(mode="json"),
                "process_execution_terminal_record": None,
                "terminal_result": None,
            }
        )
    )


def _terminal_append_result(
    command: CommitTerminalCommand,
    records: tuple[EventRecord, EventRecord],
    before_head: EventHead,
    view: RunProjection,
    process_reference: ArtifactReference,
    terminal_reference: ArtifactReference,
    audit_reference: ArtifactReference,
) -> LifecycleCommitReceipt:
    return LifecycleCommitReceipt.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": "automarkov.lifecycle-commit-receipt.v1",
                "command_id": command.command_id,
                "idempotency_key": command.idempotency_key,
                "command_fingerprint": _lifecycle_command_fingerprint(command),
                "run_id": view.run_id,
                "before_head": before_head.model_dump(mode="json"),
                "after_head": view.event_head.model_dump(mode="json"),
                "event_records": [record.model_dump(mode="json") for record in records],
                "artifact_references": [
                    reference.model_dump(mode="json")
                    for reference in (
                        process_reference,
                        terminal_reference,
                        audit_reference,
                    )
                ],
                "run_view": view.model_dump(mode="json"),
                "process_execution_terminal_record": process_reference.model_dump(
                    mode="json"
                ),
                "terminal_result": terminal_reference.model_dump(mode="json"),
            }
        )
    )


def _active_approval_snapshots(
    records: tuple[EventRecord, ...],
) -> tuple[ApprovalEventSnapshot, ...]:
    revoked_ids = {
        record.event.supersedes_approval_event_id
        for record in records
        if isinstance(record.event, SignedApprovalEvent)
        and record.event.decision == "revoked"
    }
    snapshots = (
        ApprovalEventSnapshot(
            event=EventReference(
                event_id=record.event.event_id,
                sequence_no=record.event.sequence_no,
                event_hash=record.event_hash,
            ),
            validity="valid",
        )
        for record in records
        if isinstance(record.event, SignedApprovalEvent)
        and record.event.decision == "approved"
        and record.event.event_id not in revoked_ids
    )
    return tuple(
        sorted(snapshots, key=lambda item: item.event.event_id.encode("utf-8"))
    )


def _replacement_terminal_result_payload(
    command: CreateReplacementRunCommand,
    transition_result: RunAppendStep,
    process_reference: ArtifactReference,
    approvals: tuple[ApprovalEventSnapshot, ...],
) -> TerminalResult:
    process = command.process_terminal_record
    transition = cast(StateTransitioned, transition_result.event_record.event)
    return TerminalResult.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": "automarkov.terminal-result.v1",
                "signing_domain": "AutoMarkov-TerminalResult-v1",
                "run_id": command.parent_run_id,
                "experiment_id": process.experiment_id,
                "fixed_commit_job_manifest": (
                    command.fixed_commit_job_manifest.model_dump(mode="json")
                ),
                "process_execution_terminal_record": (
                    process_reference.model_dump(mode="json")
                ),
                "process_execution_id": process.process_execution_id,
                "terminal_event": {
                    "event_id": transition.event_id,
                    "sequence_no": transition.sequence_no,
                    "event_hash": transition_result.event_record.event_hash,
                },
                "terminal_snapshot_event_head": {
                    "run_id": command.parent_run_id,
                    "sequence_no": transition.sequence_no,
                    "event_hash": transition_result.event_record.event_hash,
                },
                "terminal_state": "CANCELLED",
                "terminal_reason_code": "run_superseded",
                "payload_outputs": [
                    item.model_dump(mode="json") for item in process.payload_outputs
                ],
                "terminal_time_approvals": [
                    item.model_dump(mode="json") for item in approvals
                ],
                "projector_version": command.projector_version,
                "projector_hash": command.projector_hash,
                "created_at": transition.issued_at,
            }
        )
    )


def _replacement_root_audit_projection_payload(
    command: CreateReplacementRunCommand,
    transition_result: RunAppendStep,
    terminal_reference: ArtifactReference,
    approvals: tuple[ApprovalEventSnapshot, ...],
) -> RunAuditProjection:
    payload: dict[str, object] = {
        "schema_version": "automarkov.run-audit-projection.v1",
        "signing_domain": "AutoMarkov-RunAuditProjection-v1",
        "run_id": command.parent_run_id,
        "experiment_id": command.process_terminal_record.experiment_id,
        "projector_version": command.projector_version,
        "projector_hash": command.projector_hash,
        "as_of_event_head": {
            "run_id": transition_result.run_view.event_head.run_id,
            "sequence_no": transition_result.run_view.event_head.sequence_no,
            "event_hash": transition_result.run_view.event_head.event_hash,
        },
        "previous_projection": None,
        "terminal_result": terminal_reference.model_dump(mode="json"),
        "current_approval_snapshots": [
            item.model_dump(mode="json") for item in approvals
        ],
        "post_terminal_audit_event_references": [],
        "signed_deviations": [],
        "outcome_mask": {
            "e2e_valid": 0,
            "gold_policy_evaluation_valid": 0,
            "q_gate": 0,
        },
    }
    return RunAuditProjection.model_validate_json(
        canonical_json_bytes(
            payload | {"projection_id": run_audit_projection_id(payload)}
        )
    )


def _cross_run_receipt(
    command: CreateReplacementRunCommand | CreateClarificationChildRunCommand,
    *,
    parent_before_head: EventHead,
    parent_records: tuple[EventRecord, ...],
    child_records: tuple[EventRecord, ...],
    parent_view: RunProjection,
    child_view: RunProjection,
    process_reference: ArtifactReference | None = None,
    terminal_reference: ArtifactReference | None = None,
    audit_reference: ArtifactReference | None = None,
    attestation_reference: ArtifactReference | None = None,
) -> CrossRunLifecycleCommitReceipt:
    artifacts = tuple(
        sorted(
            (
                reference
                for reference in (
                    process_reference,
                    terminal_reference,
                    audit_reference,
                    attestation_reference,
                )
                if reference is not None
            ),
            key=lambda item: item.artifact_id.encode("utf-8"),
        )
    )
    return CrossRunLifecycleCommitReceipt.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": ("automarkov.cross-run-lifecycle-commit-receipt.v1"),
                "command_type": command.command_type,
                "command_id": command.command_id,
                "idempotency_key": command.idempotency_key,
                "command_fingerprint": _lifecycle_command_fingerprint(command),
                "parent_run_id": command.parent_run_id,
                "child_run_id": command.child_run_id,
                "parent_before_head": parent_before_head.model_dump(mode="json"),
                "parent_after_head": parent_view.event_head.model_dump(mode="json"),
                "child_after_head": child_view.event_head.model_dump(mode="json"),
                "parent_event_records": [
                    item.model_dump(mode="json") for item in parent_records
                ],
                "child_event_records": [
                    item.model_dump(mode="json") for item in child_records
                ],
                "artifact_references": [
                    item.model_dump(mode="json") for item in artifacts
                ],
                "parent_run_view": parent_view.model_dump(mode="json"),
                "child_run_view": child_view.model_dump(mode="json"),
                "process_execution_terminal_record": (
                    process_reference.model_dump(mode="json")
                    if process_reference is not None
                    else None
                ),
                "terminal_result": (
                    terminal_reference.model_dump(mode="json")
                    if terminal_reference is not None
                    else None
                ),
                "run_audit_projection": (
                    audit_reference.model_dump(mode="json")
                    if audit_reference is not None
                    else None
                ),
                "execution_attestation": (
                    attestation_reference.model_dump(mode="json")
                    if attestation_reference is not None
                    else None
                ),
            }
        )
    )


def _artifact_reference(result: ArtifactPutResult) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=result.artifact_id.root,
        payload_hash=result.payload_hash.root,
    )


def _validated_projection_query(
    run_id: RunId,
    as_of: VerifiedEventHead,
    projector_version: str,
    projector_hash: Sha256Digest,
) -> RunProjectionRequest:
    if (
        type(run_id) is not RunId
        or type(as_of) is not VerifiedEventHead
        or type(projector_version) is not str
        or type(projector_hash) is not Sha256Digest
        or as_of.run_id != run_id
        or set(as_of.__dict__) != {"run_id", "sequence_no", "event_hash"}
    ):
        raise RunProjectorIdentityError(str(projector_version))
    return validate_projection_request(
        {
            "schema_version": "automarkov.run-projection-request.v1",
            "run_id": run_id.root,
            "as_of_sequence_no": as_of.sequence_no,
            "as_of_event_head_hash": as_of.event_hash.root,
            "projector_version": projector_version,
            "projector_hash": projector_hash.root,
        }
    )


def _signed_event_nonce(event: RunEvent) -> str | None:
    if isinstance(event, _SIGNED_EVENT_TYPES):
        return event.nonce_b64url
    return None


def _signed_event_slot(event: RunEvent) -> tuple[str, str, int] | None:
    if isinstance(event, _SIGNED_EVENT_TYPES):
        return event.signing_key_id, event.run_id, event.sequence_no
    return None


def _event_artifact_references(
    event: RunEvent,
) -> tuple[ArtifactReference, ...]:
    references: dict[str, ArtifactReference] = {}

    def visit(value: object) -> None:
        if isinstance(value, ArtifactReference):
            existing = references.get(value.artifact_id)
            if existing is not None and existing != value:
                raise TerminalProvenanceError(event.run_id)
            references[value.artifact_id] = value
            return
        if isinstance(value, BaseModel):
            field_names = type(value).model_fields
            for field_name in field_names:
                if not field_name.endswith("_artifact_id"):
                    continue
                payload_hash_field = (
                    field_name.removesuffix("_artifact_id") + "_payload_hash"
                )
                if payload_hash_field not in field_names:
                    continue
                artifact_id = getattr(value, field_name)
                payload_hash = getattr(value, payload_hash_field)
                if artifact_id is not None and payload_hash is not None:
                    visit(
                        ArtifactReference(
                            artifact_id=artifact_id,
                            payload_hash=payload_hash,
                        )
                    )
            for field_name in field_names:
                visit(getattr(value, field_name))
            return
        if type(value) is tuple:
            for item in value:
                visit(item)

    visit(event)
    if isinstance(event, RunCreated):
        visit(
            ArtifactReference(
                artifact_id=event.run_manifest_artifact_id,
                payload_hash=event.run_manifest_payload_hash,
            )
        )
    if isinstance(event, StateTransitioned):
        visit(
            ArtifactReference(
                artifact_id=event.budget_snapshot_artifact_id,
                payload_hash=event.budget_snapshot_payload_hash,
            )
        )
        if event.gate_report_artifact_id is not None:
            visit(
                ArtifactReference(
                    artifact_id=event.gate_report_artifact_id,
                    payload_hash=cast(str, event.gate_report_payload_hash),
                )
            )
    if isinstance(event, (GateOmittedByDesign, ExecutionTopologySubstituted)):
        visit(
            ArtifactReference(
                artifact_id=event.ablation_execution_plan_artifact_id,
                payload_hash=event.ablation_execution_plan_hash,
            )
        )
    return tuple(
        references[key]
        for key in sorted(references, key=lambda item: item.encode("utf-8"))
    )


def _event_unhashed_artifact_ids(event: RunEvent) -> tuple[str, ...]:
    artifact_ids: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, ArtifactReference):
            return
        if isinstance(value, BaseModel):
            for field_name in type(value).model_fields:
                field_value = getattr(value, field_name)
                if field_name.endswith("_artifact_ids") and type(field_value) is tuple:
                    artifact_ids.update(cast(tuple[str, ...], field_value))
                else:
                    visit(field_value)
            return
        if type(value) is tuple:
            for item in value:
                visit(item)

    visit(event)
    if isinstance(event, GateOmittedByDesign):
        artifact_ids.add(event.task_card_artifact_id)
    return tuple(sorted(artifact_ids, key=lambda item: item.encode("utf-8")))


def _terminal_result_payload(
    command: CommitTerminalCommand,
    transition_result: RunAppendStep,
    process_reference: ArtifactReference,
) -> TerminalResult:
    process = command.process_terminal_record
    event = cast(StateTransitioned, transition_result.event_record.event)
    return TerminalResult.model_validate_json(
        canonical_json_bytes(
            {
                "schema_version": "automarkov.terminal-result.v1",
                "signing_domain": "AutoMarkov-TerminalResult-v1",
                "run_id": command.run_id,
                "experiment_id": process.experiment_id,
                "fixed_commit_job_manifest": command.fixed_commit_job_manifest.model_dump(
                    mode="json"
                ),
                "process_execution_terminal_record": process_reference.model_dump(
                    mode="json"
                ),
                "process_execution_id": process.process_execution_id,
                "terminal_event": {
                    "event_id": event.event_id,
                    "sequence_no": event.sequence_no,
                    "event_hash": transition_result.event_record.event_hash,
                },
                "terminal_snapshot_event_head": {
                    "run_id": command.run_id,
                    "sequence_no": event.sequence_no,
                    "event_hash": transition_result.event_record.event_hash,
                },
                "terminal_state": transition_result.run_view.state.value,
                "terminal_reason_code": event.reason_code,
                "payload_outputs": [
                    item.model_dump(mode="json") for item in process.payload_outputs
                ],
                "terminal_time_approvals": [
                    item.model_dump(mode="json")
                    for item in command.terminal_time_approvals
                ],
                "projector_version": command.projector_version,
                "projector_hash": command.projector_hash,
                "created_at": command.created_at,
            }
        )
    )


def _root_audit_projection_payload(
    command: CommitTerminalCommand,
    transition_result: RunAppendStep,
    terminal_reference: ArtifactReference,
) -> RunAuditProjection:
    process = command.process_terminal_record
    payload: dict[str, object] = {
        "schema_version": "automarkov.run-audit-projection.v1",
        "signing_domain": "AutoMarkov-RunAuditProjection-v1",
        "run_id": command.run_id,
        "experiment_id": process.experiment_id,
        "projector_version": command.projector_version,
        "projector_hash": command.projector_hash,
        "as_of_event_head": {
            "run_id": transition_result.run_view.event_head.run_id,
            "sequence_no": transition_result.run_view.event_head.sequence_no,
            "event_hash": transition_result.run_view.event_head.event_hash,
        },
        "previous_projection": None,
        "terminal_result": terminal_reference.model_dump(mode="json"),
        "current_approval_snapshots": [
            item.model_dump(mode="json") for item in command.terminal_time_approvals
        ],
        "post_terminal_audit_event_references": [],
        "signed_deviations": [],
        "outcome_mask": {
            "e2e_valid": 0,
            "gold_policy_evaluation_valid": 0,
            "q_gate": 0,
        },
    }
    return RunAuditProjection.model_validate_json(
        canonical_json_bytes(
            payload | {"projection_id": run_audit_projection_id(payload)}
        )
    )


def _next_audit_projection_payload(
    previous_reference: ArtifactReference,
    previous: RunAuditProjection,
    terminal_reference: ArtifactReference,
    view: RunProjection,
    events: tuple[RunEvent, ...],
) -> RunAuditProjection:
    approvals = [
        item.model_dump(mode="json") for item in previous.current_approval_snapshots
    ]
    e2e_valid = (
        0
        if any(
            isinstance(event, SignedApprovalEvent) and event.decision == "revoked"
            for event in events
        )
        else previous.outcome_mask.e2e_valid
    )
    for event in events:
        if isinstance(event, SignedApprovalEvent) and event.decision == "revoked":
            approvals = [
                (
                    item | {"validity": "revoked"}
                    if cast(dict[str, object], item["event"])["event_id"]
                    == event.supersedes_approval_event_id
                    else item
                )
                for item in approvals
            ]
    audit_events = [
        item.model_dump(mode="json")
        for item in previous.post_terminal_audit_event_references
    ] + [
        {
            "event_id": event.event_id,
            "sequence_no": event.sequence_no,
            "event_hash": _event_hash(event),
        }
        for event in events
    ]
    payload: dict[str, object] = {
        "schema_version": "automarkov.run-audit-projection.v1",
        "signing_domain": "AutoMarkov-RunAuditProjection-v1",
        "run_id": previous.run_id,
        "experiment_id": previous.experiment_id,
        "projector_version": previous.projector_version,
        "projector_hash": previous.projector_hash,
        "as_of_event_head": {
            "run_id": view.event_head.run_id,
            "sequence_no": view.event_head.sequence_no,
            "event_hash": view.event_head.event_hash,
        },
        "previous_projection": previous_reference.model_dump(mode="json"),
        "terminal_result": terminal_reference.model_dump(mode="json"),
        "current_approval_snapshots": approvals,
        "post_terminal_audit_event_references": audit_events,
        "signed_deviations": [
            item.model_dump(mode="json") for item in previous.signed_deviations
        ],
        "outcome_mask": {
            "e2e_valid": e2e_valid,
            "gold_policy_evaluation_valid": (
                0
                if e2e_valid == 0
                else previous.outcome_mask.gold_policy_evaluation_valid
            ),
            "q_gate": 0 if e2e_valid == 0 else previous.outcome_mask.q_gate,
        },
    }
    return RunAuditProjection.model_validate_json(
        canonical_json_bytes(
            payload | {"projection_id": run_audit_projection_id(payload)}
        )
    )


def _decorate_projection(
    view: RunProjection,
    terminal_reference: ArtifactReference | None,
    audit_reference: ArtifactReference | None,
) -> RunProjection:
    if terminal_reference is None:
        return view
    payload = view.model_dump(mode="json", round_trip=True, warnings="error")
    payload["terminal_result"] = terminal_reference.model_dump(mode="json")
    payload["run_audit_projection"] = (
        audit_reference.model_dump(mode="json") if audit_reference is not None else None
    )
    return RunProjection.model_validate_json(canonical_json_bytes(payload))


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
        event_authenticator: EventAuthenticator | None = None,
        command_authority: CommandAuthority | None = None,
    ) -> None:
        self._schemas = schema_registry or _default_schema_registry()
        self._schemas.freeze()
        self._event_schema_contracts = default_event_schema_registry().snapshot()
        self._event_authenticator = event_authenticator or EventAuthenticator()
        self._command_authority = command_authority
        self._lock = RLock()

    def _load_run_event_records(self, run_id: str) -> tuple[EventRecord, ...]:
        """由具体存储实现加载并完整验证 Run 事件。"""

        raise NotImplementedError

    def _authenticate_command_context(
        self,
        command: LifecycleCommand,
        context: AuthenticatedCommandContext,
    ) -> None:
        authority = self._command_authority
        if isinstance(command, CreateReplacementRunCommand):
            process_ids = {command.process_terminal_record.process_execution_id}
            anchor_events: tuple[RunEvent, ...] = (
                command.run_superseded_event,
                command.parent_terminal_transition,
            )
            child_events: tuple[RunEvent, ...] = (
                command.replacement_run_created_event,
            )
            anchor_run_id = command.parent_run_id
        elif isinstance(command, CreateClarificationChildRunCommand):
            process_ids = {None}
            anchor_events = ()
            child_events = (command.clarification_child_run_created_event,)
            anchor_run_id = command.parent_run_id
        else:
            process_ids = {
                getattr(event, "actor_process_execution_id", None)
                for event in command.events
            }
            anchor_events = tuple(command.events)
            child_events = ()
            anchor_run_id = command.run_id
        if (
            authority is None
            or not authority.verifies(context)
            or context.principal_id != command.actor_principal_id
            or process_ids != {context.process_execution_id}
        ):
            raise CommandAuthenticationError(command.actor_principal_id)
        if anchor_events and isinstance(anchor_events[0], RunCreated):
            root_event = anchor_events[0]
        else:
            anchor_records = self._load_run_event_records(anchor_run_id)
            if not anchor_records:
                raise UnknownRunError(anchor_run_id)
            root_event = anchor_records[0].event
        anchor_context = self._resolve_run_event_security_context(root_event)
        received_at = datetime.fromisoformat(context.received_at)
        command_time = datetime.fromisoformat(command.issued_at)
        maximum_skew_seconds = anchor_context.max_clock_skew_ms / 1_000
        if abs((received_at - command_time).total_seconds()) > maximum_skew_seconds:
            raise CommandAuthenticationError(command.actor_principal_id)
        for event in (*anchor_events, *child_events):
            security_context = (
                self._resolve_run_event_security_context(event)
                if event in child_events
                else anchor_context
            )
            event_time = datetime.fromisoformat(event.issued_at)
            maximum_skew_seconds = security_context.max_clock_skew_ms / 1_000
            if abs((received_at - event_time).total_seconds()) > maximum_skew_seconds:
                raise CommandAuthenticationError(command.actor_principal_id)
            if isinstance(event, _SIGNED_EVENT_TYPES):
                key = security_context.signing_key(event.signing_key_id)
                if (
                    received_at < datetime.fromisoformat(key.not_before)
                    or received_at >= datetime.fromisoformat(key.not_after)
                    or key.revoked_at is not None
                    and received_at >= datetime.fromisoformat(key.revoked_at)
                ):
                    raise CommandAuthenticationError(command.actor_principal_id)

    def _terminal_commit_failpoint(self, stage: str) -> None:
        """供原子事务合同测试注入 terminal write failure。"""

    def _post_terminal_commit_failpoint(self, stage: str) -> None:
        """供原子事务合同测试注入 post-terminal write failure。"""

    def _cross_run_commit_failpoint(self, stage: str) -> None:
        """供原子事务合同测试注入 cross-run write failure。"""

    def _artifact_payload(
        self,
        reference: ArtifactReference,
        *,
        artifact_type: str | None = None,
    ) -> dict[str, object]:
        result = self.get(ArtifactId(root=reference.artifact_id))
        payload = result.payload_document.model_dump(mode="json")["payload"]
        if (
            result.envelope.payload_hash != reference.payload_hash
            or artifact_type is not None
            and result.envelope.artifact_type != artifact_type
            or type(payload) is not dict
        ):
            raise TerminalProvenanceError(reference.artifact_id)
        return cast(dict[str, object], payload)

    @staticmethod
    def _payload_values(payload: object, key: str) -> tuple[object, ...]:
        values: list[object] = []
        pending = [payload]
        while pending:
            current = pending.pop()
            if type(current) is dict:
                mapping = cast(dict[str, object], current)
                if key in mapping:
                    values.append(mapping[key])
                pending.extend(mapping.values())
            elif type(current) is list:
                pending.extend(cast(list[object], current))
        return tuple(values)

    @classmethod
    def _require_payload_value(
        cls,
        payload: object,
        key: str,
        expected: object,
        subject: str,
    ) -> None:
        if expected not in cls._payload_values(payload, key):
            raise TerminalProvenanceError(subject)

    @classmethod
    def _require_payload_reference(
        cls,
        payload: object,
        reference: ArtifactReference,
        subject: str,
    ) -> None:
        pending = [payload]
        expected = reference.model_dump(mode="json")
        while pending:
            current = pending.pop()
            if current == expected:
                return
            if type(current) is dict:
                pending.extend(cast(dict[str, object], current).values())
            elif type(current) is list:
                pending.extend(cast(list[object], current))
        raise TerminalProvenanceError(subject)

    @staticmethod
    def _verify_signed_artifact_payload(
        payload: dict[str, object],
        security_context: RunEventSecurityContext,
        *,
        principal_id: str,
        signing_key_id: str,
        received_at: str,
        require_fresh: bool,
    ) -> None:
        if (
            payload.get("authority_principal_id", principal_id) != principal_id
            or payload.get("principal_id", principal_id) != principal_id
            or payload.get("signing_key_id") != signing_key_id
            or payload.get("authority_status", "active") != "active"
            or type(payload.get("issued_at")) is not str
            or type(payload.get("signature_b64url")) is not str
        ):
            raise TerminalProvenanceError(principal_id)
        issued_at = cast(str, payload["issued_at"])
        issued_time = datetime.fromisoformat(issued_at)
        received_time = datetime.fromisoformat(received_at)
        key = security_context.signing_key(signing_key_id)
        if (
            key.principal_id != principal_id
            or issued_time < datetime.fromisoformat(key.not_before)
            or issued_time >= datetime.fromisoformat(key.not_after)
            or key.revoked_at is not None
            and received_time >= datetime.fromisoformat(key.revoked_at)
            or require_fresh
            and abs((received_time - issued_time).total_seconds())
            > security_context.max_clock_skew_ms / 1_000
        ):
            raise TerminalProvenanceError(principal_id)
        signature_b64url = cast(str, payload["signature_b64url"])
        try:
            signature = base64.urlsafe_b64decode(signature_b64url + "==")
            unsigned = dict(payload)
            del unsigned["signature_b64url"]
            Ed25519PublicKey.from_public_bytes(key.public_key_bytes()).verify(
                signature,
                canonical_json_bytes(unsigned),
            )
        except (InvalidSignature, KeyError, ValueError) as error:
            raise TerminalProvenanceError(principal_id) from error

    @staticmethod
    def _record_at_reference(
        records: tuple[EventRecord, ...],
        reference: EventReference,
        run_id: str,
    ) -> EventRecord:
        if reference.sequence_no >= len(records):
            raise TerminalProvenanceError(run_id)
        record = records[reference.sequence_no]
        if (
            record.event.event_id != reference.event_id
            or record.event_hash != reference.event_hash
        ):
            raise TerminalProvenanceError(run_id)
        return record

    def _validate_replacement_contract(
        self,
        command: CreateReplacementRunCommand,
        parent_records: tuple[EventRecord, ...],
        parent_projection: RunProjection,
        context: AuthenticatedCommandContext,
    ) -> tuple[RunEventSecurityContext, RunEventSecurityContext]:
        if (
            parent_projection.state != command.expected_parent_state
            or parent_projection.event_head
            != _event_head_from_verified(command.expected_parent_head)
            or parent_projection.state in TERMINAL_STATES
            or not parent_records
        ):
            raise EventHeadConflictError(command.parent_run_id)
        parent_root = parent_records[0].event
        parent_context = self._resolve_run_event_security_context(parent_root)
        root_manifest = ArtifactReference(
            artifact_id=cast(
                str,
                getattr(parent_root, "run_manifest_artifact_id", ""),
            ),
            payload_hash=cast(
                str,
                getattr(parent_root, "run_manifest_payload_hash", ""),
            ),
        )
        if root_manifest != command.old_run_manifest:
            raise TerminalProvenanceError(command.parent_run_id)

        old_manifest = self._artifact_payload(
            command.old_run_manifest,
            artifact_type="run_manifest",
        )
        child_manifest = self._artifact_payload(
            command.child_run_manifest,
            artifact_type="run_manifest",
        )
        policy = self._artifact_payload(
            command.replacement_policy,
            artifact_type="replacement_policy",
        )
        slot_decision = self._artifact_payload(
            command.slot_decision,
            artifact_type="slot_decision",
        )
        job_manifest = self._artifact_payload(
            command.fixed_commit_job_manifest,
            artifact_type="job_manifest",
        )
        parent_ordinal = parent_context.root_ordinal
        child_ordinal = command.run_superseded_event.replacement_ordinal
        if child_ordinal != parent_ordinal + 1:
            raise TerminalProvenanceError(command.child_run_id)
        for payload, subject in (
            (old_manifest, command.old_run_manifest.artifact_id),
            (child_manifest, command.child_run_manifest.artifact_id),
        ):
            self._require_payload_reference(
                payload,
                command.replacement_policy,
                subject,
            )
        self._require_payload_value(
            old_manifest,
            "replacement_ordinal",
            parent_ordinal,
            command.old_run_manifest.artifact_id,
        )
        for key, expected in (
            ("replacement_ordinal", child_ordinal),
            ("parent_run_id", command.parent_run_id),
            (
                "parent_run_superseded_event_id",
                command.run_superseded_event.event_id,
            ),
            ("supersession_cause", command.run_superseded_event.supersession_cause),
        ):
            self._require_payload_value(
                child_manifest,
                key,
                expected,
                command.child_run_manifest.artifact_id,
            )

        child_context = self._resolve_run_event_security_context(
            command.replacement_run_created_event
        )
        if (
            child_context.root_ordinal != child_ordinal
            or child_context.run_id != command.child_run_id
            or child_context.experiment_id != parent_context.experiment_id
            or child_context.signing_key(
                command.replacement_run_created_event.signing_key_id
            ).model_dump(mode="json")
            != parent_context.signing_key(
                command.run_superseded_event.signing_key_id
            ).model_dump(mode="json")
        ):
            raise TerminalProvenanceError(command.child_run_id)
        self._verify_signed_artifact_payload(
            policy,
            parent_context,
            principal_id=command.actor_principal_id,
            signing_key_id=command.run_superseded_event.signing_key_id,
            received_at=context.received_at,
            require_fresh=False,
        )
        for key, expected in (
            ("root_ordinal", parent_ordinal),
            ("child_ordinal_increment", 1),
            ("maximum_child_count", 1),
        ):
            self._require_payload_value(
                policy,
                key,
                expected,
                command.replacement_policy.artifact_id,
            )
        allowed_causes = self._payload_values(policy, "allowed_causes")
        eligibility = self._payload_values(policy, "eligibility_by_cause")
        cause = command.run_superseded_event.supersession_cause
        if not any(
            type(value) is list and cause in value for value in allowed_causes
        ) or not any(
            type(value) is dict
            and cast(dict[str, object], value).get(cause)
            == command.replacement_eligibility
            for value in eligibility
        ):
            raise TerminalProvenanceError(command.replacement_policy.artifact_id)

        for key, expected in (
            ("parent_run_id", command.parent_run_id),
            ("child_run_id", command.child_run_id),
            ("replacement_eligibility", command.replacement_eligibility),
        ):
            self._require_payload_value(
                slot_decision,
                key,
                expected,
                command.slot_decision.artifact_id,
            )
        self._require_payload_reference(
            slot_decision,
            command.replacement_policy,
            command.slot_decision.artifact_id,
        )
        for reference in (
            command.old_run_manifest,
            command.child_run_manifest,
            command.replacement_policy,
            command.slot_decision,
        ):
            self._require_payload_reference(
                job_manifest,
                reference,
                command.fixed_commit_job_manifest.artifact_id,
            )
        for key, expected in (
            ("parent_run_id", command.parent_run_id),
            ("child_run_id", command.child_run_id),
            ("run_superseded_event_id", command.run_superseded_event.event_id),
            (
                "replacement_run_created_event_id",
                command.replacement_run_created_event.event_id,
            ),
        ):
            self._require_payload_value(
                job_manifest,
                key,
                expected,
                command.fixed_commit_job_manifest.artifact_id,
            )

        prerequisite = command.cause_prerequisite
        if isinstance(prerequisite, RuntimeReplacementPrerequisite):
            active_wait = parent_projection.waiting
            prerequisite_record = self._record_at_reference(
                parent_records,
                prerequisite.failed_waiting_event,
                command.parent_run_id,
            )
            waiting = prerequisite_record.event
            if (
                active_wait is None
                or active_wait.event != prerequisite.failed_waiting_event
                or active_wait.wait_kind != "runtime"
                or active_wait.gate_id != prerequisite.failed_readiness_gate_id
                or active_wait.dependency_identity_hash
                != prerequisite.old_dependency_identity_hash
                or not isinstance(waiting, WaitingRuntime)
                or waiting.failed_readiness_gate_id
                != prerequisite.failed_readiness_gate_id
                or waiting.dependency_identity_hash
                != prerequisite.old_dependency_identity_hash
            ):
                raise TerminalProvenanceError(command.parent_run_id)
        else:
            if parent_projection.state.value not in _CANDIDATE_FROZEN_STATES:
                raise TerminalProvenanceError(command.parent_run_id)
            revocation_record = self._record_at_reference(
                parent_records,
                prerequisite.revocation_event,
                command.parent_run_id,
            )
            approval_record = self._record_at_reference(
                parent_records,
                prerequisite.revoked_approval_event,
                command.parent_run_id,
            )
            revocation = revocation_record.event
            approval = approval_record.event
            if (
                not isinstance(revocation, SignedApprovalEvent)
                or revocation.decision != "revoked"
                or not isinstance(approval, SignedApprovalEvent)
                or approval.decision != "approved"
                or revocation.supersedes_approval_event_id != approval.event_id
                or revocation.artifact != prerequisite.artifact
                or approval.artifact != prerequisite.artifact
                or any(
                    isinstance(record.event, SignedApprovalEvent)
                    and record.event.artifact == prerequisite.artifact
                    and record.event.sequence_no > revocation.sequence_no
                    for record in parent_records
                )
            ):
                raise TerminalProvenanceError(command.parent_run_id)

        prerequisite_reference = (
            prerequisite.failed_waiting_event
            if isinstance(prerequisite, RuntimeReplacementPrerequisite)
            else prerequisite.revocation_event
        )
        for key, expected in (
            ("prerequisite_event_id", prerequisite_reference.event_id),
            ("prerequisite_event_hash", prerequisite_reference.event_hash),
        ):
            self._require_payload_value(
                job_manifest,
                key,
                expected,
                command.fixed_commit_job_manifest.artifact_id,
            )

        process = command.process_terminal_record
        if not any(
            capability.principal_id == process.principal_id
            and capability.process_execution_id == process.process_execution_id
            and "StateTransitioned" in capability.allowed_event_types
            for capability in parent_context.actor_capabilities
        ):
            raise TerminalProvenanceError(process.process_execution_id)
        attestation_payload = command.execution_attestation.model_dump(
            mode="json",
            round_trip=True,
            warnings="error",
        )
        self._verify_signed_artifact_payload(
            cast(dict[str, object], attestation_payload),
            parent_context,
            principal_id=command.actor_principal_id,
            signing_key_id=command.execution_attestation.signing_key_id,
            received_at=context.received_at,
            require_fresh=True,
        )
        return parent_context, child_context

    def _validate_clarification_contract(
        self,
        command: CreateClarificationChildRunCommand,
        parent_records: tuple[EventRecord, ...],
        parent_projection: RunProjection,
        context: AuthenticatedCommandContext,
    ) -> None:
        if (
            parent_projection.state.value != "CLARIFICATION_REQUIRED"
            or parent_projection.event_head
            != _event_head_from_verified(command.expected_parent_head)
            or parent_projection.terminal_result != command.parent_terminal_result
            or parent_projection.terminal_snapshot_head
            != EventHead(
                run_id=command.parent_terminal_snapshot_event_head.run_id.root,
                sequence_no=(command.parent_terminal_snapshot_event_head.sequence_no),
                event_hash=(
                    command.parent_terminal_snapshot_event_head.event_hash.root
                ),
            )
        ):
            raise EventHeadConflictError(command.parent_run_id)
        terminal_payload = self._artifact_payload(
            command.parent_terminal_result,
            artifact_type="terminal_result",
        )
        terminal = TerminalResult.model_validate_json(
            canonical_json_bytes(terminal_payload)
        )
        if (
            terminal.run_id != command.parent_run_id
            or terminal.terminal_state != "CLARIFICATION_REQUIRED"
            or terminal.terminal_snapshot_event_head
            != command.parent_terminal_snapshot_event_head
        ):
            raise TerminalProvenanceError(command.parent_run_id)
        terminal_sequence = terminal.terminal_event.sequence_no
        if terminal_sequence < 1 or terminal_sequence >= len(parent_records):
            raise TerminalProvenanceError(command.parent_run_id)
        cause = parent_records[terminal_sequence - 1].event
        if (
            not isinstance(cause, ClarificationRequested)
            or cause.result != command.parent_clarification_result
        ):
            raise TerminalProvenanceError(command.parent_run_id)
        self._artifact_payload(command.parent_clarification_result)
        answer = self._artifact_payload(
            command.signed_answer_bundle,
            artifact_type="signed_answer_bundle",
        )
        policy = self._artifact_payload(
            command.continuation_policy,
            artifact_type="clarification_continuation_policy",
        )
        child_manifest = self._artifact_payload(
            command.child_run_manifest,
            artifact_type="run_manifest",
        )
        parent_context = self._resolve_run_event_security_context(
            parent_records[0].event
        )
        child_context = self._resolve_run_event_security_context(
            command.clarification_child_run_created_event
        )
        child_event = command.clarification_child_run_created_event
        child_ordinal = child_event.clarification_continuation_ordinal
        if (
            child_ordinal != parent_context.root_ordinal + 1
            or child_context.experiment_id != parent_context.experiment_id
            or child_context.signing_key(child_event.signing_key_id).model_dump(
                mode="json"
            )
            != parent_context.signing_key(child_event.signing_key_id).model_dump(
                mode="json"
            )
        ):
            raise TerminalProvenanceError(command.child_run_id)
        for reference in (
            command.parent_clarification_result,
            command.parent_terminal_result,
            command.signed_answer_bundle,
            command.continuation_policy,
        ):
            self._require_payload_reference(
                child_manifest,
                reference,
                command.child_run_manifest.artifact_id,
            )
        for key, expected in (
            ("parent_run_id", command.parent_run_id),
            ("clarification_continuation_ordinal", child_ordinal),
        ):
            self._require_payload_value(
                child_manifest,
                key,
                expected,
                command.child_run_manifest.artifact_id,
            )
        self._verify_signed_artifact_payload(
            policy,
            parent_context,
            principal_id=command.actor_principal_id,
            signing_key_id=child_event.signing_key_id,
            received_at=context.received_at,
            require_fresh=False,
        )
        answer_principal = answer.get("principal_id")
        answer_key = answer.get("signing_key_id")
        if type(answer_principal) is not str or type(answer_key) is not str:
            raise TerminalProvenanceError(command.signed_answer_bundle.artifact_id)
        self._verify_signed_artifact_payload(
            answer,
            parent_context,
            principal_id=answer_principal,
            signing_key_id=answer_key,
            received_at=context.received_at,
            require_fresh=False,
        )
        for key, expected in (
            ("child_ordinal_increment", 1),
            ("maximum_child_count", 1),
            ("experiment_eligibility", "nonconfirmatory"),
            ("budget_reset_rule", "fresh_child_budget"),
            ("runtime_reset_rule", "revalidate_runtime"),
        ):
            self._require_payload_value(
                policy,
                key,
                expected,
                command.continuation_policy.artifact_id,
            )
        allowed_answer_kinds = self._payload_values(
            policy,
            "allowed_answer_artifact_kinds",
        )
        if not any(
            type(value) is list and "signed_answer_bundle" in value
            for value in allowed_answer_kinds
        ):
            raise TerminalProvenanceError(command.continuation_policy.artifact_id)

    def _parse_verified_event_records(
        self,
        raw_records: tuple[bytes, ...],
    ) -> tuple[EventRecord, ...]:
        records = tuple(parse_event_record(raw) for raw in raw_records)
        if not records:
            return records
        security_context = self._resolve_run_event_security_context(records[0].event)
        for record in records:
            self._event_authenticator.authenticate(record.event, security_context)
        return records

    def _resolve_run_event_security_context(
        self,
        root_event: RunEvent,
    ) -> RunEventSecurityContext:
        if isinstance(root_event, RunCreated):
            manifest_artifact_id = root_event.run_manifest_artifact_id
            manifest_payload_hash = root_event.run_manifest_payload_hash
            principal_id = root_event.creation_principal_id
            expected_ordinal = 0
        elif isinstance(root_event, ReplacementRunCreated):
            manifest_artifact_id = root_event.run_manifest_artifact_id
            manifest_payload_hash = root_event.run_manifest_payload_hash
            principal_id = root_event.replacement_authority_principal_id
            expected_ordinal = root_event.replacement_ordinal
        elif isinstance(root_event, ClarificationChildRunCreated):
            manifest_artifact_id = root_event.run_manifest_artifact_id
            manifest_payload_hash = root_event.run_manifest_payload_hash
            principal_id = root_event.continuation_authority_principal_id
            expected_ordinal = root_event.clarification_continuation_ordinal
        else:
            raise TerminalProvenanceError(root_event.run_id)
        manifest = self.get(ArtifactId(root=manifest_artifact_id))
        if (
            manifest.envelope.artifact_type != "run_manifest"
            or manifest.envelope.payload_hash != manifest_payload_hash
            or manifest.envelope.created_by != principal_id
        ):
            raise TerminalProvenanceError(root_event.run_id)
        payload = manifest.payload_document.model_dump(mode="json")["payload"]
        if type(payload) is not dict or "event_security_context" not in payload:
            raise TerminalProvenanceError(root_event.run_id)
        try:
            context = RunEventSecurityContext.model_validate_json(
                canonical_json_bytes(payload["event_security_context"])
            )
        except (TypeError, ValueError) as error:
            raise TerminalProvenanceError(root_event.run_id) from error
        if (
            context.run_id != root_event.run_id
            or context.experiment_id != root_event.experiment_id
            or context.root_ordinal != expected_ordinal
            or context.run_creation.creation_principal_id != principal_id
            or context.run_creation.signing_key_id != root_event.signing_key_id
        ):
            raise TerminalProvenanceError(root_event.run_id)
        for reference in (
            context.creation_policy,
            context.approval.policy_contract,
        ):
            result = self.get(ArtifactId(root=reference.artifact_id))
            if result.envelope.payload_hash != reference.payload_hash:
                raise TerminalProvenanceError(root_event.run_id)
        return context

    def _authenticate_event(
        self,
        event: RunEvent,
        existing: tuple[EventRecord, ...],
    ) -> None:
        root_event = existing[0].event if existing else event
        context = self._resolve_run_event_security_context(root_event)
        self._event_authenticator.authenticate(event, context)

    def get(self, artifact_id: ArtifactId) -> ArtifactBytesResult:
        raise NotImplementedError

    def _budget_snapshots_for_events(
        self,
        events: tuple[RunEvent, ...],
    ) -> dict[str, object]:
        snapshots: dict[str, object] = {}
        for event in events:
            if not isinstance(event, StateTransitioned):
                continue
            result = self.get(ArtifactId(root=event.budget_snapshot_artifact_id))
            if result.envelope.payload_hash != event.budget_snapshot_payload_hash:
                raise TerminalProvenanceError(event.run_id)
            snapshots[event.budget_snapshot_artifact_id] = (
                result.payload_document.model_dump(mode="json")["payload"]
            )
        return snapshots

    def _project_run_records(
        self,
        records: tuple[EventRecord, ...],
        *,
        as_of_head: EventHead | None = None,
    ) -> RunProjection:
        return project_records(
            records,
            as_of_head=as_of_head,
            budget_snapshots=self._budget_snapshots_for_events(
                tuple(record.event for record in records)
            ),
        )

    def _require_terminal_approvals(
        self,
        records: tuple[EventRecord, ...],
        references: tuple[ApprovalEventSnapshot, ...],
    ) -> None:
        indexed = {record.event.event_id: record for record in records}
        revoked_approval_ids = {
            record.event.supersedes_approval_event_id
            for record in records
            if isinstance(record.event, SignedApprovalEvent)
            and record.event.decision == "revoked"
        }
        for value in references:
            reference = value.event
            record = indexed.get(reference.event_id)
            if (
                value.validity != "valid"
                or record is None
                or record.event_hash != reference.event_hash
                or record.event.sequence_no != reference.sequence_no
                or not isinstance(record.event, SignedApprovalEvent)
                or record.event.decision != "approved"
                or reference.event_id in revoked_approval_ids
            ):
                raise TerminalProvenanceError(records[0].event.run_id)

    @staticmethod
    def _verify_receipt_records(
        receipt: LifecycleCommitReceipt,
        records: tuple[EventRecord, ...],
    ) -> None:
        if any(
            record.event.sequence_no >= len(records)
            or records[record.event.sequence_no] != record
            for record in receipt.event_records
        ):
            raise ArtifactIntegrityError(f"lifecycle-receipt:{receipt.command_id}")
        if receipt.before_head is not None:
            before_sequence = receipt.before_head.sequence_no
            if (
                before_sequence >= len(records)
                or records[before_sequence].event_hash != receipt.before_head.event_hash
            ):
                raise ArtifactIntegrityError(f"lifecycle-receipt:{receipt.command_id}")
        after_sequence = receipt.after_head.sequence_no
        if (
            after_sequence >= len(records)
            or records[after_sequence].event_hash != receipt.after_head.event_hash
        ):
            raise ArtifactIntegrityError(f"lifecycle-receipt:{receipt.command_id}")

    def _verify_receipt_artifacts(self, receipt: LifecycleCommitReceipt) -> None:
        process_reference = receipt.process_execution_terminal_record
        terminal_reference = receipt.terminal_result
        audit_reference = receipt.run_view.run_audit_projection
        if process_reference is not None or terminal_reference is not None:
            if (
                process_reference is None
                or terminal_reference is None
                or audit_reference is None
                or receipt.run_view.terminal_result != terminal_reference
            ):
                raise ArtifactIntegrityError(f"lifecycle-receipt:{receipt.command_id}")
            expected = (
                (process_reference, "process_execution_terminal_record"),
                (terminal_reference, "terminal_result"),
                (audit_reference, "run_audit_projection"),
            )
        elif receipt.run_view.terminal_result is not None:
            if audit_reference is None:
                raise ArtifactIntegrityError(f"lifecycle-receipt:{receipt.command_id}")
            expected = ((audit_reference, "run_audit_projection"),)
        else:
            if audit_reference is not None:
                raise ArtifactIntegrityError(f"lifecycle-receipt:{receipt.command_id}")
            expected = ()
        if receipt.artifact_references != tuple(reference for reference, _ in expected):
            raise ArtifactIntegrityError(f"lifecycle-receipt:{receipt.command_id}")
        for reference, artifact_type in expected:
            result = self.get(ArtifactId(root=reference.artifact_id))
            if (
                result.envelope.artifact_type != artifact_type
                or result.envelope.payload_hash != reference.payload_hash
            ):
                raise ArtifactIntegrityError(f"lifecycle-receipt:{receipt.command_id}")

    def _verify_cross_receipt_artifacts(
        self,
        receipt: CrossRunLifecycleCommitReceipt,
    ) -> None:
        expected = tuple(
            (reference, artifact_type)
            for reference, artifact_type in (
                (
                    receipt.process_execution_terminal_record,
                    "process_execution_terminal_record",
                ),
                (receipt.terminal_result, "terminal_result"),
                (receipt.run_audit_projection, "run_audit_projection"),
                (receipt.execution_attestation, "execution_attestation"),
            )
            if reference is not None
        )
        expected_references = tuple(
            sorted(
                (reference for reference, _ in expected),
                key=lambda item: item.artifact_id.encode("utf-8"),
            )
        )
        if receipt.artifact_references != expected_references:
            raise ArtifactIntegrityError(f"lifecycle-receipt:{receipt.command_id}")
        for reference, artifact_type in expected:
            result = self.get(ArtifactId(root=reference.artifact_id))
            if (
                result.envelope.artifact_type != artifact_type
                or result.envelope.payload_hash != reference.payload_hash
            ):
                raise ArtifactIntegrityError(f"lifecycle-receipt:{receipt.command_id}")
        if receipt.command_type == "create_replacement_run":
            process_reference = receipt.process_execution_terminal_record
            terminal_reference = receipt.terminal_result
            attestation_reference = receipt.execution_attestation
            if (
                process_reference is None
                or terminal_reference is None
                or receipt.run_audit_projection is None
                or attestation_reference is None
                or receipt.parent_run_view.terminal_result != terminal_reference
                or receipt.parent_run_view.run_audit_projection
                != receipt.run_audit_projection
            ):
                raise ArtifactIntegrityError(f"lifecycle-receipt:{receipt.command_id}")
            attestation_payload = self._artifact_payload(
                attestation_reference,
                artifact_type="execution_attestation",
            )
            attestation = ExecutionAttestation.model_validate_json(
                canonical_json_bytes(attestation_payload)
            )
            if (
                attestation.process_terminal_record != process_reference
                or attestation.terminal_result != terminal_reference
            ):
                raise ArtifactIntegrityError(f"lifecycle-receipt:{receipt.command_id}")
        elif any(
            reference is not None
            for reference in (
                receipt.process_execution_terminal_record,
                receipt.terminal_result,
                receipt.run_audit_projection,
                receipt.execution_attestation,
            )
        ):
            raise ArtifactIntegrityError(f"lifecycle-receipt:{receipt.command_id}")

    def _verify_projection_artifacts(
        self,
        *,
        run_id: str,
        terminal_head: EventHead,
        terminal_reference: ArtifactReference,
        audit_head: EventHead,
        audit_reference: ArtifactReference,
    ) -> None:
        try:
            terminal_result = self.get(ArtifactId(root=terminal_reference.artifact_id))
            audit_result = self.get(ArtifactId(root=audit_reference.artifact_id))
            if (
                terminal_result.envelope.artifact_type != "terminal_result"
                or audit_result.envelope.artifact_type != "run_audit_projection"
                or terminal_result.envelope.payload_hash
                != terminal_reference.payload_hash
                or audit_result.envelope.payload_hash != audit_reference.payload_hash
            ):
                raise ValueError("projection index points to the wrong artifact kind")
            terminal = TerminalResult.model_validate_json(
                canonical_json_bytes(
                    terminal_result.payload_document.model_dump(mode="json")["payload"]
                )
            )
            audit = RunAuditProjection.model_validate_json(
                canonical_json_bytes(
                    audit_result.payload_document.model_dump(mode="json")["payload"]
                )
            )
            terminal_snapshot = terminal.terminal_snapshot_event_head
            audit_snapshot = audit.as_of_event_head
            if (
                terminal.run_id != run_id
                or terminal.terminal_event.sequence_no != terminal_head.sequence_no
                or terminal.terminal_event.event_hash != terminal_head.event_hash
                or terminal_snapshot.run_id.root != run_id
                or terminal_snapshot.sequence_no != terminal_head.sequence_no
                or terminal_snapshot.event_hash.root != terminal_head.event_hash
                or audit.run_id != run_id
                or audit.projector_version != RUN_PROJECTOR_VERSION
                or audit.projector_hash != RUN_PROJECTOR_HASH
                or audit_snapshot.run_id.root != run_id
                or audit_snapshot.sequence_no != audit_head.sequence_no
                or audit_snapshot.event_hash.root != audit_head.event_hash
                or audit.terminal_result != terminal_reference
            ):
                raise ValueError("projection index binding is inconsistent")
        except (TypeError, ValueError) as error:
            raise ArtifactIntegrityError(f"run-projection:{run_id}") from error

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
        expected_parent_references: tuple[_ExpectedParentReference, ...] | None = None
        if isinstance(registered.parent_contract, PayloadBoundParentContract):
            document = parse_canonical_document(payload_bytes)
            canonical_payload = (
                cast(dict[str, object], document).get("payload")
                if type(document) is dict
                else None
            )
            try:
                expected_parent_references = _extract_payload_parent_references(
                    canonical_payload,
                    registered.parent_contract.bindings,
                )
            except (TypeError, ValueError) as error:
                raise ArtifactParentContractError(
                    request.artifact_type,
                    ("valid payload-bound parent references",),
                    ("invalid payload-bound parent references",),
                ) from error
        expected_parent_ids = (
            tuple(reference.artifact_id for reference in expected_parent_references)
            if expected_parent_references is not None
            else None
        )
        if (
            expected_parent_ids is not None
            and expected_parent_ids != request.parent_artifact_ids
        ):
            raise ArtifactParentContractError(
                request.artifact_type,
                tuple(item.root for item in expected_parent_ids),
                tuple(item.root for item in request.parent_artifact_ids),
            )
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
            parent_contract=registered.parent_contract,
            expected_parent_references=expected_parent_references,
        )

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

    @staticmethod
    def _validate_payload_parent_contract(
        artifact_type: str,
        expected_references: tuple[_ExpectedParentReference, ...],
        actual_parents: tuple[_VerifiedParent, ...],
    ) -> None:
        expected = tuple(
            (
                reference.artifact_id.root,
                reference.payload_hash.root,
                reference.allowed_artifact_types,
            )
            for reference in expected_references
        )
        actual = tuple(
            (
                parent.artifact_id.root,
                parent.payload_hash.root,
                parent.artifact_type,
            )
            for parent in actual_parents
        )
        if len(expected_references) != len(actual_parents) or any(
            expected_reference.artifact_id != actual_parent.artifact_id
            or expected_reference.payload_hash != actual_parent.payload_hash
            or actual_parent.artifact_type
            not in expected_reference.allowed_artifact_types
            for expected_reference, actual_parent in zip(
                expected_references,
                actual_parents,
                strict=True,
            )
        ):
            raise ArtifactParentContractError(
                artifact_type,
                tuple(repr(item) for item in expected),
                tuple(repr(item) for item in actual),
            )

    def _validate_prepared_parent_contract(
        self,
        prepared: _PreparedArtifact,
        direct_parents: tuple[_VerifiedParent, ...],
    ) -> None:
        if prepared.expected_parent_references is not None:
            self._validate_payload_parent_contract(
                prepared.artifact_type,
                prepared.expected_parent_references,
                direct_parents,
            )
            return
        exact_contract = cast(ExactParentContract, prepared.parent_contract)
        self._validate_parent_contract(
            prepared.artifact_type,
            exact_contract.direct_parent_artifact_types,
            tuple(parent.artifact_type for parent in direct_parents),
        )

    def _verify(
        self,
        stored: _StoredArtifact,
        direct_parents: tuple[_VerifiedParent, ...],
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
            registered.codec.decode(stored.payload_bytes)
            if isinstance(registered.parent_contract, PayloadBoundParentContract):
                document = parse_canonical_document(stored.payload_bytes)
                if type(document) is not dict:
                    raise ValueError("invalid canonical payload document")
                payload = cast(dict[str, object], document).get("payload")
                expected_parent_references = _extract_payload_parent_references(
                    payload,
                    registered.parent_contract.bindings,
                )
                if (
                    tuple(
                        reference.artifact_id
                        for reference in expected_parent_references
                    )
                    != stored.parent_artifact_ids
                ):
                    raise ValueError("payload-bound parent IDs do not match")
                self._validate_payload_parent_contract(
                    stored.artifact_type,
                    expected_parent_references,
                    direct_parents,
                )
            else:
                self._validate_parent_contract(
                    stored.artifact_type,
                    registered.direct_parent_artifact_types,
                    tuple(parent.artifact_type for parent in direct_parents),
                )
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
        direct_parents: tuple[_VerifiedParent, ...],
    ) -> ArtifactBytesResult:
        self._verify(stored, direct_parents)
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
        event_authenticator: EventAuthenticator | None = None,
        command_authority: CommandAuthority | None = None,
    ) -> None:
        super().__init__(schema_registry, event_authenticator, command_authority)
        self._artifacts: dict[str, _StoredArtifact] = {}
        self._event_records: dict[str, tuple[bytes, ...]] = {}
        self._event_ids: dict[str, tuple[str, int, bytes]] = {}
        self._signed_event_nonces: dict[str, str] = {}
        self._signed_event_slots: dict[tuple[str, str, int], str] = {}
        self._lifecycle_commands: dict[str, tuple[str, LifecycleCommitResult]] = {}
        self._lifecycle_idempotency: dict[str, tuple[str, LifecycleCommitResult]] = {}
        self._terminal_results: dict[str, tuple[int, ArtifactReference]] = {}
        self._audit_projections: dict[str, dict[int, ArtifactReference]] = {}
        self._run_replacements: dict[str, tuple[object, ...]] = {}
        self._clarification_continuations: dict[str, tuple[object, ...]] = {}

    def _load_run_event_records(self, run_id: str) -> tuple[EventRecord, ...]:
        records = self._parse_verified_event_records(
            self._event_records.get(run_id, ())
        )
        event_ids = {record.event.event_id for record in records}
        expected_nonces = {
            signed_nonce: record.event.event_id
            for record in records
            if (signed_nonce := _signed_event_nonce(record.event)) is not None
        }
        expected_slots = {
            signed_slot: record.event.event_id
            for record in records
            if (signed_slot := _signed_event_slot(record.event)) is not None
        }
        actual_nonces = {
            key: event_id
            for key, event_id in self._signed_event_nonces.items()
            if event_id in event_ids
        }
        actual_slots = {
            key: event_id
            for key, event_id in self._signed_event_slots.items()
            if key[1] == run_id or event_id in event_ids
        }
        if actual_nonces != expected_nonces or actual_slots != expected_slots:
            raise ArtifactIntegrityError(f"signed-event-index:{run_id}")
        return records

    def commit(
        self,
        request: Mapping[str, object],
        *,
        context: AuthenticatedCommandContext,
    ) -> LifecycleCommitResult:
        command = validate_lifecycle_command(request)
        if isinstance(command, CommitTerminalCommand):
            return self._commit_terminal(command, context)
        if isinstance(command, CreateReplacementRunCommand):
            return self._commit_replacement(command, context)
        if isinstance(command, CreateClarificationChildRunCommand):
            return self._commit_clarification_child(command, context)
        fingerprint = _lifecycle_command_fingerprint(command)
        with self._lock:
            self._authenticate_command_context(command, context)
            snapshots = (
                dict(self._artifacts),
                dict(self._event_records),
                dict(self._event_ids),
                dict(self._signed_event_nonces),
                dict(self._signed_event_slots),
                dict(self._lifecycle_commands),
                dict(self._lifecycle_idempotency),
                dict(self._terminal_results),
                {
                    run_id: dict(versions)
                    for run_id, versions in self._audit_projections.items()
                },
            )
            prior_result = self._prior_lifecycle_result(
                command.command_id,
                command.idempotency_key,
                fingerprint,
            )
            if prior_result is not None:
                return prior_result
            try:
                existing = self._load_run_event_records(command.run_id)
                current_state = (
                    self._project_run_records(existing).state if existing else None
                )
                if current_state != command.expected_state and any(
                    event.sequence_no >= len(existing) for event in command.events
                ):
                    raise EventHeadConflictError(command.run_id)
                authentication_records = existing
                for event in command.events:
                    if event.run_id != command.run_id:
                        raise ValueError(
                            "append command run_id does not match an event"
                        )
                    replayed = self._event_ids.get(event.event_id)
                    if replayed is not None:
                        raise EventReplayConflictError(event.event_id)
                    self._authenticate_event(event, authentication_records)
                    signed_nonce = _signed_event_nonce(event)
                    signed_slot = _signed_event_slot(event)
                    if (
                        signed_nonce is not None
                        and signed_nonce in self._signed_event_nonces
                        or signed_slot is not None
                        and signed_slot in self._signed_event_slots
                    ):
                        raise EventReplayConflictError(event.event_id)
                    self._require_event_references(event)
                    authentication_records += (_record_for_event(event),)
                updated, step_result = _append_closed_event_batch(
                    existing,
                    tuple(command.events),
                    expected_head=command.expected_head,
                    budget_snapshots=self._budget_snapshots_for_events(
                        tuple(record.event for record in authentication_records)
                    ),
                )
                result = _with_commit_records(
                    step_result,
                    command,
                    before_head=command.expected_head,
                    records=updated[len(existing) :],
                )
                post_terminal = (
                    current_state is not None and current_state in TERMINAL_STATES
                )
                if post_terminal:
                    result = self._append_post_terminal_audit(command, result)
                else:
                    result = self._decorate_result(result)
                raw_updated = tuple(_record_bytes(record) for record in updated)
                self._event_records[command.run_id] = raw_updated
                if post_terminal:
                    self._post_terminal_commit_failpoint("after_event_records")
                for event in command.events:
                    self._event_ids[event.event_id] = (
                        event.run_id,
                        event.sequence_no,
                        raw_updated[event.sequence_no],
                    )
                    signed_nonce = _signed_event_nonce(event)
                    if signed_nonce is not None:
                        self._signed_event_nonces[signed_nonce] = event.event_id
                    signed_slot = _signed_event_slot(event)
                    if signed_slot is not None:
                        self._signed_event_slots[signed_slot] = event.event_id
                if post_terminal:
                    self._post_terminal_commit_failpoint("after_head")
                self._lifecycle_commands[command.command_id] = (fingerprint, result)
                self._lifecycle_idempotency[command.idempotency_key] = (
                    fingerprint,
                    result,
                )
                if post_terminal:
                    self._post_terminal_commit_failpoint("after_receipt")
                return _fresh_commit_receipt(result)
            except BaseException:
                (
                    self._artifacts,
                    self._event_records,
                    self._event_ids,
                    self._signed_event_nonces,
                    self._signed_event_slots,
                    self._lifecycle_commands,
                    self._lifecycle_idempotency,
                    self._terminal_results,
                    self._audit_projections,
                ) = snapshots
                raise

    def _commit_replacement(
        self,
        command: CreateReplacementRunCommand,
        context: AuthenticatedCommandContext,
    ) -> CrossRunLifecycleCommitReceipt:
        fingerprint = _lifecycle_command_fingerprint(command)
        with self._lock:
            self._authenticate_command_context(command, context)
            prior = self._prior_lifecycle_result(
                command.command_id,
                command.idempotency_key,
                fingerprint,
            )
            if prior is not None:
                if not isinstance(prior, CrossRunLifecycleCommitReceipt):
                    raise ArtifactIntegrityError(
                        f"lifecycle-command:{command.command_id}"
                    )
                return prior
            snapshots = (
                dict(self._artifacts),
                dict(self._event_records),
                dict(self._event_ids),
                dict(self._signed_event_nonces),
                dict(self._signed_event_slots),
                dict(self._lifecycle_commands),
                dict(self._lifecycle_idempotency),
                dict(self._terminal_results),
                {
                    run_id: dict(versions)
                    for run_id, versions in self._audit_projections.items()
                },
                dict(self._run_replacements),
                dict(self._clarification_continuations),
            )
            try:
                parent_records = self._load_run_event_records(command.parent_run_id)
                child_records = self._load_run_event_records(command.child_run_id)
                if (
                    not parent_records
                    or child_records
                    or command.parent_run_id in self._run_replacements
                    or command.parent_run_id in self._clarification_continuations
                    or any(
                        edge[0] == command.child_run_id
                        for edge in (
                            *self._run_replacements.values(),
                            *self._clarification_continuations.values(),
                        )
                    )
                ):
                    raise EventHeadConflictError(command.child_run_id)
                parent_projection = self._project_run_records(parent_records)
                self._validate_replacement_contract(
                    command,
                    parent_records,
                    parent_projection,
                    context,
                )
                parent_events = (
                    command.run_superseded_event,
                    command.parent_terminal_transition,
                )
                child_event = command.replacement_run_created_event
                all_events = (*parent_events, child_event)
                event_ids = tuple(event.event_id for event in all_events)
                nonces = tuple(
                    cast(str, _signed_event_nonce(event))
                    for event in all_events
                    if _signed_event_nonce(event) is not None
                )
                slots = tuple(
                    cast(tuple[str, str, int], _signed_event_slot(event))
                    for event in all_events
                    if _signed_event_slot(event) is not None
                )
                if (
                    len(set(event_ids)) != len(event_ids)
                    or len(set(nonces)) != len(nonces)
                    or len(set(slots)) != len(slots)
                    or any(event_id in self._event_ids for event_id in event_ids)
                    or any(nonce in self._signed_event_nonces for nonce in nonces)
                    or any(slot in self._signed_event_slots for slot in slots)
                ):
                    raise EventReplayConflictError(event_ids[0])
                self._authenticate_event(parent_events[0], parent_records)
                superseded_record = _record_for_event(parent_events[0])
                self._authenticate_event(
                    parent_events[1],
                    parent_records + (superseded_record,),
                )
                self._authenticate_event(child_event, ())
                for event in all_events:
                    self._require_event_references(event)
                transition_record = _record_for_event(parent_events[1])
                child_record = _record_for_event(child_event)
                updated_parent_records = parent_records + (
                    superseded_record,
                    transition_record,
                )
                updated_child_records = (child_record,)
                parent_view = self._project_run_records(updated_parent_records)
                child_view = self._project_run_records(updated_child_records)
                process = command.process_terminal_record
                self._require_references(
                    (
                        process.job_manifest,
                        *process.payload_outputs,
                        process.resource_usage,
                    )
                )
                process_result = self._put_lifecycle_model(
                    "process_execution_terminal_record",
                    process,
                    parents=(
                        process.job_manifest,
                        *process.payload_outputs,
                        process.resource_usage,
                    ),
                    created_by=process.principal_id,
                    created_at=process.created_at,
                )
                self._cross_run_commit_failpoint("after_process_artifact")
                process_reference = _artifact_reference(process_result)
                approvals = _active_approval_snapshots(parent_records)
                terminal = _replacement_terminal_result_payload(
                    command,
                    _closed_append_step(transition_record, parent_view),
                    process_reference,
                    approvals,
                )
                terminal_result = self._put_lifecycle_model(
                    "terminal_result",
                    terminal,
                    parents=(
                        command.fixed_commit_job_manifest,
                        process_reference,
                        *process.payload_outputs,
                    ),
                    created_by=process.principal_id,
                    created_at=command.parent_terminal_transition.issued_at,
                )
                terminal_reference = _artifact_reference(terminal_result)
                audit = _replacement_root_audit_projection_payload(
                    command,
                    _closed_append_step(transition_record, parent_view),
                    terminal_reference,
                    approvals,
                )
                audit_result = self._put_lifecycle_model(
                    "run_audit_projection",
                    audit,
                    parents=(terminal_reference,),
                    created_by=process.principal_id,
                    created_at=command.parent_terminal_transition.issued_at,
                )
                audit_reference = _artifact_reference(audit_result)
                self._cross_run_commit_failpoint("after_terminal_artifacts")
                attestation = command.execution_attestation
                if (
                    attestation.process_terminal_record != process_reference
                    or attestation.terminal_result != terminal_reference
                ):
                    raise TerminalProvenanceError(command.parent_run_id)
                attestation_result = self._put_lifecycle_model(
                    "execution_attestation",
                    attestation,
                    parents=(
                        attestation.job_manifest,
                        attestation.process_terminal_record,
                        *attestation.payload_outputs,
                        terminal_reference,
                    ),
                    created_by=attestation.principal_id,
                    created_at=attestation.issued_at,
                )
                attestation_reference = _artifact_reference(attestation_result)
                self._cross_run_commit_failpoint("after_attestation_artifact")
                parent_view = _decorate_projection(
                    parent_view,
                    terminal_reference,
                    audit_reference,
                )
                receipt = _cross_run_receipt(
                    command,
                    parent_before_head=_event_head_from_verified(
                        command.expected_parent_head
                    ),
                    parent_records=(superseded_record, transition_record),
                    child_records=(child_record,),
                    parent_view=parent_view,
                    child_view=child_view,
                    process_reference=process_reference,
                    terminal_reference=terminal_reference,
                    audit_reference=audit_reference,
                    attestation_reference=attestation_reference,
                )
                raw_parent = tuple(
                    _record_bytes(record) for record in updated_parent_records
                )
                raw_child = (_record_bytes(child_record),)
                self._event_records[command.parent_run_id] = raw_parent
                self._event_records[command.child_run_id] = raw_child
                for event, raw in zip(
                    all_events,
                    (raw_parent[-2], raw_parent[-1], raw_child[0]),
                    strict=True,
                ):
                    self._event_ids[event.event_id] = (
                        event.run_id,
                        event.sequence_no,
                        raw,
                    )
                    nonce = _signed_event_nonce(event)
                    if nonce is not None:
                        self._signed_event_nonces[nonce] = event.event_id
                    slot = _signed_event_slot(event)
                    if slot is not None:
                        self._signed_event_slots[slot] = event.event_id
                self._cross_run_commit_failpoint("after_event_records")
                self._terminal_results[command.parent_run_id] = (
                    transition_record.event.sequence_no,
                    terminal_reference,
                )
                self._audit_projections[command.parent_run_id] = {
                    transition_record.event.sequence_no: audit_reference
                }
                prerequisite = command.cause_prerequisite
                prerequisite_event_id = (
                    prerequisite.failed_waiting_event.event_id
                    if isinstance(prerequisite, RuntimeReplacementPrerequisite)
                    else prerequisite.revocation_event.event_id
                )
                self._run_replacements[command.parent_run_id] = (
                    command.child_run_id,
                    command.run_superseded_event.event_id,
                    prerequisite_event_id,
                    command.run_superseded_event.replacement_ordinal,
                    command.replacement_policy.artifact_id,
                    process_reference.artifact_id,
                    terminal_reference.artifact_id,
                    attestation_reference.artifact_id,
                )
                self._cross_run_commit_failpoint("after_indexes")
                self._lifecycle_commands[command.command_id] = (
                    fingerprint,
                    receipt,
                )
                self._lifecycle_idempotency[command.idempotency_key] = (
                    fingerprint,
                    receipt,
                )
                self._cross_run_commit_failpoint("after_receipt")
                return receipt
            except BaseException:
                (
                    self._artifacts,
                    self._event_records,
                    self._event_ids,
                    self._signed_event_nonces,
                    self._signed_event_slots,
                    self._lifecycle_commands,
                    self._lifecycle_idempotency,
                    self._terminal_results,
                    self._audit_projections,
                    self._run_replacements,
                    self._clarification_continuations,
                ) = snapshots
                raise

    def _commit_clarification_child(
        self,
        command: CreateClarificationChildRunCommand,
        context: AuthenticatedCommandContext,
    ) -> CrossRunLifecycleCommitReceipt:
        fingerprint = _lifecycle_command_fingerprint(command)
        with self._lock:
            self._authenticate_command_context(command, context)
            prior = self._prior_lifecycle_result(
                command.command_id,
                command.idempotency_key,
                fingerprint,
            )
            if prior is not None:
                if not isinstance(prior, CrossRunLifecycleCommitReceipt):
                    raise ArtifactIntegrityError(
                        f"lifecycle-command:{command.command_id}"
                    )
                return prior
            snapshots = (
                dict(self._event_records),
                dict(self._event_ids),
                dict(self._signed_event_nonces),
                dict(self._signed_event_slots),
                dict(self._lifecycle_commands),
                dict(self._lifecycle_idempotency),
                dict(self._clarification_continuations),
            )
            try:
                parent_records = self._load_run_event_records(command.parent_run_id)
                child_records = self._load_run_event_records(command.child_run_id)
                if (
                    not parent_records
                    or child_records
                    or command.parent_run_id in self._run_replacements
                    or command.parent_run_id in self._clarification_continuations
                    or any(
                        edge[0] == command.child_run_id
                        for edge in (
                            *self._run_replacements.values(),
                            *self._clarification_continuations.values(),
                        )
                    )
                ):
                    raise EventHeadConflictError(command.child_run_id)
                parent_projection = self._decorate_view(
                    self._project_run_records(parent_records)
                )
                self._validate_clarification_contract(
                    command,
                    parent_records,
                    parent_projection,
                    context,
                )
                event = command.clarification_child_run_created_event
                nonce = cast(str, _signed_event_nonce(event))
                slot = cast(tuple[str, str, int], _signed_event_slot(event))
                if (
                    event.event_id in self._event_ids
                    or nonce in self._signed_event_nonces
                    or slot in self._signed_event_slots
                ):
                    raise EventReplayConflictError(event.event_id)
                self._authenticate_event(event, ())
                self._require_event_references(event)
                child_record = _record_for_event(event)
                child_view = self._project_run_records((child_record,))
                receipt = _cross_run_receipt(
                    command,
                    parent_before_head=_event_head_from_verified(
                        command.expected_parent_head
                    ),
                    parent_records=(),
                    child_records=(child_record,),
                    parent_view=parent_projection,
                    child_view=child_view,
                )
                raw_child = _record_bytes(child_record)
                self._event_records[command.child_run_id] = (raw_child,)
                self._event_ids[event.event_id] = (
                    event.run_id,
                    event.sequence_no,
                    raw_child,
                )
                self._signed_event_nonces[nonce] = event.event_id
                self._signed_event_slots[slot] = event.event_id
                self._cross_run_commit_failpoint("after_child_event")
                self._clarification_continuations[command.parent_run_id] = (
                    command.child_run_id,
                    event.event_id,
                    command.signed_answer_bundle.artifact_id,
                    command.continuation_policy.artifact_id,
                )
                self._cross_run_commit_failpoint("after_indexes")
                self._lifecycle_commands[command.command_id] = (
                    fingerprint,
                    receipt,
                )
                self._lifecycle_idempotency[command.idempotency_key] = (
                    fingerprint,
                    receipt,
                )
                self._cross_run_commit_failpoint("after_receipt")
                return receipt
            except BaseException:
                (
                    self._event_records,
                    self._event_ids,
                    self._signed_event_nonces,
                    self._signed_event_slots,
                    self._lifecycle_commands,
                    self._lifecycle_idempotency,
                    self._clarification_continuations,
                ) = snapshots
                raise

    def _commit_terminal(
        self,
        command: CommitTerminalCommand,
        context: AuthenticatedCommandContext,
    ) -> LifecycleCommitReceipt:
        cause = command.events[0]
        transition = cast(StateTransitioned, command.events[1])
        process = command.process_terminal_record
        if (
            process.run_id != command.run_id
            or process.job_manifest != command.fixed_commit_job_manifest
            or cause.actor_principal_id != command.actor_principal_id
            or transition.actor_principal_id != command.actor_principal_id
        ):
            raise TerminalProvenanceError(command.run_id)
        fingerprint = _lifecycle_command_fingerprint(command)
        with self._lock:
            self._authenticate_command_context(command, context)
            prior_result = self._prior_lifecycle_result(
                command.command_id,
                command.idempotency_key,
                fingerprint,
            )
            if prior_result is not None:
                if not isinstance(prior_result, LifecycleCommitReceipt):
                    raise ArtifactIntegrityError(
                        f"lifecycle-command:{command.command_id}"
                    )
                return prior_result
            snapshots = (
                dict(self._artifacts),
                dict(self._event_records),
                dict(self._event_ids),
                dict(self._signed_event_nonces),
                dict(self._signed_event_slots),
                dict(self._lifecycle_commands),
                dict(self._lifecycle_idempotency),
                dict(self._terminal_results),
                {
                    run_id: dict(versions)
                    for run_id, versions in self._audit_projections.items()
                },
            )
            try:
                existing = self._load_run_event_records(command.run_id)
                self._require_terminal_approvals(
                    existing,
                    command.terminal_time_approvals,
                )
                current_projection = self._project_run_records(existing)
                if process.experiment_id != current_projection.experiment_id:
                    raise TerminalProvenanceError(command.run_id)
                if current_projection.state != command.expected_state:
                    raise EventHeadConflictError(command.run_id)
                if cause.event_id == transition.event_id:
                    raise EventReplayConflictError(cause.event_id)
                updated = existing
                expected_head = command.expected_head
                transition_result: RunAppendStep | None = None
                for event in (cause, transition):
                    if event.event_id in self._event_ids:
                        raise EventReplayConflictError(event.event_id)
                    self._authenticate_event(event, updated)
                    signed_nonce = _signed_event_nonce(event)
                    signed_slot = _signed_event_slot(event)
                    if (
                        signed_nonce is not None
                        and signed_nonce in self._signed_event_nonces
                        or signed_slot is not None
                        and signed_slot in self._signed_event_slots
                    ):
                        raise EventReplayConflictError(event.event_id)
                    self._require_event_references(event)
                    updated, transition_result = append_record(
                        updated,
                        event,
                        expected_head=expected_head,
                        allow_terminal=True,
                        budget_snapshots=self._budget_snapshots_for_events(
                            tuple(record.event for record in updated) + (event,)
                        ),
                    )
                    expected_head = transition_result.run_view.event_head
                if (
                    transition_result is None
                ):  # pragma: no cover - schema 要求精确事件对。
                    raise AssertionError("terminal command contained no events")
                self._require_references(
                    (
                        process.job_manifest,
                        *process.payload_outputs,
                        process.resource_usage,
                    )
                )
                process_result = self._put_lifecycle_model(
                    "process_execution_terminal_record",
                    process,
                    parents=(
                        process.job_manifest,
                        *process.payload_outputs,
                        process.resource_usage,
                    ),
                    created_by=process.principal_id,
                    created_at=process.created_at,
                )
                self._terminal_commit_failpoint("after_process_artifact")
                process_reference = _artifact_reference(process_result)
                terminal = _terminal_result_payload(
                    command,
                    transition_result,
                    process_reference,
                )
                terminal_result = self._put_lifecycle_model(
                    "terminal_result",
                    terminal,
                    parents=(
                        command.fixed_commit_job_manifest,
                        process_reference,
                        *process.payload_outputs,
                    ),
                    created_by=process.principal_id,
                    created_at=command.created_at,
                )
                self._terminal_commit_failpoint("after_terminal_artifact")
                terminal_reference = _artifact_reference(terminal_result)
                audit = _root_audit_projection_payload(
                    command,
                    transition_result,
                    terminal_reference,
                )
                audit_result = self._put_lifecycle_model(
                    "run_audit_projection",
                    audit,
                    parents=(terminal_reference,),
                    created_by=process.principal_id,
                    created_at=command.created_at,
                )
                self._terminal_commit_failpoint("after_audit_artifact")
                audit_reference = _artifact_reference(audit_result)
                view_payload = transition_result.run_view.model_dump(mode="json")
                view_payload["terminal_result"] = terminal_reference.model_dump(
                    mode="json"
                )
                view_payload["run_audit_projection"] = audit_reference.model_dump(
                    mode="json"
                )
                view = RunProjection.model_validate_json(
                    canonical_json_bytes(view_payload)
                )
                result = _terminal_append_result(
                    command,
                    cast(tuple[EventRecord, EventRecord], updated[-2:]),
                    command.expected_head,
                    view,
                    process_reference,
                    terminal_reference,
                    audit_reference,
                )
                raw_updated = tuple(_record_bytes(record) for record in updated)
                self._event_records[command.run_id] = raw_updated
                self._terminal_commit_failpoint("after_event_records")
                for event in (cause, transition):
                    self._event_ids[event.event_id] = (
                        event.run_id,
                        event.sequence_no,
                        raw_updated[event.sequence_no],
                    )
                    signed_nonce = _signed_event_nonce(event)
                    if signed_nonce is not None:
                        self._signed_event_nonces[signed_nonce] = event.event_id
                    signed_slot = _signed_event_slot(event)
                    if signed_slot is not None:
                        self._signed_event_slots[signed_slot] = event.event_id
                self._terminal_commit_failpoint("after_head")
                self._terminal_results[command.run_id] = (
                    transition.sequence_no,
                    terminal_reference,
                )
                self._terminal_commit_failpoint("after_terminal_index")
                self._audit_projections[command.run_id] = {
                    transition.sequence_no: audit_reference
                }
                self._terminal_commit_failpoint("after_audit_index")
                self._lifecycle_commands[command.command_id] = (
                    fingerprint,
                    result,
                )
                self._lifecycle_idempotency[command.idempotency_key] = (
                    fingerprint,
                    result,
                )
                self._terminal_commit_failpoint("after_receipt")
                return result
            except BaseException:
                (
                    self._artifacts,
                    self._event_records,
                    self._event_ids,
                    self._signed_event_nonces,
                    self._signed_event_slots,
                    self._lifecycle_commands,
                    self._lifecycle_idempotency,
                    self._terminal_results,
                    self._audit_projections,
                ) = snapshots
                raise

    def _prior_lifecycle_result(
        self,
        command_id: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> LifecycleCommitResult | None:
        by_id = self._lifecycle_commands.get(command_id)
        by_key = self._lifecycle_idempotency.get(idempotency_key)
        if by_id is not None and by_key is not None and by_id != by_key:
            raise ArtifactIntegrityError(f"lifecycle-command:{command_id}")
        prior = by_id or by_key
        if prior is None:
            return None
        if prior[0] != fingerprint:
            raise EventReplayConflictError(command_id)
        try:
            receipt = _fresh_commit_receipt(prior[1])
        except (TypeError, ValueError) as error:
            raise ArtifactIntegrityError(f"lifecycle-command:{command_id}") from error
        if (
            receipt.command_id != command_id
            or receipt.idempotency_key != idempotency_key
            or receipt.command_fingerprint != fingerprint
        ):
            raise ArtifactIntegrityError(f"lifecycle-command:{command_id}")
        if isinstance(receipt, LifecycleCommitReceipt):
            records = self._load_run_event_records(receipt.run_id)
            self._verify_receipt_records(receipt, records)
            self._verify_receipt_artifacts(receipt)
            expected_view = self._decorate_view(
                self._project_run_records(records, as_of_head=receipt.after_head)
            )
            if receipt.run_view != expected_view:
                raise ArtifactIntegrityError(f"lifecycle-receipt:{command_id}")
        else:
            parent_records = self._load_run_event_records(receipt.parent_run_id)
            child_records = self._load_run_event_records(receipt.child_run_id)
            if any(
                record.event.sequence_no >= len(records)
                or records[record.event.sequence_no] != record
                for records, receipt_records in (
                    (parent_records, receipt.parent_event_records),
                    (child_records, receipt.child_event_records),
                )
                for record in receipt_records
            ):
                raise ArtifactIntegrityError(f"lifecycle-receipt:{command_id}")
            expected_parent = self._decorate_view(
                self._project_run_records(
                    parent_records,
                    as_of_head=receipt.parent_after_head,
                )
            )
            expected_child = self._project_run_records(
                child_records,
                as_of_head=receipt.child_after_head,
            )
            self._verify_cross_receipt_artifacts(receipt)
            if (
                receipt.parent_run_view != expected_parent
                or receipt.child_run_view != expected_child
            ):
                raise ArtifactIntegrityError(f"lifecycle-receipt:{command_id}")
            if receipt.command_type == "create_replacement_run":
                edge = self._run_replacements.get(receipt.parent_run_id)
                superseded = cast(
                    RunSuperseded,
                    receipt.parent_event_records[0].event,
                )
                prerequisite_event_id = (
                    superseded.failed_waiting_event_id or superseded.revocation_event_id
                )
                if (
                    edge is None
                    or edge[0] != receipt.child_run_id
                    or edge[1] != superseded.event_id
                    or edge[2] != prerequisite_event_id
                    or edge[3] != superseded.replacement_ordinal
                    or edge[4] != superseded.replacement_policy_artifact_id
                    or edge[5]
                    != cast(
                        ArtifactReference,
                        receipt.process_execution_terminal_record,
                    ).artifact_id
                    or edge[6]
                    != cast(ArtifactReference, receipt.terminal_result).artifact_id
                    or edge[7]
                    != cast(
                        ArtifactReference,
                        receipt.execution_attestation,
                    ).artifact_id
                ):
                    raise ArtifactIntegrityError(f"lifecycle-receipt:{command_id}")
            else:
                edge = self._clarification_continuations.get(receipt.parent_run_id)
                if (
                    edge is None
                    or edge[0] != receipt.child_run_id
                    or edge[1] != receipt.child_event_records[0].event.event_id
                ):
                    raise ArtifactIntegrityError(f"lifecycle-receipt:{command_id}")
        return receipt

    def _append_post_terminal_audit(
        self,
        command: AppendRunEventsCommand,
        result: LifecycleCommitReceipt,
    ) -> LifecycleCommitReceipt:
        terminal_state = self._terminal_results.get(command.run_id)
        audit_versions = self._audit_projections.get(command.run_id)
        if terminal_state is None or not audit_versions:
            raise ArtifactIntegrityError(f"run-terminal:{command.run_id}")
        _, terminal_reference = terminal_state
        previous_sequence = max(audit_versions)
        previous_reference = audit_versions[previous_sequence]
        previous_result = self.get(ArtifactId(root=previous_reference.artifact_id))
        previous_payload = previous_result.payload_document.model_dump(mode="json")[
            "payload"
        ]
        previous = RunAuditProjection.model_validate_json(
            canonical_json_bytes(previous_payload)
        )
        audit = _next_audit_projection_payload(
            previous_reference,
            previous,
            terminal_reference,
            result.run_view,
            command.events,
        )
        audit_result = self._put_lifecycle_model(
            "run_audit_projection",
            audit,
            parents=(previous_reference, terminal_reference),
            created_by=command.actor_principal_id,
            created_at=command.events[-1].issued_at,
        )
        self._post_terminal_commit_failpoint("after_audit_artifact")
        audit_reference = _artifact_reference(audit_result)
        audit_versions[result.run_view.event_head.sequence_no] = audit_reference
        self._post_terminal_commit_failpoint("after_audit_index")
        view = _decorate_projection(
            result.run_view,
            terminal_reference,
            audit_reference,
        )
        return LifecycleCommitReceipt.model_validate_json(
            canonical_json_bytes(
                result.model_dump(mode="json")
                | {
                    "after_head": view.event_head.model_dump(mode="json"),
                    "artifact_references": [
                        *(
                            reference.model_dump(mode="json")
                            for reference in result.artifact_references
                        ),
                        audit_reference.model_dump(mode="json"),
                    ],
                    "run_view": view.model_dump(mode="json"),
                }
            )
        )

    def _decorate_result(
        self, result: LifecycleCommitReceipt
    ) -> LifecycleCommitReceipt:
        view = self._decorate_view(result.run_view)
        if view == result.run_view:
            return result
        return LifecycleCommitReceipt.model_validate_json(
            canonical_json_bytes(
                result.model_dump(mode="json")
                | {"run_view": view.model_dump(mode="json")}
            )
        )

    def _decorate_view(self, view: RunProjection) -> RunProjection:
        terminal_state = self._terminal_results.get(view.run_id)
        if terminal_state is None or view.event_head.sequence_no < terminal_state[0]:
            return view
        audit_versions = self._audit_projections.get(view.run_id, {})
        eligible = [
            sequence
            for sequence in audit_versions
            if sequence <= view.event_head.sequence_no
        ]
        if not eligible:
            raise ArtifactIntegrityError(f"run-audit:{view.run_id}")
        audit_sequence = max(eligible)
        audit_reference = audit_versions[audit_sequence]
        records = self._load_run_event_records(view.run_id)
        terminal_sequence, terminal_reference = terminal_state
        self._verify_projection_artifacts(
            run_id=view.run_id,
            terminal_head=EventHead(
                run_id=view.run_id,
                sequence_no=terminal_sequence,
                event_hash=records[terminal_sequence].event_hash,
            ),
            terminal_reference=terminal_reference,
            audit_head=EventHead(
                run_id=view.run_id,
                sequence_no=audit_sequence,
                event_hash=records[audit_sequence].event_hash,
            ),
            audit_reference=audit_reference,
        )
        return _decorate_projection(view, terminal_reference, audit_reference)

    def _require_references(self, references: tuple[ArtifactReference, ...]) -> None:
        for reference in references:
            result = self.get(ArtifactId(root=reference.artifact_id))
            if result.envelope.payload_hash != reference.payload_hash:
                raise TerminalProvenanceError(reference.artifact_id)

    def _require_event_references(self, event: RunEvent) -> None:
        self._require_references(_event_artifact_references(event))
        for artifact_id in _event_unhashed_artifact_ids(event):
            self.get(ArtifactId(root=artifact_id))

    def _put_lifecycle_model(
        self,
        artifact_type: str,
        model: StrictFrozenModel,
        *,
        parents: tuple[ArtifactReference, ...],
        created_by: str,
        created_at: str,
    ) -> ArtifactPutResult:
        prepared = self._prepare(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": artifact_type,
                "payload_bytes": canonical_json_bytes(
                    model.model_dump(mode="json", round_trip=True, warnings="error")
                ),
                "parent_artifact_ids": sorted(
                    {item.artifact_id for item in parents},
                    key=lambda item: item.encode("utf-8"),
                ),
                "created_by": created_by,
                "created_at": created_at,
                "source_evidence_ids": [],
            }
        )
        return self._put_prepared(prepared)

    def project(
        self,
        run_id: RunId,
        as_of: VerifiedEventHead,
        *,
        projector_version: str,
        projector_hash: Sha256Digest,
    ) -> RunProjection:
        query = _validated_projection_query(
            run_id,
            as_of,
            projector_version,
            projector_hash,
        )
        if (
            query.projector_version != RUN_PROJECTOR_VERSION
            or query.projector_hash != RUN_PROJECTOR_HASH
        ):
            raise RunProjectorIdentityError(query.projector_version)
        with self._lock:
            raw_records = self._event_records.get(query.run_id)
            if raw_records is None:
                raise UnknownRunError(query.run_id)
            records = self._load_run_event_records(query.run_id)
            project_head = EventHead(
                run_id=query.run_id,
                sequence_no=query.as_of_sequence_no,
                event_hash=query.as_of_event_head_hash,
            )
            return self._decorate_view(
                self._project_run_records(records, as_of_head=project_head)
            )

    def put(self, request: ArtifactPutInput) -> ArtifactPutResult:
        prepared = self._prepare(request)
        if prepared.artifact_type in _LIFECYCLE_DERIVED_ARTIFACT_TYPES:
            raise ArtifactWriteAuthorityError(prepared.artifact_type)
        return self._put_prepared(prepared)

    def _put_prepared(self, prepared: _PreparedArtifact) -> ArtifactPutResult:
        artifact_id = _default_artifact_id(prepared.envelope_bytes)

        with self._lock:
            if artifact_id in prepared.parent_artifact_ids:
                raise ArtifactCycleError(artifact_id.root)
            direct_parents = self._verified_parents(
                prepared.parent_artifact_ids,
                integrity_subject=None,
            )
            for parent_id in prepared.parent_artifact_ids:
                if self._has_ancestor(parent_id, artifact_id):
                    raise ArtifactCycleError(artifact_id.root)
            self._validate_prepared_parent_contract(prepared, direct_parents)
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
                    direct_parents,
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
            direct_parents = self._verified_parents(
                stored.parent_artifact_ids,
                integrity_subject=stored.artifact_id.root,
            )
            return self._read_result(stored, direct_parents)

    def lineage(self, artifact_id: ArtifactId) -> ArtifactLineageResult:
        with self._lock:
            stored = self._require_artifact(artifact_id)
            direct_parents = self._verified_parents(
                stored.parent_artifact_ids,
                integrity_subject=stored.artifact_id.root,
            )
            self._verify(stored, direct_parents)
            return ArtifactLineageResult(
                schema_version="automarkov.artifact-lineage-result.v1",
                artifact_ids=stored.parent_artifact_ids,
            )

    def _require_artifact(self, artifact_id: ArtifactId) -> _StoredArtifact:
        try:
            return self._artifacts[artifact_id.root]
        except KeyError as error:
            raise UnknownArtifactError(artifact_id.root) from error

    def _verified_parents(
        self,
        parent_artifact_ids: tuple[ArtifactId, ...],
        *,
        integrity_subject: str | None,
    ) -> tuple[_VerifiedParent, ...]:
        verified: dict[str, _VerifiedParent] = {}
        active: set[str] = set()
        pending = [(parent.root, False) for parent in reversed(parent_artifact_ids)]
        while pending:
            current_id, leaving = pending.pop()
            if current_id in verified:
                continue
            stored = self._artifacts.get(current_id)
            if stored is None:
                if integrity_subject is None:
                    raise MissingArtifactParentError(current_id)
                raise ArtifactIntegrityError(integrity_subject)
            if leaving:
                try:
                    direct_parents = tuple(
                        verified[parent.root] for parent in stored.parent_artifact_ids
                    )
                    self._verify(stored, direct_parents)
                except (ArtifactIntegrityError, KeyError) as error:
                    subject = integrity_subject or stored.artifact_id.root
                    raise ArtifactIntegrityError(subject) from error
                verified[current_id] = _VerifiedParent(
                    artifact_id=stored.artifact_id,
                    artifact_type=stored.artifact_type,
                    payload_hash=stored.payload_hash,
                )
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
                if parent.root not in verified
            )
        return tuple(verified[parent.root] for parent in parent_artifact_ids)

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
        event_authenticator: EventAuthenticator | None = None,
        command_authority: CommandAuthority | None = None,
    ) -> None:
        super().__init__(schema_registry, event_authenticator, command_authority)
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

            self._require_schema_integrity(
                actual_rows,
                require_event_contracts=False,
            )
            self._ensure_event_schema_contracts()
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def commit(
        self,
        request: Mapping[str, object],
        *,
        context: AuthenticatedCommandContext,
    ) -> LifecycleCommitResult:
        command = validate_lifecycle_command(request)
        if isinstance(command, CommitTerminalCommand):
            return self._commit_terminal(command, context)
        if isinstance(command, CreateReplacementRunCommand):
            return self._commit_replacement(command, context)
        if isinstance(command, CreateClarificationChildRunCommand):
            return self._commit_clarification_child(command, context)
        fingerprint = _lifecycle_command_fingerprint(command)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_schema_integrity()
                self._authenticate_command_context(command, context)
                prior_result = self._prior_sql_lifecycle_result(
                    command.command_id,
                    command.idempotency_key,
                    fingerprint,
                )
                if prior_result is not None:
                    self._connection.commit()
                    return prior_result

                existing = self._load_run_event_records(command.run_id)
                current_state = (
                    self._project_run_records(existing).state if existing else None
                )
                if current_state != command.expected_state and any(
                    event.sequence_no >= len(existing) for event in command.events
                ):
                    raise EventHeadConflictError(command.run_id)
                authentication_records = existing
                pending_signed_nonces: set[str] = set()
                pending_signed_slots: set[tuple[str, str, int]] = set()
                for event in command.events:
                    if event.run_id != command.run_id:
                        raise ValueError(
                            "append command run_id does not match an event"
                        )
                    replayed = self._connection.execute(
                        "SELECT run_id, sequence_no, rowid FROM run_events "
                        "WHERE event_id = ?",
                        (event.event_id,),
                    ).fetchone()
                    if replayed is not None:
                        raise EventReplayConflictError(event.event_id)
                    self._authenticate_event(event, authentication_records)
                    signed_nonce = _signed_event_nonce(event)
                    signed_slot = _signed_event_slot(event)
                    if signed_nonce is not None and (
                        signed_nonce in pending_signed_nonces
                        or self._connection.execute(
                            "SELECT 1 FROM signed_event_nonces WHERE nonce_b64url = ?",
                            (signed_nonce,),
                        ).fetchone()
                        is not None
                    ):
                        raise EventReplayConflictError(event.event_id)
                    if signed_slot is not None and (
                        signed_slot in pending_signed_slots
                        or self._connection.execute(
                            "SELECT 1 FROM signed_event_nonces "
                            "WHERE signing_key_id = ? AND run_id = ? "
                            "AND sequence_no = ?",
                            signed_slot,
                        ).fetchone()
                        is not None
                    ):
                        raise EventReplayConflictError(event.event_id)
                    if signed_nonce is not None:
                        pending_signed_nonces.add(signed_nonce)
                    if signed_slot is not None:
                        pending_signed_slots.add(signed_slot)
                    self._require_sql_event_references(event)
                    authentication_records += (_record_for_event(event),)
                updated, step_result = _append_closed_event_batch(
                    existing,
                    tuple(command.events),
                    expected_head=command.expected_head,
                    budget_snapshots=self._budget_snapshots_for_events(
                        tuple(record.event for record in authentication_records)
                    ),
                )
                newly_inserted = list(updated[len(existing) :])
                result = _with_commit_records(
                    step_result,
                    command,
                    before_head=command.expected_head,
                    records=tuple(newly_inserted),
                )
                audit_reference: ArtifactReference | None = None
                if current_state is not None and current_state in TERMINAL_STATES:
                    result, audit_reference = (
                        self._append_post_terminal_audit_in_transaction(
                            command,
                            result,
                        )
                    )
                else:
                    result = self._decorate_sql_result(result)
                for record in newly_inserted:
                    event = record.event
                    self._connection.execute(
                        "INSERT INTO run_events(run_id, sequence_no, event_id, "
                        "event_hash, record_bytes) VALUES (?, ?, ?, ?, ?)",
                        (
                            event.run_id,
                            event.sequence_no,
                            event.event_id,
                            record.event_hash,
                            _record_bytes(record),
                        ),
                    )
                    signed_nonce = _signed_event_nonce(event)
                    if signed_nonce is not None:
                        signed_slot = _signed_event_slot(event)
                        if signed_slot is None:  # pragma: no cover - 联合分支已限定。
                            raise AssertionError("signed event slot is unavailable")
                        self._connection.execute(
                            "INSERT INTO signed_event_nonces(signing_key_id, "
                            "nonce_b64url, run_id, sequence_no, event_id) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (
                                signed_slot[0],
                                signed_nonce,
                                signed_slot[1],
                                signed_slot[2],
                                event.event_id,
                            ),
                        )
                if audit_reference is not None:
                    self._post_terminal_commit_failpoint("after_event_records")
                if newly_inserted:
                    final_record = newly_inserted[-1]
                    self._connection.execute(
                        "INSERT INTO run_heads(run_id, sequence_no, event_hash) "
                        "VALUES (?, ?, ?) ON CONFLICT(run_id) DO UPDATE SET "
                        "sequence_no=excluded.sequence_no, event_hash=excluded.event_hash",
                        (
                            command.run_id,
                            final_record.event.sequence_no,
                            final_record.event_hash,
                        ),
                    )
                if audit_reference is not None:
                    self._post_terminal_commit_failpoint("after_head")
                if audit_reference is not None:
                    self._connection.execute(
                        "INSERT INTO run_audit_projections(run_id, "
                        "as_of_sequence_no, projector_hash, artifact_id, "
                        "payload_hash) VALUES (?, ?, ?, ?, ?)",
                        (
                            command.run_id,
                            result.run_view.event_head.sequence_no,
                            RUN_PROJECTOR_HASH,
                            audit_reference.artifact_id,
                            audit_reference.payload_hash,
                        ),
                    )
                    self._post_terminal_commit_failpoint("after_audit_index")
                result_bytes = canonical_json_bytes(
                    result.model_dump(mode="json", round_trip=True, warnings="error")
                )
                self._connection.execute(
                    "INSERT INTO lifecycle_commands(command_id, idempotency_key, "
                    "command_fingerprint, result_bytes) VALUES (?, ?, ?, ?)",
                    (
                        command.command_id,
                        command.idempotency_key,
                        fingerprint,
                        result_bytes,
                    ),
                )
                if audit_reference is not None:
                    self._post_terminal_commit_failpoint("after_receipt")
                self._connection.commit()
                return LifecycleCommitReceipt.model_validate_json(result_bytes)
            except BaseException:
                self._connection.rollback()
                raise

    def _require_sql_cross_run_absent(
        self, parent_run_id: str, child_run_id: str
    ) -> None:
        child_exists = self._connection.execute(
            "SELECT 1 FROM run_heads WHERE run_id = ?",
            (child_run_id,),
        ).fetchone()
        edge_exists = self._connection.execute(
            "SELECT 1 FROM run_replacements "
            "WHERE parent_run_id = ? OR child_run_id = ? "
            "UNION ALL SELECT 1 FROM run_clarification_continuations "
            "WHERE parent_run_id = ? OR child_run_id = ? LIMIT 1",
            (parent_run_id, child_run_id, parent_run_id, child_run_id),
        ).fetchone()
        if child_exists is not None or edge_exists is not None:
            raise EventHeadConflictError(child_run_id)

    def _require_sql_events_fresh(self, events: tuple[RunEvent, ...]) -> None:
        event_ids = tuple(event.event_id for event in events)
        nonces = tuple(
            nonce
            for event in events
            if (nonce := _signed_event_nonce(event)) is not None
        )
        slots = tuple(
            slot for event in events if (slot := _signed_event_slot(event)) is not None
        )
        if (
            len(set(event_ids)) != len(event_ids)
            or len(set(nonces)) != len(nonces)
            or len(set(slots)) != len(slots)
        ):
            raise EventReplayConflictError(event_ids[0])
        for event in events:
            nonce = _signed_event_nonce(event)
            slot = _signed_event_slot(event)
            if (
                self._connection.execute(
                    "SELECT 1 FROM run_events WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()
                is not None
                or nonce is not None
                and self._connection.execute(
                    "SELECT 1 FROM signed_event_nonces WHERE nonce_b64url = ?",
                    (nonce,),
                ).fetchone()
                is not None
                or slot is not None
                and self._connection.execute(
                    "SELECT 1 FROM signed_event_nonces WHERE signing_key_id = ? "
                    "AND run_id = ? AND sequence_no = ?",
                    slot,
                ).fetchone()
                is not None
            ):
                raise EventReplayConflictError(event.event_id)

    def _insert_sql_event_records(self, records: tuple[EventRecord, ...]) -> None:
        for record in records:
            event = record.event
            self._connection.execute(
                "INSERT INTO run_events(run_id, sequence_no, event_id, event_hash, "
                "record_bytes) VALUES (?, ?, ?, ?, ?)",
                (
                    event.run_id,
                    event.sequence_no,
                    event.event_id,
                    record.event_hash,
                    _record_bytes(record),
                ),
            )
            nonce = _signed_event_nonce(event)
            slot = _signed_event_slot(event)
            if nonce is not None and slot is not None:
                self._connection.execute(
                    "INSERT INTO signed_event_nonces(signing_key_id, nonce_b64url, "
                    "run_id, sequence_no, event_id) VALUES (?, ?, ?, ?, ?)",
                    (slot[0], nonce, slot[1], slot[2], event.event_id),
                )

    def _commit_replacement(
        self,
        command: CreateReplacementRunCommand,
        context: AuthenticatedCommandContext,
    ) -> CrossRunLifecycleCommitReceipt:
        fingerprint = _lifecycle_command_fingerprint(command)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_schema_integrity()
                self._authenticate_command_context(command, context)
                prior = self._prior_sql_lifecycle_result(
                    command.command_id,
                    command.idempotency_key,
                    fingerprint,
                )
                if prior is not None:
                    if not isinstance(prior, CrossRunLifecycleCommitReceipt):
                        raise ArtifactIntegrityError(
                            f"lifecycle-command:{command.command_id}"
                        )
                    self._connection.commit()
                    return prior

                parent_records = self._load_run_event_records(command.parent_run_id)
                if not parent_records:
                    raise UnknownRunError(command.parent_run_id)
                self._require_sql_cross_run_absent(
                    command.parent_run_id,
                    command.child_run_id,
                )
                parent_projection = self._project_run_records(parent_records)
                self._validate_replacement_contract(
                    command,
                    parent_records,
                    parent_projection,
                    context,
                )
                parent_events = (
                    command.run_superseded_event,
                    command.parent_terminal_transition,
                )
                child_event = command.replacement_run_created_event
                all_events = (*parent_events, child_event)
                self._require_sql_events_fresh(all_events)
                self._authenticate_event(parent_events[0], parent_records)
                superseded_record = _record_for_event(parent_events[0])
                self._authenticate_event(
                    parent_events[1],
                    parent_records + (superseded_record,),
                )
                self._authenticate_event(child_event, ())
                for event in all_events:
                    self._require_sql_event_references(event)
                transition_record = _record_for_event(parent_events[1])
                child_record = _record_for_event(child_event)
                parent_view = self._project_run_records(
                    parent_records + (superseded_record, transition_record)
                )
                child_view = self._project_run_records((child_record,))

                process = command.process_terminal_record
                self._require_sql_references(
                    (
                        process.job_manifest,
                        *process.payload_outputs,
                        process.resource_usage,
                    )
                )
                process_result = self._put_lifecycle_model_in_transaction(
                    "process_execution_terminal_record",
                    process,
                    parents=(
                        process.job_manifest,
                        *process.payload_outputs,
                        process.resource_usage,
                    ),
                    created_by=process.principal_id,
                    created_at=process.created_at,
                )
                process_reference = _artifact_reference(process_result)
                self._cross_run_commit_failpoint("after_process_artifact")
                approvals = _active_approval_snapshots(parent_records)
                terminal = _replacement_terminal_result_payload(
                    command,
                    _closed_append_step(transition_record, parent_view),
                    process_reference,
                    approvals,
                )
                terminal_result = self._put_lifecycle_model_in_transaction(
                    "terminal_result",
                    terminal,
                    parents=(
                        command.fixed_commit_job_manifest,
                        process_reference,
                        *process.payload_outputs,
                    ),
                    created_by=process.principal_id,
                    created_at=command.parent_terminal_transition.issued_at,
                )
                terminal_reference = _artifact_reference(terminal_result)
                audit = _replacement_root_audit_projection_payload(
                    command,
                    _closed_append_step(transition_record, parent_view),
                    terminal_reference,
                    approvals,
                )
                audit_result = self._put_lifecycle_model_in_transaction(
                    "run_audit_projection",
                    audit,
                    parents=(terminal_reference,),
                    created_by=process.principal_id,
                    created_at=command.parent_terminal_transition.issued_at,
                )
                audit_reference = _artifact_reference(audit_result)
                self._cross_run_commit_failpoint("after_terminal_artifacts")
                attestation = command.execution_attestation
                if (
                    attestation.process_terminal_record != process_reference
                    or attestation.terminal_result != terminal_reference
                ):
                    raise TerminalProvenanceError(command.parent_run_id)
                attestation_result = self._put_lifecycle_model_in_transaction(
                    "execution_attestation",
                    attestation,
                    parents=(
                        attestation.job_manifest,
                        attestation.process_terminal_record,
                        *attestation.payload_outputs,
                        terminal_reference,
                    ),
                    created_by=attestation.principal_id,
                    created_at=attestation.issued_at,
                )
                attestation_reference = _artifact_reference(attestation_result)
                self._cross_run_commit_failpoint("after_attestation_artifact")
                parent_view = _decorate_projection(
                    parent_view,
                    terminal_reference,
                    audit_reference,
                )
                receipt = _cross_run_receipt(
                    command,
                    parent_before_head=_event_head_from_verified(
                        command.expected_parent_head
                    ),
                    parent_records=(superseded_record, transition_record),
                    child_records=(child_record,),
                    parent_view=parent_view,
                    child_view=child_view,
                    process_reference=process_reference,
                    terminal_reference=terminal_reference,
                    audit_reference=audit_reference,
                    attestation_reference=attestation_reference,
                )

                self._insert_sql_event_records(
                    (superseded_record, transition_record, child_record)
                )
                self._cross_run_commit_failpoint("after_event_records")
                updated_head = self._connection.execute(
                    "UPDATE run_heads SET sequence_no = ?, event_hash = ? "
                    "WHERE run_id = ? AND sequence_no = ? AND event_hash = ?",
                    (
                        transition_record.event.sequence_no,
                        transition_record.event_hash,
                        command.parent_run_id,
                        command.expected_parent_head.sequence_no,
                        command.expected_parent_head.event_hash.root,
                    ),
                )
                if updated_head.rowcount != 1:
                    raise EventHeadConflictError(command.parent_run_id)
                self._connection.execute(
                    "INSERT INTO run_heads(run_id, sequence_no, event_hash) "
                    "VALUES (?, ?, ?)",
                    (
                        command.child_run_id,
                        child_record.event.sequence_no,
                        child_record.event_hash,
                    ),
                )
                self._connection.execute(
                    "INSERT INTO run_terminal_results(run_id, terminal_sequence_no, "
                    "artifact_id, payload_hash) VALUES (?, ?, ?, ?)",
                    (
                        command.parent_run_id,
                        transition_record.event.sequence_no,
                        terminal_reference.artifact_id,
                        terminal_reference.payload_hash,
                    ),
                )
                self._connection.execute(
                    "INSERT INTO run_audit_projections(run_id, as_of_sequence_no, "
                    "projector_hash, artifact_id, payload_hash) VALUES (?, ?, ?, ?, ?)",
                    (
                        command.parent_run_id,
                        transition_record.event.sequence_no,
                        RUN_PROJECTOR_HASH,
                        audit_reference.artifact_id,
                        audit_reference.payload_hash,
                    ),
                )
                prerequisite = command.cause_prerequisite
                prerequisite_event_id = (
                    prerequisite.failed_waiting_event.event_id
                    if isinstance(prerequisite, RuntimeReplacementPrerequisite)
                    else prerequisite.revocation_event.event_id
                )
                self._connection.execute(
                    "INSERT INTO run_replacements(parent_run_id, child_run_id, "
                    "supersession_event_id, prerequisite_event_id, "
                    "replacement_ordinal, replacement_policy_artifact_id, "
                    "process_terminal_artifact_id, terminal_result_artifact_id, "
                    "execution_attestation_artifact_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        command.parent_run_id,
                        command.child_run_id,
                        command.run_superseded_event.event_id,
                        prerequisite_event_id,
                        command.run_superseded_event.replacement_ordinal,
                        command.replacement_policy.artifact_id,
                        process_reference.artifact_id,
                        terminal_reference.artifact_id,
                        attestation_reference.artifact_id,
                    ),
                )
                self._cross_run_commit_failpoint("after_indexes")
                result_bytes = canonical_json_bytes(
                    receipt.model_dump(mode="json", round_trip=True, warnings="error")
                )
                self._connection.execute(
                    "INSERT INTO lifecycle_commands(command_id, idempotency_key, "
                    "command_fingerprint, result_bytes) VALUES (?, ?, ?, ?)",
                    (
                        command.command_id,
                        command.idempotency_key,
                        fingerprint,
                        result_bytes,
                    ),
                )
                self._cross_run_commit_failpoint("after_receipt")
                self._connection.commit()
                return CrossRunLifecycleCommitReceipt.model_validate_json(result_bytes)
            except BaseException:
                self._connection.rollback()
                raise

    def _commit_clarification_child(
        self,
        command: CreateClarificationChildRunCommand,
        context: AuthenticatedCommandContext,
    ) -> CrossRunLifecycleCommitReceipt:
        fingerprint = _lifecycle_command_fingerprint(command)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_schema_integrity()
                self._authenticate_command_context(command, context)
                prior = self._prior_sql_lifecycle_result(
                    command.command_id,
                    command.idempotency_key,
                    fingerprint,
                )
                if prior is not None:
                    if not isinstance(prior, CrossRunLifecycleCommitReceipt):
                        raise ArtifactIntegrityError(
                            f"lifecycle-command:{command.command_id}"
                        )
                    self._connection.commit()
                    return prior

                parent_records = self._load_run_event_records(command.parent_run_id)
                if not parent_records:
                    raise UnknownRunError(command.parent_run_id)
                self._require_sql_cross_run_absent(
                    command.parent_run_id,
                    command.child_run_id,
                )
                parent_projection = self._decorate_sql_view(
                    self._project_run_records(parent_records)
                )
                self._validate_clarification_contract(
                    command,
                    parent_records,
                    parent_projection,
                    context,
                )
                event = command.clarification_child_run_created_event
                self._require_sql_events_fresh((event,))
                self._authenticate_event(event, ())
                self._require_sql_event_references(event)
                child_record = _record_for_event(event)
                child_view = self._project_run_records((child_record,))
                receipt = _cross_run_receipt(
                    command,
                    parent_before_head=_event_head_from_verified(
                        command.expected_parent_head
                    ),
                    parent_records=(),
                    child_records=(child_record,),
                    parent_view=parent_projection,
                    child_view=child_view,
                )
                self._insert_sql_event_records((child_record,))
                self._connection.execute(
                    "INSERT INTO run_heads(run_id, sequence_no, event_hash) "
                    "VALUES (?, ?, ?)",
                    (
                        command.child_run_id,
                        child_record.event.sequence_no,
                        child_record.event_hash,
                    ),
                )
                self._cross_run_commit_failpoint("after_child_event")
                self._connection.execute(
                    "INSERT INTO run_clarification_continuations(parent_run_id, "
                    "child_run_id, child_event_id, signed_answer_bundle_artifact_id, "
                    "continuation_policy_artifact_id) VALUES (?, ?, ?, ?, ?)",
                    (
                        command.parent_run_id,
                        command.child_run_id,
                        event.event_id,
                        command.signed_answer_bundle.artifact_id,
                        command.continuation_policy.artifact_id,
                    ),
                )
                self._cross_run_commit_failpoint("after_indexes")
                result_bytes = canonical_json_bytes(
                    receipt.model_dump(mode="json", round_trip=True, warnings="error")
                )
                self._connection.execute(
                    "INSERT INTO lifecycle_commands(command_id, idempotency_key, "
                    "command_fingerprint, result_bytes) VALUES (?, ?, ?, ?)",
                    (
                        command.command_id,
                        command.idempotency_key,
                        fingerprint,
                        result_bytes,
                    ),
                )
                self._cross_run_commit_failpoint("after_receipt")
                self._connection.commit()
                return CrossRunLifecycleCommitReceipt.model_validate_json(result_bytes)
            except BaseException:
                self._connection.rollback()
                raise

    def _commit_terminal(
        self,
        command: CommitTerminalCommand,
        context: AuthenticatedCommandContext,
    ) -> LifecycleCommitReceipt:
        cause = command.events[0]
        transition = cast(StateTransitioned, command.events[1])
        process = command.process_terminal_record
        if (
            process.run_id != command.run_id
            or process.job_manifest != command.fixed_commit_job_manifest
            or cause.actor_principal_id != command.actor_principal_id
            or transition.actor_principal_id != command.actor_principal_id
        ):
            raise TerminalProvenanceError(command.run_id)
        fingerprint = _lifecycle_command_fingerprint(command)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_schema_integrity()
                self._authenticate_command_context(command, context)
                prior_result = self._prior_sql_lifecycle_result(
                    command.command_id,
                    command.idempotency_key,
                    fingerprint,
                )
                if prior_result is not None:
                    if not isinstance(prior_result, LifecycleCommitReceipt):
                        raise ArtifactIntegrityError(
                            f"lifecycle-command:{command.command_id}"
                        )
                    self._connection.commit()
                    return prior_result

                existing = self._load_run_event_records(command.run_id)
                self._require_terminal_approvals(
                    existing,
                    command.terminal_time_approvals,
                )
                current_projection = self._project_run_records(existing)
                if process.experiment_id != current_projection.experiment_id:
                    raise TerminalProvenanceError(command.run_id)
                if current_projection.state != command.expected_state:
                    raise EventHeadConflictError(command.run_id)
                if cause.event_id == transition.event_id:
                    raise EventReplayConflictError(cause.event_id)
                updated = existing
                expected_head = command.expected_head
                transition_result: RunAppendStep | None = None
                pending_signed_nonces: set[str] = set()
                pending_signed_slots: set[tuple[str, str, int]] = set()
                for event in (cause, transition):
                    if (
                        self._connection.execute(
                            "SELECT 1 FROM run_events WHERE event_id = ?",
                            (event.event_id,),
                        ).fetchone()
                        is not None
                    ):
                        raise EventReplayConflictError(event.event_id)
                    self._authenticate_event(event, updated)
                    signed_nonce = _signed_event_nonce(event)
                    signed_slot = _signed_event_slot(event)
                    if signed_nonce is not None and (
                        signed_nonce in pending_signed_nonces
                        or self._connection.execute(
                            "SELECT 1 FROM signed_event_nonces WHERE nonce_b64url = ?",
                            (signed_nonce,),
                        ).fetchone()
                        is not None
                    ):
                        raise EventReplayConflictError(event.event_id)
                    if signed_slot is not None and (
                        signed_slot in pending_signed_slots
                        or self._connection.execute(
                            "SELECT 1 FROM signed_event_nonces "
                            "WHERE signing_key_id = ? AND run_id = ? "
                            "AND sequence_no = ?",
                            signed_slot,
                        ).fetchone()
                        is not None
                    ):
                        raise EventReplayConflictError(event.event_id)
                    if signed_nonce is not None:
                        pending_signed_nonces.add(signed_nonce)
                    if signed_slot is not None:
                        pending_signed_slots.add(signed_slot)
                    self._require_sql_event_references(event)
                    updated, transition_result = append_record(
                        updated,
                        event,
                        expected_head=expected_head,
                        allow_terminal=True,
                        budget_snapshots=self._budget_snapshots_for_events(
                            tuple(record.event for record in updated) + (event,)
                        ),
                    )
                    expected_head = transition_result.run_view.event_head
                if (
                    transition_result is None
                ):  # pragma: no cover - schema 要求精确事件对。
                    raise AssertionError("terminal command contained no events")
                self._require_sql_references(
                    (
                        process.job_manifest,
                        *process.payload_outputs,
                        process.resource_usage,
                    )
                )
                process_result = self._put_lifecycle_model_in_transaction(
                    "process_execution_terminal_record",
                    process,
                    parents=(
                        process.job_manifest,
                        *process.payload_outputs,
                        process.resource_usage,
                    ),
                    created_by=process.principal_id,
                    created_at=process.created_at,
                )
                self._terminal_commit_failpoint("after_process_artifact")
                process_reference = _artifact_reference(process_result)
                terminal = _terminal_result_payload(
                    command,
                    transition_result,
                    process_reference,
                )
                terminal_result = self._put_lifecycle_model_in_transaction(
                    "terminal_result",
                    terminal,
                    parents=(
                        command.fixed_commit_job_manifest,
                        process_reference,
                        *process.payload_outputs,
                    ),
                    created_by=process.principal_id,
                    created_at=command.created_at,
                )
                self._terminal_commit_failpoint("after_terminal_artifact")
                terminal_reference = _artifact_reference(terminal_result)
                audit = _root_audit_projection_payload(
                    command,
                    transition_result,
                    terminal_reference,
                )
                audit_result = self._put_lifecycle_model_in_transaction(
                    "run_audit_projection",
                    audit,
                    parents=(terminal_reference,),
                    created_by=process.principal_id,
                    created_at=command.created_at,
                )
                self._terminal_commit_failpoint("after_audit_artifact")
                audit_reference = _artifact_reference(audit_result)
                view_payload = transition_result.run_view.model_dump(mode="json")
                view_payload["terminal_result"] = terminal_reference.model_dump(
                    mode="json"
                )
                view_payload["run_audit_projection"] = audit_reference.model_dump(
                    mode="json"
                )
                view = RunProjection.model_validate_json(
                    canonical_json_bytes(view_payload)
                )
                result = _terminal_append_result(
                    command,
                    cast(tuple[EventRecord, EventRecord], updated[-2:]),
                    command.expected_head,
                    view,
                    process_reference,
                    terminal_reference,
                    audit_reference,
                )
                new_records = updated[-2:]
                for record in new_records:
                    self._connection.execute(
                        "INSERT INTO run_events(run_id, sequence_no, event_id, "
                        "event_hash, record_bytes) VALUES (?, ?, ?, ?, ?)",
                        (
                            command.run_id,
                            record.event.sequence_no,
                            record.event.event_id,
                            record.event_hash,
                            _record_bytes(record),
                        ),
                    )
                    signed_nonce = _signed_event_nonce(record.event)
                    if signed_nonce is not None:
                        signed_slot = _signed_event_slot(record.event)
                        if signed_slot is None:  # pragma: no cover - 联合分支已限定。
                            raise AssertionError("signed event slot is unavailable")
                        self._connection.execute(
                            "INSERT INTO signed_event_nonces(signing_key_id, "
                            "nonce_b64url, run_id, sequence_no, event_id) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (
                                signed_slot[0],
                                signed_nonce,
                                signed_slot[1],
                                signed_slot[2],
                                record.event.event_id,
                            ),
                        )
                self._terminal_commit_failpoint("after_event_records")
                record = new_records[-1]
                updated_head = self._connection.execute(
                    "UPDATE run_heads SET sequence_no = ?, event_hash = ? "
                    "WHERE run_id = ? AND sequence_no = ? AND event_hash = ?",
                    (
                        record.event.sequence_no,
                        record.event_hash,
                        command.run_id,
                        command.expected_head.sequence_no,
                        command.expected_head.event_hash,
                    ),
                )
                if updated_head.rowcount != 1:
                    raise EventHeadConflictError(command.run_id)
                self._terminal_commit_failpoint("after_head")
                self._connection.execute(
                    "INSERT INTO run_terminal_results(run_id, terminal_sequence_no, "
                    "artifact_id, payload_hash) VALUES (?, ?, ?, ?)",
                    (
                        command.run_id,
                        record.event.sequence_no,
                        terminal_reference.artifact_id,
                        terminal_reference.payload_hash,
                    ),
                )
                self._terminal_commit_failpoint("after_terminal_index")
                self._connection.execute(
                    "INSERT INTO run_audit_projections(run_id, as_of_sequence_no, "
                    "projector_hash, artifact_id, payload_hash) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        command.run_id,
                        record.event.sequence_no,
                        RUN_PROJECTOR_HASH,
                        audit_reference.artifact_id,
                        audit_reference.payload_hash,
                    ),
                )
                self._terminal_commit_failpoint("after_audit_index")
                result_bytes = canonical_json_bytes(result.model_dump(mode="json"))
                self._connection.execute(
                    "INSERT INTO lifecycle_commands(command_id, idempotency_key, "
                    "command_fingerprint, result_bytes) VALUES (?, ?, ?, ?)",
                    (
                        command.command_id,
                        command.idempotency_key,
                        fingerprint,
                        result_bytes,
                    ),
                )
                self._terminal_commit_failpoint("after_receipt")
                self._connection.commit()
                return LifecycleCommitReceipt.model_validate_json(result_bytes)
            except BaseException:
                self._connection.rollback()
                raise

    def _prior_sql_lifecycle_result(
        self,
        command_id: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> LifecycleCommitResult | None:
        rows = self._connection.execute(
            "SELECT rowid, command_id, idempotency_key, command_fingerprint "
            "FROM lifecycle_commands WHERE command_id = ? OR idempotency_key = ?",
            (command_id, idempotency_key),
        ).fetchall()
        if len(rows) > 1:
            raise ArtifactIntegrityError(f"lifecycle-command:{command_id}")
        if not rows:
            return None
        rowid, _, _, prior_fingerprint = rows[0]
        if prior_fingerprint != fingerprint:
            raise EventReplayConflictError(command_id)
        raw_result = self._read_bounded_blob(
            "lifecycle_commands",
            "result_bytes",
            int(rowid),
            MAX_JSON_PAYLOAD_BYTES,
            command_id,
        )
        try:
            receipt = TypeAdapter(LifecycleCommitResult).validate_json(
                raw_result,
                strict=True,
            )
        except (TypeError, ValueError) as error:
            raise ArtifactIntegrityError(f"lifecycle-command:{command_id}") from error
        if (
            receipt.command_id != command_id
            or receipt.idempotency_key != idempotency_key
            or receipt.command_fingerprint != fingerprint
        ):
            raise ArtifactIntegrityError(f"lifecycle-command:{command_id}")
        if isinstance(receipt, LifecycleCommitReceipt):
            records = self._load_run_event_records(receipt.run_id)
            self._verify_receipt_records(receipt, records)
            self._verify_receipt_artifacts(receipt)
            expected_view = self._decorate_sql_view(
                self._project_run_records(records, as_of_head=receipt.after_head)
            )
            if receipt.run_view != expected_view:
                raise ArtifactIntegrityError(f"lifecycle-receipt:{command_id}")
            return receipt

        parent_records = self._load_run_event_records(receipt.parent_run_id)
        child_records = self._load_run_event_records(receipt.child_run_id)
        if any(
            record.event.sequence_no >= len(records)
            or records[record.event.sequence_no] != record
            for records, receipt_records in (
                (parent_records, receipt.parent_event_records),
                (child_records, receipt.child_event_records),
            )
            for record in receipt_records
        ):
            raise ArtifactIntegrityError(f"lifecycle-receipt:{command_id}")
        expected_parent = self._decorate_sql_view(
            self._project_run_records(
                parent_records,
                as_of_head=receipt.parent_after_head,
            )
        )
        expected_child = self._project_run_records(
            child_records,
            as_of_head=receipt.child_after_head,
        )
        self._verify_cross_receipt_artifacts(receipt)
        if (
            receipt.parent_run_view != expected_parent
            or receipt.child_run_view != expected_child
        ):
            raise ArtifactIntegrityError(f"lifecycle-receipt:{command_id}")
        if receipt.command_type == "create_replacement_run":
            process_reference = cast(
                ArtifactReference,
                receipt.process_execution_terminal_record,
            )
            terminal_reference = cast(ArtifactReference, receipt.terminal_result)
            attestation_reference = cast(
                ArtifactReference,
                receipt.execution_attestation,
            )
            edge = self._connection.execute(
                "SELECT child_run_id, supersession_event_id, prerequisite_event_id, "
                "replacement_ordinal, replacement_policy_artifact_id, "
                "process_terminal_artifact_id, terminal_result_artifact_id, "
                "execution_attestation_artifact_id FROM run_replacements "
                "WHERE parent_run_id = ?",
                (receipt.parent_run_id,),
            ).fetchone()
            superseded = cast(
                RunSuperseded,
                receipt.parent_event_records[0].event,
            )
            prerequisite_event_id = (
                superseded.failed_waiting_event_id or superseded.revocation_event_id
            )
            if (
                edge is None
                or edge[0] != receipt.child_run_id
                or edge[1] != superseded.event_id
                or edge[2] != prerequisite_event_id
                or edge[3] != superseded.replacement_ordinal
                or edge[4] != superseded.replacement_policy_artifact_id
                or edge[5] != process_reference.artifact_id
                or edge[6] != terminal_reference.artifact_id
                or edge[7] != attestation_reference.artifact_id
            ):
                raise ArtifactIntegrityError(f"lifecycle-receipt:{command_id}")
        else:
            edge = self._connection.execute(
                "SELECT child_run_id, child_event_id FROM "
                "run_clarification_continuations WHERE parent_run_id = ?",
                (receipt.parent_run_id,),
            ).fetchone()
            if (
                edge is None
                or edge[0] != receipt.child_run_id
                or edge[1] != receipt.child_event_records[0].event.event_id
            ):
                raise ArtifactIntegrityError(f"lifecycle-receipt:{command_id}")
        return receipt

    def _append_post_terminal_audit_in_transaction(
        self,
        command: AppendRunEventsCommand,
        result: LifecycleCommitReceipt,
    ) -> tuple[LifecycleCommitReceipt, ArtifactReference]:
        terminal_reference, previous_reference = self._projection_references_at(
            command.run_id,
            result.event_record.event.sequence_no - 1,
        )
        if terminal_reference is None or previous_reference is None:
            raise ArtifactIntegrityError(f"run-terminal:{command.run_id}")
        previous_result = self.get(ArtifactId(root=previous_reference.artifact_id))
        previous_payload = previous_result.payload_document.model_dump(mode="json")[
            "payload"
        ]
        previous = RunAuditProjection.model_validate_json(
            canonical_json_bytes(previous_payload)
        )
        audit = _next_audit_projection_payload(
            previous_reference,
            previous,
            terminal_reference,
            result.run_view,
            command.events,
        )
        audit_result = self._put_lifecycle_model_in_transaction(
            "run_audit_projection",
            audit,
            parents=(previous_reference, terminal_reference),
            created_by=command.actor_principal_id,
            created_at=command.events[-1].issued_at,
        )
        self._post_terminal_commit_failpoint("after_audit_artifact")
        audit_reference = _artifact_reference(audit_result)
        view = _decorate_projection(
            result.run_view,
            terminal_reference,
            audit_reference,
        )
        return (
            LifecycleCommitReceipt.model_validate_json(
                canonical_json_bytes(
                    result.model_dump(mode="json")
                    | {
                        "after_head": view.event_head.model_dump(mode="json"),
                        "artifact_references": [
                            *(
                                reference.model_dump(mode="json")
                                for reference in result.artifact_references
                            ),
                            audit_reference.model_dump(mode="json"),
                        ],
                        "run_view": view.model_dump(mode="json"),
                    }
                )
            ),
            audit_reference,
        )

    def _decorate_sql_result(
        self, result: LifecycleCommitReceipt
    ) -> LifecycleCommitReceipt:
        view = self._decorate_sql_view(result.run_view)
        if view == result.run_view:
            return result
        return LifecycleCommitReceipt.model_validate_json(
            canonical_json_bytes(
                result.model_dump(mode="json")
                | {"run_view": view.model_dump(mode="json")}
            )
        )

    def _decorate_sql_view(self, view: RunProjection) -> RunProjection:
        terminal_reference, audit_reference = self._projection_references_at(
            view.run_id,
            view.event_head.sequence_no,
        )
        return _decorate_projection(view, terminal_reference, audit_reference)

    def _projection_references_at(
        self,
        run_id: str,
        sequence_no: int,
    ) -> tuple[ArtifactReference | None, ArtifactReference | None]:
        terminal_row = self._connection.execute(
            "SELECT terminal_sequence_no, artifact_id, payload_hash "
            "FROM run_terminal_results WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if terminal_row is None or sequence_no < int(terminal_row[0]):
            return None, None
        audit_row = self._connection.execute(
            "SELECT as_of_sequence_no, projector_hash, artifact_id, payload_hash "
            "FROM run_audit_projections "
            "WHERE run_id = ? AND as_of_sequence_no <= ? AND projector_hash = ? "
            "ORDER BY as_of_sequence_no DESC LIMIT 1",
            (run_id, sequence_no, RUN_PROJECTOR_HASH),
        ).fetchone()
        if audit_row is None:
            raise ArtifactIntegrityError(f"run-audit:{run_id}:{sequence_no}")
        terminal_reference = ArtifactReference(
            artifact_id=str(terminal_row[1]),
            payload_hash=str(terminal_row[2]),
        )
        audit_reference = ArtifactReference(
            artifact_id=str(audit_row[2]),
            payload_hash=str(audit_row[3]),
        )
        self._require_sql_references((terminal_reference, audit_reference))
        terminal_sequence = int(terminal_row[0])
        audit_sequence = int(audit_row[0])
        head_rows = dict(
            self._connection.execute(
                "SELECT sequence_no, event_hash FROM run_events "
                "WHERE run_id = ? AND sequence_no IN (?, ?)",
                (run_id, terminal_sequence, audit_sequence),
            ).fetchall()
        )
        if set(head_rows) != {terminal_sequence, audit_sequence}:
            raise ArtifactIntegrityError(f"run-projection:{run_id}")
        self._verify_projection_artifacts(
            run_id=run_id,
            terminal_head=EventHead(
                run_id=run_id,
                sequence_no=terminal_sequence,
                event_hash=str(head_rows[terminal_sequence]),
            ),
            terminal_reference=terminal_reference,
            audit_head=EventHead(
                run_id=run_id,
                sequence_no=audit_sequence,
                event_hash=str(head_rows[audit_sequence]),
            ),
            audit_reference=audit_reference,
        )
        return terminal_reference, audit_reference

    def _require_sql_references(
        self,
        references: tuple[ArtifactReference, ...],
    ) -> None:
        for reference in references:
            stored = self._fetch_stored(ArtifactId(root=reference.artifact_id))
            if stored is None:
                raise UnknownArtifactError(reference.artifact_id)
            direct_parents = self._verified_parents(
                stored.parent_artifact_ids,
                integrity_subject=stored.artifact_id.root,
            )
            self._verify(stored, direct_parents)
            if stored.payload_hash.root != reference.payload_hash:
                raise TerminalProvenanceError(reference.artifact_id)

    def _require_sql_event_references(self, event: RunEvent) -> None:
        self._require_sql_references(_event_artifact_references(event))
        for artifact_id in _event_unhashed_artifact_ids(event):
            stored = self._fetch_stored(ArtifactId(root=artifact_id))
            if stored is None:
                raise UnknownArtifactError(artifact_id)
            direct_parents = self._verified_parents(
                stored.parent_artifact_ids,
                integrity_subject=artifact_id,
            )
            self._verify(stored, direct_parents)

    def _put_lifecycle_model_in_transaction(
        self,
        artifact_type: str,
        model: StrictFrozenModel,
        *,
        parents: tuple[ArtifactReference, ...],
        created_by: str,
        created_at: str,
    ) -> ArtifactPutResult:
        prepared = self._prepare(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": artifact_type,
                "payload_bytes": canonical_json_bytes(
                    model.model_dump(mode="json", round_trip=True, warnings="error")
                ),
                "parent_artifact_ids": sorted(
                    {item.artifact_id for item in parents},
                    key=lambda item: item.encode("utf-8"),
                ),
                "created_by": created_by,
                "created_at": created_at,
                "source_evidence_ids": [],
            }
        )
        artifact_id = _default_artifact_id(prepared.envelope_bytes)
        self._ensure_schema_contract(prepared)
        direct_parents = self._verified_parents(
            prepared.parent_artifact_ids,
            integrity_subject=None,
        )
        self._validate_prepared_parent_contract(prepared, direct_parents)
        existing = self._fetch_stored(artifact_id)
        if existing is not None:
            self._verify(existing, direct_parents)
            if (
                existing.envelope_bytes != prepared.envelope_bytes
                or existing.payload_bytes != prepared.payload_bytes
            ):
                raise ArtifactIdentityConflictError(artifact_id.root)
            return self._result(existing)
        self._connection.execute(
            "INSERT OR IGNORE INTO payload_blobs(payload_hash, payload_bytes) VALUES (?, ?)",
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
            "INSERT INTO artifact_parents(artifact_id, position, parent_id) VALUES (?, ?, ?)",
            [
                (artifact_id.root, position, parent.root)
                for position, parent in enumerate(prepared.parent_artifact_ids)
            ],
        )
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

    def project(
        self,
        run_id: RunId,
        as_of: VerifiedEventHead,
        *,
        projector_version: str,
        projector_hash: Sha256Digest,
    ) -> RunProjection:
        query = _validated_projection_query(
            run_id,
            as_of,
            projector_version,
            projector_hash,
        )
        if (
            query.projector_version != RUN_PROJECTOR_VERSION
            or query.projector_hash != RUN_PROJECTOR_HASH
        ):
            raise RunProjectorIdentityError(query.projector_version)
        with self._lock:
            self._require_schema_integrity()
            records = self._load_run_event_records(query.run_id)
            if not records:
                raise UnknownRunError(query.run_id)
            project_head = EventHead(
                run_id=query.run_id,
                sequence_no=query.as_of_sequence_no,
                event_hash=query.as_of_event_head_hash,
            )
            return self._decorate_sql_view(
                self._project_run_records(records, as_of_head=project_head)
            )

    def _load_run_event_records(self, run_id: str) -> tuple[EventRecord, ...]:
        rows = self._connection.execute(
            "SELECT rowid, sequence_no, event_id, event_hash FROM run_events "
            "WHERE run_id = ? ORDER BY sequence_no",
            (run_id,),
        ).fetchall()
        records: list[EventRecord] = []
        expected_signed_indexes: set[tuple[str, str, str, int, str]] = set()
        for rowid, sequence_no, event_id, event_hash in rows:
            raw = self._read_bounded_blob(
                "run_events",
                "record_bytes",
                int(rowid),
                MAX_JSON_PAYLOAD_BYTES,
                str(event_id),
            )
            record = parse_event_record(raw)
            if (
                record.event.run_id != run_id
                or record.event.sequence_no != sequence_no
                or record.event.event_id != event_id
                or record.event_hash != event_hash
            ):
                raise ArtifactIntegrityError(f"run-event:{run_id}:{sequence_no}")
            signed_nonce = _signed_event_nonce(record.event)
            signed_slot = _signed_event_slot(record.event)
            if signed_nonce is not None and signed_slot is not None:
                expected_signed_indexes.add(
                    (
                        signed_slot[0],
                        signed_nonce,
                        signed_slot[1],
                        signed_slot[2],
                        str(event_id),
                    )
                )
            records.append(record)
        if records:
            security_context = self._resolve_run_event_security_context(
                records[0].event
            )
            for record in records:
                self._event_authenticator.authenticate(
                    record.event,
                    security_context,
                )
        event_ids = tuple(str(record.event.event_id) for record in records)
        signed_index_query = (
            "SELECT signing_key_id, nonce_b64url, run_id, sequence_no, event_id "
            "FROM signed_event_nonces WHERE run_id = ?"
        )
        signed_index_parameters: tuple[object, ...] = (run_id,)
        if event_ids:
            signed_index_query += (
                " OR event_id IN (" + ",".join("?" for _ in event_ids) + ")"
            )
            signed_index_parameters += event_ids
        actual_signed_indexes = {
            cast(tuple[str, str, str, int, str], row)
            for row in self._connection.execute(
                signed_index_query,
                signed_index_parameters,
            ).fetchall()
        }
        if actual_signed_indexes != expected_signed_indexes:
            raise ArtifactIntegrityError(f"signed-event-index:{run_id}")
        head = self._connection.execute(
            "SELECT sequence_no, event_hash FROM run_heads WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if (
            (not records) != (head is None)
            or records
            and head
            != (
                records[-1].event.sequence_no,
                records[-1].event_hash,
            )
        ):
            raise ArtifactIntegrityError(f"run-head:{run_id}")
        return tuple(records)

    def put(self, request: ArtifactPutInput) -> ArtifactPutResult:
        prepared = self._prepare(request)
        if prepared.artifact_type in _LIFECYCLE_DERIVED_ARTIFACT_TYPES:
            raise ArtifactWriteAuthorityError(prepared.artifact_type)
        artifact_id = _default_artifact_id(prepared.envelope_bytes)
        if artifact_id in prepared.parent_artifact_ids:
            raise ArtifactCycleError(artifact_id.root)

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_schema_integrity()
                self._ensure_schema_contract(prepared)
                direct_parents = self._verified_parents(
                    prepared.parent_artifact_ids,
                    integrity_subject=None,
                )
                for parent in prepared.parent_artifact_ids:
                    if self._sqlite_has_ancestor(parent.root, artifact_id.root):
                        raise ArtifactCycleError(artifact_id.root)
                self._validate_prepared_parent_contract(prepared, direct_parents)

                try:
                    existing = self._fetch_stored(artifact_id)
                except (TypeError, ValueError) as error:
                    raise ArtifactIntegrityError(artifact_id.root) from error
                if existing is not None:
                    self._verify(existing, direct_parents)
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
        *,
        require_event_contracts: bool = True,
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
        if require_event_contracts:
            self._verify_event_schema_contracts()

    def _ensure_event_schema_contracts(self) -> None:
        rows = self._connection.execute(
            "SELECT event_type, schema_version, schema_id "
            "FROM event_schema_contracts ORDER BY event_type, schema_version"
        ).fetchall()
        if not rows:
            self._connection.executemany(
                "INSERT INTO event_schema_contracts(event_type, schema_version, "
                "schema_id) VALUES (?, ?, ?)",
                self._event_schema_contracts,
            )
            return
        if tuple(tuple(str(value) for value in row) for row in rows) != (
            self._event_schema_contracts
        ):
            raise ArtifactIntegrityError(f"event-schema:{self._database_path}")

    def _verify_event_schema_contracts(self) -> None:
        rows = self._connection.execute(
            "SELECT event_type, schema_version, schema_id "
            "FROM event_schema_contracts ORDER BY event_type, schema_version"
        ).fetchall()
        if tuple(tuple(str(value) for value in row) for row in rows) != (
            self._event_schema_contracts
        ):
            raise ArtifactIntegrityError(f"event-schema:{self._database_path}")

    def _ensure_schema_contract(self, prepared: _PreparedArtifact) -> None:
        parent_contract_bytes = _parent_contract_bytes(prepared.parent_contract)
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
        if existing[0] != prepared.schema_id or existing[1] != parent_contract_bytes:
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
        expected_parents = _parent_contract_bytes(registered.parent_contract)
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
            direct_parents = self._verified_parents(
                stored.parent_artifact_ids,
                integrity_subject=stored.artifact_id.root,
            )
            return self._read_result(stored, direct_parents)

    def lineage(self, artifact_id: ArtifactId) -> ArtifactLineageResult:
        with self._lock:
            self._require_schema_integrity()
            try:
                stored = self._fetch_stored(artifact_id)
            except (TypeError, ValueError) as error:
                raise ArtifactIntegrityError(artifact_id.root) from error
            if stored is None:
                raise UnknownArtifactError(artifact_id.root)
            direct_parents = self._verified_parents(
                stored.parent_artifact_ids,
                integrity_subject=stored.artifact_id.root,
            )
            self._verify(stored, direct_parents)
            return ArtifactLineageResult(
                schema_version="automarkov.artifact-lineage-result.v1",
                artifact_ids=stored.parent_artifact_ids,
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _verified_parents(
        self,
        parent_artifact_ids: tuple[ArtifactId, ...],
        *,
        integrity_subject: str | None,
    ) -> tuple[_VerifiedParent, ...]:
        verified: dict[str, _VerifiedParent] = {}
        stored_by_id: dict[str, _StoredArtifact] = {}
        active: set[str] = set()
        pending = [(parent.root, False) for parent in reversed(parent_artifact_ids)]
        while pending:
            current_id, leaving = pending.pop()
            if current_id in verified:
                continue
            if leaving:
                stored = stored_by_id[current_id]
                try:
                    direct_parents = tuple(
                        verified[parent.root] for parent in stored.parent_artifact_ids
                    )
                    self._verify(stored, direct_parents)
                except (ArtifactIntegrityError, KeyError) as error:
                    subject = integrity_subject or stored.artifact_id.root
                    raise ArtifactIntegrityError(subject) from error
                verified[current_id] = _VerifiedParent(
                    artifact_id=stored.artifact_id,
                    artifact_type=stored.artifact_type,
                    payload_hash=stored.payload_hash,
                )
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
                if parent.root not in verified
            )
        return tuple(verified[parent.root] for parent in parent_artifact_ids)

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
