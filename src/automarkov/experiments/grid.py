"""R05: BenchmarkGrid -- exact 6x5x2x6 = 360-cell Cartesian product generator.

Generates the complete intention-to-run grid, validates completeness,
produces the 24-strata partition (suite x variant, v1-v4 only).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from automarkov.contracts.benchmark import (
    BenchmarkCellBinding,
    BenchmarkStratum,
    MethodId,
    StrataPartition,
    SuiteId,
    TrackId,
    VariantId,
)

# -- Canonical ordering constants -------------------------------------------------

_CANONICAL_SUITES: tuple[SuiteId, ...] = (
    "taxi_mdp",
    "memory_pomdp",
    "mpe2_full_state_mg",
    "smacv2_posg",
    "metadrive_pomdp",
    "citylearn_posg",
)

_CANONICAL_VARIANTS: tuple[VariantId, ...] = (
    "v1_canonical",
    "v2_paraphrased",
    "v3_reordered_longform",
    "v4_evidence_split",
    "v5_clarification_required",
)

_CANONICAL_TRACKS: tuple[TrackId, ...] = ("AUTO", "HITL-ORACLE")

_CANONICAL_METHODS: tuple[MethodId, ...] = (
    "automarkov",
    "single_llm",
    "alamp_paper_spec",
    "agent2_paper_spec",
    "agent2world_clean_controlled",
    "react_executor",
)

_STRATA_VARIANTS: tuple[VariantId, ...] = (
    "v1_canonical",
    "v2_paraphrased",
    "v3_reordered_longform",
    "v4_evidence_split",
)

# -- Grid cell key type -------------------------------------------------------------

_CellKey = tuple[SuiteId, VariantId, TrackId, MethodId]


@dataclass(frozen=True)
class BenchmarkGrid:
    """Frozen 6x5x2x6 = 360-cell benchmark grid.

    Each cell is a (suite_id, variant_id, track_id, method_id) tuple
    with an execution_status. Validates that the grid is complete,
    unique, and canonically ordered.
    """

    cells: tuple[BenchmarkCellBinding, ...] = field(
        default_factory=lambda: tuple(
            BenchmarkCellBinding(
                suite_id=s,
                variant_id=v,
                track_id=t,
                method_id=m,
            )
            for s in _CANONICAL_SUITES
            for v in _CANONICAL_VARIANTS
            for t in _CANONICAL_TRACKS
            for m in _CANONICAL_METHODS
        )
    )

    def __post_init__(self) -> None:
        self.validate_exact_grid()

    def validate_exact_grid(self) -> None:
        """Validate that cells is the exact 6x5x2x6 = 360-cell Cartesian grid.

        Checks:
        - exactly 360 cells
        - no duplicate (suite, variant, track, method) tuples
        - no missing cells (all 360 combinations present)
        - all IDs are known (no unknown suite/variant/track/method)
        - canonical ordering matches the declared product sequence

        Raises ``ValueError`` on any violation.
        """
        expected_count = 6 * 5 * 2 * 6
        if len(self.cells) != expected_count:
            raise ValueError(
                f"expected exactly {expected_count} cells, got {len(self.cells)}"
            )

        # Check for duplicates and unknown IDs
        seen: set[_CellKey] = set()
        duplicates: list[str] = []
        unknown: list[str] = []

        valid_suites: set[SuiteId] = set(_CANONICAL_SUITES)
        valid_variants: set[VariantId] = set(_CANONICAL_VARIANTS)
        valid_tracks: set[TrackId] = set(_CANONICAL_TRACKS)
        valid_methods: set[MethodId] = set(_CANONICAL_METHODS)

        for c in self.cells:
            key: _CellKey = (c.suite_id, c.variant_id, c.track_id, c.method_id)
            if key in seen:
                duplicates.append(str(key))
            seen.add(key)

            if c.suite_id not in valid_suites:
                unknown.append(f"unknown suite_id: {c.suite_id!r}")
            if c.variant_id not in valid_variants:
                unknown.append(f"unknown variant_id: {c.variant_id!r}")
            if c.track_id not in valid_tracks:
                unknown.append(f"unknown track_id: {c.track_id!r}")
            if c.method_id not in valid_methods:
                unknown.append(f"unknown method_id: {c.method_id!r}")

        if duplicates:
            raise ValueError(
                f"duplicate cells detected: {', '.join(duplicates)}"
            )

        if unknown:
            raise ValueError(
                f"unknown IDs: {'; '.join(unknown)}"
            )

        # Check for missing cells
        missing: list[str] = []
        for s in _CANONICAL_SUITES:
            for v in _CANONICAL_VARIANTS:
                for t in _CANONICAL_TRACKS:
                    for m in _CANONICAL_METHODS:
                        if (s, v, t, m) not in seen:
                            missing.append(f"({s}, {v}, {t}, {m})")

        if missing:
            raise ValueError(
                f"missing {len(missing)} cells, e.g. {missing[:5]}"
            )

        # Verify canonical ordering
        expected_index = 0
        for s in _CANONICAL_SUITES:
            for v in _CANONICAL_VARIANTS:
                for t in _CANONICAL_TRACKS:
                    for m in _CANONICAL_METHODS:
                        cell = self.cells[expected_index]
                        if (cell.suite_id, cell.variant_id, cell.track_id, cell.method_id) != (s, v, t, m):
                            raise ValueError(
                                f"cell at index {expected_index} is out of canonical order: "
                                f"expected ({s}, {v}, {t}, {m}), "
                                f"got ({cell.suite_id}, {cell.variant_id}, {cell.track_id}, {cell.method_id})"
                            )
                        expected_index += 1

    @property
    def total_cells(self) -> int:
        """Total cell count (always 360)."""
        return len(self.cells)

    @property
    def run_cells(self) -> int:
        """Count of cells marked RUN."""
        return sum(1 for c in self.cells if c.execution_status == "RUN")

    @property
    def na_cells(self) -> int:
        """Count of cells marked N/A."""
        return sum(1 for c in self.cells if c.execution_status == "N/A")

    @property
    def deferred_cells(self) -> int:
        """Count of cells marked DEFERRED."""
        return sum(1 for c in self.cells if c.execution_status == "DEFERRED")

    def strata_partition(self, pair_count: int = 1) -> StrataPartition:
        """Generate the frozen 24-strata partition (suite x variant, v1-v4).

        Only v1 through v4 are included in the strata partition;
        v5 (clarification_required) is excluded per the specification.
        Each stratum receives the same *pair_count*.
        """
        strata: list[BenchmarkStratum] = []
        for s in _CANONICAL_SUITES:
            for v in _STRATA_VARIANTS:
                strata.append(
                    BenchmarkStratum(
                        suite_id=s,
                        variant_id=v,
                        pair_count=pair_count,
                    )
                )
        return StrataPartition(strata=tuple(strata))

    def cell_keys(self) -> frozenset[_CellKey]:
        """Return a frozenset of all (suite, variant, track, method) keys."""
        return frozenset(
            (c.suite_id, c.variant_id, c.track_id, c.method_id)
            for c in self.cells
        )

    def has_cell(
        self,
        suite_id: SuiteId,
        variant_id: VariantId,
        track_id: TrackId,
        method_id: MethodId,
    ) -> bool:
        """Check whether a specific cell exists in the grid."""
        return (suite_id, variant_id, track_id, method_id) in self.cell_keys()


__all__ = [
    "BenchmarkGrid",
]
