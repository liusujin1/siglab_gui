# Online update archives

Online manifests require ZIP for both full and incremental packages. Clients
can extract these without installing 7-Zip. Full 7z archives remain available
for manual downloads; no extraction executable is added to the application.

The publisher creates a Deflate ZIP alongside the full 7z archive and uploads
both before replacing the manifest. Existing releases can be republished with
`-UseExistingArtifacts` without rebuilding executables.

Every reverse proxy in front of `/pythonvna-admin/` must allow the full ZIP
upload size. The included nginx and publisher configurations allow 300 MiB;
external reverse proxies must also allow at least this amount. An HTTP 413
prevents publication; do not remove previous packages before resolving it.

Local cleanup keeps the latest two release versions and update archives whose
target is a retained version. Remote retention defaults to four full archive
files (ZIP and 7z), with current manifest references additionally protected.
