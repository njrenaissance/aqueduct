"""Cookiecutter post-generation hook.

Initialize a Git repo (if needed) and install the pre-commit + pre-push hooks
so quality gates run locally. Best-effort: never aborts generation if Git or
pre-commit is unavailable — prints manual instructions and exits 0 instead.
"""
import subprocess
import sys
from pathlib import Path

MANUAL_MESSAGE = (
    "\n[hooks] Could not install Git hooks automatically.\n"
    "        From inside the generated project, run:\n"
    "          uv run pre-commit install\n"
    "          uv run pre-commit install --hook-type pre-push\n"
)


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def main() -> int:
    try:
        if not Path(".git").is_dir():
            _run(["git", "init"])
        _run(["uv", "run", "pre-commit", "install"])
        _run(["uv", "run", "pre-commit", "install", "--hook-type", "pre-push"])
        print("[hooks] Installed pre-commit + pre-push Git hooks.")
    except (OSError, subprocess.CalledProcessError):
        print(MANUAL_MESSAGE)
    return 0  # hook installation is best-effort; never fail generation


if __name__ == "__main__":
    sys.exit(main())
