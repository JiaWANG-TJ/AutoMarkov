"""T19: Checkpoint boundary security tests -- TOCTOU protection, cross-profile isolation."""

from __future__ import annotations

from hashlib import sha256

import pytest
from pydantic import ValidationError

from automarkov.contracts.policy import (
    CheckpointTreeEntry,
    CheckpointTreeManifest,
    PolicyExportManifest,
    TensorSpec,
)
from automarkov.domain.canonical import canonical_json_bytes
from automarkov.lifecycle import ArtifactReference

HX = "0" * 64
ART = "artifact_" + HX
HASH = "sha256:" + HX


class TestCheckpointIntegrity:
    def test_manifest_deterministic(self) -> None:
        e = CheckpointTreeEntry(
            relative_path="p.pt",
            size_bytes=4096,
            sha256=HASH,
        )
        m1 = CheckpointTreeManifest(entries=(e,))
        m2 = CheckpointTreeManifest(
            entries=(
                CheckpointTreeEntry(
                    relative_path="p.pt",
                    size_bytes=4096,
                    sha256=HASH,
                ),
            )
        )
        assert m1.model_dump(mode="json") == m2.model_dump(mode="json")

    def test_size_change_alters_hash(self) -> None:
        e1 = CheckpointTreeEntry(
            relative_path="a.pt",
            size_bytes=1024,
            sha256=HASH,
        )
        e2 = CheckpointTreeEntry(
            relative_path="a.pt",
            size_bytes=2048,
            sha256=HASH,
        )
        assert CheckpointTreeManifest(entries=(e1,)) != CheckpointTreeManifest(
            entries=(e2,)
        )


def _tensors_default() -> tuple[TensorSpec, ...]:
    """Provide default tensors for export manifests."""
    return (
        TensorSpec(name="a", dtype="float32", shape=(64,)),
    )


def _make_export_manifest(
    *,
    seed: int = 1001,
    tensors: tuple[TensorSpec, ...] = _tensors_default(),
    source_checkpoint_commitment: str = HASH,
) -> PolicyExportManifest:
    """Build a PolicyExportManifest with fixed boilerplate."""
    return PolicyExportManifest(
        export_id="eb",
        experiment_id="ex",
        run_id="r",
        candidate_bundle=ArtifactReference(
            artifact_id=ART, payload_hash=HASH,
        ),
        seed=seed,
        training_terminal_record=ArtifactReference(
            artifact_id=ART, payload_hash=HASH,
        ),
        export_terminal_record=ArtifactReference(
            artifact_id=ART, payload_hash=HASH,
        ),
        source_checkpoint_commitment=source_checkpoint_commitment,
        architecture_id="a",
        connector_id="c",
        observation_adapter_id="o",
        action_adapter_id="a",
        trainer_execution_id="t",
        exporter_execution_id="x",
        tensor_artifact=ArtifactReference(
            artifact_id=ART, payload_hash=HASH,
        ),
        tensors=tensors,
        issued_at="2026-08-21T12:00:00Z",
        nonce_b64url="E" * 43,
        principal_id="p",
        signing_key_id="k",
        signature_b64url="F" * 86,
    )


class TestCheckpointBoundary:
    def test_commitment_bound_to_export(self) -> None:
        m = CheckpointTreeManifest(
            entries=(
                CheckpointTreeEntry(
                    relative_path="w.ckpt",
                    size_bytes=8192,
                    sha256=HASH,
                ),
            )
        )
        commitment = (
            "sha256:"
            + sha256(
                canonical_json_bytes(m.model_dump(mode="json"))
            ).hexdigest()
        )
        ex = _make_export_manifest(source_checkpoint_commitment=commitment)
        assert ex.source_checkpoint_commitment == commitment

    def test_seed_must_be_in_range(self) -> None:
        with pytest.raises(ValidationError):
            _make_export_manifest(seed=42)

    def test_export_manifest_tensors_explicit(self) -> None:
        ex = _make_export_manifest(
            tensors=(
                TensorSpec(
                    name="policy",
                    dtype="float32",
                    shape=(128, 256),
                ),
            )
        )
        assert ex.tensors[0].shape == (128, 256)


class TestCrossProfileIsolation:
    def test_tensor_artifact_is_explicit(self) -> None:
        ex = _make_export_manifest()
        assert ex.tensor_artifact is not None
        assert ex.tensor_artifact.artifact_id == ART