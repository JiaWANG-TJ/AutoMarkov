"""T23+T25+T26+T27+R10: Replication, freeze gate, redactor, release CI tests."""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import ValidationError

from automarkov.freeze_gate import FreezeGateReport, PredicateVerdict
from automarkov.lifecycle import ArtifactReference
from automarkov.release_pipeline import (
    ConfirmatoryMatrix,
    FreezeGateCheck,
    FreezeGateResult,
    PublicReportBundle,
    RedactedArtifactBinding,
    RedactionAttestation,
    RedactionManifest,
    RedactionScanResult,
    ReleaseGateCheck,
    ReleaseGateResult,
    ReplicationManifest,
    ReplicationSuiteBinding,
    check_release_gate,
    publish_bundle,
    redact_bundle,
)

HASH = "sha256:" + "0" * 64
ART = "artifact_" + "0" * 64


def _make_freeze_report(*, is_frozen: bool) -> FreezeGateReport:
    """Build a minimal FreezeGateReport for release gate tests."""
    return FreezeGateReport(
        is_frozen=is_frozen,
        total_predicates=1,
        satisfied_count=1 if is_frozen else 0,
        missing_count=0 if is_frozen else 1,
        frozen_completeness=1.0 if is_frozen else 0.0,
        predicates=(
            PredicateVerdict(
                predicate_name="plan_closed",
                is_satisfied=is_frozen,
                detail="ok" if is_frozen else "missing",
            ),
        ),
        missing_fields=() if is_frozen else ("plan_closed",),
        blocking_reasons=() if is_frozen else ("missing plan_closed",),
    )


def _freeze_checks(*, passed: bool = True) -> tuple[FreezeGateCheck, ...]:
    kinds: tuple[
        Literal[
            "schema_frozen",
            "manifest_complete",
            "seeds_allocated",
            "calibration_valid",
            "evaluator_ready",
            "dependencies_resolved",
        ],
        ...,
    ] = (
        "schema_frozen",
        "manifest_complete",
        "seeds_allocated",
        "calibration_valid",
        "evaluator_ready",
        "dependencies_resolved",
    )
    return tuple(
        FreezeGateCheck(
            check_id=f"check_{kind}",
            description=kind,
            kind=kind,
            passed=passed,
        )
        for kind in kinds
    )


# ── T23: Paper replications ──────────────────────────────────


class TestReplicationManifest:
    def test_requires_two_suites(self) -> None:
        s = (
            ReplicationSuiteBinding(
                suite_id="a_lamp_replication",
                paper_title="A-LAMP Replication",
                reproduction_status="PLANNED",
            ),
            ReplicationSuiteBinding(
                suite_id="agent2_replication",
                paper_title="Agent² Replication",
                reproduction_status="PLANNED",
            ),
        )
        m = ReplicationManifest(experiment_id="expt23", suites=s)
        assert len(m.suites) == 2

    def test_rejects_incomplete_suites(self) -> None:
        s = (
            ReplicationSuiteBinding(
                suite_id="a_lamp_replication",
                paper_title="A",
                reproduction_status="PLANNED",
            ),
        )
        with pytest.raises(ValidationError, match="all 2"):
            ReplicationManifest(experiment_id="expt23", suites=s)


# ── T25: Freeze gate ──────────────────────────────────────


class TestFreezeGate:
    def test_all_passed_is_frozen(self) -> None:
        g = FreezeGateResult(
            experiment_id="expt25",
            checks=_freeze_checks(),
            frozen=True,
        )
        assert g.passed_count == 6 and g.frozen

    def test_confirmatory_matrix(self) -> None:
        g = FreezeGateResult(
            experiment_id="expt25",
            checks=_freeze_checks(),
            frozen=True,
        )
        c = ConfirmatoryMatrix(
            experiment_id="expt25",
            freeze_gate=g,
            replication_ready=True,
            statistics_ready=True,
            publication_ready=True,
        )
        assert c.publication_ready


# ── T26: Redaction (R10 deepened) ──────────────────────────


class TestRedactionScanResult:
    def test_scan_result_model(self) -> None:
        r = RedactionScanResult(
            field_path="test_field",
            policy="high_entropy",
            passed=True,
            detail="clean",
        )
        assert r.passed
        assert r.schema_version == "automarkov.redaction-scan-result.v1"

    def test_scan_result_rejects_empty_policy(self) -> None:
        with pytest.raises(ValidationError):
            RedactionScanResult(
                field_path="x",
                policy="",
                passed=True,
                detail="d",
            )


