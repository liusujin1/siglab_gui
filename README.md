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

## Tests

```powershell
python -m unittest discover -s tests
```

## SAMBA / SIDMAT controller tools

The `codex/samba` branch also contains the controller applications:

- [`python_samba`](python_samba/README.md) — controller configuration and status UI.
- [`python_sidmat`](python_sidmat/README.md) — trace acquisition and transfer-function analysis.

Both applications default to the shared Communication Server, so only one
process owns the physical serial port while complete RCI exchanges from both
clients are executed through a global FIFO. A standalone Windows server and
LAN/Tailscale discovery let both GUIs find and connect to the controller host
without entering a fixed IP address; see the SAMBA README for setup details.

The first portable Windows pilot is built with
[`packaging/build_testkit.ps1`](packaging/build_testkit.ps1). It produces the
two onedir GUIs plus the single-file Communication Server, launcher/preflight/
diagnostic scripts, profiles, documentation, manifests, and a SHA256 sidecar.
See [`packaging/README.md`](packaging/README.md) for the reproducible build.
