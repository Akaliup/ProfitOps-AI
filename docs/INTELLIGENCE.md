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

Examples: [orders](examples/intelligence/orders.csv), [ads](examples/intelligence/ads.csv), [inventory](examples/intelligence/inventory.csv). Platform-shaped copies live under [adapters](examples/intelligence/adapters/) and are offline column maps only. They do not connect a seller account. These dates and values are synthetic. The order files include a prior week (2020-01-01 through 2020-01-05; 2019-12-30 and 2019-12-31 fall before the importer's 2000-01-01 floor) so the week ending 2020-01-12 can show a real volume/price/mix split. That split is an accounting identity, not a verified business cause. Run the files against a fresh demo database to avoid conflicts with other imported facts.

`GET /api/data/adapters?kind=orders|ads|inventory` lists presets. Preview and commit accept `adapter=<id>`. The server applies that preset unless the request also sends `mapping`, which replaces the preset and is validated again. Unknown adapters, a kind mismatch, and invalid mapping JSON return 422 and write nothing. Preset ids are `amazon-orders`, `shopify-orders`, and `amazon-ads`. There is no inventory adapter.

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

The saved report contains bridge evidence, inventory source IDs, import batch/row references and a ledger fingerprint. LLM explanation is optional and uses structured claims with known evidence IDs; invalid claims, model failure, no model configuration or incomplete ledger coverage leave deterministic results visible with an explicit fallback. The weekly page displays store comparison, SKU diagnostics, inventory filters, profit drivers, the sales decomposition, read-only price suggestions, campaign rows, and citations.

### Sales decomposition

`metrics.decomposition` explains the **gross sales** change. It does not replace the eight-term net-profit bridge and is not equal to `net_profit` term by term. With units in both periods:

- volume effect = (current units − previous units) × previous portfolio ASP
- price effect = sum over continuing SKUs of (current ASP − previous ASP) × current units
- mix effect = SKU-level volume − portfolio volume effect
- residual = sales change − volume − price − mix, in integer cents after half-up rounding

ASP is sales ÷ units. If either period has zero units, status is `INSUFFICIENT_DATA`, ASP is null, and no effect is invented. A SKU with units on only one side has no comparable ASP: its full sales change stays in the SKU volume term (new SKU sales, or the negative of discontinued SKU sales) and the gap versus the portfolio volume effect becomes mix. No ASP is imputed. `volume + price + mix + residual` equals `sales_change`. Evidence ids are `ev-volume`, `ev-price`, `ev-mix`, and `ev-residual`, or `{store_id}:ev-volume` and the same suffixes for one store.

### Price suggestions

`pricing_suggestions` uses rule version `pricing-rules-v1`. The function is read-only: it does not write a price or call a marketplace. High refunds stay at the current price (`observe_refunds`). Low margin can suggest 95% of the baseline. High or critical inventory risk with enough margin can suggest 105%. Missing units, non-positive net sales, or a variable-cost total of zero returns `INSUFFICIENT_DATA` and `suggested_price: null`, because a zero total does not prove that cost columns were imported. A caller that has proved every cost component was observed and is really zero may pass `costs_known=true`. Evidence id: `{store_id}:sku:{sku}:pricing`.

### Campaign diagnostics

Ads in the report week are grouped by store and `campaign_id`. Evidence id: `{store_id}:campaign:{campaign_id}`. `attributed_sales` is not added to the profit bridge or to order sales. The sum of campaign `ad_spend` in the current week matches the ledger `ad_spend` for the official sample. Incomplete ad rows (`complete=false`) do not mark the daily ledger complete.

## Follow-up chat

After a report is generated or reopened, the weekly page can ask bounded follow-up questions against that immutable snapshot. One tenant-scoped thread is kept per report and user plus assistant messages are capped at 20. The model must first call the read-only `get_saved_weekly_report` tool with the route-bound report ID. It cannot use live ledger tools, read another report, or access execution tools. MCP also exposes this read-only tool with server-side tenant filtering; it remains absent from the global analysis allowlist.

Citable ids are the original snapshot evidence plus deterministic derived ids. Derived ids are computed when the snapshot is read and are never written back to `WeeklyReport.body`. They are:

- `{store_id}:store` for a store comparison row
- `{store_id}:sku:{sku}` for a SKU diagnostic row, including current and previous `sales`, `units`, and `net_profit` (not order-id lists)
- `{store_id}:sku:{sku}:pricing` for a read-only price suggestion
- `{store_id}:campaign:{campaign_id}` for campaign spend and attributed sales
- `ev-volume`, `ev-price`, `ev-mix`, `ev-residual`, and the same ids prefixed with `{store_id}:`

Every successful claim needs at least one known evidence ID, and every Arabic number in the response must be present in the cited evidence. Only known monetary fields and profit-bridge amounts may be converted from integer cents to two-decimal USD. Those fields include `asp`, `suggested_price`, `current_price`, `attributed_sales`, and `sales_change`, as well as the ledger money fields and bridge amounts. `units`, `clicks`, `impressions`, `attributed_orders`, coverage days, and reorder quantities are not converted. `refund_rate_pct` and `margin_pct` are already percentages: `15` and `15.0` are both accepted, and they are not divided by 100. Identifiers such as SKUs, dates and evidence IDs are masked before numeric extraction. Chinese number characters are rejected for quantitative follow-up answers. When decomposition status is `OK`, the weekly explanation prompt asks the model to cite `ev-volume`, `ev-price`, and `ev-mix` (and `ev-residual` if the residual is mentioned). The explanation still may not contain digits.

No model key, model failure, unknown evidence, invented number, forbidden tool or invalid output leaves the saved report intact and stores an explicit `deterministic_fallback` with `claims=[]`. Out-of-scope or insufficient-evidence answers are stored as `refused`. Evidence is projected to citation IDs, sources, dates and required numeric/risk fields; order-ID lists, repeated formulas and redundant summaries stay in the original saved report. The previous comparison period is shared in `metrics.previous_period`. The official dual-store CSV example fits the default 16 KB limit, including the tool envelope and untrusted-data notice. The complete citable evidence list is never truncated: larger snapshots that exceed `LLM_TOOL_RESULT_MAX_BYTES` still fail closed and degrade.

Thread creation and the initial cap check use a short transaction. The LLM and read-only tool loop run outside write transactions. A second short transaction locks the thread, rechecks the cap and atomically saves the user/assistant pair with its audit entry. If concurrent requests fill the thread during model execution, the losing request returns `weekly_chat_limit` (422) without saving its answer. Inventory and aggregate profit-bridge citations highlight their visible rows; other original evidence remains accessible in the evidence details panel.

## Local demonstration

Run `python scripts/dev.py --demo-db` (web `http://127.0.0.1:5180`, API docs `http://127.0.0.1:8010/docs`). The flag prints and uses `data/demo-copy.db`. It does not clear or migrate `data/profitops.db`. Without the flag, the process uses Settings / `.env`, which defaults to `data/profitops.db`. In Data Import, choose Orders and upload `orders.csv` above; preview and commit as approver. Repeat for Ads and Inventory, then open Weekly Intelligence and set the UTC week ending `2020-01-12`. The two stores, both ad campaigns, and a volume/price/mix decomposition appear. The decomposition numbers are synthetic. Inventory risk stays insufficient until enough observed sales days exist. With no configured `LLM_API_KEY`, the deterministic report remains available and follow-up questions return the fixed `not_configured` fallback rather than invented analysis. With a configured model, ask a question and click any citation to highlight the matching evidence, including `ev-volume`, `ev-price`, a pricing row, or a campaign row. Re-upload the same files to verify `noop` without duplicate ledger rows.

Run the offline, synthetic verification with:

```bash
.venv/Scripts/python.exe backend/evals/weekly_chat_replay.py
```

The replay uses the production tool loop and post-validation path, makes zero network calls and writes `backend/evals/reports/weekly-chat-latest.json`. It is a regression check for citations, numbers, refusals and fallback states, not evidence of real-model accuracy.
