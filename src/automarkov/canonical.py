from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from math import isfinite
from types import MappingProxyType
from typing import Annotated, Any, Generic, Literal, TypeAlias, TypeVar, cast

import rfc8785
from jsonschema import Draft202012Validator, validators
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    PlainSerializer,
    TypeAdapter,
    WithJsonSchema,
)
from typing_extensions import TypeAliasType

MAX_JSON_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_CANONICAL_DOCUMENT_BYTES = 32 * 1024 * 1024
MAX_JSON_NESTING_DEPTH = 128
MAX_JSON_NODES = 1_000_000
MAX_CANONICAL_DOCUMENT_NESTING_DEPTH = MAX_JSON_NESTING_DEPTH + 1
MAX_CANONICAL_DOCUMENT_NODES = 2 * MAX_JSON_NODES + 16
MAX_SAFE_INTEGER = 2**53 - 1

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)


def _contains_lone_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _require_safe_int(value: object) -> int:
    if type(value) is not int or abs(value) > MAX_SAFE_INTEGER:
        raise ValueError("expected an IEEE-754 interoperable integer")
    return value


def _require_exact_float(value: object) -> float:
    if type(value) is not float or not isfinite(value) or abs(value) > MAX_SAFE_INTEGER:
        raise ValueError("expected an interoperable finite JSON float")
    return 0.0 if value == 0.0 else value


def _require_exact_true(value: object) -> Literal[True]:
    if type(value) is not bool or value is not True:
        raise ValueError("expected the exact boolean true")
    return True


def _is_exact_true_literal(value: object) -> bool:
    return (
        type(value) is list
        and len(value) == 1
        and type(value[0]) is bool
        and value[0] is True
    )


SafeCanonicalInt = Annotated[
    int,
    BeforeValidator(_require_safe_int),
    WithJsonSchema(
        {
            "type": "integer",
            "minimum": -MAX_SAFE_INTEGER,
            "maximum": MAX_SAFE_INTEGER,
        }
    ),
]
StrictCanonicalFloat = Annotated[
    float,
    BeforeValidator(_require_exact_float),
    WithJsonSchema(
        {
            "type": "number",
            "minimum": -MAX_SAFE_INTEGER,
            "maximum": MAX_SAFE_INTEGER,
            "x-automarkov-number-kind": "exact-float",
        }
    ),
]
StrictTrue = Annotated[
    Literal[True],
    BeforeValidator(_require_exact_true),
    WithJsonSchema(
        {
            "type": "boolean",
            "const": True,
            "x-automarkov-boolean-kind": "strict-true",
        }
    ),
]


def _validate_wire_json_tree(
    value: object,
    *,
    maximum_depth: int = MAX_JSON_NESTING_DEPTH,
    maximum_nodes: int = MAX_JSON_NODES,
) -> object:
    stack: list[tuple[object, int, bool]] = [(value, 0, False)]
    active_containers: set[int] = set()
    nodes = 0
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active_containers.remove(id(current))
            continue
        nodes += 1
        if (
            nodes > maximum_nodes
            or depth > maximum_depth
            or (type(current) in {list, dict} and depth >= maximum_depth)
        ):
            raise ValueError("JSON value exceeds resource limits")
        if current is None or type(current) is bool:
            continue
        if type(current) is int:
            _require_safe_int(current)
            continue
        if type(current) is float:
            _require_exact_float(current)
            continue
        if type(current) is str:
            if _contains_lone_surrogate(current):
                raise ValueError("JSON strings must not contain lone surrogates")
            continue
        if isinstance(current, BaseModel):
            raise ValueError(  # noqa: TRY004 - 公共合同固定使用 ValueError
                "public JSON trees must not contain BaseModel instances"
            )
        if type(current) not in {list, dict}:
            raise ValueError("expected an exact JSON built-in value")

        identity = id(current)
        if identity in active_containers:
            raise ValueError("JSON trees must be acyclic")
        active_containers.add(identity)
        stack.append((current, depth, True))
        if type(current) is list:
            stack.extend((item, depth + 1, False) for item in current)
            continue
        for key, item in cast(dict[object, object], current).items():
            if type(key) is not str:
                raise ValueError("JSON object keys must be exact strings")
            stack.append((item, depth + 1, False))
            stack.append((key, depth + 1, False))
    return value


def _compact_string_size(value: str, maximum: int) -> int:
    size = 2
    short_escapes = {0x08, 0x09, 0x0A, 0x0C, 0x0D}
    for character in value:
        codepoint = ord(character)
        if character in {'"', "\\"} or codepoint in short_escapes:
            size += 2
        elif codepoint < 0x20:
            size += 6
        else:
            size += len(character.encode("utf-8"))
        if size > maximum:
            raise ValueError("raw JSON payload exceeds byte limit")
    return size


