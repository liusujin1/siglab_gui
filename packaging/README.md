# Building the Windows portable TestKit

The authoritative build entry is `build_testkit.ps1`. It requires CPython
3.12 x64 on the build machine and creates an isolated venv under `.build`.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_testkit.ps1
```

The command runs the SAMBA and SIDMAT suites serially, compiles both source
trees, builds the two GUIs with one shared `_internal` runtime plus a standalone
one-file Communication Server, runs offscreen executable smoke tests,
scans staged text for known sensitive material, writes per-file SHA256 values,
and creates:

```text
release\SigLab-TestKit-0.1.0-beta.5-win-x64.zip
release\SigLab-TestKit-0.1.0-beta.5-win-x64.zip.sha256
```

Generated `.build`, `release`, PyInstaller `build/dist`, caches, egg-info,
screenshots, decompiled references, hardware captures, tokens, and recovery
files are excluded from source control and the release payload.

The beta output is deliberately unsigned and UPX is disabled. Production
promotion requires code signing, a clean Windows 10/11 x64 VM test without
Python, Defender/SmartScreen checks, and the hardware checklist in
`docs/TEST-CHECKLIST.md`.
