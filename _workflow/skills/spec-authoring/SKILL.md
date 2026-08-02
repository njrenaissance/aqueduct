---
name: spec-authoring
description: Use this skill whenever authoring or updating spec/spec.md for a new project in the Phillips Inc Software Factory outer loop. Covers the required fields, what makes Done criteria testable versus vague, and the draft/approved status lifecycle. Trigger this any time the task is to produce a project spec, not just when the word "spec" appears explicitly.
---

# Spec Authoring

## When this applies

This skill applies during Spec Planning conversations — working against a
newly created, mostly empty repository, before Cookiecutter scaffolding
or any production code exists. Your job here is producing `spec.md` — the
single canonical artifact every later stage (Scaffold, Sequencing, Inner
Plan, Inner Build, Inner Review) reads to know what "done" means.

## Required fields

Write exactly this structure — do not add or rename top-level fields
without the human explicitly asking for a change to the template itself:

```markdown
# spec.md

**Status:** draft | approved

## Purpose
One sentence. What problem does this build solve? If you need two
sentences, the scope is probably not yet well-defined enough to build.

## Inputs / Outputs
The contract. What comes in, what comes out. Be concrete about types
and shapes, not just names — "a list of transactions" is not a contract;
"a list of {date, amount, category} objects" is closer.

## What we produce
One of: library | CLI | service | batch

## Where we persist
One of: stateless | file | DB

## Method
One of: rules | classical ML | LLM

## Done criteria
Observable behaviors the TDD unit tests will assert. Not "the feature
works" — specific, checkable statements. See "Testable vs. vague" below.
```

## Testable vs. vague Done criteria

This is the part most likely to go wrong, and the part later stages
depend on most. A bad example and a good example, side by side:

**Vague (reject this):**
> "The parser correctly handles arithmetic expressions."

**Testable (write this instead):**
> - `parse("1 + 2")` returns an AST whose root is a `+` node with integer
>   leaves `1` and `2`
> - `parse("1 + 2 * 3")` respects operator precedence — the `*` node is
>   nested under the `+` node's right child
> - `parse("(1 + 2) * 3")` respects parentheses — the `+` node is nested
>   under the `*` node's left child
> - `parse("1 +")` raises a `ParseError` with a message identifying the
>   incomplete expression

The test: could someone who has never talked to you write a unit test
directly from this line, with no further clarification? If not, it's
not a Done criterion yet — keep refining it.

## Status lifecycle

- Start every spec at `Status: draft`.
- Do not change to `Status: approved` until the human explicitly states
  in conversation that the spec is approved. A short reply, a "looks
  good", or silence is not approval — see the interaction protocol in
  your project's CLAUDE.md.
- Once `Status: approved`, commit that final state. The spec is then
  treated as fixed. Any further change to an approved spec means the
  project re-enters Spec Planning; it is not edited in place downstream.

## Where this file goes

Write the final content to `spec/spec.md`, relative to the repository
root. Commit it as you go — don't hold the draft in memory and write it
only at the end; each meaningful revision should be its own commit so
the iteration history is visible, same as any other work in progress.
