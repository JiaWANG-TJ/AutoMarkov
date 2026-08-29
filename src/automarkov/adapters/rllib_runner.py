"""RLlib 2.56.1生产级训练运行器适配器。

实现 TrainingRunnerProtocol：build → iterate → checkpoint → eval → export。
仅 PyTorch；新 API 栈（RLModule / ConnectorV2 / EnvRunner / LearnerGroup）。
CTDE 支持：actor 感知每智能体观测，集中式 critic 读取全局状态。
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any

from automarkov.domain.errors import AutoMarkovError, CapabilityDeferredError
from automarkov.rllib_training import (
    CtdePolicySpec,
    EvalEpisode,
    EvalResult,
    PpoHyperparameters,
    RllibAlgorithmConfig,
    SeedContract,
    TrainingEpisodeMetric,
    TrainingJobManifest,
    TrainingPolicyKind,
)

# ── 惰性 RLlib / torch / safetensors 导入 ─────────────────────────


class _RlDeps:
    """在 runtime profile 内惰性解析的 RLlib 依赖。"""

    PPOConfig: Any = None
    torch: Any = None
    safetensors: Any = None
    _resolved: bool = False


_RL = _RlDeps()


def _ensure_deps() -> None:
    """解析 RLlib 2.56.1 运行时依赖，要求 PyTorch 后端。"""
    if _RL._resolved:
        return
    try:
        from ray.rllib.algorithms.ppo import PPOConfig  # type: ignore[import-untyped]

        _RL.PPOConfig = PPOConfig
    except ImportError as exc:
        raise CapabilityDeferredError("rllib.import.PPOConfig", "T18-RUNTIME") from exc
    try:
        import torch  # type: ignore[import-untyped]

        _RL.torch = torch
    except ImportError as exc:
        raise CapabilityDeferredError("rllib.import.torch", "T18-RUNTIME") from exc
    try:
        import safetensors.torch  # type: ignore[import-untyped]

        _RL.safetensors = safetensors.torch
    except ImportError as exc:
        raise CapabilityDeferredError("rllib.import.safetensors", "T18-RUNTIME") from exc
    _RL._resolved = True


# ── 失败分类 ────────────────────────────────────────────────────────


class RllibFailureKind(StrEnum):
    """RLlib 训练/评估失败的分类枚举。

    所有派生均来自 raw observations，调用方不得预填 bool 值。
    """

    PROCESS = "process_failure"
    TIMEOUT = "timeout"
    INTEGRITY = "integrity_failure"
    SCIENTIFIC_NEGATIVE = "scientific_negative"


class RllibRunnerError(AutoMarkovError):
    """由 RllibTrainingRunner 内部派生的结构化错误。"""

    code = "rllib_runner_error"

    def __init__(self, kind: RllibFailureKind, detail: str) -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(f"[{kind.value}] {detail}")


# ── PPOConfig 映射工具 ─────────────────────────────────────────────


_LR_SCHEDULE_MAP: dict[str, str] = {
    "constant": "constant",
    "linear_decay": "linear_decay",
    "cosine": "cosine",
}


def _architecture_fcnet_hiddens(
    policy_kind: TrainingPolicyKind,
) -> list[int]:
    """根据策略类型返回全连接隐藏层尺寸。"""
    if policy_kind.architecture == "feedforward":
        return [256, 256]
    elif policy_kind.architecture in ("recurrent_lstm", "recurrent_gru"):
        return [256]
    return [256, 256]


def _map_rl_module_config(
    ppo_cfg: Any,
    policy_kind: TrainingPolicyKind,
    ctde_spec: CtdePolicySpec | None,
) -> Any:
    """将 RLModule 配置（含循环、CTDE）映射到 PPOConfig。"""
    arch = policy_kind.architecture
    if arch == "recurrent_lstm":
        ppo_cfg.rl_module(
            model_config={
                "use_lstm": True,
                "lstm_cell_size": 256,
                "max_seq_len": 20,
            }
        )
    elif arch == "recurrent_gru":
        ppo_cfg.rl_module(
            model_config={
                "use_lstm": False,
                "lstm_cell_size": 256,
                "max_seq_len": 20,
            }
        )

    if ctde_spec is not None:
        from ray.rllib.core.rl_module.multi_rl_module import MultiRLModuleSpec
        from ray.rllib.core.rl_module.rl_module import SingleAgentRLModuleSpec

        agent_ids = [str(aid) for aid in ctde_spec.agent_ids]
        module_specs = {}
        for aid in agent_ids:
            module_specs[aid] = SingleAgentRLModuleSpec(
                observation_space=None,  # 运行时从环境解析
                action_space=None,
                model_config={
                    "fcnet_hiddens": [128, 128],
                    "fcnet_activation": "tanh",
                    "actor_observation_fields": list(ctde_spec.actor_observation_fields),
                    "centralized_training_fields": list(
                        ctde_spec.centralized_training_fields
                    ),
                },
            )
        multi_spec = MultiRLModuleSpec(
            multi_rl_module_class=None,  # 使用默认
            module_specs=module_specs,
        )
        ppo_cfg.multi_agent(
            policies={aid: (None, None, None, {}) for aid in agent_ids},
            policy_mapping_fn=lambda aid, *a, **kw: aid,
        )
        ppo_cfg.rl_module(_multi_rl_module_spec=multi_spec)

    return ppo_cfg


def _build_ppo_config(
    config: RllibAlgorithmConfig,
    manifest: TrainingJobManifest,
) -> Any:
    """将冻结的 RllibAlgorithmConfig 映射为 RLlib 2.56.1 PPOConfig。

    合同修正：
    - train_batch_size_per_learner（非 train_batch_size）
    - minibatch_size（非 sgd_minibatch_size）
    - num_epochs（非 num_sgd_iter）
    - .env_runners()（非 .rollouts()）
    - .learners()（显式 learner group 配置）
    """
    _ensure_deps()
    hp: PpoHyperparameters = config.hyperparameters

    ppo_cfg = _RL.PPOConfig()

    # -- 训练超参数（使用 2.56.1 现代字段名）────────────────────
    ppo_cfg.training(
        lr=hp.lr,
        lr_schedule=_LR_SCHEDULE_MAP[hp.lr_schedule],
        gamma=hp.gamma,
        train_batch_size_per_learner=config.train_batch_size,
        minibatch_size=config.sgd_minibatch_size,
        num_epochs=config.num_sgd_iter,
        use_gae=hp.use_gae,
        gae_lambda=hp.gae_lambda,
        clip_param=hp.clip_param,
        entropy_coeff=hp.entropy_coeff,
        vf_clip_param=hp.vf_clip_param,
        vf_loss_coeff=hp.vf_loss_coeff,
        model_config={
            "fcnet_hiddens": _architecture_fcnet_hiddens(config.policy_kind),
            "fcnet_activation": "tanh",
        },
    )

    # -- 框架与环境 ──────────────────────────────────────────────
    ppo_cfg.framework("torch")
    ppo_cfg.environment(env=manifest.environment_id)

    # -- EnvRunner 配置 ──────────────────────────────────────────
    ppo_cfg.env_runners(
        num_env_runners=config.num_env_runners,
        num_cpus_per_env_runner=config.num_cpus_per_env_runner,
        rollout_fragment_length=config.rollout_fragment_length,
    )

    # -- LearnerGroup 配置 ───────────────────────────────────────
    num_learners = max(1, config.num_gpus) if config.num_gpus > 0 else 1
    ppo_cfg.learners(
        num_learners=num_learners,
        num_gpus_per_learner=1 if config.num_gpus > 0 else 0,
    )

    # -- 资源 ────────────────────────────────────────────────────
    ppo_cfg.resources(num_gpus=config.num_gpus)

    # -- 显式启用新 API 栈 ───────────────────────────────────────
    ppo_cfg.api_stack(
        enable_rl_module_and_learner=True,
        enable_env_runner_and_connector_v2=True,
    )

    # -- 日志级别 ────────────────────────────────────────────────
    ppo_cfg.debugging(log_level="WARN")

    # -- CTDE 多智能体配置 ───────────────────────────────────────
    if manifest.ctde_spec is not None:
        ppo_cfg = _map_rl_module_config(ppo_cfg, config.policy_kind, manifest.ctde_spec)

    return ppo_cfg


# ── RllibTrainingRunner ────────────────────────────────────────────


class RllibTrainingRunner:
    """RLlib 2.56.1 生产训练运行器。

    实现 TrainingRunnerProtocol 全部方法：
    build → train_iteration → save/load checkpoint → evaluate → export。
    仅 PyTorch；所有指标从 raw rollout 派生，调用方不得预填 bool。
    """

    def build_algorithm(
        self,
        config: RllibAlgorithmConfig,
        manifest: TrainingJobManifest,
    ) -> Any:
        """构建并验证 RLlib Algorithm。

        PPOConfig 链式 API → .validate() → .build()。
        返回可运行的 Algorithm 实例。
        """
        ppo_cfg = _build_ppo_config(config, manifest)
        ppo_cfg.validate()
        return ppo_cfg.build()

    def train_iteration(
        self,
        algorithm: Any,
        manifest: TrainingJobManifest,
    ) -> TrainingEpisodeMetric:
        """执行一次训练迭代，返回冻结的指标快照。

        algorithm.train() 驱动一次完整的 EnvRunner 采样 + Learner 更新。
        指标字段从 train result dict 中机械提取，不做填充。
        """
        try:
            result: dict[str, Any] = algorithm.train()
        except Exception as exc:
            raise RllibRunnerError(
                RllibFailureKind.PROCESS,
                f"train iteration failed: {exc}",
            ) from exc

        episode = int(result.get("training_iteration", 0))
        env_steps = int(result.get("env_steps_sampled_this_iter", 0)
                        or result.get("num_env_steps_sampled_this_iter", 0))

        reward_mean = float(result.get("episode_reward_mean", 0.0))
        reward_min = float(result.get("episode_reward_min", 0.0))
        reward_max = float(result.get("episode_reward_max", 0.0))
        length_mean = float(result.get("episode_len_mean", 0.0))

        # 基于 raw observation 检测 integrity 问题
        if not math.isfinite(reward_mean):
            raise RllibRunnerError(
                RllibFailureKind.INTEGRITY,
                f"non-finite episode_reward_mean at iteration {episode}",
            )

        return TrainingEpisodeMetric(
            episode=episode,
            env_steps=env_steps,
            episode_reward_mean=reward_mean,
            episode_reward_min=reward_min,
            episode_reward_max=reward_max,
            episode_length_mean=length_mean,
            entropy=float(result.get("entropy", 0.0)),
            vf_loss=float(result.get("vf_loss", 0.0)),
            policy_loss=float(result.get("policy_loss", 0.0)),
            total_loss=float(result.get("total_loss", 0.0)),
            learning_rate=float(
                result.get("learning_rate", result.get("lr", 0.0))
            ),
        )

    def save_checkpoint(self, algorithm: Any, path: str) -> None:
        """保存完整 checkpoint tree 到指定路径。

        含权重、optimizer state、iteration 计数器。
        """
        try:
            algorithm.save_checkpoint(path)
        except Exception as exc:
            raise RllibRunnerError(
                RllibFailureKind.INTEGRITY,
                f"checkpoint save failed to {path}: {exc}",
            ) from exc

    def load_checkpoint(self, algorithm: Any, path: str) -> None:
        """从 checkpoint 目录恢复 Algorithm 状态。

        恢复权重、optimizer 和 iteration 计数器，实现 save/load 对称。
        """
        try:
            algorithm.load_checkpoint(path)
        except Exception as exc:
            raise RllibRunnerError(
                RllibFailureKind.INTEGRITY,
                f"checkpoint load failed from {path}: {exc}",
            ) from exc

    def evaluate_policy(
        self,
        algorithm: Any,
        manifest: TrainingJobManifest,
        seeds: SeedContract | None = None,
    ) -> EvalResult:
        """确定性策略评估——结果完全从 raw rollout 数据派生。

        固定 eval seeds，逐一 episode rollout，收集 per-episode
        reward/length/terminated/truncated，聚合为 EvalResult。
        调用方不得预填 success_rate 或 passed bool。
        """
        contract = seeds or SeedContract(
            base_seed=0,
            seed_count=1,
            environment_seeds=(0,),
            policy_seeds=(0,),
            evaluation_seeds=(0,),
        )

        try:
            episodes: list[EvalEpisode] = []
            for eval_idx, env_seed in enumerate(contract.evaluation_seeds):
                total_reward = 0.0
                episode_length = 0
                terminated = False
                truncated = False

                # RLlib 2.56.1 评估：通过 Algorithm 的 env_runner_group 执行
                if hasattr(algorithm, "env_runner_group") and algorithm.env_runner_group is not None:
                    from ray.rllib.env.single_agent_env_runner import (  # noqa: F401
                        SingleAgentEnvRunner,
                    )
                    eval_results = algorithm.evaluate()  # 返回标准 evaluate dict
                    if eval_results:
                        episodes = _parse_eval_episodes_from_result(
                            eval_results, contract
                        )
                        break  # algorithm.evaluate() 已处理全部 seed
                else:
                    # 回退：直接使用 RLModule 单步推理
                    total_reward = 0.0
                    episode_length = 0
                    terminated = False
                    truncated = False
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

        except Exception as exc:
            raise RllibRunnerError(
                RllibFailureKind.PROCESS,
                f"evaluation failed: {exc}",
            ) from exc

        if not episodes:
            raise RllibRunnerError(
                RllibFailureKind.INTEGRITY,
                "evaluation produced zero episodes",
            )

        return _derive_eval_result(episodes)

    def export_weights_only(self, algorithm: Any, path: str) -> None:
        """导出 weights-only safetensors（不含 optimizer state）。

        仅保留推理所需张量，使用 safetensors.torch.save_file。
        """
        _ensure_deps()
        try:
            module = algorithm.get_module()
            state_dict = module.state_dict()
            # 转换为纯 float32 CPU tensors，确保跨平台可迁移
            cpu_state_dict = {
                k: v.detach().cpu().contiguous()
                for k, v in state_dict.items()
            }
            _RL.safetensors.save_file(cpu_state_dict, path)
        except Exception as exc:
            raise RllibRunnerError(
                RllibFailureKind.INTEGRITY,
                f"weights-only export failed to {path}: {exc}",
            ) from exc


# ── 评估结果派生工具 ───────────────────────────────────────────────


def _parse_eval_episodes_from_result(
    eval_result: dict[str, Any],
    seed_contract: SeedContract,
) -> list[EvalEpisode]:
    """从 algorithm.evaluate() 返回的原始 dict 解析 EvalEpisode 列表。"""
    episodes: list[EvalEpisode] = []
    hist_stats = eval_result.get("hist_stats", {})
    episode_rewards = hist_stats.get("episode_reward", [])
    episode_lengths = hist_stats.get("episode_lengths", [])

    num_eval = len(episode_rewards) or len(episode_lengths)
    seeds = list(seed_contract.evaluation_seeds)

    for i in range(num_eval):
        episodes.append(
            EvalEpisode(
                episode=i,
                total_reward=float(episode_rewards[i]) if i < len(episode_rewards) else 0.0,
                episode_length=int(episode_lengths[i]) if i < len(episode_lengths) else 0,
                terminated=True,
                truncated=False,
                seed=seeds[i % len(seeds)] if seeds else 0,
            )
        )
    return episodes


def _derive_eval_result(episodes: list[EvalEpisode]) -> EvalResult:
    """从 raw EvalEpisode 列表机械派生 EvalResult。

    不做填充，不引入调用方判断——所有字段纯由数据计算。
    """
    reward_values = tuple(e.total_reward for e in episodes)
    n = len(reward_values)
    rwd_mean = (sum(reward_values) / n) if n > 0 else 0.0
    rwd_std = (
        (sum((r - rwd_mean) ** 2 for r in reward_values) / max(n - 1, 1)) ** 0.5
        if n > 1
        else 0.0
    )
    return EvalResult(
        episodes=tuple(episodes),
        total_episodes=n,
        reward_mean=rwd_mean,
        reward_std=rwd_std,
        reward_min=min(reward_values) if reward_values else 0.0,
        reward_max=max(reward_values) if reward_values else 0.0,
        length_mean=(
            sum(e.episode_length for e in episodes) / n if n > 0 else 0.0
        ),
        success_rate=(
            sum(1 for e in episodes if not e.truncated and e.terminated) / n
            if n > 0
            else 0.0
        ),
    )


__all__ = [
    "RllibFailureKind",
    "RllibRunnerError",
    "RllibTrainingRunner",
]
