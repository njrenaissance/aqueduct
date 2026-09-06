---
paths:
  - "tests/**/*.py"
  - "**/test_*.py"
---

# Pytest rules

- Tests live under `tests/`, mirroring `src/` module names (`test_main.py` covers `main.py`)
- Use the `unit` and `integration` markers defined in `pyproject.toml` to categorize tests
- Coverage target: `fail_under = 70` on `src/` (see `[tool.coverage.report]`), measured against unit tests only — tests or files marked `pytest.mark.unit`, not `integration`
- Run `uv run pytest -m unit` before committing changes
- Test files still follow Clean Code principles (Robert C. Martin) — no exceptions for being "just tests"
- Use `@pytest.mark.parametrize` instead of separate test functions when the function under test and assertion shape are the same across cases:

  ```python
  # Avoid
  def test_capitalize_all_lower_case():
      assert capitalize("abc") == "ABC"

  def test_capitalize_camel_case():
      assert capitalize("aBC") == "ABC"

  def test_capitalize_empty_string():
      assert capitalize("") == ""

  # Prefer
  @pytest.mark.parametrize(
      ("input_str", "expected"),
      [
          pytest.param("abc", "ABC", id="all_lower_case"),
          pytest.param("aBC", "ABC", id="camel_case"),
          pytest.param("", "", id="empty_string"),
      ],
  )
  def test_capitalize(input_str, expected):
      assert capitalize(input_str) == expected
  ```

  `pytest.param(..., id=...)` names each case in `-v` output and failure reports, so a failing row still reads like a targeted test rather than `test_capitalize[input_str1]`.
