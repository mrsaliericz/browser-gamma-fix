# Browser Gamma Fix: technical details

This document is for people who want to understand how Browser Gamma Fix works,
build it, or investigate a compatibility issue. The normal installation guide
is on the [project home page](../README.md).

## Color-management scope

The correction is deliberately limited to ordinary BT.709/sRGB SDR content.
Browser Gamma Fix changes Chromium's internal SDR-to-scRGB interpretation to
pure gamma 2.2 while preserving the browser's HDR/scRGB presentation path.

It intentionally does not alter Display-P3 or other wide-gamut content, native
HDR video, PQ, HLG, HDR black levels or HDR highlights. Keeping the browser on
the HDR/scRGB path is also why it can run alongside `dwm_eotf_rs` without the
two corrections stacking.

## Runtime model and safety

The program works only in the memory of supported browser processes; it never
changes browser binaries on disk. Before it writes anything, it structurally
validates the candidate Chromium DLL layout, including the expected SDR color
objects and the verified SDR/WCG/HDR output path. Unknown, incomplete or
ambiguous layouts fail closed.

Only the relevant browser and GPU processes receive their role-specific
changes. Renderer and utility processes retain upstream behavior. Attach retries
are bounded, and a failed debugger detach is retried before the process is ever
considered for another attach attempt.

The app monitors Chrome, Edge, Brave and Vivaldi DLL generations. A recognized
update pauses new writes, waits for the update to settle, restarts the tray
process and attaches to the new generation. Unsupported layouts remain safely
unmodified and are reported through the tray state and diagnostic log.

## Compatibility

Chrome, Brave and Vivaldi use the Chrome layout analyzer. Edge also has a
bounded Capstone-based analyzer that follows the verified output helper, usage
table values, loop limits and control flow instead of relying on one fragile
byte pattern.

The analyzer was expanded as Chromium compiler layouts evolved, including
Chrome helper stack-frame changes and Edge singleton counts of 97, 98, 99 and
101. These are accepted only together with the corresponding complete
structural checks; an arbitrary matching count is not enough. The project has
also passed live testing through multiple successive Chrome and Edge updates.
That is evidence of compatibility with tested layouts, not a guarantee for a
future Chromium implementation.

Standard Program Files and per-user installation locations are detected for
the supported browsers. Brave Beta/Nightly and arbitrary custom locations are
not automatically discovered.

## Updates and package integrity

The tray updater checks the latest stable GitHub release at startup, then once
per hour, and when the analyzer rejects a browser DLL layout. Automatic checks
are throttled globally and per observed DLL identity; manual checks bypass that
cooldown.

For ZIP installations, an update downloads the official archive over HTTPS,
verifies its GitHub asset SHA-256 and expected size, and replaces the complete
onedir package only after the old tray exits. It preserves a backup and waits
for the new tray to confirm startup; a launch failure rolls back. This is an
integrity check against GitHub metadata, not an independent code signature.

Automatic replacement requires a dedicated writable installation folder that
contains only `Gamma22Tray.exe` and `_internal`, without links or junctions.
Temporary backup and diagnostic files use a sibling `.gamma22-update-*`
directory and are intentionally retained for troubleshooting.

## Diagnostics

Use **Open diagnostic log** from the tray menu. The log is stored at:

```text
%LOCALAPPDATA%\ChromiumGamma22\Gamma22HotAttach.log
```

It records browser versions and DLL hashes, successful browser/GPU attachment,
update-triggered restarts and safely rejected layouts. A red tray dot signals
an unsupported layout or correction error; it does not mean that Browser Gamma
Fix has guessed or patched an unverified browser binary.

## Build from source

Install Python 3.9 or newer and PyInstaller, then run:

```powershell
python -m pip install pyinstaller
python -m pip install -r requirements.txt
.\build_hot_attach_exe.ps1
```

The build produces `dist\BrowserGammaFix-win64.zip`. The GitHub release
workflow runs the automated tests, builds the onedir package, and publishes its
SHA-256 together with the exact source commit.

## Historical names

The project was previously named **Gamma22Tray**. Releases through v0.6.0 use
that name. From v0.7.0 onward, the user-facing name is Browser Gamma Fix and
the release ZIP is `BrowserGammaFix-win64.zip`. The internal `Gamma22Tray`
folder, executable name, startup registry value and diagnostic paths remain for
backward compatibility with existing installations and shortcuts.
