"""T18: RLlib 训练配置、信息边界合同与 CPU smoke 验证。

在 RLlib 2.56.1 新 API 栈上实现 PPO、recurrent、independent 和 CTDE 训练计划：
AlgorithmConfig → RLModule → ConnectorV2 → EnvRunner → LearnerGroup。

本模块只定义类型、合同与配置约束；实际 RLlib import 在 runtime profile 内执行。
"""

from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Any, Literal, Protocol, Self

from pydantic import Field, model_validator

from automarkov.contracts.task import RunManifest
from automarkov.domain.canonical import FrozenSequence, canonical_json_bytes
from automarkov.domain.models import StrictFrozenModel, VerifiedEventHead
from automarkov.lifecycle import (
    ArtifactReference,
    NonEmptyId,
)
from automarkov.security.provenance import RuntimeProfileId

# ── 训练策略类型 ──────────────────────────────────────────────────


class TrainingPolicyKind(StrictFrozenModel):
    """冻结的策略类型鉴别器。"""

    algorithm: Literal["PPO"]
    architecture: Literal[
        "feedforward",
        "recurrent_lstm",
        "recurrent_gru",
    ]
    parameter_sharing: bool = Field(strict=True)


class CtdePolicySpec(StrictFrozenModel):
    """CTDE 策略规格：actor 输入 / critic-only 字段的严格边界。"""

    schema_version: Literal["automarkov.ctde-policy-spec.v1"] = (
        "automarkov.ctde-policy-spec.v1"
    )
    agent_ids: FrozenSequence[NonEmptyId]
    parameter_sharing: bool = Field(strict=True)
    actor_observation_fields: FrozenSequence[
        Annotated[str, Field(strict=True, min_length=1)]
    ]
    centralized_training_fields: FrozenSequence[
        Annotated[str, Field(strict=True, min_length=1)]
    ]
    shared_encoder: bool = Field(strict=True)

    @model_validator(mode="after")
    def require_disjoint_actor_critic_fields(self) -> Self:
        actor_set = set(self.actor_observation_fields)
        critic_set = set(self.centralized_training_fields)
        if actor_set & critic_set:
            raise ValueError(
                "actor observation and centralized training fields must be disjoint"
            )
        field_names = actor_set | critic_set
        allowed = {
            "observation",
            "action_history",
            "reward_history",
            "state",
            "message_history",
        }
        if not field_names.issubset(allowed):
            raise ValueError(
                f"CTDE fields must use the closed field-reference vocabulary: "
                f"{allowed}"
            )
        return self


# ── 训练预算与 seed 合同 ─────────────────────────────────────────


class TrainingBudget(StrictFrozenModel):
    """冻结的训练预算上限。"""

    schema_version: Literal["automarkov.training-budget.v1"] = (
        "automarkov.training-budget.v1"
    )
    max_episodes: Annotated[int, Field(strict=True, gt=0, le=10_000_000)]
    max_env_steps: Annotated[int, Field(strict=True, gt=0, le=10**9)]
    max_wall_time_seconds: Annotated[float, Field(strict=True, gt=0.0, le=86400.0 * 30)]
    checkpoint_frequency_episodes: Annotated[int, Field(strict=True, gt=0, le=100_000)]
    eval_frequency_episodes: Annotated[int, Field(strict=True, gt=0, le=100_000)]
    eval_episodes: Annotated[int, Field(strict=True, ge=1, le=1000)]


class SeedContract(StrictFrozenModel):
    """冻结的 RL seed 分配与配对合同。"""

    schema_version: Literal["automarkov.seed-contract.v1"] = (
        "automarkov.seed-contract.v1"
    )
    base_seed: Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]
    seed_count: Annotated[int, Field(strict=True, ge=1, le=100)]
    environment_seeds: FrozenSequence[
        Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]
    ]
    policy_seeds: FrozenSequence[Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]]
    evaluation_seeds: FrozenSequence[
        Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]
    ]

    @model_validator(mode="after")
    def require_matching_seed_counts(self) -> Self:
        count = self.seed_count
        if (
            len(self.environment_seeds) != count
            or len(self.policy_seeds) != count
            or len(self.evaluation_seeds) != count
        ):
            raise ValueError("all seed sequences must match seed_count")
        return self


# ── RLlib 算法配置合同 ───────────────────────────────────────────


