# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A collection of [Cookiecutter](https://cookiecutter.readthedocs.io/) scaffolding templates. Currently one template exists:

- `basic` — minimal Python project (`src/` + `tests/`) using `uv`

**Templates vs. non-template directories.** A top-level directory is a template **iff it has its own `cookiecutter.json`** (e.g. `basic/`). Top-level directories whose name starts with a **leading underscore are not templates** — they are repo documentation/config that is never rendered into a scaffolded project and never appears in the scaffold skill's template list. Currently that's `_workflow/` — the [coding-factory](_workflow/coding-workflow.md) coding methodology (spec, ADRs, managed-agent specs, runbooks, and the workflow-level skills that operate *across* scaffolding a project) that governs how a project built from these templates is planned, built, reviewed, and shipped. It lives here, beside the templates it operates on, to avoid drift — but it has no `cookiecutter.json`, so it can't be selected or rendered. (Distinct from the generic cruft-sync skills at `.claude/skills/`, which apply to any cookiecutter/cruft project regardless of this workflow.)

**Design principle**: each template bakes in as much as possible as deterministic, machine-enforced config — pinned CI, dependabot, lint/type-check/test commands, the `.cruft.json` template link — so it's enforced consistently without relying on an agent to remember it. Anything that isn't reducible to a fixed rule (coding conventions, when to branch, how docs relate to each other) goes in that generated project's own `CLAUDE.md` and its imports/rules instead, where an agent can apply judgment. When adding to a template, prefer config over a `CLAUDE.md` instruction wherever a machine can actually enforce the rule.

## Commands

