"""Controller wrapper — structured RCI access for measurements.

Wraps ``python_samba``'s :class:`ControllerSession` with measurement-oriented
methods: connect/disconnect, trace configuration, excitation, and loop status.
All heavy RCI framing lives in python_samba; this layer only maps domain
objects (IOType / TraceParameters / ExcitationParameters) onto the session API.
"""

from __future__ import annotations

import math

from python_samba.protocol.commands import FirmwareVersion, LoopStatus
from python_samba.services.session import (
    ControllerSession,
    open_comm_server,
    open_mock,
    open_serial,
)

from python_sidmat.backend.iosignal import IOType
from python_sidmat.measurement.excitation import ExcitationParameters
from python_sidmat.measurement.trace import TraceParameters

__all__ = ["Controller", "ControllerError"]


class ControllerError(RuntimeError):
    """Measurement-related controller failure."""


class Controller:
    """High-level controller facade for the SiDiMaT measurement workflow."""

    def __init__(self, session: ControllerSession) -> None:
        self.session = session
        self._version: FirmwareVersion | None = None

    # -- construction -----------------------------------------------------

    @classmethod
    def connect(cls, port: str, baudrate: int = 57600, *, readonly: bool = False) -> "Controller":
        """Open a real serial connection to the controller."""
        port = str(port).strip()
        if not port:
            raise ControllerError("serial port must not be empty")
        session = open_serial(port, baudrate=baudrate, readonly=readonly)
        return cls._open_session(session)

    @classmethod
    def connect_mock(cls, *, readonly: bool = False) -> "Controller":
        """Connect to the in-memory mock controller (no hardware needed)."""
        session = open_mock(readonly=readonly)
        return cls._open_session(session)

    @classmethod
    def connect_server(
        cls,
        port: str,
        baudrate: int = 57600,
        *,
        server: str = "127.0.0.1:47619",
        token_file: str | None = None,
        comm_server_exe: str | None = None,
        auto_start: bool = True,
        readonly: bool = False,
        timeout: float = 5.0,
    ) -> "Controller":
        """Connect through the process-shared Communication Server."""
        port = str(port).strip()
        if not port:
            raise ControllerError("serial port must not be empty")
        session = open_comm_server(
            port,
            baudrate=baudrate,
            server=server,
            token_file=token_file,
            comm_server_exe=comm_server_exe,
            auto_start=auto_start,
            client_name="python_sidmat",
            readonly=readonly,
            timeout=timeout,
        )
        return cls._open_session(session)

    @classmethod
    def _open_session(cls, session: ControllerSession) -> "Controller":
        """Open a session and close its transport again if negotiation fails."""

        ctrl = cls(session)
        try:
            ctrl.open()
        except BaseException:
            try:
                session.close()
            except Exception:
                pass
            raise
        return ctrl

    def open(self) -> FirmwareVersion:
        self._version = self.session.open()
        return self._version

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "Controller":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def connected(self) -> bool:
        return self.session.connected

    @property
    def version(self) -> FirmwareVersion | None:
        return self._version

    # -- identity / status ------------------------------------------------

    def get_version(self) -> FirmwareVersion:
        self._version = self.session.get_version()
        return self._version

    def get_sample_frequency(self) -> float:
        value = float(self.session.get_sample_frequency())
        if not math.isfinite(value) or value <= 0:
            raise ControllerError(f"invalid controller sample frequency {value!r}")
        return value

    def get_loop_status(self) -> LoopStatus:
        return self.session.get_loop_status()

    def get_output_limit(self) -> int:
        """Read the output limit in percent (BGOPL).

        With a non-zero excitation gain the controller drives the actuators;
        keep the output limit low during bring-up.
        """
        return int(self.session.get_output_limit())

    def get_system_config(self) -> list[str]:
        """Read the system configuration token list (NGEXL)."""
        return self.session.get_controller_config()

    def get_axis_loop_states(self) -> list[bool]:
        """12-axis loop-on states.

        Axes 0-5 (Xtrans, Zrot, Ytrans, Ztrans, Yrot, Xrot) map to the
        BGSTS ``individual`` word; axes 6-11 (Xrot2 … Ztrans2) map to the
        BGSST ``position`` word.
        """
        loop = self.session.get_loop_status()
        position, *_ = self.session.get_pos_pneum_digital_status()
        states = [bool(loop.individual & (1 << i)) for i in range(6)]
        states += [bool(position & (1 << i)) for i in range(6)]
        return states

    def set_axis_loop_state(self, index: int, on: bool) -> None:
        """Turn one axis loop on/off (velocity axes via BSSTS, position via BSSST)."""
        index = int(index)
        if not 0 <= index < 12:
            raise ValueError(f"axis index must be in 0..11, got {index}")
        if index < 6:
            loop = self.session.get_loop_status()
            individual = loop.individual
            individual = (individual | (1 << index)) if on else (individual & ~(1 << index))
            self.session.set_loop_status(individual, loop.system)
        else:
            pos = index - 6
            position, pneumatic, _, _ = self.session.get_pos_pneum_digital_status()
            position = (position | (1 << pos)) if on else (position & ~(1 << pos))
            self.session.set_pos_pneum_individual_loop_status(position, pneumatic)

    def get_system_info(self) -> dict[str, object]:
        """Snapshot of identity/status for the System Config Info display."""
        info: dict[str, object] = {}
        try:
            v = self.get_version()
            info["firmware"] = f"{v.major}.{v.minor}.{v.patch}"
        except Exception:
            info["firmware"] = "?"
        try:
            info["sample_frequency"] = self.get_sample_frequency()
        except Exception:
            info["sample_frequency"] = 0.0
        try:
            info["loop"] = self.get_loop_status()
        except Exception:
            pass
        try:
            position, pneumatic, digi_in, digi_out = (
                self.session.get_pos_pneum_digital_status()
            )
            info.update(position=position, pneumatic=pneumatic,
                        digital_input=digi_in, digital_output=digi_out)
        except Exception:
            pass
        try:
            info["trace"] = self.get_trace()
        except Exception:
            pass
        return info

    # -- noise / excitation filters --------------------------------------

    def get_noise_filter_usage(self) -> str:
        return self.session.get_noise_filter_usage()

    def set_noise_filter_usage(self, on_off: str) -> None:
        self.session.set_noise_filter_usage(on_off)

    def get_noise_filter_stage(self, stage: int) -> object:
        return self.session.get_noise_filter_stage(stage)

    def set_noise_filter_stage(self, stage: object) -> None:
        self.session.set_noise_filter_stage(stage)

    # -- diagnostic outputs ----------------------------------------------

    def set_diagnostic_outputs(self, io0: IOType, io1: IOType) -> None:
        """Route two signals to the diagnostic outputs (DSDOS).

        The RCI command carries six flat values: Diag0 (T, M, S) + Diag1 (T, M, S).
        """
        self.session.set_diagnostic_outputs(
            io0.type, io0.main_index, io0.sub_index,
            io1.type, io1.main_index, io1.sub_index,
        )

    def get_diagnostic_outputs(self) -> tuple[IOType, IOType]:
        """Read the two diagnostic output routes (DGDOS)."""
        tokens = self.session.get_diagnostic_outputs()
        if len(tokens) < 6:
            raise ControllerError(f"short DGDOS response: {tokens!r}")
        raw = tokens[:6]
        try:
            vals = [_parse_int_token(token) for token in raw]
        except ValueError as exc:
            raise ControllerError(f"bad DGDOS response: {tokens!r}") from exc
        return IOType(vals[0], vals[1], vals[2]), IOType(vals[3], vals[4], vals[5])

    # -- trace configuration ----------------------------------------------

    def set_trace(self, trace: TraceParameters) -> None:
        """Write trace information to the controller (DSTIV)."""
        self.session.set_digital_trace_info(*trace.encode())

    def get_trace(self) -> TraceParameters:
        """Read trace information from the controller (DGTIV)."""
        tokens = self.session.get_digital_trace_info()
        return TraceParameters.from_tokens(tokens)

    def start_trace(self) -> list[str]:
        """Trigger one trace acquisition (DASTA); returns the response tokens.

        A non-zero first token means the controller rejected the trigger (the
        original software skips that average when the DASTA error code is
        non-zero).
        """
        return self.session.start_digital_trace()

    def get_trace_status(self) -> int:
        """Poll trace status (DGTAS); 0 means the acquisition is finished."""
        tokens = self.session.get_digital_trace_status()
        if not tokens:
            raise ControllerError("empty DGTAS response")
        try:
            return _parse_int_token(tokens[0])
        except ValueError as exc:
            raise ControllerError(f"bad DGTAS token {tokens[0]!r}") from exc

    def get_trace_buffer(self, read_offset: int) -> tuple[list[float], list[float]]:
        """Read up to 16 sample pairs (DGTBV).

        Returns ``(ch1_chunk, ch2_chunk)``.  Wire format per the RCI doc is
        ``NumSamples Ch1[n] Ch2[n]`` — all channel-1 samples first, then
        channel-2 samples.
        """
        vals = self.session.get_digital_trace_buffer(read_offset)
        if not vals:
            raise ControllerError(f"empty DGTBV response at offset {read_offset}")
        if len(vals) % 2:
            raise ControllerError(
                f"odd DGTBV payload length {len(vals)} at offset {read_offset}"
            )
        n = len(vals) // 2
        return vals[:n], vals[n:]

    def get_trace_buffers(
        self, read_offsets: list[int]
    ) -> list[tuple[list[float], list[float]]]:
        """Read several text trace chunks with one remote server RPC."""
        chunks = self.session.get_digital_trace_buffers(read_offsets)
        result: list[tuple[list[float], list[float]]] = []
        for read_offset, vals in zip(read_offsets, chunks):
            if not vals or len(vals) % 2:
                raise ControllerError(
                    f"bad DGTBV payload length {len(vals)} at offset {read_offset}"
                )
            count = len(vals) // 2
            result.append((vals[:count], vals[count:]))
        return result

    def get_trace_buffer_binary(
        self, read_offset: int, sample_count: int = 40
    ) -> tuple[list[float], list[float]]:
        """Read legacy binary DGTBB samples (interleaved on the wire)."""
        vals = self.session.get_digital_trace_buffer_binary(read_offset, sample_count)
        if len(vals) % 2:
            raise ControllerError(
                f"odd DGTBB payload length {len(vals)} at offset {read_offset}"
            )
        return vals[::2], vals[1::2]

    def get_trace_buffers_binary(
        self, requests: list[tuple[int, int]]
    ) -> list[tuple[list[float], list[float]]]:
        """Read several binary trace chunks with one remote server RPC."""
        chunks = self.session.get_digital_trace_buffers_binary(requests)
        result: list[tuple[list[float], list[float]]] = []
        for (read_offset, _sample_count), vals in zip(requests, chunks):
            if len(vals) % 2:
                raise ControllerError(
                    f"odd DGTBB payload length {len(vals)} at offset {read_offset}"
                )
            result.append((vals[::2], vals[1::2]))
        return result

    # -- excitation -------------------------------------------------------

    def set_excitation(self, exc: ExcitationParameters) -> None:
        self.session.set_excitation_params(*exc.encode())

    def get_excitation(self) -> ExcitationParameters:
        tokens = self.session.get_excitation_params()
        return ExcitationParameters.from_tokens(tokens)

    def get_excitation_offset(self) -> float:
        """Read the optional extended-excitation DC offset (DGEOV)."""
        return float(self.session.get_excitation_offset())

    def set_excitation_offset(self, value: float) -> None:
        """Write the optional extended-excitation DC offset (DSEOV)."""
        self.session.set_excitation_offset(float(value))

    def set_noise_inject(self, io: IOType) -> None:
        self.session.set_noise_inject_point(*io.encode())

    def get_noise_inject(self) -> IOType:
        tokens = self.session.get_noise_inject_point()
        if len(tokens) < 3:
            raise ControllerError(f"bad DGNIP response: {tokens!r}")
        try:
            vals = [_parse_int_token(t) for t in tokens[:3]]
        except ValueError as exc:
            raise ControllerError(f"bad DGNIP response: {tokens!r}") from exc
        return IOType(*vals)


def _parse_int_token(token: object) -> int:
    """Parse decimal and hexadecimal RCI integer tokens consistently."""
    text = str(token).strip()
    try:
        return int(text, 0)
    except ValueError:
        return int(text)
