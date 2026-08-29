# AutoMarkov 问题修复、完整开发、实验运行与项目交付执行计划

## 0. 文档合同

### 0.1 Material Passport

- Origin: repository audit + primary-source refresh + experiment planning
- Origin Date: 2026-08-25
- Verification Status: `ANALYZED`
- Document Status: `PROPOSED_RECOVERY_CONTRACT`
- Baseline Commit: `4bbc601e499d8db52a7e1f937cf0a7d9f7a90789`
- Scope: 从当前红色工程基线恢复到可独立复现、可审计交付的 AutoMarkov
- Non-goal: 本文不宣称任一未运行实验已完成，也不替代 owner 对预算、凭据、许可和正式发布的批准

### 0.2 权威顺序

本文是执行和恢复路线，不修改已经接受的研究合同。发生冲突时按以下顺序处理：

1. 当前用户指令；
2. `AGENTS.md`；
3. `docs/adr/0001-immutable-artifacts-and-append-only-events.md`；
4. `docs/adr/0002-isolated-runtime-profiles.md`；
5. `docs/adr/0003-sealed-evaluation-boundary.md`；
6. `docs/AutoMarkov_complete_development_specification.md`；
7. `docs/experiments/automarkov-code-experiment-plan.md`；
8. 本文。

任何实现发现上述来源互相矛盾时，必须停止受影响 slice，记录 `SpecificationConflictDetected`，给出具体字段、两个冲突来源和最小可选决策；不得由实现者静默选边。

### 0.3 完成状态词汇

本文判定工作包完成度时只使用以下状态，禁止用模糊的“基本完成”“差不多可用”。实验规划中的`READY`、`BLOCKED_DESIGN`、`RUN/N/A`、terminal failure taxonomy等是已冻结的domain enum，不得被误当为工作包完成度：

| 状态 | 可证明含义 | 不代表 |
|---|---|---|
| `DOCUMENTED` | 问题、接口或命令已有文本合同 | 代码存在、测试通过 |
| `IMPLEMENTED` | 生产 adapter 已存在并可由公共 seam 到达 | 静态检查、runtime 或实验通过 |
| `STATIC_VERIFIED` | lint、type、schema/contract tests 在 exact bytes 上通过 | profile 可安装或服务可用 |
| `METADATA_VERIFIED` | lock、manifest、SBOM、license、source identity 通过 | image built、import smoke、真实推理 |
| `RUNTIME_VERIFIED` | exact profile/image/service 完成 fresh import/canary/handshake | 六 suite 实验已完成 |
| `EXPERIMENT_READY` | preregistration、design power、gold calibration、sealed handshake 和完整 intention ledger 均冻结 | 任一 confirmatory result |
| `EXPERIMENT_COMPLETE` | 所有预注册 slot 都有 signed terminal outcome，统计可从工件重建 | release bundle 已安全发布 |
| `RELEASE_READY` | clean build、CI、扫描、redaction、cards、复现报告均通过 | owner 已授权发布 |
| `BLOCKED` | 缺少外部 authority、asset、credential、预算或已登记决定 | 失败可被实现者绕过 |
| `DEFERRED` | 明确不属于当前交付并保留恢复条件 | 已完成或失败 |
| `WAITING_RUNTIME` / `WAITING_ASSET` | 只有依赖对应runtime/asset的branch暂时不可继续；精确记录dependency和resume state | 全局`BLOCKED`、无关确定性检查停止或gate已关闭 |
| `PARTIAL_METHOD_IMPLEMENTATION` | 至少一个非blocked `R17A[s]`已经capability/budget/cache/transcript验证，但R17B六suite总门未关闭 | six-suite method completeness、R18或confirmatory readiness |

### 0.4 使用方法

每个 Coding Agent 只领取一个无未完成 blocker 的工作包。开始前读取该包列出的输入，先运行“最小红测试”，只修改列出的 seam 和直接测试；完成后运行该包验证，不提前执行后续包。工作包关闭必须同时满足：代码、测试、运行工件、Git/manifest identity 和允许声明五项证据。

### 0.5 每个工作包的强制开发流程

1. 每个project-development任务都先调用`grill-with-docs`，读取当前spec、CONTEXT、适用ADR、ticket和本文工作包，确认domain language、scope和hard-to-reverse decisions；只有难以逆转且有真实权衡的决定写ADR。
2. 单一可运行设计问题必须先`handoff`到独立上下文，调用`prototype`取得证据，再`handoff`回原设计上下文；prototype不进入production或实验结果。
3. 多session工作在同一设计上下文依次调用`to-spec`和`to-tickets`，发布带显式blockers的GitHub tickets后，每个ticket在新上下文按blocker顺序实施；single-session工作才直接调用`implement`。
4. `implement`按一个red-green slice调用TDD；每次只运行当前seam和直接integration的必要测试。
5. slice完成后执行Standards/Spec双轴code review，修复actionable findings。
6. 外层operator再运行恰好一个native review target；同一review进程不得递归review。
7. Git/issue/PR/release写入仍需owner权限，工作流完成不自动授权外部状态变更。

若某个要求的workflow skill在执行环境不可用，agent必须报告缺失的skill/阶段和影响，不得用“已手工等价完成”掩盖门禁。

---

## 1. 当前事实基线

### 1.1 仓库与 tracker

- 本文问题审计的代码baseline是`4bbc601e499d8db52a7e1f937cf0a7d9f7a90789`。
- 文档编写期间出现未经本对话owner批准记录的外部Git写入：`main`和`origin/main`前移至doc-only child `3cea996309c6bc3bfe5b29dd83b82f5131ca4366`，新增本文初版和`.codegraph/.gitignore`。该commit不改变生产代码，但构成governance deviation；禁止擅自回滚，等待D17处置。
- 当前worktree在`3cea996`上继续修改本文，并新增未提交的上游研究笔记；这些bytes尚无approved commit/push证据。
- GitHub Issues `T01`–`T27` 均被关闭，但 T18–T27 的多项验收命令、路径和运行工件不存在。
- 当前仓库没有 PR 记录能证明 T18–T27 经独立 Standards/Spec review 后合入。
- 最新HEAD `3cea996`对应的GitHub Actions `provenance-contract`仍失败；从`e272be4`起已连续八次push workflow失败。

### 1.2 新鲜验证结果

| 检查 | 2026-08-25 结果 | 当前允许声明 |
|---|---|---|
| baseline `4bbc601` provenance | exit 1；6 个 restricted ingress/frozen source identity errors | provenance invalid |
| HEAD `3cea996` CI provenance | exit 1；10 errors；新增doc名称token ingress和tracked `.codegraph/.gitignore`错误 | provenance invalid；doc commit没有修复baseline |
| `uv run --locked ruff check .` | 22 errors | lint invalid |
| `uv run --locked pyright` | 88 errors | type invalid |
| `uv run --locked pytest -q` | 在约 15 分钟、47% 时停止；已出现重复 provenance baseline setup errors | 不得声明 full suite green |
| CLI discovery | 只有 `compile`、`verify-provenance`、`pilot run` | `experiment`/`publish` 命令尚未实现 |
| artifact discovery | 仅发现 CartPole CPU engineering pilot | confirmatory matrix 未运行 |

全仓 pytest 停止不是通过，也不是新的产品缺陷计数。它只证明当前 provenance baseline 会级联破坏依赖“pristine repository valid”的测试，并暴露 default schema registry 重建存在高 CPU 成本。修复 R02/R03 后只允许重跑一次全仓 suite。

### 1.3 Runtime 状态

| Profile 类别 | 当前状态 | 结论 |
|---|---|---|
| `llm-qwen36-vllm` | `attached_unverified` | 未取得 fresh current-connection + model/tokenizer/weights identity 闭环 |
| 其余可执行 profiles | `recipe_frozen` | 只证明构建输入冻结，不证明 image/import/runtime 可用 |
| `replication-agent2world-restricted` | `restricted_disabled` | 正确保持不可构建、不可发布 |

没有任何 profile 当前可仅凭 `profile.json` 升级为 `RUNTIME_VERIFIED`。CartPole pilot 来自旧 commit `179603d`，只含一次 PPO iteration 和一个 evaluation episode，并明确是 nonconfirmatory engineering evidence。

### 1.4 允许立即沿用的强实现

以下模块具有较高的继续复用价值，但仍需通过 R02–R04 的当前基线验证：

- bounded canonical JSON、strict/frozen raw ingress；
- memory/SQLite immutable artifact repository；
- append-only lifecycle、specified-head projection、terminal/cross-run CAS；
- authenticated attached local LLM adapter 的 identity/probe 合同；
- Tavily lease/rotation/receipt 和 evidence artifact 合同；
- RemoteEnv canonical codec、mTLS identity 和 transport limits；
- sealed four-gate evaluator topology与 request/verdict contracts；
- FixedCommitRunner 的 preflight、terminal record、attestation 和 replay store。

---

## 2. 问题总表

### 2.1 P0：阻断所有后续实验和交付

| ID | 问题 | 根因 | 可复现证据 | 必须解决到 |
|---|---|---|---|---|
| P0-01 | Tracker 关闭状态与验收事实不一致 | ticket 被按“类存在/测试样例存在”而非完整 acceptance closure 关闭 | T25/T27 命令和测试目录不存在；CI 先失败后集中关票 | R00/R01 |
| P0-02 | 当前工程基线为红色 | `release_pipeline.py` 改字节后未同步 frozen source identity，restricted token policy又把声明文本当 ingress | provenance CLI/Actions 均 exit 1 | R02 |
| P0-03 | T18–T27 使用 caller 自报结论 | 模型保存 `frozen/released/rejected/non_inferior`，没有从 signed inputs 计算 | 零 checks 可 `released=true`；`p=0.9` 可 `rejected=true` | R05–R11 |
| P0-04 | benchmark identity 与预注册合同漂移 | 后期实现另造 suite/variant/method IDs | CartPole 替换 MetaDrive；variant/method IDs 不匹配 | R05 |
| P0-05 | 默认 Compiler 不形成产品链 | `compile_task` 只调用 `InMemoryCompiler.start`，package 与多个 public adapter 仍 deferred/scripted | CLI 只返回 `RunId` | R06/R12–R16 |
| P0-06 | 没有真实 GG training/export | `rllib_training.py` 只有 schema/Protocol，且字段混用旧 RLlib 命名 | 无 production `TrainingRunner`、六 suite training artifacts | R07/R12–R16B |
| P0-07 | 没有统计实现 | `statistics.py` 只有结果模型，没有 counter/bootstrap/Holm 计算 | 无 design-power、calibration 或 analysis CLI | R08/R21/R27 |
| P0-08 | 没有 redactor/publisher | `release_pipeline.py` 没有 taint closure、fixed renderer、allowlisted writer | 无 public report bundle/attestation | R10/R27 |
| P0-09 | T18–T27 工件没有进入ArtifactRepository | late modules只增加Pydantic类型，默认schema/parent registry没有training/benchmark/statistics/release registrations | JSON对象不能形成受DAG/CAS/replay保护的系统工件 | R05–R10 |

### 2.2 P1：高风险正确性和可维护性问题

