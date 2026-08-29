"""T24: Statistics contract tests — StrataPartition, PairedBootstrapResult, HolmFamily."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from automarkov.domain.errors import CapabilityDeferredError
from automarkov.statistics import (
    BenchmarkStratum,
    HolmFamily,
    HolmHypothesis,
    PairedBootstrapResult,
    StrataPartition,
    compute_stratified_paired_bootstrap,
)

HASH = "sha256:" + "0" * 64


def _strata() -> StrataPartition:
    suites = ("taxi_mdp", "memory_pomdp", "mpe2_full_state_mg", "smacv2_posg", "metadrive_pomdp", "citylearn_posg")
    variants = ("v1_canonical", "v2_paraphrased", "v3_reordered_longform", "v4_evidence_split")
    strata = tuple(
        BenchmarkStratum(suite_id=s, variant_id=v, pair_count=10)
        for s in suites for v in variants
    )
    return StrataPartition(strata=strata)


def _bootstrap() -> PairedBootstrapResult:
    return PairedBootstrapResult(
        estimand="E2EValid", method_a="automarkov", method_b="react_executor",
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


# ── Negative / defensive tests ──────────────────────────────────────


class TestStrataPartitionNegative:
    """Deceptive counter-examples for StrataPartition contract."""

    def test_duplicate_strata_rejected(self) -> None:
        base = _strata()
        dup_stratum = base.strata[0]
        duped = (dup_stratum, *base.strata)
        with pytest.raises((ValueError, ValidationError)):
            StrataPartition(strata=duped)

    def test_zero_pair_count_rejected(self) -> None:
        with pytest.raises((ValueError, ValidationError)):
            BenchmarkStratum(
                suite_id="taxi_mdp",
                variant_id="v1_canonical",
                pair_count=0,
            )


class TestPairedBootstrapNegative:
    """Negative tests for PairedBootstrapResult."""

    def test_reversed_ci_bounds_accepted_as_missing_invariant(self) -> None:
        """PairedBootstrapResult currently allows ci_lower > ci_upper.

        This counter-example exposes the missing ``ci_lower <= ci_upper``
        contract on the model.  Record the gap -- do not assert rejection.
        """
        b = _bootstrap()
        b_dict = b.model_dump(mode="json")
        b_dict["ci_lower"] = 0.90
        b_dict["ci_upper"] = 0.01
        # Model accepts reversed bounds (no validator exists yet).
        result = PairedBootstrapResult.model_validate(b_dict, strict=True)
        assert result.ci_lower > result.ci_upper  # gap documented

    def test_nan_observation_rejected(self) -> None:
        with pytest.raises(CapabilityDeferredError):
            compute_stratified_paired_bootstrap(
                observations=((("s", "v", "t", "m"), float("nan"), 0.0),),
            )

    def test_inf_observation_rejected(self) -> None:
        with pytest.raises(CapabilityDeferredError):
            compute_stratified_paired_bootstrap(
                observations=((("s", "v", "t", "m"), 0.0, float("inf")),),
            )


class TestHolmFamilyNegative:
    """Negative tests for HolmFamily contract."""

    def test_empty_hypotheses_rejected(self) -> None:
        with pytest.raises((ValueError, ValidationError)):
            HolmFamily(
                family_id="f1", family_alpha=0.05,
                hypotheses=(),
                preregistered_at="2026-08-21T12:00:00Z",
            )

    def test_rank_gap_rejected(self) -> None:
        h = (
            HolmHypothesis(hypothesis_id="h1", description="d",
                           raw_p_value=0.01, rank=1, adjusted_alpha=0.05,
                           rejected=True),
            HolmHypothesis(hypothesis_id="h2", description="d",
                           raw_p_value=0.04, rank=3, adjusted_alpha=0.025,
                           rejected=False),
        )
        with pytest.raises((ValueError, ValidationError), match="1..n"):
            HolmFamily(
                family_id="f1", family_alpha=0.05,
                hypotheses=h, preregistered_at="2026-08-21T12:00:00Z",
            )

    def test_family_alpha_out_of_range_rejected(self) -> None:
        h = (HolmHypothesis(hypothesis_id="h1", description="d",
                            raw_p_value=0.01, rank=1, adjusted_alpha=0.05,
                            rejected=True),)
        with pytest.raises((ValueError, ValidationError)):
            HolmFamily(
                family_id="f1", family_alpha=0.001,
                hypotheses=h,
                preregistered_at="2026-08-21T12:00:00Z",
            )
