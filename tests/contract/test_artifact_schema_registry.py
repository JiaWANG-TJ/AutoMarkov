from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import UUID

import pytest
from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    PlainSerializer,
    WithJsonSchema,
    WrapValidator,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic.annotated_handlers import GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from typing_extensions import TypedDict

from automarkov.adapters import InMemoryArtifactRepository, SqliteArtifactRepository
from automarkov.domain.canonical import (
    CanonicalJsonValue,
    FrozenSequence,
    FrozenStringMapping,
    SafeCanonicalInt,
    StrictCanonicalFloat,
    StrictTrue,
    canonical_json_bytes,
    parse_canonical_document,
)
from automarkov.domain.errors import ArtifactIntegrityError, ArtifactParentContractError
from automarkov.domain.models import StrictFrozenModel
from automarkov.lifecycle import ArtifactIdValue, Sha256Value
from automarkov.repository import ArtifactSchemaRegistry, ParentBinding


class _PlainFloatArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-plain-float.v1"]
    value: float


class _PlainIntArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-plain-int.v1"]
    value: int


class _NestedFloatValue(StrictFrozenModel):
    value: float


class _NestedFloatArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-nested-float.v1"]
    nested: _NestedFloatValue


class _ListFloatArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-list-float.v1"]
    values: list[float]


class _UnionFloatArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-union-float.v1"]
    value: int | float


class _StrictFloatArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-strict-float.v1"]
    value: StrictCanonicalFloat


class _PayloadParentArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-payload-parent.v1"]
    value: str


class _NullableArtifactReference(StrictFrozenModel):
    artifact_id: ArtifactIdValue | None
    payload_hash: Sha256Value | None


class _PayloadBoundChildArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-payload-bound-child.v1"]
    parent: _NullableArtifactReference
    many_parents: FrozenSequence[_NullableArtifactReference]
    optional_parent: _NullableArtifactReference | None
    undeclared_parent: _NullableArtifactReference | None


class _NestedStrictFloatValue(StrictFrozenModel):
    value: StrictCanonicalFloat


class _StrictFloatOrNestedArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-float-or-nested.v1"]
    value: StrictCanonicalFloat | _NestedStrictFloatValue


class _ExactFloatBranch(StrictFrozenModel):
    exact_label: str
    value: StrictCanonicalFloat


class _CanonicalJsonBranch(StrictFrozenModel):
    canonical_label: str
    value: CanonicalJsonValue


class _UndiscriminatedObjectUnionArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-object-union.v1"]
    item: _ExactFloatBranch | _CanonicalJsonBranch


class _AmbiguousExactFloatBranch(StrictFrozenModel):
    value: StrictCanonicalFloat


class _AmbiguousCanonicalJsonBranch(StrictFrozenModel):
    value: CanonicalJsonValue


class _AmbiguousObjectUnionArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-ambiguous-object-union.v1"]
    item: _AmbiguousExactFloatBranch | _AmbiguousCanonicalJsonBranch


class _TypedExactFloatBranch(StrictFrozenModel):
    value: StrictCanonicalFloat


class _TypedStringBranch(StrictFrozenModel):
    value: str


class _PropertyTypedObjectUnionArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-property-typed-object-union.v1"]
    item: _TypedExactFloatBranch | _TypedStringBranch


class _AmbiguousScalarUnionArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-ambiguous-scalar-union.v1"]
    value: CanonicalJsonValue | StrictCanonicalFloat


class _EnumExactFloatBranch(StrictFrozenModel):
    kind: Literal["alpha", "beta"]
    value: StrictCanonicalFloat


class _OtherEnumExactFloatBranch(StrictFrozenModel):
    kind: Literal["gamma", "delta"]
    value: StrictCanonicalFloat


class _EnumSelectedUnionArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-enum-selected-union.v1"]
    item: _EnumExactFloatBranch | _OtherEnumExactFloatBranch


class _PatternExactFloatBranch(StrictFrozenModel):
    kind: Annotated[str, Field(pattern=r"^alpha$")]
    value: StrictCanonicalFloat


class _OtherPatternExactFloatBranch(StrictFrozenModel):
    kind: Annotated[str, Field(pattern=r"^beta$")]
    value: StrictCanonicalFloat


class _PatternSelectedUnionArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-pattern-selected-union.v1"]
    item: _PatternExactFloatBranch | _OtherPatternExactFloatBranch


class _HiddenBoundedFloatArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-hidden-bounded-float.v1"]
    value: Annotated[StrictCanonicalFloat, Field(ge=0.0, le=1.0)]


class _HiddenNestedFloatValue(StrictFrozenModel):
    value: StrictCanonicalFloat


_HiddenNestedFloat = Annotated[
    _HiddenNestedFloatValue,
    WithJsonSchema(
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
        }
    ),
]


class _HiddenNestedFloatArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-hidden-nested-float.v1"]
    nested: _HiddenNestedFloat


class _FieldTypeOverrideArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-field-type-override.v1"]
    value: SafeCanonicalInt = Field(json_schema_extra={"type": "string"})


class _FieldMarkerOverrideArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-field-marker-override.v1"]
    value: SafeCanonicalInt = Field(
        json_schema_extra={"x-automarkov-number-kind": "exact-float"}
    )


class _DynamicDefaultArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-dynamic-default.v1"]
    value: SafeCanonicalInt = Field(default_factory=lambda: 1)


class _ExcludedFieldArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-excluded-field.v1"]
    visible: str
    hidden: SafeCanonicalInt = Field(default=1, exclude=True)


class _CustomFieldSerializerArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-custom-field-serializer.v1"]
    value: SafeCanonicalInt

    @field_serializer("value")
    def serialize_value(self, value: int) -> str:
        return str(value)


class _AnnotatedSerializerArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-annotated-serializer.v1"]
    value: Annotated[
        SafeCanonicalInt,
        PlainSerializer(str, return_type=str, when_used="json"),
    ]


class _TypeMutatingAfterValidatorArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-type-mutating-after-validator.v1"]
    value: SafeCanonicalInt

    @model_validator(mode="after")
    def mutate_validated_type(self) -> _TypeMutatingAfterValidatorArtifact:
        object.__setattr__(self, "value", float(self.value))
        return self