| ID | 问题 | 根因 | 影响 | 必须解决到 |
|---|---|---|---|---|
| P1-01 | 同仓中央source-hash allowlist与目标源码可共同修改 | 期望hash可与目标代码在同一commit同时修改，且普通文本token scan过宽 | 独立信任收益有限、开发脆弱、诱发字符串规避 | D01/R02 |
| P1-02 | release pipeline 可表达矛盾状态 | 缺少 derived result、closed check set、evidence cardinality | 假 freeze、假 release、假 replication complete | R09/R10 |
| P1-03 | grid/strata 只检查计数 | 未检查完整笛卡尔积、唯一 tuple、canonical order | 重复 cell 冒充覆盖 | R05/R08 |
| P1-04 | suite 与 route 未绑定 | `TaskCard` 允许任意 suite/implementation 组合 | CityLearn 可被声明成 classic control | R05 |
| P1-05 | RLlib config 不对齐 2.56 新 API | 合同使用旧 `train_batch_size/num_sgd_iter/num_gpus` 命名 | runtime adapter 不能可靠构建 PPOConfig | R07 |
| P1-06 | 真实 profile worker 缺失 | core 中只有可注入 backend protocol/fake，缺 profile-local official package worker | suite tests 不能证明官方 simulator 可用 | R13–R16 |
| P1-07 | 文献/upstream 证据过期或缺失 | A-LAMP/Agent2 只在研究文档出现，未进入 typed upstream manifest/paper passports | paper-spec 边界无法审计 | R19 |
| P1-08 | README、handoff、issues 三套状态漂移 | 没有单一 derived status report | 新 agent 易把历史状态当当前事实 | R01/R11 |
| P1-09 | full suite 高成本且失败扩散 | provenance fixture 重复重建/扫描，default registry重复生成 schema hash | 反馈慢、错误根因被大量 setup errors 淹没 | R03/R04 |
| P1-10 | “通用”RemoteEnv worker硬编码CartPole | worker固定4D observation、二元action和`CartPole-v1` | suite tracer被误当生产通用worker | R05A/R12–R16B |
| P1-11 | RLlib-facing adapter持有进程内backend | `SingleAgentSuiteLifecycle`/`RemoteGymnasiumEnv`保存Python backend而非只经RemoteEnv | 跨profile共享对象，违反ADR 0002 | R05A/R12 |
| P1-12 | checkpoint path和签名合同不闭合 | relative path未拒绝`../`/absolute；export/request只验证signature字符串形状 | path traversal、伪签名、clock/replay/substitution | R07 |
| P1-13 | smoke/audit pass可自报 | CPU smoke、information audit可同时携带失败evidence和`passed=true` | 错误训练进入下一gate | R07/R09 |
| P1-14 | profile build状态没有生产升级路径 | verifier对`built`声明fail closed，缺build/import attestation生成和specified-head resolver | FixedCommitRunner无法取得可执行profile | R05A |
| P1-15 | 本次文档编写期间发生未授权Git commit/push | 外部进程创建并推送`3cea996`，无本对话事前branch/author/message/scope批准 | governance/delivery deviation；R00只记录，不等待处置；写入型R01和交付受阻 | D17/R01/交付 |

### 2.3 P2：完成前必须处理的工程债

- `repository.py`、`lifecycle.py`、`fixed_commit_runner.py` 的接口有价值，但实现体量大；后续只通过现有 public seam 测试，禁止新增能绕过它们的平行脚本。
- late-stage tests 多为“构造一个合法对象”，缺少欺骗性反例、evidence substitution、duplicate/cross-cell、restart 和真实 profile smoke。
- 当前 CI 只覆盖 metadata/provenance subset；lint、type、secret、dependency、container、artifact rebuild 尚未形成必过 job。
- 30 篇论文的结构化阅读记录、claim-to-source mapping 和 paper-replication deviation ledger 尚未形成。
- 当前没有可独立交给第三方、从 clean checkout 复现实验主表的入口和包。

---

## 3. 修复架构：只深化现有六条 public seams

### 3.1 Seam ownership

| Public seam | 必须隐藏的复杂性 | Production adapter | Test adapter | 禁止的旁路 |
|---|---|---|---|---|
| `Compiler` | task lifecycle、agent orchestration、freeze/preflight、package/analysis coordination | repository-backed compiler | deterministic in-memory compiler | 独立 experiment shell 直接改 run 状态 |
| `ArtifactRepository` | canonical identity、DAG、events、CAS、projection、replay | SQLite repository | memory repository | 以目录存在替代 artifact identity |
| `LocalLlmRuntime` | current connection、token admission、completion/trace provenance | attached vLLM adapter | scripted verified transport | hosted LLM、普通未认证 HTTP fallback |
| `EvidenceGateway` | Tavily endpoint policy、lease、rotation、receipt、cache、conflict evidence | Tavily gateway | deterministic fake transport | agent 直接持有 provider key/HTTP client |
| `ExecutionSandbox` | fixed commit、profile、mount/capability/network policy、terminal attestation | FixedCommitRunner adapter | memory executor | 任意 `subprocess` 作为 publication run |
| `EnvironmentBinding` / `TrainingRunner` | RemoteEnv session、official suite worker、RLlib plan/train/export/evaluate | profile-local environment/training adapters | in-memory byte worker/fake trainer | core import simulator/Ray、checkpoint 跨 profile |

实验矩阵协调、统计和发布不是新增第七条 public seam。它们是 `Compiler` 内部深模块，通过 `ArtifactRepository` 读取 specified-head 工件，并通过 `ExecutionSandbox` 运行 fixed-commit jobs。统计计算必须是纯函数内部 seam；publisher 是 packaging 阶段的隔离 execution，不拥有 mutable run state。

Runtime profile resolver是`ArtifactRepository + ExecutionSandbox`的内部ownership：repository从caller-specified head解析build/import attestations，sandbox只消费验证后的profile identity。Sealed evaluation execution也归`ExecutionSandbox`，不是新增public seam；`TrainingRunner.evaluate`只协调显式signed request，不加载gold或sealed locator。

### 3.2 Replace, do not layer

R05–R10 必须替换浅的 T18–T27 模型，而不是在其外增加第二套“corrected” schema。每个概念只保留一个当前write version和显式migration/read policy；历史immutable artifacts可verified-read、migrate或reference-only，禁止原地改写：

1. 先写能让旧模型错误通过的红测试；
2. 在原模块或经批准的新深内部模块中实现 derived result；
3. 将调用方改为只提交原始 evidence，不提交结论布尔值；
4. 删除被新 interface 覆盖的浅测试；
5. 对已持久化旧 schema 给出 reject/migrate/reference-only 决策；
6. 通过公共 seam 证明调用路径，不直接测试内部私有状态。

### 3.3 目标执行链

```text
TaskRequest
  -> Compiler.dispatch
  -> TaskContract + approvals
  -> DecisionProcessSpec + approvals
  -> ImplementationRoute
  -> EnvironmentBinding
  -> public validation
  -> one sealed E2E gate
  -> TrainingRunner (eligible candidates only)
  -> weights-only PolicyExportManifest
  -> sealed policy evaluation
  -> terminal artifacts/attestations
  -> experiment-level paired analysis
  -> isolated redaction
  -> fixed public report rendering
```

每条箭头必须有schema、artifact lineage/parent contract、负例和restart/replay语义；只有发生Run lifecycle状态变化时才追加transition event。实验级analysis不写回单Run state。目录或Python object不是跨箭头协议。

---

## 4. 恢复工作包与依赖

### 4.1 依赖总览

```text
R00 -> R01-doc
  |       |
  +-> R02 -> R03 -> R04 -> R05 -> R06
                                +-> R05A-BUILD
                                +-> R05A-ATTACH
                                +-> R08 -> R09 -> R10 -> R11
                                +-> R19 -> R20

R06 + R05A-BUILD -> R12-DET
R12-DET -> R07-CORE
R12-DET + R05A-ATTACH -> R12-E2E (= R12 closed)
R07-CORE + R12-E2E -> R07-TAXI-E2E (= R07 closed)
R07-CORE + R12-DET + R05A-BUILD -> R13-DET/R14-DET/R15-DET/R16A-DET/R16B-DET
For each non-Taxi suite s:
  suite_det[s] + R05A-ATTACH + R12-E2E -> suite_e2e[s] (= suite_impl[s])

For each suite s:
  suite_det[s] -> R24-DET[s]
  suite_impl[s] + R19/R20 -> R17A[s]
  R24-DET[s] + suite_impl[s] + R17A[s] -> R24-E2E[s] (= R24[s])
  R24[s] + R21A -> R25[s]
All six suite_impl[s] + all R17A[s] -> R17B -> R18
All R24[s] -> R24-ALL; all R25[s] -> R25-ALL
D11 -> R21A
R08 + R18 + R19 + R21A + (R25-ALL iff Public Dev is selected) -> R21B (= R21 closed)
All suite_impl[s] + R19 + R24-ALL -> R22
R09 + R20 + R21B + R22 + R18 + R24-ALL + R25-ALL -> R23
R23 -> R26 -> R27
D15 -> R05 -> R06/R07/R08/R09/R10
D06 -> R05A-ATTACH
D16 -> R05A-BUILD
D18 -> R16A
D19 -> R16B
```

`suite_det[s]`是只依赖built profile、RemoteEnv、official API/seed、runner core和确定性fixtures的suite子门；`suite_impl[s]`是R12、R13、R14、R15、R16A、R16B中对应suite的DET+E2E总门。`R24-DET[s]`是不依赖LLM的engineering pilot子门，`R24-E2E[s]`才联合suite/method/LLM证据关闭`R24[s]`。`R17A[s]`、`R24[s]`、`R25[s]`是同一suite的partial状态，`R24-ALL`/`R25-ALL`才是六suite全局关闭门。任一外部asset未解决时，只把相应`s`标为`WAITING_ASSET`，不阻塞Taxi、MiniGrid、MPE2或其他已就绪suite的R17A/R24/R25；但R17B、R18、R22全局关闭、R23及confirmatory run仍要求六suite全部闭合。`R01-doc`表示本地README/handoff事实修复；GitHub reopen/relabel和D17处置是owner授权的governance/delivery blockers，不阻塞只读R00或本地R02–R04。R00–R04恢复可信开发基线；D15必须在R05替换late schema前决定，R05下游继承该write/migration/read policy。R05修复canonical contracts和artifact registrations；D16是唯一的OCI build/registry/attestation authority决策，D06只管理runtime签名/审批keys和approved attach relay/service principal。R05A-BUILD和R05A-ATTACH可并行；R06用production-shape fake adapters完成composition root也可并行。R12-DET只在R05A-BUILD后完成真实Taxi profile/RemoteEnv/sealed确定性基础，并解锁R07-CORE和其他suite的DET工作；R12-E2E另等R05A-ATTACH后运行真实generation。R07-CORE不等attach；R07-TAXI-E2E只在R12-E2E后关闭R07。D18/D19分别处理MetaDrive/CityLearn数据授权。R13–R16B的DET子门不等LLM，E2E子门才等R05A-ATTACH/R12-E2E。R19刷新学术来源，R20冻结task cards；R17A可逐suite实现非blocked方法，全部资产具备后R17B才允许关闭六suite方法合同，随后R18实现六消融。R24/R25产生nonconfirmatory engineering和non-overlapping Public Dev证据。D11先经R21A冻结不含outcome的nuisance-source选择和Public Dev partition/probe合同，然后R25才运行；若选Public Dev，R21B在R25-ALL后读取冻结rows并做design-power，若选一手先验/保守边界则不等R25结果。R20、R21B、R22形成实际freeze inputs，R23才允许冻结，R26/R27才运行和交付。

### 4.2 R00：证据快照与状态对账

- Objective: 产生一个不修改 GitHub 的当前状态清单，明确每个 T01–T27 的真实证据等级。
- Inputs: HEAD、Git status、issue bodies、workflow runs、CLI help、profiles、artifacts、README、handoff。
- Change scope: `docs/agents/current-development-handoff.md` 后续更新；本包不修改生产代码。
- Procedure:
  1. 导出 issue number/title/state/acceptance/checklist/closure time/linked PR。
  2. 把每项 acceptance 映射到 code symbol、test、runtime artifact、workflow URL。
  3. 未找到证据的 acceptance 标记 `UNPROVEN`，不能按文件名推定通过。
  4. 记录 HEAD、profile statuses、artifact inventory 和最近一次成功/失败命令。
