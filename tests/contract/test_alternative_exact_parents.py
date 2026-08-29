from __future__ import annotations

import json
from typing import Literal

import pytest

from automarkov.adapters import InMemoryArtifactRepository
from automarkov.domain.errors import ArtifactParentContractError
from automarkov.domain.models import StrictFrozenModel
from automarkov.repository import ArtifactSchemaRegistry


class _Parent(StrictFrozenModel):
    schema_version: Literal["automarkov.test-parent.v1"]
    name: str


class _Child(StrictFrozenModel):
    schema_version: Literal["automarkov.test-child.v1"]
    value: str


def _put(
    repository: InMemoryArtifactRepository,
    artifact_type: str,
    payload: dict[str, object],
    parents: list[str] | None = None,
):
    return repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": artifact_type,
            "payload_bytes": json.dumps(payload).encode(),
            "parent_artifact_ids": sorted(parents or []),
            "created_by": "principal_test",
            "created_at": "2026-08-12T00:00:00Z",
            "source_evidence_ids": [],
        }
    )


def _repository() -> InMemoryArtifactRepository:
    registry = ArtifactSchemaRegistry()
    for artifact_type in (
        "classification_result",
        "reduction_proposal",
        "task_contract",
        "task_contract_authoring_context",
    ):
        registry.register(
            artifact_type,
            "automarkov.test-parent.v1",
            _Parent,
            direct_parent_artifact_types=(),
        )
    registry.register(
        "child_task_contract",
        "automarkov.test-child.v1",
        _Child,
        direct_parent_artifact_types=("task_contract_authoring_context",),
        alternative_direct_parent_artifact_type_sets=(
            ("classification_result", "reduction_proposal", "task_contract"),
        ),
    )
    registry.freeze()
    return InMemoryArtifactRepository(schema_registry=registry)


def test_exact_parent_contract_accepts_authoring_and_reduction_branches() -> None:
    repository = _repository()
    authoring = _put(
        repository,
        "task_contract_authoring_context",
        {"schema_version": "automarkov.test-parent.v1", "name": "authoring"},
    )
    _put(
        repository,
        "child_task_contract",
        {"schema_version": "automarkov.test-child.v1", "value": "draft"},
        [authoring.artifact_id.root],
    )

    parents = [
        _put(
            repository,
            artifact_type,
            {"schema_version": "automarkov.test-parent.v1", "name": artifact_type},
        ).artifact_id.root
        for artifact_type in (
            "task_contract",
            "classification_result",
            "reduction_proposal",
        )
    ]
    _put(
        repository,
        "child_task_contract",
        {"schema_version": "automarkov.test-child.v1", "value": "reduced"},
        parents,
    )


@pytest.mark.parametrize("missing_or_extra", ("missing", "hybrid", "extra"))
def test_exact_parent_contract_rejects_partial_or_hybrid_branches(
    missing_or_extra: str,
) -> None:
    repository = _repository()
    refs = {
        artifact_type: _put(
            repository,
            artifact_type,
            {"schema_version": "automarkov.test-parent.v1", "name": artifact_type},
        ).artifact_id.root
        for artifact_type in (
            "task_contract_authoring_context",
            "task_contract",
            "classification_result",
            "reduction_proposal",
        )
    }
    parents = {
        "missing": [refs["task_contract"], refs["classification_result"]],
        "hybrid": [refs["task_contract_authoring_context"], refs["task_contract"]],
        "extra": [
            refs["task_contract_authoring_context"],
            refs["classification_result"],
        ],
    }[missing_or_extra]
    with pytest.raises(ArtifactParentContractError):
        _put(
            repository,
            "child_task_contract",
            {"schema_version": "automarkov.test-child.v1", "value": missing_or_extra},
            parents,
        )
