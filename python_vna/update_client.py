from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from python_vna import __version__


CONFIG_FILE_NAME = "update_config.json"
CONFIG_EXAMPLE_FILE_NAME = "update_config.example.json"
DEFAULT_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True, slots=True)
class UpdateSettings:
    manifest_url: str
    channel: str = "stable"


@dataclass(frozen=True, slots=True)
class UpdatePackage:
    version: str
    url: str
    sha256: str
    size: int
    archive_type: str
    kind: str
    safe_overlay: bool = True


@dataclass(frozen=True, slots=True)
class UpdateDecision:
    available: bool
    current_version: str
    latest_version: str
    package: UpdatePackage | None = None
    message: str = ""


def suite_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def config_path(root: Path | None = None) -> Path:
    return (root or suite_root()) / CONFIG_FILE_NAME


def config_example_path(root: Path | None = None) -> Path:
    return (root or suite_root()) / CONFIG_EXAMPLE_FILE_NAME


def load_update_settings(root: Path | None = None) -> UpdateSettings | None:
    env_url = os.environ.get("PYTHON_VNA_UPDATE_MANIFEST_URL", "").strip()
    env_channel = os.environ.get("PYTHON_VNA_UPDATE_CHANNEL", "").strip()
    if env_url:
        return UpdateSettings(manifest_url=env_url, channel=env_channel or "stable")

    path = config_path(root)
    if not path.exists():
        return None

    payload = json.loads(path.read_text(encoding="utf-8"))
    manifest_url = str(payload.get("manifest_url", "")).strip()
    if not manifest_url:
        return None
    channel = str(payload.get("channel", "stable") or "stable").strip() or "stable"
    return UpdateSettings(manifest_url=manifest_url, channel=channel)


def fetch_manifest(manifest_url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, object]:
    request = Request(manifest_url, headers={"User-Agent": "PythonVNA-Updater"})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        text = response.read().decode(charset)
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("Update manifest must be a JSON object.")
    return payload


def resolve_url(manifest_url: str, url: str) -> str:
    return urljoin(manifest_url, url)


def version_key(version: str) -> tuple[int, ...]:
    values = [int(part) for part in re.findall(r"\d+", version)]
    return tuple(values or [0])


def is_newer_version(candidate: str, current: str) -> bool:
    return version_key(candidate) > version_key(current)


def select_update(
    manifest: dict[str, object],
    *,
    current_version: str = __version__,
    manifest_url: str = "",
    allow_full: bool = True,
) -> UpdateDecision:
    latest = str(manifest.get("latest", "")).strip()
    if not latest:
        raise ValueError("Update manifest is missing 'latest'.")

    if not is_newer_version(latest, current_version):
        return UpdateDecision(
            available=False,
            current_version=current_version,
            latest_version=latest,
            message="Already up to date.",
        )

    updates = manifest.get("updates", [])
    if isinstance(updates, list):
        for item in updates:
            if not isinstance(item, dict):
                continue
            if str(item.get("from", "")).strip() != current_version:
                continue
            if str(item.get("to", "")).strip() != latest:
                continue
            package = _package_from_manifest_item(
                item,
                version=latest,
                kind="incremental",
                manifest_url=manifest_url,
            )
            return UpdateDecision(
                available=True,
                current_version=current_version,
                latest_version=latest,
                package=package,
                message="Incremental update available.",
            )

    full_item = manifest.get("full")
    if allow_full and isinstance(full_item, dict):
        package = _package_from_manifest_item(
            full_item,
            version=latest,
            kind="full",
            manifest_url=manifest_url,
        )
        return UpdateDecision(
            available=True,
            current_version=current_version,
            latest_version=latest,
            package=package,
            message="Full update available.",
        )

    message = (
        "A newer version is listed, but no matching incremental package is available."
        if not allow_full
        else "A newer version is listed, but no usable update package is available."
    )
    return UpdateDecision(
        available=False,
        current_version=current_version,
        latest_version=latest,
        message=message,
    )


def _package_from_manifest_item(
    item: dict[str, object],
    *,
    version: str,
    kind: str,
    manifest_url: str,
) -> UpdatePackage:
    url = str(item.get("url", "")).strip()
    sha256 = str(item.get("sha256", "")).strip()
    if not url:
        raise ValueError("Update package is missing 'url'.")
    if not sha256:
        raise ValueError("Update package is missing 'sha256'.")
    archive_type = str(item.get("archive_type", "zip") or "zip").strip().lower()
    safe_overlay = bool(item.get("safe_overlay", True))
    size = int(item.get("size", 0) or 0)
    return UpdatePackage(
        version=version,
        url=resolve_url(manifest_url, url) if manifest_url else url,
        sha256=sha256.lower(),
        size=size,
        archive_type=archive_type,
        kind=kind,
        safe_overlay=safe_overlay,
    )


def updater_executable(root: Path | None = None) -> Path:
    executable_name = "PythonVNAUpdater.exe" if os.name == "nt" else "PythonVNAUpdater"
    return (root or suite_root()) / executable_name


def cleanup_stale_updater_runner(root: Path | None = None) -> None:
    if os.name != "nt":
        return
    runner = (root or suite_root()) / "PythonVNAUpdaterRunner.exe"
    if not runner.exists():
        return
    try:
        runner.unlink()
    except OSError:
        pass


def prepare_isolated_updater_runtime(root: Path, updater: Path) -> tuple[Path, Path]:
    runtime_root = Path(tempfile.mkdtemp(prefix="python_vna_updater_runtime_")).resolve()
    launch_path = runtime_root / "PythonVNAUpdaterRunner.exe"
    shutil.copy2(updater, launch_path)

    internal_source = root / "_internal"
    if internal_source.exists():
        shutil.copytree(internal_source, runtime_root / "_internal")

    return launch_path, runtime_root


def launch_updater(
    *,
    manifest_url: str,
    current_version: str = __version__,
    target_dir: Path | None = None,
    restart_executable: str = "",
) -> None:
    root = target_dir or suite_root()
    updater = updater_executable(root)
    if not updater.exists():
        raise FileNotFoundError(f"Updater executable was not found: {updater}")
    launch_path = updater
    cleanup_root: Path | None = None
    if getattr(sys, "frozen", False) and os.name == "nt":
        try:
            cleanup_stale_updater_runner(root)
            launch_path, cleanup_root = prepare_isolated_updater_runtime(root, updater)
        except OSError:
            launch_path = updater

    args = [
        str(launch_path),
        "--manifest-url",
        manifest_url,
        "--current-version",
        current_version,
        "--target-dir",
        str(root),
    ]
    if cleanup_root is not None:
        args += ["--cleanup-root", str(cleanup_root)]
    if restart_executable:
        args += ["--restart", restart_executable]
    subprocess.Popen(args, cwd=str(launch_path.parent), close_fds=True)