- Completion criterion: 27 行 status matrix 每行都有 `implemented/static/runtime/experiment/release` 五列和 evidence locator。
- Claim limit: 只允许说“对账完成”，不改变 issue state。

### 4.3 R01：修复 tracker 和文档事实源

- Blocked by: R00；GitHub 写入另需 owner 明确授权。
- Local blocker: 先更新README/handoff，去除过期HEAD/relay/staged状态；该`R01-doc`完成后本地工程可继续。
- Recommended tracker action: 由R00五维证据矩阵决定所有需reopen的tickets，不能预设只有T18–T27；T16/T17历史native review未完成时也不得自动保留CLOSED。若owner要保留历史关闭，则创建`Recovery` milestone和对应R tickets，并在旧issue中只添加事实链接。
- Required ticket fields: objective、non-goals、blocked-by、exact acceptance、targeted red command、runtime gate、artifact outputs、claim limit。
- Docs changes:
  - README 只描述当前可证明能力；
  - handoff 指向当前 blocker，不保留已过期 relay/暂存状态；
  - issue state 是工作流状态，规格 gate report 是工程事实，两者不可互相替代。
- Completion criterion: tracker 中不存在 closed-but-unproven ticket；README/handoff/issue 对当前 HEAD 无矛盾。
- Governance degradation: 未获GitHub授权时，本地文档可完成并继续R02；tracker动作保持`BLOCKED`（`reason_code=governance_authorization_missing`），必须在release前解决。

### 4.4 R02：恢复 provenance 与 restricted-source policy

- Blocked by: D01 owner decision。
- Red commands:
  - `uv run --locked automarkov verify-provenance --repository-root .`
  - 当前 GitHub Actions metadata job。
- Recommended design:
  1. development CI 以 clean Git tree、typed upstream/profile manifests、AST/import/path rules作为事实源；
  2. restricted policy 拒绝 import、vendored paths、package/lock ingress和已知 source fingerprints，而不是拒绝合法文档中的项目名称；
  3. release identity 在 clean build 后生成外部 subject hashes、SBOM 和 signed provenance statement；
  4. 若保留 `_REGISTERED_SOURCE_HASHES`，其文件必须由仓库外签名 authority 冻结，不能把同一 commit 内可一起修改的字典当独立信任根；
  5. restricted-upstream相关文档必须走registered typed ingress：`ResearchSourceStatement`只允许项目名、URL、commit、license hash/摘要、概念级论文事实和禁止事项，结构化标记`metadata_only=true`、`executable_content=false`、`redistribution=false`；源码、prompt、test、archive、patch、expressive pseudocode、package/lock/container ingress仍拒绝；
  6. `docs/research/2026-08-25-recovery-upstream-standards-refresh.md`在上述schema/AST/path检查和positive/negative fixtures与它同commit通过前保持未跟踪；不得为让CI变绿而改写项目名或删掉许可风险。
- Target files: `src/automarkov/provenance.py`、对应 provenance tests、`.github/workflows/provenance.yml`、必要时新 ADR。
- Negative tests: import、encoded import、archive member、symlink、Git index/worktree divergence、lock dependency、container COPY、generated wheel/sdist ingress。
- Positive tests: registered docs/research/spec可直接写正确项目名称和必要许可事实；合法 clean-room method ID 不被误判为受限源码。
- Completion criterion: 研究笔记被纳入typed document registry后，local verifier和含该文件的clean-checkout CI均 valid；将restricted code/prompt/test或未登记文档换入同一路径会稳定 red；合法声明文本不需字符串拼接规避。

### 4.5 R03：恢复 lint、type 和测试反馈速度

- Red commands: `ruff check .`、`pyright`。
- Required fixes:
  - 使用真正的 `TypeAlias`，不把 `Literal[...]` 赋给声明为 `type` 的变量；
  - 测试直接构造 typed `ArtifactReference`，不依赖 Pydantic runtime coercion绕过 type checker；
  - import order、unused imports、broad exception assertions 全部清零；
  - default schema registry和固定 JSON schema hash在进程内缓存，缓存 key 必须含 registry version/hash且返回深冻结对象；
  - provenance fixtures共享只读 pristine snapshot，只对每个 case 复制最小被测子树；
  - pytest marker区分 `unit/contract/security/integration/profile_smoke/experiment/release`。
- Completion criterion: ruff 0、pyright 0；focused trust-substrate suite在批准的时间预算内完成；无 provenance setup error fan-out。

### 4.6 R04：可信基线总门禁

- Blocked by: R01-doc、R02、R03；不被尚未授权的GitHub写操作阻塞。
- Run once:
  1. `uv sync --locked`；
  2. all profile `uv lock --check`；
  3. ruff；
  4. pyright；
  5. provenance verifier；
  6. focused core/security/integration suites；
  7. full pytest一次；
  8. `git diff --check`；
  9. 外层 operator 运行恰好一个 `codex review --uncommitted`。
- Output: content-addressed baseline report，记录命令、exit、duration、test count、HEAD、worktree state。
- Completion criterion: 所有项目 green，native review 无 actionable finding；否则后续工作包保持 blocked。

### 4.7 R05：重建 benchmark、method 和 ablation identity

- Blocked by: R04、D15。
- Canonical suites: `taxi_mdp`、`memory_pomdp`、`mpe2_full_state_mg`、`smacv2_posg`、`metadrive_pomdp`、`citylearn_posg`。
- Canonical variants: `v1_canonical`、`v2_paraphrased`、`v3_reordered_longform`、`v4_evidence_split`、`v5_clarification_required`。
- Canonical methods: `single_llm`、`react_executor`、`alamp_paper_spec`、`agent2_paper_spec`、`agent2world_clean_controlled`、`automarkov`。
- Canonical component ablations: 规格登记的六项，不能把主方法 `automarkov_no_evidence` 与六方法矩阵混为一谈。
- Required invariants:
  - suite唯一解析 source-access mode、implementation route、profile和evaluation contract；
  - full grid 精确等于六 suite × 五 variant × 两 track × 六 method 的唯一笛卡尔积；
  - cell tuple、pair IDs、task-card refs canonical且无重复；
  - `RUN/N/A` 有 closed reason和pre-run evidence；`DEFERRED` 只允许全局明确延期项；
  - schema必须要求`pair_count`引用signed `DesignPowerReport`；R05只用synthetic signed fixtures验证，真实值由R21生成并在R23实例化，不能由caller任意填；
  - 144 个 component-ablation cells和MPE2 information slots独立建账。
- Red tests: duplicate cell、missing tuple、wrong suite route、wrong variant、post-run N/A mutation、method result进入pair binding、full/ablation identity leak。
- ArtifactRepository integration: training/benchmark/method/ablation/statistics/preflight/redaction/release每种新工件必须注册schema version、exact/payload-bound parent contract和migration/read policy；不能只写JSON目录。
- Completion criterion: 所有合法grid可由单一manifest重建并持久化到ArtifactRepository；上述反例全部失败。

### 4.8 R05A：Runtime profile build/attach、attestation 与 specified-head resolution

- Sub-gates: `R05A-BUILD` blocked by R04、R05、D16；`R05A-ATTACH` blocked by R04、R05、D06。approved service/relay暂时不可达时标记`WAITING_RUNTIME`并保留resume state，不误标`BLOCKED`。两子门可并行，任一不得伪写另一类状态。
- Objective: 对recipe profile补齐`recipe_frozen -> built`的唯一生产升级路径；对复用现场服务补齐`attached_unverified -> RUNTIME_VERIFIED attach`证据路径。tracked `profile.json`仍保持配置态，runtime结论来自immutable attach manifest/attestation，不把attached service伪写为`built`或要求OCI image fields。
- Implementation:
  - BUILD: clean build从allowlisted context和exact lock生成OCI image，签发content-addressed build/import-smoke attestations，ArtifactRepository以caller-specified head重验它们；
  - BUILD: verifier校验OCI manifest digest、linux/amd64、libc/OpenSSL/CA和profile identity后才返回built view；
  - ATTACH: operator-side attestor在当前connection上绑定listener/process/package/model/tokenizer/weights/cache/network/redacted-argv和probe identities，生成signed/content-addressed immutable attach manifest；consumer从specified ArtifactRepository head解析并重验；
  - moving tag、wrong head/digest/platform、缺attestation、unknown/revoked issuer全部fail closed；
  - package provenance按ingress分流：PyPI-sourced工件验官方filename/URL/hash，VCS source验repository/commit/tree hash，内部local-version wheel验immutable artifact ID/wheel hash/base-source/build-context/toolchain attestation；不为PyPI未发布的`0.25.1+cu129`伪造或要求PyPI hash；
  - tracked `profile.json`保持recipe/attached/disabled状态，不原地冒充现场built identity。
- Local-LLM security inputs: 冻结expected model ID/revision、weights shard/tokenizer/chat-template hashes、vLLM version/container digest、closed/redacted startup-argv projection、proxy route allowlist和network namespace/listener contract。还必须枚举model/HF、`VLLM_CACHE_ROOT`、Triton/torch-compile及临时cache roots，为每个root冻结canonical path、mount/source artifact identity、owner UID/GID、mode、可写principal、launch-time content/empty-state hash和reuse/disposal policy；API key只是一层控制，不作完整security perimeter。
- Local-LLM security gates: raw vLLM internal listener位于generation worker无法加入的独立network namespace，internal port只对relay principal可达，必须用namespace/cgroup/UID-aware firewall或等价隔离证明generation principal即使在同host也不能直连。项目owned relay向`LocalLlmRuntime`只暴露规格允许的authenticated loopback `/v1` endpoint（或未来ADR批准的Unix socket）；`AUTOMARKOV_VLLM_BASE_URL`指向relay而不是raw vLLM port。generation→relay edge冻结source/target principal、protocol/schema、API-key credential ID和transport capability；relay→vLLM edge在隔离namespace内用受限veth/firewall或mTLS。单纯把raw vLLM绑到与generation worker共享namespace的loopback不合格。外部请求只能通过鉴权relay的closed route allowlist。relay的closed text-only schema必须在转发前拒绝所有非文本content parts和HTTP(S)/`data:`/`file:` media fields，vLLM namespace对外default-deny egress；仅把`allowed_local_media_path`置空或把`allowed_media_domains`留空均不算关闭media。`trust_remote_code=false`，tool server、runtime LoRA和未冻结multimodal/tool endpoints默认关闭。model/HF cache只能是已attest的content-addressed只读mount；可变compile/runtime cache必须专用于exact service identity、service launch时为空、只对service principal可写、不挂载给其他profile，并在service lifecycle结束后销毁；跨service identity reuse必须先出新的content manifest/attestation。identity canary必须同时核对`/health`、`/v1/models`、一次真实generation、tokenization边界和上述hash/redacted argv projection；model listing单独不算ready。
- Taxi minimum graph: `runner-control`、`rllib-taxi-synthesis`、`sealed-env-taxi-gold`、`sealed-evaluator-rllib`，以及实际需要的authoring/local-LLM edges。
- Test paths: `tests/contract/test_profile_build_attestations.py`、`tests/runner/test_runtime_profile_resolution.py`、`tests/security/test_runtime_profile_substitution.py`、`tests/security/test_vllm_network_boundary.py`、`tests/integration/test_local_llm_identity_canary.py`。
- Negative tests: 绕过proxy直连internal port、未在allowlist的route、HTTP/`data:`/`file:` media payload、媒体请求产生任何egress、`trust_remote_code=true`、任一tool/runtime-LoRA capability未授权开启、cache root可被其他principal写入、预热cache无attestation或content drift、可变cache非空启动/跨service identity复用或挂载到其他profile、model/tokenizer/weights/chat-template/redacted-argv任一identity drift、只返回model listing但真实inference/tokenization失败，均必须fail closed。
- Sensitive-argv rule: attestation issuer可在运行时私有边界比对raw process argv，但持久化工件只保存allowlisted non-secret flags/values、credential ID和private-locator的opaque artifact/profile ID；API key、credential path、Authorization、private locator、raw argv及它们的可枚举hash全部禁止。report仅对该redacted projection做domain-separated hash，并记录issuer签名的`raw_argv_matches_projection=true`。
- Attach failure classification: 只有listener/process暂时不可达、未超出冻结deadline的瞬时transport失败或尚未provision的approved service才进`WAITING_RUNTIME`。任一signature/binding/schema/principal/auth/route/media/cache/package/model/tokenizer/weights/argv identity不匹配、负例未被拒绝或protocol violation都产生terminal `FAILED`/`protocol_integrity_violation`证据，禁止沿原resume path继续；必须修正后用新runtime identity和new attempt重做。
- Completion criterion: Taxi所需recipe nodes经R05A-BUILD、local-LLM node经R05A-ATTACH，均可从specified head解析为verified runtime identities；伪造built/attach、两种状态互换和任一identity substitution被拒绝；另存一份content-addressed local-LLM security report，其包含proxy/listener/egress证据、冻结feature flags、cache root/source/permission/reuse证据、model/tokenizer/weights hashes、redacted argv projection/hash、真实generation/tokenization canary及所有负例结果；secret/private locator scan必须为空。

