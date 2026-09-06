# Architecture Decision Records

These ADRs capture the load-bearing design choices behind onedrive-enum and *why*
they were made. Each is self-contained; read them in order for the full picture.

| ADR | Decision |
|-----|----------|
| [ADR-0001](ADR-0001-ENUMERATE-VIA-WEB-SESSION.md) | Enumerate through the interactive **web session**, not the Graph API |
| [ADR-0002](ADR-0002-DOWNLOAD-VIA-DOWNLOAD-ASPX.md) | Download via `download.aspx` (Range-capable), not `$value` |
| [ADR-0003](ADR-0003-AUTH-IN-USER-CONFIG-DIR.md) | Keep auth tokens in `~/.odenum`; working data in the current directory |
| [ADR-0004](ADR-0004-SHARE-URL-VIA-ENV-VAR.md) | Carry the share URL between steps in an environment variable |
| [ADR-0005](ADR-0005-SRC-PACKAGE-LAYOUT.md) | Ship as a `src/` package with console entry points |
| [ADR-0006](ADR-0006-SHA256-INTEGRITY-HASH.md) | Use SHA-256 (not Microsoft's QuickXorHash) as the integrity hash |
| [ADR-0007](ADR-0007-DESKTOP-VM-OPERATING-MODEL.md) | Desktop-VM operating model for unsupported small practices |
| [ADR-0008](ADR-0008-STREAM-TO-BLOB-PRESERVATION.md) | Stream-to-Blob preservation with ledger-driven restart |
| [ADR-0009](ADR-0009-TAMPER-EVIDENT-LEDGER.md) | Tamper-evident hash ledger (SQL ledger / Confidential Ledger + immutable anchor) |

ADR-0001–0006 describe the shipped collection tool; ADR-0007–0009 are forward-looking
decisions that govern the [pipeline SPEC](../SPEC.md) and are not yet implemented.

> Examples below use a fictional share
> (`contoso-my.sharepoint.com`, `jdoe@example.org`). Substitute your own.
