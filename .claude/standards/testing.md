# Testing

- Tests are written and agreed before implementation — per the Plan phase of
  this project's agent workflow, the approved unit tests are what "done"
  means for a change. Implementation is written to satisfy tests that
  already exist, not the other way around.
- Favor many fast `unit`-marked tests over a few slow `integration`-marked
  ones. Reserve `integration` tests for the boundaries that genuinely can't
  be safely faked (a real database, a real network call, the filesystem) —
  everything else should be reachable with a unit test.
- CI runs the two suites in separate stages (see `.github/workflows/`):
  `unit-tests` runs `pytest -m "not integration"`, and a dedicated
  `integration-tests` stage runs `pytest -m integration` after standing up its
  backing services. That stage self-detects whether any `integration`-marked
  tests exist — with none, it goes green without starting anything. But the
  moment you add one, **CI expects a `docker-compose.yml` (or `compose.yaml`)
  at the project root** to bring those services up, and the stage fails loudly
  if it's missing rather than silently skipping. So adding an integration test
  and committing the compose file that stands up its services are one change,
  not two.
- Use `pytest-mock`'s `mocker` fixture for mocking, not manual
  `unittest.mock.patch` decorators or context managers — it's less
  boilerplate and composes better with other fixtures.
- Mock only true external boundaries (network, filesystem, time, randomness,
  a third-party API). Never mock the unit under test itself, and never mock
  an internal collaborator just to avoid constructing it — if a class is
  hard to construct for a test, that's a design smell to fix, not a reason
  to mock it away.
- The coverage floor enforced in `[tool.coverage.report]` (see
  `.claude/rules/pytest-rules.md` for the current threshold) applies to the
  whole `src/` tree, not a per-file target — critical or complex logic
  should be covered well above it, and trivial glue code isn't worth
  contorting a test to hit a number.
- `tests/conftest.py` enables live log output at `INFO` automatically when
  running `pytest -v` (via a `pytest_configure` hook that sets
  `log_cli_level` whenever `--verbose` is passed and no explicit
  `--log-cli-level` was given) — plain `pytest` stays quiet. This is the
  fast path to seeing what a test actually logged without digging through
  the failure-only "Captured log call" section; don't disable it by hand,
  and don't reach for `-s`/`print()` debugging when `pytest -v` already
  shows this. If the project uses `structlog` (see `logging.md`), its
  output only appears here once `structlog` is wired through stdlib
  `logging` — otherwise this hook has nothing to show.
- Test-file conventions — directory layout, `unit`/`integration` markers,
  `@pytest.mark.parametrize` style, naming — are covered in
  `.claude/rules/pytest-rules.md`, which loads automatically whenever a test
  file is touched. This document is the cross-cutting "why," that one is the
  file-level "how" — don't duplicate one into the other.

## Approved libraries

- `pytest`
- `pytest-cov` — coverage measurement, enforced via `[tool.coverage.report]`
- `pytest-mock` — mocking, in place of stdlib `unittest.mock` used directly
