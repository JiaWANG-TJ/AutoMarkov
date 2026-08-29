"""T18: CPU smoke test 合同验证。

验证 RllibCpuSmokeContract、RllibSmokeAssertion 与 RllibCpuSmokeAttempt
的 schema 不变式与通过/失败语义。
"""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import ValidationError

from automarkov.lifecycle import ArtifactReference
from automarkov.rllib_training import (
    RllibCpuSmokeAttempt,
    RllibCpuSmokeContract,
    RllibSmokeAssertion,
)


def _assertions() -> tuple[RllibSmokeAssertion, ...]:
    return (
        RllibSmokeAssertion(
            assertion_id="env_runner_sample",
            description="EnvRunner can sample one episode",
            kind="env_runner_sample",
        ),
        RllibSmokeAssertion(
            assertion_id="module_forward",
            description="RLModule forward pass returns valid action distribution",
            kind="module_forward",
        ),
        RllibSmokeAssertion(
            assertion_id="checkpoint_roundtrip",
            description="checkpoint save/load round-trip preserves weights",
            kind="checkpoint_roundtrip",
        ),
        RllibSmokeAssertion(
            assertion_id="action_space_valid",
            description="all sampled actions fall within the action space",
            kind="action_space_valid",
        ),
        RllibSmokeAssertion(
            assertion_id="reward_finite",
            description="all collected rewards are finite",
            kind="reward_finite",
        ),
        RllibSmokeAssertion(
            assertion_id="termination_reachable",
            description="episodes terminate within the max horizon",
            kind="termination_reachable",
        ),
    )


def _smoke_contract() -> RllibCpuSmokeContract:
    return RllibCpuSmokeContract(
        job_manifest=ArtifactReference(
            artifact_id="artifact_" + "a" * 64,
            payload_hash="sha256:" + "e" * 64,
        ),
        assertions=_assertions(),
        minimum_required_assertions=5,
        timeout_seconds=300.0,
    )


def _all_passed_attempt() -> RllibCpuSmokeAttempt:
    return RllibCpuSmokeAttempt(
        attempt_id="smoke_attempt_passed",
        smoke_contract=ArtifactReference(
            artifact_id="artifact_" + "a" * 64,
            payload_hash="sha256:" + "f" * 64,
        ),
        assertion_results=(
            ("env_runner_sample", "passed"),
            ("module_forward", "passed"),
            ("checkpoint_roundtrip", "passed"),
            ("action_space_valid", "passed"),
            ("reward_finite", "passed"),
            ("termination_reachable", "passed"),
        ),
        started_at="2026-08-21T12:00:00Z",
        finished_at="2026-08-21T12:05:00Z",
        passed=True,
    )


# ── RllibSmokeAssertion ─────────────────────────────────────────


class TestRllibSmokeAssertion:
    def test_accepts_valid_assertion(self) -> None:
        a = RllibSmokeAssertion(
            assertion_id="test_1",
            description="verify env runner sample",
            kind="env_runner_sample",
        )
        assert a.kind == "env_runner_sample"

    def test_rejects_empty_description(self) -> None:
        with pytest.raises(ValidationError):
            RllibSmokeAssertion(
                assertion_id="test_2",
                description="",
                kind="module_forward",
            )

    def test_rejects_invalid_kind(self) -> None:
        with pytest.raises(ValidationError):
            RllibSmokeAssertion(
                assertion_id="test_3",
                description="bad kind",
                kind="gpu_benchmark",  # type: ignore[arg-type]
            )

    def test_all_kinds_accepted(self) -> None:
        kinds = [
            "env_runner_sample",
            "module_forward",
            "checkpoint_roundtrip",
            "action_space_valid",
            "reward_finite",
            "termination_reachable",
        ]
        for kind in kinds:
            a = RllibSmokeAssertion(
                assertion_id=f"kind_{kind}",
                description=f"test {kind}",
                kind=kind,  # type: ignore[arg-type]
            )
            assert a.kind == kind


# ── RllibCpuSmokeContract ───────────────────────────────────────


