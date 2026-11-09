## Browser Gamma Fix v0.9.0 — update-resilient analyzers

### Why

Almost every Edge (and some Chrome) update changed how the compiler laid out
the two places Browser Gamma Fix adjusts, even though the browser logic itself
did not change. Each of those layout changes showed up as **Unsupported** until
a new release added the next pattern.

### New: layout-independent fallbacks

The verified, version-specific analyzers are still tried first. When one of
them rejects a new build, that part of the fix now falls back independently to
an equivalent that does not depend on compiler layout:

- **GPU process (gamma 2.2):** every place that builds a color space from the
  canonical sRGB transfer function immediately followed by the BT.709 gamut is
  redirected to the adjacent gamma 2.2 constant. The number of such places,
  their instruction encoding and the singleton/factory shape no longer matter.
  Loads of the same constant that are *not* paired with BT.709 (Display-P3 and
  other wide-gamut spaces, Skia's own comparisons) are left unchanged.
- **Browser process (SDR output on the scRGB/F16 path):** instead of patching
  the ScreenWin loop (compact, split, fused, ... layouts), a small detour at the
  entry of the stable `SetOutputColorSpaceAndFormat` helper repeats each
  wide-color-gamut output setting for SDR. ScreenWin only sets that usage when
  Windows HDR is active, so the result is the same as the earlier loop patch.
  The detour code is placed only in INT3 padding between declared functions and
  is enabled last / removed first.

Every fallback keeps the fail-closed rules: unique matches, exact original-byte
verification before each write, role-separated writes and no changes to
browser files on disk.

### Diagnostics

- The diagnostic log now states which strategy each part uses, for example
  `gamma=strict, output=fallback`, and lists why a strict analyzer declined.
- An unsupported build now reports the reason of every analyzer that was tried.
- New `tools/compatibility_report.py` runs the analyzers read-only against all
  installed Chrome/Edge/Brave/Vivaldi channels or given DLLs.
- New daily GitHub workflow analyzes the runner's Stable Chrome/Edge and
  downloaded Beta/Dev/Canary builds, giving weeks of warning before a layout
  reaches Stable.

### Validation

- 19 new automated tests: constructor-count drift, mixed BT.709/P3 loads,
  movups pairs in either order, partial copies, chained detour caves,
  padding-only cave selection, role routing and write ordering. On Windows the
  suite also executes the real helper bytes with and without the detour; all
  80 tests pass.
- The current Microsoft Edge build, which v0.8.0 reported as Unsupported
  (77 sRGB singleton constructors, below the previous 97-120 bound), is now
  supported via the GPU gamma fallback (77 redirects) and the verified fused
  output-loop analyzer. Chrome 156.0.8078.80 continues to use the strict
  analyzers (90 gamma writes).
- Read-only analysis of four real Chromium x64 Windows binaries (Electron 42,
  44, 45.0 and 45.2) — whose layouts the strict Chrome analyzer does not
  recognize — produced complete plans with 40–53 gamma redirects. On all four,
  the detour was executed on the binary's own code and only mirrored
  wide-color-gamut settings into SDR.
