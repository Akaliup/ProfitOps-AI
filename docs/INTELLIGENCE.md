# Multi-store weekly intelligence (phase one)

This increment reuses `orders`, `daily_operations`, the existing profit bridge, tenant-scoped authentication, audit records, LangGraph, and the read-only tool registry. Migration `0004_intelligence` adds `source_records` (immutable normalized source rows and batch/row provenance) and `weekly_reports` (tenant-scoped report snapshots); `0005_weekly_chat` adds tenant-scoped chat threads and messages. `0003_import_batches` supplies batch history. Apply migrations to an existing database with `alembic -c backend/alembic.ini upgrade head` using the configured database URL. Do not delete the running database.

## Import

Upload UTF-8 CSV as the request body with a Bearer token; preview is read-only, commit requires `approver`. The data import page provides a file picker, the first five parsed rows, optional JSON field mapping, validation results, and batch history. An invalid row blocks the entire file and is recorded as a rejected batch; `accepted_rows` is zero on a blocked commit. Re-uploading identical natural keys skips those rows; changed values for the same key are rejected. Store and SKU must already belong to the authenticated tenant. Only USD is accepted. Decimal USD inputs are converted to integer cents. A missing cost is never inferred from ad attribution or sales.

| Kind | Required CSV columns | Optional columns |
| --- | --- | --- |
| orders | `store_id,order_id,order_date,sku,quantity,gross_sales,cogs` | `refund_amount,platform_fee,shipping_fee,other_fee,discounts,currency` |
| ads | `store_id,date,sku,campaign_id,ad_spend` | `campaign_name,attributed_sales,attributed_orders,clicks,impressions,complete` |
| inventory | `store_id,snapshot_date,sku,on_hand,lead_time_days,safety_stock_days,moq,case_pack` | `reserved,inbound_quantity,inbound_eta` |

The canonical order headers `date,sales,refunds,platform_fees,fulfillment,other_costs` are also accepted. Optional money and quantity columns default to zero. `complete` defaults to false: an ad campaign file alone does not establish a complete daily ledger. The older `operations` CSV remains available through `/api/imports`. Current `orders` natural key includes tenant/store/SKU/order ID; ads include tenant/store/SKU/date/campaign ID; inventory includes tenant/store/SKU/snapshot date. Use `?mapping={"store_id":"shop"}` with URL encoding for a canonical-to-input-column map. The UI accepts the same JSON mapping. The imported rows, original row numbers, and batch IDs remain queryable as report provenance.

