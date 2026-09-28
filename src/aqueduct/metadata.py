"""Shared utilities for provenance metadata capture (tool version, operator identity, timing).

Coordinates with ADR-0011 (chain-of-custody) and ADR-0009 (tamper-evident ledger).
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aqueduct import __version__


def get_version() -> str:
    """Return the tool version (from aqueduct.__version__)."""
    return __version__


def get_host_info() -> dict[str, str]:
    """Return host information for the acquisition record."""
    return {
        "hostname": socket.gethostname(),
        "platform": platform.system(),
        "python_version": platform.python_version(),
    }


def get_operator_identity(default: str = "unspecified") -> str:
    """Return operator identity from environment variable or default.

    Supports $AQUEDUCT_OPERATOR env var for explicit opt-in; defaults to
    "unspecified" to avoid incidental PII capture. Never auto-captures email,
    username, or other credentials.
    """
    return os.environ.get("AQUEDUCT_OPERATOR", "").strip() or default


def format_metadata_header(
    tool: str,
    version: str,
    operator: str,
    host_info: dict[str, str],
    started_at_utc: str,
    completed_at_utc: str,
) -> list[str]:
    """Generate CSV-compatible comment lines for provenance.

    Format matches webenum's _provenance_lines pattern (# prefix so CSV readers
    that honor comments skip them; actual header and data follow untouched).
    """
    return [
        "# aqueduct acquisition (provenance; full run metadata in .metadata.json sidecar)",
        f"# tool: {tool} {version}",
        f"# started_at_utc: {started_at_utc}",
        f"# completed_at_utc: {completed_at_utc}",
        f"# operator: {operator}",
        f"# host: {host_info.get('hostname', '?')} ({host_info.get('platform', '?')})",
    ]


def write_metadata_sidecar(path: Path, metadata: dict[str, Any]) -> None:
    """Write run metadata as JSON sidecar.

    Atomic write (temp + replace) to ensure consistency with results CSV.
    Sidecar is supplementary; JSON is structured for programmatic parsing
    and future ledger integration.
    """
    sidecar_path = path.with_suffix(path.suffix + ".metadata.json")
    tmp = sidecar_path.with_suffix(sidecar_path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)
    tmp.replace(sidecar_path)


@contextlib.contextmanager
def timed_run(tool: str, operator: str | None = None) -> Any:
    """Context manager that times a tool run and yields metadata.

    Usage:
        with TimedRun("filecopy", operator_identity) as meta:
            # do work
            pass
        # meta contains started_at_utc, completed_at_utc, duration_seconds
    """
    started = datetime.now(UTC)
    started_str = started.isoformat()
    operator_id = operator or get_operator_identity()

    meta: dict[str, Any] = {
        "tool": tool,
        "tool_version": get_version(),
        "started_at_utc": started_str,
        "operator": operator_id,
        "host_info": get_host_info(),
    }

    try:
        yield meta
    finally:
        completed = datetime.now(UTC)
        completed_str = completed.isoformat()
        duration = (completed - started).total_seconds()
        meta["completed_at_utc"] = completed_str
        meta["duration_seconds"] = duration
