from __future__ import annotations

import base64
import struct

import pytest
from pydantic import ValidationError

from automarkov.remote_env_codec import (
    canonicalize_tensor_bytes,
    decode_remote_env_frame,
    encode_remote_env_frame,
    make_tensor_descriptor,
    remote_env_frame_hash,
    remote_env_transition_hash,
)
from automarkov.remote_env_contracts import SPACE_ADAPTER, RemoteEnvFrameHeader

_DIGEST = "sha256:" + "1" * 64
_SESSION = "sha256:" + "2" * 64
_SIGNATURE = base64.urlsafe_b64encode(bytes(64)).decode("ascii").rstrip("=")
_NONCE = base64.urlsafe_b64encode(bytes(32)).decode("ascii").rstrip("=")


def _grant() -> dict[str, object]:
    return {
        "schema_version": "automarkov.remote-env-capability.v1",
        "signing_domain": "AutoMarkov-RemoteEnv-Capability-v1",
        "grant_id": "grant_codec",
        "experiment_id": "experiment_codec",
        "run_id": "run_codec",
        "session_id": _SESSION,
        "profile_graph_hash": _DIGEST,
        "source_process_execution_id": "execution_client",
        "source_profile_id": "rllib-core",
        "source_principal_id": "principal_actor",
        "target_process_execution_id": "execution_worker",
        "target_profile_id": "env-cartpole",
        "environment_id": "CartPole-v1",
        "role": "actor",
        "allowed_methods": ["Close", "Describe", "Reset", "Step"],
        "max_sequence": 100,
        "max_step": 100,
        "max_frame_bytes": 1_000_000,
        "max_tensor_bytes": 100_000,
        "not_before": "2026-08-12T00:00:00Z",
        "expires_at": "2026-08-13T00:00:00Z",
        "nonce_b64url": _NONCE,
        "signing_key_id": "key_runner",
        "signature_b64url": _SIGNATURE,
    }


def _header(
    *,
    payload: dict[str, object],
    tensors: list[dict[str, object]],
    message_kind: str = "Describe",
) -> RemoteEnvFrameHeader:
    return RemoteEnvFrameHeader.model_validate(
        {
            "codec_version": "automarkov.remote-env-frame.v1",
            "message_kind": message_kind,
            "envelope": {
                "protocol_version": "automarkov.remote-env.v1",
                "run_id": "run_codec",
                "session_id": _SESSION,
                "source_process_execution_id": "execution_client",
                "source_profile_id": "rllib-core",
                "source_principal_id": "principal_actor",
                "target_process_execution_id": "execution_worker",
                "target_profile_id": "env-cartpole",
                "sequence": 1,
                "step_id": 0,
                "grant": _grant(),
            },
            "payload": payload,
            "tensors": tensors,
        },
        strict=True,
    )


@pytest.mark.parametrize(
    "space",
    [
        {"kind": "Discrete", "n": 2, "start": 0, "dtype": "int64"},
        {
            "kind": "Box",
            "shape": [4],
            "dtype": "float32",
            "low_tensor_id": "tensor_low",
            "high_tensor_id": "tensor_high",
        },
        {
            "kind": "MultiDiscrete",
            "nvec_tensor_id": "tensor_nvec",
            "start_tensor_id": "tensor_start",
            "dtype": "int64",
        },
        {"kind": "MultiBinary", "n": 8, "dtype": "int8"},
        {"kind": "Text", "min_length": 1, "max_length": 20, "charset": "abc"},
        {"kind": "FiniteText", "values": ["alpha", "beta"]},
        {
            "kind": "Tuple",
            "items": [{"kind": "Discrete", "n": 2, "start": 0, "dtype": "int8"}],
        },
        {
            "kind": "Dict",
            "entries": [
                {
                    "key": "action",
                    "space": {"kind": "Discrete", "n": 2, "start": 0, "dtype": "int8"},
                }
            ],
        },
    ],
)
def test_closed_space_union_round_trips_without_python_objects(
    space: dict[str, object],
) -> None:
    parsed = SPACE_ADAPTER.validate_python(space, strict=True)

    assert SPACE_ADAPTER.dump_python(parsed, mode="json") == space


def test_remote_env_frame_is_byte_identical_and_rejects_trailing_data() -> None:
    high, high_data = make_tensor_descriptor(
        tensor_id="tensor_high",
        dtype="float32",
        shape=(2,),
        offset=0,
        data=struct.pack("<ff", 1.0, float("inf")),
        allow_infinity=True,
    )
    low, low_data = make_tensor_descriptor(
        tensor_id="tensor_low",
        dtype="float32",
        shape=(2,),
        offset=len(high_data),
        data=struct.pack("<ff", -1.0, float("-inf")),
        allow_infinity=True,
    )
    header = _header(
        payload={
            "environment_repository_commit": "4f74260de0413812cab0680921163083a948a4f2",
            "environment_spec": "codec-test",
            "observation_space": {
                "kind": "Box",
                "shape": [2],
                "dtype": "float32",
                "low_tensor_id": "tensor_low",
                "high_tensor_id": "tensor_high",
            },
            "action_space": {"kind": "Discrete", "n": 2, "start": 0, "dtype": "int64"},
            "seed_contract": {"kind": "integer_reset", "seed": 0},
            "supports_aec": False,
            "supports_parallel": False,
            "supports_state": False,
        },
        tensors=[high.model_dump(mode="json"), low.model_dump(mode="json")],
    )

    frame = encode_remote_env_frame(
        header, {"tensor_high": high_data, "tensor_low": low_data}
    )
    decoded = decode_remote_env_frame(
        frame, max_frame_bytes=1_000_000, max_tensor_bytes=100_000
    )

    assert decoded.canonical_frame_bytes == frame
    assert decoded.frame_hash == remote_env_frame_hash(frame)
    assert decoded.tensors == {"tensor_high": high_data, "tensor_low": low_data}
    with pytest.raises(ValueError, match="trailing"):
        decode_remote_env_frame(
            frame + b"x", max_frame_bytes=1_000_000, max_tensor_bytes=100_000
        )