### 4.9 R06：深化 Compiler 并接通真实 adapters

- Blocked by: R05。R06用typed production-shape test adapters完成composition root，不被R05A-ATTACH暂时不可用阻塞；后续真实profile execution按branch分别依赖R05A-BUILD或R05A-ATTACH。
- Interface target: 保持 `start/dispatch/resume/package`，复杂阶段路由隐藏在Compiler实现内；不增加一组平行 `experiment_*` public methods。
- Implementation:
  - repository-backed composition root显式注入六条 seam adapters；
  - `start` 只创建 root run；
  - `dispatch` 只接受closed lifecycle command；
  - agent work通过 fixed-commit process输出typed artifact，再由authenticated command提交；
  - `resume` 必须指定 verified head；
  - `package(run_id,head)`只打包该单Run的terminal artifacts和audit projection；实验级analysis/report publishing是独立fixed-commit jobs，不写回单Run `PACKAGING` gate；
  - CLI返回artifact/result view，不把进程内对象作为结果。
- Red integration test: composition root用typed fake adapters从TaskRequest推进到implementation-selected specified head；restart后不得依赖进程内`_runs`，任一未批准task/formal artifact都不能越级。
- Completion criterion: 默认API不再每次创建孤立临时`InMemoryCompiler`后只返回RunId；repository-backed composition root可在SQLite上restart/resume。真实Taxi EnvironmentBinding归R12。

### 4.10 R07：实现 RLlib 2.56 TrainingRunner

- Sub-gates: `R07-CORE` blocked by R05、R05A-BUILD、R06、R12-DET；`R07-TAXI-E2E` blocked by R07-CORE和R12-E2E。R07-CORE不依赖LocalLlmRuntime，可先向其他suite提供确定性runner/checkpoint/export seams；两子门均通过才关闭R07。
- Official route:
  - `PPOConfig`/`AlgorithmConfig`；
  - `RLModule`/`MultiRLModule`；
  - `ConnectorV2`；
  - `EnvRunner`/`MultiAgentEnvRunner`或经验证的RemoteEnv adapter；
  - `LearnerGroup`；
  - PyTorch only。
- RemoteEnv integration: RLlib 2.56.1新栈external-environment support仍为under development；production实现必须是AutoMarkov自有、versioned、authenticated custom `EnvRunner`/adapter，不得宣称复用了稳定官方wire protocol。multi-agent setup也不能默认套用单智能体`num_envs_per_env_runner` vectorization。
- Contract corrections:
  - `train_batch_size_per_learner`；
  - `minibatch_size`；
  - `num_epochs`；
  - `.env_runners(num_env_runners, num_envs_per_env_runner)`；
  - `.learners(num_learners, num_gpus_per_learner)`；
  - `rl_module()`和Connector graph identity；
  - exact seed/environment/policy/evaluation streams。
- TrainingRunner responsibilities: compile plan、validate capability、CPU smoke、fixed-budget train、checkpoint commit、weights-only export request、failure taxonomy；不拥有sealed evaluator。
- Policy export/evaluation responsibilities: checkpoint entries拒绝absolute/`..`/symlink；manifest/request实现domain-separated signing bytes、trusted key/clock/revocation/replay、repository identity/lineage resolution；success/failure/Q mappings全部由terminal evidence派生。
- Checkpoint boundary: RLlib checkpoint官方结构含pickle/class constructor和algorithm/subcomponent state，只允许同一trusted trainer profile内crash recovery；跨profile和sealed evaluator只接收经one-shot trusted exporter核验的finite weights-only safetensors与strict manifest。
- Required policies: feed-forward PPO、stateful recurrent PPO、independent PPO、CTDE-PPO；DQN/SAC/TD3只在对应paper-replication contract启用。
- Red tests: actor读取critic-only state、multi-agent缺CTDE、violation但audit pass、failed assertion但smoke pass、old ModelV2/Policy/RolloutWorker config、wrong seed count、budget extension、checkpoint path traversal/locator跨profile、伪签名/replay、success branch与Q矛盾、export含critic fields。
- `R07-CORE` proof: CartPole仅作engineering smoke；production-shape RemoteEnv fixture完成config→one-iteration train→checkpoint→fresh-process restart→weights-only export→trusted evaluator load，并用synthetic ten-slot ledger验证success/failure派生；不声称Taxi或策略效果。
- `R07-TAXI-E2E` proof: 对R12-E2E冻结candidate执行Taxi训练→restart→export→evaluate tracer，生成exact-profile terminal records和safetensors manifest；未通过时留terminal failure且不关闭R07。

### 4.11 R08：实现确定性统计深模块

- Blocked by: R05。
- Input interface: signed terminal outcome rows + frozen analysis manifest；caller不能提交point estimate、CI、p-value、rank、rejected或non-inferior结论。
- Implementation requirements:
  - RFC 8785 JCS counter stream；
  - SHA-256 rejection sampling，无modulo bias；
  - five canonical counter vectors；
  - fixed inverse empirical-CDF quantile vector；
  - suite→variant→pair→seed nesting；
  - invalid-to-zero和outcome masks；
  - exact McNemar diagnostics；
  - 100,000-replicate effect/null-centered streams；
  - Holm adjusted p-values和Bonferroni familywise bounds；
  - design-power与gold-calibration独立stream domains。
- Red tests: duplicate strata、reversed CI、NaN/inf、p/rank/rejection mismatch、wrong Holm family cardinality、interpolated quantile、method-specific bootstrap indices、missing seed silent drop。
- Completion criterion: synthetic fixtures恢复已知effect/family；所有output完全由inputs重算且byte-deterministic。
- Calibration conformance fixture: observed gap必须为`0.75`，四个replicates精确为`[0.5,0.75,0.75,0.75]`，按唯一inverse empirical-CDF得到`Q_0.025=0.5`；任一bytes/order/quantile差异均失败。

### 4.12 R09：实现 freeze gate 和 experiment coordinator

- Blocked by: R05/R08。R19–R22提供真实preflight inputs，但不阻塞使用synthetic fixtures实现和验证coordinator。
- Coordinator是Compiler内部模块；它读取artifact refs并产出derived `ExperimentPreflightReport`。
- Freeze gate必须检查完整closed set，而非任意checks数组：plan、source commit、profiles、task cards、methods、eligibility、pair/seed ledger、design power、calibrations、keys、sealed handshake、runner dry run、RemoteEnv vectors、analysis fixtures、replacement policy。
- `READY` 只能由全部required predicates计算；任一false输出typed blocker且不创建Run。
- Red tests: zero checks READY、failed calibration READY、missing task card READY、wrong n_pair、unknown key、post-freeze manifest mutation。
- Completion criterion: preflight报告可由第三方从refs重放；caller无法指定verdict。

### 4.13 R10：实现隔离 redactor、fixed publisher 和 release gate

- Blocked by: R08/R09；可先用synthetic fixtures实现，真实public bundle必须等待R26 terminal coverage和R27 analysis。
- Redactor input: restricted result bundle + taint registry；output只允许strict `PublicReportBundle`和signed `RedactionAttestation`。
- Publisher input: 上述两个工件；publisher不得挂载sealed store/taint registry，不接受自由Markdown或任意列。
- Required scans: field-level provenance、high-entropy raw/base64/hex、low-entropy answer hash dictionary、secret/path/URI、symlink、extra file/column、Markdown injection。
- Public allowlist:
  - `confirmatory_report.md`；
  - `redacted_manifest.json`；
  - `tables/primary_outcomes.csv`；
  - `tables/secondary_outcomes.csv`；
  - `tables/protocol_deviations.csv`。
- Release result必须从fixed closed checks计算，不能接受`released=true`。
- Completion criterion: 每种禁止字段/编码均有负例；合法0/1/yes、count和metric不会被误拒；输出可从bundle确定性重渲染。

### 4.14 R11：CI、supply-chain和release automation

- Blocked by: R02–R10。
- Required push/PR jobs:
  - core lock/install；
  - ruff；
  - pyright；
  - targeted unit/contract/security/integration tests；
  - provenance/profile metadata；
  - secret scan；
  - dependency/license scan；
  - build wheel/sdist并扫描内容；
  - artifact schema/rebuild checks。
- Required manual jobs:
  - profile import-smoke matrix；
  - clean OCI build、SBOM、vulnerability scan；
  - runtime canary；
  - pilot/experiment submission；
  - release bundle verification。
- Workflow rules: actions用full commit pin、最小permissions、无persisted write credential、所有输出有retention和hash、失败不自动retry。
- Release attestation: 只对已redact并通过bundle verifier的final bytes生成artifact attestation；生成成功和独立`gh attestation verify`成功分别记录，workflow green不能替代subject digest/signature verification。
- Completion criterion: clean fork/checkout可运行metadata CI；手动heavy workflow产出独立attestation，不把其成功自动升级为实验完成或owner release approval。

### 4.15 旧 ticket 与恢复工作包映射

| 旧 ticket | 不能沿用的完成判断 | 恢复工作包 | 新关闭证据 |
|---|---|---|---|
| T18 | config/schema objects和mock smoke存在 | R07、R12–R16 | official RLlib production runner + six-suite runtime evidence |
| T19 | export/evaluation request模型可构造 | R07、R22、R27 | real checkpoint commitment→safetensors→sealed ten-seed evaluation |
| T20 | 360个对象数量正确 | R05、R20、R23 | exact canonical Cartesian grid、30 reviewed task cards、frozen pair ledger |
| T21 | 六个名称出现在pair模型 | R05、R17A、R17B | six executable common-backend methods和capability/transcript隔离 |
| T22 | 单一no-evidence ledger存在 | R14、R18 | six component ablations + MPE2 info ablation完整paired bindings |
| T23 | replication manifest列出名称 | R17A、R17B、R19、R26 | three source/licensing matrices、paper-spec implementations、terminal/deviation records |
| T24 | result schema可保存CI/p-value | R08、R21、R22、R27 | production counter/bootstrap/calibration/design-power重算 |
| T25 | `frozen=true`对象可构造 | R09、R23、R26 | derived preflight READY + complete terminal intention matrix |
| T26 | redaction引用模型可构造 | R10、R27 | isolated taint closure、signed attestation、fixed rendered files |
| T27 | release check names齐全 | R10、R11、R27 | clean build/CI/scans/cards/reproduction/public verifier全通过 |

旧 ticket 的历史代码和测试可作为反例来源，但不能作为恢复包的验收权威。恢复包完成后是否关闭/reclose tracker仍受R01和owner GitHub授权约束。

### 4.16 目标测试布局与执行顺序

