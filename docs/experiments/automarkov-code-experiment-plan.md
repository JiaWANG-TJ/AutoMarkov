# AutoMarkov 核心代码实验预注册计划

## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: plan
- Origin Date: 2026-08-09
- Verification Status: UNVERIFIED
- Version Label: code_plan_v1
- Upstream Dependencies: `docs/AutoMarkov_complete_development_specification.md`

本文件是设计工件，不表示命令已经实现、suite 已可运行或结果已经验证。所有版本、commit、数据 revision、task-card 文本、预算和阈值必须在首个 confirmatory run 前写入冻结 manifest。

## Experiment Overview

- **Title**: AutoMarkov evidence-grounded decision-process compilation benchmark
- **Objective**: 比较 AutoMarkov 与五种受控方法在六类任务上，从冻结 `TaskCard` 产生语义、形式、API 与行为均有效的环境，并评价有效环境上的 RL 策略质量与自动化成本。
- **Primary hypothesis**: 在 `AUTO` 轨的 `v1_canonical` 至 `v4_evidence_split` 上，AutoMarkov 相对预注册主比较方法 `react_executor` 的 `E2EValid` 绝对差，其 suite-stratified 单侧 97.5% confidence interval 下界大于 0。
- **Co-primary policy hypothesis**: 在同一 `AUTO/v1..v4` outcome mask 上，AutoMarkov 相对 `react_executor` 的 gate-aware normalized policy score 达到固定 non-inferiority margin `0.05`；该尺度令 random policy 为 0、reference policy 为 1，因此 `0.05` 等于 reference–random return gap 的 5%。
- **Type**: simulation
- **Design**: 主 intention-to-run grid 为六个 suite × 每 suite 五个冻结 `TaskCardVariant` × 两条独立轨道 × 六个自动方法 × `n_pair` 个 generation-pair slots；`n_pair` 由首个 generation/tool call 前的功效设计门禁冻结；每个 suite 唯一冻结 source-access mode 与 required implementation route，二者不另增维度；method manifest 在运行前把每个 method cell 冻结为 `RUN` 或有证据的 `N/A`，只有 `RUN` cell 执行 `n_pair` 次 paired generation，每个有效环境使用十个 paired RL seeds。六项核心消融使用独立 `AUTO/v1..v4` paired ledger。

## Research Question and Estimands

### Primary research question

在预注册 `AUTO` 主轨中，相同 task card、基础模型、采样参数、检索预算、工具权限和生成 pair 下，AutoMarkov 是否提高强定义的端到端有效率，同时保持 gate-aware normalized policy score 的非劣性？

### Primary estimand

`AutoMarkov` 与预注册主比较方法 `react_executor` 在 `AUTO` 轨的六个 suite、四个完整语义 variants（`v1_canonical` 至 `v4_evidence_split`）和全部 generation pairs 上的 paired `E2EValid` 平均绝对差。`E2EValid = 1` 当且仅当 text、formal、API 和 hidden behavior 四个 gate 全部通过；其余 baseline comparisons 为 Holm-adjusted secondary family。`v5_clarification_required` 的 `AUTO` run 不进入 `E2EValid` 或 policy co-primary estimand，而以独立 post-terminal signed `ClarificationEvaluationVerdict` 派生的 `SafeClarificationRequired` 评价正确澄清行为。`HITL-ORACLE` 不进入主估计量，作为预注册机制轨独立估计和报告。

### Secondary estimands

- text contract field F1、object classification accuracy、formal closure 与 observation leakage rate；
- API/property/metamorphic/differential/behavioral test pass rate；
- normalized policy score、return、success/win rate、area under learning curve 与 sample efficiency；
- LLM calls、tokens、Tavily calls、repair iterations、wall time、environment steps 与 GPU hours；
- `AUTO` 与 `HITL-ORACLE` 两条轨道的 treatment interaction。

## Frozen Factors

### Core suites

| Suite ID | Domain object | Frozen upstream target | Evaluation focus |
|---|---|---|---|
| `taxi_mdp` | finite discrete MDP | Gymnasium `Taxi-v4` | 可枚举 transition/reward/termination fidelity |
| `memory_pomdp` | POMDP | MiniGrid `MiniGrid-MemoryS17Random-v0` | memory requirement 与 observation non-leakage |
| `mpe2_full_state_mg` | cooperative MG adaptation | MPE2 `simple_spread_v3` | full-state actor symmetry、coverage、collision 与 joint-action effect；另运行 native-local POSG ablation |
| `smacv2_posg` | cooperative POSG | SMACv2 `protoss_5_vs_5` | decentralized actor boundary、action mask 与 held-out configurations |
| `metadrive_pomdp` | sensor-based POMDP | MetaDrive `ScenarioEnv` + ScenarioNet | held-out scenario IDs、安全与 route completion |
| `citylearn_posg` | cooperative POSG | CityLearn frozen challenge schema | energy conservation、future leakage 与 held-out period KPIs |

每个 suite 的 source-access/route contract 在首个 generation 前冻结，所有方法、轨道、variant 和 pair 共用：

| Suite ID | Source access | Required route |
|---|---|---|
| `taxi_mdp` | `SYNTHESIS` | `GENERATE` |
| `memory_pomdp` | `REUSE` | `COMPOSE` |
| `mpe2_full_state_mg` | `REUSE` | `COMPOSE` |
| `smacv2_posg` | `REUSE` | `COMPOSE` |
| `metadrive_pomdp` | `REUSE` | `COMPOSE` |
| `citylearn_posg` | `REUSE` | `COMPOSE` |

`SYNTHESIS/GENERATE` 的 Taxi Allowed Evidence 只含公开规则与 API contract，禁止官方 env import、源码和完整 transition table。由于 RLlib 固定的 Gymnasium 1.2.2 仍分发 Taxi-v3 源码，Taxi generation/training cell 必须使用 `rllib-taxi-synthesis` deny-layer profile，并在 image build 与每次 preflight 证明 Taxi source/bytecode/resource/wheel/sdist/cache 的 direct read、resource lookup、`find_spec`/import 与 cache discovery 全部 fail closed；Taxi-v4 仅在 sealed evaluator 作 gold。其余 suite 必须复用冻结官方内核并组合 wrapper/adapter，不重新实现 physics/dynamics。mode/route 缺失、方法间漂移或权限越界在 generation 前 fail closed；由于它们由 `suite_id` 函数决定，主 grid 仍恰为 360 cells。

权威上游入口：

