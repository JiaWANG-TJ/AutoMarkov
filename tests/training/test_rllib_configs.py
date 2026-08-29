"""T18: RLlib AlgorithmConfig 合同测试。

验证 frozen AlgorithmConfig、PPO 超参数与训练计划 manifest 的 schema 不变式。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from automarkov.lifecycle import ArtifactReference
from automarkov.rllib_training import (
    CtdePolicySpec,
    PpoHyperparameters,
    RllibAlgorithmConfig,
    SeedContract,
    TrainingBudget,
    TrainingJobManifest,
    TrainingPolicyKind,
)

# ── helpers ─────────────────────────────────────────────────────


def _ppo_hp() -> PpoHyperparameters:
    return PpoHyperparameters()


def _feedforward_ppo() -> TrainingPolicyKind:
    return TrainingPolicyKind(
        algorithm="PPO",
        architecture="feedforward",
        parameter_sharing=False,
    )


def _recurrent_ppo() -> TrainingPolicyKind:
    return TrainingPolicyKind(
        algorithm="PPO",
        architecture="recurrent_lstm",
        parameter_sharing=False,
    )


def _seeds(count: int = 10) -> SeedContract:
    return SeedContract(
        base_seed=42,
        seed_count=count,
        environment_seeds=tuple(range(count)),
        policy_seeds=tuple(range(100, 100 + count)),
        evaluation_seeds=tuple(range(200, 200 + count)),
    )


def _budget() -> TrainingBudget:
    return TrainingBudget(
        max_episodes=10_000,
        max_env_steps=1_000_000,
        max_wall_time_seconds=3600.0,
        checkpoint_frequency_episodes=100,
        eval_frequency_episodes=500,
        eval_episodes=10,
    )


def _config(
    policy: TrainingPolicyKind | None = None,
    hp: PpoHyperparameters | None = None,
) -> RllibAlgorithmConfig:
    return RllibAlgorithmConfig(
        experiment_id="expt18",
        run_id="runt18001",
        job_id="jobt18001",
        policy_kind=policy or _feedforward_ppo(),
        hyperparameters=hp or _ppo_hp(),
    )


def _ctde_spec() -> CtdePolicySpec:
    return CtdePolicySpec(
        agent_ids=("agent_0", "agent_1"),
        parameter_sharing=True,
        actor_observation_fields=("observation", "reward_history"),
        centralized_training_fields=("state", "action_history"),
        shared_encoder=True,
    )


def _env_artifact() -> tuple[str, str]:
    return (
        "artifact_" + "a" * 64,
        "sha256:" + "a" * 64,
    )


def _profile_artifact() -> tuple[str, str]:
    return (
        "artifact_" + "a" * 64,
        "sha256:" + "b" * 64,
    )


def _training_manifest(
    *,
    policy: TrainingPolicyKind | None = None,
    ctde: CtdePolicySpec | None = None,
) -> TrainingJobManifest:
    env_id, env_hash = _env_artifact()
    profile_id, profile_hash = _profile_artifact()
    return TrainingJobManifest(
        job_id="jobt18smoke",
        experiment_id="expt18",
        run_id="runt18smoke",
        environment_id="taxiv3",
        environment_artifact=ArtifactReference(artifact_id=env_id, payload_hash=env_hash),
        profile_id="profiletrainer",
        profile_manifest_artifact=ArtifactReference(artifact_id=profile_id, payload_hash=profile_hash),
        policy_kind=policy or _feedforward_ppo(),
        ctde_spec=ctde,
        algorithm_config=_config(policy=policy or _feedforward_ppo()),
        budget=_budget(),
        seeds=_seeds(),
        principal_id="principaltrainer",
        fixed_commit="a" * 40,
        source_commit="b" * 40,
    )


# ── PPO 超参数 ─────────────────────────────────────────────────


class TestPpoHyperparameters:
    def test_defaults_are_valid(self) -> None:
        hp = PpoHyperparameters()
        assert hp.lr == 3e-4
        assert hp.gamma == 0.99
        assert hp.use_gae is True

    def test_rejects_zero_learning_rate(self) -> None:
        with pytest.raises(ValidationError):
            PpoHyperparameters(lr=0.0)

    def test_rejects_negative_gamma(self) -> None:
        with pytest.raises(ValidationError):
            PpoHyperparameters(gamma=-0.1)

    def test_rejects_gamma_one(self) -> None:
        with pytest.raises(ValidationError):
            PpoHyperparameters(gamma=1.0)

    def test_rejects_invalid_entropy_coeff(self) -> None:
        with pytest.raises(ValidationError):
            PpoHyperparameters(entropy_coeff=1.5)

    def test_rejects_unsupported_lr_schedule(self) -> None:
        with pytest.raises(ValidationError):
            PpoHyperparameters(lr_schedule="exponential")  # type: ignore[arg-type]

    def test_custom_lr_valid(self) -> None:
        hp = PpoHyperparameters(lr=1e-4, lr_schedule="cosine")
        assert hp.lr == 1e-4


# ── AlgorithmConfig ──────────────────────────────────────────────


class TestRllibAlgorithmConfig:
    def test_accepts_minimal_config(self) -> None:
        cfg = _config()
        assert cfg.policy_kind.algorithm == "PPO"
        assert cfg.framework == "torch"

    def test_rejects_indivisible_batch(self) -> None:
        with pytest.raises(ValidationError, match="divisible"):
            RllibAlgorithmConfig(
                experiment_id="expt18",
                run_id="runt18002",
                job_id="jobt18002",
                policy_kind=_feedforward_ppo(),
                hyperparameters=_ppo_hp(),
                train_batch_size=4000,
                sgd_minibatch_size=300,
            )

    def test_accepts_divisible_batch(self) -> None:
        cfg = RllibAlgorithmConfig(
            experiment_id="expt18",
            run_id="runt18003",
            job_id="jobt18003",
            policy_kind=_feedforward_ppo(),
            hyperparameters=_ppo_hp(),
            train_batch_size=4096,
            sgd_minibatch_size=256,
        )
        assert cfg.train_batch_size % cfg.sgd_minibatch_size == 0

    def test_rejects_gpu_count_negative(self) -> None:
        with pytest.raises(ValidationError):
            RllibAlgorithmConfig(
                experiment_id="expt18",
                run_id="runt18004",
                job_id="jobt18004",
                policy_kind=_feedforward_ppo(),
                hyperparameters=_ppo_hp(),
                num_gpus=-1,  # type: ignore[arg-type]
            )

    def test_rejects_zero_env_runners_negative(self) -> None:
        with pytest.raises(ValidationError):
            RllibAlgorithmConfig(
                experiment_id="expt18",
                run_id="runt18005",
                job_id="jobt18005",
                policy_kind=_feedforward_ppo(),
                hyperparameters=_ppo_hp(),
                num_env_runners=-1,  # type: ignore[arg-type]
            )

    def test_round_trips_through_canonical_json(self) -> None:
        cfg = _config(policy=_recurrent_ppo())
        dumped = cfg.model_dump(mode="json")
        reloaded = RllibAlgorithmConfig.model_validate(dumped, strict=True)
        assert reloaded.policy_kind.architecture == "recurrent_lstm"
        assert reloaded.model_dump() == cfg.model_dump()


# ── SeedContract ─────────────────────────────────────────────────


class TestSeedContract:
    def test_accepts_ten_seeds(self) -> None:
        s = _seeds(10)
        assert s.seed_count == 10
        assert len(s.environment_seeds) == 10

    def test_rejects_mismatched_seed_lengths(self) -> None:
        with pytest.raises(ValidationError):
            SeedContract(
                base_seed=1,
                seed_count=3,
                environment_seeds=(0, 1, 2),
                policy_seeds=(100, 101),
                evaluation_seeds=(200, 201, 202),
            )

    def test_rejects_seed_count_zero(self) -> None:
        with pytest.raises(ValidationError):
            _seeds(0)

    def test_round_trips(self) -> None:
        s = _seeds(5)
        reloaded = SeedContract.model_validate(s.model_dump(mode="json"), strict=True)
        assert reloaded.environment_seeds == s.environment_seeds


# ── TrainingJobManifest ─────────────────────────────────────────


class TestTrainingJobManifest:
    def test_accepts_single_agent_feedforward(self) -> None:
        m = _training_manifest()
        assert m.policy_kind.architecture == "feedforward"
        assert m.ctde_spec is None

    def test_accepts_recurrent_lstm(self) -> None:
        m = _training_manifest(policy=_recurrent_ppo())
        assert m.policy_kind.architecture == "recurrent_lstm"

    def test_accepts_ctde_for_multi_agent(self) -> None:
        m = _training_manifest(ctde=_ctde_spec())
        assert m.ctde_spec is not None
        assert m.ctde_spec.shared_encoder is True

    def test_round_trips_complete_manifest(self) -> None:
        m = _training_manifest()
        dumped = m.model_dump(mode="json")
        reloaded = TrainingJobManifest.model_validate(dumped, strict=True)
        assert reloaded.policy_kind == m.policy_kind
        assert reloaded.seeds.base_seed == m.seeds.base_seed
        assert reloaded.budget.max_episodes == m.budget.max_episodes
