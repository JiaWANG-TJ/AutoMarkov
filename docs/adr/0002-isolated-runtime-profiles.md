# Isolated runtime profiles

## Status

Accepted

## Context

AutoMarkov 同时依赖 agent authoring、symbolic planning、单智能体与多智能体 RL、复杂 simulator、论文复现和 sealed evaluation。它们的 Python、native library、CUDA、Ray、Gymnasium/PettingZoo 与 benchmark 约束并不保证兼容；把全部依赖装入一个环境会使解析结果、复现性和故障归因随安装顺序漂移。

## Decision

每个 `ProcessExecution`（coordinator、authoring worker、RL trainer、environment worker 或 sealed evaluator）必须绑定且只能绑定一个 immutable `RuntimeProfile`。profile 记录 lockfile 或 environment manifest、Python 与关键 package 版本、external repository commit、dataset revision、model identity、container digest、hardware contract 和可用 capability；在比较开始前冻结并以稳定 `runtime_profile_id` 引用。一个逻辑 `Run` 可以编排多个 process executions，但其 `RunManifest` 必须在启动前冻结完整 profile graph、每个节点的 profile identity、允许的边、protocol version 与 capability。

核心 authoring、planning、RL training、复杂 suite replication 与 sealed evaluation 按依赖兼容性拆分 profile，不追求单一 universal environment。跨 profile 的持久化交接只交换 schema-versioned、content-hashed immutable `Artifact`。在线消息只允许 run manifest 中的 closed edge kinds：`LocalLlmRuntime` inference、`EvidenceGateway` retrieval、`ClarificationBroker`、`ExperimentApprovalPolicy`、`RemoteEnv`、`SealedEvaluator` 和 `FixedCommitRunner` control/attestation；每条 edge 必须冻结 source/target principal/profile、protocol/version、transport authentication、message schema、capability、budget/egress policy 和 transcript-hash contract。`RemoteEnv` 是唯一高频 environment step-stream edge。任何边都不得共享另一个 profile 的 editable checkout、site-packages、Python/Ray 进程内对象、pickle 或 cloudpickle payload。

`RemoteEnv` 只使用 run manifest 冻结 codec/schema hash 的单一 canonical frame codec；合法 source/target topology 仅为隔离的 `trainer→environment-worker` 与 evaluator-owned default-deny namespace 内的 `sealed-evaluator→sealed-environment-worker`，两者不得复用 principal、grant、session 或 worker。替代 metadata/tensor codec、caller-selected encoding、未登记 codec version 或 trainer/sealed principal 交叉均 fail closed。

普通 RLlib checkpoint 只是原 frozen trainer profile 内 ignored、run-local 的 recovery state；同 profile 的一次性 export execution 可读取它，跨 profile、packaging、publisher 和 sealed evaluator 只接收 content-addressed weights-only safetensors、`PolicyExportManifest`、source-checkpoint commitment 与 terminal record。

## Consequences

- 不兼容依赖和受限资产获得清晰隔离，失败可归因到具体 profile，重复运行能够解析到相同环境。
- 构建、缓存和维护多个 profile 会增加磁盘、CI 与 operator 成本。
- profile 间接口必须稳定且可序列化，升级依赖需要产生新 profile identity，并重新执行对应 compatibility checks。
- 在线 service/control message 不是领域 artifact；它是受 manifest 限权的短生命周期 protocol envelope。未知 edge kind/version/message/capability、未认证 peer、profile/principal identity mismatch、replay 或 transcript discontinuity 必须 fail closed；需要留存的 request/response digest、terminal summary 与 attestation 转成 artifact。
- “在某台机器上可运行”不构成 runtime provenance；缺少 manifest、digest 或 commit 的运行不得升级为 verified reproduction。
