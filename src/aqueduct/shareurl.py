"""shareurl - the baseline share URL, carried between login and enumerate in an
environment variable so you type it once.

`login.py` surfaces the URL you sign in with as `ONEDRIVE_SHARE_URL`; `webenum.py`
reads it as the default share URL when you don't pass one. (filecopy.py and
validate.py never need it - they get the site and every file path from
manifest.json.)

Scope is deliberate: the URL is **per-case**, so it is *session-scoped*, not
persisted into the machine/user environment. A globally persisted value would
silently misapply to the next case (see docs/adr/ADR-0004). A child process cannot
set its parent shell's environment, so `login` prints the one-liner to set it in the
current shell; run that (or pass the URL explicitly) before `webenum enumerate`.
"""

from __future__ import annotations

import os
import sys

ENV = "ONEDRIVE_SHARE_URL"


def get() -> str | None:
    """The share URL from the environment, or None."""
    v = os.environ.get(ENV)
    return v.strip() if v and v.strip() else None


def remember(url: str) -> None:
    """Surface `url` for the enumerate step, scoped to the current shell.

    We intentionally do NOT write it to the persistent user environment (that would
    outlive the case and misapply to the next one). A process can't set its parent
    shell's variables, so we print the one-liner to run; after that `webenum
    enumerate` picks it up with no URL argument.
    """
    os.environ[ENV] = url  # harmless in this process; the printed command is what carries it
    print(f"\nTo reuse this share URL without retyping it, set {ENV} in your shell:")
    if os.name == "nt":
        print(f'  PowerShell:  $env:{ENV} = "{url}"')
        print(f'  cmd.exe:     set "{ENV}={url}"')
    else:
        print(f'  bash/zsh:    export {ENV}="{url}"')
    print(
        "It is per-case by design (not persisted across terminals); pass the URL "
        "explicitly or re-run login for a different share."
    )


def resolve(arg: str | None) -> str:
    """A share URL from the CLI arg if given, else the environment variable.

    Exits with a clear message if neither is available.
    """
    url = arg or get()
    if not url:
        print(
            f"No share URL given and {ENV} is not set.\n"
            f"Run 'login <share-url>' first (it surfaces the URL), "
            f"or pass the URL explicitly.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return url
