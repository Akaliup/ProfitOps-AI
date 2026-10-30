## 变更摘要

<!-- 说明这个 PR 解决了什么问题，以及为什么需要这次变更。 -->

## 关联任务

<!-- 关联 issue、阶段或文档；没有则填写“无”。 -->

## 实际验证

请只勾选实际执行过的检查，并在下方粘贴关键结果。

- [ ] `python -m pytest backend/tests -q`
- [ ] `npm --prefix frontend test`
- [ ] `npm --prefix frontend run build`
- [ ] `python scripts/smoke.py`（涉及 HTTP/前端流程时）
- [ ] `docker compose config --quiet`（涉及 Compose 时）
- [ ] 真实 LLM 调用（如执行，注明次数、模型和报告路径；自动 CI 不执行）
- [ ] 未执行项已在下文明确标注

验证结果：

```text
<!-- 粘贴实际输出摘要，不要粘贴 API Key、token 或 .env 内容。 -->
```

## 安全与边界

- [ ] 未提交 `.env`、API Key、数据库、缓存、构建产物或本机运行数据
- [ ] 所有执行仍为 simulation，没有真实广告、采购、支付或外部系统副作用
- [ ] 金额仍以 USD 整数美分存储和传输
- [ ] 服务端仍决定角色，未信任客户端角色请求头
- [ ] 审批与执行仍依赖条件更新、事务和唯一约束
- [ ] 未在自动 CI 中调用真实 LLM

## 契约与文档

- [ ] 契约变更已同步 `docs/CONTRACT.md`、后端测试和前端类型
- [ ] 涉及评测的变更已同步 `docs/EVALUATION.md`
- [ ] 新增限制或未验证项已写入相关文档

## 审查提示

请重点检查金额守恒、证据 ID 可追溯性、LLM 失败/降级可见性、审批执行幂等和敏感信息。自动审查工具的评论只是辅助，不替代人工审查。
