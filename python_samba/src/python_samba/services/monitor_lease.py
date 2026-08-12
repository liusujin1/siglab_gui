"""Crash-recoverable lease for the controller's forty monitor slots."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


def controller_endpoint_identity(session) -> dict[str, Any]:
    """Return the stable endpoint fields used to guard a pending restore."""

    info = getattr(session, "info", None)
    return {
        "backend": str(getattr(info, "backend", "")),
        "server_endpoint": str(getattr(info, "server_endpoint", "") or ""),
        "port": str(getattr(info, "port", "") or ""),
        "baudrate": int(getattr(info, "baudrate", 0) or 0),
    }


def endpoint_key(identity: dict[str, Any]) -> str:
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def default_recovery_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".python_samba"
    return root / "python_samba" / "monitor_slot_recovery"


@dataclass(frozen=True, slots=True)
class MonitorLeaseRecovery:
    endpoint: dict[str, Any]
    definitions: tuple[tuple[int, int, int], ...]
    created_utc: str
    state: str = "pending"
    controller: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "endpoint": self.endpoint,
            "definitions": [list(values) for values in self.definitions],
            "created_utc": self.created_utc,
            "state": self.state,
            "controller": self.controller or {},
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "MonitorLeaseRecovery":
        if int(payload.get("schema", 0)) != 1:
            raise ValueError("unsupported monitor recovery schema")
        definitions = tuple(
            tuple(int(value) for value in row[:3])
            for row in payload.get("definitions", [])
        )
        if len(definitions) != 40 or any(len(row) != 3 for row in definitions):
            raise ValueError("monitor recovery must contain exactly 40 definitions")
        endpoint = payload.get("endpoint")
        if not isinstance(endpoint, dict):
            raise ValueError("monitor recovery has no endpoint identity")
        return cls(
            endpoint=dict(endpoint),
            definitions=definitions,
            created_utc=str(payload.get("created_utc", "")),
            state=str(payload.get("state", "pending")),
            controller=(
                dict(payload["controller"])
                if isinstance(payload.get("controller"), dict)
                else {}
            ),
        )


class MonitorSlotLease:
    """Snapshot, configure, verify, and restore all monitor slots safely."""

    _registry_lock = threading.RLock()
    _active_endpoints: set[str] = set()

    def __init__(
        self,
        session,
        *,
        recovery_directory: str | Path | None = None,
        controller: dict[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.identity = controller_endpoint_identity(session)
        self.key = endpoint_key(self.identity)
        self.recovery_directory = Path(
            recovery_directory or default_recovery_directory()
        )
        self.recovery_path = self.recovery_directory / f"monitor_slots_{self.key}.json"
        self.controller = dict(controller or {})
        self.original_definitions: tuple[tuple[int, int, int], ...] | None = None
        self.configured_definitions: tuple[tuple[int, int, int], ...] = ()
        self.active = False
        self.restore_error = ""
        self._operation_lock = threading.RLock()

    def rebind(self, session) -> None:
        identity = controller_endpoint_identity(session)
        if endpoint_key(identity) != self.key:
            raise RuntimeError("pending monitor restore belongs to another controller endpoint")
        self.session = session

    @classmethod
    def pending_for_session(
        cls,
        session,
        *,
        recovery_directory: str | Path | None = None,
    ) -> MonitorLeaseRecovery | None:
        identity = controller_endpoint_identity(session)
        path = Path(recovery_directory or default_recovery_directory()) / (
            f"monitor_slots_{endpoint_key(identity)}.json"
        )
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        recovery = MonitorLeaseRecovery.from_payload(payload)
        if endpoint_key(recovery.endpoint) != endpoint_key(identity):
            return None
        return recovery

    def _write_recovery(self, recovery: MonitorLeaseRecovery) -> None:
        self.recovery_directory.mkdir(parents=True, exist_ok=True)
        temporary = self.recovery_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(recovery.to_payload(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(self.recovery_path)

    def _claim(self) -> None:
        with self._registry_lock:
            if self.key in self._active_endpoints:
                raise RuntimeError("the controller monitor slots are already leased")
            self._active_endpoints.add(self.key)
        claim = getattr(self.session, "claim_monitor_slots", None)
        try:
            if callable(claim):
                claim(self)
        except BaseException:
            with self._registry_lock:
                self._active_endpoints.discard(self.key)
            raise

    def _release_claim(self) -> None:
        release = getattr(self.session, "release_monitor_slots", None)
        if callable(release):
            release(self)
        with self._registry_lock:
            self._active_endpoints.discard(self.key)

    @staticmethod
    def _normalize(
        definitions: Sequence[Sequence[int | str | float]],
    ) -> tuple[tuple[int, int, int], ...]:
        normalized = tuple(tuple(int(value) for value in row) for row in definitions)
        if any(len(row) != 3 for row in normalized):
            raise ValueError("each monitor definition must contain Type/Main/Sub")
        return normalized

    def acquire(
        self,
        definitions: Sequence[Sequence[int | str | float]],
    ) -> tuple[tuple[int, int, int], ...]:
        """Lease slots 0..N-1 and return the authoritative 40-slot snapshot."""

        with self._operation_lock:
            return self._acquire_locked(definitions)

    def _acquire_locked(
        self,
        definitions: Sequence[Sequence[int | str | float]],
    ) -> tuple[tuple[int, int, int], ...]:

        requested = self._normalize(definitions)
        if not 1 <= len(requested) <= 40:
            raise ValueError("monitor lease requires between 1 and 40 definitions")
        if not self.session or not self.session.connected:
            raise RuntimeError("controller is not connected")
        if self.active:
            if requested == self.configured_definitions:
                return self.original_definitions or ()
            raise RuntimeError("monitor lease is already active with another selection")
        if self.recovery_path.exists():
            raise RuntimeError(
                "a monitor-slot restore is already pending for this endpoint; "
                "use Retry Restore before starting a new lease"
            )
        self._claim()
        try:
            original = tuple(self.session.get_monitor_signals(40))
            if len(original) != 40:
                raise RuntimeError(
                    f"DGMOS snapshot returned {len(original)} slots; expected 40"
                )
            self.original_definitions = original
            self._write_recovery(
                MonitorLeaseRecovery(
                    endpoint=self.identity,
                    definitions=original,
                    created_utc=datetime.now(timezone.utc).isoformat(),
                    controller=self.controller,
                )
            )
            try:
                self.session.set_monitor_signals(requested, lease_owner=self)
                readback = tuple(self.session.get_monitor_signals(len(requested)))
                if readback != requested:
                    raise RuntimeError(
                        "monitor definition readback mismatch: "
                        f"expected {requested!r}, got {readback!r}"
                    )
                values = self.session.get_monitor_values(0, len(requested) - 1)
                if len(values) != len(requested):
                    raise RuntimeError(
                        f"DGMSV returned {len(values)} values; expected {len(requested)}"
                    )
            except BaseException:
                self._restore_definitions(original)
                self.recovery_path.unlink(missing_ok=True)
                self._release_claim()
                raise
            self.configured_definitions = requested
            self.active = True
            self.restore_error = ""
            return original
        except BaseException:
            if not self.active:
                self._release_claim()
            raise

    def _restore_definitions(
        self, definitions: Sequence[Sequence[int | str | float]]
    ) -> None:
        original = self._normalize(definitions)
        if len(original) != 40:
            raise ValueError("restore requires exactly 40 monitor definitions")
        self.session.set_monitor_signals(original, lease_owner=self)
        readback = tuple(self.session.get_monitor_signals(40))
        if readback != original:
            mismatches = [
                index
                for index, (expected, actual) in enumerate(zip(original, readback))
                if expected != actual
            ]
            raise RuntimeError(f"monitor restore verification failed at slots {mismatches}")

    def restore(self) -> bool:
        """Restore the active snapshot; preserve the file if communication fails."""

        with self._operation_lock:
            return self._restore_locked()

    def _restore_locked(self) -> bool:

        definitions = self.original_definitions
        if definitions is None and self.recovery_path.exists():
            definitions = MonitorLeaseRecovery.from_payload(
                json.loads(self.recovery_path.read_text(encoding="utf-8"))
            ).definitions
        if definitions is None:
            self.active = False
            self._release_claim()
            return True
        if not self.session or not self.session.connected:
            self.restore_error = (
                "controller disconnected before monitor slots could be restored; "
                "reconnect to the same endpoint and use Retry Restore"
            )
            self.active = False
            self._release_claim()
            return False
        if endpoint_key(controller_endpoint_identity(self.session)) != self.key:
            self.restore_error = "refusing to restore monitor slots to another endpoint"
            self.active = False
            self._release_claim()
            return False
        try:
            self._restore_definitions(definitions)
        except BaseException as exc:
            self.restore_error = str(exc)
            self.active = False
            self._release_claim()
            return False
        self.recovery_path.unlink(missing_ok=True)
        self.active = False
        self.original_definitions = None
        self.configured_definitions = ()
        self.restore_error = ""
        self._release_claim()
        return True

    @classmethod
    def retry_pending(
        cls,
        session,
        *,
        recovery_directory: str | Path | None = None,
    ) -> tuple[bool, str]:
        recovery = cls.pending_for_session(
            session, recovery_directory=recovery_directory
        )
        if recovery is None:
            return True, "No pending monitor restore for this endpoint."
        lease = cls(
            session,
            recovery_directory=recovery_directory,
            controller=recovery.controller,
        )
        lease.original_definitions = recovery.definitions
        lease._claim()
        lease.active = True
        if lease.restore():
            return True, "Monitor slots restored and verified."
        return False, lease.restore_error


__all__ = [
    "MonitorLeaseRecovery",
    "MonitorSlotLease",
    "controller_endpoint_identity",
    "default_recovery_directory",
    "endpoint_key",
]
