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

This project uses `uv` for package management, linting, and formatting. After
cloning, run setup once — it installs dependencies and the local Git hooks:

```bash
make setup
```

(Equivalent to `uv sync && uv run pre-commit install && uv run pre-commit install --hook-type pre-push`.)

## Git hooks

Local quality gates run through the [`pre-commit`](https://pre-commit.com/)
framework (config in `.pre-commit-config.yaml`): `git commit` runs `ruff` and
`mypy`{{ " and `bandit`" if cookiecutter.security == "yes" else "" }}, and
`git push` runs `pytest`. `make setup` (above) installs them. Git can't
auto-install hooks on clone, so this one-time step is how they get wired up — but
CI (`.github/workflows/ci.yml`) runs the same checks regardless, so it stays the
real gate even when the local hooks aren't installed.

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

{% if cookiecutter.coding_factory == "yes" -%}
## Coding-factory launchers

This project opted into the coding-factory workflow, so it carries two thin
**Scrum Master launcher** GitHub Actions under `.github/workflows/`. Each does no
build work — it launches the resumable **Scrum Master** orchestrator (a Claude
Managed Agent) and exits. The Scrum Master is the sole thing that spawns and
judges every subagent (Sequencing / Planner / Build).

- **`scrum-master.yml`** — fires on every merge to `main` (single-flight;
  `workflow_dispatch` also lets you re-reconcile by hand). The Scrum Master
  reconciles from repo state: sequence an approved-but-unsequenced spec,
  just-in-time launch the current ready group's Planners, advance groups, or
  no-op.
- **`approve.yml`** — fires when an authorized reviewer comments exactly
  `/approve` on a plan's draft PR. It launches the Scrum Master (which records
  the plan's approval and spawns Build); it never merges.

**Required repository secrets** — set both under *Settings → Secrets and
variables → Actions* before the launchers can run:

| Secret | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Launches the Scrum Master managed agent via the Anthropic API. |
| `GITHUB_MCP_TOKEN` | GitHub token with **repo + issues write** scope, consumed by the agents' GitHub remote MCP server to read/write issues and PRs. |

The launchers ship with the managed-agents API call stubbed (a loud `::error::`
until wired) — finalize the launch call in each workflow once the API surface is
pinned. See the coding-factory docs (`_workflow/` in the template repo) for the
Scrum Master's behaviour and kickoff messages.

{% endif -%}
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
