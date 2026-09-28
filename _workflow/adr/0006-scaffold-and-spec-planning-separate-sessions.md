# ADR-0006: Scaffold and Spec Planning are separate sessions

## Status
accepted

## Context
Scaffold and Spec Planning are both Claude Code HITL sessions running against
the same repo, back to back. Merging them into one session is tempting —
Scaffold's context footprint is negligible, and it does no heavy work of its
own. But Scaffold is what installs the project's `CLAUDE.md`, `.claude/`
settings, and project skills/directives (from the template), and those load
only when a Claude Code session **initializes** against the repo — they are not
hot-reloaded when files appear mid-session.

## Decision
Keep Scaffold and Spec Planning as **separate sessions**. Scaffold renders the
template and lands it via a PR; the human merges it; then a **fresh** Spec
Planning session starts against the now-scaffolded `main`, which loads the
project's `CLAUDE.md` and directives at startup.

## Consequences
- **Easier:** Spec Planning authors the spec *with* the project's own
  conventions loaded — the stage that most needs them gets them for free. This
  is the whole point: the fresh session is the mechanism that forces the
  scaffolded directives to load.
- **Easier:** a clean handoff — Scaffold ends, human merges the scaffold PR,
  Spec Planning begins on updated `main` — instead of one session carrying an
  open scaffold PR through into spec work on the same branch.
- **Harder:** one more session boundary and human touchpoint (start a new
  session after the merge) rather than a single continuous conversation.
- **Forecloses:** a merged "scaffold-and-plan-in-one-go" session. If a future
  change made project directives load dynamically mid-session, this reasoning
  would weaken and merging could be reconsidered.