def test_tensor_codec_normalizes_negative_zero_and_rejects_nan() -> None:
    assert canonicalize_tensor_bytes("float32", struct.pack("<f", -0.0)) == struct.pack(
        "<f", 0.0
    )
    with pytest.raises(ValueError, match="NaN"):
        canonicalize_tensor_bytes("float32", struct.pack("<f", float("nan")))


def test_only_typed_tensor_reference_fields_claim_descriptors() -> None:
    descriptor, data = make_tensor_descriptor(
        tensor_id="tensor_observation",
        dtype="float32",
        shape=(1,),
        offset=0,
        data=struct.pack("<f", 1.0),
    )
    info_spoof = _header(
        message_kind="Reset",
        payload={
            "observation_tensor_id": "tensor_missing",
            "info": {"ordinary_string": "tensor_observation"},
        },
        tensors=[descriptor.model_dump(mode="json")],
    )

    with pytest.raises(ValueError, match="typed tensor reference"):
        encode_remote_env_frame(info_spoof, {"tensor_observation": data})

    dangling_bound = _header(
        payload={
            "environment_repository_commit": "4f74260de0413812cab0680921163083a948a4f2",
            "environment_spec": "codec-test",
            "observation_space": {
                "kind": "Box",
                "shape": [1],
                "dtype": "float32",
                "low_tensor_id": "tensor_missing",
                "high_tensor_id": "tensor_observation",
            },
            "action_space": {
                "kind": "Discrete",
                "n": 2,
                "start": 0,
                "dtype": "int64",
            },
            "seed_contract": {"kind": "integer_reset", "seed": 0},
            "supports_aec": False,
            "supports_parallel": False,
            "supports_state": False,
        },
        tensors=[descriptor.model_dump(mode="json")],
    )

    with pytest.raises(ValueError, match="typed tensor reference"):
        encode_remote_env_frame(dangling_bound, {"tensor_observation": data})


def test_actor_space_and_grant_contracts_fail_closed() -> None:
    with pytest.raises(ValidationError):
        SPACE_ADAPTER.validate_python(
            {"kind": "FiniteText", "values": ["beta", "alpha"]}, strict=True
        )


def test_multi_agent_step_binds_every_agent_and_transition() -> None:
    descriptors = []
    tensors: dict[str, bytes] = {}
    offset = 0
    for agent, value in (("agent_a", 1.0), ("agent_b", 2.0)):
        descriptor, data = make_tensor_descriptor(
            tensor_id=f"tensor_{agent}",
            dtype="float32",
            shape=(1,),
            offset=offset,
            data=struct.pack("<f", value),
        )
        descriptors.append(descriptor.model_dump(mode="json"))
        tensors[descriptor.tensor_id] = data
        offset += len(data)
    request = encode_remote_env_frame(
        _header(
            message_kind="Step",
            payload={"action": {"agent_a": 0, "agent_b": 1}},
            tensors=[],
        ),
        {},
    )
    placeholder = "sha256:" + "0" * 64
    response_header = _header(
        message_kind="Step",
        payload={
            "observations": {
                "agent_a": "tensor_agent_a",
                "agent_b": "tensor_agent_b",
            },
            "rewards": {"agent_a": 1.0, "agent_b": -1.0},
            "terminations": {"agent_a": False, "agent_b": True},
            "truncations": {"agent_a": False, "agent_b": False},
            "infos": {"agent_a": {}, "agent_b": {}},
            "active_aec_agent": "agent_b",
            "cycle_index": 4,
            "transition_hash": placeholder,
        },
        tensors=descriptors,
    )
    provisional = encode_remote_env_frame(response_header, tensors)
    transition_hash = remote_env_transition_hash(request, provisional)
    response = encode_remote_env_frame(
        RemoteEnvFrameHeader.model_validate(
            {
                **response_header.model_dump(mode="json"),
                "payload": {
                    **response_header.payload.model_dump(mode="json"),
                    "transition_hash": transition_hash,
                },
            },
            strict=True,
        ),
        tensors,
    )

    decoded = decode_remote_env_frame(
        response, max_frame_bytes=1_000_000, max_tensor_bytes=100_000
    )

    assert decoded.header.payload.observations == {  # type: ignore[union-attr]
        "agent_a": "tensor_agent_a",
        "agent_b": "tensor_agent_b",
    }
    assert remote_env_transition_hash(request, response) == transition_hash
    invalid = _grant()
    invalid["allowed_methods"] = ["State"]
    with pytest.raises(ValidationError, match="actor"):
        RemoteEnvFrameHeader.model_validate(
            {
                **_header(payload={}, tensors=[]).model_dump(mode="json"),
                "envelope": {
                    **_header(payload={}, tensors=[]).envelope.model_dump(mode="json"),
                    "grant": invalid,
                },
            },
            strict=True,
        )
