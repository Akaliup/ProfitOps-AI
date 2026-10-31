# CLAUDE.md

本文件是 Claude Code 在本仓库的工作入口。所有规则以 `AGENTS.md` 为准；本文件只做摘要，不覆盖或放宽 `AGENTS.md` 的安全、契约和 PR 规范。

## 当前协作模式

- Claude 自主开发 + GitHub CI + Codex Review + 人工合并。
- 阶段范围与验收条件见 `docs/ROADMAP.md`；当前进度见 `docs/STATUS.md`。
- 每个阶段从最新 `master` 开独立功能分支，向 `master` 提 PR；不直接推送 `master`。
- 用户批准阶段后，自主完成分析、拆分、实现、测试、修复、本地提交、推送和创建 PR。
- 阶段完成后停止新增功能，等待用户确认合并或批准下一阶段。

## 审查循环

- 用 `gh` 读取 CI、普通评论、行内评论和 Codex 审查摘要；每轮记录 Codex Review 对应的 commit SHA。
- Review 落后于最新提交时，对最新提交重新请求审查。
- 每阶段最多两轮自主 Review 修复；仍有阻断问题则停止并报告。
- P0/P1 问题自行修复并补回归测试；误报需给出证据。
- CI 通过或 Codex 无发现都不自动合并；PR 由人工合并。

## 不可违反

- 不泄露密钥，不提交 `.env`、数据库、缓存和构建产物。
- 不 force push、不 `reset --hard`、不清理未提交修改、不直接推送 `master`。
- 不自动部署；所有执行仅为 simulation。
- 真实 LLM 测试必须有调用预算；普通 CI 只用 Mock，不调用付费模型。

## 阶段汇报

只给：PR URL、Git Commit、CI 结果、Codex Review 结果与对应 SHA、真实测试证据、未解决风险、是否建议合并。详细过程写入 PR 与 `docs/STATUS.md`。
