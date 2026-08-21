"""T24: Statistics contract tests — StrataPartition, PairedBootstrapResult, HolmFamily."""

from __future__ import annotations
import pytest
from pydantic import ValidationError
from automarkov.statistics import (
    BenchmarkStratum, HolmFamily, HolmHypothesis, PairedBootstrapResult, StrataPartition,
)

HASH = "sha256:" + "0" * 64


def _strata() -> StrataPartition:
    suites = ("mab_cartpole", "grid_taxi", "nav_minigrid", "mpe2_spread", "starcraft_smacv2", "energy_citylearn")
    variants = ("v1_plain", "v2_reversed", "v3_reworded", "v4_ambiguous")
    strata = tuple(
        BenchmarkStratum(suite_id=s, variant_id=v, pair_count=10)
        for s in suites for v in variants
    )
    return StrataPartition(strata=strata)


def _bootstrap() -> PairedBootstrapResult:
    return PairedBootstrapResult(
        estimand="E2EValid", method_a="automarkov", method_b="react",
        strata=_strata(), replicates=100_000,
        point_estimate=0.15, bias_corrected_estimate=0.14,
        ci_lower=0.05, ci_upper=0.25, ci_level=0.975,
        p_value_two_sided=0.003, non_inferior=True,
    )


class TestStrataPartition:
    def test_accepts_complete_24(self) -> None:
        s = _strata()
        assert len(s.strata) == 24

    def test_rejects_incomplete(self) -> None:
        with pytest.raises(ValidationError, match="exactly"):
            StrataPartition(strata=_strata().strata[:10])


class TestPairedBootstrapResult:
    def test_accepts_significant(self) -> None:
        b = _bootstrap()
        assert b.non_inferior and b.p_value_two_sided < 0.05


class TestHolmFamily:
    def test_accepts_valid_family(self) -> None:
        hypotheses = (
            HolmHypothesis(hypothesis_id="h1", description="MPE2 info structure",
                           raw_p_value=0.003, rank=1, adjusted_alpha=0.025, rejected=True),
            HolmHypothesis(hypothesis_id="h2", description="CityLearn CTDE",
                           raw_p_value=0.040, rank=2, adjusted_alpha=0.025, rejected=False),
        )
        f = HolmFamily(
            family_id="ablation_family_1", family_alpha=0.05,
            hypotheses=hypotheses, preregistered_at="2026-08-21T12:00:00Z",
        )
        assert f.rejected_count == 1

    def test_round_trips(self) -> None:
        h = (HolmHypothesis(hypothesis_id="h1", description="test",
                            raw_p_value=0.01, rank=1, adjusted_alpha=0.05, rejected=True),)
        f = HolmFamily(family_id="f1", family_alpha=0.05, hypotheses=h,
                       preregistered_at="2026-08-21T12:00:00Z")
        r = HolmFamily.model_validate(f.model_dump(mode="json"), strict=True)
        assert r.rejected_count == 1
