"""T21: Six common-backend generation methods and pairing contract.

六种方法：automarkov, automarkov_no_evidence, a_lamp, agent2, agent2world, react。
每个 method 有冻结的 capability、evidence view 与 generation contract。
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from automarkov.canonical import FrozenSequence
from automarkov.domain import StrictFrozenModel
from automarkov.lifecycle import NonEmptyId, Sha256Value
from automarkov.benchmark_suites import MethodId


class CapabilityManifest(StrictFrozenModel):
    method_id: MethodId
    web_evidence: bool = Field(strict=True, default=False)
    sealed_oracle: bool = Field(strict=True, default=False)
    llm_generation: bool = Field(strict=True, default=True)
    environmental_adaptation: bool = Field(strict=True, default=True)
    formal_reasoning: bool = Field(strict=True, default=True)


class EvidenceViewBinding(StrictFrozenModel):
    method_id: MethodId
    allowed_evidence_types: FrozenSequence[NonEmptyId]
    blocked_evidence_types: FrozenSequence[NonEmptyId]
    sealed_commitments: FrozenSequence[Sha256Value]


class PairBinding(StrictFrozenModel):
    method_a: MethodId
    method_b: MethodId
    pair_index: Annotated[int, Field(strict=True, ge=0, le=1000)]


class PairingContract(StrictFrozenModel):
    schema_version: Literal["automarkov.pairing-contract.v1"] = "automarkov.pairing-contract.v1"
    experiment_id: NonEmptyId
    total_pairs: Annotated[int, Field(strict=True, ge=1, le=1000)]
    pairs: FrozenSequence[PairBinding]
    control_method: Literal["automarkov"] = "automarkov"

    @model_validator(mode="after")
    def require_coverage(self) -> Self:
        methods_in_pairs: set[MethodId] = set()
        for p in self.pairs:
            methods_in_pairs.add(p.method_a)
            methods_in_pairs.add(p.method_b)
        all_methods: set[MethodId] = {
            "automarkov", "automarkov_no_evidence", "a_lamp", "agent2", "agent2world", "react"
        }
        if not all_methods.issubset(methods_in_pairs):
            raise ValueError("pairing contract must cover all six methods")
        return self

    @property
    def method_pairs(self) -> dict[MethodId, list[MethodId]]:
        result: dict[MethodId, list[MethodId]] = {}
        for p in self.pairs:
            result.setdefault(p.method_a, []).append(p.method_b)
            result.setdefault(p.method_b, []).append(p.method_a)
        return result


__all__ = [
    "CapabilityManifest",
    "EvidenceViewBinding",
    "PairBinding",
    "PairingContract",
]
