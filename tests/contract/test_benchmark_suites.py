"""T20: Benchmark suites contract tests — TaskCard, BenchmarkManifest, GoldScoreCalibration."""

from __future__ import annotations
import pytest
from pydantic import ValidationError
from automarkov.benchmark_suites import (
    BenchmarkCellBinding, BenchmarkManifest, GoldScoreCalibration, TaskCard,
)

HX = "0" * 64
HASH = "sha256:" + HX
ART = "artifact_" + HX

_SUITE: tuple = ("mab_cartpole", "grid_taxi", "nav_minigrid", "mpe2_spread", "starcraft_smacv2", "energy_citylearn")
_VAR: tuple = ("v1_plain", "v2_reversed", "v3_reworded", "v4_ambiguous", "v5_clarification_required")
_TRACK: tuple = ("AUTO", "HITL-ORACLE")
_METHOD: tuple = ("automarkov", "automarkov_no_evidence", "a_lamp", "agent2", "agent2" + "world", "react")


def _task_card(suite="mab_cartpole", variant="v1_plain") -> TaskCard:
    return TaskCard(
        suite_id=suite, variant_id=variant, description="test task",
        required_implementation="gymnasium.classic_control",
        source_access="public_repository",
        allowed_evidence_sources=("tavily",), blocked_evidence_sources=(),
    )


def _full_grid() -> tuple[BenchmarkCellBinding, ...]:
    cells = []
    for suite in _SUITE:
        for variant in _VAR:
            for track in _TRACK:
                for method in _METHOD:
                    cells.append(BenchmarkCellBinding(
                        suite_id=suite, variant_id=variant,
                        track_id=track, method_id=method,
                        execution_status="RUN",
                    ))
    return tuple(cells)


class TestTaskCard:
    def test_accepts_valid(self) -> None:
        c = _task_card()
        assert c.suite_id == "mab_cartpole"

    def test_rejects_empty_description(self) -> None:
        with pytest.raises(ValidationError):
            TaskCard(
                suite_id="mab_cartpole", variant_id="v1_plain",
                description="",
                required_implementation="gymnasium.classic_control",
                source_access="public_repository",
                allowed_evidence_sources=("tavily",),
                blocked_evidence_sources=(),
            )

    def test_rejects_invalid_suite(self) -> None:
        with pytest.raises(ValidationError):
            _task_card(suite="invalid")


class TestBenchmarkManifest:
    def test_accepts_full_grid(self) -> None:
        m = BenchmarkManifest(
            experiment_id="exp_t20", pair_count=10,
            cells=_full_grid(), frozen_at="2026-08-21T12:00:00Z",
        )
        assert m.run_count == 360  # 6*5*2*6 = all RUN

    def test_rejects_incomplete_grid(self) -> None:
        with pytest.raises(ValidationError, match="exactly"):
            BenchmarkManifest(
                experiment_id="exp_t20", pair_count=10,
                cells=_full_grid()[:100],
                frozen_at="2026-08-21T12:00:00Z",
            )

    def test_round_trips(self) -> None:
        m = BenchmarkManifest(
            experiment_id="exp_t20", pair_count=10,
            cells=_full_grid(), frozen_at="2026-08-21T12:00:00Z",
        )
        r = BenchmarkManifest.model_validate(m.model_dump(mode="json"), strict=True)
        assert r.run_count == 360


class TestGoldScoreCalibration:
    def test_accepts_valid(self) -> None:
        c = GoldScoreCalibration(
            suite_id="mab_cartpole", variant_id="v1_plain",
            calibration_condition="full",
            random_return=0.0, reference_return=500.0,
            evaluator_valid=True, evaluated_seeds=10,
            calibration_artifact={"artifact_id": ART, "payload_hash": HASH},
        )
        assert c.evaluator_valid
