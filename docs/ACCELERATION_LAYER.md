# Acceleration Layer

The acceleration layer pre-materializes usage facts into scheduled aggregate tables so the dashboard avoids live `ACCOUNT_USAGE` scans on every load.

## Serving contract

| Dependency kind | Source | Served window |
|---|---|---|
| `windowed_aggregate` | Scheduled aggregate table | Through the prior UTC day (`utc_today - 1`) |
| `global_cached` | Long-lived cache or materialized global table | Current-state metadata; no date cap |
| `direct_only` | Live `ACCOUNT_USAGE` | Requested window; not aggregate-eligible |

### Settled-day rule

The current UTC day is excluded from aggregate serving because `ACCOUNT_USAGE` can backfill rows after the nightly snapshot task has finished. A force refresh updates the snapshot but does not make today's rows eligible for aggregate serving.

### Windowed aggregate sources

Nine windowed sources use scheduled tables. Model Rates is the tenth table, described below:

- CoCo: `SNOWFLAKE_COCO_USAGE_HISTORY`
- Cortex AI Functions
- Cortex Agents
- CoWork (facts + service mix)
- Cortex Search (daily, serving, batch)
- Warehouse Attribution

### Direct usage and global metadata

- **Cortex Inference API**: usage always reads `ACCOUNT_USAGE` directly. Pricing uses the captured Model Rates result, which can come from the global aggregate or direct rate query.
- **Model rates**: a nightly global aggregate built from the same 90-day CoCo rate math. The cache key includes the successful task timestamp. Missing, unhealthy, empty, or unreadable tables fall back to the exact live rate query.
- **User tags**: current-state `TAG_REFERENCES`; cached 1 hour independently of the date window.

## Why scheduled tables

The layer uses regular tables refreshed by tasks with `INSERT OVERWRITE`, read through a standard
warehouse.
Two more obvious designs were tried first and rejected. Both checks ran against a live account
with `ACCOUNTADMIN` on August 17, 2026; recheck them before revisiting the design, since platform
limits can change.

- **Dynamic Tables over `ACCOUNT_USAGE`: rejected.** Creating a dynamic table on these views
  failed with `Error in secure object`. `ACCOUNT_USAGE` views are secure views, and dynamic
  tables need change tracking on their sources. The same bodies worked as `CREATE TABLE ... AS
  SELECT` and `INSERT OVERWRITE`, including the `UNION ALL`, aggregate, and multi-source join
  shapes.
- **Interactive warehouse for reads: rejected.** An interactive warehouse can query only
  interactive tables, not standard tables, dynamic tables, or views. Reads therefore use the
  app's standard query warehouse, and the refresh tasks run on their own standard task warehouse.
- **Cortex Inference API: not materialized.** Its usage query binds model rates as literals, so
  it has no stable body to store; it always reads `ACCOUNT_USAGE` directly.

The original plan targeted Dynamic Tables, so some identifiers still carry that name:
`DtBodySpec`, `DT_BODY_REGISTRY`, and the `"dt"` source mode all refer to these scheduled tables.

## Page-to-dependency registry

Every navigable page declares its data dependencies in `utils.queries.PAGE_REFRESH_PLANS`. The registry is the single source of truth for:

- Source-mode dialog copy in the loading/refresh modal
- Cache-key identity (page change + source mode change both trigger a re-warm)
- Offline tests that verify no undeclared dependency appears

## Freshness

The sidebar and Acceleration Settings show the latest successful completion among aggregates with an existing table, an active task, and a recorded success. This includes global Model Rates. One recent completion does not establish freshness or ingestion completeness for every source. The tasks run nightly at 01:00 UTC.

## Provisioning without Acceleration Settings

`sql/config.sql` provisions the entire acceleration layer from SQL: the task warehouse, every aggregate table and scheduled task, an immediate refresh of each task, and seeded `CORTEX_DASHBOARD_CONFIG` rows (including the `owned_objects` registry). Its DDL is generated from the same `DtBodySpec` builders (`create_aggregate_table_sql`, `create_refresh_task_sql`) Acceleration Settings calls, so it cannot drift from what the app provisions. Because it seeds `owned_objects`, Acceleration Settings shows the resources as app-owned (no untracked-objects warning) and in-app teardown works.

