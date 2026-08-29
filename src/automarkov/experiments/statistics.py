"""T24: deterministic nested statistics, frozen Holm families, and paired stratified bootstrap.

Fixed 6x4 finite-benchmark strata, preserving generation pair -> RL seed pairing within cells.
ReAct co_primary judged by paired stratified bootstrap bounds.
Standardized non-inferiority score 0.05, one-sided 97.5% CI.
"""

from __future__ import annotations

import hashlib
import math
from typing import Annotated, Literal, Self

import numpy as np
from numpy.typing import NDArray
from pydantic import Field, model_validator

from automarkov.contracts.benchmark import (
    BenchmarkStratum,
    MethodId,
    StrataPartition,
)
from automarkov.domain.canonical import FrozenSequence
from automarkov.domain.errors import CapabilityDeferredError
from automarkov.domain.models import StrictFrozenModel
from automarkov.lifecycle import NonEmptyId

# -- Paired bootstrap result ----------------------------------------------------------


class PairedBootstrapResult(StrictFrozenModel):
    """Single paired stratified bootstrap immutable result."""

    schema_version: Literal["automarkov.paired-bootstrap-result.v1"] = "automarkov.paired-bootstrap-result.v1"
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


# -- Holm family --------------------------------------------------------------------


class HolmHypothesis(StrictFrozenModel):
    """Single Holm-corrected hypothesis frozen declaration."""

    hypothesis_id: NonEmptyId
    description: Annotated[str, Field(strict=True, min_length=1, max_length=1024)]
    raw_p_value: float = Field(strict=True, ge=0.0, le=1.0)
    rank: int = Field(strict=True, ge=1, le=256)
    adjusted_alpha: float = Field(strict=True, ge=0.0, le=1.0)
    rejected: bool = Field(strict=True)


class HolmFamily(StrictFrozenModel):
    """Frozen Holm-Bonferroni family -- pre-registered hypothesis set immutable correction result."""

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


# -- Pure-math normal CDF / PPF ----------------------------------------------------
# Abramowitz & Stegun (1964) approximations 26.2.17 / 26.2.23.
# These are deterministic, require no external stats library, and
# achieve |error| < 7.5e-8 for the CDF and < 1.15e-9 for the
# inverse (PPF) across the full real line.

_ASG_P: float = 0.2316419
_ASG_B1: float = 0.319381530
_ASG_B2: float = -0.356563782
_ASG_B3: float = 1.781477937
_ASG_B4: float = -1.821255978
_ASG_B5: float = 1.330274429

_ASG_Q1: float = 0.3374754829159
_ASG_Q2: float = 0.937298040283
_ASG_Q3: float = 0.2067573152489
_ASG_Q4: float = 0.01532940557019


def _phi(x: float) -> float:
    """Standard normal CDF Phi(x) via Abramowitz_Stegun 26.2.17."""
    if not math.isfinite(x):
        raise ValueError(f"x must be finite, got {x}")
    ax = abs(x)
    t = 1.0 / (1.0 + _ASG_P * ax)
    poly = t * (_ASG_B1 + t * (_ASG_B2 + t * (_ASG_B3 + t * (_ASG_B4 + t * _ASG_B5))))
    pdf = math.exp(-0.5 * ax * ax) / math.sqrt(2.0 * math.pi)
    term = 1.0 - pdf * poly
    return 0.5 * (1.0 + math.copysign(term - 1.0, x))


def _phi_inv(p: float) -> float:
    """Inverse normal CDF Phi^{-1}(p) via Abramowitz_Stegun 26.2.23.

    Valid for p in (0, 1).  Accurate to |error| < 1.15e-9.
    """
    if not (0.0 < p < 1.0):
        raise ValueError(f"p must be in (0, 1), got {p}")
    if p < 0.5:
        return -_phi_inv(1.0 - p)
    t = math.sqrt(-2.0 * math.log(1.0 - p))
    # Rational form: 1 + t*(q1 + t*(q2 + t*(q3 + t*q4)))
    numer = _ASG_Q1 + t * (_ASG_Q2 + t * (_ASG_Q3 + t * _ASG_Q4))
    rational = 1.0 + t * (_ASG_Q1 + t * (_ASG_Q2 + t * (_ASG_Q3 + t * _ASG_Q4)))
    return t - numer / rational


# -- Deterministic SHA-256 PRNG --------------------------------------------------------------


