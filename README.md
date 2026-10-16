# Browser Gamma Fix

**Browser Gamma Fix makes ordinary SDR web content look correct in Windows HDR.**

When Windows HDR is on, Chromium-based browsers can make SDR shadows look too
bright. This small Windows tray app corrects that behavior to **gamma 2.2** in
the normally installed 64-bit versions of **Google Chrome, Microsoft Edge,
Brave and Vivaldi**.

> **[Download Browser Gamma Fix](https://github.com/mrsaliericz/browser-gamma-fix/releases/latest)**

It is free, open source and MIT-licensed. If it helps your setup, you can
optionally [buy me a coffee ☕](https://buymeacoffee.com/mrsaliericze).

## What it does — and does not change

- Fixes the appearance of ordinary SDR/sRGB web pages when Windows HDR is on.
- Keeps **Display-P3 / wide-gamut content** wide gamut.
- Keeps **native HDR video**, PQ, HLG, HDR black levels and highlights unchanged.
- Does not modify Chrome, Edge, Brave or Vivaldi files on disk.
- Works alongside [`dwm_eotf_rs`](https://github.com/SERGEYDJUM/dwm_eotf_rs).
  Using both is recommended: Browser Gamma Fix handles the browser, while
  `dwm_eotf_rs` can continue correcting other Windows applications.

## Quick start

1. Enable **HDR** in Windows 11.
2. Download and extract `BrowserGammaFix-win64.zip` from the
   [latest release](https://github.com/mrsaliericz/browser-gamma-fix/releases/latest).
3. Keep the complete `Gamma22Tray` folder together — the EXE needs its
   `_internal` folder.
4. Run `Gamma22Tray.exe` normally; do **not** run it as administrator.
5. Keep using Chrome, Edge, Brave or Vivaldi as usual.

The program appears in the Windows notification area. Its menu opens with a
left-click, double-left-click or right-click.

## Tray icon guide

- A **gold sparkle** means Windows HDR is on.
- A **green dot** means the correction is active and healthy.
- A **red dot** means the current browser layout is not supported or needs
  attention.
- A **gray dot** means the correction is off or switching.

The menu lets you enable or disable the fix, start it with Windows, check for
updates, open the diagnostic log, visit the project website or contact the
author.

## Browser updates

Browser Gamma Fix checks for its own updates at startup and then hourly. It
also watches installed browser versions and safely restarts itself after a
recognized browser update. This has been verified through multiple real Chrome
and Edge updates.

An unfamiliar browser layout is rejected safely rather than guessed. If you
see a red tray dot or **Unsupported**, use **Check for updates…** in the menu
and update Browser Gamma Fix if a newer version is available.

## Important notes

- **SDR video:** browser video can follow a different compositor path. NVIDIA
  RTX Video HDR, when enabled, produces HDR output that this app intentionally
  leaves unchanged.
- **HDR photos:** HDR gain-map photos and wide-gamut images are intentionally
  not altered. Their appearance can therefore differ from Apple Photos or
  iCloud, even when HDR highlights are working correctly.
- **Antivirus:** the app is unsigned and must attach to browser processes and
  write process memory. Some antivirus products can flag this behavior. Do not
  disable security software; download only from this repository, inspect the
  source, or build it yourself if you are unsure.

## More information

- [Technical details and compatibility history](docs/TECHNICAL_DETAILS.md)
- [Legacy version-specific patcher documentation](archive/LEGACY_VERSION_SPECIFIC_PATCHER.md)
- [Release notes](release)
- [MIT license](LICENSE)

## Hi, I'm Jaroslav 👋

I'm **[Jaroslav Safar](https://jaroslavsafar.com)**, an independent developer
from the Czech Republic building iOS apps, Windows tools, open-source software
and web projects.

Selected work:

- iOS apps including TrayMate, Můj radar and Health Metrics Widgets
- Browser Gamma Fix
- **[JarosView](https://apps.microsoft.com/detail/9NDJ4BMB0DS0?hl=neutral&gl=CZ&ocid=pdpshare)** — a Windows app for correct HDR JXR screenshots and HDR image viewing

Portfolio: [jaroslavsafar.com](https://jaroslavsafar.com) · Contact:
[hello@jaroslavsafar.com](mailto:hello@jaroslavsafar.com)
