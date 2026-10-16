# Dashboard Metrics

## Coverage

The overview combines CoCo, CoWork token credits, Cortex Agents token credits,
Cortex Search daily credits, Cortex AI Functions credits, and estimated Inference
API credits. It is not an invoice total. It excludes warehouse compute, storage,
data transfer, and billing categories not represented by those sources, such as
standalone Analyst, fine-tuning, and provisioned throughput. Embedded tool costs
may already be included in agent credits.

Reported and estimated credits are separate. Inference estimates use observed
CoCo model rates, not a billing guarantee. A model having an observed rate does
not prove that every token type has a rate. Coverage checks each token type
against the same captured rate snapshot used to price both periods. Unaccounted
token totals remain unrated. Missing rates suppress credit deltas and label the
estimate partial; entirely unrated usage displays Unavailable, not free usage.

Both dashboards describe these as tokens that could not be priced, not missing
usage. Warnings use exact counts from the currently filtered period, name the
affected credit totals, and explain that tokens and requests are still counted.
Overview also states that Reported credits are unaffected by the pricing gap.
If any unpriced-token count is missing, the warning says pricing coverage and the
count are unknown instead of treating missing counts as zero. A small unpriced
count is never rounded into a claim that 100% of tokens are priced. Comparison
captions explain why a gap in either period prevents a credit-change percentage.
No missing credit amount is inferred from the token count.

A failed overview source is listed as unavailable. The remaining observed total
is partial, not a zero for the unavailable source. Successfully reading a source
does not certify ingestion completeness.

Every included source contributes a coverage record, even if it returns no rows
or no acquisition metadata. Missing metadata remains unknown. Dedicated-page
source failures display Unavailable with a diagnostic and a refresh action;
auxiliary failures do not remove a successful main-source headline.

## Reload and Reference SQL boundaries

In App Runtime, an administrator can **Reload results** for the active view, bypassing
its server result cache without refreshing the separate model-rate cache. Other viewers
can **Refetch cached results**; that request can still reuse a successful cached result
for its 30-minute TTL. Neither action refreshes aggregate tables or source ingestion.
Streamlit clears recorded active-page dependency query keys, including user tags, while
preserving the separate Model Rates cache. Shared matching keys can then refresh for other
viewers; unrelated SQL and window keys are not cleared.

Reference SQL shows baseline direct-source SQL for the selected window. Nullable rates
render as SQL `NULL`, but the displayed statements do not encode in-memory filters,
expanded comparison reads, or accelerated routing. The local display/rendering checks
do not establish that every generated statement was executed in a worksheet.

## Filters

Every record belongs to one of three disjoint partitions:

- **Matching:** all selected dimensions are supported, known, and match.
- **Not attributable:** no known mismatch, but at least one selected dimension is
  unsupported or unknown.
- **Excluded:** at least one known dimension does not match.

Known mismatches take precedence over unknown dimensions. Unknown user/model
values never act as wildcard matches. With no filters, all records match.
Selected user tags use current user-tag references, not historical tags at request
time. A successful lookup with an empty resolved user list means no tagged users matched.
An unavailable lookup with active tags remains unknown, not an inactive filter or zero
matches. Known mismatches on other selected dimensions still exclude those records.

CoWork and Agents main facts do not support a model filter. Search whole-service
credits do not support user, model, or user-tag attribution. Dedicated pages label
their non-attributable view explicitly; these records never enter a matching
overview total. CoWork granular service mix does not support instance/agent
attribution at its current aggregate grain.

Warehouse components are scoped by dates and service only. Request/query identity
in a warehouse row is not proof that user/model/page filters were applied.
CoWork SQL metadata and warehouse attribution can overlap; do not add them.
CoWork metadata zero can also mean absent or delayed attribution because the
existing aggregate schema does not preserve null coverage.

## Counts

- CoCo **Usage records** count model-expanded request rows, not distinct requests.
- AI Functions **Usage records** count hourly function/model records. A long-running
  invocation can span multiple records. `IS_COMPLETED=false` means not completed
  in that aggregation window, not a failed call.
- Agents and CoWork **Requests** count main request records. Root/child counts use
  parent-request identity.
- Inference API **Requests** count request rows.
- Search daily and serving **Usage records** are daily/hourly metering records.
  Search's primary metrics use credits, services, and embedding tokens, not calls.
- Search batch records can have multiple consumption types for a query. The batch
  detail groups by query before joining warehouse attribution.

The physical `CALLS` column is retained for compatibility. There is no meaningful
cross-service call total or cross-service credits-per-call ratio.

## Shares And Precision

Share bars use a fixed 0-100% scale and the full matching denominator before top-N
truncation. `Other` sums additive credit amounts, not distinct user counts.
User/agent tag groups overlap; their percentages are not additive.

Missing KPI values display **Unavailable**. True zeros remain zero. Positive
credits smaller than 0.01 display **<0.01** in compact metrics. Detail data retains
numeric values for sorting and CSV export. Missing batch warehouse attribution
stays blank rather than being filled with zero.

## Source Timing

These are documented ACCOUNT_USAGE bounds, not measured freshness guarantees:

- CoCo, Agents and CoWork main usage: up to one hour.
- Search daily: up to three hours; serving and batch: up to one hour.
- Standard warehouse attribution: up to eight hours; adaptive query metering:
  up to one hour. Membership sources can add uncertainty to classification.
