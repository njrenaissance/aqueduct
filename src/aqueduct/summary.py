"""summary - the one-page, human-readable outcome of a run (issue #16), preserved with the evidence.

``upload`` writes ``summary.md`` into the acquisition record (``_audit/<run-id>/``), so a reviewer can answer
"did everything make it, and if not, why?" without opening three CSVs. Each stage's status meanings are defined
next to the code that assigns them (``STATUS_DEFINITIONS`` in filecopy, validate and blobupload) and rendered
here at generation time, so an old page keeps the meaning its statuses had when it was written.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from aqueduct import filecopy, validate
from aqueduct.validated import _read_validate_rows

NOT_RUN = "not run"


@dataclass(frozen=True)
class Stage:
    """One pipeline stage's outcome. ``counts`` is None when the stage left no record."""

    title: str
    counts: Mapping[str, int] | None
    failed: Sequence[str]
    definitions: Mapping[str, str]

    @property
    def ran(self) -> bool:
        return self.counts is not None

    @property
    def failures(self) -> int:
        return sum((self.counts or {}).get(s, 0) for s in self.failed)

    @property
    def total(self) -> int:
        return sum((self.counts or {}).values())

    @property
    def passed(self) -> bool:
        """A stage that left no record has not shown that its files made it, so it does not pass."""
        return self.ran and self.failures == 0


def download_stage(results_path: Path) -> Stage:
    counts = filecopy.status_counts(results_path)
    return Stage("Downloaded", counts, filecopy.FAILED_STATUSES, filecopy.STATUS_DEFINITIONS)


def validate_stage(results_path: Path) -> Stage:
    try:
        rows = _read_validate_rows(results_path).values()
        counts: dict[str, int] | None = dict(Counter(validate.classify_row(r) for r in rows)) or None
    except (FileNotFoundError, KeyError):  # absent, or not a validate results file
        counts = None
    return Stage("Validated", counts, validate.FAILED_STATUSES, validate.STATUS_DEFINITIONS)


def _breakdown(stage: Stage) -> str:
    if not stage.counts:
        return NOT_RUN if not stage.ran else "none"
    parts = [f"{s}: {stage.counts[s]}" for s in stage.failed if stage.counts.get(s)]
    return ", ".join(parts) or "none"


def _stats_table(stages: Sequence[Stage]) -> list[str]:
    lines = ["| Stage | Files | OK | Failed | Failed, by status |", "|---|---:|---:|---:|---|"]
    for stage in stages:
        ok = stage.total - stage.failures if stage.ran else NOT_RUN
        failed = str(stage.failures) if stage.ran else NOT_RUN
        total = str(stage.total) if stage.ran else NOT_RUN
        lines.append(f"| {stage.title} | {total} | {ok} | {failed} | {_breakdown(stage)} |")
    return lines


def _definitions(stages: Sequence[Stage]) -> list[str]:
    lines = ["## Definitions", ""]
    for stage in stages:
        lines += [f"**{stage.title}**", ""]
        lines += [f"- `{status}`: {meaning}" for status, meaning in stage.definitions.items()]
        lines.append("")
    return lines


def _not_run_note(stages: Sequence[Stage]) -> list[str]:
    missing = [stage.title for stage in stages if not stage.ran]
    if not missing:
        return []
    return [f"No results were found for: {', '.join(missing)}. A stage with no record cannot pass.", ""]


def overall_passed(stages: Sequence[Stage]) -> bool:
    return all(stage.passed for stage in stages)


def render_summary(
    stages: Sequence[Stage],
    *,
    tool_version: str,
    destination: str,
    run_id: str,
    total_bytes: int,
    data_digest: str,
) -> str:
    """The Markdown page. ``data_digest`` hashes the ``data/`` lines of SHA256SUMS, which itself lists this page."""
    verdict = "PASS" if overall_passed(stages) else "FAIL"
    lines = [
        "# Acquisition summary",
        "",
        f"**Overall: {verdict}**",
        "",
        *_not_run_note(stages),
        f"- Tool version: aqueduct {tool_version}",
        f"- Vault destination: `{destination}`",
        f"- Run id: `{run_id}`",
        f"- Total bytes preserved: {total_bytes:,}",
        f"- SHA-256 of the `data/` lines of `SHA256SUMS`: `{data_digest}`",
        "",
        "The full `SHA256SUMS` lists this page, so its own hash cannot appear here: it is in `custody.json` and is",
        "printed at the end of the run. Record it outside the vault. The upload of the audit files themselves",
        "happens after this page is written and is not counted here.",
        "",
        "## Results",
        "",
        *_stats_table(stages),
        "",
        *_definitions(stages),
    ]
    return "\n".join(lines).rstrip() + "\n"
