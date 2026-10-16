# Web dashboard on Snowflake App Runtime

The `web/` directory is a Next.js port of the Streamlit dashboard, deployed as a Snowflake
App Runtime application service. It runs side by side with the Streamlit apps and reads the
same `ACCOUNT_USAGE` views, the same config table, and the same aggregate tables. Ordinary viewers
only read data. Allowlisted Acceleration Settings administrators can create and manage the
registered warehouse, tables, tasks, and settings rows.

## Architecture

```text
browser ──HTTPS──▶ Snowflake ingress (OAuth sign-in) ──▶ Application Service (Next.js on :8080)
                                                            │  App Router pages (client components, URL state via nuqs)
                                                            │  /api/preflight   session, UTC today, viewer, admin flag
                                                            │  /api/settings    config table, task health, source routing
                                                            │  /api/dependency  one fact read per surface per window
                                                            │  /api/admin/*     state, settings, provision, action (admins only)
                                                            ▼
                                                     snowflake-sdk (pooled, OAuth session token)
                                                            ▼
                                       SNOWFLAKE.ACCOUNT_USAGE  ·  COST_MGMT_DB.PUBLIC aggregate tables
```

- **Owner's rights.** Queries run as the platform-managed service user under the service's
  owner role. The viewer's identity arrives in the `sf-context-current-user` header and is used
  only for the admin allowlist; it does not select a query role. This trusts Snowflake ingress
  to authenticate the viewer and supply the header. Local preview has no equivalent boundary.
- **Same SQL, same math.** `web/lib/sql/` builds the identical statements the Python app runs,
  checked byte for byte against `tests/fixtures/golden_sql.json`. `web/lib/agg/` ports the
  pandas aggregation, checked to 1e-9 against `tests/fixtures/agg_cases.json`. Regenerate both
  fixtures with `scripts/export_golden.py` and `scripts/export_agg_cases.py` after changing a
  builder or an aggregation.
- **Native nulls.** The SDK pool sets `representNullAsStringNull: false`. SQL NULL rates
  must remain JavaScript `null`: the SDK's default `"NULL"` string becomes `NaN` during
  numeric conversion and breaks the next inference query's numeric binds. Unknown rates
  are not zero-cost rates. SQL/parameter parity tests alone do not cover driver decoding.
- **Same URL contract.** Page paths and query parameters match the Streamlit app
  (`range`, `start`, `end`, `users`, `models`, `tags`, per-page breakdown keys), so a link from
  one app opens the same view in the other.
