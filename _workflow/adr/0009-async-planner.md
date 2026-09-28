# ADR-0009: Inner Plan is async — a Planner agent drafts, the human approves with `/approve`

## Status
accepted (supersedes [ADR-0005](0005-inner-plan-stays-interactive.md))

## Context
[ADR-0005](0005-inner-plan-stays-interactive.md) kept Inner Plan an interactive
Claude Code session, arguing the plan + red tests are high-judgment,
multi-round, co-authored work, not a single approve/reject. That held while the
human was the *author*. But planning stayed the dominant HITL drag, and picking
it apart showed the drag was never the number of plans — it was **waiting
synchronously while the plan was produced**, and co-authoring test code round by
round.

Two things dissolve that. First, a **reviewability contract**: if the plan
leads with the acceptance criteria and the test list (detail below the fold),
approving it is a ~30-second skim of "are these the right tests?", not
co-authoring. Second, [ADR-0007](0007-test-integrity-hash-guard.md): the tests
are now the load-bearing oracle Build is graded against, so what must be
preserved is the human's *sign-off on the tests* — not the human's *authorship*
of them. Those can be separated.

## Decision
Inner Plan becomes **async**. A **Planner** Managed Agent
([`inner-plan.yml`](../managed-agents/inner-plan.yml)) drafts the plan, the red
TDD tests (validated genuinely red), and the test lock on the issue branch —
off the critical path — then opens a **draft PR** and requests review. The
human reviews on their own time and approves with a one-tap **`/approve`**
comment on the draft PR (see [`../runbooks/reviewing-a-plan.md`](../runbooks/reviewing-a-plan.md)),
which launches the Scrum Master to spawn Build. No live session; no waiting for
authoring.

- **Per issue, not per group.** Batching plans was considered and rejected: once
  planning is async, reviewing five plans in one file is the same work as five
  files, and batching couples them (the slowest plan gates the whole group's
  build). The parallel *group* remains only the scheduling unit.
- **The human still signs off on the oracle** — the `/approve` is the gate that
  makes the hash-guard meaningful. Drafting moved to an agent; approval did not.
- **No red tests reach `main`.** Plan, tests, and lock live on the issue branch;
  the draft PR is the review surface; `/approve` is a file-state approval, not a
  merge.

## Consequences
- **Easier:** the drag that actually hurt — synchronous waiting — is gone. The
  human is notified when a plan is ready and approves from a phone in seconds.
- **Easier:** planning parallelizes across a group without more human time,
  since each plan is drafted and approved independently.
- **Harder:** the human no longer edits the tests directly, so review quality
  now rests entirely on genuine scrutiny of a skimmable artifact — batch or
  sloppy review degrades the oracle silently. The reviewability contract (lead
  with criteria + test list) is therefore load-bearing, not cosmetic.
- **Harder:** an agent authoring the tests can get them subtly wrong in ways a
  fast approval misses; the backstops are Deploy+Evaluate's independent E2E and
  the final human PR review.
- **Reverses ADR-0005** and realizes the "purely message-driven Plan approval
  loop" it foreclosed — now wanted, because the reviewability contract plus the
  hash-guard make a one-tap approval safe where free-form co-authoring once
  seemed required.
