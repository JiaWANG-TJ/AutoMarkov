







# AutoMarkov 深度代码审查、问题修复、实验运行与开源交付开发文档

**审查对象**：`JiaWANG-TJ/AutoMarkov`
**审查基线**：`main@3cea996309c6bc3bfe5b29dd83b82f5131ca4366`
**审查日期**：2026-08-25
**文档状态**：`ANALYZED / PROPOSED_IMPLEMENTATION_CONTRACT`
**适用范围**：代码、算法、Agentic RAG、运行时隔离、强化学习、统计分析、实验预注册、供应链安全、开源治理与交付
**不作出的声明**：本文不宣称未执行的实验已经完成，不把 Pydantic 模型存在等同于生产功能存在，也不把提交信息或已关闭 Issue 等同于验收通过。

---

## 0. 证据边界与审查口径

### 0.1 证据等级

本文将证据严格分为五类：


| 证据级别           | 含义                                         | 本文用法                                 |
| -------------------- | ---------------------------------------------- | ------------------------------------------ |
| `LIVE-CI`          | 当前 GitHub Actions 对当前提交的实际运行结果 | 可用于判定当前`main` 是否绿色            |
| `SOURCE-VERIFIED`  | 直接读取当前提交中的源码、测试、配置和文档   | 可用于判定接口、校验逻辑与缺失实现       |
| `TRACKER-VERIFIED` | 直接读取 Issue/PR/Release 元数据             | 可用于审查流程状态与验收一致性           |
| `REPO-RECORDED`    | 仓库维护者在文档中记录的本地运行结果         | 只能作为先前基线证据，不视为本次独立复现 |
| `PROPOSED`         | 本文提出的修复、架构、实验或交付方案         | 必须通过后续实现与测试才能升级状态       |

### 0.2 独立执行限制

本次审查通过 GitHub API/连接器读取了当前仓库、源码、测试、Issues、Actions 与外部官方规范。工作容器尝试执行 clean clone 时无法解析 `github.com`，因此本文**不声称已在本地重新运行** `uv sync`、Ruff、Pyright、pytest、RLlib 或 suite runtime。当前 `main` 的 GitHub Actions 失败属于独立的 `LIVE-CI` 证据；仓库内恢复文档记录的 Ruff/Pyright/pytest 数字属于 `REPO-RECORDED` 证据。

### 0.3 最重要的状态区分

后续所有开发、Issue、README 和发布说明只允许使用以下状态：

- `DOCUMENTED`：只有规格或文档。
- `CONTRACT_IMPLEMENTED`：数据模型、协议或验证器存在。
- `EXECUTOR_IMPLEMENTED`：生产执行器存在，且公共入口可到达。
- `STATIC_VERIFIED`：lint、type、unit/contract tests 通过。
- `RUNTIME_VERIFIED`：真实 profile、进程、依赖和协议完成 fresh smoke。
- `EXPERIMENT_READY`：预注册、功效、sealed evaluator、完整意向矩阵全部冻结。
- `EXPERIMENT_COMPLETE`：所有预注册 slot 均有可验证终态。
- `RELEASE_READY`：clean build、安装、扫描、SBOM、attestation、文档与复现包全部通过。
- `BLOCKED`：缺外部资源、许可、凭据、预算或必要实现。
- `DEFERRED`：明确不属于当前里程碑，不得伪装成完成。

---

# 1. 执行摘要

## 1.1 总体结论

AutoMarkov 不是空仓库，也不是简单原型。它已经建设了较强的可信工件、严格 ingress、append-only 生命周期、SQLite/内存工件库、证据权限、Tavily key leasing、Local LLM identity/attestation、RemoteEnv codec、FixedCommitRunner 和 sealed evaluation 合同。其**可信底座和研究规格具有明显价值**。

但是，当前项目仍然不是可运行的“自然语言任务 → 证据检索 → MDP/POMDP/MG/POSG 形式化 → 环境绑定 → RL 训练 → 密封评测 → 统计分析 → 发布”的端到端系统。默认 `compile` 路径只完成 Run bootstrap；`Compiler.package`、默认 LLM/Evidence/Training/RemoteEnv 适配器仍有 deferred/scripted 分支；训练、统计、实验 CLI 和发布执行器没有完成生产闭环。当前 `main` 的最新 CI 也处于失败状态。

**准确定位**：

> 当前 AutoMarkov 是一个“合同和可信执行基础设施较强、产品主路径和实验执行层尚未贯通的 research-grade architecture prototype”，而不是 `EXPERIMENT_READY` 或 `RELEASE_READY` 系统。

## 1.2 启发式审查评分

下表仅用于确定工程优先级，不是科学评价指标：


| 维度                 | 评分 / 100 | 判断                                                                                           |
| ---------------------- | -----------: | ------------------------------------------------------------------------------------------------ |
| 研究问题与预注册设计 |         84 | 设计严谨，考虑 paired comparison、sealed evaluation、missingness、ablation 和 release boundary |
| 可信工件与安全合同   |         78 | 多处设计先进，但真实部署、外部信任根和端到端验证不足                                           |
| 形式化语义完备性     |         43 | 类型外壳较强，核心 kernel/reward/predicate 仍大量为自由字符串                                  |
| 产品可执行性         |         27 | 默认产品链未贯通，多个公共 seam 仍 deferred                                                    |
| RL 训练与策略评测    |         22 | 主要为 schema/Protocol，缺生产`TrainingRunner`                                                 |
| 统计分析可信度       |         18 | 预注册理念较强，但实现层可由调用方自报结论                                                     |
| 实验就绪度           |         12 | experiment CLI、真实 profiles、完整 suite、power/calibration 尚未闭合                          |
| 开源工程与发布       |         25 | 有许可证、锁文件和 SHA-pinned Action，但 CI、治理、release、attestation 不完整                 |
| 可维护性             |         36 | 巨型模块和状态/合同分散导致高变更放大                                                          |
| **总体交付成熟度**   |     **31** | 必须先完成 P0 恢复，不应启动 confirmatory matrix                                               |

## 1.3 当前必须停止的行为

在以下条件未满足前，不应：

1. 宣称 AutoMarkov 已完成端到端自动编译；
2. 宣称 T18–T27 已按原验收标准完成；
3. 运行正式六 suite confirmatory matrix；
4. 根据当前 `passed/released/rejected/non_inferior` 字段生成论文结论；
5. 发布 `0.1.0` wheel、Docker image 或实验结果为正式 release；
6. 将 `recipe_frozen` 或 `attached_unverified` profile 称为 runtime-ready；
7. 把当前单次 CartPole smoke 当作训练能力或实验复现证据；
8. 把密封评测“模型存在”当作 evaluator 已在隔离域运行。

## 1.4 首要阻断项


| 排名 | 阻断项                                | 为什么优先                           |
| -----: | --------------------------------------- | -------------------------------------- |
|    1 | 当前`main` CI 失败                    | 任何后续修改都缺少可信绿色基线       |
|    2 | Tracker 关闭状态与验收事实不一致      | 里程碑、进度和交付结论不可信         |
|    3 | 结果字段可由调用方自报                | 可产生统计、训练和发布伪阳性         |
|    4 | 默认 Compiler 主路径未贯通            | 项目核心价值主张当前不可执行         |
|    5 | DecisionProcess 核心语义仍是字符串    | “形式化”无法机械验证或可靠编译     |
|    6 | 真实 RemoteEnv/profile worker 未闭合  | 违反自身隔离 ADR，suite 不能可信运行 |
|    7 | 无生产 RLlib runner/export/evaluation | 无法形成真实策略结果                 |
|    8 | 统计模块没有计算实现                  | 预注册结论无法从原始观测机械重建     |
|    9 | experiment CLI 与工件链缺失           | 预注册计划仍是设计文档               |
|   10 | release/redaction/publisher 缺失      | 不具备安全公开结果和软件包的能力     |

---

# 2. 当前系统：目标链路与真实实现差距

## 2.1 目标产品链

```text
TaskRequest
  -> TaskContract
  -> Evidence Plan / Evidence Ledger
  -> MDP|POMDP|MG|POSG Classification
  -> DecisionProcessSpec
  -> Environment Route Selection
  -> EnvironmentBinding
  -> Public + Sealed Behavioral Gates
  -> RL Training
  -> Policy Export
  -> Sealed Policy Evaluation
  -> Paired Statistical Analysis
  -> Redacted Public Report / Release
```

## 2.2 当前链路状态


