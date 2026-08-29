from __future__ import annotations

import pytest

from automarkov.adapters import InMemoryArtifactRepository
from automarkov.domain.errors import ArtifactSchemaError


@pytest.mark.parametrize(
    "artifact_type",
    ("public_validation_plan", "public_validation_gate_report"),
)
def test_t15_artifacts_remain_unregistered_until_their_parent_dag_closes(
    artifact_type: str,
) -> None:
    repository = InMemoryArtifactRepository()

    with pytest.raises(ArtifactSchemaError):
        repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": artifact_type,
                "payload_bytes": b"{}",
                "parent_artifact_ids": [],
                "created_by": "principal_t15_test",
                "created_at": "2026-08-12T00:00:00Z",
                "source_evidence_ids": [],
            }
        )
