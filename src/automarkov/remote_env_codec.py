from __future__ import annotations

import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import cast

from automarkov.canonical import (
    MAX_JSON_PAYLOAD_BYTES,
    canonical_json_bytes,
    parse_json_payload,
)
from automarkov.domain import Sha256Digest
from automarkov.remote_env_contracts import (
    BoxSpace,
    DescribeResultPayload,
    DictSpace,
    MultiAgentStepResultPayload,
    MultiDiscreteSpace,
    ObservationPayload,
    RemoteEnvFrameHeader,
    Space,
    StepResultPayload,
    TensorDescriptor,
    TensorDtype,
    TupleSpace,
)

_FRAME_DOMAIN = b"AutoMarkov-RemoteEnv-Frame-v1\n"
_TRANSITION_DOMAIN = b"AutoMarkov-RemoteEnv-Transition-v1\n"
_PREFIX = struct.Struct(">Q")
_DTYPE_LAYOUT: dict[str, tuple[int, str | None]] = {
    "bool": (1, None),
    "uint8": (1, None),
    "int8": (1, None),
    "uint16": (2, None),
    "int16": (2, None),
    "uint32": (4, None),
    "int32": (4, None),
    "uint64": (8, None),
    "int64": (8, None),
    "float16": (2, "e"),
    "float32": (4, "f"),
    "float64": (8, "d"),
}


@dataclass(frozen=True, slots=True)
class DecodedRemoteEnvFrame:
    header: RemoteEnvFrameHeader
    tensors: Mapping[str, bytes]
    canonical_frame_bytes: bytes
    frame_hash: str


def _digest(data: bytes) -> str:
    return f"sha256:{sha256(data).hexdigest()}"


def _element_count(shape: tuple[int, ...], *, ceiling: int) -> int:
    count = 1
    for dimension in shape:
        if type(dimension) is not int or dimension < 0:
            raise ValueError(
                "tensor shape dimensions must be nonnegative safe integers"
            )
        if dimension and count > ceiling // dimension:
            raise ValueError("tensor shape exceeds byte ceiling")
        count *= dimension
    return count


def canonicalize_tensor_bytes(
    dtype: TensorDtype,
    data: bytes,
    *,
    allow_infinity: bool = False,
) -> bytes:
    """验证 canonical little-endian tensor，并把浮点负零规范化为正零。"""

    if type(data) is not bytes:
        raise ValueError("tensor storage must be exact bytes")
    width, float_format = _DTYPE_LAYOUT[dtype]
    if len(data) % width:
        raise ValueError("tensor byte length is not aligned to dtype width")
    if dtype == "bool":
        if any(item not in {0, 1} for item in data):
            raise ValueError("bool tensor elements must be encoded as 0 or 1")
        return data
    if float_format is None:
        return data
    output = bytearray(data)
    codec = struct.Struct(f"<{float_format}")
    for offset in range(0, len(data), width):
        value = cast(float, codec.unpack_from(data, offset)[0])
        if math.isnan(value):
            raise ValueError("tensor NaN values are forbidden")
        if math.isinf(value) and not allow_infinity:
            raise ValueError("infinity is only allowed in Box bounds tensors")
        if value == 0.0:
            codec.pack_into(output, offset, 0.0)
    return bytes(output)


def make_tensor_descriptor(
    *,
    tensor_id: str,
    dtype: TensorDtype,
    shape: tuple[int, ...],
    offset: int,
    data: bytes,
    allow_infinity: bool = False,
) -> tuple[TensorDescriptor, bytes]:
    canonical = canonicalize_tensor_bytes(dtype, data, allow_infinity=allow_infinity)
    width = _DTYPE_LAYOUT[dtype][0]
    count = _element_count(shape, ceiling=max(len(canonical), 1))
    if count * width != len(canonical):
        raise ValueError("tensor shape, dtype, and byte length disagree")
    return (
        TensorDescriptor(
            tensor_id=tensor_id,
            dtype=dtype,
            shape=shape,
            offset=offset,
            nbytes=len(canonical),
            sha256=Sha256Digest(root=_digest(canonical)),
        ),
        canonical,
    )