优先在现有测试文件中替换浅测试；只有新的深行为没有合适位置时才新增文件。

| Recovery area | 首选测试路径 | 必须覆盖 |
|---|---|---|
| provenance | `tests/contract/test_provenance_*.py` | clean declarations、restricted import/vendor/archive/index、external attestation |
| benchmark/methods | `tests/contract/test_benchmark_suites.py`、`test_generation_methods.py` | exact grid、route、pair、eligibility、capability view |
| ablations | `tests/contract/test_ablation_ledger.py`、`test_ablation_gates.py` | six single-diff plans、144 cells、post-terminal binding |
| RLlib | `tests/training/test_rllib_configs.py`、`test_information_boundaries.py`、`test_cpu_smoke.py` | official config translation、actor/critic、budget/seeds、checkpoint/export |
| statistics | `tests/contract/test_statistics.py` + `test_analysis_counter.py` | vectors、quantile、nested bootstrap、Holm、derived decisions |
| design/calibration | `tests/experiments/test_design_power.py`、`test_gold_calibration.py` | common random prefix、selection thresholds、paired calibration |
| preflight/matrix | `tests/experiments/test_preflight.py`、`test_matrix_completeness.py` | closed gate set、READY/BLOCKED、terminal slot coverage |
| redaction/release | `tests/release/test_redactor.py`、`test_publisher.py`、`test_release_bundle.py` | taint、allowlist、deterministic renderer、clean reconstruction |
| vertical slices | `tests/end_to_end/test_taxi_compiler_training_slice.py` 后按suite扩展 | SQLite restart、fixed commit、RemoteEnv、sealed、train/export/evaluate |

单个slice执行顺序固定为：一个最小红测试 → 直接相关unit/contract/security → 一个直接integration/end-to-end → static checks。只有R04、release candidate和最终integration允许运行全仓suite。

---

## 5. 垂直 tracer 和 suite 扩展

### 5.1 R12：Taxi MDP compiler tracer

- Sub-gates: `R12-DET` blocked by R05、R05A-BUILD、R06；`R12-E2E` blocked by R12-DET和R05A-ATTACH。只有两者通过才关闭R12。
- Input: 已批准Taxi TaskContract、SYNTHESIS/GENERATE suite manifest、只含公开规则/API的Allowed Evidence。
- Generation side: 禁止官方Taxi源码、transition table和sealed gold；生成完整有限环境。
- Validation: strict schema、API、seed、transition totality、property/metamorphic、public behavioral tests。
- Sealed side: 独立Taxi-v4 gold对text/formal/API/hidden behavior四门评分。
- Output: candidate bundle、E2E request/verdict、terminal record/result、execution attestation。
- Target files: `environment_implementation.py`、`environment_sandbox.py`、`remote_env*.py`、`suite_adapters.py`、`sealed_evaluation.py`、`adapters.py`、`repository.py`和Taxi profile-local worker；不得复用硬编码CartPole worker冒充Taxi。
- Red tests: trainer读取official Taxi source/transition table、candidate/gold principal/session复用、in-process backend跨profile、unbuilt profile launch、wrong specified head。
- `R12-DET` completion: 真实Taxi profile、RemoteEnv、SQLite/restart/fixed-commit、candidate/gold worker权限互斥和确定性fixtures可重放；不要求LocalLlmRuntime。
- `R12-E2E` completion: 在verified attach上执行真实generation，candidate通过public validation和single sealed E2E gate，且工件链从specified head可重放；未通过时保留terminal failure，不关闭R12。

### 5.2 R13：MiniGrid Memory POMDP tracer

- Sub-gates: `R13-DET` blocked by R05A-BUILD、R07-CORE、R12-DET；`R13-E2E` blocked by R13-DET、R05A-ATTACH、R12-E2E。前者可与其他suite DET并行，不被LLM attach阻塞；两者均通过才得关闭R13/`suite_impl[memory_pomdp]`。
- Reuse/Compose官方MiniGrid Memory内核；actor只读局部observation/history。
- 验证MissionSpace adapter、terminated/truncated、history lags、recurrent module state和seed determinism。
- DET completion: official API/seed/history/recurrent-state和隐藏状态泄漏负例通过；E2E completion: generated candidate可train/export/evaluate并有terminal lineage。

### 5.3 R14：MPE2 full-state MG 与 native-local POSG

- Sub-gates: `R14-DET` blocked by R05A-BUILD、R07-CORE、R12-DET和official MPE2 runtime；`R14-E2E` blocked by R14-DET、R05A-ATTACH、R12-E2E。前者不被LLM attach阻塞；两者均通过才关闭R14/`suite_impl[mpe2_full_state_mg]`。
- 官方事实合同：native observation 18D、global state 54D。
- Full condition: actor/critic按预注册full-state MG adaptation获取54D state。
- Native condition: actor只读18D local observation，global state只给centralized critic。
- 两condition除actor input capability外保持相同policy shapes、optimizer、budget和seeds。
- DET completion: official `state()`逐元素复用、native actor无state capability、信息结构负例通过；E2E completion: 两condition的generated candidate可train/export/evaluate，post-terminal binding后才分析paired contrast。

### 5.4 R15：SMACv2 POSG

- Sub-gates: `R15-DET` blocked by R05A-BUILD、R07-CORE、R12-DET和D04 asset gate；`R15-E2E` blocked by R15-DET、R05A-ATTACH、R12-E2E。其他suite DET不等D04；两者均通过才关闭R15/`suite_impl[smacv2_posg]`。
- Manual gate: SC2 binary/maps exact build、license和content hash未解决时保持`WAITING_ASSET`。
- Reuse/Compose官方SMACv2 battle core、action masks、decentralized actor、centralized critic。
- DET completion: official battle core/action mask/API、crash/reconnect/step identity和asset provenance通过；E2E completion: generated candidate可train/export并按50 battles/seed合同evaluate。

### 5.5 R16A：MetaDrive POMDP

- Sub-gates: `R16A-DET` blocked by R05A-BUILD、R07-CORE、R12-DET和ScenarioNet dataset owner gate；`R16A-E2E` blocked by R16A-DET、R05A-ATTACH、R12-E2E。前者不被LLM attach阻塞；两者均通过才关闭R16A/`suite_impl[metadrive_pomdp]`。
- 固定MetaDrive/ScenarioNet revision和partition、POMDP sensor view；道路/物理只复用不重写；100 episodes/seed。
- Red tests: dev/sealed scenario overlap、scenario hash drift、physics reimplementation route、global state泄漏给actor。
- DET completion: partition不重叠、dataset内容hash/license、sensor/API/physics reuse和RemoteEnv通过；E2E completion: generated candidate经统一TrainingRunner train/export/evaluate 100 episodes/seed。

### 5.6 R16B：CityLearn POSG

- Sub-gates: `R16B-DET` blocked by R05A-BUILD、R07-CORE、R12-DET和CityLearn dataset/schema owner gate；`R16B-E2E` blocked by R16B-DET、R05A-ATTACH、R12-E2E。前者不被LLM attach阻塞；两者均通过才关闭R16B/`suite_impl[citylearn_posg]`。
- 固定dataset/schema/held-out period、multi-agent observation/reward和完整period评价。
- Red tests: train/held-out overlap、schema mutation、agent keyset drift、partial period冒充完整evaluation。
- DET completion: dataset/schema/hash/license、held-out period隔离、agent keyset和RemoteEnv通过；E2E completion: generated candidate经统一TrainingRunner train/export并评估完整held-out period。

### 5.7 R17A：六方法共同后端的可并行实现

- Blocked by: 每个`R17A[s]`只依赖对应`suite_impl[s]`、R19和R20；R12提供AutoMarkov Taxi最小方法路径。R15、R16A或R16B因owner/asset gate保持`WAITING_ASSET`时，均不阻塞其他suite/method实现。
- 所有方法共享model checkpoint、sampling、task card、pair、retrieval/tool/HITL/training budgets。
- `single_llm`: 单次直接生成，无隐藏修复。
- `react_executor`: 六suite×五variant×两track全部60 cells。
- A-LAMP/Agent2: paper-spec reimplementation，公开差异进入deviation ledger。
- Agent2World: clean controlled inference-time implementation，不port/vendor受限代码，不执行SFT。
- AutoMarkov: 完整编译器路线。
- Completion criterion: 每个非blocked suite的六方法capability view、budget、cache和transcript隔离可机械审计；运行后eligibility不变。该状态只能标为`PARTIAL_METHOD_IMPLEMENTATION`，不得声明六suite完成。

### 5.8 R17B：六suite方法合同最终关闭

- Blocked by: R12–R16B六个`suite_impl[s]`和全部`R17A[s]`；任一suite的owner/asset gate未解决时不得关闭。
- Required closure: 六suite的六方法路径均取得同等级的runtime/terminal/capability证据；60个ReAct cells和所有method eligibility masks可从manifests重算。
- Completion criterion: 六suite六方法全部实现或pre-run `N/A`有合法paper-contract证据，且R17A的partial状态升级为完整method contract；任何`WAITING_ASSET`仍存在时不得关闭。

### 5.9 R18：六项组件消融

- Blocked by: R07、R08、R17B，以及public-validation/lifecycle omission events。
- 精确144 cells：六suite×四variants×AUTO×六ablations；每cell `n_pair` slots。
- 每项只有一个登记capability diff；其余gates、budget、seeds和outcome evaluator保持不变。
- full run不重跑；双方terminal后才签发`AblationReferenceBinding`。
- Completion criterion: 六条合法projection可达，所有unknown/multi-diff/masked-required-gate负例失败。

---

## 6. 学术输入、预注册与实验前门禁

### 6.1 R19：论文 passport 与 upstream/licensing 刷新

- 为30篇必读论文分别创建结构化passport：版本、RQ、算法、inputs/outputs、benchmarks、training、metrics、ablations、official code、license、limitations、AutoMarkov映射。
- 每项外部实现必须进入typed upstream manifest或显式`blocked_unresolved`。
- Agent2候选仓库必须核验作者关系、exact commit、代码/数据/模型license；无LICENSE时只可读研究，不能集成或发布。
- 在作者/权利人确认前，“只读研究”精确限制为网页和metadata审计：不clone/复制实质代码、不执行、不构建、不进入模型上下文、不生成diff、不集成或发布。
- Agent2World许可采用fixed commit根`LICENSE`的Research/Evaluation Only条款；README中的Apache badge与根LICENSE冲突，必须记录冲突并以根LICENSE fail closed，网站CC BY-SA也不覆盖仓库代码。
- A-LAMP/Agent2无可用官方代码时保持paper-spec；发现官方代码也不能在许可审查前自动升级。
- Completion criterion: 三条replication suite各有source matrix和deviation template；30个passport无空的load-bearing字段。

### 6.2 R20：三十份 task cards 与语义等价审查

- 每suite五variants，共30份。
- 两名独立领域审查者确认v1–v4语义等价；v5只删除最多三个预注册高影响点。
- 审查者只看task/gold contract，不看方法结果。
- 输出: task-card manifest、text hashes、allowed/blocked sources、sealed commitments、review receipts。
- Completion criterion: 任一语义漂移先修task card，不扩大gold tolerance。

### 6.3 R21A/R21B：Nuisance source freeze 与 DesignPowerManifest/Report

