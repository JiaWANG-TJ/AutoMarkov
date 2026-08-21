
# AutoMarkov 当前开发交接

## 交接目标

本文用于让新的 Coding Agent 在不重做既有设计、不破坏暂存改动的前提下，继续完成 AutoMarkov 当前 T16/T17 批次。

当前批次的直接目标是完成 sealed E2E evaluation 与 fixed-commit execution 的原生审查、缺陷修复、聚焦验证和发布。它不是整个 AutoMarkov 项目的最终交付，也不代表正式实验已经执行。

## 项目开发依据

AutoMarkov 将经过批准的自然语言任务合同编译为 `MDP`、`POMDP`、`MG` 或 `POSG`，随后绑定可验证环境，并通过冻结的 RLlib protocol 训练或评价策略。

项目优先建立 publication-grade 的可信实验链：

```text
TaskContract
  -> DecisionProcessSpec
  -> EnvironmentBinding
  -> FixedCommitJobManifest
  -> ProcessExecutionTerminalRecord
  -> optional TerminalResult
  -> runner-signed ExecutionAttestation
  -> sealed evaluation
  -> reproducible experiment report
```

开发必须以以下资料为权威来源：

1. `AGENTS.md`：仓库工作规则、验证边界与 Git 权限。
2. `CONTEXT.md`：领域术语与已接受的设计语言。
3. `docs/AutoMarkov_complete_development_specification.md`：完整系统规格。
4. `docs/experiments/automarkov-code-experiment-plan.md`：正式实验合同。
5. `docs/adr/0001-immutable-artifacts-and-append-only-events.md`：不可变工件、append-only events 与原子 CAS。
6. `docs/adr/0002-isolated-runtime-profiles.md`：隔离 runtime profiles、最小权限与 closed protocol edges。
7. `docs/adr/0003-sealed-evaluation-boundary.md`：generation 与 sealed evaluator 的隔离边界。
8. 当前 `git diff --cached`：当前实现字节的事实源。

核心技术优先复用官方实现：PyTorch、Gymnasium、PettingZoo、OpenSpiel、RLlib、vLLM、LlamaFactory 与 SwanLab。不得手写已有权威实现可以提供的核心 RL、MARL、环境或推理算法。

## 当前 tracer-bullet 批次

### T16：sealed E2E evaluation

- candidate、gold 与 comparator 使用独立 worker、principal、profile 和 job manifest。
- generation principal 不可访问 gold、hidden tests、reference implementation 或 evaluator diagnostics。
- request、worker evidence、verdict、runner attestation、artifact DAG 与 lifecycle transition 必须精确绑定。
- 四门全部通过才进入 `TRAINING_SMOKE_TESTING`。
- 合法 gate false 映射为 `PARTIAL`；签名、schema、binding 或 contamination 错误映射为 `FAILED`。
- replay、nonce、key/run slot、terminal provenance 与 SQLite restart 必须持久、原子且 fail closed。

### T17：FixedCommitRunner

- 从 caller-specified verified event head 解析冻结的 RunManifest 与 job graph。
- 在 detached exact commit 上执行，验证 repository URL、commit、profile、OCI image、inputs、policies、argv、seed 和 deadline。
- 使用 default-deny network、只读输入、独立输出根、非 root、capability drop、seccomp 与 AppArmor。
- 对实际输出字节执行 closed schema validation、secret/credential/gold scan 与 content hashing。
- 每个 execution 恰好产生一个 `ProcessExecutionTerminalRecord`，随后才可生成 runner-signed `ExecutionAttestation`。
- Memory 与 SQLite repository 必须支持 reservation、checkpoint、finalize、exact replay、crash recovery 与 terminal CAS。

## 当前 Git 状态

交接时已核对：

- repository：`/inspire/hdd/project/socialsimulation/wangjia-240108610168/AutoMarkov`
- branch：`main`
- expected HEAD：`179603d`
- 当前有 16 个 T16/T17 文件处于 staged 状态。
- 尚未 commit 或 push。
- 上一次 `codex review --uncommitted` 被中断，没有最终 PASS/FAIL 结论。
- 交接时没有发现仍在运行的 `codex review` 进程。

新 Agent 必须重新核对这些事实；本节是交接快照，不是永久真值。

## GPU SSH relay

当前用户提供的 GPU SSH 端口 relay 路径已记录为经批准的 redacted locator identity:

```text
[REDACTED_SESSION_RELAY] — sha256:7244b705293564740d703337df33712a48c05682e3ad96461e3cb318486be952
```

这是会话级 WebSocket/HTTPS relay 入口，不是可以直接传给 OpenSSH 的普通 `host:port`，也不证明远端 vLLM 或实验 runtime 已就绪。

接手 Agent 使用该入口时必须遵守以下顺序：

1. 先通过项目已有、完整性已核验的 tunnel client 建立临时 loopback listener。
2. 只验证 relay 是否能返回 SSH banner；探针结束后清理临时 listener。
3. 只有获得明确 SSH 认证授权后才登录远端。
4. 远端检查默认只读，不启动、停止、重启或修改进程、模型、环境和服务。
5. 不读取或输出 SSH private key、`.env`、API key、token、cookie 或 credential value。
6. 不把 relay 可达、SSH 可登录、GPU 可见、vLLM listener 存在、`/health` 成功、`/v1/models` 成功和真实 completion 成功混为一个结论。
7. runtime readiness 必须重新绑定当前 host、boot ID、PID/start time、listener/socket、package identity、model/tokenizer/template hashes 和实际请求证据。
8. URL 失效、listener 漂移、credential channel 缺失或 frozen identity 不匹配时，状态保持 `WAITING_RUNTIME`。

该 URL 不应写入 runtime manifest、正式实验工件或公开报告；正式记录只保存经过批准的 redacted locator identity 或其 hash。

## 接手步骤

### 1. 核对工作区

```bash
cd /inspire/hdd/project/socialsimulation/wangjia-240108610168/AutoMarkov

sed -n '1,260p' AGENTS.md
git status --short
git branch --show-current
git rev-parse HEAD
git diff --cached --stat
git diff --cached --check
```

完成条件：

- branch 与 expected HEAD 没有未解释的漂移。
- 既有 staged 文件完整。
- 没有不明 unstaged 或 untracked 文件。
- cached diff 没有 whitespace 错误。

保留全部既有用户改动。不得执行 `git reset --hard`、`git checkout --` 或宽目录删除。

### 2. 理解当前实现

优先查看 staged diff，而不是重新设计架构：

```bash
git diff --cached -- src/automarkov/fixed_commit_runner.py
git diff --cached -- src/automarkov/sealed_evaluation.py
git diff --cached -- src/automarkov/repository.py
git diff --cached -- src/automarkov/lifecycle.py
git diff --cached -- src/automarkov/task_contracts.py
```

完成条件：能够从公开 seam 追踪 fixed-commit request 到 terminal record/attestation，以及 E2E request 到 lifecycle materialization 的完整调用路径。

### 3. 完成唯一 native review

运行且只运行一个 review target：

```bash
codex review --uncommitted
```

不要并行启动第二个 `codex review`。

若有 actionable finding：

1. 为 finding 增加一个最小失败回归。
2. 确认 RED。
3. 修改最少量生产代码。
4. 运行该节点和直接受影响测试。
5. 运行 scoped Ruff、format 与 Pyright。
6. 更新 `src/automarkov/provenance.py` 中受影响文件的 source hash。
7. 暂存精确文件。
8. 重新运行同一 `codex review --uncommitted`。

完成条件：native review 明确 PASS，且没有未解决 actionable finding。

### 4. 聚焦验证

不要机械运行无关全仓测试。默认验证集合为：

