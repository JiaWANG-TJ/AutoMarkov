# AutoMarkov 恢复开发：上游规范与一手证据刷新

## 0. 文档合同

### 0.1 Material Passport

- 调研日期：2026-08-25（UTC）
- 文档状态：`ANALYZED_PRIMARY_SOURCES`
- 工作流：`academic-research-suite/deep-research` 的 `lit-review` 与 source-verification 路线
- 研究问题：AutoMarkov 恢复开发时，哪些框架接口、安全边界、供应链控制、学术规范和许可约束必须进入实现与交付合同？
- 证据范围：官方版本文档、官方 GitHub 仓库与 release/tag、官方 package registry、标准正文、会议或研究组织的一手政策
- 非目标：本文不证明 AutoMarkov 已实现这些要求，不证明任一 runtime 可用，不授权依赖升级、受限代码复用、正式实验或发布
- AI 披露：本文由 AI 辅助检索、比对和起草；所有 load-bearing 事实均链接到一手来源，无法核验项单独列出
- Repository tracking gate：`UNTRACKED_PENDING_R02_TYPED_INGRESS`。在R02实现并验证“受限upstream的许可/引用metadata”与“可执行、可复用或可发布的restricted-source ingress”的typed区分前，本文必须保持未跟踪，不得`git add`、commit或push。R02必须先/同commit让provenance verifier对该registered research-document kind通过；禁止通过改名、拼写或删除必要许可事实来绕过扫描。

### 0.2 证据标签

本文严格区分四种陈述：

- **事实**：来源直接陈述或可从固定 tag/commit、官方 API 元数据机械核验。
- **项目推论**：将多个事实映射到 AutoMarkov 的既有架构和研究合同；不是上游原文。
- **建议**：为降低项目风险提出的实施选择；除非另有说明，不是上游强制要求。
- **未核验**：当前证据不足、来源冲突或需要权利人/现场 runtime 才能确认。

不得把“建议”改写为“RLlib/ACM/NeurIPS 强制要求”，也不得把“版本已锁定”改写为“runtime 已验证”。

### 0.3 检索方法与纳入标准

检索渠道包括 Ray/RLlib、Farama、vLLM、Hugging Face safetensors、SLSA、OpenSSF、GitHub、ACM、NeurIPS、Center for Open Science、arXiv 及目标上游仓库。检索词围绕 versioned API、migration、checkpoint、environment compliance、security、artifact attestation、reproducibility、preregistration 和 license。版本事实同时用 package registry 与 Git tag 交叉核验。

纳入规则：

1. API 行为优先采用与项目冻结版本完全一致的 versioned docs 或固定 commit 源码。
2. 安全、许可和学术政策只采用相应项目、标准组织、出版组织或论文登记平台的一手材料。
3. 搜索摘要只用于发现来源；正文结论必须能回到原始页面、固定源码或官方 API。
4. issue、论坛和第三方文章不作为规范依据；仅在说明“仍未解决”时作为候选线索，且本文未据此形成强制结论。
5. `latest` 页面只用于观察上游现状；项目实现合同以冻结版本为准。

局限：ACM 总政策页面对当前检索客户端返回 HTTP 403，因此本文同时保留该官方 URL，并用 ACM SIGSIM/SIGMOD 的一手 artifact-evaluation 页面核验可操作定义；OpenReview 的 Agent² 页面触发浏览器验证，未能用于确认代码归属。

## 1. 结论先行

### 1.1 必须进入恢复计划的八项结论