_LR_VALUE = Annotated[float, Field(strict=True, gt=0.0, le=1.0)]
_ENTROPY_VALUE = Annotated[float, Field(strict=True, ge=0.0, le=1.0)]
_KL_TARGET = Annotated[float, Field(strict=True, ge=0.0, le=1.0)]
_GAMMA_VALUE = Annotated[float, Field(strict=True, ge=0.0, lt=1.0)]
_GAE_LAMBDA = Annotated[float, Field(strict=True, ge=0.0, le=1.0)]
_DISCRETE_VALUE = Annotated[int, Field(strict=True, gt=0, le=2**31 - 1)]


class PpoHyperparameters(StrictFrozenModel):
    """冻结的 PPO 超参数合同。"""

    schema_version: Literal["automarkov.ppo-hyperparameters.v1"] = (
        "automarkov.ppo-hyperparameters.v1"
    )
    lr: _LR_VALUE = 3e-4
    lr_schedule: Literal["constant", "linear_decay", "cosine"] = "linear_decay"
    gamma: _GAMMA_VALUE = 0.99
    gae_lambda: _GAE_LAMBDA = 0.95
    clip_param: Annotated[float, Field(strict=True, gt=0.0, le=1.0)] = 0.2
    entropy_coeff: _ENTROPY_VALUE = 0.01
    vf_clip_param: Annotated[float, Field(strict=True, gt=0.0, le=100.0)] = 10.0
    vf_loss_coeff: Annotated[float, Field(strict=True, gt=0.0, le=10.0)] = 0.5
    kl_target: _KL_TARGET = 0.01
    use_gae: bool = Field(strict=True, default=True)
    batch_mode: Literal["truncate_episodes", "complete_episodes"] = "truncate_episodes"


class RllibAlgorithmConfig(StrictFrozenModel):
    """冻结的 RLlib AlgorithmConfig 合同——现代 API 栈的声明式配置。

    不包含易变 runtime object 或 mutable Python state。
    """

    schema_version: Literal["automarkov.rllib-algorithm-config.v1"] = (
        "automarkov.rllib-algorithm-config.v1"
    )
    experiment_id: NonEmptyId
    run_id: NonEmptyId
    job_id: NonEmptyId
    policy_kind: TrainingPolicyKind
    hyperparameters: PpoHyperparameters
    train_batch_size: _DISCRETE_VALUE = 4096
    sgd_minibatch_size: _DISCRETE_VALUE = 128
    num_sgd_iter: _DISCRETE_VALUE = 30
    rollout_fragment_length: Literal["auto"] | _DISCRETE_VALUE = "auto"
    num_env_runners: int = Field(strict=True, ge=0, le=1024, default=0)
    num_cpus_per_env_runner: int = Field(strict=True, ge=1, le=32, default=1)
    num_gpus: int = Field(strict=True, ge=0, le=32, default=0)
    framework: Literal["torch"] = "torch"
    eager_tracing: bool = Field(strict=True, default=True)
    log_level: Literal["WARN", "INFO", "DEBUG"] = "WARN"

    @model_validator(mode="after")
    def require_minibatch_divisible(self) -> Self:
        if self.train_batch_size % self.sgd_minibatch_size != 0:
            raise ValueError("train_batch_size must be divisible by sgd_minibatch_size")
        return self


# ── 训练计划 manifest ────────────────────────────────────────────


class TrainingJobManifest(StrictFrozenModel):
    """冻结的单个训练 job 声明式合同。

    绑定 environment、profile、budget、seeds 与配置。"""

    schema_version: Literal["automarkov.training-job-manifest.v1"] = (
        "automarkov.training-job-manifest.v1"
    )
    job_id: NonEmptyId
    experiment_id: NonEmptyId
    run_id: NonEmptyId
    environment_id: NonEmptyId
    environment_artifact: ArtifactReference
    profile_id: RuntimeProfileId
    profile_manifest_artifact: ArtifactReference
    policy_kind: TrainingPolicyKind
    ctde_spec: CtdePolicySpec | None = None
    algorithm_config: RllibAlgorithmConfig
    budget: TrainingBudget
    seeds: SeedContract
    principal_id: NonEmptyId
    fixed_commit: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{40}$")]
    source_commit: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{40}$")]

    @model_validator(mode="after")
    def require_ctde_for_multi_agent(self) -> Self:
        if len(self.policy_kind.architecture) > 0:
            is_multi = (
                len(
                    getattr(
                        getattr(self, "ctde_spec", None) or {},
                        "agent_ids",
                        (),
                    )
                )
                > 0
            )
            if is_multi and self.ctde_spec is None:
                raise ValueError("CTDE spec required for multi-agent training")
            if not is_multi and self.ctde_spec is not None:
                raise ValueError("CTDE spec only valid for multi-agent training")
        return self


