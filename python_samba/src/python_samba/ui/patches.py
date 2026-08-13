"""SAMBA19xUI gap patches — apply all to MainWindow.

Usage:
    from python_samba.ui.patches import apply_all_patches
    apply_all_patches(MainWindow)  # monkey-patches all methods
"""
from __future__ import annotations

import os
import logging
import sys
import sysconfig
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PatchReport:
    """Result of applying the legacy page-extension layer."""

    applied: tuple[str, ...]
    failed: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failed


def _find_patches_dir() -> str:
    """Locate patches in a source checkout or an installed distribution."""
    source_dir = Path(__file__).resolve().parents[3] / "_patches"
    # Frozen and installed builds use one canonical data directory.  Search it
    # first so the portable bundle does not need a duplicate ``_patches`` tree.
    candidates: list[Path] = []
    for entry in sys.path:
        if entry:
            candidates.append(Path(entry) / "python_samba_patches")
    for key in ("data", "purelib", "platlib"):
        root = sysconfig.get_path(key)
        if root:
            candidates.append(Path(root) / "python_samba_patches")
    for candidate in candidates:
        if (candidate / "ff_filter_patch.py").is_file():
            return str(candidate.resolve())

    # A source checkout keeps the editable patch modules at project root.
    if source_dir.is_dir():
        return str(source_dir)

    try:
        dist = distribution("python-samba")
    except PackageNotFoundError:
        return str(source_dir)

    for entry in dist.files or ():
        if entry.name == "ff_filter_patch.py" and entry.parent.name == "python_samba_patches":
            installed_dir = Path(dist.locate_file(entry)).resolve().parent
            if installed_dir.is_dir():
                return str(installed_dir)
    return str(source_dir)


PATCHES_DIR = _find_patches_dir()


def load_patch_module(name: str) -> object | None:
    """Load a patch module from _patches/ directory. Public API."""
    import importlib.util
    path = os.path.join(PATCHES_DIR, f"{name}.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        LOGGER.exception("Failed to load optional UI patch %s", name)
        return None
    return mod


PATCH_MODULES = [
    "ff_filter_patch",
    "ff_config_patch",
    "pff_filter_patch",
    "system_setting_patch",
    "connect_page_patch",
    "pneumatic_page_patch",
    "polynom_patch",
    "pffconfig_posfilter_saveload_patch",
    # Unified special tab replaces safety_zms_patch + signal_progress_patch + low_priority_patch
    "unified_special_tab",
    # signal_progress_patch still provides save-load improvements and progress dialog
    "signal_progress_patch",
    # Final screenshot-oriented replacements for matrix/status/config pages.
    "reference_layout_patch",
]


def apply_all_patches(
    MainWindowClass: type, *, strict: bool = False
) -> PatchReport:
    """Apply all UI extensions and return an auditable result.

    ``strict`` is useful for packaging and CI, where silently constructing a
    partial interface is worse than failing early.  Interactive startup keeps
    the historical best-effort behaviour and reports failures to the log.
    """
    applied: list[str] = []
    failed: list[str] = []
    for mod_name in PATCH_MODULES:
        target = applied if _apply_patch(MainWindowClass, mod_name) else failed
        target.append(mod_name)

    report = PatchReport(tuple(applied), tuple(failed))
    setattr(MainWindowClass, "__python_samba_patch_report__", report)
    if report.failed:
        message = "UI patches failed: " + ", ".join(report.failed)
        if strict:
            raise RuntimeError(message)
        LOGGER.warning(message)
    else:
        LOGGER.info(
            "Applied %d UI patches to %s", len(applied), MainWindowClass.__name__
        )
    return report


def _apply_patch(cls: type, mod_name: str) -> bool:
    """Load a patch module and bind its functions to the class."""
    import importlib.util
    path = os.path.join(PATCHES_DIR, f"{mod_name}.py")
    if not os.path.exists(path):
        LOGGER.error("UI patch not found: %s", path)
        return False
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None:
        LOGGER.error("Could not create import spec for UI patch: %s", path)
        return False
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        LOGGER.exception("Error loading UI patch %s: %s", mod_name, exc)
        return False

    try:
        # Prefer an explicit extension hook.  The prefix-based fallback is
        # retained for the older generated modules until they are migrated.
        if hasattr(mod, "apply_patches"):
            mod.apply_patches(cls)
            return True

        if hasattr(mod, "patch_main_window"):
            mod.patch_main_window(cls)
            return True

        count = 0
        for name in dir(mod):
            if name.startswith("_build_") or name.startswith("_on_") or name.startswith("on_") or name.startswith("_init_") or name.startswith("_ff_") or name.startswith("_pneu_") or name.startswith("_update_") or name.startswith("_show_") or name.startswith("_hide_") or name.startswith("_add_") or name.startswith("_read_") or name.startswith("_toggle_") or name.startswith("_populate_") or name.startswith("_sync_") or name.startswith("parse_") or name.startswith("_pff_") or name.startswith("_sig_") or name.startswith("_digio_") or name.startswith("_send_") or name.startswith("_get_"):
                fn = getattr(mod, name)
                if callable(fn):
                    setattr(cls, name, fn)
                    count += 1
    except Exception as exc:
        LOGGER.exception("Error applying UI patch %s: %s", mod_name, exc)
        return False
    if count:
        return True
    LOGGER.error("UI patch %s did not expose any supported hooks", mod_name)
    return False


if __name__ == "__main__":
    # Quick test
    from python_samba.ui.main_window import MainWindow
    result = apply_all_patches(MainWindow, strict=True)
    print(f"Patch load test: OK ({len(result.applied)} modules)")
