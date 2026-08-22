"""T22: 组件消融与 MPE2 信息结构账本。

六项独立 paired ledger 消融：automarkov_no_evidence × full automarkov。
MPE2 native-local POSG 对 full-state MG adaptation。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from automarkov.canonical import FrozenSequence
from automarkov.domain import StrictFrozenModel
from automarkov.lifecycle import NonEmptyId, Sha256Value
from automarkov.benchmark_suites import SuiteId, VariantId


class AblationBinding(StrictFrozenModel):
    ablation_id: NonEmptyId
    experiment_id: NonEmptyId
    suite_id: SuiteId
    variant_id: VariantId
    full_method: Literal["automarkov"] = "automarkov"
    ablated_method: Literal["automarkov_no_evidence"] = "automarkov_no_evidence"
    pair_index: Annotated[int, Field(strict=True, ge=0, le=1000)]
    full_run_id: NonEmptyId
    ablated_run_id: NonEmptyId
    full_attestation_hash: Sha256Value
    ablated_attestation_hash: Sha256Value


class AblationLedger(StrictFrozenModel):
    schema_version: Literal["automarkov.ablation-ledger.v1"] = (
        "automarkov.ablation-ledger.v1"
    )
    experiment_id: NonEmptyId
    bindings: FrozenSequence[AblationBinding]

    @property
    def count(self) -> int:
        return len(self.bindings)


class Mpe2InfoStructureBinding(StrictFrozenModel):
    binding_id: NonEmptyId
    condition: Literal["native_local_posg", "full_state_mg"]
    run_id: NonEmptyId
    attestation_hash: Sha256Value


class Mpe2InfoStructureLedger(StrictFrozenModel):
    schema_version: Literal["automarkov.mpe2-info-structure-ledger.v1"] = (
        "automarkov.mpe2-info-structure-ledger.v1"
    )
    experiment_id: NonEmptyId
    bindings: FrozenSequence[Mpe2InfoStructureBinding]

    @model_validator(mode="after")  # type: ignore[misc]
    def require_both_conditions(self) -> "Mpe2InfoStructureLedger":
        conditions = {b.condition for b in self.bindings}
        if conditions != {"native_local_posg", "full_state_mg"}:
            raise ValueError("MPE2 ledger must contain both info structure conditions")
        return self


class AblationManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.ablation-manifest.v1"] = (
        "automarkov.ablation-manifest.v1"
    )
    experiment_id: NonEmptyId
    component_ablations: AblationLedger
    mpe2_info_structure: Mpe2InfoStructureLedger
    preregistered_families: FrozenSequence[NonEmptyId]


__all__ = [
    "AblationBinding",
    "AblationLedger",
    "AblationManifest",
    "Mpe2InfoStructureBinding",
    "Mpe2InfoStructureLedger",
]
