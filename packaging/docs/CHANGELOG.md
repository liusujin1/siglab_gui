# SigLab TestKit release notes

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
