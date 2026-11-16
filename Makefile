.PHONY: help install install-hooks test test-scorecards test-planner-fast test-durations clean lint format docs check-arch typecheck run-devtool site serve deploy _test-pytest-offline _require-legal _require-poetry _guard-pytest-command

# Prevent a pyenv-activated VIRTUAL_ENV from shadowing the project .venv.
unexport VIRTUAL_ENV

# Supported developer tools (explicit registry).
DEVTOOLS_CORE := check_architecture
DEVTOOLS_PLANNER := planner_scenario_report planner_sweep_site planner_menu_diff planner_menu_canon planner_fingerprints planner_score_quality planner_load_pricing
DEVTOOLS_FRONTEND := e2e_lifecycle static_adapters_smoke
DEVTOOLS_BENCH := bench_plans bench_figure bench_plan_view
DEVTOOLS_ALL := $(DEVTOOLS_CORE) $(DEVTOOLS_PLANNER) $(DEVTOOLS_FRONTEND) $(DEVTOOLS_BENCH)

# Test runner defaults (override at call-site as needed)
# Example:
#   make test PYTEST_PATH=tests/planner PYTEST_ARGS='-k menu -vv'
PYTEST ?= poetry run pytest
PYTEST_PATH ?= tests
PYTEST_ARGS ?=
PYTEST_MARKERS ?= not slow
PYTEST_IGNORE ?=
PYTEST_PARALLEL ?= -n auto
PYTEST_COVERAGE ?= --cov=endury --cov-report=term-missing
# Force offline test targets to ignore any real API keys from shell/.env.
OFFLINE_ENV = OPENROUTER_API_KEY= INTERVALS_API_KEY=

help:
	@echo "Available commands:"
	@echo "  make install              - Install dependencies using poetry"
	@echo "  make install-hooks        - Enable repo-managed git hooks (.githooks)"
	@echo "  make test                 - Full offline pytest suite"
	@echo "  make test-scorecards      - Scorecard baseline gates incl. the slow planner matrix"
	@echo "  make test-planner-fast    - Fast offline planner + planning loop (no coverage)"
	@echo "  make test-durations       - Offline pytest timing profile (serial, no coverage)"
	@echo "  make test PYTEST_PATH=tests/planner PYTEST_ARGS='-k menu -vv' - Optional pytest narrowing"
	@echo "  make clean                - Clean up cache and build files"
	@echo "  make lint                 - Run linter (ruff)"
	@echo "  make format               - Format code (ruff)"
	@echo "  make docs                 - Regenerate the generated docs (rule page, benchmark figure)"
	@echo "  make check-arch           - Run architecture fitness checks"
	@echo ""
	@echo "Developer Tools (core):"
	@echo "  make run-devtool TOOL=check_architecture [ARGS='...']"
	@echo ""
	@echo "Developer Tools (planner):"
	@echo "  make run-devtool TOOL=planner_scenario_report ARGS='--list'"
	@echo "  make run-devtool TOOL=planner_scenario_report ARGS='--scenario ironman_moderate'"
	@echo "  make run-devtool TOOL=planner_scenario_report ARGS='--tag regression'"
	@echo "  make run-devtool TOOL=planner_scenario_report ARGS='--all'"
	@echo "  make run-devtool TOOL=planner_menu_diff [ARGS='--rev HEAD~1 --goal ironman']"
	@echo "  make run-devtool TOOL=planner_score_quality ARGS='--matrix'"
	@echo "  make run-devtool TOOL=planner_load_pricing ARGS='--goal ironman --ambition competitive'"
	@echo "  make run-devtool TOOL=planner_sweep_site [ARGS='--goals ironman --tiers competitive']   # every plan, week by week, as one HTML page in the temp dir"
	@echo ""
	@echo "Developer Tools (browser e2e):"
	@echo "  make run-devtool TOOL=e2e_lifecycle [ARGS='--keep']"
	@echo "  make run-devtool TOOL=static_adapters_smoke   # Node, no browser: wire->view adapters"
	@echo "  make run-devtool TOOL=bench_plans ARGS='--contestants endury --scenarios headline'   # the plan benchmark; models need OPENROUTER_API_KEY"
	@echo "  make run-devtool TOOL=bench_plan_view ARGS='--scenarios ironman_32wk'   # every plan as one HTML page, written to the temp dir"
	@echo ""
	@echo "Static site (browser build):"
	@echo "  make site                 - Build the static site into site/"
	@echo "  make serve                - Serve site/ at http://localhost:8080"
	@echo "  make deploy               - Build and deploy site/ to Cloudflare (wrangler.jsonc)"

install: _require-poetry
	poetry install

