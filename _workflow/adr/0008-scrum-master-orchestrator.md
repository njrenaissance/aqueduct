# ADR-0008: The build phase is driven by a resumable Scrum Master orchestrator

## Status
accepted

## Context
The outer loop's "Build" step was always a hand-wave — "invoke the inner loop
once per issue" — with no actual driver named. Something has to sequence groups,
launch per-issue work, and decide what to do when a stage reports a problem (a
Build that can't get green, a coupling that surfaces mid-build, a defect filed
by the integration gate). Two shapes were on the table.

A **reconciler + thin CI launchers**: no orchestrator, just narrow GitHub
triggers, with all control flow mechanical (file state in, agent launched out).
This matched the workflow's earlier "narrow triggers, not a general orchestrator"
stance. But a mechanical trigger cannot *judge* — it can start a Planner or a
Build, but it can't look at a Build's outcome and decide "escalate vs re-plan vs
proceed."

The **canonical Anthropic Orchestrator Managed Agent**: an agent that delegates
to subagents and reasons over their results. This buys exactly the judgment the
reconciler lacks. Its one hazard for *this* workflow is the long human gates —
async plan `/approve`, human PR review — which can take hours or days. An
orchestrator that sits in a live session waiting on a human is fragile and
defeats "no long-running infrastructure."

## Decision
Introduce the **Scrum Master**, an orchestrator Managed Agent that owns the
whole build phase — a deliberate reversal of the "no general orchestrator"
stance. It is made safe for the human gates by being **resumable, not
long-running**: it is re-invoked per event by a thin GitHub Action, reconstructs
its state from durable artifacts, takes the single next action, and exits. It
never idles waiting on a human.

- **Two events launch it, both just "run the Scrum Master":** a merge to `main`
  (act only on an approved-but-unsequenced spec, or the last issue of a group;
  otherwise no-op), and a `/approve` comment on a plan's draft PR (spawn Build).
- **It judges outcomes.** Sequencing and Build are *awaited* subagents that
  return a **structured JSON result** (`done` / `escalated` / `blocked` + the
  problem); the SM branches on it — proceed, escalate, re-sequence. Planners are
  *fire-and-forget* (they wait on the human's `/approve`, so cannot be awaited).
- **Control flow lives in files and structured results, never labels:**
  `spec.md`'s `Status`, the existence of `build-order.md`, each plan's `Status`,
  and the returned JSON. Labels are demoted to human-facing markers.
- **Plan just-in-time:** launch Planners only for the current ready group, never
  the backlog (direction drifts; planning is expensive reasoning-class spend).

## Consequences
- **Easier:** the SM can *decide* — an escalated Build, a surfaced coupling, a
  filed defect are handled with judgment instead of dumped on the human. That
  judgment is the whole reason to pay for an orchestrator.
- **Easier:** resumability sidesteps the long-gate fragility — the orchestrator
  is never parked idle; each human gate is survived by re-invocation, and its
  memory is the repo, so a crash between events loses nothing.
- **Easier:** one uniform trigger surface (both Actions just launch the SM) and
  no labels in control flow — state you can read in the repo, not infer from a
  label's history.
- **Harder:** it reverses a stated principle and adds a reasoning layer with a
  token cost the mechanical reconciler didn't have. It also demands every
  subagent honor a **structured-result contract**, and demands the SM be written
  as a reconciler (act on state-as-it-is, never on "what just fired") so that
  firing on every merge stays safe.
- **Harder:** "orchestrator" is a slight misnomer — it is stateless across
  invocations, reconstructing position each time. A reader expecting a
  long-lived conductor will be surprised.
- **Forecloses:** the pure narrow-trigger, no-orchestrator model, and any design
  where a stage's outcome is judged only by a human rather than first by the SM.
