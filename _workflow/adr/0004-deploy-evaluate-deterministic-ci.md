# ADR-0004: Deploy to Dev + Evaluate is deterministic CI, not an agent

## Status
accepted

## Context
The workflow had no end-to-end acceptance testing of the *running*
application. We considered an adversarial "Evaluator" Managed Agent that
invents tests to try to break the app. But Determinism-first is the workflow's
top principle, and an LLM inventing tests at runtime is nondeterministic;
worse, letting an agent improvise infrastructure changes against real cloud
infra is a real cost and blast-radius risk. We needed acceptance testing
without surrendering determinism or standing up an autonomous agent on the
critical path.

## Decision
Deploy to Dev + Evaluate is realized as a **deterministic GitHub Actions CI
pipeline** — a container that applies the project's declared IaC recipe,
deploys to an isolated dev environment, and runs the **committed** E2E suite —
triggered on merge to `main`, single-flight per environment (coalescing to
latest `main`), non-blocking for Build, and gating Deliver. It runs only what
is already committed. The autonomous adversarial Evaluator agent is **cut** from
the MVP. Adversarial test generation, when wanted, is a human-invoked
`/generate-e2e-tests` skill whose output is curated and committed. A standing
Evaluator agent is **deferred** until all three hold: (1) a genuinely large
adversarial input space, (2) an oracle tight enough that findings need no human
triage, and (3) evidence the deterministic suite is actually missing defects.

## Consequences
- **Easier:** the gate is fully deterministic and replayable; no agent
  improvises infrastructure. Standard CD primitives do the work — concurrency
  groups for single-flight, required status checks for the Deliver gate,
  auto-filed issues for defects — with nothing bespoke to operate.
- **Easier:** generative LLM work stays off the critical path (invent up front,
  run only committed scripts), which is what keeps the pipeline deterministic.
- **Harder:** no continuous autonomous discovery of novel edge cases; coverage
  grows manually (spec-derived acceptance tests plus the human-invoked skill).
- **Harder:** requires the application be deployable and directable via
  declared config, isolated from prod data — a design-for-testability
  constraint this stage pushes back into Spec Planning and its ADRs.
- **Harder:** coalesced async evaluation attributes a red run coarsely
  ("changes since last green"); localizing may need a bisect over the
  deterministic suite.
- **Forecloses (until the three conditions hold):** an always-on adversarial
  agent as part of the pipeline.
