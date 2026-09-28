---
name: update-from-template
description: Update this project to the latest version of the cookiecutter template it was generated from, using cruft — check for drift, apply the delta, resolve conflicts, and re-run the project's full quality gate. Use when asked to "update from the template", "sync to the latest template", or after the Template Sync CI job reports the project is behind.
---

# Update from template

This project was generated from a cookiecutter template and linked to it with
[`cruft`](https://cruft.github.io/cruft/). The link lives in `.cruft.json` at the
project root: it records the template repo URL, the exact template **commit** the
project last synced to, and the answers given at generation. `cruft` uses that to
compute a 3-way merge of newer template changes onto this project.

Run this loop to catch the project up to the latest template.

## Preconditions

1. **`.cruft.json` must exist.** If it does not, this project predates cruft — run
   the `link-to-template` skill first to retrofit the link, which then hands back
   here.
2. **Start clean, on a fresh branch.** Working tree must be clean. Per
   `.claude/standards/git-workflow.md`, create a branch for this change, e.g.
   `git checkout -b feature/template-sync`.

## Steps

1. **Check for drift.**
   ```bash
   uvx cruft check
   ```
   Exit 0 means already up to date — stop and report "nothing to do". A non-zero
   exit means the project is behind; continue.

2. **Ensure the cruft skip list is present.** cruft must never diff/patch generated
   artifacts — `git apply` refuses paths under `.git/`, and an untracked `.venv/`
   makes a patch fail and **silently drop real template changes**. Confirm
   `.cruft.json` has a top-level `skip` array and add the entries if missing:
   ```json
   "skip": [".git", ".venv", "uv.lock"]
   ```
   (Projects generated with cruft from v1.10.0+ don't strictly need this for a
   forward update, but it is always correct — those paths are never
   template-managed — and it is essential for projects retrofitted from an older
   baseline. Add any other generated cache present in the diff, e.g. `.ruff_cache`.)

3. **Understand what is arriving (intent, not just diff).** The summary must name
   which template versions this update spans, so read the template's `CHANGELOG.md`
   delta:
   - The project's current template version is the tag pointing at the `commit`
     recorded in `.cruft.json`. Resolve it against the template repo, e.g.
     `git ls-remote --tags <template-url>` then match, or in a clone
     `git describe --tags <commit>` — expect a `basic-vX.Y.Z` tag.
   - Read every `### [X.Y.Z]` entry in the template's `CHANGELOG.md` newer than
     that version. Those entries are the *why* behind the diff you are about to
     apply — note anything that needs a manual follow-up (a new required tool, a
     renamed convention, a new standard).

4. **Apply the delta.**
   ```bash
   uvx cruft update
   ```
   cruft renders the template at the old and new commits, applies only the
   difference, and advances the `commit` in `.cruft.json`. Where a template change
   collides with a local edit, cruft writes a `*.rej` reject file instead of
   silently overwriting.

5. **Resolve conflicts.** Find every reject file (`git status`, or search for
   `*.rej`). For each: read the `.rej`, apply the intended change to the real file
   by hand, then delete the `.rej`. Do not commit any `.rej` file. Prefer the
   template's version for template-owned files unless a deliberate local
   divergence explains the conflict.

6. **Run the full gate.** Exactly the project's own checks — do not skip any:
   ```bash
   uv sync
   uv run pytest
   uv run ruff check .
   uv run mypy src
   uv run pre-commit run --all-files
   ```
   Fix anything the update broke before proceeding.

7. **Commit and open a PR.** Conventional Commits (`chore:`/`feat:` as fits), with
   the assisting-model co-author trailer, per `.claude/standards/git-workflow.md`.
   Reference the template versions spanned. Open a PR; it must pass `ci.yml`.

8. **Summarize.** Report: the version span (from → to, e.g. `basic-v1.7.0` →
   `basic-v1.10.0`), the CHANGELOG entries that explain the change, the files cruft
   modified, and every conflict you resolved by hand.

## Not the same as `refresh`

The `refresh` ("Sync to Main") command syncs a git branch to `origin/main` — a
plain VCS operation. This skill syncs the *project to its template*, a different
axis. They do not compose or supersede each other; use whichever the task needs.