| 环节                  | 当前资产                                            | 当前状态                          | 关键缺口                                                              |
| ----------------------- | ----------------------------------------------------- | ----------------------------------- | ----------------------------------------------------------------------- |
| Task ingress          | strict/frozen`TaskRequest`、canonical JSON          | `CONTRACT_IMPLEMENTED`            | CLI 输入预算和模型能力硬编码，缺完整配置                              |
| Run bootstrap         | `InMemoryCompiler.start`、event/repository          | `EXECUTOR_IMPLEMENTED`（局部）    | 默认仅创建 Run，不继续编译                                            |
| Evidence              | Tavily gateway、lease、budget、snapshot、capability | `CONTRACT_IMPLEMENTED` + 局部执行 | 默认`ScriptedEvidenceGateway` deferred；没有 production orchestration |
| Local LLM             | identity、probe、connection proof、attestation      | `CONTRACT_IMPLEMENTED` + 局部执行 | profile 未完成 fresh runtime closure；默认 adapter deferred           |
| 分类                  | `ClassificationResult`、reduction/OOD contracts     | `CONTRACT_IMPLEMENTED`            | 分类主要是 LLM/调用方给出的标签与文本理由，缺确定性 proof             |
| 形式化                | MDP/POMDP/MG/POSG schema                            | `CONTRACT_IMPLEMENTED`            | kernel、predicate、functional、reward expression 多为自由字符串       |
| Suite binding         | suite contracts/adapters/readiness                  | `CONTRACT_IMPLEMENTED`            | 真实官方 package worker 缺失；readiness 恒为 waiting                  |
| RemoteEnv             | codec、frame、identity、transport contracts         | `CONTRACT_IMPLEMENTED`            | suite-facing adapter仍持有进程内 backend，不是字节级远程执行          |
| RLlib                 | plans、metrics、manifests、Protocol                 | `CONTRACT_IMPLEMENTED`            | 无生产`RllibTrainingRunner`；配置混杂旧/新 API                        |
| Policy export         | manifest/tree/outcome contracts                     | `CONTRACT_IMPLEMENTED`            | 路径、签名、TOCTOU 和实际 safetensors export 未闭合                   |
| E2E/sealed evaluation | 请求、verdict、principal topology                   | `CONTRACT_IMPLEMENTED`            | 无完整 experiment executor 将其串入真实 run                           |
| Statistics            | bootstrap/Holm/power result schemas                 | `CONTRACT_IMPLEMENTED`            | 没有可靠计算函数，关键布尔值可自报                                    |
| Release               | check/freeze/report models                          | `CONTRACT_IMPLEMENTED`            | 无 redactor、fixed renderer、publisher、attestation workflow          |
| CLI                   | `compile`、`verify-provenance`、`pilot run`         | `EXECUTOR_IMPLEMENTED`（有限）    | 预注册中列出的`experiment ...` 命令均未进入当前 CLI                   |
| CI                    | provenance workflow                                 | `LIVE-CI: FAILED`                 | metadata step 失败，import smoke 被跳过；无完整质量矩阵               |

---

# 3. 细粒度问题登记表

## 3.1 Blocker


| ID     | 问题                                                      | 直接证据/代码位置                                                                                      | 后果                                         | 最小修复                                                            | 验收条件                                       |
| -------- | ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | --------------------------------------------------------------------- | ------------------------------------------------ |
| AM-B01 | 当前主分支为红色                                          | Actions run`32839277223`：`metadata` 的 provenance step 失败，`import-smoke` skipped                   | 不能建立可信基线或发布                       | 修复当前 provenance 输入/注册，不通过跳过校验                       | 当前 HEAD 的所有 required checks 绿色          |
| AM-B02 | Issue 完成状态失真                                        | T18、T25、T27 已关闭但验收 checkbox 未完成；要求的命令/目录不存在                                      | 假完成率、错误里程碑                         | 重开或标记`closed-not-accepted`，每项绑定 CI/artifact               | Issue 仅在 acceptance bot 验证成功后关闭       |
| AM-B03 | 默认 Compiler 不是端到端编译器                            | `api.compile_task -> InMemoryCompiler.start`；`Compiler.package` deferred                              | 核心产品声明不可执行                         | 建立 application orchestrator 和 durable state machine              | 单命令完成最小任务到可运行 env 与报告          |
| AM-B04 | 默认 LLM/Evidence/Training/RemoteEnv 仍 scripted/deferred | `adapters.py` 多个 `_deferred(...)`                                                                    | 产品 seam 只有占位实现                       | 给每个 seam 注册 production adapter；默认配置 fail closed           | production profile 中无 scripted adapter       |
| AM-B05 | 形式化 IR 的核心语义不形式化                              | `transition_kernel: str`、`initial_distribution: str`、`predicate: str`、`functional: str` 等          | 无法类型检查、归一化、执行或证明环境一致性   | 引入 typed AST/kernel/distribution/predicate DSL                    | 可解释、可类型检查、可执行、可差分验证         |
| AM-B06 | 分类结论不是机械派生                                      | `ClassificationResult.classification` + free-text rationale                                            | LLM 可错误贴标签但仍 schema-valid            | 从 agent count、observability、timing、state sufficiency 派生 proof | 同一 facts 唯一决定 kind；矛盾输入拒绝         |
| AM-B07 | 跨 profile 实际边界未落地                                 | suite adapters 持有 Python backend/lifecycle；ADR 要求仅 immutable artifacts 或 authenticated protocol | 隔离声明失真、依赖冲突和 sealed leakage 风险 | profile-local worker + RemoteEnv bytes-only client                  | 检测到跨 profile Python object 时测试失败      |
| AM-B08 | 无生产 RLlib runner                                       | `rllib_training.py` 明确主要为 type/contract；`TrainingRunner` 为 Protocol                             | 无真实训练、checkpoint、restore、evaluation  | 实现 pinned RLlib`RllibTrainingRunner`                              | build/train/save/restore/evaluate smoke 全通过 |
| AM-B09 | 训练/审计结论可自报                                       | `passed` 等布尔字段与 failure/assertion 可同时填写                                                     | 失败可被标记为通过                           | 结果类型只由 factory 从原始证据派生                                 | 所有矛盾构造均被拒绝                           |
| AM-B10 | 统计结论可自报且无算法实现                                | CI、p-value、Holm rank、rejected、non-inferior 由调用方填                                              | 可产生伪显著或伪非劣                         | 实现 deterministic bootstrap/Holm/power engine                      | 原始 observations 可重建全部结果字节           |
| AM-B11 | Benchmark 只校验计数，不校验完整笛卡尔积                  | 360 cell/24 strata 等仅计数和局部分布                                                                  | 重复 cell 可冒充覆盖，缺失 cell 不被发现     | exact key set、唯一性、canonical order                              | 重复/遗漏/替换任一 cell 均失败                 |
| AM-B12 | Experiment 计划是`UNVERIFIED` 文档，不是可运行系统        | 计划中的 preflight/generate/train/evaluate/analyze CLI 当前不存在                                      | 不能运行预注册实验                           | 实现 experiment application 层和 artifact schemas                   | `--help`、schema fixtures、small matrix E2E    |
| AM-B13 | Release 状态可自报，缺执行器                              | `released/frozen/passed` 模型无完整派生逻辑                                                            | 可发布空 bundle 或未扫描结果                 | 实现 redactor、renderer、publisher、required gate registry          | 空 checks 不得 release；bundle 可独立验证      |
| AM-B14 | 当前没有 runtime-verified profile                         | Qwen 为 attached/unverified；其余多为 recipe_frozen                                                    | LLM、suite、trainer 实际不可证明可用         | profile build/import/model/source attestation                       | 每 profile 有 current signed smoke + digest    |
| AM-B15 | 工件注册未覆盖后期模块                                    | training/benchmark/statistics/release 多为游离对象                                                     | 无 DAG、CAS、replay、specified-head 保护     | 注册 schema、parent policy、artifact kind、projector                | 关键结果必须来自 ArtifactRepository lineage    |
| AM-B16 | 当前 provenance 设计会因普通开发文本变化反复变红          | 同仓`_REGISTERED_SOURCE_HASHES` 与 restricted declaration hashes；字节 token scan                      | 高维护成本且信任根可共同修改                 | 将测量清单和签名移到 CI/外部 attest，结构化限制来源                 | 修改普通文档不需手改源码 hash allowlist        |

## 3.2 High


