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
```

## Git hooks

Local quality gates are installed automatically when the project is generated
(via `.pre-commit-config.yaml` + the [`pre-commit`](https://pre-commit.com/)
framework). `git commit` runs `ruff format --check`, `ruff check`, and
`mypy src`{{ " (plus `bandit -r src`)" if cookiecutter.security == "yes" else "" }};
`git push` runs `pytest`. A failing hook is the same signal `ci.yml` would give,
just earlier. If the hooks were not installed (e.g. Git wasn't available at
generation time), install them with
`uv run pre-commit install && uv run pre-commit install --hook-type pre-push`.

## Conventions

All code must follow Clean Code principles (Robert C. Martin) — no exceptions.

Where applicable, apply the 23 Gang of Four design patterns (*Design Patterns: Elements of Reusable Object-Oriented Software*) rather than ad-hoc structures:

- **Creational**: Abstract Factory, Builder, Factory Method, Prototype, Singleton
- **Structural**: Adapter, Bridge, Composite, Decorator, Facade, Flyweight, Proxy
- **Behavioral**: Chain of Responsibility, Command, Interpreter, Iterator, Mediator, Memento, Observer, State, Strategy, Template Method, Visitor

Don't force a pattern where a plain function or class is simpler — use these to name and structure a design once the problem actually calls for one.

Python- and test-specific conventions live in `.claude/rules/` (`python-lang.md`, `pytest-rules.md`) and load automatically when Claude touches matching files.

Run `uv run pytest`, `uv run ruff check .`, and `uv run mypy src` before considering a change done — the installed Git hooks (see **Git hooks** above) enforce the same checks at commit/push time, so don't bypass them with `--no-verify`.