class _ClassSchemaOverrideArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-class-schema-override.v1"]
    value: SafeCanonicalInt

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: Any,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        schema = handler(core_schema)
        cast(dict[str, Any], schema["properties"])["value"] = {"type": "string"}
        return schema


def _coerce_wrapped_integer(
    value: object, handler: Callable[[object], object]
) -> object:
    return handler(int(cast(Any, value)))


class _CoerciveWrapArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-coercive-wrap.v1"]
    value: Annotated[int, WrapValidator(_coerce_wrapped_integer)]


class _CanonicalJsonArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-canonical-json.v1"]
    metadata: CanonicalJsonValue


class _FrozenCollectionsArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-frozen-collections.v1"]
    values: FrozenSequence[SafeCanonicalInt]
    metadata: FrozenStringMapping[CanonicalJsonValue]


class _FrozenSequenceWithMutableListArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-frozen-sequence-mutable-list.v1"]
    values: FrozenSequence[list[SafeCanonicalInt]]


class _FrozenMappingWithMutableDictArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-frozen-mapping-mutable-dict.v1"]
    values: FrozenStringMapping[dict[str, SafeCanonicalInt]]


class _FrozenSequenceWithAnyArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-frozen-sequence-any.v1"]
    values: FrozenSequence[Any]


class _SurrogateMappingKeyArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-surrogate-mapping-key.v1"]
    values: FrozenStringMapping[SafeCanonicalInt]


class _AfterValidatorUnfreezesMappingArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-after-validator-unfreezes-mapping.v1"]
    values: FrozenStringMapping[SafeCanonicalInt]

    @field_validator("values", mode="after")
    @classmethod
    def unfreeze_values(cls, value: Mapping[str, int]) -> dict[str, int]:
        return dict(value)


class _RootGuardedArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-root-guarded.v1"]
    value: int

    @model_validator(mode="before")
    @classmethod
    def guard_root(cls, value: object) -> object:
        return value


class _OpenNestedValue(BaseModel):
    value: int


class _OpenNestedArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-open-nested.v1"]
    nested: _OpenNestedValue


class _AliasedArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-aliased.v1"]
    value: Annotated[StrictCanonicalFloat, Field(alias="v")]


class _MutableArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-mutable.v1"]
    value: SafeCanonicalInt


@dataclass
class _MutableDataclassValue:
    value: SafeCanonicalInt


class _MutableTypedDictValue(TypedDict):
    value: SafeCanonicalInt


class _MutableListArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-mutable-list.v1"]
    values: list[SafeCanonicalInt]


class _MutableTypedDictArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-mutable-typed-dict.v1"]
    value: _MutableTypedDictValue


class _MutableDataclassArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-mutable-dataclass.v1"]
    value: _MutableDataclassValue


class _UnconstrainedArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-unconstrained.v1"]
    value: Any


class _RawTrueArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-raw-true.v1"]
    confirmed: Literal[True]


class _StrictTrueArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-strict-true.v1"]
    confirmed: StrictTrue


class _IntegerOneArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-integer-one.v1"]
    value: Literal[1]


class _FalseLiteralArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-false-literal.v1"]
    value: Literal[False]


_CoerciveInt = Annotated[int, BeforeValidator(int)]


class _CoerciveIntArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-coercive-int.v1"]
    value: _CoerciveInt


class _PlainTupleArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-plain-tuple.v1"]
    values: tuple[int, ...]


class _WireState(StrEnum):
    READY = "ready"


class _PlainStringEnumArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-plain-string-enum.v1"]
    state: _WireState


class _BytesArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-bytes.v1"]
    value: bytes


class _DateArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-date.v1"]
    value: date


class _UuidArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-uuid.v1"]
    value: UUID


class _InvalidVersionArtifact(StrictFrozenModel):
    schema_version: Literal["bad"]


_ForgedExactFloat = Annotated[
    float,
    WithJsonSchema(
        {
            "type": "number",
            "x-automarkov-number-kind": "exact-float",
        }
    ),
]


class _ForgedMarkerArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-forged-marker.v1"]
    value: _ForgedExactFloat


@pytest.mark.parametrize(
    ("model_type", "schema_version"),
    [
        pytest.param(
            _PlainFloatArtifact,
            "automarkov.test-plain-float.v1",
            id="plain",
        ),
        pytest.param(
            _NestedFloatArtifact,
            "automarkov.test-nested-float.v1",
            id="nested",
        ),
        pytest.param(
            _ListFloatArtifact,
            "automarkov.test-list-float.v1",
            id="list",
        ),
        pytest.param(
            _UnionFloatArtifact,
            "automarkov.test-union-float.v1",
            id="union",
        ),
    ],
)
def test_registry_rejects_unwrapped_float_at_every_schema_shape(
    model_type: type[StrictFrozenModel],
    schema_version: str,
) -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="StrictCanonicalFloat"):
        registry.register(
            "test_float_artifact",
            schema_version,
            model_type,
            direct_parent_artifact_types=(),
        )


def test_registry_rejects_an_unbounded_integer_wire_contract() -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="safe integer bounds"):
        registry.register(
            "test_plain_int",
            "automarkov.test-plain-int.v1",
            _PlainIntArtifact,
            direct_parent_artifact_types=(),
        )


def test_strict_float_registration_preserves_exact_external_number_type() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_strict_float",
        "automarkov.test-strict-float.v1",
        _StrictFloatArtifact,
        direct_parent_artifact_types=("parent_alpha", "parent_beta"),
    )
    codec = registry.resolve(
        "test_strict_float",
        {"schema_version": "automarkov.test-strict-float.v1"},
    ).codec

    with pytest.raises(ValueError):
        codec.encode(
            {
                "schema_version": "automarkov.test-strict-float.v1",
                "value": 1,
            }
        )

    decoded = cast(
        _StrictFloatArtifact,
        codec.decode(
            codec.encode(
                {
                    "schema_version": "automarkov.test-strict-float.v1",
                    "value": 1.0,
                }
            )
        ),
    )
    assert type(decoded.value) is float
    assert decoded.value == 1.0


