## Browser Gamma Fix v0.7.9 — updater compatibility

### Fixed

- The in-app updater now accepts the harmless hidden `desktop.ini` and
  `Thumbs.db` files that Windows Explorer can create in an otherwise dedicated
  Browser Gamma Fix installation folder.
- Other top-level files, links and junctions remain blocked, so the updater
  still refuses installations that could contain unrelated user data.

### Validation

- Added regression coverage for Explorer metadata alongside the EXE and
  `_internal` folder.
- Existing package-integrity, rollback and startup-confirmation tests remain
  covered.
