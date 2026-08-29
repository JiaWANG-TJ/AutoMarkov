"""Thin re-export shim for backward compatibility — classification now lives in automarkov.domain.classification."""

from automarkov.domain.classification import (
    ClassificationFacts,
    ClassificationOodHandoff,
    ClassificationProof,
    derive_decision_process_kind,
)

__all__ = [
    "ClassificationFacts",
    "ClassificationOodHandoff",
    "ClassificationProof",
    "derive_decision_process_kind",
]
