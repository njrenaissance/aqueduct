---
name: link-to-template
description: Retrofit a project that was generated before cruft adoption (it has a .cookiecutter-template-version but no .cruft.json) onto its cookiecutter template, so it can be updated with cruft. Derives the baseline template tag from the version marker, runs cruft link, retires the old marker, then hands off to update-from-template. Use for "link this project to the template", "adopt cruft here", or any pre-cruft project you want to sync.
---

# Link to template (retrofit)

Projects generated with plain `cookiecutter` (before this template adopted
[`cruft`](https://cruft.github.io/cruft/)) have no `.cruft.json`, so there is no
recorded link back to the template and no way to compute an update. They do carry
a `.cookiecutter-template-version` file (e.g. `1.7.0`) — this skill uses it to
establish the cruft link retroactively, then retires it in favour of `.cruft.json`.

Run this from the root of the project being retrofitted.

## Preconditions

1. **No `.cruft.json` yet.** If one exists, the project is already linked — use the
   `update-from-template` skill instead.
2. The project has a `.cookiecutter-template-version` file. If it does not, its
   baseline version is unknown: determine it another way (ask the owner, or inspect
   git history / `pyproject.toml`) before linking — the baseline must match reality
   or the first update will produce spurious conflicts.

## Steps

1. **Derive the baseline tag.** Read `.cookiecutter-template-version` (e.g. `1.7.0`)
   and form the matching template tag: `basic-v` + that value → `basic-v1.7.0`. The
   template repo is tagged per version; this is the ref cruft diffs from.

2. **Link, pinned to the baseline.**
   ```bash
   cruft link https://github.com/njrenaissance/project-templates --directory basic --checkout basic-v1.7.0
   ```
   (Substitute the derived tag.) cruft will prompt for the template variables just
   like generation did — answer them to reproduce **the project's original
   answers** (project_name, author, python_version, and the app_config /
   structured_logging / telemetry / security toggles). Getting these right matters:
   they define the baseline cruft renders and diffs against, so wrong answers turn
   into fake conflicts on the first update.

3. **Sanity-check the written `.cruft.json`.** Confirm `commit` equals the SHA the
   `basic-v...` tag points at, `directory` is `"basic"`, and the recorded context
   matches how the project is actually configured.

4. **Add a cruft skip list — required for retrofit.** Baselines generated with the
   template's older post-generation hook (v1.8.0–v1.9.0) baked machine-specific
   paths into `.git/hooks/` and created a `.venv/` + `uv.lock`. cruft would try to
   diff and patch those when updating from the baseline, and `git apply` refuses
   paths under `.git/` — which makes `cruft update` **silently drop the real
   template changes**. Prevent that by adding a top-level `skip` array to
   `.cruft.json` before updating:
   ```json
   "skip": [".git", ".venv", "uv.lock"]
   ```
   (These are never template-managed, so skipping them is always correct. Add any
   other generated cache the baseline produced — e.g. `.ruff_cache`,
   `.mypy_cache`, `.pytest_cache` — if present in the diff.)

5. **Retire the old marker.** `.cruft.json` is now authoritative for template
   lineage, so remove the redundant version file — no project keeps both:
   ```bash
   git rm .cookiecutter-template-version
   ```
   Commit the swap (`.cruft.json` added with its `skip` list,
   `.cookiecutter-template-version` removed) as a Conventional Commit with the
   assisting-model co-author trailer.

6. **Catch up to latest.** The project is now linked but still pinned to its old
   baseline. Hand off to the `update-from-template` skill to apply every template
   change from the baseline tag up to the latest version (that skill runs
   `cruft update`, resolves any `.rej` conflicts, runs the full gate, and summarizes
   the CHANGELOG delta).
