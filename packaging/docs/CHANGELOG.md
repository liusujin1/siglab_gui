# SigLab TestKit release notes

## 0.1.0-beta.10 — 2026-08-28

- Component versions: Samba/Communication Server `0.1.5`, SIDMAT `0.1.4`.
- Restored the original `1840x1240` Samba reference composition and scales the
  complete canvas uniformly instead of changing individual page arrangements.
- Converts the physical-pixel reference through the Windows display scale. At
  200%, the reference becomes `920x620` Qt logical pixels, preserving the old
  screenshot's title bar, sidebar, navigation and page-content proportions.
- Extended adaptive sizing from fonts to fixed controls, layout margins,
  spacing, icons and stylesheet geometry. The original horizontal Status
  arrangement is retained and now fits inside the scaled content viewport.
- At `1920x1040` and 100%, the full reference uniformly fits to `1484x1000`;
  at the local `1440x852`/200% work area it opens at `920x620`.
- Added explicit layout regression coverage for both logical work areas and
  verifies every status lamp and the event table remain inside the viewport.

Validation: adaptive layout tests, complete Samba page tests, rendered compact
Status screenshot, compileall, full Samba/SIDMAT release gate and frozen smoke.

## 0.1.0-beta.9 — 2026-08-28

- Component versions: Samba/Communication Server `0.1.4`, SIDMAT `0.1.4`.
- Made font density respond more strongly than window geometry. On the local
  `1440x852` Qt work area, both applications now use an 8-pixel normal font
  instead of beta.8's 10-pixel floor; `1920x1040` remains at 12 pixels.
- Converted shared pyqtgraph axes, labels, legends, cursors, data tips and
  markers from fixed 9-point fonts to adaptive logical-pixel fonts.
- Scaled SIDMAT excitation LEDs and Samba reference status/toggle labels, and
  removed a runtime stylesheet that could restore an unscaled 11-pixel font.

Validation: local real-screen render at `930x585` with 8px UI/plot fonts,
adaptive font tests, full Samba/SIDMAT gate, compileall and frozen smoke.

## 0.1.0-beta.8 — 2026-08-27

- Component versions: Samba/Communication Server `0.1.3`, SIDMAT `0.1.3`.
- Replaced the beta.7 fixed window with one shared logical-work-area metrics
  engine used by Samba and SIDMAT. Window, minimum size, font and primary
  shell density now adapt without multiplying the Windows DPI ratio.
- On the local `2880x1800` panel at 200% (`1440x852` Qt work area), both GUIs
  start at `930x585`; on `1920x1080` at 100%, both start at `1240x780`.
- Made Samba navigation, loop-state panel and title controls compact on smaller
  logical desktops; compacted SIDMAT header/sidebar and shortened its title
  only when the available logical width requires it.

Validation: pure sizing matrix, real local Qt screen geometry, local rendered
GUI screenshots, cross-package GUI tests, full source gate and frozen smoke.

## 0.1.0-beta.7 — 2026-08-27

- Component versions: Samba/Communication Server `0.1.2`, SIDMAT `0.1.2`.
- Fixed the beta.6 shared scaling regression: Qt 6 now owns high-DPI
  conversion and all frozen executables declare Windows Per-Monitor-V2.
- Changed both GUI defaults from `1840x1240` to `1280x800` logical pixels,
  with a `960x640` minimum, specifically validated for a
  `1920x1080` desktop at 100% scaling.
- Reduced the shared font baseline to 12 logical pixels and compacted the
  updated SIDMAT header, workflow sidebar and fixed control geometry while
  retaining the latest layout and plot interactions.

Validation: 1920x1080/100% geometry regression tests, cross-package DPI tests,
full Samba/SIDMAT test gate, compileall and frozen executable smoke.

## 0.1.0-beta.6 — 2026-08-27

- Component versions: Samba/Communication Server `0.1.1`, SIDMAT `0.1.1`.
- Fixed Samba cross-computer enlargement by using one deterministic Qt DPI
  policy, a 13-pixel application font, and a non-multiplied stylesheet scale.
- Removed inherited Qt scale overrides from packaged launchers and normalized
  oversized legacy page fonts.
- Integrated the 2026-08-23 SIDMAT layout update: frameless application
  header, compact workflow sidebar, draggable workspace and Records/Plot
  cursor, marker, zoom and CSV interaction controller.
- Preserved the independent Communication Server, distinct application icons,
  NumPy signal processing and internal MAT v5 support.

Validation: 100%/200% subprocess DPI tests, full Samba/SIDMAT test gate,
compileall, frozen GUI/Server smoke, automatic server startup and standalone
Server copy.

## 0.1.0-beta.5 — 2026-08-14

- Unified Samba and SIDMAT Qt DPI bootstrap and monitor-aware font scaling.
- Clear inherited Qt scale environment overrides by default, with an explicit
  `SIGLAB_RESPECT_QT_SCALE=1` escape hatch.
- Clamped SIDMAT startup size and minimum size to the logical work area at
  100%, 125%, 150%, and 200% display scaling.
- Rebuilt the current eight-group SIDMAT layout with the 2x2 plot workspace.
- Added distinct Samba, SIDMAT, and Communication Server source/raster/ICO
  icons and wired them into all three frozen executables and runtime windows.
- Kept Communication Server independently runnable and copyable for another
  controller computer.
- Preserved the beta.4 NumPy signal/MAT v5 runtime with no SciPy payload.

Validation: full Samba/SIDMAT test gate, compileall, frozen GUI/Server smoke,
automatic local-server startup, standalone Server copy, and SHA256 manifest.