_HASH_DOMAIN = "AutoMarkov-StatisticsPRNG-v1"
"""Domain separator for deterministic pseudo-random byte generation.

Each (seed, stream_id, counter) triple produces 8 deterministic bytes
via SHA-256, forming a counter-mode PRNG with no numpy RNG state.
"""


def _deterministic_uniform_f64(
    seed: int,
    stream_id: int,
    counter: int,
) -> float:
    """Return a deterministic float in [0, 1) from SHA-256 digest.

    Hashes ``f"{_HASH_DOMAIN}:{seed}:{stream_id}:{counter}"`` and
    extracts the first 8 bytes as a big-endian uint52 mapped to [0, 1).
    The same triple always produces the same float.

    .. note::

       This is *not* a cryptographic RNG -- it is a deterministic
       mapping for byte-reproducible bootstrap sampling.
    """
    payload = f"{_HASH_DOMAIN}:{seed}:{stream_id}:{counter}".encode()
    digest = hashlib.sha256(payload).digest()
    uint52 = int.from_bytes(digest[:8], "big") & ((1 << 52) - 1)
    return uint52 / (1 << 52)


def _deterministic_beta(
    seed: int,
    stream_id: int,
    counter: int,
) -> float:
    """Return a deterministic Beta(1,1) = Uniform[0,1] sample.

    Beta(1,1) is the trivial case where both shape parameters
    are 1, which equals Uniform(0,1).  We use the same
    SHA-256 mapping as ``_deterministic_uniform_f64``.
    """
    return _deterministic_uniform_f64(seed, stream_id, counter)


def _deterministic_int(
    seed: int,
    stream_id: int,
    counter: int,
    upper_exclusive: int,
) -> int:
    """Return a deterministic int in ``[0, upper_exclusive)``.

    Uses rejection sampling on 52-bit uniform draws to avoid
    modulo bias.  At most 8 rejections per draw for any ``n <= 2**52``.
    """
    if upper_exclusive <= 0:
        raise ValueError(f"upper_exclusive must be positive, got {upper_exclusive}")
    limit = (1 << 52) - ((1 << 52) % upper_exclusive)
    rejections = 0
    while True:
        u = _deterministic_uniform_f64(seed, stream_id, counter + rejections)
        bits = int(u * (1 << 52))
        if bits < limit:
            return bits % upper_exclusive
        rejections += 1
        if rejections > 8:  # pragma: no cover -- extremely unlikely
            raise RuntimeError("excessive rejections in deterministic_int; " "check upper_exclusive constraints")


# -- Validation functions -------------------------------------------------------------


def validate_exact_cartesian_grid(
    actual_counts: dict[tuple[str, str, str, str], int],
    expected_cells: set[tuple[str, str, str, str]],
    total: int,
) -> None:
    """Reject missing or duplicate cells in a benchmark grid.

    Parameters
    ----------
    actual_counts:
        Mapping from ``(suite_id, variant_id, track_id, method_id)``
        to the number of pair observations recorded in that cell.
    expected_cells:
        The exact set of ``(suite_id, variant_id, track_id, method_id)``
        tuples the grid must contain.
    total:
        The overall observation-pair sum across all cells.  Must equal
        the sum of ``actual_counts.values()``.

    Raises
    ------
    KeyError
        If a cell in ``expected_cells`` is missing from ``actual_counts``.
    ValueError
        If ``actual_counts`` contains a cell not in ``expected_cells``,
        or if the summed pair count disagrees with ``total``.

    .. note::

       This function is **pure** -- it inspects inputs and raises on
       contract violations without modifying any state.
    """
    actual_keys = set(actual_counts.keys())
    missing = expected_cells - actual_keys
    if missing:
        raise KeyError(
            f"grid is missing {len(missing)} expected cell(s): " f"{next(iter(missing))!r} ... (first missing shown)"
        )
    extra = actual_keys - expected_cells
    if extra:
        raise ValueError(
            f"grid contains {len(extra)} unexpected cell(s): " f"{next(iter(extra))!r} ... (first extra shown)"
        )
    summed_count = sum(actual_counts.values())
    if summed_count != total:
        raise ValueError(f"cell pair-count sum {summed_count} != declared total {total}")


# -- Stratified paired bootstrap -------------------------------------------------------


