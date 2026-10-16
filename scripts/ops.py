"""Guarded Snowflake lifecycle commands behind the root Makefile.

Run through ``make <target>``; each target maps to one subcommand here, e.g.
``make bootstrap CONNECTION=c EXPECTED_ACCOUNT=a DATABASE=COST_MGMT_DB SCHEMA=PUBLIC``.
Inputs arrive as environment variables (Make exports command-line assignments).

Every input is validated before any tool runs. Snowflake CLI calls use argument
lists, never a shell. New lifecycle mutations are dry runs until ``CONFIRM`` names
the exact target; a mismatched confirmation, account, or ownership record stops
before any side effect. Teardown acts only on objects in the ``owned_objects``
registry and never drops the config table, database, schema, roles, or warehouses
named by the deployment manifests.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import streamlit.logger

# utils.config decorates loaders with st.cache_data, which warns outside a Streamlit run.
streamlit.logger.set_log_level("error")

from utils.config import (  # noqa: E402
    CONFIG_TABLE_NAME,
    _parse_owned_objects,
    drop_object_sql,
    execute_task_sql,
    resume_task_sql,
    suspend_task_sql,
)
from utils.snowflake import quote_identifier  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "web"

# Both runtimes read configuration from this location (snowflake.yml identifier and
# web/app.yml DASHBOARD_CONFIG_*); tests/test_make_interface.py keeps them in sync.
CONFIG_DB = "COST_MGMT_DB"
CONFIG_SCHEMA = "PUBLIC"
CONFIG_LOCATION = f"{CONFIG_DB}.{CONFIG_SCHEMA}"
CONFIG_FQN = f"{quote_identifier(CONFIG_DB)}.{quote_identifier(CONFIG_SCHEMA)}.{CONFIG_TABLE_NAME}"
# Query warehouses named in the manifests are shared and never dropped.
SHARED_WAREHOUSES = frozenset({"COMPUTE_WH"})
# Compute pool named by snowflake.yml; the admin role needs USAGE on it to deploy.
STREAMLIT_COMPUTE_POOL = "SYSTEM_COMPUTE_POOL_CPU"
# Snowflake's PyPI mirror; the Streamlit container installs pyproject.toml from it.
PYPI_REPOSITORY = "SNOWFLAKE.SNOWPARK.PYPI_SHARED_REPOSITORY"
# SNOWFLAKE database roles covering every ACCOUNT_USAGE view the dashboard reads:
# usage/metering history, TAG_REFERENCES, and USERS respectively.
ACCOUNT_USAGE_ROLES = ("USAGE_VIEWER", "GOVERNANCE_VIEWER", "SECURITY_VIEWER")
MIN_SNOW = (3, 27)
HOSTED_TOKEN = Path("/snowflake/session/token")
# Credential variables that would bypass an explicitly selected connection.
CREDENTIAL_ENV = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_HOST", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD")

_CONNECTION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_TARGET = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_ACCOUNT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,254}")
_TITLE_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f'\"\\$`]|<%|%>")
_QUOTED_FQN = re.compile(r'"([A-Z0-9_]+)"\."([A-Z0-9_]+)"\."([A-Z0-9_]+)"')
_QUOTED_NAME = re.compile(r'"([A-Z0-9_]+)"')


class OpsError(Exception):
    """A preflight failure: reported before any side effect, exit status 2."""


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def _get(name: str) -> str:
    return os.environ.get(name, "")


def require(name: str, pattern: re.Pattern[str] | None = None, hint: str = "") -> str:
    """Return a required input, rejecting missing or malformed values."""
    value = _get(name)
    if not value:
        raise OpsError(f"{name} is required.{' ' + hint if hint else ''}")
    if pattern is not None and not pattern.fullmatch(value):
        raise OpsError(f"{name} has an invalid value: {value!r}")
    return value


def optional(name: str, pattern: re.Pattern[str]) -> str:
    """Return an optional input, validating it when present."""
    value = _get(name)
    if value and not pattern.fullmatch(value):
        raise OpsError(f"{name} has an invalid value: {value!r}")
    return value


def require_title(name: str) -> str:
    """Return a display title; spaces are allowed, quoting and template tokens are not."""
    value = require(name)
    if len(value) > 255 or _TITLE_FORBIDDEN.search(value):
        raise OpsError(f"{name} must be printable text without quotes, $, backticks, or <% %>.")
    return value


def confirmed(label: str, var: str = "CONFIRM") -> bool:
    """Return True for an exact confirmation, False for a dry run; reject mismatches."""
    value = _get(var)
    if not value:
        print(f"Dry run: nothing executed. Re-run with {var}={label} to apply.")
        return False
    if value != label:
        raise OpsError(f"{var}={value!r} does not match the target {label!r}.")
    return True


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def require_snow() -> None:
    """Require Snowflake CLI at or above the supported version."""
    if shutil.which("snow") is None:
        raise OpsError("Install Snowflake CLI 3.27 or later.")
    out = subprocess.run(["snow", "--version"], capture_output=True, text=True, check=False)
    match = re.search(r"(\d+)\.(\d+)", out.stdout)
    if match is None or (int(match[1]), int(match[2])) < MIN_SNOW:
        raise OpsError(f"Snowflake CLI 3.27 or later is required; found {out.stdout.strip()!r}.")


def require_tool(name: str, hint: str) -> None:
    """Require an executable on PATH."""
    if shutil.which(name) is None:
        raise OpsError(hint)


def run(argv: list[str], cwd: Path = REPO, env: dict[str, str] | None = None) -> None:
    """Run one command without a shell, echoing it, and stop on failure."""
    print(f"+ {shlex.join(argv)}", file=sys.stderr)
    result = subprocess.run(argv, cwd=cwd, env=env, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def query(connection: str, sql: str) -> list[dict]:
    """Run one read-only statement and return its rows as dictionaries."""
    argv = ["snow", "sql", "--connection", connection, "--format", "JSON", "-q", sql]
    print(f"+ {shlex.join(argv)}", file=sys.stderr)
    result = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise OpsError(f"Read-only query failed:\n{result.stderr.strip() or result.stdout.strip()}")
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise OpsError("Snowflake CLI returned non-JSON output.") from exc
    return rows if isinstance(rows, list) else []


def execute_each(connection: str, statements: list[str]) -> None:
    """Execute statements one call at a time, stopping at the first failure."""
    for index, sql in enumerate(statements):
        argv = ["snow", "sql", "--connection", connection, "-q", sql]
        print(f"+ {shlex.join(argv)}", file=sys.stderr)
        if subprocess.run(argv, cwd=REPO, check=False).returncode != 0:
            remaining = statements[index:]
            print("Stopped. Not completed:\n  " + "\n  ".join(remaining), file=sys.stderr)
            raise SystemExit(1)


def verify_account(connection: str, expected: str) -> None:
    """Stop unless the connection resolves to the expected account."""
    rows = query(
        connection,
        "SELECT CURRENT_ACCOUNT() AS LOCATOR, CURRENT_ORGANIZATION_NAME() || '-' || "
        "CURRENT_ACCOUNT_NAME() AS ACCOUNT_ID, CURRENT_ROLE() AS ROLE",
    )
    row = rows[0] if rows else {}
    names = {str(row.get("LOCATOR", "")).upper(), str(row.get("ACCOUNT_ID", "")).upper()}
    if expected.upper() not in names:
        raise OpsError(
            f"Connection {connection!r} resolves to {sorted(names - {''})}, "
            f"not EXPECTED_ACCOUNT={expected!r}."
        )
    print(f"Account {expected} verified as role {row.get('ROLE', '?')}.")


def verify_account_if_set(connection: str) -> None:
    """Apply the account guard when the caller supplied EXPECTED_ACCOUNT."""
    expected = optional("EXPECTED_ACCOUNT", _ACCOUNT)
    if expected:
        verify_account(connection, expected)


def preview_env(connection_var: str, connection: str) -> dict[str, str]:
    """Return a preview environment pinned to one connection with no local admins."""
    if HOSTED_TOKEN.exists():
        raise OpsError(f"{HOSTED_TOKEN} exists; local previews must not use a hosted token.")
    env = {k: v for k, v in os.environ.items() if k not in CREDENTIAL_ENV}
    env[connection_var] = connection
    env["DASHBOARD_ADMIN_USERS"] = "[]"
    return env


# ---------------------------------------------------------------------------
# Ownership registry
# ---------------------------------------------------------------------------


def read_config(connection: str) -> dict[str, str]:
    """Read the shared config table; a failure means it is missing or unreadable."""
    try:
        rows = query(connection, f"SELECT KEY, VALUE FROM {CONFIG_FQN}")
    except OpsError as exc:
        raise OpsError(f"{exc}\nRun `make bootstrap` first, or check read privileges.") from None
    return {str(r.get("KEY")): str(r.get("VALUE") or "") for r in rows}


def owned_inventory(config: dict[str, str]) -> list[tuple[str, str]]:
    """Return registered (type, fqn) pairs, stopping on any ambiguous record."""
    try:
        raw = json.loads(config.get("owned_objects", "[]") or "[]")
    except json.JSONDecodeError:
        raise OpsError("owned_objects is not valid JSON; ownership is ambiguous.") from None
    if not isinstance(raw, list):
        raise OpsError("owned_objects is not a list; ownership is ambiguous.")
    prefix = config.get("object_prefix", "")
    warehouse = config.get("warehouse_name", "").upper()
    if raw and not _IDENT.fullmatch(prefix):
        raise OpsError(f"object_prefix {prefix!r} is invalid; ownership is ambiguous.")
    if (config.get("database", CONFIG_DB), config.get("schema", CONFIG_SCHEMA)) != (
        CONFIG_DB,
        CONFIG_SCHEMA,
    ):
        raise OpsError("Registry names a database/schema other than the manifest location.")
    prefix = prefix.upper()
    problems: list[str] = []
    inventory: list[tuple[str, str]] = []
    for obj in _parse_owned_objects(raw):
        if obj.object_type == "WAREHOUSE":
            match = _QUOTED_NAME.fullmatch(obj.fqn)
            ok = bool(match) and match[1] == warehouse and match[1] not in SHARED_WAREHOUSES
        elif obj.object_type in ("TABLE", "TASK"):
            match = _QUOTED_FQN.fullmatch(obj.fqn)
            stem = f"{prefix}_TASK_" if obj.object_type == "TASK" else f"{prefix}_"
            ok = (
                bool(match)
                and (match[1], match[2]) == (CONFIG_DB, CONFIG_SCHEMA)
                and match[3].startswith(stem)
                and match[3] != CONFIG_TABLE_NAME
                and (obj.object_type == "TASK" or not match[3].startswith(f"{prefix}_TASK_"))
            )
        else:
            ok = False
        if ok:
            inventory.append((obj.object_type, obj.fqn))
        else:
            problems.append(f"{obj.object_type} {obj.fqn}")
    if problems:
        raise OpsError("Ambiguous ownership; nothing changed:\n  " + "\n  ".join(problems))
    return inventory


def require_inventory(connection: str) -> list[tuple[str, str]]:
    """Return a non-empty, unambiguous inventory of registered objects."""
    inventory = owned_inventory(read_config(connection))
    if not inventory:
        raise OpsError("No registered acceleration objects; nothing to act on.")
    return inventory


def print_plan(title: str, statements: list[str], preserved: str = "") -> None:
    """Print the exact statements a command would run."""
    print(f"{title}:")
    for sql in statements:
        print(f"  {sql}")
    if preserved:
        print(f"Preserved: {preserved}")


def mutation_inputs() -> tuple[str, str]:
    """Return the connection and expected account every new mutation requires."""
    connection = require("CONNECTION", _CONNECTION)
    expected = require(
        "EXPECTED_ACCOUNT", _ACCOUNT, "Use the account identifier from `make inspect`."
    )
    return connection, expected


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def deploy_streamlit() -> None:
    """Deploy the Streamlit view, then attach the PyPI repository its pyproject.toml needs."""
    connection = require("CONNECTION", _CONNECTION)
    app_name = require("APP_NAME", _IDENT)
    app_title = require_title("APP_TITLE")
    require_snow()
    verify_account_if_set(connection)
    run(
        [
            "snow", "streamlit", "deploy", "streamlit_app", "--connection", connection,
            "--replace", "--prune", "--env", f"app_name={app_name}", "--env", f"app_title={app_title}",
        ]
    )  # fmt: skip
    # snowflake.yml has no artifact-repository field; without one the container cannot
    # resolve pyproject.toml from PyPI. The deploy already restarts the app, so setting
    # the same repository on every redeploy adds no extra disruption.
    run(
        [
            "snow", "sql", "--connection", connection, "-q",
            f"ALTER STREAMLIT {CONFIG_DB}.{CONFIG_SCHEMA}.{app_name} "
            f"SET ARTIFACT_REPOSITORIES = ({PYPI_REPOSITORY})",
        ]
    )  # fmt: skip


def deploy_app_runtime() -> None:
    """Validate, then deploy one reviewed App Runtime target."""
    connection = require("CONNECTION", _CONNECTION)
    target = require("TARGET", _TARGET)
    confirm = require("CONFIRM_EXAMPLE_TARGET")
    require_snow()
    if confirm != target:
        raise OpsError(
            "Set CONFIRM_EXAMPLE_TARGET to the exact example target name after reviewing web/app.yml."
        )
    verify_account_if_set(connection)
    run(["snow", "app", "validate", "--connection", connection, "--target", target], cwd=WEB)
    run(
        [
            "snow",
            "app",
            "deploy",
            "--connection",
            connection,
            "--target",
            target,
            "--force",
            "--verbose",
        ],
        cwd=WEB,
    )


def app_runtime_read(extra: list[str], verb: str) -> Callable[[], None]:
    """Build a read-only App Runtime command (open --print-only, events)."""

    def command() -> None:
        connection = require("CONNECTION", _CONNECTION)
        target = require("TARGET", _TARGET)
        require_snow()
        run(["snow", "app", verb, "--connection", connection, "--target", target, *extra], cwd=WEB)

    return command


def app_runtime_teardown() -> None:
    """Tear down one App Runtime target; data-plane resources are untouched."""
    connection = require("CONNECTION", _CONNECTION)
    target = require("TARGET", _TARGET)
    confirm = require("CONFIRM_TEARDOWN")
    if confirm != target:
        raise OpsError("Set CONFIRM_TEARDOWN to the exact target name.")
    require_snow()
    verify_account_if_set(connection)
    run(
        ["snow", "app", "teardown", "--connection", connection, "--target", target, "--force"],
        cwd=WEB,
    )


def smoke_streamlit() -> None:
    """Start the Streamlit view on loopback against one explicit connection."""
    connection = require("CONNECTION", _CONNECTION)
    require_tool("uv", "Install uv from https://docs.astral.sh/uv/")
    env = preview_env("SNOWFLAKE_DEFAULT_CONNECTION_NAME", connection)
    run(
        ["uv", "run", "streamlit", "run", "streamlit_app.py", "--server.address", "127.0.0.1"],
        env=env,
    )


def smoke_app_runtime() -> None:
    """Start the web view on loopback against one explicit connection."""
    connection = require("CONNECTION", _CONNECTION)
    require_tool("npm", "Install Node.js 22.12 or later on the 22.x line.")
    env = preview_env("SNOWFLAKE_CONNECTION_NAME", connection)
    run(["npm", "run", "dev", "--", "--hostname", "127.0.0.1"], cwd=WEB, env=env)


def inspect() -> None:
    """Show the manifest targets and, read-only, what the connection resolves to."""
    connection = require("CONNECTION", _CONNECTION)
    require_snow()
    print(f"Manifests read configuration from {CONFIG_LOCATION}.{CONFIG_TABLE_NAME}.")
    print("App Runtime targets: see web/app.yml (dev, test, prod share that data plane).")
    run(["snow", "connection", "test", "--connection", connection])
    rows = query(
        connection,
        "SELECT CURRENT_ACCOUNT() AS LOCATOR, CURRENT_ORGANIZATION_NAME() || '-' || "
        "CURRENT_ACCOUNT_NAME() AS ACCOUNT_ID, CURRENT_ROLE() AS ROLE, "
        "CURRENT_WAREHOUSE() AS WAREHOUSE",
    )
    print(json.dumps(rows[0] if rows else {}, indent=2))
    try:
        config = read_config(connection)
    except OpsError as exc:
        print(f"Config table: not readable.\n{exc}")
        return
    print(f"Config table: {len(config)} settings; owned_objects:")
    for object_type, fqn in owned_inventory(config):
        print(f"  {object_type} {fqn}")


def bootstrap() -> None:
    """Create the database, schema, and config table if absent (sql/setup.sql)."""
    connection, expected = mutation_inputs()
    database = require("DATABASE", _IDENT).upper()
    schema = require("SCHEMA", _IDENT).upper()
    if (database, schema) != (CONFIG_DB, CONFIG_SCHEMA):
        raise OpsError(
            f"Both runtimes read configuration from {CONFIG_LOCATION} (snowflake.yml, web/app.yml). "
            f"{database}.{schema} would be unreachable; edit the manifests and sql/setup.sql first."
        )
    print(
        "Plan: snow sql -f sql/setup.sql — CREATE DATABASE/SCHEMA/TABLE IF NOT EXISTS for "
        f"{CONFIG_LOCATION}.{CONFIG_TABLE_NAME}. Existing objects and settings are kept."
    )
    if not confirmed(CONFIG_LOCATION):
        return
    require_snow()
    verify_account(connection, expected)
    run(["snow", "sql", "--connection", connection, "-f", "sql/setup.sql"])


def grants() -> None:
    """Grant viewer access, optional admin deploy and config-write access, to existing roles."""
    connection, expected = mutation_inputs()
    viewer = require("VIEWER_ROLE", _IDENT)
    warehouse = require("QUERY_WAREHOUSE", _IDENT)
    admin = optional("ADMIN_ROLE", _IDENT)
    account_usage = optional("GRANT_ACCOUNT_USAGE", re.compile(r"1"))
    if account_usage and not admin:
        raise OpsError("GRANT_ACCOUNT_USAGE=1 grants to ADMIN_ROLE, the app owner; set ADMIN_ROLE.")
    require_snow()
    verify_account(connection, expected)
    tables = [fqn for kind, fqn in owned_inventory(read_config(connection)) if kind == "TABLE"]
    db, schema = quote_identifier(CONFIG_DB), quote_identifier(CONFIG_SCHEMA)
    statements: list[str] = []
    for role in [quote_identifier(r) for r in (viewer, admin) if r]:
        statements += [
            f"GRANT USAGE ON DATABASE {db} TO ROLE {role}",
            f"GRANT USAGE ON SCHEMA {db}.{schema} TO ROLE {role}",
            f"GRANT USAGE ON WAREHOUSE {quote_identifier(warehouse)} TO ROLE {role}",
            f"GRANT SELECT ON TABLE {CONFIG_FQN} TO ROLE {role}",
            *[f"GRANT SELECT ON TABLE {fqn} TO ROLE {role}" for fqn in tables],
        ]
    role = quote_identifier(viewer)
    for scope in ("ALL", "FUTURE"):
        statements += [
            f"GRANT USAGE ON {scope} STREAMLITS IN SCHEMA {db}.{schema} TO ROLE {role}",
            f"GRANT USAGE ON {scope} APPLICATION SERVICES IN SCHEMA {db}.{schema} TO ROLE {role}",
        ]
    if admin:
        role = quote_identifier(admin)
        statements += [
            f"GRANT INSERT, UPDATE ON TABLE {CONFIG_FQN} TO ROLE {role}",
            "GRANT CREATE STREAMLIT, CREATE STAGE, CREATE APPLICATION SERVICE, "
            f"CREATE ARTIFACT REPOSITORY ON SCHEMA {db}.{schema} TO ROLE {role}",
            f"GRANT USAGE ON COMPUTE POOL {quote_identifier(STREAMLIT_COMPUTE_POOL)} TO ROLE {role}",
            f"GRANT DATABASE ROLE SNOWFLAKE.PYPI_REPOSITORY_USER TO ROLE {role}",
        ]
    if account_usage:
        statements += [
            f"GRANT DATABASE ROLE SNOWFLAKE.{name} TO ROLE {quote_identifier(admin)}"
            for name in ACCOUNT_USAGE_ROLES
        ]
    print_plan(
        "Grants",
        statements,
        "no roles are created. Without GRANT_ACCOUNT_USAGE=1, ACCOUNT_USAGE access is granted "
        "separately (docs/OPERATIONS.md).",
    )
    if confirmed(viewer):
        execute_each(connection, statements)


def acceleration_status() -> None:
    """Show the registry and scheduled-task state, read-only."""
    connection = require("CONNECTION", _CONNECTION)
    require_snow()
    config = read_config(connection)
    inventory = owned_inventory(config)
    print(f"enabled={config.get('enabled', 'false')} registered objects={len(inventory)}")
    for object_type, fqn in inventory:
        print(f"  {object_type} {fqn}")
    prefix = config.get("object_prefix", "COST_MGMT").upper()
    rows = query(
        connection,
        f"SHOW TASKS LIKE '{prefix}_TASK_%' IN SCHEMA "
        f"{quote_identifier(CONFIG_DB)}.{quote_identifier(CONFIG_SCHEMA)}",
    )
    for row in rows:
        print(f"  task {row.get('name')}: {row.get('state')}")


def acceleration_provision() -> None:
    """Run sql/config.sql on a fresh config table only."""
    connection, expected = mutation_inputs()
    require_snow()
    verify_account(connection, expected)
    config = read_config(connection)
    if config:
        raise OpsError(
            f"{CONFIG_FQN} already holds settings ({', '.join(sorted(config))}). "
            "sql/config.sql replaces tasks and resets settings, so it runs only on a fresh "
            "install. Use Acceleration Settings or a reviewed targeted change."
        )
    print(
        "Plan: snow sql -f sql/config.sql — default names only (COST_MGMT prefix, COST_MGMT_WH); "
        "creates the task warehouse, aggregate tables and tasks, queues refreshes, seeds settings."
    )
    if confirmed(CONFIG_LOCATION):
        run(["snow", "sql", "--connection", connection, "-f", "sql/config.sql"])


def task_action(builder: Callable[[str], str], verb: str) -> Callable[[], None]:
    """Build a refresh/suspend/resume command over registered tasks."""

    def command() -> None:
        connection, expected = mutation_inputs()
        require_snow()
        verify_account(connection, expected)
        tasks = [fqn for kind, fqn in require_inventory(connection) if kind == "TASK"]
        statements = [builder(fqn) for fqn in tasks]
        print_plan(f"{verb} registered tasks", statements)
        if confirmed(CONFIG_LOCATION):
            execute_each(connection, statements)

    return command


def acceleration_teardown() -> None:
    """Drop registered tasks, tables, then warehouse; clear the registry last."""
    connection, expected = mutation_inputs()
    require_snow()
    verify_account(connection, expected)
    inventory = require_inventory(connection)
    order = {"TASK": 0, "TABLE": 1, "WAREHOUSE": 2}
    statements = [
        drop_object_sql(fqn, kind) for kind, fqn in sorted(inventory, key=lambda o: order[o[0]])
    ]
    statements.append(
        f"UPDATE {CONFIG_FQN} SET VALUE = IFF(KEY = 'enabled', 'false', '[]'), "
        "UPDATED_AT = CURRENT_TIMESTAMP() WHERE KEY IN ('owned_objects', 'enabled')"
    )
    print_plan(
        "Owned-resource teardown",
        statements,
        f"{CONFIG_FQN}, database, schema, roles, query warehouses, apps, artifact repositories.",
    )
    if confirmed(CONFIG_LOCATION):
        execute_each(connection, statements)


def _streamlit_fqn() -> tuple[str, str, str]:
    connection = require("CONNECTION", _CONNECTION)
    app_name = require("APP_NAME", _IDENT)
    # Validated unquoted identifier: resolves the same way the deploy command names it.
    return connection, app_name, f"{CONFIG_DB}.{CONFIG_SCHEMA}.{app_name}"


def streamlit_status() -> None:
    """Describe the deployed Streamlit object, read-only."""
    connection, _, fqn = _streamlit_fqn()
    require_snow()
    run(["snow", "sql", "--connection", connection, "-q", f"DESCRIBE STREAMLIT {fqn}"])


def streamlit_teardown() -> None:
    """Drop one Streamlit app object; shared data and other apps are preserved."""
    connection, app_name, fqn = _streamlit_fqn()
    expected = require("EXPECTED_ACCOUNT", _ACCOUNT)
    statement = f"DROP STREAMLIT IF EXISTS {fqn}"
    print_plan(
        "App-only teardown", [statement], "config table, aggregates, warehouses, other apps."
    )
    if not confirmed(app_name, "CONFIRM_TEARDOWN"):
        return
    require_snow()
    verify_account(connection, expected)
    run(["snow", "sql", "--connection", connection, "-q", statement])


COMMANDS: dict[str, Callable[[], None]] = {
    "deploy-streamlit": deploy_streamlit,
    "deploy-app-runtime": deploy_app_runtime,
    "app-runtime-open": app_runtime_read(["--print-only"], "open"),
    "app-runtime-events": app_runtime_read(["--last", "1000"], "events"),
    "app-runtime-teardown": app_runtime_teardown,
    "smoke-streamlit": smoke_streamlit,
    "smoke-app-runtime": smoke_app_runtime,
    "inspect": inspect,
    "bootstrap": bootstrap,
    "grants": grants,
    "acceleration-status": acceleration_status,
    "acceleration-provision": acceleration_provision,
    "acceleration-refresh": task_action(execute_task_sql, "Refresh"),
    "acceleration-suspend": task_action(suspend_task_sql, "Suspend"),
    "acceleration-resume": task_action(resume_task_sql, "Resume"),
    "acceleration-teardown": acceleration_teardown,
    "streamlit-status": streamlit_status,
    "streamlit-teardown": streamlit_teardown,
}


def main(argv: list[str]) -> int:
    """Dispatch one subcommand; preflight failures exit 2."""
    # Plans and results print to stdout and command echoes to stderr; line buffering
    # keeps them in order when output is piped or logged.
    sys.stdout.reconfigure(line_buffering=True)
    if len(argv) != 1 or argv[0] not in COMMANDS:
        print(f"usage: ops.py {{{','.join(COMMANDS)}}}", file=sys.stderr)
        return 2
    try:
        COMMANDS[argv[0]]()
    except OpsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
