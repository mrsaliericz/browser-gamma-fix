## Browser Gamma Fix v0.7.0 — Chrome, Edge, Brave and Vivaldi

Gamma22Tray is now **Browser Gamma Fix**. This release adds Vivaldi support,
application update checks and optional user-confirmed update installation.

### Changes

- Detects standard per-user and Program Files Vivaldi installations and its
  `vivaldi.dll`. Uses the existing Chrome analyzer without relaxing checks.
- Includes Vivaldi in process monitoring, on/off control and update handling.
- Renames the interface and download to `BrowserGammaFix-win64.zip`. The
  internal `Gamma22Tray.exe`, folder, startup value and log paths stay unchanged
  for compatibility with existing installations and shortcuts.
- Checks for new application releases on startup, periodically and after an
  unsupported browser layout is detected. Manual checks are also available.
- Installation requires confirmation, verifies the GitHub asset SHA-256 and
  size, keeps a backup and rolls back if the replacement fails its startup check.
- Adds an optional **Do you like this app? Support me!** menu link.

The repository is now https://github.com/mrsaliericz/browser-gamma-fix.
Old repository and release links redirect to the new location.

### Validation and limitations

All 49 automated tests pass. Vivaldi 8.2.4133.52 passed structural analysis;
live attachment applied 94 GPU code writes, one cached sRGB correction and two
browser output writes. Its on-disk DLL hash remained unchanged. The author
approved publication after testing. Future Vivaldi updates are not guaranteed;
unsupported layouts still fail closed. Custom installation paths are not detected.

The updater was tested with a local full-package handoff, startup confirmation,
rollback tests and a real GitHub download/integrity check. A future public
release-to-release update still needs real-world validation. SHA-256 checked
against GitHub metadata is an integrity check, not an independent signature.

Native HDR, PQ/HLG and Display-P3 paths are unchanged. Browser files on disk
are never modified. Use alongside `dwm_eotf_rs` as before.

### Install or upgrade

1. Download `BrowserGammaFix-win64.zip` and extract the complete `Gamma22Tray` folder.
2. Exit the previous tray application. Browsers may remain open.
3. Replace the complete application package; keep `Gamma22Tray.exe` beside `_internal`.
4. Run `Gamma22Tray.exe` normally, without administrator rights.

Versions through v0.6.0 require this manual upgrade. Keep the installation path
for Start with Windows, or reconfigure it after moving the folder. Automatic
updates require a dedicated writable folder containing only the EXE and `_internal`.

The tool is unsigned; debugger attachment and memory writes can trigger antivirus
heuristics. Do not disable security software. Review the README security notice.

Free, MIT-licensed and open source. Optional support:
https://buymeacoffee.com/mrsaliericze
