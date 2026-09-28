# ADR-0007: Test-integrity hash-guard on pre-push

## Status
accepted

## Context
Inner Plan authors the executable TDD tests and commits them red; Inner Build
implements until the suite is green. Those same tests are simultaneously the
*specification* (what Build builds against) and the *acceptance oracle* (the
pre-push gate that declares the work done). The agent being graded therefore
owns the answer key: nothing structural stops Inner Build from deleting an
assertion or loosening a boundary in a test file it was handed, and the
pre-push suite still goes green. That is a silent false-green — the one failure
the whole determinism-first design is meant to preclude — and "did the diff
quietly weaken a test" is exactly what is easy to miss in human Review.

We considered making the plan+tests their own PR that merges *before* Build, so
the tests become an authoritative baseline Build provably can't rewrite. It was
rejected: Plan's tests are deliberately red, so they can't merge to a green,
protected `main` without breaking Deploy+Evaluate and every parallel issue's
Build; and routing them through a per-issue base branch to keep `main` green
adds a **second formal human review per issue**. The human reviewer is already
the factory's throughput ceiling ([ADR-0005](0005-inner-plan-stays-interactive.md)),
and spending more of that resource to buy integrity we can get deterministically
for free is a bad trade.

## Decision
Inner Plan pins the red tests. In the same `[red]` commit that carries the plan
and stub, Plan writes a per-issue lock — `spec/issues/00N-tests.lock`, mapping
each committed test file to its `git hash-object` blob SHA. The **pre-push
hook** verifies *every* `spec/issues/*-tests.lock` against the working tree
before running the suite: if any locked test file is missing or its blob no
longer matches, the push is rejected. Because it checks all locks, it protects
the whole accumulated, plan-approved test corpus on `main`, not just the current
issue's.

Inner Build (unattended) has **no override**. If Build concludes a test itself
is wrong, that is a plan problem, not a code problem — it halts and escalates
down the existing 3-retry `build:escalated` path rather than editing the test.
Adding new test files is unaffected (they aren't in any lock); only weakening or
deleting a plan-approved test is blocked.

The lock *is* the override. Changing a locked test requires re-running the pin
step so the lock records the new blob — a deliberate, reviewable act, never
silent, in the same spirit as the two documented `--no-verify` exceptions
([ADR-0003](0003-split-hook-strategy.md)). Only a human does this, during a Plan
re-visit or a Review fix; the re-pin and the changed test land together in a
diff Review sees.

Reference — the pin helper Plan runs, and the guard the hook runs:

```bash
# tests-lock.sh <issue-number> — Plan runs this after authoring the red tests.
# Writes spec/issues/00N-tests.lock pinning every current test file's blob SHA.
n=$(printf '%03d' "$1"); lock="spec/issues/${n}-tests.lock"
{ echo "# issue ${1} test lock — do not edit by hand; regenerate via tests-lock.sh"
  git ls-files tests | while read -r f; do printf '%s %s\n' "$(git hash-object "$f")" "$f"; done
} > "$lock"

# pre-push guard — runs before the full suite; rejects any weakened/deleted lock entry.
rc=0
for lock in spec/issues/*-tests.lock; do
  [ -e "$lock" ] || continue
  while read -r sha path; do
    case "$sha" in \#*|'') continue;; esac
    if [ ! -f "$path" ]; then echo "TEST-GUARD: locked test deleted: $path ($lock)"; rc=1
    elif [ "$(git hash-object "$path")" != "$sha" ]; then
      echo "TEST-GUARD: locked test modified since its plan: $path ($lock)"; rc=1
    fi
  done < "$lock"
done
[ "$rc" -eq 0 ] || { echo "To change a plan-approved test, re-plan and re-pin (tests-lock.sh); do not edit it here."; exit 1; }
```

## Consequences
- **Easier:** the false-green a self-graded oracle allows is closed
  deterministically — by `git hash-object`, not by trusting the agent or the
  human reviewer to notice a weakened assertion.
- **Easier:** no new human-in-the-loop. Build reuses the existing escalation
  path when it thinks a test is wrong; the guard is a hook, not a review stage.
- **Easier:** checking all locks means a later issue can't quietly weaken an
  earlier issue's merged tests either — the guard grows with the corpus.
- **Harder:** a genuinely-wrong test now costs an escalation/re-plan round trip
  instead of an inline fix by Build. This is intended friction — a test change
  *should* be deliberate — but it is a real cost when Plan simply got a test
  wrong.
- **Harder:** one more artifact per issue (`00N-tests.lock`) and a pin step Plan
  must not forget; a missing lock silently protects nothing for that issue.
- **Bypass, bounded:** `--no-verify` still skips the guard, but only the two
  documented exceptions use it, and neither reaches `main` — Plan's red push
  establishes the baseline, and Build's escalation push halts for human review.
  So the guard can't be silently circumvented on any path to `main`.
- **Forecloses:** Inner Build ever legitimately editing the tests it builds
  against. That authority now lives only in Plan (and human Review), which is
  where authoring the oracle belongs.
