from __future__ import annotations

import pytest
from pydantic import ValidationError

from automarkov.classification_contracts import validate_reduction_proposal_payload


def _ref(digit: str) -> dict[str, str]:
    return {
        "artifact_id": f"artifact_{digit * 64}",
        "payload_hash": f"sha256:{digit * 64}",
    }


def _proposal() -> dict[str, object]:
    return {
        "schema_version": "automarkov.reduction-proposal.v1",
        "proposal_kind": "decision_process_reduction",
        "source_task_ref": _ref("1"),
        "classification_ref": _ref("2"),
        "target_kind": "MDP",
        "assumptions": [
            {
                "assumption_id": "finite_inventory",
                "kind": "finite_state",
                "statement": "Inventory is capped at 100 units.",
                "semantic_loss": "States above the cap are excluded.",
                "evidence_ids": ["E-capacity"],
            }
        ],
        "preserved_properties": ["inventory balance"],
        "lost_properties": ["unbounded storage"],
        "supersedes_proposal_ref": None,
        "trigger_classification_ref": None,
        "approval_required": True,
    }


def test_initial_reduction_proposal_is_strict_and_approval_gated() -> None:
    proposal = validate_reduction_proposal_payload(_proposal())

    assert proposal.target_kind == "MDP"
    assert proposal.approval_required is True
    assert proposal.supersedes_proposal_ref is None


def test_reduction_revision_requires_both_lineage_references() -> None:
    payload = _proposal()
    payload["supersedes_proposal_ref"] = _ref("3")

    with pytest.raises((ValueError, ValidationError)):
        validate_reduction_proposal_payload(payload)

    payload["trigger_classification_ref"] = _ref("4")
    assert validate_reduction_proposal_payload(payload).trigger_classification_ref


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("assumptions", []),
        (
            "assumptions",
            [
                {
                    "assumption_id": "duplicate",
                    "kind": "horizon",
                    "statement": "Use a day horizon.",
                    "semantic_loss": "Long effects are excluded.",
                    "evidence_ids": [],
                },
                {
                    "assumption_id": "duplicate",
                    "kind": "chance",
                    "statement": "Use expected demand.",
                    "semantic_loss": "Tail events are excluded.",
                    "evidence_ids": [],
                },
            ],
        ),
        ("approval_required", False),
    ),
)
def test_reduction_rejects_unapproved_or_ambiguous_semantic_loss(
    field: str,
    value: object,
) -> None:
    payload = _proposal()
    payload[field] = value
    with pytest.raises((ValueError, ValidationError)):
        validate_reduction_proposal_payload(payload)