- `R21A` blocked by: D11。它必须在R25前生成signed/content-addressed `NuisanceSourceDeclaration`，closed selector仅允许`PUBLIC_DEV | LITERATURE_PRIOR | CONSERVATIVE_MAX_VARIANCE`。选Public Dev时只冻结不重叠的suite/variant/seed/scenario partitions、probe algorithm/config、允许字段/单位、coverage和missing policy，不得含未来outcome值；选先验时冻结citation/evidence IDs；选保守边界时冻结closed构造规则。
- `R21B` blocked by: R08、R18、R19、R21A；只当R21A选`PUBLIC_DEV`时另blocked by `R25-ALL`。R21A既不读取R25结果也不被R25阻塞，因而无循环。
- Candidate set: `n_pair ∈ {20,24,30,40,60,80}`。
- 2,000 deterministic datasets；每gate至少10,000 production-equivalent bootstraps。
- Alternative: E2E difference `+0.10`，policy difference `0.00`；null boundaries `0.00/-0.05`。
- Nuisance inputs只来自non-overlapping Public Dev、正式一手先验或保守最大方差边界。
- Selection: 最小candidate同时满足joint-power lower≥0.80、各marginal lower≥0.90、null false-success upper≤0.025。
- 无合格值或预算不足: `BLOCKED_DESIGN`，不得创建Run。
- Completion criterion: R21A声明和R21B的独立实现/report均可byte-reproduce，selected n_pair与source selector、exact evidence rows绑定；两者通过才关闭R21。

### 6.4 R22：GoldScoreCalibration

- Blocked by: R12–R16B的六suite实现、R19、`R24-ALL`，以及所需sealed/reference runtime authority。各suite可在`R24[s]`后准备calibration证据，但R22全局关闭要求七个condition都有terminal gate。
- 至少七份独立signed calibration：六个main suites各一份，加`mpe2_native_local_posg` condition一份；若后续新增不同gold environment/reward/adapter condition，必须新增对应calibration，不能复用名字相近的记录。
- reference/random在相同pilot seed×episode/scenario上paired评价。
- 100,000 nested counter bootstrap；direction-adjusted gap 97.5% LCB严格大于positive `min_reference_random_gap`。
- calibration失败阻断suite，不把candidate `Q_gate`置0来掩盖。
- MPE2信息消融跨condition统一使用full-state calibration公共尺度。
- Completion criterion: 六个main suite calibration加MPE2 native condition gate全部通过或明确blocked，且generation side只见commitment/aggregate verdict；跨condition policy estimand只使用full-state calibration公共尺度。

### 6.5 R23：Pre-run freeze

- Blocked by: R05A、R07、R09–R11、R17B、R18、R20–R22、`R24-ALL`、`R25-ALL`，以及D04–D12中适用的owner/manual gates。
- Freeze: plan revision、source commit、30 task cards、suite/method/ablation manifests、selected n_pair、pair IDs、ten RL seeds、budgets、keys、replacement policy、outcome masks、Holm families、runner policy、RemoteEnv codec、analysis hashes。
- Dry runs: nonterminal runner job、Run-terminal job、policy export私有descriptor path、sealed request handshake、redactor/publisher fixtures。
- Freeze后任一byte变更产生新experiment version；旧/新run family不混合。
- Completion criterion: `automarkov experiment preflight`（R09实现后）返回derived `READY`，并保存signed/content-addressed report。

---

## 7. 实验执行手册

### 7.1 命令状态账本

| 命令 | 当前状态 | 何时允许使用 |
|---|---|---|
| `uv run --locked automarkov verify-provenance --repository-root .` | `CURRENT/BROKEN` | R02修复验证 |
| `uv run --locked automarkov pilot run --manifest ...` | `CURRENT/ENGINEERING_ONLY` | nonconfirmatory pilot |
| `uv run --locked automarkov compile ...` | `CURRENT/WALKING_SKELETON` | 不得声称完整compile |
| `automarkov execution submit` | `PLANNED` | R06/R09完成后；唯一publication-grade job submission入口 |
| `automarkov experiment preflight` | `PLANNED` | R09/R23完成后 |
| `automarkov experiment generate` | `PLANNED` | freeze READY后 |
| `automarkov experiment train` | `PLANNED` | candidate E2EValid后 |
| `automarkov experiment export-policy` | `PLANNED` | successful training slot后 |
| `automarkov experiment evaluate` | `PLANNED` | exact 10 seed branch records齐全后的sealed policy evaluation |
| `automarkov experiment ablate` | `PLANNED` | component-ablation RUN slot |
| `automarkov experiment bind-ablation-reference` | `PLANNED` | full/ablation双方terminal后 |
| `automarkov experiment ablate-mpe2-information` | `PLANNED` | MPE2 native information-structure slot |
| `automarkov experiment e2e-gate` | `PLANNED` | candidate freeze后、training前的single sealed E2E gate |
| `automarkov experiment evaluate-clarification` | `PLANNED` | AUTO/v5 terminal result/attestation后的sealed clarification evaluation |
| `automarkov experiment analyze` | `PLANNED` | intention matrix terminal coverage闭合后 |

当前accepted experiment plan没有定义`automarkov experiment publish`。`Compiler.package(run_id,head)`只打包单Run；实验级publisher由analysis完成后的内部coordinator提交独立fixed-commit packaging job。若要增加public publish CLI，必须先修订规格和命令合同，不能由实现者自行添加。

当前计划也没有为DesignPower和GoldScoreCalibration定义独立CLI。R21/R22必须先冻结其fixed-commit payload schema、command和output contracts；在该设计完成前，文档不得虚构`design-power`或`calibrate`命令。

### 7.2 R24：Engineering pilots

- Phase dependencies: `R24-DET[s]`的profile import/reset/step/API/seed和lightweight learning phases只依赖R05A-BUILD、R07-CORE和对应`suite_det[s]`；`R24-E2E[s]`的generation/method/security phases另依赖R05A-ATTACH、对应`suite_impl[s]`和`R17A[s]`。因此LLM attach未就绪不阻塞或取消R24-DET；两子门通过才关闭`R24[s]`，但它不等R17B/R18或其他suite的asset。blocked suite只记录未满足gate，不伪造pilot。R18所需的ablation-specific pilot可后续追加，不倒置阻塞base suite pilot。
- Purpose: 验证profile/runtime/transport，不估计confirmatory outcome。
- Branch order: 不依赖LLM的Taxi CPU smoke、各已就绪suite import/reset/step、lightweight learning probe和sealed transport dry run可并行先行；local-LLM security/identity canary通过后，另运行one fixed-commit Taxi train/export/evaluate和各suite generation/method probes。这不是“canary失败就停止全序列”的串行链。
- vLLM gate: 使用`LocalLlmRuntime`的pilot branch必须绑定R05A-ATTACH的local-LLM security report；重验proxy route allowlist、principal-bound transport、internal-port isolation/default-deny egress、text-only schema、`trust_remote_code=false`、tool/runtime-LoRA disabled、cache root/source/owner/mode/writable-principal/launch-state和model/tokenizer/weights identity。对每个禁止route/feature及HTTP/`data:`/`file:` media运行负例，验证media无egress，并重做真实generation/tokenization canary；重新扫描持久化argv projection不含secret/private locator。暂时connection/service不可用只标记当前LLM-dependent branch为`WAITING_RUNTIME`；任一security/identity/binding/schema/protocol谓词失败则该attempt记`FAIL`并生成terminal `FAILED`/`protocol_integrity_violation`，修正后必须使用new runtime identity/new attempt，不得resume原attempt。两类情况都只停止LLM-dependent branch；import/reset/step、profile metadata、environment API/seed等不依赖LLM的确定性branch继续运行并各自留存terminal evidence。
- Official environment checks: Gymnasium adapter至少通过官方`check_env`及deterministic seed checks；PettingZoo adapter至少通过`api_test`或`parallel_api_test`及seed checks；这些只证明接口/seed合同，不替代AutoMarkov行为和sealed gates。
- Monitoring: process alive、phase、elapsed/wall budget、stdout/stderr bytes、metrics rows、env steps、artifact count/hash、heartbeat freshness。
- Retry: 不自动retry；失败先分类为code/runtime/asset/protocol/budget，再由新engineering job处理。
- Completion criterion: 每个attempted pilot都有terminal record、attestation、compact report和nonconfirmatory label，但failure/`WAITING_*`只完成证据覆盖，不关闭readiness gate。冻结`PilotReadinessMatrix`对每个`suite_id × required_phase`记录`required`、`phase_group=DET|E2E`、exact success predicate、terminal artifact/attestation IDs和`PASS | FAIL | WAITING | MISSING`；DET组required rows全PASS即可关闭`R24-DET[s]`，不等E2E组。`R24[s]`只在DET/E2E的全部required rows为`PASS`时关闭，`R24-ALL`只在六suite的`R24[s]`均关闭、所有LLM-dependent required rows完成network/feature/cache/identity security gate且无`FAIL/WAITING/MISSING`时成立。

### 7.3 R25：Public Dev 与恢复演练

- Blocked by: 对应suite的`R24[s]`和已冻结的R21A `NuisanceSourceDeclaration`。`R25[s]`可逐suite执行，不等待其他asset；六suite failure/recovery matrix均有terminal证据后才得记为`R25-ALL`。
- 运行public validation和learning probe，验证nearest-cause rollback。
- 注入crash、timeout、schema drift、hash mismatch、runtime replacement、approval revocation。
- 证明restart/resume只从specified head恢复，pre-generation replacement与post-generation slot failure按policy区分。
- Public Dev结果可以调试实现，但不得进入design nuisance以外的confirmatory分析；任何使用必须有预注册evidence row。
- Completion criterion: 对应suite的failure/recovery matrix每格均有预期terminal状态和artifact lineage；全部`s`闭合后才计为`R25-ALL`。

### 7.4 R26：Confirmatory matrix

设R21最终冻结`n=n_pair`，执行前必须从manifests重算以下覆盖；公式是验收，不是完成声明：

| 范围 | 公式 | 解释 |
|---|---:|---|
| 主intention grid | `6×5×2×6×n = 360n` | intention slots；包含pre-run N/A，不等于attempts |
| 实际主生成 | `N_RUN×n` | 只有RUN cells启动 |
| ReAct强制覆盖 | `6×5×2×n = 60n` | 全部RUN |
| AUTO完整语义层 | `6×4×6×n = 144n` | v1–v4 E2E/Q masks |
| AUTO/v5 clarification | `6×1×6×n = 36n` | 只评价SafeClarificationRequired |
| HITL机制层 | `6×5×6×n = 180n` | 与AUTO分开分析 |
| co-primary paired units | `6×4×n = 24n` | AutoMarkov vs ReAct；对应`48n` method observations |
| 主矩阵最大RL slots | `324×n×10 = 3240n` | 完整语义RUN candidates全部有效时的上界 |
| 六项组件消融 | `6×4×6×n = 144n` | 全部RUN；最大`1440n` RL slots |
| MPE2 native新增生成 | `4n` | full的`4n`复用主矩阵 |
| MPE2两condition observations | `8n` | 最大`80n` RL seed slots |
| 新增generation上界 | `360n+144n+4n = 508n` | 不含三项paper replications |

报告必须分列`intention/RUN/N/A/attempt/terminal`五个计数；任何把`360n`写成实际完成数的报告无效。

- Start condition: R23 READY且owner明确批准D05/D12预算和执行。
- Slot identity: `(experiment,suite,variant,track,method,pair)`；有效candidate再绑定十个RL seeds。
- Scheduler只提交manifest中的`RUN`；`N/A`不启动且保留pre-run reason。
- No retry: crash、timeout、OOM、sandbox/protocol failure保留原slot terminal failure；不得换pair/seed。
- AUTO/v5只评分`SafeClarificationRequired`，不训练、不进入E2E/Q。
- Generation/repair不得读取任何sealed verdict、gold、method peer output或aggregate result。
- 三项独立paper replication suites也在本阶段执行，但与六suite common-backend matrix分开建账和报告：
  - A-LAMP paper-matched/paper-spec workload；
  - Agent2 paper-matched/paper-spec workload；
  - Agent2World许可允许的restricted upstream research evaluation与仓库内clean controlled inference-time workload分列；SFT保持`DEFERRED_LICENSE_AND_COMPUTE_REVIEW`。
