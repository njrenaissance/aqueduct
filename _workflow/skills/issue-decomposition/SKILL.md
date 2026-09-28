---
name: issue-decomposition
description: Use this skill when the Outer Sequencing agent breaks an approved, merged spec down into the first-pass set of GitHub issues. Covers what "independently buildable" means in practice and the content each created GitHub issue must carry. Trigger this whenever decomposing a feature into units of work, not only when the human says "issues" or "tickets."
---

# Issue Decomposition

## When this applies

This skill applies during **Outer Sequencing** — the automated Managed
Agent pass that runs *after* the spec is approved and merged to `main`,
working against the fully scaffolded repository. Scaffolding is the very
first outer-loop step, so by the time decomposition happens the repo
already has its full project structure, CI, and issue templates in place.

This is **not** part of Spec Planning, and it is **not** before
scaffolding — there is no point in the workflow before scaffolding. The
Sequencing agent's work runs in two parts: (1) this skill — read
`spec/spec.md` and decompose it into independently buildable GitHub
issues; then (2) dependency analysis and build ordering over the issues
just created (see "What this step does NOT do").

## What "independently buildable" actually means

An issue is decomposed finely enough when you can write its acceptance
criteria without referencing another issue's implementation details.
Use this as a direct test, not a vibe check:

> Read the issue body back to yourself. Does understanding it require
> knowing *how* another issue will be built — not just *that* it
> exists? If yes, split further or resequence.

It's fine for issue B to depend on issue A's existence ("assumes the
`Token` type from issue #1 exists") — that's a sequencing fact the second
part of this same Sequencing pass will handle. It's not fine for issue
B's acceptance criteria to be unwritable without knowing issue A's
internal design.

## Prefer smaller over larger

Inner Plan has a 5-round turn limit before an issue gets flagged as too
large or ambiguous and sent back for splitting. Decomposing generously
here avoids paying that cost downstream. When in doubt, split — a
too-small issue costs a little coordination overhead; a too-large issue
costs a stalled Inner Plan and a re-sequencing round trip.

**Signal an issue is too large:** its Done criteria (once inner Plan
gets to it) would span multiple unrelated behaviors, or its
implementation would plausibly touch more than a handful of files
across different concerns.

**Signal an issue is appropriately sized:** you could describe its
acceptance criteria in a handful of testable statements (see the
spec-authoring skill's "testable vs. vague" guidance — the same bar
applies to issue-level Done criteria as project-level ones), and doing
so doesn't require describing another issue's internals.

## What this step does NOT do

No dependency analysis, no build order, no parallel grouping — that is the
**second part of the same Sequencing pass**, run over the GitHub issues
this step creates. Keeping decomposition and dependency analysis separate
matters: dependency judgments are made against the real, created issues,
not guessed while the spec is still being split. That second part writes
`spec/build-order.md` in the machine-parseable format defined in
`coding-workflow.md` (a YAML block of `issues` / `groups` / `order` that
the Scrum Master parses on every merge) — not this step's concern.

## Issue content and format

Create each unit of work in the repository's **GitHub Issues tracker** via
`gh issue create` — not as a file in the working tree. There is no
`issues/` folder; the only output of this step is new entries in the
repo's Issues list.

**Fill in the installed `feature_request` template — don't invent an issue
shape.** Read `.github/ISSUE_TEMPLATE/feature_request.md` in the repo and
populate its sections from the spec (the same way scaffolding reads
`cookiecutter.json` live rather than assuming fields). It sets the `feat:`
title prefix and the `enhancement` label. Its **Acceptance criteria**
section must be testable to the same bar as spec.md's Done criteria —
Inner Plan authors the executable tests straight from it, so write each
criterion precisely enough that someone else could write a test from it
without asking you anything further. Reserve `bug_report` for defects, not
planned work.

```bash
gh issue create \
  --title "feat: <short, specific title>" \
  --label enhancement \
  --body-file <rendered feature_request body>
```

Once the issues exist, the second part of Outer Sequencing labels them
into parallel groups and establishes build order — this decomposition
portion only creates them.
