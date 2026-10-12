# Configuration

- All configuration lives under one `Settings(BaseSettings)` class (e.g.
  `src/<pkg>/settings.py`) built on `pydantic-settings` — no scattered
  `os.environ[...]`/`os.getenv(...)` calls anywhere else in the codebase. One
  source of truth means an agent (or a human) can find every configurable
  value by reading a single file instead of grepping the whole tree.
- Group related settings into their own nested `BaseSettings` subclass (e.g.
  `DatabaseSettings`) instead of flattening everything onto the top-level
  `Settings` class — it keeps large configs readable and each concern's
  fields easy to find. Attach it as a field with a `default_factory` so the
  nested class parses its own slice of the environment independently:

  ```python
  class DatabaseSettings(BaseSettings):
      model_config = SettingsConfigDict(env_prefix="MYAPP__DATABASE_")

      somesetting: str

  class Settings(BaseSettings):
      model_config = SettingsConfigDict(env_prefix="MYAPP_", env_file=".env")

      database: DatabaseSettings = Field(default_factory=DatabaseSettings)
  ```

- Env var names for a nested section use **exactly one** double underscore
  (`__`) — marking the boundary between the app prefix and the section name
  — then a single underscore to join the section name to each field, the
  same way an ordinary multi-word field name would be joined. Given the app
  prefix `MYAPP_` and `DatabaseSettings.somesetting` above, that's
  `MYAPP__DATABASE_SOMESETTING` — not `MYAPP_DATABASE_SOMESETTING` (no
  marked boundary, so it's unclear where the app prefix ends and the
  section begins) and not `MYAPP__DATABASE__SOMESETTING` (a double
  underscore at every join makes a field indistinguishable from a further
  nested subsection at a glance). Reserve `__` exclusively for the
  app-prefix-to-section boundary; every other join stays a single `_`.
- Local development loads variables from a `.env` file via
  `model_config = SettingsConfigDict(env_file=".env")`; real environment
  variables (CI, prod) always take precedence over `.env` automatically —
  no separate prod-vs-dev branching logic is needed.
- JSON config files are also an acceptable settings source — enable one via
  `model_config = SettingsConfigDict(json_file="config.json")` plus a
  `settings_customise_sources` override that includes
  `JsonConfigSettingsSource` (see `pydantic-settings`' docs on other file
  formats). Reach for this when a project already ships structured JSON
  config from elsewhere (deploy tooling, a shared config service) rather
  than duplicating those values into `.env`; `.env` stays the default for
  ordinary local development.
- Secrets (API keys, DB passwords, tokens) are **required** fields with no
  default, typed as `pydantic.SecretStr`. A required field with no default
  makes a missing secret a startup-time crash instead of a silent `None`
  that fails mysteriously three calls later. `SecretStr` keeps the value out
  of `repr()`, tracebacks, and accidental logging.
- Keep every non-secret default value in one JSON-serializable dict defined
  once in the settings module (e.g. `DEFAULTS: dict[str, Any] = {...}`)
  rather than scattering literals across each field's `default=`. One
  object is easy to scan at a glance, and tests can override just the
  fields they care about without touching env vars or files:
  `Settings(**{**DEFAULTS, "database": {"somesetting": "test-value"}})`.
  Every field must still be type-annotated so pydantic validates it at load
  time (e.g. `AnyUrl` for URLs, `int` for ports) rather than failing deep
  inside business logic with a confusing error.
- Instantiate `Settings` exactly once — either a module-level singleton or a
  `@functools.lru_cache`-wrapped `get_settings()` factory — and import that
  instance everywhere. Re-instantiating per call site re-parses and
  re-validates the environment on every use for no benefit.
- Check in a `.env.example` listing every variable the project reads, each
  with a placeholder value — never a real secret. This is how a new
  contributor (or agent) discovers what configuration exists without
  reading the settings module line by line.
- `.env` itself must stay git-ignored (already the case in this template's
  `.gitignore`).

## Approved library

- `pydantic-settings` — the only sanctioned way to read configuration. Do
  not introduce `python-decouple`, `dynaconf`, hand-rolled `configparser`
  usage, or similar; if a project genuinely needs something
  `pydantic-settings` can't do, raise it as a standards change rather than
  substituting a library locally.
