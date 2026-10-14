## Browser Gamma Fix v0.7.5 — fail-safe debugger detach recovery

This reliability release fixes a rare Windows debugger lifecycle failure that
could leave a Chrome, Edge, Brave or Vivaldi process paused when
`DebugActiveProcessStop` temporarily returned an error.

### Fixed

- `DebugActiveProcessStop` is now retried up to eight times with a short,
  bounded backoff before any attach failure is reported.
- A failed detach is now higher priority than a failed attach. The tray never
  attempts another `DebugActiveProcess` on a PID whose debugger ownership has
  not been released.
- If all detach retries fail, Browser Gamma Fix displays a tray warning and
  safely restarts itself. The retiring debugger process releases the browser
  because `DebugSetProcessKillOnExit(FALSE)` remains in effect.
- The diagnostic log now prefixes every line with a timestamp.

### Validation

- All automated tests pass, including new retry, fail-safe classification and
  timestamped-log tests.
- Edge `155.0.4283.45` support remains unchanged.

Native HDR, PQ/HLG and Display-P3 paths are unchanged. Browser files on disk
are never modified. Browser Gamma Fix remains free, MIT-licensed and open source.
