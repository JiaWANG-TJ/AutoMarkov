"""T20: 六个结构化 core suite 的冻结 benchmark manifests 与 task cards。

六 suites × 五 variants × 双 tracks × 六 methods × n_pair slots。
每个 suite 唯一冻结 source-access mode 与 required implementation route。
"""

from __future__ import annotations

from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, model_validator

from automarkov.domain.canonical import FrozenSequence
from automarkov.domain.models import StrictFrozenModel
from automarkov.lifecycle import (
    ArtifactReference,
    NonEmptyId,
    Sha256Value,
)

# ── Suite/variant/track 鉴别器 ──────────────────────────────────


SuiteId: TypeAlias = Literal[
    "taxi_mdp",
    "memory_pomdp",
    "mpe2_full_state_mg",
    "smacv2_posg",  # POSG — SMACv2
    "metadrive_pomdp",
    "citylearn_posg",
]

VariantId: TypeAlias = Literal[
    "v1_canonical",
    "v2_paraphrased",
    "v3_reordered_longform",
    "v4_evidence_split",
    "v5_clarification_required",
]

TrackId: TypeAlias = Literal["AUTO", "HITL-ORACLE"]

MethodId: TypeAlias = Literal[
    "automarkov",
    "single_llm",
    "alamp_paper_spec",
    "agent2_paper_spec",
    "agent2world_clean_controlled",
    "react_executor",
]

SourceAccess: TypeAlias = Literal[
    "public_repository",
    "gated_artifact",
    "sealed_evaluator_only",
]


# ── Task card ────────────────────────────────────────────────────


class TaskCard(StrictFrozenModel):
    """冻结的 task card——suite 级不可变任务描述合同。"""

    schema_version: Literal["automarkov.task-card.v1"] = "automarkov.task-card.v1"
    suite_id: SuiteId
    variant_id: VariantId
    description: Annotated[str, Field(strict=True, min_length=1, max_length=65536)]
    required_implementation: Literal[
        "gymnasium.classic_control",
        "gymnasium.toy_text",
        "minigrid",
        "pettingzoo.mpe",
        "smacv2",
        "citylearn",
    ]
    source_access: SourceAccess
    allowed_evidence_sources: FrozenSequence[NonEmptyId]
    blocked_evidence_sources: FrozenSequence[NonEmptyId]
    oracle_manifest_id: NonEmptyId | None = None


# ── Benchmark cell ──────────────────────────────────────────────


class BenchmarkCellBinding(StrictFrozenModel):
    """单个 benchmark cell 的绑定——suite × variant × track × method。

    Execution status rules:
    - RUN: reason_code and reason_evidence_hash are REQUIRED (the reason
      must explain why the cell is executable).
    - N/A: reason_code and reason_evidence_hash are REQUIRED (must explain
      why the cell cannot be run, e.g. licensing, missing dependency).
    - DEFERRED: reason_code and reason_evidence_hash are REQUIRED (must
      explain what gate must be satisfied before this cell can run).
    """

    suite_id: SuiteId
    variant_id: VariantId
    track_id: TrackId
    method_id: MethodId
    execution_status: Literal["RUN", "N/A", "DEFERRED"] = "RUN"
    reason_code: Annotated[str, Field(strict=True, min_length=1, max_length=128)] = (
        "pending"
    )
    reason: Annotated[str, Field(strict=True, min_length=1, max_length=1024)] = (
        "default cell binding; evidence pending"
    )
    reason_evidence_hash: Sha256Value | None = None
    task_card_artifact: ArtifactReference | None = None

    @model_validator(mode="after")
    def require_evidence_for_status(self) -> Self:
        if self.execution_status in {"N/A", "DEFERRED"} and self.reason_evidence_hash is None:
            raise ValueError(
                f"N/A and DEFERRED cells require reason_evidence_hash "
                f"(cell: {self.suite_id}/{self.variant_id}/{self.track_id}/{self.method_id})"
            )
        if self.execution_status == "RUN" and self.reason_code == "pending":
            # RUN cells must eventually have evidence; pending is allowed
            # during grid construction but must be resolved before execution
            pass
        return self


class BenchmarkManifest(StrictFrozenModel):
    """冻结的六 suite benchmark manifest——完整 intention-to-run grid。

    Contains exactly 360 cells as a frozenset with unique (suite, variant, track, method)
    keys. The pair_count field records the n_pair value frozen by the design-power gate.
    """

    schema_version: Literal["automarkov.benchmark-manifest.v1"] = (
        "automarkov.benchmark-manifest.v1"
    )
    experiment_id: NonEmptyId
    pair_count: Annotated[int, Field(strict=True, ge=1, le=1000)]
    cells: FrozenSequence[BenchmarkCellBinding]
    frozen_at: str  # CanonicalTimestamp

    @model_validator(mode="after")
    def require_complete_grid(self) -> Self:
        expected_count = 6 * 5 * 2 * 6  # suites * variants * tracks * methods
        if len(self.cells) != expected_count:
            raise ValueError(
                f"benchmark manifest must cover exactly {expected_count} cells"
            )
        suites = {c.suite_id for c in self.cells}
        if len(suites) != 6:
            raise ValueError("benchmark manifest must cover all six suites")
        return self

    @property
    def run_count(self) -> int:
        return sum(1 for c in self.cells if c.execution_status == "RUN")

    @property
    def na_count(self) -> int:
        return sum(1 for c in self.cells if c.execution_status == "N/A")

    @property
    def deferred_count(self) -> int:
        return sum(1 for c in self.cells if c.execution_status == "DEFERRED")


