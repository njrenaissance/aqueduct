---
paths:
  - ".github/workflows/*.yml"
---

# GitHub Actions conventions

## Security

- Pin third-party actions to a full commit SHA, not a mutable tag like `@v4` — a moved or hijacked tag is a supply-chain attack vector. Keep a Dependabot/Renovate config updating those pins.
- Default to least-privilege `permissions:`. Set `contents: read` at the workflow level (as `ci.yml` and the reusable workflows already do) and only grant broader scopes (`pull-requests: write`, etc.) on the specific job that needs them.
- Never interpolate untrusted input (PR titles, branch names, issue bodies — anything from `github.event.*`) directly into a `run:` shell string; that's a script-injection vector. Pass it through `env:` and reference the env var instead.
- Avoid `pull_request_target` combined with checking out the PR's own head ref — it runs with write-level secrets against untrusted code.
- Prefer OIDC (`id-token: write` + the cloud provider's OIDC action) over long-lived credentials stored in secrets, for any future deploy workflow.

## Structure & composability

- Every workflow meant to be a building block declares `on: workflow_call:` (plus `workflow_dispatch:` for manual/ad-hoc runs) so it's independently runnable and composable into larger pipelines via `uses: ./.github/workflows/<name>.yml` — see `format-lint.yml`, `type-check.yml`, `unit-tests.yml` and how `ci.yml` assembles them.
- Use `concurrency:` with `cancel-in-progress: true` (grouped by `github.workflow`-`github.ref`) on workflows triggered by `pull_request`, so superseded pushes to the same PR don't keep burning runners on stale commits.
- Set `paths-ignore` (or `paths`) to skip runs irrelevant to the change, as `ci.yml` already does for docs.
- Set `timeout-minutes` on every job — an unbounded hung job silently burns CI minutes instead of failing loudly.

## Reliability

- Use a shallow checkout (`actions/checkout` default `fetch-depth: 1`) unless a job genuinely needs history.
- Cache dependencies (`enable-cache: true` on `astral-sh/setup-uv`, as done here) rather than reinstalling from scratch every run.
- Set explicit `retention-days` on uploaded artifacts instead of relying on the default.

## Governance

- Branch protection's required status checks must reference the exact job `name:` values (e.g. `Lint & Format`, `Type Check`, `Unit Tests`, `Integration Tests`) so `ci.yml` is actually enforced as a merge gate, not just advisory.
- `Template Sync` (`template-sync.yml`) is deliberately **not** a required status check and is intentionally *not* wired into `ci.yml`. It answers "is this project behind its template?" — a maintenance signal, not a "this PR is broken" signal — so it runs only on demand (`workflow_dispatch`) and must not gate merges. Do not add it to branch protection. Note that `cruft check` (which the job runs) compares commit SHAs, not rendered content — it reports "behind" whenever the template repo's tracked branch has any newer commit, even one that didn't change the template. That's fine for an on-demand advisory job (`cruft update` still applies only the real delta), but it is another reason this must never be a blocking gate.
- Don't add a new top-level workflow when the logic belongs in an existing reusable one — extend `format-lint.yml`/`type-check.yml`/`unit-tests.yml` or add a new reusable workflow and call it from `ci.yml`, keeping the "callable building block" structure intact. (`template-sync.yml` is the deliberate exception: it is standalone and on-demand precisely *because* it must stay off the PR gate.)
