# Changes

## 0.3.4

- Repair legacy bootstrap-created 0700 public source directories during package
  configuration; preserve source contents and reject unsafe ownership/symlinks.
- Explain unreadable source configuration or public keys without disguising a
  permissions failure as an empty offline catalog.
- Publish Launcher 0.3.4 with touch-adjustable fan curves and a bootstrap script
  that creates public directories correctly under umask 077 and repairs existing
  trusted sources after normal verification and administrator authorization.

## 0.3.3

- Simplify application display names in the catalog, details, search and
  confirmations by removing standalone Typix / TypixDeck prefixes.
- Keep signed records, package names and desktop launch identities unchanged.
- Retain the private-icon ownership fix introduced in 0.3.2.

## 0.3.2

- Fix Store self-updates failing when an installed application's desktop icon
  had the same path as a catalog icon exported by Store 0.3.1. Catalog logos now
  stay in Store's private directory; other packages retain their icon ownership.
- Reject signed catalog assembly when two packages own the same file or link,
  including byte-identical icons. Shared directories remain valid.
- Add real deb ownership checks and isolated dpkg upgrade regression coverage.