class TestRedactionAttestation:
    def test_attestation_model(self) -> None:
        a = RedactionAttestation(
            experiment_id="expt26",
            bundle_hash=HASH,
            redaction_checks=(
                RedactionScanResult(
                    field_path="f",
                    policy="p",
                    passed=True,
                    detail="d",
                ),
            ),
            attestation_hash=HASH,
        )
        assert a.schema_version == "automarkov.redaction-attestation.v1"

    def test_attestation_rejects_empty_checks(self) -> None:
        with pytest.raises(ValidationError):
            RedactionAttestation(
                experiment_id="expt26",
                bundle_hash=HASH,
                redaction_checks=(),
                attestation_hash=HASH,
            )


class TestRedactBundle:
    def test_returns_two_dicts(self) -> None:
        bundle = {
            "experiment_id": "expt_test",
            "confirmatory_report_path": "confirmatory_report.md",
            "tables": {"t1": "tables/a.csv"},
        }
        manifest, attestation = redact_bundle(bundle)
        assert isinstance(manifest, dict)
        assert isinstance(attestation, dict)

    def test_manifest_has_experiment_id(self) -> None:
        bundle = {
            "experiment_id": "expt_r10",
        }
        manifest, attestation = redact_bundle(bundle)
        assert manifest["experiment_id"] == "expt_r10"
        assert attestation["experiment_id"] == "expt_r10"

    def test_deterministic(self) -> None:
        bundle = {
            "experiment_id": "expt_det",
            "nested": {"a": 1, "b": 2},
        }
        m1, a1 = redact_bundle(bundle)
        m2, a2 = redact_bundle(bundle)
        assert m1 == m2
        assert a1 == a2

    def test_sealed_key_rejected(self) -> None:
        bundle = {
            "experiment_id": "expt_seal",
            "nonce": "should_not_be_here",
        }
        _, attestation = redact_bundle(bundle)
        checks = attestation["redaction_checks"]
        sealed = [
            c for c in checks if c["policy"] == "sealed_identity"
        ]
        assert len(sealed) >= 1
        assert any(not c["passed"] for c in sealed)

    def test_file_allowlist_enforced(self) -> None:
        bundle = {
            "experiment_id": "expt_file",
            "bad_file.py": b"content",
        }
        _, attestation = redact_bundle(bundle)
        checks = attestation["redaction_checks"]
        allowlist = [
            c for c in checks if c["policy"] == "file_allowlist"
        ]
        assert any(not c["passed"] for c in allowlist)

    def test_public_file_allowed(self) -> None:
        bundle = {
            "experiment_id": "expt_public",
            "confirmatory_report.md": b"# report",
        }
        _, attestation = redact_bundle(bundle)
        checks = attestation["redaction_checks"]
        allowlist = [
            c for c in checks if c["policy"] == "file_allowlist"
        ]
        assert all(c["passed"] for c in allowlist)

    def test_csv_table_allowed(self) -> None:
        bundle = {
            "experiment_id": "expt_csv",
            "tables": {"data": "tables/results.csv"},
        }
        _, attestation = redact_bundle(bundle)
        assert attestation["experiment_id"] == "expt_csv"

    def test_symlink_rejected(self) -> None:
        bundle = {
            "experiment_id": "expt_sym",
            ".hidden": b"bad",
        }
        _, attestation = redact_bundle(bundle)
        checks = attestation["redaction_checks"]
        symlink = [
            c for c in checks if c["policy"] == "file_symlink"
        ]
        assert any(not c["passed"] for c in symlink)

    def test_bundle_hash_present(self) -> None:
        bundle = {"experiment_id": "expt_hash"}
        _, attestation = redact_bundle(bundle)
        assert "bundle_hash" in attestation
        assert attestation["bundle_hash"].startswith("sha256:")

    def test_attestation_hash_present(self) -> None:
        bundle = {"experiment_id": "expt_ahash"}
        _, attestation = redact_bundle(bundle)
        assert "attestation_hash" in attestation
        assert attestation["attestation_hash"].startswith("sha256:")


# ── T26: Redacted Manifest ────────────────────────────────────