1. **RLlib 必须按 2.56.1 新 API 栈实现。** `RLModule`、`Learner`、`ConnectorV2`、`EnvRunner` 是职责边界；训练参数使用 `train_batch_size_per_learner`、`minibatch_size`、`num_epochs`，资源使用 `.learners(...)`，采样使用 `.env_runners(...)`。新 API 栈在该版本默认开启。[2.56.1 migration guide](https://docs.ray.io/en/releases-2.56.1/rllib/new-api-stack-migration-guide.html) [fixed AlgorithmConfig source](https://github.com/ray-project/ray/blob/936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a/rllib/algorithms/algorithm_config.py#L450-L456)
2. **RemoteEnv 不是可以宣称“复用稳定官方 ExternalEnv API”的表面。** 2.56.1 文档明确将新栈 external-environment 支持标为 under development，并推荐自定义 `EnvRunner`；因此 AutoMarkov 必须把自己的协议版本、鉴权、重试、step identity 和失败语义作为项目合同验证。[2.56.1 environment reference](https://docs.ray.io/en/releases-2.56.1/rllib/package_ref/env.html)
3. **RLlib checkpoint 只能作为同一可信 profile 内的恢复工件。** 官方 checkpoint 目录包含 `*.pkl`、`class_and_ctor_args.pkl` 以及 pickle/msgpack state；它不是跨安全域的 weights-only 格式。跨 profile 或进入 sealed evaluator 时转换成 manifest-bound safetensors 是合理且必要的项目安全推论。[2.56.1 checkpointing](https://docs.ray.io/en/releases-2.56.1/rllib/checkpoints.html)
4. **Gymnasium/PettingZoo 合规测试是必要条件，不是行为正确性的充分条件。** 必须同时验证 `terminated`/`truncated`、seed replay、空间约束、信息结构、reward/transition oracle 和隐藏行为。[Gymnasium Env API 1.2.2](https://gymnasium.farama.org/v1.2.2/api/env/) [PettingZoo tests 1.26.1](https://pettingzoo.farama.org/1.26.1/content/environment_tests/)
5. **MPE2 full-state 主轨是受控 adaptation，不是 native MPE2 information structure。** `simple_spread_v3` 默认三智能体的单 agent observation 是 18 维，global state 是 54 维；native-local POSG 与给 actor 注入 global state 的 condition 必须分名、分 capability、共享其余合同。[MPE2 Simple Spread](https://mpe2.farama.org/environments/simple_spread/)
6. **vLLM 的 `--api-key` 不是服务边界。** 0.25.1 官方安全页明确说明它只保护特定路径前缀，仍存在未鉴权 inference、utility 和 operational endpoints；必须依靠独立network namespace/按principal防火墙或等价authenticated IPC、鉴权relay route allowlist 和最小端口暴露。与generation worker共享namespace的loopback不是隔离边界。[vLLM 0.25.1 security](https://docs.vllm.ai/en/v0.25.1/usage/security/)
7. **“有 safetensors/SBOM/attestation”均不能单独推出可信。** safetensors 解决 tensor 反序列化任意代码问题，但不证明来源、模型语义或数值有限；SLSA provenance 描述构建来源，但级别声明还取决于 builder、签名和隔离；OpenSSF Scorecard 是风险信号，不是认证。[safetensors format](https://github.com/huggingface/safetensors/blob/a406ca3e7a90598be0cd05a50069cb9bf5ef6ba6/README.md) [SLSA 1.2](https://slsa.dev/spec/v1.2/) [OpenSSF Scorecard checks](https://github.com/ossf/scorecard/blob/main/docs/checks.md)
8. **两个论文上游都必须 fail closed。** Agent2World 根 `LICENSE` 只允许非商业研究评估并禁止分发衍生物和 hosted service；Agent² 候选仓库没有可识别 license，也没有从论文一侧确认的官方归属。前者不得 port/vendor，后者不得集成或发布，除非取得新的可审计授权。[Agent2World LICENSE at audited commit](https://github.com/DeepExperience/agent2world/blob/1330f3cde9509f05d204a255f0f7f43208515dce/LICENSE) [Agent² paper record](https://arxiv.org/abs/2509.13368) [candidate repository](https://github.com/wyjayyo/RL-Agent-Automation/tree/d3ed13755d86c7ed06b52a5a6fb17aa2ce6faf0c)

### 1.2 恢复顺序上的直接含义

**项目推论：** 先修正版本化合同和许可状态，再写训练/实验代码。正确顺序是：冻结 upstream identity → 实现一条 Taxi 新栈 tracer → 同 profile checkpoint round-trip → weights-only export → sealed evaluation → 扩展 MPE2 等 suite → 冻结预注册 → 运行 confirmatory matrix → 生成有 provenance 的 release artifact。反过来先跑大矩阵会把接口漂移、信息泄漏或许可违规固化进结果。

## 2. 冻结版本与“观察到的最新版本”

### 2.1 项目相关版本矩阵

下表的“冻结版本”来自仓库 profile 配置；它仅证明配置意图。Tag commit 由官方 Git tags 在 2026-08-25 重新核验。

| 执行 profile / 组件 | AutoMarkov 冻结版本 | 官方 tag/source commit | 2026-08-25 观察到的 PyPI 最新版 | 结论 |
|---|---:|---|---:|---|
| `rllib-core` / `rllib-taxi-synthesis` / `sealed-evaluator-rllib`: Ray/RLlib | `2.56.1` | `936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a` | `2.58.0` | 保持2.56.1；latest不自动授权升级 |
| `rllib-core` / `rllib-taxi-synthesis` / `env-minigrid` / `env-mpe2` / `sealed-evaluator-rllib`: Gymnasium | `1.2.2` | `a923da5d4415a1aa5195d99341069da5e16deed7` | `1.3.0` | 这些profile用1.2.2 versioned API验收 |
| `sealed-env-taxi-gold`: Gymnasium | `1.3.0` | `53bf3e9a884783eb72ad3fc8b15780914c97c3e1` | `1.3.0` | Taxi gold必须按1.3.0/Taxi-v4身份验收，不能套用core 1.2.2 |
| `env-citylearn`: Gymnasium | `0.28.1` | `7c107f9df1a35a02b02c55579ef3e1777b85ed94` | `1.3.0` | 保留CityLearn兼容profile，不跨profile松pin |
| `env-metadrive` lock: Gymnasium | `1.3.0` | `53bf3e9a884783eb72ad3fc8b15780914c97c3e1` | `1.3.0` | 该transitive lock身份独立于rllib-core |
| RLlib/PettingZoo profiles | `1.26.1` | `1756a4d7494b532651f0024ff7087ef4945432a6` | `1.27.0` | 用1.26.1 tests/API验收 |
| MPE2 | `1.1.0` | `7590d9d52791e321974d4fda6090fb18f34dbf49` | `1.1.0` | 版本与当前 latest 一致 |
| `llm-qwen36-vllm`: vLLM | `0.25.1+cu129` | source release `0.25.1@752a3a504485790a2e8491cacbb35c137339ad34` | `0.27.1` | attached runtime必须保留local-version suffix、wheel/environment和source lineage |
| safetensors | `0.8.0` | `a406ca3e7a90598be0cd05a50069cb9bf5ef6ba6` | `0.8.0` | 冻结 tag 与当前 latest 一致 |

本地版本声明按profile分别见对应`pyproject.toml`、`uv.lock`、`profile.json`和SBOM；不得汇总成一个全局Gymnasium/vLLM版本。最新版本交叉来源为对应的 [PyPI project registry](https://pypi.org/)；固定 tag 可从 [Ray](https://github.com/ray-project/ray/tags)、[Gymnasium](https://github.com/Farama-Foundation/Gymnasium/tags)、[PettingZoo](https://github.com/Farama-Foundation/PettingZoo/tags)、[MPE2](https://github.com/Farama-Foundation/MPE2/tags)、[vLLM](https://github.com/vllm-project/vllm/tags) 与 [safetensors](https://github.com/huggingface/safetensors/tags) 复核。

### 2.2 版本治理规则

**建议：** 所有 profile 共同冻结 package/source/Python/ABI/platform 与权限上限，但身份字段必须按 lifecycle/ingress 分流。`recipe_frozen -> built` profile 另要lockfile、OCI manifest digest、SBOM/license-manifest hashes、build/import-smoke attestations；`attached_unverified` service 不伪造OCI/build fields，而是绑定listener/process、实际package/environment、model/tokenizer/weights、cache/network、脱敏argv和current-connection probes的signed attach manifest。二进制来源证据再按 ingress 分流：PyPI-sourced wheel/sdist 绑定官方 registry filename/URL/SHA-256；VCS source 绑定 exact repository、40-hex commit、source-tree hash 和 signed build attestation；内部 local-version wheel 绑定 immutable internal artifact ID、wheel filename/SHA-256、base source commit、build context/toolchain 和 signed build/installation attestation。`vLLM 0.25.1+cu129` 在 PyPI 无同版本属性是必须记录的 `NOT_PYPI_SOURCED`，不是要求一个不存在的 PyPI hash；未满足对应路径的必需字段时才只能称 `recipe_frozen` 或 `attached_unverified`。

**建议：** 上游升级必须是独立变更：重新解析 lock、重建镜像、运行 import smoke、环境 API tests、训练 tracer、checkpoint round-trip、export/evaluation parity 和 provenance verification。不得因“最新版存在”在当前恢复 slice 中顺手升级。

## 3. Ray/RLlib 2.56.1 正确实现合同

### 3.1 新 API 栈的职责分解

**事实：** RLlib 2.56.1 默认启用 `RLModule and Learner` 与 `EnvRunner and ConnectorV2` 两部分新 API 栈；迁移指南将核心职责分给 `RLModule`、`Learner`、episodes、`ConnectorV2`，而 `AlgorithmConfig`/`Algorithm` 继续作为配置与协调表面。[migration guide](https://docs.ray.io/en/releases-2.56.1/rllib/new-api-stack-migration-guide.html) [AlgorithmConfig default source](https://github.com/ray-project/ray/blob/936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a/rllib/algorithms/algorithm_config.py#L450-L456)

| RLlib 对象 | 官方职责 | AutoMarkov 应放入的逻辑 | 不应放入 |
|---|---|---|---|
| `AlgorithmConfig` | 声明 environment、sampling、learner、module、multi-agent、evaluation 配置 | 从冻结 `TrainingPlan` 机械构造配置 | caller 自报训练成功或动态改预算 |
| `RLModule` / `MultiRLModule` | inference、exploration、train forward 与网络状态 | recurrent actor、centralized critic、module schema | 环境 transition、sealed scoring、任意文件发现 |
| `ConnectorV2` | env-to-module、module-to-env、learner preprocessing pipeline | observation adapter、state/mask、episode-to-batch 映射 | 偷读 critic-only state、任意网络调用 |
| `EnvRunner` | 环境创建、采样和 episode metrics | Gymnasium/PettingZoo 或受控 RemoteEnv adapter | 发布、统计结论、sealed gold access |
| `Learner` / `LearnerGroup` | optimizer、loss、distributed update 与 learner state | PPO loss、CTDE critic loss、优化器、learner resource | generation、evaluator、run lifecycle mutation |
| `Algorithm` | 协调 EnvRunnerGroup 和 LearnerGroup、训练迭代、checkpoint | profile-local runner 的一次训练 execution | 跨 profile 传递 Python 对象 |

官方类定义可在固定源码中核验：[RLModule](https://github.com/ray-project/ray/blob/936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a/rllib/core/rl_module/rl_module.py#L260)、[ConnectorV2](https://github.com/ray-project/ray/blob/936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a/rllib/connectors/connector_v2.py#L32)、[EnvRunner](https://github.com/ray-project/ray/blob/936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a/rllib/env/env_runner.py#L36)、[LearnerGroup](https://github.com/ray-project/ray/blob/936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a/rllib/core/learner/learner_group.py#L100)。

### 3.2 旧字段到 2.56.1 字段的纠正

**事实：** 迁移指南明确要求新栈训练 batch 使用 per-learner 语义；固定 PPO 源码定义 `num_epochs` 与 `minibatch_size`，`AlgorithmConfig.learners()` 定义 learner 数量和每 learner 资源。[migration guide](https://docs.ray.io/en/releases-2.56.1/rllib/new-api-stack-migration-guide.html) [PPOConfig source](https://github.com/ray-project/ray/blob/936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a/rllib/algorithms/ppo/ppo.py#L127-L147) [learners source](https://github.com/ray-project/ray/blob/936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a/rllib/algorithms/algorithm_config.py#L2273)

| 旧/错误表达 | 2.56.1 新栈表达 | 验收规则 |
|---|---|---|
| `train_batch_size` | `training(train_batch_size_per_learner=...)` | 记录 per learner 值和 `total_train_batch_size` derived 值 |
| `sgd_minibatch_size` | `training(minibatch_size=...)` | 必须满足`minibatch_size <= train_batch_size_per_learner`；不能与total train batch比较 |
| `num_sgd_iter` | `training(num_epochs=...)` | 禁止并存旧字段 |
| `num_gpus` | `learners(num_learners=..., num_gpus_per_learner=...)` | CPU/GPU 来自 signed compute manifest |
| `num_rollout_workers` | `env_runners(num_env_runners=...)` | 区分 remote runners 与 local runner |
| `num_envs_per_worker` | `env_runners(num_envs_per_env_runner=...)` | 多智能体限制见 3.5 |
| `model={...}` / `ModelV2` | `rl_module(rl_module_spec=RLModuleSpec(...))` | module class/source/config 进入 identity |
| `Policy` 内 custom loss | custom `Learner` | loss 和 optimizer 不留在旧 Policy seam |
| old connector/preprocessor | `ConnectorV2` pipeline | pipeline 顺序与每个 piece identity 冻结 |

### 3.3 推荐的最小构造骨架

下列代码是**项目建议骨架**，不是完整训练实现。所有省略值必须来自已批准、signed、immutable `TrainingPlan`，不得使用运行时 outcome 回填。

```python
from ray.rllib.algorithms.ppo import PPOConfig

config = (
    PPOConfig()
    .api_stack(
        enable_rl_module_and_learner=True,
        enable_env_runner_and_connector_v2=True,
    )
    .environment(env=env_id, env_config=env_config)
    .env_runners(
        num_env_runners=num_env_runners,
        num_envs_per_env_runner=num_envs_per_env_runner,
    )
    .learners(
        num_learners=num_learners,
        num_gpus_per_learner=num_gpus_per_learner,
    )
    .training(
        train_batch_size_per_learner=train_batch_size_per_learner,
        minibatch_size=minibatch_size,
        num_epochs=num_epochs,
    )
    .rl_module(rl_module_spec=rl_module_spec)
)
```

**建议：** production adapter 先构造、序列化和 hash 一份 normalized config projection，再调用 RLlib。保存 projection、RLlib version/commit、module/connector class hashes、environment-binding hash 和 seed tuple；不要尝试序列化整个可变 Python `AlgorithmConfig` 作为跨域协议。

### 3.4 recurrent、CTDE 与 multi-agent

**事实：** RLlib 2.56.1 的 multi-agent 配置通过 `policy_mapping_fn` 将 agent 映射到 modules/policies，并可设置 `policies_to_train`、`algorithm_config_overrides_per_module` 与 `MultiRLModuleSpec`。第三方 PettingZoo 环境可通过官方 wrapper 连接。[2.56.1 multi-agent guide](https://docs.ray.io/en/releases-2.56.1/rllib/multi-agent-envs.html)

**项目推论：** “CTDE-PPO”不是只设置一个布尔值即可得到的官方算法。AutoMarkov 必须明确：

1. actor 输入来自哪个 capability；
2. centralized critic 的 state 在何处由 ConnectorV2 添加；
3. inference/exploration 输出不得依赖 critic-only state；
4. learner 如何计算 centralized value loss；
5. module/policy mapping、参数共享、trainable module set；
6. recurrent state 的 initial state、sequence chopping、padding/mask 和 burn-in；
7. export 时保留 actor 所需 tensor，排除仅训练用 critic tensor的规则。

**建议：** 把 feed-forward PPO、recurrent PPO、independent PPO、CTDE-PPO 视为四份 closed `TrainingProtocol`，共享 runner 但不共享隐式默认值。不得将任意 centralized-critic PPO 结果直接标作某篇论文的 MAPPO，除非算法、loss、normalization、GAE、batching 和参数共享均逐项对齐并记录 deviation。

### 3.5 EnvRunner、vectorization 与外部环境

**事实：** 2.56.1 单智能体环境可用 `num_envs_per_env_runner` vectorize；同版文档明确写明 multi-agent setups 尚不可 vectorize。恢复计划不得假定 PettingZoo/MPE2 能按单智能体方式在一个 `MultiAgentEnvRunner` 中无条件向量化。[2.56.1 multi-agent scaling note](https://docs.ray.io/en/releases-2.56.1/rllib/multi-agent-envs.html#scaling-to-many-multiagentenvs-per-envrunner)

**事实：** 同版 package reference 将新栈 external env 支持标为 under development，推荐编写 custom `EnvRunner` 连接 TCP/shared-memory simulator。[2.56.1 external env note](https://docs.ray.io/en/releases-2.56.1/rllib/package_ref/env.html#external-envs)

**项目推论：** AutoMarkov 的 RemoteEnv codec/mTLS/session 协议是项目-owned adapter，不是“RLlib 已保证兼容”的标准。其 acceptance 必须独立覆盖：版本协商、request/response schema、最大尺寸、deadline、幂等 request ID、episode/agent/step identity、disconnect/reconnect、重复/乱序/过期消息、terminal/truncation、resource exhaustion 和 authentication failure。

### 3.6 训练、evaluation 与 checkpoint

**事实：** `Algorithm`/subcomponents 采用 `save_to_path`、`restore_from_path`、`from_checkpoint`；2.56.1 文档称 Ray 2.40 起 checkpoint 在 2.x 后续版本向后兼容。该兼容性是 RLlib 的读取承诺，不是安全承诺。[2.56.1 checkpointing](https://docs.ray.io/en/releases-2.56.1/rllib/checkpoints.html)

**事实：** checkpoint 目录包含 algorithm state、`class_and_ctor_args.pkl`、subcomponent 目录和 pickle/msgpack state。直接向不可信 profile 或 sealed evaluator 提供 checkpoint 会同时提供 Python constructor/class 信息和非 weights-only state。[checkpoint directory structure](https://docs.ray.io/en/releases-2.56.1/rllib/checkpoints.html#structure-of-a-checkpoint-directory)

**建议：** 采用两阶段合同：

1. trainer-local checkpoint：只用于相同 source commit、profile、RLlib/PyTorch build 下的 crash recovery；记录 content commitment，不公开。
2. trainer-local one-shot exporter：恢复 checkpoint，核验 architecture 和 tensor inventory，导出 weights-only safetensors 与 `PolicyExportManifest`。
3. sealed evaluator：只运行预注册可信 `RLModule`/Connector/adapter 代码，按 manifest 精确加载 tensor；不 import candidate checkpoint 中的 class 或 pickle。

**最低 targeted runtime 证据：** exact profile import → config validation → one-iteration CPU smoke → checkpoint save → fresh process restore → deterministic evaluation seed → safetensors export → fresh trusted evaluator load。通过该 tracer 只允许声明 runner 路径可用，不能声明算法效果或 suite 完成。

## 4. Gymnasium、PettingZoo 与 MPE2 合同

### 4.1 Gymnasium 1.2.2

**事实：** `Env.step()` 返回 `(observation, reward, terminated, truncated, info)`；Gymnasium 将旧 `done` 拆成 `terminated` 与 `truncated`，原因之一是二者对 bootstrapping 不同。`reset()` 返回 `(observation, info)`，自定义环境应在开头调用 `super().reset(seed=seed)` 初始化 RNG。[Env API 1.2.2](https://gymnasium.farama.org/v1.2.2/api/env/) [custom env guide 1.2.2](https://gymnasium.farama.org/v1.2.2/introduction/create_custom_env/)

**实现要求建议：**

- `terminated`只表达MDP/POMDP语义终点；environment contract内的time limit或外部episode/resource上限使用`truncated`。process crash、transport断开、安全拒绝等技术中断是execution failure，绝不伪造成environment truncation或success terminal。
- 环境所有随机性从 `self.np_random` 或显式子流派生；不读取 ambient global RNG。
- observation/action 必须逐 step 属于声明空间，dtype/shape/bounds 精确稳定。
- `gymnasium.utils.env_checker.check_env` 作为 API gate；另外运行 reference trajectories、reward decomposition、transition properties、seed replay、invalid action 和 boundary tests。
- `info` 不能泄露 gold、完整 state 或 evaluator-only字段；debug-only 信息必须在 production profile 关闭。

### 4.2 PettingZoo 1.26.1

**事实：** AEC API 表达 agent 顺序行动；Parallel API 表达同时行动的 POSG。AEC 到 Parallel 的转换只在环境按完整 cycle 更新等约束成立时安全，不能把任意 turn-based 环境机械包装为 simultaneous。[AEC API 1.26.1](https://pettingzoo.farama.org/1.26.1/api/aec/) [conversion constraints](https://pettingzoo.farama.org/1.26.1/api/wrappers/pz_wrappers/)

**事实：** PettingZoo 提供 `api_test`、`parallel_api_test`、`seed_test`、`parallel_seed_test`；这些测试检查 API consistency 和 determinism。[environment tests 1.26.1](https://pettingzoo.farama.org/1.26.1/content/environment_tests/)

**实现要求建议：**

- 明确 `possible_agents`、当前 `agents`、agent removal/termination 规则与 agent ID canonical order。
- 同步环境优先实现/复用 Parallel API；序贯环境保留 AEC，不为训练便利更改博弈时序。
- 测试 agent set 动态变化、dead-agent action、per-agent terminations/truncations、space lookup、state availability 和 seed replay。
- RLlib wrapper 前后分别跑 PettingZoo compliance 与 RemoteEnv/RLlib integration tests，避免 wrapper 遮蔽基础缺陷。

### 4.3 MPE2 1.1.0

**事实：** MPE2 1.1.0 tag 为 `7590d9d52791e321974d4fda6090fb18f34dbf49`，release 声明其达到 Farama Mature Environment status；MPE 已从 PettingZoo 1.26.0 移出并由 MPE2 维护。[MPE2 1.1.0 release](https://github.com/Farama-Foundation/MPE2/releases/tag/v1.1.0) [PettingZoo 1.26.0 release](https://github.com/Farama-Foundation/PettingZoo/releases/tag/1.26.0)

**事实：** `simple_spread_v3` 默认 `N=3`，Parallel API 可用，single-agent observation shape 为 `(18,)`，global state shape 为 `(54,)`；任务同时包含共享 landmark-distance reward 与 local collision penalty。默认还涉及 `local_ratio=0.5`、`max_cycles=25`、离散动作，并提供 curriculum、neighbor-limited partial observation 等可选参数。[Simple Spread docs](https://mpe2.farama.org/environments/simple_spread/)

**项目推论：**

- `mpe2_full_state_mg`：如果 actor 读取 54D `state()`，这是 AutoMarkov capability wrapper 下的 full-state adaptation。
- `mpe2_native_local_posg`：actor 只读本 agent 18D native observation，central critic 可在训练时经独立 capability 读取 state。
- 两者不能都称“native MPE2”；也不能用不同 network、hidden size、normalization、budget 或 seed 解释为纯 information-structure contrast。

**建议冻结字段：** MPE2 package/tag/commit、environment version、`N`、`local_ratio`、`max_cycles`、`continuous_actions`、`dynamic_rescaling`、`curriculum`、`terminate_on_success`、agent/landmark neighbor limits、render mode、physics seed、policy seed和 wrapper source hash。所有非预注册 optional features 默认关闭，不能由 agent 自适应开启。

## 5. vLLM 0.25.1 安全与身份边界

### 5.1 版本和身份

**事实：** vLLM 0.25.1 tag 对应 commit `752a3a504485790a2e8491cacbb35c137339ad34`；它是 0.25.0 上的 targeted patch release。[v0.25.1 release](https://github.com/vllm-project/vllm/releases/tag/v0.25.1)

**事实：** `--trust-remote-code` 默认 `False`；`--revision`、`--code-revision`、`--tokenizer-revision` 是不同 identity 维度。[0.25.1 engine arguments](https://docs.vllm.ai/en/v0.25.1/configuration/engine_args/)

**建议：** AutoMarkov 保持 `trust_remote_code=false`；显式冻结 model、tokenizer、code revision 和实际 snapshot bytes。`served_model_name` 只是 API alias，不得作为权重身份。保存所有 weight shard SHA-256、index/config/tokenizer/chat-template hashes 和 closed、脱敏的启动参数 canonical projection；API key、credential/private locator 和由它们直接计算的可离线枚举 hash 不得进入工件。

### 5.2 网络边界和鉴权

**事实：** 官方 0.25.1 安全页明确说明 `--api-key`/`VLLM_API_KEY` 只保护 `/v1`、`/v2`、`/inference` 等特定前缀；同一 server 上仍有未保护 inference、utility、operational routes。官方结论是不得只依赖 API key。[0.25.1 API key limitations](https://docs.vllm.ai/en/v0.25.1/usage/security/#api-key-authentication-limitations)

**事实：** 官方建议最小化 firewall surface，并禁止把 `torch.distributed`、KV-cache transfer 等 internal ports 暴露给不可信网络。[0.25.1 firewall guidance](https://docs.vllm.ai/en/v0.25.1/usage/security/)

**建议的 AutoMarkov closed surface：**

- raw vLLM internal listener 放在generation worker无法加入的专用 network namespace；不监听公共 interface。仅绑定raw vLLM loopback但与generation worker共享namespace不算隔离。
- 项目owned relay向`LocalLlmRuntime`暴露规格允许的authenticated loopback `/v1` endpoint，`AUTOMARKOV_VLLM_BASE_URL`指向relay；仅relay principal能通过namespace/cgroup/UID-aware firewall、受限veth或mTLS连raw internal port。generation→relay冻结source/target principals、protocol/schema、API-key credential ID和transport capability；route allowlist 仅包含所需 `/health`、`/v1/models`、`/tokenize`、`/v1/chat/completions`，各route分capability。
- `/health` 仅作 liveness；readiness 还必须验证 authenticated model list、token admission 和一次真实 non-streaming completion。
- 禁止 generation principal 访问 profiler、weight update、runtime LoRA update、tokenizer-info、tool server 和其他 operational routes。
- sealed evaluator network namespace无到 vLLM、Tavily、model hub 或 generation relay 的 route。

### 5.3 multimodal、工具和远程取数

**事实：** `--allowed-local-media-path` 允许请求读取服务器文件，官方 CLI 将其标为 security risk；`--allowed-media-domains` 只在列表非空时限制远程 media URL，缺省空列表不代表拒绝远程媒体。因此 text-only 服务不能靠“不设置 media flags”防 SSRF。[vLLM fixed media connector](https://github.com/vllm-project/vllm/blob/752a3a504485790a2e8491cacbb35c137339ad34/vllm/multimodal/media/connector.py#L337-L361) [vLLM security](https://docs.vllm.ai/en/v0.25.1/usage/security/)

**事实：** tool server 不是默认开启；demo Python tool 可执行模型生成代码，其 Docker 网络默认并非严格隔离，官方提示 SSRF、LAN 和 cloud metadata 风险。[0.25.1 tool/MCP security](https://docs.vllm.ai/en/v0.25.1/usage/security/#tool-server-and-mcp-security)

**建议：** text-only AutoMarkov relay 和 request schema 在转发前机械拒绝任何非文本 content part、`image_url`、`audio_url`、`video_url`、`file://`、`data:` 或 HTTP(S) media field；vLLM namespace 保持 default-deny egress，`allowed_local_media_path` 为空、media redirect 关闭，tool server/demo、runtime LoRA updating、prompt embeddings 和 profiler 均关闭。默认路线的负例必须证明 HTTP/data/file 三类媒体都在 relay/schema 层失败且 vLLM 无出站连接。若未来出现必要 multimodal cell，必须新增 ADR/profile，固定域名、禁止 redirect、限制 bytes/MIME/dimensions，并在独立 fetcher 中 content-address 后再交给 vLLM。

### 5.4 缓存与可写根

**事实：** vLLM 0.25.1 假设 cache directories 是 private/trusted，会加载没有 cryptographic integrity verification 的内容，其中包括可执行格式；官方要求防止不可信用户/进程写入 `VLLM_CACHE_ROOT`、Triton 和其他相关 caches。[vLLM cache-directory security](https://docs.vllm.ai/en/v0.25.1/usage/security/#cache-directory-security)

**项目推论：** model/HF cache 作为加载输入时必须是 signed/content-addressed 只读 mount。Triton/torch-compile/runtime cache 若必须可写，应为 exact service identity 建立独立空 root，只允许 service principal 写入，不与其他 profile 共享，并在 service lifecycle 结束时销毁。任何预热或跨 service-identity reuse 都要新的 content manifest、来源和 attestation。security report 必须冻结每个root的canonical path、mount/source identity、UID/GID、mode、writable principals、launch state 和 disposal/reuse policy。

### 5.5 输入输出与可复现性

**建议：** 每次请求先用同一 effective tokenizer/chat-template 进行 token admission，冻结 `max_tokens`、sampling、seed、tool schema 和 reasoning controls。响应按不可信数据处理：限制 bytes/tokens、严格 JSON schema、记录 finish reason/usage/request ID，不执行模型返回的代码或 shell。vLLM seed 和 deterministic request 仍不能自动保证跨 GPU/kernel/build bitwise reproducibility；应把 exact runtime identity 与可接受的行为容差写进实验合同。

## 6. safetensors：能解决什么，不能解决什么

### 6.1 可核验能力

**事实：** safetensors 官方将格式定位为相对 pickle 的安全、快速 tensor 存储。格式由 little-endian header length、受限 JSON header 和连续 tensor byte buffer 构成；buffer 必须被完整索引且不能有 holes，以防 polyglot file。官方实现还限制 header size，降低恶意 JSON 导致的 DoS 风险。[fixed v0.8.0 README](https://github.com/huggingface/safetensors/blob/a406ca3e7a90598be0cd05a50069cb9bf5ef6ba6/README.md)

**事实：** 官方同一格式说明明确指出 tensor values 不会被检查，文件可包含 NaN 或正负 Inf。安全反序列化不等于数值有效。[format notes](https://github.com/huggingface/safetensors/blob/a406ca3e7a90598be0cd05a50069cb9bf5ef6ba6/README.md#format)

### 6.2 不应作出的结论

以下推断均无效：

- “扩展名是 `.safetensors`，所以文件来自批准模型”；
- “可以安全解析，所以 tensor keys/shapes 与 architecture 一致”；
- “权重不执行任意代码，所以配套 model/connector Python 也可信”；
- “文件 hash 匹配，所以模型科研结论正确”；
- “导出成功，所以 checkpoint/training/evaluation 都成功”。

### 6.3 AutoMarkov export gate

**建议：** `PolicyExportManifest` 至少绑定：source checkpoint commitment、trainer/exporter profile identity、RLlib/PyTorch/safetensors versions、trusted architecture/module/connector/observation/action adapter IDs、sorted tensor inventory、每 tensor dtype/shape/nbytes、finite-value verdict、总文件 bytes/SHA-256、export terminal record 和 seed identity。

**建议：** evaluator 加载前重新计算 file hash，要求 key set 完全相等，不允许 extra/missing keys，逐 tensor 校验 dtype/shape/finite/nbytes；只把 tensor map注入预注册可信代码。任何失败产生独立 export/evaluation failure，不回写训练结果。

## 7. SLSA、OpenSSF 与 GitHub Actions 供应链

### 7.1 SLSA 1.2 的准确边界

**事实：** 截至检索日，SLSA 1.2 为 Approved current specification。Build L1 要求 provenance 存在；L2 要求 hosted build platform 生成并签名 provenance且消费者验证真实性；L3 再要求 hardened build platform 和更强隔离/签名秘密保护。[SLSA 1.2](https://slsa.dev/spec/v1.2/) [Build track basics](https://slsa.dev/spec/v1.2/build-track-basics)

**事实：** provenance 是关于 artifact 在何处、何时、如何产生的可验证信息；它不证明 artifact 无漏洞、算法正确或实验结论成立。[SLSA provenance](https://slsa.dev/spec/v1.2/provenance)

**项目建议：**

- 开发/pilot 工件可先达到可验证的 L1-like provenance；不要自授正式 SLSA badge。
- public release artifact 目标至少是由 hosted builder 签名且 consumer verifier 实际验证的 L2 语义。
- 只有 reusable workflow、builder isolation、secret separation 等完整满足并经审查时才陈述 L3；GitHub 文档只说相关组合“can help achieve”，不是添加一个 action 即自动达到 L3。[GitHub SLSA L3 guidance](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/increase-security-rating)

### 7.2 GitHub artifact attestation

**事实：** GitHub artifact attestations 可记录 workflow、repository、organization、environment、commit SHA、trigger event 等 build provenance。生成 binary attestation 需要 `id-token: write`、`contents: read`、`attestations: write`；消费方使用 `gh attestation verify` 验证。[artifact attestation concepts](https://docs.github.com/en/actions/concepts/security/artifact-attestations) [generation guide](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)

**建议：** release workflow 只对已经完成 redaction、hash 和 verifier 的 final bundle attestation；attestation subject digest 必须与公开下载 bytes 一致。workflow green、attestation created 与 artifact verified 是三个不同状态。

### 7.3 Workflow hardening

**事实：** GitHub 安全文档称 full-length commit SHA 是使用 action 的 immutable release 方式，并建议核验 SHA 来自原 action repository而非 fork。[GitHub secure use reference](https://docs.github.com/en/actions/reference/security/secure-use)

**最低控制建议：**

1. 所有 third-party `uses:` pin 到完整 commit SHA；旁注 human-readable tag。
2. 顶层`permissions: contents: read`；GitHub只支持workflow/job级permissions，因此只在隔离的专用privileged job提升OIDC/attestation/package/release权限，不能声称step级限权。release job与PR test job分离。
3. 不在能执行 untrusted PR code 的 job 中提供 secrets、OIDC、package/release write permission。
4. 对 `pull_request_target`、workflow command injection、artifact/caches from forks 设显式拒绝测试。
5. package publish 优先短期 OIDC/trusted publishing，不保存长寿命 token。
6. dependency lock、wheel hashes、base image digest、action SHA 与 build inputs进入 provenance。
7. 生成规格要求的SPDX SBOM并随release artifact发布，保留source-build closure的`BUILD_DEPENDENCY_OF`关系；CycloneDX只能作为额外输出，不能替代SPDX。SBOM与实际bundle重新比对。

### 7.4 OpenSSF Scorecard 的用法

**事实：** Scorecard 检查包括 Branch-Protection、CI-Tests、Code-Review、Pinned-Dependencies、SAST、Security-Policy、Signed-Releases、Token-Permissions、Vulnerabilities 等；官方文档也说明自动检测会有 false negative/不适用项，低分不是确定存在漏洞的证明。[Scorecard README](https://github.com/ossf/scorecard) [check definitions](https://github.com/ossf/scorecard/blob/main/docs/checks.md)

**建议：** 把固定版本 Scorecard JSON 作为 release evidence 的 advisory 输入，逐 finding 给 remediation/accepted-risk/not-applicable 理由；不能只设置阈值后宣称“OpenSSF certified”。Signed-Releases check 发现签名也不实际验证签名，因此 AutoMarkov 的 verifier仍必须验证 attestation/signature/digest。

## 8. ACM、NeurIPS 与 COS 学术规范

### 8.1 ACM artifact 状态不能自报

**事实：** ACM Artifact Review and Badging v1.1 区分 Artifacts Available、Artifacts Evaluated—Functional、Artifacts Evaluated—Reusable 和 Results Reproduced 等状态。Functional 关注 documented、consistent、complete、exercisable 及 verification/validation evidence；Results Reproduced 需要作者之外的人员使用作者提供的部分工件获得主要结果。[ACM policy URL](https://www.acm.org/publications/policies/artifact-review-and-badging-current) [ACM SIGSIM implementation](https://sigsim.acm.org/conf/pads/2024/blog/artifact-evaluation/) [ACM SIGMOD implementation](https://reproducibility.sigmodconf.hosting.acm.org/index.html)

**项目推论：** AutoMarkov 自己完成 clean-checkout rerun 可以称“internal reproducibility check passed”，不能称获得 ACM badge；`Results Reproduced` 至少需要独立 reviewer/team 及其报告。artifact 存在也不等于 functional/reusable。

**建议的 artifact appendix 最小目录：** artifact inventory、license、persistent locator/DOI、platform/compute、install/build、exact commands、expected duration、inputs、seeds、raw-to-table scripts、expected outputs/tolerances、failure troubleshooting、known limitations、security/privacy restrictions、independent report。

### 8.2 NeurIPS 2026 checklist

**事实：** NeurIPS checklist 旨在促进 reproducibility、transparency、research ethics 和 societal impact；不包含 checklist 的投稿会 desk reject。核心问题涵盖 claims/scope、limitations、experimental reproducibility、code/data access、training/test details、statistical significance/error bars、compute resources 等。[NeurIPS Paper Checklist](https://neurips.cc/public/guides/PaperChecklist)

**事实：** NeurIPS 2026 Main Track Handbook 要求完成 checklist；dataset 建议持久化 repository、persistent identifier、metadata 标准和 license/access restrictions。[2026 Main Track Handbook](https://neurips.cc/Conferences/2026/MainTrackHandbook)

**建议：** AutoMarkov 论文和 compact report 对每个 checklist 答案给 exact artifact/section locator，不用“见代码”泛指。必须报告：所有训练/evaluation hyperparameters及选择过程、每层样本单位、paired seeds、失败/缺失处理、CI/error bar定义、multiple-comparison family、算力/时长/硬件，以及无法公开资产的理由和可验证替代材料。

### 8.3 COS preregistration

**事实：** COS 将 preregistration 定义为研究前预先提交研究计划，用于区分 planned 与 unplanned 工作；探索和确认都重要，但同一数据不能同时无区分地生成和检验假设。[COS preregistration](https://www.cos.io/initiatives/prereg)

**事实：** COS 明确要求报告所有 pre-analysis plan 结果；计划变更可以发生，但必须通过新 registration/withdrawal 或 Transparent Changes 文档披露。额外分析允许进行，但须标作 exploratory。Registered Reports 还包括结果出现前的同行评审和 in-principle acceptance，不等同于普通 preregistration。[COS preregistration FAQ](https://www.cos.io/initiatives/prereg#preregistration-is-new-to-many-researchers-here-are-the-questions-we-get-asked-most-often) [Registered Reports](https://www.cos.io/initiatives/registered-reports)

**AutoMarkov 具体建议：**

- engineering pilot、power analysis、gold calibration 和 confirmatory outcomes 使用完全不重叠的标识、seed/task partitions 和报告标题。
- 首个 confirmatory generation 前冻结 task cards、eligible cells、pair count、RL seeds、budget、failure policy、outcome definitions、analysis families、estimator/counter algorithm 和 stopping rule。
- 所有 slot 留在 intention ledger；crash/OOM/timeout/NaN 不按结果删除或换 seed。
- 任何冻后修订追加 deviation artifact，不原地改 preregistration；受影响结果降级为 exploratory 或重新登记的新 experiment family。
- 报告 planned null/negative/failure outcomes，不只解释显著或成功 cells。

## 9. Agent2World 许可审计

### 9.1 当前可核验事实

审计对象：`DeepExperience/agent2world`，2026-08-25 `main`/HEAD 为 `1330f3cde9509f05d204a255f0f7f43208515dce`。

根 [LICENSE](https://github.com/DeepExperience/agent2world/blob/1330f3cde9509f05d204a255f0f7f43208515dce/LICENSE) 明确：

- 只准为 associated paper 的非商业研究评估和 reproducibility 查看、下载、运行代码；
- 禁止商业用途；
- 禁止分发、再许可、销售代码或 derivative works；
- 禁止作为 hosted service 提供；
- 不得删除或改变 notice；其他用途需书面许可。

**事实：** 项目网页的 CC BY-SA 4.0 声明只描述网站素材；它不能覆盖代码仓库根 LICENSE。[project page footer](https://agent2world.github.io/)

**事实：** 固定 commit 的 README license badge 文本指向 Apache 2.0，且链接仍指向 internal repository；这与公开仓库实际根 LICENSE 冲突。[README at audited commit](https://github.com/DeepExperience/agent2world/blob/1330f3cde9509f05d204a255f0f7f43208515dce/README.md) 许可判断必须采用实际 LICENSE 并保留冲突记录，不能采用徽章。

### 9.2 AutoMarkov 可做与不可做

**建议的 fail-closed 状态：** `restricted_disabled` 保持不可构建、不可打包、不可发布。

允许在当前恢复范围内：

- 保存 URL、commit、license hash、paper metadata 和非实质性结构说明；
- 基于论文公开概念设计独立、clean、controlled variant；
- 在论文中明确标为 `Agent2World-inspired controlled variant`，列出 model/tool/benchmark/implementation deviations。

禁止：

- 复制、翻译、port、vendor 其源码、prompt、test 或 expressive implementation；
- 把 restricted repo 加入 public/private release bundle、container layer 或生成代码上下文；
- 对外提供由其代码或衍生物支持的 hosted service；
- 把 clean controlled variant 称为 official reproduction、faithful reproduction 或 authorized derivative；
- 在没有新书面许可时执行其 SFT/trajectory 复用路线。

**未核验：** 该许可是否允许本机构预期的所有内部研究运行、论文附录截图、生成结果再分发或模型微调产物发布，需要权利人书面答复和机构法律/科研管理审查。本文不是法律意见。

## 10. Agent² 候选仓库与许可

### 10.1 候选关系

论文一手记录为 arXiv `2509.13368v2`，作者 Yuan Wei、Xiaohan Shan、Ran Miao、Jianmin Li；页面描述 Generator Agent、Target Agent、MDP modeling、algorithmic optimization 与 MuJoCo/MetaDrive/MPE/SMAC 实验，但 arXiv record 未给出作者声明的 code URL。[arXiv record](https://arxiv.org/abs/2509.13368)

候选仓库 `wyjayyo/RL-Agent-Automation` 的 README 使用相同题名、引用同一 arXiv ID，并包含对应环境/算法与 MCP server；审计 commit 为 `d3ed13755d86c7ed06b52a5a6fb17aa2ce6faf0c`。[candidate README](https://github.com/wyjayyo/RL-Agent-Automation/blob/d3ed13755d86c7ed06b52a5a6fb17aa2ce6faf0c/README.md)

这只能形成“高度相关 candidate”推论，不能证明它由论文作者、作者机构或正式项目维护。候选 repository owner 与论文作者的身份关系未从论文侧核验。

### 10.2 许可状态

**事实：** 2026-08-25 GitHub repository metadata 的 `license` 为 `null`；固定 tree 无 `LICENSE`、`LICENSE.md` 或 `COPYING`，repository 页面也未显示 license。[GitHub repository API](https://api.github.com/repos/wyjayyo/RL-Agent-Automation) [fixed tree](https://github.com/wyjayyo/RL-Agent-Automation/tree/d3ed13755d86c7ed06b52a5a6fb17aa2ce6faf0c)

**项目推论：** 公开可见或可 clone 不等于获得复制、修改、再分发、集成或发布授权。当前状态应为 `candidate_upstream / authorship_unverified / license_unresolved`。

**建议：**

1. 在作者/权利人确认前，只做网页与 metadata 的只读审计；不执行、不复制实质代码、不进入模型上下文、不生成 diff、不构建镜像。
2. 向论文通讯作者索取 official code locator、repository ownership、exact release commit、code/data/model license 和允许的 reproduction/distribution scope。
3. 如果取得许可，重新做 dependency、dataset、model、environment asset 的逐层 license review；repo-level license 不自动覆盖所有资产。
4. 若不能取得证据，Agent² 保持 paper-spec clean reimplementation，并记录不能核验的实现细节与 deviation；不得称 official-code reproduction。

**未核验：** OpenReview 页面当前需要浏览器 challenge；未核验 supplemental files、review revisions 或作者回复是否含 code locator。

## 11. 可执行的恢复 acceptance matrix

| Gate | 必须输入 | 机械检查 | 通过后允许声明 | 不允许声明 |
|---|---|---|---|---|
| `UPSTREAM_IDENTITY` | tag/commit/version/lock/wheel hashes/license | exact match、无 mutable ref | recipe identity frozen | runtime usable |
| `RLLIB_CONFIG` | signed TrainingPlan | 禁旧字段、new-stack config validate、module/connector hashes | config accepted by 2.56.1 | training effective |
| `ENV_API` | environment binding | Gym/PZ compliance、seed、spaces、termination | API compatible | behavior correct |
| `ENV_BEHAVIOR` | reference/hidden tests | transition/reward/info structure properties | environment candidate valid | policy effective |
| `TRAINING_RUNTIME` | exact profile/image/compute/seed | import、one iteration、terminal record | tracer works | 10-seed experiment complete |
| `CHECKPOINT_RECOVERY` | trainer-local checkpoint | fresh same-profile restore and state continuity | recovery path works | cross-profile safe |
| `POLICY_EXPORT` | checkpoint commitment + trusted exporter | safetensors hash/key/dtype/shape/finite | weights-only export valid | policy score valid |
| `VLLM_RUNTIME` | ingress-typed package/build provenance、process/model identity、redacted argv projection、cache-root policy | text-only schema/media negatives、network/egress boundary、auth、cache ownership/content state、models/tokenize/canary、secret scan | attached service verified | model scientifically correct |
| `SUPPLY_CHAIN` | bundle/SBOM/provenance/attestation | digest/signature/signer/workflow/commit verify | artifact provenance verified | SLSA L3 unless assessed |
| `PREREG_FREEZE` | design/task/calibration/intention manifests | hashes、time/order、complete cells/families | experiment ready | experiment run/complete |
| `EXPERIMENT_COVERAGE` | all terminal slots | no silent drop/replacement、analysis replay | experiment complete | independent reproduction |
| `INDEPENDENT_REPRO` | clean checkout + external report | independent rebuild/rerun/tolerance | independently reproduced scope | ACM badge unless awarded |
| `LICENSE` | per-component/asset terms | compatible use/distribution and notices | permitted scoped use | ownership/permission beyond terms |

## 12. 对主恢复文档的精确补充建议

以下内容应由主文档 owner 整合；本文不修改该文件。

1. 把“RLlib 2.56”写成 exact `Ray/RLlib 2.56.1 @ 936f0d7...`，并链接 versioned migration guide。
2. 在 R07 增加：new API stack default、custom loss 属于 `Learner`、observation/action preprocessing 属于 `ConnectorV2`、multi-agent 暂不可用 `num_envs_per_env_runner` 向量化。
3. 将 R07 的“RemoteEnv adapter”明确为 project-owned custom `EnvRunner` protocol；上游 external env support 是 under development，不声称稳定官方 wire protocol。
4. 在 checkpoint/export 条款增加官方目录含 pickle/class constructor 的事实，说明为什么 checkpoint 只能 trainer-local，safetensors 不能省略 tensor schema/finite/hash gate。
5. 将 vLLM 要求落入 R05A/R24 的可执行 gate：reverse-proxy route allowlist、internal port isolation/default-deny egress、`--api-key` 不完整、`trust_remote_code=false`、relay/schema 拒绝全部 media、tool-server/runtime-LoRA 关闭、cache root/source/owner/mode/writer/lifecycle 冻结；同时要求媒体/缓存/路由负例、model/tokenizer/weights、脱敏 argv projection、secret scan 和真实generation/tokenization canary 作为完成证据。
6. 在 CI/release 工作包加入 full-SHA pinned actions、least-privilege permissions、artifact attestation 的生成与独立 verification；SLSA/Scorecard 均不得自我认证式表述。
7. 在学术交付加入 NeurIPS checklist locator、ACM badge 只能由独立 evaluation 授予、COS planned/unplanned/deviation 全报告规则。
8. 在 Agent2World 条款记录 README Apache badge 与 root restricted LICENSE 冲突，以 root LICENSE fail closed。
9. 将 Agent² 候选仓库状态明确为 `authorship_unverified + license_unresolved`；“无 LICENSE 时只可读研究”应收紧为“只读 metadata/网页审计，不复制、运行、集成或发布实质代码，等待权利人许可”。

## 13. Source Verification Matrix

| 来源 | 类型/版本 | 直接支持的范围 | 状态 |
|---|---|---|---|
| [Ray 2.56.1 migration guide](https://docs.ray.io/en/releases-2.56.1/rllib/new-api-stack-migration-guide.html) | 官方 versioned docs | 新栈对象与字段迁移 | `VERIFIED` |
| [Ray 2.56.1 multi-agent](https://docs.ray.io/en/releases-2.56.1/rllib/multi-agent-envs.html) | 官方 versioned docs | mapping、MultiRLModule、vectorization限制 | `VERIFIED` |
| [Ray 2.56.1 env reference](https://docs.ray.io/en/releases-2.56.1/rllib/package_ref/env.html) | 官方 versioned docs | external env WIP/custom EnvRunner | `VERIFIED` |
| [Ray 2.56.1 checkpointing](https://docs.ray.io/en/releases-2.56.1/rllib/checkpoints.html) | 官方 versioned docs | Checkpointable、pickle目录、兼容声明 | `VERIFIED` |
| [Ray tag source](https://github.com/ray-project/ray/tree/936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a) | 固定官方源码 | classes/config signatures | `VERIFIED` |
| [Gymnasium 1.2.2 Env API](https://gymnasium.farama.org/v1.2.2/api/env/) | 官方 versioned docs | reset/step/termination | `VERIFIED` |
| [PettingZoo 1.26.1 tests](https://pettingzoo.farama.org/1.26.1/content/environment_tests/) | 官方 versioned docs | API/parallel/seed tests | `VERIFIED` |
| [MPE2 1.1.0 release](https://github.com/Farama-Foundation/MPE2/releases/tag/v1.1.0) | 官方 release | version/status | `VERIFIED` |
| [MPE2 Simple Spread](https://mpe2.farama.org/environments/simple_spread/) | 官方 docs | dims/reward/config | `VERIFIED` |
| [vLLM 0.25.1 security](https://docs.vllm.ai/en/v0.25.1/usage/security/) | 官方 versioned docs | network/auth/tool security | `VERIFIED` |
| [vLLM 0.25.1 engine args](https://docs.vllm.ai/en/v0.25.1/configuration/engine_args/) | 官方 versioned docs | revision/trust/seed fields | `VERIFIED` |
| [safetensors 0.8.0 README](https://github.com/huggingface/safetensors/blob/a406ca3e7a90598be0cd05a50069cb9bf5ef6ba6/README.md) | 固定官方源码说明 | format/safety/limitations | `VERIFIED` |
| [SLSA 1.2](https://slsa.dev/spec/v1.2/) | Approved standard | provenance/levels | `VERIFIED` |
| [GitHub artifact attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations) | 官方 docs | attestation content/verification | `VERIFIED` |
| [GitHub secure use](https://docs.github.com/en/actions/reference/security/secure-use) | 官方 docs | full-SHA pinning | `VERIFIED` |
| [OpenSSF Scorecard](https://github.com/ossf/scorecard/blob/main/docs/checks.md) | 官方 project docs | checks/limitations | `VERIFIED` |
| [ACM artifact policy](https://www.acm.org/publications/policies/artifact-review-and-badging-current) | 官方 policy URL | badge taxonomy | `ACCESS_DEGRADED`, ACM SIG pages交叉核验 |
| [NeurIPS checklist](https://neurips.cc/public/guides/PaperChecklist) | 官方 conference policy | reproducibility/reporting | `VERIFIED` |
| [COS preregistration](https://www.cos.io/initiatives/prereg) | 官方 organization guidance | planned/unplanned/deviation | `VERIFIED` |
| [Agent2World LICENSE](https://github.com/DeepExperience/agent2world/blob/1330f3cde9509f05d204a255f0f7f43208515dce/LICENSE) | 固定仓库许可 | use/distribution restrictions | `VERIFIED_RED_FLAG` |
| [Agent² arXiv record](https://arxiv.org/abs/2509.13368) | 论文一手登记 | authors/title/method/benchmarks | `VERIFIED` |
| [Agent² candidate](https://github.com/wyjayyo/RL-Agent-Automation/tree/d3ed13755d86c7ed06b52a5a6fb17aa2ce6faf0c) | 候选仓库 | content similarity/README claim | `AUTHORSHIP_UNVERIFIED`, `LICENSE_UNRESOLVED` |

## 14. 残余风险与下一次刷新触发器

- Ray、Gymnasium、PettingZoo、vLLM 已有更新版本；当前结论只约束冻结版本。任何 profile upgrade 都触发整节重验。
- upstream docs 的 `latest` 会漂移；实现 ticket 必须引用本文的 versioned docs 或 fixed commit links。
- vLLM 安全 endpoint 列表可能继续变化；部署验收必须从 exact 0.25.1 route table 和现场反向代理配置机械生成 allowlist diff。
- MPE2 文档站未提供可用的 `/1.1.0/` version path；因此 version identity 由 v1.1.0 tag固定，行为说明由当前官方环境页与本地 exact package runtime测试共同证明。
- SLSA level、ACM badge、independent reproduction、论文复现均需要外部或独立证据，不能由项目 schema 中的 boolean 自报。
- Agent2World 与 Agent² 的许可/归属是 release blocker；除非新证据由权利人或论文作者提供并进入 immutable manifest，否则保持当前 fail-closed 状态。
