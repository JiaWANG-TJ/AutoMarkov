"""Shim: delegate to experiments.statistics for benchmark statistics."""

from automarkov.contracts.benchmark import BenchmarkStratum, StrataPartition  # noqa: F401
from automarkov.experiments.statistics import *
