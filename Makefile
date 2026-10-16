# Root command interface. Run `make` for the command list and docs/OPERATIONS.md for
# side effects, inputs, ownership limits, and confirmations.
#
# Inputs are Make variables (`make deploy:streamlit CONNECTION=c APP_NAME=N APP_TITLE="A B"`).
# Recipes read them from the environment as "$$VAR", so values with spaces or shell
# metacharacters reach the command as one argument. Snowflake commands run through
# scripts/ops.py, which validates every input before calling the Snowflake CLI.
#
# GNU Make 3.81 does not run escaped-colon prerequisites, so aggregates recurse.

SHELL := /bin/bash
.DEFAULT_GOAL := help

PYTHON_PATHS := streamlit_app.py app_pages utils tests scripts
DOC_PATHS := README.md CONTRIBUTING.md CHANGELOG.md web/README.md docs/*.md
OPS ?= uv run --python 3.11 python -m scripts.ops
SUB := $(MAKE) --no-print-directory
PROJECT_VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo nogit)

WEB_COMPILER ?= turbopack
E2E_SERVER ?= dev
E2E_PORT ?= 3100
export WEB_COMPILER E2E_SERVER E2E_PORT

# $(call need,tool,message): fail with an actionable message when a tool is missing.
need = @command -v $(1) >/dev/null 2>&1 || { echo "$(2)" >&2; exit 1; }
NEED_UV := Install uv from https://docs.astral.sh/uv/
NEED_NPM := Install Node.js 22.12 or later on the 22.x line.
# $(call ops,subcommand): run a guarded Snowflake command.
define ops
$(call need,$(firstword $(OPS)),Snowflake commands run through uv: $(NEED_UV))
@$(OPS) $(1)
endef

.PHONY: help doctor install install\:browser \
	validate validate\:python validate\:web validate\:docs \
	web\:lint web\:typecheck web\:format web\:test web\:build web\:e2e \
	smoke\:streamlit-live smoke\:app-runtime-live inspect bootstrap grants \
	acceleration\:status acceleration\:provision acceleration\:refresh \
	acceleration\:suspend acceleration\:resume acceleration\:teardown \
	deploy\:streamlit streamlit\:status streamlit\:teardown \
	deploy\:app-runtime app-runtime\:open app-runtime\:events app-runtime\:teardown

help:
	@if [ -t 1 ] && [ -z "$${NO_COLOR:-}" ]; then \
	  B=$$'\033[1m'; T=$$'\033[1;36m'; C=$$'\033[36m'; Y=$$'\033[33m'; D=$$'\033[2m'; R=$$'\033[0m'; \
	else B=; T=; C=; Y=; D=; R=; fi; \
	h() { printf '\n%s%s%s\n' "$$B" "$$1" "$$R"; }; \
	n() { printf '  %s%s%s\n' "$$D" "$$1" "$$R"; }; \
	t() { printf '  %s%-24s%s %s\n' "$$C" "$$1" "$$R" "$$2"; \
	  [ -z "$$3" ] || printf '  %-24s %s%s%s\n' '' "$$Y" "$$3" "$$R"; }; \
	printf '\n%sCortex AI Consumption Dashboard%s v%s  %s(%s)%s\n' "$$T" "$$R" '$(PROJECT_VERSION)' "$$D" '$(GIT_SHA)' "$$R"; \
	printf 'Usage: make <target> [NAME=value ...]  %sDetails: docs/OPERATIONS.md%s\n' "$$D" "$$R"; \
	h 'Local (no Snowflake access)'; \
	t 'doctor' 'Check tools and runtime versions' ''; \
	t 'install' 'uv sync --group dev, then npm --prefix web ci' ''; \
	t 'install:browser' 'Install Playwright Chromium' '[WITH_DEPS=1]'; \
	t 'validate' 'validate:python, validate:web, validate:docs' ''; \
	t 'validate:python' 'Ruff lint/format, compileall, pytest -m "not snowflake"' ''; \
	t 'validate:web' 'web:lint typecheck format test build e2e, in order' ''; \
	t 'validate:docs' 'Lint maintained Markdown' ''; \
	t 'web:lint' 'ESLint' ''; \
	t 'web:typecheck' 'Route types and tsc' ''; \
	t 'web:format' 'Prettier check' ''; \
	t 'web:test' 'Vitest unit tests' ''; \
	t 'web:build' 'Offline production build' '[WEB_COMPILER=turbopack|webpack]'; \
	t 'web:e2e' 'Mocked browser tests on 127.0.0.1' '[E2E_SERVER=dev|production] [E2E_PORT=3100]'; \
	h 'Live local preview'; \
	n 'Reads real account data; loopback only; no local admins.'; \
	t 'smoke:streamlit-live' 'Streamlit view' 'CONNECTION=c'; \
	t 'smoke:app-runtime-live' 'App Runtime view (next dev)' 'CONNECTION=c'; \
	h 'Snowflake account'; \
	n 'New mutations are dry runs until CONFIRM matches the target.'; \
	t 'inspect' 'Connection, account, config registry (read-only)' 'CONNECTION=c'; \
	t 'bootstrap' 'Database, schema, config table (IF NOT EXISTS)' 'CONNECTION=c EXPECTED_ACCOUNT=a DATABASE=COST_MGMT_DB SCHEMA=PUBLIC [CONFIRM=COST_MGMT_DB.PUBLIC]'; \
  t 'grants' 'Viewer, admin deploy, and opt-in ACCOUNT_USAGE grants to existing roles' 'CONNECTION=c EXPECTED_ACCOUNT=a VIEWER_ROLE=r QUERY_WAREHOUSE=w [ADMIN_ROLE=r2 [GRANT_ACCOUNT_USAGE=1]] [CONFIRM=r]'; \
	t 'acceleration:status' 'Registry and task state (read-only)' 'CONNECTION=c'; \
	t 'acceleration:provision' 'sql/config.sql on a fresh config table' 'CONNECTION=c EXPECTED_ACCOUNT=a [CONFIRM=COST_MGMT_DB.PUBLIC]'; \
	t 'acceleration:refresh' 'Queue a run of each registered task' 'CONNECTION=c EXPECTED_ACCOUNT=a [CONFIRM=COST_MGMT_DB.PUBLIC]'; \
	t 'acceleration:suspend' 'Suspend registered tasks' 'same inputs as acceleration:refresh'; \
	t 'acceleration:resume' 'Resume registered tasks' 'same inputs as acceleration:refresh'; \
	t 'acceleration:teardown' 'Drop registered objects only' 'same inputs as acceleration:refresh'; \
	h 'Streamlit runtime'; \
	  t 'deploy:streamlit' 'snow streamlit deploy --replace --prune, then attach PyPI repository' 'CONNECTION=c APP_NAME=N APP_TITLE="Title" [EXPECTED_ACCOUNT=a]'; \
	t 'streamlit:status' 'Describe the app (read-only)' 'CONNECTION=c APP_NAME=N'; \
	t 'streamlit:teardown' 'Drop one app; data is kept' 'CONNECTION=c APP_NAME=N EXPECTED_ACCOUNT=a [CONFIRM_TEARDOWN=N]'; \
	h 'App Runtime'; \
	n 'TARGET comes from web/app.yml; all targets share one data plane.'; \
	t 'deploy:app-runtime' 'snow app validate, then deploy' 'CONNECTION=c TARGET=t CONFIRM_EXAMPLE_TARGET=t [EXPECTED_ACCOUNT=a]'; \
	t 'app-runtime:open' 'Print the app URL' 'CONNECTION=c TARGET=t'; \
	t 'app-runtime:events' 'Recent service logs' 'CONNECTION=c TARGET=t'; \
	t 'app-runtime:teardown' 'Remove one target; data is kept' 'CONNECTION=c TARGET=t CONFIRM_TEARDOWN=t [EXPECTED_ACCOUNT=a]'; \
	printf '\nExample: %smake deploy:app-runtime CONNECTION=my_connection TARGET=test CONFIRM_EXAMPLE_TARGET=test%s\n\n' "$$Y" "$$R"

doctor:
	@status=0; \
	for tool in uv uvx npm node; do \
	  command -v $$tool >/dev/null 2>&1 || { echo "missing: $$tool"; status=1; }; \
	done; \
	if command -v node >/dev/null 2>&1; then \
	  version=$$(node -p process.versions.node); major=$${version%%.*}; minor=$${version#*.}; minor=$${minor%%.*}; \
	  if [ "$$major" = 22 ] && [ "$$minor" -ge 12 ]; then echo "node $$version: ok"; \
	  else echo "node $$version: Node.js 22.12 or later on the 22.x line is required"; status=1; fi; \
	fi; \
	if command -v uv >/dev/null 2>&1; then \
	  uv python find 3.11 >/dev/null 2>&1 && echo "python 3.11: ok" || { echo "python 3.11: not found (uv python install 3.11)"; status=1; }; \
	fi; \
	if command -v snow >/dev/null 2>&1; then echo "$$(snow --version) (3.27+ required for Snowflake commands)"; \
	else echo "snow: not installed (needed only for Snowflake commands)"; fi; \
	if [ -d web/node_modules/@playwright/test ]; then \
	  (cd web && npx playwright install --dry-run chromium >/dev/null 2>&1) && echo "playwright: installed" || echo "playwright: run make install:browser"; \
	else echo "web dependencies: run make install"; fi; \
	exit $$status

install:
	$(call need,uv,$(NEED_UV))
	$(call need,npm,$(NEED_NPM))
	uv sync --group dev
	npm --prefix web ci

install\:browser:
	$(call need,npx,$(NEED_NPM))
	npx --prefix web playwright install $(if $(WITH_DEPS),--with-deps )chromium

validate:
	@$(SUB) validate:python
	@$(SUB) validate:web
	@$(SUB) validate:docs

validate\:python:
	$(call need,uv,$(NEED_UV))
	uv run --group dev ruff check $(PYTHON_PATHS)
	uv run --group dev ruff format --check $(PYTHON_PATHS)
	uv run --python 3.11 python -m compileall -q $(PYTHON_PATHS)
	uv run --python 3.11 --group dev pytest -m "not snowflake"

validate\:web:
	@$(SUB) web:lint
	@$(SUB) web:typecheck
	@$(SUB) web:format
	@$(SUB) web:test
	@$(SUB) web:build
	@$(SUB) web:e2e

validate\:docs:
	$(call need,uvx,$(NEED_UV))
	uvx --from pymarkdownlnt pymarkdown --config .pymarkdown.yml scan $(DOC_PATHS)

web\:lint:
	$(call need,npm,$(NEED_NPM))
	npm --prefix web run lint

web\:typecheck:
	$(call need,npm,$(NEED_NPM))
	npm --prefix web run typecheck

web\:format:
	$(call need,npm,$(NEED_NPM))
	npm --prefix web run format:check

web\:test:
	$(call need,npm,$(NEED_NPM))
	npm --prefix web test

# Build-time route imports need SDK options; these synthetic values stay on this one
# command and never reach the E2E server. turbopack is the Next.js default compiler.
web\:build:
	$(call need,npm,$(NEED_NPM))
	@case "$$WEB_COMPILER" in turbopack|webpack) ;; \
	  *) echo "WEB_COMPILER must be turbopack or webpack." >&2; exit 2;; esac
	test ! -e /snowflake/session/token && \
	SNOWFLAKE_ACCOUNT=offline \
	SNOWFLAKE_USER=offline \
	SNOWFLAKE_PASSWORD=not-a-credential \
	SNOWFLAKE_CONNECTION_NAME=e2e-no-such-connection \
	SNOWFLAKE_DEFAULT_CONNECTION_NAME=e2e-no-such-connection \
	SNOWFLAKE_DISABLE_PLATFORM_DETECTION=true \
	npm --prefix web run build $$([ "$$WEB_COMPILER" = webpack ] && echo -- --webpack)

# The preflight rejects bad inputs, a hosted token, a busy loopback port, and a
# missing production build before Playwright starts its own server.
web\:e2e:
	$(call need,npm,$(NEED_NPM))
	node web/e2e/preflight.mjs
	npm --prefix web run e2e

smoke\:streamlit-live:
	$(call ops,smoke-streamlit)

smoke\:app-runtime-live:
	$(call ops,smoke-app-runtime)

inspect:
	$(call ops,inspect)

bootstrap:
	$(call ops,bootstrap)

grants:
	$(call ops,grants)

acceleration\:status:
	$(call ops,acceleration-status)

acceleration\:provision:
	$(call ops,acceleration-provision)

acceleration\:refresh:
	$(call ops,acceleration-refresh)

acceleration\:suspend:
	$(call ops,acceleration-suspend)

acceleration\:resume:
	$(call ops,acceleration-resume)

acceleration\:teardown:
	$(call ops,acceleration-teardown)

deploy\:streamlit:
	$(call ops,deploy-streamlit)

streamlit\:status:
	$(call ops,streamlit-status)

streamlit\:teardown:
	$(call ops,streamlit-teardown)

deploy\:app-runtime:
	$(call ops,deploy-app-runtime)

app-runtime\:open:
	$(call ops,app-runtime-open)

app-runtime\:events:
	$(call ops,app-runtime-events)

app-runtime\:teardown:
	$(call ops,app-runtime-teardown)
