"""T19: Policy Export contract tests — CheckpointTreeManifest, PolicyExportManifest, TensorSpec."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from automarkov.contracts.policy import (
    CheckpointTreeEntry,
    CheckpointTreeManifest,
    PolicyExportManifest,
    TensorSpec,
)
from automarkov.lifecycle import ArtifactReference

HX = "0" * 64  # valid hex for sha256
ART = "artifact_" + HX
HASH = "sha256:" + HX


def _entry(path: str, size: int = 1024) -> CheckpointTreeEntry:
    return CheckpointTreeEntry(relative_path=path, size_bytes=size, sha256=HASH)


def _manifest(*paths: str) -> CheckpointTreeManifest:
    return CheckpointTreeManifest(entries=tuple(_entry(p) for p in paths))


def _tensor(name: str) -> TensorSpec:
    return TensorSpec(name=name, dtype="float32", shape=(64,))


def _export() -> PolicyExportManifest:
    return PolicyExportManifest(
        export_id="export001", experiment_id="expt19", run_id="runt19001",
        candidate_bundle=ArtifactReference(artifact_id=ART, payload_hash=HASH),
        seed=1001,
        training_terminal_record=ArtifactReference(artifact_id=ART, payload_hash=HASH),
        export_terminal_record=ArtifactReference(artifact_id=ART, payload_hash=HASH),
        source_checkpoint_commitment=HASH, architecture_id="arch001",
        connector_id="conn001", observation_adapter_id="obs001",
        action_adapter_id="act001", trainer_execution_id="trainer001",
        exporter_execution_id="exporter001",
        tensor_artifact=ArtifactReference(artifact_id=ART, payload_hash=HASH),
        tensors=(_tensor("policy"), _tensor("value")),
        issued_at="2026-08-21T12:00:00Z", nonce_b64url="A" * 43,
        principal_id="principal001", signing_key_id="key001",
        signature_b64url="B" * 86,
    )


class TestCheckpointTreeEntry:
    def test_accepts_valid(self) -> None:
        e = _entry("ckpt/policy.pt")
        assert e.size_bytes == 1024

    def test_rejects_empty_path(self) -> None:
        with pytest.raises(ValidationError):
            _entry("")

    def test_round_trips(self) -> None:
        e = _entry("model.pt", size=2048)
        r = CheckpointTreeEntry.model_validate(e.model_dump(mode="json"), strict=True)
        assert r.size_bytes == 2048


class TestCheckpointTreeManifest:
    def test_accepts_sorted(self) -> None:
        m = _manifest("a.pt", "b.pt", "c.pt")
        assert len(m.entries) == 3

    def test_rejects_unsorted(self) -> None:
        with pytest.raises(ValidationError, match="sorted"):
            _manifest("b.pt", "a.pt")

    def test_rejects_duplicates(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            _manifest("a.pt", "a.pt")

    def test_empty_valid(self) -> None:
        assert len(CheckpointTreeManifest(entries=()).entries) == 0

    def test_round_trips(self) -> None:
        m = _manifest("optimizer.pt", "policy.pt")
        r = CheckpointTreeManifest.model_validate(m.model_dump(mode="json"), strict=True)
        assert r.entries[0].relative_path == "optimizer.pt"


class TestTensorSpec:
    def test_accepts_valid(self) -> None:
        t = TensorSpec(name="actor", dtype="float32", shape=(256, 64))
        assert t.dtype == "float32"

    def test_rejects_bad_dtype(self) -> None:
        with pytest.raises(ValidationError):
            TensorSpec(name="t", dtype="float16", shape=(1,))  # type: ignore[arg-type]

    def test_all_dtypes(self) -> None:
        for dt in ("float32", "float64", "int32", "int64", "bool", "uint8"):
            assert TensorSpec(name=dt, dtype=dt, shape=(1,)).dtype == dt


class TestPolicyExportManifest:
    def test_accepts_valid(self) -> None:
        m = _export()
        assert m.seed == 1001 and len(m.tensors) == 2

    def test_rejects_empty_tensors(self) -> None:
        with pytest.raises(ValidationError, match="at least one"):
            PolicyExportManifest(
                export_id="e1", experiment_id="ex", run_id="rx",
                candidate_bundle=ArtifactReference(artifact_id=ART, payload_hash=HASH),
                seed=1001,
                training_terminal_record=ArtifactReference(artifact_id=ART, payload_hash=HASH),
                export_terminal_record=ArtifactReference(artifact_id=ART, payload_hash=HASH),
                source_checkpoint_commitment=HASH, architecture_id="a",
                connector_id="c", observation_adapter_id="o",
                action_adapter_id="a", trainer_execution_id="t",
                exporter_execution_id="x",
                tensor_artifact=ArtifactReference(artifact_id=ART, payload_hash=HASH),
                tensors=(),
                issued_at="2026-08-21T12:00:00Z", nonce_b64url="A" * 43,
                principal_id="p", signing_key_id="k",
                signature_b64url="B" * 86,
            )

    def test_rejects_duplicate_names(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            PolicyExportManifest(
                export_id="e2", experiment_id="ex", run_id="rx",
                candidate_bundle=ArtifactReference(artifact_id=ART, payload_hash=HASH),
                seed=1001,
                training_terminal_record=ArtifactReference(artifact_id=ART, payload_hash=HASH),
                export_terminal_record=ArtifactReference(artifact_id=ART, payload_hash=HASH),
                source_checkpoint_commitment=HASH, architecture_id="a",
                connector_id="c", observation_adapter_id="o",
                action_adapter_id="a", trainer_execution_id="t",
                exporter_execution_id="x",
                tensor_artifact=ArtifactReference(artifact_id=ART, payload_hash=HASH),
                tensors=(_tensor("dup"), _tensor("dup")),
                issued_at="2026-08-21T12:00:00Z", nonce_b64url="A" * 43,
                principal_id="p", signing_key_id="k",
                signature_b64url="B" * 86,
            )

    def test_round_trips(self) -> None:
        m = _export()
        r = PolicyExportManifest.model_validate(m.model_dump(mode="json"), strict=True)
        assert r.seed == 1001