def test_exact_float_collection_follows_the_selected_nested_union_branch() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_float_or_nested",
        "automarkov.test-float-or-nested.v1",
        _StrictFloatOrNestedArtifact,
        direct_parent_artifact_types=(),
    )
    codec = registry.resolve(
        "test_float_or_nested",
        {"schema_version": "automarkov.test-float-or-nested.v1"},
    ).codec

    encoded = codec.encode(
        {
            "schema_version": "automarkov.test-float-or-nested.v1",
            "value": {"value": 1.0},
        }
    )
    document = parse_canonical_document(encoded)

    assert type(document) is dict
    assert document["exact_float_paths"] == ["/payload/value/value"]
    decoded = cast(_StrictFloatOrNestedArtifact, codec.decode(encoded))
    assert isinstance(decoded.value, _NestedStrictFloatValue)
    assert type(decoded.value.value) is float
    assert codec.encode(decoded.model_dump(mode="python")) == encoded


def test_exact_float_collection_uses_only_the_valid_object_union_branch() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_object_union",
        "automarkov.test-object-union.v1",
        _UndiscriminatedObjectUnionArtifact,
        direct_parent_artifact_types=(),
    )
    codec = registry.resolve(
        "test_object_union",
        {"schema_version": "automarkov.test-object-union.v1"},
    ).codec

    canonical_encoded = codec.encode(
        {
            "schema_version": "automarkov.test-object-union.v1",
            "item": {"canonical_label": "chosen", "value": 0.5},
        }
    )
    canonical_document = parse_canonical_document(canonical_encoded)
    assert type(canonical_document) is dict
    assert canonical_document["exact_float_paths"] == []
    canonical_decoded = cast(
        _UndiscriminatedObjectUnionArtifact,
        codec.decode(canonical_encoded),
    )
    assert isinstance(canonical_decoded.item, _CanonicalJsonBranch)

    exact_encoded = codec.encode(
        {
            "schema_version": "automarkov.test-object-union.v1",
            "item": {"exact_label": "chosen", "value": 0.5},
        }
    )
    exact_document = parse_canonical_document(exact_encoded)
    assert type(exact_document) is dict
    assert exact_document["exact_float_paths"] == ["/payload/item/value"]
    exact_decoded = cast(
        _UndiscriminatedObjectUnionArtifact,
        codec.decode(exact_encoded),
    )
    assert isinstance(exact_decoded.item, _ExactFloatBranch)


def test_exact_float_collection_rejects_an_ambiguous_object_union() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_ambiguous_object_union",
        "automarkov.test-ambiguous-object-union.v1",
        _AmbiguousObjectUnionArtifact,
        direct_parent_artifact_types=(),
    )
    codec = registry.resolve(
        "test_ambiguous_object_union",
        {"schema_version": "automarkov.test-ambiguous-object-union.v1"},
    ).codec

    with pytest.raises(ValueError, match="ambiguous.*union"):
        codec.encode(
            {
                "schema_version": "automarkov.test-ambiguous-object-union.v1",
                "item": {"value": 0.5},
            }
        )


def test_union_selection_uses_nested_property_types() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_property_typed_object_union",
        "automarkov.test-property-typed-object-union.v1",
        _PropertyTypedObjectUnionArtifact,
        direct_parent_artifact_types=(),
    )
    codec = registry.resolve(
        "test_property_typed_object_union",
        {"schema_version": "automarkov.test-property-typed-object-union.v1"},
    ).codec

    float_bytes = codec.encode(
        {
            "schema_version": "automarkov.test-property-typed-object-union.v1",
            "item": {"value": 0.5},
        }
    )
    float_document = parse_canonical_document(float_bytes)
    assert type(float_document) is dict
    assert float_document["exact_float_paths"] == ["/payload/item/value"]
    assert isinstance(
        cast(_PropertyTypedObjectUnionArtifact, codec.decode(float_bytes)).item,
        _TypedExactFloatBranch,
    )

    string_bytes = codec.encode(
        {
            "schema_version": "automarkov.test-property-typed-object-union.v1",
            "item": {"value": "one"},
        }
    )
    string_document = parse_canonical_document(string_bytes)
    assert type(string_document) is dict
    assert string_document["exact_float_paths"] == []
    assert isinstance(
        cast(_PropertyTypedObjectUnionArtifact, codec.decode(string_bytes)).item,
        _TypedStringBranch,
    )


def test_ambiguous_scalar_union_fails_closed_before_path_derivation() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_ambiguous_scalar_union",
        "automarkov.test-ambiguous-scalar-union.v1",
        _AmbiguousScalarUnionArtifact,
        direct_parent_artifact_types=(),
    )
    codec = registry.resolve(
        "test_ambiguous_scalar_union",
        {"schema_version": "automarkov.test-ambiguous-scalar-union.v1"},
    ).codec

    with pytest.raises(ValueError, match="ambiguous.*union"):
        codec.encode(
            {
                "schema_version": "automarkov.test-ambiguous-scalar-union.v1",
                "value": 0.5,
            }
        )


def test_union_selection_honors_exact_enum_membership() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_enum_selected_union",
        "automarkov.test-enum-selected-union.v1",
        _EnumSelectedUnionArtifact,
        direct_parent_artifact_types=(),
    )
    codec = registry.resolve(
        "test_enum_selected_union",
        {"schema_version": "automarkov.test-enum-selected-union.v1"},
    ).codec

    encoded = codec.encode(
        {
            "schema_version": "automarkov.test-enum-selected-union.v1",
            "item": {"kind": "alpha", "value": 1.0},
        }
    )
    document = parse_canonical_document(encoded)
    assert type(document) is dict
    assert document["exact_float_paths"] == ["/payload/item/value"]
    assert isinstance(
        cast(_EnumSelectedUnionArtifact, codec.decode(encoded)).item,
        _EnumExactFloatBranch,
    )


def test_union_selection_uses_official_json_schema_constraints() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_pattern_selected_union",
        "automarkov.test-pattern-selected-union.v1",
        _PatternSelectedUnionArtifact,
        direct_parent_artifact_types=(),
    )
    codec = registry.resolve(
        "test_pattern_selected_union",
        {"schema_version": "automarkov.test-pattern-selected-union.v1"},
    ).codec

    encoded = codec.encode(
        {
            "schema_version": "automarkov.test-pattern-selected-union.v1",
            "item": {"kind": "alpha", "value": 1.0},
        }
    )
    document = parse_canonical_document(encoded)
    assert type(document) is dict
    assert document["exact_float_paths"] == ["/payload/item/value"]
    assert isinstance(
        cast(_PatternSelectedUnionArtifact, codec.decode(encoded)).item,
        _PatternExactFloatBranch,
    )


