# Sealed evaluation boundary

## Status

Accepted

## Context

AutoMarkov 的生成、检索、修复和训练循环具有自适应能力。若这些循环能够读取 gold `TaskContract`、gold `DecisionProcessSpec`、hidden behavioral tests、reference implementation 或最终 evaluator 细节，系统可能针对评价资产优化而不是学习任务语义，端到端有效率也不再是可信的泛化证据。

## Decision

建立 generation boundary 与 sealed evaluation boundary。生成侧只能访问预注册 `TaskCard`、allowed-evidence manifest、公开 validator、冻结预算和显式 validation feedback；它没有 sealed asset path、credential、source、test name、expected value 或完整 failure trace 的读取权限。

sealed evaluator 在独立 `RuntimeProfile` 和权限域中运行，只接受三个预注册 signed request branch：完整 candidate 的 `E2EGateEvaluationRequest`、`AUTO/v5` post-terminal `ClarificationEvaluationRequest` 和 post-training `PolicyEvaluationRequest`。E2E request 绑定 run/manifest/candidate bundle、candidate TaskContract/DecisionProcessSpec/EnvironmentBinding 和 evaluator profile 的唯一 IDs/hashes，并以 sealed gold 分别判定 text、formal、API 与 hidden behavior。candidate code 只能在无 sealed mount/key/locator/network 的独立 untrusted worker 执行；gold/reference code 位于另一 trusted worker，只有 evaluator comparator 可同时读取两侧输出，expected values/trace不回传 candidate。pre-training 只向无 generation capability 的 run coordinator 返回绑定 request/candidate/四 subjects 的 signed `E2EGateVerdict` 四个 bool；四门 conjunction 唯一定义 `E2EValid`。verdict 不向 generation principal 返回，且不包含 test identity、trace、expected value或counterexample。任一合法 false 将该 immutable run 终止为 `PARTIAL` 且无训练 outcome；签名/binding/schema 无效、contamination 或 protocol 违规终止为 `FAILED`。

`AUTO/v5` 的 `ClarificationEvaluationRequest` 只能在 run 已以 `CLARIFICATION_REQUIRED` 终止、terminal result 与 runner execution attestation 均定址后，由无 generation/sealed capability 的 coordinator签发。request 显式绑定 run manifest/outcome mask、`ClarificationRequiredResult`、terminal result/event、从 `TerminalResult` 显式 roots 重算的 canonical terminal artifact-DAG closure hash、execution attestation、generation-visible sealed commitment及 evaluator profile，不含 sealed gap/oracle identity、payload/content hash、nonce、locator、answer或 expected value。clarification evaluator role只有 answer-redacted scoring capability，没有 broker socket、credential或 answer-serving capability；它在 sealed 域解析已注册 frozen gap scoring manifest，验证 exact gap coverage、零 semantic guessing/introduced assumptions、零 formal/environment descendants，以及 mount/capability/egress records 证明 `AUTO` 不可达 oracle broker。它只返回 closed signed `ClarificationEvaluationVerdict.safe_clarification_required` 单一 bool，不返回任何分项判断、gap identity/count、answer、trace或counterexample。合法 false与任何 generation/evaluation missing、timeout、integrity、contamination或protocol failure都保留预注册 slot并映射为 0，且不改写 terminal snapshot；只有 frozen deadline内对相同 request bytes/ID 的有界幂等 transport retry可执行。该 flow 是预注册 sealed evaluation，不是 generation-side boundary exception；request、verdict或 outcome 回流 generation/child run 仍需新 ADR 与 preregistration revision。

post-training aggregate 和 post-terminal clarification outcome 只进入受限报告流，完整 trace 留在受限 evaluation artifact 中。生成、开发、训练和修复 principal 不得获得任何 sealed-derived counterexample，最终评价结果也不得反馈用于同一预注册 run family 的修复。

唯一预注册的 generation-side 例外是 `HITL-ORACLE` track 的 `ClarificationOracleBroker`，它与 `SealedEvaluator` 使用不同 service account、capability、manifest 和审计流。broker capability 绑定 exact experiment、suite、variant、track、method、pair、round、question budget 与 expiry；broker 只在方法实际提交问题后返回与该问题匹配的 answer payload，不返回任何 artifact metadata/identity/content hash/nonce/locator，也不返回 gold contract/spec、缺口全集、hidden test、evaluator trace 或剩余答案。`AUTO` track 没有 broker mount、socket、credential 或网络 route。每次 request/response 产生 sealed append-only transcript，公开侧只得到预算使用量和合规状态。任何跨 track、跨 method/pair、超预算、预取、枚举或 replay 都 fail closed，并使受影响 run family 不进入 confirmatory statistics。

## Consequences

- 评价结果能区分公开验证通过与未见资产上的行为正确性，降低 benchmark leakage 和 evaluator gaming 风险。
- 调试信息受到限制，hidden failure 需要由独立 evaluator owner 审计，开发侧只能依据公开测试改进。
- 需要访问控制、独立 credential、输入 hash 校验、release policy 和泄漏审计；单纯把文件加入 `.gitignore` 不构成 sealed boundary。
- 除上述固定 clarification broker 外，任何边界例外都必须在运行前形成新的 ADR 与 preregistration revision；发生泄漏的 generation family 不进入 confirmatory statistics。
