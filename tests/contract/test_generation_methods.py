"""T21: Six generation methods + pairing contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from automarkov.contracts.methods import (
    CapabilityManifest,
    PairBinding,
    PairingContract,
)


class TestCapabilityManifest:
    def test_automarkov_has_full_caps(self) -> None:
        c = CapabilityManifest(method_id="automarkov", web_evidence=True, sealed_oracle=False,
                               llm_generation=True, environmental_adaptation=True, formal_reasoning=True)
        assert c.web_evidence

    def test_single_llm_lacks_oracle(self) -> None:
        c = CapabilityManifest(method_id="single_llm")
        assert not c.sealed_oracle


class TestPairingContract:
    def test_accepts_all_six_methods(self) -> None:
        pairs = (
            PairBinding(method_a="automarkov", method_b="single_llm", pair_index=0),
            PairBinding(method_a="automarkov", method_b="alamp_paper_spec", pair_index=1),
            PairBinding(method_a="automarkov", method_b="agent2_paper_spec", pair_index=2),
            PairBinding(method_a="automarkov", method_b="agent2world_clean_controlled", pair_index=3),
            PairBinding(method_a="automarkov", method_b="react_executor", pair_index=4),
            PairBinding(method_a="automarkov", method_b="automarkov", pair_index=5),
        )
        c = PairingContract(experiment_id="expt21", total_pairs=6, pairs=pairs)
        assert len(c.method_pairs) >= 6

    def test_rejects_missing_methods(self) -> None:
        pairs = (PairBinding(method_a="automarkov", method_b="alamp_paper_spec", pair_index=0),)
        with pytest.raises(ValidationError, match="all six"):
            PairingContract(experiment_id="expt21", total_pairs=1, pairs=pairs)