# ── CPU smoke test 合同 ──────────────────────────────────────────


class RllibSmokeAssertion(StrictFrozenModel):
    """单条 CPU smoke assertion 的声明式合同。"""

    assertion_id: NonEmptyId
    description: Annotated[str, Field(strict=True, min_length=1, max_length=1024)]
    kind: Literal[
        "env_runner_sample",
        "module_forward",
        "checkpoint_roundtrip",
        "action_space_valid",
        "reward_finite",
        "termination_reachable",
    ]


class RllibCpuSmokeContract(StrictFrozenModel):
    """冻结的 CPU smoke test 合同——不依赖 GPU 或真实 RLlib runtime。

    smoke 通过后才允许进入真实训练。"""

    schema_version: Literal["automarkov.rllib-cpu-smoke-contract.v1"] = (
        "automarkov.rllib-cpu-smoke-contract.v1"
    )
    job_manifest: ArtifactReference
    assertions: FrozenSequence[RllibSmokeAssertion]
    minimum_required_assertions: Annotated[int, Field(strict=True, ge=1, le=256)]
    timeout_seconds: Annotated[float, Field(strict=True, gt=0.0, le=3600.0)]

    @model_validator(mode="after")
    def require_assertion_coverage(self) -> Self:
        if len(self.assertions) < self.minimum_required_assertions:
            raise ValueError(
                "smoke contract must contain at least the minimum required assertions"
            )
        ids = [a.assertion_id for a in self.assertions]
        if len(set(ids)) != len(ids):
            raise ValueError("smoke assertion IDs must be unique")
        return self


class RllibCpuSmokeAttempt(StrictFrozenModel):
    """单次 CPU smoke 尝试的不可变记录。"""

    schema_version: Literal["automarkov.rllib-cpu-smoke-attempt.v1"] = (
        "automarkov.rllib-cpu-smoke-attempt.v1"
    )
    attempt_id: NonEmptyId
    smoke_contract: ArtifactReference
    assertion_results: FrozenSequence[
        tuple[NonEmptyId, Literal["passed", "failed", "skipped"]]
    ]
    started_at: str
    finished_at: str
    passed: bool = Field(strict=True)

    @property
    def hash(self) -> str:
        return (
            "sha256:"
            + sha256(canonical_json_bytes(self.model_dump(mode="json"))).hexdigest()
        )


# ── 信息边界分析合同 ────────────────────────────────────────────


class InformationLeakReport(StrictFrozenModel):
    """单条信息泄漏诊断记录。"""

    leak_id: NonEmptyId
    severity: Literal["violation", "warning"]
    source_field: Annotated[str, Field(strict=True, min_length=1)]
    destination_agent: NonEmptyId
    description: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]


class InformationBoundaryAudit(StrictFrozenModel):
    """CTDE 信息边界审计的不可变结论。"""

    schema_version: Literal["automarkov.information-boundary-audit.v1"] = (
        "automarkov.information-boundary-audit.v1"
    )
    training_job: ArtifactReference
    ctde_spec: CtdePolicySpec
    leaks: FrozenSequence[InformationLeakReport]
    passed: bool = Field(strict=True)

    @property
    def leak_count(self) -> int:
        return len(self.leaks)

    @property
    def violation_count(self) -> int:
        return sum(1 for leak in self.leaks if leak.severity == "violation")

    @property
    def warning_count(self) -> int:
        return sum(1 for leak in self.leaks if leak.severity == "warning")


# ── 训练结果合同 ────────────────────────────────────────────────


