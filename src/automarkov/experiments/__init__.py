"""AutoMarkov experiments layer -- registry, grid, statistics, and result analysis."""

from automarkov.experiments.grid import BenchmarkGrid
from automarkov.experiments.registry import SuiteCanonicalContract, SuiteRegistry

__all__ = [
    "BenchmarkGrid",
    "SuiteCanonicalContract",
    "SuiteRegistry",
]
