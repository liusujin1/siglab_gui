from __future__ import annotations

import faulthandler
from pathlib import Path
import time


_LOG_DIR = Path.home() / "AppData" / "Local" / "PythonVNA" / "logs"
_FAULT_HANDLE = None


def log_path() -> Path:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR / "python_vna.log"


def append_log(message: str) -> None:
    try:
        path = log_path()
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {message}\n")
    except OSError:
        pass


def enable_fault_log() -> None:
    global _FAULT_HANDLE
    if _FAULT_HANDLE is not None:
        return
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        _FAULT_HANDLE = (_LOG_DIR / "python_vna_fault.log").open("a", encoding="utf-8")
        faulthandler.enable(file=_FAULT_HANDLE, all_threads=True)
    except OSError:
        _FAULT_HANDLE = None