def _validate_and_measure_json_tree(
    value: object,
    maximum_bytes: int,
    *,
    maximum_depth: int = MAX_JSON_NESTING_DEPTH,
    maximum_nodes: int = MAX_JSON_NODES,
    canonical_numbers: bool = False,
) -> int:
    _validate_wire_json_tree(
        value,
        maximum_depth=maximum_depth,
        maximum_nodes=maximum_nodes,
    )
    total = 0
    active_containers: set[int] = set()
    stack: list[tuple[str, object, int]] = [("value", value, 0)]

    def add_size(size: int) -> None:
        nonlocal total
        total += size
        if total > maximum_bytes:
            raise ValueError("raw JSON payload exceeds byte limit")

    while stack:
        frame_kind, current, identity = stack.pop()
        if frame_kind == "list_iterator":
            iterator = cast(Iterator[object], current)
            try:
                item = next(iterator)
            except StopIteration:
                active_containers.remove(identity)
            else:
                stack.append((frame_kind, iterator, identity))
                stack.append(("value", item, 0))
            continue
        if frame_kind == "dict_iterator":
            iterator = cast(Iterator[tuple[str, object]], current)
            try:
                key, item = next(iterator)
            except StopIteration:
                active_containers.remove(identity)
            else:
                stack.append((frame_kind, iterator, identity))
                add_size(_compact_string_size(key, maximum_bytes - total))
                stack.append(("value", item, 0))
            continue

        if current is None:
            add_size(4)
        elif type(current) is bool:
            add_size(4 if current else 5)
        elif type(current) is int:
            add_size(len(str(current)))
        elif type(current) is float:
            add_size(
                len(rfc8785.dumps(cast(Any, current)))
                if canonical_numbers
                else len(repr(current))
            )
        elif type(current) is str:
            add_size(_compact_string_size(current, maximum_bytes - total))
        elif type(current) is list:
            identity = id(current)
            if identity in active_containers:
                raise ValueError("raw JSON tree must be acyclic")
            active_containers.add(identity)
            add_size(2 + max(len(current) - 1, 0))
            stack.append(("list_iterator", iter(current), identity))
        elif type(current) is dict:
            identity = id(current)
            if identity in active_containers:
                raise ValueError("raw JSON tree must be acyclic")
            active_containers.add(identity)
            add_size(2 + len(current) + max(len(current) - 1, 0))
            stack.append(("dict_iterator", iter(current.items()), identity))
        else:  # pragma: no cover - 已由 _validate_wire_json_tree 保证
            raise ValueError("unexpected raw JSON value")
    return total


def validate_and_measure_raw_json_tree(value: object) -> int:
    """验证不可信 Python JSON 树并返回紧凑编码大小。"""

    return _validate_and_measure_json_tree(value, MAX_JSON_PAYLOAD_BYTES)


def _reject_duplicate_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member: {key!r}")
        result[key] = value
    return result


def _reject_nonfinite_constant(token: str) -> object:
    raise ValueError(f"non-finite JSON number: {token}")


def _parse_finite_float(token: str) -> float:
    try:
        decimal_value = Decimal(token)
        value = float(token)
        if not isfinite(value):
            raise ValueError("JSON floats must be finite")
        if decimal_value != 0 and value == 0.0:
            raise ValueError("nonzero JSON float underflowed to zero")
        return value
    except InvalidOperation as error:
        raise ValueError(
            "JSON float is outside the supported decimal domain"
        ) from error


def _parse_safe_int(token: str) -> int:
    if len(token.removeprefix("-")) > 16:
        raise ValueError("JSON integer exceeds the interoperable domain")
    return _require_safe_int(int(token))


def _enforce_json_input_limits(
    raw: bytes,
    maximum_bytes: int,
    *,
    maximum_depth: int,
    maximum_nodes: int,
) -> None:
    if type(raw) is not bytes:
        raise ValueError("JSON input must be exact bytes")
    if len(raw) > maximum_bytes:
        raise ValueError("JSON payload exceeds byte limit")
    depth = 0
    nodes = 0
    in_string = False
    in_scalar = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            nodes += 1
            in_string = True
            in_scalar = False
        elif byte in {0x5B, 0x7B}:
            nodes += 1
            depth += 1
            in_scalar = False
            if depth > maximum_depth:
                raise ValueError("JSON payload exceeds nesting limit")
        elif byte in {0x5D, 0x7D}:
            depth -= 1
            in_scalar = False
            if depth < 0:
                raise ValueError("malformed JSON nesting")
        elif byte in {0x09, 0x0A, 0x0D, 0x20, 0x2C, 0x3A}:
            in_scalar = False
        elif not in_scalar:
            nodes += 1
            in_scalar = True
        if nodes > maximum_nodes:
            raise ValueError("JSON payload exceeds node limit")
    if in_string or depth != 0:
        raise ValueError("malformed JSON framing")


def _parse_json_bytes(
    raw: bytes,
    maximum_bytes: int,
    *,
    maximum_depth: int,
    maximum_nodes: int,
) -> JsonValue:
    _enforce_json_input_limits(
        raw,
        maximum_bytes,
        maximum_depth=maximum_depth,
        maximum_nodes=maximum_nodes,
    )
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("UTF-8 BOM is forbidden")
    try:
        decoded = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=_reject_nonfinite_constant,
            parse_float=_parse_finite_float,
            parse_int=_parse_safe_int,
        )
        return cast(
            JsonValue,
            _validate_wire_json_tree(
                decoded,
                maximum_depth=maximum_depth,
                maximum_nodes=maximum_nodes,
            ),
        )
    except (RecursionError, UnicodeDecodeError) as error:
        raise ValueError("invalid bounded UTF-8 JSON payload") from error


def parse_json_payload(raw: bytes) -> JsonValue:
    """解析不可信 JSON bytes，同时保留重复键检测与数值 token 类型。"""

    return _parse_json_bytes(
        raw,
        MAX_JSON_PAYLOAD_BYTES,
        maximum_depth=MAX_JSON_NESTING_DEPTH,
        maximum_nodes=MAX_JSON_NODES,
    )