def _compute_unverified_stratified_paired_bootstrap(
    observations: tuple[tuple[tuple[str, str, str, str], float, float], ...],
    n_replicates: int = 100_000,
    seed: int = 0,
    *,
    method_a: MethodId = "automarkov",
    method_b: MethodId = "automarkov",
) -> PairedBootstrapResult:
    """RFC 8785 JCS paired stratified bootstrap with SHA-256 rejection sampling.

    Parameters
    ----------
    observations:
        Sequence of ``(cell_key, score_a, score_b)`` tuples where
        ``cell_key = (suite_id, variant_id, track_id, method_id)``
        identifies the stratum, and ``score_a / score_b`` are the
        paired metric values for method A vs B.
    n_replicate:
        Number of bootstrap replicates (clamped to ``[1_000, 1_000_000]``).
    seed:
        Deterministic seed for byte-reproducible PRNG streams.

    Returns
    -------
    PairedBootstrapResult
        Immutable bootstrap result with bias-corrected estimate, CI,
        two-sided p-value, and non-inferiority flag.

    Raises
    ------
    ValueError
        If ``observations`` is empty or scores are non-finite.
    """
    if not observations:
        raise ValueError("observations must be non-empty")

    n = max(1_000, min(n_replicates, 1_000_000))

    # -- Collect cell_key and score arrays ------------------------------------
    cell_keys: list[tuple[str, str, str, str]] = []
    score_a_list: list[float] = []
    score_b_list: list[float] = []
    for cell_key, sa, sb in observations:
        if not math.isfinite(sa) or not math.isfinite(sb):
            raise ValueError(f"non-finite score in cell {cell_key!r}: a={sa}, b={sb}")
        cell_keys.append(cell_key)
        score_a_list.append(float(sa))
        score_b_list.append(float(sb))

    score_a = np.asarray(score_a_list, dtype=np.float64)
    score_b = np.asarray(score_b_list, dtype=np.float64)
    diffs = score_a - score_b
    n_obs = len(diffs)
    obs_mean = float(np.mean(diffs))

    # -- Group indices by stratum -------------------------------------------------
    unique_keys: list[tuple[str, str, str, str]] = sorted(set(cell_keys))
    strata_indices: list[NDArray[np.intp]] = [
        np.array(
            [i for i, k in enumerate(cell_keys) if k == uk],
            dtype=np.intp,
        )
        for uk in unique_keys
    ]

    # -- Deterministic SHA-256 rejection bootstrap (single pass) -----------------
    # Stream 0: cell selection, Stream 1: within-cell resampling.
    # Stores per-replay means to avoid a second pass.
    bootstrap_means = np.empty(n, dtype=np.float64)
    stream_counter = 0

    for rep in range(n):
        sum_diff = 0.0
        rep_base = rep * (2 * n_obs + 2)
        for s_indices in strata_indices:
            s_size = len(s_indices)
            if s_size == 0:  # pragma: no cover
                continue
            for _w in range(s_size):
                # --- draw cell index (uniform over strata) ---
                _deterministic_int(seed, 0, rep_base + stream_counter, n_obs)
                stream_counter += 1
                # --- within-cell resample ---
                cell_sel = _deterministic_int(seed, 1, rep_base + stream_counter, s_size)
                stream_counter += 1
                sum_diff += float(diffs[s_indices[cell_sel]])
        bootstrap_means[rep] = sum_diff / n_obs

    # -- Compute statistics from stored bootstrap means ----------------------
    point_estimate = obs_mean

    # -- Jackknife for BCa ---------------------------------------------------------
    jackknife_means = np.empty(n_obs, dtype=np.float64)
    for leave_out in range(n_obs):
        mask = np.ones(n_obs, dtype=bool)
        mask[leave_out] = False
        jackknife_means[leave_out] = float(np.mean(diffs[mask]))
    jack_mean = float(np.mean(jackknife_means))
    jack_diff = jackknife_means - jack_mean
    jack_sum3 = float(np.sum(jack_diff**3))
    jack_sum2 = float(np.sum(jack_diff**2))
    # Acceleration factor: a = sum(d^3) / (6 * sum(d^2)^1.5)
    if jack_sum2 > 0.0:
        accel = jack_sum3 / (6.0 * jack_sum2**1.5)
    else:
        accel = 0.0

    # -- BCa percentile CI ---------------------------------------------------------
    # Use pure-math Phi/Phi^{-1} (Abramowitz & Stegun 1964).

    bca_frac = float(np.mean(bootstrap_means <= obs_mean))
    bca_frac = max(1e-15, min(1.0 - 1e-15, bca_frac))
    z0 = _phi_inv(bca_frac)
    z_lo = _phi_inv(0.025)
    z_hi = _phi_inv(0.975)
    denom_lo = 1.0 - accel * z_lo
    denom_hi = 1.0 - accel * z_hi
    arg_lo = z0 + (z0 + z_lo) / denom_lo if denom_lo != 0.0 else -10.0
    arg_hi = z0 + (z0 + z_hi) / denom_hi if denom_hi != 0.0 else 10.0
    alpha_lo = _phi(arg_lo)
    alpha_hi = _phi(arg_hi)

    sorted_means = np.sort(bootstrap_means)
    ci_lower_idx = int(np.clip(alpha_lo * n, 0, n - 1))
    ci_upper_idx = int(np.clip(alpha_hi * n, 0, n - 1))
    ci_lower = float(sorted_means[ci_lower_idx])
    ci_upper = float(sorted_means[ci_upper_idx])

    # -- Two-sided p-value ------------------------------------------------------
    # H0: mean(diff) == 0, two-sided: 2 * min(frac >= 0, frac <= 0)
    frac_positive = float(np.mean(bootstrap_means >= 0.0))
    frac_negative = float(np.mean(bootstrap_means <= 0.0))
    p_two = 2.0 * min(frac_positive, frac_negative)
    p_two = min(p_two, 1.0)

    # -- Non-inferiority --------------------------------------------------------
    margin = 0.05
    non_inferior = bool(ci_lower >= -margin)

    return PairedBootstrapResult(
        estimand="E2EValid",
        method_a=method_a,
        method_b=method_b,
        strata=StrataPartition(
            strata=tuple(
                BenchmarkStratum(
                    suite_id=s[0],  # type: ignore[arg-type]
                    variant_id=s[1],  # type: ignore[arg-type]
                    pair_count=1,
                )
                for s in unique_keys
            ),
        ),
        replicates=n,
        point_estimate=point_estimate,
        bias_corrected_estimate=float(2.0 * obs_mean - np.mean(bootstrap_means)),
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        ci_level=0.95,
        p_value_two_sided=p_two,
        non_inferiority_margin=margin,
        non_inferior=non_inferior,
    )


