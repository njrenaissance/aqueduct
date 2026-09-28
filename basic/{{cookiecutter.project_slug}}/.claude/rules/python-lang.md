---
paths:
  - "**/*.py"
---

# Python conventions

- Line length: 120 (ruff) — wider than the PEP 8 default of 79/88 so type hints and f-strings don't force awkward wrapping, while still keeping diffs and side-by-side reviews readable
- Ruff lint rules — run `uv run ruff check .` and `uv run ruff format .`:
  - `E` (pycodestyle): PEP 8 style issues — spacing, indentation, line structure
  - `F` (Pyflakes): logical errors — unused imports/variables, undefined names, unreachable code
  - `I` (isort): consistent, deterministic import ordering so diffs don't churn on import order
  - `UP` (pyupgrade): flags outdated syntax and rewrites it to match the project's target Python version (e.g. old-style `%` formatting, `typing.List` instead of `list`)
  - `B` (flake8-bugbear): catches common bug-prone patterns pyflakes misses (mutable default arguments, misused `except`, etc.)
  - `N` (pep8-naming): enforces conventional, meaningful casing for functions/classes/variables — Clean Code's "use intention-revealing names" only works if names are consistent enough to scan
  - `ARG` (flake8-unused-arguments): flags parameters a function never uses — Clean Code's "functions should do one thing"; an unused argument usually means the function's responsibility has drifted from its signature
  - `SIM` (flake8-simplify): rewrites unnecessarily convoluted code to a simpler equivalent (nested `if`s that could be one condition, redundant `else` after `return`, etc.) — Clean Code favors the simplest expression of an idea
  - `ERA` (eradicate): flags commented-out code — Clean Code says delete dead code instead of commenting it out; version control already remembers it
  - `PLR` (Pylint refactor): flags structural smells — too many function arguments, too many branches/statements in one function, magic numbers used in comparisons — Clean Code's "small functions," "few arguments," "no magic numbers"
  - `C901` (mccabe): flags functions whose cyclomatic complexity exceeds 10 (`[tool.ruff.lint.mccabe]`) — Clean Code's "functions should do one thing," measured instead of eyeballed
- Type hints required on all new code in `src/`; mypy runs with:
  - `disallow_untyped_defs` — every function must declare parameter and return types, so type errors surface at review/CI time instead of at runtime
  - `warn_return_any` — flags functions that silently return `Any`, which would otherwise let untyped values leak through and defeat the point of typing
  - `no_implicit_optional` — requires `T | None` to be written explicitly instead of inferring it from a `None` default, preventing accidental null bugs
- Run `uv run mypy src` before considering a change done
- Before writing a new function or class, search for an existing equivalent — use the LSP `workspaceSymbol` operation if available, otherwise `Grep` for likely names/signatures — and reuse or extend it instead of duplicating logic
