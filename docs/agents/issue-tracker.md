# Issue tracker

## 配置

- Provider：GitHub Issues
- Repository：`JiaWANG-TJ/AutoMarkov`
- Remote：`https://github.com/JiaWANG-TJ/AutoMarkov.git`
- CLI：`gh`

## 工作合同

- `triage`、`to-spec` 与 `to-tickets` 使用 GitHub Issues，不在仓库中建立平行的本地 backlog。
- 读取 issue 时保留 issue number、title、body、labels、state、assignees、milestone、linked pull requests 与 blocking relationships。
- 发布 ticket 前先确认它是当前已接受 spec 的 tracer bullet，写清目标、非目标、验收标准、验证命令与显式 blocker。
- GitHub 写操作只在用户明确授权的工作流中执行。未获授权时可以准备 issue body，但不能创建、编辑、关闭、reopen、assign 或 relabel issue。
- 不使用 issue comment 代替可持久化的领域术语或架构决定；术语进入 `CONTEXT.md`，符合条件的决定进入 `docs/adr/`。
- 完成实现不自动关闭 issue。只有验收标准和所需验证均已满足，且用户授权更新 tracker 时才改变 issue state。

## 常用只读命令

```bash
gh issue list --repo JiaWANG-TJ/AutoMarkov
gh issue view <issue-number> --repo JiaWANG-TJ/AutoMarkov
```

需要写入时，先向用户展示目标 repository、issue number、拟执行动作和完整内容。
