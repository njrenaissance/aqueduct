"""login - sign in once to a OneDrive/SharePoint "specific people" share and save
the authenticated web session for the other tools to reuse.

This is step 1 of the workflow. It opens a REAL browser window (sign-in involves
redirects and MFA, which cannot be done headless), you sign in as the account the
share was granted to, and it writes the session to ~/.aqueduct/auth_state.json. The
enumerate and download steps then ride that saved session headlessly - no further
sign-in - until it expires.

    login <share-url>      # HEADED; sign in; saves the session + remembers the URL

The saved session is as sensitive as a password: it is a live logged-in session.
It lives in the per-user config dir (~/.aqueduct), outside any repo. It also expires
- when webenum/filecopy start returning 401/403 or land on a login page, re-run
this (with no argument, since the URL is remembered).
"""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

from aqueduct import paths, shareurl

# Auth session lives in the per-user config dir (~/.aqueduct), not the project.
AUTH_STATE_PATH = paths.AUTH_STATE_PATH


def login(share_url: str) -> int:
    print(
        "Opening a real browser window. Sign in as the account the share was "
        "granted to.\n"
        "  - Tick 'Stay signed in' if offered (keeps the session longer).\n"
        "  - Wait until you can actually SEE the shared files.\n"
        "Then come back HERE and press Enter to save the session.\n",
        flush=True,
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(share_url, wait_until="domcontentloaded")
        try:
            input("Press Enter here once the file list is visible in the browser... ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted; not saving session.")
            browser.close()
            return 1
        paths.ensure_config_dir()
        context.storage_state(path=str(AUTH_STATE_PATH))
        browser.close()
    print(f"\nSaved session to {AUTH_STATE_PATH}. You can now run webenum / filecopy until it expires.")
    # Surface the URL (session-scoped) so 'webenum enumerate' can reuse it.
    shareurl.remember(share_url)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="login", description="Headed sign-in; save the web session and remember the share URL."
    )
    ap.add_argument("share_url", nargs="?", help=f"share URL (default: ${shareurl.ENV}, once saved by a prior login)")
    args = ap.parse_args()
    return login(shareurl.resolve(args.share_url))


if __name__ == "__main__":
    sys.exit(main())
