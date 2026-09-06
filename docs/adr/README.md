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

> Examples below use a fictional share
> (`contoso-my.sharepoint.com`, `jdoe@example.org`). Substitute your own.