def parse_canonical_document(raw: bytes) -> JsonValue:
    """解析受独立上限约束的 repository 内部 canonical document。"""

    return _parse_json_bytes(
        raw,
        MAX_CANONICAL_DOCUMENT_BYTES,
        maximum_depth=MAX_CANONICAL_DOCUMENT_NESTING_DEPTH,
        maximum_nodes=MAX_CANONICAL_DOCUMENT_NODES,
    )


def canonical_json_bytes(value: object) -> bytes:
    """在 AutoMarkov 严格入口校验后返回 RFC 8785 bytes。"""

    validate_and_measure_raw_json_tree(value)
    return rfc8785.dumps(cast(Any, value))


def _canonical_document_bytes(value: object) -> bytes:
    try:
        _validate_and_measure_json_tree(
            value,
            MAX_CANONICAL_DOCUMENT_BYTES,
            maximum_depth=MAX_CANONICAL_DOCUMENT_NESTING_DEPTH,
            maximum_nodes=MAX_CANONICAL_DOCUMENT_NODES,
            canonical_numbers=True,
        )
    except ValueError as error:
        if "byte limit" in str(error):
            raise ValueError("canonical document exceeds byte limit") from error
        raise
    encoded = rfc8785.dumps(cast(Any, value))
    if len(encoded) > MAX_CANONICAL_DOCUMENT_BYTES:  # defensive postcondition
        raise ValueError("canonical document exceeds byte limit")
    return encoded


def _normalize_canonical_json(value: object) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        normalized = _require_exact_float(value)
        return int(normalized) if normalized.is_integer() else normalized
    if type(value) is list:
        return [_normalize_canonical_json(item) for item in value]
    if type(value) is dict:
        return {
            key: _normalize_canonical_json(item)
            for key, item in cast(dict[str, object], value).items()
        }
    raise ValueError("expected canonical JSON value")


def _canonical_json_input(value: object) -> object:
    if type(value) is tuple or type(value) is MappingProxyType:
        value = _thaw_json(value)
    validate_and_measure_raw_json_tree(value)
    return _normalize_canonical_json(value)


def _freeze_json(value: object) -> object:
    if type(value) is list:
        return tuple(_freeze_json(item) for item in value)
    if type(value) is dict:
        return MappingProxyType(
            {
                key: _freeze_json(item)
                for key, item in cast(dict[str, object], value).items()
            }
        )
    return value


def _thaw_json(value: object) -> object:
    if type(value) is tuple:
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    return value


_ValueT = TypeVar("_ValueT")


def _freeze_sequence(value: object) -> tuple[object, ...]:
    if type(value) is list or type(value) is tuple:
        return tuple(value)
    raise ValueError("expected a JSON array or immutable tuple")


def _thaw_frozen_string_mapping_input(value: object) -> object:
    if type(value) is dict:
        mapping = cast(dict[object, object], value)
    elif type(value) is MappingProxyType:
        mapping = cast(dict[object, object], _thaw_json(value))
    else:
        raise ValueError("expected a JSON object or immutable string mapping")
    if any(
        type(key) is not str or _contains_lone_surrogate(cast(str, key))
        for key in mapping
    ):
        raise ValueError("mapping keys must be exact strings without lone surrogates")
    return mapping


def _freeze_string_mapping(
    value: dict[str, _ValueT],
) -> Mapping[str, _ValueT]:
    return MappingProxyType(dict(value))


def _thaw_string_mapping(
    value: Mapping[str, _ValueT],
) -> dict[str, _ValueT]:
    return cast(dict[str, _ValueT], _thaw_json(value))


FrozenSequence = TypeAliasType(
    "FrozenSequence",
    Annotated[
        tuple[_ValueT, ...],
        BeforeValidator(_freeze_sequence),
    ],
    type_params=(_ValueT,),
)


_CanonicalJsonNode = TypeAliasType(
    "_CanonicalJsonNode",
    None
    | bool
    | int
    | float
    | str
    | list["_CanonicalJsonNode"]
    | dict[str, "_CanonicalJsonNode"],
)


class _CanonicalJsonSchema:
    def __get_pydantic_json_schema__(
        self, core_schema: Any, handler: Any
    ) -> dict[str, object]:
        schema = cast(dict[str, object], handler(core_schema))
        schema["x-automarkov-number-kind"] = "canonical-json-normalized"
        return schema


CanonicalJsonValue = TypeAliasType(
    "CanonicalJsonValue",
    Annotated[
        _CanonicalJsonNode,
        BeforeValidator(_canonical_json_input),
        AfterValidator(_freeze_json),
        PlainSerializer(_thaw_json, return_type=object, when_used="json"),
        _CanonicalJsonSchema(),
    ],
)

FrozenStringMapping = TypeAliasType(
    "FrozenStringMapping",
    Annotated[
        dict[str, _ValueT],
        BeforeValidator(_thaw_frozen_string_mapping_input),
        AfterValidator(_freeze_string_mapping),
        PlainSerializer(
            _thaw_string_mapping,
            return_type=dict[str, _ValueT],
            when_used="json",
        ),
    ],
    type_params=(_ValueT,),
)


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


