"""T22: Component ablation + MPE2 info structure ledger tests."""

import pytest
from pydantic import ValidationError

from automarkov.contracts.ablation import (
    AblationBinding,
    AblationLedger,
    Mpe2InfoStructureBinding,
    Mpe2InfoStructureLedger,
)

HASH = "sha256:" + "0" * 64


class TestAblationLedger:
    def test_accepts_bindings(self) -> None:
        b = AblationBinding(
            ablation_id="ab01", experiment_id="expt22",
            suite_id="taxi_mdp", variant_id="v1_canonical",
            pair_index=0, full_run_id="fullrun01", ablated_run_id="ablrun01",
            full_attestation_hash=HASH, ablated_attestation_hash=HASH,
        )
        assert AblationLedger(experiment_id="expt22", bindings=(b,)).count == 1

    @pytest.mark.parametrize(
        "ablation_method",
        (
            "automarkov_no_evidence",
            "automarkov_no_text_critic",
            "automarkov_no_formal_critic",
            "automarkov_no_simulation_tester",
            "automarkov_no_training_feedback",
            "automarkov_single_agent_workflow",
        ),
    )
    def test_accepts_each_registered_component_ablation(
        self,
        ablation_method: str,
    ) -> None:
        binding = AblationBinding(
            ablation_id="ab_registered",
            experiment_id="expt22",
            suite_id="taxi_mdp",
            variant_id="v1_canonical",
            ablated_method=ablation_method,  # type: ignore[arg-type]
            pair_index=0,
            full_run_id="fullrun01",
            ablated_run_id="ablrun01",
            full_attestation_hash=HASH,
            ablated_attestation_hash=HASH,
        )
        assert binding.ablated_method == ablation_method


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