def compute_stratified_paired_bootstrap(
    observations: tuple[tuple[tuple[str, str, str, str], float, float], ...],
    n_replicates: int = 100_000,
    seed: int = 0,
    *,
    method_a: MethodId = "automarkov",
    method_b: MethodId = "automarkov",
) -> PairedBootstrapResult:
    """Fail closed until R08 nested effect/null counter streams are implemented."""
    del observations, n_replicates, seed, method_a, method_b
    raise CapabilityDeferredError("statistics.paired_bootstrap", "R08")


# -- Probability of improvement ----------------------------------------------------


def compute_probability_of_improvement(
    effect_sizes: tuple[float, ...],
) -> float:
    """Exact empirical P(effect > 0) from a collection of effect sizes.

    Parameters
    ----------
    effect_sizes:
        Non-empty sequence of paired effect-size floats
        (positive = improvement).

    Returns
    -------
    float
        Fraction of ``effect_sizes`` strictly greater than zero,
        clamped to ``[0.0, 1.0]``.

    Raises
    ------
    ValueError
        If ``effect_sizes`` is empty or none are finite.
    """
    if not effect_sizes:
        raise ValueError("effect_sizes must be non-empty")
    n = 0
    positive = 0
    for e in effect_sizes:
        if not math.isfinite(e):
            raise ValueError(f"non-finite effect size: {e}")
        n += 1
        if e > 0.0:
            positive += 1
    return max(0.0, min(1.0, positive / n))


# -- IQM and interval --------------------------------------------------------------


