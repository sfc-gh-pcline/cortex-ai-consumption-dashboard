"""Offline command-recorder tests for the root Makefile and scripts/ops.py.

Each test runs real ``make`` with fake ``snow``/``npm``/``npx``/``uv``/``uvx`` on a
minimal PATH. The fakes record argv, working directory, and Snowflake-related
environment variables, and answer read-only ``snow sql --format JSON`` queries from
canned responses. Nothing reaches Snowflake. "Side effect" below means any recorded
call other than ``snow --version`` or a read-only JSON query.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import ops

REPO = Path(__file__).resolve().parents[1]
MAKE = shutil.which("make")
NODE = shutil.which("node")
OPS = f"{sys.executable} -m scripts.ops"
pytestmark = pytest.mark.skipif(MAKE is None, reason="GNU Make is required")

FAKE = """#!{python}
import json, os, sys
tool = os.path.basename(sys.argv[0])
argv = sys.argv[1:]
env = {{k: v for k, v in os.environ.items() if k.startswith(("SNOWFLAKE_", "DASHBOARD_"))}}
with open(os.environ["RECORD"], "a") as fh:
    fh.write(json.dumps({{"tool": tool, "argv": argv, "cwd": os.getcwd(), "env": env}}) + "\\n")
if tool == "snow" and argv == ["--version"]:
    print("Snowflake CLI version: " + os.environ.get("FAKE_SNOW_VERSION", "3.28.0"))
    sys.exit(0)
if tool == "snow" and "JSON" in argv:
    sql = argv[-1]
    for needle, out, code in json.load(open(os.environ["RESPONSES"])):
        if needle in sql:
            print(out)
            sys.exit(code)
    print("[]")
