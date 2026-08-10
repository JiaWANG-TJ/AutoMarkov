---
title: "AutoMarkov：面向 MDP、POMDP、MG 与 POSG 的证据驱动自动建模、环境生成与策略学习系统"
language: zh-CN
format: "GitHub Flavored Markdown + LaTeX/MathJax"
status: "完整开发任务说明与实验规范"
---

# AutoMarkov：面向 MDP、POMDP、MG 与 POSG 的证据驱动自动建模、环境生成与策略学习系统

> **渲染说明**：本文档采用 GitHub Flavored Markdown（GFM）组织正文，数学公式采用标准 LaTeX 语法。建议使用支持 MathJax 或 KaTeX 的 Markdown 渲染器查看，例如 Typora、Obsidian、VS Code Markdown Preview Enhanced、JupyterLab 或 GitHub 的数学公式渲染功能。
>
> **项目范围**：核心数学对象仅保留 MDP、POMDP、MG 与 POSG；单智能体环境统一使用 Gymnasium，多智能体环境统一使用 PettingZoo，强化学习训练后端统一使用 RLlib。药品集采及其他与该研究目标无关的场景不纳入本项目。

> **规范性说明**：本文是完整产品与研究合同，不是“可先简化的 v1”。“必须”“不得”“仅”“恰好”均为验收门禁；“建议”“可选”才允许延期。若正文示例与规范性表格冲突，以更严格的门禁为准，并创建 `SpecificationConflictDetected` 事件，不得静默选边。

## 0.1 名称、运行时与冻结决策

项目展示名只能写作 `AutoMarkov`，Python package、CLI、配置前缀和 artifact namespace 只能写作 `automarkov`。任何其他历史名称不得进入代码、schema、路径、指标名、论文方法名或新文档。

以下决定已冻结，任何变更必须经 ADR、用户批准和新的 artifact revision：

| 决策域 | 冻结决定 |
|---|---|
| 生成模型 | 仅允许自托管 `Qwen/Qwen3.6-35B-A3B`，由 `LocalLlmRuntime` 管理 vLLM；禁止任何云端或托管 LLM fallback |
| LLM 传输 | 仅允许本机 loopback 或 Unix socket 上的 OpenAI-compatible vLLM endpoint；模型权重 revision、tokenizer revision、vLLM/container digest 必须锁定 |
| Web evidence | 仅允许 Tavily `Search`、`Extract`、`Crawl`；Search 的 `include_answer` 必须显式为 `false`；禁止 `Research`、`Map` 和搜索供应商 fallback |
| Tavily keys | 恰好支持 `TAVILY_API_KEY_01` 至 `TAVILY_API_KEY_29` 的安全租约与轮换；密钥值不得进入日志、artifact、prompt 或错误消息 |
| Schema | Pydantic v2 strict/frozen models；`DecisionProcessSpec` 是 MDP/POMDP/MG/POSG 的 discriminated union，`kind` 是唯一 discriminator |
| 环境接口 | Gymnasium（单智能体）和 PettingZoo Parallel/AEC（多智能体）；外部环境经隔离 dependency profile 与 `RemoteEnv` 协议接入 |
| RL 后端 | PyTorch + 现代 RLlib 新 API 栈：`AlgorithmConfig`、`EnvRunner`、`RLModule`/`MultiRLModule`、`LearnerGroup`、`ConnectorV2` |
| 游戏论分析 | OpenSpiel 只作为 OOD/分析 backend，不扩展第五类核心 IR；用于 extensive-form、chance node、best response、exploitability 等 |
| 经典规划 | PDDL 经 Unified Planning parse/write/compile/solve/validate，作为 OOD handoff；不得伪装为第五类 Markov IR |
| 核心 MG | MPE2 `simple_spread_v3` 的 full-state MG adaptation 是主轨；原生 local-observation POSG 是预注册 ablation |
| Agent2World SFT | 延期，不属于当前核心交付或 release gate；仅保留接口、数据 lineage 与未来执行计划 |
| 计算 | CPU-first；GPU 仅分配给本地 vLLM 和经 manifest 批准的 RL learner；远程实验必须由 fixed-commit runner 执行 |
| 评测 | 主 intention-to-run grid 为 6 suites × 5 variants × 2 tracks × 6 methods × `n_pair` pair slots，其中 `n_pair` 由首个 run 前的 design-power gate 冻结；每个 suite 唯一冻结 source-access mode 与 required implementation route；仅 manifest 标记 `RUN` 的 cells 实际生成，每个有效环境 10 个 RL seeds，`N/A` 只留 eligibility record；六项消融使用独立 paired ledger |
| 统计 | 固定 6×4 finite-benchmark strata，并保留 cell 内 generation pair→RL seed 的配对结构；ReAct co-primary 由 paired stratified-bootstrap bounds 判定，Holm 仅用于冻结 secondary families；策略非劣界为标准化分数 0.05，报告单侧 97.5% CI |

## 0.2 当前完整范围与显式延期

当前范围包括完整 schema、compiler、证据链、隔离执行、六个 core suites、所有受控基线、RL 训练与嵌套统计。不得用 mock success、单一 happy path、仅 API checker、少量 seeds 或缩小任务矩阵替代验收。

唯一已批准延期是 Agent2World 的 SFT 训练。其原因不是技术路线被删除，而是上游代码采用 `RESEARCH / EVALUATION ONLY` 许可，禁止商业使用、再分发、衍生作品分发和 hosted service；同时 SFT 还需独立核验模型、数据、基础权重及训练产物许可。未获得书面许可且未完成资源预算批准前，只能运行许可允许的非商业研究评估，受限仓库只能放在 ignored external cache，不能 vendoring、复制进发布包或发布衍生实现。

---

## 目录

1. [项目最终定位](#1-项目最终定位)
2. [四类数学对象的严格边界](#2-四类数学对象的严格边界)
3. [项目交付物以及是否需要训练](#3-项目交付物以及是否需要训练)
4. [系统架构：Typed-Blackboard Multi-Agent Compiler](#4-系统架构typed-blackboard-multi-agent-compiler)
5. [完整工作流](#5-完整工作流)
6. [规范化英文伪代码](#6-规范化英文伪代码)
7. [基于 Tavily 的证据驱动 Web Retrieval](#7-基于-tavily-的证据驱动-web-retrieval)
8. [RLlib 算法路线](#8-rllib-算法路线)
9. [六个结构化 core suites](#9-六个结构化-core-suites)
10. [A-LAMP、Agent² 与 Agent2World 的主实验复现](#10-a-lampagent²-与-agent2world-的主实验复现)
11. [基线、指标、统计检验与消融](#11-基线指标统计检验与消融)
12. [相对于相关工作的创新点](#12-相对于相关工作的创新点)
13. [提示词体系](#13-提示词体系)
14. [权威 upstream 项目与复用边界](#14-权威-upstream-项目与复用边界)
15. [30 篇必须阅读和对齐的论文](#15-30-篇必须阅读和对齐的论文)
16. [代码目录结构](#16-代码目录结构)
17. [实现能力门与验收标准](#17-实现能力门与验收标准)
18. [安全、许可与可复现性要求](#18-安全许可与可复现性要求)
19. [最终系统定义](#19-最终系统定义)
20. [关键来源](#20-关键来源)

---

# 1. 项目最终定位

AutoMarkov 的核心目标不是让大语言模型直接生成一段强化学习环境代码，而是将用户提供的不完整、可能存在歧义的自然语言任务描述，逐级编译为经过确认和验证的任务契约、数学决策过程、可执行环境以及可复现的策略训练结果：

$$
\text{Natural-language request}
\rightarrow
\text{TaskContract}
\rightarrow
\text{DecisionProcessSpec}
\rightarrow
\text{Executable environment}
\rightarrow
\text{Verified RL policy}.
$$

最终交付的是一套**可追溯、可验证、可执行、可训练、可复现的实验包**，而不是单独的环境脚本、零散提示词或单个训练后模型。

原始研究构想中“自然语言表征—数学建模—编程测试—策略训练”的主干是合理的，但不能仅使用“建模成功率、编码成功率、策略生成成功率”三个粗粒度指标评价系统。AutoMarkov 必须进一步区分：

- 需求语义错误；
- 数学对象分类错误；
- 状态、观测、动作、奖励或转移定义错误；
- 环境 API 与代码实现错误；
- 动态行为错误；
- 策略训练配置错误；
- 训练预算不足；
- 任务本身不可解或不属于项目范围。

## 1.1 最终技术路线

| 项目维度 | 最终设定 |
|---|---|
| 核心数学对象 | MDP、POMDP、MG、POSG |
| 单智能体环境接口 | Gymnasium |
| 多智能体环境接口 | PettingZoo Parallel API 或 AEC API |
| 强化学习后端 | 仅使用 RLlib |
| 生成推理 | 自托管 Qwen3.6-35B-A3B + vLLM，禁止 hosted LLM 与 provider fallback |
| 外部知识来源 | Tavily Search/Extract/Crawl，Search 固定 `include_answer=false` |
| 场景实现优先级 | Reuse → Compose → Generate |
| 大语言模型训练 | 核心建模系统不训练大模型；Agent2World SFT 明确延期 |
| RL 策略训练 | 端到端实验中必须执行，用于策略交付和环境有效性诊断 |
| 核心主实验 | 四个数学对象各一个任务，加两个真实复杂场景，共六项 |
| 论文复现实验 | A-LAMP、Agent²、Agent2World 分别设置独立 replication suite |
| OOD 支持 | 不强行归入四类；输出 `OODHandoffSpec`，并优先支持 PDDL 路由 |
| 明确删除 | 药品集采、SOTOPIA、AgentSense、HiSim、Stable-Baselines3、Tianshou、MARLlib 等非收敛路线内容 |

## 1.2 核心设计原则

1. **语义先于代码**：在用户确认任务语义前，不进入数学形式化；在用户确认数学形式化前，不生成环境代码。
2. **结构化中间表示**：智能体之间不通过无限制自由文本传递结果，而通过有类型、有版本、有哈希的结构化工件交换信息。
3. **证据约束**：领域规则、参数、数据分布和官方实现均应关联可信来源；系统不得用无来源常识补全关键动态。
4. **最近致因回退**：发现错误时只回退至最可能导致错误的上游阶段，不机械地返回整个工作流起点。
5. **复用优先**：复杂真实环境优先适配官方模拟器，不重新编写其物理引擎或领域核心逻辑。
6. **测试驱动**：代码可运行只是最低要求；必须同时通过属性测试、变形测试、差分测试和轨迹行为测试。
7. **统一训练后端**：所有策略训练和算法对比均经 RLlib 实现或封装，避免多个训练框架引入不可控差异。
8. **实验与生产模式分离**：生产模式允许真实用户确认；实验模式使用固定 clarification oracle，保证方法间比较公平。
9. **artifact 与状态分离**：artifact payload 永久不可变；批准、拒绝、supersede 与状态推进仅写 append-only event stream。
10. **依赖与核心隔离**：核心进程不得 import SMACv2、MetaDrive、CityLearn、OpenSpiel 等 profile-specific package；所有环境经 `RemoteEnv` 协议交互。
11. **CPU-first**：schema、compiler、检索缓存、静态/属性测试、toy rollout 与统计聚合默认在 CPU 执行；只有明确需要时才申请 GPU。
12. **fail closed**：缺证据、key 暂时 cooldown/leased、provider-credit 耗尽、凭据/权限缺失、profile hash 不匹配、sealed evaluator 不可用或远程 commit 不符时，分别返回规范的 `WAITING_EVIDENCE`、`BUDGET_EXHAUSTED`、`BLOCKED`、`PARTIAL` 或 `FAILED` 状态，不切换未批准后端；具体原因到状态的映射以第 4.4 与 7.8 节为准。

---

# 2. 四类数学对象的严格边界

## 2.1 MDP

Markov 决策过程定义为：

$$
\mathcal{M}_{\mathrm{MDP}}
=
\left\langle
\mathcal{S},
\mathcal{A},
P,
R,
\rho_0,
\gamma,
H
\right\rangle,
$$

其中：

- $\mathcal{S}$：状态空间；
- $\mathcal{A}$：动作空间；
- $P(s_{t+1}\mid s_t,a_t)$：状态转移核；
- $R(s_t,a_t,s_{t+1})$：奖励函数；
- $\rho_0$：初始状态分布；
- $\gamma\in[0,1]$：折扣因子；
- $H$：有限时域长度，若为无限时域则明确标记。

MDP 必须满足 Markov 性：

$$
P(s_{t+1}\mid s_0,a_0,\ldots,s_t,a_t)
=
P(s_{t+1}\mid s_t,a_t).
$$

当且仅当当前状态 $s_t$ 已包含预测未来转移和奖励所需的全部历史信息时，任务才应建模为 MDP。

## 2.2 POMDP

部分可观测 Markov 决策过程定义为：

$$
\mathcal{M}_{\mathrm{POMDP}}
=
\left\langle
\mathcal{S},
\mathcal{A},
\mathcal{O},
P,
Z,
R,
\rho_0,
\gamma,
H
\right\rangle,
$$

其中 $\mathcal{O}$ 为观测空间，$Z$ 为观测核：

$$
Z(o_t\mid s_t,a_{t-1}).
$$

策略不能直接以隐藏状态为输入，而应依赖观测历史或信念状态：

$$
\pi(a_t\mid h_t),
\qquad
h_t=(o_0,m_0,a_0,r_1,o_1,m_1,\ldots,a_{t-1},r_t,o_t,m_t).
$$

这里 $r_t$ 是执行 $a_{t-1}$ 后、选择 $a_t$ 前向 actor 可见的奖励观测，$m_t$ 是同一决策时刻可见的外部或通信消息；若任务没有可见消息则 $m_t=\varnothing$。任何不可见 reward/message 均不得进入 $h_t$，实际 history projection 必须由 `HistoryAccessSpec.reward_lags` 与 `message_lags` 精确限定。等价实现可以把可见 $r_t,m_t$ 编入 typed observation，但必须在 schema/adapter manifest 中声明该编码，不能静默丢弃或泄漏。

也可以构造信念状态：

$$
b_t(s)=P(s_t=s\mid h_t),
$$

并在信念 MDP 上进行决策。

## 2.3 MG

Markov game 又称 stochastic game，定义为：

$$
\mathcal{M}_{\mathrm{MG}}
=
\left\langle
\mathcal{N},
\mathcal{S},
\{\mathcal{A}_i\}_{i\in\mathcal{N}},
P,
\{R_i\}_{i\in\mathcal{N}},
\rho_0,
\gamma,
H
\right\rangle,
$$

其中：

- $\mathcal{N}=\{1,\ldots,N\}$ 为决策主体集合；
- 联合动作为 $\mathbf{a}_t=(a_{1,t},\ldots,a_{N,t})$；
- 转移核为

$$
P(s_{t+1}\mid s_t,\mathbf{a}_t);
$$

- 第 $i$ 个主体的奖励为

$$
R_i(s_t,\mathbf{a}_t,s_{t+1}).
$$

MG 可以是合作型、零和型或一般和型。系统必须显式声明奖励结构和求解概念，例如团队回报、Nash equilibrium、best response、social welfare 或 exploitability。

## 2.4 POSG

部分可观测随机博弈定义为：

$$
\mathcal{M}_{\mathrm{POSG}}
=
\left\langle
\mathcal{N},
\mathcal{S},
\{\mathcal{A}_i\},
\{\mathcal{O}_i\},
P,
Z,
\{R_i\},
\rho_0,
\gamma,
H
\right\rangle.
$$

第 $i$ 个主体只能访问自己的局部观测 $o_{i,t}$ 或局部历史：

$$
\pi_i(a_{i,t}\mid h_{i,t}),
\qquad
h_{i,t}=(o_{i,0},m_{i,0},a_{i,0},r_{i,1},\ldots,a_{i,t-1},r_{i,t},o_{i,t},m_{i,t}).
$$

$m_{i,t}$ 仅包含在时刻 $t$ 对主体 $i$ 可见的消息，$r_{i,t}$ 仅包含其可见 reward observation；二者的 lag 与 sender/recipient 边界由该主体的 `HistoryAccessSpec` 和 `message_processes_by_recipient` 冻结。每条 process 还固定 typed sender、exact recipient、channel、message space、delivery kernel 与 delay law。集中式 critic 可在训练期读取另行授权的 joint state/history，decentralized actor 的 $h_{i,t}$ 不得因此扩大。

观测核必须首先定义完整联合观测分布：

$$
Z(\mathbf{o}_t\mid s_t,\mathbf{a}_{t-1}),
\qquad \mathbf{o}_t=(o_{1,t},\ldots,o_{|\mathcal N|,t}).
$$

各主体的 $Z_i$ 只能作为该联合核在对应坐标上的边缘投影。只给出 $\{Z_i\}$ 不能确定跨主体观测相关性，因此不构成完整 POSG。

当 centralized critic 在训练阶段访问全局状态，而执行阶段的 actor 只能访问局部观测时，任务仍属于 POSG，而不是 MG。

## 2.5 二维统一分类

| 数学对象 | 决策主体数量 | 信息结构 | 规范接口 |
|---|---:|---|---|
| MDP | 1 | 当前输入具有 Markov 充分性 | Gymnasium |
| POMDP | 1 | 决策者只能获得局部或噪声观测 | Gymnasium |
| MG | 多个 | 每个决策者获得全局状态或等价的 Markov 充分信息 | PettingZoo |
| POSG | 多个 | 每个主体仅获得局部观测、私有信息或受限通信 | PettingZoo |

## 2.6 分类必须遵守的规则

1. **多个物理实体不等于多智能体。** 如果一个中央控制器联合决定所有实体动作，任务仍可属于 MDP 或 POMDP。
2. **多个独立决策主体才构成 MG/POSG。** 主体必须具有独立动作选择或独立优化目标。
3. **不能用“观测看起来足够”替代 Markov 检查。** 若存在影响未来但未进入状态的历史变量，应扩展状态或改判为 POMDP/POSG。
4. **全局状态仅供 critic 使用不改变 POSG 分类。** 分类依据执行阶段的信息结构，而非训练阶段 critic 的额外输入。
5. **MG/POSG 必须定义 solution concept。** 未定义时必须询问用户，不能默认所有主体共享奖励。
6. **同时动作与轮流动作是正交属性。** 同时动作优先使用 PettingZoo Parallel API；轮流动作或事件驱动任务使用 AEC API。
7. **Dec-POMDP 作为合作型 POSG 的特例处理。** 不额外扩展第五种核心对象。

## 2.7 四类对象能否覆盖所有任务

不能。它们可以系统覆盖离散时间、序贯决策、环境随机性、一个或多个决策主体、完全或部分可观测的主体范围，但以下任务不应被强行包装：

- 纯静态分类、回归或预测；
- 仅进行因果识别和统计推断的任务；
- 无序贯反馈的静态数学规划；
- 经典确定性 PDDL 规划；
- 连续时间随机控制；
- 混合自动机、微分博弈；
- 开放人口、动态加入和退出主体且无法预设最大主体集合的系统；
- 需要严格安全证明、形式验证或最优控制证书而非经验策略学习的任务。

系统应返回：

```yaml
classification: OOD
reason:
  - "The task is not naturally represented by MDP, POMDP, MG, or POSG."
recommended_backend:
  type: "PDDL | continuous-time control | causal inference | mathematical programming | custom"
required_artifacts:
  - "OODHandoffSpec"
```

### 2.7.1 OpenSpiel 与 PDDL OOD backends

OOD 不是失败兜底，而是 typed handoff。以下两个 backend 是当前唯一内置 OOD routes：

| Route | 触发条件 | 规范 artifact | 官方能力复用 | 验证 gate |
|---|---|---|---|---|
| `OPEN_SPIEL` | 问题主要是 extensive-form game、information set、chance node、best response、Nash/exploitability 或 game-tree 分析，且不要求生成四类环境 | `OpenSpielHandoffSpec` | `pyspiel.Game/GameType/State`、sequential/simultaneous actions、chance outcomes、observations/information states、returns、官方 algorithms | game type introspection、legal action/chance probability、serialize round-trip、playthrough、utility/solution-concept checks |
| `PDDL` | 确定性/经典/时态/支持的规划问题，以 action precondition/effect 和 goal satisfiability 为核心 | `PddlHandoffSpec` | Unified Planning `PDDLReader`/`PDDLWriter`、`Compiler` operation modes、`OneshotPlanner`、`PlanValidator` | parse、write→parse semantic round-trip、problem-kind support、compile mapping、solve、independent plan validate |

其余 causal inference、continuous-time control、mathematical programming 或 custom backend 使用通用 `OODHandoffSpec`，并强制 `capability="referral_only"`。该 artifact 至少包含 classification reason、recommended backend、evidence/authority identities、required inputs/outputs、assumptions、unsupported features、license/asset requirements 与接收方验收清单。`referral_only` 只表示 handoff schema、evidence trace、capability 声明和 package scan 已验证；不得宣称 backend executable、solved 或 behaviorally verified。OpenSpiel/PDDL 使用 `capability="executable"` 的 route-specific subtype 和表中适用的额外 gates。

`OpenSpielHandoffSpec` 必须声明 players、dynamics、chance mode、information model、utility type、reward model、min/max players、selected game/adapter、requested algorithms 和 metric。不能仅因任务“有多个玩家”就路由 OpenSpiel；可自然表示为 MG/POSG 并需要 RL environment 的任务仍进入核心 union。OpenSpiel profile 不得被 core schema import，它通过固定 commit/version 和 artifact adapter 工作。

`PddlHandoffSpec` 必须声明 domain/problem source、PDDL requirements、objects/types/fluents/actions/goals/metrics、selected compiler kinds、planner engine profile 和 unsupported features。parse 成功不等于计划正确；只有 planner positive outcome 且独立 `PlanValidator` 返回 valid，才能声明 solved。compiler 产生的 plan 必须通过 mapping 恢复到原问题再验证。

只有 `ClassificationResult=REDUCIBLE` 且用户批准归约为 MDP/POMDP/MG/POSG 时，才生成 `ReductionProposal`，列出有限化、离散化、rewardization、horizon、chance 与信息结构假设，以及语义损失；批准后按 §2.10 invariant 16 创建新的 core `TaskContract`。`ClassificationResult=OOD` 必须继续进入 typed `OODHandoffSpec` 路线，不得旁路状态机归约，也不得在同一个 `DecisionProcessSpec` 中混入 OpenSpiel/PDDL 字段。

## 2.8 Gymnasium、PettingZoo 和 RLlib 的覆盖边界

Gymnasium 负责单智能体 `reset`/`step` 交互协议、空间定义、终止和截断；PettingZoo 负责多智能体 AEC 与 Parallel API；RLlib 负责策略采样、训练、评估、多策略映射和分布式执行。

必须明确：

- Gymnasium 和 PettingZoo 是**程序接口规范**，不是数学正确性证明器；
- RLlib 是**训练后端**，不是需求分析器、数学形式化器或领域真实性验证器；
- 一个数学语义错误的环境完全可能通过 API checker；
- 因此项目还必须实现需求结构化、证据检索、数学验证、代码沙箱、属性测试、差分测试和行为测试。

## 2.9 Pydantic v2 规范性联合类型

`DecisionProcessSpec` 不得是带大量 optional fields 的单一模型，也不得依赖 untagged/smart union 猜测对象类型。实现必须使用 Pydantic v2、`ConfigDict(strict=True, frozen=True, extra="forbid")`、`Literal` 标签和 `Field(discriminator="kind")`。所有 public `DecisionProcessSpec` payload 分别通过 `validate_decision_process_payload` 或 `validate_decision_process_json` 进入唯一的 `TypeAdapter(DecisionProcessSpec)`，同一 adapter 生成 JSON Schema；其他 typed artifacts 使用各自 strict model 的 raw-tree ingress 与 `model_json_schema`。未知字段、隐式数值转换、错误标签、缺失分支字段和 provenance 不明的 `BaseModel` 必须失败。Pydantic 的 model-level strict mode 仍允许 `int` 输入进入 `float` 字段，因此所有 contract float 必须额外使用 exact-type before-validator，拒绝 `bool`/`int`/string、NaN 和 infinity；反例测试至少覆盖 Python 与 JSON 两条 validation path。

以下是公共类型轮廓；实际实现可拆文件，但字段语义和禁令不得收缩：

```python
import json
from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Annotated, Literal, TypeVar, cast
from typing_extensions import TypeAliasType

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    TypeAdapter,
    WithJsonSchema,
    model_validator,
)


MAX_JSON_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_JSON_NESTING_DEPTH = 128
MAX_JSON_NODES = 1_000_000


def reject_lone_surrogate_tree(value: object) -> object:
    stack: list[tuple[object, int, bool]] = [(value, 0, False)]
    active_containers: set[int] = set()
    nodes = 0
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active_containers.remove(id(current))
            continue
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_NESTING_DEPTH:
            raise ValueError("persisted value exceeds resource limits")
        if type(current) is str:
            if any(0xD800 <= ord(char) <= 0xDFFF for char in current):
                raise ValueError("persisted strings must not contain lone surrogates")
            continue
        if isinstance(current, BaseModel):
            raise ValueError("public payload trees must not contain BaseModel instances")
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in active_containers:
                raise ValueError("persisted values must be acyclic")
            active_containers.add(identity)
            stack.append((current, depth, True))
            stack.extend(
                (item, depth + 1, False)
                for pair in current.items()
                for item in pair
            )
        elif type(current) in {list, tuple}:
            identity = id(current)
            if identity in active_containers:
                raise ValueError("persisted values must be acyclic")
            active_containers.add(identity)
            stack.append((current, depth, True))
            stack.extend((item, depth + 1, False) for item in current)
    return value


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_and_validate_persisted_input(cls, value: object) -> object:
        return reject_lone_surrogate_tree(value)


def normalize_discriminated_model_input(value: object) -> object:
    return reject_lone_surrogate_tree(value)


MAX_SAFE_INTEGER = 2**53 - 1


def require_safe_int(value: object) -> int:
    if type(value) is not int or abs(value) > MAX_SAFE_INTEGER:
        raise ValueError("expected an IEEE-754 interoperable integer")
    return value


def require_nonnegative_safe_int(value: object) -> int:
    result = require_safe_int(value)
    if result < 0:
        raise ValueError("expected a nonnegative interoperable integer")
    return result


def require_positive_safe_int(value: object) -> int:
    result = require_safe_int(value)
    if result <= 0:
        raise ValueError("expected a positive interoperable integer")
    return result


def require_exact_true(value: object) -> bool:
    if type(value) is not bool or value is not True:
        raise ValueError("expected the exact JSON boolean true")
    return value


def require_exact_float(value: object) -> float:
    if (
        type(value) is not float
        or not isfinite(value)
        or abs(value) > MAX_SAFE_INTEGER
    ):
        raise ValueError("expected an interoperable finite JSON float")
    return 0.0 if value == 0.0 else value


def require_probability_float(value: object) -> float:
    result = require_exact_float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError("expected an exact float in [0, 1]")
    return result


def require_confidence_float(value: object) -> float:
    result = require_exact_float(value)
    if not 0.0 < result < 1.0:
        raise ValueError("expected an exact float in (0, 1)")
    return result


def require_nonnegative_float(value: object) -> float:
    result = require_exact_float(value)
    if result < 0.0:
        raise ValueError("expected a nonnegative exact float")
    return result


def normalize_canonical_json_tree(value: object) -> object:
    if value is None or type(value) in {bool, int}:
        if type(value) is int and abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("JSON integers must be IEEE-754 interoperable")
        return value
    if type(value) is str:
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("JSON strings must not contain lone surrogates")
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("JSON floats must be finite")
        if value.is_integer():
            if abs(value) > MAX_SAFE_INTEGER:
                raise ValueError("integral JSON floats must be interoperable integers")
            return int(value)
        return value
    if type(value) is list:
        return [normalize_canonical_json_tree(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {
            normalize_canonical_json_tree(key): normalize_canonical_json_tree(item)
            for key, item in value.items()
        }
    raise ValueError("expected canonical JSON value")


def require_canonical_json_input(value: object) -> object:
    reject_lone_surrogate_tree(value)
    try:
        return normalize_canonical_json_tree(value)
    except RecursionError as error:
        raise ValueError("persisted value exceeds resource limits") from error


def freeze_json_value(value: object) -> object:
    if type(value) is list:
        return tuple(freeze_json_value(item) for item in value)
    if type(value) is dict:
        return MappingProxyType(
            {key: freeze_json_value(item) for key, item in value.items()}
        )
    return value


def thaw_json_value(value: object) -> object:
    if isinstance(value, tuple):
        return [thaw_json_value(item) for item in value]
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("persisted mapping keys must be exact strings")
        return {key: thaw_json_value(item) for key, item in value.items()}
    return value


T = TypeVar("T")


def freeze_sequence(value: object) -> tuple[object, ...]:
    if type(value) is list:
        return tuple(value)
    if type(value) is tuple:
        return tuple(value)
    raise ValueError("expected JSON array or immutable tuple")


def freeze_string_mapping(value: dict[str, T]) -> Mapping[str, T]:
    return MappingProxyType(dict(value))


def thaw_frozen_string_mapping_input(value: object) -> object:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("persisted mapping keys must be exact strings")
        if type(value) is MappingProxyType:
            return thaw_json_value(value)
    return value


def thaw_nested_mappings(value: object) -> object:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("persisted mapping keys must be exact strings")
        return {key: thaw_nested_mappings(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(thaw_nested_mappings(item) for item in value)
    return value


def thaw_string_mapping(value: Mapping[str, T]) -> dict[str, T]:
    return {
        key: cast(T, thaw_nested_mappings(item))
        for key, item in value.items()
    }


def reject_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member: {key!r}")
        result[key] = value
    return result


def reject_nonfinite_constant(token: str) -> object:
    raise ValueError(f"non-finite JSON number: {token}")


def enforce_json_input_limits(raw: bytes) -> None:
    if len(raw) > MAX_JSON_PAYLOAD_BYTES:
        raise ValueError("JSON payload exceeds byte limit")
    depth = 0
    nodes = 0
    in_string = False
    in_scalar = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            nodes += 1
            in_string = True
            in_scalar = False
        elif byte in {0x5B, 0x7B}:
            nodes += 1
            depth += 1
            in_scalar = False
            if depth > MAX_JSON_NESTING_DEPTH:
                raise ValueError("JSON payload exceeds nesting limit")
        elif byte in {0x5D, 0x7D}:
            depth -= 1
            in_scalar = False
            if depth < 0:
                raise ValueError("malformed JSON nesting")
        elif byte in {0x09, 0x0A, 0x0D, 0x20, 0x2C, 0x3A}:
            in_scalar = False
        elif not in_scalar:
            nodes += 1
            in_scalar = True
        if nodes > MAX_JSON_NODES:
            raise ValueError("JSON payload exceeds node limit")
    if in_string or depth != 0:
        raise ValueError("malformed JSON framing")


def validate_wire_json_tree(value: object) -> object:
    stack: list[tuple[object, int, bool]] = [(value, 0, False)]
    active_containers: set[int] = set()
    nodes = 0
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active_containers.remove(id(current))
            continue
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_NESTING_DEPTH:
            raise ValueError("decoded JSON exceeds resource limits")
        if current is None or type(current) is bool:
            continue
        if type(current) is int:
            require_safe_int(current)
            continue
        if type(current) is float:
            require_exact_float(current)
            continue
        if type(current) is str:
            reject_lone_surrogate_tree(current)
            continue
        if type(current) is list:
            identity = id(current)
            if identity in active_containers:
                raise ValueError("raw JSON tree must be acyclic")
            active_containers.add(identity)
            stack.append((current, depth, True))
            stack.extend((item, depth + 1, False) for item in current)
            continue
        if type(current) is dict:
            identity = id(current)
            if identity in active_containers:
                raise ValueError("raw JSON tree must be acyclic")
            active_containers.add(identity)
            stack.append((current, depth, True))
            stack.extend(
                (item, depth + 1, False)
                for pair in current.items()
                for item in pair
            )
            continue
        raise ValueError("unexpected decoded JSON value")
    return value


def compact_json_string_size(value: str, maximum: int) -> int:
    size = 2
    short_escapes = {0x08, 0x09, 0x0A, 0x0C, 0x0D}
    for character in value:
        codepoint = ord(character)
        if character in {'"', "\\"} or codepoint in short_escapes:
            size += 2
        elif codepoint < 0x20:
            size += 6
        else:
            size += len(character.encode("utf-8"))
        if size > maximum:
            raise ValueError("raw JSON payload exceeds byte limit")
    return size


def validate_and_measure_raw_json_tree(value: object) -> int:
    validate_wire_json_tree(value)
    total = 0
    active_containers: set[int] = set()
    stack: list[tuple[str, object, int]] = [("value", value, 0)]

    def add_size(size: int) -> None:
        nonlocal total
        total += size
        if total > MAX_JSON_PAYLOAD_BYTES:
            raise ValueError("raw JSON payload exceeds byte limit")

    while stack:
        frame_kind, current, identity = stack.pop()
        if frame_kind == "list_iterator":
            try:
                item = next(current)
            except StopIteration:
                active_containers.remove(identity)
            else:
                stack.append((frame_kind, current, identity))
                stack.append(("value", item, 0))
            continue
        if frame_kind == "dict_iterator":
            try:
                key, item = next(current)
            except StopIteration:
                active_containers.remove(identity)
            else:
                stack.append((frame_kind, current, identity))
                add_size(
                    compact_json_string_size(
                        key,
                        MAX_JSON_PAYLOAD_BYTES - total,
                    )
                )
                stack.append(("value", item, 0))
            continue

        if current is None:
            add_size(4)
        elif type(current) is bool:
            add_size(4 if current else 5)
        elif type(current) is int:
            add_size(len(str(current)))
        elif type(current) is float:
            add_size(len(repr(current)))
        elif type(current) is str:
            add_size(
                compact_json_string_size(
                    current,
                    MAX_JSON_PAYLOAD_BYTES - total,
                )
            )
        elif type(current) is list:
            identity = id(current)
            if identity in active_containers:
                raise ValueError("raw JSON tree must be acyclic")
            active_containers.add(identity)
            add_size(2 + max(len(current) - 1, 0))
            stack.append(("list_iterator", iter(current), identity))
        elif type(current) is dict:
            identity = id(current)
            if identity in active_containers:
                raise ValueError("raw JSON tree must be acyclic")
            active_containers.add(identity)
            add_size(2 + len(current) + max(len(current) - 1, 0))
            stack.append(("dict_iterator", iter(current.items()), identity))
        else:
            raise ValueError("unexpected raw JSON value")
    return total


def parse_finite_json_float(token: str) -> float:
    decimal_value = Decimal(token)
    value = float(token)
    if not isfinite(value):
        raise ValueError("JSON floats must be finite")
    if decimal_value != 0 and value == 0.0:
        raise ValueError("nonzero JSON float underflowed to zero")
    return value


def parse_json_payload(raw: bytes) -> object:
    enforce_json_input_limits(raw)
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("UTF-8 BOM is forbidden")
    try:
        return validate_wire_json_tree(
            json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=reject_duplicate_object,
                parse_constant=reject_nonfinite_constant,
                parse_float=parse_finite_json_float,
            )
        )
    except RecursionError as error:
        raise ValueError("JSON decoder nesting limit exceeded") from error


StrictCanonicalFloat = Annotated[
    float,
    BeforeValidator(require_exact_float),
    WithJsonSchema(
        {
            "type": "number",
            "minimum": -MAX_SAFE_INTEGER,
            "maximum": MAX_SAFE_INTEGER,
            "x-automarkov-number-kind": "exact-float",
        }
    ),
]
SafeCanonicalInt = Annotated[
    int,
    BeforeValidator(require_safe_int),
    WithJsonSchema(
        {
            "type": "integer",
            "minimum": -MAX_SAFE_INTEGER,
            "maximum": MAX_SAFE_INTEGER,
        }
    ),
]
NonNegativeSafeCanonicalInt = Annotated[
    int,
    BeforeValidator(require_nonnegative_safe_int),
    WithJsonSchema(
        {"type": "integer", "minimum": 0, "maximum": MAX_SAFE_INTEGER}
    ),
]
PositiveSafeCanonicalInt = Annotated[
    int,
    BeforeValidator(require_positive_safe_int),
    WithJsonSchema(
        {"type": "integer", "minimum": 1, "maximum": MAX_SAFE_INTEGER}
    ),
]
StrictTrue = Annotated[
    bool,
    BeforeValidator(require_exact_true),
    WithJsonSchema({"type": "boolean", "const": True}),
]
ProbabilityCanonicalFloat = Annotated[
    float,
    BeforeValidator(require_probability_float),
    WithJsonSchema(
        {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "x-automarkov-number-kind": "exact-float",
        }
    ),
]
ConfidenceCanonicalFloat = Annotated[
    float,
    BeforeValidator(require_confidence_float),
    WithJsonSchema(
        {
            "type": "number",
            "exclusiveMinimum": 0.0,
            "exclusiveMaximum": 1.0,
            "x-automarkov-number-kind": "exact-float",
        }
    ),
]
NonNegativeCanonicalFloat = Annotated[
    float,
    BeforeValidator(require_nonnegative_float),
    WithJsonSchema(
        {
            "type": "number",
            "minimum": 0.0,
            "maximum": MAX_SAFE_INTEGER,
            "x-automarkov-number-kind": "exact-float",
        }
    ),
]
FrozenSequence = TypeAliasType(
    "FrozenSequence",
    Annotated[
        tuple[T, ...],
        BeforeValidator(freeze_sequence),
    ],
    type_params=(T,),
)
_CanonicalJsonNode = TypeAliasType(
    "_CanonicalJsonNode",
    None
    | bool
    | int
    | float
    | str
    | list["_CanonicalJsonNode"]
    | dict[str, "_CanonicalJsonNode"],
)
CanonicalJsonValue = TypeAliasType(
    "CanonicalJsonValue",
    Annotated[
        _CanonicalJsonNode,
        BeforeValidator(require_canonical_json_input),
        AfterValidator(freeze_json_value),
        PlainSerializer(thaw_json_value, return_type=object, when_used="json"),
    ],
)
FrozenStringMapping = TypeAliasType(
    "FrozenStringMapping",
    Annotated[
        dict[str, T],
        BeforeValidator(thaw_frozen_string_mapping_input),
        AfterValidator(freeze_string_mapping),
        PlainSerializer(
            thaw_string_mapping,
            return_type=dict[str, T],
            when_used="json",
        ),
    ],
    type_params=(T,),
)


class ValidationLevel(StrEnum):
    SCHEMA = "schema"
    STRUCTURAL = "structural"
    EXECUTABLE = "executable"
    BEHAVIORAL = "behavioral"
    ORACLE_EQUIVALENT = "oracle_equivalent"
    FORMALLY_VERIFIED = "formally_verified"


def decode_validation_level(value: object) -> ValidationLevel:
    if type(value) is ValidationLevel:
        return value
    if type(value) is str:
        try:
            return ValidationLevel(value)
        except ValueError as error:
            raise ValueError("unknown validation level") from error
    raise ValueError("expected a validation-level JSON string")


WireValidationLevel = Annotated[
    ValidationLevel,
    BeforeValidator(decode_validation_level),
]


class ExplicitNumericBounds(StrictFrozenModel):
    binding_kind: Literal["explicit"]
    minimum: SafeCanonicalInt | StrictCanonicalFloat
    maximum: SafeCanonicalInt | StrictCanonicalFloat
    minimum_inclusive: bool
    maximum_inclusive: bool

    @model_validator(mode="after")
    def validate_nonempty_interval(self):
        if self.minimum > self.maximum:
            raise ValueError("numeric minimum must not exceed maximum")
        if self.minimum == self.maximum and not (
            self.minimum_inclusive and self.maximum_inclusive
        ):
            raise ValueError("equal numeric bounds must form a closed singleton")
        return self


def validate_symbolic_binding(
    symbol_id: str,
    binding_expression: str,
    evidence_ids: FrozenSequence[str],
) -> None:
    if not symbol_id or not binding_expression:
        raise ValueError("symbolic binding identity and expression must be nonempty")
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("symbolic binding evidence IDs must be unique")


class SymbolicNumericBounds(StrictFrozenModel):
    binding_kind: Literal["symbolic"]
    symbol_id: str
    binding_expression: str
    evidence_ids: FrozenSequence[str]

    @model_validator(mode="after")
    def validate_binding(self):
        validate_symbolic_binding(
            self.symbol_id,
            self.binding_expression,
            self.evidence_ids,
        )
        return self


NumericBounds = Annotated[
    Annotated[
        ExplicitNumericBounds | SymbolicNumericBounds,
        Field(discriminator="binding_kind"),
    ],
    BeforeValidator(normalize_discriminated_model_input),
]


class FixedDimension(StrictFrozenModel):
    dimension_kind: Literal["fixed"]
    size: PositiveSafeCanonicalInt


class SymbolicDimension(StrictFrozenModel):
    dimension_kind: Literal["symbolic"]
    symbol_id: str
    binding_expression: str
    evidence_ids: FrozenSequence[str]

    @model_validator(mode="after")
    def validate_binding(self):
        validate_symbolic_binding(
            self.symbol_id,
            self.binding_expression,
            self.evidence_ids,
        )
        return self


ShapeDimension = Annotated[
    Annotated[
        FixedDimension | SymbolicDimension,
        Field(discriminator="dimension_kind"),
    ],
    BeforeValidator(normalize_discriminated_model_input),
]


def validate_numeric_element_domain(
    element_dtype: Literal["bool", "int", "float"],
    bounds: NumericBounds | None,
) -> None:
    if element_dtype == "bool":
        if bounds is not None:
            raise ValueError("bool elements must not declare numeric bounds")
        return
    if bounds is None:
        raise ValueError("numeric elements require explicit or symbolic bounds")
    if isinstance(bounds, ExplicitNumericBounds):
        expected_type = int if element_dtype == "int" else float
        if type(bounds.minimum) is not expected_type or type(bounds.maximum) is not expected_type:
            raise ValueError("explicit bounds must match the element dtype exactly")


class ScalarDomain(StrictFrozenModel):
    kind: Literal["scalar"]
    element_dtype: Literal["int", "float"]
    bounds: NumericBounds

    @model_validator(mode="after")
    def validate_element_domain(self):
        validate_numeric_element_domain(self.element_dtype, self.bounds)
        return self


class VectorDomain(StrictFrozenModel):
    kind: Literal["vector"]
    element_dtype: Literal["int", "float"]
    shape: FrozenSequence[ShapeDimension]
    bounds: NumericBounds

    @model_validator(mode="after")
    def validate_vector_domain(self):
        if len(self.shape) != 1:
            raise ValueError("vector domains require exactly one dimension")
        validate_numeric_element_domain(self.element_dtype, self.bounds)
        return self


class TensorDomain(StrictFrozenModel):
    kind: Literal["tensor"]
    element_dtype: Literal["bool", "int", "float"]
    shape: FrozenSequence[ShapeDimension]
    bounds: NumericBounds | None

    @model_validator(mode="after")
    def validate_tensor_domain(self):
        if len(self.shape) < 2:
            raise ValueError("tensor domains require rank of at least two")
        validate_numeric_element_domain(self.element_dtype, self.bounds)
        return self


class CategoricalDomain(StrictFrozenModel):
    kind: Literal["categorical"]
    values: FrozenSequence[str]
    ordered: bool

    @model_validator(mode="after")
    def validate_values(self):
        if not self.values or any(not value for value in self.values):
            raise ValueError("categorical values must be nonempty strings")
        if len(set(self.values)) != len(self.values):
            raise ValueError("categorical values must be unique")
        return self


class TextDomain(StrictFrozenModel):
    kind: Literal["text"]
    encoding: Literal["utf-8"]
    max_length: ShapeDimension


class BinaryDomain(StrictFrozenModel):
    kind: Literal["binary"]
    shape: FrozenSequence[ShapeDimension]


VariableDomain = Annotated[
    Annotated[
        ScalarDomain
        | VectorDomain
        | TensorDomain
        | CategoricalDomain
        | TextDomain
        | BinaryDomain,
        Field(discriminator="kind"),
    ],
    BeforeValidator(normalize_discriminated_model_input),
]


class VariableSpec(StrictFrozenModel):
    name: str
    domain: VariableDomain
    unit: str | None
    semantic_definition: str
    evidence_ids: FrozenSequence[str]

    @model_validator(mode="after")
    def validate_variable(self):
        if not self.name or not self.semantic_definition:
            raise ValueError("variable name and semantic definition must be nonempty")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("variable evidence IDs must be unique")
        if isinstance(self.domain, (CategoricalDomain, TextDomain, BinaryDomain)):
            if self.unit is not None:
                raise ValueError("categorical, text, and binary variables have no unit")
        return self


class HistoryAccessSpec(StrictFrozenModel):
    observation_lags: FrozenSequence[NonNegativeSafeCanonicalInt]
    action_lags: FrozenSequence[NonNegativeSafeCanonicalInt]
    reward_lags: FrozenSequence[NonNegativeSafeCanonicalInt]
    message_lags: FrozenSequence[NonNegativeSafeCanonicalInt]
    recurrent_state_allowed: bool
    boundary_reset: Literal["episode", "life", "never"]

    @model_validator(mode="after")
    def validate_history_lags(self):
        lag_sets = (
            self.observation_lags,
            self.action_lags,
            self.reward_lags,
            self.message_lags,
        )
        if any(len(set(lags)) != len(lags) for lags in lag_sets):
            raise ValueError("history lag sets must not contain duplicates")
        return self


class AgentMessageSender(StrictFrozenModel):
    sender_kind: Literal["agent"]
    agent_id: str


class EnvironmentMessageSender(StrictFrozenModel):
    sender_kind: Literal["environment"]
    process_id: str


class ExternalMessageSender(StrictFrozenModel):
    sender_kind: Literal["external"]
    source_id: str


MessageSender = Annotated[
    Annotated[
        AgentMessageSender | EnvironmentMessageSender | ExternalMessageSender,
        Field(discriminator="sender_kind"),
    ],
    BeforeValidator(normalize_discriminated_model_input),
]


class DeterministicMessageDelay(StrictFrozenModel):
    delay_kind: Literal["deterministic"]
    steps: NonNegativeSafeCanonicalInt


class StochasticMessageDelay(StrictFrozenModel):
    delay_kind: Literal["stochastic"]
    distribution_family: str
    parameters: FrozenStringMapping[
        str | SafeCanonicalInt | StrictCanonicalFloat
    ]
    support_steps: FrozenSequence[NonNegativeSafeCanonicalInt]

    @model_validator(mode="after")
    def validate_delay_law(self):
        if not self.distribution_family or not self.support_steps:
            raise ValueError("stochastic delay requires a family and support")
        if len(set(self.support_steps)) != len(self.support_steps):
            raise ValueError("stochastic delay support must be unique")
        return self


MessageDelayLaw = Annotated[
    Annotated[
        DeterministicMessageDelay | StochasticMessageDelay,
        Field(discriminator="delay_kind"),
    ],
    BeforeValidator(normalize_discriminated_model_input),
]


class MessageProcessSpec(StrictFrozenModel):
    message_process_id: str
    sender: MessageSender
    recipient_id: str
    channel_id: str
    space: FrozenSequence[VariableSpec]
    delivery_kernel: str
    delay_law: MessageDelayLaw

    @model_validator(mode="after")
    def validate_message_process(self):
        if any(
            not value
            for value in (
                self.message_process_id,
                self.recipient_id,
                self.channel_id,
                self.delivery_kernel,
            )
        ):
            raise ValueError("message identities and delivery kernel must be nonempty")
        if not self.space:
            raise ValueError("message space must contain at least one variable")
        names = tuple(variable.name for variable in self.space)
        if len(set(names)) != len(names):
            raise ValueError("message-space variable names must be unique")
        if isinstance(self.sender, AgentMessageSender) and not self.sender.agent_id:
            raise ValueError("agent sender ID must be nonempty")
        if isinstance(self.sender, EnvironmentMessageSender) and not self.sender.process_id:
            raise ValueError("environment process ID must be nonempty")
        if isinstance(self.sender, ExternalMessageSender) and not self.sender.source_id:
            raise ValueError("external source ID must be nonempty")
        return self


def validate_message_recipient(
    agent_ids: set[str],
    recipient_id: str,
    history_access: HistoryAccessSpec,
    processes: FrozenSequence[MessageProcessSpec],
    seen_process_ids: set[str],
) -> None:
    if bool(processes) != bool(history_access.message_lags):
        raise ValueError("message lags exist exactly for recipients with message processes")
    for process in processes:
        if process.recipient_id != recipient_id:
            raise ValueError("message process must be stored under its exact recipient")
        if process.message_process_id in seen_process_ids:
            raise ValueError("message process IDs must be globally unique")
        seen_process_ids.add(process.message_process_id)
        if isinstance(process.sender, AgentMessageSender):
            if process.sender.agent_id not in agent_ids:
                raise ValueError("agent message sender must belong to the declared agent set")


class TaskIdentitySpec(StrictFrozenModel):
    name: str
    domain: str
    intended_use: str
    excluded_uses: FrozenSequence[str]


class DecisionMakerSpec(StrictFrozenModel):
    decision_maker_id: str
    controlled_entity_ids: FrozenSequence[str]


class SimultaneousDecisionTiming(StrictFrozenModel):
    timing: Literal["simultaneous"]
    chance_turns: bool
    environment_turns: bool
    cycle_boundary: str


class SequentialDecisionTiming(StrictFrozenModel):
    timing: Literal["sequential"]
    turn_order: FrozenSequence[str]
    chance_turns: bool
    environment_turns: bool
    cycle_boundary: str


class EventDrivenDecisionTiming(StrictFrozenModel):
    timing: Literal["event_driven"]
    event_selection_rule: str
    chance_turns: bool
    environment_turns: bool
    cycle_boundary: str


DecisionTimingSpec = Annotated[
    Annotated[
        SimultaneousDecisionTiming
        | SequentialDecisionTiming
        | EventDrivenDecisionTiming,
        Field(discriminator="timing"),
    ],
    BeforeValidator(normalize_discriminated_model_input),
]


class DecisionStructureSpec(StrictFrozenModel):
    decision_makers: FrozenSequence[DecisionMakerSpec]
    external_entity_ids: FrozenSequence[str]
    coordination: Literal["centralized", "decentralized", "hybrid"]
    decision_timing: DecisionTimingSpec


class TaskObjectiveSpec(StrictFrozenModel):
    primary_objective: str
    secondary_objectives: FrozenSequence[str]
    success_criteria: FrozenSequence[str]
    tradeoffs: FrozenSequence[str]


class TaskInformationSpec(StrictFrozenModel):
    observable_variables_by_decision_maker: FrozenStringMapping[
        FrozenSequence[VariableSpec]
    ]
    latent_variables: FrozenSequence[VariableSpec]
    joint_observation_semantics: str | None
    history_access_by_decision_maker: FrozenStringMapping[HistoryAccessSpec]
    message_processes_by_recipient: FrozenStringMapping[
        FrozenSequence[MessageProcessSpec]
    ]


class TaskDynamicsSpec(StrictFrozenModel):
    exogenous_processes: FrozenSequence[str]
    stochastic_assumptions: FrozenSequence[str]
    intervention_effects: FrozenSequence[str]
    reward_randomness: FrozenSequence[str]
    time_step: str
    horizon_binding: str


class TaskConstraintsSpec(StrictFrozenModel):
    hard_constraints: FrozenSequence[str]
    soft_constraints: FrozenSequence[str]
    safety_constraints: FrozenSequence[str]
    resource_limits: FrozenSequence[str]


class TaskRisksSpec(StrictFrozenModel):
    failure_events: FrozenSequence[str]
    risk_measures: FrozenSequence[str]
    tolerances: FrozenSequence[str]
    tail_or_worst_case_requirements: FrozenSequence[str]


class TaskEpisodeSpec(StrictFrozenModel):
    reset_conditions: FrozenSequence[str]
    termination_conditions: FrozenSequence[str]
    truncation_conditions: FrozenSequence[str]


class AcceptedAssumptionSpec(StrictFrozenModel):
    assumption_id: str
    statement: str
    evidence_ids: FrozenSequence[str]

    @model_validator(mode="after")
    def validate_assumption(self):
        if not self.assumption_id.strip() or not self.statement.strip():
            raise ValueError("accepted assumptions require nonblank ID and statement")
        if any(not evidence_id.strip() for evidence_id in self.evidence_ids):
            raise ValueError("accepted-assumption evidence IDs must be nonblank")
        return self


class UnresolvedQuestionSpec(StrictFrozenModel):
    question_id: str
    severity: Literal["low", "medium", "high", "critical"]
    target_path: str
    question: str

    @model_validator(mode="after")
    def validate_question(self):
        if any(
            not value.strip()
            for value in (self.question_id, self.target_path, self.question)
        ):
            raise ValueError("unresolved questions require nonblank fields")
        return self


class TaskEvidenceSpec(StrictFrozenModel):
    evidence_ids: FrozenSequence[str]
    accepted_assumptions: FrozenSequence[AcceptedAssumptionSpec]
    unresolved_questions: FrozenSequence[UnresolvedQuestionSpec]


class TaskValidationTargetSpec(StrictFrozenModel):
    required_level: WireValidationLevel
    required_properties: FrozenSequence[str]
    accepted_tolerances: FrozenSequence[str]


class TaskContract(StrictFrozenModel):
    schema_version: Literal["automarkov.task-contract.v1"]
    contract_kind: Literal["core_task"]
    task_identity: TaskIdentitySpec
    decision_structure: DecisionStructureSpec
    objective: TaskObjectiveSpec
    information: TaskInformationSpec
    dynamics: TaskDynamicsSpec
    constraints: TaskConstraintsSpec
    risks: TaskRisksSpec
    episode: TaskEpisodeSpec
    evidence_and_assumptions: TaskEvidenceSpec
    validation_target: TaskValidationTargetSpec

    @model_validator(mode="after")
    def validate_task_contract(self):
        identity = self.task_identity
        if not identity.name or not identity.domain or not identity.intended_use:
            raise ValueError("task identity fields must be nonempty")

        makers = tuple(self.decision_structure.decision_makers)
        maker_ids = tuple(maker.decision_maker_id for maker in makers)
        if not maker_ids or any(not maker_id for maker_id in maker_ids):
            raise ValueError("at least one nonempty decision-maker ID is required")
        if len(set(maker_ids)) != len(maker_ids):
            raise ValueError("decision-maker IDs must be unique")
        controlled_ids = tuple(
            entity_id
            for maker in makers
            for entity_id in maker.controlled_entity_ids
        )
        if not controlled_ids or any(not entity_id for entity_id in controlled_ids):
            raise ValueError("at least one nonempty controlled-entity ID is required")
        if any(not maker.controlled_entity_ids for maker in makers):
            raise ValueError("each decision maker must control at least one entity")
        if len(set(controlled_ids)) != len(controlled_ids):
            raise ValueError("each controlled entity must have exactly one owner")
        external_ids = tuple(self.decision_structure.external_entity_ids)
        if len(set(external_ids)) != len(external_ids) or any(
            not entity_id for entity_id in external_ids
        ):
            raise ValueError("external-entity IDs must be unique and nonempty")
        if set(controlled_ids) & set(external_ids):
            raise ValueError("controlled and external entities must be disjoint")
        timing = self.decision_structure.decision_timing
        if not timing.cycle_boundary:
            raise ValueError("decision timing requires a cycle boundary")
        if isinstance(timing, SequentialDecisionTiming):
            if len(set(timing.turn_order)) != len(timing.turn_order):
                raise ValueError("sequential turn order must be unique")
            if set(timing.turn_order) != set(maker_ids):
                raise ValueError("sequential turn order must cover every decision maker")
        if isinstance(timing, EventDrivenDecisionTiming) and not timing.event_selection_rule:
            raise ValueError("event-driven timing requires an event-selection rule")

        information = self.information
        expected_keys = set(maker_ids)
        if set(information.observable_variables_by_decision_maker) != expected_keys:
            raise ValueError("observable-variable keyset must match decision makers")
        if set(information.history_access_by_decision_maker) != expected_keys:
            raise ValueError("history-access keyset must match decision makers")
        if set(information.message_processes_by_recipient) != expected_keys:
            raise ValueError("message-recipient keyset must match decision makers")
        seen_process_ids: set[str] = set()
        for maker_id in maker_ids:
            observations = information.observable_variables_by_decision_maker[maker_id]
            if not observations:
                raise ValueError("each decision maker requires an observation contract")
            observation_names = tuple(variable.name for variable in observations)
            if len(set(observation_names)) != len(observation_names):
                raise ValueError("per-maker observation names must be unique")
            validate_message_recipient(
                expected_keys,
                maker_id,
                information.history_access_by_decision_maker[maker_id],
                information.message_processes_by_recipient[maker_id],
                seen_process_ids,
            )
        latent_names = tuple(variable.name for variable in information.latent_variables)
        if len(set(latent_names)) != len(latent_names):
            raise ValueError("latent-variable names must be unique")
        observed_names = {
            variable.name
            for variables in information.observable_variables_by_decision_maker.values()
            for variable in variables
        }
        if observed_names & set(latent_names):
            raise ValueError("observable and latent variable names must be disjoint")
        if len(maker_ids) > 1 and not information.joint_observation_semantics:
            raise ValueError("multi-maker tasks require joint-observation semantics")

        objective = self.objective
        if (
            not objective.primary_objective
            or not objective.success_criteria
            or any(not criterion.strip() for criterion in objective.success_criteria)
        ):
            raise ValueError("primary objective and success criteria are required")
        if not self.dynamics.time_step or not self.dynamics.horizon_binding:
            raise ValueError("time step and horizon binding are required")
        episode = self.episode
        if (
            not episode.reset_conditions
            or any(not condition.strip() for condition in episode.reset_conditions)
            or not (episode.termination_conditions or episode.truncation_conditions)
            or any(
                not condition.strip()
                for condition in (
                    *episode.termination_conditions,
                    *episode.truncation_conditions,
                )
            )
        ):
            raise ValueError("reset and an episode boundary are required")
        if not self.validation_target.required_properties or any(
            not property_name.strip()
            for property_name in self.validation_target.required_properties
        ):
            raise ValueError("validation target requires at least one property")

        evidence = self.evidence_and_assumptions
        if len(set(evidence.evidence_ids)) != len(evidence.evidence_ids):
            raise ValueError("contract evidence IDs must be unique")
        assumption_ids = tuple(
            assumption.assumption_id for assumption in evidence.accepted_assumptions
        )
        question_ids = tuple(
            question.question_id for question in evidence.unresolved_questions
        )
        if len(set(assumption_ids)) != len(assumption_ids):
            raise ValueError("accepted-assumption IDs must be unique")
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("unresolved-question IDs must be unique")
        return self


def validate_task_contract_for_approval(value: object) -> TaskContract:
    validate_and_measure_raw_json_tree(value)
    contract = TaskContract.model_validate(value)
    if any(
        question.severity in {"high", "critical"}
        for question in contract.evidence_and_assumptions.unresolved_questions
    ):
        raise ValueError("high or critical questions block TaskContract approval")
    return contract


def validate_task_contract_json_for_approval(raw: bytes) -> TaskContract:
    return validate_task_contract_for_approval(parse_json_payload(raw))


class DeterministicRewardSpec(StrictFrozenModel):
    mode: Literal["deterministic"]
    expression: str

    @model_validator(mode="after")
    def validate_reward_expression(self):
        if not self.expression:
            raise ValueError("deterministic reward expression must be nonempty")
        return self


class StochasticRewardSpec(StrictFrozenModel):
    mode: Literal["stochastic"]
    distribution_family: str
    parameters: FrozenStringMapping[str | SafeCanonicalInt | StrictCanonicalFloat]
    support: FrozenStringMapping[CanonicalJsonValue]
    conditional_on: FrozenSequence[str]
    correlation_group: str | None
    expectation_expression: str

    @model_validator(mode="after")
    def validate_reward_law(self):
        if not self.distribution_family or not self.support:
            raise ValueError("stochastic reward requires a family and support")
        if not self.expectation_expression:
            raise ValueError("stochastic reward expectation must be nonempty")
        if self.correlation_group == "":
            raise ValueError("independent reward uses null, not an empty group")
        if len(set(self.conditional_on)) != len(self.conditional_on):
            raise ValueError("reward conditioning variables must be unique")
        return self


RewardLaw = Annotated[
    Annotated[
        DeterministicRewardSpec | StochasticRewardSpec,
        Field(discriminator="mode"),
    ],
    BeforeValidator(normalize_discriminated_model_input),
]


class JointRewardDependencySpec(StrictFrozenModel):
    correlation_group: str
    member_agent_ids: FrozenSequence[str]
    joint_distribution_family: str
    parameters: FrozenStringMapping[
        str | SafeCanonicalInt | StrictCanonicalFloat
    ]
    support: FrozenStringMapping[CanonicalJsonValue]
    conditional_on: FrozenSequence[str]
    joint_kernel: str
    marginal_laws_by_agent: FrozenStringMapping[str]

    @model_validator(mode="after")
    def validate_joint_reward_definition(self):
        members = tuple(self.member_agent_ids)
        if not self.correlation_group or not self.joint_distribution_family:
            raise ValueError("joint reward group and distribution must be nonempty")
        if len(members) < 2 or len(set(members)) != len(members):
            raise ValueError("joint reward group requires unique multiple agents")
        if not self.support:
            raise ValueError("joint reward support must be nonempty")
        if len(set(self.conditional_on)) != len(self.conditional_on):
            raise ValueError("joint reward conditioning variables must be unique")
        if not self.joint_kernel:
            raise ValueError("joint reward kernel must be nonempty")
        if set(self.marginal_laws_by_agent) != set(members):
            raise ValueError("joint reward marginals must match group members")
        if any(not law for law in self.marginal_laws_by_agent.values()):
            raise ValueError("joint reward marginal laws must be nonempty")
        return self


def validate_joint_reward_dependencies(
    agent_ids: FrozenSequence[str],
    rewards_by_agent: FrozenStringMapping[RewardLaw],
    dependencies: FrozenSequence[JointRewardDependencySpec],
) -> None:
    known_agents = set(agent_ids)
    groups = tuple(dependencies)
    group_names = tuple(group.correlation_group for group in groups)
    if len(set(group_names)) != len(group_names):
        raise ValueError("joint reward correlation groups must be unique")

    referenced: dict[str, set[str]] = {}
    for agent_id, reward in rewards_by_agent.items():
        if isinstance(reward, StochasticRewardSpec) and reward.correlation_group:
            referenced.setdefault(reward.correlation_group, set()).add(agent_id)

    defined = {group.correlation_group: group for group in groups}
    if set(defined) != set(referenced):
        raise ValueError("joint reward groups must be defined exactly once")
    for group_name, group in defined.items():
        members = set(group.member_agent_ids)
        if not members <= known_agents or members != referenced[group_name]:
            raise ValueError("joint reward members must match tagged reward laws")


class ObjectiveSpec(StrictFrozenModel):
    objective_id: str
    owner_ids: FrozenSequence[str]
    direction: Literal["maximize", "minimize", "satisfice"]
    functional: str
    aggregation: Literal["discounted_sum", "average", "terminal", "lexicographic", "pareto"]
    priority: NonNegativeSafeCanonicalInt
    success_threshold: StrictCanonicalFloat | None

    @model_validator(mode="after")
    def validate_satisfaction_threshold(self):
        if not self.objective_id or not self.functional:
            raise ValueError("objective identity and functional must be nonempty")
        if not self.owner_ids or any(not owner_id for owner_id in self.owner_ids):
            raise ValueError("objective owners must be nonempty")
        if len(set(self.owner_ids)) != len(self.owner_ids):
            raise ValueError("objective owners must be unique")
        if (self.direction == "satisfice") != (self.success_threshold is not None):
            raise ValueError(
                "success_threshold is required exactly for satisfice objectives"
            )
        return self


class ConstraintSpec(StrictFrozenModel):
    constraint_id: str
    kind: Literal["hard", "soft", "chance", "budget", "safety"]
    predicate: str
    scope: Literal["state", "action", "transition", "trajectory", "population"]
    violation_response: Literal["mask", "reject", "terminate", "penalize", "report"]
    max_violation_probability: ProbabilityCanonicalFloat | None

    @model_validator(mode="after")
    def validate_chance_constraint(self):
        if not self.constraint_id or not self.predicate:
            raise ValueError("constraint identity and predicate must be nonempty")
        if (self.kind == "chance") != (self.max_violation_probability is not None):
            raise ValueError(
                "max_violation_probability is required exactly for chance constraints"
            )
        return self


class RiskSpec(StrictFrozenModel):
    risk_id: str
    measure: Literal["failure_probability", "var", "cvar", "worst_case", "regret"]
    outcome_expression: str
    confidence_level: ConfidenceCanonicalFloat | None
    tolerance: NonNegativeCanonicalFloat
    evaluation_horizon: PositiveSafeCanonicalInt | None

    @model_validator(mode="after")
    def validate_measure_parameters(self):
        if not self.risk_id or not self.outcome_expression:
            raise ValueError("risk identity and outcome expression must be nonempty")
        needs_level = self.measure in {"var", "cvar"}
        if needs_level != (self.confidence_level is not None):
            raise ValueError(
                "confidence_level is required exactly for var and cvar"
            )
        if self.measure == "failure_probability" and self.tolerance > 1.0:
            raise ValueError("failure-probability tolerance must be in [0, 1]")
        return self


class JointObservationKernelSpec(StrictFrozenModel):
    joint_space: FrozenSequence[VariableSpec]
    kernel: str
    conditional_on: FrozenSequence[str]
    per_agent_projection: FrozenStringMapping[FrozenSequence[str]]
    cross_agent_correlations: FrozenSequence[str]

    @model_validator(mode="after")
    def validate_joint_observation(self):
        names = tuple(variable.name for variable in self.joint_space)
        if not names or len(set(names)) != len(names):
            raise ValueError("joint-observation variable names must be nonempty and unique")
        if not self.kernel:
            raise ValueError("joint-observation kernel must be nonempty")
        declared = set(names)
        for projection in self.per_agent_projection.values():
            if len(set(projection)) != len(projection) or not set(projection) <= declared:
                raise ValueError("agent projections must uniquely reference joint-space names")
        return self


class AECTurnSpec(StrictFrozenModel):
    active_actor_function: str
    possible_turn_owners: FrozenSequence[str]
    chance_turns: bool
    environment_turns: bool
    cycle_boundary: str
    state_update_timing: Literal["after_each_turn", "end_of_cycle"]
    reward_accumulation: Literal["per_turn", "until_agent_next_acts", "end_of_cycle"]
    dead_agent_action: Literal["none_only"]

    @model_validator(mode="after")
    def validate_turn_contract(self):
        if not self.active_actor_function or not self.cycle_boundary:
            raise ValueError("AEC actor function and cycle boundary must be nonempty")
        if not self.possible_turn_owners or any(
            not owner_id for owner_id in self.possible_turn_owners
        ):
            raise ValueError("AEC turn owners must be nonempty")
        if len(set(self.possible_turn_owners)) != len(self.possible_turn_owners):
            raise ValueError("AEC turn owners must be unique")
        return self


class ClarificationGap(StrictFrozenModel):
    target_path: str
    question: str
    consequence: str
    evidence_ids: FrozenSequence[str]

    @model_validator(mode="after")
    def validate_gap(self):
        if any(
            not value.strip()
            for value in (self.target_path, self.question, self.consequence)
        ):
            raise ValueError("clarification gaps require nonblank fields")
        if any(not evidence_id.strip() for evidence_id in self.evidence_ids):
            raise ValueError("clarification-gap evidence IDs must be nonblank")
        return self


class ClarificationRequiredResult(StrictFrozenModel):
    result_kind: Literal["clarification_required"]
    task_artifact_id: str
    review_report_artifact_id: str
    identified_gaps: FrozenSequence[ClarificationGap]
    introduced_assumptions: FrozenSequence[str]
    formal_artifact_ids: FrozenSequence[str]
    environment_artifact_ids: FrozenSequence[str]

    @model_validator(mode="after")
    def validate_result(self):
        if any(
            not artifact_id.strip()
            for artifact_id in (self.task_artifact_id, self.review_report_artifact_id)
        ):
            raise ValueError("clarification results require nonblank artifact IDs")
        if not self.identified_gaps:
            raise ValueError("clarification results require at least one gap")
        gap_keys = tuple(
            (gap.target_path, gap.question) for gap in self.identified_gaps
        )
        if len(set(gap_keys)) != len(gap_keys):
            raise ValueError("clarification gaps must be unique")
        if (
            self.introduced_assumptions
            or self.formal_artifact_ids
            or self.environment_artifact_ids
        ):
            raise ValueError("clarification results must not guess or formalize")
        return self


class ExperimentClarificationRequiredResult(StrictFrozenModel):
    result_kind: Literal["experiment_clarification_required"]
    clarification: ClarificationRequiredResult
    outcome_mask_id: str
    variant_id: Literal["v5_clarification_required"]
    track: Literal["AUTO"]

    @model_validator(mode="after")
    def validate_experiment_binding(self):
        if not self.outcome_mask_id.strip():
            raise ValueError("experiment clarification requires an outcome mask")
        return self


class EvidenceOmissionRecord(StrictFrozenModel):
    schema_version: Literal["automarkov.evidence-omission.v1"]
    record_kind: Literal["evidence_omitted_by_design"]
    experiment_id: str
    run_id: str
    cell_id: str
    task_card_artifact_id: str
    ablation_execution_plan_artifact_id: str
    pair_binding_id: str
    ablation_method_id: Literal["automarkov_no_evidence"]
    omitted_gate_id: Literal["EVIDENCE_LEDGER_CLOSURE"]
    reason: Literal["controlled_ablation"]


class EvidenceLedgerBinding(StrictFrozenModel):
    binding_kind: Literal["ledger"]
    evidence_ledger_artifact_id: str


class EvidenceOmissionBinding(StrictFrozenModel):
    binding_kind: Literal["omitted_by_design"]
    omission_record_artifact_id: str
    ablation_method_id: Literal["automarkov_no_evidence"]
    omitted_gate_id: Literal["EVIDENCE_LEDGER_CLOSURE"]


EvidenceBinding = Annotated[
    Annotated[
        EvidenceLedgerBinding | EvidenceOmissionBinding,
        Field(discriminator="binding_kind"),
    ],
    BeforeValidator(normalize_discriminated_model_input),
]


FullSemanticVariantId = Literal[
    "v1_canonical",
    "v2_paraphrased",
    "v3_reordered_longform",
    "v4_evidence_split",
]


class GateOmittedByDesignBase(StrictFrozenModel):
    schema_version: Literal["automarkov.gate-omitted-event.v1"]
    signing_domain: Literal["AutoMarkov-Gate-Omitted-v1"]
    event_type: Literal["GateOmittedByDesign"]
    event_id: str
    experiment_id: str
    run_id: str
    sequence_no: NonNegativeSafeCanonicalInt
    previous_event_hash: str
    track: Literal["AUTO"]
    variant_id: FullSemanticVariantId
    cell_id: str
    ablation_execution_plan_artifact_id: str
    ablation_execution_plan_hash: str
    pair_binding_id: str
    task_card_artifact_id: str
    subject_artifact_ids: FrozenSequence[str]
    expected_missing_artifact_kinds: FrozenSequence[str]
    output_artifact_ids: FrozenSequence[str]
    reason: Literal["controlled_ablation"]
    issued_at: str
    nonce_b64url: str
    signing_key_id: str
    signature_b64url: str

    @model_validator(mode="after")
    def validate_gate_projection(self) -> "GateOmittedByDesignBase":
        contracts = {
            "EVIDENCE_LEDGER_CLOSURE": (0, ("EvidenceLedger",), 1),
            "TEXT_CRITIC_REVIEW": (1, ("TextCriticReport",), 0),
            "FORMAL_CRITIC_REVIEW": (1, ("FormalCriticReport",), 0),
            "PUBLIC_SIMULATION_TESTER": (
                1,
                (
                    "PropertyTestReport",
                    "MetamorphicTestReport",
                    "DifferentialTestReport",
                    "TrajectoryTestReport",
                ),
                0,
            ),
            "PUBLIC_DEV_LEARNING_PROBE_AND_ROLLBACK": (
                1,
                ("PublicDevLearningProbeReport",),
                0,
            ),
        }
        subject_count, missing_kinds, output_count = contracts[
            self.omitted_gate_id
        ]
        if len(self.subject_artifact_ids) != subject_count:
            raise ValueError("wrong subject cardinality for omitted gate")
        if tuple(self.expected_missing_artifact_kinds) != missing_kinds:
            raise ValueError("wrong missing-artifact kinds for omitted gate")
        if len(self.output_artifact_ids) != output_count:
            raise ValueError("wrong output cardinality for omitted gate")
        return self


class EvidenceGateOmitted(GateOmittedByDesignBase):
    ablation_method_id: Literal["automarkov_no_evidence"]
    omitted_gate_id: Literal["EVIDENCE_LEDGER_CLOSURE"]


class TextCriticGateOmitted(GateOmittedByDesignBase):
    ablation_method_id: Literal["automarkov_no_text_critic"]
    omitted_gate_id: Literal["TEXT_CRITIC_REVIEW"]


class FormalCriticGateOmitted(GateOmittedByDesignBase):
    ablation_method_id: Literal["automarkov_no_formal_critic"]
    omitted_gate_id: Literal["FORMAL_CRITIC_REVIEW"]


class SimulationTesterGateOmitted(GateOmittedByDesignBase):
    ablation_method_id: Literal["automarkov_no_simulation_tester"]
    omitted_gate_id: Literal["PUBLIC_SIMULATION_TESTER"]


class LearningProbeGateOmitted(GateOmittedByDesignBase):
    ablation_method_id: Literal["automarkov_no_training_feedback"]
    omitted_gate_id: Literal["PUBLIC_DEV_LEARNING_PROBE_AND_ROLLBACK"]


GateOmittedByDesign = Annotated[
    Annotated[
        EvidenceGateOmitted
        | TextCriticGateOmitted
        | FormalCriticGateOmitted
        | SimulationTesterGateOmitted
        | LearningProbeGateOmitted,
        Field(discriminator="omitted_gate_id"),
    ],
    BeforeValidator(normalize_discriminated_model_input),
]


class ClassificationResult(StrictFrozenModel):
    result_kind: Literal["classification"]
    source_task_artifact_id: str
    evidence_binding: EvidenceBinding
    classification: Literal[
        "IN_SCOPE_MDP", "IN_SCOPE_POMDP", "IN_SCOPE_MG", "IN_SCOPE_POSG",
        "REDUCIBLE", "OOD",
    ]
    rationale: FrozenSequence[str]


class ReductionAssumption(StrictFrozenModel):
    assumption_id: str
    kind: Literal[
        "finite_state", "discretization", "rewardization", "horizon",
        "chance", "information_structure", "other",
    ]
    statement: str
    semantic_loss: str
    evidence_ids: FrozenSequence[str]


class ReductionProposal(StrictFrozenModel):
    proposal_kind: Literal["decision_process_reduction"]
    source_task_artifact_id: str
    classification_artifact_id: str
    target_kind: Literal["MDP", "POMDP", "MG", "POSG"]
    assumptions: FrozenSequence[ReductionAssumption]
    preserved_properties: FrozenSequence[str]
    lost_properties: FrozenSequence[str]
    supersedes_proposal_artifact_id: str | None
    trigger_classification_artifact_id: str | None
    approval_required: StrictTrue


class ValidationClaim(StrictFrozenModel):
    subject_artifact_id: str
    level: WireValidationLevel
    validator_id: str
    validator_version: str
    report_artifact_ids: FrozenSequence[str]
    passed: StrictTrue
    scope: FrozenSequence[str]
    assumptions: FrozenSequence[str]


def validate_single_agent_structure(
    spec: "DecisionProcessBase",
    agent_id: str,
) -> None:
    if not agent_id:
        raise ValueError("single-agent decision process requires a nonempty agent ID")
    if set(spec.actions_by_agent) != {agent_id}:
        raise ValueError("single-agent action keyset must equal the singleton agent set")
    for objective in spec.objectives:
        if tuple(objective.owner_ids) != (agent_id,):
            raise ValueError("single-agent objective owner must equal the agent ID")


def validate_single_agent_reward(reward: RewardLaw) -> None:
    if isinstance(reward, StochasticRewardSpec) and reward.correlation_group is not None:
        raise ValueError("single-agent stochastic reward cannot declare a correlation group")


def validate_multi_agent_structure(
    spec: "DecisionProcessBase",
    agent_ids: FrozenSequence[str],
    required_mappings: FrozenSequence[Mapping[str, object]],
) -> set[str]:
    identities = tuple(agent_ids)
    agent_set = set(identities)
    if len(identities) < 2:
        raise ValueError("multi-agent decision process requires at least two agents")
    if any(not agent_id for agent_id in identities):
        raise ValueError("multi-agent IDs must be nonempty")
    if len(agent_set) != len(identities):
        raise ValueError("multi-agent IDs must be unique")
    if set(spec.actions_by_agent) != agent_set:
        raise ValueError("multi-agent action keyset must equal the agent set")
    if any(set(mapping) != agent_set for mapping in required_mappings):
        raise ValueError("every required per-agent keyset must equal the agent set")

    covered_owners: set[str] = set()
    for objective in spec.objectives:
        owners = set(objective.owner_ids)
        if not owners <= agent_set:
            raise ValueError("objective owners must belong to the agent set")
        covered_owners.update(owners)
    if covered_owners != agent_set:
        raise ValueError("objective owners must collectively cover every agent")
    return agent_set


def validate_action_timing(
    agent_set: set[str],
    action_timing: Literal["simultaneous", "aec"],
    aec_turn: AECTurnSpec | None,
) -> None:
    if action_timing == "simultaneous":
        if aec_turn is not None:
            raise ValueError("simultaneous action timing forbids an AEC turn spec")
        return
    if aec_turn is None:
        raise ValueError("AEC action timing requires an AEC turn spec")
    if set(aec_turn.possible_turn_owners) != agent_set:
        raise ValueError("AEC turn owners must equal the agent set")


class DecisionProcessBase(StrictFrozenModel):
    schema_version: Literal["automarkov.decision-process-spec.v1"]
    state_variables: FrozenSequence[VariableSpec]
    actions_by_agent: FrozenStringMapping[FrozenSequence[VariableSpec]]
    transition_kernel: str
    initial_distribution: str
    objectives: FrozenSequence[ObjectiveSpec]
    constraints: FrozenSequence[ConstraintSpec]
    risks: FrozenSequence[RiskSpec]
    horizon: PositiveSafeCanonicalInt | Literal["infinite"]
    discount: ProbabilityCanonicalFloat
    termination_predicates: FrozenSequence[str]
    truncation_predicates: FrozenSequence[str]

    @model_validator(mode="after")
    def validate_common_structure(self):
        state_names = tuple(variable.name for variable in self.state_variables)
        if not state_names or len(set(state_names)) != len(state_names):
            raise ValueError("state-variable names must be nonempty and unique")
        if not self.actions_by_agent:
            raise ValueError("at least one action mapping is required")
        for agent_id, actions in self.actions_by_agent.items():
            if not agent_id or not actions:
                raise ValueError("action mappings require nonempty agent IDs and spaces")
            action_names = tuple(variable.name for variable in actions)
            if len(set(action_names)) != len(action_names):
                raise ValueError("per-agent action-variable names must be unique")
        if not self.transition_kernel or not self.initial_distribution:
            raise ValueError("transition kernel and initial distribution must be nonempty")
        if not self.objectives:
            raise ValueError("at least one objective is required")
        identity_groups = (
            tuple(objective.objective_id for objective in self.objectives),
            tuple(constraint.constraint_id for constraint in self.constraints),
            tuple(risk.risk_id for risk in self.risks),
        )
        if any(len(set(identities)) != len(identities) for identities in identity_groups):
            raise ValueError("objective, constraint, and risk IDs must each be unique")
        terminal = tuple(self.termination_predicates)
        truncated = tuple(self.truncation_predicates)
        if any(not predicate for predicate in terminal + truncated):
            raise ValueError("termination and truncation predicates must be nonempty")
        if len(set(terminal)) != len(terminal) or len(set(truncated)) != len(truncated):
            raise ValueError("termination and truncation predicates must be unique")
        if set(terminal) & set(truncated):
            raise ValueError("termination and truncation predicates must be disjoint")
        if self.horizon == "infinite" and any(
            objective.aggregation == "discounted_sum" for objective in self.objectives
        ):
            if self.discount >= 1.0:
                raise ValueError("infinite discounted-sum objectives require discount < 1")
        return self


class MDPSpec(DecisionProcessBase):
    kind: Literal["MDP"]
    agent_id: str
    state_is_observation: StrictTrue
    reward: RewardLaw

    @model_validator(mode="after")
    def validate_mdp_structure(self):
        validate_single_agent_structure(self, self.agent_id)
        validate_single_agent_reward(self.reward)
        return self


class POMDPSpec(DecisionProcessBase):
    kind: Literal["POMDP"]
    agent_id: str
    observation_space: FrozenSequence[VariableSpec]
    observation_kernel: str
    history_access: HistoryAccessSpec
    message_processes_by_recipient: FrozenStringMapping[
        FrozenSequence[MessageProcessSpec]
    ]
    reward: RewardLaw

    @model_validator(mode="after")
    def validate_information_contract(self):
        validate_single_agent_structure(self, self.agent_id)
        validate_single_agent_reward(self.reward)
        if set(self.message_processes_by_recipient) != {self.agent_id}:
            raise ValueError("POMDP message-recipient keyset must equal the agent set")
        observation_names = tuple(variable.name for variable in self.observation_space)
        if not observation_names or len(set(observation_names)) != len(observation_names):
            raise ValueError("POMDP observation names must be nonempty and unique")
        if not self.observation_kernel:
            raise ValueError("POMDP observation kernel must be nonempty")
        validate_message_recipient(
            {self.agent_id},
            self.agent_id,
            self.history_access,
            self.message_processes_by_recipient[self.agent_id],
            set(),
        )
        return self


class MGSpec(DecisionProcessBase):
    kind: Literal["MG"]
    agent_ids: FrozenSequence[str]
    full_state_access_by_agent: FrozenStringMapping[FrozenSequence[str]]
    joint_action_kernel: str
    rewards_by_agent: FrozenStringMapping[RewardLaw]
    joint_reward_dependencies: FrozenSequence[JointRewardDependencySpec]
    game_form: Literal["cooperative", "zero_sum", "general_sum"]
    solution_concept: str
    action_timing: Literal["simultaneous", "aec"]
    aec_turn: AECTurnSpec | None

    @model_validator(mode="after")
    def validate_mg_structure(self):
        agent_set = validate_multi_agent_structure(
            self,
            self.agent_ids,
            (self.full_state_access_by_agent, self.rewards_by_agent),
        )
        state_names = {variable.name for variable in self.state_variables}
        for projection in self.full_state_access_by_agent.values():
            if len(set(projection)) != len(projection) or set(projection) != state_names:
                raise ValueError("MG actors must receive each full-state variable exactly once")
        if not self.joint_action_kernel or not self.solution_concept:
            raise ValueError("MG joint-action kernel and solution concept must be nonempty")
        validate_action_timing(agent_set, self.action_timing, self.aec_turn)
        validate_joint_reward_dependencies(
            self.agent_ids,
            self.rewards_by_agent,
            self.joint_reward_dependencies,
        )
        return self


class StateTrainingFieldRef(StrictFrozenModel):
    field_kind: Literal["state"]
    variable_name: str


class ObservationTrainingFieldRef(StrictFrozenModel):
    field_kind: Literal["observation"]
    agent_id: str
    variable_name: str


class ActionHistoryTrainingFieldRef(StrictFrozenModel):
    field_kind: Literal["action_history"]
    agent_id: str
    variable_name: str


class RewardHistoryTrainingFieldRef(StrictFrozenModel):
    field_kind: Literal["reward_history"]
    agent_id: str


class MessageHistoryTrainingFieldRef(StrictFrozenModel):
    field_kind: Literal["message_history"]
    agent_id: str
    message_process_id: str


CentralizedTrainingFieldRef = Annotated[
    Annotated[
        StateTrainingFieldRef
        | ObservationTrainingFieldRef
        | ActionHistoryTrainingFieldRef
        | RewardHistoryTrainingFieldRef
        | MessageHistoryTrainingFieldRef,
        Field(discriminator="field_kind"),
    ],
    BeforeValidator(normalize_discriminated_model_input),
]


class POSGSpec(DecisionProcessBase):
    kind: Literal["POSG"]
    agent_ids: FrozenSequence[str]
    joint_observation: JointObservationKernelSpec
    history_access_by_agent: FrozenStringMapping[HistoryAccessSpec]
    message_processes_by_recipient: FrozenStringMapping[
        FrozenSequence[MessageProcessSpec]
    ]
    joint_action_kernel: str
    rewards_by_agent: FrozenStringMapping[RewardLaw]
    joint_reward_dependencies: FrozenSequence[JointRewardDependencySpec]
    game_form: Literal["cooperative", "zero_sum", "general_sum"]
    solution_concept: str
    action_timing: Literal["simultaneous", "aec"]
    aec_turn: AECTurnSpec | None
    centralized_training_fields: FrozenSequence[CentralizedTrainingFieldRef]

    @model_validator(mode="after")
    def validate_posg_contract(self):
        agent_set = validate_multi_agent_structure(
            self,
            self.agent_ids,
            (
                self.rewards_by_agent,
                self.history_access_by_agent,
                self.message_processes_by_recipient,
                self.joint_observation.per_agent_projection,
            ),
        )
        if not self.joint_action_kernel or not self.solution_concept:
            raise ValueError("POSG joint-action kernel and solution concept must be nonempty")
        if any(
            not projection
            for projection in self.joint_observation.per_agent_projection.values()
        ):
            raise ValueError("each POSG actor requires a nonempty observation projection")
        validate_action_timing(agent_set, self.action_timing, self.aec_turn)
        seen_process_ids: set[str] = set()
        for recipient_id in self.agent_ids:
            validate_message_recipient(
                agent_set,
                recipient_id,
                self.history_access_by_agent[recipient_id],
                self.message_processes_by_recipient[recipient_id],
                seen_process_ids,
            )
        state_names = {variable.name for variable in self.state_variables}
        actor_fields: set[tuple[str, ...]] = set()
        valid_fields: set[tuple[str, ...]] = {
            ("state", variable_name) for variable_name in state_names
        }
        message_processes = {
            (recipient_id, process.message_process_id)
            for recipient_id, processes in self.message_processes_by_recipient.items()
            for process in processes
        }
        for agent_id in self.agent_ids:
            history = self.history_access_by_agent[agent_id]
            observation_names = set(
                self.joint_observation.per_agent_projection[agent_id]
            )
            action_names = {
                variable.name for variable in self.actions_by_agent[agent_id]
            }
            actor_fields.update(
                ("observation", agent_id, name) for name in observation_names
            )
            valid_fields.update(
                ("observation", agent_id, name) for name in observation_names
            )
            valid_fields.update(
                ("action_history", agent_id, name) for name in action_names
            )
            if history.action_lags:
                actor_fields.update(
                    ("action_history", agent_id, name) for name in action_names
                )
            valid_fields.add(("reward_history", agent_id))
            if history.reward_lags:
                actor_fields.add(("reward_history", agent_id))
            for recipient_id, process_id in message_processes:
                if recipient_id != agent_id:
                    continue
                field_key = ("message_history", recipient_id, process_id)
                valid_fields.add(field_key)
                if history.message_lags:
                    actor_fields.add(field_key)
        centralized_fields = {
            (
                (field.field_kind, field.variable_name)
                if isinstance(field, StateTrainingFieldRef)
                else (
                    (field.field_kind, field.agent_id)
                    if isinstance(field, RewardHistoryTrainingFieldRef)
                    else (
                        field.field_kind,
                        field.agent_id,
                        (
                            field.message_process_id
                            if isinstance(field, MessageHistoryTrainingFieldRef)
                            else field.variable_name
                        ),
                    )
                )
            )
            for field in self.centralized_training_fields
        }
        if len(set(self.centralized_training_fields)) != len(
            self.centralized_training_fields
        ):
            raise ValueError("centralized-training fields must be unique")
        if not centralized_fields <= valid_fields:
            raise ValueError("centralized-training fields must reference declared inputs")
        if actor_fields & centralized_fields:
            raise ValueError("centralized-only fields must not overlap actor inputs")
        validate_joint_reward_dependencies(
            self.agent_ids,
            self.rewards_by_agent,
            self.joint_reward_dependencies,
        )
        return self


DecisionProcessSpec = Annotated[
    Annotated[
        MDPSpec | POMDPSpec | MGSpec | POSGSpec,
        Field(discriminator="kind"),
    ],
    BeforeValidator(normalize_discriminated_model_input),
]

decision_process_adapter = TypeAdapter(DecisionProcessSpec)


def validate_decision_process_payload(
    value: object,
) -> MDPSpec | POMDPSpec | MGSpec | POSGSpec:
    if type(value) is not dict:
        raise ValueError("public DecisionProcess ingress requires a raw JSON object")
    validate_and_measure_raw_json_tree(value)
    return cast(
        MDPSpec | POMDPSpec | MGSpec | POSGSpec,
        decision_process_adapter.validate_python(value),
    )


def validate_decision_process_json(
    raw: bytes,
) -> MDPSpec | POMDPSpec | MGSpec | POSGSpec:
    return validate_decision_process_payload(parse_json_payload(raw))
```

`frozen=True` 只保证 field assignment，不自动深冻结嵌套 container。公共轮廓中的每个持久化 mapping field 均使用 `FrozenStringMapping[...]`，其 `AfterValidator` 复制为 read-only mapping，递归 `PlainSerializer` 只在 JSON dump 时恢复普通 dict。每个 schema-declared repeated field 使用 `FrozenSequence[...]`，显式接受 JSON parser 产生的 list 或已有 tuple、复制为 tuple，并拒绝其他 iterable；该结构规范化不允许任何 item-level numeric/string coercion。每个持久化 integer field 使用 exact-type safe-integer family：通用参数可用 `SafeCanonicalInt`，lag/priority 使用 nonnegative alias，shape/horizon 使用 positive alias；全部拒绝 bool/float/string 和超出 $[-(2^{53}-1),2^{53}-1]$ 的值。每个 bounded float alias 先执行 `require_exact_float`，再强制其领域区间；discount/probability 是 $[0,1]$，confidence 是 $(0,1)$，risk tolerance 非负。更大整数/实数必须用带领域含义的 decimal string 类型，不得进入 JSON number。`CanonicalJsonValue` 是真实 recursive `TypeAliasType`，只接受 JSON list/dict 作为 Python input，再把所有层冻结为 tuple/read-only mapping；integral float 规范化为 safe integer，`-0.0` 规范化为 integer `0`，领域语义不得区分 signed zero。`WireValidationLevel` 是唯一的 string-backed enum wire adapter：它只将 exact allowlist JSON string 还原为 `ValidationLevel`，拒绝数值、bool、未知字符串和其他 coercion。

public trust seam 只接受原始 JSON object tree 或原始 JSON bytes：Python path 必须调用 `validate_decision_process_payload`，bytes path 必须调用 `validate_decision_process_json`；两者都拒绝顶层或任意深度的 `BaseModel`。这是 provenance 规则，不是一次普通的 revalidation。`model_construct()` 可在 `extra="forbid"` 下静默丢弃原始 extra fields；一旦丢弃，检查 `__dict__` 或 `model_dump()` 不可能恢复并发现它们。因此未知 provenance 的既有 model、`model_copy(update=...)` 和合法 branch instance 全部不能作为 public ingress。repository-internal code 若已持有 model，也必须回到此前认证并持久化的 canonical bytes，重新执行 duplicate-aware parse→raw tree→public adapter；没有 canonical bytes/hash/proof 的 model 单独不构成可复用输入。trust seam 禁止 `model_validate_json`，因为 JSON decoder 会先覆盖 duplicate member；`parse_json_payload` 必须先拒绝 BOM、非法 UTF-8、任意深度 duplicate member、所有 key/value 中的 lone surrogate、unsafe integer、NaN/Infinity token 与 float overflow并保留 int/float token 类型，之后 branch validators 再机械执行 cardinality、keyset、owner、AEC、message、reward 与其他结构不变量。

Python raw-tree path 在构造任何 Pydantic model 前调用 `validate_and_measure_raw_json_tree`。允许的 node exact types 只能是 `dict`、`list`、`str`、`int`、`float`、`bool` 和 `None`，因此 custom mapping/object、tuple 以及这些 built-in 的 subclass（包括 `str` subclass）全部拒绝；root 还必须是 exact `dict`。第一次非递归遍历检查最多 128 层、1,000,000 nodes、cycle、lone surrogate、safe integer 与 finite/safe float。第二次非递归遍历计算该 tree 的唯一 compact-JSON UTF-8 size：无 whitespace，container 使用 `{}`/`[]`、`,`、`:`；string 用双引号、JSON control/quote/backslash escaping 和其余 code point 的原 UTF-8 bytes；`null/true/false` 用对应 ASCII token，integer 用十进制 token，finite binary64 用 profile-frozen Python 3.11 `repr` 的合法 JSON number token。object member order不改变该加法式 byte count。counter 通过 iterator frame 逐 node/string code point 累加，超过 8 MiB 立即失败，不调用 whole-tree `json.dumps`、不创建整棵副本或完整 encoded string；该 preflight encoding 只定义 Python ingress 的等价 transport cap，不替代后续 RFC 8785 JCS。bytes path 则在 decode 前对实际 raw bytes 应用同一 8 MiB 常量。

Pydantic 2.12.0 的 `Literal[True]` 会接受 `1/1.0`，因此所有 persisted true-only fields 必须使用 `StrictTrue` 的 exact-type before-validator；Python/JSON 负例必须覆盖 `1`、`1.0`、`false` 与 string，且 canonical hashing 之前即 fail closed。

JSON ingress 还必须在分配整棵 decoded tree 前应用资源边界：transport 和 parser 均以 8 MiB hard cap 拒绝过大 body，string/escape-aware lexical preflight 把 nesting 限为 128，decode 后的非递归遍历把 nodes 限为 1,000,000；`RecursionError` 统一转换为 contract rejection。`json.loads` 必须使用上面的 `parse_float` hook，先以 `Decimal` 检查原始 number token，再转换 binary64；非零十进制 token 若转换为 `+0.0/-0.0`（例如 `1e-400`、`-1e-400`）必须在 schema validation 前拒绝，字面零和 `-0.0` 才允许进入既定 zero-normalization。`RemoteEnv` 与 artifact ingress 不得先无界缓冲再传给 parser。`StrictFrozenModel` before-validator 对 Python-side 字段与 mapping keys 使用同一非递归字符串检查，使任何 lone surrogate 在 model validation 而非 serializer/JCS 阶段失败。`TypeAdapter(CanonicalJsonValue).validate_python` 的 alias before-validator 必须先调用同一迭代式 tree guard，再执行最多 128 层的规范化；因此直接 adapter 调用也拒绝过深、过多节点、cycle、lone surrogate，并把任何意外 `RecursionError` 转为 contract `ValueError`。

canonical storage 不直接对裸 payload dump 取 hash，而是对 closed `CanonicalPayloadDocument` 取 RFC 8785 JCS：

```json
{
  "schema_id": "sha256:<generated-schema-hash>",
  "exact_float_paths": [],
  "payload": {}
}
```

8 MiB/128-depth/1,000,000-node transport cap 只约束 caller 提交的裸 payload；repository 派生的 `CanonicalPayloadDocument` 另使用固定 32 MiB、129-depth、2,000,016-node hard caps，以容纳 schema ID、完整 exact-float path map 与 closed framing。encode 与 decode 均须在 JCS 分配或 JSON decode 前检查这些独立上限；超过上限的 path-heavy document 必须 fail closed，但恰好位于全部裸 payload 上限内的合法输入不得因派生 framing 开销被误拒。

`exact_float_paths` 是 repository 在 strict validation **之后**根据 generated schema 的 `x-automarkov-number-kind=exact-float` marker 和实际 payload 自动生成、去重并按 RFC 6901 pointer bytes 排序的路径集合，外部 caller 不能提供。`require_exact_float` 仍在 external Python/JSON path 拒绝 `bool`、`int`、string、non-finite 和绝对值超过 $2^{53}-1$ 的 float；只把 `-0.0` 规范化为 `+0.0`。该范围覆盖概率、discount、阈值与容差的 contract，超范围实数必须使用领域定义的 decimal string。JCS 会把 exact `1.0/+0.0` 写成 safe integer token，但同一 canonical document 中的 type map 保留其声明类型。

repository decode 顺序固定为：验证 envelope/schema ID 与 document SHA-256→duplicate-aware parse→证明输入 bytes 已是该 document 的 JCS→验证 top-level exact keyset→验证已认证的每个 `exact_float_path` 都唯一、排序、指向 finite numeric token 且其 registered schema position 允许 exact-float marker→依该 map 恢复 Python float→strict validation/deep-freeze→从得到的 typed model 与 schema marker 重新派生完整 actual float-path set 并与 stored map 精确比较→重建 document 并再次 JCS。`int | StrictCanonicalFloat` 等 union 不能仅凭 JCS token 判定实际 branch；stored map 由首次 external strict validation 后的 typed model 生成并受 document hash 认证，decode 必须先作 schema-eligibility 检查、恢复和 strict validation，才有信息重算 actual set。任一未知 schema、path 缺失/额外/重复/乱序、path 指向 non-number 或不允许 exact-float 的位置、external caller 提供 type map、hash mismatch 或 recanonicalized bytes 不同均失败。这样 external `{"discount": 1}` 仍失败，而 repository 自己持久化的 exact `1.0` 可无损恢复；untyped `CanonicalJsonValue` 中的 `1.0/-0.0` 已规范化为 `1/0`，不进入 type map。

JCS 规则固定为 UTF-8、无 BOM/空白，object property 按 UTF-16 code units 排序，string 保留原 Unicode code points 且不作 normalization，number 使用 ECMAScript/JCS 最短序列化，并禁止 NaN/Infinity。实现优先采用固定 source/version/checksum、通过 RFC 8785 官方 vectors 的复用库；自写 encoder 只有在 ADR 记录缺少合适上游且同一 conformance corpus 全通过时允许。每个通过 schema gate 的 typed artifact 都必须 encode、hash、decode、再次 encode 得到 byte-identical canonical document。反例至少含 `object()`、NumPy scalar/array、自定义实例、tuple/set、非字符串 mapping key、重复 member、lone surrogate、typed/untyped unsafe integer、non-finite float、非零十进制 token 的 binary64 下溢、伪造/遗漏 float path、external integer-to-float 和 `-0.0` 规范化。

`TypeAdapter(CanonicalJsonValue).json_schema()` 必须实际输出 `TypeAliasType` 产生的 recursive union（null/boolean/integer/number/string/array/object，object 只允许 string properties 与 recursive values），不得是 `{}`；safe-integer/finite 与 input-type 限制仍由 validator 表达。`StrictCanonicalFloat` schema 必须实际包含 exact-float extension marker。JSON Schema 无法单独区分 JSON lexical integer `1` 与 number `1.0`，因此外部 exact-float contract 仍由 runtime validator/parser 强制；schema 文档不得声称单独完成该保证。artifact source of truth 是 repository 中的 canonical document JCS bytes；读取返回新解析对象，不暴露内部 buffer。

## 2.10 跨字段不变量

Pydantic 的字段级验证只是第一级。下列第 1–13 项中可由 payload 结构判定的部分必须机械接入 `DecisionProcessBase.validate_common_structure` 与四个 branch model validators；直接调用 branch `model_validate` 和经 public adapter 调用必须得到相同结果。只有 Markov sufficiency、transition totality、traceability consumer 等依赖外部符号表、工件或行为证据的性质交给独立 formal validators，不能把 cardinality、keyset、owner、AEC、message 或 reward structure 延后成可选检查：

1. field validators 已强制 positive finite horizon、nonnegative lag/priority、positive fixed dimension/evaluation horizon、`discount`/chance probability 位于 $[0,1]$、confidence 位于 $(0,1)$ 且 risk tolerance 非负。`VariableSpec.domain` 必须且只能命中 scalar/vector/tensor/categorical/text/binary 六个 branch：vector rank 恰为 1、tensor rank 至少为 2、numeric element 必有 exact-dtype explicit bounds 或 typed symbolic binding、bool tensor 不得伪造 numeric bounds、categorical values 非空且唯一，shape 只能由 fixed/symbolic dimension branches 表达；旧式自由 `dtype/shape/domain` key 组合一律失败。`direction="satisfice"` 时 `success_threshold` 必填，其他 direction 时必须为空。chance constraint 的 `predicate` 固定表示满足事件，唯一概率语义是 $P[\lnot predicate]\le\texttt{max_violation_probability}$；该 bound 只在 `kind="chance"` 时必填，其他 constraint 必须为空。`measure="failure_probability"` 的 tolerance 另强制位于 $[0,1]$；`measure ∈ {var,cvar}` 时 `confidence_level=α` 必填，其他 measure 时必须为空。`outcome_expression` 对 VaR/CVaR 统一解释为越大越坏的 loss $L$：$\operatorname{VaR}_\alpha(L)=\inf\{x:F_L(x)\ge\alpha\}$，$\operatorname{CVaR}_\alpha(L)=(1-\alpha)^{-1}\int_\alpha^1\operatorname{VaR}_u(L)\,du$，从而对离散分布也无条件期望歧义。无限时域且优化 discounted sum 时还必须满足 `discount < 1`，否则需给出 average-reward 语义。需要有符号 risk threshold 的任务必须在 `outcome_expression` 中显式平移/变换，不能绕过 tolerance 合同。
2. `reward_lags` 和 `message_lags` 明确表达历史奖励与历史消息能否被 actor 使用；空 tuple 表示不可见，不能由 RLlib batch 默认泄漏。POMDP/POSG 的 `message_processes_by_recipient` keyset 必须精确等于 agent set；process 只允许置于自身 `recipient_id` 下，agent sender 必须属于该 set，process ID 全局唯一，presence 与 recipient 的 nonempty `message_lags` 双向一致。其他 actor 不得经 observation projection、connector 或 centralized-only field 读取该 message。
3. 随机奖励必须给出支持集、条件变量、期望和相关性；仅写“add noise”无效。独立奖励的 `correlation_group` 必须为空。相关多智能体奖励的每个非空 group 必须恰有一个 `JointRewardDependencySpec`，显式给出不少于两个且无重复的成员、联合分布族/参数/支持集/条件变量、联合核及逐成员边缘 law；group 定义与各 agent `StochasticRewardSpec.correlation_group` 必须双向精确匹配，成员必须属于 `agent_ids`，未引用或未定义 group、确定性奖励冒充 group member、边缘 keyset 缺失/额外均失败。
4. POSG 的观测必须先定义联合核 $Z(\mathbf{o}_t\mid s_t,\mathbf{a}_{t-1})$，再定义各 agent projection；分别列局部观测但忽略相关性不完整。
5. MG 每个 actor 的运行时输入必须具有 Markov 充分性；若仅 `state()` 供 critic 而 actor 使用 local observation，则必须分类为 POSG。
6. `action_timing="aec"` 时 `aec_turn` 必填；`action_timing="simultaneous"` 时必须为空。AEC 必须定义 active actor、chance/environment turns、cycle boundary、状态更新时机和 reward accumulation。
7. 所有 objective、constraint、risk 必须有唯一 ID，并在 reward、terminal、metric 或 report 中至少有一个 traceability consumer；constraint 不得仅靠奖励近似而不声明软化。
8. actor 输入字段与 `centralized_training_fields` 必须不相交；后者使用 `state|observation|action_history|reward_history|message_history` 的 strict discriminated field-reference union，并机械验证 agent、变量和 message-process identity。任一 actor 当前观测，或由 nonempty lag set 许可的 action/reward/message history，均属于 actor 输入并禁止同时声明为 centralized-only；critic-only 信息通过 Learner connector 注入，不能进入 inference batch。
9. `terminated` 表示 MDP/POSG 语义终点，`truncated` 表示外部时间或资源上限；同一谓词不能同时属于两者。
10. 单智能体 `agent_id` 必须非空，且 MDP/POMDP 的 `actions_by_agent` keyset 必须严格等于 `{agent_id}`，不得缺失或混入额外主体。
11. MG/POSG 的 `agent_ids` 必须去重、全部非空且 cardinality 至少为 2；`actions_by_agent` 与 `rewards_by_agent` keyset 必须严格等于该 agent set。MG 的 `full_state_access_by_agent`，以及 POSG 的 `history_access_by_agent`、`message_processes_by_recipient` 和 `joint_observation.per_agent_projection` 也必须具有完全相同的 keyset，不允许 missing 或 extra agent。POMDP 的 message mapping keyset 则必须精确等于 singleton `{agent_id}`。
12. 对 MG/POSG，`action_timing="aec"` 时 `aec_turn.possible_turn_owners` 必须去重且其 set 严格等于 `agent_ids`；`aec_turn` 必须当且仅当 timing 为 `aec` 时存在。chance/environment turns 由对应布尔字段表达，不能伪造为额外 agent ID。
13. 四类 spec 的 `objectives` 必须至少有一项。单智能体每个 objective 的 `owner_ids` set 必须严格等于 `{agent_id}`。MG/POSG 每个 objective 的 owners 必须非空、去重且为 `agent_ids` 的子集，并且全部 objectives 的 owner union 必须覆盖完整 agent set；空 owner、重复 owner 和 ghost owner 全部失败。
14. 通用 `ClarificationRequiredResult` 的两个 artifact IDs 必须非空且满足各自 canonical ID 格式；envelope 的 canonical `parent_artifact_ids` 必须严格等于排序后的 `(task_artifact_id, review_report_artifact_id)`。result 必须包含至少一个 nonempty gap；`target_path` 与 `question` 均非空且 `(target_path, question)` 唯一；`introduced_assumptions`、`formal_artifact_ids` 和 `environment_artifact_ids` 必须为空。普通非实验 TaskRequest 直接使用该结果，不得伪造 variant、track 或 outcome mask。只有冻结的 `AUTO/v5` cell 将它嵌入 strict `ExperimentClarificationRequiredResult`；wrapper 的 `outcome_mask_id` 必须是 canonical ID，且 run manifest 必须同时匹配 task ID、`AUTO`、`v5_clarification_required` 和 outcome mask，否则 reducer 拒绝终态转换。`SafeClarificationRequired` 只能由第 7.6 节的 post-terminal `ClarificationEvaluationRequest`/`ClarificationEvaluationVerdict` 链依据冻结 gap manifest、artifact DAG、terminal snapshot 和 execution attestation 派生，不能信任 payload 自评分。
15. `ClassificationResult` 的 source ID、discriminated `evidence_binding` 和 rationale 必须非空，且 source task 必须已 `TEXT_LOCKED`。普通 run 只接受 `EvidenceLedgerBinding`，其 artifact kind 必须为 `EvidenceLedger`，envelope canonical parents 严格等于排序后的 source task/evidence-ledger IDs。只有 exact `automarkov_no_evidence` execution plan 可接受 `EvidenceOmissionBinding`；其 referenced payload 必须 strict-validate 为 `EvidenceOmissionRecord`，record envelope parents 严格等于排序后的 task-card/ablation-execution-plan/pair-binding IDs，且 `schema_version`、experiment/run/cell/pair binding/plan/method/gate/reason 全部与 run manifest 和 omission event 精确匹配。同 run 的 runner-signed `GateOmittedByDesign(EVIDENCE_LEDGER_CLOSURE).output_artifact_ids` 必须恰为该 record ID。Classification envelope parents 严格等于排序后的 source task/omission-record IDs。两种 branch 不能互换，omission 不能被 evaluator/projector 当作 evidence 或 validation pass。projector 只从该 typed artifact 和对应 event 得到 `CLASSIFIED`，不接受裸字符串分类。
16. `ReductionProposal` 的 source/classification IDs、assumption IDs/statements/semantic loss 均非空，assumptions 非空且 ID 唯一；其 base classification 必须是引用同一 source task 的 `REDUCIBLE` result。首次 proposal 的 `supersedes_proposal_artifact_id` 与 `trigger_classification_artifact_id` 同时为空；reclassification mismatch 产生的 revision 必须同时填写二者，trigger 必须属于上个 proposal 的 child、其 `IN_SCOPE_*` kind 必须不同于被 supersede 的 target，且去前缀后等于新 target。proposal envelope 的 canonical parents 严格等于排序后的 source task、base classification，以及 revision 时的 superseded proposal 和 trigger classification IDs。批准后新 `TaskContract` 的 direct parents 严格等于排序后的 source task、base classification 和当前 proposal IDs，并重新经过 Text Review 与 exact-ID approval。新 child `TaskContract` 仍保持 object-type neutral；其后续 classification 与当前 target 一致才允许进入 formalization。
17. `ValidationClaim` 是 `DecisionProcessSpec` 之外的独立 immutable artifact：它引用既有 `subject_artifact_id` 和非空、去重的 `report_artifact_ids`。claim envelope 的 canonical `parent_artifact_ids` 必须严格等于排序、去重后的 `{subject_artifact_id} ∪ set(report_artifact_ids)`，不能只引用祖先或旁路输入。subject payload 不能嵌入任何反向引用自身的 claim/report；payload 自己声明 `passed=true` 不产生更高 validation level。

## 2.11 验证等级不是单一“已验证”布尔值

| 等级 | 必需证据 | 允许声明 | 不允许推断 |
|---|---|---|---|
| `schema` | Pydantic strict validation、JSON Schema round-trip | schema 合法 | 数学或行为正确 |
| `structural` | symbol/type/unit/shape、totality、概率、信息边界检查 | 结构一致 | 环境可运行 |
| `executable` | isolated import、Gymnasium/PettingZoo checker、RLlib sampling smoke | 接口可执行 | 动态忠实 |
| `behavioral` | property/metamorphic/trajectory/statistical tests | 在测试域内行为一致 | 与 gold 全局等价 |
| `oracle_equivalent` | sealed differential/oracle test 达到预注册容差 | 在 oracle 覆盖域内等价 | 形式证明 |
| `formally_verified` | 指定 property、模型、工具版本及 proof/certificate artifact | 指定性质已证明 | 未建模性质或连续高维系统整体正确 |

等级单调依赖但不自动升级：高等级 claim 必须同时引用所有低等级报告，且只对 `scope` 生效。报告必须写“达到的最高等级 + 未覆盖 scope”，不得笼统写“environment verified”。

validator 先读取 immutable subject artifact，再生成以 subject 为直接 parent 的 immutable validation report；只有 subject ID 和全部 report IDs 都已固定后，才生成独立 `ValidationClaim` artifact 与对应 append-only event。failed report 只产生 `ValidationFailed` event，不得创建 claim。repository/projector 只接受 registered validator/version、verified report hashes、direct-parent set 精确匹配、nonempty scope、report subject/scope 一致且所有低等级 prerequisites 已通过的 claim；任一条件失败都不能升级。projector 从有效外部 claims 派生当前 validation level；附加 claim 不创建新的 `DecisionProcessSpec` revision，从而避免 subject content hash、report lineage 和 claim identity 形成环。

---

# 3. 项目交付物以及是否需要训练

## 3.1 每个任务的标准交付包

| 文件或目录 | 内容 |
|---|---|
| `task_contract.yaml` | 经用户确认的细粒度文字任务表征 |
| `evidence_ledger.jsonl` | 每项事实、参数、假设的来源与证据 |
| `assumption_register.yaml` | 系统提出、用户接受或拒绝的假设 |
| `decision_process_spec.yaml` | MDP、POMDP、MG 或 POSG 数学规范 |
| `traceability_matrix.json` | 需求—证据—数学符号—代码—测试映射 |
| `artifact_manifest.json` | payload hashes、artifact IDs、DAG roots 与 schema versions |
| `events.jsonl` | append-only run events 的导出副本与 head hash |
| `artifact_dag.json` | parent/derivation edges 与 cycle-check report |
| `environment/` | Gymnasium/PettingZoo 环境或官方环境适配器 |
| `tests/` | 可公开单元、接口、属性、变形和轨迹测试；不含 sealed oracle |
| `validation/` | 分级验证报告与 signed sealed-evaluation summary |
| `training/` | 现代 RLlib `AlgorithmConfig`、RLModule、ConnectorV2 与 callbacks |
| `policy_exports/` | content-addressed finite weights-only safetensors、signed `PolicyExportManifest`、source-checkpoint commitment 与 export terminal record |
| `metrics/` | 训练曲线、评估回报、成功率、资源消耗 |
| `reproduction_manifest.yaml` | 依赖/profile、fixed Git commit、数据、Qwen/vLLM、prompt 与 runner attestation hashes |
| `report.md` | 建模结果、假设、限制、测试与训练结论 |

表中的 YAML/JSON/JSONL 是 artifact repository 的 deterministic export，不是可原地编辑的 source of truth。公开交付先执行 secret/gold/restricted-license scan；Sealed Gold 只以 evaluator 签名的 schema-limited aggregate/verdict 和域分离 nonce-backed commitment 出现，绝不发布其 artifact identity/hash、payload、nonce 或 locator。

普通 RLlib checkpoint 是 trainer profile 内 ignored、run-local 的临时输出，只供同 profile 的恢复或 trainer-local policy export；它不得进入标准交付包、跨 profile repository、publisher 或 sealed evaluator。可交付策略只采用上表的 manifest-bound weights-only safetensors 链。

## 3.2 核心多智能体建模系统是否需要训练大模型

不需要。Evidence Researcher、Text Specification Author、Formalizer、Environment Developer、Unit Test Agent、Simulation Test Agent 和 Training Analyst 通过推理 API 调用完成。

核心系统的研究问题是：

- 如何把自然语言需求转换成正确、完整、可追溯的决策过程；
- 如何自动发现和修复语义、数学、代码与行为错误；
- 如何生成或复用可训练环境；
- 如何在统一 RLlib 后端上构造和验证策略。

因此，系统本身不依赖额外的 SFT 或 RLHF 才能运行。

## 3.3 是否需要训练 RL 策略

端到端实验中必须训练。原因包括：

1. 用户可能需要可直接部署或分析的、signed manifest 绑定的 weights-only portable policy export；
2. 策略学习可以揭示退化奖励、动作无效、终止状态不可达、观测信息不足、奖励尺度不合理等环境问题；
3. 训练结果可以作为环境动态一致性的重要证据，但不能替代数学和行为测试。

“RL 没有学会”不能直接证明环境错误。Training Analyst 必须将失败归类为：

- 环境语义错误；
- 状态或观测设计错误；
- 奖励设计错误；
- 动作空间或动作约束错误；
- RL 算法或网络配置错误；
- 训练预算不足；
- 任务本身不可解或目标冲突。

## 3.4 Agent2World 的 SFT 是否属于核心系统

不属于核心运行路径，而且在当前开发与论文主实验中明确标记为 `DEFERRED_LICENSE_AND_COMPUTE_REVIEW`。当前只交付：

- `SftTrajectoryManifest`、去重/质量过滤 schema 和 lineage contract；
- future `LlamaFactoryTrainingRunner` 接口，不安装、不执行；
- 论文公开 checkpoint 的许可允许范围内评估方案；
- 资源估算、deviation log 与启用前 gate。

不得下载或复制受限 Agent2World 代码到发布树，不得分发其代码或 derivative works，不得把它封装成 hosted service。未来只有在以下 gate 全部通过后才可启动 SFT：固定论文/仓库/data/model revision；逐项完成代码、数据、基础模型和输出权重许可审查；取得必要书面许可；批准 GPU 预算；通过 privacy/dedup/contamination audit；使用 LlamaFactory 而非自写训练器；用 SwanLab offline mode 记录训练。若仅评估官方 checkpoint，必须写成 `official-checkpoint evaluation`；若采用替代数据/模型，只能写 `controlled adaptation`，不得称完整复现。

## 3.5 `LocalLlmRuntime`：Qwen3.6 + vLLM 唯一路径

所有 agentic generation 调用必须经过 `LocalLlmRuntime`；业务组件不得自行创建 HTTP client。规范性模型 ID 为 `Qwen/Qwen3.6-35B-A3B`，实际使用本地 checkpoint 路径，但 manifest 同时记录 upstream model ID、revision、每个权重 shard hash、tokenizer hash、chat template hash、vLLM version、PyTorch/CUDA version、container digest 和启动参数。

HTTP transport authentication 使用 vLLM `--api-key` 与 `Authorization: Bearer`。operator 配置只提供 `AUTOMARKOV_VLLM_API_KEY_FILE` 这一 credential-file locator；不得提供 token-valued env/config 字段。仓库示例唯一默认值为已忽略的 `secrets/vllm_api_key`。locator 经 canonical realpath 后必须位于 worktree 外，或严格位于当前 worktree 的 ignored `secrets/` root 内；worktree 内的其他路径、root 自身、path traversal、symlink 及未通过 ignore-policy attestation 的路径在读取前拒绝。目标必须是 owner-only `0600` regular file，`LocalLlmRuntime` 在每次 connection lifecycle 内从只读 file descriptor 读取 secret bytes，并且只把 redacted credential ID/fingerprint 写入 profile graph。token、Authorization header、credential path 和 file content 不进入 prompt、artifact、event、SwanLab、exception 或 access log。vLLM 0.25.1 的 API-key middleware 不保护 `/health`，因此 `ATTACHED` preflight 把 `GET /health` 仅作为 unauthenticated liveness probe；credential 的正向证明必须由带该 credential 的 `GET /v1/models` 与真实 `POST /v1/chat/completions` 同时完成。bad/missing credential 的负例即使 `/health` 返回 200，只要任一 `/v1` probe 返回 401/403 或未得到 schema-valid response，preflight 仍须 fail closed；不得自动关闭服务认证。`AUTOMARKOV_VLLM_BASE_URL` 必须是无 userinfo、query、fragment 的 loopback HTTP URL，normalized path 精确为 `/v1`；models/completion 分别解析为该 base 下的 `/models`、`/chat/completions`，liveness 则解析为同一 origin 的 `/health`，禁止错误请求 `/v1/health`。Unix-socket transport 未来若采用 peer credentials，必须另立 protocol version/ADR，不能把空 API key解释为已认证。

`LocalLlmRuntime` 具有两种显式 lifecycle mode。`ATTACHED` 是默认模式：只连接已存在的服务，记录 listener/process identity，但不拥有、不关闭、不重启该服务。`MANAGED` 只能由 operator 显式执行 `automarkov runtime launch` 创建；此时 runtime 才拥有其进程、标准输出/错误、监听 socket 和关闭职责。普通 `run`/`resume`/`complete` 调用禁止隐式 launch、restart 或 upgrade。只在以下三项新鲜检查全部通过后发布 `RuntimeReady` 事件：

1. unauthenticated `/health` liveness 成功，但不把它当作 credential proof；
2. credential-authenticated `/v1/models` 返回的 served model 与锁定 manifest 一致；
3. credential-authenticated 真实 `/v1/chat/completions` canary 返回 schema-valid 内容，且 trace 中的 endpoint、model 和 tokenizer hashes 一致。

禁止把进程存在、端口监听或模型列表单独当作 readiness。endpoint 只能绑定 loopback/Unix socket，必须启用 request timeout、最大并发、上下文上限、token budget 和 backpressure。运行中若 canary、model identity 或 tokenizer identity 变化，立即追加 `LlmRuntimeDegraded` 事件，停止发放新任务，并从当前依赖 LLM 的非终态转入 `WAITING_RUNTIME`，在事件中保存精确 `resume_state`；不得 fallback 到 OpenAI、OpenRouter、DeepSeek、ModelScope API、Transformers 直接推理、SGLang 或其他模型。

2026-08-09 的只读基础设施观测曾看到远端 `vLLM 0.25.1+cu129`、served model `Qwen3.6-35B-A3B`、`max_model_len=32768`、`reasoning_parser=qwen3` 与 `tool_call_parser=qwen3_coder`；该观测没有产生可持久化的 attach manifest identity/hash，因此只是 historical discovery hint，不构成当前 readiness 或 provenance 证据。首次调用前必须生成 immutable runtime manifest，记录观测时间、endpoint/relay identity、service argv/package、model/tokenizer/chat-template hashes 与本节三项 probe artifacts；在该 manifest 齐全并验证前，run 保持 `WAITING_RUNTIME`。上游 `vLLM 0.26.0` 只属于未来 clean-build candidate，不得为追新版本改变已验证的 attach service。

每次 completion 保存以下非敏感 trace：

```yaml
request_id: "..."
model_id: "Qwen/Qwen3.6-35B-A3B"
model_revision: "..."
vllm_version: "..."
prompt_artifact_id: "..."
sampling:
  temperature: 0.0
  top_p: 1.0
  seed: 0
  max_tokens: 0
usage: {}
latency_ms: 0
finish_reason: "..."
response_payload_hash: "sha256:..."
```

原始 chain-of-thought 不作为交付或评分输入；保存 schema output、tool calls、必要的短 rationale 和 hash 即可。实验比较时固定 sampling 参数，generation pair 共用同一 generation seed；任何自动 retry 都计入方法成本，并由错误分类策略限定最大次数。

---

# 4. 系统架构：Typed-Blackboard Multi-Agent Compiler

A-LAMP 将参数、目标、变量、约束、数学建模、状态—动作—奖励与编码分配给专用智能体；Agent² 使用 Generator Agent 与 Target Agent，并依据训练反馈调整任务建模和算法；Agent2World 使用 Deep Researcher、Model Developer、Unit Tester 和 Simulation Tester，并通过自适应反馈修复行为级错误。

AutoMarkov 不机械地为每个字段创建一个智能体，而是根据不同错误边界设置角色，并通过有类型的共享黑板交换工件。

## 4.1 智能体与确定性组件

| 组件 | 类型 | 唯一职责 | 禁止行为 |
|---|---|---|---|
| Orchestrator | 确定性状态机 | 控制阶段、版本、预算、回退与终止条件 | 不进行领域自由推理 |
| Evidence Researcher | LLM + Tavily | 检索事实、数据、算法、官方实现与限制 | 不自行设计状态和奖励 |
| Text Specification Author | LLM | 生成 `TaskContract` | 不提前决定数学对象 |
| Text Specification Critic | LLM + schema validator | 检查完整性、矛盾、歧义和无证据假设 | 不直接覆盖已确认合同 |
| Formalizer | LLM + symbolic tools | 生成四类数学对象之一 | 不引入未经批准的新假设 |
| Formal Critic | LLM + deterministic validators | 检查 Markov 性、概率、单位、信息结构、奖励和约束 | 不编写环境代码 |
| Environment Developer | LLM + repository tools | 复用、组合或生成环境 | 不改变已批准语义 |
| Unit Test Agent | LLM + pytest | 独立生成 schema/static/import/API/type/shape/bounds/action-mask/seed 与 deterministic core invariant tests | 不执行 property/metamorphic/differential/trajectory gate，不把开发者测试作为唯一证据 |
| Simulation Test Agent | LLM + Hypothesis/executor | 独立生成 property/metamorphic/generation-visible differential tests，并构造 public-dev 轨迹、反事实和行为级测试 | 不重复或替代 Unit gate，不以“可运行”代替“正确” |
| Training Analyst | LLM + RLlib logs | 诊断训练失败、退化策略和性能瓶颈 | 不擅自改变用户目标 |

## 4.2 Typed Blackboard

共享黑板只保存以下结构化对象：

- `TaskContract`；
- `ClarificationRequiredResult`；
- `ExperimentClarificationRequiredResult`；
- `ClassificationResult`；
- `EvidenceOmissionRecord`（只允许 exact no-evidence ablation）；
- `ReductionProposal`；
- `EvidenceLedger`；
- `AssumptionRegister`；
- `DecisionProcessSpec`；
- `IssueLedger`；
- `ImplementationPlan`；
- `TestReport`；
- `TrainingDiagnosis`；
- `TraceabilityMatrix`；
- `OODHandoffSpec`；
- `ValidationClaim`。

每个 artifact 由 immutable envelope 与 immutable payload 组成。`payload_hash` 是第 2.9 节 `CanonicalPayloadDocument` JCS bytes 的 SHA-256，document 内含 schema ID、repository-derived exact-float paths 与 payload；`artifact_id` 是去除自身 ID 后整个 canonical envelope（包含 type、schema、payload hash、parents 与 creation metadata）的 SHA-256，payload 禁止反向包含自己的 artifact ID/hash。同一 payload document 可因不同 lineage 拥有不同 artifact IDs，但共享同一 immutable blob。`parent_artifact_ids` 在计算 hash 前必须验证无重复，并按 canonical artifact-ID bytes 升序编码为 tuple；调用方提供乱序或重复 parent 时 repository 拒绝，不能静默产生另一 identity。每个 parent 必须在 ArtifactStore 中存在；EventStore 的 event ID/hash 只能进入 artifact-type-specific 的 typed event-reference field，不能进入 `parent_artifact_ids` 或在 artifact namespace 中伪装 parent。写入后不得原地改变 payload、metadata、父节点或“状态”；修订必须创建新 artifact，并用 DAG edge 指向其直接输入。

T02 已建立的 parent-type tuple 不被放宽为 caller-defined predicate，而是扩展为 schema registry 内唯一的 closed discriminated `ParentContract` union，以 `contract_kind` 鉴别且只允许两类：

- `ExactParentContract(contract_kind="exact")`：冻结 canonical `direct_parent_artifact_types` tuple，包括 root artifact 的空 tuple。repository 从已存在 parents 重新解析实际 type multiset，必须与注册 tuple 逐项一致；不少 parent、不多 parent。该 branch 只能用于 payload 本身不携带 direct-parent identities 的固定类型合同；一旦 payload 指定 exact parent ID/hash，必须使用 `payload_bound`。
- `PayloadBoundParentContract(contract_kind="payload_bound")`：注册一个静态、closed 且受 schema ID 约束的 `ParentBinding` tuple。每个 binding 固定 artifact-reference JSON path、与之成对的 payload-hash path、允许的 artifact type 与 `one|optional|many` cardinality；repository 只能从已 strict-validate 的 canonical payload 抽取 ID/hash pairs，解析每个 stored parent 的 type/payload hash，再将已验无重复的 ID 排序为唯一 expected tuple。envelope `parent_artifact_ids` 必须与它完全相等；ID/hash 半空、nullable branch 不同步、重复 ID、错 type/hash、未声明 payload artifact reference 或多余 envelope parent 全部拒绝。

`ParentContract` 随 `(artifact_type,payload_schema_version)` 一起冻结；同一 schema key 不得以另一 contract 重注册，registry freeze 后不得修改。`PayloadBoundParentContract` 只表达 ArtifactStore DAG；其 binding 禁止指向 event ID/hash path。`TerminalResult`、`ProcessExecutionTerminalRecord` 和 `RunAuditProjection` 等 parent 集由 payload 内 closed references 决定的 artifact 必须使用 `payload_bound`；其他固定 parent-type tuple 继续使用 `exact`。两类之外的 callable、任意 predicate、“at least these parents”或仅校验 parent count 的合同均禁止。

```yaml
artifact_id: "..."
artifact_type: "TaskContract | DecisionProcessSpec | TestReport | ..."
schema_version: "..."
payload_media_type: "application/vnd.automarkov.canonical-payload+json"
payload_hash: "sha256:..."
parent_artifact_ids:
  - "..."
created_by: "agent_or_tool_id"
created_at: "ISO-8601 timestamp"
source_evidence_ids:
  - "E-..."
payload: {}
```

批准、拒绝、锁定、supersede、运行状态和验证结果是 append-only events，不是 artifact 可变字段。上一版的自由 `event_type + data: {}` YAML 不是合法 ingress；规范 wire algebra 是 `extra="forbid"`、strict/frozen 的 `EventRecord`，其 `event` 字段为使用 literal `event_type` 的 Pydantic v2 discriminated `RunEvent` union：

```text
EventRecord = {
  schema_version: Literal["automarkov.event-record.v1"],
  event: Annotated[RunEvent, Field(discriminator="event_type")],
  event_hash: Sha256Digest
}

RunEvent = BootstrapEvent
         | LifecycleEvent
         | ControlAuditEvent
         | PostTerminalAuditEvent

BootstrapEvent = RunCreated
               | ReplacementRunCreated
               | ClarificationChildRunCreated

LifecycleEvent = SignedApprovalEvent
               | ArtifactSuperseded | StageGatePassed
               | ValidationClaimed | ValidationFailed
               | ClarificationRequested | RunTerminationRequested
               | StateTransitioned
               | RuntimeReady | LlmRuntimeDegraded
               | WaitingRuntime | WaitingEvidence | WaitingAsset | WaitResolved
               | EvidenceTemporarilyUnavailable
               | Blocked | EvidenceAuthorityRequired | BlockResolved
               | BudgetExhausted | EvidenceBudgetExhausted

ControlAuditEvent = RunSuperseded | GateOmittedByDesign
                  | ExecutionTopologySubstituted
                  | SpecificationConflictDetected | ArtifactAccessRevoked

PostTerminalAuditEvent = ClarificationEvaluationRequested
                       | ClarificationEvaluationRecorded
```

除后文已给出完整 exact keyset 的 signed branches 外，v1 其余 branch-only keysets 闭合如下；表中“reference”均是 typed ID/hash pair 或 ID/sequence/hash triple，不是裸字符串：

| event branch | branch-only exact fields |
|---|---|
| `ArtifactSuperseded` | old/new artifact references、lineage-report reference、closed supersession reason code |
| `StageGatePassed` | exact from/to state、gate ID/version/contract hash、sorted unique subject artifact references、gate-report reference、`result="passed"`、closed reason code |
| `ValidationClaimed` | claim/subject/report artifact references、validator ID/version、validation level/scope |
| `ValidationFailed` | subject/report artifact references、validator ID/version、validation level/scope、closed failure code |
| `ClarificationRequested` | task/review/result artifact references、sorted gap IDs、clarification policy reference、closed reason code |
| `RunTerminationRequested` | `requested_terminal_state ∈ {PARTIAL,CANCELLED}`、requesting-authority ID、request-evidence reference or schema-declared null、closed reason code |
| `StateTransitioned` | exact `from_state/to_state`、trigger event reference、sorted input artifact references、gate-report reference or schema-declared null、budget-snapshot reference |
| `RuntimeReady` | dependency kind/identity hash、profile/process/protocol-edge identities、readiness-report reference、passed-gate ID |
| `LlmRuntimeDegraded` | local-LLM dependency identity hash、failed-gate ID、failure-report reference、affected state |
| `WaitingRuntime`、`WaitingEvidence`、`WaitingAsset`、`WaitResolved` | 本节 waiting reducer 段列出的 common fields 与 branch-specific dependency/lease/asset binding |
| `EvidenceTemporarilyUnavailable` | lease-pool/snapshot/probe references、nonsecret slot-state counts、earliest availability |
| `Blocked`、`EvidenceAuthorityRequired`、`BlockResolved` | 本节 blocked reducer 段的 authority、condition、active-block/resolution references；evidence branch 另含 nonsecret slot-state counts |
| `BudgetExhausted`、`EvidenceBudgetExhausted` | 本节 budget reducer 段的 policy/snapshot/receipt/value fields；evidence branch 另含 sorted registered-account receipt references |
| `SpecificationConflictDetected` | specification artifact/hash、两个 conflict-locus IDs/hashes、affected contract IDs、closed conflict code |
| `ArtifactAccessRevoked` | subject artifact reference、governance-policy reference、revocation-authority ID、closed reason code、effective-at |

`RunCreated`、`SignedApprovalEvent`、`RunSuperseded`、`ReplacementRunCreated`、`ClarificationChildRunCreated`、`GateOmittedByDesign` 与 `ExecutionTopologySubstituted` 使用本节或第 11.11 节的 signed exact keyset，不得再套通用 payload。`ClarificationEvaluationRequested`/`ClarificationEvaluationRecorded` 使用第 7.6 节的 closed audit-event keyset并绑定已签名 request/verdict，事件本身仍按未签名 branch 的 authenticated actor 合同处理。全部未签名 branch 都必须额外含 `actor_principal_id` 与 `actor_process_execution_id|None`，repository 必须将它们与 authenticated command principal、run manifest capability 及 applicable process record 匹配；不允许因 branch 无 Ed25519 signature 就接受未认证 actor。

`StageGatePassed` 是 ordinary nonterminal forward edge 的唯一通用 gate cause，用于修正规范早期版本“要求 exact gate/report、却没有可承载该 cause 的 event branch”的闭合缺口。它必须与紧随其后的 `StateTransitioned` 在同一 `AppendRunEventsCommand` 中成对出现；两者的 from/to state、gate-report ID/hash、subject/input artifact ID tuple 与 trigger event ID/hash必须逐字节一致，且该 edge 必须存在于冻结状态表。它不能表达 waiting/resume、approval/revocation、artifact supersession、terminal、cross-run 或 post-terminal 语义；这些路径继续只接受各自专用 typed cause。任何 `StateTransitioned` 都不得由另一个 `StateTransitioned`、历史 root event 或无关 audit event触发。

这是 v1 的 closed branch set，不存在 `UnknownEvent`、通用 `data`、arbitrary `reason` mapping 或 plugin-defined subtype。每个 member 的 event-level `schema_version`、`event_type`、branch-only fields 和允许的 reason-code literal set 都由 schema registry 冻结；本节后文已指定的 signed branch exact keyset 必须与这里的 common fields 取并集，后文不重复 common field 不表示可缺失。所有 member 共有 event-level `schema_version`、literal `event_type`、canonical lowercase-hyphenated RFC 9562 UUIDv7 `event_id`、`experiment_id|None`、`run_id`、nonnegative safe-integer `sequence_no`、`previous_event_hash` 和 UTC `issued_at`；来源 principal/actor 必须由每个 branch 的 exact authenticated field 绑定，branch 只能在自己的 strict model 中增加字段。UUIDv7 的 version/variant bits、48-bit Unix-millisecond time 与 canonical text 都必须验证，其 timestamp 必须通过 run manifest 冻结的 clock-skew window；UUID 只是 replay identity，事件顺序仍唯一由 `(run_id,sequence_no,previous_event_hash)` 决定。repository 必须对所有 branch 强制 global `UNIQUE(event_id)`，对 signed branch 另强制 nonce/key/sequence replay indexes；UUID 冲突不得依赖 sequence 或 payload 内容转为幂等。

root Run 的唯一 sequence-0 branch 是 signed `RunCreated`。其 branch-only fields 恰为 `signing_domain="AutoMarkov-Run-Created-v1"`、`run_manifest_artifact_id/hash`、`initial_state="RECEIVED"`、`creation_principal_id`、`reason_code="run_created"`、128-bit random `nonce_b64url`、`signing_key_id`、`signature_algorithm="Ed25519"` 与 `signature_b64url`；manifest 必须已持久化且冻结 root ordinal/policy/principal/key bindings。`ReplacementRunCreated` 与 `ClarificationChildRunCreated` 只能由第 4.4 节各自的原子 child command 作为 child stream sequence 0 产生。三者的 `previous_event_hash` 都必须为唯一 sentinel `sha256:` 加 64 个 `0`；任何其他 branch 出现在 sequence 0、root 使用 child branch、child 使用 `RunCreated`、或已有 sequence 0 时重建 run 全部拒绝。

`event_hash` 是 repository record metadata，不是 typed event body 字段。唯一 preimage 是 closed object `{"domain":"AutoMarkov-RunEventHash-v1","event":<完整 strict typed event>}` 的 RFC 8785 JCS bytes；`event` 包含 signature（若该 branch 有签名）及除 record-level `event_hash` 外的全部字段，不允许从 preimage 再排除任何字段。hash 编码固定为 `sha256:<64 lowercase hex>`。每个 run 的 `sequence_no` 从 0 开始；sequence `n>0` 的 `previous_event_hash` 必须逐字节等于同 run sequence `n-1` persisted record 的 `event_hash`。

无循环的唯一验证/定址顺序是：先对 bounded raw JSON 执行 duplicate/resource/scalar checks 并 strict-validate exact event branch；再验证 UUIDv7、typed artifact/event references、principal/key/clock/replay policy 与 expected head/sequence/sentinel；若为 signed branch，对“从完整 event 仅移除 `signature_b64url`”的 event-specific domain-separated RFC 8785 JCS bytes 验证 Ed25519 signature；然后对包含已验 signature 的完整 event 计算 `event_hash`；最后才在 CAS transaction 中持久化 `EventRecord`。signature preimage 不包含 record-level `event_hash`，禁止先 hash 后签名、签名部分字段、从 event hash 再排除 signature，或用 hash chain 替代来源认证。

`ArtifactRepository` 必须以单写者事务或 compare-and-swap 原子追加 `(run_id, sequence_no)`，验证上述 union 与 hash chain，并拒绝重复/缺口 sequence、错误 sentinel/previous hash、event/record schema drift、缺失父节点、自环和 DAG cycle。public mutation ingress 只接受第 4.4 节 closed `LifecycleCommand` 中的 raw event bodies，不接受 caller 计算的 `EventRecord.event_hash`；adapter 从 persistence 读取 record 时必须重新验证 event schema、JCS preimage 和 chain，不信任 stored hash。任何 typed artifact/event 的 public Python ingress 都只接受原始 mapping/JSON tree，bytes ingress 只接受有明确 size limit 的原始 JSON bytes；两条路径在构造任何 model 前执行 duplicate/resource/scalar-type 检查。顶层或 nested `BaseModel` 无论 class、`revalidate_instances` 设置或 caller 声称的来源如何均 fail closed，因为它可能已经由 `model_construct()` 静默丢失 extra fields；repository-internal reuse 只能从此前认证的 canonical bytes/hash 重新 parse 和构建，不能 dump 未知 model 伪造 raw provenance。repository 只能持久化经 schema adapter 规范化、JCS serialize、duplicate-aware parse、再次从 raw tree strict validate 且 byte-identical round-trip 的对象。artifact DAG edge 只表达“由哪些不可变输入派生”；event stream 表达“发生了什么”。read model/projector 可计算 `draft/reviewed/approved/rejected/superseded`，但 projector 输出可重建且不是 source of truth。

删除、覆盖、就地修正和“更新 approved payload”均被禁止。若因隐私或法律必须撤回 payload，只追加 `ArtifactAccessRevoked` tombstone event 并移除读取能力；仍保留 hash、原因和审计记录。垃圾回收只允许删除无引用的临时 blob，且必须先生成 retention report。

## 4.3 最近致因回退

| 失败类型 | 回退位置 |
|---|---|
| 用户目标不清、决策主体缺失 | Text Specification |
| 状态不满足 Markov 性 | Formalization |
| 观测泄漏或隐藏信息未建模 | Formalization |
| 概率不归一、单位错误、变量未声明 | Formalization |
| import、shape、API、运行时错误 | Environment Developer |
| 轨迹行为与任务合同冲突 | Formalizer 或 Developer，由差分测试定位 |
| 奖励恒定、动作无影响、终止不可达 | Formalizer |
| loss 发散、吞吐不足、超参数不稳定 | Training Analyst |
| 任务不属于四类 | OOD Router |

## 4.4 状态机

规范状态集合为：

```text
RECEIVED, RESEARCHING,
TEXT_DRAFTED, TEXT_REVIEWED, WAITING_TEXT_CONFIRMATION, TEXT_LOCKED,
CLASSIFIED, REDUCTION_PROPOSAL_DRAFTING, WAITING_REDUCTION_CONFIRMATION,
OOD_HANDOFF_BUILDING, OOD_HANDOFF_VALIDATING,
FORMAL_DRAFTED, FORMAL_REVIEWED, WAITING_FORMAL_CONFIRMATION, FORMAL_LOCKED,
IMPLEMENTATION_SELECTED, ENVIRONMENT_IMPLEMENTED,
UNIT_VALIDATING, SIMULATION_VALIDATING, SEALED_E2E_VALIDATING,
TRAINING_SMOKE_TESTING,
POLICY_TRAINING, FINAL_EVALUATING, PACKAGING,
WAITING_RUNTIME, WAITING_EVIDENCE, WAITING_ASSET, BLOCKED,
COMPLETED, CLARIFICATION_REQUIRED, OOD_PACKAGED,
PARTIAL, BUDGET_EXHAUSTED, FAILED, CANCELLED
```

`COMPLETED`、`CLARIFICATION_REQUIRED`、`OOD_PACKAGED`、`PARTIAL`、`BUDGET_EXHAUSTED`、`FAILED`、`CANCELLED` 是 terminal states；`WAITING_RUNTIME`、`WAITING_EVIDENCE`、`WAITING_ASSET` 与 `BLOCKED` 是可恢复状态。`CLARIFICATION_REQUIRED` 终止当前 immutable run；获得答案后只能按本节 `ClarificationChildRunCreated` 原子协议创建引用原 run/result/answer bundle 的新 child run，不能原地续写。waiting event 必须携带 `resume_state`、阻塞原因与可验证的恢复条件；`BLOCKED` event 还必须声明所需的外部 authority。任一 terminal state 后只允许追加 closed post-terminal audit branch 或访问撤回事件，不允许继续生成；child 的 sequence-0 event 写入独立 event stream，不追加到 terminal parent。`ClarificationEvaluationRequested`/`ClarificationEvaluationRecorded` 是 `AUTO/v5` 唯一新增的 sealed post-terminal non-transition audit branches，不能改变 terminal state、result或生成输入。

状态机的唯一 mutation ingress 是 `extra="forbid"`、strict/frozen 且以 literal `command_type` 鉴别的 closed `LifecycleCommand` union，不接受“调用某 reducer method”或自由 event list 等旁路：

```text
LifecycleCommand = AppendRunEventsCommand
                 | CommitTerminalCommand
                 | CreateReplacementRunCommand
                 | CreateClarificationChildRunCommand
```

四个 branch 共有 `schema_version="automarkov.lifecycle-command.v1"`、literal `command_type`、canonical UUIDv7 `command_id`、`actor_principal_id`、UTC `issued_at` 和 nonempty canonical `idempotency_key`，但 exact head 与 payload 合同不同：

| command branch | exact CAS 输入 | 允许的效果 |
|---|---|---|
| `AppendRunEventsCommand` | `run_id`、`expected_state`、`expected_head: VerifiedEventHead or None`、nonempty typed event-body tuple | `expected_head=None` 时只能原子创建 root Run 并追加唯一 `RunCreated` sequence 0；否则只能追加普通 nonterminal lifecycle/control-audit events、或 terminal run 的允许 post-terminal events。post-terminal branch 同事务创建下一 `RunAuditProjection`；任何 branch 均不得产生 terminal transition、child stream 或新 `TerminalResult` |
| `CommitTerminalCommand` | run 的 `expected_state/head`、terminal cause event bodies、唯一 terminal `StateTransitioned`、frozen job/process identity、payload outputs、resource usage 与 projector version/hash | 在一个 artifact/event CAS transaction 中落库 `ProcessExecutionTerminalRecord`、terminal event records、`TerminalResult` 与 root `RunAuditProjection`；不创建 child |
| `CreateReplacementRunCommand` | parent `expected_state/head`、child-absent precondition、old/child manifests、replacement policy/cause prerequisite、cancellation-control process identity、slot decision | 仅执行本节的 cross-run cancellation transaction：parent 终止为 `CANCELLED`、创建 parent terminal provenance、child Run/manifest/`ReplacementRunCreated` 与 control attestation |
| `CreateClarificationChildRunCommand` | terminal parent 的 `expected_parent_head`、已验 terminal-result/snapshot、child-absent precondition、signed answer bundle、continuation policy 与 child manifest | parent stream/snapshot 不变；单一 transaction 创建 child Run 并追加唯一 `ClarificationChildRunCreated` sequence 0 |

`AppendRunEventsCommand.events` 的 nonterminal branch 只允许 schema registry 内另行闭合的 `OrdinaryAppendEvent` union：它排除全部 bootstrap events、`RunSuperseded`、`RunTerminationRequested`、`BudgetExhausted`、`EvidenceBudgetExhausted`、任何 terminal `StateTransitioned` 及 post-terminal-only branches。`CommitTerminalCommand.events` 只允许 `TerminalCauseEvent + StateTransitioned(to∈terminal states)`，`TerminalCauseEvent` 闭合为 verified `ClarificationRequested|ValidationClaimed|ValidationFailed|RunTerminationRequested|BudgetExhausted|EvidenceBudgetExhausted`；其中 claim/failure 必须绑定状态表对应的 exact gate/result report 与 terminal reason。`RunSuperseded` 及其 terminal transition 只能出现在 `CreateReplacementRunCommand` 的 fixed causal tuple。terminal parent 上的 `AppendRunEventsCommand.events` 只允许下文闭合的 post-terminal set。三个 event subsets 不得依赖 runtime predicate 或 caller 声明互换 member。

`VerifiedEventHead` 是 closed value object `(run_id, sequence_no, event_hash)`，三者必须指向同一条已逐条验证的 persisted record。除 root creation 的 `None` 外，所有 mutation 都必须提供 head，不得用“current”、只有 sequence 或只有 hash 的模糊 CAS。`commit` 还必须接收由可信 transport/control 层签发、且不属于 command JSON wire 的 `AuthenticatedCommandContext`；repository 以对象能力验证其 principal、process identity 与可信接收时间，caller 在 raw command 中自报相同字段不能替代认证。`Compiler.dispatch(command)` 保持窄 public seam；其 adapter 必须对每次调用通过 transport-owned `command_context_provider(validated_command)` 获取新 context，禁止在构造期缓存并跨命令复用 principal/process/received-at。command handler 返回 closed `LifecycleCommitResult`：单 run branch 为 `LifecycleCommitReceipt`，cross-run branch 为 `CrossRunLifecycleCommitReceipt`；两者记录所有受影响 run 的 before/after verified heads、新 event IDs/hashes 与新 artifact IDs/hashes。相同 idempotency key 只能返回 byte-identical result，不同 payload 重用 key 必须拒绝。

projection 也不得默认偷读最新 head。规范 query 是 `project(run_id, as_of: VerifiedEventHead, projector_version, projector_hash) -> RunView`：repository 从 sequence 0 到 `as_of.sequence_no` 逐条验证 strict union、连续 sequence、sentinel/hash chain 与末条 hash，再用冻结 reducer 纯函数重放。`RunView` 至少冻结 run/experiment ID、as-of sequence/head、projector version/hash、current state、nullable resume state、active waiting/block binding、budget snapshot、approval projection、nullable terminal-result reference与 post-terminal audit references。head 不存在、run/sequence/hash 不一致、前缀不完整、projector 未注册或 hash 不匹配都 fail closed。可选 `project_current` 仅能先读取并返回它使用的 exact `VerifiedEventHead`，不能替代验收、terminal result 或 audit projection 所需的 specified-head API。

每个 run manifest 必须预注册唯一 `ApprovalPrincipal`。交互式产品 run 使用经认证的 `interactive_user`；confirmatory experiment run 使用 source/hash/version 均冻结的 deterministic `experiment_approval_policy`。除下一段的 no-evidence branch 外，后者只读取 generation-visible task card、Allowed Evidence、candidate artifact、critic/strict-schema/traceability/public-dev validation reports 与 canonical parent IDs；它不得读取 sealed gold/oracle、调用 LLM、提出澄清、改变 payload 或按 method/result 使用不同阈值。仅 11.11 中 exact ablation ID 的 frozen predicate projection 可以把对应被移除 component 的 predicate 标为 omitted-by-design；它不能改写任何保留 predicate 或阈值。全部适用的公开 acceptance predicates 通过时，它对 exact candidate ID 追加 `SignedApprovalEvent(decision=approved)`；未通过时以结构化且仅由该 branch 可见输入导出的 public reason 追加同类型 `decision=rejected` event，并只在既有 public-dev revision budget 内形成新 revision。已批准 exact artifact 后续被确认无效时，同一 registered principal 或 manifest 中显式登记的 revocation principal 只能追加 `decision=revoked` 的签名事件，不能删除或改写原 approval。`HITL-ORACLE` clarification broker 仍只返回当前 answer payload，永不充当 ApprovalPrincipal，也不接收/返回 candidate metadata。`AUTO/v5` 在进入 approval gate 前按 clarification contract 终止；其余实验 cell 不允许临时人类审批或未登记 oracle 介入。

`automarkov_no_evidence` 的 experiment approval policy 必须使用 run manifest 冻结的 `NoEvidenceApprovalProjection`。该 projection 的输入 allowlist 恰为 task-card artifact、candidate artifact、`EvidenceOmissionRecord`/`EvidenceOmissionBinding`、无需外部证据即可由 task card 与 candidate 确定性导出的 strict-schema/structural/API/public-dev reports、这些对象的 canonical direct parents，以及 opaque sealed commitments；Allowed/Blocked Evidence manifest、`EvidenceLedger`、source metadata、evidence handle/snippet/cache、evidence-derived critic/traceability report 和任何 evidence artifact ID/hash 均不得挂载、读取或出现在 `input_report_artifact_ids`。projection 仅移除 evidence-closure 与 evidence-derived predicate，其他 branch-visible predicate/阈值不变。其 `reason_code` 只能取预注册 closed allowlist 中由上述 branch-visible checks 直接确定的通用 predicate ID；不得编码或暗示被隐藏 claim、来源、snippet、evidence count、locator 或检索结果。revision loop 只接收该 closed reason code 与对应 branch-visible validation report，禁止通过 rejection event、日志或错误文本回传 evidence-derived feedback。runner 必须在首个 approval call 前校验 approval input DAG 的完整 taint closure，并以 no-evidence principal 无 EvidenceGateway/Tavily capability 的 mount/egress attestation 证明这一边界。

approval/rejection/revocation 不能塞进通用 event 的自由 `data`。event union 必须包含 `extra="forbid"`、strict/frozen 的 `SignedApprovalEvent`，字段固定为：`schema_version="automarkov.approval-event.v1"`、`signing_domain="AutoMarkov-Approval-v1"`、canonical `event_id`、`experiment_id|None`、`run_id`、`sequence_no`、`previous_event_hash`、`decision ∈ {approved,rejected,revoked}`、`artifact_id`、`artifact_payload_hash`、`supersedes_approval_event_id|None`、`approval_principal_id`、`approval_principal_kind ∈ {interactive_user,experiment_approval_policy,registered_revocation_policy}`、`approval_policy_source_hash|None`、去重排序的 `input_report_artifact_ids`、`reason_code`、`issued_at`、128-bit random `nonce_b64url`、`signing_key_id` 与 `signature_b64url`。`approved/rejected` 必须令 `supersedes_approval_event_id=None`；`revoked` 必须引用同 run、artifact ID/payload hash 的当前有效 approved event，且 signer 必须是原 principal 或 run manifest 登记的 revocation principal。signature 使用 Ed25519，对除 `signature_b64url` 外整个 closed object 的 RFC 8785 JCS bytes 签名；没有裸字符串拼接或部分字段签名。

run manifest 冻结 principal→key ID/public key、允许的 principal kind、revocation authority、policy source/image hash 与 key validity/revocation contract。repository 必须先验证 signature、domain/schema、artifact payload hash、policy source hash、input-report allowlist、run/experiment/sequence/previous hash、clock window、superseded approval identity 与 key status，再以 compare-and-swap 原子追加；projector 只接受这一 verified event 驱动 confirmation/revocation projection。`event_id`、`nonce`、`(signing_key_id, run_id, sequence_no)` 在 repository 全局 replay index 中唯一；跨 run copy、旧 sequence、nonce/event reuse、unknown/revoked key、重复/串联撤销、signature malleability、artifact/hash/policy/report substitution 全部 fail closed。revocation 只令该 exact approval 自撤销 sequence 起失效，不删除历史。candidate freeze 前，仍依赖该 approval 的 nonterminal run 必须在同一 repository transaction 追加 `StateTransitioned`，文字审批回到 `TEXT_DRAFTED`、形式审批回到 `FORMAL_DRAFTED`，并以 supersede events 失效化依赖工件；candidate freeze 后的 nonterminal run 转 `CANCELLED`，并且只有按冻结 replacement policy 创建的新 child Run 才能继续。terminal run 不回写状态；immutable `TerminalResult` 只绑定产生终态的 typed event reference、该原子提交完成时的 `terminal_snapshot_event_head_hash`、terminal-time approval event references/validity、payload output artifact IDs/hashes，以及启动前冻结的 fixed-commit job manifest、产生终态的 exact process terminal record 与 process execution identity；它不引用尚未生成的 `ExecutionAttestation`，不声称绑定后来追加的“最终”event head，也永不覆盖。每个 bounded `ProcessExecution` 都先生成自己的 immutable `ProcessExecutionTerminalRecord`；runner 在该 execution 的 payload outputs 和 terminal record 完成并定址后才签发 `ExecutionAttestation`。只有恰好产生 Run terminal CAS 的 execution 才让 attestation 额外引用该 Run 的 `TerminalResult`，形成 job manifest→process terminal record→optional run terminal result→attestation 的单向依赖；非终态 training/e2e/analysis execution 不得伪造或提前创建 `TerminalResult`。后续审批撤销或审计事件只生成带自身 `as_of_event_head_hash` 的新 immutable `RunAuditProjection` 版本；当前视图可由 event stream 重建，旧 terminal snapshot 保持可寻址。撤销后的 projection 将被撤销的 exact payload/result 排除出直接质量解释，同时保留原 intention slot、method/pair/seed denominator 和 signed deviation。对 E2E/policy outcome mask 内的 slot 固定映射 `E2EValid=0`、`GoldPolicyEvaluationValid=0`、`Q_gate=0`；不得改标 `N/A`、删除 observation、替换 seed/method 或因结果创建 confirmatory retry。hash chain负责顺序与完整性，Ed25519负责来源认证，两者不能互相替代。

`ProcessExecutionTerminalRecord` 是每个 fixed-commit job 恰好一个、与 Run 是否终止无关的 content-addressed strict/frozen artifact。closed payload fields 固定为 schema/domain、experiment/run/job/process-execution/profile/principal identity、job-manifest ID/hash、`status ∈ {success,terminal_failure}`、exit/reason code、started/finished-at、stdout/stderr hash、按 artifact ID 排序的 payload-output `ArtifactReference` tuple、resource-usage `ArtifactReference`、network/mount/capability-decision/egress-log hashes 与 terminal-record created-at。其 `PayloadBoundParentContract` 恰好抽取 job manifest、payload outputs 和 resource-usage artifact；它明确排除 future `TerminalResult`、`RunAuditProjection` 与 `ExecutionAttestation`，任何反向引用或多余 envelope parent 均在定址前拒绝。

`TerminalResult` 是 terminal CAS 同事务创建的 content-addressed strict/frozen artifact。closed payload fields 恰为 schema/domain、run/experiment ID、job-manifest `ArtifactReference`、process-terminal-record `ArtifactReference`、`process_execution_id`、terminal `EventReference(event_id,sequence_no,event_hash)`、`terminal_snapshot_event_head: VerifiedEventHead`、terminal state/reason、与 process record byte-identical 的 sorted payload-output references、按 approval event ID 排序的 terminal-time approval event ID/hash/validity tuple、projector version/hash 和 created-at。payload exact keyset 明确排除自身 artifact ID/hash、future attestation 和 future audit projection；envelope identity 只在 payload/envelope bytes 定址后由 repository 赋予。其 `PayloadBoundParentContract` 恰好抽取 job manifest、process terminal record 与所列 payload outputs；terminal/approval event ID/hash 只进入 typed event-reference fields，不进入 artifact parent DAG。terminal snapshot sequence/head 必须恰等于本 transaction 的新 head，process record 的 run/job/process identity、status 和 output tuple 必须与 terminal command 逐字节一致。

`RunAuditProjection` 也是 content-addressed strict/frozen artifact。closed payload fields 恰为 schema/domain、projection/run/experiment ID、projector version/hash、`as_of_event_head: VerifiedEventHead`、previous-projection `ArtifactReference|None`、terminal-result `ArtifactReference`、当前 approval event references/validity tuple、post-terminal audit event-reference tuple、signed-deviation artifact references 与 derived outcome mask；root projection 的 previous pair 同时为 null，后续版本必须引用前一 projection。其 `PayloadBoundParentContract` 恰好抽取 nullable previous projection、terminal result 与 deviation artifacts，event references 仍排除在 DAG 外。projection ID 由除自身 ID 外的 RFC 8785 JCS preimage 域分离确定。repository 只允许从 caller 指定且已验证的 event head 确定性重放生成，以 `(run_id,as_of_sequence_no,projector_hash)` UNIQUE CAS 落库；重复请求只能返回相同 bytes/hash，head/sequence/previous projection mismatch 或覆盖旧 projection 一律拒绝。

`CommitTerminalCommand` 的原子 provenance 顺序固定为：锁定 expected head 并用 reducer 验证 exact terminal transition；验证/定址 payload outputs 与 resource usage；构造完整 terminal cause/`StateTransitioned` event bodies 并预计算它们的 record hashes；定址不反向引用 terminal artifacts 的 `ProcessExecutionTerminalRecord`；定址绑定 exact process record 与新 event head 的 `TerminalResult`；从同一新 head 重放并定址 root `RunAuditProjection`；最后在单一 transaction 中同时插入这三个 provenance artifacts、已定址的 payload outputs 及 event records，并更新 head。任一 schema/signature/hash/parent/reducer/uniqueness/write 失败都整笔回滚，不能留下 terminal event 却无 process record/result/root projection，也不能留下不可达的 terminal artifact。若 terminal-derived outcome 必须引用本 terminal event hash，repository 在事务内使用已完整确定但尚未可见的 event bytes/hash 定址 outcome，再将 outcome 列入 process/result 的同一 output tuple；任何可改变 event bytes 的后续步骤都拒绝。

runner 只在上述 terminal transaction 成功后签发 `ExecutionAttestation`，并重验同一 process record。attestation 的 `terminal_result_artifact_id/hash` 是 closed nullable pair，只有本 execution 原子产生 Run terminal CAS 时才同时非空并匹配 `TerminalResult.process_execution_id` 与 process-record binding，其余 execution 必须同时为 null。一个 execution/job 的 terminal record 与 attestation 各恰好一个，retry 必须创建新预登记 execution identity。第 4.4 节 cross-run cancellation 是更严格的特例：依 ADR0001，其 control attestation 与 parent terminal provenance/child sequence 0 必须在同一 cross-run transaction 可见或整体回滚。

terminal head 后可追加的 branch 闭合为 `SignedApprovalEvent(decision=revoked)`、`ArtifactAccessRevoked`、`ClarificationEvaluationRequested`、`ClarificationEvaluationRecorded` 和 `SpecificationConflictDetected`；其余 approval decision、bootstrap/lifecycle/control event、任何 `StateTransitioned` 或 generation input 都拒绝。每次允许的 post-terminal append 都必须在同一 `AppendRunEventsCommand` transaction 中，从新 specified head 生成一个以旧 projection 为 direct parent 的新 `RunAuditProjection`；原 `TerminalResult`、root projection 和 terminal snapshot 不变。event 已追加但 projection 缺失、projection 未绑定新 head、跳过 previous projection 或就地覆盖均必须整笔回滚。

| 当前状态 | 事件与 gate | 下一状态 | 失败/回边 |
|---|---|---|---|
| `RECEIVED` | intake schema 合法、预算和权限存在 | `RESEARCHING` | 缺权限→`BLOCKED`；用户取消→`CANCELLED` |
| `RESEARCHING` | 研究问题有 evidence/insufficient 标记且 ledger 闭合 | `TEXT_DRAFTED` | pool 暂时 cooldown/leased→`WAITING_EVIDENCE`；凭据/权限待外部处理→`BLOCKED`；额度耗尽→`BUDGET_EXHAUSTED`；vLLM 不可用→`WAITING_RUNTIME`；不可恢复内部错误→`FAILED` |
| `TEXT_DRAFTED` | strict schema pass | `TEXT_REVIEWED` | parse/schema issue→新 `TEXT_DRAFTED` revision |
| `TEXT_REVIEWED` | critical issue 已全部关闭；high issue 已关闭，或每项都转成 explicit assumption 且等待同一 registered principal exact-ID 接受；关键参数均有来源/确认或保持符号化 | `WAITING_TEXT_CONFIRMATION` | 普通编译的 strict `ClarificationRequiredResult`，或 `AUTO/v5` 的 strict `ExperimentClarificationRequiredResult`，其 result/run IDs 与 canonical direct parents 全部匹配；实验 wrapper 还必须匹配 outcome mask→terminal CAS 写入 reason=`clarification_required` 并转 `CLARIFICATION_REQUIRED`，随后仅实验 branch 由 frozen projector 签发 missingness projection；任一 critical 未关闭、high 未关闭且未转为 explicit assumption、未经澄清引入假设或继续形式化→拒绝转换；critic 可据现有 evidence 修订→`TEXT_DRAFTED` |
| `WAITING_TEXT_CONFIRMATION` | manifest 登记的 `interactive_user` 或 `experiment_approval_policy` 对 exact artifact ID 追加 verified `SignedApprovalEvent(decision=approved)` | `TEXT_LOCKED` | signed rejection→`TEXT_DRAFTED`；interactive authority 待响应→`BLOCKED`；signature/principal/hash/输入越权→`FAILED` |
| `TEXT_LOCKED` | verified approved `SignedApprovalEvent` 与 payload hash 一致，strict `ClassificationResult` 的 source/evidence binding 与 branch-specific canonical parents 验证通过 | `CLASSIFIED` | signature/hash/kind/identity/parent 不一致→`FAILED` |
| `CLASSIFIED` | strict `ClassificationResult` 为 `IN_SCOPE_*`，且无 reduction lineage 或 normalized kind 与已批准 proposal target 一致 | `FORMAL_DRAFTED` | `REDUCIBLE` 或 reduction target mismatch→`REDUCTION_PROPOSAL_DRAFTING`；`OOD`→`OOD_HANDOFF_BUILDING`；identity/parent mismatch→`FAILED` |
| `REDUCTION_PROPOSAL_DRAFTING` | strict initial/superseding `ReductionProposal` 的 target、trigger、supersedes 与 canonical parent tuple 全部验证通过 | `WAITING_REDUCTION_CONFIRMATION` | schema/lineage/mismatch issue→新 proposal revision；不可恢复错误→`FAILED` |
| `WAITING_REDUCTION_CONFIRMATION` | 用户批准 strict `ReductionProposal` 的 exact ID，并创建以 source task、base classification、当前 proposal 为 exact direct parents 的新 core `TaskContract` | `TEXT_DRAFTED` | 未创建新 contract、parent tuple 不匹配或未保留 lineage→拒绝转换；拒绝归约→`OOD_HANDOFF_BUILDING`；等待→`BLOCKED` |
| `OOD_HANDOFF_BUILDING` | generic referral 已冻结 evidence/authority identity、capability/unsupported-feature declarations 与 traceability；executable OpenSpiel/PDDL subtype 还须冻结 source/profile identity | `OOD_HANDOFF_VALIDATING` | schema/semantic issue→新 handoff revision；资产或许可待授权→`WAITING_ASSET`；其他 authority 缺失→`BLOCKED` |
| `OOD_HANDOFF_VALIDATING` | generic referral 的 schema/evidence/capability/package checks，以及 executable route 的官方 adapter/profile、round-trip/playthrough/solve/validate 中适用 checks 全部通过 | `OOD_PACKAGED` | handoff issue→`OOD_HANDOFF_BUILDING`；资产或许可待授权→`WAITING_ASSET`；不可恢复错误→`FAILED` |
| `FORMAL_DRAFTED` | discriminated union + structural checks pass | `FORMAL_REVIEWED` | formal counterexample→新 `FORMAL_DRAFTED` revision；新语义假设→`TEXT_DRAFTED` |
| `FORMAL_REVIEWED` | critical/high issue 闭合 | `WAITING_FORMAL_CONFIRMATION` | 数学修订→`FORMAL_DRAFTED`；语义修订→`TEXT_DRAFTED` |
| `WAITING_FORMAL_CONFIRMATION` | 同一 registered `ApprovalPrincipal` 对 exact spec artifact 追加 verified `SignedApprovalEvent(decision=approved)` | `FORMAL_LOCKED` | signed rejection→`FORMAL_DRAFTED`；interactive authority 待响应→`BLOCKED`；signature/principal/hash/输入越权→`FAILED` |
| `FORMAL_LOCKED` | approval、traceability、validation reports 完整 | `IMPLEMENTATION_SELECTED` | DAG 不闭合→`FAILED` |
| `IMPLEMENTATION_SELECTED` | Reuse/Compose/Generate 恰选其一，profile 可解 | `ENVIRONMENT_IMPLEMENTED` | runtime 不可用→`WAITING_RUNTIME`；受限资产待人工授权→`WAITING_ASSET`；其他权限问题→`BLOCKED` |
| `ENVIRONMENT_IMPLEMENTED` | code/config/commit artifacts 冻结 | `UNIT_VALIDATING` | build/import issue→新 implementation revision |
| `UNIT_VALIDATING` | mandatory schema/static/import/minimal-run/unit/API/shape/dtype/bounds/action-mask/seed 与 deterministic core invariant checks pass | `SIMULATION_VALIDATING` | 当前 gate 所需 runtime profile/remote service 暂不可用→绑定 exact dependency 的 `WAITING_RUNTIME`；implementation issue→`ENVIRONMENT_IMPLEMENTED`；formal/semantic issue→相应上游 |
| `SIMULATION_VALIDATING` | property/metamorphic/generation-visible differential/public-dev trajectory gates 与 fixed-budget `PublicDevLearningProbe` pass，随后 candidate bundle/`E2EGateEvaluationRequest` 冻结 | `SEALED_E2E_VALIDATING` | 当前 gate 所需 runtime profile/remote service 暂不可用→绑定 exact dependency 的 `WAITING_RUNTIME`；capability-aware router 只允许 independently derived public-dev counterexample 回到其授权层；official/reference-derived differential/trajectory payload只能回 `ENVIRONMENT_IMPLEMENTED`/Tester，禁止流入 Text/Formal；精确 ablation gate projection 见 11.11 |
| `SEALED_E2E_VALIDATING` | 独立 evaluator 对 frozen candidate 返回 signed `E2EGateVerdict`，且 `text_passed=formal_passed=api_passed=hidden_behavior_passed=true`；任何 verdict/category/trace/counterexample 都不向 generation principal 释放 | `TRAINING_SMOKE_TESTING` | evaluator 暂不可用→绑定 evaluator protocol edge 的 `WAITING_RUNTIME`；任一合法 false→terminal CAS 写入 reason=`sealed_e2e_gate_failed`、保留 `E2EValid=0`并终止为 `PARTIAL`，随后 frozen projector签发 missingness projection；签名/binding/schema 无效、contamination 或 protocol 违规→`FAILED`；禁止修补或原 bundle 重试 |
| `TRAINING_SMOKE_TESTING` | RLlib EnvRunner sample、module forward、checkpoint round-trip pass并产生 signed smoke-pass attestation | `POLICY_TRAINING` | runtime 缺失→`WAITING_RUNTIME`；资产缺失→`WAITING_ASSET`；adapter/config smoke 不通过→terminal CAS 写入 reason=`training_smoke_failed`、记录 `GoldPolicyEvaluationValid=0`并终止为 `PARTIAL`，随后 frozen projector签发 missingness projection；attestation/protocol 违规→`FAILED`；禁止回退修改 frozen candidate |
| `POLICY_TRAINING` | 冻结预算已执行且 10 seeds 的 success/failure terminal 状态均有记录 | `FINAL_EVALUATING` | runtime 缺失→`WAITING_RUNTIME`；冻结预算耗尽→同一 terminal CAS 先签发 `post_training_terminal(phase=training,reason=budget_exhausted)` outcome再转 `BUDGET_EXHAUSTED`；attestation/protocol 违规→`FAILED`；不得因 learning diagnostic 回退修改 frozen candidate |
| `FINAL_EVALUATING` | evaluator-signed `evaluated|invalid` `PolicyOutcomeRecord` 的 request ID/hash exact direct parent、十 seed request/export/evaluation records、evaluator signature、outcome branch与许可全部验证通过；低策略分数/不支持研究假设是结果，不是 gate failure；不重跑或更改 pre-training `E2EGateVerdict` | `PACKAGING` | evaluator 暂不可用→绑定 evaluator protocol edge 的 `WAITING_RUNTIME`；缺必需结果但无安全/完整性违规→同一 terminal CAS 先签发 `post_training_terminal(phase=final_evaluation,...)` outcome再转 `PARTIAL`；request/parent/seed/signature/binding/license/contamination/protocol 违规→`FAILED` |
| `PACKAGING` | manifest 可重建、公开包无 secret/gold/restricted asset | `COMPLETED` | 缺必需产物→`PARTIAL`；secret/license violation→`FAILED` |
| `WAITING_RUNTIME` | 用户明确声明原资源恢复或提供新资源，且 waiting event 绑定的 exact dependency identity 与原 readiness gate 重新通过 | event 中的 `resume_state` | dependency/gate identity 不匹配或原 gate 仍失败→保持等待；用户取消→`CANCELLED`；不再继续→`PARTIAL` |
| `WAITING_EVIDENCE` | 当前时间达到最早 `available_at/leased_until`，且原子租约 probe 发现可租 slot | event 中的 `resume_state` | 额度耗尽→`BUDGET_EXHAUSTED`；凭据/权限需外部处理→`BLOCKED`；用户取消→`CANCELLED` |
| `WAITING_ASSET` | 用户完成 EULA/login/asset provisioning，且 license/hash gate 重新通过 | event 中的 `resume_state` | 用户拒绝或资产不可合法获得→`PARTIAL` |
| `BLOCKED` | `BlockResolved` 且原 gate 重新验证 | event 中的 `resume_state` | 用户取消→`CANCELLED`；确认不再继续→`PARTIAL` |

可恢复与预算路径是 reducer 的一等闭合规则，不是调用阶段自行解释的 error mapping：

- `WaitingRuntime`、`WaitingEvidence` 与 `WaitingAsset` 共有 exact `resume_state`、closed `wait_reason_code`、`trigger_event_id/hash`、`failure_report_artifact_id/hash`、`recovery_gate_id`、`recovery_condition_hash` 与 `entered_at`。`resume_state` 必须逐字节等于进入等待前的 current nonterminal state；`WaitingRuntime` 另含下文 exact dependency binding，`WaitingEvidence` 另含 lease-pool/snapshot ID/hash 与 earliest availability，`WaitingAsset` 另含 asset/license/provisioning-authority identity。cause event→matching waiting event→`StateTransitioned(from=resume_state,to=WAITING_*)` 必须在同一 command 中相邻追加；三者不得交叉使用 reason 或恢复 gate。
- 等待中除 matching `WaitResolved`、用户取消/接受 partial、已验预算耗尽，以及 `WAITING_RUNTIME` 的预注册 replacement 外，reducer 拒绝任何普通 stage event。`WaitResolved` 的 exact fields 为 `wait_kind`、waiting-event ID/hash、`resume_state`、原 recovery-gate ID、新 recovery-report ID/hash、dependency/lease/asset identity hash 和 resolved-at；它必须与 active waiting binding 全部一致，并与 `StateTransitioned(from=WAITING_*,to=resume_state)` 在同一 command 中相邻追加。probe 仍失败只能保持原 view，不追加伪恢复 event。
- `Blocked` 的 exact fields 为 `resume_state`、closed `block_reason_code`、`external_authority_kind`、`external_authority_principal_id`、`resolution_condition_hash`、failure-report ID/hash、recheck-gate ID 与 entered-at；`EvidenceAuthorityRequired` 是它的 evidence-specific typed trigger，不能用自由 reason 代替。trigger→`Blocked`→`StateTransitioned(...,BLOCKED)` 必须同 command 相邻追加。`BlockResolved` 必须绑定 active blocked-event ID/hash、registered authority、resolution-evidence artifact ID/hash 与重验报告，然后与返回 exact `resume_state` 的 transition 同 command 提交；临时 runtime/evidence/asset 不可用、已证预算耗尽或内部 bug 均不是 `BLOCKED`。
- `BudgetExhausted` 是 terminal cause event，exact fields 为 `budget_kind ∈ {revision,token,tool_call,provider_credit,wall_time,global_cost}`、budget-policy artifact ID/hash、budget-snapshot artifact ID/hash、canonical unit、nonnegative limit/consumed/reserved values、cause receipt/report ID/hash、`phase`、reason code 和 exhausted-at；`consumed+reserved >= limit` 必须由冻结 policy 与 receipt 可重算。evidence quota 还必须由 `EvidenceBudgetExhausted` 的全 registered-account provider receipts 闭合；存在可修复 invalid credential 时只能 `BLOCKED`。cause→`StateTransitioned(...,BUDGET_EXHAUSTED)` 只能由 `CommitTerminalCommand` 原子提交；policy training 已启动时同一 transaction 还必须产生第 11.7 节 closed `post_training_terminal` outcome。不允许转 `PARTIAL`、恢复、扩预算或新 run 覆盖原 terminal denominator。

所有转换必须追加 `StateTransitioned` event，包含 from/to、触发 event、input artifact IDs、gate report ID 和预算快照。非法转换、跳级和仅凭 LLM 自评分放行必须由 reducer 拒绝。每条回边都有 `max_revisions_per_stage` 和全局成本上限，不得无限循环或暗中扩预算。

第 11.10 节的 suite→variant→pair→seed 嵌套统计属于实验级 `experiment analyze` job；它只在对应 intention-to-run matrix 的 run terminal records 齐全后执行，不是任一单个 `Run` 的 `FINAL_EVALUATING→PACKAGING` 门禁，也不得被写回单 run state projection。

每个 `WaitingRuntime` event 必须以 closed fields 冻结 `resume_state`、`dependency_kind`（`local_llm|runtime_profile|remote_service`）、`profile_id|None`、`process_execution_id|None`、`protocol_edge_id|None`、`dependency_identity_hash`、`failed_readiness_gate_id` 与原 failure report ID。原 `Run` 的恢复只能重跑同一 identity 的同一 gate：`local_llm` 重跑 health、served-model/tokenizer identity 与真实 completion；`runtime_profile` 重验 lock/image/SBOM/platform、import 与该 profile 的 smoke contract；`remote_service` 重验 endpoint identity、transport authentication、protocol handshake 与服务 canary。identity 的任一组成变化都禁止恢复或 supersede 原 waiting event；用户若接受新 identity，coordinator 必须按预注册 replacement policy 取消原 Run 并创建 child Run。child 不继承原 Run 的生成工件、readiness 结论、`TerminalResult`、`RunAuditProjection` 或其他结果；它能否占用同一 confirmatory slot 只由 generation/tool call 前已冻结的 replacement rule 决定，否则原 slot保留 terminal failure。不能用 vLLM completion 代替 Ray/env/runner profile readiness，也不能用任意 profile smoke 恢复另一个 dependency。

`RunSuperseded` 是 event union 中一个由 `supersession_cause` 鉴别的 `extra="forbid"`、strict/frozen、Ed25519-signed union。两个 branch 的 common exact fields 为：`schema_version="automarkov.run-superseded.v1"`、`signing_domain="AutoMarkov-Run-Superseded-v1"`、canonical `event_id`、`experiment_id|None`、`run_id`（old）、`sequence_no`、`previous_event_hash`、`supersession_cause ∈ {runtime_identity_replacement,approval_revocation}`、`child_run_id`、positive-safe-integer `replacement_ordinal`、old/child run manifest artifact ID 与 payload hash、replacement policy artifact ID 与 payload hash、`replacement_eligibility ∈ {confirmatory_slot_reused,new_nonconfirmatory_slot,slot_terminal_failure}`、`replacement_authority_principal_id`、`reason_code`、`issued_at`、128-bit random `nonce_b64url`、`signing_key_id`、`signature_b64url`。`runtime_identity_replacement` branch 另外且只能包含 `failed_waiting_event_id`、`failed_readiness_gate_id`、`old_dependency_identity_hash`、`new_dependency_identity_hash`；二者必须不同并与当前 `WAITING_RUNTIME` projection 精确匹配。`approval_revocation` branch 另外且只能包含 `revocation_event_id`、`revoked_approval_event_id`、`artifact_id`、`artifact_payload_hash`；它们必须绑定同 run、candidate freeze 后当前失效的 approved exact artifact。signature 覆盖移除且仅移除 `signature_b64url` 后的完整 RFC 8785 JCS object；branch field 混用、缺失或额外字段全部拒绝。

`SignedRunReplacementPolicy` 是 experiment/run manifest 引用的独立 strict/frozen signed input，payload 只冻结 schema/domain、experiment ID、允许的两个 cause、authority principal/key/status、root ordinal、child ordinal rule、每个 cause 的 eligibility rule、generation/tool-call boundary、confirmatory slot rule、maximum child count、issued-at/nonce/signature。payload 严格不包含自身 policy artifact ID/hash；先对该 payload 签名并通过 canonical envelope 定址，随后 run/experiment manifest 与 replacement events 只引用 repository 已计算的 envelope artifact ID 和 payload hash。root `RunManifest.replacement_ordinal` 必须为 `0`；每个 child manifest 同时冻结 `parent_run_id`、`parent_run_superseded_event_id`、`supersession_cause` 与 `replacement_ordinal=parent.replacement_ordinal+1`。runtime identity 只有在首个 generation/tool call 前才可按预注册规则复用 confirmatory slot；其后以及 candidate-freeze approval revocation只能产生 `slot_terminal_failure` 或 `new_nonconfirmatory_slot`。child 不继承 old outputs、生成工件、readiness 结论、`TerminalResult` 或 `RunAuditProjection`；runtime child 冻结新 identity/profile graph/budget/edge IDs，approval child 重新从仍有效的 immutable root inputs 执行。

replacement child 的 sequence-0 event 只能是独立 strict/frozen signed branch `ReplacementRunCreated`，字段固定为：`schema_version="automarkov.replacement-run-created.v1"`、`signing_domain="AutoMarkov-Replacement-Run-Created-v1"`、canonical `event_id`、`experiment_id|None`、`run_id=child_run_id`、`sequence_no=0`、全零 sentinel `previous_event_hash`、`run_manifest_artifact_id=child_run_manifest_artifact_id`、`run_manifest_payload_hash=child_run_manifest_payload_hash`、`parent_run_id`、`parent_run_superseded_event_id`、同一 `supersession_cause`、同一 `replacement_ordinal`、replacement policy artifact ID/hash、replacement authority principal ID、issued-at、独立 128-bit random nonce、同一 authority signing key ID 与 signature。signature/replay 规则与 `RunSuperseded` 相同。

每次 replacement 由 parent manifest 预登记的 fixed-commit cancellation-control `ProcessExecution` 执行；其 job manifest 精确绑定 old/child manifest、replacement policy、cause prerequisite、expected event IDs 和 slot decision。在锁定 old/child heads 后，repository 以一个跨 run/artifact/event 的数据库 transaction 原子完成：验证 old event head、job/process identity、signed replacement policy、cause-specific prerequisite、`child ordinal=parent ordinal+1` 与 slot rule→写入该 control execution 的 immutable success `ProcessExecutionTerminalRecord`→追加 `RunSuperseded`→追加以该 event 为 trigger 的 `StateTransitioned(<current nonterminal state>→CANCELLED)`→以该 transition 作为 terminal event 为 old run 创建 `TerminalResult(terminal_state=CANCELLED, reason=run_superseded)` 及 root `RunAuditProjection`→创建唯一 child Run/manifest→追加精确绑定 parent event 的 `ReplacementRunCreated`→签发并持久化引用 exact process terminal record 与 old-run `TerminalResult` 的 `ExecutionAttestation`。`TerminalResult` 不反向引用 attestation；process record、terminal result、root projection 与 attestation 的父节点/typed event references 仍严格遵守上文单向合同。runtime branch 的 current state 必须为 `WAITING_RUNTIME` 且 prerequisite 是 exact waiting/readiness event；approval branch 的 prerequisite 是 candidate freeze 后 exact verified revocation event。任一签名、hash、写入或 CAS 失败均整笔回滚，不能留下已取消 parent、无 terminal provenance 的 intention slot，或孤立 child。repository 强制 `UNIQUE(parent_run_id)` replacement edge、`UNIQUE(child_run_id)`、runtime branch 的 `UNIQUE(failed_waiting_event_id)`、approval branch 的 `UNIQUE(revocation_event_id)`、control execution terminal record/attestation uniqueness、confirmatory-slot uniqueness及两 event 共享字段逐字节一致；ordinal 不能被 caller 选择来绕过单 child 约束。

clarification continuation 使用独立的 `ClarificationContinuationPolicy`，不伪装成 replacement。root `RunManifest.clarification_continuation_ordinal=0`，policy 冻结 authority principal/key/status、允许的 answer artifact kind、`child_ordinal=parent+1`、每 parent 最多一个 child、budget/runtime reset rule、experiment eligibility、issued-at/nonce/signature；child manifest 冻结 parent run、parent `ClarificationRequiredResult`、parent `TerminalResult`/snapshot head、signed answer bundle、policy ID/hash和递增 ordinal，并从这些 immutable inputs 重新开始，不继承 parent outputs、approval、readiness、terminal result、audit projection或 sealed verdict。

clarification child 的唯一 sequence-0 branch 是 `ClarificationChildRunCreated`，closed fields 固定为：`schema_version="automarkov.clarification-child-run-created.v1"`、`signing_domain="AutoMarkov-Clarification-Child-Run-Created-v1"`、canonical event/experiment IDs、`run_id=child_run_id`、`sequence_no=0`、全零 sentinel previous hash、child run-manifest ID/hash、`parent_run_id`、parent clarification-result ID/hash、parent terminal-result ID/hash与 snapshot-head hash、signed answer-bundle ID/hash、continuation-policy ID/hash、positive-safe-integer continuation ordinal、authority principal ID、`reason_code="clarification_answer_received"`、issued-at、独立 128-bit random nonce、signing key ID/signature。signature 覆盖移除且仅移除 signature 后的 RFC 8785 JCS object，并执行 key/clock/nonce/event replay检查。repository 在单一 transaction 中验证 parent 当前确为 `CLARIFICATION_REQUIRED`、全部 parent/result/snapshot/answer/policy bindings、`child ordinal=parent+1` 和 eligibility，创建 child manifest/Run 后追加该 event；parent event stream与 terminal snapshot保持不变。`UNIQUE(parent_run_id)` continuation edge、`UNIQUE(child_run_id)` 与 answer/policy binding 防止双 child；失败整笔回滚。confirmatory `AUTO/v5` 不创建此 child，post-terminal verdict/outcome也永不成为 continuation input；`HITL-ORACLE` 是预注册的独立 root run。实验后若要基于答案继续，必须是新 preregistration 下的 nonconfirmatory child。

上述 dependency-aware 规则适用于所有依赖 runtime profile 或 remote service 的非终态，而不只适用于表中逐项列出的阶段：暂时的 listener/process/profile/service/readiness failure 一律追加绑定 exact dependency 的 `WaitingRuntime` 并转入 `WAITING_RUNTIME`；只有 credential、许可、provisioning 或其他外部 authority 缺失才进入 `BLOCKED`。reducer 必须拒绝把同一暂时可用性故障按调用阶段分别映射为 `BLOCKED` 或普通 implementation failure。

`LlmRuntimeDegraded` 是运行事件，不是状态。从任何当前 gate 依赖 `LocalLlmRuntime` 的非终态收到该事件时，reducer 必须转入绑定 `dependency_kind=local_llm` 的 `WAITING_RUNTIME` 并记录原状态为 `resume_state`；不依赖 LLM 的确定性校验可以继续。恢复后必须重新通过该 frozen LocalLlmRuntime 的 probe、identity 和真实 completion gate，才能回到该精确状态。任何阶段达到已冻结的 revision、token、tool-call、provider-credit、wall-time 或全局成本上限时，必须转入 `BUDGET_EXHAUSTED`；若 policy training 已启动，terminal CAS 必须先按 11.7 签发保留 intention slot 和现有 seed artifacts 的 `post_training_terminal` outcome。预算尚未耗尽但缺少外部 authority 时才使用 `BLOCKED`，短期 runtime、evidence 或 asset 可用性分别使用对应 `WAITING_*` 状态，不得以 `PARTIAL` 绕过预算终态。

## 4.5 六条深 public seams

系统只公开六条稳定接缝。它们隐藏复杂实现和第三方依赖，核心 domain code 只能依赖这些 protocol，不得越过接缝直接调用 vLLM、Tavily、Ray、外部 env 或 artifact 文件系统。

| 接缝 | 最小 public contract | 内部拥有的复杂度 | 失败语义 |
|---|---|---|---|
| `Compiler` | `start(request) -> RunId`；`dispatch(command: LifecycleCommand) -> LifecycleCommitResult`；`resume(run_id, head)`；`package(run_id, head)` | 单一 lifecycle reducer、agent routing、预算、最近致因回退、gate 顺序 | typed domain error；不吞异常，不改 approved payload |
| `ArtifactRepository` | `put(envelope)`；`get(id)`；`commit(command: LifecycleCommand, context: AuthenticatedCommandContext) -> LifecycleCommitResult`；`lineage(id)`；`project(run_id, as_of: VerifiedEventHead, projector_version, projector_hash)` | canonical serialization、closed parent contracts、content address、DAG、strict EventRecord/hash chain、cross-run CAS、specified-head projection、retention/ACL | schema/parent/conflict/cycle/signature/hash/head/reducer mismatch fail closed |
| `LocalLlmRuntime` | `start(manifest)`；`probe()`；`complete(request)`；`close()` | Qwen3.6 vLLM lifecycle、backpressure、canary、model identity、trace | degraded/unavailable；绝无 hosted fallback |
| `EvidenceGateway` | `search(query)`；`extract(urls)`；`crawl(root)`；`resolve(claim)` | 29-key lease、endpoint allowlist、ranking、cache、robots/license/ACL、provenance | insufficient/rate-limited/quarantined 显式返回 |
| `ExecutionSandbox` | `run(bundle, limits)`；`test(bundle, plan)`；`run_at_commit(job)` | namespace/container、CPU/GPU/network/file limits、fixed-commit runner、attestation | timeout/resource/security/commit mismatch 分型 |
| `EnvironmentBinding` / `TrainingRunner` | `bind(profile, env_ref) -> RemoteEnv`；`reset/step/spaces/close`；`train(frozen_plan)`；`evaluate(policy_evaluation_request)` | dependency profiles、wire protocol、RLlib new stack、trainer-local checkpoint/export、metrics、CTDE 边界 | protocol/profile/training/evaluation error 分型 |

第六条是同一“环境执行”接缝的两个窄视图：`EnvironmentBinding` 管理环境语义和 `RemoteEnv` lifecycle，`TrainingRunner` 管理 RLlib 训练与评估；二者通过 frozen `EnvironmentHandle` 连接，不能共享可变 Python env object。公开 evaluation view 只接受显式冻结、已签名且绑定 `PolicyExportManifest`/safetensors/十 seed terminal records 的 `PolicyEvaluationRequest`；普通 RLlib checkpoint 的读取、round-trip smoke 与导出只存在于同一 trainer profile/filesystem namespace 的私有实现中，checkpoint 或 locator 不跨 public seam/profile。

EventStore、command handler、reducer、terminal coordinator 与 projector 是 `ArtifactRepository`/`Compiler` 这两个视图后同一 lifecycle deep module 的私有协作部件，不得拆成第七条 public seam。所有写路必须经同一 `LifecycleCommand` union、EventRecord schema registry、head CAS 和 reducer；CLI、agent、runner、evaluator 与 experiment code 均不能直接 insert event/artifact rows、预写 projection 或调用私有“set state”方法。

接缝对象必须支持 in-memory fake 用于 CPU unit tests，同时 production adapter 必须通过相同 contract tests。任何第三方升级只允许影响 adapter/profile，并须证明 public contract 与 artifact schema 未漂移。

---

# 5. 完整工作流

## 5.1 阶段 A：任务接收与研究问题分解

输入包括：

- 用户自然语言任务描述；
- 用户提供的数据、文档或代码；
- 目标输出和评价要求；
- 可接受的假设范围；
- 允许使用的外部工具；
- LLM、搜索和训练预算；
- 安全、隐私和许可证约束。

Researcher 将任务分解为六类检索问题：

1. 领域定义与真实规则；
2. 决策主体及其信息结构；
3. 可控动作及动作约束；
4. 转移动态、随机变量和参数分布；
5. 官方环境、数据、代码和可复用组件；
6. 评价指标、基准、风险与安全要求。

检索结果写入 `EvidenceLedger`，不能直接写入环境代码。

## 5.2 阶段 B：细粒度文字任务表征

`TaskContract` 的唯一规范是第 2.9 节的 strict/frozen Pydantic model，不再把 YAML skeleton 当作可扩展输入。root exact keyset 固定为 `schema_version`、`contract_kind`、`task_identity`、`decision_structure`、`objective`、`information`、`dynamics`、`constraints`、`risks`、`episode`、`evidence_and_assumptions` 与 `validation_target`；其中 `schema_version` 只能是 `automarkov.task-contract.v1`，`contract_kind` 只能是 `core_task`。所有 nested objects 同样继承 `extra="forbid"`，所有 repeated fields 和 mappings 分别使用 `FrozenSequence` 与 `FrozenStringMapping`，因此未知 key、缺字段、隐式 coercion、mutable alias 和未注册 schema version 均在定址前失败。

该 model 的 decision timing、variable domain、message sender/delay law 都是 discriminated unions。它机械强制：decision-maker ID 唯一且非空；每个 controlled entity 仅有一个 owner，并与 external entities 不相交；sequential turn order 的 set 精确等于 decision-maker set；observable/history/message mappings 的 keyset 精确等于 decision-maker set；每个 actor 至少有一个 typed observation；observable 与 latent names 不冲突；message process 只出现在 exact recipient 下，agent sender 必须来自同一 decision-maker set，且 message-process presence 与 `message_lags` 双向一致；primary objective、success criterion、time step、horizon binding、reset condition、至少一种 episode boundary 和 validation property 均非空；evidence、assumption 与 unresolved-question IDs 各自唯一。它不含 `MDP|POMDP|MG|POSG` discriminator、Gymnasium/PettingZoo target 或实现路线，因而不会提前分类。

每个 draft 的 Python raw tree 与 JSON bytes 分别只经 `validate_task_contract_for_approval` 和 `validate_task_contract_json_for_approval` 进入：先执行第 2.9 节相同的 resource/provenance guard，拒绝既有 `BaseModel`、`model_construct()`/`model_copy()` 结果、tuple 或其他非 JSON tree，再按 exact model strict-validate、deep-freeze并装入 `CanonicalPayloadDocument` 做 RFC 8785 JCS 和 content addressing。approval gate 还必须拒绝任何 `severity ∈ {high,critical}` 的 unresolved question，并验证所有关键数值来自顶层 evidence ID、accepted assumption 或显式 symbolic binding。用户签名绑定 exact `TaskContract` artifact ID 与 payload hash；sealed text comparator 只接收同一 registered schema ID 的 canonical payload bytes 和预注册字段投影，禁止 YAML defaults、unknown-field stripping、字符串重排或 approval 后 normalization。任何文字修改都产生新 artifact ID并重新审批，旧 bytes 保持不可变。

该阶段不输出 MDP 五元组，也不提前决定 Gymnasium 或 PettingZoo。

## 5.3 阶段 C：文字表征检查与用户确认

Critic 不输出单一的 0–100 分，而输出可定位的问题对象：

```json
{
  "path": "decision_structure.decision_makers",
  "severity": "critical",
  "type": "ambiguity",
  "reason": "It is unclear whether the two controllers act independently.",
  "consequence": "The task cannot be classified as an MDP or an MG.",
  "question": "Are the two controllers optimized independently?",
  "evidence_ids": []
}
```

退出条件：

- `critical` 问题数量为 0；
- `high` 问题数量为 0，或被用户明确接受为假设；
- 所有关键数值参数具有来源、用户确认或保持为符号变量；
- 用户批准当前 `TaskContract`；
- 合同经内容哈希锁定。

每轮最多向用户提出三个高影响问题，避免一次性提出大量低价值问题。

## 5.4 阶段 D：对象分类与 OOD 路由

分类器输出以下状态之一：

```text
IN_SCOPE_MDP
IN_SCOPE_POMDP
IN_SCOPE_MG
IN_SCOPE_POSG
REDUCIBLE
OOD
```

`REDUCIBLE` 必须列出归约假设。例如，将确定性 PDDL 规划编译为 MDP，需要明确有限状态、确定性转移、奖励和终止条件。未经用户批准，不得执行归约。

## 5.5 阶段 E：数学形式化

`DecisionProcessSpec` 必须实例化第 2.9 节的 Pydantic v2 discriminated union。下面只是可读投影，不是第二套宽松 schema；实现时不得把四个 `variant` 同时放进 payload。

```yaml
kind: "MDP | POMDP | MG | POSG"
schema_version: "automarkov.decision-process-spec.v1"
state_variables: []
actions_by_agent: {}
transition_kernel: ""
initial_distribution: ""
objectives: []
constraints: []
risks: []
horizon: ""
discount: 0.0
termination_predicates: []
truncation_predicates: []
variant:
  MDP:
    state_is_observation: true
    reward: {}
  POMDP:
    observation_space: []
    observation_kernel: ""
    history_access: {}
    message_processes_by_recipient: {}
    reward: {}
  MG:
    full_state_access_by_agent: {}
    joint_action_kernel: ""
    rewards_by_agent: {}
    joint_reward_dependencies: []
    solution_concept: ""
    action_timing: "simultaneous | aec"
    aec_turn: null
  POSG:
    joint_observation: {}
    history_access_by_agent: {}
    message_processes_by_recipient: {}
    rewards_by_agent: {}
    joint_reward_dependencies: []
    solution_concept: ""
    action_timing: "simultaneous | aec"
    aec_turn: null
    centralized_training_fields: []
```

Gymnasium/PettingZoo target、Reuse/Compose/Generate choice、dependency profile 和 wrappers 属于后续 immutable `ImplementationPlan`，不能写进数学 spec 后由 developer 反向改变数学语义。

验证等级和报告不属于该 payload。它们由第 2.11 节定义的独立 `ValidationClaim` artifacts 与 append-only events 投影，避免数学 spec 为引用自己的验证结果而产生 content-address/lineage 环。

## 5.6 阶段 F：形式检查与第二次用户确认

必须执行：

- 所有符号均已声明；
- 类型、维度、shape 与单位一致；
- 离散概率归一；
- 连续分布的支持集和参数合法；
- 每个合法状态—动作组合都有转移定义；
- 确定性或随机奖励 law 完整、支持集/期望/相关性合法，并与任务目标一致；
- 终止 `terminated` 与截断 `truncated` 明确区分；
- action mask 不产生空合法动作集；
- actor 未获得用户未允许的全局状态；
- joint observation、history rewards/messages 和 AEC turn/reward accumulation 语义闭合；
- 状态包含影响未来的必要历史变量；
- MG/POSG 的奖励结构与 solution concept 已明确；
- `TaskContract` 的每项核心需求都映射到数学对象；
- 每个数学假设都有证据或用户批准；
- objective、constraint、risk 都有 traceability consumer，required validation level 的证据充分。

有限离散任务可选用 Storm/StormPy 进行 reachability 或概率属性检查。连续高维任务只能声明完成属性测试和统计模拟验证，不能伪称完成形式证明。

## 5.7 阶段 G：实现模式选择

### 5.7.1 Reuse

存在语义匹配的官方环境时，生成：

- 安装和版本锁定；
- YAML 配置；
- observation/action/reward wrapper；
- RLlib adapter；
- 语义差异报告；
- 官方实现与任务合同的映射。

### 5.7.2 Compose

已有环境组件与 wrapper 可以组合时，组合：

- MiniGrid 基础网格与观测 wrapper；
- MPE2 环境与 full-state wrapper；
- CityLearn schema 与 PettingZoo/RLlib adapter；
- MetaDrive ScenarioEnv 与任务特定奖励、观测和评估 wrapper。

### 5.7.3 Generate

仅在不存在可信官方实现或可组合组件时，才根据 `DecisionProcessSpec` 生成新的 Gymnasium/PettingZoo 环境。

复杂系统中不得重新编写已经存在的领域核心模拟器。例如：

- 不重新实现 MetaDrive 物理和道路引擎；
- 不重新实现 StarCraft II 战斗内核；
- 不重新实现 CityLearn 的完整建筑能源模拟器。

AutoMarkov 只负责生成可验证的任务配置、观测、奖励、动作约束、适配器和 RLlib 接口。

## 5.8 阶段 H：代码验证

固定验证顺序：

1. schema 与静态检查；
2. import 和最小运行测试；
3. Gymnasium/PettingZoo API 测试；
4. shape、dtype、bounds 和 action-mask 测试；
5. seed 可复现性与不可省略的 deterministic core invariant tests；
6. Hypothesis 属性测试；
7. metamorphic tests；
8. 只与 `Public Dev Store` 中 generation-visible 官方环境/fixture 做差分测试，禁止在该可修复循环读取 gold simulator；
9. 基于 public-dev fixture 的正常、边界、反事实和对抗轨迹测试；
10. 执行 frozen seed/budget 的 `PublicDevLearningProbe`，只在 candidate environment/public task contract 上用固定 lightweight learner 检测 constant/non-finite reward、action no-effect、终止不可达、观测或奖励尺度退化，并把结构化诊断路由到最近负责层；它不得挂载 sealed/gold、使用最终十个 RL seeds、生成可进入 policy outcome 的 checkpoint/metric，或替代后续 RLlib smoke/training；
11. 当且仅当 1–10 通过时，冻结 content-addressed candidate bundle 和 evaluation request；
12. 独立 `SealedEvaluator` 对该 frozen bundle 单次执行四门 E2E gold/hidden gate，只向无 generation capability 的 coordinator 返回 signed `E2EGateVerdict`；
13. 四门 conjunction 为 false 时记录 `E2EValid=0`、RL missing-by-design 并终止为 `PARTIAL`，不返回 counterexample 且不修补/重试该 bundle；签名、binding、contamination 或 protocol 违规则终止为 `FAILED`；
14. 四门 bool 全部为 true 时才执行 RLlib sampling/module/checkpoint smoke test；
15. 策略训练、frozen gold environment 上的 post-training policy evaluation 与诊断。

对候选环境实现而言，1–10 是唯一允许 implementation/behavioral/learning-diagnostic counterexample 回流修复的 public-dev 循环。`PublicDevLearningProbe` 与 publication RL training 是不同 artifact kind、seed namespace、budget 和 outcome mask。12 以后的 sealed verdict、aggregate 和 trace 不得进入同一预注册 run family 的生成/修复通道。

## 5.9 阶段 I：安全沙箱

生成代码在以下约束下执行：

- 默认关闭网络；
- 限制 CPU、GPU、内存、磁盘和执行时间；
- 只挂载必要的只读资源；
- 禁止任意 shell、`subprocess`、外部命令和 `eval`；
- 禁止访问宿主机密钥与环境变量；
- 奖励和转移表达式使用受限 AST、Lark grammar 或 SymPy 解析；
- 记录文件写入、异常、资源消耗和退出代码。

---

# 6. 规范化英文伪代码

## Algorithm 1: Evidence-Grounded, Verifier-Driven Decision-Process Synthesis

| Line | Procedure |
|---:|---|
| 1 | **Input:** user request $u$, optional resources $D$, tool and compute budgets $B$, registered approval principal $a$, and an optional bounded clarification capability $h$. |
| 2 | **Output:** a verified decision-process package $\mathcal{P}$, an explicit out-of-domain handoff specification $\mathcal{H}$, or a typed clarification-required result $\mathcal{K}$. |
| 3 | Select a frozen execution topology $T$: full method uses registered multi-role orchestration; only `automarkov_single_agent_workflow` selects the registered single-Qwen sequential topology and emits a signed non-transition `ExecutionTopologySubstituted` audit event. |
| 4 | Initialize the typed blackboard $\mathcal{B}$, issue ledger $\mathcal{I}$, and traceability graph $\mathcal{G}$. |
| 5 | Build strict evidence binding $\mathcal{V}$: normal/full methods initialize an `EvidenceLedger`; only exact `automarkov_no_evidence` emits the signed omission record and constructs `EvidenceOmissionBinding` without creating a ledger. |
| 6 | Decompose $u$ into research questions concerning actors, objectives, information, dynamics, constraints, data, and existing implementations. |
| 7 | If $\mathcal{V}$ is the ledger branch, retrieve evidence through allowlisted Tavily Search, Extract, and Crawl calls with hosted answers disabled, rank sources, identify conflicts, and record provenance; if $\mathcal{V}$ is exact `EvidenceOmissionBinding`, skip retrieval entirely and issue no Tavily call. |
| 8 | **repeat** |
| 9 | $\quad C \leftarrow \operatorname{LocalQwenTextAuthor}(u,D,\mathcal{V},\mathcal{I},T)$, then persist a new immutable artifact. |
| 10 | $\quad \mathcal{I}_T \leftarrow \operatorname{TextCritic}(C,\mathcal{V})$. |
| 11 | $\quad$ If critical or high-impact ambiguities remain, request bounded clarification from $h$ and append new artifacts/events to $\mathcal{B}$; when no answer capability is available, persist the generic $\mathcal{K}$ as `ClarificationRequiredResult` and terminate as `CLARIFICATION_REQUIRED` without inventing experiment metadata. For a frozen `AUTO` clarification-outcome cell, wrap the same generic result in `ExperimentClarificationRequiredResult`, append `ClarificationRequested`, terminate with no formal/environment artifact, and return the wrapper. After terminal result and runner attestation are fixed, a separate coordinator with no generation capability executes the sealed clarification request/verdict chain before experiment analysis; its output never resumes or mutates this run. |
| 12 | **until** $C$ satisfies the applicable textual acceptance criteria and registered principal $a$ approves its exact artifact ID. |
| 13 | Persist $a$'s verified exact-ID approval event and a strict `ClassificationResult` with discriminated $\mathcal{V}$ as `IN_SCOPE_MDP`, `IN_SCOPE_POMDP`, `IN_SCOPE_MG`, `IN_SCOPE_POSG`, `REDUCIBLE`, or `OOD`. |
| 14 | If the task is reducible, enter `REDUCTION_PROPOSAL_DRAFTING` and persist a strict `ReductionProposal`; only after exact approval create a new core $C'$ whose canonical direct parents are the source task, base classification, and current proposal, then return $C'$ to textual review. If $C'$ is later classified to a different kind than the approved target, draft a proposal that references the superseded proposal and trigger classification, obtain new approval, create another immutable child, and repeat textual review. If reduction is rejected or the task is OOD, build a generic or executable route-specific $\mathcal{H} \leftarrow \operatorname{BuildHandoff}(C,\mathcal{V})$, persist traceability/capability identities and, only for executable routes, source/profile identities; run the applicable referral, OpenSpiel, or PDDL gates and return only after `OOD_HANDOFF_VALIDATING` reaches `OOD_PACKAGED`. |
| 15 | **repeat** |
| 16 | $\quad M \leftarrow \operatorname{LocalQwenFormalizer}(C,\mathcal{V},\mathcal{I},T)$ and validate the `kind`-discriminated Pydantic union. |
| 17 | $\quad \mathcal{I}_M \leftarrow \operatorname{FormalCritic}(M,C,\mathcal{V})$. |
| 18 | $\quad$ Check symbol closure, units, transition totality, probabilities, stochastic rewards, joint observations, histories, AEC turns, objectives, constraints, risks, information boundaries, and Markov sufficiency. |
| 19 | $\quad$ If a new semantic assumption is required, return it to $h$; otherwise revise $M$ using the generated counterexamples. |
| 20 | **until** $M$ satisfies the applicable formal acceptance criteria and registered principal $a$ approves its exact artifact ID. |
| 21 | Persist $a$'s verified exact-ID approval event for $M$, then create a separate implementation plan selecting one of $\{\mathrm{Reuse},\mathrm{Compose},\mathrm{Generate}\}$. |
| 22 | $E_\theta \leftarrow \operatorname{EnvironmentDeveloper}(C,M,\mathcal{V},T)$. |
| 23 | **repeat** |
| 24 | $\quad R_U \leftarrow \operatorname{UnitTester}(E_\theta,M)$; execute schema/static/import/minimal-run/API/shape/dtype/bounds/action-mask/seed and deterministic core invariant checks. |
| 25 | $\quad R_S \leftarrow \operatorname{PublicSimulationTester}(E_\theta,C,M)$; execute property, metamorphic, generation-visible differential, and public-dev trajectory tests. |
| 26 | $\quad R_L \leftarrow \operatorname{PublicDevLearningProbe}(E_\theta,C,M)$ with frozen probe seed/budget; do not mount gold/hidden assets or use final RL seeds. |
| 27 | $\quad$ Route only actual public-dev unit/simulation/learning counterexamples from non-omitted components through a capability-aware provenance filter. Independently derived property/core/probe evidence may reach its authorized responsible layer; any official/reference-derived expected transition, reward, trajectory, state or value is confined to Developer/Tester and cannot reach Text/Formal. |
| 28 | **until** every required public gate passes and every exact ablation omission event/projection validates. |
| 29 | Freeze the content-addressed candidate bundle and signed `E2EGateEvaluationRequest`; no later step may mutate this bundle. |
| 30 | Enter `SEALED_E2E_VALIDATING` exactly once for this bundle; the isolated evaluator returns only a signed four-boolean `E2EGateVerdict` to a run coordinator with no generation capability. |
| 31 | If the signed boolean is false, record `E2EValid=0`, mark every RL outcome missing-by-design, terminate as `PARTIAL`, and release no category, trace, expected value, or counterexample; invalid signature/binding/schema, contamination, or protocol violation terminates as `FAILED`; never repair or retry this bundle. |
| 32 | Construct and pass the RLlib EnvRunner/module/checkpoint smoke contract, then construct a modern `AlgorithmConfig`, `RLModule`, `ConnectorV2`, EnvRunner, and Learner plan $A_\phi$ for the locked semantics. |
| 33 | Train the target policy $\pi_\phi$ under the frozen compute budget; for each successful seed export one manifest-bound weights-only safetensors artifact, freeze the signed exact-ten-seed policy-evaluation request, then evaluate only its explicit success bindings on the frozen gold environment; post-training results enter only the restricted reporting flow and never the generation/repair channel of this preregistered run family. |
| 34 | Classify learning failures as environment, specification, observability, reward, algorithm, budget, or runtime outcomes without using sealed trace/counterexample feedback and without mutating the frozen candidate. |
| 35 | Persist content-addressed environment, public tests, restricted evaluator reports, policy-export safetensors/manifest/commitment, prompts, tool traces, profile locks, fixed-commit attestation, data hashes, DAG, and append-only event head; raw checkpoint stays ignored and trainer-local. |
| 36 | Return $\mathcal{P}=\{C,M,\mathcal{V},E_\theta,R_U,R_S,R_L,A_\phi,\pi_\phi,\mathcal{G}\}$. |

---

# 7. 基于 Tavily 的证据驱动 Web Retrieval

本项目不假设已经拥有覆盖任意领域的本地知识库。核心检索模块应命名为：

> **Evidence-Grounded Web Retrieval**

而不是声称存在一个完整、静态且可覆盖所有任务的领域 RAG 数据库。

## 7.1 检索流水线

```text
Frozen TaskRequest / intake artifact
    ↓
Research-question decomposition
    ↓
Tavily Search
    ↓
Source filtering and ranking
    ├── selected URLs → Tavily Extract
    └── approved site root → Tavily Crawl
    ↓
Deduplication and conflict detection
    ↓
Claim–evidence records
    ↓
Task-scoped local evidence index
    ↓
Text/Formal agents
```

首次 `RESEARCHING` 必须从 sequence-0 run 已绑定的 frozen intake artifact 启动，不依赖尚未存在的 `TaskContract`。后续 `TaskContract` draft 只能依据同一 evidence budget/allowlist 提交增量 research questions，并生成新的 ledger revision；它不能改写首轮输入或形成无 provenance 的自反馈检索。

## 7.2 检索查询生成

Researcher 不直接用整段用户描述搜索，而是生成结构化查询：

```yaml
research_question_id: "RQ-001"
target_field: "dynamics.exogenous_processes"
question: "What stochastic process is used for demand arrivals in the reference task?"
queries:
  - "official documentation demand arrival stochastic process environment"
  - "paper benchmark demand Poisson process reinforcement learning"
preferred_domains:
  - "official documentation"
  - "paper publisher"
  - "author GitHub repository"
required_evidence_count: 2
conflict_policy: "escalate"
```

## 7.3 来源优先级

1. 官方软件文档、标准和数据发布页；
2. 原始论文与作者官方仓库；
3. 政府、大学和研究机构数据；
4. 正式 benchmark 页面；
5. 高质量预印本；
6. 二次博客和聚合页仅用于发现线索，不能单独确定关键动态或参数。

## 7.4 EvidenceLedger

```json
{
  "claim_id": "C-0042",
  "claim": "The environment uses simultaneous actions.",
  "supported_fields": [
    "decision_structure.action_timing"
  ],
  "source_type": "official_repository",
  "source_locator": "repository/path/commit",
  "retrieved_at": "2026-01-01T00:00:00Z",
  "source_available_at": "2025-12-15T00:00:00Z",
  "content_hash": "sha256:...",
  "evidence_excerpt_hash": "sha256:...",
  "confidence": "high",
  "conflicts_with": [],
  "license_note": "...",
  "agent_interpretation": "The official step function collects all agents' actions before state transition."
}
```

## 7.5 任务级本地证据索引

搜索结果经清洗后写入只服务于当前任务的本地索引：

```text
artifacts/<run_id>/evidence/
├── ledger.jsonl
├── documents/
│   ├── E-0001.md
│   └── E-0002.md
├── chunks.parquet
├── vector_index/
├── keyword_index/
└── conflicts.json
```

索引可采用混合检索：

$$
\operatorname{score}(d,q)
=
\alpha\,\operatorname{BM25}(d,q)
+
(1-\alpha)\,\operatorname{cosine}(e_d,e_q)
+
\beta\,\operatorname{sourceQuality}(d)
+
\eta\,\operatorname{recency}(d).
$$

其中来源质量权重不得由 LLM 随意生成，应在配置文件中固定。

## 7.6 防止 benchmark 泄漏

资源必须物理和权限隔离为三个 tier：

| Tier | 内容 | 可读 principal | 禁止内容/行为 |
|---|---|---|---|
| `Allowed Evidence Store` | task card、用户材料、批准的官方文档/论文/数据说明、抓取摘要 | Researcher、Text/Formal agents、Developer、公开 tester | 参考实现、gold spec、隐藏测试、oracle trajectory |
| `Public Dev Store` | 公开 API fixtures、公开 checker、公开 toy data、许可允许的官方 env package、开发期可见测试 | spec 锁定后的 Developer、Unit/Simulation Tester、TrainingRunner | 将 dev test 当 hidden test；向 Text/Formal agent泄漏 reference implementation |
| `Sealed Gold Store` | gold contract/spec、reference environment、hidden tests、oracle trajectories、held-out IDs、gold seeds | 独立 `SealedEvaluator` service account | 挂载到 generator/dev/training process；返回原始 gold；进入公开 artifact |

三者使用不同 root、不同 OS account/capability 和不同 artifact encryption key。仅靠目录命名或 prompt 禁令不算隔离。`SealedEvaluator` 只接受三个预注册的 signed request branch：完整 candidate 的 `E2EGateEvaluationRequest`、`AUTO/v5` 已终止 run 的 `ClarificationEvaluationRequest`，以及 post-training `PolicyEvaluationRequest`；其他请求类型全部拒绝，运行时 network disabled。E2E request 显式绑定 run/manifest、candidate bundle、candidate `TaskContract`、`DecisionProcessSpec`、`EnvironmentBinding` 及 sealed evaluator protocol/profile 的唯一 IDs/hashes，不含 gold locator，并由无 generation/sealed capability 的 coordinator 按冻结 Ed25519/JCS/key-status/replay 合同签名。text/formal 比较只把候选数据交给 trusted evaluator；任何候选代码都在独立 untrusted candidate worker 内执行，该 worker 无 sealed mount/key/locator、无网络、无任意 IPC，只通过 bounded typed protocol 接收 opaque scenario/action inputs并返回 candidate outputs。gold/reference code 在另一 trusted gold worker 内执行；只有 evaluator 内部 comparator 能同时读取两侧输出，expected transition/reward/trace 从不发给 candidate worker。evaluator 分别以 sealed gold contract/spec/API/hidden tests判定 text、formal、API、hidden behavior，并返回 `extra="forbid"` signed `E2EGateVerdict`：closed fields 仅含 schema/signing domain、verdict/request/run/manifest/candidate及四个 subject IDs/hashes、四个 exact bool `text_passed`/`formal_passed`/`api_passed`/`hidden_behavior_passed`、issued-at、32-byte nonce、`signature_algorithm="Ed25519"`、evaluator key ID/signature。signature 覆盖除 signature 外完整 RFC 8785 JCS object，request/verdict/nonce执行唯一性与 replay 校验；四门 conjunction 是 `E2EValid` 的唯一来源。verdict 只交给无 generation capability 的 run coordinator和受限分析流，不返回 test identity、trace、expected value 或 counterexample；任一合法 false 立即令该 immutable run 终止为 `PARTIAL`，签名/binding/schema 无效、contamination 或 protocol 违规则终止为 `FAILED`，两者均不得修补或重试同一 bundle。post-training evaluator 只向受限报告流释放预注册 aggregate score 和 redacted verdict，不得将任何 sealed-derived counterexample 释放给生成、开发、训练或修复 principal；最终结果也不得反馈到同一预注册 run family。

`AUTO/v5` 使用独立 post-terminal chain，不能伪造 `DecisionProcessSpec`、`EnvironmentBinding` 或 E2E request。run 必须先以 `CLARIFICATION_REQUIRED` terminal CAS 冻结 `ExperimentClarificationRequiredResult`/`TerminalResult`；wrapper 内的通用 `ClarificationRequiredResult` 提供 gap 语义，wrapper 自身提供实验 binding。runner 再按第 8.8 节签发绑定 terminal result、output hashes、mount table、capability decision log、network policy 与 egress decision log 的 `ExecutionAttestation`；只有两者均已定址后，无 generation/sealed capability 的 coordinator 才可签发 `extra="forbid"`、strict/frozen 的 `ClarificationEvaluationRequest`。其 closed fields 恰为：`schema_version="automarkov.clarification-evaluation-request.v1"`、`signing_domain="AutoMarkov-Clarification-Evaluation-Request-v1"`、request/experiment/run/cell/suite/method/pair/pair-binding IDs、generation seed、`variant_id="v5_clarification_required"`、`track="AUTO"`、run-manifest ID/hash、task/review-report IDs/hashes、outcome-mask ID/hash、clarification-result ID/hash、terminal-result ID/hash、terminal event ID/hash、terminal snapshot sequence/head hash、execution-attestation ID/hash、terminal artifact-DAG closure hash、generation-visible `clarification_oracle` sealed commitment、sealed evaluator protocol/profile/lock/image/schema identities、issued/not-before/expires-at、32-byte unpadded-base64url nonce、`signature_algorithm="Ed25519"`、coordinator key ID 与 signature。DAG closure hash 是 `SHA256(RFC8785-JCS({"domain":"AutoMarkov-Terminal-Artifact-DAG-v1","run_id":...,"terminal_snapshot_event_head_hash":...,"artifacts":<按 artifact ID bytes 排序的 artifact ID/type/payload-hash/canonical-parent tuple>}))`；`artifacts` 必须精确等于从 `TerminalResult.payload_output_artifact_ids` roots 沿 direct parents 到 root 的完整闭包，repository 与 evaluator都从显式 roots重算，禁止目录扫描或 caller omission。request 的 artifact parents 精确等于所列 public artifact references；terminal event/head 仍是 typed EventStore references。它不包含 sealed gap/oracle identity、payload/content hash、nonce、locator、answer 或 expected value。signature 覆盖从完整 request 移除且只移除 signature 后的 RFC 8785 JCS bytes；request ID、nonce、`(signing_key_id,run_id)` 与 exact subject tuple进入唯一性/replay index。

sealed evaluator 从注册 commitment 在其权限域内解析唯一 frozen gap scoring manifest；该 evaluator role没有 broker socket、credential 或 answer-serving capability。它只依据显式 request references验证：reported gap set 与预注册高影响 gap set 精确一致且没有额外伪缺口；没有 semantic guessing 或 introduced assumption；terminal snapshot/DAG 中没有 formal/environment artifact 或下游 descendant；execution attestation 的 mount/capability/egress records 证明 `AUTO` 无 `ClarificationOracleBroker` capability、访问或旁路。它返回 `extra="forbid"`、strict/frozen 的 `ClarificationEvaluationVerdict`，closed fields 恰为：`schema_version="automarkov.clarification-evaluation-verdict.v1"`、`signing_domain="AutoMarkov-Clarification-Evaluation-Verdict-v1"`、verdict/request ID/hash、experiment/run/cell/suite/variant/track/method/pair/outcome-mask ID/hash、clarification-result/terminal-result/execution-attestation IDs/hashes、terminal artifact-DAG closure hash、单一 exact bool `safe_clarification_required`、issued-at、32-byte nonce、`signature_algorithm="Ed25519"`、evaluator key ID 与 signature。signature、key-status、canonical ID、subject binding 和 replay 规则与 E2E verdict 相同；verdict envelope 的 artifact parents 精确等于 request 与所列 public subject artifacts。每个 request 只允许一个 byte-identical verdict；重复 exact request 幂等返回现有 verdict，不同第二 verdict fail closed。verdict 不释放逐项 pass/fail、缺口数量/identity、answer、trace、expected value 或 counterexample，也不回流 generation principal。

`ClarificationOutcomeRecord` 是公开分析输入的 closed discriminated union：`evaluated` 必须引用一个完全有效的 request/verdict exact parent，且 `SafeClarificationRequired` 等于该单一 bool；`invalid` 由预注册 repository-projector/analysis principal 签名，保留 experiment/run/suite/variant/method/pair/outcome-mask/terminal references，固定 `SafeClarificationRequired=0`，reason 只允许 `generation_contract_failed|missing_required_artifact|evaluation_timeout|evaluation_integrity_failure|contamination|protocol_violation`。合法 false、生成了 assumption/formal/environment artifact、错误终态、缺 request/verdict、冻结 deadline 前未完成、签名/schema/binding/replay错误、contamination 或 protocol violation 均保留原 intention slot 并映射为 0，不能改标 `N/A` 或静默删除；contamination 还必须追加 incident/deviation，并按 ADR 禁止受影响 family 进入 confirmatory statistics。相同 request bytes/request ID 的有界幂等 transport retry 可以在冻结 deadline 内执行；改变任一 subject、重新签 request 或根据结果重跑均禁止。repository 对已验证 request/verdict 分别追加 strict closed `ClarificationEvaluationRequested`/`ClarificationEvaluationRecorded` non-transition audit event；event 绑定 artifact ID/hash、terminal result/event/head、actor、issued-at与当前 sequence/previous hash，不能携带 sealed payload。post-terminal evaluation 只追加 request/verdict/outcome/audit artifacts，不改变 `CLARIFICATION_REQUIRED` terminal snapshot、向 child run传 verdict，或创建 generation 回边。

outcome union 的 common closed fields 恰为 schema/signing domain、`outcome_kind` discriminator、outcome/experiment/run/cell/suite/variant/track/method/pair/pair-binding IDs、run-manifest/outcome-mask IDs/hashes、nullable terminal/request/verdict IDs/hashes、`safe_clarification_required`、nullable closed reason、issued-at、32-byte nonce、`signature_algorithm="Ed25519"`、projector/analysis key ID 与 signature。`evaluated` 强制 terminal/request/verdict references 非空、reason 为空且 bool 等于 verified verdict；`invalid` 强制 bool 为 false、reason 非空，并按 reason/cardinality validator要求只引用实际已落库的 nullable subjects，禁止伪造缺失 artifact。signature 覆盖除 signature 外完整 JCS object；key status、clock、ID/nonce/replay 与 `(experiment_id,run_id,outcome_mask_id)` UNIQUE CAS 全部验证后才可进入分析。

每次 run 生成 access ledger，记录 principal、tier、artifact ID、purpose 和时间。CI 执行路径 denylist、gold hash scan、archive inspection 和 prompt/tool trace scan；发现泄漏后整条 generation pair 作废并进入 contamination incident，不得删日志后重跑。

每个任务设置：

- **Synthesis mode**：禁止读取参考实现，只根据自然语言描述和允许证据构建；
- **Reuse mode**：允许发现并调用官方环境，评价环境检索、选择、配置和适配能力。

该设计分别测量：

- 从任务描述构建环境的能力；
- 正确利用开源生态并完成适配的能力。

## 7.7 Tavily endpoint 硬契约

`EvidenceGateway` 的网络 allowlist 只有 `POST /search`、`POST /extract`、`POST /crawl`。禁止 `/research`、`/map`、MCP 的等价 answer/research 工具和其他搜索服务。所有 request/response 先经过版本化 schema，保存 provider `request_id`、usage、响应 hash 和调用成本。

Search 请求必须显式设置：

```yaml
endpoint: "/search"
include_answer: false
include_usage: true
include_raw_content: false
include_images: false
auto_parameters: false
search_depth: "basic | advanced"
max_results: 5
include_domains: []
exclude_domains: []
```

`include_answer` 和 `include_usage` 都不能依赖 provider default。Search、Extract、Crawl 三种 versioned request schema 均固定 `include_usage=true`；缺失或为 false 的 request 在发网前拒绝。response 出现非空 `answer` 时不得把它作为 evidence，记录 `ProviderContractViolation`；response 缺少 endpoint 对应的 usage/credit fields 时也记录 contract violation，保留 ambiguous cost reservation，且不得把未知消费记作 0。Search 只用于发现候选 URL 和短 snippets；关键 claim 必须经 Extract 或 Crawl 的原始页面内容支持。`search_depth`、provider-supported positive-integer `max_results`、Extract URL 数、`extract_depth`、Crawl `max_depth/max_breadth/limit/select_paths/exclude_paths` 全部由冻结 budget manifest 决定；示例中的 5 不是可漂移 default。

Extract 每批最多遵守官方 endpoint 限额，必须逐项处理 `failed_results`，不能因 HTTP 200 就视为全批成功。Crawl 仅对已通过 domain/robots/license/SSRF 审核的 root 启用，默认 `allow_external=false`，限定 path、depth、breadth、page limit 和 timeout；外链必须返回 Search→filter 流程，不能被 crawler 自动越权访问。

## 7.8 29-key 安全租约与轮换

key slots 固定为 `TAVILY_API_KEY_01` … `TAVILY_API_KEY_29`。代码只看到 `SecretRef(slot_id)`；secret provider 在发送 Authorization header 的最后一刻解析值。application 不枚举或输出值，不写 `.env`，不把 key 放入 URL、exception、HTTP debug log、artifact、SwanLab 或 prompt。

`KeyLeaseStore` 为所有进程提供原子租约，状态为 `AVAILABLE | COOLDOWN | EXHAUSTED | INVALID`，并维护 endpoint-specific token bucket、`leased_until`、`available_at`、失败计数和最近非敏感 request metadata。选择策略是对 `AVAILABLE` slots 的公平 round-robin；起点固定由 `HMAC-SHA-256(key=server_secret, message=RFC8785-JCS({"domain":"AutoMarkov-Tavily-Key-RoundRobin-v1","run_id":run_id}))` 派生，`server_secret` 是 KeyLeaseStore 独占的至少 256-bit secret key，避免总从 01 开始，同时不让 query 内容影响选 key。禁止交换 HMAC key/message、把 secret 或 digest 写入 ledger，ledger 只保存选中 slot ID 与非敏感调度 metadata。

一次请求流程为：

1. 原子租用一个 available slot，并预留该 endpoint 的 RPM/credit budget；
2. 发送一次请求；日志只写 slot ID、endpoint、request hash、HTTP status、provider request ID 和 usage；
3. 成功则提交实际 usage 并释放；
4. `429` 时严格采用 `Retry-After` 与 full jitter 设置该 key 的 `COOLDOWN`，释放租约后路由到下一可用 key；每个逻辑调用的 provider attempts 受冻结 budget 限制；
5. `401` 将当前 key 标为 `INVALID` 并自动路由下一 key；`403` 作为 request/endpoint permission failure 不做 transient retry，必须清除当前 `leased_until`、恢复该 slot 为 `AVAILABLE`，记录非敏感权限原因并终止当前逻辑调用；`432/433` 将当前 key 标为 `EXHAUSTED` 并自动路由下一 key。多个 key 可能共享 account quota，全池或共享账户额度耗尽时 fail closed，不能承诺轮换必然恢复；
6. 连接中断或 `5xx` 执行有界 exponential backoff + full jitter；因结果与 credit 消耗可能未知，每次 attempt 使用新 request ID、记录 `AMBIGUOUS_PROVIDER_RESULT` 并计入成本，超过预算后失败；
7. 全池无 `AVAILABLE` key 时按状态分流：若至少一个 slot 为 `COOLDOWN` 或仍持有未过期租约，先在冻结的 `key_lease_wait` 内等待；超时后追加 `EvidenceTemporarilyUnavailable` 并转入 `WAITING_EVIDENCE`，记录 `resume_state=RESEARCHING` 与最早 `available_at/leased_until`，过期租约由原子 lease store 回收。只有 29 个 registered slots 全部具有 provider usage/credit receipt 证明的 `EXHAUSTED` 状态，或 account-level signed provider usage receipt 能证明这些 slots 所属全部账户的总 quota 均耗尽时，才追加 `EvidenceBudgetExhausted` 并转入 `BUDGET_EXHAUSTED`。只要存在一个 `INVALID` slot 且上述全账户耗尽证明不成立，混合 `EXHAUSTED+INVALID` 池就追加 `EvidenceAuthorityRequired` 并转入 `BLOCKED`：事件记录各非敏感状态计数、缺少的 credential authority 和恢复后必须重新执行的 availability probe；不得把尚可经修复凭据恢复的容量当成已耗尽。全为 `INVALID` 或请求因 403 权限失败同样进入该 authority branch。任何分支都不得降级到另一个 provider 或让 LLM 凭常识补全。

官方 RPM 是上限而非目标。每个 key 的本地 limiter 使用 manifest 中更保守的值，并分别管理 Crawl 与其他 endpoint；收到 provider header/config 变化时只能收紧，不得无批准放宽。并发测试必须证明：同一 slot 不会超租、429 遵守 `retry-after`、auth error 不泄密、pool exhaustion fail closed、进程崩溃后过期租约可回收。

## 7.9 证据时间、许可与冲突

`retrieved_at` 只表示抓取时间，网页的 `published_date` 也不自动证明该信息在 benchmark cutoff 前可获得。每个 claim 还需 `source_available_at` 或明确 `availability_unverified`，并由 suite cutoff gate 决定是否可用。被允许阅读不等于允许再分发；raw capture 默认进入 ignored restricted store，公开包只保留 URL、hash、短合规摘要和 license note。

相互冲突的一手来源进入 `EvidenceConflict` artifact，记录各方 authority、version/date、受影响字段和待决问题。LLM 不得以多数票或较新日期静默化解；由明确 policy 或用户/oracle 决定，决定本身形成 append-only event。

---

# 8. RLlib 算法路线

## 8.1 现代 RLlib 新 API 栈

训练代码必须以锁定 Ray/RLlib release 的新 API stack 为唯一实现。主入口是具体 `AlgorithmConfig`（例如 `PPOConfig`），采样由 `EnvRunner`/`MultiAgentEnvRunner` 与 `EnvRunnerGroup` 执行，模型由 `RLModule`/`MultiRLModule` 表达，优化由 `Learner`/`LearnerGroup` 执行，数据变换由 `ConnectorV2` pipelines 完成，trajectory 以 `SingleAgentEpisode`/`MultiAgentEpisode` 传递。

禁止新写 `ModelV2`、legacy `Policy` customization、`RolloutWorker` callback、`num_workers` 配置、preprocessor API、`build_trainer()` 或通过关闭 `enable_rl_module_and_learner`/`enable_env_runner_and_connector_v2` 回退旧栈。若锁定 release 暂不支持某算法的新栈，该算法在该 profile 中标记 `UNAVAILABLE`，不得为追求矩阵完整而混用 legacy stack。

规范配置顺序为：

```python
config = (
    PPOConfig()
    .framework("torch")
    .environment(env=registered_remote_env, env_config=frozen_env_config)
    .env_runners(
        num_env_runners=num_env_runners,
        num_envs_per_env_runner=num_envs_per_env_runner,
        env_to_module_connector=env_to_module_connector_factory,
    )
    .learners(
        num_learners=num_learners,
        num_gpus_per_learner=num_gpus_per_learner,
    )
    .rl_module(rl_module_spec=rl_module_spec)
    .multi_agent(
        policies=policy_ids,
        policy_mapping_fn=policy_mapping_fn,
    )
    .evaluation(
        evaluation_interval=evaluation_interval,
        evaluation_config=frozen_evaluation_config,
    )
)
```

stateful POMDP/POSG policy 通过 stateful `RLModule` 的 initial state 与 forward methods 表达；不得仅设置 legacy `use_lstm=True`。action mask、frame/history stacking、agent-to-module mapping、centralized critic fields 和 normalization 均使用明确的 `ConnectorV2` 或 `RLModule` contract。Learner connector 可以读取完整 `MultiAgentEpisode` 以构造 critic input；EnvRunner 的 actor inference connector 必须删除 critic-only fields。导出的 actor checkpoint 运行图接受 local observation/history/message/reward history 与 recurrent state，不接受 global state。

每个训练 run 必须测试：config validate/build、EnvRunner sample、RLModule exploration/inference/train forward、Learner update、multi-agent module mapping、state reset、checkpoint save/restore、deterministic evaluation 和 actor-only export。Ray dashboard、Tune 与 distributed scaling 是可选 operator features，不是算法语义来源。

## 8.2 统一算法映射

| 对象或任务 | RLlib 实现 |
|---|---|
| MDP | PPO；小型离散环境增加 DQN sanity check |
| POMDP | Recurrent PPO，使用 LSTM 或 attention RLModule |
| 合作型 MG | Shared-policy PPO、Independent PPO、centralized-value PPO |
| 一般和 MG | 每种角色使用独立 policy，由 `policy_mapping_fn` 管理 |
| POSG | Recurrent centralized-training/decentralized-execution PPO |
| 连续控制 | PPO；论文复现中按需要加入 SAC 或 TD3 |
| 异构主体 | `MultiRLModule` + role-based policy mapping |
| 同构主体 | 参数共享，并向网络提供 agent ID 或 role embedding |
| 合法动作约束 | action masking connector 或自定义 RLModule |
| 变长主体 | padding、agent mask 和预定义最大主体集合 |

## 8.3 POMDP/POSG 的循环策略

循环策略的隐藏状态更新为：

$$
h_t=f_\theta(h_{t-1},o_t,m_t,r_t,a_{t-1}),
$$

其中 $m_t/r_t$ 只在对应 `HistoryAccessSpec` 授权时存在；未授权字段必须从 actor inference connector 与导出图中删除，而不是以空值旁路 capability check。

动作分布为：

$$
\pi_\theta(a_t\mid h_t).
$$

必须在 rollout、训练、评估和 checkpoint 恢复中正确传递 recurrent state，并测试 episode boundary 时 hidden state 是否被重置。

## 8.4 CTDE-PPO

对于合作型 MG/POSG，主方法采用 centralized training with decentralized execution，但 actor 输入必须保持已分类的信息结构：

- MG actor 输入 Markov-sufficient global state 与 agent identity/role；
- POSG actor 只输入本地观测、已授权的历史/消息与 recurrent state；
- critic 可输入全局状态、其他主体上下文和 agent mask；
- 执行阶段的 POSG actor 不允许读取 global state 或任何 critic-only field；MG actor 则必须继续读取分类时冻结的 Markov-sufficient global state，不得在运行时退化为 local-observation policy。

PPO clipped objective 为：

$$
L^{\mathrm{CLIP}}(\theta)
=
\mathbb{E}_t\left[
\min\left(
 r_t(\theta)\hat{A}_t,
 \operatorname{clip}(r_t(\theta),1-\epsilon,1+\epsilon)\hat{A}_t
\right)
\right],
$$

其中：

$$
r_t(\theta)
=
\frac{\pi_\theta(a_t\mid o_t)}{\pi_{\theta_{\mathrm{old}}}(a_t\mid o_t)}.
$$

文稿中应称其为：

> **RLlib-based recurrent CTDE-PPO, implemented in a MAPPO-compatible manner.**

不能仅通过修改配置名称就声称完整复现某个特定 MAPPO 实现。

## 8.5 Agent² 风格算法优化的受控搜索空间

算法优化只能在以下范围内执行：

- PPO、DQN、SAC，以及经锁定版本确认可用的 TD3；
- actor/critic 网络深度与宽度；
- recurrent module；
- learning rate；
- entropy coefficient；
- PPO clip parameter；
- batch size；
- reward normalization；
- observation normalization；
- centralized critic；
- policy sharing；
- action masking。

算法优化只能依据训练集和验证集反馈修改配置，不能修改：

- gold 测试集；
- 任务目标；
- 核心环境规则；
- 隐藏评估指标。

## 8.6 隔离 dependency profiles

不得构建包含所有环境和复现实验的单一 Python environment。每个 profile 都有独立 `pyproject.toml`、`uv.lock`、Python ABI、container digest、SBOM、license manifest 和 compatibility test；核心进程只安装 `core` protocol types。

| Profile | 允许依赖与用途 |
|---|---|
| `core` | Pydantic 2.12.0、`cryptography==49.0.0`、`rfc8785==0.1.4`、artifact/state machine、signature/JCS verification 与 domain protocols；不得包含 Ray/vLLM/环境包 |
| `authoring` | Python 3.11；CAMEL 0.2.90、Pydantic 2.12.0、httpx、sentence-transformers 5.7.0、LanceDB 0.36.0；只通过 HTTP 调用推理与检索 adapters；该 pin 满足 CAMEL 的 `>=2.10.6,<=2.12.0` 合同 |
| `llm-qwen36-vllm` | historical discovery hint 为 vLLM 0.25.1+cu129，使用前必须由 immutable runtime manifest 重新证明；未来 clean build 可在单独验证后使用 0.26.0；记录实际 PyTorch/Transformers/CUDA 与 Qwen3.6 model/tokenizer/chat-template hashes |
| `retrieval-tavily` | `tavily-python==0.7.27` 作为官方 schema/reference；生产 transport 使用锁定 httpx 直接保留 status/header/`Retry-After`、cache 与 key lease client |
| `runner-control` | Python 3.11、`cryptography==49.0.0`、`rfc8785==0.1.4`、stdlib `ssl.SSLContext` with TLS 1.3；只负责 Ed25519/X.509、mTLS profile graph、fixed-commit control、attestation/replay index，不安装 LLM/Ray/env package |
| `rllib-core` | Python 3.11；`ray[rllib]==2.56.1`、`gymnasium==1.2.2`、`pettingzoo==1.26.1`、`mpe2==1.1.0`、`minigrid==3.1.0`、`safetensors==0.8.0`、PyTorch；Gymnasium 1.3.0 和 OpenSpiel 不进入该 profile；该通用 profile 不得执行 Taxi generation/training cell |
| `rllib-taxi-synthesis` | 从 `rllib-core` 同一 lock/image 构建的 Taxi 专用 deny-layer profile；generation/training principal 对 Gymnasium `toy_text/taxi.py`、对应 bytecode/resource、wheel/sdist 与 package cache 均无 read/import capability，仅可读取公开 TaskCard/API contract 与 generated candidate；image build 和每次 execution preflight 必须证明 direct open、`find_spec`/import、resource lookup 与 cache discovery 全部 fail closed并签发 filesystem/import attestation |
| `sealed-env-taxi-gold` | Python 3.11、`gymnasium==1.3.0`、`Taxi-v4`；不安装 Ray，只经 `RemoteEnv` 接受 sealed evaluator principal，永不连接 generation 或 `rllib-core` training process |
| `sealed-evaluator-rllib` | 与 `rllib-core` 相同的 Ray/PyTorch wire-compatible pins，加 core crypto/JCS verifier与 `safetensors==0.8.0`；只从已验证 export manifest 实例化预注册可信 RLModule/connector code并加载 weights-only safetensors，拒绝读取或反序列化普通 RLlib checkpoint、pickle/cloudpickle、candidate Python code/object，随后连接 sealed gold workers；不含 authoring/Tavily capability |
| `env-minigrid`、`env-mpe2` | 默认复用 `rllib-core` 的精确 Farama versions；若上游 contract test 证明冲突才拆 profile，禁止现场松 pin |
| `env-smacv2` | SMACv2 commit `577ab5a2cff2391f8df582da5731ea9cd6adf3c6`、`protobuf<3.21`、锁定 PySC2/SC2 build/maps/checksums；独立 container |
| `env-metadrive` | Python 3.11、`metadrive-simulator==0.4.3`；ScenarioNet commit `d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170` 的 converter 因其 legacy Ray 约束再隔离，只输出 hashed scenario artifacts |
| `env-citylearn` | `citylearn==2.5.0`、`gymnasium==0.28.1`、NumPy 1.x；经 `RemoteEnv` 接入现代 RLlib，官方 CityLearn RLlib wrapper 作 differential oracle |
| `ood-openspiel` | `open_spiel==2.0.1`/`pyspiel` 与有限博弈 analysis |
| `ood-pddl` | `unified-planning==1.3.0` 与明确 allowlist 的 planner engines；PDDL 指标不进入四分类 `E2EValid` |
| `replication-agent2world-restricted` | Python 3.10、外部 checkout commit `1330f3cde9509f05d204a255f0f7f43208515dce`；ignored、不可发布、默认 disabled，SFT deferred |

Trainer-local policy export 不是独立 RuntimeProfile 或跨 profile edge。trainer 在其已绑定的 frozen RLlib/PyTorch profile、checkout 与 filesystem namespace 内启动无 sealed/gold capability 的一次性 `ProcessExecution`；该 profile 已 pin `safetensors==0.8.0`。training terminal record 先绑定 checkpoint tree 的 canonical manifest（UTF-8 relative path、size、SHA-256 按 path bytes 排序）；export execution 在加载前重验整棵 tree 及 manifest，使用 read-only snapshot/file descriptors 避免 TOCTOU，验证预注册 architecture/connector IDs 后导出 finite、closed name/shape/dtype tensor map、strict JCS manifest 与 source-checkpoint commitment，随后销毁。只有 safetensors、manifest、commitment 和 terminal record 可跨 profile；checkpoint、pickle/cloudpickle、Python object/code/import path、optimizer state与可执行 connector 永不跨 profile。

profile resolution 以 `(profile_name, lock_hash, image_digest, platform)` 唯一标识。核心不得直接 import profile package；`EnvironmentBinding` 根据 frozen manifest 启动 worker。如果两个 suite 版本约束冲突，创建两个 profile，而不是修改全局 lock。不存在 `automarkov[all]` release extra。

密码学与 canonical JSON 禁止自写实现。Ed25519/X.509 使用 PyCA `cryptography==49.0.0`；JCS 使用 Trail of Bits `rfc8785==0.1.4`（PyPI wheel SHA-256 `520d690b448ecf0703691c76e1a34a24ddcd4fc5bc41d589cb7c58ec651bcd48`）；TLS 使用 Python stdlib `ssl.SSLContext` 并把 minimum/maximum version 都设为 TLS 1.3。每个 profile 的 `uv.lock` 保留实际 platform wheel/sdist SHA-256 和 PyPI provenance，SBOM 记录 package source/tag；container manifest 另外冻结 Python patch、`ssl.OPENSSL_VERSION`、CA bundle 与 image digest。`cryptography` 的 platform wheel hash 随 ABI/platform 变化，必须由该 profile lock 精确给出，不能只信版本字符串。preflight 对版本、artifact hash、OpenSSL/TLS capability、RFC 8785 official vectors、Ed25519/X.509 vectors 和 cross-profile interop 全部 fail closed。

一个逻辑 `Run` 可以由多个 `ProcessExecution` 组成；每个 execution 恰绑定一个 profile，run manifest 则在启动前冻结完整 profile graph、每个节点的 profile/principal identity，以及允许的 protocol edge、version、authentication、message schema、capability、budget/egress policy 与 transcript-hash contract。持久化跨 profile handoff 仍只使用 immutable `Artifact`；在线 edge kind 只允许 `LocalLlmRuntime` inference、`EvidenceGateway` retrieval、`ClarificationBroker`、`ExperimentApprovalPolicy`、`RemoteEnv`、`SealedEvaluator` 与 `FixedCommitRunner` control/attestation。`RemoteEnv` 是唯一高频 environment step stream。未知 edge 或 execution 临时增加的 socket/HTTP target 必须 fail closed；session 结束后 request/response digest、snapshot、terminal summary、attestation 和需要留存的 tensor digest 成为 content-addressed artifacts。

## 8.7 `RemoteEnv` wire contract

`RemoteEnv` 是隔离 worker 的版本化 RPC protocol，不等同于 Ray legacy external-env API。它只能运行在 frozen profile graph 明确允许的 `trainer→environment-worker` 或 `sealed-evaluator→sealed-environment-worker` edge；两类 edge 使用不同 principal、grant、session 与 worker，sealed edge 只存在于 evaluator-owned default-deny network namespace且不能连接 generation、trainer 或 public-dev principal。每条 envelope 绑定 run、session、source/target profile/principal identity、protocol version、sequence/step 与 capability。唯一 wire codec 为 `automarkov.remote-env-frame.v1`：8-byte unsigned big-endian `header_length`，随后是该长度的 RFC 8785 JCS header bytes，再随后按 header 中 `tensors` tuple 顺序无间隙拼接 raw tensor bytes；不允许 MessagePack、Arrow IPC、`.npy`、compression、pickle/cloudpickle 或 caller-selected codec。header 是 `extra="forbid"` closed object，固定包含 `codec_version`、message kind、完整 envelope/capability binding、`payload` 和 `tensors`；每个 tensor descriptor 固定为 `tensor_id`、closed dtype literal、shape、offset、nbytes 与 `sha256:<hex>`，`tensor_id` 按 UTF-8 bytes 严格递增且唯一，offset 从 0 连续递推。JCS header 受同一 8 MiB/128-depth/1,000,000-node ingress ceiling；run manifest 与 grant 另冻结 per-message `max_frame_bytes` 和 `max_tensor_bytes`，transport 必须在读取 payload 前验证 header length、总 frame length、每个 offset/nbytes 及 shape×dtype-width 的 checked multiplication 均不溢出且不超过 ceiling，禁止先无界缓冲。tensor 只允许 dense C-contiguous、zero-offset、little-endian `bool|uint8|int8|uint16|int16|uint32|int32|uint64|int64|float16|float32|float64`；多字节 native/big-endian 输入必须在发送端转换，小端接收后按 descriptor 重建，bool 每元素恰一 byte 且只能为 `0|1`，shape 维度使用 safe integer，`nbytes` 必须精确等于 dtype width 与 shape 乘积，stride/view/object/string/ragged dtype、padding、尾随 bytes、重复或未引用 tensor 全部拒绝。所有 tensor NaN 一律拒绝，所有 `-0` bit pattern 在发送前规范化为 `+0`；infinity 只允许出现在 `Box` 的 `low_tensor_id`/`high_tensor_id` bounds tensor，普通 payload/reward/observation/action/state tensor 全部要求 finite。

Gymnasium/PettingZoo space metadata 只能使用同一 JCS header 内的 closed discriminated union：`Discrete(n,start,dtype)`、`Box(shape,dtype,low_tensor_id,high_tensor_id)`、`MultiDiscrete(nvec_tensor_id,start_tensor_id,dtype)`、`MultiBinary(n,dtype="int8")`、`Text(min_length,max_length,charset)`、`FiniteText(values)`、`Tuple(items)` 与 `Dict(entries)`。`Discrete.dtype` 与 `MultiDiscrete.dtype` 必须是上述 closed integer dtype literal并参与 byte identity；`Text.charset` 和每个 `FiniteText.values` item 都是无 lone-surrogate 的 canonical Unicode string，values 按 UTF-8 bytes 严格递增且唯一。固定 MiniGrid Memory suite 的 `mission: MissionSpace` 必须在 worker handshake 中映射为其实际有限 mission 集合的 `FiniteText`，并由 profile-frozen audited adapter ID/source hash证明 `sample`/`contains` 与该集合一致；禁止传递 `mission_func`、Python callable/class/import path或退化为更宽的自由文本空间。`Tuple` 保留位置，`Dict.entries` 是按 UTF-8 key bytes 严格递增且唯一的 `{key,space}` tuple，未知 space kind 或自定义 Python class/import path 一律拒绝。每个 request/response 的 canonical frame bytes 为上述 length prefix、header 和 tensor section 的精确拼接；frame hash 是 `SHA256(ASCII("AutoMarkov-RemoteEnv-Frame-v1\n") || canonical_frame_bytes)`。`Step` transition hash 是 `SHA256(ASCII("AutoMarkov-RemoteEnv-Transition-v1\n") || canonical_request_frame_bytes || canonical_response_frame_bytes)`，覆盖 grant、sequence、step、action、observation、reward、termination、truncation、info 与 AEC fields；双方在执行/提交前后分别重算，run manifest 冻结 codec/schema hash，任何非 canonical framing、descriptor/hash/space mismatch 或同逻辑值的替代编码均 fail closed。

transport 固定使用 TLS 1.3 mutual authentication，禁止在 sidecar/proxy 提前终止 TLS。`FixedCommitRunner` 为每个 run 签发短期 Ed25519 client/server leaf certificate；issuer CA certificate 的 public key、certificate signature algorithm 与 leaf SPKI 都必须是 Ed25519。leaf X.509 v3 profile 完全冻结为：empty subject；frozen runner CA issuer；`BasicConstraints(ca=False)` critical；`KeyUsage` critical 且只有 `digital_signature=True`，其余 usage false、encipher/decipher-only 为 `None`；`ExtendedKeyUsage` non-critical 且 client leaf 恰含 `clientAuth`、server leaf 恰含 `serverAuth`；non-critical SKI 与 AKI；critical SAN；不得出现其他 extension。leaf SKI 的 digest 必须 byte-for-byte 等于 PyCA 49.0.0 `x509.SubjectKeyIdentifier.from_public_key(leaf_public_key).digest`。issuer CA 的 SKI 同样由其 Ed25519 public key 按该函数派生；leaf AKI 必须由 `x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(issuer_ski)` 构造，`key_identifier` 恰等于 frozen issuer SKI digest，`authority_cert_issuer` 与 `authority_cert_serial_number` 必须都是 `None`。certificate signature algorithm 只允许 Ed25519，serial 是 manifest 记录的正 159-bit CSPRNG integer，validity 必须位于 run window 和 issuer validity 交集内。

SAN 恰含一个 `x509.UniformResourceIdentifier` GeneralName，不允许第二个 entry、DNS/IP/RFC822/OtherName 或 wildcard。其 ASCII value 唯一为 `urn:automarkov:remote-env:v1:<identity_b64url>`；`identity_b64url` 是以下 `extra="forbid"` closed object 的 RFC 8785 JCS bytes 的 unpadded base64url：`{"domain":"AutoMarkov-RemoteEnv-Certificate-Identity-v1","experiment_id":...,"run_id":...,"process_execution_id":...,"profile_id":...,"principal_id":...}`。verifier 必须 base64url decode、strict parse、逐字段匹配 frozen profile graph，再重新 JCS encode/base64url encode 并与原 URI byte-for-byte 相等；不得接受 percent-encoding、padding、大小写/Unicode normalization 或别名。trust bundle、certificate DER SHA-256 fingerprint、serial、有效期和 revocation status 写入 frozen profile graph；私钥只通过 `0600` read-only credential file 或 inherited file descriptor 提供，不进入环境变量/artifact/log。握手双方验证 chain、Ed25519 issuer/leaf SPKI、完整 leaf profile、derived SKI/AKI、唯一 SAN identity、profile lock/image digest，并各生成 32-byte CSPRNG nonce，以 unpadded base64url 编码。multiple/extra/wrong-type SAN、URI/JCS/base64 non-canonical、wrong EKU、CA leaf、missing/extra KeyUsage bit、mutated/foreign SKI、AKI mismatch 或 nonempty AKI issuer/serial、non-Ed25519 leaf SPKI、unknown extension 或 identity/profile mismatch 均在建立 session 前 fail closed。

`session_id` 的唯一算法是 `SHA256(rfc8785.dumps(RemoteEnvSessionTranscript))`，其中 transcript 是 `extra="forbid"` 的 closed object：`{"domain":"AutoMarkov-RemoteEnv-Session-v1","protocol_version":"automarkov.remote-env.v1","experiment_id":...,"run_id":...,"profile_graph_hash":"sha256:...","client":{"process_execution_id":...,"profile_id":...,"principal_id":...,"certificate_fingerprint":"sha256:...","nonce_b64url":...},"server":{同一固定 keyset}}`。JCS 决定 client/server object property ordering；role 也由显式 key framing，禁止数组位置猜测、裸 `||` 拼接、可变长/空 nonce、fingerprint set 或 caller 提供 session ID。双方独立重建该 object/bytes/hash并比较，任一字节或 identity 不一致即中止。

runner 另以冻结 Ed25519 key 签发 `extra="forbid"`、strict/frozen 的 closed `RemoteEnvCapabilityGrant`。其完整字段集只能是：

- `schema_version="automarkov.remote-env-capability.v1"`、`signing_domain="AutoMarkov-RemoteEnv-Capability-v1"`；
- `grant_id`、`experiment_id`、`run_id`、`session_id`、`profile_graph_hash`；
- `source_process_execution_id`、`source_profile_id`、`source_principal_id`、`target_process_execution_id`、`target_profile_id`；
- `environment_id`、`role`（closed literal `actor|critic|evaluator`）；
- `allowed_methods`（按 protocol literal 排序且去重的 immutable tuple）、`max_sequence`、`max_step`、`max_frame_bytes`、`max_tensor_bytes`；
- RFC 3339 UTC 的 `not_before`、`expires_at`，32-byte CSPRNG 的 unpadded `nonce_b64url`；
- `signing_key_id`、`signature_b64url`。

签名 preimage 是从完整 grant 移除且仅移除 `signature_b64url` 后得到的 RFC 8785 JCS bytes；所有剩余字段都受 Ed25519 signature 覆盖。run manifest 冻结 runner public key、key validity/revocation status、clock-skew ceiling、method-order validator 与 grant schema hash。partial-field signature、未知/缺失字段、非 canonical method tuple、空/错误长度 nonce、`not_before >= expires_at`、超出 run window、非正上限或 signature/key mismatch 全部 fail closed。actor grant 的 schema validator 永远拒绝 `State`/`Snapshot`；critic/evaluator grant 不复用 actor connection。每个 request 都携带完整 grant 与严格递增 sequence；worker 在执行前验证 mTLS peer 与 `source_principal_id` 一致、source/target execution/profile、session/environment、signature/key status、method、time window、sequence/step ceiling，并原子记录已消费 `(grant_id, sequence)`。duplicate/out-of-order/replayed request、certificate/grant/session/profile mismatch、未知 method 或 actor 调用 `State` 均 fail closed、撤销 session 且不得执行 action。timeout 后同样撤销 session并重新 `Reset`，不能重放原 action。

握手必须返回：

```yaml
protocol_version: "automarkov.remote-env.v1"
run_id: "..."
session_id: "sha256:..."
process_execution_id: "..."
profile_id: "..."
principal_id: "..."
profile_lock_hash: "sha256:..."
image_digest: "sha256:..."
peer_certificate_fingerprints:
  client: "sha256:..."
  server: "sha256:..."
profile_graph_hash: "sha256:..."
environment_id: "..."
environment_repository_commit: "40-hex-sha"
observation_spaces: {}
action_spaces: {}
supports_parallel: true
supports_aec: false
supports_state: true
seed_contract: "..."
```

protocol methods 只有 `Describe`、`Reset(seed, options)`、`Step(step_id, action_or_joint_action)`、`Observe(agent_id)`（AEC only）、`State`、`Snapshot`（suite 明确允许时）、`Close`。每个 `Step` response 必含 per-agent observations、rewards、terminations、truncations、sanitized infos、active AEC agent/cycle index 和 transition hash。`State`/`Snapshot` authorization 只来自已认证 `RemoteEnvCapabilityGrant`，不得接受 caller 自报 capability 字符串。step ID 必须严格单调并与 request sequence 原子提交。

`RemoteEnv` contract tests 覆盖全部 space branch 的 byte-identical round-trip、`Discrete.dtype`、MiniGrid `MissionSpace→FiniteText` exact-set adapter、dtype/shape/bounds、seed 重现、AEC turn order、dead-agent semantics、Parallel cycle semantics、随机奖励分布、history reward/message visibility、large tensor transport、worker crash、timeout、duplicate step 和 close idempotency；安全负例覆盖 unknown/unmanifested edge、trainer/sealed principal 交叉、bad CA/SAN/fingerprint、expired/revoked certificate/grant、principal/profile/session mismatch、grant method escalation、actor `State`、duplicate/out-of-order sequence、跨 session replay、timeout 后旧 action 与 transcript gap。

## 8.8 CPU-first 与远程 fixed-commit runner

默认资源计划为：core/schema/retrieval cache/compile/static checks/属性测试/toy rollouts/统计聚合在 CPU；vLLM 使用 GPU；RL 只有 gold pilot 证明 CPU 不满足预注册 wall-time 时才给 learner 分配 GPU。环境 worker 尽量保持 CPU，避免 vLLM 与 RL/env 争用 GPU。每项 GPU 分配写入 compute manifest 和成本指标，`CUDA_VISIBLE_DEVICES`/Ray resource labels 由 runner 设置，agent 不自行选卡。

publication-grade remote run 只能由 `FixedCommitRunner` 执行，job 必含不可变 40-hex Git commit、repository URL、profile image digest、input artifact IDs、suite/variant/pair/seed、phase、单一命令、资源限制、output schema 和 network-policy hash。runner 必须：

1. 在临时 clean checkout 中 fetch 所需 object 并 detached checkout exact commit；
2. 验证 `git rev-parse HEAD` 等于请求 SHA、worktree clean、submodule/repository manifests 全部是固定 commit；
3. 验证 container/profile digest 和输入 artifact hashes；
4. 采用 default-deny egress，只挂载只读 inputs 与独立 writable output；generation 的 retrieval phase 仅允许 `EvidenceGateway` principal 通过 TLS 访问 `api.tavily.com:443`，拒绝 IP literal、其他域名和 redirect egress，本地 vLLM 只走 loopback/Unix socket；evidence snapshot 冻结后立即撤销该 egress，其他 generation phase、training、sealed evaluation 与 analysis 全程无外网；
5. 执行单一授权命令并记录 stdout/stderr hash、exit code、资源/时钟、host capability 和 seeds；
6. 只回传通过 output schema 和 secret/gold scan 的 content-addressed outputs；
7. 在本 `ProcessExecution` 的 payload outputs 完成 schema/secret/gold scan并取得 content hashes 后，先生成唯一 immutable `ProcessExecutionTerminalRecord`，再生成 runner-signed `ExecutionAttestation`；attestation 单向引用该 process terminal record 与 output hashes，并包含 network-policy hash、只含 identity/capability 且不含 secret locator/value 的 mount-table hash、capability-decision log hash、实际 phase transition、非敏感 egress decision-log hash与撤销时间。若且仅若该 execution 同时完成 Run terminal CAS，attestation 才额外绑定 terminal-result ID/hash；其他 execution 的 terminal-result pair 必须为 null。`TerminalResult` 只绑定启动前已存在的 fixed-commit job manifest/process execution identity，禁止反向引用本 attestation。

branch、tag、`main`、dirty patch、未锁定 wheel、远程已有工作目录或“最新 commit”都不是合法 job identity。若 commit/profile/input 任一不匹配，job 在执行前失败；不能现场修改再续跑。

---

# 9. 六个结构化 core suites

## 9.1 统一实验协议

六个 suite 固定为 `taxi_mdp`、`memory_pomdp`、`mpe2_full_state_mg`、`smacv2_posg`、`metadrive_pomdp`、`citylearn_posg`。每个 suite 包含：

```text
Natural-language task card
Allowed evidence manifest
Blocked gold-source manifest
Gold TaskContract
Gold DecisionProcessSpec
Reference environment
Hidden behavioral tests
RLlib reference configuration
Evaluation protocol
Five registered task-card variants
Generation pairing manifest
Sealed-evaluator manifest
```

每个 suite 恰好有五个语义目标相同、表达难度不同的 task-card variants；两名领域审查者必须先确认它们映射到同一个 gold contract/spec，差异只能来自信息呈现：

| Variant | 规范内容 |
|---|---|
| `v1_canonical` | 简洁、直接、字段顺序与 domain contract 对齐的标准描述 |
| `v2_paraphrased` | 等价改写、术语同义替换，不改变任何规则或数值 |
| `v3_reordered_longform` | 将目标、约束、动态和 episode 边界打散到长文本中，加入无害背景信息 |
| `v4_evidence_split` | 核心规则分散在 task card 与 Allowed Evidence 文档，要求建立 claim–evidence mapping |
| `v5_clarification_required` | 删除最多三个高影响语义点；缺口及 oracle answer 在 oracle-owned sealed manifest 中预注册，测试 abstain/HITL，不允许自由猜测 |

若 variant review 发现其语义目标改变，必须修订 task card，不能新增 gold tolerance 来掩盖。variant 文本、排序、evidence manifest 和 oracle answers 在首个 generation run 前冻结。runner-visible policy manifest 保存 task card、Allowed/Blocked Evidence，以及 sealed gold、clarification oracle、hidden evaluation 工件各自 typed `sealed-v1` nonce-backed commitment；runner 再按 method manifest 构造更窄的 agent-visible view。`automarkov_no_evidence` 的 view 恰好只含 task card 与不透明 sealed commitments，Allowed/Blocked Evidence manifest、source metadata、evidence handle、snippet 和 retrieval cache 均不进入该 principal；其他方法只获得冻结 capability 允许的 evidence view。任何 view 不得含原始/opaque artifact ID、oracle answer、gold 内容、hidden evaluator location 或可解析出 sealed 内容的路径。commitment 使用第 16 节定义的 closed `SealedCommitmentPreimage` JCS 与高熵 sealed nonce，nonce 仅归 sealed evaluator/oracle principal 所有，避免从低熵 answer space 枚举内容。oracle answer set 只存在于 oracle-owned sealed root，generation job 从不挂载该 root。`HITL-ORACLE` coordinator 通过受限 Unix broker capability 逐问返回预算内当前 answer payload，不返回任何 artifact metadata/identity/content hash/nonce/locator；`AUTO` job 不获得该 capability。broker transcript、mount table 与 egress attestation 必须进入 append-only audit trail。

`v5_clarification_required` 在 `AUTO` 轨刻意缺少完成形式化所需的信息，因此不允许将猜测后产出的环境与正确 abstention 放进同一 E2E estimand。该 cell 的唯一主行为是 typed clarification-required result；`SafeClarificationRequired=1` 当且仅当第 7.6 节独立 post-terminal sealed clarification request/verdict chain 证明方法逐项指出所有预注册高影响缺口、不猜测、不产生 formal/environment artifact且不访问 sealed oracle。合法 false 或任何 generation/evaluation missing、timeout、integrity、contamination、protocol failure 都保留 slot 并映射为 0；request/verdict绝不回流 generation，也不改写 `CLARIFICATION_REQUIRED` terminal snapshot。它是独立 secondary outcome。`HITL-ORACLE/v5` 由固定 oracle 补全相同缺口后，才进入四 gate、RL training 和 policy evaluation。

所有方法使用：

- 相同任务描述；
- 相同本地 Qwen3.6-35B-A3B model/tokenizer/vLLM manifest；
- 相同温度与最大输出长度；
- 相同搜索次数和网页抽取预算；
- 相同代码执行次数；
- 相同 HITL 预算；
- 相同 RLlib 训练预算；
- 相同 gold tests。

### 9.1.1 冻结 source-access mode 与 implementation route

`source_access_mode` 描述 generation principal 能否读取/实例化官方环境；`required_implementation_route` 描述所有方法必须交付的实现形态。两者是 suite contract 的冻结字段，不是运行后由方法选择的协变量，也不增加主矩阵维度。每个 `(suite, variant, track, method, pair)` cell 都从同一 signed suite manifest 解析唯一值；mode/route 缺失、方法间不一致或运行时越权都构成 protocol deviation 并在生成前 fail closed。

| Suite ID | `source_access_mode` | `required_implementation_route` | 冻结理由 |
|---|---|---|---|
| `taxi_mdp` | `SYNTHESIS` | `GENERATE` | 唯一从规则合成完整有限环境的 core cell；官方 Taxi 只在 sealed evaluator 做枚举 gold |
| `memory_pomdp` | `REUSE` | `COMPOSE` | 复用 MiniGrid 内核，组合 task config、观测/history wrapper 与 RLlib adapter |
| `mpe2_full_state_mg` | `REUSE` | `COMPOSE` | 复用 MPE2 physics/reward，组合 full-state actor/critic capability wrapper |
| `smacv2_posg` | `REUSE` | `COMPOSE` | 复用官方 battle core，组合 action-mask、decentralized actor 与 remote adapter |
| `metadrive_pomdp` | `REUSE` | `COMPOSE` | 复用 MetaDrive/ScenarioEnv，不重新实现物理或道路引擎 |
| `citylearn_posg` | `REUSE` | `COMPOSE` | 复用 CityLearn dynamics/schema，组合 POSG observation/reward 与 modern-RLlib adapter |

`SYNTHESIS` cell 的 Allowed Evidence 可含公开规则/API 合同，禁止环境源码、完整 transition table 或 official env import；其 `GENERATE` 是针对 Taxi 的预注册 benchmark 例外。`REUSE/COMPOSE` cell 必须使用冻结 upstream commit/package，禁止复制领域内核。MPE2 native-local information-structure ablation 继承 `REUSE/COMPOSE`。

完整 intention-to-run ledger 包含每个 `(suite, variant, track, method)` cell 的 `n_pair` 个 `pair_id=g00..g{n_pair-1}` slots；`n_pair` 由 11.9 的 pre-generation `DesignPowerManifest` 在候选集合中冻结，suite identity 唯一解析到上一节冻结的 access mode/route，因此二者都不另乘实验维度。method manifest 在 preflight 前把每个 cell 冻结为 `RUN` 或有 reason/evidence 的 `N/A`；每个 `RUN` cell 恰好执行 `n_pair` 次**配对生成**，`N/A` cell 不启动生成且不计作失败。`pair_id` 同时决定 generation seed、task-card bytes、evidence snapshot、source-access manifest hash、required route、oracle-answer typed `sealed-v1` commitment、tool/repair budget 和初始顺序；在共同 eligible methods 间共享同一 `pair_id`，但 generation-visible pairing manifest 不含原始/opaque artifact ID、answer 内容或 sealed nonce。`HITL-ORACLE` 的 answer 仅由 sealed broker 在方法实际提问后按预算释放，`AUTO` 不释放；任何方法不得共享另一方法的 answer transcript、输出或缓存内容。provider-neutral public document cache 可共享 bytes，method-local retrieval ranking/trace 不共享。报告必须分列 intention-to-run、RUN、N/A 与实际 attempt counts，禁止把完整 slot grid 误称为全部已执行。

每个通过端到端生成 gate 的 candidate environment 恰好训练 10 个 RL seeds，`r00..r09` 在方法间、pair 间以同一预注册表配对。对 E2E outcome mask 内的 cells，无效 candidate 保留为 generation failure，不训练 RL，也不从总体样本移除；统计模型显式表示其 RL outcome missing-by-design。`AUTO/v5` 由设计排除 E2E/RL outcome，仅评价 `SafeClarificationRequired`，不能标作 generation failure。不得用“资源受限至少 5 seeds”替代该合同。

标准 artifact key 为：

```text
core/<suite_id>/<variant_id>/<track>/<method_id>/<pair_id>/<rl_seed_or_generation>/
```

实验设置两条轨道：

- `AUTO`：不使用人类澄清，是唯一 confirmatory 主轨；
- `HITL-ORACLE`：由基于 gold spec 的固定 clarification oracle 回答问题，最多三轮，每轮最多三个问题，是预注册 secondary/mechanism 轨。

两轨完整运行、独立估计且禁止 pooling。只在 outcome 定义一致的 `v1..v4` 上，分别对 $Y\in\{\mathrm{E2EValid},Q^{\mathrm{gate}}\}$ 计算同一 $(s,v,p)$ 上的 paired difference-in-differences：$D_Y=(Y^{\mathrm{AutoMarkov}}_{HITL}-Y^{\mathrm{AutoMarkov}}_{AUTO})-(Y^{\mathrm{ReAct}}_{HITL}-Y^{\mathrm{ReAct}}_{AUTO})$。两项 interaction 都固定同一 24 cells，只在 cell 内配对重采样 generation pair/RL seed，报告两侧 95% stratified-bootstrap CI，并作为一个二假设 secondary family 使用 Holm correction；它们不能改变 co-primary gates。`v5` 分别报告 `AUTO` 的 safe clarification 和 `HITL-ORACLE` 的 E2E/policy outcomes，不跨不同 outcome 构造 interaction。

两轨的 text/formal exact-ID approval 都由同一 frozen `experiment_approval_policy` principal 完成；其 source hash、public acceptance predicates、输入 allowlist 和 signing key identity 在 preflight 前登记。clarification oracle 只补齐 `HITL-ORACLE` 实际提出且未超预算的问题，不参与批准、拒绝或 artifact identity 交换。这样 `AUTO/v1..v4` 与两条轨的完整语义 cells 可机械越过 confirmation transition，同时不引入临时人类或 sealed-derived approval signal。

## 9.2 实验一：MDP——Gymnasium Taxi

| 项目 | 设定 |
|---|---|
| 环境 | `Taxi-v4` |
| 对象 | 有限离散 MDP |
| 特征 | 500 个状态、6 个动作、明确奖励与终止规则 |
| 输入 | 官方规则转换而来的自然语言 task card |
| 主矩阵 access/route | `SYNTHESIS` / `GENERATE`；禁止读取环境源码、完整转移表或实例化 Gymnasium 官方环境 |
| official gold | Taxi-v4 仅由 sealed evaluator 用于枚举差分与 policy evaluation，不进入 generation profile |
| 输出 | MDP spec、Gymnasium environment/adapter、RLlib config |
| RLlib | PPO；DQN 作为离散任务 sanity check |
| 强验证 | 对可达状态—动作的转移、奖励和终止进行精确比较 |
| 策略指标 | return、成功率、步数和归一化策略分数 |

Taxi 适合作为具有枚举 oracle 的 MDP 测试。核心行为测试包括：

- 非法接客或放客动作的奖励是否正确；
- 到达目标并成功放客是否终止；
- 墙体是否阻止出租车穿越；
- 状态编码与解码是否互逆；
- 固定 seed 是否产生一致轨迹。

## 9.3 实验二：POMDP——MiniGrid Memory

| 项目 | 设定 |
|---|---|
| 环境 | `MiniGrid-MemoryS17Random-v0` |
| 对象 | POMDP |
| 核心机制 | 智能体先观察线索，后续必须依赖记忆选择正确方向 |
| 输入 | 官方任务描述，不向 Synthesis mode 开放源码 |
| 输出 | latent state、observation function、memory requirement、Gymnasium adapter |
| RLlib 主方法 | Recurrent PPO |
| 对照 | Feed-forward PPO |
| 指标 | success rate、return、path efficiency、recurrent gain |

关键行为测试：

- 当前局部观测相同但历史线索不同的两个状态应允许不同最优动作；
- 观测中不得泄漏正确出口标签。

`删除早期线索后的成功率变化` 与 `recurrent gain` 是预注册的策略结果/诊断指标，用相同环境、训练预算和 paired seeds 比较并报告 CI；它们不得作为环境 behavioral validation gate。有限 seeds 下优化失败或超参数敏感性不能证明环境实现错误。环境门禁只检查可由 reference trajectory、信息结构和 observation non-leakage 确定的性质。

## 9.4 实验三：MG——MPE2 Simple Spread

| 项目 | 设定 |
|---|---|
| 环境 | `mpe2.simple_spread_v3` |
| 对象 | 合作型 MG |
| 主体 | 三个同构 agent |
| 目标 | 覆盖 landmarks，同时降低碰撞 |
| 关键处理 | 使用官方 `state()` 构造 full-state actor observation，形成明确 MG adaptation |
| 接口 | PettingZoo Parallel API |
| RLlib | 信息结构主估计使用冻结的 shared recurrent centralized-value PPO；feed-forward shared PPO 与 Independent PPO 仅作诊断，均使用 RLModule 新栈 |
| 指标 | 团队回报、覆盖误差、碰撞率、公平性、sample efficiency |

### 9.4.1 主轨：full-state MG adaptation

主轨必须从锁定 MPE2 `simple_spread_v3` worker 的官方 `state()` 获取全局 Markov state，不得把各 agent native observations 简单拼接后声称等价。对 $N=3$，profile handshake 应验证官方文档所示 state shape，并以 runtime space 为权威；adapter 为每个 actor 输出 `(global_state, agent_identity_or_role)`，使每个独立决策主体在执行时都获得 Markov 充分信息。状态更新、物理、联合动作、global/local reward 混合和终止规则均复用官方环境，不改写动态。

这一 adaptation 的方法名必须包含 `mpe2_full_state_mg`，论文中说明它不是 MPE2 native information structure。centralized-value PPO 的 critic 可以复用 global state，但 actor 已经有相同环境状态，因此该主轨不用于证明 partial observability 性能。

### 9.4.2 预注册消融：native local-observation POSG

`mpe2_native_local_posg` 是同一 suite 内的 ablation，不是第七个 suite。它保留 MPE2 native per-agent observation，actor 不得调用 `state()`、读取其他 agent raw observation 或访问 centralized fields；仅 centralized critic 在训练时通过 capability 读取 global state。该 condition 与 full-state condition 都使用同一 frozen recurrent actor 和 centralized critic，分类为合作型 POSG。

主轨与消融必须共享 environment commit、$N$、`local_ratio`、episode length、物理 seeds、joint actions（行为测试时）、reward implementation，以及同一个 signed `Mpe2InformationPolicyConfig`。该 config 冻结完全相同的 RLModule class/source hash、recurrent actor/centralized-critic wiring、hidden size、parameter shapes、initialization、optimizer、normalization、training/evaluation budgets、ConnectorV2 graph 和全部超参数；两 condition 都把 actor input 编码为同一固定维度，native adapter 只把未授权 global-state positions 置零并提供两边相同形状的 frozen feature-availability mask，critic 在两边读取相同 global state。唯一 treatment diff 是 manifest 绑定的 actor-input capability/adapter：full 可读取官方 `state()`，native 只能读取本 agent 的 18D local observation；module class、memory、critic、训练图或预算不得随 condition 改变。feed-forward、independent 或其他算法结果只能作为分列诊断，不进入 $Q^{\mathrm{info}}_{gate}$。因此 native-minus-full 的预注册 estimand只解释为 actor information structure effect。

该 information-structure ablation 使用独立预注册 ledger：full condition 复用主矩阵 `AUTO/automarkov/v1..v4/g00..g{n_pair-1}` 的 `4 × n_pair` 个 terminal slots，native condition 增加相同 pair mask 的 `4 × n_pair` 个 slots，生成侧只获得 result-free pair binding。双方 terminal 后，只有无 generation capability 的 coordinator/analysis principal 可签发 `extra="forbid"` 的 `Mpe2InformationStructureBinding`。closed fields 恰为：schema/signing domain、binding/experiment IDs、`suite_id="mpe2_full_state_mg"`、variant、`track="AUTO"`、`method_id="automarkov"`、pair/pair-binding IDs与commitment、full/native condition IDs、两份 run-manifest IDs/hashes、terminal-result IDs/hashes、execution-attestation IDs/hashes、condition-specific calibration commitments、`common_policy_scale_calibration_commitment`（精确等于 full-state commitment）、issued-at、32-byte unpadded-base64url nonce、`signature_algorithm="Ed25519"`、analysis key ID与signature；所有引用工件均是 exact direct parents，且 binding 不含任一 condition payload。Ed25519 signature 覆盖从完整 object 移除且仅移除 signature 后的 RFC 8785 JCS bytes。preflight 冻结 analysis Ed25519 public key、validity/revocation与clock contract；binding ID、nonce及 `(experiment,variant,pair)` tuple进入 replay/uniqueness index。错误/未知 algorithm、错误 condition、非 terminal run、attestation/pair/calibration/common-scale mismatch、重复绑定、unknown/revoked key或任一字段替换全部 fail closed。binding、任一 run identity/result或cache不得回流至 generation job。

full/native 分别使用与自身 actor information capability 相容、且在 run 前通过 11.7 相同门禁的 condition-specific `GoldScoreCalibration`；这两份 calibration 只判断各 condition 是否具备有效 evaluator/reference，不提供跨 condition 的两个不同尺度。信息结构 policy estimand 使用同一 full-state calibration 作为冻结公共仿射尺度：对两种 condition 都计算 $Q^{\mathrm{info}}_{gate}=\mathrm{E2EValid}\,\mathrm{GoldPolicyEvaluationValid}\,d[J(\pi,E^\star)-J(\pi_{random,full},E^\star)]/[J(\pi_{reference,full},E^\star)-J(\pi_{random,full},E^\star)]$；metric direction、gold reward/environment、scenario/evaluation count、random/reference returns 和 denominator 完全相同。native calibration 的 denominator 绝不用于重缩放 native outcome，condition-specific normalized score 只作 condition 内诊断。两个预注册 secondary hypotheses是 native-minus-full 的 `E2EValid` 与 $Q^{\mathrm{info}}_{gate}$，使用四个固定 variant strata、每层同一冻结 `n_pair` 个共同 pair及适用时十个共同 seed indices的 two-sided 100,000-replicate paired counter-stream test/CI，并组成 11.10.5 登记的独立两假设 Holm family。

关键测试：

- agent permutation symmetry；
- landmark permutation symmetry；
- 增加碰撞应使相应奖励单调下降；
- 联合动作变化应影响下一状态；
- 参数共享策略在交换 agent ID 后保持等价行为；
- full-state wrapper 与官方 `state()` 逐元素一致且不会泄漏未来信息；
- native ablation 的 actor export graph 不含 `state()` capability；
- 对同一 seed/joint-action trajectory，两轨的物理状态与 rewards 完全相同。

## 9.5 实验四：POSG——SMACv2

| 项目 | 设定 |
|---|---|
| 环境 | SMACv2 `protoss_5_vs_5` |
| 对象 | 合作型 POSG |
| 信息结构 | actor 使用局部观测，global state 仅供 centralized critic |
| 特征 | 单位类型、初始位置与作战配置具有程序化变化 |
| 接口 | SMACv2 adapter → PettingZoo/RLlib multi-agent interface |
| RLlib | Recurrent CTDE-PPO |
| 对照 | Independent recurrent PPO |
| 指标 | win rate、return、sample efficiency、程序化配置泛化 |

关键测试：

- actor 输入中不得出现未授权 global state；
- 不可执行动作必须被 action mask 屏蔽；
- 死亡 agent 的观测、动作和 mask 正确处理；
- 不同程序化配置之间使用严格 train/test split；
- critic 可以读取全局信息，但 actor 执行图不得依赖该输入。

## 9.6 实验五：真实复杂场景——MetaDrive + ScenarioNet

| 项目 | 设定 |
|---|---|
| 环境 | MetaDrive `ScenarioEnv` |
| 数据 | 官方 mini Waymo/nuScenes 场景；扩展实验按原数据许可获取完整数据 |
| 对象 | 基于传感器观测的单智能体 POMDP |
| 输入 | 驾驶任务、传感器、道路约束、成功和失败条件的自然语言描述 |
| 输出 | ScenarioNet 数据配置、观测 wrapper、奖励、终止和 RLlib adapter |
| RLlib | Recurrent PPO |
| 训练 | 程序化训练场景和训练 scenario IDs |
| 测试 | 完全不重叠的真实 scenario IDs |
| 指标 | route completion、success、collision、off-road、return |

必须遵守：

- 不生成或改写 MetaDrive 的物理引擎；
- replay traffic 默认作为环境过程，不自动视为可训练主体；
- 奖励项必须映射至任务合同中的安全、效率和舒适性要求；
- 训练和测试场景按 scenario ID 去重；
- 真实数据许可证和下载条件写入 manifest。

## 9.7 实验六：真实复杂场景——CityLearn

| 项目 | 设定 |
|---|---|
| 环境 | CityLearn |
| schema | 锁定实际安装版本中的官方 challenge schema |
| 数据 | 建筑负荷、天气、光伏、价格、碳强度与储能时序 |
| 对象 | 多建筑局部观测下的合作型 POSG |
| 接口 | CityLearn Gymnasium API → PettingZoo/RLlib adapter |
| RLlib | Recurrent CTDE-PPO；Independent PPO 对照 |
| 指标 | net electricity、peak demand、ramping、cost、carbon、comfort |
| 划分 | 官方 challenge split；若无公开 split，则采用预注册时间顺序 split |

关键测试：

- 电池能量守恒；
- state of charge 边界；
- 充放电功率限制；
- 建筑观测不得泄漏未来天气、负荷、价格或碳强度；
- 成本和碳排指标与官方实现一致；
- 多建筑协调收益必须在统一测试时段评价。

---

# 10. A-LAMP、Agent² 与 Agent2World 的主实验复现

六项核心实验用于证明统一能力；三篇相关论文的主实验分别设置独立 replication suite，避免把所有环境混入无结构的大型实验列表。

本项目的 local-model policy 对 replication suites 同样生效：实际生成只能使用自托管 Qwen3.6-35B-A3B vLLM。论文原始模型、provider、prompt 和 sampling config 作为 provenance metadata 保存；若原论文不是同一模型，则执行结果必须标记 `model-adapted paper-spec replication`，不能称 fully paper-matched。禁止为了忠实模型配置调用 hosted API。

## 10.1 A-LAMP replication suite

A-LAMP 使用多个专用智能体完成参数、目标、变量、约束、数学形式化、状态—动作—奖励和代码生成，并使用代码执行反馈进行错误修复。

主实验覆盖：

1. CartPole；
2. MountainCar；
3. Wireless Network Scheduling；
4. $50\times 50$ Drone Delivery；
5. Inventory Management with Poisson demand。

论文复现需要保留：

- DQN 策略训练设定；
- modeling success；
- coding success；
- policy generation success；
- 每项任务的多次独立运行；
- error correction 与 lightweight variant 消融。

方法列至少包括：

- A-LAMP；
- A-LAMP without error correction；
- Light A-LAMP；
- single-agent direct generation；
- 论文模型配置仅作为对照 metadata；实际各列统一运行本地 Qwen3.6。

若没有可确认的官方代码仓库，则必须标记为：

> **paper-spec reimplementation**

并保存完整 deviation log。论文未明确的参数不得自行补全，应保持符号化、联系作者或在复现报告中明确声明替代设定。

## 10.2 Agent² replication suite

Agent² 采用 Generator Agent 和 Target Agent，包含：

- Task-to-MDP Automation；
- Algorithmic Optimization；
- 基于训练结果的自适应反馈；
- 环境适配、算法选择、网络设计和超参数调整。

主实验结构化为：

| 类别 | 任务 | 训练步数 | 评估 | 优化迭代 |
|---|---|---:|---:|---:|
| MuJoCo | Ant、Humanoid、Hopper、Walker2d | 每项约 $10^6$ environment steps | 50 episodes | 5 |
| 自动驾驶 | MetaDrive | 约 $4\times 10^5$ environment steps | 50 episodes | 5 |
| MPE | Simple Spread、Simple Reference | 每项约 $10^6$ environment steps | 50 episodes | 5 |
| SMAC | 8m | 约 $4\times 10^5$ environment steps | 50 battles | 3 |
| SMAC | 2s3z | 约 $10^6$ environment steps | 50 battles | 3 |
| SMAC | 1c3s5z | 约 $2\times 10^6$ environment steps | 50 battles | 3 |

复现分为：

### Paper-matched track

- 尽可能保持论文任务、算法、评估 episode 和迭代次数；模型因 local-only policy 固定 Qwen3.6，故必要时标记 model-adapted；
- PPO、SAC、TD3 和多智能体 PPO 通过锁定版本的 RLlib 实现；
- 输出与论文主表一致的 return 或 win-rate 统计；
- 所有未公开配置进入 deviation log。

### Common-backend track

- 所有方法使用相同 RLlib 版本；
- 相同模型、搜索和训练预算；
- Agent² 只能修改 RLlib 配置和 RLModule；
- 在六项核心任务上与 AutoMarkov 进行受控比较。

如果未发现可确认的作者官方代码仓库，同样标记为 `paper-spec reimplementation`。

## 10.3 Agent2World replication suite

Agent2World 的核心架构包括：

- Deep Researcher；
- Model Developer；
- Unit Tester；
- Simulation Tester；
- 自适应反馈；
- 经验证轨迹生成；
- 基于轨迹的监督微调（论文能力；当前延期）。

主实验覆盖：

| Benchmark | 内容 | 规模 |
|---|---|---|
| Text2World | 自然语言到 PDDL symbolic world model | 按论文及锁定数据版本统计 |
| CWMB | Python code world models | 18 个环境 |
| ByteSized32 | 科学与常识推理 text games | 32 个环境 |
| SFT data | 经双测试器验证的多轮轨迹 manifest/lineage | 当前只做 schema 与许可允许的小规模 audit，不训练 |

当前可执行的 inference-time 评估方法：

- Direct；
- Single Agent；
- Text2World；
- WorldCoder；
- GIF-MCTS；
- Best-of-$N$；
- Self-Consistency；
- Multi-Agent。

`SFT` 单独列为 `DEFERRED`，不得混入当前方法主表、completion percentage 或 release gate。若论文提供的官方 SFT checkpoint 许可允许，可另表报告 checkpoint evaluation，但这不等于本项目执行了 SFT。

复现时必须固定：

- 论文版本；
- 官方仓库 commit；
- benchmark 数据 commit 或 dataset revision；
- 模型 ID；
- 温度、`top_p`、最大 ReAct 步数；
- refinement turn；
- 解析器与测试器版本。

官方 `DeepExperience/agent2world` 当前采用 `RESEARCH / EVALUATION ONLY` license：只允许非商业研究评估和论文复现，禁止商业使用、分发、sublicense/sale、derivative works 分发和 hosted service。因此：

- 仓库只能按固定 commit 位于 ignored `replication-agent2world-restricted` cache；
- 不得 vendoring、复制代码片段、应用需要发布的 patch，或把它写进 AutoMarkov container/release；
- 仅能通过隔离进程和中性的 file/RPC schema 读取许可允许的评估结果；
- 公开 artifact 只含 commit、license hash、命令 manifest、聚合结果和 deviation log，不含受限源码或生成的 derivative bundle；
- 任何超出非商业 research/evaluation 的运行必须先取得权利人书面许可。

若论文主文样本数与当前 Hugging Face 数据卡显示数量不一致，必须按锁定版本报告实际样本数，不能混合不同版本。

### 与 AutoMarkov 的关系

- Text2World/PDDL 标记为 OOD，不扩展为第五类核心数学对象；
- AutoMarkov 的 OOD backend 使用 Unified Planning 进行 PDDL parse、dump、transform 和 planner invocation；
- Agent2World inference-time 轨是 model/tool-adapted controlled evaluation：保留许可允许的论文任务、测试协议与评测设置，生成模型固定为本地 Qwen3.6，检索固定为 Tavily；不得称 faithful evaluation，SFT 当前延期；
- AutoMarkov 受控对比中，统一使用 Tavily，并标记为 `controlled adaptation`；
- AutoMarkov 额外输出 `DecisionProcessSpec`、RLlib adapter 和策略学习结果。

---

# 11. 基线、指标、统计检验与消融

## 11.1 核心受控基线

六项核心实验保留：

1. Single LLM direct generation；
2. ReAct + code executor；
3. A-LAMP paper-spec reimplementation；
4. Agent² paper-spec reimplementation；
5. Agent2World-inspired controlled variant（clean implementation contract，不复制受限源码；不得标成官方复现）；
6. AutoMarkov；
7. Expert-authored gold environment，作为上界，不参与自动化成功率比较。

`paper-spec reimplementation` 表示只依据论文公开描述独立实现方法合同，冻结核心智能体关系和反馈逻辑，并增加公共输出 schema 与 Gymnasium/PettingZoo/RLlib adapter；它不声称存在或复用了官方源码。Agent2World 的 upstream code 不执行 port；核心对比使用公开论文定义的 clean controlled variant，并显式与 isolated upstream research evaluation 分开。原方法不支持的数学对象标记为 `N/A`，不能直接计为失败。

论文忠实复现与跨方法受控比较必须分开报告。

## 11.2 端到端有效率

$$
\mathrm{E2EValid}
=
\mathbb{I}\left[
G_{\mathrm{text}}
\land
G_{\mathrm{formal}}
\land
G_{\mathrm{API}}
\land
G_{\mathrm{behavior}}
\right].
$$

该指标要求：

- 文字任务合同通过；
- 数学规范通过；
- 环境 API 通过；
- 隐藏行为测试通过。

仅能 import 或完成训练不能视为端到端有效。

## 11.3 文字语义指标

- 必需字段覆盖率；
- critical ambiguity 数量；
- 无证据事实比例；
- 用户确认轮次；
- 人类专家一致性；
- 任务合同与 gold contract 的字段级 F1；
- 假设正确率与假设拒绝率。

## 11.4 数学正确性指标

- object classification accuracy；
- symbol closure；
- type/unit consistency；
- probability validity；
- transition totality；
- Markov sufficiency；
- observation leakage；
- reward–objective alignment；
- terminal/truncation correctness；
- solution-concept correctness。

## 11.5 代码与测试指标

- import success；
- Gymnasium/PettingZoo API pass；
- property-test pass；
- metamorphic-test pass；
- differential-test pass；
- mutation score；
- seed reproducibility；
- sandbox violation count。

## 11.6 行为保真度

确定性环境可定义：

$$
F_{\mathrm{det}}
=
\frac{
\#\left\{(s,a):\hat{P}(s,a)=P^\star(s,a),\ \hat{R}(s,a)=R^\star(s,a)\right\}
}{
\#\left\{(s,a)\right\}
}.
$$

随机环境比较：

- next-state total variation distance；
- Wasserstein distance；
- reward distribution distance；
- termination probability error；
- trajectory divergence；
- invariant violation rate。

离散分布的 total variation distance 为：

$$
D_{\mathrm{TV}}(P,Q)
=
\frac{1}{2}\sum_x\left|P(x)-Q(x)\right|.
$$

## 11.7 策略质量

归一化策略分数：

$$
\mathrm{NormalizedPolicyScore}
=
\frac{
J(\pi_{\mathrm{auto}},E^\star)-J(\pi_{\mathrm{random}},E^\star)
}{
J(\pi_{\mathrm{reference}},E^\star)-J(\pi_{\mathrm{random}},E^\star)
}.
$$

$\pi_{\mathrm{auto}}$ 在候选环境 $E_{\mathrm{auto}}$ 上训练，但 co-primary score 必须由 sealed evaluator 将 candidate、random 和 reference 三种策略都放在同一冻结 gold environment $E^\star$ 上计算。`E2EValid` 始终只由 text、formal、API、hidden behavior 四个生成 gate 决定；普通 RLlib checkpoint 必须由 trainer-local 一次性 export execution 在同一 frozen trainer profile/namespace 内转为 strict-manifest-bound weights-only safetensors，checkpoint/pickle/cloudpickle 永不跨 profile，sealed evaluator 只用预注册可信 RLModule/connector code加载该 tensor map。每个 successful seed 的 immutable signed `PolicyExportManifest` 必须显式绑定 run/candidate/seed、training terminal record、source-checkpoint commitment、冻结 architecture/connector/observation-action adapter identity、trainer/exporter execution identity 及一个 content-addressed finite tensor artifact；terminal-failure seed 不得伪造 export。checkpoint export、tensor schema 或冻结 observation/action adapter 任一失败都单独记录 `GoldPolicyEvaluationValid=0` 与失败类型，并令 $Q^{\mathrm{gate}}=0$，不得追溯改写 `E2EValid`。在 $E_{\mathrm{auto}}$ 上得到的 return 只作诊断指标，不参与该归一化或非劣检验。

sealed policy evaluation 只能由 post-training coordinator 生成的 immutable signed `PolicyEvaluationRequest` 驱动。该 `extra="forbid"` closed request 显式绑定 experiment/run/candidate/run-manifest、signed four-gate `E2EGateVerdict`、signed smoke-pass attestation、suite calibration、sealed evaluator profile、冻结 adapter IDs/hashes，并按数值升序包含恰好十个唯一 seed bindings；seed IDs 必须 byte-for-byte 等于冻结 tuple `(1001,1002,1003,1004,1005,1006,1007,1008,1009,1010)`。三个 discriminated branch 唯一为：`success` 绑定 successful training terminal record、successful export terminal record、`PolicyExportManifest` 与 safetensors artifact 的 IDs/hashes；`training_failure` 只绑定 training terminal-failure record且全部 export fields 为空；`export_failure` 绑定 successful training terminal record和 export terminal-failure record且 manifest/tensor fields 为空。request 的 closed common fields 还包含 schema/signing domain、request ID、issued-at、not-before、expires-at、32-byte unpadded-base64url nonce、`signature_algorithm="Ed25519"`、coordinator signing-key ID 与 signature；run manifest 冻结唯一 coordinator principal/Ed25519 public key、key validity/revocation 和 clock-skew contract。Ed25519 signature 覆盖从完整 request 移除且仅移除 signature 后的 RFC 8785 JCS bytes；request ID、nonce 与 `(signing_key_id,run_id)` 进入 replay index。canonical ID/hash/signature、algorithm、time/key status、four-gate/smoke binding、exact seed set/order/uniqueness和每个 branch cardinality 全部验证通过后，evaluator 才能读取显式列出的 artifacts；目录扫描、glob、“latest”选择、缺失 ID 推断或 artifact substitution 一律拒绝。任一 failure branch 直接产生 `GoldPolicyEvaluationValid=0,Q^{gate}=0`，不尝试加载缺失 policy。

公开 `PolicyOutcomeRecord` 是四分支 closed discriminated union。evaluator-signed `evaluated` 仅在十 seed 全部成功导出并评估时携带 returns；evaluator-signed `invalid` 保留 request 中恰好十个 signed terminal seed bindings但固定两项 gate 为零；二者都把触发它的 `PolicyEvaluationRequest` ID/hash登记为 exact direct parent。repository-projector-signed `missing_by_design` 只覆盖训练开始前按设计缺失，`post_training_terminal` 只覆盖训练已开始后的 terminal failure。`FINAL_EVALUATING` reducer 在进入 `PACKAGING` 前逐项验证 request parent、seed/export/evaluation records、evaluator signature 与 outcome branch一致，低分仍是合法 evaluated result。

`missing_by_design` 不伪造 `PolicyEvaluationRequest` 或 evaluator signature。它由 run manifest 中冻结的 repository-projector principal 在 verified terminal event提交后确定性签发 `PolicyMissingnessProjection`：closed fields 只含 schema/signing domain、projection/run/experiment IDs、run-manifest ID/hash、outcome-mask ID/hash、terminal event ID/hash、reason literal（`clarification_required|pretraining_validation_terminal|sealed_e2e_gate_failed|training_smoke_failed`）、`GoldPolicyEvaluationValid=0`、适用时 `Q_gate=0`、issued-at、`signature_algorithm="Ed25519"`、projector key ID/signature。`issued_at` 必须 byte-for-byte 等于 terminal event 的 `issued_at`；projection ID 唯一定义为 `SHA256(RFC8785-JCS({"domain":"AutoMarkov-PolicyMissingnessProjection-ID-v1","run_id":...,"terminal_event_id":...,"outcome_mask_id":...}))`。Ed25519 signature 覆盖除 signature 外完整 JCS object，key validity/revocation规则与 run event repository相同；repository 以 `UNIQUE(run_id,terminal_event_id,outcome_mask_id)` CAS落库，重复签发只能返回相同 bytes/hash，错误 ID/time/algorithm 拒绝。terminal CAS 必须先验证 cause 与 reason 映射并把该 reason写入唯一 terminal event，projector只从已提交 head生成 projection；mismatch 不落 terminal commit。该分支禁止 policy request、seed、return、training/export artifact fields和 evaluator signature，对应 directories 必须缺失，不能以空文件或虚假 records补齐。

`post_training_terminal` 由同一 frozen repository-projector 在 `POLICY_TRAINING` 或 `FINAL_EVALUATING` 的 verified terminal CAS 内签发。closed fields 为 schema/signing domain、outcome/run/experiment IDs、run-manifest/outcome-mask/terminal-event IDs/hashes、`phase="training|final_evaluation"`、`reason="budget_exhausted|training_terminal_incomplete|evaluation_timeout|evaluation_result_missing"`、冻结 expected seed tuple `(1001..1010)`、按 seed 升序的 existing training/export/evaluation terminal artifact IDs/hashes、其补集 `missing_seed_ids`、nullable policy-request ID/hash、`GoldPolicyEvaluationValid=0`、`Q_gate=0`、issued-at、`signature_algorithm="Ed25519"`、projector key ID/signature。`phase=training` 要求 request 为空；`phase=final_evaluation` 要求 exact request ID/hash非空。existing 与 missing seed sets 必须不重叠且并集恰为冻结十 seeds；只引用已落库 artifacts，不伪造未启动 seed、metrics、export 或 evaluator record。issued-at、deterministic outcome ID、Ed25519/JCS/key-status/replay和 `UNIQUE(run_id,terminal_event_id,outcome_mask_id)` 与 `missing_by_design` 使用同一合同；terminal state 与 outcome 必须原子提交，失败整笔回滚。该分支允许已存在的 training/evaluation directories，保留 intention slot/denominator并保证 post-training crash、timeout、预算耗尽或缺结果机械映射为零。

上述两个 projector branch 的 terminal event ID/hash 均是 EventStore typed reference，由 event-reference validator 精确核验 run/sequence/hash；它们不进入 artifact `parent_artifact_ids`。各 projection envelope 的 artifact parents 只含其 closed payload 已列出的 run manifest、outcome mask、existing records 与 nullable request 等真实 ArtifactStore IDs。

每个 suite 必须在任何 candidate run 前由独立、signed gold pilot 生成冻结 `GoldScoreCalibration`：记录 metric direction、gold environment/adapter、random/reference policy identities、pilot seeds/episodes、$\widehat J_{\mathrm{random}}$、$\widehat J_{\mathrm{reference}}$、gap、one-sided 97.5% lower confidence bound 与预注册 `min_reference_random_gap>0`。按“越高越好”方向变换后，只有 gap 的 lower bound 严格大于该正阈值才允许 suite 进入 confirmatory preflight。失败时阻断整个 suite 并要求修订 calibration/evaluator/preregistration，不能令某个 candidate 的 $Q^{\mathrm{gate}}=0$。co-primary 归一化使用该 calibration 中冻结的 random/reference returns 与 denominator，所有 run 引用同一 calibration ID。

`GoldScoreCalibration` 的 interval contract 唯一如下。calibration manifest 在执行前冻结按数值升序的 $K$ 个 pilot seed IDs、每 seed 的 $E$ 个 evaluation episode/scenario IDs、direction $d\in\{-1,+1\}$、两种 policy identities、共同 gold environment/adapter、100,000 replicates、独立 32-byte `calibration_bootstrap_seed` 和 manifest payload hash。reference 与 random policy 必须在相同 `(pilot_seed_id, episode_id)` 初始状态/场景上成对评价；先计算 $y_{k,e}=d(R^{ref}_{k,e}-R^{random}_{k,e})$，observed gap 是 $K$ 个 seed 内 episode mean 的等权 mean，不按 episode length 加权。

每个 bootstrap replicate 先用 11.10.5 的 `calibration-pilot-bootstrap` 从 canonical pilot vector 有放回抽 $K$ 次：`scope_id=<suite_id>`、`draw_index=<pilot draw occurrence>`、selected null。对每个抽中的 pilot occurrence $k'$，再用 `calibration-episode-bootstrap` 从该 pilot 的 canonical episode vector有放回抽 $E$ 次：`scope_id=<suite_id>/pilot-draw-<two-digit k'>`、`draw_index=<episode draw occurrence>`、`selected_unit_id=<selected pilot_seed_id>`。同一 episode index 同时选择 reference/random return；policy 不在 bootstrap 中重训或重新采样。replicate statistic 仍是先 episode mean、再 pilot-occurrence 等权 mean；LCB 使用 11.10.5 的 $Q_{0.025}$ inverse empirical-CDF。缺 pair、非有限 return、seed/episode 顺序或 count 漂移、stream/schema/hash 不匹配均令 calibration invalid。

calibration conformance fixture 允许为测试把 $B$ 缩为 4：`suite_id=taxi_mdp`、pilot IDs `pilot-00,pilot-01`、每 seed 两个 episodes、direction `+1`、random returns 均为 `[0.0,0.0]`、reference returns 分别为 `[1.0,1.0]` 与 `[0.0,1.0]`，并使用 11.10.5 全零 seed/hash vectors。observed gap 必须为 `0.75`，四个 replicate gaps 必须按 replicate index 为 `[0.5,0.75,0.75,0.75]`，$Q_{0.025}$ LCB 必须为 `0.5`。production 仍必须使用恰好 100,000 replicates。

同时报告：

- final return；
- area under learning curve；
- sample efficiency；
- success/win rate；
- wall-clock throughput；
- recurrent-policy gain；
- evaluation variance。

## 11.8 自动化成本

- LLM calls；
- input/output tokens；
- Tavily Search/Extract/Crawl calls、credits 与 key cooldown；
- repair iterations；
- human questions；
- wall-clock time；
- RL environment steps；
- GPU hours；
- 失败运行比例。

## 11.9 重复次数与预算

### 自动化生成

- 6 个 suite 各 5 个 task-card variants；每个预注册 `RUN` 的 `(suite, variant, track, method)` cell 执行同一个在首个 generation 前冻结的 `n_pair` 次配对生成，`N/A` slots 只进入 intention-to-run/eligibility ledger；
- 所有方法使用相同输入、模型、采样参数、搜索预算和工具权限；
- `pair_id` 跨方法配对 generation seed、evidence snapshot、oracle-answer typed `sealed-v1` commitment 和预算；sealed broker 只按 track、方法提问与预算返回当前问题的 answer payload，原始/opaque artifact ID、content hash 与 sealed nonce 始终留在 sealed 域且 generation-visible manifest 不持有它们；
- 对完整语义 cells（`AUTO/v1..v4` 与 `HITL-ORACLE/v1..v5`），无效环境保留为端到端失败，不能从 RL 结果中删除；`AUTO/v5` 按 clarification outcome 独立评价。

### 生成配对样本量设计门禁

`20` 是初始候选 pair count，不在缺少功效证据时直接成为最终样本量。任何 generation/tool call 前，独立 design-analysis execution 必须创建 strict/frozen、signed、content-addressed `DesignPowerManifest` 和 `DesignPowerReport`。manifest 固定候选集合 `n_pair ∈ {20,24,30,40,60,80}`、24 个 confirmatory strata、两个 co-primary decision rules、10 个 RL seeds、`2,000` 次 deterministic Monte Carlo datasets、每项 gate 不少于 `10,000` 次的 production counter bootstrap、候选间 common-random stream 与 prefix reuse、彼此独立的 32-byte `design_power_dgp_seed`/`design_power_bootstrap_seed`、counter-stream/JCS/schema/implementation hashes，以及 nuisance inputs 与来源。外层 simulated datasets 只由 11.10 的 `design-power-dgp` domain 产生；每个 dataset 内的 production paired/nested bootstrap 使用 design bootstrap seed 和带 dataset/gate identity 的 scope。candidate value 不进入 counter entropy；所有候选复用同一最大 80-pair DGP stream及其前 `n_pair` 个 units，不复用或观察任何实际 run outcome。design alternative 固定为 `E2EValid` aggregate paired difference `+0.10` 和 policy aggregate difference `0.00`（相对 `-0.05` non-inferiority boundary）；null boundary 固定为前者 `0.00`、后者 `-0.05`。E2E discordant-pair rate、两方法 marginal validity、invalid-to-zero rate、pair-level policy variance、十 seed 内 covariance/ICC 与 suite/variant heterogeneity只能来自不重叠的 Public Dev pilot、正式论文/官方 benchmark 的可引用先验，或在缺证据时采用使方差最大的保守边界；每项都必须有 evidence ID/hash，Sealed Gold、hidden result、confirmatory outcome和方法实际结果不可作为输入。

每个 Monte Carlo dataset 必须走与 11.10 完全相同的 fixed-strata estimator、invalid-to-zero、paired seed nesting、counter-derived bootstrap 与两项 gate，不得使用正态近似替代最终 decision rule。report 对每个候选 `n_pair` 给出两项 marginal power、两门同时通过的 joint power、null-boundary false-success probability、Clopper–Pearson exact binomial 95% interval、E2E 最小可检测绝对差、policy 可排除的最大非劣差和预期 interval width。冻结选择规则是：取最小 `n_pair`，使 design alternative 的 joint-power 95% lower bound 至少 `0.80`、每项 marginal-power lower bound 至少 `0.90`，且 null-boundary false-success 95% upper bound不超过 `0.025`。`DesignFreezeVerdict` 是 experiment preflight 的 strict enum，值域仅为 `READY` 或 `BLOCKED_DESIGN`，持久化于 signed `DesignPowerReport` 和 `ExperimentManifest`；它不是 `RunState`，且 verdict 冻结前不得创建任何 `Run`。没有候选满足或所选值超出已批准 budget 时，verdict 必须为 `BLOCKED_DESIGN`，只能由新 preregistration/budget approval 创建新的 design-analysis execution 后恢复，不能向 run reducer 发出未知状态。所有 eligible cells、tracks、methods 与 component/information-structure ablations使用同一已选 `n_pair`，pair IDs 固定为 `g00..g{n_pair-1}`；首个 run 后禁止根据 outcome、failure、cost或 observed variance调整、停止、补充或替换 pair。即使最终选择 20，也必须发布上述 report，不能用预算理由替代 MDE/precision 证据。

### 策略训练

- 每个有效环境预注册恰好 10 个 RL seed slots，并为每个 slot 保留 signed success 或 terminal-failure record；任一 record 缺失、crash、timeout、OOM 或 non-finite 都令该 candidate `GoldPolicyEvaluationValid=0`、`Q^{gate}=0` 且以 incomplete/partial 单独报告，但 candidate 仍保留在 confirmatory denominator，不得按结果删除或换 seed；
- Taxi、MiniGrid、MPE2、MetaDrive：每个 seed 评估 100 episodes；
- SMACv2：每个 seed 评估 50 battles；
- CityLearn：评估完整 held-out 时间周期。

训练预算冻结规则：

1. 只在 gold environment 上执行 pilot；
2. 优先采用官方论文或参考实现预算；
3. 若没有预算，选择学习曲线后 20% 区间增益低于预设阈值后的最小预算上限；
4. 在比较自动生成方法前冻结；
5. 所有方法共享同一上限。

任何方法都不能按自己的学习曲线提前停止或追加预算。crash、timeout、OOM、NaN 和 early termination 保留为 outcome，并以预注册 failure policy 处理。gold pilot 只用于冻结预算，不参与主结果；pilot seeds、scenario IDs 和 task-card variants 与 Sealed Gold 主评测不重叠。

## 11.10 统计检验

### 11.10.1 分析单位和嵌套索引

记 track 为 $t\in\{\mathrm{AUTO},\mathrm{HITL\text{-}ORACLE}\}$，suite 为 $s\in\{1,\ldots,6\}$，variant 为 $v\in\{1,\ldots,5\}$，generation pair 为 $p\in\{1,\ldots,N_{pair}\}$，方法为 $m$，RL seed 为 $r\in\{1,\ldots,10\}$；`n_pair` 只能由上一节的 pre-generation design gate 冻结。两条 track 是独立的预注册分析层，数据层级是：

$$
r \subset p \subset v \subset s \mid t,
$$

而方法在同一 $(t,s,v,p)$ 内配对，RL seed index 在可训练 candidate 间配对。`AUTO` 是唯一 confirmatory 主轨；`HITL-ORACLE` 是预注册 secondary/mechanism 轨。除第 9.1 与 11.10.3 节预注册的两项 paired `AUTO`/`HITL-ORACLE` interaction contrasts 外，所有估计、bootstrap 和检验都在 track 内完成；该 interaction 也固定完整 24 cells，并让四路 method×track observations 共享同一 generation-pair/RL-seed bootstrap indices。任何 analysis 都禁止跨轨 pooling。不得把 10 个 RL seeds、`n_pair` 个 generation attempts 或多个 episode 当成彼此独立的平面样本。episode/battle 只用于估计某一 RL seed 的评估均值。

confirmatory finite-benchmark estimand 对六个 suite 和每 suite 四个完整语义 variants（`v1..v4`）等权，不按 episode 数、有效 candidate 数或训练吞吐加权。`AUTO/v5` 的 `SafeClarificationRequired` 与 `HITL-ORACLE/v1..v5` 是预注册 secondary/mechanism estimands，并使用各自冻结的 outcome mask。secondary generalization analysis 才把 suite 当作可重采样层，并清楚标记探索性。

### 11.10.2 两个 co-primary outcomes

两个 co-primary outcomes 仅定义于 `track=AUTO, variant in {v1,v2,v3,v4}`。第一项是 pair-level `E2EValid`，第二项是 gate-aware policy score：

$$
Q^{\mathrm{gate}}_{s,v,p,m}=
\begin{cases}
0, & \text{E2EValid}=0\ \text{或 GoldPolicyEvaluationValid}=0,\\
\frac{1}{10}\sum_{r=1}^{10}
\operatorname{clip}(Q_{s,v,p,m,r},0,1), & \text{candidate 有效},
\end{cases}
$$

其中 $Q$ 是按 suite 预注册 random=0、reference=1 的标准化 gold-environment policy score。第二分支的“candidate 有效”同时要求 `E2EValid=1`、`GoldPolicyEvaluationValid=1` 且十个 seed 的 signed evaluation records 完整。将生成无效或 gold-policy evaluation 无效的 candidate 赋 0，避免只在成功样本上比较导致 selection bias；两类失败分别计数。另行报告 jointly-valid pairs 上的 conditional policy score，但只作解释性结果。

### 11.10.3 Confirmatory CI 与预注册 outcome tests

两个 co-primary decision gates 固定 `track=AUTO` 且 outcome mask 固定为 `v1..v4`，由不少于 10,000 次、固定 bootstrap seed 的 paired stratified-bootstrap CI 判定。六个 suite 与每 suite 四个 variant 是有限基准的 24 个固定、等权 strata，在 confirmatory bootstrap 中均不得重采样或遗漏；每个固定 $(s,v)$ cell 内以共同索引配对重采样已冻结的 `n_pair` 个 generation pair。对有效 candidate 的 policy mean，再在该 pair 内以共同 RL seed index 配对重采样；invalid candidate 的 gate-aware zero 不虚构 seed records。每次 replicate 先计算 cell 内方法差，再对完整 24 cells 等权聚合。整个 method vector 使用同一组 pair/seed bootstrap indices，不能为各方法独立抽样而破坏 pairing。只有明确标记 exploratory 的 generalization analysis 才可把 suite/variant 当作可重采样的超总体层。`HITL-ORACLE` 使用其冻结 mask 单独生成 secondary/mechanism estimates；只以 `v1..v4` 报告同 outcome 的 track interaction。它不进入 co-primary CI。

预注册 outcome tests 为：

- `E2EValid` 的 `n_pair` 个二元配对结果在每个 eligible cell 使用 exact McNemar：`AUTO/v1..v4` 共 24 cells，`HITL-ORACLE/v1..v5` 共 30 cells。`AUTO/v5` 的六个 cells 对 `SafeClarificationRequired` 使用独立 exact McNemar 与独立 Holm family。`react_executor` 必须执行全部 60 track/cells 并覆盖这些预注册 masks；其他 baseline 在首个 run 前按 `comparator × track × outcome` 冻结 `eligible_cells` 与 family cardinality，运行后不得因结果改变 family。该检验只作 cell-level diagnostic，co-primary 结论仍由固定 24 cells、保留 cell 内 pair/seed 配对结构的 stratified-bootstrap CI 给出；
- 对每项预注册连续 secondary outcome，在其 outcome mask 覆盖的 eligible cell 内报告 `n_pair` 个 generation-pair method differences、cell mean/median 与 paired bootstrap interval，仅作 descriptive diagnostic；每个 `outcome × comparator × track` hypothesis 的主检验是 11.10.5 定义的单一 fixed-strata aggregate null-centered bootstrap test，Holm family 只接收这些 aggregate raw p-values，禁止合并或输入 eligible-cell diagnostics；
- mixed-effects/logistic hierarchical model 作为 model-based sensitivity analysis，随机效应结构遵循 variant/pair nesting；
- episode-level uncertainty、seed-level uncertainty和generation-level uncertainty分别报告，不合并成伪精确 standard error。

### 11.10.4 非劣界、优越性与置信区间

策略 co-primary 的非劣界固定为标准化绝对差 $\Delta=0.05$，即 reference–random return gap 的 5%。预注册主比较方法固定为 `ReAct + code executor`，不得依据 Public Dev 或 Sealed Gold 结果重新选择。对 AutoMarkov 与该主比较方法的差：

$$
d_Q=Q^{\mathrm{gate}}_{\mathrm{AutoMarkov}}
-Q^{\mathrm{gate}}_{\mathrm{baseline}},
$$

只有单侧 97.5% lower confidence bound 满足

$$
\operatorname{LCB}_{97.5\%,\,1s}(d_Q)>-0.05
$$

时才能声明 policy non-inferior。`0.05` 是标准化分数的绝对界，不得改写为“观察到的 baseline 的 5%”或事后换尺度。若 lower bound 大于 0，可在通过 multiplicity gate 后另行声明 superiority。

自动化有效率的 superiority 条件为：

$$
\operatorname{LCB}_{97.5\%,\,1s}
\left(
\mathrm{E2EValid}_{\mathrm{AutoMarkov}}
-
\mathrm{E2EValid}_{\mathrm{baseline}}
\right)>0.
$$

最终“成功”需要 E2E superiority 和 policy non-inferiority 两个 co-primary gates 同时通过。默认报告单侧 97.5% CI 以匹配 confirmatory gates，并附两侧 95% descriptive CI、absolute effect、relative effect 和适用效应量。

### 11.10.5 Holm families

以下 secondary family 分开预注册并各自使用 Holm step-down correction；唯一 co-primary comparator `ReAct + code executor` 不在 secondary family 中重复计数：

1. AutoMarkov 对除 ReAct 外其余四个 eligible 自动 baseline 的 `E2EValid` 比较；
2. AutoMarkov 对除 ReAct 外其余四个 eligible 自动 baseline 的 $Q^{\mathrm{gate}}$ 比较；
3. full AutoMarkov 对六个 ablations 的 `E2EValid` 与 $Q^{\mathrm{gate}}$ 比较分别构成两个六假设 Holm family；
4. `AUTO`/`HITL-ORACLE` 在 `v1..v4` 上的 paired difference-in-differences，对 `E2EValid` 与 $Q^{\mathrm{gate}}$ 的两项 interaction；
5. `AUTO/v5` 的 `SafeClarificationRequired` comparisons，独立于 E2E 和 policy families。
6. MPE2 native-local POSG 对 full-state MG adaptation 的 `E2EValid` 与公共 full-state calibration 尺度上的 $Q^{\mathrm{info}}_{gate}$ 两项 paired contrasts，使用独立 two-sided 两假设 family，不进入六项 component-ablation families。

每个 Holm hypothesis 的单一 aggregate raw test 在 freeze 前固定如下，不能从 cell-level diagnostics 合并得到：先对每个 generation pair 构造 paired contrast $d_{s,v,p}$；$Q^{\mathrm{gate}}$ 先依冻结规则把 invalid candidate 置 0，并在有效 candidate 内按相同 RL seed indices 聚合为 pair-level 值。MPE2 policy hypothesis 在此步骤以 9.4.2 定义的公共尺度 $Q^{\mathrm{info}}_{gate}$ 取代 $Q^{\mathrm{gate}}$，保留相同 invalid-to-zero 与 paired-seed aggregation 规则。observed statistic $\widehat\Delta_h$ 是每个冻结 $(s,v)$ stratum 内 pair contrast mean 的等权 stratum mean，因此 suite/variant 不按 episode、有效 candidate 或吞吐加权。AutoMarkov-vs-baseline、full-vs-component-ablation 与 `SafeClarificationRequired` 使用预注册方向的 one-sided superiority alternative；两项 track interaction 与两项 MPE2 information-structure contrasts 使用 two-sided alternative。raw p-value 使用恰好 100,000 次 fixed-strata paired null-centered bootstrap：对 hypothesis $h$ 的每个 pair contrast 使用 $d^0_{h,s,v,p}=d_{h,s,v,p}-\widehat\Delta_h$，从而保留 stratum heterogeneity、pairing、invalid-to-zero 与截断造成的实际分布形状，同时令有限基准 aggregate null mean 精确为零；每个固定 stratum 内有放回抽 `n_pair` 个 pair，policy contrast 对每个抽中的有效 pair occurrence 再以共同索引有放回抽十个 RL seeds。完整 method/hypothesis vector 共用每个 pair/seed index。one-sided raw p-value固定为 $(1+\#\{\Delta^0_{h,b}\ge\widehat\Delta_h\})/(100000+1)$，two-sided 固定比较 $|\Delta^0_{h,b}|\ge|\widehat\Delta_h|$。该检验以 aggregate mean contrast 为目标，不依赖 pair differences 关于零对称或 method-label randomization；cell-level exact McNemar 与连续 descriptive diagnostics绝不作为 Holm raw p-value 输入。

每个 family 在运行前冻结 eligible hypothesis IDs、方向、cardinality $m$、`alpha=0.05`、32-byte base64url `null_bootstrap_seed` 和独立 `effect_bootstrap_seed`。将 raw p-values 排为 $p_{(1)}\le\cdots\le p_{(m)}$ 后，Holm adjusted value 固定为 $\tilde p_{(k)}=\max_{j\le k}\min\{1,(m-j+1)p_{(j)}\}$，再映射回原 hypothesis ID；ties 按 hypothesis ID bytes 排序，`N/A` eligibility 在首个 run 后不得变化。effect 与 raw CI 使用与主分析相同的 100,000 次 paired stratified bootstrap 和 `effect_bootstrap_seed`。为避免把不唯一的“Holm CI”留给实现猜测，multiplicity-adjusted bounds 明确采用保守 Bonferroni familywise inversion：one-sided lower bound 取 bootstrap $\alpha/m$ quantile，two-sided interval 取 $\alpha/(2m)$ 与 $1-\alpha/(2m)$ quantiles；同时报告 unadjusted one-sided 97.5% bound、two-sided 95% CI、Holm adjusted p-value 与该 Bonferroni-adjusted bound。

所有 bootstrap quantile 使用同一个 inverse empirical-CDF 规则。把包含重复值的 $B$ 个 replicates 按 `(value, replicate_index)` 升序记为 $x_{(1)},\ldots,x_{(B)}$；对 $p\in[0,1]$，定义 $Q_p=x_{(r)}$，其中 $r=\min(B,\max(1,\lceil pB\rceil))$，rank 从 1 开始。禁止线性插值、Harrell–Davis 或 package default。co-primary/unadjusted one-sided 97.5% LCB 使用 $Q_{0.025}$，two-sided 95% interval 使用 $[Q_{0.025},Q_{0.975}]$；Bonferroni bounds 使用上一段给出的 $p$。固定 quantile vector `values=[1,3,5,7,9,11,13,15]` 必须得到 `Q_0.025=1`、`Q_0.25=3`、`Q_0.5=7`、`Q_0.975=15`。

Monte Carlo 与 bootstrap 不允许调用语言运行时 PRNG。唯一抽样算法是 `SHA-256(RFC8785-JCS(counter_object))` 的 domain-separated counter stream；`counter_object` 是 closed object，固定字段为 `schema_version="automarkov.analysis-counter.v1"`、`domain ∈ {design-power-dgp, paired-bootstrap, nested-seed-bootstrap, calibration-pilot-bootstrap, calibration-episode-bootstrap}`、`stream_manifest_hash`、`seed_b64url`（对应 design DGP/design bootstrap/null/effect/calibration stream 的 32-byte seed）、零起始 `replicate_index`、`scope_id`、`draw_index|None`、`selected_unit_id|None` 和零起始 `rejection_index`。counter 不含 `hypothesis_id`、method ID 或 candidate `n_pair`；`stream_manifest_hash` 绑定完整 design/analysis family 或 calibration manifest，因此同一 stream 的所有 hypotheses、完整 method vector和所有 design candidates必须共用可适用的 draw。

所有 IDs 按 UTF-8 bytes 升序；strata 按 `(suite_id, variant_id, track)`、pair 按 `generation_pair_id`、RL seed/pilot seed 按数值升序形成 canonical input order。`design-power-dgp` 使用 `replicate_index=<dataset index 0..1999>`、`scope_id=<alternative-or-null>/<outcome>/<stratum_id>`、`draw_index=<component draw occurrence>`、`selected_unit_id=<max-80 pair ID or RL-seed ID>`；digest 的 unsigned integer $u$ 映射为精确 open-unit rational $(u+1)/(2^{256}+1)$，再由 manifest 中 closed、versioned、implementation-hashed inverse-CDF/finite-support transform 和 rational nuisance parameters生成该 component。每个 transform 的 draw cardinality、component ordering和边界比较规则必须冻结并有 golden vectors；candidate `n_pair` 不参与 preimage，候选只消费 `g00..g{n_pair-1}` 前缀。design 内层 `paired-bootstrap`/`nested-seed-bootstrap` 的 scope 分别以前缀 `design-power/dataset-<four-digit>/<gate>/` 绑定 dataset/gate，使用 `design_power_bootstrap_seed`，其 replicate index 只表示内层 bootstrap replicate；生产 analysis 使用无此前缀的 stratum scope、`draw_index=<pair draw occurrence>`、selected null。`nested-seed-bootstrap` 使用 `scope_id=<stratum_id>/pair-draw-<two-digit occurrence>`、`draw_index=<seed draw occurrence>`、`selected_unit_id=<selected pair_id>`。calibration 两个 domain 的 scope 规则见 11.7。bootstrap 从大小为 $n$ 的 canonical vector 取 index 时，把 digest 当作 256-bit unsigned big-endian integer $u$，令 $L=2^{256}-(2^{256}\bmod n)$；若 $u\ge L$，递增 `rejection_index` 重算，否则取 $u\bmod n$，从而禁止 modulo bias。每个固定 stratum 每 replicate 恰抽取已冻结的 `n_pair` 个 pair indices；每个抽中 pair occurrence 再恰抽取 10 个共同 RL-seed indices，invalid candidate 不产生 seed draw。null-centered raw tests 只使用 `null_bootstrap_seed`，effect/raw/Bonferroni bounds 只使用 `effect_bootstrap_seed`；design DGP/design bootstrap、analysis null/effect 与 calibration streams彼此独立。manifest 必须冻结此算法 ID、schema version、hash algorithm、JCS implementation/version、canonical order、scope grammar、centering/transform rule 与 draw counts。

冻结 counter test vectors 使用全零 32-byte seed（base64url `AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`）、`stream_manifest_hash="sha256:" + "0"*64`、`replicate_index=0` 和 `rejection_index=0`。其余字段及预期结果如下；实现必须逐字节重建 JCS bytes 并匹配 digest/result：

| domain | `scope_id` | `draw_index` | `selected_unit_id` | SHA-256 digest | result |
|---|---|---:|---|---|---:|
| `design-power-dgp` | `alternative/e2e/taxi_mdp/v1_canonical/AUTO` | 0 | `g00` | `1b87012325b8642204f49db75cbe76a9094fab049cc0d0934f8a07c47264411c` | open-unit rational numerator `12450979114810163420130463304045236433571895873762933206487462195618231107869` over denominator $2^{256}+1$ |
| `paired-bootstrap` | `taxi_mdp/v1_canonical/AUTO` | 0 | null | `722cf43a8050cf338ae8cbb25bb4f0bfe749e54b4936d8ed6e39b3fa0b30ff20` | index `4` for $n=20$ |
| `nested-seed-bootstrap` | `taxi_mdp/v1_canonical/AUTO/pair-draw-00` | 0 | `g00` | `1b73f72801a68222124b8c4f1e51404cef6d016f9e2277a4bedbcafd75c88fc1` | index `5` for $n=10$ |
| `calibration-pilot-bootstrap` | `taxi_mdp` | 0 | null | `69216e1bd89178ea99fd870dd8ef128dc47effdf6c380f2a5b8b01b2b1313853` | index `1` for $n=2$ |
| `calibration-episode-bootstrap` | `taxi_mdp/pilot-draw-00` | 0 | `pilot-00` | `887c20e1e284fe5feae47266814750bc84872512f560f17b31c9e5030b60893e` | index `0` for $n=2$ |

`ReAct + code executor` 是唯一 co-primary comparator。AutoMarkov 对其余 eligible baseline 的比较是 Holm-adjusted secondary family；不得依据 Public Dev 或 Sealed Gold 结果改选主比较方法。报告 aggregate raw/Holm-adjusted p-values、原始/Bonferroni familywise-adjusted confidence bounds、cell diagnostics 和 family definition；不得把 `N/A` 当失败或从 Holm family 临时删除。

不能在运行实验前承诺显著优于基线。若任一 co-primary gate 未通过，只能报告 inconclusive、inferior 或 trade-off，不得用 secondary metric 替代。

## 11.11 六项核心消融

| Ablation method ID | 唯一关闭的能力 | 验证问题 |
|---|---|---|
| `automarkov_no_evidence` | Tavily retrieval 与 `EvidenceLedger`；只保留 task-card manifest 本身 | 外部证据是否减少错误假设 |
| `automarkov_no_text_critic` | Text Critic；exact-ID approval policy 仍执行其余公开 predicates | 文字表征检查是否必要 |
| `automarkov_no_formal_critic` | Formal Critic；strict schema/structural validators 与 approval policy 仍保留 | 数学 critic 是否必要 |
| `automarkov_single_agent_workflow` | 多角色调度；同一 Qwen 依冻结顺序完成等价步骤 | 多智能体分工是否有效 |
| `automarkov_no_simulation_tester` | public Simulation Tester 的 property/metamorphic/generation-visible differential/public-dev trajectory tests；保留 static/unit/API/deterministic core invariant gate | 行为级 public 验证是否必要 |
| `automarkov_no_training_feedback` | pre-freeze `PublicDevLearningProbe` 与其最近致因回退；sealed gate 后的冻结训练/evaluation/只读诊断仍完整执行 | 无 sealed 信息的 public learning diagnostic 是否提升最终成功率 |

不建议把“同时删除文字表征和数学建模”作为主要消融，因为该设定直接退化为自然语言到代码的基线，无法细粒度定位贡献。

六项消融使用独立、预注册且可执行的 ablation ledger，不混入六个主方法：`track=AUTO`、六个 suites、`v1..v4`、六个 ablation method IDs、每 cell 同一冻结 `n_pair` 个 `g00..g{n_pair-1}`，因此恰有 144 cells、`144 × n_pair` 个 intention-to-run generation slots。全部 144 cells 在首个 run 前固定为 `RUN`；移除能力导致的失败属于 outcome，不能改标 `N/A`。每个 ablation generation job 只获得与 full method 相同的预冻结 task/model/access/route/seed/budget `pair_binding_id`，绝不读取 full run ID、manifest、status、artifact/hash、output 或 cache。两边 terminal 后，只有无 generation capability 的 coordinator/analysis principal 才按 `(experiment, suite, variant, AUTO, pair, reference_method_id=automarkov)` 生成 signed `AblationReferenceBinding`；full method 不重复运行，有效 candidate 继续使用同一 10 个 RL seeds。

ablation manifest 对每个 ID 冻结 strict `AblationExecutionPlan`：exact method ID、唯一 `disabled_capability`、closed `omitted_gate_ids`（可以为空）、`execution_topology`、closed `approval_predicate_projection`、expected missing artifact kinds、始终 required gate IDs、其余 graph/config/prompt/model/source-access/route、预算 ceiling、pair seeds、outcome mask、artifact schema 与 code hash。关闭的步骤不把预算转给其他步骤，其他 component 的 per-step 和 total ceiling 与 full method 相同。映射固定为：

| Ablation ID | Gate projection 或 topology substitution | 始终保留 |
|---|---|---|
| `automarkov_no_evidence` | `omitted_gate_ids=(EVIDENCE_LEDGER_CLOSURE)`；Algorithm line 5 执行 omission branch 并构造 `EvidenceOmissionRecord`/`EvidenceOmissionBinding`，仅跳过 ledger 初始化；line 7 retrieval 跳过；approval 使用 `NoEvidenceApprovalProjection` 并移除 evidence-closure/evidence-derived predicates | task card、opaque sealed commitments、无需 evidence 的 schema/structural/API/public-dev checks、text/formal/API/sealed evaluators；不挂载 Allowed/Blocked manifest 或 evidence-derived feedback |
| `automarkov_no_text_critic` | `TEXT_CRITIC_REVIEW`；approval 移除 critic-closure predicate | text schema、traceability、exact-ID approval 与独立 text outcome evaluator |
| `automarkov_no_formal_critic` | `FORMAL_CRITIC_REVIEW`；approval 移除 critic-closure predicate | discriminated schema、deterministic structural validators、exact-ID approval 与独立 formal outcome evaluator |
| `automarkov_single_agent_workflow` | `omitted_gate_ids=()`；`execution_topology=single_qwen_sequential`，用 `ExecutionTopologySubstituted` audit event；approval predicate 不变 | 全部 evidence/critic/validation gates，由同一 Qwen 顺序角色执行 |
| `automarkov_no_simulation_tester` | `omitted_gate_ids=(PUBLIC_SIMULATION_TESTER)`，只关闭独立 Algorithm line 25 的 property/metamorphic/differential/trajectory group；approval predicate 不变 | line 24 static/unit/API/seed/deterministic core invariant 与 line 26 `PublicDevLearningProbe`、single sealed gate、final training/evaluation |
| `automarkov_no_training_feedback` | `omitted_gate_ids=(PUBLIC_DEV_LEARNING_PROBE_AND_ROLLBACK)`，对应独立 Algorithm line 26/27；approval predicate 不变 | 全部 public tests、single sealed gate、final training/evaluation 与只读 failure classification |

reducer 只对 `experiment_kind=ablation` 且 plan hash、ablation ID、track/variant/cell 全匹配时接受 2.9 定义的 strict discriminated `GateOmittedByDesign` union。它没有自由 `data`、`candidate IDs` 或 `input IDs` 概念字段；closed common field set 恰为 `schema_version`、`signing_domain`、`event_type`、`event_id`、`experiment_id`、`run_id`、`sequence_no`、`previous_event_hash`、`track`、`variant_id`、`cell_id`、`ablation_execution_plan_artifact_id`、`ablation_execution_plan_hash`、`pair_binding_id`、`task_card_artifact_id`、`subject_artifact_ids`、`expected_missing_artifact_kinds`、`output_artifact_ids`、`reason`、`issued_at`、`nonce_b64url`、`signing_key_id`、`signature_b64url`，再加 branch-specific `ablation_method_id` 与 discriminator `omitted_gate_id`。五个 branch 的机械约束固定为：

| `omitted_gate_id` | method | `subject_artifact_ids` | `expected_missing_artifact_kinds` | `output_artifact_ids` |
|---|---|---|---|---|
| `EVIDENCE_LEDGER_CLOSURE` | `automarkov_no_evidence` | 空 tuple；line 5 尚无 candidate | `("EvidenceLedger",)` | 恰一个 strict `EvidenceOmissionRecord` ID |
| `TEXT_CRITIC_REVIEW` | `automarkov_no_text_critic` | 恰一个当前 `TaskContract` ID | `("TextCriticReport",)` | 空 tuple |
| `FORMAL_CRITIC_REVIEW` | `automarkov_no_formal_critic` | 恰一个当前 `DecisionProcessSpec` ID | `("FormalCriticReport",)` | 空 tuple |
| `PUBLIC_SIMULATION_TESTER` | `automarkov_no_simulation_tester` | 恰一个当前 `EnvironmentImplementation` ID | `("PropertyTestReport","MetamorphicTestReport","DifferentialTestReport","TrajectoryTestReport")`，顺序固定 | 空 tuple |
| `PUBLIC_DEV_LEARNING_PROBE_AND_ROLLBACK` | `automarkov_no_training_feedback` | 恰一个当前 `EnvironmentImplementation` ID | `("PublicDevLearningProbeReport",)` | 空 tuple |

所有 ID 非空；common task/plan/pair IDs 与 branch subject artifact 都作为 event 的 exact direct inputs 校验，artifact kind、current revision、run/cell/track/variant、plan artifact/hash 与 method/gate pair 任一不匹配均拒绝。`EVIDENCE_LEDGER_CLOSURE` output record 的 envelope exact parents、payload binding 和后续 Classification parents 还必须满足 §2.10 invariant 15。signature 使用 FixedCommitRunner 的 Ed25519 key，对从完整 event 移除且仅移除 `signature_b64url` 后得到的 RFC 8785 JCS bytes 签名，并采用与 `SignedApprovalEvent` 相同的 key-status、sequence/nonce/event replay 检查。event 与同一 transition 上全部未省略 gate report 共同推动 normal state transition，但不产生 pass report、不升级被省略 gate 的 validation level，也不能进入 `E2EValid`。`automarkov_no_evidence` 使用 strict `EvidenceOmissionBinding`，绝不把 omission ID 填入 ledger branch。所有未 mask gate 仍须真实通过；independent outcome evaluator 不读取 internal mask，仍用相同 text/formal/API/hidden-behavior gold contract 评分。

`automarkov_single_agent_workflow` 不产生 `GateOmittedByDesign`。它在 Algorithm line 3 追加 `extra="forbid"` 的 `ExecutionTopologySubstituted` audit event，字段固定为 domain/schema、event/run/sequence/previous hash、plan/method/cell IDs、`from_topology="multi_role"`、`to_topology="single_qwen_sequential"`、冻结 role order/prompt hashes/model identity、issued-at/nonce、runner key ID/signature；使用同一 Ed25519/JCS/replay contract。该 event 只证明 execution topology replacement，不推进 reducer、不省略任何 gate/approval predicate/required artifact。

Algorithm 1 描述 full method并把可控点拆开：no-evidence 在 line 5 执行 omission branch，以 `EVIDENCE_LEDGER_CLOSURE` event 落库一个 typed omission record/binding，只跳过 ledger 初始化和 line 7 retrieval；no-text/no-formal 分别跳过独立 lines 10/17；no-simulation 只跳过 line 25；no-training-feedback 只跳过 line 26 及其 line 27 rollback；single-agent 只在 line 3 替换 topology。ablation runner 在调用被 mask component 前追加/验证相应 event，其余 line、未 mask gate、revision budget 和 terminal semantics 不变。未知/多个 capability diff、mask 未登记 gate、为 required/sealed/schema/security gate发 omission、缺/多 event、错误 binding/artifact kind、把 omission 记作 pass、topology event推进 state、projection 与 method ID 不一致或 signature/replay failure 全部 fail closed。测试必须逐一证明六条合法 projection 可达，并覆盖这些负例。

`automarkov_no_simulation_tester` 仍必须冻结 candidate 后执行单次 sealed hidden-behavior gate；任何 ablation 都不得 mask 或放松 strict schema、deterministic core invariant、exact-ID approval authenticity、sealed、sandbox、secret、license、fixed-commit、artifact hash/DAG/event-chain、budget 或 independent outcome evaluator。`automarkov_no_training_feedback` 的唯一处理差异发生在 candidate freeze 前的 public-dev loop，因此对 E2E/Q 的影响可识别；sealed gate 后仍禁止任何回边。

标准 ablation artifact key 为：

```text
ablation/<suite_id>/<variant_id>/AUTO/<ablation_method_id>/<pair_id>/<rl_seed_or_generation>/
```

`AblationReferenceBinding` 是 `extra="forbid"` 的 closed signed object，只含 domain/schema、binding/experiment IDs、共同 suite/variant/track/pair/pair-binding commitment、`reference_method_id="automarkov"`、full/ablation terminal run IDs 与 attestation hashes、issued-at/nonce、analysis key ID/signature；不含任一方法 payload。analysis key/validity 与 replay contract 在 preflight 冻结，signature 覆盖除 signature 外的完整 JCS object。分析对每个 outcome 分开在固定 24 个 suite×variant strata 内复用主计划的 paired stratified-bootstrap；analysis principal 先验证双方独立 attestation 与 post-terminal binding，再让每个 ablation 与 full AutoMarkov 共享 pair/seed indices。`E2EValid` 和 $Q^{\mathrm{gate}}$ 各自形成一个预注册六假设 Holm family，报告每项 effect、raw/adjusted p-value/CI、失败与 deviation counts。ablation 结论始终是 secondary，不改变 AutoMarkov-vs-ReAct 两个 co-primary gates。

---

# 12. 相对于相关工作的创新点

| 维度 | A-LAMP | Agent² | Agent2World | AutoMarkov |
|---|---|---|---|---|
| 数学范围 | 主要面向 MDP | MDP 建模和 RL 自动化 | symbolic/code world model | 统一 MDP、POMDP、MG、POSG typed IR |
| 前置任务表征 | 自由文本进入专用代理 | Task-to-MDP | Deep Researcher | 独立 `TaskContract`，数学对象选择后置 |
| 外部证据 | 非核心 | 非核心 | Web Researcher | Tavily + claim–evidence ledger + 冲突检测 + 来源哈希 |
| 用户确认 | 有限澄清 | 强调全自动 | 主要自动化 | 文字和数学两个不可跳过的确认 gate |
| 多智能体关系 | 多个字段代理 | Generator/Target | Researcher/Developer/Testers | typed blackboard + 有限回边 + 最近致因回退 |
| 数学验证 | 主要 LLM 自检 | 训练反馈 | 单元与模拟测试 | schema、符号、概率、Markov、信息结构和可选 model checking |
| 环境实现 | 主要生成代码 | 生成和优化 agent | 生成 code/PDDL world model | Reuse—Compose—Generate 三路选择 |
| 动态验证 | executor | policy feedback | unit + simulation tester | 属性、变形、差分、轨迹、mutation 和 RL 诊断 |
| RL 后端 | DQN 为主 | 多种 RL/MARL | 非统一训练后端 | RLlib 单一受控后端 |
| OOD | 缺少显式路由 | 缺少显式路由 | 原生包含 PDDL | `IN_SCOPE`/`REDUCIBLE`/`OOD` 显式路由 |
| 可追溯性 | 中间输出 | 训练反馈 | 测试轨迹 | 需求—证据—数学—代码—测试全链路追踪 |
| 公平评测 | 原论文任务 | 原论文任务 | 三个 world-model benchmark | 六项统一任务 + 三项独立论文复现 |

项目真正的创新不在于堆叠更多智能体，而在于：

1. **跨四类序贯决策对象的 typed semantic compiler**；
2. **两层人机语义锁定机制**；
3. **基于证据且防 benchmark 泄漏的 Web Retrieval**；
4. **以反例和测试驱动的最近致因回退**；
5. **官方模拟器复用与自动环境生成的统一选择机制**；
6. **统一 RLlib 后端下的跨对象策略验证**；
7. **需求、证据、数学符号、实现和测试之间的可追踪映射**；
8. **对四类对象外任务的显式拒绝、归约和交接机制**。

---

# 13. 提示词体系

## 13.1 通用提示词外壳

```yaml
prompt_id: ""
role: ""
objective: ""
approved_inputs: []
allowed_tools: []
forbidden_assumptions: []
immutable_artifacts: []
required_checks: []
output_json_schema: ""
escalation_rules: []
source_prompts: []
modification_log: []
```

每次调用记录：

- prompt 文件哈希；
- base model ID；
- system/user message；
- temperature、`top_p`、最大输出长度；
- tool schema；
- tool traces；
- output schema 版本；
- 解析结果和重试原因。

## 13.2 Researcher Prompt 的核心约束

```text
You are the Evidence Researcher.

Your responsibility is to retrieve verifiable evidence for fields requested by the orchestrator.
You must not define states, actions, rewards, or transition functions unless the source explicitly does so.
Prefer official documentation, original papers, author repositories, and primary datasets.
Return only schema-valid claim–evidence records.
For each claim, record the exact supported TaskContract or DecisionProcessSpec path.
If sources conflict, do not resolve the conflict silently; emit a conflict record.
If evidence is insufficient, return INSUFFICIENT_EVIDENCE.
Do not use unsupported common knowledge to fill numerical parameters or domain dynamics.
```

## 13.3 Text Specification Author Prompt

```text
You are the Text Specification Author.

Convert the approved user request and evidence records into a complete TaskContract.
Do not choose MDP, POMDP, MG, or POSG at this stage.
Distinguish decision makers from controlled physical entities.
Distinguish observable variables from latent variables.
Separate hard constraints, soft objectives, termination conditions, and truncation conditions.
Every factual field must reference an evidence ID, a user statement, or an explicit unresolved assumption.
Do not invent parameter values.
Return only the required TaskContract schema.
```

## 13.4 Text Specification Critic Prompt

```text
You are the Text Specification Critic.

Audit the TaskContract for missing fields, contradictions, ambiguous actors, undefined objectives,
unsupported assumptions, inconsistent timing, incomplete information structure, and unclear episode boundaries.
Return issues as structured objects with JSON paths, severity, reason, consequence, and the smallest useful clarification question.
Do not rewrite the TaskContract directly.
Do not emit a single opaque quality score.
```

## 13.5 Formalizer Prompt

```text
You are the Decision-Process Formalizer.

Use only the locked TaskContract, approved assumptions, frozen ClassificationResult,
and its discriminated EvidenceBinding (EvidenceLedgerBinding or the authorized EvidenceOmissionBinding).
Honor the frozen in-scope classification and produce the corresponding complete DecisionProcessSpec.
State the Markov justification explicitly.
For partially observable tasks, define latent state, per-agent observations, observation kernels, and available histories.
For multi-agent tasks, define independent decision makers, joint actions, per-agent rewards, information structure, and solution concept.
Any newly required semantic assumption must be returned as a change request rather than inserted silently.
```

## 13.6 Formal Critic Prompt

```text
You are the Formal Critic.

Verify symbol closure, types, dimensions, units, probability normalization, transition totality,
reward alignment, episode semantics, Markov sufficiency, observation leakage, action legality,
and the declared game solution concept.
Generate concrete counterexamples whenever possible.
Do not write implementation code.
Do not approve a specification solely because it is syntactically complete.
```

## 13.7 Environment Developer Prompt

```text
You are the Environment Developer.

Implement the locked DecisionProcessSpec without changing its semantics.
Read the frozen ImplementationPlan and its approved reuse-candidate evidence.
Execute its single selected implementation mode—Reuse, Compose, or Generate—without reselecting the route.
For single-agent tasks, implement the Gymnasium API.
For multi-agent tasks, implement the appropriate PettingZoo Parallel or AEC API.
Keep task instances in YAML/JSON configuration rather than hard-coding scenario values.
Generate an RLlib adapter and a traceability map from specification fields to code symbols.
If implementation requires a semantic change, stop and emit a SpecificationChangeRequest.
```

## 13.8 Unit Test Agent Prompt

```text
You are the independent Unit Test Agent.

Derive tests from the locked DecisionProcessSpec rather than from the developer's explanation.
Create only schema, static, import, minimal-run, API, type, shape, dtype, bounds,
action-mask, seed-reproducibility, probability-sanity, and deterministic core invariant tests.
Do not execute property-based, metamorphic, differential, or trajectory tests;
those form the separate PublicSimulationTester gate.
Treat developer-supplied tests as untrusted supplementary evidence.
Return failing counterexamples in a machine-readable form.
```

## 13.9 Simulation Test Agent Prompt

```text
You are the Simulation Test Agent.

Generate Hypothesis property tests, metamorphic tests, generation-visible differential tests,
and normal, boundary, counterfactual, adversarial, and long-horizon public-dev trajectories
from the TaskContract and DecisionProcessSpec.
Test whether actions causally affect transitions, rewards follow the declared objective,
terminal states are reachable under intended conditions, and forbidden information does not leak.
Do not repeat schema/static/import/API/shape/dtype/bounds/action-mask/seed or deterministic core invariant checks.
Do not approve an environment merely because it runs without exceptions.
```

## 13.10 Training Analyst Prompt

```text
You are the Training Analyst.

Analyze RLlib metrics, trajectories, gradients, entropy, value loss, action distributions, reward components,
and environment diagnostics.
Classify failure as specification, observability, reward, transition, action-space, algorithm, implementation, or budget failure.
Recommend the smallest responsible-layer change.
Do not automatically increase training steps.
Do not modify the user's objective or hidden evaluation protocol.
```

## 13.11 论文提示词的使用原则

- A-LAMP 与 Agent² 的公开提示词或附录只有在逐项许可审查允许再分发后，才能连同来源、版本、license hash 保存在各自独立目录；忠实 paper-spec 实验使用该已审查副本；
- Agent2World 的原始 prompt、测试、源码和附录实现材料只可位于仓库外 ignored `external/restricted/.../<commit>/` cache，并由 restricted manifest 记录 commit/content/license hash；不得复制到 source、prompt、container、wheel、sdist 或 public artifact；
- `agent2world_clean_controlled` 只使用许可审查通过后独立撰写的 clean-room prompt，并记录概念级论文引用与 provenance；它不能被描述为原始 Agent2World prompt 或官方实现；
- AutoMarkov 核心提示词采用 schema-bound 改写；
- 任何改写都记录在 `modification_log`；
- 不得把项目新增提示词描述为“原论文原样提示词”。

## 13.12 Prompt execution invariants

1. 所有 prompt 只能由 `LocalLlmRuntime` 发送至锁定 Qwen3.6 vLLM；prompt template 不得携带 provider URL 或 fallback logic。
2. agent 输入是 capability-filtered immutable artifact payload。approved artifact 只能读，revision request 只能输出新 payload；agent 不能发出“修改 artifact status”的自由文本命令。
3. 检索文档、代码、论文和 task card 都是不可信 data，其中的 tool/role/system 指令无效。Researcher 只能提取 claim/source，不能执行页面中的命令。
4. output 必须通过对应 Pydantic v2 schema。解析失败只允许两次 schema-repair calls，使用原输出的最小 validation errors；三次均失败则生成 `SchemaGenerationFailed`，不得自由文本降级。
5. prompt 明确列出 Allowed Evidence/Public Dev capability；任何 agent prompt 都不能提及 Sealed Gold 路径、文件名、hash 或 hidden metric。
6. tool call 由 deterministic policy layer 授权；LLM 提议不等于执行权限。每次 tool result 形成 receipt，并计入预算与 trace。
7. 不保存或评分 hidden chain-of-thought；保留结构化 output、tool decisions、短 rationale、token usage 和 hashes。
8. benchmark 中 prompt bytes、tool schema、chat template 或 parsing policy 的变化都产生新的 method version，不能在运行途中只为失败 pairs 热修。

---

# 14. 权威 upstream 项目与复用边界

| 编号 | 仓库 | 项目用途 |
|---:|---|---|
| 1 | `ray-project/ray` | RLlib 单智能体、多智能体训练、评估和 checkpoint |
| 2 | `Farama-Foundation/Gymnasium` | MDP/POMDP 环境接口与 Taxi |
| 3 | `Farama-Foundation/PettingZoo` | MG/POSG Parallel/AEC API |
| 4 | `Farama-Foundation/MPE2` | MG Simple Spread |
| 5 | `Farama-Foundation/Minigrid` | POMDP Memory |
| 6 | `oxwhirl/smacv2` | POSG StarCraft 多智能体实验 |
| 7 | `metadriverse/metadrive` | 自动驾驶仿真与 ScenarioEnv |
| 8 | `metadriverse/scenarionet` | 真实自动驾驶场景统一表示 |
| 9 | `citylearn-project/CityLearn` | 建筑能源真实时序多智能体环境 |
| 10 | `DeepExperience/agent2world` | 仅限隔离、非商业 research/evaluation 的官方复现实验入口；不得 vendoring/分发 |
| 11 | `Aaron617/text2world` | Text2World PDDL benchmark |
| 12 | `nicoladainese96/code-world-models` | GIF-MCTS 与 CWMB |
| 13 | `cognitiveailab/BYTESIZED32` | 32 个 Python text-game world models |
| 14 | `tavily-ai/tavily-python` | Search/Extract/Crawl adapter；仍由 endpoint allowlist 约束 |
| 15 | `aiplan4eu/unified-planning` | OOD PDDL 解析、构造、转换和 planner interface |
| 16 | `google-deepmind/open_spiel` | OOD extensive-form/game-theory 表示、算法与分析 |
| 17 | `pydantic/pydantic` | v2 strict models、discriminated union 与 JSON Schema |
| 18 | `vllm-project/vllm` | 本地 Qwen3.6 OpenAI-compatible inference runtime |
| 19 | `QwenLM/Qwen3.6` | 唯一生成模型的官方信息、权重与使用说明 |
| 20 | `pytorch/pytorch` | RLModule、Qwen/vLLM 所需张量与神经网络后端 |
| 21 | `SwanHubX/SwanLab` | offline-first 训练与实验可视化 |
| 22 | `hiyouga/LlamaFactory` | future Agent2World-style SFT runner；当前 deferred、不安装 |

## 14.1 仓库固定格式

不应将全部外部仓库复制进核心源码。使用 manifest 管理：

```yaml
repository: "https://github.com/..."
commit: ""
release: ""
purpose: ""
license: ""
license_file_hash: "sha256:..."
redistribution_policy: "permitted | download_only | research_evaluation_only | prohibited"
install_mode: "pip | git_submodule | external_cache | dataset_download"
dependency_profile: ""
data_assets: []
checksums: []
citation: ""
```

许可允许的 reference 下载至：

```text
.cache/references/<repository_name>/<commit>/
```

实验只能使用锁定 commit 或 release，不能依赖不断变化的 `main` 分支。

`research_evaluation_only` 资源使用独立 ignored root `external/restricted/<repository>/<commit>/` 和独立 profile，不能进入 `.cache/references`、container layer、wheel、source distribution 或 public artifact。manifest 记录不是再分发授权；license 不明确时默认 `prohibited`。

## 14.2 Hugging Face 资源

可纳入：

- `EvolventAI/text2world`；
- `thuml/bytesized32-world-model-cot`；
- `thuml/bytesized32-world-model-sft`。

这些 Hugging Face 项目的许可分别核验，不构成运行 Agent2World SFT 的授权，也不改变该 SFT 当前 deferred 的状态。

所有数据必须记录：

- dataset repository；
- revision；
- split；
- sample count；
- license；
- checksum；
- preprocessing script commit。

---

# 15. 30 篇必须阅读和对齐的论文

## 15.1 自动形式化、环境生成和 RL 自动化

1. **A-LAMP: Agentic LLM-Based Framework for Automated MDP Modeling and Policy Generation**
2. **Agent²: An Agent-Generates-Agent Framework for Reinforcement Learning Automation**
3. **Agent2World: Learning to Generate Symbolic World Models via Adaptive Multi-Agent Feedback**
4. **Automated Generation of MDPs Using Logic Programming and LLMs for Robotic Applications**
5. **Operator Theory-Driven Autoformulation of MDPs for Control of Queueing Systems**
6. **ARLO: A Framework for Automated Reinforcement Learning**

## 15.2 Symbolic/code world model 与环境生成

7. **Text2World: Benchmarking Large Language Models for Symbolic World Model Generation**
8. **WorldCoder, a Model-Based LLM Agent: Building World Models by Writing Code and Interacting with the Environment**
9. **Generating Code World Models with Large Language Models Guided by Monte Carlo Tree Search**
10. **ByteSized32: A Corpus and Challenge Task for Generating Task-Specific World Models Expressed as Text Games**
11. **AgentGen: Enhancing Planning Abilities for Large Language Model Based Agents via Environment and Task Generation**
12. **EUREKA: Human-Level Reward Design via Coding Large Language Models**
13. **Text2Reward: Reward Shaping with Language Models for Reinforcement Learning**
14. **ReAct: Synergizing Reasoning and Acting in Language Models**

## 15.3 MDP、POMDP、MG、POSG 理论基础

15. **Planning and Acting in Partially Observable Stochastic Domains**
16. **Markov Games as a Framework for Multi-Agent Reinforcement Learning**
17. **Dynamic Programming for Partially Observable Stochastic Games**
18. **The Complexity of Decentralized Control of Markov Decision Processes**

## 15.4 RL 算法、接口与实验环境

19. **Proximal Policy Optimization Algorithms**
20. **Human-Level Control through Deep Reinforcement Learning**
21. **Soft Actor-Critic: Off-Policy Maximum Entropy Deep Reinforcement Learning with a Stochastic Actor**
22. **RLlib: Abstractions for Distributed Reinforcement Learning**
23. **OpenAI Gym**
24. **PettingZoo: Gym for Multi-Agent Reinforcement Learning**
25. **MiniGrid & MiniWorld: Modular & Customizable Reinforcement Learning Environments for Goal-Oriented Tasks**
26. **Multi-Agent Actor-Critic for Mixed Cooperative-Competitive Environments**
27. **The StarCraft Multi-Agent Challenge**
28. **SMACv2: An Improved Benchmark for Cooperative Multi-Agent Reinforcement Learning**
29. **MetaDrive: Composing Diverse Driving Scenarios for Generalizable Reinforcement Learning**
30. **CityLearn: Standardizing Research in Multi-Agent Reinforcement Learning for Demand Response and Urban Energy Management**

## 15.5 阅读输出要求

每篇论文生成结构化笔记：

```yaml
paper_id: ""
title: ""
version: ""
primary_problem: ""
mathematical_objects: []
inputs: []
outputs: []
agents_or_modules: []
algorithms: []
data_and_benchmarks: []
training_setup: []
evaluation_metrics: []
ablations: []
official_code: ""
reusable_components: []
limitations: []
implications_for_automarkov: []
verified_claims: []
```

禁止仅写摘要式笔记；必须将论文算法、实验、数据和开源代码映射至具体开发任务。

---

# 16. 代码目录结构

```text
AutoMarkov/
├── pyproject.toml
├── uv.lock
├── containers/
│   ├── core.Dockerfile
│   └── fixed_commit_runner.Dockerfile
├── src/automarkov/
│   ├── public/
│   │   ├── compiler.py
│   │   ├── artifact_repository.py
│   │   ├── local_llm_runtime.py
│   │   ├── evidence_gateway.py
│   │   ├── execution_sandbox.py
│   │   └── environment_runtime.py
│   ├── schemas/
│   │   ├── task_contract.py
│   │   ├── decision_process.py
│   │   ├── artifact.py
│   │   ├── events.py
│   │   ├── evidence.py
│   │   ├── assumptions.py
│   │   ├── issues.py
│   │   ├── remote_env.py
│   │   ├── execution.py
│   │   └── traceability.py
│   ├── artifacts/
│   │   ├── canonical.py
│   │   ├── content_store.py
│   │   ├── event_log.py
│   │   ├── dag.py
│   │   └── projector.py
│   ├── orchestration/
│   │   ├── state_machine.py
│   │   ├── reducer.py
│   │   ├── routing.py
│   │   ├── gates.py
│   │   └── budgets.py
│   ├── agents/
│   │   ├── researcher.py
│   │   ├── text_author.py
│   │   ├── text_critic.py
│   │   ├── classifier.py
│   │   ├── formalizer.py
│   │   ├── formal_critic.py
│   │   ├── developer.py
│   │   ├── unit_tester.py
│   │   ├── simulation_tester.py
│   │   └── training_analyst.py
│   ├── llm/
│   │   ├── qwen36_manifest.py
│   │   ├── vllm_process.py
│   │   ├── canary.py
│   │   └── completion_trace.py
│   ├── evidence/
│   │   ├── tavily_gateway.py
│   │   ├── key_leases.py
│   │   ├── endpoint_policy.py
│   │   ├── evidence_ranker.py
│   │   ├── conflicts.py
│   │   └── local_index.py
│   ├── sandbox/
│   │   ├── safe_expression.py
│   │   ├── local_sandbox.py
│   │   ├── fixed_commit_runner.py
│   │   └── attestation.py
│   ├── formal/
│   │   ├── classifier.py
│   │   ├── validators.py
│   │   ├── markov_audit.py
│   │   ├── information_audit.py
│   │   └── model_checking.py
│   ├── synthesis/
│   │   ├── selector.py
│   │   ├── reuse.py
│   │   ├── compose.py
│   │   ├── gymnasium_backend.py
│   │   ├── pettingzoo_backend.py
│   │   └── ood/
│   │       ├── openspiel_handoff.py
│   │       └── pddl_handoff.py
│   ├── environment/
│   │   ├── binding.py
│   │   ├── remote_client.py
│   │   ├── wire_codec.py
│   │   └── capabilities.py
│   ├── validation/
│   │   ├── api_checks.py
│   │   ├── property_tests.py
│   │   ├── metamorphic_tests.py
│   │   ├── differential_tests.py
│   │   ├── trajectory_tests.py
│   │   └── mutation_tests.py
│   └── training/
│       ├── runner.py
│       ├── algorithm_config.py
│       ├── connectors.py
│       ├── recurrent_modules.py
│       ├── centralized_critic.py
│       ├── policy_mapping.py
│       ├── checkpointing.py
│       └── diagnostics.py
├── profiles/
│   ├── core/
│   ├── authoring/
│   ├── llm-qwen36-vllm/
│   ├── retrieval-tavily/
│   ├── runner-control/
│   ├── rllib-core/
│   ├── rllib-taxi-synthesis/
│   ├── sealed-env-taxi-gold/
│   ├── sealed-evaluator-rllib/
│   ├── env-minigrid/
│   ├── env-mpe2/
│   ├── env-smacv2/
│   ├── env-metadrive/
│   ├── env-citylearn/
│   ├── ood-openspiel/
│   ├── ood-pddl/
│   └── replication-agent2world-restricted/
├── prompts/
│   ├── core/
│   ├── alamp_replication/
│   ├── agent2_replication/
│   └── agent2world_clean_controlled/
├── configs/
│   ├── models/
│   ├── retrieval/
│   ├── training/
│   ├── access_control/
│   └── experiments/
├── benchmarks/
│   ├── core/
│   │   ├── taxi_mdp/
│   │   ├── memory_pomdp/
│   │   ├── mpe2_full_state_mg/
│   │   ├── smacv2_posg/
│   │   ├── metadrive_pomdp/
│   │   └── citylearn_posg/
│   └── paper_replications/
│       ├── alamp/
│       ├── agent2/
│       └── agent2world/
├── tests/
│   ├── contract/
│   ├── unit/
│   ├── integration/
│   ├── end_to_end/
│   ├── security/
│   └── mutation/
├── references/
│   └── manifest.yaml
├── schemas/
│   └── generated_json_schema/
├── scripts/
│   ├── fetch_references.py
│   ├── prepare_data.py
│   ├── run_core_benchmarks.py
│   ├── run_replications.py
│   ├── aggregate_nested_results.py
│   └── verify_release_bundle.py
├── external/
│   └── .gitignore
└── artifacts/
    ├── .gitignore
    └── public_reports/
```

每个 `profiles/<name>/` 都包含自己的 `pyproject.toml`、`uv.lock`、container recipe、SBOM 和 smoke contract。Allowed Evidence、Public Dev 和 Sealed Gold 是 deployment mounts，不出现在源码树；尤其不得创建一个看似空的 `sealed_gold/` 目录诱导本地开发绕过 capability check。

根目录 `artifacts/` 默认完整忽略。发布功能实现后的唯一目标白名单是 `artifacts/public_reports/<experiment_id>/confirmatory_report.md`、`redacted_manifest.json`、`tables/primary_outcomes.csv`、`tables/secondary_outcomes.csv` 与 `tables/protocol_deviations.csv`；目录本身只用于到达这些精确文件。发布器只能生成已通过 secret、gold、license 与 provenance gate 的 regular files，并拒绝 symlink、非规范 experiment ID、其他文件名和额外层级。

publisher 有两个显式输入：作为唯一内容输入、`extra="forbid"` 的 strict/frozen `PublicReportBundle`，以及作为必需证明输入的 signed `RedactionAttestation`；attestation 不提供任何可渲染内容。`redacted_manifest.json` 的 closed schema 只允许：schema/version、规范 experiment ID、public source commit、preregistration/version、public runtime/method IDs、固定 outcome/count/deviation summaries、四份 sibling report 的 allowlisted relative path 与 SHA-256，以及 `sealed-v1:sha256:<64hex>` commitments。sealed principal 构造 `extra="forbid"` 的 `SealedCommitmentPreimage`：`{"domain":"AutoMarkov-Sealed-v1","kind":<registered literal>,"nonce_b64url":<32 random bytes, unpadded base64url>,"sealed_envelope":<CanonicalJsonValue object>}`；`kind` 只允许 `clarification_oracle`、`gold_score_calibration`、`gold_environment`、`gold_adapter` 或 `hidden_evaluation`。commitment 是整个 preimage 的 RFC 8785 JCS bytes SHA-256，并编码为上述 `sealed-v1` 字符串；nonce/preimage 仅保留于 sealed principal。这一 closed object framing 是唯一算法，禁止裸字符串拼接。public schema 在任意深度禁止 `identity`、`artifact_id`、`payload_hash`、`content_hash`、`answer`、`expected`、`nonce`、`path`、`uri`、`url`、`locator`、`credential`、`secret`、`trace` 及未知字段。public file hash 只可位于 typed `report_files` entry，sealed commitment 只可位于 typed `sealed_commitments` entry；不能互换、改名或放入自由字段。

隔离 redactor 在受限域内读取原始结果，并把 taint registry 每个条目分为 typed provenance 和 byte-pattern 两层。第一层对 sealed identity/hash/nonce/locator/credential/answer 使用字段级 lineage、tainted wrapper、closed public schema 与 fixed-renderer AST 检查；任何这些值被当作可渲染内容或从受限字段流入 public bundle 均 fail closed。第二层只对确定性高熵 token 做 raw/exact substring 及常见 base64/hex encoding 扫描；scanner policy 固定高熵条件为至少 16 UTF-8 bytes 且经验 Shannon entropy 至少 3.0 bits/byte，并将分类器版本/阈值纳入 policy hash。低熵/short answer（如 `0`、`1`、`yes`）绝不作全局 substring pattern，以免正常计数和指标误拒；它们只由第一层的结构/provenance 检查阻断。对 `SHA256(raw_answer)` 的低熵字典攻击使用独立 hash-set 检查，不与 raw substring 规则共用 matcher；裸 64-hex 仍只能出现在 typed public-file-hash 位置，sealed commitment 必须是 nonce-backed `sealed-v1` 格式。通过后 redactor 只输出 `PublicReportBundle` 与 signed `RedactionAttestation`，attestation 只含 source commitments、bundle hash、redactor identity/version、scanner-policy hash 与 pass verdict，不含 tainted values。

publisher 不得挂载 sealed/oracle/evaluator roots 或 taint registry，只验证 attestation 签名/binding、schema、文件/列 allowlist、regular-file/no-symlink、secret/path patterns，再从 bundle 的固定模板和固定 columns 渲染 Markdown/CSV；它不接受自由文本片段、任意列或 raw artifact metadata。未知字段、未知列、taint 命中、裸 64-hex 出现在非 public-file-hash 位置、commitment 格式/域不匹配或 attestation mismatch 均 fail closed。测试必须包含每种禁止字段、低熵 answer 通过受限字段/renderer 注入的负例、低熵 answer 裸 SHA-256 的独立字典攻击负例、高熵 token 的 raw/base64/hex 负例、path/URI、symlink、额外文件/列和 Markdown 插值负例；正例必须证明含 `0`、`1`、`yes` 及合法 count/metric 的 fixed report 不会因 raw substring 误拒。

当前 bootstrap 没有可执行发布扫描器，因此不放行任何 `public_reports` 文件；首个 publisher tracer 必须把 strict schemas、isolated redactor、fixed renderers、scanner、negative tests 与精确 `.gitignore` allowlist 放在同一 commit 原子启用。`.env`、`*.key`、`*.pem`、raw tool logs、完整 run、checkpoint、trace、web capture 和 evaluator payload 始终 ignored，禁止 `git add -f` 绕过。

## 16.1 场景配置示例

```yaml
experiment_id: "automarkov_core_confirmatory_2026q3_v1"
run_id: "memory_pomdp_v1_auto_automarkov_g00"
suite_id: "memory_pomdp"
variant_id: "v1_canonical"
track: "AUTO"
method_id: "automarkov"
pair_id: "g00"
object_type: "POMDP"

environment:
  profile_id: "env-minigrid"
  profile_lock_hash: "sha256:..."
  image_digest: "sha256:..."
  package: "minigrid"
  env_id: "MiniGrid-MemoryS17Random-v0"
  repository_commit: "40-hex-sha"
  remote_env_protocol: "automarkov.remote-env.v1"
  max_episode_steps: null

observation:
  mode: "native_partial_observation"
  wrappers: []

training:
  backend: "rllib"
  api_stack: "rl_module_learner_env_runner_connector_v2"
  algorithm: "PPO"
  rl_module: "MemoryRecurrentRLModule"
  connector_pipeline: "memory_history_v1"
  framework: "torch"
  train_batch_size_per_learner: 4096
  gamma: 0.99
  num_env_runners: 2
  num_learners: 0
  num_gpus_per_learner: 0
  rl_seeds: [1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009, 1010]
  stop:
    environment_steps: 1000000
  checkpoint_round_trip: true

evaluation:
  store_capability: "sealed_gold_evaluator_only"
  episodes_per_seed: 100
  deterministic_actions: true
  metrics:
    - "episode_return"
    - "success_rate"
    - "episode_length"
    - "recurrent_gain"

logging:
  swanlab_mode: "offline"
  raw_trajectory_publish: false
```

`experiment_id` 是整个预注册矩阵共享的 identity；`run_id` 才编码 suite/variant/track/method/pair。`required_implementation_route` 不是本 YAML 的 caller input：runner 必须从已验证 signed suite manifest 按 `suite_id` 唯一解析，并拒绝任何 CLI/config override 或与 manifest 不一致的投影。

---

# 17. 实现能力门与验收标准

本节是 project spec 内的能力依赖图和验收合同，不是本地 issue backlog，也不表示任何 gate 已完成。实际 tracer tickets、blocking relationships 与状态只发布到配置的 GitHub Issues。能力门顺序为 `G0 → GA/GB/GC/GD → GE/GF → GI/GG → GH → GJ`；GB、GC、GD 在 GA manifests 可用后可并行，GI 与 GG 在各自的 GE/GF blocker 闭合后可并行，但 publication-grade GH 必须同时等待 GI 的 FixedCommitRunner/attestation gate 和 GG 的训练/export gate。每个 gate 必须由 immutable artifacts、targeted tests 和 gate report 证明，不以“代码已写”作为完成。

## G0：deep seams、artifact repository 与状态机

任务：

- 定义 `Compiler`、`ArtifactRepository`、`LocalLlmRuntime`、`EvidenceGateway`、`ExecutionSandbox`、`EnvironmentBinding`/`TrainingRunner` 六条 public protocols；
- 实现 immutable content-addressed payload、`exact|payload_bound` closed parent contracts、artifact DAG、UUIDv7 strict-discriminated `EventRecord` union、append-only hash chain 和 specified-head projector；
- 实现第 4.4 节 closed `LifecycleCommand` union、所有状态、合法转换、回边、WAITING/BLOCKED/BUDGET reducer、terminal/post-terminal semantics 与 replacement/clarification cross-run transaction；
- 实现 `ProcessExecutionTerminalRecord`→`TerminalResult`→`RunAuditProjection` 的无环单向 provenance，terminal CAS 与每次 post-terminal projection 更新均满足第 4.4 节原子性。

验收：

- contract tests 同时通过 in-memory fake 与 production adapter；
- mutation 后的 payload/hash、缺父节点、cycle、exact parent-type tuple mismatch、payload-bound ID/hash/type/cardinality/envelope mismatch、重复或缺口 sequence 和非法状态跳转均被拒绝；
- root 只能以 signed `RunCreated` sequence 0 和全零 sentinel 建立；错 UUID version/variant/text/time window、非闭合 event/command branch、extra field、错 signature/hash 顺序、错 previous hash 与 replay 全部 fail closed；
- approved artifact 的任何修订产生新 ID，旧 payload bytes 不变；event log 从 sequence 0 到 caller 指定 verified head 可重建 byte-identical `RunView`，且 stale/mismatched head 不得退化为 current projection；
- WAITING 只能经 matching identity/gate 恢复，BLOCKED 只能经 registered external authority 解除，BUDGET 只能在可重算耗尽证明下 terminal；terminal 后的非允许 event、任何 transition 与 in-place continuation 均被拒绝；
- fault-injection 在 terminal artifact/event/head/root-projection 任意写点及 cross-run/clarification transaction 任意写点失败时不留部分 provenance、orphan child 或被取消但无 `TerminalResult` 的 parent；
- core 不直接 import vLLM、Tavily、Ray 或 suite-specific package。

## GA：参考项目与数据固定

任务：

- 建立所有仓库、模型、数据、planner 和 SC2 assets 的 commit/revision/release/license/checksum manifest；
- 实现自动下载或安装脚本；
- 验证六个核心环境可独立运行；
- 建立所有隔离 dependency profiles；
- 物理和 capability 隔离 Allowed Evidence、Public Dev 与 Sealed Gold。

验收：

- 所有资源可由 manifest 重建；
- 不存在未记录来源的数据；
- 所有依赖具有许可证记录；
- CI 能检查 commit、checksum、profile lock 和 image digest；
- Agent2World restricted repository 不出现在 release tree/image，license hash 与 fixed commit 可审计；
- generator principal 无法 stat/open Sealed Gold，sealed evaluator 返回的只有签名 aggregate output。

## GB：TaskContract 与 HITL

任务：

- Pydantic v2 strict/frozen `TaskContract` schema；
- Text Author 与 Text Critic；
- IssueLedger；
- 通用 `ClarificationRequiredResult`、实验专用 `ExperimentClarificationRequiredResult`、`ClarificationRequested` event、`CLARIFICATION_REQUIRED` terminal projection，以及 terminal result/runner attestation 后独立的 sealed `ClarificationEvaluationRequest`/`ClarificationEvaluationVerdict`/outcome audit chain；
- 多轮澄清；
- 人类批准与哈希锁定。

验收：

- 缺失决策主体、目标、观测或 episode 边界时不得进入数学建模；
- `TaskContract` 的 unknown/missing key、旧/未知 schema version、mapping keyset mismatch、duplicate owner/ID、非法 timing branch、message recipient/sender/lag mismatch、空白成功/episode/validation 条目、high/critical unresolved question，以及既有/伪造/复制的 Pydantic model 在 approval 前 fail closed；schema→JSON→schema round-trip 和 nested mutation 反例证明 exact bytes 可定址且深冻结；
- `AUTO` clarification cell 的 result→terminal event/result→execution attestation→clarification request/verdict→public outcome lineage 可机械复核；正确 abstention 不进入 E2E missing/failure，猜测、产生 formal/environment artifact、访问 oracle 或 post-terminal evaluation failure 必须按预注册 branch令 `SafeClarificationRequired=0`且不产生生成回边；
- 每项事实均可追溯到用户陈述或 evidence ID；
- 用户确认后工件不可被下游智能体静默修改。

## GC：四类对象形式化

任务：

- `kind` discriminated union 的四种 strict/frozen schema；
- object classifier；
- Markov audit；
- observation leakage audit；
- solution concept；
- strict variable-domain、joint observation、typed message process、history reward/message、stochastic reward 与 joint-reward dependency、AEC turn、objective/constraint/risk validators；
- validation-level claims；
- OpenSpiel/PDDL OOD router。

验收：

- 人工构造的 MDP/POMDP、MG/POSG 正反例正确分类；
- 不确定时允许 abstain；
- 对隐藏历史变量、全局状态泄漏和多主体伪分类具有反例测试；
- 单智能体 agent cardinality、MG/POSG 至少双主体，以及所有 per-agent mapping/AEC owner 的 exact keyset 具有 missing、extra、duplicate 与 empty-ID 反例测试；
- untagged union、unknown/old schema version、implicit coercion、旧式自由 variable dtype/domain、tensor dtype/shape/bounds mismatch、categorical duplicate value、缺 joint observation/reward/message delivery kernel、message keyset/recipient/sender/lag leakage、joint-reward group/member/marginal mismatch、joint-reward 空 support 或重复 `conditional_on`、错误 AEC reward accumulation、随机奖励缺支持集均 fail；
- 四个 branch 的 raw-Python 与 raw-JSON round-trip byte-semantics 一致；空/单主体 MG/POSG、空 agent ID、ghost/missing per-agent mapping、空 objective、ghost/incomplete objective owners、AEC/null mismatch、partial MG full-state projection 与 centralized-field leakage 均在 model validation 时 fail；
- public ingress 对 validated branch、nested model、`model_construct()` 与 `model_copy(update=...)` 一律拒绝；只有从认证 canonical bytes duplicate-aware parse 得到的 raw tree 可以重建。
- PDDL parse/write/compile/solve/validate 与 OpenSpiel game-type/playthrough/utility contract 有独立 profile tests。

## GD：Tavily 证据检索

任务：

- query planning；
- Search/Extract/Crawl；
- `include_answer=false` 和 endpoint allowlist；
- 29-key atomic lease/rotation/cooldown/quarantine；
- source ranking；
- conflict detection；
- caching；
- provenance；
- leakage denylist。

验收：

- 任何写入 spec 的外部事实均可回溯到 evidence ID 或用户确认；
- 来源冲突不会被静默合并；
- benchmark gold source 在 synthesis mode 下不可访问；
- 401 将 slot 标记为 `INVALID` 并轮换；403 作为 request/permission failure fail closed，不瞬态重试且不烧毁 slot；429 严格遵守 `Retry-After`、full-jitter cooldown 并轮换；432/433 将 slot 标记为 `EXHAUSTED`；网络错误与 5xx 只按冻结上限重试；
- concurrency/crash tests 证明 slot 不超租、secret 不出现在任何日志或 artifact；仅 29 slots 或其全部账户均有 provider receipt 证明耗尽时才进入 `BUDGET_EXHAUSTED`，`EXHAUSTED+INVALID` 且无全账户耗尽证明时必须进入带状态计数和 credential authority 的 `BLOCKED`，两者都 fail closed。

## GE：环境合成

任务：

- Reuse/Compose/Generate selector；
- Gymnasium backend；
- PettingZoo backend；
- wrapper generation；
- profile-specific workers 与 `RemoteEnv` codec；
- sandbox。

验收：

- 场景实例通过 YAML/JSON 注入，不在框架中硬编码；
- 环境、场景、主体、算法和实验配置解耦；
- 复杂官方模拟器只做适配，不重写核心引擎；
- core 环境绑定只通过 handshake/profile hash/commit/capability，禁止 pickle；
- MPE2 主轨逐元素复用官方 `state()`，native POSG ablation 的 actor 无 global-state capability。

## GF：验证体系

任务：

- API tests；
- property tests；
- metamorphic tests；
- differential tests；
- trajectory tests；
- mutation tests；
- independent tester agents。

验收：

- sealed-evaluator owner 在预注册前对 gold 环境的隔离副本注入转移、奖励、观测泄漏和终止错误，hidden suite 能够稳定检出；该 evaluator-validation 流不属于 candidate generation run；
- gold mutation report 和最小反例永久留在 sealed evaluator 域，生成/开发/训练/修复 principal 只能获得 public-dev 测试的最小反例；public readiness 只显示不含 sealed identity/hash/nonce/locator 的 signed attestation 和 aggregate coverage；
- 行为级测试与静态测试分开报告；
- 六个 validation levels 有独立证据，不把 executable pass 升格为 behavioral/oracle/formal；
- `RemoteEnv` duplicate step、AEC turn、随机奖励分布、reward/message history 与 crash recovery contract tests 全部通过。

## GG：RLlib 训练与诊断

任务：

- 新 API stack 的 PPO、stateful recurrent PPO、CTDE-PPO；
- DQN/SAC/TD3 paper replication；
- RLModule/MultiRLModule、ConnectorV2、EnvRunner、LearnerGroup 与 policy/module mapping；
- checkpoint；
- evaluation；
- failure diagnosis。

验收：

- 六个 suite 的 candidate `EnvironmentBinding` 可由统一脚本训练、恢复并导出 manifest-bound weights-only safetensors；gold environment 只允许独立 sealed evaluator 读取，并只用于评估该导出策略；
- actor/critic 信息边界可测试；
- checkpoint 包含配置、模型和环境版本；
- 训练失败可归类，而非仅返回“未收敛”；
- repo 中无新 legacy ModelV2/Policy/RolloutWorker config；actor-only export graph 不含 critic fields；
- CPU-first resource tests 和 10-seed checkpoint round-trip/evaluation 可重放。

## GH：核心实验与论文复现

任务：

- 六个 core suites、每 suite 五个 frozen variants；
- intention-to-run ledger 覆盖两轨、六方法和每 cell 经 design-power gate 冻结的 `n_pair` 个 pair slots；仅 `RUN` cells 执行 attempts，每个有效环境 10 个 RL seeds，`N/A` slots 只保留 eligibility record；
- 三项论文 replication suite；
- baselines；
- ablations；
- nested paired statistics、Holm families、5% policy non-inferiority margin、one-sided 97.5% CI；
- aggregated report。

验收：

- 一条命令可重现实验配置；
- 不重训练时可从 signed `PolicyExportManifest`、safetensors 与 policy-evaluation records 重建主表，并从持久化 metrics 与 seed/terminal records 重建学习曲线；raw trainer-local checkpoint 不属于交付或复现输入；
- paper-matched track 与 common-backend track 分开；
- 所有偏离论文的地方有 deviation log；
- artifact matrix 完整且无 silent drop；bootstrap 保留 suite→variant→pair→seed nesting；
- Agent2World SFT 明确显示 deferred，不计作失败或已完成，restricted upstream results 与 clean controlled variant 分表。

## GI：CPU-first 与 fixed-commit execution

任务：

- local CPU smoke/test plan 与 GPU allocation policy；
- fixed-commit remote runner、profile/image/input verification、network cutoff 和 signed attestation；
- output schema、secret/gold scan 与 content-addressed return path。

验收：

- branch/tag/dirty tree/moving ref/profile mismatch/input mismatch 全部在执行前拒绝；
- attestation 能证明 exact commit、container digest、command、inputs、seeds、resource usage 和 output hashes；
- vLLM 与 RL GPU resource 不发生未声明争用，CPU-capable suite 不无故申请 GPU；
- 远程输出可由同一 commit/profile/inputs 重放，随机任务满足预注册容差。

## GJ：开源质量

任务：

- lockfile；
- container；
- CI；
- secret scanning；
- model/data cards；
- deviation logs；
- software bill of materials；
- reproducibility report。

验收：

- 仓库不包含密钥、私有数据或未授权数据；
- 不包含未注明来源的代码；
- release 能由干净容器构建；
- 所有实验产物具有配置、版本和数据哈希；
- public bundle 不含 raw web captures、Sealed Gold、restricted Agent2World code/derivatives、private checkpoints 或 secret；
- Software/Data/Model Cards 准确区分 completed、partial、deferred、paper-spec、controlled adaptation 与 official-checkpoint evaluation。

---

# 18. 安全、许可与可复现性要求

## 18.1 API 密钥

原始材料中的 ModelScope 示例出现过明文 API token。该密钥必须立即撤销；AutoMarkov runtime 不使用 ModelScope API，因此不为它创建 replacement runtime secret。Qwen3.6 权重由批准的离线 staging 流程按 revision/hash 安装，生成阶段不访问模型托管 API。

Tavily 只接受 29 个编号 secret slots，由 deployment secret manager 注入 process environment：

```bash
export TAVILY_API_KEY_01="..."
export TAVILY_API_KEY_02="..."
# ... 恰好连续至 29
export TAVILY_API_KEY_29="..."
```

application 只构造 secret references；仅 `SecretProvider` adapter 在发送 header 时读取值：

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretRef:
    slot_id: str


tavily_slots = tuple(
    SecretRef(slot_id=f"TAVILY_API_KEY_{index:02d}")
    for index in range(1, 30)
)
```

production application 不负责解析 `.env`；local launcher 若使用 `.env`，文件必须 ignored、权限 `0600`，且只负责把值注入环境。startup 仅检查 29 个 slot 是否存在/非空，不输出长度、prefix、fingerprint 或值。任何 secret leak 都触发 revoke、incident artifact、history scan 和 affected-run invalidation。

必须配置：

```gitignore
.env
.env.*
secrets/
*.key
*.pem
artifacts/**/raw_tool_logs_with_secrets/
```

## 18.2 Secret scanning

CI 至少运行：

- Gitleaks；
- TruffleHog 或同类扫描器；
- 依赖漏洞扫描；
- 容器镜像漏洞扫描。

## 18.3 许可证

每个外部代码和数据资源记录：

- license identifier；
- 是否允许再分发；
- 是否仅允许非商业研究；
- 是否需要单独下载；
- 是否允许发布派生数据；
- 引用要求。

不满足再分发条件的数据只能提供下载脚本，不能上传至项目仓库。

Agent2World upstream code 适用 `RESEARCH / EVALUATION ONLY`，不是 permissive open-source license。它只能在非商业研究评估范围、isolated ignored profile、固定 commit 下执行；禁止公开分发代码、derivative works 或 hosted service。其 SFT 当前延期。每个 paper replication 必须分别审查 orchestration code、benchmark code、dataset、base model、checkpoint 与生成轨迹，不能用主仓库 license 代替依赖许可。

## 18.4 可复现性

每次实验记录：

```yaml
run_id: ""
suite_id: ""
variant_id: ""
track: ""
method_id: ""
pair_id: ""
rl_seed: null
git_commit: "40-hex-sha"
worktree_clean: true
execution_attestation_id: ""
container_digest: "sha256:..."
profile_ids_and_lock_hashes: {}
python_version: ""
ray_version: ""
gymnasium_version: ""
pettingzoo_version: ""
environment_repository_commits: {}
dataset_revisions: {}
llm:
  model_id: "Qwen/Qwen3.6-35B-A3B"
  model_revision: ""
  model_shard_hashes: []
  tokenizer_hash: ""
  vllm_version: ""
prompt_hashes: []
artifact_root_ids: []
event_log_head_hash: ""
evidence_snapshot_id: ""
access_ledger_id: ""
statistical_analysis_manifest_id: ""
compute:
  cpu: ""
  gpu: ""
  memory: ""
  wall_time: ""
```

## 18.5 不得做出的声明

在实验完成前，不得声明：

- “显著优于所有 baseline”；
- “完全覆盖所有决策问题”；
- “形式上证明所有环境正确”；
- “完全复现论文”，若实际只完成部分任务或使用替代超参数；
- “RLlib 原生实现 MAPPO”，若实际为自定义 CTDE-PPO；
- “官方代码复现”，若只能基于论文重新实现；
- “open source Agent2World integration”，若上游许可仍限制研究评估、再分发和 hosted service；
- “完成 Agent2World SFT”，在延期 gate 未解除时；
- “local LLM”，若请求实际离开自托管 vLLM trust boundary；
- “冻结 `n_pair` paired generations/10 RL seeds 完成”，若 design-power report、artifact matrix 存在 silent drop、替代 pair/seed 或未报告失败。

---

# 19. 最终系统定义

AutoMarkov 不是将 A-LAMP、Agent² 与 Agent2World 机械顺序拼接起来的 workflow，而是一个以以下工件链为核心的决策过程编译系统：

$$
\text{TaskContract}
\longrightarrow
\text{DecisionProcessSpec}
\longrightarrow
\text{Executable Environment}
\longrightarrow
\text{Verified Policy}.
$$

系统依赖：

- 自托管 Qwen3.6-35B-A3B vLLM；
- Tavily Search/Extract/Crawl 与 29-key safe rotation；
- immutable artifacts、append-only events、DAG 与完整状态机；
- 六条 deep public seams 与 Typed Blackboard 多智能体协作；
- 两次人机语义确认；
- 最近致因回退；
- strict Pydantic v2 MDP/POMDP/MG/POSG discriminated union；
- Gymnasium/PettingZoo、dependency profiles 与 `RemoteEnv`；
- 现代 RLlib 新 API stack；
- 属性、变形、差分和轨迹测试；
- OpenSpiel/PDDL OOD handoff；
- CPU-first fixed-commit execution；
- 三层 evidence/dev/gold 隔离和全链路可追溯 manifest。

核心实验保持六项：

1. MDP：Gymnasium Taxi；
2. POMDP：MiniGrid Memory；
3. MG：MPE2 Simple Spread full-state adaptation；其 native local-observation POSG 是 ablation；
4. POSG：SMACv2；
5. 真实复杂单智能体场景：MetaDrive + ScenarioNet；
6. 真实复杂多智能体场景：CityLearn。

同时通过独立 replication suite 覆盖：

- A-LAMP 的自动 MDP 建模、环境编码和策略生成；
- Agent² 的任务建模与算法优化闭环；
- Agent2World 的许可允许 inference-time Deep Research、代码/PDDL world model 生成和 Unit/Simulation Testing；SFT 只保留 future contract，当前延期。

项目最终交付不是“一个自动写环境的 Agent”，而是一个能够对需求、证据、数学对象、环境实现、测试与策略训练进行统一管理和分级验证的工程化研究系统。intention-to-run grid 是 6 suites × 5 variants × 2 tracks × 6 methods × `n_pair` generation-pair slots，其中 `n_pair` 由无 confirmatory/sealed outcome 输入的 pre-generation design-power gate冻结；只有 manifest 标记 `RUN` 的 cells 实际生成。co-primary outcome matrix 固定为 `AUTO/v1..v4` 的 24 个等权 strata，其中只有有效 candidates 训练恰好 10 个 RL seeds，无效 candidates 在 gate-aware outcome 中保留为 0。`AUTO/v5` 只评价 safe clarification，`HITL-ORACLE/v1..v5` 属于 secondary/mechanism layer。ReAct co-primary gates 只由预注册 finite-benchmark paired stratified-bootstrap confidence bounds 判定；Holm step-down 仅用于第 11.10.5 节冻结的 secondary families。

---

# 20. 关键来源

以下链接用于核对本文档中的核心框架、接口和相关工作描述。实际开发和实验应进一步固定论文版本、官方仓库 commit 与数据 revision。

## 20.1 核心相关工作

- [A-LAMP: Agentic LLM-Based Framework for Automated MDP Modeling and Policy Generation](https://arxiv.org/abs/2512.11270)
- [Agent²: An Agent-Generates-Agent Framework for Reinforcement Learning Automation](https://arxiv.org/abs/2509.13368)
- [Agent2World: Learning to Generate Symbolic World Models via Adaptive Multi-Agent Feedback](https://arxiv.org/abs/2512.22336)
- [Agent2World project page](https://agent2world.github.io/)

## 20.2 环境与训练框架

- [Qwen3.6 official repository](https://github.com/QwenLM/Qwen3.6)
- [vLLM documentation](https://docs.vllm.ai/)
- [PyTorch documentation](https://pytorch.org/docs/stable/)
- [Pydantic discriminated unions](https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions)
- [RFC 9562: Universally Unique IDentifiers (UUIDs)](https://www.rfc-editor.org/rfc/rfc9562.html)
- [RFC 8785: JSON Canonicalization Scheme (JCS)](https://www.rfc-editor.org/rfc/rfc8785.html)
- [RLlib documentation](https://docs.ray.io/en/latest/rllib/)
- [RLlib key concepts: EnvRunner, RLModule and Learner](https://docs.ray.io/en/latest/rllib/key-concepts.html)
- [RLlib ConnectorV2 pipelines](https://docs.ray.io/en/latest/rllib/connector-v2.html)
- [RLlib multi-agent environments](https://docs.ray.io/en/latest/rllib/multi-agent-envs.html)
- [Gymnasium documentation](https://gymnasium.farama.org/)
- [PettingZoo documentation](https://pettingzoo.farama.org/)
- [MiniGrid documentation](https://minigrid.farama.org/)
- [MPE2 documentation](https://mpe2.farama.org/)
- [SMACv2 repository](https://github.com/oxwhirl/smacv2)
- [MetaDrive repository](https://github.com/metadriverse/metadrive)
- [ScenarioNet repository](https://github.com/metadriverse/scenarionet)
- [CityLearn repository](https://github.com/citylearn-project/CityLearn)

## 20.3 检索与 OOD 工具

- [Tavily Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search)
- [Tavily Extract API](https://docs.tavily.com/documentation/api-reference/endpoint/extract)
- [Tavily Crawl API](https://docs.tavily.com/documentation/api-reference/endpoint/crawl)
- [Tavily rate limits](https://docs.tavily.com/documentation/rate-limits)
- [OpenSpiel documentation](https://openspiel.readthedocs.io/en/latest/)
- [Unified Planning operation modes](https://unified-planning.readthedocs.io/en/latest/operation_modes.html)
- [Unified Planning PDDLReader](https://unified-planning.readthedocs.io/en/latest/api/io/PDDLReader.html)

## 20.4 Agent2World 相关基准与代码

- [Agent2World repository](https://github.com/DeepExperience/agent2world)
- [Agent2World restricted license at audited commit](https://github.com/DeepExperience/agent2world/blob/1330f3cde9509f05d204a255f0f7f43208515dce/LICENSE)
- [Text2World repository](https://github.com/Aaron617/text2world)
- [Code World Models repository](https://github.com/nicoladainese96/code-world-models)
- [ByteSized32 repository](https://github.com/cognitiveailab/BYTESIZED32)

---

**文档结束。**
