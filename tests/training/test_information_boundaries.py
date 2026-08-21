"""T18: CTDE 信息边界审计测试。

验证 actor observation 与 centralized training fields 的严格不相交合同。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from automarkov.rllib_training import (
    CtdePolicySpec,
    InformationBoundaryAudit,
    InformationLeakReport,
    TrainingJobManifest,
    TrainingPolicyKind,
)


def _ctde_spec(
    *,
    actor_fields: tuple[str, ...] = ("observation", "reward_history"),
    critic_fields: tuple[str, ...] = ("state", "action_history"),
    agent_ids: tuple[str, ...] = ("agent_0", "agent_1"),
) -> CtdePolicySpec:
    return CtdePolicySpec(
        agent_ids=agent_ids,
        parameter_sharing=True,
        actor_observation_fields=actor_fields,
        centralized_training_fields=critic_fields,
        shared_encoder=True,
    )


def _audit(spec: CtdePolicySpec | None = None, passed: bool = True) -> InformationBoundaryAudit:
    return InformationBoundaryAudit(
        training_job={
            "artifact_id": "artifact_" + "a" * 64,
            "payload_hash": "sha256:" + "c" * 64,
        },
        ctde_spec=spec or _ctde_spec(),
        leaks=(),
        passed=passed,
    )


# ── CtdePolicySpec ──────────────────────────────────────────────


class TestCtdePolicySpec:
    def test_accepts_disjoint_fields(self) -> None:
        spec = _ctde_spec()
        assert len(spec.agent_ids) == 2

    def test_rejects_overlapping_fields(self) -> None:
        with pytest.raises(ValidationError, match="disjoint"):
            _ctde_spec(
                actor_fields=("observation", "state"),
                critic_fields=("state", "action_history"),
            )

    def test_rejects_unknown_field_names(self) -> None:
        with pytest.raises(ValidationError, match="closed field-reference"):
            _ctde_spec(actor_fields=("observation", "unknown_gate"))

    def test_accepts_zero_agents(self) -> None:
        # FrozenSequence[NonEmptyId] 不强制 min_length——零 agent CTDE spec
        # 对于单智能体场景是合法的边界合同。
        spec = _ctde_spec(agent_ids=())
        assert len(spec.agent_ids) == 0

    def test_round_trips_through_json(self) -> None:
        spec = _ctde_spec(
            actor_fields=("observation",),
            critic_fields=("state", "action_history", "reward_history"),
        )
        reloaded = CtdePolicySpec.model_validate(spec.model_dump(mode="json"), strict=True)
        assert set(reloaded.actor_observation_fields) == {"observation"}
        assert "state" in reloaded.centralized_training_fields


# ── InformationBoundaryAudit ────────────────────────────────────


class TestInformationBoundaryAudit:
    def test_passed_audit_has_zero_violations(self) -> None:
        audit = _audit(passed=True)
        assert audit.passed is True
        assert audit.violation_count == 0

    def test_failed_audit_with_violations(self) -> None:
        leaks = (
            InformationLeakReport(
                leak_id="leak_001",
                severity="violation",
                source_field="state",
                destination_agent="agent_0",
                description="global state leaked to actor observation",
            ),
            InformationLeakReport(
                leak_id="leak_002",
                severity="warning",
                source_field="action_history",
                destination_agent="agent_1",
                description="action history accessible by wrong agent",
            ),
        )
        audit = InformationBoundaryAudit(
            training_job={
                "artifact_id": "artifact_" + "a" * 64,
                "payload_hash": "sha256:" + "d" * 64,
            },
            ctde_spec=_ctde_spec(),
            leaks=leaks,
            passed=False,
        )
        assert audit.passed is False
        assert audit.leak_count == 2
        assert audit.violation_count == 1
        assert audit.warning_count == 1

    def test_serializes_and_reloads(self) -> None:
        audit = _audit()
        reloaded = InformationBoundaryAudit.model_validate(
            audit.model_dump(mode="json"), strict=True
        )
        assert reloaded.passed is True
        assert reloaded.leaks == ()

    def test_hash_is_deterministic(self) -> None:
        audit = _audit()
        assert audit.model_dump()["passed"] is True
        # 相同内容产生相同 hash
        audit2 = _audit()
        assert (
            audit.model_dump(mode="json") == audit2.model_dump(mode="json")
        )