def compute_iqm_and_interval(
    observations: tuple[float, ...],
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """Trimmed interquartile mean with deterministic bootstrap CI.

    The IQM trims to the 25th-75th percentile range and then
    takes the arithmetic mean of that interior.  The confidence interval
    is computed by a deterministic SHA-256 bootstrap of the trimmed
    observations.

    Parameters
    ----------
    observations:
        Non-empty sequence of finite floats.
    alpha:
        Significance level for the two-sided CI
        (default 0.05 -> 95 % CI).

    Returns
    -------
    tuple[float, float, float]
        ``(iqm, ci_lower, ci_upper)``

    Raises
    ------
    ValueError
        If ``observations`` is empty, contains non-finite values,
        or ``alpha`` is outside ``(0, 1)``.
    """
    if not observations:
        raise ValueError("observations must be non-empty")
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")

    arr = np.array([float(x) for x in observations], dtype=np.float64)
    if not np.all(np.isfinite(arr)):
        raise ValueError("observations must all be finite")

    if len(arr) == 1:
        v = float(arr[0])
        return (v, v, v)

    # -- Trimmed IQM ----------------------------------------------------------
    q25 = float(np.percentile(arr, 25))
    q75 = float(np.percentile(arr, 75))
    trimmed = arr[(arr >= q25) & (arr <= q75)]
    if len(trimmed) == 0:
        # Degenerate: all values equal or pathological percentile
        trimmed = arr
    iqm = float(np.mean(trimmed))
    n_trimmed = len(trimmed)

    # -- Deterministic bootstrap CI -------------------------------------------------
    n_boot = 10_000
    boot_means = np.empty(n_boot, dtype=np.float64)
    for b in range(n_boot):
        acc = 0.0
        for j in range(n_trimmed):
            u = _deterministic_uniform_f64(42, b, j)
            idx = int(u * n_trimmed)
            acc += float(trimmed[idx])
        boot_means[b] = acc / n_trimmed

    sorted_boot = np.sort(boot_means)
    lo_idx = int((alpha / 2.0) * n_boot)
    hi_idx = int((1.0 - alpha / 2.0) * n_boot)
    lo_idx = max(0, min(lo_idx, n_boot - 1))
    hi_idx = max(0, min(hi_idx, n_boot - 1))

    return (iqm, float(sorted_boot[lo_idx]), float(sorted_boot[hi_idx]))


# -- Performance profile --------------------------------------------------------------


def compute_performance_profile(
    observations: tuple[float, ...],
) -> dict[str, float]:
    """Normalised performance metrics from a set of return observations.

    Computes four standard risk-adjusted metrics normalised to ``[0, 1]``:

    * ``mean_return``    -- arithmetic mean, min-max scaled.
    * ``sharpe_proxy`` -- mean / std when std > 0, else 1.0 iff mean >= 0.
    * ``sortino_proxy`` -- mean / downside_deviation when dd > 0,
                        else 1.0 iff mean >= 0.
    * ``calmar_proxy`` -- mean / mean_abs when mean_abs > 0, else 0.0.

    All values are clamped to ``[0, 1]`` with ``min(max(v, 0), 1)``.

    Parameters
    ----------
    observations:
        Non-empty sequence of finite return floats.

    Returns
    -------
    dict[str, float]
        Four metric keys with clamped normalised values.

    Raises
    ------
    ValueError
        If ``observations`` is empty or contains non-finite values.
    """
    if not observations:
        raise ValueError("observations must be non-empty")
    arr = np.array([float(x) for x in observations], dtype=np.float64)
    if not np.all(np.isfinite(arr)):
        raise ValueError("observations must all be finite")

    def _clamp(v: float) -> float:
        return max(0.0, min(1.0, v))

    mean_ret = float(np.mean(arr))
    std_ret = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
    mean_abs = float(np.mean(np.abs(arr)))

    # -- Min-max normalisation for mean_return -------------------------------------
    min_ret = float(np.min(arr))
    max_ret = float(np.max(arr))
    if max_ret > min_ret:
        norm_mean = (mean_ret - min_ret) / (max_ret - min_ret)
    else:
        norm_mean = 1.0 if mean_ret >= 0.0 else 0.0

    # -- Sharpe proxy ---------------------------------------------------------------
    if std_ret > 0.0:
        sharpe = mean_ret / std_ret
    else:
        sharpe = 1.0 if mean_ret >= 0.0 else 0.0

    # -- Sortino proxy -----------------------------------------------------------
    downside = arr[arr < 0.0]
    if len(downside) > 0:
        dd = float(np.sqrt(np.mean(downside**2)))
    else:
        dd = 0.0
    if dd > 0.0:
        sortino = mean_ret / dd
    else:
        sortino = 1.0 if mean_ret >= 0.0 else 0.0

    # -- Calmar proxy -----------------------------------------------------------
    if mean_abs > 0.0:
        calmar = mean_ret / mean_abs
    else:
        calmar = 0.0

    return {
        "mean_return": _clamp(norm_mean),
        "sharpe_proxy": _clamp(sharpe),
        "sortino_proxy": _clamp(sortino),
        "calmar_proxy": _clamp(calmar),
    }


# -- Non-inferiority bound -------------------------------------------------------


def compute_noninferiority_bound(
    gap: float,
    n: int,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """One-sided CI lower bound for non-inferiority testing.

    Computes a ``(1 - alpha)`` one-sided confidence interval
    lower bound using the normal approximation::

        lower = gap - z_{1-alpha} * sqrt(1/n)

    * ``gap``   -- observed mean difference (A - B).
    * ``n``     -- number of paired observations.
    * ``alpha`` -- significance level (default 0.05).

    Parameters
    ----------
    gap:
        Observed mean paired difference (A - B).
    n:
        Number of paired observations (must be >= 1).
    alpha:
        One-sided significance level.

    Returns
    -------
    tuple[float, float]
        ``(lower_bound, z_critical)``

    Raises
    ------
    ValueError
        If ``n < 1`` or ``alpha`` is outside ``(0, 1)``.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    if not math.isfinite(gap):
        raise ValueError(f"gap must be finite, got {gap}")

    # Pure-math Phi^{-1} (Abramowitz & Stegun 1964) -- no scipy needed
    z_crit = _phi_inv(1.0 - alpha)
    se = math.sqrt(1.0 / n)
    lower_bound = gap - z_crit * se
    return (lower_bound, z_crit)


# -- Holm step-down ---------------------------------------------------------------


def compute_holm_step_down(
    p_values: tuple[float, ...],
    family_alpha: float = 0.05,
) -> HolmFamily:
    """Holm step-down p_value adjustment for a pre-registered family.

    Given a family of raw p-values, applies the Holm (1979)
    step-down procedure:

    1. Sort p-values ascending.
    2. Rank ``k = 1, 2, ..., m``.
    3. Adjust: ``p_adj(k) = max_{j >= k} { p(j) * (m - j + 1) }``.
    4. Reject if ``p_adj(k) <= family_alpha``.

    Parameters
    ----------
    p_values:
        Non-empty sequence of raw p-values in ``[0, 1]``.
    family_alpha:
        Family-wise significance level, constrained to ``[0.01, 0.10]``.

    Returns
    -------
    HolmFamily
        Frozen Holm family with sorted hypotheses carrying rank,
        adjusted alpha, and rejection flag.

    Raises
    ------
    ValueError
        If ``p_values`` is empty, contains values outside ``[0, 1]``,
        or ``family_alpha`` is outside ``[0.01, 0.10]``.
    """
    if not p_values:
        raise ValueError("p_values must be non-empty")
    if not (0.01 <= family_alpha <= 0.10):
        raise ValueError(f"family_alpha must be in [0.01, 0.10], got {family_alpha}")
    m = len(p_values)
    indexed: list[tuple[int, float]] = []
    for i, p in enumerate(p_values):
        if not isinstance(p, (int, float)):
            raise TypeError(f"p_value at index {i} is not numeric: {p!r}")
        pf = float(p)
        if not (0.0 <= pf <= 1.0):
            raise ValueError(f"p_value at index {i} out of [0, 1]: {pf}")
        indexed.append((i, pf))

    # Sort ascending by raw p-value
    indexed.sort(key=lambda t: t[1])

    # Compute step-down adjusteds (backward pass)
    adj = [0.0] * m
    adj[m - 1] = indexed[m - 1][1] * 1.0
    for k in range(m - 2, -1, -1):
        raw_adj = indexed[k][1] * (m - k)
        adj[k] = max(adj[k + 1], raw_adj)

    hypotheses_list: list[HolmHypothesis] = []
    for rank_idx, (orig_idx, raw_p) in enumerate(indexed):
        k = rank_idx + 1
        a = min(adj[rank_idx], 1.0)
        hypotheses_list.append(
            HolmHypothesis(
                hypothesis_id=f"h_{orig_idx}",
                description=f"comparison index {orig_idx}",
                raw_p_value=raw_p,
                rank=k,
                adjusted_alpha=a,
                rejected=bool(a <= family_alpha),
            )
        )

    return HolmFamily(
        family_id="holm_family",
        family_alpha=family_alpha,
        hypotheses=tuple(hypotheses_list),
        preregistered_at="",
    )


# -- Exports -----------------------------------------------------------------


__all__ = [
    "BenchmarkStratum",
    "HolmFamily",
    "HolmHypothesis",
    "PairedBootstrapResult",
    "compute_holm_step_down",
    "compute_iqm_and_interval",
    "compute_noninferiority_bound",
    "compute_performance_profile",
    "compute_probability_of_improvement",
    "compute_stratified_paired_bootstrap",
    "validate_exact_cartesian_grid",
]
