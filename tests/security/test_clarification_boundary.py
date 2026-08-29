from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import TypeAdapter, ValidationError

from automarkov.clarification import (
    ClarificationEvaluationRequest,
    ClarificationEvaluationVerdict,
    ClarificationOutcomeRecord,
    ClarificationReplayConflictError,
    EvaluatedClarificationOutcome,
    InMemoryClarificationReplayIndex,
    InvalidClarificationOutcome,
    SqliteClarificationReplayIndex,
    clarification_signature_preimage,
    validate_clarification_evaluation_binding,
    validate_clarification_outcome_binding,
    verify_clarification_signature,
)
from automarkov.domain.canonical import canonical_json_bytes

_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"\x71" * 32)
_ISSUED_AT = "2026-08-12T12:00:00Z"


@pytest.fixture(params=("memory", "sqlite"))
def replay_index(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[InMemoryClarificationReplayIndex | SqliteClarificationReplayIndex]:
    index: InMemoryClarificationReplayIndex | SqliteClarificationReplayIndex
    if request.param == "memory":
        index = InMemoryClarificationReplayIndex()
    else:
        index = SqliteClarificationReplayIndex(tmp_path / "clarification-replay.sqlite")
    try:
        yield index
    finally:
        index.close()


def _nonce(value: int) -> str:
    return base64.urlsafe_b64encode(value.to_bytes(32, "big")).decode().rstrip("=")


def _sign(payload: dict[str, object]) -> dict[str, object]:
    signed = dict(payload)
    signed["signature_b64url"] = "A" * 86
    schema_version = cast(str, payload["schema_version"])
    if schema_version == "automarkov.clarification-evaluation-request.v1":
        model_type = ClarificationEvaluationRequest
    elif schema_version == "automarkov.clarification-evaluation-verdict.v1":
        model_type = ClarificationEvaluationVerdict
    else:
        model_type = (
            EvaluatedClarificationOutcome
            if payload["outcome_kind"] == "evaluated"
            else InvalidClarificationOutcome
        )
    unverified = model_type.model_validate(signed, strict=True)
    signed["signature_b64url"] = (
        base64.urlsafe_b64encode(
            _PRIVATE_KEY.sign(clarification_signature_preimage(unverified))
        )
        .decode()
        .rstrip("=")
    )
    return signed


def _request_payload() -> dict[str, object]:
    return {
        "schema_version": "automarkov.clarification-evaluation-request.v1",
        "signing_domain": "AutoMarkov-Clarification-Evaluation-Request-v1",
        "request_id": "request_clarification_001",
        "experiment_id": "experiment_clarification",
        "run_id": "run_clarification_001",
        "cell_id": "cell_auto_v5",
        "suite_id": "suite_cartpole",
        "method_id": "automarkov",
        "generation_pair_id": "g00",
        "pair_binding_id": "pair_binding_001",
        "generation_seed": 9001,
        "variant_id": "v5_clarification_required",
        "track": "AUTO",
        "run_manifest_artifact_id": "artifact_" + "1" * 64,
        "run_manifest_payload_hash": "sha256:" + "1" * 64,
        "task_artifact_id": "artifact_" + "2" * 64,
        "task_payload_hash": "sha256:" + "2" * 64,
        "review_report_artifact_id": "artifact_" + "3" * 64,
        "review_report_payload_hash": "sha256:" + "3" * 64,
        "outcome_mask_artifact_id": "artifact_" + "4" * 64,
        "outcome_mask_payload_hash": "sha256:" + "4" * 64,
        "clarification_result_artifact_id": "artifact_" + "5" * 64,
        "clarification_result_payload_hash": "sha256:" + "5" * 64,
        "terminal_result_artifact_id": "artifact_" + "6" * 64,
        "terminal_result_payload_hash": "sha256:" + "6" * 64,
        "terminal_event_id": "019921bc-4a00-7000-8000-000000000001",
        "terminal_event_hash": "sha256:" + "7" * 64,
        "terminal_snapshot_sequence_no": 7,
        "terminal_snapshot_event_head_hash": "sha256:" + "7" * 64,
        "execution_attestation_artifact_id": "artifact_" + "7" * 64,
        "execution_attestation_payload_hash": "sha256:" + "8" * 64,
        "terminal_artifact_dag_closure_hash": "sha256:" + "9" * 64,
        "clarification_oracle_commitment": "sha256:" + "a" * 64,
        "evaluator_protocol_id": "clarification-evaluator-v1",
        "evaluator_protocol_hash": "sha256:" + "b" * 64,
        "evaluator_profile_id": "profile_clarification-evaluator",
        "evaluator_profile_hash": "sha256:" + "c" * 64,
        "evaluator_lock_hash": "sha256:" + "d" * 64,
        "evaluator_image_hash": "sha256:" + "e" * 64,
        "evaluator_schema_id": "clarification-verdict-schema-v1",
        "evaluator_schema_hash": "sha256:" + "f" * 64,
        "issued_at": _ISSUED_AT,
        "not_before": "2026-08-12T11:59:00Z",
        "expires_at": "2026-08-12T12:05:00Z",
        "nonce_b64url": _nonce(1),
        "signature_algorithm": "Ed25519",
        "coordinator_key_id": "key_clarification_coordinator",
    }


def _verdict_payload(request: ClarificationEvaluationRequest) -> dict[str, object]:
    return {
        "schema_version": "automarkov.clarification-evaluation-verdict.v1",
        "signing_domain": "AutoMarkov-Clarification-Evaluation-Verdict-v1",
        "verdict_id": "verdict_clarification_001",
        "request_id": request.request_id,
        "request_payload_hash": "sha256:" + "0" * 64,
        "experiment_id": request.experiment_id,
        "run_id": request.run_id,
        "cell_id": request.cell_id,
        "suite_id": request.suite_id,
        "variant_id": request.variant_id,
        "track": request.track,
        "method_id": request.method_id,
        "generation_pair_id": request.generation_pair_id,
        "outcome_mask_artifact_id": request.outcome_mask_artifact_id,
        "outcome_mask_payload_hash": request.outcome_mask_payload_hash,
        "clarification_result_artifact_id": request.clarification_result_artifact_id,
        "clarification_result_payload_hash": request.clarification_result_payload_hash,
        "terminal_result_artifact_id": request.terminal_result_artifact_id,
        "terminal_result_payload_hash": request.terminal_result_payload_hash,
        "execution_attestation_artifact_id": (
            request.execution_attestation_artifact_id
        ),
        "execution_attestation_payload_hash": (
            request.execution_attestation_payload_hash
        ),
        "terminal_artifact_dag_closure_hash": (
            request.terminal_artifact_dag_closure_hash
        ),
        "safe_clarification_required": False,
        "issued_at": "2026-08-12T12:01:00Z",
        "nonce_b64url": _nonce(2),
        "signature_algorithm": "Ed25519",
        "evaluator_key_id": "key_clarification_evaluator",
    }


def _outcome_common(
    request: ClarificationEvaluationRequest,
) -> dict[str, object]:
    return {
        "schema_version": "automarkov.clarification-outcome.v1",
        "signing_domain": "AutoMarkov-Clarification-Outcome-v1",
        "outcome_id": "outcome_clarification_001",
        "experiment_id": request.experiment_id,
        "run_id": request.run_id,
        "cell_id": request.cell_id,
        "suite_id": request.suite_id,
        "variant_id": request.variant_id,
        "track": request.track,
        "method_id": request.method_id,
        "generation_pair_id": request.generation_pair_id,
        "pair_binding_id": request.pair_binding_id,
        "run_manifest_artifact_id": request.run_manifest_artifact_id,
        "run_manifest_payload_hash": request.run_manifest_payload_hash,
        "outcome_mask_artifact_id": request.outcome_mask_artifact_id,
        "outcome_mask_payload_hash": request.outcome_mask_payload_hash,
        "terminal_result_artifact_id": request.terminal_result_artifact_id,
        "terminal_result_payload_hash": request.terminal_result_payload_hash,
        "issued_at": "2026-08-12T12:02:00Z",
        "nonce_b64url": _nonce(3),
        "signature_algorithm": "Ed25519",
        "analysis_key_id": "key_clarification_analysis",
    }


def test_sealed_verdict_exposes_only_one_boolean_and_false_is_evaluated() -> None:
    request = ClarificationEvaluationRequest.model_validate(
        _sign(_request_payload()), strict=True
    )
    verdict_payload = _verdict_payload(request)
    verdict_payload["request_payload_hash"] = (
        "sha256:"
        + hashlib.sha256(
            canonical_json_bytes(request.model_dump(mode="json"))
        ).hexdigest()
    )
    verdict = ClarificationEvaluationVerdict.model_validate(
        _sign(verdict_payload), strict=True
    )

    verify_clarification_signature(request, _PRIVATE_KEY.public_key())
    verify_clarification_signature(verdict, _PRIVATE_KEY.public_key())
    validate_clarification_evaluation_binding(request, verdict)

    outcome_payload = _outcome_common(request) | {
        "outcome_kind": "evaluated",
        "request_id": request.request_id,
        "request_payload_hash": verdict.request_payload_hash,
        "verdict_id": verdict.verdict_id,
        "verdict_payload_hash": "sha256:"
        + hashlib.sha256(
            canonical_json_bytes(verdict.model_dump(mode="json"))
        ).hexdigest(),
        "safe_clarification_required": False,
        "reason": None,
    }
    outcome = EvaluatedClarificationOutcome.model_validate(
        _sign(outcome_payload), strict=True
    )
    validate_clarification_outcome_binding(outcome, request, verdict)
    assert outcome.safe_clarification_required is False

    leaked = _sign(verdict_payload) | {"gap_id": "sealed_gap_001"}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ClarificationEvaluationVerdict.model_validate(leaked, strict=True)


def test_invalid_outcomes_preserve_slot_and_map_failures_to_zero() -> None:
    request = ClarificationEvaluationRequest.model_validate(
        _sign(_request_payload()), strict=True
    )
    invalid_payload = _outcome_common(request) | {
        "outcome_kind": "invalid",
        "request_id": request.request_id,
        "request_payload_hash": "sha256:" + "8" * 64,
        "verdict_id": None,
        "verdict_payload_hash": None,
        "safe_clarification_required": False,
        "reason": "evaluation_timeout",
    }
    outcome = TypeAdapter(ClarificationOutcomeRecord).validate_python(
        _sign(invalid_payload), strict=True
    )
    assert isinstance(outcome, InvalidClarificationOutcome)
    assert outcome.safe_clarification_required is False

    invalid_payload["safe_clarification_required"] = True
    with pytest.raises(
        ValidationError, match="invalid clarification outcomes map to zero"
    ):
        TypeAdapter(ClarificationOutcomeRecord).validate_python(
            _sign(invalid_payload), strict=True
        )


def test_replay_claims_are_atomic_and_exact_retry_is_idempotent(
    replay_index: InMemoryClarificationReplayIndex | SqliteClarificationReplayIndex,
) -> None:
    request_payload = _request_payload()
    first = ClarificationEvaluationRequest.model_validate(
        _sign(request_payload), strict=True
    )

    assert replay_index.record(first) is True
    assert replay_index.record(first) is False

    changed = ClarificationEvaluationRequest.model_validate(
        _sign(
            request_payload
            | {
                "generation_seed": 9002,
                "nonce_b64url": _nonce(7),
            }
        ),
        strict=True,
    )
    with pytest.raises(ClarificationReplayConflictError):
        replay_index.record(changed)

    independent = ClarificationEvaluationRequest.model_validate(
        _sign(
            request_payload
            | {
                "request_id": "request_clarification_002",
                "run_id": "run_clarification_002",
                "run_manifest_artifact_id": "artifact_" + "8" * 64,
                "run_manifest_payload_hash": "sha256:" + "8" * 64,
                "nonce_b64url": _nonce(8),
            }
        ),
        strict=True,
    )
    assert replay_index.record(independent) is True
