"""T23+T25+T26+T27: Replication, freeze gate, redactor, release CI tests."""

import pytest
from pydantic import ValidationError
from automarkov.release_pipeline import (
    ConfirmatoryMatrix, FreezeGateCheck, FreezeGateResult,
    RedactedArtifactBinding, RedactionManifest, ReleaseGateCheck, ReleaseGateResult,
    ReplicationManifest, ReplicationSuiteBinding, PublicReportManifest,
)

HASH = "sha256:" + "0" * 64
ART = "artifact_" + "0" * 64


class TestReplicationManifest:
    def test_requires_two_suites(self) -> None:
        s = (ReplicationSuiteBinding(
            suite_id="a_lamp_replication", paper_title="A-LAMP Replication",
            reproduction_status="PLANNED",
        ), ReplicationSuiteBinding(
            suite_id="agent2_replication", paper_title="Agent² Replication",
            reproduction_status="PLANNED",
        ))  # agent²world SFT deferred
        m = ReplicationManifest(experiment_id="expt23", suites=s)
        assert len(m.suites) == 3

    def test_rejects_incomplete_suites(self) -> None:
        s = (ReplicationSuiteBinding(suite_id="a_lamp_replication", paper_title="A", reproduction_status="PLANNED"),)
        with pytest.raises(ValidationError, match="all 2"):
            ReplicationManifest(experiment_id="expt23", suites=s)


class TestFreezeGate:
    def test_all_passed_is_frozen(self) -> None:
        checks = (FreezeGateCheck(check_id="c1", description="schema", kind="schema_frozen", passed=True),
                  FreezeGateCheck(check_id="c2", description="manifest", kind="manifest_complete", passed=True))
        g = FreezeGateResult(experiment_id="expt25", checks=checks, frozen=True)
        assert g.passed_count == 2 and g.frozen

    def test_confirmatory_matrix(self) -> None:
        g = FreezeGateResult(experiment_id="expt25", checks=(
            FreezeGateCheck(check_id="c1", description="d", kind="schema_frozen", passed=True),),
            frozen=True)
        c = ConfirmatoryMatrix(experiment_id="expt25", freeze_gate=g,
                               replication_ready=True, statistics_ready=True, publication_ready=True)
        assert c.publication_ready


class TestRedactor:
    def test_redaction_manifest(self) -> None:
        r = RedactedArtifactBinding(
            original_artifact={"artifact_id": ART, "payload_hash": HASH},
            redacted_artifact={"artifact_id": ART, "payload_hash": HASH},
            redaction_rules_applied=("strip_credentials",), attestation_hash=HASH,
        )
        m = RedactionManifest(experiment_id="expt26", source_artifacts=(r,),
                              redactor_principal_id="redactor01", attestation_hash=HASH)
        assert len(m.source_artifacts) == 1


class TestReleaseGate:
    def test_all_checks_passed_is_released(self) -> None:
        checks = tuple(ReleaseGateCheck(check_id=f"c{i}", description=f"check {i}",
                                        kind=k, passed=True)
                       for i, k in enumerate(["sbom_complete", "license_verified",
                                             "reproducibility_sealed", "ci_passing",
                                             "cards_frozen", "release_approved"]))
        r = ReleaseGateResult(experiment_id="expt27", checks=checks, released=True)
        assert r.passed_count == 6 and r.released
