# Common developer tasks. Run `make setup` once after cloning this repo.
#
# Git cannot auto-install hooks on clone (it never runs repo-controlled code on
# clone), so `make setup` is the one bootstrap step. CI (.github/workflows/ci.yml)
# is the real gate regardless of whether local hooks are installed.
.PHONY: setup sync test lint format typecheck check template-check

setup:  ## Install dependencies and the local Git hooks (run once after cloning)
	uv sync
	uv run pre-commit install
	uv run pre-commit install --hook-type pre-push

sync:  ## Install / refresh dependencies
	uv sync

test:  ## Run the test suite
	uv run pytest

lint:  ## Lint with ruff
	uv run ruff check .

format:  ## Format with ruff
	uv run ruff format .

typecheck:  ## Type-check with mypy
	uv run mypy src

check: lint typecheck test  ## Run all local quality gates

template-check:  ## Report whether this project is behind its template
	uvx cruft check
