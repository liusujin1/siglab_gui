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

## Smart impact selection

The Modal parameter dialog contains the smart impact selection switch, FRF
deviation tolerance, evaluation frequency band, response-channel selection and
optional force limits. It is enabled by default for Avg acquisition with Every
Frame triggering, linear averaging and a target of at least three accepted hits.
Use independent frames (No Overlap), and configure the hammer reference channel
and its engineering-unit sensitivity before enabling force limits.

The first hit is provisional. A consistent pair establishes the reference group;
if the first two hits disagree, the third can establish a better pair. Subsequent
hits must agree with every accepted hit on all selected, enabled responses.
The default 25% tolerance controls symmetric complex-FRF RMS deviation, including
amplitude and phase. DC, weakly excited bins and response zeros are excluded.
The force must cover 20% of the evaluation band, and each selected response needs
at least eight usable bins. Narrow resonances can therefore remain valid.

Only accepted spectra contribute to the existing H1/coherence averages. Overload,
double-hit and optional force-limit failures do not advance the average count.
The status bar shows acceptance progress and rejection reasons, and highlights
ten consecutive rejections. Acquisition waits until the target is met or Stop is
pressed. An early stop preserves the actual count and an incomplete marker.
First-hit ranging is enabled by default with smart selection. The first valid
hammer hit sets only the reference channel's full scale to the next available
step at or above 1.2 times its raw voltage peak (up to 10 V), and the trigger voltage
to 35% of that peak. Those automatic settings remain locked for that run.
For example, a 1 V peak selects a 1.25 V full scale and a 0.35 V trigger; the trigger
percentage selector then shows 28% because its denominator is full scale.
The old reference full scale is bypassed only while finding that first valid hit.
Subsequent hits exceeding the locked range are rejected. Other channels retain
their configured range checks, and actual hardware overloads are always checked.
USB-4431 acquisition uses its 10 V hardware range independently of these software
limits. Trigger changes occur between frames on the acquisition thread.
VNA/JSON saves retain the settings and up to 200 hit summaries; VNA stores the
extension as ASCII JSON in `SLm.modal.py_hit_selection_json` alongside the existing
MATLAB fields. The latest hit remains available as the time-domain preview.

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
