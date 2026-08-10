# AutoMarkov 仓库工作规则

本文件将全局 Codex 工作规则落实到 AutoMarkov。用户当前指令始终优先；未经用户授权，不扩大任务范围或改变已批准的研究合同。

## Agent skills

- **Issue tracker**：使用 GitHub Issues。创建、拆分、查询或更新工作项前读取 `docs/agents/issue-tracker.md`。
- **Triage labels**：使用 `needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix` 五个默认状态标签；使用前读取 `docs/agents/triage-labels.md`。
- **Domain docs**：本仓库采用 single-context 布局；术语表是根目录 `CONTEXT.md`，架构决策位于 `docs/adr/`。修改领域语言或架构决策前读取 `docs/agents/domain.md`。
- **Bootstrap state**：`setup-matt-pocock-skills` 已完成。只有在用户明确要求更换 issue tracker、标签词汇或 domain-document 布局时才重新运行。

## 权限、语言与范围

- 以用户指令为最高权威；明确区分规划、只读审计、实现、实验执行、发布与完成。
- 内部推理使用英文；最终回复、代码注释和 operator-facing 文档使用中文。`README.md` 使用英文。
- 标识符、文件名、函数名、类名、CLI flag、配置键和协议名使用英文。
- 仅完成已接受的合同。保留无关的用户改动，不做机会主义重构，不清理本任务之外的工件。
- 不读取、显示、复制或提交真实 `.env`；配置发现只读取 `.env.example`。密钥、token、sealed asset 和原始私有材料始终视为敏感数据。

## 工程工作流

每个 project-development 任务使用 Matt Pocock 主流程，并按规模选择分支：

1. 用 `grill-with-docs` 澄清变更，持续更新领域 glossary；只有难以逆转、上下文不直观且存在真实权衡的决定才写 ADR。
2. 需要可运行证据的单一设计问题，通过 `handoff` 进入独立上下文，用 `prototype` 验证，再把结论交回原上下文。
3. 多 session 工作先用 `to-spec` 形成项目 spec，再用 `to-tickets` 拆成带显式依赖的 tracer-bullet tickets；每个 ticket 在新上下文中按 blocker 顺序实施。
4. 单 session 工作直接用 `implement`；`implement` 按 red-green slice 调用 `tdd`，并在结束前执行 Standards 与 Spec 两轴 `code-review`。
5. 完成 Matt 流程后，由外层 operator 另外执行一次且仅一个 native review target：`codex review --uncommitted`、`codex review --base <branch>` 或 `codex review --commit <sha>`。正在执行 `codex review` 的进程不得递归启动另一个 review。修复 actionable findings 后，由外层 operator 复跑同一 target。

缺陷诊断使用 `diagnosing-bugs`，从一个能够稳定复现问题的紧命令开始，以回归测试结束。合并或 rebase 冲突只使用 `resolving-merge-conflicts`。学术研究、实验设计、统计解释和复现验证同时使用 `academic-research-suite`，并先选择一个 bundled workflow；前端与视觉工作同时使用最具体的 UI/UX Pro Max skill，先形成 design system，再验证 accessibility、responsive、dark mode 与 reduced motion。

只有边界清晰且能够独立完成的 workstream 才交给 subagent。所有 root 与 role subagents 使用 `gpt-5.6-sol` 和 `xhigh` reasoning，除非用户明确覆盖；等待 subagent 完成后再统一核对其证据与当前 scope。

## 权威上游复用

- 实现前检索最新官方仓库、package README 与官方文档；优先复用官方包、源实现和架构，并记录 version、tag、commit、dataset revision、model ID、checksum 或 container digest。
- 神经网络、机器学习和 LLM 组件使用 PyTorch；单智能体环境使用 Gymnasium；多智能体 Markov game/POSG 环境使用 PettingZoo；博弈分析优先 OpenSpiel；训练复用 RLlib，不手写可由官方框架提供的 RL/MARL 算法。
- 多智能体 agent 建模优先 CAMEL-AI 或 OASIS；本地生成推理使用 vLLM；训练或微调使用 LlamaFactory；训练记录与可视化使用 SwanLab。
- 外部主张和复用实现都需要 provenance。无法核验的事实明确标为 unverified，不凭记忆补全版本、参数或论文设定。

## 仓库理解与检索

