# ADR-0002: The plan is a persisted artifact, not a conversation

## Status
accepted

## Context
Inner Plan (a Claude Code HITL session) and Inner Build (a Managed Agent) are
separate execution surfaces with no shared memory — see
[ADR-0001](0001-two-execution-surfaces.md). Build cannot see Plan's
conversation. Yet Build needs the agreed plan, the acceptance criteria, and
the tests that assert them. Something has to carry that agreement across the
surface boundary.

## Decision
Inner Plan commits the plan (`spec/issues/00N-plan.md`), the red TDD tests
(`tests/`), and a minimal stub to the issue branch, then pushes. Inner Build
reads them off the checkout it mounts. The plan is a versioned artifact on the
branch — not a handoff held in conversation context.

## Consequences
- **Easier:** Build reads an authoritative plan versioned with the exact
  commit it builds against; the plan is visible in the PR and history, and
  reviewable like any other change. This is what makes the two-surface split
  in ADR-0001 workable at all.
- **Harder:** it requires a deliberate commit-and-push step at the end of Plan
  — a required action, not implied by approval, and easy to forget. The doc
  calls it out explicitly for that reason.
- **Harder:** the initial push carries deliberately-red tests, which forces a
  documented `--no-verify` exception to the pre-push gate (see
  [ADR-0003](0003-split-hook-strategy.md)).
- **Harder:** Plan and Build can't clarify interactively. If the committed
  plan is ambiguous, Build cannot ask — it must halt and escalate. This puts
  the weight on getting plan quality right up front.
- **Forecloses:** a lightweight conversational "just tell the builder what to
  do" handoff.
