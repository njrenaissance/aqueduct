"""Cookiecutter post-generation hook.

Initialize a Git repo (if the target isn't already one). Best-effort: never
aborts generation if Git is unavailable.

The pre-commit / pre-push hooks are deliberately NOT installed here. cruft (the
template-sync mechanism, see the project's README / CLAUDE.md) re-runs this hook
inside its own internal renders when computing an update; `pre-commit install`
would bake machine-specific absolute paths into `.git/hooks/` and `uv run` would
create a `.venv/`, and cruft would then try to diff/patch both — silently
dropping real template changes. Keeping this hook to a bare, deterministic
`git init` keeps `cruft update` correct. Installing the local quality-gate hooks
is therefore a one-time manual step the developer runs after generation.
"""
import subprocess
import sys
from pathlib import Path

INSTALL_MESSAGE = (
    "\n[hooks] Project generated. Install the local quality-gate hooks once,\n"
    "        from inside the project:\n"
    "          uv run pre-commit install\n"
    "          uv run pre-commit install --hook-type pre-push\n"
)


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def main() -> int:
    try:
        if not Path(".git").is_dir():
            _run(["git", "init"])
    except (OSError, subprocess.CalledProcessError):
        pass  # git unavailable; the project still generated fine
    print(INSTALL_MESSAGE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
