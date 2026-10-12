"""publish - stream preserved files from the Azure Blob vault to a SharePoint library (ADR-0014). Skeleton."""
# ruff: noqa: ARG001 - API-only stub; the implementation replaces it

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from aqueduct.blobupload import open_container
from aqueduct.graphclient import ClientCredentialsTokenProvider, FolderRef, GraphClient, load_config
from aqueduct.validated import UploadEntry

__all__ = ["ClientCredentialsTokenProvider", "GraphClient", "load_config", "open_container"]

DEFAULT_MAX_BYTES = 0
AUDIOVISUAL_EXT: tuple[str, ...] = ()


@dataclass(frozen=True)
class VaultBlob:
    name: str
    entry: UploadEntry


@dataclass
class PublishResult:
    path: str
    status: str
    size: int
    sha256: str
    quick_xor_hash: str
    uniqueid: str
    attempts: int
    seconds: float
    detail: str = ""


@dataclass
class Ctx:
    container: Any
    graph: Any
    client: httpx.AsyncClient | Any
    sem: asyncio.Semaphore
    max_retries: int
    chunk: int
    delete_mismatched: bool = False


def list_vault(container: Any, prefix: str) -> list[VaultBlob]:
    return []


def screen_blobs(
    blobs: list[VaultBlob], max_bytes: int, include_av: bool, skip_ext: Sequence[str]
) -> tuple[list[VaultBlob], list[tuple[VaultBlob, str]]]:
    return blobs, []


async def publish_one(ctx: Ctx, blob: VaultBlob, dest: FolderRef) -> PublishResult:
    return PublishResult(blob.entry.path, "", 0, "", "", "", 0, 0.0)


async def run(
    ctx: Ctx,
    blobs: list[VaultBlob],
    skipped: list[tuple[VaultBlob, str]],
    base: FolderRef,
    results_path: Path,
    source: str,
    policy: dict,
    operator: str | None = None,
) -> int:
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return argparse.Namespace()


def main(argv: Sequence[str] | None = None) -> int:
    return 0
