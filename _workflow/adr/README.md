# Architecture Decision Records

Why we made the choices we did about the coding workflow itself. These
record decisions about **this repo's design** — the factory — not about any
project the factory builds.

## Convention

We follow the same format this repo's own [`adr-authoring`
skill](../skills/adr-authoring/SKILL.md) prescribes for scaffolded projects —
dogfooding it here:

- **Format:** `# ADR-000N: <title>` with **Status / Context / Decision /
  Consequences** (see [`template.md`](template.md)).
- **When to write one:** only when the decision is *expensive to reverse* —
  "if this was the wrong call, is undoing it a small edit or a rewrite?" A
  small edit → just note it in `coding-workflow.md`. Rewrite-scale → ADR.
- **Numbering:** sequential from `0001`; never reuse a number. A decision
  rejected during discussion still takes its number with `Status: rejected`.
- **One decision per file**, `000N-lowercase-slug.md`, each its own commit.

**Location note:** factory ADRs live here in `adr/` because this repo has no
`spec/spec.md` — its spec is `../coding-workflow.md`. Projects the factory
*scaffolds* put their ADRs in `spec/adr/` per the skill; this is the one place
the location differs.

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-two-execution-surfaces.md) | Two execution surfaces matched to human-in-the-loop | accepted |
| [0002](0002-plan-as-persisted-artifact.md) | The plan is a persisted artifact, not a conversation | accepted |
| [0003](0003-split-hook-strategy.md) | Split hooks — pre-commit vs pre-push | accepted |
| [0004](0004-deploy-evaluate-deterministic-ci.md) | Deploy to Dev + Evaluate is deterministic CI, not an agent | accepted |
| [0005](0005-inner-plan-stays-interactive.md) | Inner Plan stays an interactive HITL session | superseded by [0009](0009-async-planner.md) |
| [0006](0006-scaffold-and-spec-planning-separate-sessions.md) | Scaffold and Spec Planning are separate sessions | accepted |
| [0007](0007-test-integrity-hash-guard.md) | Test-integrity hash-guard on pre-push | accepted |
| [0008](0008-scrum-master-orchestrator.md) | The build phase is driven by a resumable Scrum Master orchestrator | accepted |
| [0009](0009-async-planner.md) | Inner Plan is async — a Planner agent drafts, the human approves with `/approve` | accepted |
