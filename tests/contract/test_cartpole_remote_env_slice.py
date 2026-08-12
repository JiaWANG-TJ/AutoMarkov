from __future__ import annotations

import base64
from collections.abc import Mapping
from datetime import UTC, datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from automarkov.public import RemoteEnv
from automarkov.remote_env import (
    CartPoleRemoteEnvWorker,
    RemoteEnvGrantVerifier,
    SingleAgentRemoteEnvWorker,
    formal_remote_env_readiness,
    sign_remote_env_grant,
)
from automarkov.remote_env_codec import (
    decode_remote_env_frame,
    encode_remote_env_frame,
    remote_env_transition_hash,
)
from automarkov.remote_env_contracts import (
    RemoteEnvCertificateIdentity,
    RemoteEnvEnvelope,
    RemoteEnvFrameHeader,
)

_DIGEST = "sha256:" + "1" * 64
_SESSION = "sha256:" + "2" * 64
_NONCE = base64.urlsafe_b64encode(bytes(32)).decode("ascii").rstrip("=")


class _DeterministicCartPoleBackend:
    def __init__(self) -> None:
        self.state = (0.0, 0.0, 0.0, 0.0)
        self.closed_count = 0

    def reset(
        self, *, seed: int, options: Mapping[str, object] | None
    ) -> tuple[tuple[float, float, float, float], Mapping[str, object]]:
        del options
        offset = float(seed % 10) / 100.0
        self.state = (offset, 0.0, -offset, 0.0)
        return self.state, {"seed": seed}

    def step(
        self, action: int
    ) -> tuple[
        tuple[float, float, float, float],
        float,
        bool,
        bool,
        Mapping[str, object],
    ]:
        direction = -0.01 if action == 0 else 0.01
        self.state = (
            self.state[0] + direction,
            direction,
            self.state[2] - direction,
            -direction,
        )
        return self.state, 1.0, False, False, {}

    def close(self) -> None:
        self.closed_count += 1


def _identity(*, server: bool = False) -> RemoteEnvCertificateIdentity:
    return RemoteEnvCertificateIdentity.model_validate(
        {
            "domain": "AutoMarkov-RemoteEnv-Certificate-Identity-v1",
            "experiment_id": "experiment_cartpole",
            "run_id": "run_cartpole",
            "process_execution_id": "execution_worker"
            if server
            else "execution_client",
            "profile_id": "env-cartpole" if server else "rllib-core",
            "principal_id": "principal_worker" if server else "principal_actor",
        },
        strict=True,
    )


def _grant_fields() -> dict[str, object]:
    return {
        "schema_version": "automarkov.remote-env-capability.v1",
        "signing_domain": "AutoMarkov-RemoteEnv-Capability-v1",
        "grant_id": "grant_cartpole",
        "experiment_id": "experiment_cartpole",
        "run_id": "run_cartpole",
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
        "max_sequence": 20,
        "max_step": 10,
        "max_frame_bytes": 1_000_000,
        "max_tensor_bytes": 100_000,
        "not_before": "2026-08-12T00:00:00Z",
        "expires_at": "2026-08-13T00:00:00Z",
        "nonce_b64url": _NONCE,
        "signing_key_id": "key_runner",
    }


def _request(
    grant: object,
    *,
    method: str,
    sequence: int,
    step_id: int,
    payload: dict[str, object],
) -> bytes:
    header = RemoteEnvFrameHeader.model_validate(
        {
            "codec_version": "automarkov.remote-env-frame.v1",
            "message_kind": method,
            "envelope": {
                "protocol_version": "automarkov.remote-env.v1",
                "run_id": "run_cartpole",
                "session_id": _SESSION,
                "source_process_execution_id": "execution_client",
                "source_profile_id": "rllib-core",
                "source_principal_id": "principal_actor",
                "target_process_execution_id": "execution_worker",
                "target_profile_id": "env-cartpole",
                "sequence": sequence,
                "step_id": step_id,
                "grant": grant,
            },
            "payload": payload,
            "tensors": [],
        },
        strict=True,
    )
    return encode_remote_env_frame(header, {})


