# Logging

- Whether structured logging is required depends on the `structured_logging`
  flag chosen when the project was created (`cookiecutter.json`). If it was
  set to `yes`, use `structlog` (see Approved library below). If it's `no`,
  the standard library's `logging` module is an acceptable alternative —
  don't add `structlog` as a dependency just to satisfy this standard on a
  project that has no downstream consumer for structured logs.
- Regardless of which library is in use, avoid bare `print()` for anything
  but a script's final human-facing output — it can't be leveled, filtered,
  or routed like a real log call.

## When using `structlog`

- Configure logging exactly once, at the application entrypoint, via a
  `configure_logging()` function. Every other module just does
  `logger = structlog.get_logger(__name__)` and logs — it never reconfigures
  processors, renderers, or output streams itself.
- Log key-value pairs, not interpolated strings:
  `logger.info("user_created", user_id=user.id)`, not
  `logger.info(f"User {user.id} created")`. The structured form stays
  machine-parseable and greppable/queryable once logs land in any
  aggregation system; an f-string is opaque prose the moment it's written.
- In production, render logs as JSON (`structlog`'s JSON renderer); in local
  dev, render to a human-readable console format. Switch on
  `Settings.environment` (see `configuration.md`) rather than hardcoding a
  renderer, so the same code produces the right output in both contexts.
- Wire `structlog` through stdlib `logging` (`structlog.stdlib.ProcessorFormatter`
  as the final processor, per `structlog`'s "Rendering Using structlog-based
  Formatters within logging" recipe) rather than a standalone
  `ConsoleRenderer`/`PrintLoggerFactory` writing straight to stdout.
  `pytest`'s log capture — including the `pytest -v` live output described
  in `testing.md` — and any other stdlib-`logging`-based tooling only see
  log records that pass through the stdlib `logging` module; a
  `structlog` setup that bypasses it produces output nothing else can
  capture.

## Regardless of library

- Level guidance:
  - `DEBUG` — dev-time tracing, verbose internal state
  - `INFO` — normal operational events (request handled, job completed)
  - `WARNING` — recoverable issues worth noticing (retry succeeded, fallback used)
  - `ERROR` — a failure that needs attention
- Log exceptions with `logger.exception(...)` — it preserves the full stack
  trace, unlike `logger.error(str(e))`, which throws it away.
- Never log secrets or PII directly. If a value might be sensitive, redact
  it with a processor (or, for stdlib `logging`, a `Filter`) or omit the
  field — don't rely on remembering to scrub it at each call site.

## Approved library

- `structlog` — the sanctioned library when structured logging is required.
  Do not reach for a different structured-logging package (e.g. `loguru`)
  without raising it as a standards change first.
