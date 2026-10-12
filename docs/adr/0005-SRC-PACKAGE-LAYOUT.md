# ADR-0005 — Ship as a `src/` package with console entry points

**Status:** Accepted — the src-layout/entry-point decision stands, but the package
listing below still shows `odenum.py`/`quickxor.py`, removed per
[ADR-0010](0010-WEB-SESSION-ONLY-REMOVE-GRAPH-PATH.md).

## Context

The tools started as loose scripts in the repo root (`webenum.py`, `filecopy.py`, …)
run as `python webenum.py …`. As the toolset grew (five entry points plus shared
`quickxor`, `shareurl`, `paths` modules) this had problems: shared code was imported
by bare module name (fragile, depends on CWD), there was no packaging for future
distribution, and no clean home for tests.

## Decision

Adopt the standard **`src/` layout as an installable package**:

```text
src/aqueduct/   __init__.py, login.py, webenum.py, filecopy.py,
                     validate.py, odenum.py, quickxor.py, shareurl.py, paths.py
tests/               pytest suite
pyproject.toml       hatchling build; [project.scripts] entry points
```

- Intra-package imports are absolute (`from aqueduct import shareurl`).
- Each tool exposes a `main()` wired to a **console entry point** in
  `[project.scripts]`, so `uv sync` installs `login` / `webenum` / `filecopy` /
  `validate` / `odenum` as commands runnable from the repo root.
- `hatchling` is the build backend, so the project can be built into a wheel and
  distributed later without further restructuring.

## Consequences

- Run tools as `uv run webenum enumerate` etc.; imports resolve regardless of CWD.
- Data-file locations had to stop being "next to the code" — see
  [ADR-0003](0003-AUTH-IN-USER-CONFIG-DIR.md).
- Tooling (ruff, mypy, pytest, coverage) is configured in `pyproject.toml`, adapted
  from the team's project template; a couple of complexity/argument thresholds are
  raised slightly for the I/O-heavy download loops.
- The package is import-installed by `uv sync`, so tests import `aqueduct`
  directly and the entry points match what a future `pip install` would provide.
