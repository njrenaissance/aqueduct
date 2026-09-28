# ADR-0001: Two execution surfaces matched to human-in-the-loop

## Status
accepted

## Context
The workflow has two kinds of stage with fundamentally different interaction
needs. Some require live human judgment — Scaffold, Spec Planning, Inner Plan,
Inner Review — where a person and Claude iterate together. Others run fully
unattended — Sequencing and Inner Build — where no human is present during the
work. We needed one consistent answer for *where* each kind of stage runs,
under two hard constraints: we operate no infrastructure of our own to host
agents, and the human-facing stages must be reachable from any device,
including a phone.

## Decision
HITL stages run as **Claude Code sessions** (interactive, using account-level
skills invoked as slash commands, reachable from any device including Claude
Code on the Web). Automated stages run as **Claude Managed Agents**. Neither
surface requires infrastructure we provision or operate.

## Consequences
- **Easier:** each stage uses the surface that fits its interaction model —
  interactive editing/test-running for HITL, unattended remote execution
  against a mounted checkout for automated stages. Mobile/async human review
  comes for free via Claude Code on the Web, with no extra tooling.
- **Harder — no shared memory across the boundary:** a Managed Agent is a
  different system from the Claude Code session that precedes it, with no
  access to that session's conversation. This forces plans to be **persisted
  artifacts** (Inner Plan commits the plan, tests, and stub to the branch so
  Inner Build can read them off the checkout) rather than handed over in
  context. See ADR on the plan-as-artifact decision.
- **Harder — divergent operational models:** the Managed Agent surface has
  constraints the HITL side doesn't share and that only surfaced when we ran
  it end-to-end — the repo Resource clones the *default branch only* (agents
  must check out their target branch themselves), custom skills are referenced
  by uploaded `skill_id` rather than by name, and per-run context arrives via
  an initial `user.message`, not a title field. Two surfaces means two sets of
  operational rules to maintain (see `managed-agents/README.md`).
- **Forecloses:** a single unified agent runtime spanning both interactive and
  automated stages. If a future stage needs to be *both* interactive and
  unattended, neither surface fits cleanly and this split would need
  revisiting.
