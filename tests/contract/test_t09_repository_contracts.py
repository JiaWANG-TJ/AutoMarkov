from __future__ import annotations

import json
from typing import Literal

import pytest

from automarkov.adapters import InMemoryArtifactRepository
from automarkov.classification_contracts import ClassificationResult, ReductionProposal
from automarkov.domain import StrictFrozenModel
from automarkov.errors import ArtifactParentContractError
from automarkov.repository import ArtifactSchemaRegistry, ParentBinding


class _Parent(StrictFrozenModel):
    schema_version: Literal["automarkov.test-t09-parent.v1"]
    value: str


def _put(
    repository: InMemoryArtifactRepository,
    artifact_type: str,
    payload: dict[str, object],
    parents: tuple[str, ...] = (),
):
    return repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": artifact_type,
            "payload_bytes": json.dumps(payload).encode(),
            "parent_artifact_ids": sorted(parents),
            "created_by": "principal_t09_test",
            "created_at": "2026-08-12T00:00:00Z",
            "source_evidence_ids": [],
        }
    )


def _registry() -> ArtifactSchemaRegistry:
    registry = ArtifactSchemaRegistry()
    for artifact_type in ("evidence_ledger", "task_contract"):
        registry.register(
            artifact_type,
            "automarkov.test-t09-parent.v1",
            _Parent,
            direct_parent_artifact_types=(),
        )
    registry.register(
        "classification_result",
        "automarkov.classification-result.v1",
        ClassificationResult,
        payload_parent_bindings=(
            ParentBinding(
                artifact_id_path=("evidence_binding.evidence_ledger_ref.artifact_id"),
                payload_hash_path=("evidence_binding.evidence_ledger_ref.payload_hash"),
                allowed_artifact_types=("evidence_ledger",),
                cardinality="one",
            ),
            ParentBinding(
                artifact_id_path="source_task_ref.artifact_id",
                payload_hash_path="source_task_ref.payload_hash",
                allowed_artifact_types=("task_contract",),
                cardinality="one",
            ),
        ),
    )
    registry.register(
        "reduction_proposal",
        "automarkov.reduction-proposal.v1",
        ReductionProposal,
        payload_parent_bindings=(
            ParentBinding(
                artifact_id_path="classification_ref.artifact_id",
                payload_hash_path="classification_ref.payload_hash",
                allowed_artifact_types=("classification_result",),
                cardinality="one",
            ),
            ParentBinding(
                artifact_id_path="source_task_ref.artifact_id",
                payload_hash_path="source_task_ref.payload_hash",
                allowed_artifact_types=("task_contract",),
                cardinality="one",
            ),
            ParentBinding(
                artifact_id_path="supersedes_proposal_ref.artifact_id",
                payload_hash_path="supersedes_proposal_ref.payload_hash",
                allowed_artifact_types=("reduction_proposal",),
                cardinality="optional",
            ),
            ParentBinding(
                artifact_id_path="trigger_classification_ref.artifact_id",
                payload_hash_path="trigger_classification_ref.payload_hash",
                allowed_artifact_types=("classification_result",),
                cardinality="optional",
            ),
        ),
    )
    registry.freeze()
    return registry


def _ref(result) -> dict[str, str]:
    return {
        "artifact_id": result.artifact_id.root,
        "payload_hash": result.payload_hash.root,
    }


