from __future__ import annotations

import copy
import json
import math
from collections.abc import Callable, Mapping
from hashlib import sha256
from typing import Literal, cast

import pytest
import rfc8785

import automarkov.canonical as canonical_module
from automarkov.canonical import (
    MAX_CANONICAL_DOCUMENT_BYTES,
    MAX_JSON_NESTING_DEPTH,
    MAX_JSON_NODES,
    MAX_JSON_PAYLOAD_BYTES,
    CanonicalJsonValue,
    CanonicalPayloadCodec,
    FrozenStringMapping,
    SafeCanonicalInt,
    StrictCanonicalFloat,
    canonical_json_bytes,
    parse_canonical_document,
    parse_json_payload,
    validate_and_measure_raw_json_tree,
)
from automarkov.domain import (
    RequestBudget,
    RequestPermissions,
    StrictFrozenModel,
    TaskRequest,
    validate_task_request_payload,
)
from automarkov.public import validate_task_request_json


class _NumericArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-numeric-artifact.v1"]
    integer_value: SafeCanonicalInt
    float_value: StrictCanonicalFloat
    signed_zero: StrictCanonicalFloat
    metadata: CanonicalJsonValue


class _FloatMappingArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-float-mapping.v1"]
    values: FrozenStringMapping[StrictCanonicalFloat]


class _BoundaryPayloadArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.test-boundary-payload.v1"]
    value: str


def _numeric_payload() -> dict[str, object]:
    return {
        "schema_version": "automarkov.test-numeric-artifact.v1",
        "integer_value": 1,
        "float_value": 1.0,
        "signed_zero": -0.0,
        "metadata": {
            "whole": 1.0,
            "minus_zero": -0.0,
            "fraction": 0.5,
        },
    }


def _nested_array(depth: int) -> object:
    value: object = 0
    for _ in range(depth):
        value = [value]
    return value