def test_registry_rejects_numeric_constraints_hidden_by_alias_wrapping() -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="exact wire alias"):
        registry.register(
            "test_hidden_bounded_float",
            "automarkov.test-hidden-bounded-float.v1",
            _HiddenBoundedFloatArtifact,
            direct_parent_artifact_types=(),
        )


def test_registry_rejects_a_schema_override_that_hides_nested_exact_floats() -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="JSON schema override"):
        registry.register(
            "test_hidden_nested_float",
            "automarkov.test-hidden-nested-float.v1",
            _HiddenNestedFloatArtifact,
            direct_parent_artifact_types=(),
        )


@pytest.mark.parametrize(
    ("artifact_type", "schema_version", "model_type"),
    [
        (
            "test_field_type_override",
            "automarkov.test-field-type-override.v1",
            _FieldTypeOverrideArtifact,
        ),
        (
            "test_field_marker_override",
            "automarkov.test-field-marker-override.v1",
            _FieldMarkerOverrideArtifact,
        ),
    ],
)
def test_registry_rejects_field_level_json_schema_overrides(
    artifact_type: str,
    schema_version: str,
    model_type: type[StrictFrozenModel],
) -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="JSON schema override"):
        registry.register(
            artifact_type,
            schema_version,
            model_type,
            direct_parent_artifact_types=(),
        )


def test_registry_rejects_dynamic_default_factories() -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="default_factory"):
        registry.register(
            "test_dynamic_default",
            "automarkov.test-dynamic-default.v1",
            _DynamicDefaultArtifact,
            direct_parent_artifact_types=(),
        )


def test_registry_rejects_fields_excluded_from_serialization() -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="serialization"):
        registry.register(
            "test_excluded_field",
            "automarkov.test-excluded-field.v1",
            _ExcludedFieldArtifact,
            direct_parent_artifact_types=(),
        )


def test_registry_rejects_custom_field_serializers() -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="serializer"):
        registry.register(
            "test_custom_field_serializer",
            "automarkov.test-custom-field-serializer.v1",
            _CustomFieldSerializerArtifact,
            direct_parent_artifact_types=(),
        )


def test_registry_rejects_unapproved_annotated_serializers() -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="serializer"):
        registry.register(
            "test_annotated_serializer",
            "automarkov.test-annotated-serializer.v1",
            _AnnotatedSerializerArtifact,
            direct_parent_artifact_types=(),
        )


def test_codec_rejects_serializer_type_warnings_before_hashing() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_type_mutating_after_validator",
        "automarkov.test-type-mutating-after-validator.v1",
        _TypeMutatingAfterValidatorArtifact,
        direct_parent_artifact_types=(),
    )
    codec = registry.resolve(
        "test_type_mutating_after_validator",
        {"schema_version": ("automarkov.test-type-mutating-after-validator.v1")},
    ).codec

    with pytest.raises(ValueError, match="serialized value may not be as expected"):
        codec.encode(
            {
                "schema_version": ("automarkov.test-type-mutating-after-validator.v1"),
                "value": 1,
            }
        )


def test_registry_rejects_class_level_json_schema_overrides() -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="JSON schema override"):
        registry.register(
            "test_class_schema_override",
            "automarkov.test-class-schema-override.v1",
            _ClassSchemaOverrideArtifact,
            direct_parent_artifact_types=(),
        )


def test_canonical_json_registration_normalizes_untyped_float_values() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_canonical_json",
        "automarkov.test-canonical-json.v1",
        _CanonicalJsonArtifact,
        direct_parent_artifact_types=(),
    )
    codec = registry.resolve(
        "test_canonical_json",
        {"schema_version": "automarkov.test-canonical-json.v1"},
    ).codec

    decoded = cast(
        _CanonicalJsonArtifact,
        codec.decode(
            codec.encode(
                {
                    "schema_version": "automarkov.test-canonical-json.v1",
                    "metadata": {
                        "whole": 1.0,
                        "negative_zero": -0.0,
                        "fraction": 0.5,
                    },
                }
            )
        ),
    )

    assert isinstance(decoded.metadata, Mapping)
    assert type(decoded.metadata["whole"]) is int
    assert decoded.metadata["whole"] == 1
    assert type(decoded.metadata["negative_zero"]) is int
    assert decoded.metadata["negative_zero"] == 0
    assert type(decoded.metadata["fraction"]) is float
    assert decoded.metadata["fraction"] == 0.5


def test_registered_frozen_collections_round_trip_as_deeply_immutable_json() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_frozen_collections",
        "automarkov.test-frozen-collections.v1",
        _FrozenCollectionsArtifact,
        direct_parent_artifact_types=(),
    )
    codec = registry.resolve(
        "test_frozen_collections",
        {"schema_version": "automarkov.test-frozen-collections.v1"},
    ).codec
    payload = {
        "schema_version": "automarkov.test-frozen-collections.v1",
        "values": [1, 2],
        "metadata": {"nested": {"values": [1, 2]}},
    }

    encoded = codec.encode(payload)
    decoded = cast(_FrozenCollectionsArtifact, codec.decode(encoded))

    assert decoded.values == (1, 2)
    assert isinstance(decoded.metadata, Mapping)
    assert decoded.metadata["nested"] == {"values": (1, 2)}
    with pytest.raises(TypeError):
        cast(dict[str, object], decoded.metadata)["new"] = 3
    assert codec.encode(payload) == encoded


