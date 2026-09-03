from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import traceback
from urllib.request import Request, urlopen
import zipfile

from python_vna.update_client import fetch_manifest, select_update

try:
    from PySide6 import QtCore, QtWidgets
except Exception:  # pragma: no cover - fallback for non-GUI environments
    QtCore = None
    QtWidgets = None


CHUNK_SIZE = 1024 * 1024


class UpdateCancelled(RuntimeError):
    pass


def _resolve_within(root: Path, relative_path: str | Path) -> Path:
    root_path = root.resolve()
    candidate = (root_path / relative_path).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise RuntimeError(f"Path escapes update root: {relative_path}") from exc
    return candidate


class ProgressReporter:
    def __init__(self) -> None:
        self._enabled = QtWidgets is not None
        self._app = None
        self._window = None
        self._label = None
        self._bar = None
        self._cancel_button = None
        self._cancelled = False
        if self._enabled:
            self._app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
            self._app.setApplicationName("PythonVNA Updater")
            self._window = QtWidgets.QDialog()
            self._window.setWindowTitle("PythonVNA 更新")
            self._window.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)
            self._window.setWindowFlag(QtCore.Qt.WindowCloseButtonHint, True)
            self._window.setModal(True)
            self._window.rejected.connect(self.cancel)

            layout = QtWidgets.QVBoxLayout(self._window)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(10)

            self._label = QtWidgets.QLabel("准备更新...")
            self._label.setWordWrap(True)
            self._bar = QtWidgets.QProgressBar()
            self._bar.setRange(0, 0)
            self._bar.setTextVisible(True)
            self._bar.setFormat("处理中...")
            self._cancel_button = QtWidgets.QPushButton("取消")
            self._cancel_button.clicked.connect(self.cancel)

            layout.addWidget(self._label)
            layout.addWidget(self._bar)
            layout.addWidget(self._cancel_button, alignment=QtCore.Qt.AlignRight)

            self._window.resize(480, 160)
            self._window.show()
            self._pump()

    def _pump(self) -> None:
        if self._app is not None:
            self._app.processEvents()

    def cancel(self) -> None:
        self._cancelled = True
        if self._enabled:
            self._label.setText("正在取消更新...")
            if self._cancel_button is not None:
                self._cancel_button.setEnabled(False)
            self._pump()

    def check_cancelled(self) -> None:
        self._pump()
        if self._cancelled:
            raise UpdateCancelled("用户取消了更新。")

    def set_busy(self, text: str) -> None:
        if not self._enabled:
            print(text)
            return
        self.check_cancelled()
        self._label.setText(text)
        self._bar.setRange(0, 0)
        self._bar.setFormat("处理中...")
        self._pump()

    def set_progress(self, current: int, total: int, text: str) -> None:
        if not self._enabled:
            print(f"{current}/{total}: {text}")
            return
        self.check_cancelled()
        total = max(total, 1)
        current = max(0, min(current, total))
        self._label.setText(text)
        self._bar.setRange(0, total)
        self._bar.setValue(current)
        percent = int(current / total * 100)
        self._bar.setFormat(f"{percent}%")
        self._pump()

    def close(self) -> None:
        if self._enabled and self._window is not None:
            try:
                self._window.rejected.disconnect(self.cancel)
            except Exception:
                pass
            self._window.close()
            self._pump()


def show_error(message: str) -> None:
    if QtWidgets is not None:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        QtWidgets.QMessageBox.critical(None, "PythonVNA 更新失败", message)
        app.processEvents()
        return
    print(message, file=sys.stderr)


def show_info(title: str, message: str) -> None:
    if QtWidgets is not None:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        QtWidgets.QMessageBox.information(None, title, message)
        app.processEvents()
        return
    print(message)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PythonVNA suite updater.")
    parser.add_argument("--manifest-url", required=True)
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--cleanup-root", default="")
    parser.add_argument("--restart", default="")
    parser.add_argument("--wait-seconds", type=float, default=3.0)
    return parser.parse_args(argv)


