# ADR-0011 — Logical acquisition of a remote source (physical is impossible)

**Status:** Accepted

## Context

A recurring, fair question about this project is *"aren't you just reimplementing a
downloader — why not `rclone` or `aria2`?"* — and, underneath it, *"what kind of
tool is this, exactly?"* Answering both needs a vocabulary the code has been using
implicitly but never named.

Digital forensics has always had **two acquisition modes**:

| Mode | What it captures | Typical tools |
|---|---|---|
| **Physical** | A bit-for-bit copy of the *entire medium* — including unallocated space, slack space, and deleted-but-not-overwritten data | E01/EWF, `dd`, `dc3dd`, Guymager, FTK Imager |
| **Logical** | The files and folders *as the filesystem presents them* — live, allocated, named files only | FTK Imager custom-content/AD1, `rsync`-style copies |

Disk imagers lean **physical** because they hold a *seizable block device*: a drive
they can attach a write blocker to and read sector by sector.

Our data does not live on a medium we can hold. It lives in a SharePoint share on
Microsoft's datacenter disks. There is **no block device to attach a write blocker
to**, and unallocated/slack/deleted space is unreachable by any means available to a
recipient. **Physical acquisition is impossible here** — not disfavored, impossible.
The only reachable layer is the application layer, through the credential the share
actually honors: the interactive web session ([ADR-0001](0001-ENUMERATE-VIA-WEB-SESSION.md)).

This is the same historical shift that moved evidence off seizable external drives
and into cloud shares: **acquisition follows the data down to whatever layer you can
still reach.** When the physical layer becomes someone else's datacenter, acquisition
necessarily moves *up* to the file/API layer.

## Decision

Frame aqueduct as performing **logical acquisition of a remote source** — file-level
capture of the live, named files the authorized session can see, with an
acquisition-time SHA-256 ([ADR-0006](0006-SHA256-INTEGRITY-HASH.md)) reconciled
against a dated manifest ([ADR-0002](0002-DOWNLOAD-VIA-DOWNLOAD-ASPX.md)).

Describe the **method**, not a category the tool has not earned:

- ✅ "logical acquisition of a web-only share," "defensible, dated record," "SHA-256
  computed at acquisition time" — accurate descriptions of what it does.
- ⚠️ Do **not** brand it a "forensic tool" or "forensically sound." Those are terms
  of art that invite a standard — NIST CFTT validation, SWGDE guidelines, Daubert/Frye
  admissibility challenges. An unvalidated tool wearing the label is *easier* to attack
  than the same tool described plainly. The forensics is conferred by the process, a
  qualified operator, and a defensible record — never by the tool's name.

## Consequences

- **Justifies the code's existence.** `rclone`/`aria2` are bulk downloaders whose
  hashing is *verification against a supplied digest*, not *production of an
  acquisition record*; forensic imagers produce that record but speak block devices,
  not authenticated SharePoint sessions. aqueduct sits in the seam between the two —
  logical acquisition of a web-authorized remote source. It is not reinventing a
  wheel either category ships.
- **Scope is bounded and stated honestly.** A logical acquisition inherently omits
  deleted/slack/unallocated data. The record captures *"what the share presented to an
  authorized viewer at time T,"* no more. This limitation is a defensibility point, not
  a flaw — the record claims exactly what was captured.
- **Two properties disk imagers get for free that we do not**, both inherent to remote
  logical acquisition — naming them precisely strengthens the record rather than
  weakening it:
  1. **No frozen source.** A write-blocked disk is immutable the instant it is seized;
     a live share can drift between enumeration and download. A same-size edit would
     currently pass the size check unnoticed. *(Motivates follow-up: drift detection.)*
  2. **No verify-against-source.** A disk imager re-reads the same physical sectors to
     verify; we can only re-read our own copy, which proves the local write was clean,
     not that we received what the server held (see ADR-0006, "not a source match").
     *(Motivates follow-up: verify against a server-asserted hash where reachable.)*
- **Naming discipline is now a rule, not a preference.** README, module docstrings,
  and user-facing text describe the method and the record; they do not claim the
  "forensic" label. This is consistent with the existing `CLAUDE.md` framing
  ("defensible, dated record") and makes it deliberate.
