# Error Handling

- Define one project-specific exception hierarchy rooted at a single base
  class (e.g. `class AppError(Exception)` in `src/<pkg>/errors.py`). Raise
  and catch domain-specific subclasses of it — never raise or catch bare
  `Exception`, which makes it impossible for a caller to handle one failure
  mode without also swallowing every other one.
- Never write `except:` or `except Exception: pass`. Catch the specific
  exception type you expect and either handle it, re-raise it, or log it and
  re-raise — silently swallowing an exception hides the failure from
  whoever debugs it later.
- Distinguish programmer errors from expected runtime failures:
  - Programmer errors (bad arguments, broken invariants) should fail loudly
    and immediately — let the exception propagate, don't catch and paper
    over it.
  - Expected runtime failures (a network call times out, a file is missing)
    should be caught at the point they're expected and handled or
    translated into a domain exception.
- Catch broadly exactly once, at the system boundary — the CLI entrypoint or
  an API handler — and convert the exception into a clean exit code or
  response there. Always log the full original exception at that point
  (`logger.exception(...)`, see `logging.md`) before converting it; don't
  let the stack trace disappear along with the exception type.
- Always use `raise NewError(...) from original_err` when translating one
  exception into another. Dropping the `from` clause breaks the exception
  chain and hides the real root cause from whoever reads the traceback.
- Don't use exceptions for control flow in hot paths — an exception is for
  an exceptional condition, not a routine branch.

No external library is prescribed for this concern; it's a discipline
enforced through code review and the plain `Exception` hierarchy in the
standard library.
