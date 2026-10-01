# Changes

## 0.3.2

- Fix Store self-updates failing when an installed application's desktop icon
  had the same path as a catalog icon exported by Store 0.3.1. Catalog logos now
  stay in Store's private directory; other packages retain their icon ownership.
- Reject signed catalog assembly when two packages own the same file or link,
  including byte-identical icons. Shared directories remain valid.
- Add real deb ownership checks and isolated dpkg upgrade regression coverage.
