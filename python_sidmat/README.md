# python_sidmat

Vendor-free **SiDiMaT** measurement software for IDE TC-MFD / OPTICON active
vibration-isolation controllers. Rebuilt from the decompiled
`SAMBA19xLib`/`SAMBA19xUI` measurement logic using the pure-RCI stack from
`python_samba` (no Rci32.dll or vendor CommServer needed). The bundled shared
Communication Server lets SIDMAT measure while SAMBA continues reading or tuning.

> **Sidmat scope — measurement and plotting only.** `python_samba` remains the
> controller configuration tool. Sidmat connects to the controller, selects
> diagnostic/excitation signals, acquires multi-average traces, estimates the
> transfer function, and saves/reopens measurement data and figures. It does
> not duplicate the full controller-center pages.

## Layout

```
Connect      server (default) | serial | mock, port, baud, server, status
Trace Info   Ch0/Ch1 IO signals, length, undersample, anti-alias, averages
Excitation   type, 4 params, offset, injection IO point, four noise filters
Helping Hand velocity/position signal routing, measurement stage selection
Plots        time waveform · FRF magnitude (dB) · phase · coherence
Files        .sidimat19x raw MAT files · .idefigure plot MAT files · CSV CLI
```

## Install

```bat
cd D:\AI_test\claude\python_samba && pip install -e .      :: required dependency
cd D:\AI_test\claude\python_sidmat && pip install -e .[gui,dev,mat]
```

Requires Python ≥ 3.11. `python_samba` must be installed editable so the
two projects share the same RCI protocol layer.

## Run

```bat
:: GUI (shared server by default — click Connect, then Start Measurement)
python -m python_sidmat.app

:: CLI headless measurement → CSV
python -m python_sidmat.cli --mock --length 8192 --avg 3 -o out.csv
python -m python_sidmat.cli --port COM1 --baud 57600 --length 8192 --avg 5

:: Diagnostic-only direct serial mode (exclusively owns COM1)
python -m python_sidmat.cli --backend serial --port COM1 --baud 57600 --length 8192
```

## Test

```bat
python -m pytest
```

## Architecture

```
ui/            PySide6 — main window, TraceInfo, Excitation, plots, IO picker
measurement/   TraceParameters, ExcitationParameters, MeasurementEngine
analysis/      windows.py (9 window types), pwelch.py (Welch H1), types.py
backend/       Controller (wraps python_samba session), iosignal.py (IOType+names)
└── python_samba  → protocol/ (RCI frame+commands) · services/ (session) · transport/
```

The default `server` backend connects to `127.0.0.1:47619` and auto-starts the
single-instance tray server. SAMBA status reads and SIDMAT DASTA/DGTAS/DGTBB
measurement requests share one global FIFO. There is intentionally no measurement
lease: another client may update parameters during acquisition, and the last write
wins. Use `--server`, `--token-file`, and `--no-auto-start` for a Tailscale server.

* **Measurement engine** ports `SAMBA19xUI.UserControls.TraceInfo`
  `StartTracing`/`LookForTraceStatus`/`GetTraceData`: per average
  `DASTA → poll DGTAS → DGTBV` (16 pairs/chunk), or the original binary
  `DGTBB` path (40 pairs/chunk) when Fast Data Load is enabled. It runs on a
  worker thread with progress and cancellation. Stored samples keep the
  controller's base rate plus the undersample factor; plots and CSV time axes
  use the effective rate.
* **Welch H1** ports the `SAMBA19xLib.PwelchTF.pwelch` equations (window
  formulas, negative-frequency folding, H1 = Cxy/Sxx, coherence, RMS
  amplitude spectra), while fixing incomplete-overlap and odd-FFT edge cases.
  All nine original window functions are reproduced.
* **IO naming** ports `SAMBA19xLabels.GetIOName` and reuses
  `python_samba.ui.label_files` so channel labels match the original software.
* **Helping Hand** routes velocity and position diagnostic signals without
  touching individual loop enable states; the latter stays in `python_samba`'s
  controller workflow.
* **Offline Tuner** applies the legacy `FilterTF` equations and generates the
  corresponding closed-loop curve from the filtered open-loop result.
* **File compatibility** uses the original MATLAB v5 field layout for both
  `SiDiMat19x` raw files and `IdeFigure` plot files.

## Verification

* Unit tests cover window formulas, pwelch against known signals, the mock
  acquisition loop, and IO naming — all green without hardware.
* GUI smoke: `mock` backend → Connect → Start Measurement → four plots draw.
* Real hardware: connect via `server` (or diagnostic `serial`), verify amplitude/phase/coherence
  against the official SiDiMaT with identical parameters.

## Deliberate boundary

Controller parameter pages, global loop tuning, 12-axis controller tables,
and report generation remain in `python_samba`; Sidmat consumes the configured
controller and focuses on acquisition, analysis, plotting, and measurement
file exchange.
