# Runbook — Reviewing and approving a plan (iPhone)

Operator procedure for the **inner Plan** HITL gate. Inner Plan is async: a
**Planner** managed agent drafts the plan + red tests + lock for one issue and
opens a **draft PR**, then requests your review. You approve on your own time
from the GitHub mobile app; approval kicks Build. No red tests ever reach `main`
— the plan, tests, and lock live on the issue branch throughout.

This is the one thing that has to stay light. The whole async-planning design
rests on this being a 30-second skim, not a chore. See
[`../coding-workflow.md`](../coding-workflow.md) (Inner · Plan) for where this
sits in the workflow.

## What kicks it off

The Planner opens a **draft PR** for the issue (plan `spec/issues/00N-plan.md`,
red tests, and `spec/issues/00N-tests.lock` on the issue branch) and **requests
your review**. GitHub Mobile sends a push notification. You were doing nothing
until it arrived — you never wait for a plan to be produced.

## Steps (GitHub iOS app)

1. **Tap the push notification** — *"Planner requested your review on #42."* The
   PR opens.
2. **Read the summary (~20–30s).** The PR description leads with the
   **acceptance criteria** and the **test list** — the Planner is required to
   write plans to be skimmed, with implementation detail below the fold. For a
   normal issue this is the entire review.
3. **(Optional) Check the tests.** Tap **Files changed** → the test file, and
   confirm the tests actually assert the criteria. `plan.md`'s reasoning is
   there too if you want it.
4. **Decide and act:**
   - **Good →** in the PR's **Conversation** tab, use the main comment box at
     the bottom (a normal PR comment — **not** an inline comment on a code line),
     type **`/approve`**, and send.
   - **Needs a change →** comment what's wrong instead. The Planner revises and
     re-requests your review, or you open a Claude Code session on the branch to
     work it through.
   - **Too big / wrong shape →** say so in a comment; the issue goes back to
     Sequencing to be re-decomposed into smaller issues.
5. **Done — close the app.** Approval is asynchronous; nothing else is needed
   from you here.

## What happens after `/approve` (automatic)

- A launcher GitHub Action (`on: issue_comment`) verifies the commenter is
  authorized and the body is `/approve`, then **launches the Scrum Master** —
  which records `Status: approved` in the plan file (the source of truth) and
  spawns **Build** for the issue. (The Action does not write status or start
  Build itself; the Scrum Master is the sole orchestrator that spawns and judges
  every subagent — see [ADR-0008](../adr/0008-scrum-master-orchestrator.md).)
  Note this fires only for **conversation** comments, which is why `/approve`
  must be a top-level PR comment, not an inline review comment on a diff line.
- Build implements, gets the tests green, runs a context-free code-review
  subagent over its own diff (one fix pass), and flips the **same PR** from
  draft to **ready for review**.
- The next time #42 surfaces for you, it is a **ready** PR — your final code
  review, a separate and normal **Approve + merge**.

## Notes

- **`/approve` approves the *plan*, not the code.** The implementation gets its
  own review when Build marks the PR ready. Don't conflate the two — that
  separation is deliberate.
- **Why a comment, not the native Approve button.** A `/approve` comment works
  reliably on the mobile app and on a *draft* PR; a native review-approve is
  finicky on drafts. The comment is also unambiguously distinct from the final
  code-review Approve.
- **Approval is file state.** `Status: approved` in the plan file is the source
  of truth (same pattern as the spec's `Status`); `/approve` is just the
  mobile-friendly way to set it. Labels are never used as control-flow triggers.
- **Reviewability is a Planner contract, not a hope.** If plans drift long, no
  gesture saves the review. The Planner must lead every plan with criteria + the
  test list; detail goes below the fold.
