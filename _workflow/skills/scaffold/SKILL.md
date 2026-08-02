---
name: scaffold
description: Use this skill when rendering a brand-new project's structure from a Cookiecutter template into a repository that already exists — created and protected by the Create Repo phase — during the first Claude Code session that does project work. It renders the template and opens a PR to land the scaffold on the protected `main`. Trigger whenever the task is "set up/scaffold/initialize a new project's structure." It does NOT create the repo, seed labels, or protect `main` — those happen before it, in Create Repo.
allowed-tools: Read Write Glob Bash(git clone:*) Bash(git status) Bash(git diff:*) Bash(gh repo clone:*) Bash(gh api repos/*/contents*) Bash(bash skills/scaffold/scaffold.sh:*)
---

# Scaffold

## When this applies

This is the **first** Claude Code session that does project work for a new
project — but the GitHub repository **already exists**. A prior **Create Repo**
phase created it, protected `main`, and seeded the workflow's labels. This skill
renders the chosen template *into* that repo and lands it through a **PR**
(because `main` is protected). No spec work has happened yet; Spec Planning (a
separate session, guided by the spec-authoring, adr-authoring, and
issue-decomposition skills) always comes after this step.

This skill does **not** create the repository, create labels, or protect `main`
— those are Create Repo's job and require GitHub-admin credentials this session
(Claude Code on the Web) does not have.

Do not use this skill against a repository that already has project structure in
it — that's an established project, not scaffolding.

## Step 1 — Pick the template, then read its actual questions

1. **Language / template.** Read the actual `project-templates` repository
   directly — list its top-level directories and treat as a template **only**
   those with their own `cookiecutter.json`. **Skip any leading-underscore
   directory** (e.g. `_workflow/`): the underscore marks repo docs/config that is
   never a selectable template, and it has no `cookiecutter.json` to render.
   For any plausible match, read its `cookiecutter.json` and any README. This
   repository
   must be accessible in this session — cloned/added alongside (e.g.
   `--add-dir`) or read via the GitHub API/`gh` CLI without a full clone (`gh api
   repos/<org>/project-templates/contents`, or `gh repo clone
   <org>/project-templates /tmp/project-templates --depth 1`). If neither is
   available, say so explicitly rather than falling back to a remembered or
   assumed list — a wrong guess breaks Cookiecutter outright.
2. Never invent a template name or assume one exists from general Cookiecutter
   conventions. Only use a directory actually present in `project-templates`
   right now, confirmed by reading it this session.
3. **Before asking the human anything else, read that template's actual
   `cookiecutter.json`.** Do not assume field names — every template defines a
   different set of variables, defaults, and choices. Reading it first is what
   lets you ask only what this template actually needs.
4. **The project name/slug is NOT asked — it comes from the repo.** Create Repo
   already named the repository, and that name **is** the `project_slug`. Read it
   from the repo you're in and use it; do not prompt the human for a project
   name. Every other template variable is still fair game for the question batch
   below.

## Step 2 — Ask the human's questions upfront, in one batch

Once `cookiecutter.json` is read, you know the complete set of variables this
template needs (minus `project_slug`/`project_name`, which come from the repo).
Present the rest to the human **together, in one message**:

- For clearly boilerplate fields (author name, license) propose a sensible
  default and let them override rather than asking outright.
- For anything resembling an **advanced feature toggle** (structured logging,
  security scaffolding, or any boolean/choice adding capability beyond bare
  structure) **default to off** and say so: state "defaulting \[list the actual
  toggle names from cookiecutter.json\] off unless you want any on" and let the
  human name exceptions.
- Only ask a genuine question for fields where you can't reasonably guess the
  human's intent (e.g. a choice between architecturally different variants).

Do not proceed until the human has responded to this batch.

## Step 3 — Write the context file

Generate `cookiecutter-context.json` using the **exact field names read from the
template's own `cookiecutter.json` in Step 1** — never a guessed or generic
shape. The structure is always `{"cookiecutter": {...}}`; which keys go inside
depends entirely on that template. Set `project_slug` (and `project_name` if the
template uses it) to the **repo's name** — the source of truth — and the rest to
the values agreed in Step 2.

**This — choosing the template, eliciting the answers, and writing this context
file — is the only judgment work in Scaffold.** Everything after (render →
verify → branch → commit → push → open PR) is a fixed, decision-free sequence
handled by [`scaffold.sh`](scaffold.sh) — do **not** re-do those steps by hand or
improvise git/gh commands.

## Step 4 — Run the scaffold script

Invoke the script with the template directory from Step 1 and the context file
you just wrote, **from inside the repo checkout**. It renders the template into
the repo, verifies the tree, creates a branch, commits, pushes, and opens a PR
into the protected `main`:

```bash
bash skills/scaffold/scaffold.sh \
  --template-dir /path/to/fetched/project-templates/<template-dir> \
  --context-file cookiecutter-context.json
```

What the script guarantees so you don't have to re-check it in prose:

- It derives the expected `project_slug` from the repo it runs in and **stops on
  a mismatch** with the rendered project — a mismatch means the context's slug
  doesn't match the repo Create Repo made.
- It renders the template and lays the generated project into the repo,
  overwriting the placeholder commit (e.g. the README) Create Repo left.
- It runs `pre-commit install` when the template rendered a
  `.pre-commit-config.yaml`, and **warns** if pre-commit config or a CI workflow
  is missing from the render.
- It creates a branch (default `scaffold`), commits the rendered tree, pushes,
  and opens a **PR into `main`** — the scaffold's path onto the protected
  branch. It does **not** commit directly to `main`, create the repo, seed
  labels, or touch branch protection; those were Create Repo's.

If the script exits non-zero, relay its message to the human and stop — do not
paper over a failed render, push, or PR step by finishing the sequence manually.
The human reviews and merges the scaffold PR to complete the phase.

## What this skill does NOT do

No repo creation, no label seeding, no branch protection — all of that is the
prior Create Repo phase. No spec, no ADRs, no issues — those come later. This
session's job ends at a **scaffold PR opened into the protected `main`** for the
human to merge. Tell the human clearly when the PR is up so they can review and
merge, after which Spec Planning (the next session) can begin.
