"""R05: SuiteRegistry -- frozen 6-suite canonical contract registry.

Six canonical suites with immutable source-access mode, implementation route,
official package, and evaluation contract. Frozen after construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from automarkov.contracts.benchmark import SourceAccess, SuiteId


@dataclass(frozen=True)
class SuiteCanonicalContract:
    """Immutable canonical contract for a single benchmark suite."""

    suite_id: SuiteId
    source_access: SourceAccess
    implementation_route: Literal[
        "gymnasium.classic_control",
        "gymnasium.toy_text",
        "minigrid",
        "pettingzoo.mpe",
        "smacv2",
        "metadrive",
        "citylearn",
    ]
    official_package: Literal[
        "gymnasium",
        "minigrid",
        "pettingzoo",
        "smacv2",
        "metadrive",
        "citylearn",
    ]
    evaluation_contract: Literal[
        "sealed_gold_return",
        "public_benchmark_return",
    ]


# -- Frozen canonical suite definitions --------------------------------------------

_CANONICAL_SUITES: tuple[SuiteCanonicalContract, ...] = (
    SuiteCanonicalContract(
        suite_id="taxi_mdp",
        source_access="public_repository",
        implementation_route="gymnasium.toy_text",
        official_package="gymnasium",
        evaluation_contract="public_benchmark_return",
    ),
    SuiteCanonicalContract(
        suite_id="memory_pomdp",
        source_access="public_repository",
        implementation_route="minigrid",
        official_package="minigrid",
        evaluation_contract="sealed_gold_return",
    ),
    SuiteCanonicalContract(
        suite_id="mpe2_full_state_mg",
        source_access="public_repository",
        implementation_route="pettingzoo.mpe",
        official_package="pettingzoo",
        evaluation_contract="sealed_gold_return",
    ),
    SuiteCanonicalContract(
        suite_id="smacv2_posg",
        source_access="gated_artifact",
        implementation_route="smacv2",
        official_package="smacv2",
        evaluation_contract="sealed_gold_return",
    ),
    SuiteCanonicalContract(
        suite_id="metadrive_pomdp",
        source_access="sealed_evaluator_only",
        implementation_route="metadrive",
        official_package="metadrive",
        evaluation_contract="sealed_gold_return",
    ),
    SuiteCanonicalContract(
        suite_id="citylearn_posg",
        source_access="sealed_evaluator_only",
        implementation_route="citylearn",
        official_package="citylearn",
        evaluation_contract="sealed_gold_return",
    ),
)

_SOURCE_ACCESS_ORDER: dict[SourceAccess, int] = {
    "public_repository": 0,
    "gated_artifact": 1,
    "sealed_evaluator_only": 2,
}


@dataclass(frozen=True)
class SuiteRegistry:
    """Frozen registry mapping suite_id to its canonical contract.

    Validates that all six suites are present, IDs are unique, and no
    unknown suite_id can be looked up. Immutable after construction.
    """

    _by_id: dict[SuiteId, SuiteCanonicalContract] = field(
        default_factory=lambda: {c.suite_id: c for c in _CANONICAL_SUITES}
    )

    def __post_init__(self) -> None:
        expected: set[SuiteId] = {
            "taxi_mdp",
            "memory_pomdp",
            "mpe2_full_state_mg",
            "smacv2_posg",
            "metadrive_pomdp",
            "citylearn_posg",
        }
        actual = set(self._by_id.keys())
        if actual != expected:
            missing = expected - actual
            extra = actual - expected
            msg_parts: list[str] = []
            if missing:
                msg_parts.append(f"missing suites: {sorted(missing)}")
            if extra:
                msg_parts.append(f"unknown suites: {sorted(extra)}")
            raise ValueError("SuiteRegistry must cover exactly six suites. " + "; ".join(msg_parts))
        # Verify no duplicate suite_ids (defensive)
        if len(self._by_id) != len(_CANONICAL_SUITES):
            raise ValueError("SuiteRegistry contains duplicate suite_id entries")

    def lookup(self, suite_id: SuiteId) -> SuiteCanonicalContract:
        """Return the canonical contract for *suite_id*.

        Raises ``KeyError`` for any suite_id not in the six canonical suites.
        """
        if suite_id not in self._by_id:
            raise KeyError(
                f"unknown suite_id {suite_id!r}; "
                f"valid suites: {sorted(self._by_id.keys())}"
            )
        return self._by_id[suite_id]

    @property
    def suite_ids(self) -> tuple[SuiteId, ...]:
        """Canonical ordering of all six suite IDs."""
        return tuple(
            sorted(
                self._by_id.keys(),
                key=lambda sid: _SOURCE_ACCESS_ORDER.get(
                    self._by_id[sid].source_access, 99
                ),
            )
        )

    def __contains__(self, suite_id: str) -> bool:
        return suite_id in self._by_id

    def __len__(self) -> int:
        return len(self._by_id)


__all__ = [
    "SuiteCanonicalContract",
    "SuiteRegistry",
]
