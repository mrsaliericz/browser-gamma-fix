## Browser Gamma Fix v0.9.1 — ready for Edge 158

### Why

The new daily compatibility check installed Edge Beta 158 and Edge Dev 159 and
found that both would be **Unsupported**: the compiler now places the small
`SetOutputColorSpaceAndFormat` helper in two pieces about 120 MB apart (entry
and body joined by jumps), so the exact byte match no longer found it.

### Fixed

- The output helper is now recognized by its exact instructions and control
  flow instead of one contiguous byte string. Every instruction must still
  match exactly; only block placement and jump encoding may differ. All
  analyzers (Chrome hook, Edge loop analyzers and the detour fallback) use it.
- Split helpers are verified instruction by instruction in the browser process
  before any write.

### Validation

- Edge Beta 158.0.4370.7: supported with the verified strict analyzers
  (`gamma=strict, output=strict`). The detour fallback was also executed on
  this build's own split helper code and behaved exactly as intended.
- Edge 157.0.4341.42 (current stable), Chrome 156/158/159 and four Electron
  builds remain supported.
- 3 new tests for split, relocated and modified helpers; 83 tests in total.