"""

ACCOUNT = [["CURRENT_ACCOUNT()", json.dumps([{"LOCATOR": "AB12345", "ACCOUNT_ID": "ORG-DEMO", "ROLE": "R"}]), 0]]  # fmt: skip
TASK = '"COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COCO"'
TABLE = '"COST_MGMT_DB"."PUBLIC"."COST_MGMT_COCO"'
REGISTRY = [
    {"fqn": TASK, "type": "TASK"},
    {"fqn": TABLE, "type": "TABLE"},
    {"fqn": '"COST_MGMT_WH"', "type": "WAREHOUSE"},
]
FORMER_TASKS = [
    "install", "validate:python", "validate:web", "validate:docs", "validate",
    "deploy:streamlit", "deploy:app-runtime", "app-runtime:open", "app-runtime:events",
    "app-runtime:teardown", "smoke:streamlit-live", "smoke:app-runtime-live",
]  # fmt: skip


def config_rows(registry: object = REGISTRY, **overrides: str) -> list:
    """Return a canned config-table response."""
    values = {
        "database": "COST_MGMT_DB",
        "schema": "PUBLIC",
        "object_prefix": "COST_MGMT",
        "warehouse_name": "COST_MGMT_WH",
        "owned_objects": registry if isinstance(registry, str) else json.dumps(registry),
        **overrides,
    }
    rows = [{"KEY": k, "VALUE": v} for k, v in values.items()]
    return [["SELECT KEY, VALUE FROM", json.dumps(rows), 0]]


class Harness:
    """Fake tool directory plus a make runner that captures recorded calls."""

    def __init__(self, tmp: Path):
        """Create fake tools under *tmp* that record into *tmp*/record.jsonl."""
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.record = tmp / "record.jsonl"
        self.responses = tmp / "responses.json"
        self.home = tmp / "home"
        self.home.mkdir()
        self.respond([])
        for tool in ("snow", "npm", "npx", "uv", "uvx"):
            path = self.bin / tool
            path.write_text(FAKE.format(python=sys.executable))
            path.chmod(0o755)
        if NODE:
            (self.bin / "node").symlink_to(NODE)

    def respond(self, responses: list) -> None:
        self.responses.write_text(json.dumps(responses))

    def remove(self, tool: str) -> None:
        (self.bin / tool).unlink()

    def make(self, target: str, *assignments: str, env: dict[str, str] | None = None):
        run_env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.home),
            "RECORD": str(self.record),
            "RESPONSES": str(self.responses),
            **(env or {}),
        }
        argv = [MAKE, "--no-print-directory", "-C", str(REPO), target, f"OPS={OPS}", *assignments]
        return subprocess.run(argv, env=run_env, capture_output=True, text=True, check=False)

    def calls(self) -> list[dict]:
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def side_effects(self) -> list[dict]:
        return [
            c
            for c in self.calls()
            if not (c["tool"] == "snow" and (c["argv"] == ["--version"] or "JSON" in c["argv"]))
        ]


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Interface and parity
# ---------------------------------------------------------------------------


def test_default_target_lists_every_command(h):
    result = h.make("help")
    phony = (REPO / "Makefile").read_text().split(".PHONY:")[1].split("\n\n")[0]
    targets = [t.replace("\\:", ":") for t in phony.replace("\\\n", " ").split()]
    default = subprocess.run(
        [MAKE, "--no-print-directory", "-C", str(REPO)], capture_output=True, text=True, check=True
    )
    assert default.stdout == result.stdout
    missing = [t for t in targets if t != "help" and t not in result.stdout]
    assert missing == []
    assert set(FORMER_TASKS) <= set(targets)
    assert not (REPO / "Taskfile.yml").exists()


def test_install_order_and_missing_tool(h):
    assert h.make("install").returncode == 0
    assert [(c["tool"], c["argv"], c["cwd"]) for c in h.calls()] == [
        ("uv", ["sync", "--group", "dev"], str(REPO)),
        ("npm", ["--prefix", "web", "ci"], str(REPO)),
    ]
    h.record.unlink()
    h.remove("uv")
    result = h.make("install")
    assert result.returncode != 0
    assert "Install uv from https://docs.astral.sh/uv/" in result.stderr
    assert h.calls() == []


PY_PATHS = ["streamlit_app.py", "app_pages", "utils", "tests", "scripts"]
PYTHON_CALLS = [
    ["run", "--group", "dev", "ruff", "check", *PY_PATHS],
    ["run", "--group", "dev", "ruff", "format", "--check", *PY_PATHS],
    ["run", "--python", "3.11", "python", "-m", "compileall", "-q", *PY_PATHS],
    ["run", "--python", "3.11", "--group", "dev", "pytest", "-m", "not snowflake"],
]
BUILD_ENV = {
    "SNOWFLAKE_ACCOUNT": "offline",
    "SNOWFLAKE_USER": "offline",
    "SNOWFLAKE_PASSWORD": "not-a-credential",
    "SNOWFLAKE_CONNECTION_NAME": "e2e-no-such-connection",
    "SNOWFLAKE_DEFAULT_CONNECTION_NAME": "e2e-no-such-connection",
    "SNOWFLAKE_DISABLE_PLATFORM_DETECTION": "true",
}


def test_validate_python_runs_the_same_checks_in_order(h):
    assert h.make("validate:python").returncode == 0
    assert [c["argv"] for c in h.calls()] == PYTHON_CALLS
    assert {c["tool"] for c in h.calls()} == {"uv"}


@pytest.mark.skipif(NODE is None, reason="the e2e preflight runs on Node.js")
def test_validate_runs_python_web_docs_and_scopes_build_env(h):
    result = h.make("validate", f"E2E_PORT={free_port()}")
    assert result.returncode == 0, result.stderr
    calls = h.calls()
    assert [c["argv"] for c in calls[:4]] == PYTHON_CALLS
    web = [c["argv"] for c in calls[4:10]]
    assert web == [
        ["--prefix", "web", "run", "lint"],
        ["--prefix", "web", "run", "typecheck"],
        ["--prefix", "web", "run", "format:check"],
        ["--prefix", "web", "test"],
        ["--prefix", "web", "run", "build"],
        ["--prefix", "web", "run", "e2e"],
    ]
    docs = calls[10]
    assert docs["tool"] == "uvx" and docs["argv"][:6] == [
        "--from", "pymarkdownlnt", "pymarkdown", "--config", ".pymarkdown.yml", "scan",
    ]  # fmt: skip
    assert "docs/APP_RUNTIME.md" in docs["argv"] and "docs/OPERATIONS.md" in docs["argv"]
    assert len(calls) == 11
    build = calls[8]
    assert build["env"] == BUILD_ENV
    for call in calls[4:8] + calls[9:]:
        assert not set(BUILD_ENV) & set(call["env"]), call["argv"]


def test_webpack_compiler_is_explicit_and_validated(h):
    assert h.make("web:build", "WEB_COMPILER=webpack").returncode == 0
    assert h.calls()[0]["argv"] == ["--prefix", "web", "run", "build", "--", "--webpack"]
    h.record.unlink()
    result = h.make("web:build", "WEB_COMPILER=rspack")
    assert result.returncode != 0 and "turbopack or webpack" in result.stderr
    assert h.calls() == []


@pytest.mark.skipif(NODE is None, reason="the e2e preflight runs on Node.js")
@pytest.mark.parametrize(
    ("assignment", "message"),
    [
        ("E2E_SERVER=staging", "dev or production"),
        ("E2E_PORT=80", "1024 to 65535"),
        ("E2E_PORT=3100;id", "1024 to 65535"),
    ],
)
def test_e2e_preflight_rejects_bad_inputs(h, assignment, message):
    result = h.make("web:e2e", assignment)
    assert result.returncode != 0 and message in result.stderr
    assert h.calls() == []


@pytest.mark.skipif(NODE is None, reason="the e2e preflight runs on Node.js")
def test_e2e_refuses_a_port_another_server_holds(h):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        result = h.make("web:e2e", f"E2E_PORT={busy.getsockname()[1]}")
    assert result.returncode != 0 and "is in use" in result.stderr
    assert h.calls() == []


def test_browser_server_is_isolated_from_caller_credentials():
    # Playwright merges webServer.env over process.env, so these must be overridden.
    config = (REPO / "web" / "playwright.config.ts").read_text()
    assert "reuseExistingServer: false" in config
    assert "--hostname 127.0.0.1" in config and 'HOSTNAME: "127.0.0.1"' in config
    for name in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_HOST", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"):
        assert f'{name}: ""' in config


def test_docs_and_browser_install(h):
    assert h.make("install:browser", "WITH_DEPS=1").returncode == 0
    assert h.calls()[-1]["argv"] == [
        "--prefix", "web", "playwright", "install", "--with-deps", "chromium",
    ]  # fmt: skip


def test_snowflake_targets_default_to_uv():
    dry = subprocess.run(
        [MAKE, "-n", "-C", str(REPO), "bootstrap"], capture_output=True, text=True, check=True
    )
    assert "uv run --python 3.11 python -m scripts.ops bootstrap" in dry.stdout


# ---------------------------------------------------------------------------
# Deploy, inspect, and teardown commands
# ---------------------------------------------------------------------------

TITLE = "Cortex AI Consumption Dashboard"


def test_deploy_streamlit_passes_values_with_spaces_intact(h):
    result = h.make(
        "deploy:streamlit", "CONNECTION=my_conn", "APP_NAME=MY_DASH", f"APP_TITLE={TITLE}"
    )
    assert result.returncode == 0, result.stderr
    assert [c["argv"] for c in h.side_effects()] == [
        [
            "streamlit", "deploy", "streamlit_app", "--connection", "my_conn", "--replace",
            "--prune", "--env", "app_name=MY_DASH", "--env", f"app_title={TITLE}",
        ],
        [
            "sql", "--connection", "my_conn", "-q",
            "ALTER STREAMLIT COST_MGMT_DB.PUBLIC.MY_DASH "
            "SET ARTIFACT_REPOSITORIES = (SNOWFLAKE.SNOWPARK.PYPI_SHARED_REPOSITORY)",
        ],
    ]  # fmt: skip
    assert not [c for c in h.calls() if "JSON" in c["argv"]]  # no account read unless asked


@pytest.mark.parametrize(
    "assignments",
    [
        ["APP_NAME=MY_DASH", f"APP_TITLE={TITLE}"],
        ["CONNECTION=c", f"APP_TITLE={TITLE}"],
        ["CONNECTION=c", "APP_NAME=MY_DASH"],
        ["CONNECTION=c --account other", "APP_NAME=MY_DASH", f"APP_TITLE={TITLE}"],
        ["CONNECTION=c;touch pwned", "APP_NAME=MY_DASH", f"APP_TITLE={TITLE}"],
        ["CONNECTION=c", "APP_NAME=X; DROP DATABASE D", f"APP_TITLE={TITLE}"],
        ["CONNECTION=c", "APP_NAME=MY_DASH", 'APP_TITLE=a" --env "app_name=EVIL'],
        ["CONNECTION=c", "APP_NAME=MY_DASH", "APP_TITLE=<% ctx.env.x %>"],
        ["CONNECTION=c", "APP_NAME=MY_DASH", "APP_TITLE=`touch pwned`"],
    ],
)
def test_deploy_streamlit_rejects_missing_and_injection_shaped_inputs(h, assignments):
    result = h.make("deploy:streamlit", *assignments)
    assert result.returncode != 0
    assert "error:" in result.stderr
    assert h.calls() == []
    assert not (REPO / "pwned").exists()


def test_missing_or_old_snowflake_cli_fails_before_side_effects(h):
    args = ("CONNECTION=c", "TARGET=test", "CONFIRM_EXAMPLE_TARGET=test")
    result = h.make("deploy:app-runtime", *args, env={"FAKE_SNOW_VERSION": "3.12.0"})
    assert result.returncode != 0 and "3.27 or later" in result.stderr
    assert h.side_effects() == []
    h.remove("snow")
    result = h.make("deploy:app-runtime", *args)
    assert result.returncode != 0 and "Install Snowflake CLI" in result.stderr


def test_deploy_app_runtime_validates_then_deploys_from_web(h):
    args = ("CONNECTION=c", "TARGET=test", "CONFIRM_EXAMPLE_TARGET=test")
    assert h.make("deploy:app-runtime", *args).returncode == 0
    calls = h.side_effects()
    assert [c["argv"] for c in calls] == [
        ["app", "validate", "--connection", "c", "--target", "test"],
        ["app", "deploy", "--connection", "c", "--target", "test", "--force", "--verbose"],
    ]
    assert {c["cwd"] for c in calls} == {str(REPO / "web")}


@pytest.mark.parametrize(
    ("target", "assignments"),
    [
        ("deploy:app-runtime", ["TARGET=test", "CONFIRM_EXAMPLE_TARGET=prod"]),
        ("deploy:app-runtime", ["TARGET=test"]),
        ("app-runtime:teardown", ["TARGET=test", "CONFIRM_TEARDOWN=TEST"]),
        ("app-runtime:teardown", ["TARGET=test"]),
        ("app-runtime:teardown", ["TARGET=../x", "CONFIRM_TEARDOWN=../x"]),
    ],
)
def test_app_runtime_confirmation_mismatch_has_no_side_effect(h, target, assignments):
    result = h.make(target, "CONNECTION=c", *assignments)
    assert result.returncode != 0
    assert h.side_effects() == []


def test_expected_account_mismatch_stops_a_deploy(h):
    h.respond(ACCOUNT)
    args = ("CONNECTION=c", "TARGET=test", "CONFIRM_EXAMPLE_TARGET=test", "EXPECTED_ACCOUNT=OTHER")
    result = h.make("deploy:app-runtime", *args)
    assert result.returncode != 0 and "not EXPECTED_ACCOUNT" in result.stderr
    assert h.side_effects() == []
    assert h.make("deploy:app-runtime", *args[:3], "EXPECTED_ACCOUNT=org-demo").returncode == 0


@pytest.mark.parametrize(
    ("target", "tail"),
    [
        ("app-runtime:open", ["open", "--connection", "c", "--target", "test", "--print-only"]),
        (
            "app-runtime:events",
            ["events", "--connection", "c", "--target", "test", "--last", "1000"],
        ),
        ("app-runtime:teardown", ["teardown", "--connection", "c", "--target", "test", "--force"]),
    ],
)
def test_app_runtime_commands(h, target, tail):
    result = h.make(target, "CONNECTION=c", "TARGET=test", "CONFIRM_TEARDOWN=test")
    assert result.returncode == 0, result.stderr
    [call] = h.side_effects()
    assert call["argv"] == ["app", *tail] and call["cwd"] == str(REPO / "web")


@pytest.mark.parametrize(
    ("target", "tool", "argv", "cwd", "connection_var"),
    [
        (
            "smoke:streamlit-live", "uv",
            ["run", "streamlit", "run", "streamlit_app.py", "--server.address", "127.0.0.1"],
            REPO, "SNOWFLAKE_DEFAULT_CONNECTION_NAME",
        ),
        (
            "smoke:app-runtime-live", "npm",
            ["run", "dev", "--", "--hostname", "127.0.0.1"],
            REPO / "web", "SNOWFLAKE_CONNECTION_NAME",
        ),
    ],
)  # fmt: skip
def test_live_previews_bind_loopback_and_fail_closed(h, target, tool, argv, cwd, connection_var):
    caller = {"DASHBOARD_ADMIN_USERS": '["ME"]', "SNOWFLAKE_PASSWORD": "caller-secret"}
    result = h.make(target, "CONNECTION=my_conn", env=caller)
    assert result.returncode == 0, result.stderr
    [call] = h.calls()
    assert (call["tool"], call["argv"], call["cwd"]) == (tool, argv, str(cwd))
    assert call["env"][connection_var] == "my_conn"
    assert call["env"]["DASHBOARD_ADMIN_USERS"] == "[]"
    assert "SNOWFLAKE_PASSWORD" not in call["env"]
    assert h.make(target).returncode != 0


def test_inspect_is_read_only(h):
    h.respond(ACCOUNT + config_rows())
    result = h.make("inspect", "CONNECTION=c")
    assert result.returncode == 0, result.stderr
    assert [c["argv"] for c in h.side_effects()] == [["connection", "test", "--connection", "c"]]
    assert TASK in result.stdout


def test_streamlit_status_and_app_only_teardown(h):
    assert h.make("streamlit:status", "CONNECTION=c", "APP_NAME=MY_DASH").returncode == 0
    assert h.side_effects()[-1]["argv"][-1] == "DESCRIBE STREAMLIT COST_MGMT_DB.PUBLIC.MY_DASH"
    h.record.unlink()
    args = ("CONNECTION=c", "APP_NAME=MY_DASH", "EXPECTED_ACCOUNT=AB12345")
    dry = h.make("streamlit:teardown", *args)
    assert dry.returncode == 0 and "Dry run" in dry.stdout
    assert h.calls() == []
    assert h.make("streamlit:teardown", *args, "CONFIRM_TEARDOWN=OTHER").returncode != 0
    assert h.calls() == []
    h.respond(ACCOUNT)
    assert h.make("streamlit:teardown", *args, "CONFIRM_TEARDOWN=MY_DASH").returncode == 0
    [call] = h.side_effects()
    assert call["argv"][-1] == "DROP STREAMLIT IF EXISTS COST_MGMT_DB.PUBLIC.MY_DASH"


# ---------------------------------------------------------------------------
# Bootstrap, grants, and acceleration lifecycle
# ---------------------------------------------------------------------------

TARGET = ("CONNECTION=c", "EXPECTED_ACCOUNT=AB12345")
BOOT = (*TARGET, "DATABASE=COST_MGMT_DB", "SCHEMA=PUBLIC")


def test_bootstrap_dry_run_confirm_and_execute(h):
    dry = h.make("bootstrap", *BOOT)
    assert dry.returncode == 0 and "Dry run" in dry.stdout and h.calls() == []
    assert h.make("bootstrap", *BOOT, "CONFIRM=COST_MGMT_DB").returncode != 0
    assert h.calls() == []
    h.respond(ACCOUNT)
    assert h.make("bootstrap", *BOOT, "CONFIRM=COST_MGMT_DB.PUBLIC").returncode == 0
    assert [c["argv"] for c in h.side_effects()] == [
        ["sql", "--connection", "c", "-f", "sql/setup.sql"]
    ]


@pytest.mark.parametrize(
    "assignments",
    [
        ["CONNECTION=c", "DATABASE=COST_MGMT_DB", "SCHEMA=PUBLIC"],
        [*TARGET, "DATABASE=OTHER_DB", "SCHEMA=PUBLIC"],
        [*TARGET, 'DATABASE=COST_MGMT_DB"; DROP', "SCHEMA=PUBLIC"],
        ["CONNECTION=c", "EXPECTED_ACCOUNT=a b", "DATABASE=COST_MGMT_DB", "SCHEMA=PUBLIC"],
    ],
)
def test_bootstrap_rejects_inputs_before_side_effects(h, assignments):
    result = h.make("bootstrap", *assignments, "CONFIRM=COST_MGMT_DB.PUBLIC")
    assert result.returncode != 0
    assert h.calls() == []


def test_bootstrap_account_mismatch(h):
    h.respond(ACCOUNT)
    args = ("CONNECTION=c", "EXPECTED_ACCOUNT=ZZ999", *BOOT[2:], "CONFIRM=COST_MGMT_DB.PUBLIC")
    assert h.make("bootstrap", *args).returncode != 0
    assert h.side_effects() == []


def test_grants_are_least_privilege_and_opt_in(h):
    h.respond(ACCOUNT + config_rows())
    args = (
        *TARGET,
        "VIEWER_ROLE=DASH_VIEWER",
        "QUERY_WAREHOUSE=COMPUTE_WH",
        "ADMIN_ROLE=DASH_ADMIN",
    )
    dry = h.make("grants", *args)
    assert dry.returncode == 0 and "Dry run" in dry.stdout
    assert h.side_effects() == []
    assert h.make("grants", *args, "CONFIRM=DASH_VIEWER").returncode == 0
    statements = [c["argv"][-1] for c in h.side_effects()]
    assert all(s.startswith("GRANT ") for s in statements)
    assert f'GRANT SELECT ON TABLE {TABLE} TO ROLE "DASH_VIEWER"' in statements
    writes = [s for s in statements if "INSERT" in s]
    assert writes == [
        'GRANT INSERT, UPDATE ON TABLE "COST_MGMT_DB"."PUBLIC".CORTEX_DASHBOARD_CONFIG '
        'TO ROLE "DASH_ADMIN"'
    ]
    schema = '"COST_MGMT_DB"."PUBLIC"'
    assert (
        f'GRANT USAGE ON FUTURE STREAMLITS IN SCHEMA {schema} TO ROLE "DASH_VIEWER"' in statements
    )
    assert (
        f'GRANT USAGE ON ALL APPLICATION SERVICES IN SCHEMA {schema} TO ROLE "DASH_VIEWER"'
        in statements
    )
    deploy = [s for s in statements if "CREATE" in s or "COMPUTE POOL" in s]
    assert deploy == [
        "GRANT CREATE STREAMLIT, CREATE STAGE, CREATE APPLICATION SERVICE, CREATE ARTIFACT "
        f'REPOSITORY ON SCHEMA {schema} TO ROLE "DASH_ADMIN"',
        'GRANT USAGE ON COMPUTE POOL "SYSTEM_COMPUTE_POOL_CPU" TO ROLE "DASH_ADMIN"',
    ]
    assert [s for s in statements if "DATABASE ROLE" in s] == [
        'GRANT DATABASE ROLE SNOWFLAKE.PYPI_REPOSITORY_USER TO ROLE "DASH_ADMIN"'
    ]
    assert not any(token in " ".join(statements) for token in ("ACCOUNTADMIN", "CREATE ROLE", "OWNERSHIP"))  # fmt: skip


def test_grants_account_usage_is_opt_in_for_the_admin_role(h):
    h.respond(ACCOUNT + config_rows())
    base = (*TARGET, "VIEWER_ROLE=DASH_VIEWER", "QUERY_WAREHOUSE=COMPUTE_WH")
    no_admin = h.make("grants", *base, "GRANT_ACCOUNT_USAGE=1")
    assert no_admin.returncode == 2 and "set ADMIN_ROLE" in no_admin.stderr
    bad = h.make("grants", *base, "ADMIN_ROLE=DASH_ADMIN", "GRANT_ACCOUNT_USAGE=yes")
    assert bad.returncode == 2 and "GRANT_ACCOUNT_USAGE" in bad.stderr
    assert h.side_effects() == []
    args = (*base, "ADMIN_ROLE=DASH_ADMIN", "GRANT_ACCOUNT_USAGE=1", "CONFIRM=DASH_VIEWER")
    assert h.make("grants", *args).returncode == 0
    roles = [c["argv"][-1] for c in h.side_effects() if "DATABASE ROLE" in c["argv"][-1]]
    assert roles == [
        f'GRANT DATABASE ROLE SNOWFLAKE.{name} TO ROLE "DASH_ADMIN"'
        for name in ("PYPI_REPOSITORY_USER", "USAGE_VIEWER", "GOVERNANCE_VIEWER", "SECURITY_VIEWER")
    ]


def test_provision_runs_only_on_a_fresh_config_table(h):
    h.respond(ACCOUNT + config_rows())
    result = h.make("acceleration:provision", *TARGET, "CONFIRM=COST_MGMT_DB.PUBLIC")
    assert result.returncode != 0 and "fresh install" in result.stderr
    assert h.side_effects() == []
    h.respond([*ACCOUNT, ["SELECT KEY, VALUE FROM", "[]", 0]])
    assert h.make("acceleration:provision", *TARGET, "CONFIRM=COST_MGMT_DB.PUBLIC").returncode == 0
    assert [c["argv"] for c in h.side_effects()] == [
        ["sql", "--connection", "c", "-f", "sql/config.sql"]
    ]


def test_missing_config_table_stops_lifecycle_commands(h):
    h.respond([*ACCOUNT, ["SELECT KEY, VALUE FROM", "does not exist", 1]])
    result = h.make("acceleration:teardown", *TARGET, "CONFIRM=COST_MGMT_DB.PUBLIC")
    assert result.returncode != 0 and "make bootstrap" in result.stderr
    assert h.side_effects() == []


def test_owned_teardown_order_and_preserved_resources(h):
    h.respond(ACCOUNT + config_rows())
    dry = h.make("acceleration:teardown", *TARGET)
    assert dry.returncode == 0 and "Preserved:" in dry.stdout and TASK in dry.stdout
    assert h.side_effects() == []
    assert h.make("acceleration:teardown", *TARGET, "CONFIRM=COST_MGMT_DB.PUBLIC").returncode == 0
    statements = [c["argv"][-1] for c in h.side_effects()]
    assert statements[:3] == [
        f"DROP TASK IF EXISTS {TASK}",
        f"DROP TABLE IF EXISTS {TABLE}",
        'DROP WAREHOUSE IF EXISTS "COST_MGMT_WH"',
    ]
    assert statements[3].startswith('UPDATE "COST_MGMT_DB"."PUBLIC".CORTEX_DASHBOARD_CONFIG')
    assert len(statements) == 4
    assert not any("DATABASE" in s or "SCHEMA" in s or "CORTEX_DASHBOARD_CONFIG" in s for s in statements[:3])  # fmt: skip


@pytest.mark.parametrize(
    "registry",
    [
        [{"fqn": '"OTHER_DB"."PUBLIC"."COST_MGMT_COCO"', "type": "TABLE"}],
        [{"fqn": '"COST_MGMT_DB"."PUBLIC"."SOMEONE_ELSES_TABLE"', "type": "TABLE"}],
        [{"fqn": '"COST_MGMT_DB"."PUBLIC"."CORTEX_DASHBOARD_CONFIG"', "type": "TABLE"}],
        [{"fqn": '"COMPUTE_WH"', "type": "WAREHOUSE"}],
        [{"fqn": '"ANOTHER_WH"', "type": "WAREHOUSE"}],
        [{"fqn": '"COST_MGMT_DB"', "type": "DATABASE"}],
        [{"fqn": TASK + "; DROP DATABASE X", "type": "TASK"}],
        "not json",
        {"fqn": TASK},
        [],
    ],
)
def test_ambiguous_ownership_stops_teardown(h, registry):
    h.respond(ACCOUNT + config_rows(registry))
    result = h.make("acceleration:teardown", *TARGET, "CONFIRM=COST_MGMT_DB.PUBLIC")
    assert result.returncode != 0
    assert h.side_effects() == []


def test_shared_query_warehouse_is_never_owned(h):
    h.respond(ACCOUNT + config_rows([{"fqn": '"COMPUTE_WH"', "type": "WAREHOUSE"}], warehouse_name="COMPUTE_WH"))  # fmt: skip
    result = h.make("acceleration:teardown", *TARGET, "CONFIRM=COST_MGMT_DB.PUBLIC")
    assert result.returncode != 0 and "Ambiguous ownership" in result.stderr
    assert h.side_effects() == []


@pytest.mark.parametrize(
    ("target", "statement"),
    [
        ("acceleration:refresh", f"EXECUTE TASK {TASK}"),
        ("acceleration:suspend", f"ALTER TASK {TASK} SUSPEND"),
        ("acceleration:resume", f"ALTER TASK {TASK} RESUME"),
    ],
)
def test_task_actions_touch_registered_tasks_only(h, target, statement):
    h.respond(ACCOUNT + config_rows())
    assert h.make(target, *TARGET, "CONFIRM=wrong").returncode != 0
    assert h.side_effects() == []
    assert h.make(target, *TARGET, "CONFIRM=COST_MGMT_DB.PUBLIC").returncode == 0
    assert [c["argv"][-1] for c in h.side_effects()] == [statement]


def test_status_is_read_only(h):
    h.respond(ACCOUNT + config_rows())
    assert h.make("acceleration:status", "CONNECTION=c").returncode == 0
    assert h.side_effects() == []


def test_ops_config_location_matches_both_manifests():
    streamlit = yaml.safe_load((REPO / "snowflake.yml").read_text())["entities"]["streamlit_app"]
    assert (streamlit["identifier"]["database"], streamlit["identifier"]["schema"]) == (
        ops.CONFIG_DB,
        ops.CONFIG_SCHEMA,
    )
    web = yaml.safe_load((REPO / "web" / "app.yml").read_text())
    envs = [web["environment_variables"]] + [
        t["environment_variables"] for t in web["targets"].values() if "environment_variables" in t
    ]
    for env in envs:
        values = {e["name"]: e["value"] for e in env}
        assert values["DASHBOARD_CONFIG_DATABASE"] == ops.CONFIG_DB
        assert values["DASHBOARD_CONFIG_SCHEMA"] == ops.CONFIG_SCHEMA
        assert values["DASHBOARD_ADMIN_USERS"] == "[]"
    assert {streamlit["query_warehouse"], web["query_warehouse"]} <= ops.SHARED_WAREHOUSES
    assert streamlit["compute_pool"] == ops.STREAMLIT_COMPUTE_POOL


def test_ops_rejects_unknown_subcommand():
    result = subprocess.run(
        [sys.executable, "-m", "scripts.ops", "drop-everything"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 2 and "usage:" in result.stderr
