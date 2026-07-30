# Changelog

All notable changes to the templates in this repository are documented here, per template. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## basic

### [1.5.0] - 2026-07-29

#### Added

- `.claude/standards/decisions.md`, a new unconditional standard that defines
  *when* an architecture decision earns an ADR (significant or hard-to-reverse
  choices — storage/framework, module boundaries, subsystem-wide dependencies —
  judged on reversibility and blast radius, not size) and points at the `adr`
  skill for the mechanics. The standard carries only the trigger policy; the
  skill (assumed available in the environment) owns the format, numbering, and
  file location, with a `docs/adr/NNNN-*.md` Markdown fallback if the skill is
  absent. Imported into `CLAUDE.md` for every project.

### [1.4.0] - 2026-07-12

#### Added

- `.claude/standards/wiki.md`, a new unconditional standard describing the
  `openwiki/` codebase wiki: it is generated output that is never hand-edited,
  and must be regenerated (`openwiki code --update`) and committed alongside
  the code change that prompted it. Imported into `CLAUDE.md` for every
  project.
- A `## Wiki` section in `README.md` documenting the per-machine `openwiki`
  install (`npm install -g openwiki`), one-time auth, and the
  `openwiki code --init`/`--update` regenerate commands — `openwiki` is a
  global CLI, never a project dependency.
- `openwiki` entries (`Bash(openwiki)`, `Bash(openwiki *)`,
  `Bash(npm install -g openwiki)`) in the `.claude/settings.json` whitelist so
  agents may regenerate the wiki without an approval prompt.

#### Changed

- `CLAUDE.md`'s `## Imports` section now renders each `@`-import as a Markdown
  list item (`- @...`) instead of a bare line, so it displays as a proper list
  while still resolving as a Claude Code import.

### [1.3.0] - 2026-07-12

#### Added

- A `## Profile` section in `CLAUDE.md` listing whether each of app-config,
  structured-logging, telemetry, and security is enabled for the project, so
  an agent can read the active cross-cutting concerns directly instead of
  inspecting `cookiecutter.json` or `pyproject.toml`.

#### Changed

- `CLAUDE.md`'s `## Imports` section now conditionally imports
  `configuration.md` (`app_config`), `telemetry.md` (`telemetry`), and
  `security.md` (`security`) based on the matching toggle, matching the
  conditional dependencies already in `pyproject.toml`. `git-workflow.md`,
  `testing.md`, `error-handling.md`, and `logging.md` import unconditionally
  — the last of these because `logging.md` itself already gates its
  `structlog`-vs-stdlib guidance on `structured_logging`. This closes out
  the wiring flagged as a follow-up in the 1.2.0 entry below.

### [1.2.0] - 2026-07-12

#### Added

- Full content for the six `.claude/standards/` documents stubbed in 1.1.0
  (`configuration.md`, `logging.md`, `telemetry.md`, `error-handling.md`,
  `security.md`, `testing.md`), each prescribing patterns and approved
  Python libraries for its concern (`pydantic-settings`, `structlog`,
  OpenTelemetry, `bandit`, `pytest-mock`). `configuration.md` covers nested
  per-concern `BaseSettings` sub-sections (with a `__`-delimited env var
  naming convention), JSON config files as an additional settings source,
  and centralizing non-secret defaults in a single dict for easy test
  overrides. `logging.md` treats `structlog` as required only when
  `structured_logging` is enabled, with `logging` module as the fallback.
- `pytest-mock` added to the `dev` dependency group (unconditional — the
  testing standard applies regardless of profile).
- `tests/conftest.py` with a `pytest_configure` hook that turns on live
  `INFO`-level log output automatically when running `pytest -v`.

#### Changed

- `.claude/standards/` documents are no longer wired to a `CLAUDE.md`
  section that doesn't exist yet (the earlier stub-era draft referenced
  scaffold-time profile checks in `CLAUDE.md`; that wiring is still a
  follow-up, not yet in this template).

### [1.1.0] - 2026-07-12

#### Added

- `app_config`, `structured_logging`, `telemetry`, and `security` (`no`/`yes`) cookiecutter prompts; `pyproject.toml` now conditionally includes `pydantic-settings`/`python-dotenv` (app_config), `structlog` (structured_logging), `opentelemetry-sdk`/`opentelemetry-api` (telemetry), and `bandit` (security) based on the selections — each prompt is independent, with no forced bundling.
- `.claude/standards/` directory alongside `.claude/rules/` for cross-cutting, non-file-type-specific agent guidance — stubbed with `configuration.md`, `logging.md`, `telemetry.md`, `error-handling.md`, `security.md`, `testing.md` (full content is a follow-up issue).

#### Changed

- Moved `docs/GITWORKFLOW.md` to `.claude/standards/git-workflow.md` and updated `CLAUDE.md`'s `@` import path accordingly.

#### Removed

- The `docs/` folder (empty after the `GITWORKFLOW.md` move) and the now-dead `docs/**` entry in `ci.yml`'s `paths-ignore`.

### [1.0.0] - 2026-07-07

#### Added

- Initial `basic` template: minimal Python project (`src/` + `tests/`) using `uv`.
- CI pipeline (`ci.yml`) composed of reusable `format-lint.yml`, `type-check.yml`, `unit-tests.yml` workflows, with pinned action SHAs, `timeout-minutes`, and PR concurrency cancellation.
- `.github/dependabot.yml` to keep pinned GitHub Actions SHAs updated.
- Modular git workflow docs (`docs/GITWORKFLOW.md`) imported into `CLAUDE.md` via Claude Code's `@` import syntax.
- `.cookiecutter-template-version` to track which template version a generated project was scaffolded from.
