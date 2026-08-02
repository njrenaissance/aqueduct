# Managed agents — operational reference

Definitions for the automated stages that run as Claude **Managed Agents**. The
**Scrum Master** orchestrator (`scrum-master.yml`) is itself a Managed Agent,
re-invoked per event (see `../coding-workflow.md`), and spawns the other three:
`outer-sequencing.yml`, `inner-plan.yml` (the Planner), and `inner-build.yml`.
Sequencing and Build are *awaited* subagents whose structured result the Scrum
Master judges; the Planner is launched *fire-and-forget* (it waits on the
human's async `/approve`).

The interactive HITL stages — Scaffold, Spec Planning, Inner Review — run as
Claude Code sessions. Inner Plan is split: the Planner here drafts it, and the
human's part is a lightweight async `/approve` on the draft PR (no live session;
see `../runbooks/reviewing-a-plan.md`).

## Custom skill IDs

Managed agents reference custom skills by an uploaded `skill_id`, resolved
from the **workspace registry at runtime** — not inlined, not by repo name.
These IDs are workspace-specific and must match the workspace the agent runs
in.

| Skill | `skill_id` | Consumed by |
|---|---|---|
| `issue-decomposition` | `skill_01TR1xtctooMQ8b9TBYHQ45J` | `outer-sequencing.yml` |

**Only `issue-decomposition` is a managed-agent skill.** The other workflow
skills — `scaffold`, `spec-authoring`, `adr-authoring` — belong to HITL stages
that run as Claude Code sessions, invoked as slash commands (`/scaffold`,
`/spec-authoring`, `/adr-authoring`) in **Claude Code on the Web**. Claude Code
resolves skills by name, not by a managed-agent `skill_id`, so none of them
need an entry in the table above. (`scaffold` was also uploaded to the
workspace as `skill_01DqDZEtcv3SgwSTCwLLFqxS`, but nothing in the managed-agent
path consumes it.)

To re-list workspace skills and their IDs:

```bash
curl -sS https://api.anthropic.com/v1/skills \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "anthropic-beta: skills-2025-10-02" \
| jq -r '.data[] | [.id, (.display_title // "")] | @tsv'
```

## Launching manually

Per-run context is not a field in these YAMLs — it arrives at session start:

- **Repository** — attached as a `github_repository` **Resource** (cloned at
  the repo's **default branch** only; there is no branch/ref field, so an
  agent that needs another branch checks it out itself).
- **Kickoff instruction** — an **initial `user.message` event** (there is no
  "Title" field for this).

Kickoff templates. In production the **Scrum Master** sends these when it spawns
each subagent; they are also how to launch one by hand for validation:

- **Outer Sequencing** — attach repo at `main`:
  > Sequence the approved spec at `spec/spec.md`. Create the issues, analyze
  > dependencies, commit `spec/build-order.md`, and return a structured result.
  > Do not use labels for control flow.
- **Inner Plan (Planner)** — attach repo (clones `main`); name the issue:
  > Plan issue #\<N>. Draft `spec/issues/00N-plan.md` (acceptance criteria +
  > test list on top), author the red tests and `spec/issues/00N-tests.lock`,
  > `[red]` push with `--no-verify`, open a **draft** PR into `main`, and
  > request review. Then halt.
- **Inner Build** — attach repo (clones `main`); name the branch in the message:
  > Build the issue planned on branch `issue-<N>` (#\<N>). Plan and red tests
  > are committed under `spec/issues/` and `tests/`. Get to green, run the
  > code-review subagent, mark the **existing draft PR ready**, and return a
  > structured result.
