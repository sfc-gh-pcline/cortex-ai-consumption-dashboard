# Cortex AI Consumption Dashboard (Snowflake App Runtime)

Next.js port of the Streamlit dashboard in the repository root, hosted as a Snowflake
App Runtime Application Service. It reads the same `ACCOUNT_USAGE` views, the same
`CORTEX_DASHBOARD_CONFIG` table, and the same acceleration tables as the Streamlit app.

The SQL builders in `lib/sql/` must emit byte-identical statements to the Python
builders in `../utils/queries.py`; `tests/sql-parity.test.ts` checks them against
`../tests/fixtures/golden_sql.json`, exported from the Python side.

## Commands

Run these independent commands from the repository root. They are not one setup sequence.

Install dependencies:

```bash
make install
make install:browser
```

For offline validation, use the synthetic SDK initialization environment in
[CONTRIBUTING](../CONTRIBUTING.md#dependency-maintenance). Then run the web gate, or any of
its steps (`web:lint`, `web:typecheck`, `web:format`, `web:test`, `web:build`, `web:e2e`):

```bash
make validate:web
```

For a local preview with live account data, run
`make smoke:app-runtime-live CONNECTION=my_connection`. It reads real account data and is
loopback-only; see [Live local preview](../docs/OPERATIONS.md#live-local-preview).

Deploy only after reviewing the target; this changes the selected Snowflake application:

```bash
make deploy:app-runtime CONNECTION=my_connection TARGET=test CONFIRM_EXAMPLE_TARGET=test
```

See [App Runtime](../docs/APP_RUNTIME.md) for architecture, customer example targets, and the runbook.