def test_json_ingress_enforces_all_three_resource_boundaries() -> None:
    assert MAX_JSON_PAYLOAD_BYTES == 8 * 1024 * 1024
    assert MAX_JSON_NESTING_DEPTH == 128
    assert MAX_JSON_NODES == 1_000_000

    boundary_bytes = b'"' + b"a" * (MAX_JSON_PAYLOAD_BYTES - 2) + b'"'
    parsed_boundary = parse_json_payload(boundary_bytes)
    assert type(parsed_boundary) is str
    assert len(parsed_boundary) == MAX_JSON_PAYLOAD_BYTES - 2
    with pytest.raises(ValueError):
        parse_json_payload(boundary_bytes[:-1] + b'a"')

    boundary_depth = _nested_array(MAX_JSON_NESTING_DEPTH)
    assert validate_and_measure_raw_json_tree(boundary_depth) == 257
    assert isinstance(
        parse_json_payload(
            b"[" * MAX_JSON_NESTING_DEPTH + b"0" + b"]" * MAX_JSON_NESTING_DEPTH
        ),
        list,
    )
    with pytest.raises(ValueError):
        validate_and_measure_raw_json_tree(_nested_array(MAX_JSON_NESTING_DEPTH + 1))
    empty_container_chain: object = []
    for _ in range(MAX_JSON_NESTING_DEPTH):
        empty_container_chain = [empty_container_chain]
    with pytest.raises(ValueError):
        validate_and_measure_raw_json_tree(empty_container_chain)
    with pytest.raises(ValueError):
        parse_json_payload(
            b"[" * (MAX_JSON_NESTING_DEPTH + 1)
            + b"0"
            + b"]" * (MAX_JSON_NESTING_DEPTH + 1)
        )

    boundary_nodes = [0] * (MAX_JSON_NODES - 1)
    assert validate_and_measure_raw_json_tree(boundary_nodes) == 2 * MAX_JSON_NODES - 1
    with pytest.raises(ValueError):
        validate_and_measure_raw_json_tree([0] * MAX_JSON_NODES)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b'{"outer":{"x":1,"x":2}}', id="nested-duplicate"),
        pytest.param(b"\xef\xbb\xbf{}", id="bom"),
        pytest.param(b'{"x":"\xff"}', id="invalid-utf8"),
        pytest.param(b'{"x":NaN}', id="nan-token"),
        pytest.param(b'{"x":Infinity}', id="infinity-token"),
        pytest.param(b'{"x":-Infinity}', id="negative-infinity-token"),
        pytest.param(b'{"x":1e400}', id="float-overflow"),
        pytest.param(b'{"x":1e-400}', id="positive-float-underflow"),
        pytest.param(b'{"x":-1e-400}', id="negative-float-underflow"),
        pytest.param(b'{"x":9007199254740992}', id="unsafe-integer"),
        pytest.param(b'{"x":"\\ud800"}', id="lone-surrogate"),
    ],
)
def test_json_bytes_reject_non_interoperable_or_ambiguous_values(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_json_payload(raw)


def test_integer_negative_zero_is_accepted_and_canonicalized() -> None:
    parsed = parse_json_payload(b'{"x":-0}')

    assert type(parsed) is dict
    assert parsed == {"x": 0}
    assert type(parsed["x"]) is int
    assert canonical_json_bytes(parsed) == b'{"x":0}'


class _DictSubclass(dict[str, object]):
    pass


class _StringSubclass(str):
    pass


@pytest.mark.parametrize(
    "value",
    [
        pytest.param({"value": object()}, id="arbitrary-object"),
        pytest.param({"value": (1, 2)}, id="tuple"),
        pytest.param({"value": {1, 2}}, id="set"),
        pytest.param({1: "non-string-key"}, id="non-string-key"),
        pytest.param(_DictSubclass(value=1), id="dict-subclass"),
        pytest.param({"value": _StringSubclass("subclass")}, id="str-subclass"),
        pytest.param({"value": 9_007_199_254_740_992}, id="unsafe-integer"),
        pytest.param({"value": math.nan}, id="nan"),
        pytest.param({"value": math.inf}, id="infinity"),
        pytest.param({"value": "\ud800"}, id="lone-surrogate"),
    ],
)
def test_python_tree_ingress_accepts_only_exact_json_builtins(value: object) -> None:
    with pytest.raises(ValueError):
        validate_and_measure_raw_json_tree(value)


def test_python_tree_ingress_rejects_cycles_and_model_provenance() -> None:
    cyclic_list: list[object] = []
    cyclic_list.append(cyclic_list)
    cyclic_dict: dict[str, object] = {}
    cyclic_dict["self"] = cyclic_dict
    for value in (cyclic_list, cyclic_dict):
        with pytest.raises(ValueError):
            validate_and_measure_raw_json_tree(value)

    codec = CanonicalPayloadCodec(_NumericArtifact)
    validated = _NumericArtifact.model_validate(_numeric_payload())
    constructed = _NumericArtifact.model_construct(
        schema_version="automarkov.test-numeric-artifact.v1",
        integer_value=1,
        float_value=1.0,
        signed_zero=-0.0,
        metadata={},
    )
    copied = validated.model_copy(update={"float_value": 2.0})
    for value in (
        validated,
        constructed,
        copied,
        _numeric_payload() | {"metadata": {"nested_model": validated}},
    ):
        with pytest.raises(ValueError):
            codec.encode(value)


@pytest.mark.parametrize(
    "invalid_budget",
    [
        pytest.param(
            RequestBudget.model_construct(
                schema_version="automarkov.request-budget.v1",
                wall_time_seconds=0,
                llm_token_limit=0,
                tool_call_limit=0,
            ),
            id="model-construct",
        ),
        pytest.param(
            RequestBudget(
                schema_version="automarkov.request-budget.v1",
                wall_time_seconds=1,
                llm_token_limit=0,
                tool_call_limit=0,
            ).model_copy(update={"wall_time_seconds": 0}),
            id="model-copy",
        ),
    ],
)
def test_task_request_ingress_rejects_invalid_nested_model_instances(
    invalid_budget: RequestBudget,
) -> None:
    with pytest.raises(ValueError):
        validate_task_request_payload(
            {
                "schema_version": "automarkov.task-request.v1",
                "request_id": "request_nested_provenance",
                "task_text": "Model a bounded inventory process.",
                "budget": invalid_budget,
                "permissions": RequestPermissions(
                    schema_version="automarkov.request-permissions.v1",
                    allow_retrieval=False,
                    allow_clarification=False,
                    allow_code_execution=False,
                ),
            }
        )


def _task_request_payload() -> dict[str, object]:
    return {
        "schema_version": "automarkov.task-request.v1",
        "request_id": "request_public_ingress",
        "task_text": "Model a bounded inventory process.",
        "budget": {
            "schema_version": "automarkov.request-budget.v1",
            "wall_time_seconds": 1,
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


def test_task_request_raw_dict_and_duplicate_aware_json_ingress() -> None:
    payload = _task_request_payload()
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    direct = TaskRequest.model_validate(payload)
    approved_python = validate_task_request_payload(payload)
    approved_json = validate_task_request_json(raw)
    assert (
        direct.model_dump()
        == approved_python.model_dump()
        == approved_json.model_dump()
    )
    assert not direct.has_validated_provenance()
    assert approved_python.has_validated_provenance()
    assert approved_json.has_validated_provenance()
    with pytest.raises(ValueError, match="validate_task_request_json"):
        TaskRequest.model_validate_json(raw)

    duplicate = raw.replace(
        b'"wall_time_seconds":1',
        b'"wall_time_seconds":1,"wall_time_seconds":1',
    )
    assert duplicate != raw
    with pytest.raises(ValueError, match="duplicate JSON member"):
        validate_task_request_json(duplicate)


@pytest.mark.parametrize(
    ("field_name", "copied"),
    [
        pytest.param("budget", False, id="existing-budget"),
        pytest.param("budget", True, id="copied-budget"),
        pytest.param("permissions", False, id="existing-permissions"),
        pytest.param("permissions", True, id="copied-permissions"),
    ],
)
def test_task_request_python_ingress_rejects_existing_or_copied_nested_models(
    field_name: str,
    copied: bool,
) -> None:
    payload = _task_request_payload()
    nested_model: RequestBudget | RequestPermissions
    if field_name == "budget":
        nested_model = RequestBudget.model_validate(payload[field_name])
    else:
        nested_model = RequestPermissions.model_validate(payload[field_name])
    if copied:
        nested_model = nested_model.model_copy()

    with pytest.raises(ValueError):
        validate_task_request_payload(payload | {field_name: nested_model})


@pytest.mark.parametrize("nested", [False, True], ids=["root", "nested"])
def test_task_request_python_ingress_rejects_dict_subclasses(nested: bool) -> None:
    class DictSubclass(dict[str, object]):
        pass

    payload = _task_request_payload()
    budget = payload["budget"]
    assert type(budget) is dict
    candidate: object = (
        payload | {"budget": DictSubclass(budget)} if nested else DictSubclass(payload)
    )

    with pytest.raises(ValueError):
        validate_task_request_payload(candidate)


def test_json_float_with_unrepresentable_decimal_exponent_is_contract_rejection() -> (
    None
):
    with pytest.raises(ValueError, match="JSON float"):
        parse_json_payload(b'{"value":1e999999999999999999999999999}')


def test_canonical_json_bytes_match_rfc8785_rules() -> None:
    value = {
        "\u20ac": "Euro Sign",
        "\r": "Carriage Return",
        "\ufb33": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "\U0001f600": "Emoji: Grinning Face",
        "\u0080": "Control",
        "\u00f6": "Latin Small Letter O With Diaeresis",
        "numbers": [333333333.33333329, 4.50, 2e-3, -0.0],
    }

    assert canonical_json_bytes(value) == (
        b'{"\\r":"Carriage Return","1":"One","numbers":'
        b"[333333333.3333333,4.5,0.002,0],"
        b'"\xc2\x80":"Control","\xc3\xb6":"Latin Small Letter O With Diaeresis",'
        b'"\xe2\x82\xac":"Euro Sign","\xf0\x9f\x98\x80":"Emoji: Grinning Face",'
        b'"\xef\xac\xb3":"Hebrew Letter Dalet With Dagesh"}'
    )


def test_typed_codec_preserves_int_float_types_and_canonical_byte_identity() -> None:
    codec = CanonicalPayloadCodec(_NumericArtifact)
    payload = _numeric_payload()

    encoded = codec.encode(payload)
    document = parse_json_payload(encoded)
    expected_schema_id = (
        "sha256:"
        + sha256(canonical_json_bytes(_NumericArtifact.model_json_schema())).hexdigest()
    )

    assert codec.schema_id == expected_schema_id
    assert document == {
        "schema_id": expected_schema_id,
        "exact_float_paths": [
            "/payload/float_value",
            "/payload/signed_zero",
        ],
        "payload": {
            "schema_version": "automarkov.test-numeric-artifact.v1",
            "integer_value": 1,
            "float_value": 1,
            "signed_zero": 0,
            "metadata": {
                "whole": 1,
                "minus_zero": 0,
                "fraction": 0.5,
            },
        },
    }
    assert encoded == codec.encode(payload)

    first = codec.decode(encoded)
    second = codec.decode(encoded)
    assert first == second
    assert first is not second
    assert type(first.integer_value) is int
    assert type(first.float_value) is float
    assert type(first.signed_zero) is float
    assert math.copysign(1.0, first.signed_zero) == 1.0
    assert isinstance(first.metadata, Mapping)
    assert type(first.metadata["whole"]) is int
    assert type(first.metadata["minus_zero"]) is int
    with pytest.raises(TypeError):
        cast(dict[str, object], first.metadata)["whole"] = 2


def test_typed_codec_round_trips_exact_float_mapping_values() -> None:
    codec = CanonicalPayloadCodec(_FloatMappingArtifact)
    encoded = codec.encode(
        {
            "schema_version": "automarkov.test-float-mapping.v1",
            "values": {"whole": 1.0, "minus_zero": -0.0, "fraction": 0.5},
        }
    )
    document = parse_canonical_document(encoded)

    assert type(document) is dict
    assert document["exact_float_paths"] == [
        "/payload/values/fraction",
        "/payload/values/minus_zero",
        "/payload/values/whole",
    ]
    decoded = codec.decode(encoded)
    assert all(type(value) is float for value in decoded.values.values())
    assert decoded.values == {"whole": 1.0, "minus_zero": 0.0, "fraction": 0.5}
    assert math.copysign(1.0, decoded.values["minus_zero"]) == 1.0


def test_exact_raw_payload_boundary_uses_an_independent_document_limit() -> None:
    codec = CanonicalPayloadCodec(_BoundaryPayloadArtifact)
    empty_payload: dict[str, object] = {
        "schema_version": "automarkov.test-boundary-payload.v1",
        "value": "",
    }
    payload = empty_payload | {
        "value": "a"
        * (MAX_JSON_PAYLOAD_BYTES - validate_and_measure_raw_json_tree(empty_payload))
    }

    assert validate_and_measure_raw_json_tree(payload) == MAX_JSON_PAYLOAD_BYTES
    encoded = codec.encode(payload)
    assert MAX_JSON_PAYLOAD_BYTES < len(encoded) <= MAX_CANONICAL_DOCUMENT_BYTES
    assert codec.decode(encoded).value == payload["value"]

    with pytest.raises(ValueError, match="JSON payload exceeds byte limit"):
        parse_canonical_document(b" " * (MAX_CANONICAL_DOCUMENT_BYTES + 1))


def test_exact_float_path_collection_applies_document_budget_before_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codec = CanonicalPayloadCodec(_FloatMappingArtifact)
    payload = {
        "schema_version": "automarkov.test-float-mapping.v1",
        "values": {"long_common_prefix": 1.0},
    }
    empty_path_document = {
        "schema_id": codec.schema_id,
        "exact_float_paths": [],
        "payload": payload,
    }
    monkeypatch.setattr(
        canonical_module,
        "MAX_CANONICAL_DOCUMENT_BYTES",
        len(rfc8785.dumps(empty_path_document)) + 1,
    )

    def fail_if_document_encoding_starts(_: object) -> bytes:
        raise AssertionError("oversized path map reached full document encoding")

    monkeypatch.setattr(
        canonical_module,
        "_canonical_document_bytes",
        fail_if_document_encoding_starts,
    )

    with pytest.raises(ValueError, match="canonical document exceeds byte limit"):
        codec.encode(payload)


def test_canonical_document_limit_counts_rfc8785_number_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value_count = 20
    document = {
        "schema_id": "sha256:" + "0" * 64,
        "exact_float_paths": [
            f"/payload/values/{index}" for index in range(value_count)
        ],
        "payload": {"values": [1e-6] * value_count},
    }
    repr_size = canonical_module._validate_and_measure_json_tree(
        document,
        1_000_000,
    )
    jcs_size = len(rfc8785.dumps(document))
    limit = (repr_size + jcs_size) // 2
    assert repr_size < limit < jcs_size
    monkeypatch.setattr(canonical_module, "MAX_CANONICAL_DOCUMENT_BYTES", limit)

    with pytest.raises(ValueError, match="canonical document exceeds byte limit"):
        canonical_module._canonical_document_bytes(document)


def test_typed_codec_revalidates_type_map_and_rejects_noncanonical_bytes() -> None:
    codec = CanonicalPayloadCodec(_NumericArtifact)
    encoded = codec.encode(_numeric_payload())
    document = parse_json_payload(encoded)
    assert type(document) is dict

    with pytest.raises(ValueError):
        codec.encode(_numeric_payload() | {"float_value": 1})

    noncanonical = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    assert noncanonical != encoded
    with pytest.raises(ValueError):
        codec.decode(noncanonical)

    def remove_float_path(value: dict[str, object]) -> None:
        value["exact_float_paths"] = ["/payload/float_value"]

    def add_ineligible_path(value: dict[str, object]) -> None:
        value["exact_float_paths"] = [
            "/payload/float_value",
            "/payload/integer_value",
            "/payload/signed_zero",
        ]

    def duplicate_float_path(value: dict[str, object]) -> None:
        value["exact_float_paths"] = [
            "/payload/float_value",
            "/payload/float_value",
            "/payload/signed_zero",
        ]

    def reorder_float_paths(value: dict[str, object]) -> None:
        value["exact_float_paths"] = [
            "/payload/signed_zero",
            "/payload/float_value",
        ]

    def forge_schema_id(value: dict[str, object]) -> None:
        value["schema_id"] = "sha256:" + "0" * 64

    def add_document_member(value: dict[str, object]) -> None:
        value["caller_controlled"] = True

    mutations: tuple[Callable[[dict[str, object]], None], ...] = (
        remove_float_path,
        add_ineligible_path,
        duplicate_float_path,
        reorder_float_paths,
        forge_schema_id,
        add_document_member,
    )
    for mutate in mutations:
        forged = copy.deepcopy(document)
        mutate(cast(dict[str, object], forged))
        with pytest.raises(ValueError):
            codec.decode(canonical_json_bytes(forged))
