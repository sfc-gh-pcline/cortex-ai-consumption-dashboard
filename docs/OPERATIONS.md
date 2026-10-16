# Operations

The root `Makefile` is the supported command interface for both views. Run `make` for the
command list; it is colored on a terminal and plain when piped or when `NO_COLOR` is set.
This page states what each command changes, what it needs, and what it never
touches. No GitHub Actions workflow runs these checks; validation is local.

Prerequisites: GNU Make 3.81 or later, uv, Node.js 22.12 or later on the 22.x line, and
Snowflake CLI 3.27 or later for account commands. `make doctor` checks them without
contacting Snowflake.

## Conventions

- Inputs are Make variables: `make <target> NAME=value`. Values with spaces need shell
  quoting (`APP_TITLE="Cortex AI Consumption Dashboard"`). Make expands `$`, so values
  containing `$` are not supported.
- Every Snowflake command takes an explicit `CONNECTION`. Nothing falls back to a default
  connection. Inputs are validated before any tool runs: connection and target names, and
  Snowflake identifiers (letters, digits, underscore). Titles may contain spaces but not
  quotes, backslashes, `$`, backticks, or `<% %>`. Snowflake CLI calls receive argument
  lists, never a shell string.
- `EXPECTED_ACCOUNT` is the account locator or `ORG-ACCOUNT` identifier shown by
  `make inspect`. When it is set, the command first reads `CURRENT_ACCOUNT()` and stops on a
  mismatch. New mutations require it. Deploy and App Runtime teardown accept it optionally.
- New mutations (`bootstrap`, `grants`, `acceleration:provision`, `:refresh`, `:suspend`,
  `:resume`, `:teardown`, and `streamlit:teardown`) are dry runs until `CONFIRM` or
  `CONFIRM_TEARDOWN` names the exact target. Without it they print the plan and exit 0. A
  mismatched value exits 2 before any side effect. `deploy:app-runtime` and
  `app-runtime:teardown` have no dry run: they exit 2 unless their confirmation is set.
  `deploy:streamlit` takes no confirmation.
- To run a command as a role other than the connection's default, set
  `SNOWFLAKE_CONNECTIONS_<NAME>_ROLE` for that command, where `<NAME>` is the connection name
  in upper case. For example,
  `SNOWFLAKE_CONNECTIONS_MY_CONNECTION_ROLE=COST_MGMT_DASHBOARD_ADMIN make deploy:streamlit ...`.
  The Snowflake CLI honors it. The live previews do not: they use the `role` in
  `connections.toml`, so preview as another role with a separate connection entry.
- `dev`, `test`, and `prod` App Runtime targets and every Streamlit app read the same config
  table and aggregate tables in `COST_MGMT_DB.PUBLIC`. An app name does not isolate data.

## Local commands (no Snowflake access)

| Command | Side effects | Inputs |
| --- | --- | --- |
| `make doctor` | None | – |
| `make install` | `uv sync --group dev`, then `npm --prefix web ci` | – |
| `make install:browser` | Installs Playwright Chromium | `WITH_DEPS=1` adds OS packages |
| `make validate` | Runs `validate:python`, `validate:web`, `validate:docs` in order | as below |
| `make validate:python` | None: Ruff lint and format check, Python 3.11 compileall, `pytest -m "not snowflake"` | – |
| `make validate:web` | Runs `web:lint`, `web:typecheck`, `web:format`, `web:test`, `web:build`, `web:e2e` | as below |
| `make web:build` | Writes `web/.next` | `WEB_COMPILER=turbopack` (default) or `webpack` |
| `make web:e2e` | Starts and stops a loopback server | `E2E_SERVER=dev` (default) or `production`; `E2E_PORT=3100` |
| `make validate:docs` | None | – |

`web:build` refuses to run when `/snowflake/session/token` exists. It passes synthetic SDK
options (`SNOWFLAKE_ACCOUNT=offline` and related values) to the build command only.

`web:e2e` runs mocked browser tests. Its preflight refuses invalid inputs, a hosted session
token, and a loopback port already in use, so an unrelated or live server cannot answer the
tests. `E2E_SERVER=production` serves the standalone output of a previous `web:build` and
refuses to start without it. Playwright never reuses an existing server. The test server
receives blank credential variables and a nonexistent connection name, so an un-mocked API
call fails instead of reaching an account.

The compiler is selected explicitly. Turbopack is the Next.js default. In some sandboxed
environments its CSS worker cannot bind a port, and webpack is the documented alternative.
There is no silent fallback. A production-browser run looks like this:

```bash
make web:build WEB_COMPILER=webpack
make web:e2e E2E_SERVER=production E2E_PORT=4317
```

