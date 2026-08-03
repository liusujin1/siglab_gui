"""Write-safety helpers: snapshots, confirmations, dangerous ops."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from python_samba.protocol.commands import FilterStage
from python_samba.services.session import ControllerSession

DANGEROUS_OPS = frozenset({
    "set_loop_status",
    "save_nvram",
    "restore_nvram",
    "clear_nvram",
    "use_current_proximity_offsets",
    "set_noise_type",
    "nvram_save",
    "nvram_restore",
    "nvram_clear",
})


@dataclass
class ParamChange:
    """One pending write with human-readable before/after."""

    op: str
    summary: str
    before: Any
    after: Any
    apply: Callable[[], None] = field(repr=False)


@dataclass
class Snapshot:
    created_at: str
    version: str
    loop_individual: int
    loop_system: int
    sample_hz: float | None
    velocity_filter_0_0: dict[str, Any] | None = None
    proximity_offsets: list[float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


class SafetyGate:
    """Requires explicit unlock for writes; keeps local snapshots."""

    def __init__(self, session: ControllerSession, snapshot_dir: Path | None = None) -> None:
        self.session = session
        self.unlocked = not session.readonly
        self.snapshot_dir = snapshot_dir or Path.home() / ".python_samba" / "snapshots"
        self.pending: list[ParamChange] = []

    def unlock(self) -> None:
        self.session.readonly = False
        self.unlocked = True

    def lock(self) -> None:
        self.session.readonly = True
        self.unlocked = False

    def require_unlock(self) -> None:
        if not self.unlocked or self.session.readonly:
            raise PermissionError("writes are locked; unlock safety gate first")

    def take_snapshot(self) -> Snapshot:
        now = datetime.now(timezone.utc)
        version = self.session.get_version()
        loop = self.session.get_loop_status()
        try:
            fs = self.session.get_sample_frequency()
        except Exception:
            fs = None
        try:
            vf = self.session.get_velocity_filter(0, 0)
            vf_dict = {
                "axis": vf.axis,
                "stage": vf.stage,
                "type": vf.filter_type,
                "params": list(vf.params),
            }
        except Exception:
            vf_dict = None
        try:
            offsets = self.session.get_proximity_offsets()
        except Exception:
            offsets = None
        snap = Snapshot(
            created_at=now.isoformat(),
            version=str(version),
            loop_individual=loop.individual,
            loop_system=loop.system,
            sample_hz=fs,
            velocity_filter_0_0=vf_dict,
            proximity_offsets=offsets,
        )
        # Include microseconds and still guard against a collision.  The former
        # second-resolution name silently overwrote snapshots from rapid writes.
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        path = self.snapshot_dir / f"snap_{stamp}.json"
        suffix = 1
        while path.exists():
            path = self.snapshot_dir / f"snap_{stamp}_{suffix}.json"
            suffix += 1
        snap.save(path)
        return snap

    def queue_velocity_filter(self, stage: FilterStage) -> ParamChange:
        self.require_unlock()
        before = self.session.get_velocity_filter(stage.axis, stage.stage)
        change = ParamChange(
            op="set_velocity_filter",
            summary=(
                f"VSVFS axis={stage.axis} stage={stage.stage} "
                f"type {before.filter_type}->{stage.filter_type}"
            ),
            before=before,
            after=stage,
            apply=lambda: self.session.set_velocity_filter(stage),
        )
        self.pending.append(change)
        return change

    def queue_proximity_filter(self, stage: FilterStage) -> ParamChange:
        self.require_unlock()
        before = self.session.get_proximity_filter(stage.axis, stage.stage)
        change = ParamChange(
            op="set_proximity_filter",
            summary=(
                f"CSPFS axis={stage.axis} stage={stage.stage} "
                f"type {before.filter_type}->{stage.filter_type}"
            ),
            before=before,
            after=stage,
            apply=lambda: self.session.set_proximity_filter(stage),
        )
        self.pending.append(change)
        return change

    def queue_proximity_offsets(self, values: list[float]) -> ParamChange:
        self.require_unlock()
        before = self.session.get_proximity_offsets()
        change = ParamChange(
            op="set_proximity_offsets",
            summary="CSPOV proximity offsets",
            before=before,
            after=list(values),
            apply=lambda: self.session.set_proximity_offsets(list(values)),
        )
        self.pending.append(change)
        return change

    def apply_pending(self, *, snapshot_first: bool = True) -> list[ParamChange]:
        self.require_unlock()
        if not self.pending:
            return []
        if snapshot_first:
            self.take_snapshot()
        applied: list[ParamChange] = []
        while self.pending:
            # Remove an item only after it succeeds.  Keeping a failed change
            # queued lets the operator inspect/retry it instead of losing it.
            change = self.pending[0]
            change.apply()
            applied.append(self.pending.pop(0))
        return applied

    def discard_pending(self) -> None:
        self.pending.clear()
