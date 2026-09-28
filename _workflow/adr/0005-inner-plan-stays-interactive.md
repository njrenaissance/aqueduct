# ADR-0005: Inner Plan stays an interactive HITL session

## Status
superseded by [ADR-0009](0009-async-planner.md)

> **Superseded.** Inner Plan is now async: a Planner Managed Agent drafts the
> plan and red tests off the critical path, and the human's part is a one-tap
> `/approve` on a draft PR — the "purely message-driven Plan approval loop" this
> ADR foreclosed. What changed the calculus: the reviewability contract (plan
> leads with acceptance criteria + the test list) turns approval into a
> 30-second skim rather than iterative co-authoring, and the test-integrity
> guard ([ADR-0007](0007-test-integrity-hash-guard.md)) is what keeps the human
> sign-off load-bearing. See [ADR-0009](0009-async-planner.md).

## Context
We considered converting Inner Plan into a Managed Agent that sends the drafted
plan out over Slack or email and handles the human's replies — an async,
mobile-friendly approval loop. But Inner Plan is the highest-judgment step in
the workflow: iterative co-authoring of a plan, the red TDD tests, and a
minimal stub over up to five rounds, including running the tests to confirm
they fail for the right reason. That is interactive, multi-artifact work, not a
single approve/reject.

## Decision
Inner Plan remains an interactive Claude Code HITL session (in Plan Mode).
Async messaging is not used to author or approve plans. If less babysitting is
wanted, HITL gates may emit an async *notification* (Slack/email) that links
into the interactive session — notify async, author and approve interactively.

## Consequences
- **Easier:** preserves the editor and test-running surface where precision
  matters most; avoids reducing the human to describing test-code edits in
  prose over a chat thread, which a headless agent would then apply blind.
- **Easier:** the mobile/async access that motivated the messaging idea is
  already provided by Claude Code on the Web (see
  [ADR-0001](0001-two-execution-surfaces.md)), so little is gained by moving to
  Slack and much is lost.
- **Easier:** no messaging integration, reply-to-session threading, or auth to
  build and operate — consistent with running no infrastructure of our own.
- **Harder:** the approval gate is not a one-tap async action; the human must
  enter a session to iterate and approve.
- **Forecloses:** a purely message-driven Plan approval loop. If a future
  context genuinely needs headless plan approval, this would need revisiting.
