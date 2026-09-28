# CLAUDE.md

Guidance for Claude Code when working in this repo.

## Imports (team standards)

- @.claude/standards/decisions.md
- @.claude/standards/git-workflow.md
- @.claude/standards/testing.md
- @.claude/standards/error-handling.md
- @.claude/standards/logging.md
- @.claude/standards/security.md

Python- and test-specific conventions live in `.claude/rules/` (`python-lang.md`,
`pytest-rules.md`) and load automatically when Claude touches matching files.

## What this project does

Produce a **defensible, dated record** of exactly what a OneDrive/SharePoint
discovery share contained, download every file, and prove the download matches the
record. Packaged as `src/aqueduct/`, exposing console entry points.

| Command | Module | Role |
|---|---|---|
| `login` | `login.py` | Headed sign-in; saves the web session + surfaces the URL (session-scoped) |
| `webenum` | `webenum.py` | Enumerate a "specific people" share via the **web session** (headless Playwright) → `manifest.json`/`.csv` |
| `filecopy` | `filecopy.py` | High-throughput async **download** of every file (resumable) |
| `validate` | `validate.py` | Reconcile the download against the manifest (size + optional SHA-256 hash) |

`shareurl.py` and `paths.py` are shared helpers. The design decisions are recorded as ADRs in
[`docs/adr/`](docs/adr/); the user-facing runbook is [`docs/WORKFLOW.md`](docs/WORKFLOW.md).

## Key constraint — read before touching share access

"Specific people"/guest shares authorize the **interactive web session**, not a
delegated Graph token from a third-party app — so Graph (and `rclone`, also a
Graph client) return **403 Forbidden** even when the recipient can open the share
in a browser. That is *authorization*, not authentication; changing scopes or
clients does not fix it. Full reasoning:
[ADR-0001](docs/adr/0001-ENUMERATE-VIA-WEB-SESSION.md).

## Python tooling: use `uv`

Use **[uv](https://docs.astral.sh/uv/)** for all Python work — not bare
`pip`/`venv`.

- `uv sync` — install/lock dependencies (also installs the console entry points)
- `uv add <pkg>` / `uv remove <pkg>` — manage dependencies (never hand-edit deps)
- `uv run <cmd>` — run in the project env, e.g. `uv run webenum enumerate`
- `uv run pytest` / `uv run ruff check .` / `uv run mypy src` — the quality gates
- One-time Playwright browser: `uv run python -m playwright install chromium`

## Data & secrets — never commit

Auth tokens live in the per-user config dir `~/.aqueduct` (outside the repo), not in
the working tree — see [ADR-0003](docs/adr/0003-AUTH-IN-USER-CONFIG-DIR.md).
Working data lives in the current directory (run the tools from a per-case `./data/`
folder). All of it is git-ignored; never commit, print in full, or send anywhere:

- `~/.aqueduct/auth_state.json` — saved web session (**as sensitive as a password**;
  expires — re-run `login` when it does)
- `data/`, `manifest*`, `raw/`, `download/`, `*.log`, `*_results.csv` — working data

**No real secrets, PII, or case/discovery content in any tracked file** — source,
docs, tests, or this file included. Never paste a real share URL, tenant/account,
person's name, case or exhibit identifier, file path from an actual share, or
credential into committed content; use only fictional placeholders (`contoso`,
`jdoe@example.org`, `IND-00000-00`, `example.com`). Real case material belongs solely
in the git-ignored `data/` folder and `~/.aqueduct`. Before committing, sweep the
tree for leaked identifiers (e.g. `rg -i` for real names/tenants/case numbers over
everything git would track).

## Documentation conventions

- **Markdown docs in the project root and under `docs/` use CAPITAL CASE filenames**
  (e.g. `README.md`, `WORKFLOW.md`, `0001-ENUMERATE-VIA-WEB-SESSION.md`) — this
  includes `docs/adr/`. Markdown elsewhere (e.g. under `.claude/`) keeps its existing
  lowercase convention. This overrides the lowercase-slug convention from the generic
  `adr-authoring` skill for root/`docs` files only.
- Record significant, hard-to-reverse decisions as ADRs under
  [`docs/adr/`](docs/adr/) — see `.claude/standards/decisions.md` for *when*, and
  [`docs/adr/TEMPLATE.md`](docs/adr/TEMPLATE.md) for the format. Add each new ADR to
  the table in `docs/adr/README.md`.