## Live local preview

| Command | Side effects | Inputs |
| --- | --- | --- |
| `make smoke:streamlit-live` | Local Streamlit server reading real account data | `CONNECTION` |
| `make smoke:app-runtime-live` | Local `next dev` server reading real account data | `CONNECTION` |

Both previews bind to `127.0.0.1` and set `DASHBOARD_ADMIN_USERS='[]'`, so no local
caller can administer the acceleration layer. Process credential variables
(`SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_HOST`, `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD`) are removed,
so the named connection is the only credential source. Read routes are still
unauthenticated: do not tunnel or proxy the listener. An empty allowlist blocks
administration, not data reads. The hosted viewer-identity header is trusted only behind
Snowflake ingress; sending it to a local server is not a login. Run one preview at a time;
each keeps running until stopped. Stop a preview before validation.

## Account lifecycle

"Safe to rerun" means a second run with the same inputs succeeds and leaves the same state.

| Phase | Command | Side effects | Required inputs | Confirmation | Safe to rerun |
| --- | --- | --- | --- | --- | --- |
| Inspect | `make inspect` | Read-only: connection test, current account and role, config and registry | `CONNECTION` | – | Yes |
| Bootstrap | `make bootstrap` | `sql/setup.sql`: database, schema, config table `IF NOT EXISTS` | `CONNECTION EXPECTED_ACCOUNT DATABASE SCHEMA` | `CONFIRM=COST_MGMT_DB.PUBLIC` | Yes; keeps objects and settings |
| Grants | `make grants` | `GRANT` statements to existing roles | `CONNECTION EXPECTED_ACCOUNT VIEWER_ROLE QUERY_WAREHOUSE`, optional `ADMIN_ROLE`, `GRANT_ACCOUNT_USAGE=1` | `CONFIRM=<VIEWER_ROLE>` | Yes; rerun after provisioning |
| Acceleration | `make acceleration:status` | Read-only | `CONNECTION` | – | Yes |
| | `make acceleration:provision` | `sql/config.sql` on a fresh config table | `CONNECTION EXPECTED_ACCOUNT` | `CONFIRM=COST_MGMT_DB.PUBLIC` | No; refuses (exit 2) once settings exist |
| | `make acceleration:refresh`, `:suspend`, `:resume` | `EXECUTE TASK` / `ALTER TASK` on registered tasks | `CONNECTION EXPECTED_ACCOUNT` | `CONFIRM=COST_MGMT_DB.PUBLIC` | Yes; each refresh queues another run |
| | `make acceleration:teardown` | Drops registered tasks, tables, then warehouse; clears the registry | `CONNECTION EXPECTED_ACCOUNT` | `CONFIRM=COST_MGMT_DB.PUBLIC` | Once; the registry is empty afterward |
| Streamlit | `make deploy:streamlit` | `snow streamlit deploy --replace --prune`, then attaches the PyPI artifact repository | `CONNECTION APP_NAME APP_TITLE` | – (parity) | Yes; replaces the live version and restarts the app |
| | `make streamlit:status` | Read-only `DESCRIBE STREAMLIT` | `CONNECTION APP_NAME` | – | Yes |
| | `make streamlit:teardown` | Drops one Streamlit object | `CONNECTION APP_NAME EXPECTED_ACCOUNT` | `CONFIRM_TEARDOWN=<APP_NAME>` | Yes (`IF EXISTS`) |
| App Runtime | `make deploy:app-runtime` | `snow app validate`, then `snow app deploy --force` | `CONNECTION TARGET` | `CONFIRM_EXAMPLE_TARGET=<TARGET>` (required) | Yes; new version, same URL |
| | `make app-runtime:open`, `make app-runtime:events` | Read-only: URL, recent logs | `CONNECTION TARGET` | – | Yes |
| | `make app-runtime:teardown` | `snow app teardown --force` for one target | `CONNECTION TARGET` | `CONFIRM_TEARDOWN=<TARGET>` (required) | Once per deployed target |

The Snowflake CLI has no App Runtime status command; use `app-runtime:open` and
`app-runtime:events`.

### Target contract

Both runtimes read configuration from `COST_MGMT_DB.PUBLIC.CORTEX_DASHBOARD_CONFIG`. The
location is set by `snowflake.yml` and the `DASHBOARD_CONFIG_*` variables in `web/app.yml`, and
a test keeps those manifests and `scripts/ops.py` in agreement. `bootstrap` takes `DATABASE`
and `SCHEMA` explicitly and refuses any other location, because neither view would read it.
To relocate, edit both manifests and `sql/setup.sql` first. Query warehouses (`COMPUTE_WH`)
and the compute pool come from the manifests.

