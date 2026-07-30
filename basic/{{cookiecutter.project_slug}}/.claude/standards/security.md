# Security

- Secrets flow through the `Settings` class and nowhere else (see
  `configuration.md`) — required `SecretStr` fields loaded from `.env` /
  real environment variables, never hardcoded in source, never committed
  even in a "temporary" form.
- `.env.example` documents every required variable with a placeholder value.
  Never put a real secret in `.env.example`, a test fixture, or a code
  comment, even for a "just for local testing" convenience.
- `bandit` runs automatically as a pre-commit hook (`uv run bandit -r src`,
  wired through `.pre-commit-config.yaml`) to catch common vulnerability
  patterns — hardcoded passwords, `eval`/`exec` on untrusted input, insecure
  hashing (e.g. MD5/SHA1 for passwords), and similar. Run it by hand the same
  way when you want a check outside a commit. Treat a `bandit` finding as a bug
  to fix, not a false positive to suppress by default.
- Dependency hygiene is handled by `dependabot.yml`, which keeps pinned
  versions current automatically. Don't pin a dependency backward to dodge a
  Dependabot bump without first investigating why the bump broke something —
  an old pin is a security liability, not a fix.
- Validate all external input (CLI arguments, API request bodies, uploaded
  files, anything crossing a trust boundary) through a pydantic model at the
  point it enters the system. Don't operate on raw dicts or strings past
  that boundary — validation deferred to "wherever the value happens to be
  used" is validation that gets missed.
- Explicit bans, no exceptions without a documented reason in code review:
  - No `pickle`/`marshal` on data that didn't originate from this process.
  - No `subprocess(..., shell=True)` with any unsanitized input.
  - No string-formatted or concatenated SQL — use parameterized queries.

## Approved libraries

- `python-dotenv` — used transitively through `pydantic-settings`'
  `env_file` support (see `configuration.md`); not something to invoke
  directly.
- `bandit` — the only sanctioned static-analysis tool for this concern.
