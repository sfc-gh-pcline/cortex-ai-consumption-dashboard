# Contributing to Cortex AI Consumption Dashboard

Thanks for taking the time to contribute. This guide covers how to set up the project,
make changes, validate them, and submit a pull request. Everyone taking part is expected
to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Table of contents

- [Development setup](#development-setup)
- [Project layout](#project-layout)
- [Workflow](#workflow)
- [Validation](#validation)
- [Dependency maintenance](#dependency-maintenance)
- [Commit conventions](#commit-conventions)
- [Changelog](#changelog)
- [Reporting issues](#reporting-issues)
- [Pull requests](#pull-requests)
- [Merge strategy](#merge-strategy)

## Development setup

Prerequisites: Python 3.11, Node.js 22.12.0 or later on the 22.x line, uv, and GNU Make
3.81 or later (the macOS default). Snowflake CLI 3.27 or later is needed only for account
commands. `make doctor` reports missing tools and unsupported versions. Only optional live
checks need a Snowflake account and source-read privileges.

```bash
# Install Python and web dependencies
make install
# Install the matching browser before the first full web gate
make install:browser
```

On Linux hosts that need browser OS libraries, use `make install:browser WITH_DEPS=1`.
Dependency and browser installation are preparation steps, not offline tests.
For validation, use the [credential-free initialization recipe](#dependency-maintenance).

Live previews read real account data and are loopback-only; see
[Live local preview](docs/OPERATIONS.md#live-local-preview) for the safety rules. Run them
from the repository root, and stop the preview before running validation:

```bash
make smoke:streamlit-live CONNECTION=my_connection
make smoke:app-runtime-live CONNECTION=my_connection
```

Dependencies live in `pyproject.toml` and require `streamlit[snowflake] >= 1.61.0`. The hosted
container resolves them from that file through its attached artifact repository, so the floor
is the oldest Streamlit a deploy can receive. Raise it when the code relies on a newer release.

## Project layout

| Path | Responsibility |
| --- | --- |
| `streamlit_app.py` | Grouped navigation, native date controls, contextual filters, native loading dialog |
| `app_pages/` | One module per Cortex surface plus the landing and SQL-reference pages |
| `utils/queries.py` | Snowflake query layer: `*_sql` builders and cached `load_*` wrappers |
| `utils/agg.py` | Shared pandas aggregations |
| `utils/ui.py` | Shared UI components (KPI cards, tables, charts, banners) |
| `utils/snowflake.py` | Shared Snowflake primitives: connection, raw execution, identifier quoting |
| `utils/config.py` | Acceleration layer: Settings model, DDL builders, health checks, source routing |
| `sql/setup.sql` | Pre-deployment DDL: database, schema, and config table (`make bootstrap`) |
| `sql/config.sql` | Fresh-install acceleration provisioning (warehouse, tables, tasks, seeded settings) without Acceleration Settings (`make acceleration:provision`) |
| `sql/teardown.sql` | Default-name manual teardown, including the config table; prefer `make acceleration:teardown` |
| `snowflake.yml` | Streamlit-in-Snowflake (SPCS) deployment definition |
| `pyproject.toml` | Python dependency manifest |
| `.streamlit/config.toml` | Theme, palette, and typography |
| `web/` | Next.js implementation for Snowflake App Runtime |
| `Makefile` | Command interface: validation, preview, and guarded account lifecycle ([Operations](docs/OPERATIONS.md)) |
| `scripts/ops.py` | Input validation, confirmations, and Snowflake CLI calls behind the account commands |

## Workflow

1. Create a feature branch from `main`, named `type/description` (for example
   `feature/add-model-breakdown`, `fix/rest-credit-estimate`).
2. Make your change. The data layer and aggregation layer are separated so a page
   rarely needs a Snowflake query: add surface facts to `utils/queries.py`, aggregate
   in `utils/agg.py`, and compose the UI in `app_pages/`.
3. Run validation (see below) and update `CHANGELOG.md` for user-facing changes.
4. Commit with a Conventional Commit message, push, and open a pull request.

## Validation

The root `Makefile` is the supported validation interface. It runs both implementations
without Snowflake credentials. There is no hosted CI; run these gates locally.

The web unit-test workers supply synthetic SDK options and block tested network paths;
`make web:build` supplies those options only to its build command. Neither changes the
live preview or the E2E server. For a fully isolated local validation process, use the
[Dependency maintenance](#dependency-maintenance) recipe. Do not select a real account
to fix an offline validation failure.

```bash
make validate:python
make validate:web
make validate:docs
make validate
```

Each web step also runs alone: `make web:lint`, `web:typecheck`, `web:format`, `web:test`,
`web:build`, and `web:e2e`. Select the compiler with `WEB_COMPILER=turbopack` (default) or
`webpack`, and the mocked browser server with `E2E_SERVER=dev` (default) or `production` and
`E2E_PORT` (default 3100). The browser preflight refuses a busy port rather than testing an
unrelated server. See [Operations](docs/OPERATIONS.md#local-commands-no-snowflake-access).

`tests/test_make_interface.py` runs the Makefile against recording fakes of `snow`, `npm`,
`npx`, `uv`, and `uvx`. It checks command order, working directories, argument forwarding, and
environment scoping. It also covers missing inputs and tools, invalid and injection-shaped
values, confirmation and account mismatches, busy ports, and ambiguous ownership. It never
contacts Snowflake.

Use Python 3.11 for Python checks to match this project's declared runtime.

Most tests build SQL text and aggregate in-memory frames, so they run without Snowflake. The
Cortex Inference API builder queries observed model rates to bind them as literals. Those cases
carry the `snowflake` marker and are excluded by `make validate:python`.

```bash
uv run --python 3.11 --group dev pytest -m "not snowflake"
```

Before opening a PR, run an approved live preview for each view you changed and confirm that
the affected page renders for a real date window. Use the loopback recipe in
[Development setup](#development-setup), not an exposed development server. Re-run checks
after review changes. Live checks and deployments need an explicit connection and are never
part of validation.

For UX work, follow the [metrics contract](docs/DASHBOARD_METRICS.md). AppTest and synthetic
browser fixtures must not connect to Snowflake, and live tests need a separately approved
target. Never deploy ignored probe launchers, tests, screenshots, or credentials.

## Dependency maintenance

1. Capture the current dirty manifests, lockfiles, and affected source before editing.
   Compare against that snapshot, not only Git HEAD. Recheck for concurrent edits before
   applying or reversing a change.
2. Record a UTC registry cutoff and check stable package releases, Python requirements,
   Node engines, and all applicable peer ranges. Keep Node 22 support and 22.x Node types.
   The current development tools require Node 22.12.0 or later on the 22.x line.
3. Preserve `requires-python = ">=3.11,<3.12"` and the lower-bound-only
   `streamlit[snowflake]>=1.61.0` declaration. Refresh local resolutions in `uv.lock`.
   Keep that lock excluded from hosted Streamlit artifacts. Do not copy local exact pins
   into `pyproject.toml` or the deployment manifest.
4. Update supported npm declarations with Next and `eslint-config-next` aligned, then
   let the package managers generate the locks. Run these commands from the repository root:

   ```bash
   uv lock --upgrade
   uv sync --locked --group dev
   npm --prefix web install --no-audit
   npm --prefix web update --no-audit
   ```

5. Review the resolved delta and every hold. Do not use `--force`, `--legacy-peer-deps`,
   or blanket overrides. TypeScript's `~6.0.3` range respects typescript-eslint's `<6.1.0`
   ceiling. ESLint 9 remains required by three lint plugins, although npm marks it
   unsupported. Compatibility with peers does not imply upstream maintenance support.
   Check current package metadata for exact versions and Python constraints,
   including NumPy's Python 3.12 requirement.
6. Reproduce both locks, then check installed requirements. Save lock hashes before
   these commands and verify the same hashes afterwards:

   ```bash
   shasum -a 256 uv.lock web/package-lock.json
   uv sync --locked --group dev
   npm --prefix web ci --no-audit
   uv pip check --python .venv/bin/python
   npm --prefix web ls --all --json
   shasum -a 256 uv.lock web/package-lock.json
   ```

7. Run `make validate` in a credential-free local process. Do not run this recipe inside
   a hosted container or alongside an existing server on the E2E port (3100 by default).
   The command below clears inherited environment variables, preserves local tool paths, and
   supplies only synthetic SDK initialization values. Disable SDK platform detection
   explicitly; synthetic options alone do not prevent metadata probes. The token-file check
   must pass before validation:

   ```bash
   test ! -e /snowflake/session/token && env -i HOME="$HOME" PATH="$PATH" TMPDIR="${TMPDIR:-/tmp}" SNOWFLAKE_ACCOUNT=offline SNOWFLAKE_USER=offline SNOWFLAKE_PASSWORD=not-a-credential SNOWFLAKE_CONNECTION_NAME=e2e-no-such-connection SNOWFLAKE_DEFAULT_CONNECTION_NAME=e2e-no-such-connection SNOWFLAKE_DISABLE_PLATFORM_DETECTION=true NEXT_TELEMETRY_DISABLED=1 UV_LOCKED=true UV_PROJECT_ENVIRONMENT="$PWD/.venv" make validate
   ```

   The existing loader tests import the SDK pool before their injected fake runner executes.
   The pool has `min: 0`, so constructing it does not authenticate. The synthetic values avoid
   reading CLI connection credentials. The unit-test network guards and build-only
   initialization do not sandbox the build, E2E server, or other processes.
   Keep Python's socket-blocking fixture and Playwright's mocked APIs intact. Never substitute
   real credentials, add a credential file, or change application configuration for this prerequisite.
   To repeat the web gate with an already-installed Node 22 runtime, put its binary directory first
   in the child process's `PATH` and use `make validate:web` in the same isolated environment.

8. Inspect parsed inventory results, not exit codes alone. `npm outdated --json` can return
   1 for a valid inventory. `npm ls --all --json` can return 0 while listing extraneous
   optional packages. Check invalid peers and missing required dependencies separately.
   `uv pip list --outdated` can omit releases incompatible with the interpreter, so also
   check Python requirements when comparing against registry latest versions.
9. With approval to send dependency metadata to the npm registry, run a live audit:

   ```bash
   npm --prefix web audit --json --offline=false --registry=https://registry.npmjs.org --loglevel=http
   ```

   Record the HTTP result, timestamp, and parsed advisories. Offline empty output or a network
   failure is not a clean audit. An npm audit does not assess Python dependencies or hosted apps.
   Do not send source, account data, or credentials to an audit service.

10. Record actual test counts, warnings, unexecuted hosted checks, and final lock hashes.
    Run `make validate:docs` and `git diff --check`. Check untracked web files with the local
    Prettier binary because Git's whitespace check omits them. Keep dependency maintenance
    under Unreleased. A release bump, global tool upgrade, or deployment needs separate scope.

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>[optional scope]: <summary>
```

Common types for this project: `feat` (new surface page or metric), `fix` (wrong credit
estimate, broken query), `refactor` (query or aggregation restructuring), `docs`
(documentation only), `perf` (faster loaders or queries), `chore` (tooling). Use a scope
when it helps, for example `feat(coco): add token-type mix table`.

Write terse, feature-focused subjects. Put the why and any validation details in the
commit body, not in `CHANGELOG.md`.

## Changelog

User-facing changes go in `CHANGELOG.md` under `## [Unreleased]`, using the standard
Keep a Changelog types (Added, Changed, Deprecated, Removed, Fixed, Security). Entries
describe the behavior change for users, not the implementation. Do not dump commit
messages or file lists into the changelog.

## Reporting issues

Open a [GitHub issue](https://github.com/Snowflake-Solutions/cortex-ai-consumption-dashboard/issues)
for bugs and feature requests. Include:

- The view: Streamlit or App Runtime.
- The build: the version and commit in the `make` help header for local work, or the build
  fingerprint the deployed app shows in its sidebar or on Acceleration Settings.
- The page, date window, filters, and UTC time of the problem, plus steps to reproduce.
- What you expected and what happened, with the error text or the `requestId` from a failed
  App Runtime request.

Never paste credentials, account identifiers, or unredacted query results into an issue.
Report a suspected security problem privately to the maintainer email in the
[README](README.md#author), not in a public issue.

## Pull requests

- Keep one logical change per PR. Split large refactors from feature work.
- Give the PR a Conventional Commit title and summarize the change and why in the body.
  The [pull request template](.github/pull_request_template.md) lists what to record,
  including how you validated the change.
- Link any related issue in the description.
- Address review feedback in new commits; do not force-push during review.

## Merge strategy

The checked-in policy requires pull requests, resolved review threads, and protection against
force pushes and branch deletion. It records zero required approvals and an administrator
bypass. The live GitHub settings matched that record on October 8, 2026. See
[the recorded configuration](docs/BRANCH_PROTECTION.md), which includes read-only commands to
recheck them.

Two merge methods are available on `main`:

- **Squash merge** is the default for ordinary feature and fix pull requests. The branch
  collapses into one commit whose subject is the pull request title, so keep that title a
  valid Conventional Commit message. Branch history is not preserved, so keep pull
  requests scoped.
- **Merge commit** is reserved for cases that genuinely need topology preserved: release
  branches, long-running integration branches, and upstream or fork syncs.

Rebase merging is not used on `main`. It stays enabled at the repository level for other
branches, but the ruleset refuses it on `main`.

GitHub cannot tell a feature pull request from a release-branch pull request, because both
target `main`. The ruleset restricts you to squash or merge; choosing correctly between
them is a convention, not something the platform enforces.