- 若仓库根目录存在 `.codegraph/`，理解结构、符号或调用路径时先使用 `codegraph explore`；索引是否存在由用户决定。
- 精确文件和文本发现使用 `rg` 或 `rg --files`。
- 需要当前事实、官方 API 或上游 provenance 时使用官方文档、主仓库和一手资料。不可用的证据渠道必须报告，不能伪造结果。

## AutoMarkov 领域边界

- AutoMarkov 将已批准的自然语言任务合同编译为 `MDP`、`POMDP`、`MG` 或 `POSG`，再绑定可验证环境并通过冻结的 RLlib protocol 评价策略。
- 公共深模块共有六条接缝：`Compiler`、`ArtifactRepository`、`LocalLlmRuntime`、`EvidenceGateway`、`ExecutionSandbox`，以及由 `EnvironmentBinding`/`TrainingRunner` 提供两个窄视图的环境执行接缝。新功能应深化这些接口，不以旁路脚本绕过合同。
- 环境路线遵循 `Reuse -> Compose -> Generate`；官方 simulator 或 environment 存在时，优先适配，不重写其物理或领域核心。
- 生成推理只使用本地 Qwen3.6-35B-A3B vLLM service。Tavily 仅允许 Search、Extract 与 Crawl，hosted answer 保持关闭。SwanLab 采用 offline-first。
- 四类对象之外的任务输出 `OODHandoffSpec`；PDDL 路由使用 Unified Planning。OOD 不是第五类核心数学对象。
- 依据论文公开概念独立实现的 Agent2World-inspired inference-time clean controlled variant 属于比较范围；受限 upstream 代码不得 port/vendor，其 SFT 是 deferred replication work，不是 AutoMarkov 核心运行依赖。

## 工件、运行时与评估

- 遵守 `docs/adr/0001-immutable-artifacts-and-append-only-events.md`：已发布工件不可原地修改，审批和运行状态以 append-only event 记录。
- 遵守 `docs/adr/0002-isolated-runtime-profiles.md`：每个 process execution 绑定一个冻结的 runtime profile；run manifest 冻结所需 profile graph。持久化交接只用 immutable artifact；在线 service/control edge 必须属于 ADR 的 closed protocol kind、版本化、鉴权、schema-valid 且可审计，禁止共享进程内对象或可编辑依赖树。
- 遵守 `docs/adr/0003-sealed-evaluation-boundary.md`：生成侧不得访问 gold spec、hidden test、reference implementation 或 sealed evaluation credential。
- 生产代码、测试、数据、生成输出与外部研究 checkout 放在不同的 coherent roots。受限数据和完整运行输出保留在 ignored artifact roots；只发布 redacted manifest 与 compact report。
- 每次实验必须可追溯到 source commit、runtime profile、prompt hash、task-card manifest、method、generation pair、RL seed 与输出 hash。

## 实现与验证

- 实现满足完整接受合同的最小深架构；优先清晰接口和集中策略，避免散落的 one-off files、无必要层级与过度嵌套。
- 测试聚焦本次改动及其直接集成边界。先运行最小失败测试，再运行相关单元、属性、集成或 runtime checks；避免机械重复无关的 repository-wide suite。
- 静态检查、targeted tests、runtime readiness、实验完成、发布成功与 release readiness 是不同结论，分别报告。
- dirty worktree 中保存无关改动。不得使用 `git reset --hard`、`git checkout --` 或面向宽目录的破坏性命令。
- Git 写入前先向用户给出准确 branch、author、commit message 与 scope，获得明确批准后只创建非 force child commit；空远端的首次 bootstrap root commit 是唯一例外，同样需要明确批准。随后核对 parent SHA、local SHA 与 remote SHA。不得擅自 commit、push、创建 PR 或把发布等同于验收完成。

## 完成与报告

- 完成需要与风险相称的新鲜静态和 runtime 证据、所有已接受工作项闭合，且没有未报告的 actionable defect。
- 只读任务的最终回复以 `No file changes` 开头，再报告范围、发现、验证与剩余风险。
- 修改文件时，最终回复先逐文件给出相对路径与精确行数：`• Edited path (+N -M)`、`• Added path (+N -0)` 或 `• Deleted path (+0 -M)`。
- 明确标注 partial、blocked、reused、copied、moved、reformatted 与 verification-only 状态；不把计划或文件存在声称为实现完成。
