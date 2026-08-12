# Isolated runtime profiles

## Status

Accepted

## Context

AutoMarkov 同时依赖 agent authoring、symbolic planning、单智能体与多智能体 RL、复杂 simulator、论文复现和 sealed evaluation。它们的 Python、native library、CUDA、Ray、Gymnasium/PettingZoo 与 benchmark 约束并不保证兼容；把全部依赖装入一个环境会使解析结果、复现性和故障归因随安装顺序漂移。

## Decision

build context identity 使用 `AutoMarkov-Runtime-Profile-Build-Context-v2` domain，对每个 allowlisted path 同时绑定 Git 语义的规范化 mode（不可执行 `0644`、可执行 `0755`）与内容 SHA-256；executable bit 或 bytes 任一变化都产生新的 profile identity，同时不让 umask 造成的 group/other 位差异改变同一 Git tree 的身份。

每个 `ProcessExecution`（coordinator、authoring worker、RL trainer、environment worker 或 sealed evaluator）必须绑定且只能绑定一个 immutable `RuntimeProfile`。profile 记录 lockfile 或 environment manifest、Python 与关键 package 版本、external repository commit、dataset revision、model identity、container digest、hardware contract 和可用 capability；在比较开始前冻结并以稳定 `runtime_profile_id` 引用。一个逻辑 `Run` 可以编排多个 process executions，但其 `RunManifest` 必须在启动前冻结完整 profile graph、每个节点的 profile identity、允许的边、protocol version 与 capability。

每个 profile identity 还必须冻结 `egress_allowlist`、逻辑 `credential_ids`、`read_mounts`、`write_mounts` 与 `protocol_edges`。这些集合是该 profile 的中央最大权限，不含 credential value。T04 只建立并验证这一 profile 上限；在 T12/T17 落地 `RemoteEnv` grant 与 fixed-commit execution 接缝前，仓库没有生产 `ProcessExecution` launch path。首个 launch path 必须让具体 `RunManifest` 只选择这些集合的子集并在启动前 fail closed，不能临时扩大；该执行门禁不得以 T04 内的平行占位 `RunManifest` 模型提前伪造。

profile image lifecycle 使用 closed `image_status`。`recipe_frozen` 只证明 lock、allowlist build context、SBOM、license inventory 和 smoke contract 已冻结；`attached_unverified` 只记录待验证的外部服务；`restricted_disabled` 保持受限 profile 不可构建、不可发布。只有从 `ArtifactRepository` 的 caller-specified verified head 解析、重验并绑定 build attestation 与 import-smoke attestation 后，状态才能成为 `built` 并携带真实 OCI digest、platform、libc、OpenSSL 与 CA bundle identity。在可信 attestation resolver 实现前，provenance verifier 对任何 `built` 声明 fail closed。普通 push/PR 只验证 metadata；安装全部重型 profile 的 smoke matrix 由显式 `workflow_dispatch` 执行。

T04 recipe 统一冻结 `target_platform=linux/amd64`、Debian bookworm glibc 2.36 与 profile 的 CPython patch version。目标安装闭包从 uv virtual root 遍历 dependency markers，并传播 requested extras/`optional-dependencies`；marker 只使用冻结的 CPython/Linux environment，涉及未冻结 kernel release/version 的表达式拒绝。registry 工件使用官方 `packaging` 的 CPython ABI、`compatible_tags` 与 wheel/sdist filename parser：按 glibc 2.36→2.5、legacy manylinux alias、`linux_x86_64` 的顺序选择唯一最优 compatible wheel，无 compatible wheel 才选择 identity-matched sdist。目标闭包外的通用 lock 分支仍进入 inventory/license coverage，但 SBOM 使用 `NOASSERTION` 且不声称 artifact hash。目标闭包内每个 registry package 的 SBOM 只记录该唯一 URL/hash；active pip upstream checksum 必须与之精确相等。active Git source 以 uv lock 的 exact repository URL 与 40-hex commit 为重建身份，不把 GitHub 自动生成 archive 的易变压缩字节当长期身份。

目标闭包中需要本地构建的集合是 closed policy：`authoring` 的 `google-search-results`、`env-citylearn` 的 `tinynumpy`、`env-metadrive` 的 `progressbar`/`scenarionet`，以及 `env-smacv2` 的 `mpyq`/`s2protocol`/`smacv2`。这些 legacy source 均通过 uv 官方 `no-build-isolation-package` 双阶段路线构建：profile 同时把 `setuptools==84.0.0` 作为 direct dependency 和 `build-constraint-dependencies` 写入，第一阶段以 locked sync 明确排除全部 source package，安装由 lock artifact hash、SBOM 与 license inventory 约束的 backend；确认 `.venv` 中 setuptools 精确版本后，第二阶段再以同一 frozen lock 构建完整闭包。central verifier 必须同时绑定 exact source set、两阶段 recipe 顺序、lock target closure、profile identity，以及 `setuptools BUILD_DEPENDENCY_OF source-package` 的 SPDX 关系；只写 build constraint、仍让隔离构建在线解析 backend 不构成冻结。

核心 authoring、planning、RL training、复杂 suite replication 与 sealed evaluation 按依赖兼容性拆分 profile，不追求单一 universal environment。跨 profile 的持久化交接只交换 schema-versioned、content-hashed immutable `Artifact`。在线消息只允许 run manifest 中的 closed edge kinds：`LocalLlmRuntime` inference、`EvidenceGateway` retrieval、`ClarificationBroker`、`ExperimentApprovalPolicy`、`RemoteEnv`、`SealedEvaluator` 和 `FixedCommitRunner` control/attestation；每条 edge 必须冻结 source/target principal/profile、protocol/version、transport authentication、message schema、capability、budget/egress policy 和 transcript-hash contract。`RemoteEnv` 是唯一高频 environment step-stream edge。任何边都不得共享另一个 profile 的 editable checkout、site-packages、Python/Ray 进程内对象、pickle 或 cloudpickle payload。

`RemoteEnv` 只使用 run manifest 冻结 codec/schema hash 的单一 canonical frame codec；合法 source/target topology 仅为隔离的 `trainer→environment-worker` 与 evaluator-owned default-deny namespace 内的 `sealed-evaluator→sealed-environment-worker`，两者不得复用 principal、grant、session 或 worker。替代 metadata/tensor codec、caller-selected encoding、未登记 codec version 或 trainer/sealed principal 交叉均 fail closed。

普通 RLlib checkpoint 只是原 frozen trainer profile 内 ignored、run-local 的 recovery state；同 profile 的一次性 export execution 可读取它，跨 profile、packaging、publisher 和 sealed evaluator 只接收 content-addressed weights-only safetensors、`PolicyExportManifest`、source-checkpoint commitment 与 terminal record。

## Consequences

- 不兼容依赖和受限资产获得清晰隔离，失败可归因到具体 profile，重复运行能够解析到相同环境。
- 构建、缓存和维护多个 profile 会增加磁盘、CI 与 operator 成本。
- profile 间接口必须稳定且可序列化，升级依赖需要产生新 profile identity，并重新执行对应 compatibility checks。
- 在线 service/control message 不是领域 artifact；它是受 manifest 限权的短生命周期 protocol envelope。未知 edge kind/version/message/capability、未认证 peer、profile/principal identity mismatch、replay 或 transcript discontinuity 必须 fail closed；需要留存的 request/response digest、terminal summary 与 attestation 转成 artifact。
- “在某台机器上可运行”不构成 runtime provenance；缺少 manifest、digest 或 commit 的运行不得升级为 verified reproduction。
