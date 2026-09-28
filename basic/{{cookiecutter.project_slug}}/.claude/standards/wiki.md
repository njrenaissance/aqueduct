# Codebase Wiki

- Every project keeps an `openwiki/` folder of generated codebase
  documentation, produced by OpenWiki — an LLM-powered tool that writes
  structured Markdown describing the code (module boundaries, architecture,
  workflows). Read the relevant wiki pages for context at the start of a
  task instead of re-deriving how the codebase fits together from scratch.
- `openwiki/` is **generated output — never hand-edit it.** The source of
  truth is always the code. If a wiki page is wrong, the fix is in the code
  (or the code's own comments/docstrings), followed by regenerating the
  wiki — not an edit to the Markdown, which the next regeneration would
  silently overwrite.
- **Regenerate before committing.** When a change alters code that the wiki
  describes, regenerate the wiki with `openwiki code --update` and stage the
  updated `openwiki/` alongside that change, so the wiki lands in the same
  pull request and never drifts from `main`. Treat a stale `openwiki/` the
  same as a stale test — part of the change isn't done until it's updated.
- Regenerating calls a paid LLM provider and needs one-time setup
  (`npm install -g openwiki`, then `openwiki auth <provider>`). Install and
  auth instructions live in the project `README`; `openwiki` is a per-machine
  global CLI, **not** a project dependency, and must never be added to
  `pyproject.toml`.
- Automating regeneration in CI (a workflow that rebuilds the wiki and opens
  a PR) is a deliberate future step, deferred for now — until then, keeping
  `openwiki/` current is the committer's manual responsibility per the rule
  above.