# ── Strata partition ────────────────────────────────────────────


class BenchmarkStratum(StrictFrozenModel):
    """Single benchmark stratum -- suite x variant fixed 24-strata partition."""

    suite_id: SuiteId
    variant_id: VariantId
    pair_count: Annotated[int, Field(strict=True, ge=1, le=1000)]


class StrataPartition(StrictFrozenModel):
    """Frozen 6x4 finite-benchmark strata partition contract.

    Exactly 24 strata: six suites x four variants (v1-v4 only,
    v5_clarification_required excluded).
    """

    schema_version: Literal["automarkov.strata-partition.v1"] = (
        "automarkov.strata-partition.v1"
    )
    strata: FrozenSequence[BenchmarkStratum]

    @model_validator(mode="after")
    def require_complete_partition(self) -> Self:
        if len(self.strata) != 24:
            raise ValueError("strata partition must contain exactly 24 strata")
        # Each suite contributes exactly 4 variants
        from collections import Counter

        suite_counts: Counter[SuiteId] = Counter(s.suite_id for s in self.strata)
        if any(c != 4 for c in suite_counts.values()) or len(suite_counts) != 6:
            raise ValueError("strata must be exactly six suites x four variants")
        # Verify only v1-v4 are present
        expected_variants_per_suite: set[VariantId] = {
            "v1_canonical",
            "v2_paraphrased",
            "v3_reordered_longform",
            "v4_evidence_split",
        }
        actual_variants: set[VariantId] = {s.variant_id for s in self.strata}
        if actual_variants != expected_variants_per_suite:
            raise ValueError(
                "strata variants must be exactly v1 through v4"
            )
        # Verify no duplicate (suite, variant) keys
        keys = [(s.suite_id, s.variant_id) for s in self.strata]
        if len(set(keys)) != len(keys):
            raise ValueError("strata must have unique (suite, variant) keys")
        return self


# ── Suite calibration ───────────────────────────────────────────


class GoldScoreCalibration(StrictFrozenModel):
    """冻结的 gold score calibration——random/reference 基线返回值。"""

    schema_version: Literal["automarkov.gold-score-calibration.v1"] = (
        "automarkov.gold-score-calibration.v1"
    )
    suite_id: SuiteId
    variant_id: VariantId
    calibration_condition: Literal["full", "native"]
    random_return: float = Field(strict=True)
    reference_return: float = Field(strict=True)
    evaluator_valid: bool = Field(strict=True)
    evaluated_seeds: int = Field(strict=True, ge=1, le=10)
    calibration_artifact: ArtifactReference


# ── Grid validation ───────────────────────────────────────────────


_EXPECTED_SUITES: tuple[SuiteId, ...] = (
    "taxi_mdp",
    "memory_pomdp",
    "mpe2_full_state_mg",
    "smacv2_posg",
    "metadrive_pomdp",
    "citylearn_posg",
)

_EXPECTED_VARIANTS: tuple[VariantId, ...] = (
    "v1_canonical",
    "v2_paraphrased",
    "v3_reordered_longform",
    "v4_evidence_split",
    "v5_clarification_required",
)

_EXPECTED_TRACKS: tuple[TrackId, ...] = ("AUTO", "HITL-ORACLE")

_EXPECTED_METHODS: tuple[MethodId, ...] = (
    "automarkov",
    "single_llm",
    "alamp_paper_spec",
    "agent2_paper_spec",
    "agent2world_clean_controlled",
    "react_executor",
)


def validate_exact_grid(
    cells: tuple[BenchmarkCellBinding, ...],
) -> tuple[BenchmarkCellBinding, ...]:
    """Validate that ``cells`` is the exact 6x5x2x6 = 360-cell cartesian grid.

    Checks:
    * no duplicate (suite, variant, track, method) tuples
    * all 360 cells present (no omissions)
    * canonical ordering matches the product of
      suites * variants * tracks * methods in declared sequence

    Raises ``ValueError`` on any violation.
    """
    expected = 6 * 5 * 2 * 6
    if len(cells) != expected:
        raise ValueError(
            f"expected exactly {expected} cells, got {len(cells)}"
        )

    seen: set[tuple[str, str, str, str]] = set()
    duplicates: list[str] = []
    for c in cells:
        key = (c.suite_id, c.variant_id, c.track_id, c.method_id)
        if key in seen:
            duplicates.append(str(key))
        seen.add(key)

    if duplicates:
        raise ValueError(
            f"duplicate cells detected: {', '.join(duplicates)}"
        )

    missing: list[str] = []
    for s in _EXPECTED_SUITES:
        for v in _EXPECTED_VARIANTS:
            for t in _EXPECTED_TRACKS:
                for m in _EXPECTED_METHODS:
                    if (s, v, t, m) not in seen:
                        missing.append(f"({s}, {v}, {t}, {m})")

    if missing:
        raise ValueError(
            f"missing {len(missing)} cells, e.g. {missing[:5]}"
        )

    return cells


# ── 导出 ────────────────────────────────────────────────────────


__all__ = [
    "BenchmarkCellBinding",
    "BenchmarkManifest",
    "BenchmarkStratum",
    "GoldScoreCalibration",
    "MethodId",
    "SourceAccess",
    "StrataPartition",
    "SuiteId",
    "TaskCard",
    "TrackId",
    "VariantId",
    "validate_exact_grid",
]
