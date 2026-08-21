"""T21: Six generation methods + pairing contract tests."""

from __future__ import annotations
import pytest
from pydantic import ValidationError
from automarkov.generation_methods import (
    CapabilityManifest, EvidenceViewBinding, PairBinding, PairingContract,
)


class TestCapabilityManifest:
    def test_automarkov_has_full_caps(self) -> None:
        c = CapabilityManifest(method_id="automarkov", web_evidence=True, sealed_oracle=False,
                               llm_generation=True, environmental_adaptation=True, formal_reasoning=True)
        assert c.web_evidence

    def test_no_evidence_lacks_web(self) -> None:
        c = CapabilityManifest(method_id="automarkov_no_evidence")
        assert not c.web_evidence


class TestPairingContract:
    def test_accepts_all_six_methods(self) -> None:
        pairs = tuple(PairBinding(method_a="automarkov", method_b=m, pair_index=i)
                      for i, m in enumerate(["a_lamp", "agent2", "agent2world", "react", "automarkov_no_evidence", "automarkov"]))
        c = PairingContract(experiment_id="expt21", total_pairs=6, pairs=pairs)
        assert len(c.method_pairs) >= 6

    def test_rejects_missing_methods(self) -> None:
        pairs = (PairBinding(method_a="automarkov", method_b="a_lamp", pair_index=0),)
        with pytest.raises(ValidationError, match="all six"):
            PairingContract(experiment_id="expt21", total_pairs=1, pairs=pairs)