def test_cartpole_describe_reset_step_close_tracer_is_seed_reproducible() -> None:
    signing_key = Ed25519PrivateKey.generate()
    grant = sign_remote_env_grant(_grant_fields(), signing_key)
    verifier = RemoteEnvGrantVerifier(
        signing_key_id="key_runner",
        signing_public_key=signing_key.public_key(),
        session_id=_SESSION,
        source_identity=_identity(),
        target_identity=_identity(server=True),
        environment_id="CartPole-v1",
        profile_graph_hash=_DIGEST,
        expected_role="actor",
    )
    template = RemoteEnvEnvelope.model_validate(
        {
            "protocol_version": "automarkov.remote-env.v1",
            "run_id": "run_cartpole",
            "session_id": _SESSION,
            "source_process_execution_id": "execution_client",
            "source_profile_id": "rllib-core",
            "source_principal_id": "principal_actor",
            "target_process_execution_id": "execution_worker",
            "target_profile_id": "env-cartpole",
            "sequence": 1,
            "step_id": 0,
            "grant": grant,
        },
        strict=True,
    )
    backend = _DeterministicCartPoleBackend()
    worker = CartPoleRemoteEnvWorker(
        backend=backend,
        verifier=verifier,
        response_envelope=template,
        repository_commit="4f74260de0413812cab0680921163083a948a4f2",
        now_provider=lambda: now,
    )
    assert CartPoleRemoteEnvWorker is SingleAgentRemoteEnvWorker
    assert isinstance(worker, RemoteEnv)
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)

    describe = worker.exchange(
        _request(grant, method="Describe", sequence=1, step_id=0, payload={}),
    )
    described = decode_remote_env_frame(
        describe, max_frame_bytes=1_000_000, max_tensor_bytes=100_000
    )
    assert described.header.payload.environment_spec == "CartPole-v1"  # type: ignore[union-attr]
    assert described.header.payload.supports_state is False  # type: ignore[union-attr]
    assert described.tensors["tensor_cartpole_high"] == bytes.fromhex(
        "9a9999400000807f5077d63e0000807f"
    )
    assert described.tensors["tensor_cartpole_low"] == bytes.fromhex(
        "9a9999c0000080ff5077d6be000080ff"
    )

    worker.exchange(
        _request(
            grant,
            method="Reset",
            sequence=2,
            step_id=0,
            payload={"seed": 7, "options": {}},
        ),
    )
    first = worker.exchange(
        _request(
            grant,
            method="Step",
            sequence=3,
            step_id=1,
            payload={"action": 1},
        ),
    )
    worker.exchange(
        _request(
            grant,
            method="Reset",
            sequence=4,
            step_id=0,
            payload={"seed": 7, "options": {}},
        ),
    )
    second = worker.exchange(
        _request(
            grant,
            method="Step",
            sequence=5,
            step_id=1,
            payload={"action": 1},
        ),
    )

    first_frame = decode_remote_env_frame(
        first, max_frame_bytes=1_000_000, max_tensor_bytes=100_000
    )
    second_frame = decode_remote_env_frame(
        second, max_frame_bytes=1_000_000, max_tensor_bytes=100_000
    )
    assert first_frame.tensors == second_frame.tensors
    assert first_frame.header.payload.reward == 1  # type: ignore[union-attr]
    assert first_frame.header.payload.termination is False  # type: ignore[union-attr]
    assert first_frame.header.payload.truncation is False  # type: ignore[union-attr]
    assert (
        first_frame.header.payload.transition_hash.root  # type: ignore[union-attr]
        == remote_env_transition_hash(
            _request(
                grant,
                method="Step",
                sequence=3,
                step_id=1,
                payload={"action": 1},
            ),
            first,
        )
    )

    close_one = worker.exchange(
        _request(grant, method="Close", sequence=6, step_id=1, payload={})
    )
    close_two = worker.exchange(
        _request(grant, method="Close", sequence=7, step_id=1, payload={})
    )
    assert close_one == close_two
    assert backend.closed_count == 1


def test_recipe_frozen_profile_remains_formally_waiting() -> None:
    status, reason = formal_remote_env_readiness("recipe_frozen")

    assert status == "WAITING_RUNTIME"
    assert "built" in reason
