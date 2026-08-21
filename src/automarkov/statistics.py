"""T24: 确定性嵌套统计、冻结 Holm families 与 paired stratified-bootstrap。

固定 6×4 finite-benchmark strata，cell 内保留 generation pair→RL seed 配对。
ReAct co-primary 由 paired stratified-bootstrap bounds 判定。
策略非劣界标准化分数 0.05，单侧 97.5% CI。
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from automarkov.canonical import FrozenSequence
from automarkov.domain import StrictFrozenModel
from automarkov.lifecycle import NonEmptyId
from automarkov.benchmark_suites import SuiteId, VariantId, MethodId


# ── Stratum definition ──────────────────────────────────────────


class BenchmarkStratum(StrictFrozenModel):
    """单个基准分层——suite × variant 的固定 24-strata 划分。"""

    suite_id: SuiteId
    variant_id: VariantId
    pair_count: Annotated[int, Field(strict=True, ge=1, le=1000)]


class StrataPartition(StrictFrozenModel):
    """冻结的 6×4 finite-benchmark strata 分区合同。"""

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
        suite_counts = Counter(s.suite_id for s in self.strata)
        if any(c != 4 for c in suite_counts.values()) or len(suite_counts) != 6:
            raise ValueError("strata must be exactly six suites × four variants")
        return self


# ── Paired bootstrap result ─────────────────────────────────────


class PairedBootstrapResult(StrictFrozenModel):
    """单次 paired stratified-bootstrap 的不可变结论。"""

    schema_version: Literal["automarkov.paired-bootstrap-result.v1"] = (
        "automarkov.paired-bootstrap-result.v1"
    )
    estimand: Literal["E2EValid", "Q_gate"]
    method_a: MethodId
    method_b: MethodId
    strata: StrataPartition
    replicates: Annotated[int, Field(strict=True, ge=1_000, le=1_000_000)]
    point_estimate: float = Field(strict=True)
    bias_corrected_estimate: float = Field(strict=True)
    ci_lower: float = Field(strict=True)
    ci_upper: float = Field(strict=True)
    ci_level: float = Field(strict=True, ge=0.90, le=0.999)
    p_value_two_sided: float = Field(strict=True, ge=0.0, le=1.0)
    non_inferiority_margin: float = Field(strict=True, default=0.05)
    non_inferior: bool = Field(strict=True)


# ── Holm family ─────────────────────────────────────────────────


class HolmHypothesis(StrictFrozenModel):
    """单条 Holm-corrected 假设的冻结声明。"""

    hypothesis_id: NonEmptyId
    description: Annotated[str, Field(strict=True, min_length=1, max_length=1024)]
    raw_p_value: float = Field(strict=True, ge=0.0, le=1.0)
    rank: int = Field(strict=True, ge=1, le=256)
    adjusted_alpha: float = Field(strict=True, ge=0.0, le=1.0)
    rejected: bool = Field(strict=True)


class HolmFamily(StrictFrozenModel):
    """冻结的 Holm-Bonferroni family——预注册假设集的不可变校正结果。"""

    schema_version: Literal["automarkov.holm-family.v1"] = "automarkov.holm-family.v1"
    family_id: NonEmptyId
    family_alpha: float = Field(strict=True, ge=0.01, le=0.10)
    hypotheses: FrozenSequence[HolmHypothesis]
    preregistered_at: str  # CanonicalTimestamp

    @model_validator(mode="after")
    def require_correct_adjustment(self) -> Self:
        if not self.hypotheses:
            raise ValueError("Holm family must contain at least one hypothesis")
        ranks = [h.rank for h in self.hypotheses]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("Holm hypothesis ranks must be 1..n")
        return self

    @property
    def rejected_count(self) -> int:
        return sum(1 for h in self.hypotheses if h.rejected)


# ── 导出 ────────────────────────────────────────────────────────


__all__ = [
    "BenchmarkStratum",
    "HolmFamily",
    "HolmHypothesis",
    "PairedBootstrapResult",
    "StrataPartition",
]
