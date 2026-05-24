"""Helpers for optional third-party imports."""

from __future__ import annotations

from importlib import import_module


def require(module_name: str, package_hint: str):
    """Import a module or raise a clear runtime error."""
    try:
        return import_module(module_name)
    except ImportError as exc:  # pragma: no cover - exercised through callers
        raise RuntimeError(
            f"Missing optional dependency '{module_name}'. Install with: {package_hint}"
        ) from exc
