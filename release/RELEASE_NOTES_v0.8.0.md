## Browser Gamma Fix v0.8.0 — Edge update compatibility

### Fixed

- Added support for the current Microsoft Edge output-loop layout, including
  the layout used by Edge 156.

### More resilient update handling

- Edge recognition now accepts a bounded range of complete sRGB singleton
  constructor layouts rather than a short list of previously observed counts.
  Every candidate must still pass the existing strict color-object,
  factory/pointer and output-path validation.
- Added a bounded analyzer for Edge's fused ScreenWin output loop. When that
  layout stores only two safe usage-table bytes, the runtime uses verified
  unused code padding for its private three-entry table and redirects only the
  verified table-load instruction. No browser files are changed on disk.
- Output-path writes are ordered safely: table storage first, redirection next,
  loop-limit enablement last; disablement restores them in reverse order.

### Validation

- Added synthetic regression coverage for the fused/packed Edge table layout,
  table relocation, malformed table rejection and the expanded constructor
  range.
- The complete automated suite passes (61 tests).