def _space_tensor_ids(space: Space, *, box_only: bool = False) -> set[str]:
    output: set[str] = set()
    stack: list[Space] = [space]
    while stack:
        current = stack.pop()
        if type(current) is BoxSpace:
            output.update((current.low_tensor_id, current.high_tensor_id))
        elif type(current) is MultiDiscreteSpace and not box_only:
            output.update((current.nvec_tensor_id, current.start_tensor_id))
        elif type(current) is TupleSpace:
            stack.extend(current.items)
        elif type(current) is DictSpace:
            stack.extend(entry.space for entry in current.entries)
    return output


def _typed_tensor_ids(header: RemoteEnvFrameHeader) -> tuple[set[str], set[str]]:
    payload = header.payload
    referenced: set[str] = set()
    bound_ids: set[str] = set()
    if type(payload) is DescribeResultPayload:
        for space in (payload.observation_space, payload.action_space):
            referenced.update(_space_tensor_ids(space))
            bound_ids.update(_space_tensor_ids(space, box_only=True))
    elif isinstance(payload, (ObservationPayload, StepResultPayload)):
        referenced.add(payload.observation_tensor_id)
    elif type(payload) is MultiAgentStepResultPayload:
        referenced.update(payload.observations.values())
    return referenced, bound_ids


def _validate_tensor_set(
    header: RemoteEnvFrameHeader,
    tensors: Mapping[str, bytes],
) -> tuple[bytes, ...]:
    descriptors = {item.tensor_id: item for item in header.tensors}
    if set(tensors) != set(descriptors):
        raise ValueError("tensor storage must exactly match frame descriptors")
    referenced, bound_ids = _typed_tensor_ids(header)
    if referenced != set(descriptors):
        raise ValueError(
            "typed tensor references must exactly match this frame's descriptors"
        )
    sections: list[bytes] = []
    total = 0
    for descriptor in header.tensors:
        raw = tensors[descriptor.tensor_id]
        if type(raw) is not bytes:
            raise ValueError("tensor storage must be exact bytes")
        width = _DTYPE_LAYOUT[descriptor.dtype][0]
        count = _element_count(
            tuple(descriptor.shape), ceiling=header.envelope.grant.max_tensor_bytes
        )
        expected_bytes = count * width
        if expected_bytes != descriptor.nbytes or expected_bytes != len(raw):
            raise ValueError("tensor shape, dtype, descriptor, and storage disagree")
        if descriptor.nbytes > header.envelope.grant.max_tensor_bytes:
            raise ValueError("tensor exceeds grant byte ceiling")
        canonical = canonicalize_tensor_bytes(
            descriptor.dtype,
            raw,
            allow_infinity=descriptor.tensor_id in bound_ids,
        )
        if canonical != raw:
            raise ValueError("tensor storage contains a non-canonical negative zero")
        if descriptor.sha256.root != _digest(raw):
            raise ValueError("tensor digest mismatch")
        if descriptor.offset != total:
            raise ValueError("tensor offsets must be gapless")
        total += len(raw)
        sections.append(raw)
    return tuple(sections)


def encode_remote_env_frame(
    header: RemoteEnvFrameHeader,
    tensors: Mapping[str, bytes],
) -> bytes:
    if type(header) is not RemoteEnvFrameHeader:
        raise ValueError("frame header must use the exact closed contract")
    if type(tensors) is not dict or any(type(key) is not str for key in tensors):
        raise ValueError("tensor storage must be an exact string-keyed dict")
    sections = _validate_tensor_set(header, tensors)
    header_bytes = canonical_json_bytes(
        header.model_dump(mode="json", round_trip=True, warnings="error")
    )
    if len(header_bytes) > MAX_JSON_PAYLOAD_BYTES:
        raise ValueError("frame header exceeds canonical JSON ceiling")
    frame = _PREFIX.pack(len(header_bytes)) + header_bytes + b"".join(sections)
    if len(frame) > header.envelope.grant.max_frame_bytes:
        raise ValueError("frame exceeds grant byte ceiling")
    return frame


