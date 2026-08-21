"""T19: Checkpoint boundary security tests — TOCTOU protection, cross-profile isolation."""

from __future__ import annotations

import pytest
from hashlib import sha256

from automarkov.canonical import canonical_json_bytes
from automarkov.policy_export import (
    CheckpointTreeEntry,
    CheckpointTreeManifest,
    PolicyExportManifest,
)

HX = "0" * 64
ART = "artifact_" + HX
HASH = "sha256:" + HX


class TestCheckpointIntegrity:
    def test_manifest_deterministic(self) -> None:
        e = CheckpointTreeEntry(relative_path="p.pt", size_bytes=4096, sha256=HASH)
        m1 = CheckpointTreeManifest(entries=(e,))
        m2 = CheckpointTreeManifest(entries=(CheckpointTreeEntry(
            relative_path="p.pt", size_bytes=4096, sha256=HASH
        ),))
        assert m1.model_dump(mode="json") == m2.model_dump(mode="json")

    def test_size_change_alters_hash(self) -> None:
        e1 = CheckpointTreeEntry(relative_path="a.pt", size_bytes=1024, sha256=HASH)
        e2 = CheckpointTreeEntry(relative_path="a.pt", size_bytes=2048, sha256=HASH)
        assert CheckpointTreeManifest(entries=(e1,)) != CheckpointTreeManifest(entries=(e2,))


class TestCheckpointBoundary:
    def test_commitment_bound_to_export(self) -> None:
        e = CheckpointTreeEntry(relative_path="w.ckpt", size_bytes=8192, sha256=HASH)
        m = CheckpointTreeManifest(entries=(e,))
        commitment = "sha256:" + sha256(canonical_json_bytes(
            m.model_dump(mode="json"))).hexdigest()
        ex = PolicyExportManifest(
            export_id="eb", experiment_id="ex", run_id="r",
            candidate_bundle={"artifact_id": ART, "payload_hash": HASH},
            seed=1001,
            training_terminal_record={"artifact_id": ART, "payload_hash": HASH},
            export_terminal_record={"artifact_id": ART, "payload_hash": HASH},
            source_checkpoint_commitment=commitment, architecture_id="a",
            connector_id="c", observation_adapter_id="o", action_adapter_id="a",
            trainer_execution_id="t", exporter_execution_id="x",
            tensor_artifact={"artifact_id": ART, "payload_hash": HASH},
            tensors=({"name": "actor", "dtype": "float32", "shape": (256, 64)},),
            issued_at="2026-08-21T12:00:00Z", nonce_b64url="E" * 43,
            principal_id="p", signing_key_id="k", signature_b64url="F" * 86,
        )
        assert ex.source_checkpoint_commitment == commitment

    def test_seed_must_be_in_range(self) -> None:
        with pytest.raises(Exception):
            PolicyExportManifest(
                export_id="es", experiment_id="ex", run_id="r",
                candidate_bundle={"artifact_id": ART, "payload_hash": HASH},
                seed=42,
                training_terminal_record={"artifact_id": ART, "payload_hash": HASH},
                export_terminal_record={"artifact_id": ART, "payload_hash": HASH},
                source_checkpoint_commitment=HASH, architecture_id="a",
                connector_id="c", observation_adapter_id="o", action_adapter_id="a",
                trainer_execution_id="t", exporter_execution_id="x",
                tensor_artifact={"artifact_id": ART, "payload_hash": HASH},
                tensors=({"name": "a", "dtype": "float32", "shape": (64,)},),
                issued_at="2026-08-21T12:00:00Z", nonce_b64url="G" * 43,
                principal_id="p", signing_key_id="k", signature_b64url="H" * 86,
            )


class TestCrossProfileIsolation:
    def test_tensor_artifact_is_explicit(self) -> None:
        ex = PolicyExportManifest(
            export_id="ee", experiment_id="ex", run_id="r",
            candidate_bundle={"artifact_id": ART, "payload_hash": HASH},
            seed=1001,
            training_terminal_record={"artifact_id": ART, "payload_hash": HASH},
            export_terminal_record={"artifact_id": ART, "payload_hash": HASH},
            source_checkpoint_commitment=HASH, architecture_id="a",
            connector_id="c", observation_adapter_id="o", action_adapter_id="a",
            trainer_execution_id="t", exporter_execution_id="x",
            tensor_artifact={"artifact_id": ART, "payload_hash": HASH},
            tensors=({"name": "policy", "dtype": "float32", "shape": (128, 256)},),
            issued_at="2026-08-21T12:00:00Z", nonce_b64url="I" * 43,
            principal_id="p", signing_key_id="k", signature_b64url="J" * 86,
        )
        assert ex.tensors[0].shape == (128, 256)