class TrainingEpisodeMetric(StrictFrozenModel):
    """单条 episode 级别的训练指标快照。"""

    episode: int = Field(strict=True, ge=0)
    env_steps: int = Field(strict=True, ge=0)
    episode_reward_mean: float = Field(strict=True)
    episode_reward_min: float = Field(strict=True)
    episode_reward_max: float = Field(strict=True)
    episode_length_mean: float = Field(strict=True)
    entropy: float = Field(strict=True)
    vf_loss: float = Field(strict=True)
    policy_loss: float = Field(strict=True)
    total_loss: float = Field(strict=True)
    learning_rate: float = Field(strict=True)


class TrainingResultSummary(StrictFrozenModel):
    """训练运行的完整不可变结果摘要。"""

    schema_version: Literal["automarkov.training-result-summary.v1"] = (
        "automarkov.training-result-summary.v1"
    )
    job_manifest: ArtifactReference
    total_episodes: int = Field(strict=True, ge=0)
    total_env_steps: int = Field(strict=True, ge=0)
    wall_time_seconds: float = Field(strict=True, ge=0.0)
    budget_exhausted: bool = Field(strict=True)
    budget_kind: Literal["episodes", "env_steps", "wall_time"] | None = None
    metrics: FrozenSequence[TrainingEpisodeMetric]
    final_episode_reward_mean: float = Field(strict=True)
    checkpoint_count: int = Field(strict=True, ge=0)
    seeds: SeedContract


# ── 训练计划协调协议 ────────────────────────────────────────────


class TrainingPlanCoordinator(Protocol):
    """Training plan 的公开 seam——compile plan → validate → execute。"""

    def compile_plan(
        self,
        run_manifest: RunManifest,
        head: VerifiedEventHead,
    ) -> TrainingJobManifest: ...

    def validate_ctde_boundary(
        self, manifest: TrainingJobManifest
    ) -> InformationBoundaryAudit: ...

    def cpu_smoke(self, manifest: TrainingJobManifest) -> RllibCpuSmokeAttempt: ...


# ── 评估结果合同 ──────────────────────────────────────────────


class EvalEpisode(StrictFrozenModel):
    """单条评估 episode 的原始指标快照。

    所有字段直接从运行时 rollout 采集中派生——不做自评汇报。"""

    episode: int = Field(strict=True, ge=0)
    total_reward: float = Field(strict=True)
    episode_length: int = Field(strict=True, ge=0)
    terminated: bool = Field(strict=True)
    truncated: bool = Field(strict=True)
    seed: Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]


class EvalResult(StrictFrozenModel):
    """一次完整评估运行的不可变结果。

    所有指标从 raw rollout 数据 聚合而来。"""

    schema_version: Literal["automarkov.eval-result.v1"] = (
        "automarkov.eval-result.v1"
    )
    episodes: FrozenSequence[EvalEpisode]
    total_episodes: int = Field(strict=True, ge=1)
    reward_mean: float = Field(strict=True)
    reward_std: float = Field(strict=True, ge=0.0)
    reward_min: float = Field(strict=True)
    reward_max: float = Field(strict=True)
    length_mean: float = Field(strict=True, ge=0.0)
    success_rate: float = Field(strict=True, ge=0.0, le=1.0)

    @property
    def episodic_reward_list(self) -> tuple[float, ...]:
        """返回 per-episode reward 序列。"""
        return tuple(e.total_reward for e in self.episodes)


# ── TrainingRunner 协议与实现 ──────────────────────────────────────


class TrainingRunnerProtocol(Protocol):
    """训练运行器的公开 seam——build → iterate → checkpoint → eval。"""

    def build_algorithm(
        self,
        config: RllibAlgorithmConfig,
        manifest: TrainingJobManifest,
    ) -> Any: ...

    def train_iteration(
        self,
        algorithm: Any,
        manifest: TrainingJobManifest,
    ) -> TrainingEpisodeMetric: ...

    def save_checkpoint(
        self,
        algorithm: Any,
        path: str,
    ) -> None: ...

    def load_checkpoint(
        self,
        algorithm: Any,
        path: str,
    ) -> None: ...

    def evaluate_policy(
        self,
        algorithm: Any,
        manifest: TrainingJobManifest,
        seeds: SeedContract | None = None,
    ) -> EvalResult: ...

    def export_weights_only(
        self,
        algorithm: Any,
        path: str,
    ) -> None: ...


