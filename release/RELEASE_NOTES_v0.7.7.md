## Browser Gamma Fix v0.7.7

### Tray status at a glance

- The tray icon now shows whether **Windows HDR** is on: a gold sparkle appears
  in its upper-right corner when Advanced Color is enabled.
- A lower-right health dot is green for an enabled and healthy correction, red
  for an unsupported layout or other correction error, and gray while the fix
  is disabled or switching.
- The menu can now be opened with a left click, a double-left-click, or a
  right-click.

### Usability

- The menu displays the application version immediately.
- Project website and author email are available as direct tray-menu actions;
  About has been kept deliberately compact.
- High-DPI support now uses a Per-Monitor V2 application manifest and
  DPI-aware tray icon sizing for sharper UI on scaled displays.
- Automatic release checks now run at startup and then once per hour.

### Safety

This release does not alter the correction recipe or browser color paths.
Chrome, Edge, Brave and Vivaldi remain patched only in process memory, and
unsupported browser layouts continue to fail closed.