- paper replication使用各自官方任务、算法、工具、模型和评价合同；任何替换都进入deviation ledger，不能用common-backend结果冒充paper-matched reproduction。
- Completion criterion: main intention ledger和三条replication ledgers的每个slot恰有N/A/deferred或一个terminal attempt；没有silent drop、extra attempt或mutable manifest。

### 7.5 R27：Analysis、复现与交付

- Blocked by: R08、R10、R11、R26完整terminal coverage和D13/D14。
- Analysis principal无generation capability，只读signed terminal artifacts和post-terminal bindings。
- Primary output: E2EValid和gate-aware policy score的AutoMarkov-vs-ReAct预注册bounds。
- Secondary: other baselines、AUTO/HITL interaction、six ablations、MPE2 info structure、conditional policy quality。
- 每个结论报告effect、CI、raw N、24 strata/cluster counts、failure/deviation counts；仅登记family报告adjusted p-value。
- Independent reproduction:
  1. clean checkout exact commit；
  2. verify source/profile/manifest hashes；
  3. 在受控reproduction capsule权限内，不重训练，从signed policy exports/evaluation records和seed-level terminal rows重建主表；
  4. 从受控metrics和terminal slots重建曲线；
  5. 独立重跑analysis并比较byte hashes；
  6. 若获预算，再抽预注册subset做environment-sensitive rerun。
- Public five-file bundle只支持fixed-render/hash/schema一致性验证，不包含seed-level受控输入，不能单独声称可重算统计。若要公开独立重算capsule，必须通过新的privacy/license/redaction审查并修订public allowlist。
- Completion criterion: 获授权的independent verifier无需raw checkpoint、sealed payload或作者私有路径即可从受控capsule重建全部public tables；普通public consumer可验证五文件bundle的schema、hash和内部一致性。

---

## 8. 失败、监控和停止政策

### 8.1 统一失败分类

| Class | 例子 | Slot结果 | 可否自动retry |
|---|---|---|---|
| `INPUT_INVALID` | manifest/schema/hash/approval不匹配 | preflight reject，无run或按已启动阶段terminal failure | 否 |
| `WAITING_RUNTIME` | image/service/handshake暂不可用 | waiting event + exact resume gate | 否 |
| `WAITING_ASSET` | SC2 maps/dataset/license缺失 | waiting event + authority | 否 |
| `BLOCKED` | credential、budget、owner/legal决定 | blocked event | 否 |
| `PROCESS_FAILURE` | nonzero、crash、OOM | terminal failure，保留denominator | 否 |
| `TIMEOUT` | hard wall limit | terminal failure，保留denominator | 否 |
| `INTEGRITY_FAILURE` | sealed leak、signature/replay、manual repair | FAILED，隔离run family | 否 |
| `SCIENTIFIC_NEGATIVE` | E2E=false、low return、gate未过 | 有效研究结果 | 不得retry |

机械研究映射还必须满足：

- pre-run `N/A`不生成、不记失败，但保留eligibility evidence；
- E2E mask内crash/timeout/missing artifact/sandbox failure固定`E2EValid=0`；
- 四门合法verdict任一false进入terminal `PARTIAL`、`E2EValid=0`，policy为`missing_by_design`；
- AUTO/v5合法false或evaluation failure固定`SafeClarificationRequired=0`，不进入E2E/Q；
- calibration failure阻断整个suite，不映射为candidate `Q=0`；
- 任一training/export seed失败或十seed不完整固定`GoldPolicyEvaluationValid=0,Q_gate=0`；
- training启动后的timeout/crash/missing result使用`post_training_terminal`列出existing/missing seed complement；
- exact approval被撤销时保留原slot并映射E2E/Q为0，只能按policy创建nonconfirmatory child；
- sealed contamination/integrity failure隔离run family并保留intention/deviation，不能普通重跑或静默删除。

### 8.2 监控字段

每个长任务至少发布：job/run ID、exact commit/profile、PID/process state、phase、start/last-heartbeat、elapsed/limit、completed/expected/terminal slot coverage、event sequence/head hash、artifact bytes/count、last durable artifact/checkpoint及hash、generation/pair/RL-seed/episode/battle/scenario进度、stdout/stderr capped bytes及truncation、CPU/RSS/GPU、LLM queue/tokens、Tavily usage、export/evaluation branch coverage、schema/profile/input/network-policy drift、anomaly首次时间、operator decision和terminal reason。不得只报告“正在运行”。

### 8.3 停止条件

- hard timeout、sealed/integrity violation、manifest identity drift立即停止对应job；
- 普通低性能、不显著或负结果不是停止理由；
- confirmatory执行不得按中间outcome做optional stopping；
- 全局存储/安全事故只由预注册incident policy停止新提交，已terminal observations保留。

“禁止retry”特指禁止替换或重跑confirmatory slot。以下transport内部重试只有在预注册policy允许时合法：provider request的bounded retry；clarification在deadline内对exact same request ID/bytes的idempotent retry。soft stall只报警；只有hard timeout或安全/完整性违规允许自动终止process。

---

## 9. 项目交付合同

### 9.1 内部完整交付

保留在ignored artifact root：全部run manifests、events、terminal records、attestations、candidate artifacts、metrics、policy exports、sealed commitments、deviations、analysis manifests和raw private reports。sealed payload、credential、oracle answer和private checkpoint不进入普通内部bundle。

### 9.2 公开交付

公开文件只由R10 fixed publisher生成。除规格白名单外，不发布完整run目录、raw web capture、checkpoint、trace、sealed identity/hash/nonce/locator、Agent2World restricted code/prompt/test或私有数据。

同时交付：

- source commit/tag和clean-tree证明；
- root/project/profile lockfiles；
- SBOM、license inventory、third-party notices；
- Software/Data/Model Cards；
- preregistration和deviation log；
- reproducibility report；
- public result files及其hash；
- CI/workflow run identities和release provenance；
- 已知限制、blocked/deferred清单。

五文件`artifacts/public_reports/<experiment_id>/`与开源release assets必须分根。README、LICENSE、`CITATION.cff`、SBOM、license inventory/notices、Software/Data/Model Cards、source/build provenance、signatures/checksums和受控reproduction capsule不得偷偷写入五文件白名单根；它们使用版本化release bundle目录，并分别通过license/privacy/provenance审查。

### 9.3 声明门禁

| 声明 | 最低证据 |
|---|---|
| “组件已实现” | production adapter可由public seam到达 + focused tests |
| “runtime可用” | fresh exact identity、canary、profile attestation |
| “实验完成” | 完整intention ledger + terminal coverage + analysis artifacts |
| “可复现” | 独立clean-checkout重建或rerun报告 |
| “论文复现” | paper-matched contract、official assets/license、deviation closure |
| “release ready” | R11/R10全门禁 + clean build + public bundle verifier |
| “已发布” | 上述证据 + owner明确发布授权和remote identity |

### 9.4 Git、review和tracker写入

- 每次Git写入前向owner展示：branch、当前parent SHA、author、拟用commit message、完整scope和已知未跟踪文件；取得明确批准后才能创建非force child commit。
- commit只能包含当前工作包文件；不得吸收`.codegraph/`、runtime artifacts、raw outputs、secret或无关用户改动。
- push前再次核对local parent/local SHA/remote parent；push后核对remote SHA精确等于local SHA。
- 不使用force push，不擅自创建PR、tag、release或关闭issue。
- native Codex review是实现后的独立门禁；执行review的进程不能递归再启动review。
- issue更新必须引用exact commit、命令、artifact/report和残余blocker；“tests pass”必须附测试scope/count，不能只贴exit code。

---

## 10. Owner 决策与人工门禁

这些决定不能由Coding Agent推断；未决时对应工作包保持`BLOCKED`。

| ID | 决定 | 推荐选项 | 需要owner提供 | 阻塞 |
|---|---|---|---|---|
| D01 | 同仓中央source-hash allowlist/字符串扫描是否替换 | 使用Git tree + typed ingress policy +外部signed release provenance | 接受设计方向；若改变ADR则批准ADR讨论 | R02 |
| D02 | 按R00矩阵处置所有closed-but-unproven tickets | 逐项reopen；若需保留历史则创建对应Recovery tickets并链接旧票 | GitHub写入授权和选择 | R01 |
| D03 | Agent2候选仓库是否作为官方source | 先`authorship_unverified + license_unresolved`，只做网页/metadata审计 | 许可/作者证据或保持paper-spec | R19 |
| D04 | SC2 binary/maps来源和研究许可 | 使用官方可审计build；无hash/许可保持WAITING_ASSET | asset locator、license approval | R15/R23 |
| D05 | confirmatory compute budget | design-power选择n_pair后再批准 | CPU/GPU/时长/成本上限 | R23/R26 |
| D06 | runtime签名/审批key与attach authority | 由operator provision，不入仓库；不决定OCI build/registry/issuer | approval/replacement/runtime key IDs、public keys、validity/revocation、principal绑定和approved attach relay/service identity | R05A-ATTACH/R23 |
| D07 | 正式发布目标 | 先私有release candidate，再public remote | repository/tag/registry/DOI目标和授权 | R27 |
| D08 | task-card reviewers与sealed-gold owner | 两名独立domain reviewers + 独立sealed owner | 人员/角色、签名key和conflict声明 | R20/R22 |
| D09 | design-power计算预算 | 与confirmatory预算分开批准 | 2,000 datasets/bootstraps所需CPU/时长上限 | R21 |
| D10 | preregistration registry与公开策略 | time-stamped、read-only registration；必要时embargo | registry、公开/embargo、提交授权 | R23 |
| D11 | calibration/训练先验与nuisance source selector | 先在R21A从`PUBLIC_DEV | LITERATURE_PRIOR | CONSERVATIVE_MAX_VARIANCE`选一并冻结来源合同，再冻结`min_reference_random_gap`和training budget rule | selector、分区/证据/保守规则、数值和批准 | R21A/R21B/R22 |
| D12 | 三项paper replication预算/资产 | 分别批准，不由common matrix预算覆盖 | model/tool/data/compute/license批准 | R19/R26 |
| D13 | independent verifier capsule | 最小signed/redacted seed-level inputs，禁止sealed payload | verifier身份、内容allowlist、传输/保留政策 | R27 |
| D14 | citation/archive元数据 | `CITATION.cff` + archival DOI + release checksums | title/authors/version/DOI授权 | R27 |
| D15 | schema migration/write-version policy | 一个current write version；历史verified-read/migrate/reference-only | 各late schema旧版本处置 | R05–R10 |
| D16 | OCI build authority | 作为唯一authority冻结build host、registry、platform和build/import attestation issuer；与D06的runtime keys不重叠 | registry/issuer/host和有效期 | R05A-BUILD |
| D17 | 未授权`3cea996`处置 | owner审阅后明确ratify，或批准非force revert/follow-up；禁止agent自行改历史 | 保留/撤销决定及Git写入授权 | R01/交付；R00只记录 |
| D18 | MetaDrive/ScenarioNet数据授权 | 冻结dataset locator/revision/hash/license和partition authority | locator/revision/license/owner | R16A |
| D19 | CityLearn数据授权 | 冻结challenge schema/data locator/revision/hash/license和held-out authority | locator/revision/license/owner | R16B |

### 10.1 决策记录格式

每个owner决定必须记录：decision ID、选择、理由、适用experiment/version、批准人、时间、受影响artifacts/tickets、是否需要ADR。凭据值、private locator和sealed内容不进入决定记录。

---

## 11. 每个工作包的交接模板

```markdown
## Work Package <ID>

- Baseline commit:
- Branch/worktree:
- Objective:
- Non-goals:
- Blocked by:
- Owner authority available:
- Public seam:
- Files allowed to change:
- Existing unrelated changes:

### Red evidence
- Command:
- Expected failure:
- Actual failure:

### Implementation
- Interface invariant:
- Adapter behavior:
- Artifact/event changes:
- Migration:
- Security/secret handling:

### Verification
- Focused static:
- Focused tests:
- Runtime check:
- Artifact/hash/schema check:
- Native review target:

### Completion evidence
- Changed files and line counts:
- Test results:
- Runtime artifacts:
- Residual risks:
- Allowed claim:
- Forbidden claim:
```

