# Architecture Decisions

- When you make a **significant or hard-to-reverse** architecture decision,
  record it as an ADR (Architecture Decision Record) — use the `adr` skill if
  it's available in the environment. "Significant" means a choice that shapes
  the codebase's structure and would be expensive to unwind later: picking a
  storage engine or framework, defining a module/service boundary, choosing a
  concurrency or error-handling model, adopting (or banning) a dependency for a
  whole subsystem. It is *not* for routine, easily-reversed choices — a
  variable name, a local refactor, which helper a single function calls.
- The test is reversibility and blast radius, not size: a small change that
  locks in a direction others will build on top of is worth an ADR; a large
  but self-contained change that any future PR could redo differently is not.
- One ADR per decision. Capture the context that forced the choice, the
  options weighed, the decision, and its consequences (including what it rules
  out) — enough that someone six months later understands *why*, not just
  *what*. The `adr` skill owns the exact format, numbering, and file location;
  this standard owns only *when* a decision earns one.
- ADRs are append-only history, not living docs. When a later decision
  overturns an earlier one, write a new ADR that supersedes it and mark the old
  one superseded — never rewrite or delete the original, since the record of
  *why the direction changed* is the point.
- If the `adr` skill isn't present, don't skip the record — fall back to a
  short dated Markdown note under `docs/adr/` (`NNNN-short-title.md`) carrying
  the same context/options/decision/consequences, and raise adding the skill as
  a follow-up.
