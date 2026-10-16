# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-10-09

### Added

- `web/`: a Next.js port of the dashboard for Snowflake App Runtime, deployed side by side
  with the Streamlit apps as `COST_MGMT_DB.PUBLIC.AI_CONSUMPTION_WEB_TEST`. Same SQL builders
  (byte parity against `tests/fixtures/golden_sql.json`), same aggregation (numeric parity
  against `tests/fixtures/agg_cases.json`), same page paths and URL parameters. Acceleration
  Settings supports the same admin mutations as the Streamlit page (toggle, Setup, provision,
  suspend/resume/refresh, teardown) behind the `DASHBOARD_ADMIN_USERS` allowlist.
  See [the App Runtime guide](docs/APP_RUNTIME.md).
- A [Code of Conduct](CODE_OF_CONDUCT.md) (Contributor Covenant 2.1), a pull request template,
  and issue-reporting guidance in [CONTRIBUTING](CONTRIBUTING.md#reporting-issues).

### Changed

- The administration page heading now reads **Acceleration Settings** in both views, matching
  its navigation label; it previously read "Config". Related in-app text names the page the
  same way.
- Refresh local dependencies to the supported stable graph verified on September 30, 2026:
  Next.js 16.3.8, React 19.3.0, Streamlit 1.64.0, and Python connector 4.8.0.
  Preserve Python 3.11, Node 22 support, and the hosted Streamlit lower-bound policy.
- Migrate shared web tables to TanStack Table 9.2.4 without changing their public props,
  rendering, CSV export, or sorting behavior. Add browser checks for ordering and refreshed rows.
- Upgrade `snowflake-sdk` to 3.4.0, replacing the reported vulnerable TOML 3.0.0 dependency
  with 5.0.0. The September 30, 2026 npm audit reported zero advisories.
  This maintenance does not deploy to TEST or production.
- Explain unpriced Inference API tokens in both dashboards using exact filtered counts,
  missing pricing context, affected credit totals, and the distinction from missing usage.
  Unknown counts stay unknown; small gaps no longer display as 100% priced. Clarify
  missing-rate comparison captions and AI Functions non-token billing help text.
- Add a root `Makefile` as the supported command interface; run `make` for the list (colored on
  a terminal, plain when piped or with `NO_COLOR`) and see [Operations](docs/OPERATIONS.md).
  It keeps the Python, web, documentation, and full validation gates, adds independent web
  lint/typecheck/format/test/build/e2e steps, an explicit
  `WEB_COMPILER` (turbopack or webpack), and a mocked browser run against the dev or production
  server on a configurable loopback port. Offline web builds receive synthetic SDK options only
  on the build command. The unreleased `Taskfile.yml` and the inert GitHub Actions workflow are
  removed; validation is local.
- Add guarded account lifecycle commands: read-only `inspect`, rerunnable `bootstrap`, opt-in
  least-privilege `grants` to existing roles, acceleration status/provision/refresh/suspend/resume
  and owned-resource teardown, and Streamlit status and app-only teardown. New mutations validate
  identifiers, verify `EXPECTED_ACCOUNT`, and are dry runs until `CONFIRM` names the exact target.
  Teardown acts only on registered objects and stops on ambiguous ownership.
- `make grants` gives `ADMIN_ROLE` the privileges both deploy commands need: create privileges on
  the schema, compute-pool usage, and `SNOWFLAKE.PYPI_REPOSITORY_USER`. It also gives
  `VIEWER_ROLE` usage on current and future apps. `GRANT_ACCOUNT_USAGE=1` adds the
  `USAGE_VIEWER`, `GOVERNANCE_VIEWER`, and `SECURITY_VIEWER` database roles to the admin role.
  [Operations](docs/OPERATIONS.md) adds a new-installation sequence, a full-uninstall
  procedure, and a safe-to-rerun column, all checked against a fresh install.
- Live previews now listen on `127.0.0.1`, run with an empty admin allowlist, and drop process
  credential variables. The browser test server never reuses an existing server.
- Replace the repository-specific Streamlit deployment wrapper with `make deploy:streamlit`.
  Streamlit deployment sends `pyproject.toml`, not the local `uv.lock`.
  The command uses the standard CLI replace/prune path, then attaches
  `SNOWFLAKE.SNOWPARK.PYPI_SHARED_REPOSITORY`, which `snowflake.yml` cannot declare. The removed
  wrapper's backup, recreation-confirmation, and publish safeguards are not provided.

- State the newest recorded usage day per source on every usage page when it precedes
  the requested window end, so empty trailing days are attributed to the ACCOUNT_USAGE
  view rather than to the dashboard. Computed from the rows already loaded; no new query.
- Show "User tags" instead of the raw `user_tags` key in the loading dialog, and tell
  a denied viewer which username was detected and that access is granted by the
  owner-managed allowlist, not by Snowflake role.
- Restore `streamlit[snowflake]>=1.61.0` as the only declared dependency. Exact pins
  differing from the container's running Streamlit made the platform rewrite the venv
  under the process and mix two Streamlit versions.
- Re-import `utils` modules at the top of the entrypoint when their files changed
  after they were imported. Hosted containers boot from an old file snapshot and
  receive the committed files afterwards; the entrypoint is re-read each run but
  imported modules are not, which left new entrypoints calling old modules.
- Make `utils` a regular package with `utils/__init__.py`. As a namespace package it
  merged every `utils/` directory on the hosted module path, letting a stale copy
  supply old modules beside new ones. The fingerprint now lists each loaded module's
  origin and warns when they span more than one directory.
- Read-only running-build fingerprint: project version, runtime-file digest, Streamlit
  and connector versions, connection TTL and process import time, shown on the
  Acceleration Settings page before the admin gate and as a build digest in the sidebar.
  It warns when runtime files on disk are newer than the process's imports, the
  hosted failure mode where a committed version leaves cached modules stale.
- Reserve a bounded native scrolling area for loading outcomes and suppress the
  nested Snowflake query spinner so progress stays inside the loading dialog.
- Reuse bounded, scoped acquisitions, including failed reads, across loading,
  filters and pages. Explicit reload retries failures; expiry reopens the load gate.
- Guarantee loading-dialog closure after native widget registration, including page early exits.
- Consolidate task health into one scoped history query, cache reconciliation, and
  give the UTC clock one five-minute cache.
- Remove provisioning's warehouse switch and serialize shared connection operations.
  Require authenticated allowlisted administrators. Both shipped administrator lists are
  empty; the deployment operator must configure intended viewers explicitly.
- Isolate the explicitly requested partial-day component in a native fragment.
- Resolve settings context from the current connection without SQL round trips.
  Cache operational health for five minutes; Reload results, Refresh status, and
  lifecycle actions invalidate it in Streamlit. Web administrators bypass the active-view
  result cache; viewer refetch can reuse it. See [reload boundaries](docs/DASHBOARD_METRICS.md#reload-and-reference-sql-boundaries).
- Restore native loading dialogs with readable source progress, retaining exact-scope
  stale results and closing only after bound filters have been re-registered.
- Parse numeric connection TTL values as seconds, not nanoseconds, through one shared
  connection helper. This prevents near-immediate connection expiry in hosted runtimes.
- Group navigation without changing existing routes; move contextual controls into
  main content and add view-specific clear/reset behavior.
- Add native Chart/Table modes, relative-day previous-period trends, fixed service
  colors/dashes, and ranked Overview drivers with full-denominator top-N shares.
- Replace duplicate headline banners and bordered section cards with native metrics
  and headings. Improve theme contrast and provide dark-mode resource status colors.
- Restrict Streamlit result reload to recorded page dependency keys and report actual source
  outcomes instead of marking unavailable sources successful.
- Defer auxiliary warehouse, CoWork service mix, and Search serving/batch reads until
  explicitly selected. Show exact-scope retained values during reload and after
  failure, with separate unscoped usage and no retained comparison delta. Preserve
  native filter URLs after reload and cancel page-specific refresh on navigation.
- Separate operational readiness from table presence. Retain probe errors and latest
  task attempts; use a documented 26-hour snapshot warning threshold.
- Validate exact registered lifecycle targets, persist partial failures for explicit
  retry, add teardown cancellation, and disable mutations during setup editing.
- Preserve completed object outcomes if configuration persistence fails. Retry only
  failed provisioning steps after rechecking targets and ownership; report warehouse
  context failures without starting surface DDL. Discard client-only setup drafts
  when leaving edit mode.
- Make selected native control labels inherit readable light/dark theme text while
  preserving the primary button color and selection border/fill.
- Bottom-align shared filter actions and page-specific filter rows. Keep absent-value
  warnings with their controls so warnings and wrapped selections do not break alignment.

- Carry coverage evidence for every Overview source, including empty, unknown,
  and failed reads. Dedicated and auxiliary sources report unavailable states
  without replacing failures with zero usage.
- Load only page-owned dependencies and derive filter options from both periods.
  Remove duplicate prefetch execution; retain warehouse baseline rows until scope
  and comparison evaluation. Verify connector call counts with cold-path fixtures.
- Check Inference rate coverage by token type using one captured snapshot, carry
  missing-rate evidence into Overview, and preserve prior-only calendar series.
  Search hourly gaps remain null; observed per-day captions state their denominator.
- Use separate Start date and End date inputs within the custom-date Apply form.
  Native `start` and `end` URL parameters preserve same-day links after reload.
  Import complete legacy `dates` links without guessing missing endpoints.
- Date presets now end yesterday UTC. Custom ranges use an Apply action and do
  not query incomplete date selections. Compare adjacent equal completed periods
  instead of splitting the current window at its midpoint; suppress deltas when
  source coverage is unverified.
- Preserve cached source acquisition evidence and aggregate generation identity.
  Keep unknown calendar gaps null and make direct partial-today reads opt-in.
- Preserve the last valid date window after invalid custom edits. Keep prior-period
  amounts visible when the current period is empty, with -100% shown only for
  covered comparisons. Add full-entrypoint tests for date and empty states.
- Separate matching, non-attributable, and excluded usage consistently across
  global and page filters. An unavailable user-tag lookup remains non-attributable for
  otherwise matching users; a successful empty lookup still means no matching users.
- Render missing model rates as SQL `NULL` in displayed Reference SQL. Web administrators
  can bypass active-view result caching with Reload results; other viewers may refetch
  while still receiving a cached result. Model-rate caching stays separate.
- Show reported and estimated overview credits separately, disclose source gaps,
  and remove heterogeneous cross-service calls and credits-per-call metrics.
- Label CoCo and AI Functions counts as usage records; distinguish incomplete
  hourly records from failures. Search uses service counts rather than calls.
- Separate warehouse and CoWork metadata costs with explicit scope and overlap
  notices. Missing warehouse attribution is no longer displayed as zero.
- Use full-denominator percentage bars, additive Other rows, and unavailable or
  below-precision formatting. Add offline rendered-page and scope regression tests.

### Removed

- The unused `target_lag_minutes` setting, left over after the Target lag control was removed
  from Acceleration Settings. Saving settings and `sql/config.sql` no longer write it; an
  existing row in the config table is ignored.

## [0.2.0] - 2026-08-21

### Added

- **feat(sql):** add a **Query Acceleration SQL** documentation page cataloguing every aggregate surface with the accelerated read, build body, table/task DDL, lifecycle operations, and read-only validation queries, all generated from the app's builders. Adds `runtime_key` to `SqlReference`, plus `reference_statement_for`, `acceleration_materialization_for`, and hardened `inline_params` (escapes quotes, preserves `?` inside values), used by that page.
- **test(sql):** add `tests/test_reference_sql_sources.py` and `tests/test_acceleration_sql.py` covering source resolution, settled-window caps, materialization parity, and lifecycle SQL.

- **feat(perf):** warm caches cumulatively by dependency and only on pages that display surface data. Documentation and settings pages (Reference SQL, Query Acceleration SQL, Config) declare no dependencies and never open the loading dialog; data pages warm only the dependencies not already warm for the current window and aggregate generation, so switching between already-warm tabs loads nothing. Adds `dependency_generation` and `dependencies_to_warm` to `utils/queries.py`.
- **docs(ui):** clean AI-writing tells from user-facing page copy (captions, help text, info banners) across the surface pages: replaced em dashes and `--` dash-substitutes with commas/periods/colons and removed a negative-parallelism ("not just a surface total"). Code comments and developer docstrings were left unchanged.
- **docs:** rename the files in `docs/` to `UPPERCASE_UNDERSCORE` (`ACCELERATION.md`, `BRANCH_PROTECTION.md`, `CONSUMPTION_ATTRIBUTION_ARCHITECTURE.md`) and update the `CONTRIBUTING.md` link; clean em-dash writing tells from `ACCELERATION.md` and `CONSUMPTION_ATTRIBUTION_ARCHITECTURE.md`, so definition lists read as `term: definition`.
- **feat(cowork):** clarify single-user attribution on the CoWork page. When every request in the window rolls up to one user, the **By user** breakdown now notes that attribution is present with just one user, so a one-row table is not mistaken for missing attribution.
- **feat(config):** surface per-loader warm timings and the resolved source/snapshot on the Config tab so slow-tab reports are measurable.
- **feat(config):** add `sql/config.sql` to provision the full acceleration layer (warehouse, aggregate tables, scheduled tasks, seeded settings with an `owned_objects` registry) without the Config tab; its DDL is generated from the same builders the app uses.
- **test(perf):** add `tests/test_warm_scope.py` covering page-scoped warm dependencies, aggregate dependency selection, statement filtering, per-page snapshot identity isolation, per-dependency generation, and the cumulative warm decision.

- **feat(acceleration):** materialize Model Rates in a nightly global aggregate with freshness-keyed reads, empty/read-error fallback to the existing live query, and immediate bootstrap task execution.
- **feat(acceleration):** add `PAGE_REFRESH_PLANS`, `DEPENDENCY_KINDS`, and `RefreshPlan` registry to `utils/queries.py` classifying every page dependency as `windowed_aggregate`, `global_cached`, or `direct_only`.
- **feat(acceleration):** add `effective_served_window`, `latest_snapshot_freshness`, `aggregate_snapshot_identity`, and `dep_source_mode` helpers to `utils/config.py` implementing the settled-day snapshot contract.
- **feat(acceleration):** add `effective_window` routing wrapper to `utils/queries.py`; windowed aggregate reads are capped at `utc_today - 1`; direct-mode reads are unchanged.
- **feat(acceleration):** resolve the active page from `st.navigation` before the cache gate; include page key, source mode, and snapshot freshness in the warmed-window identity.
- **feat(config):** display the latest successful aggregate snapshot timestamp instead of a generic "Last Refreshed" label; clarify that aggregate data ends on the prior UTC day.
- **feat(rest-api):** show a direct-only explanatory banner on the Cortex Inference API page so users understand it reads live ACCOUNT_USAGE data.
- **test(acceleration):** add `tests/test_refresh_plan.py` with offline tests for page-plan coverage, dep-key classification, DT registry alignment, and REST direct-only contract.
- **test(parity):** add settled-window boundary tests verifying windowed aggregate sources are registered, global metadata is excluded from DT, and REST is not treated as windowed.

- **chore(ci):** add `.github/workflows/ci.yml` with compile, ruff, offline pytest, and markdown lint (Python 3.11).
- **feat(config):** add a Config tab: dedicated warehouse, aggregate tables, scheduled Tasks, and one-click teardown.
- **feat(config):** add `sql/setup.sql` pre-deployment script for the config key-value table.
- **feat(config):** add `utils/snowflake.py` shared primitive: connection, raw SQL execution, identifier quoting.
- **feat(config):** add `utils/config.py` with Settings model, DDL builders, health cache, and source routing.
- **feat(config):** make all surface loaders source-aware, reading from aggregate tables when acceleration is enabled.
- **test(ui):** add an offline test suite for the share-label resolution helper and its fallback paths.
- **feat(search):** add a Cortex Search page: daily usage by type, hourly serving trend, and batch query detail.
- **feat(warehouse-attribution):** add a Warehouse Attribution page from a de-duplicated attribution fact.
- **feat(warehouse-attribution):** add a warehouse-credits KPI card to the Agents, CoWork, CoCo, and AI Functions pages.
- **feat(sync-workspace):** add an `--upload` mode with `--dry-run` to `scripts/sync-workspace.sh`, alongside download.
- **docs(governance):** document the `main` branch ruleset, merge policy, and GitHub rulesets API findings.
- **chore(lint):** add pymarkdownlnt configuration so documentation linting runs with project-appropriate limits.
- **chore(lint):** add a ruff config targeting Python 3.11 at 100 columns, with a curated rule set.
- **test:** add an offline pytest suite covering the aggregation helpers and the SQL reference structure.
- **feat(coco):** use the unified `SNOWFLAKE_COCO_USAGE_HISTORY` account-level view for cli, desktop, and snowsight usage.

### Changed

- **docs(readme):** correct Config tab to aggregate tables plus daily INSERT OVERWRITE Tasks and 365-day retention.
- **docs(readme):** add `EXECUTE TASK` to the acceleration-layer privilege prerequisites.
- **docs(readme):** note that Cortex Inference API reads `ACCOUNT_USAGE` directly regardless of the acceleration layer.
- **docs(contributing):** add `utils/snowflake.py`, `utils/config.py`, and SQL scripts to the project layout table.
- **docs(contributing):** include `CONTRIBUTING.md` in the markdown-lint validation command.
- **docs(sql):** add `EXECUTE TASK` to the privilege requirements in `sql/setup.sql`.
- **docs(sql):** add a default-prefix caveat to `sql/teardown.sql`; direct custom-prefix users to in-app teardown.
- **fix(sql):** add the Model Rates table and task to `sql/teardown.sql`, which predated the materialized Model Rates layer.
- **docs(readme):** rewrite the README to improve flow and readability.
- **feat(ui):** color-code the `ACCOUNT_USAGE` source and date range in the loading/refreshing modal captions.
- **chore(pyproject):** tighten `requires-python` to `>=3.11,<3.12` to match the container runtime constraint.
- **docs(contributing):** replace the provisional merge guidance with the enforced squash and merge-commit policy.
- **chore(repo):** require pull requests on `main` and set the squash commit subject to the pull request title.
- **docs(readme):** enumerate Cortex Search and warehouse attribution in the covered-surfaces list.
- **feat(sql):** group the Reference SQL picker by page, with a variant row and a shared-blocks expander.
- **docs(readme):** document separate new-deployment and update-deployment flows for the versioned app.
- **docs(readme):** correct the deploy docs: the live version is what viewers run, so `COMMIT` is optional.
- **docs(readme):** document a test-deployment flow targeting a `_TEST` app name via `env:` overrides.
- **fix(deploy):** correct `snowflake.yml` to use `runtime_name` instead of unsupported `run_mode` and `execute_as`.
- **chore(deploy):** template app name and title from `snowflake.yml` `env:` so test deploys need no file edit.
- **feat(nav):** move Cortex Search between Cortex Agents and Cortex Inference API in the tab bar and SQL page.
- **docs(readme):** document the `ALTER STREAMLIT SET COMMENT` step to force a container restart after deploy.

### Fixed

- **fix(perf):** the "Loading data" dialog no longer opens on every tab switch. Documentation/settings tabs (Reference SQL, Query Acceleration SQL, Config) no longer warm any surface data, and switching between already-warm data tabs no longer reopens the dialog.
- **fix(sql):** Reference SQL again always shows the baseline `ACCOUNT_USAGE` queries (the non-accelerated path); the accelerated scheduled-table reads live only on the Query Acceleration SQL tab.
- **fix(ui):** `breakdown_table` defaults `label_col` to the first column, fixing `KeyError` on the Search page.
- **fix(ui):** derive share-bar headers from column labels, fixing "Share of warehouse_credits" on the Warehouse page.
- **fix(agg):** `by_dimension` and `totals` no longer raise when a fact frame has no `USER_NAME` column.
- **fix(agg):** `by_dimension` now skips metric columns a frame lacks, fixing `KeyError` on Search serving.
- **fix(warehouse-attribution):** break down on the warehouse credit columns, which the page's fact actually carries.

## [0.1.0] - 2026-08-13

### Added

- **feat(overview):** show total credits, tokens, calls, and active users and days with per-day and period-over-period trends.
- **feat(coco):** break CoCo usage down by CLI, Desktop, and Snowsight surface, token type, user, model, and role.
- **feat(cowork):** report CoWork usage by instance, service type, and user, plus SQL-warehouse and AI-function credits.
- **feat(agents):** track Cortex Agents by root and child request volume per agent and user.
- **feat(rest-api):** estimate Cortex Inference API credits from observed per-model token rates with coverage reporting.
- **feat(ai-functions):** measure Cortex AI Functions credits, tokens, and calls by function, model, and query tag.
- **feat(sql):** render the exact ACCOUNT_USAGE queries behind every page, with how per-surface credits are derived.
- **feat(filters):** add global sidebar filters for date range, user, model, and user tag, synced to the URL.
- **feat(performance):** warm all surface queries concurrently behind a progress modal for fast renders and on-demand refresh.
- **feat(theme):** ship a Snowflake-branded light and dark theme with a shared chart palette.
- **feat(deploy):** provide a snowflake.yml container-runtime (SPCS) deployment for Streamlit in Snowflake.

[Unreleased]: https://github.com/Snowflake-Solutions/cortex-ai-consumption-dashboard/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Snowflake-Solutions/cortex-ai-consumption-dashboard/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Snowflake-Solutions/cortex-ai-consumption-dashboard/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Snowflake-Solutions/cortex-ai-consumption-dashboard/releases/tag/v0.1.0