- Gymnasium [v1.3.0 release notes](https://gymnasium.farama.org/main/gymnasium_release_notes/) 记录 `Taxi-v4`；
- MiniGrid [Memory environment](https://minigrid.farama.org/environments/minigrid/MemoryEnv/)；
- Farama [MPE2 repository](https://github.com/Farama-Foundation/MPE2)；
- Oxford WhiRL [SMACv2 repository](https://github.com/oxwhirl/smacv2)；
- MetaDrive [repository](https://github.com/metadriverse/metadrive) 与 [ScenarioNet repository](https://github.com/metadriverse/scenarionet)；
- CityLearn [official repository](https://github.com/citylearn-project/CityLearn)；
- Ray [RLlib multi-agent environment documentation](https://docs.ray.io/en/latest/rllib/multi-agent-envs.html)。

链接只建立 authority identity，不代替冻结 provenance。每个 suite 的 `reproduction_manifest` 必须记录实际 tag/commit、package version、dataset revision、license 和 checksum。

### Task-card variants

每个 suite 预注册恰好五个 `TaskCardVariant`：`v1_canonical`、`v2_paraphrased`、`v3_reordered_longform`、`v4_evidence_split`、`v5_clarification_required`，共三十份 task cards。

- 每个 `variant_id` 对应一份 runner-visible policy manifest，其中只含完整 task-card text、allowed-evidence manifest、blocked-source manifest，以及 sealed gold、clarification oracle 和 hidden evaluation 工件各自 typed `sealed-v1` nonce-backed commitment；runner 按 method manifest 构造更窄的 agent-visible view。`automarkov_no_evidence` 的 view 恰好只含 task-card text 与不透明 sealed commitments，allowed/blocked evidence manifest、source metadata、evidence handle、snippet 和 retrieval cache 均不进入该 principal；其他 method 只获得其冻结 capability 允许的 evidence view。任何 view 不得含原始/opaque artifact ID、gold 内容、oracle answer、hidden evaluator location 或可解析出这些内容的路径。commitment 使用本计划 Publishable Outputs 定义的 closed JCS preimage 和高熵 sealed nonce，nonce 不向 generation principal 公开，防止对低熵答案作字典枚举。clarification 缺口和 answer set 保存在 oracle-owned sealed manifest，generation 进程从不挂载该 root。`HITL-ORACLE` 只能经限额 broker 逐问取得当前问题的回答，`AUTO` 不获得 broker capability。
- 五个 variant 是 task-card instances，不是方法或消融。它们共享同一 suite 的完整 gold contract/spec、初始条件、允许参数和评价定义；差异只来自合法表述、信息顺序、允许证据分布以及预注册的澄清缺口。
- `v5_clarification_required` 在 `AUTO` 轨刻意缺少完成形式化所需的高影响语义。正确行为是输出 typed clarification-required result，逐项指出全部预注册缺口，不猜测、不创建 formal/environment artifact，也不访问 sealed oracle。`SafeClarificationRequired=1` 当且仅当 terminal result 与 runner attestation 之后的独立 signed clarification request/verdict chain 同时证明这些条件；该 outcome 是独立 secondary endpoint。`HITL-ORACLE` 轨由固定 oracle 补全同一缺口后，才按正常四 gate、训练和 policy outcome 评价。
- 具体文本在后续由 `$to-tickets` 发布的 benchmark-construction tracer ticket 中构建、独立审查并预注册；ticket number 在 GitHub 创建后回填，本计划不引用尚不存在的编号，也不提前伪造文本。
- 任何 confirmatory generation 启动后，不得根据输出难度改写 task card。必要修订产生新的 preregistration version，旧 run family 不混入新统计。

### Methods

| Method ID | Method | Eligibility rule |
|---|---|---|
| `single_llm` | Single LLM direct generation | 单次方法合同，不引入隐藏 repair loop |
| `react_executor` | ReAct + code executor | 共享相同执行次数与工具权限 |
| `alamp_paper_spec` | A-LAMP paper-spec reimplementation | 依据论文公开的方法合同独立实现；冻结角色、schema 和反馈关系，不能声称官方代码复现 |
| `agent2_paper_spec` | Agent² paper-spec reimplementation | 依据论文公开的方法合同独立实现；只在冻结的公共 search/training space 内优化，不能声称官方代码复现 |
| `agent2world_clean_controlled` | Agent2World-inspired clean controlled variant | 仅依据可合法引用的论文级角色/反馈概念独立实现；不复制、port、vendor 或执行受限 upstream code，不执行 SFT |
| `automarkov` | AutoMarkov | 完整 evidence、typed artifact、dual gate、behavioral test 与 nearest-cause rollback 路线 |

`expert_gold` 只作 reference upper bound，不参与自动化成功率比较，也不计入六个自动方法。`react_executor` 必须执行 `6 suites × 5 variants × 2 tracks` 的全部 60 个 method cells；其 co-primary eligibility mask 固定为 `AUTO` 的 24 个 `v1..v4` cells，`AUTO/v5` 仅进入 `SafeClarificationRequired` family，`HITL-ORACLE/v1..v5` 进入 secondary/mechanism outcomes。其他 paper-spec method 对某种对象无定义时，在首个 run 前由 method manifest 标记 `N/A`，冻结该 comparator/track/outcome 的 `eligible_cells`、outcome mask 与 family cardinality，并从对应 suite 的 eligible baseline set 排除；运行后不得改标或按结果改变 family，`N/A` 也不能记为失败。

### Ablation methods and ledger

六项消融是 AutoMarkov 的 controlled component removals，使用独立 manifest/ledger，不计入六个主方法：

| Ablation method ID | 唯一 capability diff |
|---|---|
| `automarkov_no_evidence` | 禁用 Tavily retrieval 与 `EvidenceLedger`，仅保留 task card |
| `automarkov_no_text_critic` | 禁用 Text Critic |
| `automarkov_no_formal_critic` | 禁用 Formal Critic，保留 deterministic formal validators |
| `automarkov_single_agent_workflow` | 以同一 Qwen 的冻结顺序 workflow 替代多角色调度 |
| `automarkov_no_simulation_tester` | 禁用 public Simulation Tester 的 property/metamorphic/differential/trajectory tests，保留 static/unit/API/seed/deterministic core invariant gate |
| `automarkov_no_training_feedback` | 禁用 pre-freeze `PublicDevLearningProbe` 与最近致因回退，保留 sealed 后冻结训练/评估与只读诊断 |

ablation ledger 固定 `track=AUTO`、六个 suites、`v1..v4`、六个 ablation IDs、每 cell 同一个冻结 `n_pair`：恰好 144 cells/`144 × n_pair` generation slots，全部在首个 run 前标记 `RUN`。generation job 只读取不含任何 method result 的预冻结 `pair_binding_id`；full/ablation 都 terminal 后，coordinator/analysis 才以 tuple 生成 signed `AblationReferenceBinding`，任何 full run identity/manifest/status/artifact/hash/output/cache 都不挂载到 ablation sandbox。full method 不重复运行，有效 candidate 使用同一十个 RL seeds。

每个 strict `AblationExecutionPlan` 必须把相对 full graph 的唯一 capability diff、closed `omitted_gate_ids`（可为空）、`execution_topology`、closed approval-predicate projection、expected missing artifact kinds、始终 required gates，以及其余 prompts/model/access/route/budget/pair/schema/code hash 全部冻结。runner 只能为 exact plan 中的一个 omitted state gate 签发主规格 `GateOmittedByDesign` event；event 与同 transition 的未省略 reports 共同推进 reducer，但不伪造 pass 或 validation claim。`no_evidence` event 的 `output_artifact_ids` 必须恰好绑定一个 typed `EvidenceOmissionRecord`，line 5 仍执行该 omission branch，仅跳过 ledger 初始化与 line 7 retrieval；其 approval job 使用主规格 `NoEvidenceApprovalProjection`，只读取 task card、candidate、omission binding、opaque sealed commitments 和无需 evidence 即可确定的 schema/structural/API/public-dev reports。Allowed/Blocked Evidence manifest、ledger、source metadata、handle/snippet/cache、evidence-derived reports/IDs/hash 均不挂载，结构化 rejection 也只能返回 branch-visible closed predicate ID，不能向 revision loop 泄漏隐藏 evidence。其他 omission event 的 output tuple 为空。`no_evidence`、`no_text_critic`、`no_formal_critic`、`no_simulation_tester` 和 `no_training_feedback` 的独立 gate/call mapping 完全采用主规格 11.11。single-agent 的 `omitted_gate_ids=()`，只签发 non-transition `ExecutionTopologySubstituted` event，全部 gates 仍执行。关闭步骤释放的预算不能转移。strict/deterministic core、approval authenticity、sealed、sandbox、secret、license、fixed-commit、artifact/event integrity、budget 与 independent outcome evaluators 永远不可 mask。

### Deferred work

`Agent2World SFT`、训练数据生成和 checkpoint fine-tuning 明确 deferred，不属于本 confirmatory matrix。`agent2world_clean_controlled` 的 inference-time clean implementation 仍在本计划内；受限 upstream 只允许在仓库外、许可允许的 research/evaluation 环境单独验证，结果不得混入本方法。未来 SFT replication 必须使用独立 preregistration、runtime profile、许可审查、官方 repository commit、dataset revision、model ID 与训练预算；不得把小规模替代训练称为完整复现。

### Tracks, generations and seeds

- Tracks：`AUTO` 是唯一 confirmatory 主轨；`HITL-ORACLE` 是预注册 secondary/mechanism 轨。两轨完整运行、分开分析且禁止 pooling；oracle 只依据 frozen gold semantics 回答，每任务最多三轮、每轮最多三个问题。
- Approvals and replacement：两轨都在 run manifest 中登记同一个 source/hash/version-frozen `experiment_approval_policy` principal，以及它的 Ed25519 key ID/public key、validity/revocation contract。除 `automarkov_no_evidence` 外，它只按 generation-visible task card、Allowed Evidence、strict schema、critic/traceability/public-dev reports 和 canonical parents，对 exact artifact ID 产生主规格 closed `SignedApprovalEvent`；no-evidence job 使用冻结的 `NoEvidenceApprovalProjection`，排除 Allowed/Blocked Evidence、ledger、source metadata、handle/snippet/cache、evidence-derived report/ID/hash，并只返回由 branch-visible checks 确定的 closed reason code。repository 验证完整 JCS signature、policy/input/run/sequence/hash binding、branch-specific input allowlist/taint closure 与 replay index 后才追加。policy 不得读取 sealed root、调用 LLM、提问或按 method/result 改阈值；只有 exact ablation plan 可投影掉相应 omitted component predicate，保留 predicate/阈值完全不变。clarification broker 仅返回当前 answer payload，不批准工件，也不接收/返回 artifact metadata。另行冻结 `SignedRunReplacementPolicy` 及 authority key：root ordinal 固定 0、child 固定 parent+1、每 parent 最多一个 child；仅首个 generation/tool call 前的 runtime-identity replacement 可复用原 confirmatory slot，之后的 runtime replacement 或 candidate-freeze approval revocation只记录原 slot terminal failure并可建立 nonconfirmatory child。replacement cancellation-control execution 的 terminal record、parent `TerminalResult`、child sequence-0 event 与 attestation 按主规格在同一跨-run transaction 持久化。`AUTO/v5` terminal run 不创建 clarification child，post-terminal verdict/outcome不进入任何 generation input；`HITL-ORACLE` 是独立预注册 root run。除 freeze gate 前已登记的 policy 外，实验不得引入临时人类审批或 replacement authority。
- Generations：完整 intention-to-run ledger 包含 360 个 `suite_id × variant_id × track × method_id` cells 和各自 `g00..g{n_pair-1}` slots。method manifest 在 preflight 前把 cell 冻结为 `RUN` 或 `N/A`；每个 `RUN` cell 恰好执行 `n_pair` 次生成，`N/A` cell 不启动生成且保留 reason/evidence/eligibility decision。实际 attempt 数等于 `n_pair × RUN cell count`，不能把 `N/A` 计为 failure 或伪称已执行。
- Pairing：同一 pair 共享 task card、model checkpoint、temperature、`top_p`、max tokens、retrieval budget、tool budget、timeout class 和 generation seed；方法特有步骤不能扩大公共预算。
- RL seeds：每个有效环境固定预注册 `1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009, 1010` 十个 slots，每个 slot 必须保留 signed success 或 terminal-failure record；任一 slot 缺失或失败令该 candidate `GoldPolicyEvaluationValid=0`、`Q_gate=0`，但仍保留在 confirmatory denominator，不得删除 candidate 或替换 seed。
- Public learning probe：每个 suite manifest 在 generation 前冻结 modern-RLlib lightweight learner/config、step/wall-time budget、pair-derived probe seed namespace、diagnostic predicates 与 nearest-cause routing；它只运行 candidate/public-dev inputs，seed 与最终十个 RL seeds 不重叠，产物不能进入 policy outcome。除 `automarkov_no_training_feedback` 的 registered omission 外，full 与其他 ablations 使用同一 probe contract。
- 对存在完整任务语义的 eligible cells（`AUTO/v1..v4` 与 `HITL-ORACLE/v1..v5`），无效环境保留为 `E2EValid = 0`，不得从对应 denominator 删除；gate-aware policy score 将其赋值为 0，另行报告的 conditional policy-quality analysis 只包含 jointly-valid pairs。`AUTO/v5` 是预注册的结构性 outcome mask：不训练 RL、不计算 `E2EValid`/`Q_gate`，也不把正确 abstention 编码为失败。
- 生成 crash、timeout、sandbox violation 或缺失 required artifact 均按预注册 failure taxonomy 记录，不自动 retry。

### Design-power gate

任何 generation/tool call 前，独立 design-analysis execution 必须按主规格 11.9 生成 signed/content-addressed `DesignPowerManifest` 与 `DesignPowerReport`。manifest 固定候选集合 `n_pair ∈ {20,24,30,40,60,80}`、24 个 confirmatory strata、两个 exact co-primary gates、十个 RL seeds、2,000 个 deterministic Monte Carlo datasets、每个 gate 不少于 10,000 次的 production counter bootstrap、公共随机数与 candidate-prefix reuse、彼此独立的 `design_power_dgp_seed`/`design_power_bootstrap_seed`、counter/JCS/schema/implementation hashes，以及所有 nuisance inputs/evidence。外层 dataset 只能由主规格 `design-power-dgp` counter domain 生成；内层 production bootstrap 使用带 dataset/gate scope 的既有 paired/nested domains，candidate value 不进入 entropy，所有候选共用最大 80-pair stream 的前缀。design alternative 固定为 `E2EValid` aggregate difference `+0.10`、policy difference `0.00`；null boundaries 固定为 `0.00` 与 `-0.05`。discordant-pair rate、marginal validity、invalid-to-zero rate、pair/seed variance、ICC/covariance 和 stratum heterogeneity只能来自 non-overlapping Public Dev pilot、可引用论文/官方 benchmark，或无证据时的保守最大方差边界；不得读取 Sealed Gold、hidden/confirmatory outcome或任一实际方法结果。

每个 simulated dataset 必须执行 production estimator、nested paired counter bootstrap、invalid-to-zero 与两门 decision rules。report 对每个候选给出 marginal/joint power、null false-success、exact binomial 95% intervals、MDE、policy bound 与 interval width。选择最小 `n_pair`，使 joint-power 95% lower bound `≥0.80`、每个 marginal-power lower bound `≥0.90`、null false-success 95% upper bound `≤0.025`。`DesignFreezeVerdict` 是独立的 experiment-preflight enum（`READY | BLOCKED_DESIGN`），持久化于 signed report/manifest，且不属于 run reducer 的 `RunState`；无候选满足或预算未批准时 preflight 固定 `BLOCKED_DESIGN`，不得创建 run，必须经新 preregistration/budget approval 解决。所有主方法、tracks、component ablations 与 MPE2 information-structure ablation 共用所选值；首个 run 后禁止 optional stopping、增补或替换 pair。即使选择 20，也必须发布 MDE/precision report。

## Setup

- **Language/Framework**: Python 3.11/3.12；PyTorch；Gymnasium；PettingZoo；RLlib；suite-specific official simulator/adapters
- **Generative runtime**: local Qwen3.6-35B-A3B through vLLM only
- **Agent framework**: CAMEL-AI where agent orchestration is required
- **Planning backend**: Unified Planning only for OOD/PDDL routing; OOD tasks不进入六个 core suites
- **Control-plane security**: PyCA `cryptography==49.0.0` for Ed25519/X.509、Trail of Bits `rfc8785==0.1.4` for JCS、Python stdlib `ssl` pinned to TLS 1.3；实际 wheels/OpenSSL/CA/image hashes 由 runtime profiles 冻结
- **Tracking**: SwanLab offline-first；canonical event stream 与 manifest 始终是结果 authority
- **Working Directory**: repository root
- **Dependencies**: 每个 process execution 解析到一个冻结 `RuntimeProfile`；run manifest 冻结 profile graph 与 `LocalLlmRuntime`、`EvidenceGateway`、`ClarificationBroker`、`ExperimentApprovalPolicy`、`RemoteEnv`、`SealedEvaluator`、`FixedCommitRunner` closed protocol edges，`SealedEvaluator` edge 单独冻结 E2E、post-terminal clarification 与 post-training policy 三种 request/verdict message schema、coordinator/evaluator keys、profile identities、budget/deadline、commitment binding 与 transcript hash；`RemoteEnv` 仅允许隔离的 trainer→environment-worker 与 sealed-evaluator→sealed-environment-worker 两类 topology，冻结唯一 `automarkov.remote-env-frame.v1` codec version、schema hash、space-adapter registry hash及 frame/tensor ceilings；authoring 只有 versioned seam clients，Tavily SDK/key/egress 只属于 evidence-gateway profile，vLLM provider URL/credential 只属于 inference profile；不允许 universal mutable environment
- **Environment**: compute、GPU model/count、CPU、memory、driver、CUDA 和 container digest 在 suite manifest 中冻结

## Inputs

| Input | Contract path | Description |
|---|---|---|
| Preregistration | `docs/experiments/automarkov-code-experiment-plan.md` | 本计划的冻结 revision 与 hash |
| Design-power manifest | `artifacts/experiments/<experiment_id>/manifests/public/design_power.json` | 首个 generation 前冻结的 `n_pair` candidate set、design/null alternatives、nuisance evidence、counter/bootstrap implementation、simulation budget 与选择规则 |
| Task-card manifest | `artifacts/experiments/<experiment_id>/manifests/public/task_cards.json` | 三十个 `TaskCardVariant` 的 public identity、text hash 与轨道合同 |
| Suite manifest | `artifacts/experiments/<experiment_id>/manifests/public/suites.json` | 六个 suite 的 source-access mode、required route、upstream/runtime/预算与权限合同 |
| Reproduction manifest | `artifacts/experiments/<experiment_id>/manifests/public/reproduction.json` | public source/runtime/model/data/compute provenance；不含 sealed resource identity/hash/path |
| Method manifest | `artifacts/experiments/<experiment_id>/manifests/public/methods.json` | 六个 method contracts、budgets 与 eligibility |
| Ablation manifest | `artifacts/experiments/<experiment_id>/manifests/public/ablations.json` | 六个 closed execution plans/gate masks、144 个固定 `RUN` cells、budgets、outcome masks、result-free pair rule 与 `reference_method_id=automarkov` |
| MPE2 information-structure ablation manifest | `artifacts/experiments/<experiment_id>/manifests/public/mpe2_information_structure_ablation.json` | `mpe2_full_state_mg` 与 `mpe2_native_local_posg` 的唯一信息结构差异、`4 × n_pair` 个 native generation slots、result-free pair binding、condition-specific calibration gates、公共 full-state policy scale、outcome masks 与独立 two-hypothesis family |
| Signed run replacement policy | `artifacts/experiments/<experiment_id>/manifests/public/run_replacement_policy.json` | `SignedRunReplacementPolicy`；cause allowlist、authority/key、root/child ordinal、generation/tool-call boundary、每 parent 单 child、slot reuse/failure 与原子 event branch 合同 |
| Gold score calibration | evaluator-owned signed artifact | 每 suite 的 metric direction、gold environment/adapter、random/reference policy、pilot seeds/episodes、冻结 returns、positive gap/LCB 与 `min_reference_random_gap`；public preflight response 只含 schema-limited signed calibration verdict/aggregate 和 nonce-backed sealed commitment |
| Fixed-commit job manifest | `artifacts/experiments/<experiment_id>/jobs/<job_id>.json` | exact 40-hex commit、repository URL、profile image digest、input IDs、phase、单一 payload command、资源限制、output schema 与 network-policy hash；policy-export job 只登记 training terminal record ID/hash 与 checkpoint-tree commitment，禁止 checkpoint locator/path/private descriptor/opaque trainer-local handle 值 |
| Sealed manifest | evaluator-owned path | generation side 只能看到域分离、nonce-backed commitment；不得读取原始 identity/content hash、nonce、内容或 locator |

这些路径是后续 CLI 与 artifact contract 的预注册目标；当前 `Verification Status` 为 `UNVERIFIED`，文件存在性和 schema 尚未由本任务声明。

## Entry Command Contract

以下是待实现 CLI 的 acceptance contract，不是本次已执行命令。每条命令必须解析显式 manifest，不从 mutable defaults 推断研究参数。下列 experiment command 是 job manifest 中的单一 payload command；直接执行只允许工程 pilot。任何进入 publication-grade 报告的 generation、training、sealed evaluation 或 analysis 都必须由 `FixedCommitRunner` 在 detached exact commit 上执行，并具有可验证的 runner-signed `ExecutionAttestation`。

### Publication-grade submission

```bash
uv run automarkov execution submit \
  --job-manifest artifacts/experiments/<experiment_id>/jobs/<job_id>.json \
  --attestation-output artifacts/experiments/<experiment_id>/attestations/<job_id>.json
```

成功条件：runner 在 clean checkout 验证 exact 40-hex commit、repository URL、container/profile digest 与 input hashes；执行 default-deny network policy，只在 generation retrieval phase 允许 `EvidenceGateway` 通过 TLS 访问 `api.tavily.com:443`，证据快照冻结后立即撤销，其他 phase 全程无外网；只运行 manifest 中的单一 payload command；输出通过 schema、secret 与 sealed-gold scan；每个 job 恰好产生一个 `ProcessExecutionTerminalRecord`，返回的 `ExecutionAttestation` 签名、runner identity、process terminal record、可空 Run terminal-result pair、commit、command、inputs、seeds、资源使用、network-policy、脱敏 mount-table、capability-decision/egress-log hashes 和 output hashes 全部可验证。policy-export job 的 manifest、exact payload command、process terminal record、attestation、脱敏 mount-table 与任何 portable artifact 均不得序列化 checkpoint locator/path/private descriptor/opaque trainer-local handle 值；只允许绑定 training terminal record ID/hash、checkpoint-tree commitment 及不含该私有值的 capability-decision hash。非终态 job 的 Run terminal-result pair 必须为 null；moving branch/tag、dirty patch、digest mismatch、未授权 mount/capability/egress、无签名结果或 payload command 漂移均在执行/聚合前拒绝。

`FixedCommitRunner` return allowlist 永久拒绝 raw RLlib checkpoint、checkpoint tree/locator、private descriptor/opaque trainer-local handle、pickle/cloudpickle 与 optimizer state；trainer-local export 后只允许返回 weights-only safetensors、`PolicyExportManifest`、source-checkpoint commitment、对应 `ProcessExecutionTerminalRecord` 及其他已登记 portable artifacts。

### Preflight

```bash
uv run automarkov experiment preflight \
  --plan docs/experiments/automarkov-code-experiment-plan.md \
  --experiment-id <experiment_id> \
  --design-power-manifest artifacts/experiments/<experiment_id>/manifests/public/design_power.json \
  --public-manifest-root artifacts/experiments/<experiment_id>/manifests/public
```

成功条件：验证 plan hash、全部公开 manifest、fixed-commit job schema、runner verification key、runtime profile graph、source pin、task-card 数量、method/ablation eligibility、closed gate projections、budget completeness、每 suite `PublicDevLearningProbe` 合同、`experiment_approval_policy` source/hash/input allowlist/Ed25519/replay contract、`SignedRunReplacementPolicy` 的 authority/cause/ordinal/单 child/slot rules 及 runtime/approval 两个 atomic CAS fixture、sealed evaluator handshake，以及每个 suite 的 signed `GoldScoreCalibration` 已按主规格 11.7 的 paired pilot→episode 100,000-replicate counter bootstrap 与 inverse empirical-CDF 生成，并满足方向调整后的 one-sided 97.5% gap LCB 严格大于冻结 `min_reference_random_gap>0`；任一 suite calibration 失败即阻断该 suite，不执行生成或训练，也不把失败映射为 candidate `Q_gate=0`。preflight 还须重验 signed `DesignPowerManifest/Report` 的 nuisance provenance、counter/JCS/implementation hash、2,000-dataset simulation、candidate-prefix common random stream、exact binomial intervals与阈值选择，并证明所选 `n_pair` 一致绑定全部 main/ablation/information-structure manifests；失败固定为 `BLOCKED_DESIGN`。MPE2 信息结构 preflight 另验证 `4 × n_pair` 个 native slots、result-free pair binding、full/native 两份 condition-specific calibration gates、精确等于 full-state calibration 的公共 policy-scale commitment、post-terminal closed binding/JCS signature、analysis key validity/revocation/replay、artifact key 与独立 two-hypothesis analysis family。policy-export preflight 另以 schema/command/attestation 负例证明任何 checkpoint locator/path/private descriptor/opaque trainer-local handle 值都被拒绝，而 training terminal record ID/hash 与其已绑定的 checkpoint-tree commitment 必须 exact match。generation retrieval preflight 还须证明 Search/Extract/Crawl schema 全部显式固定 `include_usage=true`，authoring 无 provider credential/egress。

runtime/profile preflight 还必须逐 edge 重算 `RemoteEnv` codec/schema/space-adapter registry hash 与 ceilings，并通过 frozen golden frame vectors、两个实际 profile 的 decode→encode byte identity、全部 space branch round-trip、`Discrete.dtype` 与 MiniGrid `MissionSpace→FiniteText` exact-set vectors，以及 header/frame overflow、shape multiplication overflow、wrong offset/hash、trailing/unreferenced tensor、noncanonical dtype/endian/layout、NaN/negative-zero、越权 infinity 和 trainer/sealed principal 交叉负例；任一不匹配在 environment process 启动前 fail closed。

### Paired generation

```bash
uv run automarkov experiment generate \
  --experiment-id <experiment_id> \
  --suite-id <suite_id> \
  --variant-id <variant_id> \
  --track <AUTO|HITL-ORACLE> \
  --method-id <method_id> \
  --generation-pair-id <g00..g{n_pair-1}> \
  --public-manifest-root artifacts/experiments/<experiment_id>/manifests/public \
  --output-root artifacts/experiments/<experiment_id>/runs
```

runner 必须由 `suite_id` 从 signed suite manifest 解析唯一 source-access mode 与 required route；CLI 不接受可覆盖这两个字段的 flag。`HITL-ORACLE` 的 coordinator 另行把受限 Unix broker capability 注入该 job；它不作为 CLI 参数、环境变量值或 public manifest 字段出现。broker 自行读取 sealed oracle manifest，只返回预算内当前问题的 answer payload，不返回任何 artifact metadata/identity/content hash/nonce/locator，不产生 approval event，并记录 sealed append-only transcript。`AUTO` job 的 mount、capability 与 egress attestation 必须证明 broker 不可达。两轨的 coordinator 只接受 frozen `experiment_approval_policy` 对 exact candidate ID 的 verified `SignedApprovalEvent`；policy process 无 sealed mount，broker 与 policy 之间无 protocol edge。bad/revoked key、partial-field signature、event/nonce/sequence replay 或 artifact/policy/report substitution 直接 `FAILED`。

### Paired ablation generation

```bash
uv run automarkov experiment ablate \
  --experiment-id <experiment_id> \
  --suite-id <suite_id> \
  --variant-id <v1_canonical|v2_paraphrased|v3_reordered_longform|v4_evidence_split> \
  --ablation-method-id <ablation_method_id> \
  --generation-pair-id <g00..g{n_pair-1}> \
  --pair-binding-id <public_result_free_pair_binding_id> \
  --public-manifest-root artifacts/experiments/<experiment_id>/manifests/public \
  --output-root artifacts/experiments/<experiment_id>/ablations
```

runner 只接受 `track=AUTO`、ablation manifest 中精确登记的 144 cells，以及只含 task/model/access/route/seed/budget commitments 的 exact pair binding。它验证 closed gate mask/predicate projection、同一 source-access/route、未转移预算、approval policy 和所有不可 mask 安全 gate；未知 ablation ID、`v5`/HITL cell、result-bearing pair input、额外关闭能力或配置漂移均 fail closed。

双方 terminal 后，只有无 generation capability 的 coordinator/analysis principal 执行 reference binding：

```bash
uv run automarkov experiment bind-ablation-reference \
  --ablation-run-manifest artifacts/experiments/<experiment_id>/ablations/<ablation_run_id>/run_manifest.json \
  --full-run-manifest artifacts/experiments/<experiment_id>/runs/<full_run_id>/run_manifest.json \
  --output artifacts/experiments/<experiment_id>/ablations/bindings/<binding_id>.json
```

该命令验证独立 runner attestations、共同 `(experiment, suite, variant, AUTO, pair)`、`reference_method_id=automarkov`、pair-binding commitment 和 terminal 状态，输出 signed `AblationReferenceBinding`。binding/两个 run manifests 不得回流或挂载到任何 generation job。

### Pre-training sealed behavior gate

```bash
uv run automarkov experiment e2e-gate \
  --e2e-evaluation-request artifacts/experiments/<experiment_id>/runs/<run_id>/e2e_gate_evaluation_request.json \
  --runtime-profile <sealed_runtime_profile_id>
```

该命令只能在 sealed evaluator 权限域执行。signed strict `E2EGateEvaluationRequest` 显式绑定 run/manifest、candidate bundle、candidate `TaskContract`、`DecisionProcessSpec`、`EnvironmentBinding` 和 evaluator protocol/profile 的唯一 IDs/hashes；不含 gold locator。candidate code 只在无 sealed mount/key/locator/network 的独立 untrusted worker 执行，经 bounded typed protocol接收 opaque inputs并返回 candidate outputs；gold/reference code 位于另一 trusted worker，只有 evaluator comparator 同时读取两侧输出，expected values/trace不回传 candidate。evaluator 对 sealed gold contract/spec、API contract 和 hidden tests分别判定，向无 generation capability 的 run coordinator 返回 closed signed `E2EGateVerdict`，其中四个 exact bool 为 `text_passed`、`formal_passed`、`api_passed`、`hidden_behavior_passed`，且 request/verdict/candidate/四 subject IDs/hashes 均绑定。request 和 verdict 分别使用冻结 coordinator/evaluator Ed25519 key、RFC 8785 JCS、issued-at/32-byte nonce/key-status/replay合同。四门 conjunction 唯一定义 `E2EValid`；任何合法 false 将该 run 固定为 `E2EValid=0` 并终止为 `PARTIAL`，不启动 RL，不修补或重试同一 candidate。verdict 只进入无 generation capability 的 coordinator/受限分析流，不向 generation principal 返回 gate category、test identity、trace、expected value 或 counterexample；相应 RL outcome 按预注册合同为 missing-by-design。签名/binding/schema 无效、contamination 或 protocol 违规固定终止为 `FAILED`。

### Post-terminal sealed clarification evaluation

```bash
uv run automarkov experiment evaluate-clarification \
  --clarification-evaluation-request artifacts/experiments/<experiment_id>/runs/<run_id>/clarification_evaluation_request.json \
  --runtime-profile <sealed_runtime_profile_id>
```

该命令只适用于已经 `CLARIFICATION_REQUIRED` terminal CAS 且 runner 已签发 `ExecutionAttestation` 的 `AUTO/v5` run，并且只能在 sealed evaluator 权限域执行。无 generation/sealed capability 的 coordinator 先创建主规格第 7.6 节 `extra="forbid"` signed `ClarificationEvaluationRequest`：它显式绑定 run manifest/outcome mask、`ExperimentClarificationRequiredResult`（包含通用 `ClarificationRequiredResult`）、terminal result/event、execution attestation、从 `TerminalResult` 显式 roots 重算的 canonical terminal artifact-DAG closure hash、generation-visible `clarification_oracle` sealed commitment和 evaluator protocol/profile identities，不含 sealed gap/oracle identity、payload/content hash、nonce、locator、answer或 expected value。evaluator 只从已注册 commitment 在 sealed 域解析 frozen gap scoring manifest，验证 exact gap coverage、零 semantic guessing/introduced assumptions、零 formal/environment artifacts，以及 mount/capability/egress attestation 证明 `AUTO` 不可达 oracle broker；只向无 generation capability 的 coordinator/受限分析流返回 closed signed `ClarificationEvaluationVerdict.safe_clarification_required` 单一 bool，不返回逐项判断、gap identity/count、answer、trace 或 counterexample。

完全有效的 request/verdict 形成 `ClarificationOutcomeRecord(evaluated)`；合法 false 映射 `SafeClarificationRequired=0`。生成 contract failure、错误终态、缺 required artifact/request/verdict、frozen deadline timeout、signature/schema/binding/replay error、contamination 或 protocol violation由 frozen projector/analysis principal 签发 `ClarificationOutcomeRecord(invalid)` 并固定映射为 0；全部保留 intention slot，不能改标 `N/A`、静默删除或改变 `CLARIFICATION_REQUIRED` terminal snapshot。冻结 deadline 内只允许对 exact same request bytes/ID 做有界幂等 transport retry，禁止更换 subject、重签 request 或依据结果重新生成。

### Training

```bash
uv run automarkov experiment train \
  --run-manifest artifacts/experiments/<experiment_id>/runs/<run_id>/run_manifest.json \
  --rl-seed <1001..1010>
```

训练命令只接受 signed `E2EGateVerdict` 四个 bool 全部为 true 的 immutable environment binding，并验证 request/verdict/candidate/manifest/四 subject binding。训练 budget 只在 gold environment pilot 上确定，采用官方/参考预算；没有来源时，以学习曲线末段 20% 的增益阈值确定候选上限，并在任何 method comparison 前冻结。

每个成功的训练 seed 必须在 trainer 已绑定的同一 frozen RuntimeProfile、checkout 与 filesystem namespace 内启动无 sealed/gold capability 的一次性 `ProcessExecution` 并显式导出；该 export 复用 trainer profile，不创建新 profile 或 protocol edge，普通 checkpoint 不形成跨 profile handoff。successful training terminal record 必须先绑定 checkpoint tree 的 canonical manifest commitment；trainer-local supervisor 再从该 exact record 与 commitment 解析只在当前 namespace 有效的私有 inherited read-only descriptor 或 opaque trainer-local handle，并把它作为不序列化的 process-launch capability 交给 exporter：

```bash
uv run automarkov experiment export-policy \
  --run-manifest artifacts/experiments/<experiment_id>/runs/<run_id>/run_manifest.json \
  --rl-seed <1001..1010> \
  --training-terminal-record-id <training_terminal_record_id> \
  --checkpoint-tree-commitment <checkpoint_tree_commitment> \
  --output artifacts/experiments/<experiment_id>/runs/<run_id>/policy_exports/<seed>
```

exact payload command、job manifest 与 attestation 只持久化 training terminal record ID/hash 和 checkpoint-tree commitment，不含 checkpoint locator/path、descriptor number、opaque handle value 或任何可反推出它们的字段；该私有 capability 也不经 CLI argument、环境变量、mount-table value、artifact metadata 或 public seam 传递。trainer-local exporter 先验证 terminal record 的 signature/status/run/seed/trainer process/profile/namespace binding，重算 descriptor/handle 所指 read-only snapshot 的 canonical tree commitment并与 command/record 中同一 commitment exact match，再验证冻结 architecture、connector 与 observation/action adapter identities；缺失、跨 namespace、可写、commitment mismatch 或 TOCTOU 证据均 fail closed。随后按无环顺序输出一个 content-addressed、finite weights-only safetensors artifact，再输出单向引用该 tensor ID/hash 的 immutable signed `PolicyExportManifest`。tensor payload/envelope 不引用 manifest；manifest envelope 以 tensor、candidate、training terminal record 和 source-checkpoint commitment 为 direct parents，并绑定 run、seed 与 trainer/exporter execution identity。跨 profile repository 只接收 safetensors、manifest、commitment 与 terminal record；checkpoint、descriptor/handle、pickle/cloudpickle、optimizer state、Python object/code/import path 或可执行 connector 不得离开 trainer profile 或进入输出。export terminal 后立即关闭/销毁私有 descriptor/handle；无 checkpoint reader 的独立 verifier 仅重验 safetensors schema/finite values、manifest signature/hash/lineage。每个成功 seed 恰有一对 export artifacts；terminal-failure seed 不伪造 export。

全部十个 seed 达到 signed success 或 terminal-failure 且每个 successful training seed 已得到 export success/failure terminal record 后，无 generation/sealed capability 的 coordinator 从显式 artifact IDs 构造 immutable signed `policy_evaluation_request.json`。request 绑定 experiment/run/candidate/run-manifest、signed four-gate `E2EGateVerdict`、signed smoke-pass attestation、suite calibration、sealed evaluator profile 与冻结 adapter 的 IDs/hashes；canonical seed tuple 必须唯一且精确等于 `(1001..1010)`。三个 discriminated branch 为：`success` 绑定 successful training record、successful export terminal record、`PolicyExportManifest` 和 safetensors IDs/hashes；`training_failure` 只绑定 training failure record且 export fields 为空；`export_failure` 绑定 successful training record和 export failure record且 manifest/tensor fields 为空。closed common fields含 schema/signing domain、request ID、issued/not-before/expires、32-byte nonce、`signature_algorithm="Ed25519"`、coordinator key ID/signature；run manifest 冻结唯一 signing principal/Ed25519 key及 validity/revocation/clock contract，Ed25519 signature 覆盖除 signature 外完整 RFC 8785 JCS object并执行 request/nonce/key-run replay检查。sealed evaluator 只接受该显式 request，禁止扫描目录、采用“最新”文件或替换 seed/export。任一 failure 分支令 `GoldPolicyEvaluationValid=0`、`Q_gate=0`，evaluator 不尝试加载不存在的 policy。

### Sealed evaluation

```bash
uv run automarkov experiment evaluate \
  --policy-evaluation-request artifacts/experiments/<experiment_id>/runs/<run_id>/policy_evaluation_request.json \
  --runtime-profile <sealed_runtime_profile_id>
```

该命令只能在 evaluator 权限域执行，评估 frozen gold environment 上的 policy outcome 并产生预注册 aggregate metrics。它不替代、重跑或改写 pre-training `E2EGateVerdict`。generation profile 不得拥有 sealed locator 或 credential；结果只进入受限报告流，不反馈给同一预注册 run family。

### Aggregate analysis

```bash
uv run automarkov experiment analyze \
  --experiment-id <experiment_id> \
  --analysis-plan docs/experiments/automarkov-code-experiment-plan.md \
  --output artifacts/experiments/<experiment_id>/reports/confirmatory
```

analysis command 必须拒绝混合不同 plan hash、post-leak run family、未冻结 task card、不兼容 schema version，或缺少有效 runner-signed `ExecutionAttestation` 的 publication-grade run。

## Expected Outputs

| Output | Contract path | Format | Success criterion |
|---|---|---|---|
| Frozen run manifest | `runs/<run_id>/run_manifest.json` | JSON | 启动前冻结的输入 identity、budget、runtime/profile graph、protocol edges、seed、replacement policy 与 root/child ordinal 完整且 schema-valid；执行后 bytes/hash 永不改变且不含 status/output hash |
| Design-power report | `manifests/public/design_power_report.json` | signed strict JSON | 对每个候选 `n_pair` 给出 marginal/joint power、null false-success、exact binomial intervals、MDE、policy bound与precision；最小合格候选按冻结规则唯一选择，或在任何 generation 前固定 `BLOCKED_DESIGN` |
| Terminal result | `runs/<run_id>/terminal_result.json` | strict immutable JSON | payload 的 terminal CAS 中恰好生成一次，绑定 frozen fixed-commit job manifest、产生终态的 exact process terminal record ID/hash与 process execution identity、typed terminal/approval event references、`terminal_snapshot_event_head_hash` 与 payload output artifact IDs/hashes；job manifest、process terminal record和 payload outputs 是 exact artifact parents，不引用尚未生成的 execution attestation，后续 event 不覆盖，当前 approval/audit 状态由版本化 `RunAuditProjection` 重建；不得回写 run manifest |
| Audit projection | `runs/<run_id>/audit_projections/<as_of_sequence_no>.json` | strict immutable JSON | 只从 caller 指定 verified event head 确定性重放，绑定 projector version/hash、as-of sequence/head、previous projection、terminal result、当前 approval/deviation/outcome mask；content-addressed 且旧版本永不覆盖 |
| Process execution terminal record | `executions/<process_execution_id>/terminal_record.json` | strict immutable JSON | 每个 fixed-commit job 恰好一个，绑定 job/execution/profile/principal、success 或 terminal-failure、exit/reason、payload outputs、stdout/stderr、resource/network/mount/capability/egress hashes；policy-export record 只绑定 training terminal record ID/hash 与 checkpoint-tree commitment，不含 checkpoint locator/path/descriptor/handle 值；与 Run 是否到达 terminal state 无关 |
| Execution attestation | `attestations/<job_id>.json` | signed JSON | runner 在 payload outputs 与 process terminal record 全部定址后签发，单向绑定该 record 及 output hashes、runner identity/signature、exact commit、profile/input/command/network-policy、脱敏 mount-table、capability-decision/egress-log hashes、phase transitions、seeds 与资源记录；policy-export attestation 的 command/input/mount/capability fields 禁止 checkpoint locator/path/descriptor/handle 值，只绑定 training terminal record ID/hash、checkpoint-tree commitment及不含私有值的 capability-decision hash；只有产生 Run terminal CAS 的 job 才额外绑定非空 terminal-result ID/hash，其他 job 的该 pair 必须为 null；TerminalResult 不反向引用本 attestation |
| Event stream | `runs/<run_id>/events.jsonl` | JSONL | append-only、序号单调、terminal event 恰好一个；其后只允许 schema-closed audit/access-revocation events，不允许状态或结果重写 |
| Artifact bundle | `runs/<run_id>/artifacts/` | typed artifacts | required artifact 全部存在、hash 与 lineage 可验证 |
| Clarification result | `runs/<run_id>/clarification_required.json` | strict JSON | `AUTO/v5` 使用主规格的 `ExperimentClarificationRequiredResult` wrapper，其内部通用 result 的 gap 非空唯一，assumption/formal/environment IDs 为空，wrapper 精确绑定 `AUTO/v5` 与 outcome mask；DAG 无下游 formal/environment artifact，并以 `CLARIFICATION_REQUIRED` terminal event 结束 |
| Clarification evaluation request | `runs/<run_id>/clarification_evaluation_request.json` | signed strict JSON | 仅在 `AUTO/v5` terminal result 与 execution attestation 定址后由无 generation/sealed capability coordinator 签发；显式绑定 result/terminal/event/attestation/DAG/outcome mask和 sealed commitment，使用主规格 closed Ed25519/JCS/replay合同且不含任何 sealed identity/payload/hash/nonce/locator/answer/expected value |
| Clarification evaluation verdict | `runs/<run_id>/clarification_evaluation_verdict.json` | signed strict JSON | sealed evaluator 只返回绑定 exact request/subjects 的单一 `safe_clarification_required` bool；不返回分项判断、gap identity/count、answer、trace、expected value或counterexample；generation/evaluation failure 使用独立 signed invalid outcome，不伪造 verdict |
| Public evaluation | `runs/<run_id>/evaluation_public.json` | signed strict JSON | closed discriminated union：E2E branch 公开四 gates/`e2e_valid`；clarification `evaluated` branch exact-parent 绑定有效 request/verdict，`invalid` branch保留 slot并固定 `safe_clarification_required=0`及 closed reason。`outcome_mask_id`、`outcome_kind`、结构性 null、failure 和 missing 均可机械区分 |
| E2E gate evaluation request | `runs/<run_id>/e2e_gate_evaluation_request.json` | signed strict JSON | candidate freeze 后生成，显式绑定 run/manifest/candidate bundle、candidate TaskContract/DecisionProcessSpec/EnvironmentBinding、sealed evaluator protocol/profile 与唯一 IDs/hashes；不含 gold locator，evaluator 禁止目录扫描或隐式替换 |
| E2E gate verdict | `runs/<run_id>/e2e_gate_verdict.json` | signed strict JSON | evaluator 对 sealed gold 分别签发 text/formal/API/hidden-behavior 四个 exact bool；绑定 request/candidate/四 subject，四门 conjunction 唯一定义 `E2EValid`，且 verdict 不回流 generation principal |
| Policy export | `runs/<run_id>/policy_exports/<seed>/` | signed manifest + safetensors | 仅 successful training seed 存在；trainer-local supervisor 以 training terminal record ID/hash及其 checkpoint-tree commitment，在同一 profile/namespace 内通过未序列化的 inherited read-only descriptor/opaque handle 解析 checkpoint，exporter 重验整树后定址 finite weights-only safetensors；job/command/terminal record/attestation/export artifacts 均不含 locator/path/descriptor/handle 值；`PolicyExportManifest` 单向引用 tensor ID/hash并绑定 run/candidate/seed/training record、source-checkpoint commitment、`exporter_process_execution_id` 与 `trainer_profile_id`；跨 profile 的 success binding 还必须显式携带 successful export terminal record ID/hash；terminal-failure 或 missing-by-design seed 必须无目录、无伪造 export |
| Policy evaluation request | `runs/<run_id>/policy_evaluation_request.json` | signed strict JSON | 仅训练实际启动且十个 seed training/export terminal records 齐全后存在；精确包含唯一 `(1001..1010)`、smoke-pass attestation和 `success|training_failure|export_failure` branches，按主规格签名/replay合同验证；evaluator 只读显式 IDs，不扫描目录 |
| Gold policy evaluation | `runs/<run_id>/policy_evaluation_public.json` | signed strict JSON | closed `PolicyOutcomeRecord` 四分支；evaluator-signed `evaluated|invalid` 强制 request ID/hash exact parent，前者携带十 seed returns/aggregate/scores，后者保留十个 terminal bindings且两项 gate为零；projector-signed `missing_by_design` 只覆盖训练前按设计缺失且禁止 training/request fields；projector-signed `post_training_terminal` 绑定 terminal event、冻结十 seed expected set、全部已存在 records、missing complement与按 phase 条件存在的 request，将训练后 crash/timeout/预算耗尽/缺结果固定映射 `GoldPolicyEvaluationValid=0,Q_gate=0`。四分支仅以 typed `sealed-v1` commitment 表达 calibration/gold environment/adapter，且不含 sealed identity/content hash/payload/path/nonce |
| Training metrics | `runs/<run_id>/training/<seed>/metrics.jsonl` | JSONL | 训练未启动时整个 `training/` 缺失并由 `missing_by_design` 解释；训练启动后正常路径恰有十个 signed seed terminal slots，successful training terminal record 绑定 checkpoint-tree commitment 但不含 locator/path/descriptor/handle 值；post-training terminal failure 则由 `post_training_terminal` 明列 existing records 与 missing seed complement，禁止伪造未启动 seed/metrics/export/evaluation |
| Ablation ledger | `ablations/ledger.jsonl` | JSONL | 144 cells/`144 × n_pair` slots、closed gate projection、post-terminal reference binding、paired outcome、十 seed terminal 与 deviation 完整 |
| MPE2 information-structure ledger | `information_structure_ablations/mpe2/ledger.jsonl` | signed JSONL | `4 × n_pair` 个 native generation slots、对应 main-matrix full reference slots、result-free pair inputs、condition/run attestations、post-terminal bindings、condition-specific calibrations、十 seed outcomes 与独立 two-hypothesis analysis family完整 |
| Confirmatory report | `reports/confirmatory/` | JSON/CSV/Markdown | intention-to-run、RUN、N/A、attempt、outcome-mask counts 分列且可重算，统计 family、CI、effect size、exclusion 和 deviation 全部可审计 |
| Publishable report | `artifacts/public_reports/<experiment_id>/` | Markdown/JSON/CSV | 目标白名单只允许 `confirmatory_report.md`、`redacted_manifest.json`、`tables/primary_outcomes.csv`、`tables/secondary_outcomes.csv`、`tables/protocol_deviations.csv`；全部由 strict `PublicReportBundle` 聚合模型和固定模板生成。扫描器与 allowlist 未实现前该根完整 ignored，二者必须在同一 source-bearing tracer 原子启用 |

`redacted_manifest.json` 使用 `extra=forbid` 的 closed schema，只允许 schema/version、规范 experiment ID、public source commit、preregistration/version、public runtime/method IDs、固定 outcome/count/deviation summaries、上述四份 sibling report 的 relative path 与 SHA-256，以及 `sealed-v1:sha256:<64hex>` commitment。sealed principal 构造 `extra=forbid` 的 `SealedCommitmentPreimage`：`{"domain":"AutoMarkov-Sealed-v1","kind":<registered literal>,"nonce_b64url":<32 random bytes, unpadded base64url>,"sealed_envelope":<canonical JSON object>}`，其中 `kind` 只允许 `clarification_oracle`、`gold_score_calibration`、`gold_environment`、`gold_adapter` 或 `hidden_evaluation`；commitment 是该完整对象 RFC 8785 JCS bytes 的 SHA-256，并编码为上述 `sealed-v1` 字符串。nonce/preimage 只保留在 sealed principal，public schema 禁止 `identity`、`artifact_id`、`payload_hash`、`content_hash`、`answer`、`expected`、`nonce`、`path`、`uri`、`url`、`locator`、`credential`、`secret`、`trace` 及任何额外字段。public file hash 只能位于 typed `report_files` entry，sealed commitment 只能位于 typed `sealed_commitments` entry，二者不能互换。

完整模型响应、web capture、sealed traces、checkpoint、restricted dataset 和 external checkout 保留在 ignored/受限 artifact roots；publishable output 只包含上述有限文件。隔离 redactor 对 sealed identity/hash/nonce/locator/credential/answer 先做 typed provenance、tainted-wrapper、closed-schema 和 fixed-renderer 检查；raw/base64/hex substring matcher 只扫至少 16 UTF-8 bytes 且 Shannon entropy 至少 3.0 bits/byte 的确定性高熵 token。`0`、`1`、`yes` 等低熵 answer 不进入全局 substring matcher，由结构/provenance 层阻断；`SHA256(raw_answer)` 字典攻击由独立 hash-set 规则检查。扫描阈值、分类器版本与规则集进入 scanner-policy hash。通过后输出 strict `PublicReportBundle` 和不含敏感值的 signed `RedactionAttestation`。publisher 运行时不得挂载 sealed/oracle/evaluator roots 或 taint registry，只能读取该 bundle/attestation；它验证签名、source commitment、schema/allowlist、regular-file/no-symlink、secret/path patterns，再由固定模板/columns 渲染 Markdown/CSV。任一结构 taint、高熵 byte taint、低熵 raw-answer hash 命中、未知字段/列或 attestation mismatch 都 fail closed；含正常低熵 count/metric 的 fixed report 必须通过正例。当前 bootstrap 没有可执行发布扫描器，因此 `.gitignore` 有意不放行任何 `public_reports` 文件，不能靠手工 `git add -f` 绕过。

## Monitoring Configuration

- **Minimum coverage**: 每个 command 至少监控 process-alive、heartbeat、wall-clock timeout、event-stream growth、terminal event 与 output-manifest validation；`AUTO/v5` 必须识别 `CLARIFICATION_REQUIRED`，并核对 clarification result、terminal event/result、execution attestation、clarification request/verdict、non-transition audit events 与 public outcome 的完整 identity/hash chain。
- **Heartbeat**: 每个 active run 周期性追加带 monotonic timestamp、phase、completed/total work units 与 last-progress timestamp 的 `heartbeat` event。
- **Resource telemetry**: 记录 CPU、RSS、GPU utilization/memory、LLM queue latency、environment steps、tokens、Tavily calls 和 disk usage；telemetry 不能含 secret 或 sealed payload。
- **Metric file**: 训练阶段只监控 `training/<seed>/metrics.jsonl` 中预注册 metric keys；SwanLab 是 offline mirror，不是 canonical state source。
- **Timeout**: 每个 phase 的 soft advisory 与 hard timeout 写入 suite runtime manifest。只有 hard timeout 可以自动终止；soft stall 只发出 `RED_FLAG`。
- **Retry policy**: crash、stall 或 timeout 不自动 retry。保留 terminal event 和 partial artifacts，由用户决定是否以新 `run_id` 重跑；confirmatory analysis 不以重跑替换原失败。
- **Scope**: monitor 只读取命令声明的 output root 和 runtime telemetry；不扫描真实 `.env`、sealed assets、其他 run roots 或外部用户目录。
- **Anomaly classes**: no heartbeat、no output progress、metric non-finite、constant reward、action no-effect、resource exhaustion、sandbox violation、schema drift 与 hash mismatch。

## Analysis Plan

### Analysis hierarchy

1. `track` 将 `AUTO` confirmatory 主轨与 `HITL-ORACLE` secondary/mechanism 轨分开，任何估计都不跨轨 pooling。
2. 每条轨内，`suite_id` 是最高预注册层，共六层。
3. `variant_id` 嵌套于 suite，每 suite 五个。
4. `generation_pair_id` 嵌套于 variant；intention slot 横跨六个 methods，实际 pair 只横跨共同 eligible 的 `RUN` methods，`N/A` method 不生成 observation。
5. `rl_seed` 嵌套于有效 environment generation，并在 methods 可比较时保持配对。

task-card variant 与 generation 不是独立任务的替代品；置信区间和 standard error 必须保留上述 cluster structure，不能把所有 run 当作 i.i.d. observations。

六项消融使用独立 hierarchy：`track=AUTO`，24 个固定 `suite_id × variant_id(v1..v4)` strata，cell 内为 design-power gate 冻结的 `n_pair` 个 generation pairs，有效 candidate 内 10 个 RL seeds；analysis 只在验证双方独立 attestations 与 post-terminal `AblationReferenceBinding` 后，让 full AutoMarkov 与每项 ablation 共享 pair/seed bootstrap indices。它不与两轨主方法矩阵 pooling。

### Primary binary outcome

- 对 `E2EValid`，在预注册 eligible `suite_id × variant_id × track` 单元内分别以 `n_pair` 个 `generation_pair_id` 对 `automarkov` 与 baseline 执行 exact McNemar：`AUTO` 只含 `v1..v4` 的 24 cells，`HITL-ORACLE` 含 `v1..v5` 的 30 cells。`AUTO/v5` 的六个 cells 对 `SafeClarificationRequired` 使用独立 exact McNemar 与独立 Holm family。`react_executor` 必须覆盖这些冻结 masks；其他 comparator 依据 method manifest 的 `eligible_cells` 与 outcome mask 确定 family cardinality。运行后不得改变 family。cell-level 检验只作 diagnostic；主结论只使用 `AUTO/v1..v4` 的 finite-benchmark paired stratified bootstrap。
- 报告绝对 risk difference、relative difference、paired odds ratio、95% confidence interval 与 discordant pair counts。
- 主比较方法固定为 `react_executor`，不依据 Public Dev 或 Sealed Gold 结果重新选择；co-primary superiority gate 为差值的 finite-benchmark paired stratified-bootstrap 单侧 97.5% lower confidence bound 大于 0。
- 其余四个 AutoMarkov-versus-baseline comparisons 构成 Holm-adjusted secondary family，family-wise alpha 为 `0.05`；`react_executor` co-primary comparison 不在该 secondary family 中重复计数。

### Continuous and policy outcomes

- 对每项预注册连续 secondary outcome，在其 outcome mask 覆盖的 `suite_id × variant_id × track` cell 内报告 `n_pair` 个 generation-pair method differences、cell mean/median 与 paired bootstrap interval，仅作 descriptive diagnostic；Holm 的每个 `outcome × comparator × track` hypothesis 使用下面定义的单一 aggregate null-centered bootstrap raw test，禁止合并 cell diagnostics。两个 co-primary decision gates 仍只由 finite-benchmark paired stratified-bootstrap CI 判定。
- confirmatory aggregate CI 固定 `track=AUTO` 和完整 `v1..v4` mask：六个 suite × 四个 variant 构成 24 个固定等权 strata，不重采样、不遗漏；只在每个固定 cell 内以共同索引配对重采样 `n_pair` 个 generation pairs，policy metric 再在有效 candidate 内以共同索引配对重采样 RL seeds。整个 method vector 共享同一组 bootstrap indices，invalid candidate 的 gate-aware zero 不生成虚假 seed records。只有明确标记 exploratory 的 generalization analysis 才可重采样 suite/variant；track 始终独立且禁止 pooling。
- gate-aware policy score 是第二个 co-primary outcome：`E2EValid=0` 或 `GoldPolicyEvaluationValid=0` 的 candidate 取 0；其余 candidate 的策略虽在 candidate environment 上训练，但十个固定 RL seeds 的 co-primary return 必须在同一冻结 gold environment 上由 sealed reference evaluator 计算；candidate、random 与 reference policy 禁止跨环境归一化。jointly-valid pairs 上的 conditional policy score 另作解释性 secondary analysis，并报告有效环境比例与 conditional sample size。
- 每个 suite 使用同一冻结 gold environment、reference evaluator、reference policy 与 random policy 计算 normalized policy score。普通 RLlib checkpoint 只能由 trainer-local 一次性 export execution 在同一 frozen trainer profile/namespace 内，通过 successful training terminal record + checkpoint-tree commitment 绑定的私有 inherited read-only descriptor/opaque handle 读取；exporter 重验预注册 architecture/connector identity与整树 commitment 后，将其转为 strict JCS manifest 绑定的 finite weights-only safetensors。checkpoint locator/path/descriptor/handle 值不进入 job manifest、exact payload command、terminal record、attestation 或任何 public/cross-profile seam；checkpoint、descriptor/handle 与任何 pickle/cloudpickle 永不跨 profile，且不为 export 新建 profile。独立 verifier 和 sealed evaluator 都只接收已验证 manifest/tensor/commitment/terminal record，sealed evaluator 只实例化预注册可信 RLModule/connector code并加载该 tensor map，禁止反序列化普通 checkpoint、pickle/cloudpickle 或 candidate code/object。checkpoint/export/adapter 任一门禁失败仅令 `GoldPolicyEvaluationValid=0`，不追溯改写四门 `E2EValid`；candidate-environment return 只作诊断。metric direction 与 evaluation episode count 在首个 confirmatory run 前冻结；non-inferiority margin 已固定为标准化绝对差 `0.05`，即 reference–random return gap 的 5%，并使用单侧 97.5% lower confidence bound 判定。
- Taxi、MiniGrid、MPE2 与 MetaDrive 每 seed 评价 100 episodes；SMACv2 每 seed 评价 50 battles；CityLearn 评价完整 held-out period。
- 只在两轨 outcome 定义一致的 `v1..v4` 上，分别对 `Y ∈ {E2EValid, Q_gate}` 计算同一 `(suite_id, variant_id, generation_pair_id)` 上的 paired difference-in-differences：`D_Y = (Y_AutoMarkov,HITL - Y_AutoMarkov,AUTO) - (Y_ReAct,HITL - Y_ReAct,AUTO)`。两项 interaction 各在固定 24 cells 内配对重采样 generation pair/RL seed，报告两侧 95% stratified-bootstrap CI，并作为一个二假设 secondary family 使用 Holm correction；它们不改变两个 co-primary gates。`v5` 只分别报告 `AUTO` 的 `SafeClarificationRequired` 与 `HITL-ORACLE` 的 E2E/policy outcomes，不跨不同 outcome 做 difference-in-differences。
- 对六项 ablation 与 full AutoMarkov 的差，在相同固定 24 cells 内执行 paired stratified bootstrap；binary `E2EValid` 另在每个 cell 使用 exact McNemar，连续 $Q^{\mathrm{gate}}$ 只报告 cell-level paired descriptive bootstrap。两个 outcome 分别形成六假设 Holm family，报告 aggregate raw/adjusted p-value/CI；它们是 secondary，不改变 co-primary gates。

所有 secondary Holm families 完全采用主规格 11.10.5 的 aggregate algorithm：每个 pair 先形成 method contrast，observed statistic 为各固定 `suite_id × variant_id` stratum 内 pair mean 的等权 mean $\widehat\Delta_h$；one-sided superiority 使用有方向 statistic，track interaction 使用 two-sided absolute statistic。raw p-value 使用严格 100,000 次 fixed-strata paired null-centered bootstrap：从每个 contrast 减去该 hypothesis 的 $\widehat\Delta_h$，在每个固定 stratum 有放回抽 `n_pair` 个 pair，并在有效 policy pair 内以共同索引抽十个 RL seeds；完整 method/hypothesis vector 共用 indices。one-sided/two-sided add-one p-value 与 centering rule完全采用主规格，不要求 pair differences 对称或 method labels 可随机交换。每个 family 在运行前冻结 hypothesis IDs、direction、cardinality、`alpha=0.05`、32-byte base64url null-bootstrap seed 和独立 effect-bootstrap seed；Holm adjusted p-value 按 step-down 公式计算，ties 按 hypothesis ID bytes 排序。effect/raw CI 使用 100,000 次 fixed-stratum paired bootstrap；multiplicity-adjusted bound 固定用 Bonferroni familywise quantile（one-sided `alpha/m`，two-sided 每尾 `alpha/(2m)`），不得把它误称为 Holm CI。所有 quantile 使用主规格唯一的 $Q_p=x_{(\min(B,\max(1,\lceil pB\rceil)))}$ inverse empirical-CDF，禁止 package interpolation default。报告 aggregate raw/Holm-adjusted p、raw CI、Bonferroni-adjusted bound 与 cell diagnostics。

抽样流不得依赖 Python、NumPy 或平台 PRNG：完全复用主规格的 `automarkov.analysis-counter.v1`，以 RFC 8785 JCS closed counter object 的 SHA-256 digest 派生无 modulo bias 的 bootstrap index或 deterministic open-unit rational。counter 只含 stream manifest、domain/seed、replicate、scope/draw/selected-unit/rejection，不含 hypothesis/method/candidate entropy；因此同一 family 内全部 hypotheses和完整 method vector共用 indices，design-power candidates 共用 dataset/pair prefix。strata 按 `(suite_id, variant_id, track)` UTF-8 bytes、pair 按 `generation_pair_id`、RL seed 按数值升序；每 replicate/stratum 固定 `n_pair` 个 pair draws，每个抽中 pair occurrence固定 10 个共同 seed draws。analysis/calibration/design-power manifests冻结算法/schema/hash/JCS 实现与版本、canonical order/scope grammar、centering/transform rules、draw counts 和彼此独立的 null/effect/calibration/design DGP/design bootstrap seeds，并以主规格五条 counter vectors、quantile vector及 calibration fixture验证 bytes/digest/index/LCB。

### MPE2 information-structure ablation

`mpe2_full_state_mg` 是主 suite：actor 接收官方 `state()` 的 54D global state，并明确标为 fully observable MG adaptation。`mpe2_native_local_posg` 是独立于六项 component ablations 的预注册 information-structure ablation：actor 只接收 native 18D local observation，global state 只允许 centralized critic 使用。二者共享环境 commit、物理参数、reward、episode horizon、generation pair、RL seeds，以及同一 signed `Mpe2InformationPolicyConfig` 冻结的 recurrent RLModule/centralized critic、parameter shapes、initialization、optimizer、ConnectorV2 graph、normalization、超参数和预算；同形状 actor-input adapters 中只有读取 global state 或 local observation 的 capability 不同。feed-forward/independent algorithms 只作分列诊断，不进入信息结构 estimand；不得将主轨称为 native MPE2。

full reference 直接复用主矩阵中 `track=AUTO, method_id=automarkov, suite_id=mpe2_full_state_mg, variant_id in {v1,v2,v3,v4}, generation_pair_id in {g00..g{n_pair-1}}` 的 `4 × n_pair` 个冻结 terminal slots，不重跑。native condition 预注册恰好相同的 `4 × n_pair` 个新增 generation slots，每个有效 candidate 使用相同十个 RL seed IDs。native job 只接收 result-free pair binding、自身 task card/condition manifest 与 gold commitments，绝不接收 full run identity、manifest、status、output、cache 或结果。

```bash
uv run automarkov experiment ablate-mpe2-information \
  --experiment-id <experiment_id> \
  --variant-id <v1_canonical|v2_paraphrased|v3_reordered_longform|v4_evidence_split> \
  --generation-pair-id <g00..g{n_pair-1}> \
  --pair-binding-id <public_result_free_pair_binding_id> \
  --manifest artifacts/experiments/<experiment_id>/manifests/public/mpe2_information_structure_ablation.json \
  --output-root artifacts/experiments/<experiment_id>/information_structure_ablations/mpe2
```

双方 terminal 后，只有无 generation capability 的 coordinator/analysis principal 才创建 `extra="forbid"` signed `Mpe2InformationStructureBinding`。closed fields精确采用主规格 9.4.2：schema/signing domain、binding/experiment、suite/variant/track/method/pair/pair-binding、full/native condition、两份 run-manifest/terminal-result/execution-attestation IDs/hashes、两份 condition-specific calibration commitments、精确等于 full-state commitment 的 `common_policy_scale_calibration_commitment`、issued-at、32-byte nonce、`signature_algorithm="Ed25519"`、analysis key ID/signature；所有引用都是 exact direct parents且没有 generation-visible payload。Ed25519 signature覆盖除 signature外完整 RFC 8785 JCS object，preflight冻结 analysis Ed25519 key validity/revocation/clock，binding ID/nonce/condition-pair tuple执行 replay/uniqueness检查。任一 algorithm/mismatch、duplicate、unknown/revoked key或字段替换拒绝；binding不回流到任一 generation job。canonical artifact key 为 `information_structure_ablations/mpe2/<condition_id>/<variant_id>/AUTO/automarkov/<pair_id>/<seed_or_generation>/`。

full/native 各自引用运行前冻结且通过相同 11.7 gate 的 condition-specific signed `GoldScoreCalibration`；这些 calibration 只验证各 condition 的 evaluator/reference 能力。跨 condition policy estimand 在同一 gold reward/environment、scenario/evaluation count 与 metric direction 上，统一使用 full-state calibration 的 random/reference returns 和 denominator 计算 $Q^{info}_{gate}$；native calibration denominator 不参与 native outcome 重缩放。condition-specific normalized score 只作 condition 内诊断。

该 ledger 预注册两个 paired secondary hypotheses：native-minus-full 的 `E2EValid` 差与公共 full-state calibration 尺度上的 $Q^{info}_{gate}$ 差。分析固定四个 variant strata、每 stratum 使用相同的 `n_pair` 个共同 pairs；policy outcome 在有效 candidate 内继续使用十个共同 seed indices。两项均使用 two-sided 100,000-replicate paired stratified null-centered counter-stream test/CI并组成独立两假设 Holm family，完全复用主规格 `automarkov.analysis-counter.v1`、canonical ordering、共享 bootstrap indices 与 failure/missingness rules；它不进入 co-primary family或六项 component-ablation Holm families。

### Missingness, deviations and exclusions

- 在 E2E outcome mask 内，generation crash、hard timeout、missing required artifact、sandbox violation 或任一强 gate failure 计为 `E2EValid = 0`。`AUTO/v5` 依据 `SafeClarificationRequired` 合同判定，不属于 E2E missingness 或 failure。
- terminal run 的 exact approval 后续被合法撤销时，terminal state 不回写；受影响 payload/result 不再作直接质量解释，但原 intention slot、method/pair/seed denominator 和 signed deviation 必须保留，并固定映射 `E2EValid=0`、`GoldPolicyEvaluationValid=0`、`Q_gate=0`。不得删除 observation、改标 `N/A`、替换 seed/method 或创建 confirmatory retry。
- 仅当 method 对该 object 在 run 前被 manifest 标记为 `N/A` 时，才从该 suite 的 eligible baseline set 排除；运行后失败不能改标 `N/A`。
- runtime/profile mismatch、task-card mutation、sealed leakage、manual artifact repair 或 budget overrun 构成 protocol deviation，单独列表；sealed leakage family 从 confirmatory set 隔离，不静默删除。
- 所有 exclusions 在解盲 method aggregate 前由规则机械确定，并同时报告 intention-to-run matrix 与 compliant sensitivity analysis。

### Reporting gates

- 不在运行前承诺 statistically significant superiority。
- 每个结论同时报告 effect size、95% CI、原始 observation count、固定-stratum/cluster count、failure count 与 deviation count；仅属于冻结 secondary family 的结论报告 raw/adjusted p-value，ReAct co-primary gates 报告预注册 finite-benchmark paired stratified-bootstrap bounds 且不伪造 Holm-adjusted p-value。
- `UNVERIFIED` 只在成功执行并通过独立 reproducibility validation 后升级；统计分析但未重跑时最高为 `ANALYZED`。
- 论文忠实 replication 与本 common-backend controlled comparison 分开报告，`paper-spec reimplementation`、`controlled adaptation` 和 `official-code reproduction` 不混称。

## Pre-run Freeze Gate

confirmatory matrix 只有在以下条件全部满足后才能启动：

- signed/content-addressed `DesignPowerManifest/Report` 已通过独立重验：nuisance inputs 只来自 non-overlapping Public Dev/可引用先验/保守边界，2,000 个 deterministic datasets 与每 gate 不少于 10,000 次 production counter bootstrap 可复现，候选 prefix 共用随机流，exact binomial intervals 与 MDE/precision 齐全；最小合格 `n_pair` 同时满足 joint-power lower `≥0.80`、marginal-power lower `≥0.90`、null false-success upper `≤0.025` 并绑定全部 manifests，否则保持 `BLOCKED_DESIGN`；
- 三十份 task cards 与各自 allowed/blocked source manifests 已冻结并通过独立语义审查；
- 六个 method contracts、每个 comparator/track/outcome 的 `eligible_cells`、outcome mask 与 family cardinality、模型与 sampling parameters、两条 track、工具权限和预算已冻结；六个 ablation closed execution plans/gate masks/predicate projections、144 个 `AUTO/v1..v4` `RUN` cells、result-free pair rule、post-terminal binding schema/analysis key、预算、pair/seed masks 与两个六假设 Holm families 已冻结；MPE2 information-structure manifest 的唯一 capability diff、`4 × n_pair` 个 native slots、`4 × n_pair` 个 main-reference slots、result-free pair contract、condition-specific calibration gates、公共 full-state policy scale、artifact key、post-terminal closed binding schema、analysis signature/key validity/revocation/replay 与独立两假设 Holm family 已冻结；`experiment_approval_policy` 的 source/hash/version、public input allowlist、acceptance predicates、Ed25519 key ID/public key、validity/revocation 与 replay contract 已冻结；`SignedRunReplacementPolicy` 的 cause allowlist、authority key、root=0/child=parent+1 ordinal、每 parent 单 child、pre-generation runtime slot reuse、post-generation/approval-revocation slot failure 与 atomic event schemas 已冻结；`react_executor` 执行两条 track全部 60 method cells，同时明确 `AUTO/v1..v4` co-primary mask、`AUTO/v5` clarification mask 和 `HITL-ORACLE/v1..v5` mechanism mask；
- `AUTO/v5` 的 clarification request/verdict/outcome/event schemas、sealed commitment/scoring-manifest binding、coordinator/evaluator keys、deadline、idempotent retry、invalid-to-zero 与 no-generation-feedback 合同已冻结；
- 六个 suite 的 source-access mode、required implementation route、official source commit、dataset revision、license、runtime profile、`PublicDevLearningProbe` algorithm/config/seed namespace/预算/diagnostic predicates、final training budget、evaluation count 与 success threshold 已冻结；mode/route 与 probe 由 suite 唯一决定且所有适用方法共享；
- 每个 suite 的独立 signed `GoldScoreCalibration` 已冻结并验证：pilot/episode paired units、canonical order、100,000-replicate counter bootstrap、seed/manifest hash、inverse empirical-CDF 及 conformance fixture 全部匹配主规格，方向调整后的 reference–random gap one-sided 97.5% LCB 严格大于预注册正数 `min_reference_random_gap`；失败 suite 在 candidate run 前阻断；
- 所选 `n_pair` 的 generation pair IDs/seeds 与十个 RL seeds 已登记；
- public validator 与 sealed evaluator 完成 hash/permission handshake，生成侧无法读取 sealed assets；
- `FixedCommitRunner` 的 job schema、runner identity/verification key、detached-checkout gate、default-deny phase network policy、Tavily-only retrieval egress、snapshot 后撤销、`ProcessExecutionTerminalRecord`、output scanner 与 attestation verifier 已冻结，并分别对非终态 job 和 Run-terminal job 完成签名 dry run；policy-export fixture 另证明同 profile/namespace 的 inherited descriptor/opaque handle 可由 training terminal record + checkpoint-tree commitment 成功解析且不新建 profile，并证明 job manifest、exact payload command、terminal record、attestation、mount/capability fields 或 portable output 一旦含 checkpoint locator/path/descriptor/handle 值即 fail closed；`RemoteEnv` 的唯一 codec version/schema/space-adapter registry hash、两类合法 topology、frame/tensor ceilings、golden frame vectors、全部 space branch（含 `Discrete.dtype` 与 MiniGrid `MissionSpace→FiniteText`）跨 profile byte-identical round-trip，以及 framing/tensor/float/principal-isolation 安全负例全部通过；
- CLI preflight 已真实通过，monitor 能识别正常、crash、stall、hard timeout、schema drift 与 hash mismatch；
- analysis code 在 synthetic fixture 上恢复预期配对、Holm family 和 hierarchical cluster counts；
- preregistration revision、Git commit 和 manifest hashes 写入 experiment root，之后的任何修改都产生新 experiment version。

在 freeze gate 前允许 pilot 与工程调试，但其结果不得进入 confirmatory statistics。