@pytest.mark.parametrize(
    ("artifact_type", "schema_version", "model_type"),
    [
        (
            "test_frozen_sequence_mutable_list",
            "automarkov.test-frozen-sequence-mutable-list.v1",
            _FrozenSequenceWithMutableListArtifact,
        ),
        (
            "test_frozen_mapping_mutable_dict",
            "automarkov.test-frozen-mapping-mutable-dict.v1",
            _FrozenMappingWithMutableDictArtifact,
        ),
        (
            "test_frozen_sequence_any",
            "automarkov.test-frozen-sequence-any.v1",
            _FrozenSequenceWithAnyArtifact,
        ),
    ],
    ids=["nested-list", "nested-dict", "nested-any"],
)
def test_registry_rejects_mutable_values_inside_frozen_wrappers(
    artifact_type: str,
    schema_version: str,
    model_type: type[BaseModel],
) -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="immutable wrapper"):
        registry.register(
            artifact_type,
            schema_version,
            model_type,
            direct_parent_artifact_types=(),
        )


def test_frozen_mapping_rejects_lone_surrogate_keys_during_validation() -> None:
    with pytest.raises(ValueError, match="surrogate"):
        _SurrogateMappingKeyArtifact.model_validate(
            {
                "schema_version": "automarkov.test-surrogate-mapping-key.v1",
                "values": {"invalid\ud800": 1},
            }
        )


def test_codec_rejects_after_validator_that_revokes_deep_freeze() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_after_validator_unfreezes_mapping",
        "automarkov.test-after-validator-unfreezes-mapping.v1",
        _AfterValidatorUnfreezesMappingArtifact,
        direct_parent_artifact_types=(),
    )
    codec = registry.resolve(
        "test_after_validator_unfreezes_mapping",
        {"schema_version": ("automarkov.test-after-validator-unfreezes-mapping.v1")},
    ).codec

    with pytest.raises(ValueError, match="deeply immutable"):
        codec.encode(
            {
                "schema_version": (
                    "automarkov.test-after-validator-unfreezes-mapping.v1"
                ),
                "values": {"key": 1},
            }
        )


def test_json_schema_marker_cannot_forge_the_core_float_contract() -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="StrictCanonicalFloat"):
        registry.register(
            "test_forged_marker",
            "automarkov.test-forged-marker.v1",
            _ForgedMarkerArtifact,
            direct_parent_artifact_types=(),
        )


@pytest.mark.parametrize(
    ("artifact_type", "schema_version", "model_type", "message"),
    [
        (
            "test_open_nested",
            "automarkov.test-open-nested.v1",
            _OpenNestedArtifact,
            "strict, frozen, and closed",
        ),
        (
            "test_aliased",
            "automarkov.test-aliased.v1",
            _AliasedArtifact,
            "aliases",
        ),
    ],
    ids=["nested-open-model", "aliased-field"],
)
def test_registry_rejects_models_that_cannot_preserve_the_wire_contract(
    artifact_type: str,
    schema_version: str,
    model_type: type[BaseModel],
    message: str,
) -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match=message):
        registry.register(
            artifact_type,
            schema_version,
            model_type,
            direct_parent_artifact_types=(),
        )


@pytest.mark.parametrize(
    ("artifact_type", "schema_version", "model_type"),
    [
        (
            "test_mutable_list",
            "automarkov.test-mutable-list.v1",
            _MutableListArtifact,
        ),
        (
            "test_mutable_typed_dict",
            "automarkov.test-mutable-typed-dict.v1",
            _MutableTypedDictArtifact,
        ),
        (
            "test_mutable_dataclass",
            "automarkov.test-mutable-dataclass.v1",
            _MutableDataclassArtifact,
        ),
        (
            "test_unconstrained",
            "automarkov.test-unconstrained.v1",
            _UnconstrainedArtifact,
        ),
    ],
    ids=["list", "typed-dict", "dataclass", "any"],
)
def test_registry_rejects_structures_without_an_immutable_wrapper(
    artifact_type: str,
    schema_version: str,
    model_type: type[BaseModel],
) -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="immutable wrapper"):
        registry.register(
            artifact_type,
            schema_version,
            model_type,
            direct_parent_artifact_types=(),
        )


def test_registry_requires_the_strict_true_wrapper() -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="StrictTrue"):
        registry.register(
            "test_raw_true",
            "automarkov.test-raw-true.v1",
            _RawTrueArtifact,
            direct_parent_artifact_types=(),
        )

    for artifact_type, schema_version, model_type in (
        (
            "test_integer_one",
            "automarkov.test-integer-one.v1",
            _IntegerOneArtifact,
        ),
        (
            "test_false_literal",
            "automarkov.test-false-literal.v1",
            _FalseLiteralArtifact,
        ),
    ):
        with pytest.raises(ValueError, match="numeric and boolean literals"):
            registry.register(
                artifact_type,
                schema_version,
                model_type,
                direct_parent_artifact_types=(),
            )

    registry.register(
        "test_strict_true",
        "automarkov.test-strict-true.v1",
        _StrictTrueArtifact,
        direct_parent_artifact_types=(),
    )
    codec = registry.resolve(
        "test_strict_true",
        {"schema_version": "automarkov.test-strict-true.v1"},
    ).codec
    decoded = cast(
        _StrictTrueArtifact,
        codec.decode(
            codec.encode(
                {
                    "schema_version": "automarkov.test-strict-true.v1",
                    "confirmed": True,
                }
            )
        ),
    )
    assert decoded.confirmed is True
    for value in (1, 1.0, False, "true"):
        with pytest.raises(ValueError):
            codec.encode(
                {
                    "schema_version": "automarkov.test-strict-true.v1",
                    "confirmed": value,
                }
            )


def test_registry_rejects_unapproved_scalar_before_validators() -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="unapproved before validator"):
        registry.register(
            "test_coercive_int",
            "automarkov.test-coercive-int.v1",
            _CoerciveIntArtifact,
            direct_parent_artifact_types=(),
        )

    with pytest.raises(ValueError, match="unapproved wrap validator"):
        registry.register(
            "test_coercive_wrap",
            "automarkov.test-coercive-wrap.v1",
            _CoerciveWrapArtifact,
            direct_parent_artifact_types=(),
        )


def test_registry_rejects_arbitrary_root_before_validators() -> None:
    registry = ArtifactSchemaRegistry()
    with pytest.raises(ValueError, match="unapproved before validator"):
        registry.register(
            "test_root_guarded",
            "automarkov.test-root-guarded.v1",
            _RootGuardedArtifact,
            direct_parent_artifact_types=(),
        )


