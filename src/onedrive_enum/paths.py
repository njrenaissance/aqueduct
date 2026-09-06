"""paths - where onedrive-enum keeps auth material.

Auth tokens live in a per-user config directory (``~/.odenum``, i.e.
``$USERPROFILE/.odenum`` on Windows), NOT in the project or working directory, so
they can't be committed by accident and are shared across runs from any folder.

Working data (manifests, downloads, logs) is separate: it stays in the current
working directory, so you can keep a per-case data folder.
"""

from __future__ import annotations

from pathlib import Path

CONFIG_DIR = Path.home() / ".odenum"
AUTH_STATE_PATH = CONFIG_DIR / "auth_state.json"      # webenum/filecopy web session
TOKEN_CACHE_PATH = CONFIG_DIR / ".token_cache.json"   # odenum's MSAL Graph cache


def ensure_config_dir() -> Path:
    """Create ~/.odenum (private to the user) if needed, and return it."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR
