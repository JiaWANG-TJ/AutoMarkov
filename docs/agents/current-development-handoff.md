# AutoMarkov 当前开发交接

## 交接目标

本文用于让新的 Coding Agent 在不重做既有设计、不破坏暂存改动的前提下，继续完成 AutoMarkov 当前未解决的阻塞问题。

当前批次的直接目标是修复 CI 合约阻塞、消除 Pyright 类型错误、并确保生产适配器的集成测试状态被准确记录。它不是整个 AutoMarkov 项目的最终交付，也不代表正式实验已经执行。

## 项目开发依据

AutoMarkov 将经过批准的自然语言任务合同编译为 `MDP`、`POMDP`、`MG` 或 `POSG`，随后绑定可验证环境，并通过冻结的 RLlib protocol 训练或评价策略。

项目优先建立 publication-grade 的可信实验链：

```text
TaskContract
  -> DecisionProcessSpec
  -> EnvironmentBinding
  -> FixCommitJobManifest
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
5. `docs/adr/0001-immutable-artifact-and-append-only-events.md`：不可变工件、append-only events 与原子 CAS。
6. `docs/adr/0002-isolated-runtime-profiles.md`：隔离 runtime profiles、最小权限与 closed protocol edges。
7. `docs/adr/0003-sealed-evaluation-boundary.md`：generation 与 sealed evaluator 的隔离边界。
8. 当前 `git diff --cached`：当前实现字节的事实源。

核心技术优先复用官方实现：PyTorch、Gymnasium、PettingZoo、OpenSpiel、RLlib、vLLM、LlamaFactory 与 SwanLab。不得手写已有权威实现可以提供的核心 RL、MARL、环境或推理算法。

## 当前未解决阻塞

### CI provenance-contract 失败

GitHub Actions `provenance-contract` workflow 中的 `metadata` job 当前因以下原因失败：

1. `verify-provenance` 步骤可能因 provenance source hash 不匹配而失败。
2. CI 中的 `pytest` 子集可能因 Pyright 30 个类型错误而无法通过。

修复要求：
- 确认 `uv run automarkov verify-provenance --repository-root .` 是否在本地通过。
- 确认 CI pytest 子集中的测试文件是否在本地通过。
- 修复 CI 步骤使其稳定通过。

### Pyright 类型错误（30 个）

以下三个文件存在报告级 `reportInvalidTypeForm` 错误：

| 文件 | 错误数 | 原因 |
|------|--------|------|
| `src/automarkov/benchmark_suites.py` | 11 | 类型别名变量用于类型表达式 |
| `src/automarkov/generation_methods.py` | 10 | 同上 |
| `src/automarkov/statistics.py` | 4 | 同上 |

修复方式：使用 `TypeAlias` + `Final` 显式声明，或改用显式类型字面量。

### 无 production RLlib runner

`src/automarkov/rllib_training.py` 存在但为 `ScriptedTrainingRunner` 适配器，不等 于 production RLlib runner。当前 `public.py` 中的 `TrainingRunner` protocol 没有 production 级实现。

### 生产适配器状态

以下适配器存在代码：

| 适配器 | 状态 |
|--------|------|
| Taxi | 代码完整，未集成测试 |
| MiniGrid | 代码完整，未集成测试 |
| MetaDrive | 代码完整，未集成测试 |
| MPE2 | 代码完整，未集成测试 |
| SMACv2 | 代码完整，未集成测试 |
| CityLearn | 代码完整，未集成测试 |

### Profile 门禁

所有 17 个 profile 均未通过 production readiness gate：

- 16 个为 `recipe_frozen`
- 1 (`llm-qwen36-vllm`) 为 `attached_unverified`

### Issue T18-T27

已关闭但验收证据不完整。

## 当前 Git 状态

- repository：`/inspire/hdd/project/socialsimulation/wangjia-240108610168/AutoMarkov`
- branch：`main`
- HEAD：`3cea996`
- 工作区有 4 个 untracked 文件（文档）。
- 没有 staged 文件。
- 没有未提交的生产代码修改。

## GPU SSH relay

当前用户提供的 GPU SSH 端口 relay 路径已记录为经批准的 redacted locator identity：

```text
[REDACTED_SESSION_RELAY] — sha256:7244b705293564740d703337df33712a48c05682e3ad96461e3cb318486be952
```

这是会话级 WebSocket/HTTPS relay 入口，不是可以直接传给 OpenSSH 的普通 `host:port`，也不证明远端 vLLM 或实验 runtime 已就绪。

接手 Agent 使用该入口时必须遵守以下顺序：

1. 先通过项目已有、完整性已核验的 tunnel client 建立临时 loopback listener。
2. 只验证 relay 是否能返回 SSH banner；探针结束后清理临时 listener。
3. 只有获得明确 SSH 认证授权后才登录远端。
4. 远程检查默认只读，不启动、停止、重启或修改进程、模型、环境和服务。
5. 不读取或输出 SSH private key、`.env`、API key、token、cookie 或 credential value。
6. 不把 relay 可达、SSH 可登录、GPU 可见、vLLM listener 存在、`/health` 成功、`/v1/models` 成功和真实 completion 成功混为一个结论。
7. runtime readiness 必须重新绑定当前 host、boot ID、PID(start time、listener socket、package identity、model/tokenizer/template hashes 和实际请求证据。
8. URL 失效、listener 漂移、credential channel 缺失或 frozen identity 不匹配时，状态保持 `WAITING_RUNTIME`。

该 URL 不应写入 runtime manifest、正式实验工件或公开报告；正式记录只保存经过批准的 redacted locator identity 或其 hash。

## 接手步骤

### 1. 核对工作区

```bash
cd /inspire/hdd/project/socialsimulation/wangjia-240108610168/AutoMarkov

git status --short
git branch --show_current
git rev-parse HEAD
git log --oneline -5
uv run ruff check src/
uv run pyright/src/ 2>&1 | tail -5
```

完成条件：

- branch 为 main，HEAD 与 log 一致。
- Ruff 0 errors，Pyright 30 errors 状态已记录。
- 工作区没有未声明的改动。

### 2. 理解当前实现

优先查看最近 commit 和未解决的文件：

```bash
git diff --cached -- src/automarkov/benchmark_suites.py
git diff --cached -- src/automarkov/generation_methods.py
git diff --cached -- src/automarkov/statistics.py
```

完成条件：能够理解 Pyright 错误的具体位置和修复模式。

### 3. 修复 Pyright 类型错误

对三个文件逐一修复：

```bash
# 对每个文件，使用 TypeAlias 修复类型别名
uv run --locked pyright src/automarkov/benchmark_suites.py
uv run --locked pyright src/automarkov/generation_methods.py
uv run --locked pyright src/automarkov/statistics.py
```

完成条件：Pyright 从 30 errors 降到 0。

### 4. 验证 CI 可通过

```bash
uv run automarkov verify-provenance --repository-root .
uv run --locked pytest -q tests/contract/test_profile_recipe_workflow.py tests/contract/test_provenance_full_identity_review.py tests/contract/test_e2e_gate_protocol.py tests/security/test_sealed_evaluator.py tests/runner/test_fixed_commit.py tests/contract/test_execution_attestation.py tests/security/test_runner_policy.py
uv run --locked ruff check src/
```

完成条件：所有 CI metadata job 步骤在本地通过。

### 5. 发布

发布前重新验证：

```bash
git rev-parse HEAD
git status --short
uv run --locked ruff check src/
uv run --locked pyright src/ 2>&1 | tail -5
```

发布授权记录：

- author：`jiawang <jiawang@tongji.edu.cn>`
- target：`origin/main`
- push mode：non-force

Pyright 仍有 error 时不得 commit。HEAD、author 或 scope 有任何漂移时，先向用户报告并获得方向。

## 完成交付标准

当前批次只有同时满足以下条件才可标记 complete：

- Pyright 从 30 errors 降到 0。
- CI metadata job 在本地验证通过。
- scoped Ruff 通过。
- provenance verifier 通过。
- Git diff check 通过。
- commit parent、local SHA 与 remote SHA 精确核对。
- 没有把 credential、sealed payload、ignored runtime output 或 mutable checkout 写入发布树。

最终报告必须分别说明：

- Pyright 修复是否完成（errors: 30 -> ?）。
- CI metadata job 是否通过。
- 每个文件的具体修改和验证证据。
- 整个 AutoMarkov 项目仍非 complete（CI import-smoke 未触发、production runner 未实现、profiles 未验证）。