def decode_remote_env_frame(
    frame: bytes,
    *,
    max_frame_bytes: int,
    max_tensor_bytes: int,
) -> DecodedRemoteEnvFrame:
    if type(frame) is not bytes:
        raise ValueError("remote environment frame must be exact bytes")
    if (
        type(max_frame_bytes) is not int
        or type(max_tensor_bytes) is not int
        or max_frame_bytes < 1
        or max_tensor_bytes < 1
    ):
        raise ValueError("transport byte ceilings must be exact positive integers")
    if max_frame_bytes < _PREFIX.size or len(frame) > max_frame_bytes:
        raise ValueError("frame exceeds transport byte ceiling")
    if len(frame) < _PREFIX.size:
        raise ValueError("truncated frame length prefix")
    header_length = _PREFIX.unpack_from(frame)[0]
    if header_length > MAX_JSON_PAYLOAD_BYTES:
        raise ValueError("frame header exceeds canonical JSON ceiling")
    header_end = _PREFIX.size + header_length
    if header_end > len(frame):
        raise ValueError("truncated frame header")
    header_bytes = frame[_PREFIX.size : header_end]
    raw_header = parse_json_payload(header_bytes)
    if type(raw_header) is not dict or canonical_json_bytes(raw_header) != header_bytes:
        raise ValueError("frame header must use its unique JCS representation")
    header = RemoteEnvFrameHeader.model_validate(raw_header, strict=True)
    if header.envelope.grant.max_frame_bytes > max_frame_bytes:
        raise ValueError("grant frame ceiling exceeds transport policy")
    if header.envelope.grant.max_tensor_bytes > max_tensor_bytes:
        raise ValueError("grant tensor ceiling exceeds transport policy")
    if len(frame) > header.envelope.grant.max_frame_bytes:
        raise ValueError("frame exceeds its signed grant byte ceiling")
    tensor_section = frame[header_end:]
    tensors: dict[str, bytes] = {}
    cursor = 0
    for descriptor in header.tensors:
        end = cursor + descriptor.nbytes
        if descriptor.nbytes > max_tensor_bytes or end > len(tensor_section):
            raise ValueError("truncated or oversized tensor section")
        tensors[descriptor.tensor_id] = tensor_section[cursor:end]
        cursor = end
    if cursor != len(tensor_section):
        raise ValueError("frame contains trailing tensor bytes")
    _validate_tensor_set(header, tensors)
    return DecodedRemoteEnvFrame(
        header=header,
        tensors=tensors,
        canonical_frame_bytes=frame,
        frame_hash=remote_env_frame_hash(frame),
    )


def remote_env_frame_hash(canonical_frame_bytes: bytes) -> str:
    return _digest(_FRAME_DOMAIN + canonical_frame_bytes)


def remote_env_transition_hash(
    canonical_request_frame_bytes: bytes,
    canonical_response_frame_bytes: bytes,
) -> str:
    decoded = decode_remote_env_frame(
        canonical_response_frame_bytes,
        max_frame_bytes=(1 << 53) - 1,
        max_tensor_bytes=(1 << 53) - 1,
    )
    if not isinstance(
        decoded.header.payload,
        (StepResultPayload, MultiAgentStepResultPayload),
    ):
        raise TypeError("transition hash requires a Step response frame")
    header_payload = decoded.header.model_dump(
        mode="json", round_trip=True, warnings="error"
    )
    payload = cast(dict[str, object], header_payload["payload"])
    payload["transition_hash"] = "sha256:" + "0" * 64
    normalized_header = RemoteEnvFrameHeader.model_validate(header_payload, strict=True)
    normalized_response = encode_remote_env_frame(
        normalized_header, dict(decoded.tensors)
    )
    return _digest(
        _TRANSITION_DOMAIN + canonical_request_frame_bytes + normalized_response
    )
