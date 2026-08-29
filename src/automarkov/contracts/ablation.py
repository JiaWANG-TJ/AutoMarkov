"""T22: 组件消融与 MPE2 信息结构账本。

六项独立 paired ledger 消融：automarkov_no_evidence × full automarkov。
MPE2 native-local POSG 对 full-state MG adaptation。
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from automarkov.contracts.benchmark import SuiteId, VariantId
from automarkov.domain.canonical import FrozenSequence
from automarkov.domain.models import StrictFrozenModel
from automarkov.lifecycle import NonEmptyId, Sha256Value

AblationMethodId: TypeAlias = Literal[
    "automarkov_no_evidence",
    "automarkov_no_text_critic",
    "automarkov_no_formal_critic",
    "automarkov_no_simulation_tester",
    "automarkov_no_training_feedback",
    "automarkov_single_agent_workflow",
]


class AblationBinding(StrictFrozenModel):
    ablation_id: NonEmptyId
    experiment_id: NonEmptyId
    suite_id: SuiteId
    variant_id: VariantId
    full_method: Literal["automarkov"] = "automarkov"
    ablated_method: AblationMethodId = "automarkov_no_evidence"
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
    def require_both_conditions(self) -> Mpe2InfoStructureLedger:
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


# ── Ablation grid validation ──────────────────────────────────────

_ABLATION_SUITES: tuple[SuiteId, ...] = (
    "taxi_mdp",
    "memory_pomdp",
    "mpe2_full_state_mg",
    "smacv2_posg",
    "metadrive_pomdp",
    "citylearn_posg",
)

_ABLATION_VARIANTS: tuple[VariantId, ...] = (
    "v1_canonical",
    "v2_paraphrased",
    "v3_reordered_longform",
    "v4_evidence_split",
)

_ABLATION_METHODS: tuple[AblationMethodId, ...] = (
    "automarkov_no_evidence",
    "automarkov_no_text_critic",
    "automarkov_no_formal_critic",
    "automarkov_no_simulation_tester",
    "automarkov_no_training_feedback",
    "automarkov_single_agent_workflow",
)


def validate_ablation_144_grid(
    bindings: tuple[AblationBinding, ...],
) -> tuple[AblationBinding, ...]:
    """Validate that ``bindings`` is the exact 6x4x6 = 144-cell ablation grid.

    Checks:
    * no duplicate (suite, variant, method) tuples
    * all 144 cells present (no omissions)
    * all (suite, variant) pairs must have both a full and an ablated run

    Raises ``ValueError`` on any violation.
    """
    expected = 6 * 4 * 6
    if len(bindings) != expected:
        raise ValueError(
            f"expected exactly {expected} ablation bindings, got {len(bindings)}"
        )

    seen: set[tuple[str, str, str]] = set()
    duplicates: list[str] = []
    for b in bindings:
        key = (b.suite_id, b.variant_id, b.ablated_method if hasattr(b, "ablated_method") else b.full_method)
        if key in seen:
            duplicates.append(str(key))
        seen.add(key)

    if duplicates:
        raise ValueError(
            f"duplicate ablation bindings detected: {', '.join(duplicates)}"
        )

    missing: list[str] = []
    for s in _ABLATION_SUITES:
        for v in _ABLATION_VARIANTS:
            for m in _ABLATION_METHODS:
                if (s, v, m) not in seen:
                    missing.append(f"({s}, {v}, {m})")

    if missing:
        raise ValueError(
            f"missing {len(missing)} ablation cells, e.g. {missing[:5]}"
        )

    return bindings


__all__ = [
    "AblationBinding",
    "AblationLedger",
    "AblationManifest",
    "AblationMethodId",
    "Mpe2InfoStructureBinding",
    "Mpe2InfoStructureLedger",
    "validate_ablation_144_grid",
]