### Bootstrap is rerunnable

`bootstrap` runs `sql/setup.sql`, which only creates missing objects. Rerunning it keeps
existing objects and settings.

`acceleration:provision` is different. `sql/config.sql` replaces tasks and resets settings,
so the command refuses to run when the config table already holds settings. It uses the
default names only (prefix `COST_MGMT`, warehouse `COST_MGMT_WH`). For other names, or to
change an existing installation, use Acceleration Settings or a reviewed targeted change.
Do not rerun all provisioning SQL. Provisioning creates the tasks, tables, and warehouse
owned by the role that runs it, and the tasks run as that role.

### New installation

The ordered steps for both interfaces, with the role SQL, created objects, and required
privileges, are in the README's
[Setup, deployment, and teardown](../README.md#setup-deployment-and-teardown). Two ordering
rules matter here: create the roles before `grants`, which never creates them, and run `grants`
after `acceleration:provision` and again after any later provisioning, because it grants
`SELECT` only on aggregate tables that are already registered.

### Grants

`grants` never creates roles, grants ownership, or grants `ACCOUNTADMIN`. It prints the
statements before executing them, and every statement is safe to repeat:

- `VIEWER_ROLE` (and `ADMIN_ROLE`, when given): `USAGE` on the database, schema, and
  `QUERY_WAREHOUSE`, and `SELECT` on the config table and each registered aggregate table.
- `VIEWER_ROLE`: `USAGE` on all current and future Streamlit apps and Application Services in
  `COST_MGMT_DB.PUBLIC`, so viewers can open apps deployed later.
- `ADMIN_ROLE`: `INSERT, UPDATE` on the config table; `CREATE STREAMLIT`, `CREATE STAGE`,
  `CREATE APPLICATION SERVICE`, and `CREATE ARTIFACT REPOSITORY` on the schema; `USAGE` on the
  manifest compute pool (`SYSTEM_COMPUTE_POOL_CPU`); and the `SNOWFLAKE.PYPI_REPOSITORY_USER`
  database role, which `deploy:streamlit` needs to attach the PyPI repository. With these, both
  deploy commands succeeded as the admin role in testing.
- `GRANT_ACCOUNT_USAGE=1` (requires `ADMIN_ROLE`): the `SNOWFLAKE.USAGE_VIEWER`,
  `SNOWFLAKE.GOVERNANCE_VIEWER`, and `SNOWFLAKE.SECURITY_VIEWER` database roles to the admin
  role. Together they cover every `ACCOUNT_USAGE` view the dashboard reads: the usage and
  metering histories, `TAG_REFERENCES`, and `USERS`. The value must be exactly `1`.

Without `GRANT_ACCOUNT_USAGE=1`, reading `SNOWFLAKE.ACCOUNT_USAGE` is a separate decision by an
account administrator, for example those database roles or imported privileges on the
`SNOWFLAKE` database. Provisioning from Acceleration Settings also needs `CREATE TABLE`,
`CREATE TASK`, `CREATE WAREHOUSE`, and `EXECUTE TASK`; `grants` does not give them. Grant
those separately after review. Users also need the viewer role granted to them.

### Teardown scope

App-only teardown and owned-resource teardown are separate commands:

- `streamlit:teardown` drops one Streamlit object. The config table, aggregates, warehouses,
  and other apps remain.
- `app-runtime:teardown` drops one Application Service and its `<TARGET_NAME>_CODE` upload stage
  if one is left. Its artifact repository (`<TARGET_NAME>_REPO`), the config table, aggregates,
  and warehouses remain.
- `acceleration:teardown` acts only on objects in the `owned_objects` registry. It stops,
  changing nothing, when the registry is empty, unparseable, or names anything outside
  `COST_MGMT_DB.PUBLIC`. It also stops when a name lacks the configured prefix, a record names
  the config table, or a warehouse is not the configured refresh warehouse or is a manifest
  query warehouse. The dry run lists every object. Statements run one at a time, and the
  registry is cleared only after every drop succeeds.

No teardown command drops the config table, database, schema, roles, query warehouses,
compute pools, or artifact repositories. Remove those manually only after confirming nothing
else uses them. `sql/teardown.sql` assumes default names and drops the config table; prefer
the commands above.

### Full uninstall

No command removes everything. The README's [Teardown](../README.md#teardown) lists the order:
app teardowns for every target and app, `acceleration:teardown`, then a reviewed
`DROP DATABASE COST_MGMT_DB`, which also removes the config table and artifact repositories,
and optionally dropping the roles.
