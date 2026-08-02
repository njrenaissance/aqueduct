#!/usr/bin/env bash
#
# scaffold.sh — deterministic tail of the Scaffold skill.
#
# Renders a Cookiecutter template INTO the repository that the Create Repo phase
# already created and protected, then lands it on the protected `main` via a PR.
# It does NOT create the repo, seed labels, or protect `main` — those are Create
# Repo's job, done where GitHub-admin credentials exist. This runs in the repo
# checkout (Claude Code on the Web), which can render, branch, commit, push, and
# open a PR, but cannot perform repo/admin operations.
#
# The SKILL owns the judgment (template choice + writing cookiecutter-context.json
# with project_slug set to the repo's name). This script owns the mechanical rest.
#
# Structure: each step is a function; main() runs them in order. Sourcing this
# file (SCAFFOLD_SOURCED=1) defines the functions without running main().
#
# Usage (run from inside the repo checkout Create Repo made):
#   scaffold.sh --template-dir <path> --context-file cookiecutter-context.json \
#     [--branch scaffold] [--base main]
#
# Requires: cookiecutter, git, gh (authenticated), and either jq or python3.

set -euo pipefail

TEMPLATE_DIR=""
CONTEXT_FILE=""
BRANCH="scaffold"
BASE="main"

REPO_ROOT=""     # set by preflight(): the target repo's toplevel
SLUG=""          # set by derive_slug(): the repo name / expected project_slug

die()  { printf '\n\033[31mSCAFFOLD ABORTED:\033[0m %s\n' "$*" >&2; exit 1; }
note() { printf '\033[36m==>\033[0m %s\n' "$*"; }

# read_json <file> <key> — echo .cookiecutter[<key>] (empty if absent).
read_json() {
  if command -v jq >/dev/null 2>&1; then
    jq -r --arg k "$2" '.cookiecutter[$k] // empty' "$1"
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("cookiecutter",{}).get(sys.argv[2],""))' "$1" "$2"
  else
    die "need jq or python3 to read the context file"
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --template-dir) TEMPLATE_DIR="${2:?}"; shift 2;;
      --context-file) CONTEXT_FILE="${2:?}"; shift 2;;
      --branch)       BRANCH="${2:?}";       shift 2;;
      --base)         BASE="${2:?}";         shift 2;;
      -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
      *) die "unknown argument: $1";;
    esac
  done
}

preflight() {
  [[ -n "$TEMPLATE_DIR" ]] || die "--template-dir is required"
  [[ -n "$CONTEXT_FILE" ]] || die "--context-file is required"
  [[ -d "$TEMPLATE_DIR" ]] || die "template dir does not exist: $TEMPLATE_DIR"
  [[ -f "$TEMPLATE_DIR/cookiecutter.json" ]] || die "no cookiecutter.json in template dir: $TEMPLATE_DIR"
  [[ -f "$CONTEXT_FILE" ]] || die "context file does not exist: $CONTEXT_FILE"

  for bin in cookiecutter git gh; do
    command -v "$bin" >/dev/null 2>&1 || die "required tool not found on PATH: $bin"
  done

  # Must run inside the repo Create Repo made.
  REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
    || die "not inside a git repository — run from the repo Create Repo created"
  git -C "$REPO_ROOT" remote get-url origin >/dev/null 2>&1 \
    || die "repo has no 'origin' remote — expected the repo Create Repo created"
  gh auth status >/dev/null 2>&1 || die "gh is not authenticated (run: gh auth login)"

  if command -v jq >/dev/null 2>&1; then
    jq -e '.cookiecutter | type == "object"' "$CONTEXT_FILE" >/dev/null 2>&1 \
      || die "context file must be {\"cookiecutter\": { ... }}: $CONTEXT_FILE"
  fi
}

# derive_slug — the repo name is the source of truth for project_slug.
derive_slug() {
  local url ctx_slug
  url="$(git -C "$REPO_ROOT" remote get-url origin)"
  SLUG="$(basename -s .git "$url")"
  [[ -n "$SLUG" ]] || SLUG="$(basename "$REPO_ROOT")"
  note "Repo (project_slug): $SLUG"

  ctx_slug="$(read_json "$CONTEXT_FILE" project_slug || true)"
  if [[ -n "$ctx_slug" && "$ctx_slug" != "$SLUG" ]]; then
    die "context project_slug ($ctx_slug) != repo name ($SLUG) — the context must use the repo Create Repo made"
  fi
}

# render — cookiecutter into a temp dir, then lay the generated project into the repo.
render() {
  local tmp generated generated_name
  tmp="$(mktemp -d)"
  note "Rendering template into a temp dir"
  cookiecutter "$TEMPLATE_DIR" --output-dir "$tmp" --no-input --config-file "$CONTEXT_FILE"

  generated="$(find "$tmp" -mindepth 1 -maxdepth 1 -type d | head -n1)"
  [[ -n "$generated" ]] || die "cookiecutter produced no output directory"

  generated_name="$(basename "$generated")"
  if [[ "$generated_name" != "$SLUG" ]]; then
    die "rendered project folder ($generated_name) != repo name ($SLUG) — project_slug mismatch"
  fi

  note "Laying rendered project into $REPO_ROOT (overwriting the placeholder)"
  cp -R "$generated"/. "$REPO_ROOT"/
  rm -rf "$tmp"
}

# verify_render — sanity-check the tree and install the pre-commit hook.
verify_render() {
  cd "$REPO_ROOT"
  [[ -n "$(ls -A)" ]] || die "repo is empty after render"

  if [[ -f .pre-commit-config.yaml ]]; then
    if command -v pre-commit >/dev/null 2>&1; then
      note "Installing pre-commit hook"
      pre-commit install >/dev/null
    else
      note "WARNING: .pre-commit-config.yaml present but pre-commit not on PATH — hook not installed"
    fi
  else
    note "WARNING: no .pre-commit-config.yaml in render — template may not wire pre-commit"
  fi

  if ! ls .github/workflows/*.y*ml >/dev/null 2>&1; then
    note "WARNING: no CI workflow found under .github/workflows — template may not wire CI"
  fi
}

# branch_commit_push — main is protected, so the scaffold lands on a branch.
branch_commit_push() {
  cd "$REPO_ROOT"
  note "Creating branch '$BRANCH'"
  git switch -c "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH"
  git add -A
  git commit -q -m "Scaffold $SLUG from $(basename "$TEMPLATE_DIR")"
  git push -u origin "$BRANCH"
  note "Pushed branch '$BRANCH'"
}

# open_pr — the scaffold's path onto the protected base branch.
open_pr() {
  cd "$REPO_ROOT"
  note "Opening PR into $BASE"
  gh pr create --base "$BASE" --head "$BRANCH" \
    --title "Scaffold $SLUG from $(basename "$TEMPLATE_DIR")" \
    --body "Renders the $(basename "$TEMPLATE_DIR") template into $SLUG. Review and merge to land the scaffold on protected $BASE." \
    || die "gh pr create failed — the branch is pushed; open the PR into $BASE manually"
}

main() {
  parse_args "$@"
  preflight
  derive_slug
  render
  verify_render
  branch_commit_push
  open_pr

  printf '\n\033[32mSCAFFOLD PR OPENED\033[0m — %s on branch %s → %s. Human reviews and merges to complete.\n' "$SLUG" "$BRANCH" "$BASE"
}

# Run main() unless sourced (SCAFFOLD_SOURCED=1) for testing individual steps.
if [[ "${SCAFFOLD_SOURCED:-0}" != "1" ]]; then
  main "$@"
fi