class _ExactFloatPathCollector:
    """在保留去重路径时同步执行 canonical document byte budget。"""

    __slots__ = ("_encoded_bytes", "_maximum_bytes", "_paths")

    def __init__(self, maximum_bytes: int) -> None:
        if maximum_bytes < 2:
            raise ValueError("canonical document exceeds byte limit")
        self._maximum_bytes = maximum_bytes
        self._encoded_bytes = 2  # 空 JSON array 的 `[]`
        self._paths: set[str] = set()

    def add(self, path: str) -> None:
        if path in self._paths:
            return
        encoded_bytes = len(rfc8785.dumps(path))
        next_size = self._encoded_bytes + encoded_bytes + bool(self._paths)
        if next_size > self._maximum_bytes:
            raise ValueError("canonical document exceeds byte limit")
        self._paths.add(path)
        self._encoded_bytes = next_size

    def sorted_paths(self) -> list[str]:
        return sorted(self._paths, key=lambda item: item.encode("utf-8"))


def _resolve_schema(schema: object, root: dict[str, object]) -> dict[str, object]:
    if type(schema) is not dict:
        return {}
    current = cast(dict[str, object], schema)
    seen: set[str] = set()
    while type(current.get("$ref")) is str:
        reference = cast(str, current["$ref"])
        if reference in seen or not reference.startswith("#/$defs/"):
            raise ValueError("unsupported or cyclic JSON Schema reference")
        seen.add(reference)
        target: object = root
        for token in reference[2:].split("/"):
            if type(target) is not dict or token not in target:
                raise ValueError("unresolved JSON Schema reference")
            target = cast(dict[str, object], target)[token]
        if type(target) is not dict:
            raise ValueError("JSON Schema reference did not resolve to an object")
        current = cast(dict[str, object], target)
    return current


def _is_canonical_json_schema(
    schema: dict[str, object],
    root: dict[str, object],
) -> bool:
    current = schema
    seen: set[str] = set()
    while True:
        if current.get("x-automarkov-number-kind") == "canonical-json-normalized":
            return True
        reference = current.get("$ref")
        if (
            type(reference) is not str
            or reference in seen
            or not reference.startswith("#/$defs/")
        ):
            return False
        seen.add(reference)
        target: object = root
        for token in reference[2:].split("/"):
            if type(target) is not dict or token not in target:
                return False
            target = cast(dict[str, object], target)[token]
        if type(target) is not dict:
            return False
        current = cast(dict[str, object], target)


def _is_exact_integer(_: object, value: object) -> bool:
    return type(value) is int


def _is_exact_number(_: object, value: object) -> bool:
    return type(value) is float


def _is_restored_number(_: object, value: object) -> bool:
    return type(value) in {int, float}


def _schema_value_type_matches(
    declared_type: object,
    value: object,
    *,
    allow_integer_number_overlap: bool,
) -> bool:
    if type(declared_type) is list:
        return any(
            _schema_value_type_matches(
                item,
                value,
                allow_integer_number_overlap=allow_integer_number_overlap,
            )
            for item in cast(list[object], declared_type)
        )
    expected_types: dict[str, tuple[type[object], ...]] = {
        "null": (type(None),),
        "boolean": (bool,),
        "integer": (int,),
        "number": (float, int) if allow_integer_number_overlap else (float,),
        "string": (str,),
        "array": (list,),
        "object": (dict,),
    }
    expected = expected_types.get(cast(str, declared_type))
    return expected is None or type(value) in expected


_EXACT_JSON_SCHEMA_VALIDATOR = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine_many(
        {"integer": _is_exact_integer, "number": _is_exact_number}
    ),
)
_RESTORED_JSON_SCHEMA_VALIDATOR = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine_many(
        {"integer": _is_exact_integer, "number": _is_restored_number}
    ),
)


def _schema_matches_value(
    schema: dict[str, object],
    root: dict[str, object],
    value: object,
    *,
    allow_integer_number_overlap: bool,
) -> bool:
    definitions = root.get("$defs")
    evaluation_schema: dict[str, object] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": definitions if type(definitions) is dict else {},
        "allOf": [schema],
    }
    validator_type = (
        _RESTORED_JSON_SCHEMA_VALIDATOR
        if allow_integer_number_overlap
        else _EXACT_JSON_SCHEMA_VALIDATOR
    )
    return validator_type(evaluation_schema).is_valid(value)


def _schema_branches(
    schema: dict[str, object],
    root: dict[str, object],
    value: object,
    *,
    allow_integer_number_overlap: bool = False,
) -> tuple[dict[str, object], ...]:
    resolved = _resolve_schema(schema, root)
    branches: list[dict[str, object]] = []
    for keyword in ("oneOf", "anyOf"):
        alternatives = resolved.get(keyword)
        if type(alternatives) is list:
            branches = [
                _resolve_schema(candidate, root)
                for candidate in alternatives
                if type(candidate) is dict
            ]
            break
    if not branches:
        branches = [resolved]
    type_matching = tuple(
        branch
        for branch in branches
        if branch.get("type") is None
        or _schema_value_type_matches(
            branch.get("type"),
            value,
            allow_integer_number_overlap=allow_integer_number_overlap,
        )
    )
    if len(type_matching) == 1:
        return type_matching
    matching = tuple(
        branch
        for branch in type_matching
        if _schema_matches_value(
            branch,
            root,
            value,
            allow_integer_number_overlap=allow_integer_number_overlap,
        )
    )
    if len(matching) > 1 and not allow_integer_number_overlap:
        raise ValueError("ambiguous schema union cannot preserve wire branch identity")
    return matching