class TestRedactedBinding:
    def test_redaction_manifest(self) -> None:
        r = RedactedArtifactBinding(
            original_artifact=ArtifactReference(
                artifact_id=ART, payload_hash=HASH
            ),
            redacted_artifact=ArtifactReference(
                artifact_id=ART, payload_hash=HASH
            ),
            redaction_rules_applied=("strip_credentials",),
            attestation_hash=HASH,
        )
        m = RedactionManifest(
            experiment_id="expt26",
            source_artifacts=(r,),
            redactor_principal_id="redactor01",
            attestation_hash=HASH,
        )
        assert len(m.source_artifacts) == 1


# ── T26: Publisher (R10 deepened) ──────────────────────────────


class TestPublishBundle:
    def test_returns_dict(self) -> None:
        manifest = {
            "experiment_id": "expt_pub",
            "source_artifacts": [],
        }
        attestation = {
            "attestation_hash": HASH,
            "all_passed": True,
            "redaction_checks": [],
        }
        result = publish_bundle(manifest, attestation)
        assert isinstance(result, dict)

    def test_bundle_hash_present(self) -> None:
        manifest = {
            "experiment_id": "expt_bh",
            "source_artifacts": [],
        }
        attestation = {
            "attestation_hash": HASH,
            "all_passed": True,
            "redaction_checks": [],
        }
        result = publish_bundle(manifest, attestation)
        assert "bundle_hash" in result
        assert result["bundle_hash"].startswith("sha256:")

    def test_sbom_entries_present(self) -> None:
        manifest = {
            "experiment_id": "expt_sbom",
            "source_artifacts": [],
        }
        attestation = {
            "attestation_hash": HASH,
            "all_passed": True,
            "redaction_checks": [],
        }
        result = publish_bundle(manifest, attestation)
        assert "sbom_entries" in result
        assert isinstance(result["sbom_entries"], list)

    def test_file_verification_present(self) -> None:
        manifest = {
            "experiment_id": "expt_fv",
            "source_artifacts": [],
        }
        attestation = {
            "attestation_hash": HASH,
            "all_passed": True,
            "redaction_checks": [],
        }
        result = publish_bundle(manifest, attestation)
        assert "file_verification" in result
        assert result["file_verification"] is False

    def test_deterministic(self) -> None:
        manifest = {
            "experiment_id": "expt_det_pub",
            "source_artifacts": [],
        }
        attestation = {
            "attestation_hash": HASH,
            "all_passed": False,
            "redaction_checks": [],
        }
        r1 = publish_bundle(manifest, attestation)
        r2 = publish_bundle(manifest, attestation)
        assert r1 == r2


class TestPublicReportBundle:
    def test_bundle_model(self) -> None:
        b = PublicReportBundle(
            experiment_id="expt26",
            redaction_manifest_hash=HASH,
            redaction_attestation_hash=HASH,
            published_files=(),
            file_hashes=(),
            sbom=(),
        )
        assert b.schema_version == "automarkov.public-report-bundle.v1"


# ── T27: Release gate (R10 deepened) ──────────────────────────


class TestReleaseGate:
    def test_all_checks_passed_is_released(self) -> None:
        checks = (
            ReleaseGateCheck(
                check_id="c0",
                description="check 0",
                kind="sbom_complete",
                passed=True,
            ),
            ReleaseGateCheck(
                check_id="c1",
                description="check 1",
                kind="license_verified",
                passed=True,
            ),
            ReleaseGateCheck(
                check_id="c2",
                description="check 2",
                kind="reproducibility_sealed",
                passed=True,
            ),
            ReleaseGateCheck(
                check_id="c3",
                description="check 3",
                kind="ci_passing",
                passed=True,
            ),
            ReleaseGateCheck(
                check_id="c4",
                description="check 4",
                kind="cards_frozen",
                passed=True,
            ),
            ReleaseGateCheck(
                check_id="c5",
                description="check 5",
                kind="release_approved",
                passed=True,
            ),
        )
        r = ReleaseGateResult(
            experiment_id="expt27",
            checks=checks,
            released=True,
        )
        assert r.passed_count == 6 and r.released


