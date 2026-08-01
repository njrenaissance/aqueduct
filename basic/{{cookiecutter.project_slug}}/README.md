# {{ cookiecutter.project_name }}

Generated from the `basic` cookiecutter template.
A minimal Python project, managed with [uv](https://docs.astral.sh/uv/).

## Structure

```bash
├── src/
│   └── main.py       # greet()
├── tests/
│   └── test_main.py  # test for greet()
└── pyproject.toml
```

## Setup

This project uses `uv` for package management, linting, and formatting.

```bash
uv sync
```

## Git hooks

Local quality gates run through the [`pre-commit`](https://pre-commit.com/)
framework (config in `.pre-commit-config.yaml`): `git commit` runs `ruff` and
`mypy`{{ " and `bandit`" if cookiecutter.security == "yes" else "" }}, and
`git push` runs `pytest`. Generation installs nothing — install the hooks once,
from inside the project:

```bash
uv run pre-commit install
uv run pre-commit install --hook-type pre-push
```

(They are not auto-installed at generation because `pre-commit install` bakes
machine-specific paths into `.git/hooks/`, which would break `cruft update` — see
**Staying in sync with the template** below.)

## Staying in sync with the template

This project was generated from the `basic` cookiecutter template and linked to it
with [`cruft`](https://cruft.github.io/cruft/). The link lives in `.cruft.json`
(template URL, the exact template commit, and the answers given at generation) — it
is what lets template improvements be pulled in later instead of the scaffold going
stale. Check whether the template has moved ahead:

```bash
uvx cruft check    # exit 0 = up to date; non-zero = behind
```

The **Template Sync** GitHub Actions workflow runs this check on demand (Actions tab
→ *Template Sync* → *Run workflow*); it is intentionally not part of the PR gate and
never blocks a merge. When the project is behind, run the `update-from-template`
skill (or `uvx cruft update` by hand) to apply the delta, resolve any `*.rej`
conflicts, and re-run the checks. See `.claude/standards/` and
`.claude/skills/update-from-template/` for the agent-run procedure.

## Wiki

This project keeps an `openwiki/` folder of generated codebase documentation
(produced by [OpenWiki](https://www.npmjs.com/package/openwiki)). It is
generated output — **never hand-edit it**; regenerate it and commit the result
alongside the code change that prompted it, so the wiki stays in step with
`main`.

OpenWiki is a per-machine global CLI, **not** a project dependency (it is never
added to `pyproject.toml`). Install and authenticate it once, then regenerate
before committing:

```bash
npm install -g openwiki    # one-time, per machine
openwiki auth <provider>   # one-time: sets up the LLM provider + API key
openwiki code --init       # first run in a fresh repo
openwiki code --update     # regenerate before committing a change
```

Regenerating calls a paid LLM provider. See `.claude/standards/wiki.md` for the
regenerate-before-commit rule agents follow.

## Run

```bash
uv run python src/main.py
```

## Test

```bash
uv run pytest
```

## Lint

```bash
uv run ruff check .
```

## Format

```bash
uv run ruff format .
```