def _collect_exact_float_paths(
    value: object,
    schema: dict[str, object],
    root: dict[str, object],
    path: str,
    output: _ExactFloatPathCollector,
) -> None:
    if _is_canonical_json_schema(schema, root):
        return
    branches = _schema_branches(schema, root, value)
    if type(value) is float and any(
        branch.get("x-automarkov-number-kind") == "exact-float" for branch in branches
    ):
        output.add(path)
        return
    if type(value) is dict:
        for key, item in cast(dict[str, object], value).items():
            for branch in branches:
                properties = branch.get("properties")
                child_schema: object = None
                if type(properties) is dict and type(properties.get(key)) is dict:
                    child_schema = properties[key]
                elif type(branch.get("additionalProperties")) is dict:
                    child_schema = branch["additionalProperties"]
                if type(child_schema) is dict:
                    _collect_exact_float_paths(
                        item,
                        cast(dict[str, object], child_schema),
                        root,
                        f"{path}/{_pointer_token(key)}",
                        output,
                    )
    elif type(value) is list:
        for index, item in enumerate(value):
            for branch in branches:
                prefix_items = branch.get("prefixItems")
                item_schema: object = (
                    prefix_items[index]
                    if type(prefix_items) is list and index < len(prefix_items)
                    else branch.get("items")
                )
                if type(item_schema) is dict:
                    _collect_exact_float_paths(
                        item,
                        cast(dict[str, object], item_schema),
                        root,
                        f"{path}/{index}",
                        output,
                    )


def _decode_pointer(pointer: str) -> tuple[str, ...]:
    if not pointer.startswith("/payload/"):
        raise ValueError("exact-float path must address the payload")
    tokens = pointer[1:].split("/")
    decoded: list[str] = []
    for token in tokens:
        index = 0
        output = ""
        while index < len(token):
            if token[index] != "~":
                output += token[index]
                index += 1
                continue
            if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
                raise ValueError("invalid RFC 6901 pointer escape")
            output += "~" if token[index + 1] == "0" else "/"
            index += 2
        decoded.append(output)
    return tuple(decoded)


def _schemas_at_tokens(
    schema: dict[str, object],
    root: dict[str, object],
    value: object,
    tokens: tuple[str, ...],
) -> tuple[dict[str, object], ...]:
    candidates = _schema_branches(
        schema,
        root,
        value,
        allow_integer_number_overlap=True,
    )
    current_value = value
    for token in tokens:
        next_candidates: list[dict[str, object]] = []
        if type(current_value) is dict:
            if token not in current_value:
                raise ValueError("exact-float path does not exist")
            current_value = current_value[token]
            for candidate in candidates:
                properties = candidate.get("properties")
                child_schema: object = None
                if type(properties) is dict and type(properties.get(token)) is dict:
                    child_schema = properties[token]
                elif type(candidate.get("additionalProperties")) is dict:
                    child_schema = candidate["additionalProperties"]
                if type(child_schema) is dict:
                    next_candidates.extend(
                        _schema_branches(
                            cast(dict[str, object], child_schema),
                            root,
                            current_value,
                            allow_integer_number_overlap=True,
                        )
                    )
        elif type(current_value) is list:
            if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
                raise ValueError("invalid JSON array pointer")
            index = int(token)
            if index >= len(current_value):
                raise ValueError("exact-float path does not exist")
            current_value = current_value[index]
            for candidate in candidates:
                prefix_items = candidate.get("prefixItems")
                item_schema: object = (
                    prefix_items[index]
                    if type(prefix_items) is list and index < len(prefix_items)
                    else candidate.get("items")
                )
                if type(item_schema) is dict:
                    next_candidates.extend(
                        _schema_branches(
                            cast(dict[str, object], item_schema),
                            root,
                            current_value,
                            allow_integer_number_overlap=True,
                        )
                    )
        else:
            raise ValueError("exact-float path traverses a scalar")
        if not next_candidates:
            raise ValueError("exact-float path is not registered by the schema")
        candidates = tuple(next_candidates)
    return candidates


def _restore_exact_float(
    payload: dict[str, object],
    schema: dict[str, object],
    root: dict[str, object],
    pointer: str,
) -> None:
    tokens = _decode_pointer(pointer)[1:]
    candidates = _schemas_at_tokens(schema, root, payload, tokens)
    if not any(
        candidate.get("x-automarkov-number-kind") == "exact-float"
        for candidate in candidates
    ):
        raise ValueError("exact-float path is not eligible in the registered schema")
    parent: JsonValue = cast(JsonValue, payload)
    for token in tokens[:-1]:
        if type(parent) is list:
            parent = parent[int(token)]
        elif type(parent) is dict:
            parent = parent[token]
        else:
            raise ValueError("exact-float path traverses a scalar")
    leaf = tokens[-1]
    if type(parent) is list:
        value = parent[int(leaf)]
    elif type(parent) is dict:
        value = parent[leaf]
    else:
        raise ValueError("exact-float path traverses a scalar")
    if type(value) not in {int, float}:
        raise ValueError("exact-float path must point to a JSON number")
    restored = _require_exact_float(float(cast(int | float, value)))
    if type(parent) is list:
        parent[int(leaf)] = restored
    elif type(parent) is dict:
        parent[leaf] = restored
    else:  # pragma: no cover - 上文已完成 parent 类型收窄
        raise ValueError("exact-float path traverses a scalar")


_ModelT = TypeVar("_ModelT", bound=BaseModel)