Run it with `make acceleration:provision` (see [Operations](OPERATIONS.md#bootstrap-is-rerunnable)). The script replaces tasks and resets settings, so the command runs only when the config table holds no settings. `make acceleration:status`, `:refresh`, `:suspend`, `:resume`, and `:teardown` act on the same `owned_objects` registry.

## Upgrade existing warehouse attribution tasks

AI Functions warehouse membership uses distinct query IDs from
`SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AI_FUNCTIONS_USAGE_HISTORY`. The source starts on
January 5, 2026; earlier attribution is incomplete. Hourly usage timestamps and
completion flags do not restrict query membership.

New installations receive this change through the shared builders and `sql/config.sql`.
Existing Snowflake tasks retain their deployed SQL when the app is updated. Plan a
targeted change to the warehouse-attribution task and refresh its table. Do not reprovision
the entire acceleration layer. Multiple app instances can read the same table.

## Page-scoped warming

Only pages that display surface data warm caches. Reference SQL, Query Acceleration SQL, and Acceleration Settings declare no data dependencies, so they never open the loading dialog; they read only the sidebar date range.

Initial page loads warm the dependencies declared by that page. In Streamlit, **Reload results**
evicts recorded active-page query keys, including user tags, while leaving Model Rates and
unrelated query/window keys cached. In App Runtime, administrators can bypass the active-view
result cache; other viewers can refetch while still receiving cached results. Both preserve
Model Rates; see [reload boundaries](DASHBOARD_METRICS.md#reload-and-reference-sql-boundaries).
Each dependency keeps its own aggregate generation. A changed generation selects new cache keys
for readers of that dependency. Both Acceleration Settings views show routing and diagnostics.

## Documentation pages

**Reference SQL** shows the **baseline** read query behind each surface for the selected window: the query the dashboard runs when acceleration is **not** enabled, straight from `SNOWFLAKE.ACCOUNT_USAGE`. It always shows these direct queries regardless of acceleration state, so it stays a stable reference for the non-accelerated path.

Reference SQL excludes in-memory filters and comparison-window expansion. Nullable rates
render as SQL `NULL`; displayed statements have not all been executed in a worksheet.

**Query Acceleration SQL** owns the acceleration path: for each aggregate surface it shows the read the dashboard runs when acceleration is on, the materialization body, the `CREATE TABLE`/`CREATE TASK` DDL, task lifecycle operations (resume, run, suspend, drop), and read-only validation queries. Cortex Inference API usage is intentionally not materialized; its rate literals are constructed from the separately loaded Model Rates result.

### Semantic differences between direct and aggregate reads

- **Window bound.** Dashboard windows end at the prior UTC day. Today's partial data is read only by the explicit Today so far action, which always uses direct sources. Aggregate reads are capped at the **prior UTC day** (`today - 1`) because `ACCOUNT_USAGE` backfills the current day, so a nightly snapshot of it would show incomplete numbers as if final.
- **Grain and columns.** Aggregate tables store the surface's fact query pre-aggregated to its `GROUP BY ALL` grain; the read is a plain `SELECT ... WHERE USAGE_DATE` range over that table, so it cannot expose columns the body did not persist.
- **Model rates.** The Model Rates aggregate is global, computed over a fixed 90-day CoCo window and read whole, independent of the dashboard date selector.

## Force refresh

The **Refresh aggregate tables** button in Acceleration Settings queues eligible active, registered tasks. Queued does not mean completed. It does not make today's data aggregate-servable; the settled-day rule still applies after completion.

## Fallback

When readiness checks do not select an aggregate, the router uses direct `ACCOUNT_USAGE` reads without necessarily setting a degradation reason. If a selected aggregate read fails, the loader retries directly and records a fallback message. Model Rates also retries directly for an empty aggregate result. Its task is queued at provisioning, without waiting for the next nightly schedule.
