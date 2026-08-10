# Triage labels

GitHub Issues 使用以下五个默认 workflow-state labels。一个 issue 同一时刻最多保留一个此表中的状态标签；产品、领域或优先级标签不属于本词汇。

| Label | 含义 | 退出条件 |
|---|---|---|
| `needs-triage` | 新进入、尚未完成范围与证据判断 | 已转为下列四种明确状态之一 |
| `needs-info` | 缺少会改变范围、设计或验收结果的信息 | 所需信息已获得并重新 triage |
| `ready-for-agent` | 范围、验收标准、依赖与权限足够明确，可由 agent 实施 | 开始实施后由当前工作流维护，或发现新的信息缺口 |
| `ready-for-human` | 下一步需要人类判断、凭据、外部 dashboard 或不可委托操作 | 人类完成动作并给出可验证结果 |
| `wontfix` | 已明确决定不实施，且理由应保留 | 只有新的事实或用户决定才允许 reopen |

## Triage 规则

1. 从 issue body 和权威来源核对事实、复现性、范围、依赖与安全影响。
2. 需要信息时提出最少且高影响的问题，并使用 `needs-info`。
3. `ready-for-agent` 必须具有可测试的完成标准和已解析的 blocking dependencies。
4. 凭据输入、账户批准、法律判断或外部 cutover 路由到 `ready-for-human`。
5. 使用 `wontfix` 时保留简短、可审计的决定理由，不把无法复现自动等同于不修复。

不得擅自创建近义标签。仓库当前若缺少这些 labels，先报告差异并获得用户授权，再通过 GitHub 写入。