| ID     | 问题                                                                                  | 影响                                                      | 修复方向                                                           |
| -------- | --------------------------------------------------------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------------- |
| AM-H01 | `provenance.py`、`repository.py`、`fixed_commit_runner.py`、`lifecycle.py` 等巨型模块 | 审查困难、变更放大、冲突和回归风险                        | 按 domain/application/adapter 拆分，保持 public seam 不变          |
| AM-H02 | CI 只有一个 provenance workflow 且只跑精选测试                                        | lint/type/full test/build/security 可在主分支缺失         | 建立分层 required checks                                           |
| AM-H03 | 未见经过 PR 独立审查的大规模开发证据                                                  | 核心安全逻辑缺 second pair of eyes                        | 强制 PR、CODEOWNERS、审批与状态检查                                |
| AM-H04 | suite-facing 环境不完全符合 Gymnasium/PettingZoo 标准类型                             | RLlib/官方检查可能无法使用，API 漂移                      | 真实继承`gymnasium.Env` / `ParallelEnv` 并跑官方测试               |
| AM-H05 | `formal_*_readiness()` 所有路径返回 waiting                                           | “实现存在”无法转为 runtime-ready                        | readiness 必须消费 build/import/behavior attestations              |
| AM-H06 | RLlib 配置混杂旧新 API 字段                                                           | 在 2.56 profile 中构建失败或语义漂移                      | 只使用 pinned 版本实际 introspection +`config.validate()`          |
| AM-H07 | 训练多主体判定逻辑可能循环依赖 CTDE 字段                                              | 非 CTDE multi-agent 可逃逸，single-agent 误判             | 从 suite/spec agent cardinality 派生                               |
| AM-H08 | 单主体 reset seed 被 manifest 固定为唯一值                                            | 多 seed 训练难以通过标准 Env 接口表达                     | profile seed namespace + episode seed grant                        |
| AM-H09 | 某些 metrics/timestamp/float 未统一 finite/canonical 约束                             | NaN/Inf/非 UTC/平台浮点差异污染统计                       | ingress 正规化与 finite validators                                 |
| AM-H10 | `policy_export` 路径合同不充分                                                        | `..`、absolute、backslash、symlink、device、TOCTOU 风险   | `PurePosixPath` + dirfd/openat + regular-file allowlist            |
| AM-H11 | 签名字段有时只检查字符串形状                                                          | 伪签名可进入模型                                          | trusted key resolver、freshness、revocation、replay store          |
| AM-H12 | no-evidence/full/ablation pairing 约束不够强                                          | 非唯一 capability diff、pair 污染                         | generic ablation manifest，exact one-diff verifier                 |
| AM-H13 | Agentic RAG 缺生产编排器                                                              | 有 Tavily/LLM 基础件但不能形成 evidence-grounded compiler | 建立 claim graph、contradiction、assumption、critic orchestration  |
| AM-H14 | 检索内容 prompt-injection 威胁未形成端到端测试矩阵                                    | Web 内容可能诱导工具、泄露或越权                          | untrusted-content parser + capability isolation + injection corpus |
| AM-H15 | suite/source/route 未由 registry 强绑定                                               | 不同方法可能使用不同实现边界，比较失真                    | signed`SuiteRegistry` 唯一解析 source-access/route                 |
| AM-H16 | 方法 manifest 不完整绑定 model/prompt/sampling/budget/tools                           | paired fairness 无法证明                                  | exact method contract 和 pair binding                              |
| AM-H17 | 预注册十个 RL seeds 尚未由真实 design-power 实现验证                                  | 可能欠功效或浪费计算                                      | 先实现功效模拟；必要时发布 prereg revision                         |
| AM-H18 | 统计模型只测合法构造 happy path                                                       | 算法和反例错误不易发现                                    | property、differential、golden、mutation tests                     |
| AM-H19 | 恢复文档 baseline 已落后当前 HEAD                                                     | 状态文档容易再次漂移                                      | machine-generated`status.json`，文档只链接                         |
| AM-H20 | 包元数据不完整                                                                        | PyPI/用户无法判断文档、Issue、成熟度、extras              | 补 URLs、classifiers、keywords、maintainers、extras、license-files |
| AM-H21 | 无 release/tag 和可验证分发工件                                                       | 用户无法安装/验证稳定版本                                 | release candidate + wheel/sdist + SBOM + attestation               |
| AM-H22 | 缺 SECURITY/CONTRIBUTING/CODE_OF_CONDUCT/CITATION/CHANGELOG/CODEOWNERS                | 开源贡献、安全披露和引用不规范                            | 补齐治理文件与模板                                                 |
| AM-H23 | 内部 provenance 不能替代外部构建证明                                                  | 同仓代码可同时修改目标和预期 hash                         | GitHub artifact attestation/SLSA provenance + downstream verify    |
| AM-H24 | sealed evaluator 有强合同但缺真实部署证据                                             | 隔离、无反馈和无共享挂载只停留在模型层                    | 独立 worker/principal、negative mount/capability tests             |

## 3.3 Medium


| ID     | 问题                                                   | 修复                                                         |
| -------- | -------------------------------------------------------- | -------------------------------------------------------------- |
| AM-M01 | 多数后期测试仅构造一个合法对象                         | 每个 invariant 至少增加一组欺骗性反例                        |
| AM-M02 | provenance/schema baseline 可能在每测试重复重建        | session-scoped immutable fixture + cache key                 |
| AM-M03 | SQLite 缺系统性 crash/restart/fault-injection 验证     | WAL、fsync、busy、power-loss、migration tests                |
| AM-M04 | 自定义 space 与 NumPy/Gym dtype 边界不明确             | codec 层统一 dtype/endian/layout/finite                      |
| AM-M05 | exact`float` 等类型约束可能拒绝合法 NumPy scalar       | 在 wire boundary 转为 canonical scalar，领域层保持 strict    |
| AM-M06 | README、handoff、Issue、实验计划状态源分裂             | 单一机器生成状态，文档不手写“已完成”                       |
| AM-M07 | method 名称通过字符串拼接规避扫描                      | 移除 lexical policy 驱动的代码规避，使用结构化 source policy |
| AM-M08 | 没有性能基准门禁                                       | canonical/codec/repository/projection microbench 与阈值      |
| AM-M09 | 未形成兼容性矩阵                                       | Python、OS、profile、suite、RLlib、CUDA 的明确支持表         |
| AM-M10 | 没有 migration/version deprecation 策略                | schema registry、reader compatibility、migration ADR         |
| AM-M11 | 日志/telemetry 与 canonical artifacts 的主从关系需强化 | telemetry 只做诊断，不能决定终态                             |
| AM-M12 | 研究复现与 common-backend controlled comparison 易混称 | 单独 provenance/label/report sections                        |

---

# 4. 核心问题深度分析与解决方案

## 4.1 当前 Compiler 只完成 bootstrap，不完成“自动编译”

### 问题

`compile_task()` 调用 `InMemoryCompiler().start()`，其主要产物是任务工件、策略/运行清单和 `RunCreated`。`package()` 仍 deferred；默认 LLM、Evidence、RemoteEnv 和 Training 适配器也没有生产闭环。因此，CLI 输出 `RunId` 只能证明一次运行尝试被建立，不能证明 TaskContract、DecisionProcessSpec 或 EnvironmentBinding 已产生。

### 正确实现

新增 application 层用例：

```text
CompileDecisionProcessUseCase.execute(request, compile_config)
  1. validate ingress
  2. create immutable run manifest
  3. plan evidence
  4. retrieve/freeze evidence snapshots
  5. build claim-evidence graph
  6. identify ambiguities/assumptions
  7. classify process kind
  8. propose typed formal spec
  9. deterministic formal validation
  10. text/formal critic
  11. approval or clarification gate
  12. select route
  13. build environment candidate
  14. public tests
  15. package candidate
  16. terminal CAS
```

每一步必须：

- 接收显式 artifact references；
- 输出 typed artifact；
- 通过 append-only event 提交；
- 不修改历史 payload；
- 失败时产生 closed reason code；
- 支持从 verified event head 幂等恢复；
- 不把模型 chain-of-thought 写入工件，只保存结构化决定、证据引用、验证错误和必要摘要。

### 代码变更

- 新增 `src/automarkov/application/compile_use_case.py`
- 新增 `src/automarkov/application/run_coordinator.py`
- 将 `api.compile_task` 改为依赖注入 production application service
- `InMemoryCompiler` 仅保留为 test adapter
- CLI 必须显式选择 config/profile，不得隐式使用 scripted adapter

### 验收

1. 用一个有限、无外部依赖的 GridWorld/CartPole engineering task；
2. 从 raw JSON 开始；
3. 产生 TaskContract、ClassificationProof、DecisionProcessSpec、EnvironmentBinding、public test report、terminal result；
4. clean process 重启后可从 event head 重放；
5. 每个工件 hash/parent/schema 可验证；
6. 任一 deferred adapter 被 production config 引用时 preflight 失败。

---

## 4.2 DecisionProcessSpec 是“强类型外壳 + 字符串语义”，尚不是真正可执行形式化

### 问题

当前 schema 对 MDP/POMDP/MG/POSG 的 agent keyset、owner、信息结构和部分风险字段做了较强校验，这是优点。但以下字段仍是自由文本：

- `transition_kernel`
- `initial_distribution`
- `DeterministicRewardSpec.expression`
- `StochasticRewardSpec.distribution_family`
- `expectation_expression`
- `ObjectiveSpec.functional`
- `ConstraintSpec.predicate`
- `RiskSpec.outcome_expression`
- `termination_predicates`
- `truncation_predicates`
- `observation_kernel`
- joint kernel / active actor function / cycle boundary

非空字符串无法保证：

- 符号已声明；
- 类型匹配；
- 概率归一化；
- support 与参数一致；
- next-state 维度完整；
- observation 不泄露 state；
- reward 和 objective 一致；
- termination/truncation 互斥是语义互斥而非字符串不同；
- 环境实现与 formal spec 等价。

### 目标 DSL

建议用封闭 AST，不执行任意 Python：

```text
Expr =
  Constant
  VariableRef
  UnaryOp
  BinaryOp
  Compare
  BooleanOp
  IfThenElse
  Lookup
  Aggregate
  Clip
  Indicator

Distribution =
  Deterministic
  Categorical
  Bernoulli
  Normal
  TruncatedNormal
  Empirical
  ExternalDistributionRef

Kernel =
  DeterministicAssignmentKernel
  FactorizedStochasticKernel
  JointStochasticKernel
  ExternalKernelRef

Predicate =
  ComparisonPredicate
  LogicalPredicate
  QuantifiedFinitePredicate
```

