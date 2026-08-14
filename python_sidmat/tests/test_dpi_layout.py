"""Cross-package DPI smoke checks for the portable TestKit GUIs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("package", ["samba", "sidmat"])
@pytest.mark.parametrize("scale", ["1", "2"])
def test_packaged_layout_fits_logical_work_area(package: str, scale: str) -> None:
    """A 100% and a 200% logical desktop must not clip the main window."""

    if package == "samba":
        import_line = "from python_samba.ui.main_window import MainWindow"
    else:
        import_line = "from python_sidmat.ui.main_window import MainWindow"
    script = f"""
import json
from PySide6 import QtWidgets
{import_line}
app = QtWidgets.QApplication([])
window = MainWindow()
screen = app.primaryScreen()
geo = screen.availableGeometry()
app.processEvents()
print(json.dumps({{
    'screen': [geo.width(), geo.height()],
    'window': [window.width(), window.height()],
    'minimum': [window.minimumWidth(), window.minimumHeight()],
}}))
window.close()
app.processEvents()
"""
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": os.pathsep.join(
                [str(_ROOT / "python_samba" / "src"), str(_ROOT / "python_sidmat" / "src")]
            ),
            "QT_QPA_PLATFORM": "offscreen",
            "QT_SCALE_FACTOR": scale,
            "SIGLAB_RESPECT_QT_SCALE": "1",
        }
    )
    for key in (
        "QT_SCREEN_SCALE_FACTORS",
        "QT_AUTO_SCREEN_SCALE_FACTOR",
        "QT_SCALE_FACTOR_ROUNDING_POLICY",
    ):
        env.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        pytest.fail(
            f"{package} DPI smoke failed at QT_SCALE_FACTOR={scale}:\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    screen_w, screen_h = payload["screen"]
    window_w, window_h = payload["window"]
    minimum_w, minimum_h = payload["minimum"]
    assert 0 < window_w <= screen_w
    assert 0 < window_h <= screen_h
    assert 0 < minimum_w <= screen_w
    assert 0 < minimum_h <= screen_h