- **Caching.** One in-process result cache keyed by statement, parameters, and aggregate
  snapshot, with the Streamlit TTLs (results 30 minutes, model rates 24 hours, user tags 1 hour,
  settings 30 seconds, health 5 minutes). `max_instances: 1` limits it to one service instance.
  Admin **Reload results** bypasses the active view's result cache but preserves Model Rates;
  viewer **Refetch cached results** can reuse the cache. See
  [reload boundaries](DASHBOARD_METRICS.md#reload-and-reference-sql-boundaries).
- **Shared number formatting.** Credit values use `fmtCredits`, including `<0.01` for tiny
  positive values. KPI deltas use one decimal for the percentage and four decimals for the
  absolute change in both views.
- **Mutations in Acceleration Settings.** Administrators can toggle acceleration, save the Setup
  form, provision the task warehouse, aggregate tables and refresh tasks, suspend, resume and
  refresh registered tasks, and tear everything down. `web/lib/admin/actions.ts` ports the
  mutation half of `utils/config.py` with the same exact-ownership rules: only objects
  registered in `owned_objects` are ever mutated, provisioning pre-registers every planned
  object in one settings write before any DDL, retries skip steps that already succeeded, a
  failed task drop blocks the dependent table and warehouse drops, and a settings write
  that fails after a drop surfaces the completed outcomes instead of hiding them. Every admin
  route calls `requireAdmin` first. Mutation execution is serialized by a process-level mutex;
  context acquisition and the read-only admin-state route are outside that lock. This is not
  transaction isolation for the whole request. The web app writes settings to the config table
  it reads from (`DASHBOARD_CONFIG_DATABASE`/`_SCHEMA`), so a
  Setup change of the acceleration database never moves the config table itself.
  Both Acceleration Settings views include **Adopt existing objects**. The action registers
  exact configured names that already exist but are unowned, so lifecycle actions and teardown
  become available without recreating anything.

## Manifest (`web/app.yml`)

| Key | Value | Why |
| --- | --- | --- |
| `database` / `schema` | `COST_MGMT_DB.PUBLIC` | Default service-object location; targets can override it. Config location and aggregate targets are resolved separately. |
| `query_warehouse` | `COMPUTE_WH` | Customer example for service queries; acceleration tasks use their own warehouse. |
| `targets.dev` | `AI_CONSUMPTION_WEB_DEV` in `USER$` | Customer example for personal iteration. |
| `targets.test` | `AI_CONSUMPTION_WEB_TEST` | Customer example for a test service. |
| `targets.prod` | `AI_CONSUMPTION_WEB` | Customer example for a production service. |
| `auto_suspend_secs` | 600 | Idle service stops billing after ten minutes. |
| `min_instances` / `max_instances` | 1 / 1 | Single instance; see caching above. |
| `run.command` | `node .next/standalone/server.js` | The platform sets `NEXT_PRIVATE_STANDALONE`, so `next start` is not supported. |
| `ignore` | tests, e2e, `.next`, `node_modules`, ... | Keeps the upload to source only; the remote build produces `.next`. |

`scripts/postbuild.mjs` runs after `next build` and copies `.next/static` and `public/` into
`.next/standalone/`, which Next leaves to the deployer. Without it the served pages have no CSS
or client JavaScript.

### Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `DASHBOARD_ADMIN_USERS` | `[]` | JSON array of Snowflake usernames allowed past the Acceleration Settings lock. Empty means nobody. A target's `environment_variables` list replaces the top-level list wholesale, so a target that overrides one variable must repeat them all. |
| `DASHBOARD_CONFIG_DATABASE` / `_SCHEMA` | `COST_MGMT_DB` / `PUBLIC` in the example manifest | Config-table location. If unset, the helper uses service database/schema values, then application defaults. Persisted settings determine aggregate database/schema separately. |
| `NEXT_PUBLIC_BUILD_INFO` | computed | Set by `next.config.ts` at build time from `scripts/build-info.mjs`: package version, a 12-hex SHA-256 digest of the runtime source files (`app/`, `components/`, `lib/`, `package.json`, `app.yml`, `next.config.ts`), the git commit when a checkout is present (the remote builder has none), build time, Node version and the versions of Next.js, React, Tailwind, TanStack Table and Query, react-vega, Vega-Lite, nuqs and snowflake-sdk. The sidebar shows `Next.js <v> \| build <digest>` with an expandable component list; Acceleration Settings shows the full fingerprint. |

The platform injects `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_HOST`, `SNOWFLAKE_DATABASE`,
`SNOWFLAKE_SCHEMA`, `SNOWFLAKE_INSECURE_MODE=true` and the session token at
`/snowflake/session/token`. `web/lib/snowflake.ts` uses those when present and otherwise falls
back to `SNOWFLAKE_USER` / `SNOWFLAKE_PASSWORD` or the CLI `connections.toml` entry named by
`SNOWFLAKE_CONNECTION_NAME` (default `default`).

### Local live preview safety

Snowflake ingress authenticates hosted viewers and supplies the identity header that the
admin allowlist checks. A local server has no such boundary, so its read routes do not
authenticate callers. Run it with `make smoke:app-runtime-live CONNECTION=my_connection`;
[Live local preview](OPERATIONS.md#live-local-preview) lists the safeguards that command
applies and the rules for using it.

## Runbook

Run commands from the repository root with GNU Make and Snowflake CLI 3.27.0 or later.
[Operations](OPERATIONS.md) lists every command's side effects and confirmations. The
checked-in targets are examples. All three share the config table and aggregate tables in
`COST_MGMT_DB.PUBLIC`; a separate app name does not isolate data. Review their database,
schema, warehouse, service names, and admin allowlist before deployment.

```bash
make inspect CONNECTION=my_connection
```

Before local validation, install the dependencies and Playwright browser described in
[Development setup](../CONTRIBUTING.md#development-setup). Use the synthetic, credential-free
SDK initialization environment in [Dependency maintenance](../CONTRIBUTING.md#dependency-maintenance).
Run these gates in that environment:

```bash
make validate:web
make validate
```

`validate:web` runs the web checks; `validate` also checks Python and docs. There is no hosted
CI. To exercise the production standalone server with the mocked browser suite:

```bash
make web:build WEB_COMPILER=webpack
make web:e2e E2E_SERVER=production E2E_PORT=4317
```

Deploy. The deploying role owns the service, and the service runs with its privileges. It needs
`CREATE APPLICATION SERVICE` and `CREATE ARTIFACT REPOSITORY` on `COST_MGMT_DB.PUBLIC`, which
`make grants` gives `ADMIN_ROLE`. Run it as that role, as in
[step 7 of the README setup](../README.md#7-deploy). The first deploy creates the
`<TARGET_NAME>_REPO` artifact repository; later deploys keep the same URL:

```bash
make deploy:app-runtime CONNECTION=my_connection TARGET=test CONFIRM_EXAMPLE_TARGET=test
```

Inspect:

```bash
make app-runtime:open CONNECTION=my_connection TARGET=test
make app-runtime:events CONNECTION=my_connection TARGET=test
```

If a request returns **The dashboard request failed.**, record the UTC time, affected route,
and build fingerprint. The failed response body includes a `requestId`; obtain it from the
browser's Network details when available. The UI banner currently displays only the error
text. An authorized operator can correlate the ID with service logs using the events command
above, inspect the underlying source or config-table failure, and decide whether to retry.
Do not paste credentials or unredacted query results into an issue. A 400 response indicates
request validation, and 403 indicates administrator authorization; neither is the generic 500
path. Do not broaden the allowlist to work around a server failure.

Remove one target. This is app-only teardown; the shared config table, aggregates, and
warehouses remain:

```bash
make app-runtime:teardown CONNECTION=my_connection TARGET=test CONFIRM_TEARDOWN=test
```

Opening the app URL redirects to a Snowflake username and password page for the
`SNOWSERVICES_INGRESS_OAUTH` integration. Sign in with your own account; the app never sees the
password.

Teardown does not drop the artifact repository. Review and remove that repository
separately if your account no longer needs its package history.