### 约束

- `ExternalKernelRef` 只允许 `REUSE/COMPOSE` 路线；
- 必须绑定 upstream source commit、adapter version、profile digest、input/output spaces 和 behavioral test suite；
- 不能把 `ExternalKernelRef` 宣称为 symbolic closure；
- 所有 AST 需要 schema version、canonical encoding 和 complexity ceiling；
- 禁止任意函数名、import、eval、pickle 或动态代码。

### 验证器

必须实现：

- symbol table；
- expression type checker；
- shape/unit checker；
- probability/support validator；
- finite/NaN validator；
- state update completeness；
- observation projection/non-leakage；
- termination reachability基本检查；
- deterministic interpreter；
- reference compiler；
- official environment differential tests。

### 验收

- 合法有限 MDP 可枚举出完整转移矩阵；
- 每个 `(s,a)` 的概率和为 1；
- 未声明变量、错误 dtype、shape mismatch、非有限数、空 support 均失败；
- Taxi/CartPole fixture 与 gold 环境进行固定轨迹差分；
- 外部 simulator 路线明确标记 `external_semantics`，不冒充完全符号化。

---

## 4.3 决策过程分类必须从事实派生，而不是让模型直接填写标签

### 问题

当前 `ClassificationResult` 可携带 `IN_SCOPE_MDP/POMDP/MG/POSG` 和文本 rationale，但 schema 并不证明标签由任务事实得到。

### 目标模型

新增 `ClassificationFacts`：

- `decision_maker_count`
- `has_strategic_other_agents`
- `simultaneous_or_sequential_actions`
- `state_sufficient_for_markov_property`
- `each_agent_observes_full_state`
- `observation_histories`
- `communication_processes`
- `chance_process`
- `continuous_time`
- `nonstationarity`
- `centralized_training_only_information`

新增纯函数：

```text
derive_decision_process_kind(facts) -> ClassificationProof
```

规则至少包括：

- 单决策主体 + 全状态 Markov → MDP；
- 单决策主体 + 部分观测 → POMDP；
- 多战略主体 + 每主体全状态 → MG；
- 多战略主体 + 局部观测/私有信息 → POSG；
- centralized critic 的 global state 不改变 actor 的 POSG 信息结构；
- 缺关键事实 → `CLARIFICATION_REQUIRED`；
- 连续时间、PDDL、OpenSpiel 等超出核心范围 → typed OOD handoff。

LLM 只能提出 facts 和证据；最终 kind 由 deterministic verifier 派生。

---

## 4.4 Agentic RAG：现有基础件很强，但缺“证据到形式化”的生产智能体图

### 4.4.1 推荐角色图

```text
Task Intake Agent
  -> Ambiguity Analyzer
  -> Retrieval Planner
  -> Evidence Gateway
  -> Snapshot Normalizer
  -> Claim Extractor
  -> Claim-Evidence Graph Builder
  -> Contradiction Resolver
  -> Assumption Registrar
  -> Classification Proposer
  -> Formal Spec Proposer
  -> Deterministic Formal Validator
  -> Text Critic
  -> Formal Critic
  -> Route Planner
  -> Environment Builder
  -> Simulation Tester
  -> Approval / Clarification Gate
```

这里的“Agent”必须是**受限角色执行器**，不是共享全部权限的自由聊天线程。

### 4.4.2 必须生成的工件


| 工件                          | 必需字段                                                       |
| ------------------------------- | ---------------------------------------------------------------- |
| `RetrievalPlan`               | unknowns、queries、source policy、budget、stop rule            |
| `RawEvidenceSnapshot`         | provider receipt、URL、retrieved-at、content hash、MIME、bytes |
| `EvidenceClaim`               | atomic claim、source span、scope、time validity、confidence    |
| `ClaimEvidenceEdge`           | entails / contradicts / contextualizes / uncertain             |
| `ContradictionSet`            | conflicting claims、authority comparison、resolution           |
| `AssumptionRecord`            | statement、impact、status、required approval                   |
| `ClassificationFacts`         | 可机械判定的事实                                               |
| `FormalizationTrace`          | 字段到 claim/assumption 的映射，不含 chain-of-thought          |
| `ValidationReport`            | machine errors、warnings、unresolved gaps                      |
| `ClarificationRequiredResult` | 问题、影响字段、禁止猜测理由                                   |

### 4.4.3 检索停止规则

检索不能只按“调用次数耗尽”停止。建议同时满足：

1. 所有 high-impact formal fields 有至少一个 authority-supported claim；
2. safety/termination/reward/observation 等关键字段无未解决冲突；
3. authority-weighted claim coverage 达阈值；
4. 新检索的 marginal evidence gain 低于阈值；
5. budget 未越界；
6. 无法满足时输出 clarification/blocked，而不是编造默认值。

### 4.4.4 Prompt injection 防线

- Web 文本永远标记为 `UNTRUSTED_EVIDENCE_CONTENT`；
- 检索结果不能发出工具调用、系统指令或 capability changes；
- parser 删除 script/style/隐藏节点并限制 MIME/大小；
- tool grants 不进入模型可见 prompt；
- secret refs 只在 transport 最终发送点解析；
- source 内容要求“忽略先前指令”“读取环境变量”“上传文件”等均作为 injection indicator；
- 建立 adversarial corpus：HTML、Markdown、PDF 文本、base64、Unicode 混淆、URL redirect、伪系统消息；
- 生成进程无 sealed evaluator、oracle、release key 和 unrestricted filesystem capability。

### 4.4.5 RAG 评价指标

- claim coverage；
- citation precision；
- authority-weighted recall；
- unsupported claim rate；
- contradiction detection recall；
- unresolved high-impact uncertainty；
- stale/future evidence rate；
- evidence-to-formal-field traceability；
- retrieval cost、calls、tokens、latency；
- prompt-injection containment rate；
- evidence leakage rate。

---

## 4.5 RuntimeProfile 与 RemoteEnv 必须真正跨进程/环境运行

### 问题

项目 ADR 已明确禁止跨 profile 共享 Python/Ray 对象，但当前 suite adapter 仍可持有进程内 lifecycle/backend；这使 `RemoteGymnasiumEnv` 的名称与真实拓扑不一致。

### 目标拓扑

```text
RLlib trainer profile
  |
  | canonical RemoteEnv frames over authenticated Unix/TCP transport
  v
suite-specific environment worker profile
  |
  +-- official package at pinned version/commit
  +-- profile-local spaces/adapters
  +-- no trainer Python object
  +-- no sealed assets
```

### 必须验证

- protocol version；
- profile ID/digest；
- worker process identity；
- source package/version/commit；
- spaces registry hash；
- frame size/tensor count/shape overflow；
- dtype、endianness、layout；
- NaN/negative-zero/infinity policy；
- reset/step episode sequence；
- seed namespace；
- timeout/heartbeat；
- no extra frames/trailing bytes；
- cross-principal rejection；
- reconnect/replay policy；
- worker crash terminalization。

### readiness 升级条件

`recipe_frozen` → `built` → `import_verified` → `behavior_verified` → `runtime_verified`。

任何一步都必须由独立 attestation artifact 产生，不能手工改 `status`。

---

## 4.6 Suite 实现必须符合上游标准

### 单主体

真实环境类应继承 `gymnasium.Env`，提供官方 spaces，并通过：

- `gymnasium.utils.env_checker.check_env`
- deterministic reset/step seed tests
- observation/action `space.contains`
- terminated/truncated semantics
- render/no-render contract
- wrapper compatibility
- vectorization smoke（适用时）

### 多主体

根据 suite route 采用 PettingZoo `ParallelEnv` 或明确 AEC：

- `parallel_api_test`
- `parallel_seed_test`
- action mask tests
- `possible_agents` / `agents` 生命周期
- terminated/truncated/reward/info keysets
- dead-agent behavior
- max cycles
- centralized critic 与 actor observation 隔离

### Suite 顺序

1. **CartPole/小型 GridWorld**：仅用于工程 vertical slice，不进入论文主矩阵；
2. **Taxi-v4**：有限 MDP、可枚举差分；
3. **MiniGrid Memory**：POMDP 和 observation leakage；
4. **MPE2 simple_spread**：MG/POSG 与 CTDE；
5. **SMACv2**：复杂 POSG；
6. **MetaDrive/ScenarioNet**：重依赖、数据许可、安全指标；
7. **CityLearn**：数据时间边界、future leakage、能量守恒。

高级 suite 不得阻断核心最小产品的交付。

---

## 4.7 RLlib 训练模块必须从“配置模型”升级为“可运行执行器”

### 4.7.1 当前问题

- `TrainingRunner` 仅为 Protocol；
- CPU smoke 与 information audit 的 `passed` 可由调用方填写；
- multi-agent/CTDE 识别不应从自身 CTDE 字段反推；
- 配置包含新旧 RLlib API 混用风险；
- 无 build/train/save/restore/export/evaluate 真实闭环；
- 无算法版本和 runtime profile 的可验证绑定。