@pytest.mark.parametrize(
    ("artifact_type", "schema_version", "model_type", "message"),
    [
        (
            "test_plain_tuple",
            "automarkov.test-plain-tuple.v1",
            _PlainTupleArtifact,
            "FrozenSequence",
        ),
        (
            "test_plain_string_enum",
            "automarkov.test-plain-string-enum.v1",
            _PlainStringEnumArtifact,
            "wire string adapter",
        ),
    ],
    ids=["plain-tuple", "plain-string-enum"],
)
def test_registry_rejects_schema_types_that_cannot_decode_wire_json(
    artifact_type: str,
    schema_version: str,
    model_type: type[BaseModel],
    message: str,
) -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match=message):
        registry.register(
            artifact_type,
            schema_version,
            model_type,
            direct_parent_artifact_types=(),
        )


@pytest.mark.parametrize(
    ("artifact_type", "schema_version", "model_type"),
    [
        ("test_bytes", "automarkov.test-bytes.v1", _BytesArtifact),
        ("test_date", "automarkov.test-date.v1", _DateArtifact),
        ("test_uuid", "automarkov.test-uuid.v1", _UuidArtifact),
    ],
    ids=["bytes", "date", "uuid"],
)
def test_registry_rejects_non_json_native_scalar_schemas(
    artifact_type: str,
    schema_version: str,
    model_type: type[BaseModel],
) -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError, match="explicit reversible wire adapter"):
        registry.register(
            artifact_type,
            schema_version,
            model_type,
            direct_parent_artifact_types=(),
        )


def test_registry_validates_artifact_type_and_schema_version_keys() -> None:
    registry = ArtifactSchemaRegistry()

    with pytest.raises(ValueError):
        registry.register(
            "Bad-Type",
            "automarkov.test-mutable.v1",
            _MutableArtifact,
            direct_parent_artifact_types=(),
        )
    with pytest.raises(ValueError):
        registry.register(
            "bad_type",
            "bad",
            _InvalidVersionArtifact,
            direct_parent_artifact_types=(),
        )


def test_registered_codec_isolated_from_later_model_class_rebuild() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_mutable",
        "automarkov.test-mutable.v1",
        _MutableArtifact,
        direct_parent_artifact_types=(),
    )
    codec = registry.resolve(
        "test_mutable",
        {"schema_version": "automarkov.test-mutable.v1"},
    ).codec
    original_extra = _MutableArtifact.model_config.get("extra")
    assert original_extra is not None
    try:
        _MutableArtifact.model_config["extra"] = "ignore"
        _MutableArtifact.model_rebuild(force=True)

        with pytest.raises(ValueError):
            codec.encode(
                {
                    "schema_version": "automarkov.test-mutable.v1",
                    "value": 1,
                    "caller_controlled": True,
                }
            )
    finally:
        _MutableArtifact.model_config["extra"] = original_extra
        _MutableArtifact.model_rebuild(force=True)


def test_registration_requires_explicit_canonical_direct_parent_types() -> None:
    registry = ArtifactSchemaRegistry()
    register = cast(Callable[..., str], registry.register)

    with pytest.raises(TypeError, match="direct_parent_artifact_types"):
        register(
            "test_strict_float",
            "automarkov.test-strict-float.v1",
            _StrictFloatArtifact,
        )

    with pytest.raises(ValueError, match="canonical order"):
        registry.register(
            "test_strict_float",
            "automarkov.test-strict-float.v1",
            _StrictFloatArtifact,
            direct_parent_artifact_types=("parent_beta", "parent_alpha"),
        )

    with pytest.raises(ValueError):
        registry.register(
            "test_strict_float",
            "automarkov.test-strict-float.v1",
            _StrictFloatArtifact,
            direct_parent_artifact_types=("Bad-Type",),
        )


def test_registry_freeze_requires_closed_parent_type_references() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_child",
        "automarkov.test-strict-float.v1",
        _StrictFloatArtifact,
        direct_parent_artifact_types=("test_parent",),
    )

    with pytest.raises(ValueError, match="unregistered parent artifact types"):
        registry.freeze()

    registry.register(
        "test_parent",
        "automarkov.test-mutable.v1",
        _MutableArtifact,
        direct_parent_artifact_types=(),
    )
    registry.freeze()
    with pytest.raises(RuntimeError, match="frozen"):
        registry.register(
            "test_other",
            "automarkov.test-mutable.v1",
            _MutableArtifact,
            direct_parent_artifact_types=(),
        )


def test_same_schema_key_rejects_a_different_parent_contract() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_strict_float",
        "automarkov.test-strict-float.v1",
        _StrictFloatArtifact,
        direct_parent_artifact_types=(),
    )
    original = registry.resolve(
        "test_strict_float",
        {"schema_version": "automarkov.test-strict-float.v1"},
    )

    with pytest.raises(ValueError, match="already registered differently"):
        registry.register(
            "test_strict_float",
            "automarkov.test-strict-float.v1",
            _StrictFloatArtifact,
            direct_parent_artifact_types=("parent_alpha",),
        )

    assert (
        registry.resolve(
            "test_strict_float",
            {"schema_version": "automarkov.test-strict-float.v1"},
        )
        is original
    )


def test_identical_reregistration_preserves_the_original_snapshot() -> None:
    registry = ArtifactSchemaRegistry()
    schema_id = registry.register(
        "test_strict_float",
        "automarkov.test-strict-float.v1",
        _StrictFloatArtifact,
        direct_parent_artifact_types=("parent_alpha",),
    )
    original = registry.resolve(
        "test_strict_float",
        {"schema_version": "automarkov.test-strict-float.v1"},
    )

    assert registry.register(
        "test_strict_float",
        "automarkov.test-strict-float.v1",
        _StrictFloatArtifact,
        direct_parent_artifact_types=("parent_alpha",),
    ) == (schema_id)
    assert (
        registry.resolve(
            "test_strict_float",
            {"schema_version": "automarkov.test-strict-float.v1"},
        )
        is original
    )


