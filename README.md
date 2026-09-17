# Browser Gamma Fix

Previously called **Gamma22Tray**. The repository has moved to
[`mrsaliericz/browser-gamma-fix`](https://github.com/mrsaliericz/browser-gamma-fix);
existing GitHub links redirect to the new location.

Older releases through v0.6.0 carry the former name. Starting with v0.7.0, builds use
the Browser Gamma Fix name in the interface and `BrowserGammaFix-win64.zip`
for downloads. The internal `Gamma22Tray` folder, `Gamma22Tray.exe`, startup
registry value and diagnostic paths are intentionally retained so existing
shortcuts and startup settings remain compatible. Historical release notes
keep their original names.

Browser Gamma Fix corrects ordinary SDR rendering in **normally installed 64-bit
Google Chrome, Microsoft Edge, Brave and Vivaldi** while Windows HDR is enabled. It keeps the
browsers on their native HDR/scRGB presentation path but interprets ordinary
BT.709/sRGB SDR content using pure gamma 2.2.

> **[Download Browser Gamma Fix v0.7.0 — Chrome, Edge, Brave and Vivaldi](https://github.com/mrsaliericz/browser-gamma-fix/releases/latest)**

Portable or isolated browser copies are not required. Browser Gamma Fix runs in the
Windows notification area and applies the correction only in process memory;
it does not modify browser files on disk.

> **Stable v0.7.0 — 16 September 2026:** Adds **Vivaldi** support and the new
> **Browser Gamma Fix** name, plus application update checks and user-confirmed
> installation. Run one tray application to monitor all four supported browsers.

The Edge analyzer decodes x64 instructions and follows arguments and branches,
allowing verified changes in registers, stack offsets and code placement.
Brave and Vivaldi use the existing Chrome analyzer. Compatibility with every future
browser update is not guaranteed; see [Browser updates](#browser-updates).

> **Free and open source, forever.** You may use, share, modify and redistribute
> this MIT-licensed project at no cost. If it improves your Windows HDR setup,
> you can optionally [buy me a coffee ☕](https://buymeacoffee.com/mrsaliericze).

Created by **[Jaroslav Safar](https://jaroslavsafar.com)**, an independent
developer from the Czech Republic. [Meet the author](#hi-im-jaroslav-).

## What it preserves

The correction is deliberately limited to ordinary SDR BT.709/sRGB content:

- Display-P3 and other wide-gamut content remains wide gamut.
- Native HDR video remains on Chromium's original HDR path.
- PQ, HLG, HDR black levels and highlights are not changed.
- SDR appearance remains stable when HDR or P3 content appears or disappears.
- Chrome, Edge, Brave and Vivaldi browser files remain untouched on disk.

## Requirements

- Windows 11 x64 with Windows HDR enabled.
- Normally installed 64-bit Google Chrome, Microsoft Edge, Brave and/or Vivaldi.
- A structurally compatible Chromium build. Unknown layouts are rejected
  before Browser Gamma Fix writes anything to process memory.

## Install and run

1. Download `BrowserGammaFix-win64.zip` from the
   [latest stable release](https://github.com/mrsaliericz/browser-gamma-fix/releases/latest).
2. Extract the **complete `Gamma22Tray` folder** to a permanent location.
3. Keep `Gamma22Tray.exe` beside its `_internal` folder. Copying the EXE alone
   will cause a missing Python DLL error.
4. Run `Gamma22Tray.exe` normally. Do not use **Run as administrator**.
5. Start or continue using the normally installed Chrome, Edge, Brave or Vivaldi.

Brave Stable is detected in its standard Program Files or per-user
`%LOCALAPPDATA%` installation directory. Brave Beta/Nightly channels and
arbitrary custom installation paths are not automatically detected.

The tray icon is colored while the correction is enabled and gray while it is
disabled. Right-click it to access:

- **Disable/Enable Gamma 2.2 fix**
- **Start with Windows**
- **About Browser Gamma Fix**
- **Check for updates…** and **Install version…** when a newer release is available
- **Do you like this app? Support me!** — opens the optional Buy Me a Coffee page.
- **Open diagnostic log**
- **Exit**

Turning the fix off restores upstream code and cached SDR color objects in
running browser processes. Exiting Browser Gamma Fix does not undo changes already
made in an existing process; disable the fix first or close the browser. All
in-memory changes disappear naturally when the browser exits.

## Browser updates

### Vivaldi support (v0.7.0)

The application also detects standard per-user and Program Files Vivaldi
installations. Vivaldi uses `vivaldi.dll`; it is checked by the existing Chrome
analyzer without relaxing structural validation. Read-only analysis passed for
Vivaldi `8.2.4133.52`. Live attachment applied 94 GPU code writes, one cached
sRGB object correction and two browser output writes. The DLL hash on disk
remained unchanged. The author approved release after testing. Recovery across
future Vivaldi updates still needs real-world confirmation; unknown layouts
are rejected. Custom and standalone installation paths are not auto-detected.

### Application updater (new in v0.7.0)

The tray updater checks GitHub's latest stable release on startup,
then daily, and when a browser DLL is rejected by the layout analyzer. Process
access errors do not trigger compatibility update checks. Automatic attempts
are limited to one per 15 minutes across browsers and one per DLL identity per
day; this history survives application restarts. **Check for updates…** in the
tray menu allows a manual check.

If an update is available, the tray offers **Install version…**. Installation
requires confirmation. A newer release may resolve compatibility, but its
availability alone does not prove that a particular browser is supported.

The updater downloads the official ZIP over HTTPS and checks its size and
GitHub asset SHA-256 before extraction. It replaces the complete onedir package
only after the old tray exits, preserves a backup and waits for the new tray to
confirm startup. A launch failure triggers rollback. Open browsers can remain
running; the installation path and Windows startup registration are retained.
This is an integrity check against GitHub metadata, not an independent code
signature.

Automatic replacement requires a dedicated, writable folder containing only
`Gamma22Tray.exe` and `_internal`, without links or junctions. Otherwise use a
manual update. Backup and diagnostic files remain in a sibling
`.gamma22-update-*` directory (`previous` contains the old installation).
They are intentionally retained rather than automatically deleted.

The updater is not included in v0.6.0 or earlier. Upgrade those versions to
v0.7.0 manually: exit the old tray and replace its complete package, keeping
the installation path if using Start with Windows.

### Browser generation monitoring

Browser Gamma Fix checks the installed Chrome, Edge, Brave and Vivaldi DLL generations every five
seconds. When it recognizes a compatible update, it:

1. suspends new process-memory writes,
2. waits 15 seconds for the browser update to settle,
3. safely restarts itself,
4. attaches to the current browser generation.

As of **27 August 2026**, the project author's everyday testing has confirmed
continued functionality through **multiple successive updates of both Chrome
and Edge**. The correction resumed automatically after the updates, without
manual intervention or repatching.

Edge `152.0.4191.53` subsequently changed the recognized singleton layout from
98 to 97 sRGB initializers. Gamma22Tray v0.4.2 adds support for this layout and
checks that every associated sRGB load and singleton store is accounted for.
Older Browser Gamma Fix versions safely report **Unsupported/Error** for this Edge
build and need to be updated; restarting the old patcher alone will not help.

Chrome `153.0.8010.37` changed two stack-frame offsets immediately before its
HDR output helper call. Gamma22Tray v0.4.3 no longer identifies this location
using those fixed offsets. It instead requires a unique verified helper,
direct call target, argument-setup structure and original hook bytes. The
updated discovery was tested against unmodified Chrome 151, 152 and 153 DLLs;
unknown or ambiguous layouts are still rejected before any write occurs.

This confirms compatibility with the updates tested so far, not every future
Chromium layout. Unfamiliar layouts still fail closed and are reported in the
diagnostic log.

### More resilient Edge output analysis

Edge `153.0.4234.32` moved output setup into a split code block and changed how
registers carry the usage table and output arguments. The previous byte pattern
could no longer identify it even though the 97 sRGB initializers still matched.

Introduced in the v0.5 beta and included in stable v0.6.0, the bounded
instruction analyzer uses Capstone. When the existing
Edge output pattern does not match, it uses PE function boundaries, identifies
the exact known output helper, and checks both supported control-flow paths.
It traces the usage-table value and output arguments, verifies the loop limit,
and verifies the analyzed function bytes again in the loaded process before
applying the correction. Unsupported instructions and ambiguous candidates are
rejected.

Tests cover changed stack offsets and loop registers, plus invalid calls,
tables, counters, arguments and branches. Read-only validation also passed for
the available original Edge 151 and Chrome 153 DLLs. Live inspection of two
Edge 153 browser/GPU pairs confirmed the intended role-specific changes, and
the author confirmed the visual result.

This improves tolerance of the supported compiler variations. It does not
remove the existing 97/98 Edge initializer-count checks, the exact output-helper
check or all other layout constraints. A different rendering implementation
can still require an update to Browser Gamma Fix.

### Brave support (v0.6.0)

Brave's installed `153.1.95.101` directory (Brave 1.95.101) contains a
`chrome.dll` accepted by the existing Chrome analyzer without relaxing its
checks. Live inspection confirmed 94 gamma writes in its GPU process and two
scRGB/F16 output writes in its browser process, with no unexpected changes and
an unchanged DLL hash on disk. The author confirmed the visual result.

Brave participates in the same process monitoring, on/off control and browser
update handling. Its initial compatibility has been tested; automatic recovery
through future Brave updates still needs real-world confirmation.

## Start with Windows

Use **Start with Windows** in the tray menu after placing the extracted folder
in its permanent location. Browser Gamma Fix creates only this per-user registry
value and does not require administrator rights:

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Gamma22Tray
```

Do not move or rename the extracted folder afterward. To change its location,
disable Start with Windows, move the complete folder, run the EXE from the new
location and enable the option again.

## Using it with dwm_eotf_rs

Browser Gamma Fix works well alongside
[`dwm_eotf_rs`](https://github.com/SERGEYDJUM/dwm_eotf_rs), and using both is
recommended when you want gamma correction in other Windows applications too.

Chromium remains on its HDR/scRGB presentation path, so `dwm_eotf_rs` does not
apply a second correction to the browser. Browser Gamma Fix handles Chromium's
internal SDR-to-scRGB conversion while `dwm_eotf_rs` continues handling other
SDR applications that pass through the Windows DWM path.

Browser Gamma Fix targets gamma **2.2**, not 2.4. There is currently no 2.4 mode.

## Browser video

- Native HDR video is not modified.
- Ordinary SDR video follows Chromium's active video/compositor path and may
  briefly change appearance when player UI or overlays appear.
- NVIDIA RTX Video HDR can convert supported SDR video to HDR before the final
  presentation path. When it is active, Browser Gamma Fix intentionally leaves that
  HDR result unchanged.

## HDR photos and Google Photos

Browser Gamma Fix intentionally does not modify HDR gain-map reconstruction or
Display-P3 images. Services can supply an iPhone photo as an Ultra HDR JPEG
with a P3 or sRGB SDR base plus a separate gain map. Its shadows and midtones
may therefore differ from Apple Photos or iCloud even while HDR highlights and
wide gamut remain active. This is outside the ordinary BT.709/sRGB path changed
by Browser Gamma Fix.

## Antivirus notice

Browser Gamma Fix is unsigned and necessarily uses Windows debugger attachment and
process-memory writes. Antivirus products can classify those behaviors as
suspicious even when the program was built from this published source.

The current release uses an unpacked **onedir** package because the earlier
self-extracting one-file beta triggered Windows Defender heuristics. It also
limits failed debugger attachments and skips incompatible WebView processes to
avoid retry storms.

Do not disable Windows security. Download only from this repository, verify the
published SHA-256, inspect the source, and build it yourself if in doubt.

## Diagnostics

Use **Open diagnostic log** in the tray menu. The log is stored at:

```text
%LOCALAPPDATA%\ChromiumGamma22\Gamma22HotAttach.log
```

Useful messages include the detected browser version and DLL hash, successful
browser/GPU process attachment, an update-triggered restart, or a safely
rejected unsupported layout.

## Build from source

Install Python 3.9 or newer and PyInstaller, then run:

```powershell
python -m pip install pyinstaller
python -m pip install -r requirements.txt
.\build_hot_attach_exe.ps1
```

The build produces:

```text
dist\BrowserGammaFix-win64.zip
```

The GitHub release workflow runs the automated tests, builds the onedir ZIP and
publishes its SHA-256 together with the exact source commit.

## Safety model

- Browser binaries are never modified on disk.
- Candidate DLL layouts are structurally verified before runtime writes.
- Unknown or incompatible layouts fail closed.
- Attach retries are bounded to avoid repeated debugger activity.
- Only browser and GPU roles receive their corresponding changes; renderer and
  utility processes retain upstream behavior.

## Hi, I'm Jaroslav 👋

I'm an independent developer from the Czech Republic building iOS apps,
Windows tools, open-source software and web projects.

Selected work:

- **iOS apps**, including TrayMate, Můj radar and Health Metrics Widgets.
- **Browser Gamma Fix** — the Windows HDR SDR gamma 2.2 runtime fix for Chrome,
  Edge, Brave and Vivaldi featured in this repository.
- **[JarosView](https://apps.microsoft.com/detail/9NDJ4BMB0DS0?hl=neutral&gl=CZ&ocid=pdpshare)** — a Windows app for creating correct HDR screenshots in JXR format, viewing HDR images and more. It is available from the Microsoft Store for a small one-time purchase.
- **Web and e-commerce projects.**

Explore my work: **[jaroslavsafar.com](https://jaroslavsafar.com)**.

## Project information

- Author: Jaroslav Safar
- Portfolio: [jaroslavsafar.com](https://jaroslavsafar.com)
- Contact: [hello@jaroslavsafar.com](mailto:hello@jaroslavsafar.com)
- License: [MIT](LICENSE)
- Current stable release: [Browser Gamma Fix v0.7.0](https://github.com/mrsaliericz/browser-gamma-fix/releases/tag/v0.7.0)

Historical documentation for the retired version-specific workflows is kept
in [`archive/LEGACY_VERSION_SPECIFIC_PATCHER.md`](archive/LEGACY_VERSION_SPECIFIC_PATCHER.md).