### 4.7.2 目标执行器

`RllibTrainingRunner` 必须：

1. 在 profile 内导入 pinned Ray/RLlib；
2. 从 `EnvironmentBinding` 解析 spaces 和 env creator；
3. 从 suite registry 派生 single/multi-agent；
4. 构建 `PPOConfig`；
5. 显式启用并验证 RLModule/Learner/EnvRunner/ConnectorV2；
6. `config.validate()`；
7. build Algorithm；
8. 执行至少一次真实 train iteration；
9. 记录完整 metrics 和资源；
10. 保存 checkpoint tree commitment；
11. 关闭并重建 Algorithm；
12. restore；
13. deterministic evaluation；
14. profile-local export finite weights-only safetensors；
15. 生成 signed terminal records。

精确配置字段必须从 pinned `ray==2.56.1` profile 现场 introspection 和官方文档确定，不能仅凭旧版记忆写映射。

### 4.7.3 结果派生

禁止：

```python
RllibCpuSmokeAttempt(passed=True, failures=[...])
```

改为：

```python
attempt = run_cpu_smoke(...)
result = derive_cpu_smoke_result(attempt)
```

结果类型应采用 private constructor 或 factory，确保：

- 有 failure → `passed=False`
- 有 leakage → `passed=False`
- non-finite metric → terminal failure
- missing restore/eval → incomplete
- export 失败不能伪造成 training success 的完整 policy outcome

### 4.7.4 CTDE

- actor 只读 per-agent observation；
- centralized critic 可读 global state；
- ConnectorV2 graph hash 进入 manifest；
- actor/critic tensor paths 分开；
- leakage test 构造只改变其他 agent 私有状态的反事实；
- MPE2 full-state adaptation 与 native local POSG 单独命名和报告。

---

## 4.8 统计模块必须只接受原始观测并机械产生结论

### 当前危险模式

当前结果模型可以表达：

- `p=0.9` 且 `rejected=true`；
- CI 下界不满足 margin 仍 `non_inferior=true`；
- Holm rank 与 p-value 排序不一致；
- 360 个重复 cell 仍通过总数校验；
- zero checks 仍 `released=true`。

### 目标函数

```text
validate_exact_cartesian_grid(...)
compute_stratified_paired_bootstrap(...)
compute_probability_of_improvement(...)
compute_iqm_and_interval(...)
compute_performance_profile(...)
compute_noninferiority_bound(...)
compute_holm_step_down(...)
simulate_design_power(...)
```

### 统计要求

- 预先冻结 estimand、方向、margin、family、eligible cells；
- paired unit 必须是同 suite/variant/track/pair；
- suite/variant 分层；
- RL seed 嵌套于 candidate；
- 失败/missingness 不得运行后改标 N/A；
- non-inferiority 使用预注册方向的一侧置信界；
- Holm 必须按原始 p-value 排序并执行 step-down；
- 同时报告 effect、CI、原始 N、cluster N、failure/deviation；
- RL 聚合同时给 IQM、median、mean、performance profile、probability of improvement；
- bootstrap counter algorithm、seed、replicate count、implementation hash 进入 artifact；
- 使用独立实现或 `rliable` 做交叉核验，但不可把第三方库输出作为不可审计黑箱。

### 功效

预注册中的十个 seeds 是待验证设计，不是天然正确。先用 Public Dev 或保守先验实现 design-power simulation。若不能满足冻结阈值：

- 不允许偷偷增加/减少 seeds；
- 发布 preregistration revision；
- 新旧 run family 分开；
- 旧 pilot 不进入 confirmatory statistics。

---

## 4.9 Provenance 必须从“同仓自校验”升级为“外部可验证构建证明”

### 当前问题

`provenance.py` 内含大量当前源码的预期 SHA-256 和 restricted-token 字节规则。此设计能捕获未同步修改，但存在两个根本问题：

1. 目标文件和预期 hash 可在同一 commit 中共同修改，不能构成独立信任根；
2. 对普通文本做字节 token 扫描，诱发字符串拼接规避和高维护成本。

### 保留内容

- exact source commit；
- package/profile digest；
- resolved dependencies；
- license/SBOM；
- immutable artifact lineage；
- default-deny publish tree；
- restricted source 不进入 native runtime。

### 替换方案

- CI 根据 Git tree 生成 source manifest；
- GitHub-hosted build 生成 wheel/sdist/SBOM；
- 使用 artifact attestation 绑定 repository、workflow、commit 和 subject digest；
- release workflow 下游执行 `gh attestation verify`；
- restricted-source policy 改为：
  - typed upstream manifest；
  - declared source-access mode；
  - import graph/AST；
  - vendored file tree；
  - lock/SBOM/license；
  - profile/mount/egress attestation；
  - 不扫描文档里是否出现方法名称。
- 同仓 hash 可以保留为完整性缓存或 regression fixture，但不能当最终信任根。

---

## 4.10 ReleasePipeline 必须成为真实隔离发布系统

### 目标流程

```text
internal analysis artifacts
  -> typed redaction input
  -> isolated redactor
  -> taint closure
  -> fixed renderer
  -> allowlisted output directory
  -> independent scanner
  -> signed redaction attestation
  -> publisher with no sealed mounts
  -> wheel/sdist/report attestations
```

### Public bundle 固定白名单

- `confirmatory_report.md`
- `redacted_manifest.json`
- `tables/primary_outcomes.csv`
- `tables/secondary_outcomes.csv`
- `tables/protocol_deviations.csv`

### 必须拒绝

- symlink/hardlink/device/FIFO；
- absolute/`..`/backslash/alternate stream；
- unknown file/column/schema；
- secret、credential、token、private path；
- sealed identity、nonce、answer、expected output、trace；
- checkpoint、pickle/cloudpickle、optimizer state；
- unverified signature；
- source commit/profile/report hash mismatch。

### 状态派生

`released` 不应是输入字段，而应由：

```text
all_required_checks_pass
AND bundle_schema_valid
AND redaction_attestation_valid
AND source_attestation_valid
AND independent_install_smoke_pass
AND owner_release_approval_present
```

机械派生。

---

# 5. 目标代码架构

## 5.1 建议目录

```text
src/automarkov/
  contracts/
    artifacts.py
    evidence.py
    runtime.py
    training.py
    evaluation.py
    experiments.py
  domain/
    ids.py
    errors.py
    task.py
    classification.py
    formal_ast.py
    decision_process.py
    lifecycle.py
  application/
    compile_use_case.py
    experiment_use_case.py
    release_use_case.py
    run_coordinator.py
  ports/
    repository.py
    llm.py
    evidence.py
    remote_env.py
    trainer.py
    evaluator.py
    publisher.py
  adapters/
    repository/
    evidence/
    llm/
    runtime/
    suites/
    training/
    evaluation/
    publishing/
  protocols/
    remote_env/
    oracle/
    sealed_evaluation/
  security/
    capabilities.py
    signing.py
    replay.py
    path_policy.py
    taint.py
  experiments/
    registry.py
    grid.py
    power.py
    statistics.py
    reporting.py
  cli/
    main.py
    compile.py
    experiment.py
    release.py
```

## 5.2 拆分原则

- `contracts` 只含 wire/domain models，不执行 I/O；
- `application` 编排事务和状态机；
- `ports` 只定义 Protocol；
- `adapters` 实现外部依赖；
- 结果模型不接受派生布尔值；
- 巨型模块逐步拆，不改变 public seam；
- 每个安全边界拥有单独 threat tests；
- 领域模型不能 import Ray、httpx、SQLite 或 Docker；
- profile worker 不能 import experiment coordinator；
- publisher 不能挂载 sealed/internal roots。

## 5.3 维护性门槛

作为渐进目标：

- 新模块建议不超过 800 LOC；
- 新函数建议不超过 60 LOC；
- critical derived functions 100% branch coverage；
- core line coverage ≥ 90%，branch ≥ 85%；
- 不以 coverage 代替 property/security tests；
- public schema 变更必须有 migration/deprecation 说明；
- 每个 PR 只处理一个 acceptance slice。

---

# 6. 按文件/模块的具体修改