class CanonicalPayloadCodec(Generic[_ModelT]):
    """带认证 exact-float 类型映射的严格 payload codec。"""

    __slots__ = ("_adapter", "_model_type", "_schema_bytes", "_sealed")

    def __init__(self, model_type: type[_ModelT]) -> None:
        adapter = TypeAdapter(model_type)
        schema = cast(dict[str, object], adapter.json_schema())
        object.__setattr__(self, "_adapter", adapter)
        object.__setattr__(self, "_model_type", model_type)
        object.__setattr__(self, "_schema_bytes", canonical_json_bytes(schema))
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("CanonicalPayloadCodec is immutable")
        object.__setattr__(self, name, value)

    @property
    def model_type(self) -> type[_ModelT]:
        return self._model_type

    @property
    def schema_bytes(self) -> bytes:
        return self._schema_bytes

    @property
    def schema_id(self) -> str:
        return f"sha256:{sha256(self._schema_bytes).hexdigest()}"

    @property
    def schema(self) -> dict[str, object]:
        return self._schema_snapshot()

    def _schema_snapshot(self) -> dict[str, object]:
        schema = parse_json_payload(self._schema_bytes)
        if (
            type(schema) is not dict
            or canonical_json_bytes(schema) != self._schema_bytes
        ):
            raise ValueError("registered schema snapshot failed integrity validation")
        return cast(dict[str, object], schema)

    def _dump_payload(self, model: _ModelT) -> dict[str, object]:
        payload = self._adapter.dump_python(
            model,
            mode="json",
            round_trip=True,
            by_alias=True,
            warnings="error",
        )
        if type(payload) is not dict:
            raise ValueError("artifact payload models must serialize to JSON objects")
        validate_and_measure_raw_json_tree(payload)
        return cast(dict[str, object], payload)

    def _float_paths(self, payload: dict[str, object]) -> list[str]:
        schema = self._schema_snapshot()
        empty_path_document = {
            "schema_id": self.schema_id,
            "exact_float_paths": [],
            "payload": payload,
        }
        try:
            base_size = _validate_and_measure_json_tree(
                empty_path_document,
                MAX_CANONICAL_DOCUMENT_BYTES,
                maximum_depth=MAX_CANONICAL_DOCUMENT_NESTING_DEPTH,
                maximum_nodes=MAX_CANONICAL_DOCUMENT_NODES,
                canonical_numbers=True,
            )
        except ValueError as error:
            if "byte limit" in str(error):
                raise ValueError("canonical document exceeds byte limit") from error
            raise
        paths = _ExactFloatPathCollector(MAX_CANONICAL_DOCUMENT_BYTES - base_size + 2)
        _collect_exact_float_paths(payload, schema, schema, "/payload", paths)
        return paths.sorted_paths()

    def encode(self, value: object) -> bytes:
        if type(value) is not dict:
            raise ValueError("typed payload ingress requires a raw JSON object tree")
        validate_and_measure_raw_json_tree(value)
        model = cast(_ModelT, self._adapter.validate_python(value, strict=True))
        _require_deeply_immutable_model(model)
        payload = self._dump_payload(model)
        document = {
            "schema_id": self.schema_id,
            "exact_float_paths": self._float_paths(payload),
            "payload": payload,
        }
        encoded = _canonical_document_bytes(document)
        self.decode(encoded)
        return encoded

    def decode(self, canonical_bytes: bytes) -> _ModelT:
        schema = self._schema_snapshot()
        document = parse_canonical_document(canonical_bytes)
        if _canonical_document_bytes(document) != canonical_bytes:
            raise ValueError("payload document is not canonical RFC 8785 bytes")
        if type(document) is not dict or set(document) != {
            "schema_id",
            "exact_float_paths",
            "payload",
        }:
            raise ValueError("canonical payload document has an invalid keyset")
        if document["schema_id"] != self.schema_id:
            raise ValueError("canonical payload schema ID mismatch")
        paths = document["exact_float_paths"]
        payload = document["payload"]
        if type(paths) is not list or any(type(path) is not str for path in paths):
            raise ValueError("canonical payload exact-float map is invalid")
        string_paths = cast(list[str], paths)
        if (
            len(set(string_paths)) != len(string_paths)
            or string_paths
            != sorted(string_paths, key=lambda item: item.encode("utf-8"))
            or type(payload) is not dict
        ):
            raise ValueError("canonical payload exact-float map is invalid")
        restored = cast(dict[str, object], _clone_json(payload))
        for pointer in string_paths:
            _restore_exact_float(restored, schema, schema, pointer)
        model = cast(_ModelT, self._adapter.validate_python(restored, strict=True))
        _require_deeply_immutable_model(model)
        normalized = self._dump_payload(model)
        if self._float_paths(normalized) != string_paths:
            raise ValueError("canonical payload exact-float map is incomplete")
        rebuilt = _canonical_document_bytes(
            {
                "schema_id": self.schema_id,
                "exact_float_paths": string_paths,
                "payload": normalized,
            }
        )
        if rebuilt != canonical_bytes:
            raise ValueError("canonical payload failed byte-identical round-trip")
        return model


def _clone_json(value: object) -> object:
    if type(value) is list:
        return [_clone_json(item) for item in value]
    if type(value) is dict:
        return {
            key: _clone_json(item)
            for key, item in cast(dict[str, object], value).items()
        }
    return value


