## Browser Gamma Fix v0.7.8 — Edge 155.0.4283.62 compatibility

### Fixed

- Adds support for Microsoft Edge `155.0.4283.62`.
- This Edge update adds one canonical BT.709/sRGB singleton constructor,
  changing the verified count from 101 to 102.

### Validation

- Read-only analysis of the installed `msedge.dll`
  (`1906B766460DA798672A9845E2772130674F7D3D866E8C63140ED88A1FE1C88D`)
  produced one factory/pointer tuple, 102 complete sRGB initializer loads and
  stores, one verified SDR/WCG/HDR output path, and a complete 104-write plan.
- The new count is accepted only with the existing constant-block, complete
  load/store, factory/pointer and output-path validation. Unknown or ambiguous
  layouts still fail closed.

Native HDR video, PQ/HLG, Display-P3 and browser files on disk are unchanged.