| 文件/模块                                    | 保留                                           | 必须修改                                                                                     |
| ---------------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| `README.md`                                  | 核心研究目标和六个 seams                       | 加真实 status matrix、当前可运行命令、限制、quickstart、release badge                        |
| `pyproject.toml`                             | Hatchling、Python 范围、核心依赖               | URLs、classifiers、keywords、maintainers、license-files、extras、test/docs/security groups   |
| `cli.py`                                     | 命令入口                                       | 拆 subcommands；production config；实现 experiment/release；禁止 hardcoded capability budget |
| `api.py`                                     | public API 入口                                | 依赖 application service；不默认创建孤立`InMemoryCompiler`                                   |
| `adapters.py`                                | test adapters 和 ports glue                    | scripted 类移到 tests；production registry fail closed；实现 package/LLM/evidence/training   |
| `decision_process.py`                        | MDP/POMDP/MG/POSG 结构校验                     | 用 AST 替换核心字符串语义；加 interpreter/type checker                                       |
| `classification_contracts.py`                | reduction/OOD types                            | 加 facts/proof/derive function；运行时 readiness resolver                                    |
| `evidence_access.py`                         | capability grant 和 signed stores              | 持久化 replay/revocation；claim-level view；prompt-injection labels                          |
| `tavily_gateway.py`                          | key leasing、secret boundary、budget、snapshot | 与 retrieval planner/claim graph 串联；端到端 fault tests                                    |
| `local_llm_runtime.py`                       | connection proof、attestation、bounded ingress | actual profile builder/current connection evidence；production registry                      |
| `remote_env.py`                              | canonical codec 和 limits                      | profile-local worker harness；fuzz/property tests；统一 suite protocol                       |
| `suite_adapters.py`                          | source/profile signing思想                     | 删除进程内 backend跨界；实现真实 Gymnasium Env client                                        |
| `multi_agent_suite_adapters.py`              | multi-agent contracts                          | 实现 ParallelEnv/RemoteEnv；官方 PettingZoo tests                                            |
| `rllib_training.py`                          | manifest/metric schema                         | 实现 runner；新 API config；结果派生；restore/export/eval                                    |
| `statistics.py`                              | result schema命名                              | 改为 computation-owned results；实现算法和 golden fixtures                                   |
| `benchmark_suites.py`                        | suite IDs/设计概念                             | exact registry/cartesian；route/source binding；N/A evidence                                 |
| `generation_methods.py`                      | method概念                                     | exact model/prompt/budget/tool/evidence manifest 和 pair verifier                            |
| `ablation_ledger.py`                         | full/no-evidence/MPE2 ideas                    | 支持六项 ablation；exact one-diff；post-terminal binding                                     |
| `policy_export.py`                           | weights-only方向                               | POSIX path、tree commitment、签名验证、dirfd、TOCTOU、真实 exporter                          |
| `release_pipeline.py`                        | closed models                                  | redactor、renderer、publisher、derived gate、attestation                                     |
| `provenance.py`                              | package/profile/license validation             | 拆分；移除易碎同仓中央 source hash trust；外部 build attest                                  |
| `repository.py`                              | immutable repository                           | 拆 schema/storage/query；migration、crash、concurrency tests                                 |
| `lifecycle.py`                               | append-only reducer/CAS                        | 拆 events/commands/reducer/projection；state-machine model tests                             |
| `fixed_commit_runner.py`                     | fixed commit、terminal record、attestation     | 拆 preflight/sandbox/scanner/attestation；真实 OS isolation tests                            |
| `tests/`                                     | 丰富的 contract/security基础                   | 增 property/integration/e2e/official suite/real RLlib；减少重复 setup                        |
| `.github/workflows/`                         | SHA-pinned actions、minimal permissions        | 完整 CI/security/build/release matrix                                                        |
| `docs/agents/current-development-handoff.md` | handoff形式                                    | 改为 machine-generated snapshot，绑定 commit 和 generated-at                                 |
| `docs/experiments/...`                       | 预注册研究设计                                 | 保持`UNVERIFIED`，直到所有 preflight artifacts 真实存在                                      |

---

# 7. 分阶段 PR / 工作包计划

努力规模使用 `S/M/L/XL`，不是日历承诺。

## Stage A：恢复事实与绿色基线

### PR-A00：冻结错误声明和恢复 Tracker

**范围**

- 给 README 顶部加 `NOT EXPERIMENT READY / NOT RELEASE READY`；
- 重开 T18–T27，或创建 replacement acceptance issues；
- 增 `status/implementation_status.json`；
- Issue 模板强制填写验收命令、artifact、commit、reviewer。

**验收**

- status 文件由脚本生成；
- 已关闭 ticket 不再作为完成证据；
- 当前 blocker 全部可追踪。

**规模**：M

### PR-A01：修复当前 provenance 红线

**范围**

- 修复当前 source identity/registered publish tree；
- 不使用字符串拼接规避 policy；
- 将 lexical restricted checks 缩到结构化入口；
- 增 current failing regression tests。

**验收**

```bash
uv run --locked automarkov verify-provenance --repository-root .
```

exit 0，且在以下负例上 exit 非 0：

- restricted vendored code；
- unregistered executable profile；
- secret/private path；
- source commit mismatch；
- tampered lock/SBOM/license。

**规模**：L

### PR-A02：建立完整质量 CI

**required jobs**

- lock/profile verification
- Ruff
- Pyright
- unit
- contract
- integration
- security-negative
- full pytest
- build wheel/sdist
- clean install smoke
- docs links/schema
- dependency/OSV
- CodeQL
- secret scan/push protection
- license/SBOM
- artifact attestation（release only）

**验收**

- pull request 和 merge_group 都执行；
- 全部 Actions pinned full SHA；
- permissions per-job minimal；
- main 禁止红色合并。

**规模**：L

### PR-A03：消除 caller-supplied truth

**影响**

- `rllib_training.py`
- `statistics.py`
- `release_pipeline.py`
- `benchmark_suites.py`
- `generation_methods.py`
- `ablation_ledger.py`

**原则**

- raw attempt/observation 与 derived result 分离；
- result 只能经 factory/computation 创建；
- 关键布尔值 `computed_field` 或私有构造。

**验收**

所有矛盾 fixture 必须失败，包括：

- failure + passed；
- p=0.9 + rejected；
- CI 不满足 margin + non_inferior；
- zero checks + released；
- duplicate grid + complete；
- missing seed + evaluated。

**规模**：XL

### PR-A04：性能与测试反馈修复

- provenance/schema fixtures session-scoped；
- pytest markers/shards；
- 缓存只以 commit/policy hash 为 key；
- 修复全仓 suite timeout/重复 baseline；
- 输出 slowest tests。

**验收**

- full suite 结束并生成 JUnit；
- 无 setup error 级联掩盖根因；
- CI 失败定位到单一 job/test。

**规模**：M

---

## Stage B：建立真正的形式化编译垂直切片

### PR-B01：ClassificationFacts/Proof

- typed facts；
- deterministic derive；
- clarification/OOD；
- evidence linkage。

**验收**：四类正例、边界反例、centralized critic 不改变 actor observability。

**规模**：L

### PR-B02：Formal AST v1

- expression/predicate/distribution/kernel；
- type/shape/symbol checker；
- finite discrete interpreter；
- external kernel ref。

**验收**：Taxi/小型 GridWorld 可枚举，错误概率/符号/shape 被拒绝。

**规模**：XL

### PR-B03：Agentic RAG 编排器

- retrieval plan；
- claim graph；
- contradictions；
- assumptions；
- formal field traceability；
- critics；
- clarification。

**验收**：一个含冲突证据的 task 不猜测，产生 typed contradiction/clarification。

**规模**：XL

### PR-B04：Compiler application service

- durable orchestrator；
- state machine；
- idempotent recovery；
- production adapter registry；
- package implementation。

**验收**：raw task → formal spec → package terminal result。

**规模**：XL

---

## Stage C：真实环境与隔离执行

### PR-C01：RemoteEnv worker harness

- worker process；
- authenticated transport；
- canonical frames；
- crash/timeout/replay；
- build/import/behavior attestations。

**验收**：trainer 和 env 不共享 Python object；进程杀死产生 typed terminal failure。

**规模**：XL

### PR-C02：单主体 suite vertical slice

- CartPole engineering；
- Taxi-v4；
- MiniGrid Memory；
- official checks；
- behavioral differential tests。

**验收**：`check_env`、seed、gold trajectory、profile attestation。

**规模**：XL

### PR-C03：MPE2 ParallelEnv/CTDE slice

- local observation/full state distinction；
- PettingZoo tests；
- action masks；
- actor/critic information audit。

**验收**：`parallel_api_test`、`parallel_seed_test`、leakage counterfactual。

**规模**：XL

### PR-C04：高级 suite profiles

SMACv2、MetaDrive/ScenarioNet、CityLearn 分开 PR，分别处理：

- license/data；
- build；
- source revision；
- runtime smoke；
- gold calibration；
- resource budget。

**规模**：每项 XL

---

## Stage D：训练、评测与统计

### PR-D01：RLlib production runner

- pinned new API；
- single-agent PPO；
- build/train/save/restore/evaluate；
- terminal records。

**验收**：CPU smoke 在 clean profile 中运行，不使用 self-reported pass。

**规模**：XL

### PR-D02：RLModule/ConnectorV2/CTDE

- recurrent module；
- multi-agent mapping；
- centralized critic；
- observation connectors；
- leakage tests。

**规模**：XL

### PR-D03：Policy export

- same-profile read-only checkpoint capability；
- canonical tree；
- safetensors；
- independent verifier；
- no private locator serialization。

**规模**：XL

### PR-D04：Statistical engine

- exact grid；
- paired stratified bootstrap；
- IQM/performance profiles；
- NI bound；
- Holm；
- power simulation；
- independent cross-check。

**规模**：XL

---

## Stage E：实验系统和发布

### PR-E01：Experiment CLI 与 preflight

实现：

```text
automarkov experiment preflight
automarkov experiment generate
automarkov experiment e2e-gate
automarkov experiment train
automarkov experiment export-policy
automarkov experiment evaluate-policy
automarkov experiment analyze
```