Examples: [orders](examples/intelligence/orders.csv), [ads](examples/intelligence/ads.csv), [inventory](examples/intelligence/inventory.csv). These dates and values are synthetic; run against a fresh demo database to avoid conflicts with other imported facts.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/data/template/{orders|ads|inventory}` | CSV header template |
| `POST /api/data/{orders|ads|inventory}/preview` | Validate and preview without writes |
| `POST /api/data/{orders|ads|inventory}/commit` | Atomic import and batch record; approver only |
| `GET /api/imports`, `GET /api/imports/{id}` | Tenant-scoped batch history and error rows |
| `GET /api/inventory?as_of=YYYY-MM-DD&store_id=...&risk=...&lookback_days=28` | Deterministic SKU inventory risks |
| `POST /api/weekly-reports` | Save a report; accepts `end_date` (completed UTC Sunday), optional `store_id`, `use_llm`, and `inventory_policy` |
| `GET /api/weekly-reports`, `GET /api/weekly-reports/{id}` | Tenant-scoped history and saved report |
| `GET /api/weekly-reports/{id}/messages` | Read the tenant-scoped chat thread and messages; does not create a thread |
| `POST /api/weekly-reports/{id}/messages` | Ask one evidence-bound question about the saved snapshot |

`get_weekly_intelligence` and `get_inventory_risks` are read-only Agent/MCP tools. The report reuses `fetch_metrics` and `build_report` for current/previous seven-day totals, SKU deltas and the eight-term profit bridge. Inventory uses separate store/SKU snapshots and `Decimal` daily demand, excludes inbound units from current sellable stock, checks ETA against coverage and lead time, and rounds reorder units to MOQ and case pack. `inventory_policy` configures lookback, observation and snapshot-age thresholds, review horizon and HIGH/MEDIUM buffers. With fewer than seven distinct order days (no feed coverage declaration) or stale inventory, risk is `INSUFFICIENT_DATA` and reorder quantity is withheld. Empty days are not silently counted as confirmed zero sales.

The saved report contains bridge evidence, inventory source IDs, import batch/row references and a ledger fingerprint. LLM explanation is optional and uses structured claims with known evidence IDs; invalid claims, model failure, no model configuration or incomplete ledger coverage leave deterministic results visible with an explicit fallback. The weekly page displays store comparison, SKU diagnostics, inventory filters, profit drivers and citations.

## Follow-up chat

After a report is generated or reopened, the weekly page can ask bounded follow-up questions against that immutable snapshot. One tenant-scoped thread is kept per report and user plus assistant messages are capped at 20. The model must first call the read-only `get_saved_weekly_report` tool with the route-bound report ID. It cannot use live ledger tools, read another report, or access execution tools; the tool is intentionally absent from the MCP exposure list and the global analysis allowlist.

Only original snapshot evidence plus two deterministic derived IDs are citable: `{store_id}:store` for a store comparison row and `{store_id}:sku:{sku}` for a SKU diagnostic row. Derived IDs are computed when the snapshot is read and are never written back to `WeeklyReport.body`. Every successful claim needs at least one known evidence ID, and every Arabic number in the response must be present in the cited evidence. Only known monetary fields and profit-bridge amounts may be converted from integer cents to two-decimal USD; inventory counts, days, MOQ and order counts are not converted. Identifiers such as SKUs, dates and evidence IDs are masked before numeric extraction. Chinese number characters are rejected for quantitative follow-up answers.

No model key, model failure, unknown evidence, invented number, forbidden tool or invalid output leaves the saved report intact and stores an explicit `deterministic_fallback` with `claims=[]`. Out-of-scope or insufficient-evidence answers are stored as `refused`. Evidence is projected to citation IDs, sources, dates and required numeric/risk fields; order-ID lists, repeated formulas and redundant summaries stay in the original saved report. The previous comparison period is shared in `metrics.previous_period`. The official dual-store CSV example fits the default 16 KB limit, including the tool envelope and untrusted-data notice. The complete citable evidence list is never truncated: larger snapshots that exceed `LLM_TOOL_RESULT_MAX_BYTES` still fail closed and degrade.

Thread creation and the initial cap check use a short transaction. The LLM and read-only tool loop run outside write transactions. A second short transaction locks the thread, rechecks the cap and atomically saves the user/assistant pair with its audit entry. If concurrent requests fill the thread during model execution, the losing request returns `weekly_chat_limit` (422) without saving its answer. Inventory and aggregate profit-bridge citations highlight their visible rows; other original evidence remains accessible in the evidence details panel.

## Local demonstration

Run `python scripts/dev.py` (web `http://127.0.0.1:5180`, API docs `http://127.0.0.1:8010/docs`) with a fresh demo database. In Data Import, choose Orders and upload `orders.csv` above; preview and commit as approver. Repeat for Ads and Inventory, then open Weekly Intelligence and set the UTC week ending `2020-01-12`. The two stores and their imported rows appear in the report. Inventory risk is marked insufficient until enough observed sales days exist; upload a full, complete operations feed and sufficient historical orders to enable an evidence-supported LLM explanation. With no configured `LLM_API_KEY`, the deterministic report remains available and follow-up questions return the fixed `not_configured` fallback rather than invented analysis. With a configured model, ask a question after selecting the target store and click any citation to open and highlight the matching evidence or derived store/SKU row. Re-upload the same files to verify `noop` without duplicate ledger rows; inspect the batch history, report evidence and chat audit for traceability.

Run the offline, synthetic verification with:

```bash
.venv/Scripts/python.exe backend/evals/weekly_chat_replay.py
```

The replay uses the production tool loop and post-validation path, makes zero network calls and writes `backend/evals/reports/weekly-chat-latest.json`. It is a regression check for citations, numbers, refusals and fallback states, not evidence of real-model accuracy.
