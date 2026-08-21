from __future__ import annotations

import pytest
from pydantic import ValidationError

from automarkov.sealed_evaluation import (
    BoundedWorkerOutput,
    E2EGateCommitResult,
    E2EGateEvaluationRequest,
    E2EGateExecutionCommitInput,
    E2EGateVerdict,
    SealedWorkerBinding,
    SealedWorkerTopology,
    SignedWorkerEvidence,
    WorkerIsolationPolicy,
)


def _policy(kind: str) -> dict[str, object]:
    branches = {
        "candidate": (
            ("bounded_candidate_execute",),
            (
                "arbitrary_ipc",
                "network",
                "sealed_asset",
                "sealed_credential",
                "sealed_key",
                "sealed_locator",
            ),
            False,
            True,
        ),
        "gold": (
            ("sealed_gold_execute",),
            ("candidate_code", "generation", "network", "training"),
            True,
            False,
        ),
        "comparator": (
            ("sealed_compare",),
            ("candidate_code", "generation", "network", "training"),
            True,
            False,
        ),
    }
    allowed, denied, sealed, candidate_code = branches[kind]
    return {
        "schema_version": "automarkov.worker-isolation-policy.v1",
        "worker_kind": kind,
        "allowed_capabilities": allowed,
        "denied_capabilities": denied,
        "linux_denied_capabilities": ("capability:all",),
        "network_access": False,
        "sealed_access": sealed,
        "candidate_code_access": candidate_code,
        "network_policy_hash": "sha256:" + "1" * 64,
        "mount_table_hash": "sha256:" + "2" * 64,
        "capability_decision_log_hash": "sha256:" + "3" * 64,
        "egress_decision_log_hash": "sha256:" + "4" * 64,
    }


def test_candidate_gold_and_comparator_policies_are_three_closed_boundaries() -> None:
    policies = tuple(
        WorkerIsolationPolicy.model_validate(_policy(kind), strict=True)
        for kind in ("candidate", "gold", "comparator")
    )

    assert policies[0].network_access is False
    assert policies[0].sealed_access is False
    assert policies[0].candidate_code_access is True
    assert policies[1].sealed_access is True
    assert policies[1].candidate_code_access is False
    assert policies[2].allowed_capabilities == ("sealed_compare",)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("network_access", True),
        ("sealed_access", True),
        ("allowed_capabilities", ("sealed_gold_execute",)),
    ),
)
def test_candidate_policy_fails_closed_on_any_privilege_drift(
    field: str, value: object
) -> None:
    raw = _policy("candidate")
    raw[field] = value

    with pytest.raises(ValidationError):
        WorkerIsolationPolicy.model_validate(raw, strict=True)


def test_worker_boundary_requires_signed_subjects_attestation_and_bounded_outputs() -> (
    None
):
    assert {
        "capability_decision_log",
        "egress_decision_log",
        "execution_attestation",
        "evidence",
        "isolation_policy",
        "mount_attestation",
        "network_policy",
        "profile_hash",
        "runner_output_refs",
    } <= set(SealedWorkerBinding.model_fields)
    assert {
        "request_ref",
        "candidate_bundle",
        "task_contract",
        "decision_process_spec",
        "environment_binding",
        "execution_attestation_ref",
        "subject_outputs",
        "outputs",
    } <= set(SignedWorkerEvidence.model_fields)
    assert set(SealedWorkerTopology.model_fields) == {
        "schema_version",
        "candidate",
        "gold",
        "comparator",
    }
    assert BoundedWorkerOutput.model_fields["byte_length"].is_required()
    assert SignedWorkerEvidence.model_fields["subject_outputs"].is_required()


def test_public_contracts_do_not_expose_sealed_diagnostics() -> None:
    forbidden = {
        "counterexample",
        "expected",
        "expected_value",
        "hidden_identity",
        "test_identity",
        "topology_ref",
        "trace",
    }
    for contract in (
        E2EGateEvaluationRequest,
        E2EGateVerdict,
        E2EGateCommitResult,
    ):
        assert forbidden.isdisjoint(contract.model_fields)


def test_verdict_is_closed_without_runner_backreferences() -> None:
    assert "runner_fingerprint" not in E2EGateVerdict.model_fields
    assert "process_execution_terminal_record" not in E2EGateVerdict.model_fields
    assert set(E2EGateExecutionCommitInput.model_fields) == {
        "schema_version",
        "runner_fingerprint",
        "process_execution_terminal_record",
    }