这些命令在实现前只能出现在“目标命令”文档中，不能被 README 描述成可用。

**规模**：XL

### PR-E02：Ablation/Clarification/Replacement

- 六项 component ablation；
- MPE2 information structure；
- AUTO/v5 clarification；
- signed replacement policy；
- post-terminal binding。

**规模**：XL

### PR-E03：Release/redaction/publisher

- taint closure；
- fixed renderer；
- allowlist；
- independent scan；
- report/SBOM/build attestations。

**规模**：XL

### PR-E04：开源交付

- CONTRIBUTING
- SECURITY
- CODE_OF_CONDUCT
- CITATION.cff
- CHANGELOG
- CODEOWNERS
- GOVERNANCE/MAINTAINERS/SUPPORT
- docs site
- examples
- package metadata
- release candidate

**规模**：L

---

# 8. 实验运行方案

## 8.1 当前阶段：只允许恢复验证

当前可执行检查应先形成绿色基线：

```bash
uv sync --frozen --all-groups
uv run automarkov verify-provenance --repository-root .
uv run ruff check .
uv run pyright
uv run pytest -q
uv build
```

随后在 clean venv 中：

```bash
python -m venv /tmp/automarkov-install-smoke
/tmp/automarkov-install-smoke/bin/pip install dist/*.whl
/tmp/automarkov-install-smoke/bin/automarkov --help
```

当前仓库尚未提供完整 experiment CLI，因此不要把预注册文档中的目标命令复制到生产任务直接运行。

## 8.2 Engineering vertical slice

### 目的

只验证系统链路，不做论文结论。

### 最小矩阵


| 维度             | 配置                                                     |
| ------------------ | ---------------------------------------------------------- |
| suite            | CartPole engineering + Taxi 或 GridWorld                 |
| method           | deterministic fixture、single LLM、AutoMarkov            |
| variants         | canonical + paraphrased                                  |
| generation pairs | 2                                                        |
| RL seeds         | 2                                                        |
| train budget     | 极小 smoke                                               |
| evaluator        | public behavior fixture + isolated mock sealed handshake |
| publication      | false                                                    |

### 必须产物

- run manifest；
- evidence ledger；
- classification proof；
- formal spec；
- environment binding；
- public tests；
- training terminal records；
- policy export；
- evaluation result；
- raw observations；
- computed statistics；
- execution attestations。

## 8.3 Integration pilot

只有 G0–G5 通过后运行：

- Taxi、MiniGrid、MPE2；
- `single_llm`、`react_executor`、`automarkov`；
- v1–v4；
- generation pairs 和 seeds 由 pilot budget 冻结；
- 不进入 confirmatory；
- 目的：估计失败率、方差、资源、effect nuisance inputs；
- 识别 suite/runtime/calibration 缺口；
- 不能按结果改 task cards 或主 estimand。

## 8.4 Confirmatory freeze

必须同时冻结：

- current source commit；
- runtime/profile digests；
- model/tokenizer/weights identity；
- sampling；
- prompts/roles/tool capabilities；
- evidence budgets；
- 30 task cards；
- allowed/blocked source manifests；
- method eligibility；
- exact Cartesian grid；
- outcome masks；
- generation pair IDs；
- RL seeds；
- suite routes；
- gold calibration；
- design power；
- sealed evaluator keys/profiles；
- analysis code hash；
- missingness/deviation rules；
- release/redaction policy。

任何修改都创建新的 experiment version，不回写旧 manifest。

## 8.5 Confirmatory 执行

- 每个 run 使用 fixed commit；
- generation retrieval 仅允许 Tavily endpoint，snapshot 后撤销网络；
- candidate worker 无 sealed mount；
- public gate 通过后才触发 sealed E2E gate；
- E2E 任一 false：保留 slot，`E2EValid=0`，不训练；
- 训练启动后每 seed 必须有 success/failure terminal record；
- policy export 失败不能伪造 evaluation；
- evaluator 只读取显式 IDs，不扫描目录；
- crash/timeout 不自动替换原 slot；
- replacement 必须遵守预注册 signed policy；
- analysis 拒绝混合 plan hash/schema/source commit。

## 8.6 推荐报告指标

### 编译质量

- text contract F1；
- classification accuracy；
- formal closure；
- unsupported assumption rate；
- API compliance；
- hidden behavior pass；
- E2EValid；
- clarification safety。

### 策略

- normalized return；
- IQM + CI；
- median/mean + CI；
- performance profile；
- probability of improvement；
- success/win/safety；
- sample efficiency；
- AUC。

### 资源

- LLM calls/tokens；
- retrieval calls/credits；
- repair iterations；
- wall time；
- environment steps；
- CPU/GPU hours；
- peak RAM/VRAM；
- artifact storage。

### 安全/治理

- prompt-injection containment；
- source-policy violations；
- sealed leakage；
- capability denial；
- replay/substitution；
- protocol deviation；
- failed redaction；
- unverifiable provenance。

---

# 9. CI、分支保护与供应链交付

## 9.1 `main` ruleset

建议：

- require pull request；
- 至少 1 个独立批准，安全/运行时模块 2 个；
- require Code Owner；
- dismiss stale approvals；
- require approval of latest push；
- require conversation resolution；
- require all status checks；
- branch up-to-date 或 merge queue；
- require signed commits；
- linear history；
- forbid force push/delete；
- no general bypass；
- release tags 受保护。

## 9.2 CI workflow 拆分

```text
ci-metadata.yml
ci-static.yml
ci-unit-contract.yml
ci-integration.yml
ci-runtime-smoke.yml
ci-security.yml
ci-build.yml
ci-docs.yml
scheduled-osv.yml
scorecard.yml
release.yml
```

所有第三方 Actions 固定完整 commit SHA，权限按 job 最小化。

## 9.3 包与 extras

建议：

```toml
[project.optional-dependencies]
llm = [...]
training = [...]
env-minigrid = [...]
env-mpe2 = [...]
env-smacv2 = [...]
env-metadrive = [...]
env-citylearn = [...]
all-envs = [...]
docs = [...]
test = [...]
security = [...]
```

核心包不应强制安装所有重型 simulator、CUDA 或受限资产。

## 9.4 Release 工件

- source tarball；
- wheel；
- checksums；
- SBOM；
- license manifest；
- build provenance/attestation；
- changelog；
- migration notes；
- reproducibility manifest；
- public report bundle；
- signed redaction attestation；
- install/verify instructions。

## 9.5 发布验收

- clean checkout build；
- wheel 与 sdist 均可安装；
- installed package 不依赖 repo-relative hidden files；
- `automarkov --help` 可用；
- small deterministic example 可用；
- no secret/private/sealed/checkpoint；
- OSV/CodeQL/secret/license pass；
- artifact attestation 可由消费者验证；
- tag、commit、version、artifact digest 一致。

---

# 10. 测试策略

## 10.1 测试金字塔


| 层级                 | 内容                                                                    |
| ---------------------- | ------------------------------------------------------------------------- |
| Unit                 | AST、validators、hash、path、statistics                                 |
| Property             | canonical round-trip、RemoteEnv frames、grid uniqueness、state machines |
| Contract             | public seams、schema registry、upstream APIs                            |
| Integration          | SQLite、multiprocess、real transport、real profiles                     |
| Official conformance | Gymnasium/PettingZoo/RLlib                                              |
| E2E                  | compile → env → train → evaluate → analyze                          |
| Security-negative    | prompt injection、path traversal、replay、substitution、leakage         |
| Fault injection      | crash、timeout、disk full、DB lock、partial write、worker kill          |
| Performance          | canonical/codec/repository/projector/training overhead                  |
| Reproducibility      | clean checkout、second machine/profile、artifact re-verification        |

## 10.2 Property tests

建议引入 Hypothesis，覆盖：

- JSON duplicate keys、node/byte ceilings；
- random AST type/shape；
- probability simplex；
- Unicode/normalization；
- path traversal；
- frame offsets/lengths；
- tensor shape multiplication overflow；
- exact Cartesian permutations；
- duplicate cell；
- event reducer state transitions；
- signature/nonce/replay；
- result derivation consistency。

## 10.3 Mutation tests

对以下关键逻辑运行 mutation test：

- gate conjunction；
- Holm step-down；
- NI bound；
- E2EValid；
- release readiness；
- path rejection；
- principal boundary；
- terminal CAS。

如果删除一个关键条件仍全部测试通过，则测试不足。

---

# 11. 开源治理文件

必须新增：


| 文件                 | 内容                                                  |
| ---------------------- | ------------------------------------------------------- |
| `CONTRIBUTING.md`    | 环境、分支、测试、PR、schema/ADR 规则                 |
| `SECURITY.md`        | 支持版本、私密披露、响应、密钥泄露处理                |
| `CODE_OF_CONDUCT.md` | Contributor Covenant 等                               |
| `CITATION.cff`       | 软件版本、作者、论文/仓库引用                         |
| `CHANGELOG.md`       | Keep a Changelog + SemVer                             |
| `CODEOWNERS`         | security/runtime/formal/training/statistics ownership |
| `GOVERNANCE.md`      | 决策、maintainer、release authority                   |
| `MAINTAINERS.md`     | 角色与职责                                            |
| `SUPPORT.md`         | 使用问题与安全问题边界                                |
| PR template          | 风险、测试、artifact、schema、threat model            |
| Issue templates      | bug、feature、experiment deviation、security          |
| Dependabot/Renovate  | 依赖更新与分组策略                                    |

