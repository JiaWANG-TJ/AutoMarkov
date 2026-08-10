# AutoMarkov Domain

AutoMarkov 的统一术语表。这里定义研究对象与边界，不记录实现方案、目录结构或运行命令。

## 任务与形式化

**TaskRequest**:
用户最初表达的序贯决策问题，其中可能仍有歧义、缺失信息或未经证实的假设。
_Avoid_: Prompt, raw task

**TaskCard**:
实验中向方法提供的冻结任务描述；同一 benchmark task 可以有多个语义目标一致、预先登记的表述变体。
_Avoid_: Prompt variant, test prompt

**TaskContract**:
对目标、参与者、信息边界、动作、约束和成功条件形成的版本化领域语义声明；其 draft、reviewed、approved 或 locked 状态只由 append-only event 表达，不属于 payload 本身。
_Avoid_: Prompt, requirements dump

**DecisionProcessKind**:
任务所属的 `MDP`、`POMDP`、`MG` 或 `POSG` 类别，由决策主体数量和信息结构共同决定。
_Avoid_: Environment type, algorithm type

**DecisionProcessSpec**:
以闭合符号、类型和语义表达一个 `DecisionProcessKind` 的正式决策过程定义。
_Avoid_: Environment code, simulator config

**OODHandoffSpec**:
任务超出四类核心决策过程时，描述其边界、理由和可接受后续路线的交接合同。
_Avoid_: Fifth model kind, failure report

## 证据与治理

**EvidenceItem**:
支持一个领域事实或选择的可追溯来源声明，包含其适用范围和不确定性。
_Avoid_: Search result, citation string

**EvidenceLedger**:
某项任务所使用、拒绝或保留为不确定的 `EvidenceItem` 集合。
_Avoid_: Browser history, bibliography

**Assumption**:
为使任务可形式化而提出、但尚未由证据或批准建立的可检验声明。
_Avoid_: Default, fact

**AssumptionRegister**:
记录 `Assumption` 的来源、状态和解决结果的任务级清单。
_Avoid_: Notes, TODO list

**ApprovalEvent**:
对一个具体工件版本作出接受、拒绝或撤销接受的领域事件。
_Avoid_: Mutable status, checkbox

## 工件与执行

**Artifact**:
AutoMarkov 工作流中具有明确类型、身份和 lineage 的领域记录。
_Avoid_: File, mutable document

**ArtifactLineage**:
一个工件与其直接来源工件之间的可追溯派生关系。
_Avoid_: Folder hierarchy, edit history

**Run**:
在冻结输入、方法、预算和运行条件下进行的一次有界执行尝试。
_Avoid_: Session, process

**ProcessExecution**:
`Run` 内由一个执行主体完成、可被独立识别与审计的一次有界执行活动。
_Avoid_: Run, worker type, mutable shell session

**RunEvent**:
描述 `Run` 生命周期中已发生转换或观测事实的领域事件。
_Avoid_: Mutable run status, log line

**RuntimeProfile**:
能够唯一说明一次 `Run` 所需执行条件与兼容性边界的冻结运行环境身份。
_Avoid_: Shell, machine name

**EnvironmentBinding**:
`DecisionProcessSpec` 与一个可执行环境之间经验证的语义对应关系。
_Avoid_: Wrapper, import path

**BehavioralTest**:
通过轨迹、反事实或不变量判断环境行为是否符合已批准语义的检查。
_Avoid_: Smoke test, import test

**TrainingRun**:
在一个 `EnvironmentBinding` 上按冻结训练合同优化策略的 `Run`。
_Avoid_: Experiment, model file

**EvaluationRun**:
在冻结且隔离的评价合同下衡量候选工件或策略的 `Run`。
_Avoid_: Training validation, debugging run

## 实验比较

**Suite**:
围绕一个冻结 benchmark task、资产边界和评价协议组织的实验单元。
_Avoid_: Test folder, method

**TaskCardVariant**:
同一 `Suite` 内预先登记的一份 `TaskCard` 实例，以稳定身份参与配对比较。
_Avoid_: Method variant, prompt tweak

**Method**:
接受相同 `TaskCard` 与预算、并产生候选工件的待比较生成系统。
_Avoid_: Task-card variant, RL algorithm

**ClarificationOracle**:
在受控实验中依据 gold semantics 一致回答允许澄清问题的固定回应者。
_Avoid_: Human participant, evaluator

**SealedEvaluationAsset**:
只供最终评价使用、且在生成和修复期间不可见的 gold 或 hidden 资产。
_Avoid_: Validation fixture, public test