def test_repository_binds_ordinary_classification_and_reduction_semantics() -> None:
    repository = InMemoryArtifactRepository(schema_registry=_registry())
    task = _put(
        repository,
        "task_contract",
        {"schema_version": "automarkov.test-t09-parent.v1", "value": "task"},
    )
    ledger = _put(
        repository,
        "evidence_ledger",
        {"schema_version": "automarkov.test-t09-parent.v1", "value": "ledger"},
    )
    classification = _put(
        repository,
        "classification_result",
        {
            "schema_version": "automarkov.classification-result.v1",
            "result_kind": "classification",
            "source_task_ref": _ref(task),
            "evidence_binding": {
                "schema_version": "automarkov.evidence-ledger-binding.v1",
                "binding_kind": "ledger",
                "evidence_ledger_ref": _ref(ledger),
            },
            "classification": "REDUCIBLE",
            "rationale": ["Finite reduction is possible with explicit semantic loss."],
        },
        (task.artifact_id.root, ledger.artifact_id.root),
    )

    proposal = _put(
        repository,
        "reduction_proposal",
        {
            "schema_version": "automarkov.reduction-proposal.v1",
            "proposal_kind": "decision_process_reduction",
            "source_task_ref": _ref(task),
            "classification_ref": _ref(classification),
            "target_kind": "MDP",
            "assumptions": [
                {
                    "assumption_id": "finite_state",
                    "kind": "finite_state",
                    "statement": "Cap the state space.",
                    "semantic_loss": "States beyond the cap are unavailable.",
                    "evidence_ids": [],
                }
            ],
            "preserved_properties": ["transition order"],
            "lost_properties": ["unbounded state"],
            "supersedes_proposal_ref": None,
            "trigger_classification_ref": None,
            "approval_required": True,
        },
        (task.artifact_id.root, classification.artifact_id.root),
    )
    assert repository.get(proposal.artifact_id).envelope.artifact_type == (
        "reduction_proposal"
    )


def test_repository_rejects_reduction_from_an_in_scope_classification() -> None:
    repository = InMemoryArtifactRepository(schema_registry=_registry())
    task = _put(
        repository,
        "task_contract",
        {"schema_version": "automarkov.test-t09-parent.v1", "value": "task"},
    )
    ledger = _put(
        repository,
        "evidence_ledger",
        {"schema_version": "automarkov.test-t09-parent.v1", "value": "ledger"},
    )
    classification = _put(
        repository,
        "classification_result",
        {
            "schema_version": "automarkov.classification-result.v1",
            "result_kind": "classification",
            "source_task_ref": _ref(task),
            "evidence_binding": {
                "schema_version": "automarkov.evidence-ledger-binding.v1",
                "binding_kind": "ledger",
                "evidence_ledger_ref": _ref(ledger),
            },
            "classification": "IN_SCOPE_MDP",
            "rationale": ["Already in scope."],
        },
        (task.artifact_id.root, ledger.artifact_id.root),
    )
    with pytest.raises(ArtifactParentContractError):
        _put(
            repository,
            "reduction_proposal",
            {
                "schema_version": "automarkov.reduction-proposal.v1",
                "proposal_kind": "decision_process_reduction",
                "source_task_ref": _ref(task),
                "classification_ref": _ref(classification),
                "target_kind": "MDP",
                "assumptions": [
                    {
                        "assumption_id": "finite_state",
                        "kind": "finite_state",
                        "statement": "Cap the state space.",
                        "semantic_loss": "States beyond the cap are unavailable.",
                        "evidence_ids": [],
                    }
                ],
                "preserved_properties": [],
                "lost_properties": ["unbounded state"],
                "supersedes_proposal_ref": None,
                "trigger_classification_ref": None,
                "approval_required": True,
            },
            (task.artifact_id.root, classification.artifact_id.root),
        )


def test_default_repository_fail_closes_unimplemented_no_evidence_persistence() -> None:
    repository = InMemoryArtifactRepository()
    with pytest.raises(ArtifactParentContractError):
        _put(
            repository,
            "classification_result",
            {
                "schema_version": "automarkov.classification-result.v1",
                "result_kind": "classification",
                "source_task_ref": {
                    "artifact_id": "artifact_" + "1" * 64,
                    "payload_hash": "sha256:" + "1" * 64,
                },
                "evidence_binding": {
                    "schema_version": "automarkov.evidence-omission-binding.v1",
                    "binding_kind": "omitted_by_design",
                    "omission_record_ref": {
                        "artifact_id": "artifact_" + "2" * 64,
                        "payload_hash": "sha256:" + "2" * 64,
                    },
                    "ablation_method_id": "automarkov_no_evidence",
                    "omitted_gate_id": "EVIDENCE_LEDGER_CLOSURE",
                },
                "classification": "OOD",
                "rationale": ["Controlled no-evidence experiment route."],
            },
        )
