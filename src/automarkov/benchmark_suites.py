"""T20: 六个结构化 core suite 的冻结 benchmark manifests 与 task cards。

六 suites × 五 variants × 双 tracks × 六 methods × n_pair slots。
每个 suite 唯一冻结 source-access mode 与 required implementation route。
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from automarkov.canonical import FrozenSequence
from automarkov.domain import StrictFrozenModel
from automarkov.lifecycle import (
    ArtifactReference,
    NonEmptyId,
)

# ── Suite/variant/track 鉴别器 ──────────────────────────────────


SuiteId: type = Literal[
    "mab_cartpole",     # MDP — CartPole-v1
    "grid_taxi",        # MDP — Taxi-v3
    "nav_minigrid",     # POMDP — MiniGrid-DoorKey-8x8-v0
    "mpe2_spread",      # MG — MPE2 simple_spread_v3
    "starcraft_smacv2", # POSG — SMACv2
    "energy_citylearn", # POSG — CityLearn
]

VariantId: type = Literal[
    "v1_plain",
    "v2_reversed",
    "v3_reworded",
    "v4_ambiguous",
    "v5_clarification_required",
]

TrackId: type = Literal["AUTO", "HITL-ORACLE"]

MethodId: type = Literal[
    "automarkov",
    "automarkov_no_evidence",
    "a_lamp",
    "agent2",
    "agent2world",
    "react",
]

SourceAccess: type = Literal[
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
    """单个 benchmark cell 的绑定——suite × variant × track × method。"""

    suite_id: SuiteId
    variant_id: VariantId
    track_id: TrackId
    method_id: MethodId
    execution_status: Literal["RUN", "N/A", "DEFERRED"] = "RUN"
    reason: Annotated[str | None, Field(strict=True, max_length=1024)] = None
    task_card_artifact: ArtifactReference | None = None


class BenchmarkManifest(StrictFrozenModel):
    """冻结的六 suite benchmark manifest——完整 intention-to-run grid。"""

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


# ── 导出 ────────────────────────────────────────────────────────


__all__ = [
    "BenchmarkCellBinding",
    "BenchmarkManifest",
    "GoldScoreCalibration",
    "MethodId",
    "SourceAccess",
    "SuiteId",
    "TaskCard",
    "TrackId",
    "VariantId",
]
