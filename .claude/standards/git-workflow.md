# Git Workflow

- **Branching**: Create a new branch for each issue/task
  - Branch naming: `issue-{number}` or `feature/{short-description}`
  - Example: `issue-1`, `feature/observability-setup`
  - Keep branches short-lived and scoped to one change; rebase/update from `main` rather than letting a branch drift far behind it.
- **Integration**: All code changes must be merged via pull request
  - Never rewrite `main` history — no force-push, no `reset --hard`, no amending commits that are already on `main`.
  - If you need to fix something already merged to `main`, open a new branch and PR rather than editing `main` directly, even for "small" fixes.
  - Every PR must pass `ci.yml` (lint, type-check, unit-tests — see `.github/workflows/`) before merging. Don't bypass required checks.
  - PRs require clear commit history demonstrating incremental progress
  - Include reference to related issue in PR description
  - Avoid squash-merging; preserve commit history through the merge
- **Commit Messages**: Use [Conventional Commits](https://www.conventionalcommits.org/)
  - Format: `<type>: <description>`
  - Types: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `revert:`
  - Example: `feat: Add infrastructure provisioning API endpoint`
  - Include detailed explanation in commit body when needed
  - End with a co-author trailer identifying the assisting model, e.g. `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` (use whichever model authored the change)
