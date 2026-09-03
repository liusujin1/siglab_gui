# Python VNA

Python replacement for the MATLAB USB-4431 test UI under `dsa/`.

## Scope

First-pass implementation:

- NI-DAQmx backend abstraction with a simulated backend for development
- Signal processing for time history, FFT, autospectrum, FRF, coherence, cross-spectrum, correlation, and impulse response
- Session model and file export to JSON, NPZ, CSV, and optional HDF5
- PySide6/pyqtgraph GUI for channel setup, acquisition control, excitation setup, and live plots

## Install

```powershell
Set-Location 'D:\SynologyDrive\codex\vna'
& 'D:\SynologyDrive\codex\vna\scripts\setup_python_vna.ps1'
& 'D:\SynologyDrive\codex\vna\.venv\Scripts\Activate.ps1'
```

If NI hardware access is required, install NI-DAQmx separately so `nidaqmx` can find the driver runtime.

## Run

Default NI backend:

```powershell
& 'D:\SynologyDrive\codex\vna\.venv\Scripts\python.exe' -m python_vna.app --device Dev1
```

Simulated backend:

```powershell
& 'D:\SynologyDrive\codex\vna\.venv\Scripts\python.exe' -m python_vna.app --backend simulated
```

## Repository layout

The repository root is the only build and release source of truth. Product code is
kept in one `python_vna` package and split into three ownership areas declared in
`config/vna_suite.json`:

- `shared`: code used by both desktop applications
- `python_vna_test`: acquisition and `PythonVNATest.exe`
- `vianalysis`: analysis and `VIanalysis.exe`

The older `vna_diagnose_isolated` and `vna_diagnostic_current` worktrees are
migration sources only. Normal build and publish scripts never copy from them.

Run the repository preflight before structural changes:

```powershell
& '.\scripts\preflight_vna_suite.ps1' -CheckLegacyWorktrees
```

Create short-lived feature worktrees from the canonical branch with:

```powershell
& '.\new_vna_feature_worktree.bat'
```

## Tests

```powershell
& '.\scripts\test_vna_product.ps1' -Product python_vna_test
& '.\scripts\test_vna_product.ps1' -Product vianalysis
& '.\.venv\Scripts\python.exe' -m pytest tests
```

## Build

Build both applications and their shared dependency directory from the canonical
root:

```powershell
& '.\build_vna_suite_release.bat'
```

Only use the legacy migration command after reviewing its preview:

```powershell
& '.\scripts\sync_worktrees_and_build_suite.ps1'
& '.\scripts\sync_worktrees_and_build_suite.ps1' -LegacyMigration -Apply
```
