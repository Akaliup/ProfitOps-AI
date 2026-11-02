# 真实语义检索对比：BGE-M3 vs Hashing（2026-11-02）

本报告记录一次**固定查询集、固定阈值、真实执行**的检索对比。BGE-M3 在本机不可用，状态为 `BLOCKED`；没有用随机向量、mock 或其他 embedding 冒充 BGE-M3，也没有把 hashing 结果写成语义模型结果。

## 1. 目标与边界

- 语料：`backend/app/knowledge.py` 的 6 篇合成 SOP（广告预算、退款排查、履约成本、平台手续费、数据完整性、审批与幂等）。
- 查询：`backend/evals/rag_queries.json`，42 条固定查询：36 条可回答（每篇 SOP 6 条中文改写）+ 6 条不可回答。
- 标签在任何 embedding 生成之前固定；运行后没有改标签、删查询或调阈值。
- 每个 Provider 使用独立临时 SQLite，执行生产 `seed_knowledge` 与生产 `search_knowledge`；不读写 `data/profitops.db`。
- 相同参数：`top_k=3`、`candidate_k=20`、`min_score=0.15`、相同的文档与查询；没有 reranker，没有 pgvector/Chroma/Milvus。
- 评测对象是**检索链路**（入库、切分、embedding、混合打分、`no_answer`、chunk 引用有效性），不是 LLM 回答质量；本次没有让 LLM 生成答案。

## 2. 环境与状态

| 项目 | 实际值 |
|---|---|
| 日期 | 2026-11-02（UTC 报告时间 2026-11-01T17:29:17Z） |
| Embedding 服务 | 本机 Ollama，`http://127.0.0.1:11434/v1` |
| 目标模型 | `bge-m3` |
| BGE-M3 状态 | **BLOCKED** |
| 阻塞原因 | `DomainError: Embedding 服务返回 HTTP 404`（模型未安装） |
| `ollama list` 实际可用模型 | `qwen3.5:4b`、`qwen3-embedding:0.6b` |
| 是否用其他模型替代 | **否**。`qwen3-embedding:0.6b` 不是 BGE-M3，不能作为替代结果。 |
| Hashing 基线 | COMPLETED（真实离线执行） |
| 网络/外部业务副作用 | 0；只访问本机 Ollama 的 `/v1/embeddings` |

`ollama pull bge-m3` 在下载约 55% 时失败：重定向目标解析为非公网地址 `198.18.0.18`，客户端拒绝继续。该失败是环境/网络限制，不是代码错误。

## 3. 指标定义

| 指标 | 定义 |
|---|---|
| hit@1 | 可回答查询中，top-1 命中目标文档的比例 |
| hit@3 | 可回答查询中，top-3 含目标文档的比例 |
| MRR | 目标文档排名倒数的平均值（只计可回答查询） |
| no-answer accuracy | 不可回答查询中，`no_answer=true` 且 hits 为空的比例 |
| answerable false no-answer | 可回答查询被错误拒答的数量 |
| citation validity | 返回 chunk 全部属于当前 active 文档片段的比例 |
| retrieval latency | 单次检索 P50/P95/均值（本机 SQLite + 当前 Provider） |
| query embedding latency | 单条查询向量生成 P50/P95/均值 |
| embedding generation | Seed 阶段向量生成墙钟时间、调用次数、向量数、每向量毫秒 |

## 4. Hashing 基线结果（真实执行）

| 指标 | 结果 |
|---|---:|
| 文档数 / chunk 数 | 6 / 32 |
| 查询数 | 42（36 可回答 / 6 不可回答） |
| hit@1 | **97.22%**（35/36） |
| hit@3 | **100%**（36/36） |
| MRR | **0.9815** |
| no-answer accuracy | **100%**（6/6） |
| answerable false no-answer | 0 |
| citation validity | **100%** |
| 检索延迟 P50 / P95 / 均值 | **6.805 ms / 8.108 ms / 6.789 ms** |
| 查询向量延迟 P50 / P95 / 均值 | **0.175 ms / 0.207 ms / 0.171 ms** |
| Seed 向量生成 | 墙钟 52.173 ms；embed 0.023 s；48 次调用；74 个向量；0.311 ms/向量 |
| 维度 | 256 |

### 4.1 失败案例

唯一未在 top-1 命中的查询：

| 查询 | 期望文档 | 实际 top-1 | 正确文档排名 | 判定 |
|---|---|---|---|---|
| `quality-03`：数据完整性检查要覆盖哪些维度和字段 | 数据完整性与缺失处理 | 履约成本异常排查 | 第 3 名 | `rank_miss_at_1`；hit@3 通过 |

没有为提升 hit@1 调整权重、阈值或查询集。该失败保留在脱敏 JSON 的 `hashing.failures` 中。

## 5. BGE-M3 结果（BLOCKED）

| 指标 | 结果 |
|---|---|
| 状态 | **BLOCKED** |
| 真实错误 | `Embedding 服务返回 HTTP 404` |
| 指标 | `null` |
| 查询结果 | `[]` |
| 失败案例 | `[]`（没有生成向量，不能产生检索排名） |
| 是否伪造/替代 | 否 |

因此本报告**没有** BGE-M3 的 hit@1、hit@3、MRR、延迟或向量生成数据，也不做“BGE-M3 更好/更差”的结论。BGE-M3 与 hashing 的对比表只有状态，没有数值。

## 6. 对比结论（当前可陈述的范围）

| 项目 | Hashing | BGE-M3 |
|---|---|---|
| 状态 | COMPLETED | BLOCKED |
| hit@1 | 97.22% | 不可用 |
| hit@3 | 100% | 不可用 |
| MRR | 0.9815 | 不可用 |
| no-answer | 100% | 不可用 |
| citation validity | 100% | 不可用 |
| P50 检索 | 6.805 ms | 不可用 |

可以陈述：**在 6 篇合成 SOP、42 条固定查询、固定阈值下，hashing 基线达到 hit@1 97.22%、hit@3 100%、no-answer 100%、引用有效性 100%。** 这证明检索/拒答/引用链路在该合成集上工作，不代表通用语义检索准确率。

不能陈述：BGE-M3 的准确率、BGE-M3 相对 hashing 的提升、真实业务语料上的效果、PDF/OCR、reranker、文档级 ACL 或大规模向量库能力。

## 7. 复现

```bash
# 1. 确认本机模型；若没有 bge-m3，报告会如实记录 BLOCKED
ollama list

# 2. 可选：尝试拉取（本机因非公网重定向失败）
ollama pull bge-m3

# 3. 运行对比；每次 Provider 使用独立临时 SQLite
PYTHONPATH=backend .venv/Scripts/python.exe backend/evals/rag_real.py --provider both \
  --json docs/rag-real-embedding-2026-11-02.json

# 4. 要求 BGE-M3 必须可用时，可用硬门禁退出码
PYTHONPATH=backend .venv/Scripts/python.exe backend/evals/rag_real.py --provider bge-m3 --require-bge-m3
```

报告文件：`docs/rag-real-embedding-2026-11-02.json`。其中 `bge_m3.status=BLOCKED`、`blocked_reason` 为真实 HTTP 404；`hashing.metrics` 为真实执行结果。

## 8. 后续建议

1. 在可访问 Ollama 模型仓库的环境安装 `bge-m3` 后重跑同一固定查询集，保持相同阈值，先不调参。
2. 增加真实业务 SOP 与人工标注查询，单独报告领域内效果。
3. 如果 BGE-M3 可用，再评估 reranker、混合权重与多语言查询；这些能力当前均未实现或未验证。