install-hooks:
	git config core.hooksPath .githooks
	@for f in .githooks/*; do [ -f "$$f" ] && chmod +x "$$f"; done; true
	@echo "Installed git hooks from .githooks/"

_require-poetry:
	@command -v poetry >/dev/null 2>&1 || { \
		echo "Error: Poetry is required but not found in PATH."; \
		echo "Install Poetry: https://python-poetry.org/docs/#installation"; \
		exit 1; \
	}

_guard-pytest-command:
	@case "$(PYTEST)" in \
		poetry\ run\ pytest*) ;; \
		*) \
			echo "Error: PYTEST must start with 'poetry run pytest'."; \
			echo "Current PYTEST='$(PYTEST)'"; \
			exit 1 ;; \
	esac

_test-pytest-offline: _require-poetry _guard-pytest-command
	$(OFFLINE_ENV) $(PYTEST) $(PYTEST_PATH) $(PYTEST_IGNORE) $(PYTEST_PARALLEL) -m "$(PYTEST_MARKERS)" $(PYTEST_COVERAGE) $(PYTEST_ARGS)

test: _test-pytest-offline

# Scorecard baseline gates, including the `slow` planner matrix and the
# `slow` R-SESS-030 card-honesty grid the default `make test` excludes.
# No coverage: this is a subset run, and the instruments live outside
# --cov=endury anyway. Run by .githooks/pre-push.
test-scorecards: _require-poetry _guard-pytest-command
	$(OFFLINE_ENV) $(PYTEST) tests/devtools/test_scorecard_baselines.py tests/planner/test_card_honesty.py -n 0 $(PYTEST_ARGS)

test-planner-fast: _require-poetry _guard-pytest-command
	$(OFFLINE_ENV) $(PYTEST) tests/planner tests/planning $(PYTEST_IGNORE) $(PYTEST_PARALLEL) -m "$(PYTEST_MARKERS)" $(PYTEST_ARGS)

test-durations:
	$(OFFLINE_ENV) $(PYTEST) $(PYTEST_PATH) $(PYTEST_IGNORE) -n 0 -m "$(PYTEST_MARKERS)" --durations=30 --durations-min=0.5 $(PYTEST_ARGS)

clean:
	rm -rf .pytest_cache
	rm -rf .ruff_cache
	rm -rf .mypy_cache
	rm -f .coverage .coverage.*
	rm -rf reports artifacts screenshots
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

lint: _require-poetry typecheck docs
	poetry run ruff check . --fix
	poetry run ruff format --check .
	poetry run python -m devtools.core.check_architecture
	poetry run python -m devtools.core.check_docs

# Regenerate every generated doc from its committed source (offline, seconds).
docs: _require-poetry
	poetry run python -m devtools.quality.rules_page
	poetry run python -m devtools.bench.figure

format: _require-poetry
	poetry run ruff format .

check-arch: _require-poetry
	poetry run python -m devtools.core.check_architecture
	poetry run python -m devtools.core.check_docs

typecheck: _require-poetry
	poetry run mypy endury/ devtools/ bench/ --ignore-missing-imports

# Run a registered developer tool by name. static_adapters_smoke is the one
# Node tool: its branch execs node, so the python runner below never runs.
run-devtool: _require-poetry
	@if [ -z "$(TOOL)" ]; then \
		echo "Error: TOOL name required. Usage: make run-devtool TOOL=<name> [ARGS='...']"; \
		echo ""; \
		echo "Supported tools:"; \
		for t in $(DEVTOOLS_ALL); do echo "  - $$t"; done; \
		exit 1; \
	fi
	@case "$(TOOL)" in \
		check_architecture) module=devtools.core.check_architecture ;; \
		planner_scenario_report) module=devtools.planner.report_sweep.report_cli ;; \
		planner_sweep_site) module=devtools.planner.report_sweep.sweep_site ;; \
		planner_menu_diff) module=devtools.planner.menu_diff ;; \
		planner_menu_canon) module=devtools.planner.menu_canon ;; \
		planner_fingerprints) module=devtools.planner.plan_fingerprints ;; \
		planner_score_quality) module=devtools.quality.planner.score_quality ;; \
		planner_load_pricing) module=devtools.planner.load_pricing ;; \
		e2e_lifecycle) module=devtools.frontend.e2e_lifecycle ;; \
		bench_plans) module=devtools.bench.run_bench ;; \
		bench_figure) module=devtools.bench.figure ;; \
		bench_plan_view) module=devtools.bench.plan_view ;; \
		static_adapters_smoke) exec node devtools/frontend/static_adapters_smoke.mjs $(ARGS) ;; \
		*) \
			echo "Error: Unsupported TOOL '$(TOOL)'."; \
			echo "Supported tools:"; \
			for t in $(DEVTOOLS_ALL); do echo "  - $$t"; done; \
			exit 1 ;; \
	esac; \
	poetry run python -m $$module $(ARGS)

# ── Static site (browser build) ──────────────────────────────────────────

# Build the static site into site/ (gitignored): web/ versioned
# by content hash, the endury/ Python source zipped for the Pyodide worker.
site: _require-poetry
	poetry run python -m devtools.site.build_site

# Serve the built site/ for local preview. Port 8080.
serve: _require-poetry
	@PID=$$(lsof -tiTCP:8080 -sTCP:LISTEN 2>/dev/null); \
	if [ -n "$$PID" ]; then \
		echo "Stopping existing server (PID $$PID) on port 8080..."; \
		kill $$PID 2>/dev/null; \
		sleep 1; \
	fi; \
	cd site && poetry run python -c "import http.server as h; h.ThreadingHTTPServer.request_queue_size = 128; h.test(h.SimpleHTTPRequestHandler, h.ThreadingHTTPServer, port=8080)"

# Build and deploy site/ as Cloudflare Workers static assets (wrangler.jsonc:
# the project name and custom domain). Needs `npx wrangler login` once.
deploy: _require-clean site _require-legal
	npx wrangler deploy

# The site serves its commit's source: no deploy of anything uncommitted.
_require-clean:
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "error: commit first; the site serves the source of HEAD"; \
		exit 1; \
	fi

# The site names its host: no deploy with the legal pages still blank.
_require-legal:
	@if grep -q 'legal-fill' site/impressum.html site/privacy.html; then \
		echo "error: blank legal pages; put your details in ~/.config/endury/legal.json (see README)"; \
		exit 1; \
	fi
