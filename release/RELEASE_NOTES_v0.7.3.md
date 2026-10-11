## Browser Gamma Fix v0.7.3 — Edge 155.0.4283.45 support

This maintenance release adds verified support for Microsoft Edge
`155.0.4283.45`.

### What changed

- Edge 155 adds two canonical sRGB singleton initializers, changing the exact
  recognized count from 99 to 101.
- v0.7.3 accepts the 101-initializer form only alongside the existing
  fail-closed structural checks: one BT.709/sRGB/gamma 2.2 constant block, one
  factory/pointer tuple, every sRGB load, every singleton store, and one
  verified SDR/WCG/HDR output path.
- All other initializer counts and ambiguous layouts continue to be rejected.

### Validation

- All 49 automated tests pass.
- Read-only analysis of Edge `155.0.4283.45` produced a complete plan with 101
  gamma writes and two browser output writes.
- The on-disk `msedge.dll` SHA-256 used for validation was
  `EC287D2F9A668BE3025F9913B1FE5B930C192EC48495B5FE31323EF487E4BFDB`.

Native HDR, PQ/HLG and Display-P3 paths are unchanged. Browser files on disk
are never modified. Browser Gamma Fix remains free, MIT-licensed and open source.

### Upgrade

Download `BrowserGammaFix-win64.zip`, extract the complete package and run
`Gamma22Tray.exe` beside `_internal`. Do not run as administrator.