- CoWork SQL metadata: up to eight hours, excluding adaptive warehouses.
- AI Functions: running-query updates have a five-minute SLA, which is not used
  as a universal historical-completeness bound. History starts January 5, 2026;
  user/role/query-tag attribution is available after February 16, 2026.
- Inference API: the reference view and catalog do not specify a latency bound.
  Completeness remains unknown rather than assuming the generic guide's latency.

History is retained for up to 365 days, subject to source introduction dates.
Reference: [ACCOUNT_USAGE catalog](https://docs.snowflake.com/en/sql-reference/account-usage)
and its linked source-view definitions, reviewed September 20, 2026.

## Date Windows

Presets end yesterday UTC: 7d means seven completed calendar days, not six
completed days plus today. Custom ranges require both endpoints and an explicit
Apply after editing. Separate Start date and End date inputs use native `start`
and `end` URL parameters. The `range` parameter remains unchanged.
Invalid or incomplete edits leave the last valid applied view unchanged. With no
valid applied view, usage reads remain blocked and reference pages remain accessible.

The period resolver separates today from the completed interval and derives an
adjacent previous interval of equal length. Main service facts load both periods
together, apply the same filters, and display only the current interval. A
percentage and absolute change appear only when the
source evidence covers both intervals; zero baselines show New usage without a
percentage, and comparable both-zero intervals show 0%.
An empty current period retains its previous-period amount and shows -100% only
when coverage verifies both periods. Otherwise it shows zero observed records
with an explicit warning that absence does not establish zero usage.

Cached query results retain acquisition start and completion times. Aggregate
generations participate in cache keys. A task completion timestamp alone does not
identify its source-read watermark: aggregate completeness remains unknown and
its delta is suppressed. Missing read timestamps are not replaced with the current
time. Unknown source latency likewise suppresses comparisons.

Calendar series fill missing days with zero only where coverage is verified.
Other missing days remain null. Sparklines are omitted when they cannot represent
those gaps safely. Per-day captions label their values as observed averages and
state the calendar-day denominator.

Today so far requires an explicit Load button. It reads direct sources without
changing acceleration settings and never enters completed totals or comparisons.
Its scope is global filters only, with a visible note that page-specific selectors
do not apply. Historical custom windows do not offer a today read.

Page filters use facts from both comparison periods without loading Overview
sources on dedicated pages. The warm path runs each statement once. Streamlit
connector-spy fixtures exercise the real builders, cache, warm
gate and page. In direct mode, Overview uses 8 calls, CoCo/Agents/AI Functions/CoWork 2 each,
Search 1, Inference 3 and Warehouse 3. Accelerated Warehouse uses 1 call in these fixtures.
These counts include tags where applicable and warehouse probe/overlap diagnostics, but
exclude connection preflight, the UTC
clock and configuration/health reads. Reference SQL, Query Acceleration SQL, and
Acceleration Settings pages load no usage facts. Warm filter changes add no calls in these
fixtures; live timing is unverified.
Warehouse detail, CoWork service mix, and Search serving/batch detail require an
explicit native control selection. Their reads are excluded from default-page
counts. Search detail adds serving, batch and warehouse probe/overlap reads.

### Custom date links

For example, `?range=Custom&start=2026-09-20&end=2026-09-20` represents a same-day
selection. Separate parameters keep both endpoints when they are the same date, which a
single repeated parameter would lose to duplicate removal.

A valid legacy link with two `dates` values converts once to `start` and `end`
before the bound controls render. New endpoint parameters take precedence. A legacy
link with only one date stays incomplete; the app never guesses the missing date.

Native URL binding reflects draft inputs before Apply. Results in the current
session retain the last applied range until submission. A complete link opened in
a new session initializes that session's applied range. Partially edited links
remain blocked when no valid range has been applied.

## Navigation And Display

Sidebar labels are independent of dependency keys; all eleven routes are preserved.
Clear filters resets global selections and the active page's local filters. Reset
view also restores 7d, default breakdown, trend and chart controls. Other pages'
local filters are not cleared. Absent URL filter values remain explicit selections
with a warning rather than silently broadening the result.

Previous-period charts align equal calendars by relative day, keeping exact dates
in the table and tooltip. Unknown daily values remain gaps. Table mode uses the same
numeric data, and Overview driver shares use the full denominator before top-N.

Reload behavior is described in
[Reload and Reference SQL boundaries](#reload-and-reference-sql-boundaries).
The Refresh aggregate tables action on Acceleration Settings is separate; Queued does not mean completed.

Failed reads can retain the last successful matched values within the same verified
account, role, period and filter scope. These values are marked stale and receive
no comparison delta. Snapshots are session-local and bounded; changing account or
role clears them.

## Operational Status

Ready requires an existing table, active task and recent successful refresh without
probe errors. Stale snapshot means the last success is older than 26 hours; this is
a dashboard warning threshold, not a Snowflake SLA. Unknown probes remain Unknown.
Latest failed attempts remain visible even when an older successful snapshot exists.
Read-source selection is intentionally unchanged: suspended resources can still
exist while the router falls back to direct usage reads.

Lifecycle actions require both an exact configured name and its ownership-registry
entry. Task state is rechecked server-side. Failed drops remain registered, and
explicit retries include failed targets only. Provisioning refuses existing
unregistered targets and queues every initial table refresh without claiming ready.
