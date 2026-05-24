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
