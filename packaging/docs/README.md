# SigLab TestKit 0.1.0-beta.10

This is a portable Windows 10/11 x64 pilot package for SAMBA and SIDMAT. It
does not require Python and does not install a Windows service.

## First run

1. Extract the complete ZIP to a user-writable directory. Do not run inside
   the ZIP. Samba and SIDMAT must stay beside their shared
   `apps\SigLabSuite\_internal` directory.
2. Run `Preflight.bat`.
3. Run `Start-Samba.bat`, `Start-SIDMAT.bat`, or `Start-TestKit.bat`.
4. For a no-hardware check, pass the mock profile:
   `Start-TestKit.bat -Profile config\test-mock.json`.
5. For hardware, keep the default Server backend, select the correct COM port
   and 57600 baud, then click Connect. The GUI starts the packaged
   Communication Server automatically when the endpoint is loopback.

The Communication Server is the only owner of the physical serial port.
SAMBA and SIDMAT must use the same server endpoint, COM port, and baudrate.
Direct Serial mode is diagnostic-only and cannot share a COM port already
owned by the server.

The packaged GUI removes inherited Qt scale overrides and uses Qt 6 native
high-DPI conversion with an explicit Windows Per-Monitor-V2 manifest.
Source-tree diagnostic runs may opt in with `SIGLAB_RESPECT_QT_SCALE=1`;
frozen releases intentionally ignore that override.

Samba and SIDMAT are packaged together under `apps\SigLabSuite` and share one
Python/Qt/NumPy runtime. The beta.10 runtime keeps beta.4's Qt/OpenGL trimming,
uniformly fits Samba's original 1840x1240 physical-pixel composition through
the Windows display scale while SIDMAT keeps its own logical-work-area layout,
includes the latest SIDMAT workflow/interactive-plot layout. It replaces the TestKit's SciPy dependency
with its tested internal signal and uncompressed MAT v5 layers; English and
Simplified Chinese remain supported. Each executable has its own source and
PE icon under `assets\` (`samba_icon`, `sidmat_icon`, `commserver_icon`).
`apps\CommServer\PythonSambaCommServer.exe`
remains a self-contained single-file program and may be copied by itself to a
different controller computer.

The signal implementation adapts BSD-licensed SciPy algorithms. See
`THIRD_PARTY_NOTICES.md` for the required attribution.

## Stopping safely

Stop SIDMAT measurement and SAMBA logging/real-time curves first, then close
both applications normally. `Stop-TestKit.bat` refuses to stop the server while
either GUI is still running; after they close, it asks the server to shut down
through its protocol. It never force-kills a process.

## Data and diagnostics

User data defaults to `%USERPROFILE%\Documents\SigLabSuite`; `logs`, `config`,
`recovery`, and sanitized `diagnostics` default to `%LOCALAPPDATA%\SigLabSuite`.
`Collect-Diagnostics.bat` excludes tokens, raw snapshots, controller config,
user names, host names, client IDs, and unredacted IP/COM details.

Remote profiles never start a local server. Copy
`config\test-remote.example.json`, keep the token outside the package, and set
only the token file path in the profile. Remote firewall access is opt-in.

See `OPERATIONS.md` and `TEST-CHECKLIST.md` for detailed operation and pilot
acceptance steps.
