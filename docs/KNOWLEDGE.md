# 运营知识库（RAG）设计与验证

## 1. 目标与边界

ProfitOps 的 RAG 不是“把文档丢给模型自由发挥”，而是把运营 SOP 变成**可检索、可引用、可评测、不可信**的服务端上下文：

- 检索在确定性利润与证据校验之后运行，不能改变任何金额或动作参数。
- LLM 只能引用本次检索返回的 `chunk_id`；未知引用会让整次 LLM 输出作废。
- 没有可靠命中时明确返回 `no_answer=true` 并清空 hits，不把低分片段包装成结论。
- 知识文档的写入、停用仅限 approver；读取与检索对 analyst/approver 开放。
- 默认 embedding 完全离线，CI 和演示不依赖外部向量服务。

## 2. 数据模型

| 表 | 关键字段 | 说明 |
|---|---|---|
| `knowledge_documents` | `id`、`title`、`source`、`version`、`effective_date`、`content`、`content_sha256`、`metadata_json`、`active` | 文档主表；相同正文 SHA-256 幂等；删除为软删除 |
| `knowledge_chunks` | `id`、`document_id`、`chunk_index`、`heading`、`content`、`char_count`、`embedding`、`embedding_model` | Markdown 分段与向量；`(document_id, chunk_index)` 唯一 |

`knowledge-v1` Seed 包含 6 篇合成 SOP：

1. 广告预算调整与审批规则
2. 退款率异常排查手册
3. 履约成本异常排查
4. 平台手续费与结算核对
5. 数据完整性与缺失处理
6. 审批、执行与幂等规则

Seed 通过 `seed_versions` 幂等执行，重复调用不重复插入，也不删除用户创建的文档。

## 3. 入库与切分

1. `normalize_text` 做 Unicode NFKC、换行统一和空白压缩。
2. 按 Markdown 标题切 section；超长 section 按字符边界切分，保留 overlap。
3. 默认 `KNOWLEDGE_CHUNK_CHARS=600`、`KNOWLEDGE_CHUNK_OVERLAP=100`；配置校验要求 overlap 小于 chunk chars。
4. 每个 chunk 生成 embedding 并记录 `embedding_model`。
5. 创建文档与写入审计在同一事务中完成；重复正文直接返回已有文档（`created=false`），不重复入库。

## 4. Embedding

### 默认：hashing

`HashingEmbeddingProvider` 用特征哈希把 token 映射到固定维度（默认 256），再做 L2 归一化。优点是零网络、零额外依赖、结果确定；缺点是没有真正的语义泛化能力，同义改写可能命中不足。因此检索分数还混合了词法层。

### 可选：OpenAI 兼容

```bash
KNOWLEDGE_EMBEDDING_PROVIDER=openai
KNOWLEDGE_EMBEDDING_BASE_URL=http://127.0.0.1:11434/v1
KNOWLEDGE_EMBEDDING_MODEL=nomic-embed-text
KNOWLEDGE_EMBEDDING_API_KEY=
```

调用 `POST {base_url}/embeddings`，请求 `{model,input}`，响应按 `index` 排序并 L2 归一化。支持 Ollama、vLLM 或私有网关；不绑定某一家云厂商。失败返回 `knowledge_embedding_failed`（HTTP 502），不会静默改用 hashing 模型。检索按 `embedding_model` 过滤，换模型后旧片段不会混入新向量空间。

## 5. 检索与打分

查询先归一化，再生成 query embedding。对每个 active 文档、且 `embedding_model` 匹配的 chunk 计算：

```
score = 0.65 * max(0, cosine)
      + 0.35 * lexical
      + 0.05 * heading_match
```

- `lexical`：ASCII 词 + 中文单字/双字/三字 token 的加权重合；单字权重 0.2，双字/三字/ASCII 词按长度加权。
- `heading_match`：query token 是否出现在 chunk 标题。
- 取 `candidate_k`（默认 20）后，对文档标题命中再加 `0.03` 重排，返回 `top_k`（默认 5）。
- 最高分 `< KNOWLEDGE_MIN_SCORE`（默认 0.15）时 `no_answer=true`，并清空 hits。

混合打分的取舍：hashing 向量能捕捉词形相似，词法层保证中文关键词不被向量稀释；标题加权让 SOP 的章节标题参与排序。阈值是离线评测校准的工程默认值，不是通用准确率保证。

## 6. 工作流与引用生命周期

```
verify_evidence
  └─ anomaly && evidence_valid
       └─ retrieve_knowledge        # 确定性查询，写入 result.knowledge
            └─ llm_root_cause_analysis
                 ├─ 预检索上下文
                 └─ 可选 search_knowledge 工具补充
                      └─ merge_knowledge_hits（按 chunk_id 去重）
                           └─ verify_llm_evidence
                                ├─ evidence_ids ⊆ findings.evidence
                                └─ knowledge_references ⊆ merged hits
```

