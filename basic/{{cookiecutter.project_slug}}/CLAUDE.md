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
```

## Conventions

All code must follow Clean Code principles (Robert C. Martin) — no exceptions.

Where applicable, apply the 23 Gang of Four design patterns (*Design Patterns: Elements of Reusable Object-Oriented Software*) rather than ad-hoc structures:

- **Creational**: Abstract Factory, Builder, Factory Method, Prototype, Singleton
- **Structural**: Adapter, Bridge, Composite, Decorator, Facade, Flyweight, Proxy
- **Behavioral**: Chain of Responsibility, Command, Interpreter, Iterator, Mediator, Memento, Observer, State, Strategy, Template Method, Visitor

Don't force a pattern where a plain function or class is simpler — use these to name and structure a design once the problem actually calls for one.

Python- and test-specific conventions live in `.claude/rules/` (`python-lang.md`, `pytest-rules.md`) and load automatically when Claude touches matching files.

Run `uv run pytest`, `uv run ruff check .`, and `uv run mypy src` before considering a change done.
