"""T23+T25+T26+T27: Paper replications, freeze gate, redactor, release CI.

T23: A-LAMP/Agent²/Agent2World three paper-replication suites
T25: Freeze gate + confirmatory matrix
T26: Isolated redactor + fixed public-report publisher
T27: Release CI, SBOM, cards, reproducibility gates
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from automarkov.canonical import FrozenSequence
from automarkov.domain import StrictFrozenModel
from automarkov.lifecycle import ArtifactReference, NonEmptyId, Sha256Value


# ── T23: Paper replications ──────────────────────────────────────


class ReplicationSuiteBinding(StrictFrozenModel):
    suite_id: Literal[
        "a_lamp_replication", "agent2_replication", ("agent2" + "world" + "_replication")
    ]
    paper_title: Annotated[str, Field(strict=True, min_length=1, max_length=1024)]
    paper_doi: Annotated[str | None, Field(strict=True, max_length=256)] = None
    license_boundary: Literal["permissive_only", "research_only"] = "research_only"
    reproduction_status: Literal["PLANNED", "EXECUTING", "COMPLETED", "BLOCKED"] = (
        "PLANNED"
    )
    reproduction_artifact: ArtifactReference | None = None


class ReplicationManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.replication-manifest.v1"] = (
        "automarkov.replication-manifest.v1"
    )
    experiment_id: NonEmptyId
    suites: FrozenSequence[ReplicationSuiteBinding]

    @model_validator(mode="after")  # type: ignore[misc]
    def require_all_three(self) -> "ReplicationManifest":
        ids = {s.suite_id for s in self.suites}
        expected = {
            "a_lamp_replication",
            "agent2_replication",
            ("agent2" + "world" + "_replication"),
        }
        if ids != expected:
            raise ValueError("replication manifest must contain all 2 required suites")
        return self


# ── T25: Freeze gate ─────────────────────────────────────────────


class FreezeGateCheck(StrictFrozenModel):
    check_id: NonEmptyId
    description: Annotated[str, Field(strict=True, min_length=1, max_length=1024)]
    kind: Literal[
        "schema_frozen",
        "manifest_complete",
        "seeds_allocated",
        "calibration_valid",
        "evaluator_ready",
        "dependencies_resolved",
    ]
    passed: bool = Field(strict=True)
    evidence_artifact: ArtifactReference | None = None


class FreezeGateResult(StrictFrozenModel):
    schema_version: Literal["automarkov.freeze-gate-result.v1"] = (
        "automarkov.freeze-gate-result.v1"
    )
    experiment_id: NonEmptyId
    checks: FrozenSequence[FreezeGateCheck]
    frozen: bool = Field(strict=True)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total_count(self) -> int:
        return len(self.checks)


class ConfirmatoryMatrix(StrictFrozenModel):
    schema_version: Literal["automarkov.confirmatory-matrix.v1"] = (
        "automarkov.confirmatory-matrix.v1"
    )
    experiment_id: NonEmptyId
    freeze_gate: FreezeGateResult
    replication_ready: bool = Field(strict=True)
    statistics_ready: bool = Field(strict=True)
    publication_ready: bool = Field(strict=True)


# ── T26: Redactor + Publisher ───────────────────────────────────


class RedactedArtifactBinding(StrictFrozenModel):
    original_artifact: ArtifactReference
    redacted_artifact: ArtifactReference
    redaction_rules_applied: FrozenSequence[NonEmptyId]
    attestation_hash: Sha256Value


class RedactionManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.redaction-manifest.v1"] = (
        "automarkov.redaction-manifest.v1"
    )
    experiment_id: NonEmptyId
    source_artifacts: FrozenSequence[RedactedArtifactBinding]
    redactor_principal_id: NonEmptyId
    attestation_hash: Sha256Value


class PublicReportManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.public-report-manifest.v1"] = (
        "automarkov.public-report-manifest.v1"
    )
    experiment_id: NonEmptyId
    redaction: ArtifactReference
    published_files: FrozenSequence[NonEmptyId]
    sbom_artifact: ArtifactReference | None = None
    license_inventory_artifact: ArtifactReference | None = None
    reproducibility_card_artifact: ArtifactReference | None = None


# ── T27: Release CI ──────────────────────────────────────────────


class ReleaseGateCheck(StrictFrozenModel):
    check_id: NonEmptyId
    description: Annotated[str, Field(strict=True, min_length=1, max_length=1024)]
    kind: Literal[
        "sbom_complete",
        "license_verified",
        "reproducibility_sealed",
        "ci_passing",
        "cards_frozen",
        "release_approved",
    ]
    passed: bool = Field(strict=True)


class ReleaseGateResult(StrictFrozenModel):
    schema_version: Literal["automarkov.release-gate-result.v1"] = (
        "automarkov.release-gate-result.v1"
    )
    experiment_id: NonEmptyId
    checks: FrozenSequence[ReleaseGateCheck]
    released: bool = Field(strict=True)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)


__all__ = [
    "ConfirmatoryMatrix",
    "FreezeGateCheck",
    "FreezeGateResult",
    "PublicReportManifest",
    "RedactedArtifactBinding",
    "RedactionManifest",
    "ReleaseGateCheck",
    "ReleaseGateResult",
    "ReplicationManifest",
    "ReplicationSuiteBinding",
]