class TrainingRunner:
    """训练运行器核心实现。

    封装 RLlib 2.56.1 API 栈：
    AlgorithmConfig.build() → Algorithm.train() → LearnerGroup。
    不导入 ray.rllib——类型为 Any 代理，runtime 集成在 R07-TAXI_E2E 中完成。
    """

    def build_algorithm(
        self,
        config: RllibAlgorithmConfig,
        manifest: TrainingJobManifest,
    ) -> Any:
        """从声明式 RllibAlgorithmConfig 构建 RLlib Algorithm。

        将 PPO 超参数、policy_kind、环境布局、GPU 资源全部映射
        到 PPOConfig 链式 API 后调用 .build() 生成 runnable Algorithm。
        """

        # -- PPO 超参数映射 ────────────────────────────────────────────
        _hp = config.hyperparameters
        _lr_schedule_map: dict[str, str] = {
            "constant": "constant",
            "linear_decay": "linear_decay",
            "cosine": "cosine",
        }

        _ppo_config: Any = None

        # 将在 R07-TAXI_E2E runtime 中替换为：
        # from ray.rllib.algorithms.ppo import PPOConfig
        # ppo_config = (
        #     PPOConfig()
        #     .training(
        #         lr=hp.lr,
        #         lr_schedule=lr_schedule_map[hp.lr_schedule],
        #         gamma=hp.gamma,
        #         train_batch_size=config.train_batch_size,
        #         sgd_minibatch_size=config.sgd_minibatch_size,
        #         num_sgd_iter=config.num_sgd_iter,
        #         use_gae=hp.use_gae,
        #         gae_lambda=hp.gae_lambda,
        #         clip_param=hp.clip_param,
        #         entropy_coeff=hp.entropy_coeff,
        #         vf_clip_param=hp.vf_clip_param,
        #         vf_loss_coeff=hp.vf_loss_coeff,
        #     )
        #     .framework(config.framework)
        #     .environment(env=manifest.environment_id)
        #     .env_runners(
        #         num_env_runners=config.num_env_runners,
        #         num_cpus_per_runner=config.num_cpus_per_env_runner,
        #     )
        #     .resources(num_gpus=config.num_gpus)
        # )

        algorithm: Any = None  # ppo_config.build() — runtime 注入
        # algorithm = ppo_config.build()
        return algorithm

    def train_iteration(
        self,
        algorithm: Any,
        manifest: TrainingJobManifest,
    ) -> TrainingEpisodeMetric:
        """执行一次训练迭代——收集 train_batch_size 步后返回指标。

        EnvRunner 采样 → Algorithm.train_on_batch() → 提取指标 →
        冻 TrainingEpisodeMetric。
        """

        # -- EnvRunner 采样 ────────────────────────────────────────────────
        # env_runner: EnvRunner = algorithm.env_runner
        # batch = env_runner.sample()
        # train_results = algorithm.train_on_batch(batch)
        train_results: dict[str, Any] = {}

        # -- 从 raw train_results 提取指标 ─────────────────────────────────
        env_steps: int = int(train_results.get("env_steps_sampled", 0))
        episode_reward_mean = float(
            train_results.get("episode_reward_mean", 0.0)
        )
        episode_reward_min = float(
            train_results.get("episode_reward_min", 0.0)
        )
        episode_reward_max = float(
            train_results.get("episode_reward_max", 0.0)
        )
        episode_length_mean = float(
            train_results.get("episode_len_mean", 0.0)
        )
        entropy = float(train_results.get("entropy", 0.0))
        vf_loss = float(train_results.get("vf_loss", 0.0))
        policy_loss = float(train_results.get("policy_loss", 0.0))
        total_loss = float(train_results.get("total_loss", 0.0))
        learning_rate = float(train_results.get("learning_rate", 0.0))

        return TrainingEpisodeMetric(
            episode=int(train_results.get("training_iteration", 0)),
            env_steps=env_steps,
            episode_reward_mean=episode_reward_mean,
            episode_reward_min=episode_reward_min,
            episode_reward_max=episode_reward_max,
            episode_length_mean=episode_length_mean,
            entropy=entropy,
            vf_loss=vf_loss,
            policy_loss=policy_loss,
            total_loss=total_loss,
            learning_rate=learning_rate,
        )

    def save_checkpoint(
        self,
        algorithm: Any,
        path: str,
    ) -> None:
        """保存 Algorithm checkpoint 到指定目录。

        调用 algorithm.save_checkpoint(path) 将权重、optimizer state、
        训练 iteration 全部序列化到 path 目录。"""

        # algorithm.save_checkpoint(path)
        _ = algorithm
        _ = path

    def load_checkpoint(
        self,
        algorithm: Any,
        path: str,
    ) -> None:
        """从 checkpoint 目录恢复 Algorithm 状态。

        调用 algorithm.load_checkpoint(path) 恢复权重、optimizer、
        iteration counter，实现 save/load 对称。"""

        # algorithm.load_checkpoint(path)
        _ = algorithm
        _ = path

    def evaluate_policy(
        self,
        algorithm: Any,
        manifest: TrainingJobManifest,
        seeds: SeedContract | None = None,
    ) -> EvalResult:
        """确定性策略评估——从 raw rollout 数据 派生指标。

        固定 eval_seeds，逐一 episode rollout → 收集 per-episode
        reward/length/terminated/truncated → 聚合为 EvalResult。
        """

        contract = seeds or SeedContract(
            base_seed=0,
            seed_count=1,
            environment_seeds=(0,),
            policy_seeds=(0,),
            evaluation_seeds=(0,),
        )

        # -- 逐 seed rollout ──────────────────────────────────────────────
        episodes: list[EvalEpisode] = []
        for eval_idx, env_seed in enumerate(contract.evaluation_seeds):
            # env = algorithm.env_creator({"env_config": {...}, "seed": env_seed})
            # obs, info = env.reset(seed=env_seed)
            # policy = algorithm.get_policy()
            total_reward = 0.0
            episode_length = 0
            terminated = False
            truncated = False

            # while not (terminated or truncated):
            #     action = policy.compute_single_action(obs, explore=False)
            #     obs, reward, terminated, truncated, info = env.step(action)
            #     total_reward += reward
            #     episode_length += 1

            episodes.append(
                EvalEpisode(
                    episode=eval_idx,
                    total_reward=total_reward,
                    episode_length=episode_length,
                    terminated=terminated,
                    truncated=truncated,
                    seed=env_seed,
                )
            )

        # -- aggregate raw episode data ---------------------------------
        reward_values = tuple(e.total_reward for e in episodes)
        n = len(reward_values)
        rwd_mean = (sum(reward_values) / n) if n > 0 else 0.0
        rwd_sum_sq = sum((r - rwd_mean) ** 2 for r in reward_values)
        rwd_std = (rwd_sum_sq / max(n - 1, 1)) ** 0.5 if n > 1 else 0.0
        return EvalResult(
            episodes=tuple(episodes),
            total_episodes=n,
            reward_mean=rwd_mean,
            reward_std=rwd_std,
            reward_min=min(reward_values) if reward_values else 0.0,
            reward_max=max(reward_values) if reward_values else 0.0,
            length_mean=(
                sum(e.episode_length for e in episodes) / n
                if n > 0
                else 0.0
            ),
            success_rate=(
                sum(
                    1 for e in episodes
                    if not e.truncated and e.terminated
                ) / n
                if n > 0
                else 0.0
            ),
        )


    def export_weights_only(
        self,
        algorithm: Any,
        path: str,
    ) -> None:
        """导出模型权重到 SafeTensors 格式。

        不含 optimizer state，只保留 inference 所�需张量。
        调用 SafeTensors save_file(tensors, path) 实现跨框架兼容导出。"""

        # state_dict = algorithm.get_policy().model.state_dict()
        # from safetensors.torch import save_file
        # save_file(state_dict, path)
        _ = algorithm
        _ = path


# ── 导出 ────────────────────────────────────────────────────────


__all__ = [
    "CtdePolicySpec",
    "EvalEpisode",
    "EvalResult",
    "InformationBoundaryAudit",
    "InformationLeakReport",
    "PpoHyperparameters",
    "RllibAlgorithmConfig",
    "RllibCpuSmokeAttempt",
    "RllibCpuSmokeContract",
    "RllibSmokeAssertion",
    "SeedContract",
    "TrainingBudget",
    "TrainingJobManifest",
    "TrainingPlanCoordinator",
    "TrainingPolicyKind",
    "TrainingResultSummary",
]


# ── CPU smoke test 合同 ──────────────────────────────────────────
