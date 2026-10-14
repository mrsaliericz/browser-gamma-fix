## Browser Gamma Fix v0.7.6 — bounded failed-generation cache

This reliability release avoids retaining a failed analyzer exception object in
the browser-generation cache. Python exception objects retain their traceback,
which can retain a large DLL image while a browser update is temporarily
unsupported.

### Fixed

- Failed generation entries now retain only the retry deadline, error type and
  diagnostic text.
- The same error is reported during the bounded retry cooldown without keeping
  the original traceback or its analyzed DLL data alive.

### Validation

- Added a regression test that verifies the cache stores metadata rather than
  an exception instance.
- Existing browser-update, debugger-detach and native HDR safety tests remain
  covered.

Native HDR, PQ/HLG and Display-P3 paths are unchanged. Browser files on disk
are never modified. Browser Gamma Fix remains free, MIT-licensed and open source.
