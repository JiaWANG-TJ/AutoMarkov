# Immutable artifacts and append-only events

## Status

Accepted

## Context

AutoMarkov 的多个 agent、validator、runtime 和 evaluator 会在不同时间消费同一份领域工件。若已被引用或批准的内容能够原地改变，hash、lineage、审批语义和实验复现都会失去可信边界；若运行状态只保留最新值，也无法判断状态如何形成。

## Decision

所有已持久化 `Artifact` 都是 immutable、versioned 且 content-addressed。修订产生具有新 identity 和显式 parent lineage 的新工件，旧工件保持可读取；批准、拒绝、supersede、run transition 与异常记录为 append-only domain events。当前状态只允许作为可重建的 projection，不作为权威事实源。

terminal result 绑定终止事务的 event/head snapshot 且永不覆盖；终止后审批或审计变化从 caller 指定的 verified event head 重建为新的 content-addressed projection snapshot，旧 projection 保持可寻址。

任何将 parent run 转为 `CANCELLED` 的 replacement 跨-run CAS 必须在同一原子事务中持久化 cancellation-control process terminal record、parent `TerminalResult`、child sequence-0 event 与引用 parent terminal result 的 execution attestation；失败不得留下无 terminal provenance 的 parent 或孤立 child。`CLARIFICATION_REQUIRED` parent 保持终态且不被 supersede；获得 signed answer 后只可按独立 continuation policy，在新的 child event stream 以 `ClarificationChildRunCreated` sequence-0 event 原子创建 child，parent stream/snapshot不变。confirmatory sealed verdict不能作为 continuation input。

artifact envelope 的 parent DAG 只含 artifact IDs；event ID/hash 通过独立 typed event-reference fields 绑定，绝不冒充 artifact parent。fixed-commit runner 对每个 bounded process execution 先定址 payload outputs 与唯一 `ProcessExecutionTerminalRecord`；若该 execution 同时完成 Run terminal CAS，`TerminalResult` 必须把该 record ID/hash 作为 closed typed field 与 direct parent，随后 runner 才签发同时绑定同一 record和该 `TerminalResult` 的 execution attestation。非终态 job 不伪造 Run terminal result，并保持 job manifest→process terminal record→optional run terminal result→attestation 的单向 hash graph。

删除、保留期与隐私擦除作为显式治理流程处理，不伪装为普通内容更新。内部 provenance manifest 引用稳定 artifact identity 与 content hash；公开 manifest 对 public artifact 才可发布该 identity/hash。对 sealed gold、clarification oracle、hidden evaluator 与 credential-bearing artifact，只发布域分离的 nonce-backed commitment；sealed nonce、原始 identity/content hash、payload、answer、locator 和 credential 均不得进入公开派生物。

## Consequences

- 审计者可以重建任一决定和运行的输入、顺序与结果，也能区分修订和篡改。
- 写路径必须处理幂等 event、并发冲突、lineage 验证和 projection 重建。
- 存储会增长，需要独立的 retention、redaction 与 compaction policy；这些派生操作不能改写仍受 provenance 约束的原始记录。
- 纠错不能覆盖历史错误，而要产生新工件或补偿事件，因此 operator UI 必须明确显示 current projection 与历史 lineage。
- 隔离 redactor 必须用内部 taint registry 对 strict redacted aggregate 做负向检查并签发不含敏感值的 attestation；publisher 只从该 aggregate 的固定模板渲染有限白名单文件，运行时不挂载 sealed store/taint registry，并验证 attestation 与结构性扫描。不能把“文件有 hash”当作可公开的充分条件。