def download_file(url: str, path: Path, progress: ProgressReporter | None = None) -> str:
    digest = hashlib.sha256()
    request = Request(url, headers={"User-Agent": "PythonVNA-Updater"})
    with urlopen(request, timeout=30.0) as response:
        total = int(response.headers.get("Content-Length", "0") or 0)
        downloaded = 0
        started = time.monotonic()
        with path.open("wb") as output:
            while True:
                if progress is not None:
                    progress.check_cancelled()
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                output.write(chunk)
                downloaded += len(chunk)
                if progress is not None and total > 0:
                    elapsed = max(time.monotonic() - started, 0.001)
                    speed = downloaded / elapsed / 1024 / 1024
                    progress.set_progress(
                        downloaded,
                        total,
                        (
                            f"正在下载更新包：{downloaded / 1024 / 1024:.1f} / "
                            f"{total / 1024 / 1024:.1f} MB\n"
                            f"速度：{speed:.1f} MB/s"
                        ),
                    )
    return digest.hexdigest().lower()


def extract_archive(archive: Path, destination: Path, archive_type: str) -> None:
    if archive_type == "zip":
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                _resolve_within(destination, member.filename)
                mode = (member.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise RuntimeError(f"Update archive contains a symbolic link: {member.filename}")
            zf.extractall(destination)
        return

    if archive_type == "7z":
        seven_zip = find_7zip()
        if seven_zip is None:
            raise RuntimeError("7-Zip was not found. Install 7-Zip or publish zip updates.")
        subprocess.run(
            [seven_zip, "x", "-y", f"-o{destination}", str(archive)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        return

    raise ValueError(f"Unsupported archive type: {archive_type}")


def find_7zip() -> str | None:
    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "7-Zip" / "7z.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "7-Zip" / "7z.exe",
        shutil.which("7z.exe"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return str(path)
    return None


def copy_file_with_retry(
    source_path: Path,
    target_path: Path,
    *,
    attempts: int = 90,
    progress: ProgressReporter | None = None,
    current: int = 0,
    total: int = 0,
    display_path: str = "",
) -> None:
    delay = 0.5
    for attempt in range(1, attempts + 1):
        try:
            shutil.copy2(source_path, target_path)
            return
        except PermissionError as exc:
            if attempt >= attempts:
                message = f"{target_path} is in use and could not be updated after {attempts} attempts."
                raise PermissionError(message) from exc
            if progress is not None:
                label = display_path or source_path.name
                if total > 0:
                    progress.set_progress(
                        max(0, current - 1),
                        total,
                        f"等待文件释放：{label} ({attempt}/{attempts})",
                    )
                else:
                    progress.set_busy(f"等待文件释放：{label} ({attempt}/{attempts})")
            time.sleep(delay)
            delay = min(delay * 1.2, 2.0)


def copy_tree_overlay(
    source: Path,
    target: Path,
    *,
    skip_names: set[str] | None = None,
    progress: ProgressReporter | None = None,
) -> None:
    skip_names = {name.lower() for name in (skip_names or set())}
    file_paths = [
        path
        for path in source.rglob("*")
        if path.is_file() and path.name.lower() not in skip_names
    ]
    total = len(file_paths)
    for index, source_path in enumerate(file_paths, start=1):
        if progress is not None:
            progress.check_cancelled()
        relative_path = source_path.relative_to(source)
        if relative_path.parts and relative_path.parts[0].startswith("UPDATE_"):
            continue
        target_path = target / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        copy_file_with_retry(
            source_path,
            target_path,
            progress=progress,
            current=index,
            total=total,
            display_path=relative_path.as_posix(),
        )
        if progress is not None:
            progress.set_progress(index, total, f"正在应用文件：{relative_path.as_posix()}")


def apply_removed_files(staging: Path, target: Path) -> None:
    removed_path = staging / "UPDATE_REMOVED_FILES.txt"
    if not removed_path.exists():
        return
    target_root = target.resolve()
    for line in removed_path.read_text(encoding="utf-8").splitlines():
        relative = line.strip()
        if not relative:
            continue
        target_path = _resolve_within(target_root, relative)
        if target_path.exists() and target_path.is_file():
            target_path.unlink()


def normalize_staging_root(staging: Path) -> Path:
    children = [path for path in staging.iterdir()]
    directories = [path for path in children if path.is_dir()]
    files = [path for path in children if path.is_file()]
    if len(directories) == 1 and not files and directories[0].name.startswith("PythonVNA_"):
        return directories[0]
    return staging


def write_update_log(target: Path, message: str) -> None:
    log_path = target / "UPDATE_LOG.txt"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def restart_app(target: Path, restart_name: str) -> None:
    if not restart_name:
        return
    restart_path = target / restart_name
    if restart_path.exists():
        subprocess.Popen([str(restart_path)], cwd=str(target), close_fds=True)


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def should_cleanup_runner(executable: Path | None = None) -> bool:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    path = executable or Path(sys.executable)
    return path.name.lower() == "pythonvnaupdaterrunner.exe"


def schedule_runner_cleanup(executable: Path | None = None, cleanup_root: Path | None = None) -> None:
    if cleanup_root is None and not should_cleanup_runner(executable):
        return
    if cleanup_root is not None:
        target_path = cleanup_root.resolve()
        command = (
            f"$p={_powershell_literal(str(target_path))}; "
            "for ($i=0; $i -lt 60; $i++) { "
            "Start-Sleep -Milliseconds 500; "
            "try { Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction Stop; break } catch {} "
            "}"
        )
    else:
        path = (executable or Path(sys.executable)).resolve()
        command = (
            f"$p={_powershell_literal(str(path))}; "
            "for ($i=0; $i -lt 60; $i++) { "
            "Start-Sleep -Milliseconds 500; "
            "try { Remove-Item -LiteralPath $p -Force -ErrorAction Stop; break } catch {} "
            "}"
        )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-Command",
                command,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    progress = ProgressReporter()
    target: Path | None = None
    cleanup_root = Path(args.cleanup_root).resolve() if args.cleanup_root else None
    try:
        target = Path(args.target_dir).resolve()
        if not target.exists():
            raise FileNotFoundError(f"Target directory does not exist: {target}")

        progress.set_busy("正在检查更新...")
        time.sleep(max(0.0, args.wait_seconds))
        manifest = fetch_manifest(args.manifest_url, timeout=30.0)
        decision = select_update(
            manifest,
            current_version=args.current_version,
            manifest_url=args.manifest_url,
            allow_full=True,
        )
        if not decision.available or decision.package is None:
            write_update_log(target, f"No update applied: {decision.message}")
            progress.set_busy(decision.message or "没有可用更新。")
            time.sleep(0.8)
            return 0

        package = decision.package
        with tempfile.TemporaryDirectory(prefix="python_vna_update_") as tmp:
            tmp_path = Path(tmp)
            extension = ".7z" if package.archive_type == "7z" else ".zip"
            archive_path = tmp_path / f"update{extension}"
            progress.set_busy("正在下载更新包...")
            downloaded_hash = download_file(package.url, archive_path, progress=progress)
            progress.set_busy("正在校验更新包...")
            if downloaded_hash != package.sha256:
                raise RuntimeError(
                    f"SHA256 mismatch for update archive. Expected {package.sha256}, got {downloaded_hash}."
                )
            staging = tmp_path / "staging"
            staging.mkdir()
            progress.set_busy("正在解压更新包...")
            extract_archive(archive_path, staging, package.archive_type)
            update_root = normalize_staging_root(staging)
            progress.set_busy("正在应用文件...")
            apply_removed_files(update_root, target)
            skip_names = {Path(sys.executable).name} if getattr(sys, "frozen", False) else set()
            copy_tree_overlay(update_root, target, skip_names=skip_names, progress=progress)

        write_update_log(
            target,
            json.dumps(
                {
                    "updated_to": decision.latest_version,
                    "package_url": package.url,
                    "package_kind": package.kind,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                ensure_ascii=False,
            ),
        )
        progress.set_busy("正在启动程序...")
        restart_app(target, args.restart)
        time.sleep(0.3)
        return 0
    except UpdateCancelled as exc:
        if target is not None:
            write_update_log(target, f"Update cancelled: {exc}")
        progress.close()
        show_info("PythonVNA 更新", "更新已取消。")
        return 2
    except Exception as exc:
        message = f"{exc}\n\n{traceback.format_exc()}"
        progress.close()
        show_error(message)
        return 1
    finally:
        progress.close()
        schedule_runner_cleanup(cleanup_root=cleanup_root)


if __name__ == "__main__":
    raise SystemExit(main())