```bash
uv run --locked pytest -q \
  tests/runner/test_fixed_commit.py \
  tests/security/test_runner_policy.py \
  tests/security/test_sealed_evaluator.py \
  tests/contract/test_e2e_gate_protocol.py \
  tests/contract/test_execution_attestation.py

uv run --locked ruff check \
  src/automarkov/fixed_commit_runner.py \
  src/automarkov/sealed_evaluation.py \
  src/automarkov/repository.py \
  src/automarkov/lifecycle.py \
  src/automarkov/task_contracts.py

uv run --locked ruff format --check \
  src/automarkov/fixed_commit_runner.py \
  src/automarkov/sealed_evaluation.py \
  src/automarkov/repository.py \
  src/automarkov/lifecycle.py \
  src/automarkov/task_contracts.py

uv run --locked pyright \
  src/automarkov/fixed_commit_runner.py \
  src/automarkov/sealed_evaluation.py \
  src/automarkov/repository.py \
  src/automarkov/lifecycle.py \
  src/automarkov/task_contracts.py

uv run --locked automarkov verify-provenance --repository-root .
git diff --cached --check
```

只有 finding 涉及直接集成边界时才增加相应节点。

### 5. 发布

历史授权记录中的发布参数为：

- author：`jiawang <jiawang@tongji.edu.cn>`
- commit message：`feat: add sealed evaluation and fixed-commit runner`
- target：`origin/main`
- push mode：non-force

发布前重新验证：

```bash
git rev-parse HEAD
git rev-parse origin/main
git config user.name
git config user.email
git status --short
```

native review 未 PASS 时不得 commit。HEAD、origin、author、scope 或授权有任何漂移时，先向用户报告并获得方向。

## 完成交付标准

T16/T17 批次只有同时满足以下条件才可标记 complete：

- native review PASS，actionable finding 为零。
- targeted runtime/contract/security tests 通过。
- scoped Ruff、format 与 Pyright 通过。
- provenance verifier 通过。
- Git diff check 通过。
- commit parent、local SHA 与 remote SHA 精确核对。
- 没有把 credential、sealed payload、ignored runtime output 或 mutable checkout 写入发布树。

最终报告必须分别说明：

- T16/T17 batch 是否完成。
- 正式实验是否执行。
- whole AutoMarkov project 是否完成。
- native review、测试、provenance、commit 和 remote parity 的新鲜证据。

## 可直接交给新 Agent 的提示词

```text
你正在接手 AutoMarkov 当前 T16/T17 开发，不要从头重做规划。

工作目录：
/inspire/hdd/project/socialsimulation/wangjia-240108610168/AutoMarkov

模型要求：
gpt-5.6-sol，xhigh reasoning。

当前交接快照：
- branch: main
- expected HEAD: 179603d
- 当前有16个T16/T17文件已暂存
- 尚未commit/push
- 上次 codex review --uncommitted 被中断，没有最终结论
- 交接时没有活跃的codex review进程

GPU SSH relay：[REDACTED_SESSION_RELAY]

先完整读取：
- AGENTS.md
- docs/agents/current-development-handoff.md

任务范围：
1. T16 sealed E2E evaluation gate。
2. T17 FixedCommitRunner。
3. 相关 ArtifactRepository、lifecycle、public ExecutionSandbox、provenance和CI集成。
4. 不扩展到后续正式实验任务。
5. 不把本批完成称为整个AutoMarkov项目全部完成。

执行要求：
1. 核对git status、branch、HEAD、cached diff及无关改动。
2. 保留全部既有暂存内容。
3. 运行唯一native gate：codex review --uncommitted。
4. 只修复明确actionable findings。
5. 每个finding使用最小TDD RED->GREEN切片。
6. 只运行finding节点、直接相关T16/T17测试和scoped static checks。
7. 更新受影响的provenance source hashes并暂存精确文件。
8. 重新运行同一native target直到PASS。
9. 核对provenance、cached diff、branch、author、parent和remote。
10. native review未PASS前不得commit或push。

GPU入口只代表relay locator。先验证SSH banner；没有独立授权时不登录、不读取credential、不修改远端。relay可达不等于vLLM或实验runtime READY。

发布授权记录：
- author: jiawang <jiawang@tongji.edu.cn>
- message: feat: add sealed evaluation and fixed-commit runner
- target: origin/main
- non-force only

完成报告必须区分：
- T16/T17 batch complete
- formal experiments not run
- whole AutoMarkov project not yet complete
- commit SHA、parent SHA、remote SHA、focused tests和native review结论
```
