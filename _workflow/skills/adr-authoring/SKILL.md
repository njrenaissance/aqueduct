---
name: adr-authoring
description: Use this skill when deciding whether a design decision made during Spec Planning warrants an Architecture Decision Record, and when writing one. Covers the ADR format and — more importantly — the judgment call for when a decision is significant enough to record versus when it belongs as an ordinary spec detail. Trigger this whenever Spec Planning surfaces a choice between competing approaches, not only when the human uses the word "ADR."
---

# ADR Authoring

## When this applies

This skill applies during Spec Planning conversations — working against a
newly created, mostly empty repository, before Cookiecutter scaffolding
or any production code exists.

## When a decision needs an ADR

Not every choice made during Spec Planning is worth an ADR. Writing one
for every minor decision buries the decisions that actually matter.
Write an ADR only when the decision is **expensive to reverse later** —
use this test:

> If we discover in three months that this was the wrong call, is
> reversing it a small edit, or a rewrite touching multiple files and
> the public contract?

Small edit → not an ADR, just note it in `spec.md` or move on.
Rewrite-scale → write an ADR.

**Examples that warrant an ADR:**
- Choice of persistence layer (file vs. DB vs. stateless) when the
  project could plausibly need to change this as it grows
- A grammar/parsing strategy that constrains what the language can
  later express
- A decision to exclude a capability the human might reasonably expect
  later, with the reasoning for why

**Examples that do not warrant an ADR:**
- Variable naming conventions
- Which specific library implements a well-understood, swappable piece
  (e.g. which stdlib module handles date parsing)
- Anything already fully specified by `stack.yaml` from the
  stack-catalog skill — don't re-litigate the template's own choices

## Format

One file per decision, numbered sequentially starting at `0001`:

```markdown
# ADR-000N: <short decision title>

## Status
proposed | accepted | superseded by ADR-000M

## Context
What situation forced this decision? What were the real constraints —
not a restatement of the whole project, just what's relevant to this
one choice.

## Decision
What was decided, stated plainly in one or two sentences.

## Consequences
What this makes easier, what this makes harder, and what it forecloses.
Be honest about the downsides — an ADR that only lists benefits isn't
useful to a future reader deciding whether to revisit it.
```

## Where these files go

Write each to `spec/adr/000N-title.md`, relative to the repository root,
using a lowercase, hyphenated slug for the title portion (e.g.
`spec/adr/0001-file-based-persistence.md`). Number sequentially — do not
reuse a number even if an earlier ADR was rejected during the same
conversation; give the rejected one `Status: rejected` and move on to
the next number. Commit each ADR as its own commit, same as `spec.md`.
