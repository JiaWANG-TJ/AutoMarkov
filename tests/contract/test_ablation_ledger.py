"""T22: Component ablation + MPE2 info structure ledger tests."""

import pytest
from pydantic import ValidationError
from automarkov.ablation_ledger import (
    AblationBinding, AblationLedger, AblationManifest,
    Mpe2InfoStructureBinding, Mpe2InfoStructureLedger,
)

HASH = "sha256:" + "0" * 64


class TestAblationLedger:
    def test_accepts_bindings(self) -> None:
        b = AblationBinding(
            ablation_id="ab01", experiment_id="expt22",
            suite_id="mab_cartpole", variant_id="v1_plain",
            pair_index=0, full_run_id="fullrun01", ablated_run_id="ablrun01",
            full_attestation_hash=HASH, ablated_attestation_hash=HASH,
        )
        assert AblationLedger(experiment_id="expt22", bindings=(b,)).count == 1


class TestMpe2InfoStructure:
    def test_requires_both_conditions(self) -> None:
        b1 = Mpe2InfoStructureBinding(binding_id="b1", condition="native_local_posg", run_id="r1", attestation_hash=HASH)
        b2 = Mpe2InfoStructureBinding(binding_id="b2", condition="full_state_mg", run_id="r2", attestation_hash=HASH)
        m = Mpe2InfoStructureLedger(experiment_id="expt22", bindings=(b1, b2))
        assert len(m.bindings) == 2

    def test_rejects_single_condition(self) -> None:
        b1 = Mpe2InfoStructureBinding(binding_id="b1", condition="native_local_posg", run_id="r1", attestation_hash=HASH)
        with pytest.raises(ValidationError, match="both"):
            Mpe2InfoStructureLedger(experiment_id="expt22", bindings=(b1,))
