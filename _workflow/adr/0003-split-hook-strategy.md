# ADR-0003: Split hooks — pre-commit (lint/format/typecheck) vs pre-push (full suite)

## Status
accepted

## Context
Two things in the workflow fight a naive "all tests pass on every commit"
gate. Inner Plan deliberately commits tests that are *supposed* to fail (the
red of red/green TDD), and Inner Build makes many intermediate commits while
iterating toward green. A single test gate on every commit would block both.
But the opposing requirement is just as real: a human must never see a *pushed*
state whose tests don't actually pass.

## Decision
Two hooks enforce two different things. **pre-commit** runs lint, format, and
typecheck only — fast, and never touching test outcomes — on every commit.
**pre-push** runs the full test suite as the real correctness gate. Two
deliberate, documented exceptions bypass pre-push with `git push --no-verify`,
each explained in its commit message: Inner Plan's initial red push, and Inner
Build's 3rd failed-retry escalation push (to preserve the failing state for a
human to inspect).

## Consequences
- **Easier:** red/green TDD and Build's free-form iterative commits don't fight
  a test gate — only pushes are gated, which is where "must be green" actually
  matters.
- **Easier:** pre-commit is cheap and never needs bypassing; a type-valid stub
  still commits, so it doesn't block a deliberately-red TDD commit.
- **Harder:** two hooks plus two documented exceptions to maintain and explain.
  `--no-verify` appearing anywhere outside those two cases is a smell worth
  investigating, which is itself a rule the team must hold.
- **Harder:** putting typecheck in pre-commit means a stub must be type-valid
  to commit at all. Deliberately-red *tests* still pass typecheck, so this is
  acceptable — but a genuine type error now blocks even an intended-red commit.
- **Forecloses:** a single uniform commit-time gate.
