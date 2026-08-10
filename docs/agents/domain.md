# Domain documentation

## 布局

AutoMarkov 使用 single-context domain model：

- 根目录 `CONTEXT.md`：唯一 ubiquitous-language glossary。
- `docs/adr/`：系统级且难以逆转的架构决定。

除非仓库演变为具有独立语言和所有权边界的真实多 context 系统，并经用户确认，否则不创建 `CONTEXT-MAP.md` 或子目录 glossary。

## 更新规则

- 讨论或实现领域变更前读取 `CONTEXT.md`，在 prompt、spec、schema、代码和测试中使用 canonical term。
- 新术语必须属于 AutoMarkov 领域，定义控制在一至两句，并用 `_Avoid_` 收敛歧义同义词。
- `CONTEXT.md` 只定义概念，不记录 package、class、path、schema field、runtime command、实现状态或项目计划。
- 当用户使用的词与 glossary 冲突时，先指出冲突并澄清，再更新术语；不要静默重定义。
- ADR 仅记录同时满足以下条件的决定：难以逆转、缺少上下文会令人意外、且确实在多个合理选项间做了权衡。
- 实验参数、工作包、验收标准和阶段状态属于 spec 或 experiment plan，不进入 glossary。

每次领域文档变更都检查术语是否在一个位置定义、ADR 是否引用 canonical term，以及新的表述是否引入了第二套同义语言。
