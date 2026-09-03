from __future__ import annotations

from pathlib import Path
import sys


def resource_path(relative_path: str) -> Path:
    """Resolve a bundled resource in source and PyInstaller environments."""
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base_path / relative_path
