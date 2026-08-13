# Operations

## Profiles

- `test-mock.json`: UI and data-flow testing without a controller.
- `test-local.json`: local `127.0.0.1:47619`; Connect auto-starts the packaged
  Communication Server.
- `test-remote.example.json`: template only. Copy it outside the extracted
  release and reference an external token file.

Command line `--comm-server-exe` takes priority over
`SIGLAB_COMM_SERVER_EXE`, then the standard
`apps\CommServer\PythonSambaCommServer.exe` location, then
the GUI directory. A frozen client never calls a system Python interpreter.

## Upgrade and rollback

Keep beta versions in separate directories. Stop measurement, logging,
real-time curves, and trace downloads; wait for monitor-slot recovery; then
disconnect and close both clients before exiting the server. Run the new version with the mock
profile before hardware. Rollback means reopening the previous directory;
per-user data is not removed or overwritten.

## Removal

Run `Stop-TestKit.bat`, then delete the extracted version directory. Runtime
data is retained under Documents and LocalAppData. Delete those directories
only when their records, logs, configuration, and recovery files are no longer
needed.

## Known pilot limitations

- The beta ZIP is portable and may be unsigned; Windows SmartScreen can warn.
- USB/serial device drivers are external prerequisites.
- No firewall rule is installed automatically. Remote access must be enabled
  explicitly on a trusted LAN/Tailscale network.
- This pilot is Windows x64 only. PyInstaller output is not cross-platform.
