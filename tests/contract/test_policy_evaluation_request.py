"""T19: Policy Evaluation Request 合同测试。

验证 exact-ten-seed (1001–1010)、三个 discriminated branch
与 PolicyEvaluationOutcome 的 invariant。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from automarkov.contracts.policy import (
    CANONICAL_TEN_SEEDS,
    PolicyEvaluationOutcome,
    PolicyEvaluationRequest,
    PolicyEvaluationSeedBinding,
)
from automarkov.lifecycle import ArtifactReference

ART = "artifact_" + "a" * 64
HASH = "sha256:" + "0" * 64


def _seed_binding(seed: int, branch: str = "success") -> PolicyEvaluationSeedBinding:
    if branch == "success":
        return PolicyEvaluationSeedBinding(
            seed=seed,
            branch="success",
            training_terminal_record=ArtifactReference(artifact_id=ART, payload_hash=HASH),
            export_terminal_record=ArtifactReference(artifact_id=ART, payload_hash=HASH),
            export_manifest=ArtifactReference(artifact_id=ART, payload_hash=HASH),
            tensor_artifact=ArtifactReference(artifact_id=ART, payload_hash=HASH),
        )
    elif branch == "training_failure":
        return PolicyEvaluationSeedBinding(
            seed=seed,
            branch="training_failure",
            training_terminal_record=ArtifactReference(artifact_id=ART, payload_hash=HASH),
        )
    else:
        return PolicyEvaluationSeedBinding(
            seed=seed,
            branch="export_failure",
            training_terminal_record=ArtifactReference(artifact_id=ART, payload_hash=HASH),
            export_terminal_record=ArtifactReference(artifact_id=ART, payload_hash=HASH),
        )


def _request(
    *branches: str,
    prefix: str = "req001",
) -> PolicyEvaluationRequest:
    if len(branches) != 10:
        branches = ("success",) * 10
    return PolicyEvaluationRequest(
        request_id=prefix,
        experiment_id="expt19",
        run_id="runt19eval",
        candidate_bundle=ArtifactReference(artifact_id=ART, payload_hash=HASH),
        run_manifest=ArtifactReference(artifact_id=ART, payload_hash=HASH),
        e2e_verdict=ArtifactReference(artifact_id=ART, payload_hash=HASH),
        smoke_attestation=ArtifactReference(artifact_id=ART, payload_hash=HASH),
        suite_calibration=ArtifactReference(artifact_id=ART, payload_hash=HASH),
        evaluator_profile_id="evalprofile01",
        evaluator_profile_hash=HASH,
        observation_adapter_id="obsadapter01",
        action_adapter_id="actadapter01",
        seed_bindings=tuple(
            _seed_binding(seed, branch)
            for seed, branch in zip(CANONICAL_TEN_SEEDS, branches, strict=True)
        ),
        issued_at="2026-08-21T12:00:00Z",
        not_before="2026-08-21T11:00:00Z",
        expires_at="2026-08-21T13:00:00Z",
        nonce_b64url="C" * 43,
        coordinator_principal_id="coord001",
        coordinator_key_id="key001",
        signature_b64url="D" * 86,
    )


# ── PolicyEvaluationSeedBinding ─────────────────────────────────


class TestPolicyEvaluationSeedBinding:
    def test_accepts_success_binding(self) -> None:
        b = _seed_binding(1001, "success")
        assert b.seed == 1001
        assert b.tensor_artifact is not None

    def test_accepts_training_failure_binding(self) -> None:
        b = _seed_binding(1002, "training_failure")
        assert b.branch == "training_failure"
        assert b.export_manifest is None

    def test_accepts_export_failure_binding(self) -> None:
        b = _seed_binding(1003, "export_failure")
        assert b.branch == "export_failure"
        assert b.export_terminal_record is not None
        assert b.export_manifest is None
        assert b.tensor_artifact is None

    def test_success_rejects_missing_fields(self) -> None:
        with pytest.raises(ValidationError):
            PolicyEvaluationSeedBinding(
                seed=1001,
                branch="success",
                training_terminal_record=ArtifactReference(
                    artifact_id=ART, payload_hash=HASH
                ),
            )

    def test_training_failure_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            PolicyEvaluationSeedBinding(
                seed=1001,
                branch="training_failure",
                training_terminal_record=ArtifactReference(
                    artifact_id=ART, payload_hash=HASH
                ),
                export_manifest=ArtifactReference(artifact_id=ART, payload_hash=HASH),
            )

    def test_rejects_seed_outside_range(self) -> None:
        with pytest.raises(ValidationError):
            _seed_binding(999, "success")
        with pytest.raises(ValidationError):
            _seed_binding(1011, "success")


# ── PolicyEvaluationRequest ──────────────────────────────────────


class TestPolicyEvaluationRequest:
    def test_accepts_ten_success_seeds(self) -> None:
        r = _request()
        assert len(r.seed_bindings) == 10
        assert r.success_count == 10
        assert r.failure_count == 0

    def test_accepts_mixed_branches(self) -> None:
        branches = (
            "success", "success", "success",
            "training_failure", "training_failure",
            "success", "success", "success",
            "export_failure", "success",
        )
        r = _request(*branches)
        assert r.success_count == 7
        assert r.failure_count == 3

    def test_rejects_nine_seeds(self) -> None:
        with pytest.raises(ValidationError, match="exactly 1001"):
            PolicyEvaluationRequest(
                **_request_fields(),
                seed_bindings=tuple(
                    _seed_binding(s, "success") for s in range(1001, 1010)
                ),
            )

    def test_rejects_out_of_order_seeds(self) -> None:
        with pytest.raises(ValidationError, match="exactly 1001"):
            bindings = tuple(
                _seed_binding(s, "success")
                for s in [1001, 1002, 1004, 1003, 1005, 1006, 1007, 1008, 1009, 1010]
            )
            PolicyEvaluationRequest(
                **_request_fields(),
                seed_bindings=bindings,
            )

    def test_rejects_duplicate_seeds(self) -> None:
        with pytest.raises(ValidationError):
            bindings = tuple(
                _seed_binding(1001 if i < 5 else 1002, "success")
                for i in range(10)
            )
            PolicyEvaluationRequest(
                **_request_fields(),
                seed_bindings=bindings,
            )

    def test_round_trips(self) -> None:
        r = _request()
        reloaded = PolicyEvaluationRequest.model_validate(
            r.model_dump(mode="json"), strict=True
        )
        assert reloaded.request_id == "req001"
        assert reloaded.success_count == 10


def _request_fields() -> dict:
    return {
        "request_id": "req001",
        "experiment_id": "expt19",
        "run_id": "runt19eval",
        "candidate_bundle": ArtifactReference(artifact_id=ART, payload_hash=HASH),
        "run_manifest": ArtifactReference(artifact_id=ART, payload_hash=HASH),
        "e2e_verdict": ArtifactReference(artifact_id=ART, payload_hash=HASH),
        "smoke_attestation": ArtifactReference(artifact_id=ART, payload_hash=HASH),
        "suite_calibration": ArtifactReference(artifact_id=ART, payload_hash=HASH),
        "evaluator_profile_id": "evalprofile01",
        "evaluator_profile_hash": HASH,
        "observation_adapter_id": "obsadapter01",
        "action_adapter_id": "actadapter01",
        "issued_at": "2026-08-21T12:00:00Z",
        "not_before": "2026-08-21T11:00:00Z",
        "expires_at": "2026-08-21T13:00:00Z",
        "nonce_b64url": "C" * 43,
        "coordinator_principal_id": "coord001",
        "coordinator_key_id": "key001",
        "signature_b64url": "D" * 86,
    }


# ── PolicyEvaluationOutcome ──────────────────────────────────────


class TestPolicyEvaluationOutcome:
    def test_success_outcome(self) -> None:
        o = PolicyEvaluationOutcome(
            seed=1001,
            branch="success",
            gold_policy_evaluation_valid=True,
            q_gate=0.85,
            normalized_return=0.72,
        )
        assert o.gold_policy_evaluation_valid is True

    def test_training_failure_forces_zero(self) -> None:
        with pytest.raises(ValidationError):
            PolicyEvaluationOutcome(
                seed=1002,
                branch="training_failure",
                gold_policy_evaluation_valid=True,
                q_gate=0.0,
            )

    def test_export_failure_forces_zero(self) -> None:
        with pytest.raises(ValidationError):
            PolicyEvaluationOutcome(
                seed=1003,
                branch="export_failure",
                gold_policy_evaluation_valid=False,
                q_gate=0.5,
            )

    def test_non_success_cannot_have_normalized_return(self) -> None:
        with pytest.raises(ValidationError):
            PolicyEvaluationOutcome(
                seed=1004,
                branch="training_failure",
                gold_policy_evaluation_valid=False,
                q_gate=0.0,
                normalized_return=0.5,
            )

    def test_round_trips(self) -> None:
        o = PolicyEvaluationOutcome(
            seed=1005,
            branch="success",
            gold_policy_evaluation_valid=True,
            q_gate=0.92,
            normalized_return=0.88,
        )
        reloaded = PolicyEvaluationOutcome.model_validate(
            o.model_dump(mode="json"), strict=True
        )
        assert reloaded.q_gate == 0.92