def test_same_schema_key_freezes_the_complete_payload_parent_binding() -> None:
    registry = ArtifactSchemaRegistry()
    binding = ParentBinding(
        artifact_id_path="parent.artifact_id",
        payload_hash_path="parent.payload_hash",
        allowed_artifact_types=("parent_alpha",),
        cardinality="one",
    )
    registry.register(
        "test_strict_float",
        "automarkov.test-strict-float.v1",
        _StrictFloatArtifact,
        payload_parent_bindings=(binding,),
    )

    with pytest.raises(ValueError, match="already registered differently"):
        registry.register(
            "test_strict_float",
            "automarkov.test-strict-float.v1",
            _StrictFloatArtifact,
            payload_parent_bindings=(
                binding.model_copy(update={"allowed_artifact_types": ("parent_beta",)}),
            ),
        )


@pytest.mark.parametrize("adapter", ["memory", "sqlite"])
def test_public_put_checks_the_payload_bound_parent_hash(
    adapter: str,
    tmp_path: Path,
) -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "parent_alpha",
        "automarkov.test-payload-parent.v1",
        _PayloadParentArtifact,
        direct_parent_artifact_types=(),
    )
    registry.register(
        "parent_beta",
        "automarkov.test-payload-parent.v1",
        _PayloadParentArtifact,
        direct_parent_artifact_types=(),
    )
    registry.register(
        "payload_bound_child",
        "automarkov.test-payload-bound-child.v1",
        _PayloadBoundChildArtifact,
        payload_parent_bindings=(
            ParentBinding(
                artifact_id_path="many_parents.*.artifact_id",
                payload_hash_path="many_parents.*.payload_hash",
                allowed_artifact_types=("parent_alpha", "parent_beta"),
                cardinality="many",
            ),
            ParentBinding(
                artifact_id_path="optional_parent.artifact_id",
                payload_hash_path="optional_parent.payload_hash",
                allowed_artifact_types=("parent_alpha",),
                cardinality="optional",
            ),
            ParentBinding(
                artifact_id_path="parent.artifact_id",
                payload_hash_path="parent.payload_hash",
                allowed_artifact_types=("parent_alpha",),
                cardinality="one",
            ),
        ),
    )
    registry.freeze()
    repository = (
        InMemoryArtifactRepository(registry)
        if adapter == "memory"
        else SqliteArtifactRepository(tmp_path / "payload-binding.sqlite", registry)
    )
    parent = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "parent_alpha",
            "payload_bytes": canonical_json_bytes(
                {
                    "schema_version": "automarkov.test-payload-parent.v1",
                    "value": "parent",
                }
            ),
            "parent_artifact_ids": [],
            "created_by": "principal_payload_binding_test",
            "created_at": "2026-08-10T09:00:00Z",
            "source_evidence_ids": [],
        }
    )
    child_request = {
        "schema_version": "automarkov.artifact-put-request.v2",
        "artifact_type": "payload_bound_child",
        "payload_bytes": canonical_json_bytes(
            {
                "schema_version": "automarkov.test-payload-bound-child.v1",
                "parent": {
                    "artifact_id": parent.artifact_id.root,
                    "payload_hash": "sha256:" + "0" * 64,
                },
                "many_parents": [],
                "optional_parent": None,
                "undeclared_parent": None,
            }
        ),
        "parent_artifact_ids": [parent.artifact_id.root],
        "created_by": "principal_payload_binding_test",
        "created_at": "2026-08-10T09:00:00Z",
        "source_evidence_ids": [],
    }
    try:
        with pytest.raises(ArtifactParentContractError):
            repository.put(child_request)
    finally:
        if isinstance(repository, SqliteArtifactRepository):
            repository.close()