---

# 12. 交付门禁

## G0：事实基线

- current CI green；
- tracker 与 acceptance 一致；
- README 状态准确；
- no unreviewed direct-main feature merge。

## G1：静态质量

- provenance、Ruff、Pyright、full pytest；
- clean build/install；
- schema registry；
- no caller-supplied truth。

## G2：最小产品链

- raw task → evidence → classification → formal spec → environment package；
- restart/replay；
- 无 deferred production adapter。

## G3：真实 runtime/profile

- profile build/import/behavior attest；
- bytes-only RemoteEnv；
- official suite tests；
- principal isolation。

## G4：真实训练

- RLlib build/train/restore/evaluate；
- policy export；
- single/multi-agent；
- non-finite/failure paths。

## G5：统计与实验基础

- exact grid；
- bootstrap/Holm/NI/power；
- golden cross-check；
- synthetic analysis fixtures。

## G6：实验就绪

- preregistration；
- task cards；
- model/evidence/tool budgets；
- sealed handshake；
- calibration；
- design power；
- complete intention ledger。

## G7：实验完成

- 每个 slot signed terminal；
- no silent retry/deletion；
- analysis fully rebuildable；
- deviations reported。

## G8：发布就绪

- redaction/publisher；
- public bundle；
- software artifacts；
- SBOM/attestation；
- independent clean reproduction；
- owner approval。

---

# 13. Definition of Done

任何 Issue/PR 只有同时满足以下条件才能关闭：

1. 目标行为和非目标明确；
2. 受影响 public seam 明确；
3. schema/version/migration 明确；
4. 生产实现可由公共入口到达；
5. 正例测试；
6. 至少一个欺骗性反例；
7. 安全边界测试（适用时）；
8. fault/restart 测试（适用时）；
9. lint/type/full relevant tests；
10. exact commit/profile/artifact 证据；
11. 文档和 changelog；
12. 独立 reviewer；
13. acceptance checkbox 由可验证证据支撑；
14. README/status 自动更新；
15. 不使用“模型存在”“测试对象可构造”“提交信息说通过”作为完成证据。

---

# 14. 风险登记


| 风险                                      | 概率 | 影响 | 处置                                                  |
| ------------------------------------------- | ------ | ------ | ------------------------------------------------------- |
| 先跑大矩阵再修基础设施                    | 高   | 极高 | G0–G6 阻断                                           |
| LLM 生成形式化字符串但不可执行            | 高   | 极高 | typed AST + deterministic validator                   |
| suite 依赖冲突                            | 高   | 高   | profile isolation + bytes-only protocol               |
| RLlib API 漂移                            | 高   | 高   | pinned profile + runtime introspection                |
| 少 seed 导致错误结论                      | 中高 | 高   | power + interval/IQM/profile                          |
| sealed leakage                            | 中   | 极高 | separate principal/process/mount + negative tests     |
| prompt injection                          | 高   | 高   | untrusted RAG pipeline/capabilities                   |
| same-repo provenance false confidence     | 高   | 高   | external build attest/verification                    |
| giant module regression                   | 高   | 中高 | staged refactor + seam tests                          |
| tracker 再次假完成                        | 高   | 高   | machine-derived status + branch rules                 |
| restricted upstream license contamination | 中   | 极高 | typed source policy + SBOM/license/profile egress     |
| report redaction failure                  | 中   | 极高 | isolated redactor + fixed renderer + independent scan |
| compute budget失控                        | 中   | 高   | pilot telemetry + frozen ceilings                     |
| task card/version drift                   | 中   | 高   | content address + prereg version                      |
| hidden evaluator feedback污染             | 中   | 极高 | post-terminal only, no generation capability          |

---

# 15. 最终开发决策

为了最大化真实可交付性，建议固定以下决策：

1. **先做一个可信垂直切片，不并行实现六 suite。**
2. **保留现有可信工件/事件/证据/密封评测思想，不推倒重来。**
3. **将 late-stage “结果模型”改为 computation-owned，不再让调用方填写真值。**
4. **将 DecisionProcessSpec 从自由字符串升级为 typed formal AST。**
5. **将真实 runtime worker 与核心包解耦，跨 profile 只走 canonical protocol。**
6. **以 pinned RLlib 新 API 的真实 smoke 决定配置，不根据历史版本猜测。**
7. **统计设计和运行代码一体预注册，任何结论可由 raw observations 重建。**
8. **内部 provenance 作为补充，外部 build attestation 作为发布信任链。**
9. **所有完成状态由 CI/artifact 派生，而不是 Issue 标签、提交信息或手工文字。**
10. **confirmatory matrix 只能在 G6 后启动。**

---

# 16. 外部规范与主要来源

## 仓库证据

- AutoMarkov current source at reviewed commit:
  `https://github.com/JiaWANG-TJ/AutoMarkov/tree/3cea996309c6bc3bfe5b29dd83b82f5131ca4366`
- Current failed Actions run:
  `https://github.com/JiaWANG-TJ/AutoMarkov/actions/runs/32839277223`
- README:
  `https://github.com/JiaWANG-TJ/AutoMarkov/blob/3cea996309c6bc3bfe5b29dd83b82f5131ca4366/README.md`
- Decision process schemas:
  `https://github.com/JiaWANG-TJ/AutoMarkov/blob/3cea996309c6bc3bfe5b29dd83b82f5131ca4366/src/automarkov/decision_process.py`
- Default adapters:
  `https://github.com/JiaWANG-TJ/AutoMarkov/blob/3cea996309c6bc3bfe5b29dd83b82f5131ca4366/src/automarkov/adapters.py`
- RLlib contracts:
  `https://github.com/JiaWANG-TJ/AutoMarkov/blob/3cea996309c6bc3bfe5b29dd83b82f5131ca4366/src/automarkov/rllib_training.py`
- Statistics:
  `https://github.com/JiaWANG-TJ/AutoMarkov/blob/3cea996309c6bc3bfe5b29dd83b82f5131ca4366/src/automarkov/statistics.py`
- Experiment preregistration:
  `https://github.com/JiaWANG-TJ/AutoMarkov/blob/3cea996309c6bc3bfe5b29dd83b82f5131ca4366/docs/experiments/automarkov-code-experiment-plan.md`
- Repository recovery document:
  `https://github.com/JiaWANG-TJ/AutoMarkov/blob/3cea996309c6bc3bfe5b29dd83b82f5131ca4366/docs/AutoMarkov_recovery_completion_and_delivery_plan.md`

## 官方工程规范

- Ray RLlib new API stack migration:
  `https://docs.ray.io/en/latest/rllib/new-api-stack-migration-guide.html`
- Ray Learner API:
  `https://docs.ray.io/en/latest/rllib/rllib-learner.html`
- Gymnasium custom environment/checker:
  `https://gymnasium.farama.org/introduction/create_custom_env/`
- PettingZoo environment tests:
  `https://pettingzoo.farama.org/main/content/environment_tests/`
- GitHub protected branches:
  `https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches`
- GitHub Actions security:
  `https://docs.github.com/en/actions/how-tos/secure-your-work`
- GitHub artifact attestations:
  `https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations`
- SLSA Build Track:
  `https://slsa.dev/spec/v1.2/build-track-basics`
- OSV-Scanner GitHub Action:
  `https://google.github.io/osv-scanner/github-action/`
- OpenSSF Scorecard checks:
  `https://github.com/ossf/scorecard/blob/main/docs/checks.md`
- PyPA project metadata:
  `https://packaging.python.org/specifications/declaring-project-metadata/`

## 统计与强化学习实验

- Henderson et al., “Deep Reinforcement Learning That Matters,” AAAI 2018:
  `https://ojs.aaai.org/index.php/AAAI/article/view/11694`
- Agarwal et al., “Deep Reinforcement Learning at the Edge of the Statistical Precipice,” NeurIPS 2021:
  `https://proceedings.neurips.cc/paper/2021/hash/f514cec81cb148559cf475e7426eed5e-Abstract.html`

---

# 17. 审查结论

AutoMarkov 的正确路线不是继续增加更多 Pydantic 类型或更长的预注册文档，而是完成以下转换：

```text
合同存在
  -> 生产执行器可达
  -> 真实 profile 运行
  -> 结果机械派生
  -> 原始证据可重建
  -> 外部供应链可验证
  -> 小型垂直切片
  -> 受控 pilot
  -> confirmatory experiment
  -> release
```

只要严格按 G0–G8 推进，现有可信底座可以被保留，并逐步转化为真正可审计、可复现、可投稿、可开源交付的系统。绕过前置门禁直接扩充六 suite 或大规模实验，只会把当前“合同—执行失配”放大为高成本、不可解释的结果。
