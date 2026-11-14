# 评测报告目录

- `rag-split-latest.json`：当前 RAG 拆分评测（hashing，校准集 26 条 + 冻结测试集 24 条，weighted 与 hybrid_rrf 双策略对比）。2026-11-13：校准集 hit@1/hit@3 100%、MRR 1.0000；测试集 hit@1 85%、hit@3 95%、MRR 0.8917、nDCG@5 0.9065、no-answer/citation 100%、跨文档 coverage@3 66.67%，网络调用 0。由 `backend/evals/rag.py --dataset split` 生成，每次运行覆盖。
- `rag-split-20261113.json`：上述拆分评测的带日期冻结副本；测试集保留 `test-quality-03`、`test-cross-01` 的 top-1 未中与 `test-cross-02` 的 top-3 未中。
- `rag-latest.json`：历史 RAG 检索评测（hashing，42 条旧集合，weighted 与 hybrid_rrf 双策略对比）。2026-11-03：hit@1 97.22%、hit@3 100%、MRR 0.9815、nDCG@5 0.9861、no-answer 100%、citation validity 100%，网络调用 0。仅由 `--dataset legacy` 更新，不再代表当前测试集。
- `rag-bge-m3.json`：历史同一套 42 条查询、`bge-m3`（本机 Ollama，Q8_0 GGUF 导入，1024 维，`min_score=0.42`）：hit@1 100%、MRR 1.0000、no-answer 100%、跨文档覆盖 hybrid 50% / weighted 25%；阈值校准建议 0.4164。
- `rag-qwen3-embedding.json`：历史同一套 42 条查询、真实 embedding（`qwen3-embedding:0.6b`，`min_score=0.40`）：hit@1 100%、MRR 1.0000、no-answer 100%、跨文档覆盖 hybrid 75% / weighted 50%。本机 Ollama，非公网调用。
- `offline-20261031-032645.json`：当前评分器下的最终离线报告；25 次运行，正常 4/4，负向对照 21/21，真实网络调用 0。
- `offline-20261031-032133.json`：评分器最后修正前（订单计数收紧前）的离线报告；25 次运行，正常 4/4，负向 21/21，保留用于对照。
- `offline-20261031-031638.json`：`max_tokens` 改为 4000 后、评分器修正前的离线报告；25 次运行，正常 4/4，负向 21/21，保留用于对照。
- `real-20261031-023033.json`：2026-10-31 首轮真实 DeepSeek 试点原始执行记录；3 次调用，广告/履约 `llm_success`，退款 `llm_invalid_json`，数据不完整案例跳过。
- `real-20261031-023033-regraded.json`：用首轮已保存输出、当前评分器重评；**没有新增真实调用**。广告/履约通过全部内容硬门禁；首轮退款仍失败（`expected_status`）。
- `real-20261031-023951.json`：用户要求重跑退款后的原始执行记录；1 次真实调用，退款 `llm_success`；原始评分 0/1，原因是两个评分器误判。
- `real-20261031-023951-regraded.json`：用退款重跑已保存输出、当前评分器重评；**没有新增真实调用**，1/1 通过，13/13 硬门禁通过。
- `real-20261031-030304.json`：`max_tokens=3000` 三次重复的原始执行记录；9 次真实调用，6 次 `llm_success`，3 次 `finish_reason=length` 的截断/空内容；原始评分 4/9（另有两个评分器误判）。
- `real-20261031-030304-regraded.json`：用 3000 三次重复已保存输出、当前评分器重评；**没有新增真实调用**，6/9 通过；履约 3/3、广告 2/3、退款 1/3，已包含稳定性指标。
- `real-20261031-031646.json`：`max_tokens=4000` 三次重复的原始执行记录；9 次真实调用，9 次 `llm_success`、`finish_reason=stop`；原始评分 6/9（三个评分器误判）。
- `real-20261031-031646-regraded.json`：用 4000 三次重复已保存输出、当前评分器重评；**没有新增真实调用**，9/9 通过；广告、履约、退款均 3/3，13 项硬门禁全部 1.0。

更早时间戳的 `offline-*.json`（包括 `offline-20261031-023422.json`、`offline-20261031-024242.json`、`offline-20261031-024707.json`、`offline-20261031-024934.json`、`offline-20261031-025110.json`、`offline-20261031-030727.json`）是评分器修正前的中间运行，保留用于审计，不作为最终结论。`docs/EVALUATION.md` 引用 `offline-20261031-032645.json`、首轮真实报告/重评报告、退款重跑/重评报告、3000 三次重复/重评报告和 4000 三次重复/重评报告。