class TestCheckReleaseGate:
    """R10: release gate decision from CHECKS, not from released input."""

    def test_untyped_mapping_evidence_cannot_release(self) -> None:
        gate = _make_freeze_report(is_frozen=True)
        result = check_release_gate(
            freeze_gate=gate,
            redacted_manifest={
                "experiment_id": "expt_r10_ok",
                "attestation_hash": HASH,
            },
            redaction_attestation={
                "all_passed": True,
                "attestation_hash": HASH,
            },
            source_attestation={
                "source_hash": HASH,
            },
            bundle_schema_valid=True,
            owner_approval=True,
        )
        assert not result.released
        assert result.passed_count == 4

    def test_not_released_when_freeze_fails(self) -> None:
        gate = _make_freeze_report(is_frozen=False)
        result = check_release_gate(
            freeze_gate=gate,
            redacted_manifest={"experiment_id": "e", "attestation_hash": HASH},
            redaction_attestation={"all_passed": True, "attestation_hash": HASH},
            source_attestation={"source_hash": HASH},
            bundle_schema_valid=True,
            owner_approval=True,
        )
        assert not result.released

    def test_not_released_when_redaction_fails(self) -> None:
        gate = _make_freeze_report(is_frozen=True)
        result = check_release_gate(
            freeze_gate=gate,
            redacted_manifest={"experiment_id": "e", "attestation_hash": HASH},
            redaction_attestation={"all_passed": False, "attestation_hash": HASH},
            source_attestation={"source_hash": HASH},
            bundle_schema_valid=True,
            owner_approval=True,
        )
        assert not result.released

    def test_not_released_when_owner_approval_missing(self) -> None:
        gate = _make_freeze_report(is_frozen=True)
        result = check_release_gate(
            freeze_gate=gate,
            redacted_manifest={"experiment_id": "e", "attestation_hash": HASH},
            redaction_attestation={"all_passed": True, "attestation_hash": HASH},
            source_attestation={"source_hash": HASH},
            bundle_schema_valid=True,
            owner_approval=False,
        )
        assert not result.released

    def test_not_released_when_source_attestation_invalid(self) -> None:
        gate = _make_freeze_report(is_frozen=True)
        result = check_release_gate(
            freeze_gate=gate,
            redacted_manifest={"experiment_id": "e", "attestation_hash": HASH},
            redaction_attestation={"all_passed": True, "attestation_hash": HASH},
            source_attestation={"source_hash": ""},
            bundle_schema_valid=True,
            owner_approval=True,
        )
        assert not result.released

    def test_six_checks_count(self) -> None:
        gate = _make_freeze_report(is_frozen=False)
        result = check_release_gate(
            freeze_gate=gate,
            redacted_manifest={"experiment_id": "e", "attestation_hash": ""},
            redaction_attestation={"all_passed": False, "attestation_hash": ""},
            source_attestation={"source_hash": ""},
            bundle_schema_valid=False,
            owner_approval=False,
        )
        assert result.total_count == 6
        assert result.passed_count == 0


# ── Negative / defensive tests ──────────────────────────────────────


class TestFreezeGateNegative:
    """Deceptive counter-examples for FreezeGateResult contract."""

    def test_zero_checks_but_frozen(self) -> None:
        with pytest.raises(ValidationError, match="closed check set"):
            FreezeGateResult(experiment_id="expt25", checks=(), frozen=True)

    def test_unfrozen_with_passing_checks(self) -> None:
        checks = (
            FreezeGateCheck(check_id="c1", description="d",
                             kind="schema_frozen", passed=True),
        )
        with pytest.raises(ValidationError, match="closed check set"):
            FreezeGateResult(experiment_id="expt25", checks=checks, frozen=False)


class TestReleaseGateNegative:
    """Deceptive counter-examples for ReleaseGateResult contract."""

    def test_zero_checks_but_released(self) -> None:
        with pytest.raises(ValidationError, match="closed check set"):
            ReleaseGateResult(
                experiment_id="expt27", checks=(), released=True,
            )

    def test_all_failed_checks_but_released(self) -> None:
        checks = (
            ReleaseGateCheck(check_id="c0", description="d",
                              kind="sbom_complete", passed=False),
        )
        with pytest.raises(ValidationError, match="closed check set"):
            ReleaseGateResult(
                experiment_id="expt27", checks=checks, released=True,
            )

    def test_confirmatory_matrix_rejects_contradictory_freeze_gate(self) -> None:
        with pytest.raises(ValidationError, match="closed check set"):
            FreezeGateResult(
                experiment_id="expt25",
                checks=(
                    FreezeGateCheck(
                        check_id="c1",
                        description="d",
                        kind="schema_frozen",
                        passed=False,
                    ),
                ),
                frozen=True,
            )
