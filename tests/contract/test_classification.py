from __future__ import annotations

import pytest
from pydantic import ValidationError

from automarkov.classification_contracts import (
    ClassificationResult,
    validate_classification_payload,
)


def _ref(digit: str) -> dict[str, str]:
    return {
        "artifact_id": f"artifact_{digit * 64}",
        "payload_hash": f"sha256:{digit * 64}",
    }


def _classification_payload() -> dict[str, object]:
    return {
        "schema_version": "automarkov.classification-result.v1",
        "result_kind": "classification",
        "source_task_ref": _ref("1"),
        "evidence_binding": {
            "schema_version": "automarkov.evidence-ledger-binding.v1",
            "binding_kind": "ledger",
            "evidence_ledger_ref": _ref("2"),
        },
        "classification": "IN_SCOPE_MDP",
        "rationale": ["A single actor receives a Markov-sufficient state."],
    }


def test_classification_requires_a_strict_evidence_bound_result() -> None:
    result = validate_classification_payload(_classification_payload())

    assert isinstance(result, ClassificationResult)
    assert result.evidence_binding.binding_kind == "ledger"
    assert result.classification == "IN_SCOPE_MDP"
    assert result.has_validated_provenance()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("rationale", []),
        ("rationale", ["same", "same"]),
        ("classification", "STATIC_OPTIMIZATION"),
    ),
)
def test_classification_rejects_incomplete_or_open_ended_results(
    field: str,
    value: object,
) -> None:
    payload = _classification_payload()
    payload[field] = value

    with pytest.raises((ValueError, ValidationError)):
        validate_classification_payload(payload)


def test_classification_accepts_only_the_closed_no_evidence_ablation_binding() -> None:
    payload = _classification_payload()
    payload["evidence_binding"] = {
        "schema_version": "automarkov.evidence-omission-binding.v1",
        "binding_kind": "omitted_by_design",
        "omission_record_ref": _ref("3"),
        "ablation_method_id": "automarkov_no_evidence",
        "omitted_gate_id": "EVIDENCE_LEDGER_CLOSURE",
    }
    result = validate_classification_payload(payload)
    assert result.evidence_binding.binding_kind == "omitted_by_design"

    payload["evidence_binding"]["ablation_method_id"] = "custom_ablation"  # type: ignore[index]
    with pytest.raises((ValueError, ValidationError)):
        validate_classification_payload(payload)
