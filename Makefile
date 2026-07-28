.DEFAULT_GOAL := help
UV ?= uv
RUN := $(UV) run

.PHONY: help
help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------ setup ---
.PHONY: install
install:  ## Create the venv and install everything (dev + serve + decontam)
	$(UV) sync --all-extras

.PHONY: lock
lock:  ## Refresh uv.lock
	$(UV) lock

# ------------------------------------------------------------------ quality -
.PHONY: fmt
fmt:  ## Auto-format and auto-fix
	$(RUN) ruff format src tests
	$(RUN) ruff check --fix src tests

.PHONY: lint
lint:  ## Lint + format check + type check
	$(RUN) ruff format --check src tests
	$(RUN) ruff check src tests
	$(RUN) mypy

.PHONY: test
test:  ## Run the test suite
	$(RUN) pytest

.PHONY: cov
cov:  ## Run tests with coverage
	$(RUN) pytest --cov --cov-report=term-missing

.PHONY: check
check: lint test gates  ## Everything CI runs

.PHONY: demo
demo:  ## Verify the zero-key demo works and print what to look at
	$(RUN) toolsmith demo --run

# ------------------------------------------------------------------- gates --
# The suite is derived from a seed rather than committed, so a fresh clone does
# not have it and two of the gates correctly refuse to pass without it. CI
# generates it before running them; so does this, or `make check` would fail on
# a clean checkout for a reason that is not a failure. Regenerating is a no-op
# when the file is already there.
.PHONY: gates
gates: data  ## All five CI policy gates, in one command, exactly as CI runs them
	$(RUN) toolsmith ci all

.PHONY: data
data: data/tasks/tasks.jsonl  ## Generate the task suite if this checkout lacks it

data/tasks/tasks.jsonl:
	$(RUN) toolsmith tasks build

.PHONY: firewall
firewall:  ## License firewall: no forbidden model may appear in training data
	$(RUN) toolsmith ci firewall

.PHONY: decontam
decontam:  ## Train/test leakage check
	$(RUN) toolsmith ci decontam

.PHONY: budget
budget:  ## Assert cumulative spend is under the cap
	$(RUN) toolsmith ci budget

# ------------------------------------------------------------------- build --
.PHONY: worlds
worlds:  ## Build the three sandboxed worlds and print digests
	$(RUN) toolsmith world build --all

.PHONY: tasks
tasks:  ## Generate the task suite with oracle programs and splits
	$(RUN) toolsmith tasks build

.PHONY: matrix
matrix:  ## Run the full evaluation matrix (simulated provider, $0)
	$(RUN) toolsmith matrix run --provider simulated --n 180 --trials 3

.PHONY: optimize
optimize:  ## Run the four improvement tracks
	$(RUN) toolsmith optimize run all --n 120

.PHONY: report
report:  ## Regenerate every published artifact from results.jsonl
	$(RUN) toolsmith report build

.PHONY: site
site: report  ## Render the Quarto site into docs/_site
	quarto render docs

.PHONY: serve
serve:  ## Run the control plane on http://127.0.0.1:7860
	$(RUN) uvicorn app.main:app --host 127.0.0.1 --port 7860 --reload

# ------------------------------------------------------------------- all ----
.PHONY: all
all: worlds tasks matrix optimize report site demo  ## Full reproduction, end to end

.PHONY: clean
clean:  ## Remove generated artifacts (keeps committed fixtures)
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	rm -rf build/worlds build/tasks docs/_site docs/.quarto
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