def _require_deeply_immutable_model(value: BaseModel) -> None:
    """拒绝 validator 在 strict validation 后重新引入的可变对象。"""

    allowed_private_fields = {
        "_validation_provenance",
        "_validation_canonical_bytes",
        "_validation_python_shape",
    }
    active: set[int] = set()
    verified: set[int] = set()
    pending: list[tuple[object, bool]] = [(value, False)]
    while pending:
        current, leaving = pending.pop()
        if type(current) in {bool, float, int, str} or current is None:
            continue
        identity = id(current)
        if leaving:
            active.remove(identity)
            verified.add(identity)
            continue
        if identity in active:
            raise ValueError("registered artifact model is not deeply immutable")
        if identity in verified:
            continue

        children: tuple[object, ...]
        if isinstance(current, BaseModel):
            fields = type(current).model_fields
            if set(current.__dict__) != set(fields):
                raise ValueError("registered artifact model is not deeply immutable")
            private = current.__pydantic_private__ or {}
            if set(private) - allowed_private_fields or any(
                private.get(name) is not None for name in allowed_private_fields
            ):
                raise ValueError("registered artifact model is not deeply immutable")
            children = tuple(current.__dict__.values())
        elif type(current) is tuple:
            children = current
        elif type(current) is MappingProxyType:
            mapping = cast(Mapping[object, object], current)
            if any(
                type(key) is not str or _contains_lone_surrogate(cast(str, key))
                for key in mapping
            ):
                raise ValueError("registered artifact model is not deeply immutable")
            children = tuple(mapping.values())
        else:
            raise ValueError("registered artifact model is not deeply immutable")

        active.add(identity)
        pending.append((current, True))
        pending.extend((child, False) for child in reversed(children))