---

## 12. 最终验收清单

### 12.1 Engineering

- [ ] HEAD、parent、remote SHA和worktree state已记录；
- [ ] ruff、pyright、provenance、focused/full tests通过；
- [ ] 六条public seams均有production和test adapter；
- [ ] 默认CLI可完成Taxi端到端，不是只返回RunId；
- [ ] 六suite official profile worker均有fresh runtime evidence；
- [ ] native Codex review无actionable finding。

### 12.2 Scientific

- [ ] 30 paper passports和source/licensing matrix完成；
- [ ] 30 task cards独立语义审查完成；
- [ ] DesignPowerReport选择并冻结n_pair；
- [ ] 六suite GoldScoreCalibration通过；
- [ ] intention ledger、masks、families、keys、budgets冻结；
- [ ] confirmatory slots无silent drop/替代/retry；
- [ ] effect/CI/N/failure/deviation完整报告；
- [ ] 11类统计/方法谬误检查记录完成。

### 12.3 Reproducibility and release

- [ ] clean checkout能重建environment和analysis配置；
- [ ] 不重训练可从exports/evaluation records重建主表；
- [ ] independent verifier重建public tables及hash；
- [ ] SBOM/license/cards/deviation/reproducibility report一致；
- [ ] redaction负例、publisher allowlist和secret scans通过；
- [ ] release candidate与owner授权分开记录；
- [ ] public remote/tag/registry SHA与local release evidence一致。

只有三组清单全部闭合，项目才可声明`RELEASE_READY`；只有owner完成发布并核对remote identity，才可声明“已发布”。

---

## 13. 直接交付 Go/No-Go 合同

### 13.1 交付状态机

```text
DEVELOPMENT
  -> ENGINEERING_VERIFIED
  -> EXPERIMENT_READY
  -> EXPERIMENT_COMPLETE
  -> REPRODUCTION_VERIFIED
  -> RELEASE_READY
  -> DELIVERY_AUTHORIZED
  -> DELIVERED
```

状态只由下一表证据推进，不能由issue label、commit message、文件存在或人工口头结论推进：

| Transition | 必需输入 | 派生输出 |
|---|---|---|
| `DEVELOPMENT -> ENGINEERING_VERIFIED` | R04报告、clean HEAD、0 lint/type/provenance/test/review findings | signed engineering baseline report |
| `ENGINEERING_VERIFIED -> EXPERIMENT_READY` | R23完整preflight、owner runtime/asset/key/compute gates | signed `READY` report + frozen experiment version |
| `EXPERIMENT_READY -> EXPERIMENT_COMPLETE` | main/ablation/info/replication ledgers全部terminal闭合 | terminal coverage report + immutable analysis inputs |
| `EXPERIMENT_COMPLETE -> REPRODUCTION_VERIFIED` | independent controlled capsule table rebuild、deterministic analysis hash match | independent reproduction report |
| `REPRODUCTION_VERIFIED -> RELEASE_READY` | R10/R11、clean build、SBOM/license/cards、redacted public bundle | release evidence bundle |
| `RELEASE_READY -> DELIVERY_AUTHORIZED` | owner核对scope、claims、target和residual risks并签署授权 | delivery approval record |
| `DELIVERY_AUTHORIZED -> DELIVERED` | local/remote/tag/registry/DOI identities一致 | final delivery receipt |

### 13.2 DeliveryManifest

最终交付必须有一个strict/frozen、content-addressed、signed `DeliveryManifest`；它是交付索引，不复制private/sealed内容。closed字段至少包括：

- schema/signing domain、delivery/project/experiment/release IDs；
- source commit、parent、tag和worktree-clean attestation；
- spec、ADR、preregistration和recovery-plan versions/hashes；
- engineering baseline、runtime profile graph和build/import attestation refs；
- design-power、task-card、suite/method/ablation、calibration和freeze report refs；
- main/ablation/MPE2-info/paper-replication terminal coverage refs；
- analysis manifest/report、deviation report和independent reproduction refs；
- public five-file bundle、release assets、SBOM/license/cards及每个文件hash；
- CI/native review/release-verifier run identities；
- deferred/blocked/known limitation tuples；
- owner delivery approval、issued-at、nonce、key ID和signature。

Manifest只能引用已验证工件；未知字段、缺必需ref、HEAD/tag不一致、public hash mismatch、未解决blocker或过期/撤销key均拒绝。Agent2World SFT等明确deferred项可存在于delivery manifest，但不得被映射成completed capability。

### 13.3 Deterministic Go/No-Go

Delivery verifier按固定顺序执行，首个失败项决定`NO_GO` reason；不允许人工覆盖布尔值：

1. 验证DeliveryManifest signature、clock、nonce/replay和closed schema；
2. 验证local HEAD、parent、clean tree、tag和remote SHA；
3. 重放engineering baseline和runtime profile attestations；
4. 重验preregistration/freeze inputs在首个confirmatory run前已冻结；
5. 重算所有intention/RUN/N/A/attempt/terminal coverage；
6. 从signed rows重跑deterministic analysis并比较report/table hashes；
7. 验证independent reproduction report状态为`VERIFIED`，不是`ANALYZED/CANNOT_VERIFY`；
8. 重跑public schema、redaction attestation、file/column allowlist、secret/gold/restricted-content scans；
9. 验证SBOM、license inventory、cards、deviation和claims互相一致；
10. 验证所有required owner gates和delivery approval已签署且未撤销；
11. 核对remote/tag/registry/archive/DOI identity；
12. 全部通过才输出`GO`和final delivery receipt。

任何一步失败都输出`NO_GO`、exact failed predicate、subject refs和恢复工作包；不得输出“conditional GO”。如果交付目标不需要public release，也必须完成engineering、experiment、reproduction和受控delivery gates，只能把public remote步骤标为`N/A`并附owner-approved delivery-target policy，不能删除验证。

### 13.4 可直接交付的最终内容

执行本文全部required工作包后，交付对象应能直接用于：

- 接收方从clean checkout安装并运行已支持的AutoMarkov pipeline；
- operator按manifest提交新任务、查看waiting/blocker、恢复合法run；
- 审计者从signed artifacts重建每个结论和failure denominator；
- 独立验证者在受控capsule上不重训练重建主表，并在获授权runtime上执行复现subset；
- 论文作者直接使用固定public tables、effect/CI/deviation counts和复现声明；
- 开源接收方取得license/SBOM/cards/CITATION/provenance而不接触sealed/private/restricted内容。

项目代码和实验结果只有在13.3输出`GO`后才满足“直接交付”。本文本身是执行合同，不是`GO`证明；每次代码、数据、runtime、preregistration或交付目标变化都必须重新运行受影响的派生门禁。

---

## 14. 外部开源和学术规范对齐

详细的一手来源、版本和无法核验项记录在[上游规范刷新](research/2026-08-25-recovery-upstream-standards-refresh.md)。主文档只保留会改变实现或交付门禁的规则。任何`latest`网页只能用于发现；生产身份必须回到本项目锁定release/commit和runtime conformance tests。

| 来源 | 本项目必须采用的规则 | 落地工作包 |
|---|---|---|
| [RLlib 2.56.1 new API migration](https://docs.ray.io/en/releases-2.56.1/rllib/new-api-stack-migration-guide.html) | `AlgorithmConfig/PPOConfig`、`RLModule/MultiRLModule`、`ConnectorV2`、`EnvRunner`、`LearnerGroup`；新栈使用per-learner batch/resource配置 | R07 |
| [RLlib 2.56.1 environments](https://docs.ray.io/en/releases-2.56.1/rllib/package_ref/env.html) | external env under development，使用project-owned custom EnvRunner；agent→module/multi-agent语义由pinned runtime验证 | R05A/R07/R13–R16B |
| [RLlib 2.56.1 checkpointing](https://docs.ray.io/en/releases-2.56.1/rllib/checkpoints.html) | checkpoint含pickle/class/state，只允许trainer-local recovery；跨域必须weights-only export | R07 |
| [Gymnasium 1.2.2 Env API](https://gymnasium.farama.org/v1.2.2/api/env/) | `rllib-core`/Taxi candidate、MiniGrid、MPE2和sealed evaluator在各自1.2.2 profile运行checker/seed/reset/step tests | R12/R13/R14 |
| [Gymnasium 1.3.0 Env API](https://gymnasium.farama.org/v1.3.0/api/env/) | sealed Taxi gold和MetaDrive 1.3.0 lock按该profile运行检查；不能套用1.2.2 identity | R12/R16A |
| [Gymnasium 0.28.1 Env API](https://gymnasium.farama.org/v0.28.1/api/env/) | CityLearn兼容profile按0.28.1 contract运行检查；不得跨profile升级 | R16B |
| [PettingZoo 1.26.1 environment tests](https://pettingzoo.farama.org/1.26.1/content/environment_tests/) | AEC/Parallel adapter运行official API/parallel/seed tests；再叠加AutoMarkov information/reward/history tests | R14/R15/R16B |
| [MPE2 Simple Spread](https://mpe2.farama.org/environments/simple_spread/) | native observation/state维度和官方physics/reward来自pinned MPE2；full-state与native-local估计分开 | R14/R22 |
| [vLLM 0.25.1 security](https://docs.vllm.ai/en/v0.25.1/usage/security/) | API key不是完整perimeter；internal listener与generation principal必须namespace/firewall/authenticated-IPC隔离，并冻结relay route allowlist与current-connection proof | R05A/R24 |
| [safetensors](https://github.com/huggingface/safetensors) | 跨profile只交付finite weights-only tensors和strict manifest；不反序列化pickle/cloudpickle checkpoint | R07 |
| [SLSA v1.2](https://slsa.dev/spec/v1.2/) | release build产生外部subject/build provenance；level取决于builder/签名/隔离，同仓hash allowlist不冒充独立签名来源 | D01/R02/R11 |
| [SPDX specifications](https://spdx.dev/use/specifications/) | SBOM使用可验证SPDX document/package/relationship语义，package/license/source可追溯 | R11/R27 |
| [REUSE Specification 3.3](https://reuse.software/spec/) | 源文件/third-party材料具有machine-readable copyright/license metadata；restricted和no-license明确表达 | R11/R19 |
| [GitHub Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use) | action full-SHA pin、least permissions、不信任PR input、build/release凭据隔离 | R11 |
| [OpenSSF Scorecard](https://scorecard.dev/) | 作为recommended hardening report；只有owner将其纳入release policy后才成为blocking gate | D07/R11 |
| [ACM Artifact Review and Badging](https://www.acm.org/publications/policies/artifact-review-and-badging-current) | documented、complete、exercisable、reusable和independently reproduced分别给证据，不能互相替代 | R27/§13 |
| [NeurIPS Paper Checklist](https://neurips.cc/public/guides/PaperChecklist) | 公开training/test details、error bars/statistics、compute和code/data availability边界 | R19/R27 |
| [OSF Registrations](https://help.osf.io/article/330-welcome-to-registrations) | confirmatory plan使用time-stamped read-only registration或等价registry；修订生成新version | D10/R23 |
| [GitHub citation files](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-citation-files) | release提供准确`CITATION.cff`，方法/软件版本可引用 | D14/R27 |
| [Zenodo quick start](https://help.zenodo.org/docs/get-started/quickstart/) | owner选择archive后，release/tag与DOI metadata和checksums一致 | D14/R27 |

外部规范发生变化时，先更新研究笔记和typed upstream manifest，再决定是否产生新runtime/experiment/release version。已经开始的confirmatory family继续使用冻结版本；不得把中途升级混入原family。
