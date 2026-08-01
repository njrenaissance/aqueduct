"""Cookiecutter post-generation hook.

Deliberately has NO filesystem side effects — it only prints the one-time manual
step for installing the local Git hooks. cruft (the template-sync mechanism, see
the project's README / CLAUDE.md) re-runs this hook inside its own internal
renders when computing an update, so anything this hook writes to the tree
(`git init` → a `.git/`; `pre-commit install` → machine-specific paths in
`.git/hooks/`; `uv run`/`uv sync` → a `.venv/`) becomes state cruft has to
diff and patch — and `git apply` refuses paths under `.git/`, which makes
`cruft update` silently drop real template changes. Keeping this hook to a pure
print keeps `cruft update` correct. The target is assumed to already be a Git
repository; installing the quality-gate hooks is a one-time manual step.
"""
import sys

INSTALL_MESSAGE = (
    "\n[hooks] Project generated. Install the local quality-gate hooks once,\n"
    "        from inside the project:\n"
    "          uv run pre-commit install\n"
    "          uv run pre-commit install --hook-type pre-push\n"
)


def main() -> int:
    print(INSTALL_MESSAGE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
