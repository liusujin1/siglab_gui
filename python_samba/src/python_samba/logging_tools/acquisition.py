"""Background host-side monitor acquisition independent of Qt."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import threading
import time
from typing import Callable

from python_samba.logging_tools.models import AcquisitionStats, FileLoggingConfig
from python_samba.logging_tools.storage import DelimitedStreamWriter


SampleCallback = Callable[[AcquisitionStats, list[float]], None]
FinishedCallback = Callable[[AcquisitionStats, BaseException | None], None]


class FileLoggingService:
    """Poll DGMSV on a daemon worker and stream every sample to disk."""

    def __init__(self, session) -> None:
        self.session = session
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.stats = AcquisitionStats()

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(
        self,
        config: FileLoggingConfig,
        *,
        on_sample: SampleCallback | None = None,
        on_finished: FinishedCallback | None = None,
    ) -> None:
        config.validate()
        with self._lock:
            if self.running:
                raise RuntimeError("file logging is already running")
            if not self.session or not self.session.connected:
                raise RuntimeError("controller is not connected")
            self._stop.clear()
            self.stats = AcquisitionStats(
                state="waiting" if config.start_after_s else "running",
                requested_interval_ms=float(config.interval_ms),
                output_path=str(config.path),
            )
            self._thread = threading.Thread(
                target=self._run,
                name="SambaFileLogging",
                args=(config, on_sample, on_finished),
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, wait: bool = False, timeout: float | None = None) -> None:
        self._stop.set()
        if self.running:
            self.stats.state = "stopping"
            self.stats.message = "stopping after the current controller read"
        thread = self._thread
        if wait and thread and thread is not threading.current_thread():
            thread.join(timeout)

    def _snapshot(self) -> AcquisitionStats:
        return replace(self.stats)

    def _run(
        self,
        config: FileLoggingConfig,
        on_sample: SampleCallback | None,
        on_finished: FinishedCallback | None,
    ) -> None:
        writer: DelimitedStreamWriter | None = None
        read_session = self.session
        owned_session = None
        error: BaseException | None = None
        terminal_state = "cancelled"
        try:
            open_reader = getattr(self.session, "open_background_reader", None)
            if callable(open_reader):
                owned_session = open_reader("python_samba-file-logging")
                if owned_session is not None:
                    read_session = owned_session
            if self._stop.is_set():
                self.stats.state = "cancelled"
                self.stats.message = "stopped before acquisition started"
                return
            writer = DelimitedStreamWriter(config)
            self.stats.output_path = str(writer.path)
            if config.start_after_s and self._stop.wait(config.start_after_s):
                self.stats.state = "cancelled"
                self.stats.message = "stopped before acquisition started"
                return

            writer.set_running()
            self.stats.state = "running"
            started = time.monotonic()
            previous_sample: float | None = None
            interval_s = config.interval_ms / 1000.0
            deadline = started
            interval_total = 0.0
            interval_count = 0
            while not self._stop.is_set():
                now = time.monotonic()
                if config.duration_s is not None and now - started >= config.duration_s:
                    terminal_state = "complete"
                    break
                if now < deadline and self._stop.wait(deadline - now):
                    break
                request_started = time.monotonic()
                if request_started - deadline > max(interval_s * 0.25, 0.010):
                    self.stats.late_samples += 1
                values = list(
                    read_session.get_monitor_values(0, config.signal_count - 1)
                )
                sampled = time.monotonic()
                if self._stop.is_set():
                    terminal_state = "cancelled"
                    break
                if len(values) != config.signal_count:
                    raise RuntimeError(
                        f"DGMSV returned {len(values)} values; expected {config.signal_count}"
                    )
                elapsed = sampled - started
                writer.append(datetime.now(timezone.utc), elapsed, values)
                self.stats.samples += 1
                self.stats.elapsed_s = elapsed
                if previous_sample is not None:
                    measured = sampled - previous_sample
                    interval_total += measured
                    interval_count += 1
                    self.stats.actual_interval_ms = 1000.0 * interval_total / interval_count
                previous_sample = sampled
                if on_sample:
                    on_sample(self._snapshot(), values)

                deadline += interval_s
                # Do not burst repeatedly after a slow serial transaction.
                if deadline <= sampled:
                    missed = int((sampled - deadline) // interval_s) + 1
                    deadline += missed * interval_s
                # A useful error message if an unrealistically short interval
                # was requested, without treating jitter as a fatal failure.
                request_ms = (sampled - request_started) * 1000.0
                if request_ms > config.interval_ms:
                    self.stats.message = f"serial read needs {request_ms:.1f} ms"
            else:
                terminal_state = "cancelled"
            if self._stop.is_set() and terminal_state != "complete":
                terminal_state = "cancelled"
            self.stats.state = terminal_state
        except BaseException as exc:  # report worker failures to the GUI
            error = exc
            terminal_state = "error"
            self.stats.state = terminal_state
            self.stats.message = str(exc)
        finally:
            if writer is not None:
                writer.finish(
                    terminal_state,
                    message=self.stats.message,
                    stats={
                        "elapsed_s": self.stats.elapsed_s,
                        "actual_interval_ms": self.stats.actual_interval_ms,
                        "late_samples": self.stats.late_samples,
                    },
                )
            if owned_session is not None:
                try:
                    owned_session.close()
                except Exception:
                    pass
            with self._lock:
                self._thread = None
            if on_finished:
                on_finished(self._snapshot(), error)