- Generate a project **with cruft** (recommended — writes a `.cruft.json` so the project can be synced to later template versions): `cruft create https://github.com/njrenaissance/project-templates --directory basic` (via `uvx cruft create …` if cruft isn't installed). Plain `cookiecutter https://github.com/njrenaissance/project-templates --directory basic` (or `cookiecutter ./basic` against a local checkout) still works but produces no `.cruft.json` and cannot be updated — retrofit it later with the `link-to-template` skill.
- Test-render a template with all defaults (no prompts), to validate changes before committing: `cookiecutter --no-input -o <output-dir> ./basic`
- After test-rendering, sanity-check the generated project actually works: `cd <output-dir>/<project_slug> && uv sync && uv run pytest && uv run ruff check . && uv run mypy src`

## Architecture

Each template directory follows the standard Cookiecutter layout:

- `cookiecutter.json` — the prompt schema: variable names, defaults, and choice lists (e.g. `basic/cookiecutter.json` defines `project_name`, a computed `project_slug` derived from it, `author`, and a `python_version` choice list).
- `{{cookiecutter.project_slug}}/` — the literal contents that become the generated project. **Every file under here is rendered as a Jinja2 template** (there's no `_copy_without_render` configured), including non-obvious ones like `pyproject.toml`, `CLAUDE.md`, and the GitHub Actions workflow files.
- Template lineage is tracked by **cruft**, not a hand-maintained file. When a project is generated with `cruft create` (or retrofitted with the `link-to-template` skill), cruft writes a `.cruft.json` at the project root recording the template URL, the exact template **git commit**, the `directory` (`basic`), and the answered context. That is the authoritative record of "which template version this project came from" and what drives `cruft update`. There is deliberately **no** `.cookiecutter-template-version` file in the template source — it was retired in favour of `.cruft.json` (see the `cruft` subsection below). The template's own version is recorded by `CHANGELOG.md` plus a `basic-v<semver>` git tag per release.

### Jinja/GitHub Actions delimiter collision

GitHub Actions expression syntax (`${{ ... }}`) and Jinja2's variable delimiters are the same (`{{ }}`). Because workflow files under `{{cookiecutter.project_slug}}/.github/workflows/` get rendered by Jinja, any *new* GitHub Actions expression added there (`${{ github.* }}`, `${{ secrets.* }}`, `${{ matrix.* }}`, etc.) must be wrapped in `{% raw %}...{% endraw %}`, otherwise Cookiecutter's render fails outright (e.g. `'github' is undefined`) rather than quietly producing a broken file. See `ci.yml`'s `concurrency.group` for a working example. Always test-render (`cookiecutter --no-input -o ...`) after touching a workflow file to catch this.

### Project standard docs

Docs like `git-workflow.md` live under each template's own `{{cookiecutter.project_slug}}/.claude/standards/` folder (cross-cutting guidance, alongside the tool/language-specific `{{cookiecutter.project_slug}}/.claude/rules/`) and get linked from that template's `CLAUDE.md` via Claude Code's `@` import syntax (rather than duplicated inline into `CLAUDE.md` itself). This keeps a project's `CLAUDE.md` referencing a swappable module instead of hardcoding conventions that a given project might not need. If a second template ends up needing the same doc, promote it back to a root-level canonical copy (this repo tried that once — see git history — and dropped it while there was only one template) and keep each template's copy in sync with it, e.g. via a CI check that diffs them.

### CHANGELOG.md

`CHANGELOG.md` at the repo root tracks notable changes *to the templates themselves* (one section per template, e.g. `## basic`), not changes to any project generated from them — a generated project's own history lives in its git log and its own changelog, if it has one.

**Release/bump ritual** for a meaningful template change (config *and* the version record are machine-checkable, so keep them in lockstep):

1. Make the change under `basic/`.
2. Add a `### [X.Y.Z] - <date>` entry to `CHANGELOG.md` (bump the minor for a feature, patch for a fix).
3. Test-render (`cookiecutter --no-input -o …`) and cruft-verify (`cruft create … --directory basic` produces a valid `.cruft.json`; the render passes the generated project's own gate).
4. Merge to `main` via PR.
5. Tag the merge commit `basic-vX.Y.Z` (annotated) and push it: `git tag -a basic-vX.Y.Z <sha> -m "basic template vX.Y.Z" && git push origin basic-vX.Y.Z`.

The pushed **tag is the authoritative version marker** and the ref `cruft`/`link-to-template` diff against — a release that isn't tagged can't be synced to. (There is no longer a `.cookiecutter-template-version` file to bump.)

### cruft (template → project sync)

[`cruft`](https://cruft.github.io/cruft/) is a drop-in over Cookiecutter (same `cookiecutter.json`, same `{{cookiecutter.project_slug}}/`) that keeps a generated project linked to this template so template improvements can be pulled in later instead of the scaffold going stale.

- **Never hand-author or commit a `.cruft.json` into the template source.** cruft writes it into the *generated* project at `cruft create` time, populated with a real commit SHA and the resolved answers. A literal `.cruft.json` under `{{cookiecutter.project_slug}}/` would be Jinja-rendered and then clobbered — pure downside.
- cruft only ever renders/diffs files **inside** the template directory (`basic/`). Repo-root files (`CLAUDE.md`, `CHANGELOG.md`) are not part of a generated project, so editing them never changes what `cruft update` applies downstream.
- **There is intentionally no cookiecutter post-generation hook** (`basic/hooks/` was removed). cruft re-runs a template's post-gen hook inside its own internal renders when computing an update, so any hook that writes to the tree (`git init` → a `.git/`; `pre-commit install` → absolute paths in `.git/hooks/`; `uv run`/`uv sync` → a `.venv/`) becomes state cruft must diff and patch — and `git apply` refuses paths under `.git/`, making cruft's patch fail and **silently drop real template changes**. Do not re-add a post-gen hook that touches the filesystem. Onboarding (install deps + Git hooks) is the generated project's own `make setup`; Git can't auto-install hooks on clone, and `ci.yml` is the real gate regardless.
- `cruft check` compares commit SHAs, not rendered content: it reports "behind" whenever the tracked branch has *any* newer commit, even one that didn't touch `basic/` (a repo-root doc edit still flips it). That's why the `Template Sync` gate is on-demand only — a false "behind" is harmless there, and `cruft update` still applies only the real delta.
- Generated projects track **`main`** (their `.cruft.json._commit` is a commit on `main`); `cruft check` reports when the template has moved ahead. The `basic-v*` tags are the human/version anchors and the baseline refs the **retrofit** path pins to — not what `check` compares against.
- Two skills drive updates (both are `.claude/skills/<name>/SKILL.md` packages): **`update-from-template`** (ships inside the template — `cruft check` → `cruft update` → resolve `.rej` → run the gate → summarize the `CHANGELOG.md` delta) and **`link-to-template`** (retrofit a pre-cruft project, then hand off to `update-from-template`).
- `link-to-template` is authored **twice** — once at this repo's root `.claude/skills/link-to-template/SKILL.md` (so it's usable against external projects that predate cruft and lack the shipped copy) and once inside the template (so future projects can self-retrofit). **Keep the two copies byte-identical** (same obligation flagged for duplicated standards docs above); a CI diff check is the natural enforcement if they start to drift.
