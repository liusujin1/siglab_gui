# Pilot acceptance checklist

## Clean machine

- [ ] Windows 10/11 x64, no Python installed.
- [ ] ZIP SHA256 matches the published sidecar.
- [ ] Extracted into a path containing spaces and Chinese characters.
- [ ] `Preflight.bat` passes; 100%, 125%, and 150% DPI checked.
- [ ] Windows Defender scan completes without a detection.

## Mock

- [ ] SAMBA and SIDMAT start with `test-mock.json`.
- [ ] SAMBA Records/Plot opens `samples\synthetic_record.csv`.
- [ ] Windows can close normally and diagnostics ZIP contains no secrets.

## Local hardware

- [ ] With no server running, SAMBA Connect starts exactly one server.
- [ ] With no server running, SIDMAT Connect starts exactly one server.
- [ ] Simultaneous Connect still results in one server process.
- [ ] Both clients connect to the same COM/baud through the same endpoint.
- [ ] SIDMAT acquisition and SAMBA status refresh run concurrently.
- [ ] Disconnecting one client does not interrupt the other.
- [ ] Last client detach releases COM; reconnect reuses the tray server.
- [ ] Real-time curve 3/40 channels and 100 ms sampling work.
- [ ] Saved records reopen; FFT/PSD/filter/export work.
- [ ] Before/after 40 DGMOS definitions, DGETP/DGETI, event config, and
      SavedTraceNum are unchanged.
- [ ] `Stop-TestKit.bat` refuses while either GUI is running, then exits the
      server without force-killing it after both GUIs close normally.

## Failure behavior

- [ ] Missing CommServer EXE gives a component-missing error.
- [ ] Slow startup times out without freezing either GUI.
- [ ] Remote endpoint failure never starts a local server.
- [ ] Direct Serial never starts the server and reports a busy COM clearly.