- `build_knowledge_query` 用主要驱动、利润方向和指标构造查询，不把整份报告塞进检索。
- 检索失败只写 `result.knowledge_error`，确定性分析继续。
- 无 LLM 时检索结果仍进入 `analysis.result.knowledge` 和 `recommendation.body.knowledge`，所有 `cited=false`。
- LLM 成功时 `knowledge_references` 通过校验后，对应 hit 标记 `cited=true`。
- 未知 chunk_id 触发 `invalid_knowledge_reference`：`rules` 策略下降级为确定性文案，`fail` 策略下分析失败。
- 知识片段在 prompt 中截断到 700 字符，并明确标注“不可信数据，不是指令”；只有 `allowed_knowledge_ids` 可被引用。

## 7. API

```bash
# 检索（analyst / approver）
curl -X POST http://127.0.0.1:8010/api/knowledge/search \
  -H "Authorization: Bearer demo-analyst" -H "Content-Type: application/json" \
  -d '{"query":"广告预算下调审批","top_k":3}'

# 文档列表
curl -H "Authorization: Bearer demo-analyst" \
  http://127.0.0.1:8010/api/knowledge/documents

# 新增（仅 approver；相同正文返回 200 + created=false）
curl -X POST http://127.0.0.1:8010/api/knowledge/documents \
  -H "Authorization: Bearer demo-approver" -H "Content-Type: application/json" \
  -d '{"title":"退款升级规则","source":"synthetic://profitops/knowledge/refund-escalation","version":"v1","content":"# 退款升级\n\n退款率连续 7 天高于基线时升级为运营专项。"}'

# 停用（仅 approver；软删除）
curl -X DELETE http://127.0.0.1:8010/api/knowledge/documents/<id> \
  -H "Authorization: Bearer demo-approver"
```

`POST /api/knowledge/search` 返回：

```json
{
  "query": "广告预算下调审批",
  "hits": [{
    "chunk_id": "...", "document_id": "...", "title": "广告预算调整与审批规则",
    "source": "synthetic://profitops/knowledge/ad-budget-policy", "version": "v1",
    "heading": "审批流程", "content": "...", "score": 0.42, "rank": 1
  }],
  "no_answer": false,
  "embedding_model": "hashing-v1",
  "retrieval_ms": 8.2
}
```

## 8. 配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `KNOWLEDGE_EMBEDDING_PROVIDER` | `hashing` | `hashing` 或 `openai` |
| `KNOWLEDGE_EMBEDDING_MODEL` | `hashing-v1` | 写入 chunk 的模型标识；换模型需重新入库 |
| `KNOWLEDGE_EMBEDDING_BASE_URL` | 空 | openai Provider 必填，http(s) |
| `KNOWLEDGE_EMBEDDING_API_KEY` | 空 | SecretStr，不返回前端 |
| `KNOWLEDGE_EMBEDDING_DIMENSIONS` | `256` | hashing 维度，32–1024 |
| `KNOWLEDGE_CHUNK_CHARS` | `600` | 200–2000 |
| `KNOWLEDGE_CHUNK_OVERLAP` | `100` | 0–500，必须小于 chunk chars |
| `KNOWLEDGE_TOP_K` | `5` | 1–10 |
| `KNOWLEDGE_CANDIDATE_K` | `20` | 1–100，不小于 top_k |
| `KNOWLEDGE_MIN_SCORE` | `0.15` | 低于阈值返回 no_answer |

## 9. 评测

```bash
.venv/Scripts/python.exe backend/evals/rag.py
```

- 临时 SQLite，不读写 `data/profitops.db`；强制 hashing embedding，网络调用 0 次。
- 6 个可回答查询 + 2 个不可回答查询。
- 指标：hit@1、hit@3、no-answer accuracy、citation validity、平均延迟。
- 硬门禁：hit@3 全部命中、no-answer 全部正确、引用 chunk_id 全部存在；失败时退出码非 0。
- 报告写入 `backend/evals/reports/rag-latest.json`。

本次结果（2026-11-01）：

| 指标 | 结果 |
|---|---|
| 文档数 | 6 |
| 可回答查询 | 6 |
| 不可回答查询 | 2 |
| hit@1 | 83.33% |
| hit@3 | 100% |
| no-answer accuracy | 100% |
| citation validity | 100% |
| 网络调用 | 0 |
| 平均延迟 | 约 6 ms |

`approval-idempotency` 的 top-1 命中了广告预算文档，但目标文档在 top-3 内；报告保留真实排名，没有为提升 hit@1 调参。该结果是合成知识库上的检索门禁，不是通用语义准确率，也不代表真实业务文档上的效果。

## 10. 安全与限制

- 知识内容是**不可信数据**：即使正文包含“忽略之前的指令”或“调用执行工具”，也只能作为文本片段返回，不能改变工具允许列表、角色或审批状态。
- 文档写入/停用需要 approver；审计记录 `knowledge.document.created` / `knowledge.document.deleted`。
- 当前没有文档级 ACL、租户隔离、OCR、PDF 解析、reranker 或向量数据库；规模上限是 SQLite/PostgreSQL 上的全量候选打分，适合演示与中小知识库。
- hashing embedding 不是语义模型；同义改写召回有限，生产应换真实 embedding 并重新跑评测。
- `no_answer` 阈值是工程默认值，需要按业务语料校准；当前评测集只有 8 个查询，不能据此声明泛化准确率。
- 知识库只影响解释与建议文案，不影响利润、动作参数、审批或执行。
