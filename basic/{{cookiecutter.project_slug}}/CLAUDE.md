# {{ cookiecutter.project_name }}

Minimal Python project managed with [uv](https://docs.astral.sh/uv/).

## Profile

Cross-cutting concerns enabled for this project:

- App config (`pydantic-settings`): {{ "enabled" if cookiecutter.app_config == "yes" else "disabled" }}
- Structured logging (`structlog`): {{ "enabled" if cookiecutter.structured_logging == "yes" else "disabled" }}
- Telemetry (OpenTelemetry): {{ "enabled" if cookiecutter.telemetry == "yes" else "disabled" }}
- Security scanning (`bandit`): {{ "enabled" if cookiecutter.security == "yes" else "disabled" }}

## Imports

- @.claude/standards/git-workflow.md
- @.claude/standards/decisions.md
- @.claude/standards/wiki.md
- @.claude/standards/testing.md
- @.claude/standards/error-handling.md
- @.claude/standards/database.md
{%- if cookiecutter.app_config == "yes" %}
- @.claude/standards/configuration.md
{%- endif %}
- @.claude/standards/logging.md
{%- if cookiecutter.telemetry == "yes" %}
- @.claude/standards/telemetry.md
{%- endif %}
{%- if cookiecutter.security == "yes" %}
- @.claude/standards/security.md
{%- endif %}

## Structure

```text
├── src/
│   └── main.py       # greet()
├── tests/
│   └── test_main.py  # test for greet()
└── pyproject.toml
```

## Commands

```bash
uv sync                    # install dependencies
uv run python src/main.py  # run
uv run pytest              # test
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy src            # type-check
uv run pre-commit run --all-files  # run all Git hooks manually
uvx cruft check            # is this project behind its template?
```

## Git hooks

Local quality gates run through the [`pre-commit`](https://pre-commit.com/)
framework (config in `.pre-commit-config.yaml`). Generation installs nothing;
install the hooks once, from inside the project:

```bash
uv run pre-commit install
uv run pre-commit install --hook-type pre-push
```

Then `git commit` runs `ruff format --check`, `ruff check`, and
`mypy src`{{ " (plus `bandit -r src`)" if cookiecutter.security == "yes" else "" }};
`git push` runs `pytest`. A failing hook is the same signal `ci.yml` would give,
just earlier. (The hooks are not auto-installed at generation on purpose —
`pre-commit install` bakes machine-specific paths into `.git/hooks/` that would
break `cruft update`; see **Template sync**.)

## Template sync

This project is linked to the cookiecutter template it was generated from via
`.cruft.json` (the template URL, the exact template commit, and the answers given
at generation). `.cruft.json` is the authoritative record of template lineage —
there is no separate version file. `uvx cruft check` reports whether the template
has moved ahead; the on-demand `Template Sync` workflow
(`.github/workflows/template-sync.yml`) runs the same check in CI when you trigger
it. To pull template changes in, use the **`update-from-template`** skill
(`.claude/skills/update-from-template/`), which runs `cruft update`, resolves any
conflicts, and re-runs the gate above. A project that somehow lost its `.cruft.json`
can re-establish the link with the **`link-to-template`** skill.

## Conventions

All code must follow Clean Code principles (Robert C. Martin) — no exceptions.

Where applicable, apply the 23 Gang of Four design patterns (*Design Patterns: Elements of Reusable Object-Oriented Software*) rather than ad-hoc structures:

- **Creational**: Abstract Factory, Builder, Factory Method, Prototype, Singleton
- **Structural**: Adapter, Bridge, Composite, Decorator, Facade, Flyweight, Proxy
- **Behavioral**: Chain of Responsibility, Command, Interpreter, Iterator, Mediator, Memento, Observer, State, Strategy, Template Method, Visitor

Don't force a pattern where a plain function or class is simpler — use these to name and structure a design once the problem actually calls for one.

Python- and test-specific conventions live in `.claude/rules/` (`python-lang.md`, `pytest-rules.md`) and load automatically when Claude touches matching files.

Run `uv run pytest`, `uv run ruff check .`, and `uv run mypy src` before considering a change done — the installed Git hooks (see **Git hooks** above) enforce the same checks at commit/push time, so don't bypass them with `--no-verify`.
