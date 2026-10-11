## Browser Gamma Fix v0.7.4 — updater startup fix

This corrective release fixes the automatic-update handoff introduced by
v0.7.3. It retains verified support for Microsoft Edge `155.0.4283.45`.

### Fixed

- v0.7.3 was built with an internal application version left at `0.7.2`.
  The update helper correctly rejected the resulting startup confirmation and
  rolled the installation back to the previous version.
- v0.7.4 declares its correct internal version, so the helper accepts the
  verified `v0.7.4` startup handoff.
- A regression test now proves that startup confirmation succeeds only when
  the release tag and embedded application version match.

### Validation

- All automated tests pass, including the new update-handoff regression test.
- Edge `155.0.4283.45` remains structurally verified: 101 gamma writes and
  two browser output writes.

Native HDR, PQ/HLG and Display-P3 paths are unchanged. Browser files on disk
are never modified. Browser Gamma Fix remains free, MIT-licensed and open source.

### Upgrade

If v0.7.3 reported **Invalid application startup confirmation**, it should
already have rolled back safely. Use **Check for updates…** and then
**Install version…** to install v0.7.4.