class TestRllibCpuSmokeContract:
    def test_accepts_sufficient_assertions(self) -> None:
        c = _smoke_contract()
        assert len(c.assertions) >= c.minimum_required_assertions

    def test_rejects_insufficient_assertions(self) -> None:
        with pytest.raises(ValidationError):
            RllibCpuSmokeContract(
                job_manifest=ArtifactReference(
                    artifact_id="artifact_" + "a" * 64,
                    payload_hash="sha256:" + "f" * 64,
                ),
                assertions=_assertions()[:1],
                minimum_required_assertions=2,
                timeout_seconds=60.0,
            )

    def test_rejects_duplicate_assertion_ids(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            RllibCpuSmokeContract(
                job_manifest=ArtifactReference(
                    artifact_id="artifact_" + "a" * 64,
                    payload_hash="sha256:" + "f" * 64,
                ),
                assertions=(
                    RllibSmokeAssertion(
                        assertion_id="dup",
                        description="first",
                        kind="module_forward",
                    ),
                    RllibSmokeAssertion(
                        assertion_id="dup",
                        description="second",
                        kind="module_forward",
                    ),
                ),
                minimum_required_assertions=1,
                timeout_seconds=60.0,
            )

    def test_rejects_zero_timeout(self) -> None:
        with pytest.raises(ValidationError):
            RllibCpuSmokeContract(
                job_manifest=ArtifactReference(
                    artifact_id="artifact_" + "a" * 64,
                    payload_hash="sha256:" + "f" * 64,
                ),
                assertions=_assertions()[:2],
                minimum_required_assertions=2,
                timeout_seconds=0.0,
            )

    def test_round_trips(self) -> None:
        c = _smoke_contract()
        reloaded = RllibCpuSmokeContract.model_validate(
            c.model_dump(mode="json"), strict=True
        )
        assert reloaded.minimum_required_assertions == c.minimum_required_assertions


# ── RllibCpuSmokeAttempt ───────────────────────────────────────


class TestRllibCpuSmokeAttempt:
    def test_passed_attempt_all_assertions_passed(self) -> None:
        a = _all_passed_attempt()
        assert a.passed is True
        assert all(r[1] == "passed" for r in a.assertion_results)

    def test_failed_attempt_with_failure(self) -> None:
        ids = [a.assertion_id for a in _assertions()]
        results: tuple[
            tuple[str, Literal["passed", "failed", "skipped"]],
            tuple[str, Literal["passed", "failed", "skipped"]],
            tuple[str, Literal["passed", "failed", "skipped"]],
            tuple[str, Literal["passed", "failed", "skipped"]],
            tuple[str, Literal["passed", "failed", "skipped"]],
            tuple[str, Literal["passed", "failed", "skipped"]],
        ] = (
            (ids[0], "passed"),
            (ids[1], "passed"),
            (ids[2], "failed"),
            (ids[3], "passed"),
            (ids[4], "passed"),
            (ids[5], "passed"),
        )
        attempt = RllibCpuSmokeAttempt(
            attempt_id="smoke_attempt_failed",
            smoke_contract=ArtifactReference(
                artifact_id="artifact_" + "a" * 64,
                payload_hash="sha256:" + "f" * 64,
            ),
            assertion_results=results,
            started_at="2026-08-21T12:00:00Z",
            finished_at="2026-08-21T12:05:00Z",
            passed=False,
        )
        assert attempt.passed is False
        assert sum(1 for r in attempt.assertion_results if r[1] == "failed") == 1

    def test_hash_is_deterministic(self) -> None:
        a1 = _all_passed_attempt()
        a2 = _all_passed_attempt()
        assert a1.hash == a2.hash

    def test_hash_differs_on_failure(self) -> None:
        passed = _all_passed_attempt()
        failed = RllibCpuSmokeAttempt(
            attempt_id="smoke_attempt_failed",
            smoke_contract=ArtifactReference(
                artifact_id="artifact_" + "a" * 64,
                payload_hash="sha256:" + "f" * 64,
            ),
            assertion_results=(
                ("env_runner_sample", "failed"),
                ("module_forward", "failed"),
                ("checkpoint_roundtrip", "failed"),
                ("action_space_valid", "failed"),
                ("reward_finite", "failed"),
                ("termination_reachable", "failed"),
            ),
            started_at="2026-08-21T12:00:00Z",
            finished_at="2026-08-21T12:05:00Z",
            passed=False,
        )
        assert passed.hash != failed.hash