@pytest.mark.parametrize("adapter", ["memory", "sqlite"])
def test_public_put_enforces_closed_payload_parent_bindings(
    adapter: str,
    tmp_path: Path,
) -> None:
    registry = ArtifactSchemaRegistry()
    for artifact_type in ("parent_alpha", "parent_beta"):
        registry.register(
            artifact_type,
            "automarkov.test-payload-parent.v1",
            _PayloadParentArtifact,
            direct_parent_artifact_types=(),
        )
    registry.register(
        "payload_bound_child",
        "automarkov.test-payload-bound-child.v1",
        _PayloadBoundChildArtifact,
        payload_parent_bindings=(
            ParentBinding(
                artifact_id_path="many_parents.*.artifact_id",
                payload_hash_path="many_parents.*.payload_hash",
                allowed_artifact_types=("parent_alpha", "parent_beta"),
                cardinality="many",
            ),
            ParentBinding(
                artifact_id_path="optional_parent.artifact_id",
                payload_hash_path="optional_parent.payload_hash",
                allowed_artifact_types=("parent_alpha",),
                cardinality="optional",
            ),
            ParentBinding(
                artifact_id_path="parent.artifact_id",
                payload_hash_path="parent.payload_hash",
                allowed_artifact_types=("parent_alpha",),
                cardinality="one",
            ),
        ),
    )
    registry.freeze()
    repository = (
        InMemoryArtifactRepository(registry)
        if adapter == "memory"
        else SqliteArtifactRepository(
            tmp_path / "closed-payload-binding.sqlite", registry
        )
    )

    def put_parent(artifact_type: str, value: str) -> object:
        return repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": artifact_type,
                "payload_bytes": canonical_json_bytes(
                    {
                        "schema_version": "automarkov.test-payload-parent.v1",
                        "value": value,
                    }
                ),
                "parent_artifact_ids": [],
                "created_by": "principal_payload_binding_test",
                "created_at": "2026-08-10T09:00:00Z",
                "source_evidence_ids": [],
            }
        )

    alpha = cast(Any, put_parent("parent_alpha", "alpha"))
    beta = cast(Any, put_parent("parent_beta", "beta"))
    alpha_ref = {
        "artifact_id": alpha.artifact_id.root,
        "payload_hash": alpha.payload_hash.root,
    }
    beta_ref = {
        "artifact_id": beta.artifact_id.root,
        "payload_hash": beta.payload_hash.root,
    }

    def child_request(
        *,
        parent: dict[str, object],
        many_parents: list[dict[str, object]] | None = None,
        optional_parent: dict[str, object] | None = None,
        undeclared_parent: dict[str, object] | None = None,
        envelope_parents: list[str] | None = None,
    ) -> dict[str, object]:
        parent_ids = envelope_parents
        if parent_ids is None:
            parent_ids = [cast(str, parent["artifact_id"])] + [
                cast(str, item["artifact_id"]) for item in many_parents or []
            ]
            if optional_parent is not None:
                parent_ids.append(cast(str, optional_parent["artifact_id"]))
        return {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": "payload_bound_child",
            "payload_bytes": canonical_json_bytes(
                {
                    "schema_version": "automarkov.test-payload-bound-child.v1",
                    "parent": parent,
                    "many_parents": many_parents or [],
                    "optional_parent": optional_parent,
                    "undeclared_parent": undeclared_parent,
                }
            ),
            "parent_artifact_ids": sorted(
                set(parent_ids), key=lambda item: item.encode("utf-8")
            ),
            "created_by": "principal_payload_binding_test",
            "created_at": "2026-08-10T09:00:00Z",
            "source_evidence_ids": [],
        }

    valid_request = child_request(parent=alpha_ref, many_parents=[beta_ref])
    try:
        result = repository.put(valid_request)
        assert repository.put(valid_request) == result
        assert repository.get(result.artifact_id).envelope.parent_artifact_ids == tuple(
            sorted((alpha.artifact_id, beta.artifact_id), key=lambda item: item.root)
        )

        invalid_requests = (
            child_request(
                parent={"artifact_id": alpha.artifact_id.root, "payload_hash": None}
            ),
            child_request(
                parent={"artifact_id": None, "payload_hash": None},
                envelope_parents=[],
            ),
            child_request(
                parent=alpha_ref,
                many_parents=[{"artifact_id": None, "payload_hash": None}],
                envelope_parents=[alpha.artifact_id.root],
            ),
            child_request(parent=alpha_ref, many_parents=[alpha_ref]),
            child_request(parent=beta_ref),
            child_request(parent=alpha_ref, envelope_parents=[]),
            child_request(
                parent=alpha_ref,
                envelope_parents=[alpha.artifact_id.root, beta.artifact_id.root],
            ),
            child_request(parent=alpha_ref, undeclared_parent=beta_ref),
        )
        for invalid_request in invalid_requests:
            with pytest.raises(ArtifactParentContractError):
                repository.put(invalid_request)
        if isinstance(repository, SqliteArtifactRepository):
            stored_contract = repository._connection.execute(
                "SELECT direct_parent_artifact_types "
                "FROM artifact_schema_contracts "
                "WHERE artifact_type = ? AND schema_version = ?",
                (
                    "payload_bound_child",
                    "automarkov.test-payload-bound-child.v1",
                ),
            ).fetchone()
            assert stored_contract == (
                canonical_json_bytes(
                    {
                        "contract_kind": "payload_bound",
                        "bindings": [
                            {
                                "artifact_id_path": "many_parents.*.artifact_id",
                                "payload_hash_path": "many_parents.*.payload_hash",
                                "allowed_artifact_types": [
                                    "parent_alpha",
                                    "parent_beta",
                                ],
                                "cardinality": "many",
                            },
                            {
                                "artifact_id_path": "optional_parent.artifact_id",
                                "payload_hash_path": "optional_parent.payload_hash",
                                "allowed_artifact_types": ["parent_alpha"],
                                "cardinality": "optional",
                            },
                            {
                                "artifact_id_path": "parent.artifact_id",
                                "payload_hash_path": "parent.payload_hash",
                                "allowed_artifact_types": ["parent_alpha"],
                                "cardinality": "one",
                            },
                        ],
                    }
                ),
            )

            drift_registry = ArtifactSchemaRegistry()
            for artifact_type in ("parent_alpha", "parent_beta"):
                drift_registry.register(
                    artifact_type,
                    "automarkov.test-payload-parent.v1",
                    _PayloadParentArtifact,
                    direct_parent_artifact_types=(),
                )
            drift_registry.register(
                "payload_bound_child",
                "automarkov.test-payload-bound-child.v1",
                _PayloadBoundChildArtifact,
                payload_parent_bindings=(
                    ParentBinding(
                        artifact_id_path="many_parents.*.artifact_id",
                        payload_hash_path="many_parents.*.payload_hash",
                        allowed_artifact_types=("parent_alpha", "parent_beta"),
                        cardinality="many",
                    ),
                    ParentBinding(
                        artifact_id_path="optional_parent.artifact_id",
                        payload_hash_path="optional_parent.payload_hash",
                        allowed_artifact_types=("parent_alpha",),
                        cardinality="optional",
                    ),
                    ParentBinding(
                        artifact_id_path="parent.artifact_id",
                        payload_hash_path="parent.payload_hash",
                        allowed_artifact_types=("parent_beta",),
                        cardinality="one",
                    ),
                ),
            )
            drift_registry.freeze()
            drift_repository = SqliteArtifactRepository(
                tmp_path / "closed-payload-binding.sqlite",
                drift_registry,
            )
            try:
                with pytest.raises(ArtifactIntegrityError):
                    drift_repository.get(result.artifact_id)
            finally:
                drift_repository.close()
    finally:
        if isinstance(repository, SqliteArtifactRepository):
            repository.close()


@pytest.mark.parametrize(
    ("attribute", "replacement"),
    [
        pytest.param("model_type", _PlainFloatArtifact, id="model-type"),
        pytest.param("schema", {}, id="schema"),
        pytest.param("schema_bytes", b"forged", id="schema-bytes"),
        pytest.param("schema_id", "sha256:" + "0" * 64, id="schema-id"),
    ],
)
def test_registered_codec_public_attributes_are_read_only(
    attribute: str,
    replacement: object,
) -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_strict_float",
        "automarkov.test-strict-float.v1",
        _StrictFloatArtifact,
        direct_parent_artifact_types=(),
    )
    codec = registry.resolve(
        "test_strict_float",
        {"schema_version": "automarkov.test-strict-float.v1"},
    ).codec

    with pytest.raises(AttributeError):
        setattr(codec, attribute, replacement)


def test_registered_codec_schema_returns_an_isolated_copy() -> None:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "test_strict_float",
        "automarkov.test-strict-float.v1",
        _StrictFloatArtifact,
        direct_parent_artifact_types=(),
    )
    codec = registry.resolve(
        "test_strict_float",
        {"schema_version": "automarkov.test-strict-float.v1"},
    ).codec

    first = codec.schema
    second = codec.schema
    assert first == second
    assert first is not second

    first["forged"] = True
    assert "forged" not in codec.schema