def require_registered_model_number_contract(model_type: type[BaseModel]) -> None:
    """验证 registered model 的数字与嵌套容器均受可信 wrapper 保护。"""

    core: object = TypeAdapter(model_type).core_schema
    definitions: dict[str, object] = {}
    raw_definitions: object = None
    if type(core) is dict:
        raw_definitions = cast(dict[str, object], core).get("definitions")
    if type(raw_definitions) is list:
        for definition in raw_definitions:
            if type(definition) is dict and type(definition.get("ref")) is str:
                definitions[cast(str, definition["ref"])] = definition

    pending: list[tuple[object, bool, int]] = [(core, False, 0)]
    visited: set[tuple[int, bool, int]] = set()
    unwrapped_structure = False
    non_json_native_types = {
        "arguments",
        "bytes",
        "call",
        "callable",
        "complex",
        "date",
        "datetime",
        "decimal",
        "generator",
        "is-instance",
        "is-subclass",
        "json",
        "json-or-python",
        "lax-or-strict",
        "multi-host-url",
        "time",
        "timedelta",
        "url",
        "uuid",
    }
    while pending:
        current, normalized_context, immutable_scope = pending.pop()
        if type(current) is list:
            pending.extend(
                (item, normalized_context, immutable_scope) for item in current
            )
            continue
        if type(current) is not dict:
            continue
        node = cast(dict[str, object], current)
        visit_key = (
            id(node),
            normalized_context,
            immutable_scope,
        )
        if visit_key in visited:
            continue
        visited.add(visit_key)

        raw_node_type = node.get("type")
        node_type = raw_node_type if type(raw_node_type) is str else None
        serialization = node.get("serialization")
        if serialization is not None:
            validator_spec = node.get("function")
            validator = (
                validator_spec.get("function") if type(validator_spec) is dict else None
            )
            serializer = (
                serialization.get("function") if type(serialization) is dict else None
            )
            return_schema = (
                serialization.get("return_schema")
                if type(serialization) is dict
                else None
            )
            serialization_shape_is_closed = (
                type(serialization) is dict
                and set(serialization)
                == {"type", "function", "info_arg", "return_schema", "when_used"}
                and serialization.get("type") == "function-plain"
                and serialization.get("info_arg") is False
                and serialization.get("when_used") == "json"
            )
            canonical_json_serializer = (
                node_type == "function-after"
                and validator is _freeze_json
                and serialization_shape_is_closed
                and serializer is _thaw_json
                and type(return_schema) is dict
                and return_schema == {"type": "any"}
            )
            frozen_mapping_serializer = (
                node_type == "function-after"
                and validator is _freeze_string_mapping
                and serialization_shape_is_closed
                and serializer is _thaw_string_mapping
                and type(return_schema) is dict
                and return_schema.get("type") == "dict"
                and return_schema.get("keys_schema") == {"type": "str"}
                and return_schema.get("values_schema") == {"type": "any"}
                and set(return_schema) == {"type", "keys_schema", "values_schema"}
            )
            if not (canonical_json_serializer or frozen_mapping_serializer):
                raise ValueError("artifact schema contains an unapproved serializer")
        metadata = node.get("metadata")
        if type(metadata) is dict and "pydantic_js_extra" in metadata:
            raise ValueError(
                "artifact schema contains an unapproved JSON schema override"
            )
        annotation_functions = (
            metadata.get("pydantic_js_annotation_functions")
            if type(metadata) is dict
            else None
        )
        if type(annotation_functions) is list and annotation_functions:
            function_spec = node.get("function")
            function = (
                function_spec.get("function") if type(function_spec) is dict else None
            )
            approved_override = len(annotation_functions) == 1 and (
                (
                    node_type == "function-before"
                    and function
                    in {
                        _require_exact_float,
                        _require_safe_int,
                        _require_exact_true,
                    }
                )
                or (node_type == "function-after" and function is _freeze_json)
            )
            schema_updates = (
                metadata.get("pydantic_js_updates") if type(metadata) is dict else None
            )
            rejected_by_specific_contract = (
                node_type == "float" and not normalized_context
            ) or (
                node_type == "function-after"
                and type(schema_updates) is dict
                and any(
                    key in schema_updates
                    for key in ("ge", "gt", "le", "lt", "multiple_of")
                )
            )
            if not approved_override and not rejected_by_specific_contract:
                raise ValueError(
                    "artifact schema contains an unapproved JSON schema override"
                )
        if node_type == "definition-ref":
            reference = node.get("schema_ref")
            target = definitions.get(cast(str, reference))
            if target is None:
                raise ValueError(
                    "artifact core schema contains an unresolved reference"
                )
            pending.append((target, normalized_context, immutable_scope))
            continue
        if node_type == "model":
            for key, value in node.items():
                if key in {"definitions", "function", "serialization"}:
                    continue
                pending.append((value, normalized_context, immutable_scope))
            continue
        if node_type == "function-before":
            function_spec = node.get("function")
            function = (
                function_spec.get("function") if type(function_spec) is dict else None
            )
            inner = node.get("schema")
            if function is _require_exact_float:
                if type(inner) is not dict or inner.get("type") != "float":
                    raise ValueError(
                        "StrictCanonicalFloat must wrap exactly one float scalar"
                    )
                pending.append((inner, True, immutable_scope))
                continue
            if function is _require_safe_int:
                if type(inner) is not dict or inner.get("type") != "int":
                    raise ValueError(
                        "SafeCanonicalInt must wrap exactly one int scalar"
                    )
                continue
            if function is _canonical_json_input:
                pending.append((inner, True, 2))
                continue
            if function is _freeze_sequence:
                if type(inner) is not dict or inner.get("type") != "tuple":
                    raise ValueError(
                        "FrozenSequence must wrap exactly one tuple schema"
                    )
                pending.append((inner, normalized_context, 1))
                continue
            if function is _thaw_frozen_string_mapping_input:
                if type(inner) is not dict or inner.get("type") != "dict":
                    raise ValueError(
                        "FrozenStringMapping must wrap exactly one string-keyed mapping"
                    )
                pending.append((inner, normalized_context, 1))
                continue
            if function is _require_exact_true:
                if (
                    type(inner) is not dict
                    or inner.get("type") != "literal"
                    or not _is_exact_true_literal(inner.get("expected"))
                ):
                    raise ValueError("StrictTrue must wrap exactly Literal[True]")
                continue
            raise ValueError("artifact schema contains an unapproved before validator")
        if node_type == "function-after":
            function_spec = node.get("function")
            function = (
                function_spec.get("function") if type(function_spec) is dict else None
            )
            if function is _freeze_string_mapping:
                pending.append((node.get("schema"), normalized_context, 1))
                continue
            metadata = node.get("metadata")
            updates = (
                metadata.get("pydantic_js_updates") if type(metadata) is dict else None
            )
            if type(updates) is dict and any(
                key in updates for key in ("ge", "gt", "le", "lt", "multiple_of")
            ):
                raise ValueError(
                    "numeric constraints must use an explicit registered exact wire alias"
                )
        if node_type in {"function-plain", "function-wrap"}:
            validator_kind = cast(str, node_type).removeprefix("function-")
            raise ValueError(
                f"artifact schema contains an unapproved {validator_kind} validator"
            )
        if node_type == "float" and not normalized_context:
            raise ValueError("artifact float fields must use StrictCanonicalFloat")
        if node_type == "int" and not normalized_context:
            lower = node.get("ge")
            upper = node.get("le")
            if (
                type(lower) is not int
                or type(upper) is not int
                or lower < -MAX_SAFE_INTEGER
                or upper > MAX_SAFE_INTEGER
            ):
                raise ValueError(
                    "artifact int fields must declare exact safe integer bounds"
                )
        if node_type == "literal" and _is_exact_true_literal(node.get("expected")):
            raise ValueError("true-only artifact fields must use StrictTrue")
        if node_type == "literal":
            expected = node.get("expected")
            if type(expected) is list and any(
                type(item) in {bool, int, float} for item in expected
            ):
                raise ValueError(
                    "numeric and boolean literals require an exact-type wire wrapper"
                )
        if node_type == "tuple" and immutable_scope == 0:
            raise ValueError("artifact repeated fields must use FrozenSequence")
        if node_type == "enum":
            raise ValueError("artifact enums require an explicit wire string adapter")
        if node_type in non_json_native_types:
            raise ValueError(
                "non-JSON artifact scalars require an explicit reversible wire adapter"
            )
        if (
            node_type
            in {
                "any",
                "dataclass",
                "dict",
                "frozenset",
                "list",
                "set",
                "typed-dict",
            }
            and immutable_scope == 0
        ):
            unwrapped_structure = True
        child_immutable_scope = (
            2
            if immutable_scope == 2
            else 0
            if node_type
            in {
                "any",
                "dataclass",
                "dict",
                "frozenset",
                "list",
                "set",
                "tuple",
                "typed-dict",
            }
            else immutable_scope
        )
        for key, value in node.items():
            if key not in {"definitions", "function", "serialization"}:
                pending.append((value, normalized_context, child_immutable_scope))
    if unwrapped_structure:
        raise ValueError(
            "artifact structured fields require an approved immutable wrapper"
        )
