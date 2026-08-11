"""Controller session: request/response over a Transport."""

from __future__ import annotations

import struct
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from python_samba.protocol.commands import (
    CommandEncoder,
    FilterStage,
    FirmwareVersion,
    LoopStatus,
    RciCommandError,
)
from python_samba.protocol.frame import ProtocolError, RciResponse
from python_samba.transport.serial_port import Transport, TransportError


@dataclass
class ConnectionInfo:
    backend: str
    port: str | None = None
    baudrate: int | None = None
    server_endpoint: str | None = None


def _request_identity(frame: bytes) -> tuple[str, str, str]:
    """Extract message id, CRL and mnemonic from an encoder-built request."""
    try:
        text = frame.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProtocolError(f"non-ASCII request: {exc}") from exc
    if not text.startswith(":") or not text.endswith("\r") or len(text) < 10:
        raise ProtocolError(f"malformed request frame: {text!r}")

    # Strip ':' plus the trailing two-character CRC and CR.  The first two
    # remaining characters are the length field (or '##').
    mid = text[1:-3]
    rest = mid[2:]
    if len(rest) < 2:
        raise ProtocolError(f"request missing message id/data: {text!r}")
    msg_id = rest[0]
    tokens = rest[1:].split()
    if len(tokens) < 2:
        raise ProtocolError(f"request missing CRL/mnemonic: {text!r}")
    return msg_id, tokens[0], tokens[1]


class ControllerSession:
    """High-level TC-MFD / OPTICON access. Default mode is read-friendly."""

    def __init__(
        self,
        transport: Transport,
        *,
        backend_name: str = "serial",
        port: str | None = None,
        baudrate: int | None = None,
        server_endpoint: str | None = None,
        timeout: float = 5.0,
        readonly: bool = True,
    ) -> None:
        self.transport = transport
        self.info = ConnectionInfo(
            backend=backend_name,
            port=port,
            baudrate=baudrate,
            server_endpoint=server_endpoint,
        )
        self.timeout = timeout
        self.readonly = readonly
        self.encoder = CommandEncoder()
        # A Sidmat measurement worker and the Qt refresh timer can use the
        # same controller session concurrently.  Keep each request/response
        # pair atomic so bytes from BGSTS/DGTAS/DASTA cannot interleave on the
        # serial link.
        self._io_lock = threading.RLock()
        self._connected = False
        self.last_response: RciResponse | None = None

    @property
    def connected(self) -> bool:
        return self._connected and self.transport.is_open

    def open(self) -> FirmwareVersion:
        self.transport.open()
        try:
            version = self.get_version()
        except Exception:
            self.transport.close()
            self._connected = False
            raise
        self._connected = True
        return version

    def close(self) -> None:
        self._connected = False
        self.transport.close()

    def open_background_reader(self, client_name: str) -> ControllerSession | None:
        """Open an independent read-only server client for a long worker.

        A file logger must not hold the GUI session's ``_io_lock`` for every
        remote sample.  Shared-server backends can cheaply attach a second TCP
        client to the same physical COM port; serial and mock sessions return
        ``None`` so callers keep using the original session.
        """

        if not self.connected or self.info.backend != "server":
            return None
        from python_samba.transport.comm_server import (
            CommServerConfig,
            CommServerTransport,
        )

        if not isinstance(self.transport, CommServerTransport):
            return None
        config: CommServerConfig = replace(
            self.transport.config,
            client_name=str(client_name),
            auto_start=False,
            timeout=float(self.timeout),
        )
        transport = CommServerTransport(config)
        session = ControllerSession(
            transport,
            backend_name="server",
            port=config.port,
            baudrate=config.baudrate,
            server_endpoint=config.endpoint,
            timeout=self.timeout,
            readonly=True,
        )
        session.open()
        return session

    def __enter__(self) -> ControllerSession:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def transact(self, frame: bytes) -> RciResponse:
        """Execute one serialized request/response transaction."""
        with self._io_lock:
            return self._transact_unlocked(frame)

    def _transact_unlocked(self, frame: bytes) -> RciResponse:
        if not self.transport.is_open:
            raise TransportError("session transport is not open")
        identity = _request_identity(frame)
        try:
            raw = self.transport.exchange(frame, b"\r", self.timeout)
        except TransportError as exc:
            # A Windows WriteFile/ReadFile failure means this handle can no
            # longer be trusted (device reset, unplug, or driver revocation).
            # Invalidate it immediately so callers cannot keep flooding an
            # already-dead COM port with follow-up requests.
            self._connected = False
            try:
                self.transport.close()
            except Exception:
                pass
            raise TransportError(f"{identity[2]}: {exc}") from exc
        return self._parse_transaction_response(raw, identity)

    def transact_many(self, frames: Sequence[bytes]) -> list[RciResponse]:
        """Execute several RCI frames as one transport-level transaction group.

        On a Communication Server backend this crosses the network once.  The
        server still exchanges every frame with the controller sequentially and
        returns responses in exactly the same order.  Serial and mock backends
        use the compatible sequential fallback supplied by ``Transport``.
        """
        frame_list = [bytes(frame) for frame in frames]
        if not frame_list:
            return []
        with self._io_lock:
            if not self.transport.is_open:
                raise TransportError("session transport is not open")
            identities = [_request_identity(frame) for frame in frame_list]
            try:
                raw_responses = self.transport.exchange_many(
                    [(frame, b"\r", self.timeout) for frame in frame_list]
                )
            except TransportError as exc:
                self._connected = False
                try:
                    self.transport.close()
                except Exception:
                    pass
                first_mnemonic = identities[0][2]
                label = (
                    first_mnemonic
                    if len(identities) == 1
                    else f"{first_mnemonic} batch[{len(identities)}]"
                )
                raise TransportError(f"{label}: {exc}") from exc
            if len(raw_responses) != len(frame_list):
                raise ProtocolError(
                    "transport response count mismatch: "
                    f"expected {len(frame_list)}, got {len(raw_responses)}"
                )
            return [
                self._parse_transaction_response(raw, identity)
                for raw, identity in zip(raw_responses, identities)
            ]

    def _query_snapshot(
        self,
        queries: Sequence[
            tuple[str, bytes, Callable[[RciResponse], object]]
        ],
    ) -> dict[str, object]:
        """Execute named read queries in one batch and decode them in order."""
        responses = self.transact_many([frame for _, frame, _ in queries])
        return {
            name: decoder(response)
            for (name, _, decoder), response in zip(queries, responses)
        }

    def _decode_raw_ints(self, response: RciResponse, mnemonic: str) -> list[int]:
        self.encoder.ensure_ok(response, mnemonic)
        return [int(value) for value in response.data_tokens]

    def _decode_raw_floats(
        self, response: RciResponse, mnemonic: str
    ) -> list[float]:
        self.encoder.ensure_ok(response, mnemonic)
        return [float(value) for value in response.data_tokens]

    def _parse_transaction_response(
        self,
        raw: bytes,
        identity: tuple[str, str, str],
    ) -> RciResponse:
        expected_msg_id, expected_crl, expected_mnemonic = identity
        response = self.encoder.parse(raw)
        self.last_response = response
        if response.msg_id != expected_msg_id:
            raise ProtocolError(
                f"response message id mismatch: expected {expected_msg_id!r}, "
                f"got {response.msg_id!r}"
            )
        if response.protocol_ok and response.crl != expected_crl:
            raise ProtocolError(
                f"response CRL mismatch: expected {expected_crl}, got {response.crl}"
            )
        if response.protocol_ok and response.mnemonic != expected_mnemonic:
            raise ProtocolError(
                "response mnemonic mismatch: "
                f"expected {expected_mnemonic}, got {response.mnemonic}"
            )
        return response

    def get_version(self) -> FirmwareVersion:
        return self.encoder.decode_bgvis(self.transact(self.encoder.bgvis()))

    def get_loop_status(self) -> LoopStatus:
        return self.encoder.decode_bgsts(self.transact(self.encoder.bgsts()))

    def set_loop_status(self, individual: int, system: int) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.bssts(individual, system)), "BSSTS")

    def get_pos_pneum_digital_status(self) -> tuple[int, int, int, int]:
        """Return position loop, pneumatic loop, digital input and output words.

        This is the original ``GetPosPneumIndividualLoopStatus``/``BGSST``
        path.  It must not be inferred from ``BGSTS``: that command's first
        word contains velocity-axis states only.
        """
        return self.encoder.decode_bgsst(self.transact(self.encoder.bgsst()))

    def set_pos_pneum_individual_loop_status(
        self, position: int, pneumatic: int
    ) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(
            self.transact(self.encoder.bssst(position, pneumatic)), "BSSST"
        )

    def get_sample_frequency(self) -> float:
        return self.encoder.decode_ngsfr(self.transact(self.encoder.ngsfr()))

    def get_velocity_filter(self, axis: int, stage: int) -> FilterStage:
        return self.encoder.decode_vgvfs(
            self.transact(self.encoder.vgvfs(axis, stage)), axis, stage
        )

    def get_velocity_filters(
        self, keys: Sequence[tuple[int, int]]
    ) -> list[FilterStage]:
        addresses = [(int(axis), int(stage)) for axis, stage in keys]
        responses = self.transact_many(
            [self.encoder.vgvfs(axis, stage) for axis, stage in addresses]
        )
        return [
            self.encoder.decode_vgvfs(response, axis, stage)
            for response, (axis, stage) in zip(responses, addresses)
        ]

    def set_velocity_filter(self, stage: FilterStage) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.vsvfs(stage)), "VSVFS")

    def get_velocity_sensor_matrix(self, axis: int) -> list[float]:
        return self.encoder.decode_vgsmv(self.transact(self.encoder.vgsmv(axis)))

    def set_velocity_sensor_matrix(self, axis: int, values: list[float]) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.vssmv(axis, values)), "VSSMV")

    def get_velocity_motor_matrix(self, axis: int) -> list[float]:
        return self.encoder.decode_vgmmv(self.transact(self.encoder.vgmmv(axis)))

    def set_velocity_motor_matrix(self, axis: int, values: list[float]) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.vsmmv(axis, values)), "VSMMV")

    def get_geophone_inputs(self) -> list[int]:
        return self.encoder.decode_vggiv(self.transact(self.encoder.vggiv()))

    def get_position_sensor_matrix(self, axis: int) -> list[float]:
        return self.encoder.decode_cgsmv(self.transact(self.encoder.cgsmv(axis)))

    def set_position_sensor_matrix(self, axis: int, values: list[float]) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.cssmv(axis, values)), "CSSMV")

    def get_position_motor_matrix(self, axis: int) -> list[float]:
        return self.encoder.decode_cgmmv(self.transact(self.encoder.cgmmv(axis)))

    def set_position_motor_matrix(self, axis: int, values: list[float]) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.csmmv(axis, values)), "CSMMV")

    def get_proximity_offsets(self, count: int = 6) -> list[float]:
        """Read six- or eight-proximity offsets according to controller config."""
        if int(count) == 8:
            return self.encoder.decode_cgpox(self.transact(self.encoder.cgpox()))
        return self.encoder.decode_cgpov(self.transact(self.encoder.cgpov()))

    def set_proximity_offsets(self, values: list[float]) -> None:
        self._ensure_writable()
        if len(values) == 8:
            self.encoder.ensure_ok(self.transact(self.encoder.cspox(values)), "CSPOX")
        else:
            self.encoder.ensure_ok(self.transact(self.encoder.cspov(values)), "CSPOV")

    def use_current_proximity_offsets(self, count: int = 6) -> None:
        self._ensure_writable()
        if int(count) == 8:
            self.encoder.ensure_ok(self.transact(self.encoder.caucx()), "CAUCX")
        else:
            self.encoder.ensure_ok(self.transact(self.encoder.cauco()), "CAUCO")

    def get_proximity_input_values(self, count: int = 6) -> list[float]:
        """Read live proximity sensors using the 6- or 8-channel command."""
        if int(count) == 8:
            return self.encoder.decode_pggix(self.transact(self.encoder.pggix()))
        return self.encoder.decode_pggiv(self.transact(self.encoder.pggiv()))

    def get_proximity_filter(self, axis: int, stage: int) -> FilterStage:
        return self.encoder.decode_cgpfs(
            self.transact(self.encoder.cgpfs(axis, stage)), axis, stage
        )

    def get_proximity_filters(
        self, keys: Sequence[tuple[int, int]]
    ) -> list[FilterStage]:
        addresses = [(int(axis), int(stage)) for axis, stage in keys]
        responses = self.transact_many(
            [self.encoder.cgpfs(axis, stage) for axis, stage in addresses]
        )
        return [
            self.encoder.decode_cgpfs(response, axis, stage)
            for response, (axis, stage) in zip(responses, addresses)
        ]

    def set_proximity_filter(self, stage: FilterStage) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.cspfs(stage)), "CSPFS")

    # --- Feedforward ---

    def get_ff_status(self) -> list[str]:
        return self.encoder.decode_fgffs(self.transact(self.encoder.fgffs()))

    def set_ff_status(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.fsffs(*params)), "FSFFS")

    def get_ff_filter(self, source: int, stage: int) -> FilterStage:
        # Original SAMBA wrappers address reference/secondary filters as
        # (Axis=0, Source=n, Stage=0..5), but error filters as
        # (Axis=n, Source=0, Stage=6..7).  Keep the existing compact public
        # API while sending all three required wire parameters.
        wire_axis = source if stage >= 6 else 0
        wire_source = 0 if stage >= 6 else source
        return self.encoder.decode_fgpfs(
            self.transact(self.encoder.fgpfs(wire_axis, wire_source, stage)),
            source,
            stage,
        )

    def get_ff_filters(
        self, keys: Sequence[tuple[int, int]]
    ) -> list[FilterStage]:
        addresses = [(int(source), int(stage)) for source, stage in keys]
        wire_addresses = [
            (source if stage >= 6 else 0, 0 if stage >= 6 else source, stage)
            for source, stage in addresses
        ]
        responses = self.transact_many(
            [
                self.encoder.fgpfs(wire_axis, wire_source, stage)
                for wire_axis, wire_source, stage in wire_addresses
            ]
        )
        return [
            self.encoder.decode_fgpfs(response, source, stage)
            for response, (source, stage) in zip(responses, addresses)
        ]

    def set_ff_filter(self, stage: FilterStage) -> None:
        self._ensure_writable()
        wire_axis = stage.axis if stage.stage >= 6 else 0
        wire_source = 0 if stage.stage >= 6 else stage.axis
        self.encoder.ensure_ok(
            self.transact(self.encoder.fspfs(wire_axis, wire_source, stage)),
            "FSPFS",
        )

    def get_ff_inputs(self) -> list[str]:
        return self.encoder.decode_fgffi(self.transact(self.encoder.fgffi()))

    # --- Diagnostics ---

    def get_noise_type(self) -> int:
        return self.encoder.decode_dgnty(self.transact(self.encoder.dgnty()))

    def set_noise_type(self, noise_type: int) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dsnty(noise_type)), "DSNTY")

    def get_noise_gain(self) -> float:
        return self.encoder.decode_dgnsg(self.transact(self.encoder.dgnsg()))

    def set_noise_gain(self, gain: float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dsnsg(gain)), "DSNSG")

    def get_noise_inject_point(self) -> list[str]:
        return self.encoder.decode_dgnip(self.transact(self.encoder.dgnip()))

    def set_noise_inject_point(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dsnip(*params)), "DSNIP")

    def get_switch_status(self) -> list[str]:
        return self.encoder.decode_dgcss(self.transact(self.encoder.dgcss()))

    # --- NVRAM ---

    def nvram_save(self) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.nasup()), "NASUP")

    def nvram_restore(self) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.narup()), "NARUP")

    def nvram_clear(self) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.naclr()), "NACLR")

    def check_nvram_checksums(self) -> list[int]:
        """Return status, then saved/actual monitor, firmware and config checksums."""
        return self.encoder.decode_bcncs(self.transact(self.encoder.bcncs()))

    def build_nvram_checksums(self) -> list[int]:
        """Recalculate and return monitor, firmware and configuration checksums."""
        self._ensure_writable()
        return self.encoder.decode_bbncs(self.transact(self.encoder.bbncs()))

    # --- Basic extras ---

    def get_output_limit(self) -> int:
        return self.encoder.decode_bgopl(self.transact(self.encoder.bgopl()))

    def set_output_limit(self, percent: int) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.bsopl(percent)), "BSOPL")


    # --- Switch ---

    def get_switch_signal(self) -> list[str]:
        return self.encoder.decode_bgsws(self.transact(self.encoder.bgsws()))

    def set_switch_signal(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.bssws(*params)), "BSSWS")

    def get_switch_conditions(self) -> list[str]:
        return self.encoder.decode_bgocd(self.transact(self.encoder.bgocd()))

    def set_switch_conditions(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.bsocd(*params)), "BSOCD")

    # --- Motor protection ---

    def get_motor_overcurrent_config(self) -> list[str]:
        return self.encoder.decode_bgocv(self.transact(self.encoder.bgocv()))

    def set_motor_overcurrent_config(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.bsocv(*params)), "BSOCV")

    def get_motor_power_values(self) -> list[float]:
        return self.encoder.decode_bgmpv(self.transact(self.encoder.bgmpv()))

    def get_motor_failsafe_status(self) -> list[str]:
        return self.encoder.decode_bgmps(self.transact(self.encoder.bgmps()))

    # --- Performance ---

    def get_performance_monitor(self) -> list[str]:
        return self.encoder.decode_dgpmv(self.transact(self.encoder.dgpmv()))

    def set_performance_monitor(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dspmv(*params)), "DSPMV")

    def get_performance_status(self) -> list[str]:
        return self.encoder.decode_dgpms(self.transact(self.encoder.dgpms()))

    def get_system_load(self) -> float:
        return self.encoder.decode_dgslo(self.transact(self.encoder.dgslo()))

    # --- Ramp ---

    def get_startup_ramp(self) -> list[str]:
        return self.encoder.decode_bgsut(self.transact(self.encoder.bgsut()))

    def set_startup_ramp(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.bssut(*params)), "BSSUT")

    # --- DAC/ADC ---

    def get_adc_sequence(self) -> list[int]:
        return self.encoder.decode_bgads(self.transact(self.encoder.bgads()))

    def set_adc_sequence(self, values: list[int]) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.bsads(values)), "BSADS")

    def get_dac_sequence(self) -> list[int]:
        return self.encoder.decode_bgdas(self.transact(self.encoder.bgdas()))

    def set_dac_sequence(self, values: list[int]) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.bsdas(values)), "BSDAS")

    # --- Pneumatic ---

    def get_pneumatic_filter(self, axis: int, stage: int) -> FilterStage:
        return self.encoder.decode_pgpaf(
            self.transact(self.encoder.pgpaf(axis, stage)), axis, stage
        )

    def get_pneumatic_filters(
        self, keys: Sequence[tuple[int, int]]
    ) -> list[FilterStage]:
        addresses = [(int(axis), int(stage)) for axis, stage in keys]
        responses = self.transact_many(
            [self.encoder.pgpaf(axis, stage) for axis, stage in addresses]
        )
        return [
            self.encoder.decode_pgpaf(response, axis, stage)
            for response, (axis, stage) in zip(responses, addresses)
        ]

    def set_pneumatic_filter(self, stage: FilterStage) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.pspaf(stage)), "PSPAF")

    def get_pneumatic_steering_matrix(self, axis: int) -> list[float]:
        # PGPSM is the command used by the original IIDETCMFD2 source and the
        # RCI specification.  Its response length is firmware-dependent, so
        # the decoder intentionally keeps every returned input/output value.
        return self.encoder.decode_pgpsm(self.transact(self.encoder.pgpsm(axis)))

    def set_pneumatic_steering_matrix(self, axis: int, values: list[float]) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(
            self.transact(self.encoder.pspsm(axis, values)), "PSPSM"
        )

    def get_pneumatic_config(self) -> list[str]:
        return self.encoder.decode_pgpcp(self.transact(self.encoder.pgpcp()))

    def set_pneumatic_config(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.pspcp(*params)), "PSPCP")

    def get_pneumatic_valve_offsets(self) -> list[float]:
        # Firmware >=3.3 may return more values under the same documented
        # PGPVO mnemonic.  Do not probe invented extension mnemonics first.
        return self.encoder.decode_pgpvo(self.transact(self.encoder.pgpvo()))

    def set_pneumatic_valve_offsets(self, values: list[float]) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(
            self.transact(self.encoder.pspvo(values)), "PSPVO"
        )

    def get_pneumatic_axes_status(self) -> list[str]:
        return self.encoder.decode_pgpas(self.transact(self.encoder.pgpas()))

    def get_pneumatic_heights_valves(self) -> list[str]:
        return self.encoder.decode_pgphv(self.transact(self.encoder.pgphv()))

    def get_pneumatic_status_timer(self) -> tuple[float, float]:
        """Return the last pneumatic OK/NOK state durations in seconds."""
        return self.encoder.decode_pgpst(self.transact(self.encoder.pgpst()))

    def get_pneumatic_proximity_inputs(self) -> list[float]:
        # Historical compatibility alias.  PGGIV is the six-channel position
        # proximity read even though it lives in the RCI pneumatic group.
        return self.get_proximity_input_values(6)

    def get_dither_value(self) -> float:
        return self.encoder.decode_pgdit(self.transact(self.encoder.pgdit()))

    def set_dither_value(self, value: float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.psdit(value)), "PSDIT")

    def get_dither_frequency(self) -> float:
        return self.encoder.decode_pgdfr(self.transact(self.encoder.pgdfr()))

    def set_dither_frequency(self, freq: float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.psdfr(freq)), "PSDFR")

    def get_dither_alpha(self) -> float:
        return self.encoder.decode_pgdca(self.transact(self.encoder.pgdca()))

    def set_dither_alpha(self, alpha: float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.psdca(alpha)), "PSDCA")

    def get_pneumatic_setpoint_status(self) -> int:
        return self.encoder.decode_pgpss(self.transact(self.encoder.pgpss()))

    def set_pneumatic_setpoint_status(self, use_all: int) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.pspss(use_all)), "PSPSS")

    def use_current_pressure_offsets(self, condition: int = 1) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(
            self.transact(self.encoder.pauco(condition)), "PAUCO"
        )

    def move_pneumatic(self, action: int) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.pamov(action)), "PAMOV")

    # --- Logging ---

    def get_event_trace_params(self) -> list[str]:
        return self.encoder.decode_dgetp(self.transact(self.encoder.dgetp()))

    def set_event_trace_params(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dsetp(*params)), "DSETP")

    def get_event_trace_info(self) -> list[str]:
        return self.encoder.decode_dgeti(self.transact(self.encoder.dgeti()))

    def start_stop_event_tracing(self, logging_status: int) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dsset(logging_status)), "DSSET")

    # --- PFF ---

    def get_pff_config(self) -> list[str]:
        return self.encoder.decode_fgcpf(self.transact(self.encoder.fgcpf()))

    def set_pff_config(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.fscpf(*params)), "FSCPF")

    # --- Logging deep ---

    def get_logged_sample(self, trace_num: int, sample_num: int) -> list[float]:
        return self.encoder.decode_dgldv(
            self.transact(self.encoder.dgldv(trace_num, sample_num))
        )

    def get_logged_data(self, trace_num: int) -> list[float]:
        return self.encoder.decode_dglda(self.transact(self.encoder.dglda(trace_num)))

    def download_logged_trace(
        self,
        trace_num: int,
        max_samples: int | None = None,
        *,
        progress_callback=None,
        cancel_event: threading.Event | None = None,
    ) -> list[list[float]]:
        """Download a saved trace sample-by-sample via DGLDV.

        Returns ``rows[sample][channel]``.  Newer firmware reports the actual
        number of logged samples as the fifth DGETI value; using it avoids both
        thousands of needless requests and an OUT_OF_RANGE at the partial end
        of a trace.  Older firmware falls back to DGETP.MaxBuffLen.
        """
        params = self.get_event_trace_params()
        info = self.get_event_trace_info()
        try:
            buff_len = int(params[1]) if len(params) > 1 else 16
            mon_n = int(params[2]) if len(params) > 2 else 1
        except ValueError:
            buff_len, mon_n = 16, 1
        try:
            actual_samples = int(info[4]) if len(info) > 4 else 0
        except ValueError:
            actual_samples = 0
        if actual_samples > 0:
            buff_len = min(buff_len, actual_samples)
        if max_samples is not None:
            buff_len = min(buff_len, max_samples)
        rows: list[list[float]] = []
        for sample in range(buff_len):
            if cancel_event is not None and cancel_event.is_set():
                break
            vals = self.get_logged_sample(trace_num, sample)
            if mon_n and len(vals) > mon_n:
                vals = vals[:mon_n]
            rows.append(vals)
            if progress_callback is not None:
                progress_callback(sample + 1, buff_len)
        return rows

    def get_monitor_values(self, index1: int = 0, index2: int = 3) -> list[float]:
        return self.encoder.decode_dgmsv(self.transact(self.encoder.dgmsv(index1, index2)))

    def get_event_time(self, trace_num: int) -> list[str]:
        return self.encoder.decode_dgevt(self.transact(self.encoder.dgevt(trace_num)))

    # --- PFF deep ---

    def get_pff_gains_as(self, axis: int, source: int) -> list[float]:
        return self.encoder.decode_fggpf(
            self.transact(self.encoder.fggpf_axis_source(axis, source))
        )

    def set_pff_gains_as(self, axis: int, source: int, gains: list[float]) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(
            self.transact(self.encoder.fsgpf_axis_source(axis, source, gains)), "FSGPF"
        )

    # --- System limits ---

    def get_ff_output_limit(self) -> int:
        return self.encoder.decode_bgffl(self.transact(self.encoder.bgffl()))

    def set_ff_output_limit(self, percent: int) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.bsffl(percent)), "BSFFL")

    def get_fb_limiter(self) -> list[float]:
        return self.encoder.decode_bgfbl(self.transact(self.encoder.bgfbl()))

    def set_fb_limiter(self, values: list[float]) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.bsfbl(values)), "BSFBL")

    def get_global_system_constants(self) -> list[str]:
        return self.encoder.decode_bggsc(self.transact(self.encoder.bggsc()))

    def get_controller_type(self) -> list[str]:
        return self.encoder.decode_bgcot(self.transact(self.encoder.bgcot()))

    def set_controller_type(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.bscot(*params)), "BSCOT")

    # --- Stage FF Z-rotation parameters ---

    def get_ff_zrot_params(self) -> list[str]:
        return self.encoder.decode_fgzrp(self.transact(self.encoder.fgzrp()))

    def set_ff_zrot_params(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.fszrp(*params)), "FSZRP")

    # --- Diagnostics deep ---

    def get_noise_frequency(self) -> float:
        return self.encoder.decode_dgnsf(self.transact(self.encoder.dgnsf()))

    def set_noise_frequency(self, freq: float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dsnsf(freq)), "DSNSF")

    def get_noise_filter_usage(self) -> str:
        return self.encoder.decode_dgnfu(self.transact(self.encoder.dgnfu()))

    def set_noise_filter_usage(self, on_off: str) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dsnfu(on_off)), "DSNFU")

    def get_noise_filter_stage(self, stage: int) -> FilterStage:
        return self.encoder.decode_dgnfs(self.transact(self.encoder.dgnfs(stage)), stage)

    def set_noise_filter_stage(self, stage: FilterStage) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dsnfs(stage)), "DSNFS")

    def get_diagnostic_outputs(self) -> list[str]:
        return self.encoder.decode_dgdos(self.transact(self.encoder.dgdos()))

    def set_diagnostic_outputs(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dsdos(*params)), "DSDOS")

    def get_test_mode(self) -> list[str]:
        return self.encoder.decode_dgtmo(self.transact(self.encoder.dgtmo()))

    def set_test_mode(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dstmo(*params)), "DSTMO")

    def get_digital_trace_info(self) -> list[str]:
        return self.encoder.decode_dgtiv(self.transact(self.encoder.dgtiv()))

    def set_digital_trace_info(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dstiv(*params)), "DSTIV")

    def start_digital_trace(self) -> list[str]:
        self._ensure_writable()
        return self.encoder.decode_dasta(self.transact(self.encoder.dasta()))

    def get_digital_trace_status(self) -> list[str]:
        return self.encoder.decode_dgtas(self.transact(self.encoder.dgtas()))

    def get_digital_trace_buffer(self, read_offset: int = -1) -> list[float]:
        return self.encoder.decode_dgtbv(
            self.transact(self.encoder.dgtbv(read_offset))
        )

    def get_digital_trace_buffers(
        self, read_offsets: Sequence[int]
    ) -> list[list[float]]:
        """Read several text DGTBV chunks in one transport round trip."""

        offsets = [int(value) for value in read_offsets]
        responses = self.transact_many(
            [self.encoder.dgtbv(read_offset) for read_offset in offsets]
        )
        return [self.encoder.decode_dgtbv(response) for response in responses]

    def get_digital_trace_buffer_binary(
        self, read_offset: int, sample_count: int = 40
    ) -> list[float]:
        """Read one binary trace response without allowing interleaving."""
        return self.get_digital_trace_buffers_binary(
            [(int(read_offset), int(sample_count))]
        )[0]

    def get_digital_trace_buffers_binary(
        self, requests: Sequence[tuple[int, int]]
    ) -> list[list[float]]:
        """Read several binary DGTBB chunks in one transport round trip."""

        items = [(int(offset), int(count)) for offset, count in requests]
        if not items:
            return []
        frames = [self.encoder.dgtbb(offset, count) for offset, count in items]
        with self._io_lock:
            if not self.transport.is_open:
                raise TransportError("session transport is not open")
            identities = [_request_identity(frame) for frame in frames]
            try:
                raw_responses = self.transport.exchange_many(
                    [(frame, b"\r", self.timeout) for frame in frames]
                )
            except TransportError as exc:
                self._connected = False
                try:
                    self.transport.close()
                except Exception:
                    pass
                raise TransportError(f"DGTBB batch[{len(frames)}]: {exc}") from exc
            if len(raw_responses) != len(frames):
                raise ProtocolError(
                    "DGTBB response count mismatch: "
                    f"expected {len(frames)}, got {len(raw_responses)}"
                )
            values = [
                self._decode_dgtbb_response(raw, sample_count, identity)
                for raw, (_, sample_count), identity in zip(
                    raw_responses, items, identities
                )
            ]
            self.last_response = None
            return values

    @staticmethod
    def _decode_dgtbb_response(
        raw: bytes,
        sample_count: int,
        identity: tuple[str, str, str],
    ) -> list[float]:
        """Decode interleaved Ch1/Ch2 values from one binary DGTBB frame.

        The binary payload is intentionally parsed here instead of passing it
        through ``parse_frame``: six-byte Opticon floats are not ASCII RCI
        tokens.  The controller sends two six-byte values per sample, in
        Ch1/Ch2 order.
        """
        _expected_msg_id, _expected_crl, expected_mnemonic = identity
        if expected_mnemonic.upper() != "DGTBB":
            raise ProtocolError(f"expected DGTBB identity, got {identity!r}")
        marker = b" DGTBB "
        marker_pos = raw.upper().find(marker)
        if marker_pos < 0:
            raise ProtocolError(f"DGTBB response missing mnemonic: {raw!r}")
        # The compact binary response normally ends with ``##`` plus the
        # terminator, but real controllers may append their two ASCII CRC
        # characters instead.  In the latter form the CRC is immediately
        # adjacent to the last six-byte value, so stripping it before splitting
        # is essential: otherwise a complete 80-value response is reported as
        # 79 values plus one invalid eight-byte field.
        payload = raw[marker_pos + len(marker):]
        payload = payload.rstrip(b"\r\n")
        expected_values = int(sample_count) * 2
        if payload.endswith(b"##"):
            payload = payload[:-2]
        else:
            crc = payload[-2:]
            candidate = payload[:-2]
            crc_is_ascii = len(crc) == 2 and all(
                byte in b"0123456789abcdefABCDEF" for byte in crc
            )
            candidate_fields = [
                field for field in candidate.split(b" ") if len(field) == 6
            ]
            if crc_is_ascii and len(candidate_fields) >= expected_values:
                payload = candidate
        fields = [field for field in payload.split(b" ") if len(field) == 6]
        if len(fields) < expected_values:
            raise ProtocolError(
                f"DGTBB short payload: expected {expected_values} six-byte values, "
                f"got {len(fields)}"
            )
        fields = fields[-expected_values:]
        return [_decode_opticon_float(field) for field in fields]

    # --- Position devices / offsets ---

    def get_position_sensor_devices(self) -> list[str]:
        return self.encoder.decode_cgpsd(self.transact(self.encoder.cgpsd()))

    def set_position_sensor_devices(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.cspsd(*params)), "CSPSD")

    def get_position_sensor_devices_for_axis(self, axis: int) -> list[str]:
        return self.encoder.decode_cgpsd(self.transact(self.encoder.cgpsd(axis)))

    def set_position_sensor_devices_for_axis(
        self, axis: int, values: list[int]
    ) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(
            self.transact(self.encoder.cspsd(axis, *values)), "CSPSD"
        )

    def get_position_motor_devices(self) -> list[str]:
        return self.encoder.decode_cgpmd(self.transact(self.encoder.cgpmd()))

    def set_position_motor_devices(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.cspmd(*params)), "CSPMD")

    def get_position_motor_devices_for_axis(self, axis: int) -> list[str]:
        return self.encoder.decode_cgpmd(self.transact(self.encoder.cgpmd(axis)))

    def set_position_motor_devices_for_axis(
        self, axis: int, values: list[int]
    ) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(
            self.transact(self.encoder.cspmd(axis, *values)), "CSPMD"
        )

    def get_motor_offsets(self) -> list[float]:
        return self.encoder.decode_cgmov(self.transact(self.encoder.cgmov()))

    def set_motor_offsets(self, values: list[float]) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.csmov(values)), "CSMOV")

    def get_linear_motor_offsets(self) -> list[float]:
        """Read the optional 12-channel linear motor offsets (LGLMO)."""
        response = self.raw_command("LGLMO")
        self.encoder.ensure_ok(response, "LGLMO")
        return [float(value) for value in response.data_tokens]

    def set_linear_motor_offsets(self, values: list[float]) -> None:
        """Write the optional 12-channel linear motor offsets (LSLMO)."""
        self._ensure_writable()
        response = self.raw_command("LSLMO", *[float(value) for value in values[:12]])
        self.encoder.ensure_ok(response, "LSLMO")

    # --- Setup extras ---

    def set_sample_frequency(self, hz: float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.nssfr(hz)), "NSSFR")

    def get_controller_config(self) -> list[str]:
        return self.encoder.decode_ngexl(self.transact(self.encoder.ngexl()))

    def set_controller_config(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.nsexl(*params)), "NSEXL")

    def get_adc_set_number(self) -> int:
        return self.encoder.decode_ngasn(self.transact(self.encoder.ngasn()))

    def set_adc_set_number(self, n: int) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.nsasn(n)), "NSASN")

    def get_temp_sensor_adc_mapping(self) -> list[int]:
        response = self.raw_command("BGTSA")
        self.encoder.ensure_ok(response, "BGTSA")
        return [int(value) for value in response.data_tokens]

    def set_temp_sensor_adc_mapping(self, values: list[int]) -> None:
        self._ensure_writable()
        response = self.raw_command("BSTSA", *[int(value) for value in values])
        self.encoder.ensure_ok(response, "BSTSA")

    # --- Analysis ---

    def get_analysis_params(self) -> list[str]:
        return self.encoder.decode_lganp(self.transact(self.encoder.lganp()))

    def set_analysis_params(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.lsanp(*params)), "LSANP")

    def get_analysis_input(self) -> list[str]:
        return self.encoder.decode_lgais(self.transact(self.encoder.lgais()))

    def set_analysis_input(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.lsais(*params)), "LSAIS")

    def get_analysis_filter_config(self, *params: str | int | float) -> list[str]:
        return self.encoder.decode_lgafc(self.transact(self.encoder.lgafc(*params)))

    def set_analysis_filter_config(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.lsafc(*params)), "LSAFC")

    def get_analysis_filter_outputs(self) -> list[float]:
        return self.encoder.decode_lgafo(self.transact(self.encoder.lgafo()))

    def get_analysis_event_num(self) -> list[str]:
        return self.encoder.decode_lgaen(self.transact(self.encoder.lgaen()))

    def get_analysis_events(self) -> list[str]:
        return self.encoder.decode_lgaev(self.transact(self.encoder.lgaev()))

    def get_analysis_filter_spec(self) -> list[str]:
        return self.encoder.decode_lgafs(self.transact(self.encoder.lgafs()))

    def set_analysis_filter_spec(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.lsafs(*params)), "LSAFS")

    # --- FF methods matching SAMBA19xLib.TCMFDRCI ---

    def get_ff_config(self) -> list[str]:
        return self.encoder.decode_fgffc(self.transact(self.encoder.fgffc()))

    def set_ff_config(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.fsffc(*params)), "FSFFC")

    def get_ff_parameters(self, source: int) -> list[str]:
        return self.encoder.decode_fgffp(self.transact(self.encoder.fgffp(source)))

    def set_ff_parameters(
        self,
        source: int,
        outputs: str | int,
        adaptive: str | int | bool,
        adaption_rate: float,
    ) -> None:
        self._ensure_writable()
        output_hex = outputs if isinstance(outputs, str) else f"{int(outputs):X}"
        if isinstance(adaptive, bool):
            adaptive_token: str | int = "T" if adaptive else "F"
        else:
            adaptive_token = adaptive
        self.encoder.ensure_ok(
            self.transact(
                self.encoder.fsffp(
                    source, output_hex, adaptive_token, float(adaption_rate)
                )
            ),
            "FSFFP",
        )

    def get_ff_gains(self, source: int) -> list[float]:
        gains: list[float] = []
        for axis in range(6):
            gains.extend(
                self.encoder.decode_fgffg(
                    self.transact(self.encoder.fgffg(axis, source))
                )
            )
        return gains

    def set_ff_gains(self, source: int, *gains: float) -> None:
        self._ensure_writable()
        # IIDETCMFD2 transfers five taps for one (axis, source) pair.  The UI
        # presents all six axes as one flat 6x5 matrix, so split that shape
        # into the six documented commands.  Keep a short-list compatibility
        # path for callers that intentionally edit axis zero only.
        if len(gains) == 30:
            for axis in range(6):
                chunk = gains[axis * 5:(axis + 1) * 5]
                self.encoder.ensure_ok(
                    self.transact(self.encoder.fsffg(axis, source, *chunk)),
                    "FSFFG",
                )
            return
        self.encoder.ensure_ok(
            self.transact(self.encoder.fsffg(0, source, *gains)), "FSFFG"
        )

    def get_stage_ff_multipliers(self) -> list[float]:
        return self.encoder.decode_fgsfm(self.transact(self.encoder.fgsfm()))

    def set_stage_ff_multipliers(self, values: list[float]) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.fssfm(values)), "FSSFM")

    def get_ff_adaptive_algo(self) -> int:
        return self.encoder.decode_fgfat(self.transact(self.encoder.fgfat()))

    def set_ff_adaptive_algo(self, algo: int) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.fsfat(algo)), "FSFAT")

    def reset_ff_fir(self, source: int) -> None:
        self._ensure_writable()
        for axis in range(6):
            self.encoder.ensure_ok(
                self.transact(self.encoder.farff(axis, source)), "FARFF"
            )

    def set_ff_inputs(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.fsffi(*params)), "FSFFI")

    # --- PFF methods ---

    def get_pff_filter(self, axis: int, source: int, stage: int) -> FilterStage:
        return self.encoder.decode_fgfsp(
            self.transact(self.encoder.fgfsp(axis, source, stage)), axis, source, stage
        )

    def get_pff_filters(
        self, keys: Sequence[tuple[int, int, int]]
    ) -> list[FilterStage]:
        addresses = [
            (int(axis), int(source), int(stage)) for axis, source, stage in keys
        ]
        responses = self.transact_many(
            [
                self.encoder.fgfsp(axis, source, stage)
                for axis, source, stage in addresses
            ]
        )
        return [
            self.encoder.decode_fgfsp(response, axis, source, stage)
            for response, (axis, source, stage) in zip(responses, addresses)
        ]

    def set_pff_filter(self, axis: int, source: int, stage: int, filter_type: int, params: tuple[float, ...]) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(
            self.transact(self.encoder.fsfsp(axis, source, stage, filter_type, params)), "FSFSP"
        )

    def reset_pff_fir(self, axis: int, source: int) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.farpf(axis, source)), "FARPF")

    def get_pff_parameters(self, source: int) -> list[str]:
        return self.encoder.decode_fgppf(self.transact(self.encoder.fgppf(source)))

    def set_pff_parameters(self, source: int, outputs: str | int, adaption_rate: float) -> None:
        self._ensure_writable()
        output_hex = outputs if isinstance(outputs, str) else f"{int(outputs):X}"
        self.encoder.ensure_ok(
            self.transact(self.encoder.fsppf(source, output_hex, adaption_rate)),
            "FSPPF",
        )

    def get_pff_gains(self, axis: int = 0, source: int = 0) -> list[float]:
        return self.encoder.decode_fggpf(self.transact(self.encoder.fggpf_axis_source(axis, source)))

    def set_pff_gains(self, *args) -> None:
        """Set PFF gains. Supports new API: set_pff_gains(axis, source, gains_list)
        and old API: set_pff_gains(g1, g2, g3, ...)."""
        if len(args) == 3 and isinstance(args[2], (list, tuple)):
            axis, source, gains = args
            self._ensure_writable()
            self.encoder.ensure_ok(
                self.transact(self.encoder.fsgpf_axis_source(int(axis), int(source), list(gains))), "FSGPF"
            )
        else:
            # Old API: individual floats
            gains = list(args)
            self._ensure_writable()
            self.encoder.ensure_ok(
                self.transact(self.encoder.fsgpf_axis_source(0, 0, gains)), "FSGPF"
            )

    def get_pff_inputs(self) -> list[int]:
        return self.encoder.decode_fgipf(self.transact(self.encoder.fgipf()))

    def set_pff_inputs(self, inputs: list[int]) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.fsipf(inputs)), "FSIPF")

    # --- WAN-efficient page snapshots ---

    def get_system_setting_snapshot(self) -> dict[str, object]:
        """Read all System Setting page values with one transport batch."""
        return self._query_snapshot(
            [
                ("loop", self.encoder.bgsts(), self.encoder.decode_bgsts),
                ("sample_frequency", self.encoder.ngsfr(), self.encoder.decode_ngsfr),
                ("system_load", self.encoder.dgslo(), self.encoder.decode_dgslo),
                ("controller_config", self.encoder.ngexl(), self.encoder.decode_ngexl),
                ("performance", self.encoder.dgpmv(), self.encoder.decode_dgpmv),
                ("performance_status", self.encoder.dgpms(), self.encoder.decode_dgpms),
                ("switch_conditions", self.encoder.bgocd(), self.encoder.decode_bgocd),
                ("switch_signal", self.encoder.bgsws(), self.encoder.decode_bgsws),
                ("switch_status", self.encoder.dgcss(), self.encoder.decode_dgcss),
                ("startup_ramp", self.encoder.bgsut(), self.encoder.decode_bgsut),
            ]
        )

    def get_status_page_snapshot(
        self,
        *,
        include_loop: bool = True,
        include_switch_conditions: bool = True,
        include_axis_status: bool = True,
        include_events: bool = True,
    ) -> dict[str, object]:
        """Read loop/status/event words in one transport batch."""
        queries: list[tuple[str, bytes, Callable[[RciResponse], object]]] = []
        if include_loop:
            queries.append(("loop", self.encoder.bgsts(), self.encoder.decode_bgsts))
        queries.append(
            ("switch_status", self.encoder.dgcss(), self.encoder.decode_dgcss)
        )
        if include_switch_conditions:
            queries.append(
                (
                    "switch_conditions",
                    self.encoder.bgocd(),
                    self.encoder.decode_bgocd,
                )
            )
        if include_axis_status:
            queries.append(
                ("axis_status", self.encoder.bgsst(), self.encoder.decode_bgsst)
            )
        if include_events:
            queries.append(("events", self.encoder.dgade(), self.encoder.decode_dgade))
        return self._query_snapshot(queries)

    def get_live_refresh_snapshot(
        self,
        *,
        include_switch_conditions: bool = False,
        include_axis_status: bool = False,
        include_controller_config: bool = False,
        proximity_count: int = 0,
        include_motor: bool = False,
        include_power_supply: bool = False,
        include_pneumatic: bool = False,
        monitor_count: int = 0,
    ) -> dict[str, object]:
        """Collect one visible-page refresh in a single server RPC.

        This method is intentionally data-only so the server backend can run it
        on a worker thread.  The physical RCI exchanges remain ordered, while a
        300+ ms Tailscale round trip is paid once per tick instead of once per
        field group.
        """

        queries: list[tuple[str, bytes, Callable[[RciResponse], object]]] = [
            ("loop", self.encoder.bgsts(), self.encoder.decode_bgsts),
            ("switch_status", self.encoder.dgcss(), self.encoder.decode_dgcss),
        ]
        if include_switch_conditions:
            queries.append(
                (
                    "switch_conditions",
                    self.encoder.bgocd(),
                    self.encoder.decode_bgocd,
                )
            )
        if include_axis_status or include_pneumatic:
            queries.append(
                ("axis_status", self.encoder.bgsst(), self.encoder.decode_bgsst)
            )
        if include_controller_config:
            queries.append(
                (
                    "controller_config",
                    self.encoder.ngexl(),
                    self.encoder.decode_ngexl,
                )
            )
        count = int(proximity_count)
        if count:
            if count == 8:
                queries.append(
                    (
                        "proximity_values",
                        self.encoder.pggix(),
                        self.encoder.decode_pggix,
                    )
                )
            else:
                queries.append(
                    (
                        "proximity_values",
                        self.encoder.pggiv(),
                        self.encoder.decode_pggiv,
                    )
                )
        if include_motor:
            queries.extend(
                [
                    (
                        "motor_power",
                        self.encoder.bgmpv(),
                        self.encoder.decode_bgmpv,
                    ),
                    (
                        "motor_failsafe",
                        self.encoder.bgmps(),
                        self.encoder.decode_bgmps,
                    ),
                ]
            )
            if include_power_supply:
                queries.append(
                    (
                        "power_supply",
                        self.encoder.lgpsl(),
                        self.encoder.decode_lgpsl,
                    )
                )
        if include_pneumatic:
            queries.extend(
                [
                    (
                        "pneumatic_axes_status",
                        self.encoder.pgpas(),
                        self.encoder.decode_pgpas,
                    ),
                    (
                        "pneumatic_heights_valves",
                        self.encoder.pgphv(),
                        self.encoder.decode_pgphv,
                    ),
                    (
                        "pneumatic_status_timer",
                        self.encoder.pgpst(),
                        self.encoder.decode_pgpst,
                    ),
                    (
                        "pneumatic_setpoint_status",
                        self.encoder.pgpss(),
                        self.encoder.decode_pgpss,
                    ),
                ]
            )
        used_monitors = max(0, min(40, int(monitor_count)))
        if used_monitors:
            queries.append(
                (
                    "monitor_values",
                    self.encoder.dgmsv(0, used_monitors - 1),
                    self.encoder.decode_dgmsv,
                )
            )
        return self._query_snapshot(queries)

    def get_monitor_page_snapshot(self, count: int) -> dict[str, object]:
        """Read monitor definitions and current values in one transport batch."""
        signal_count = max(0, int(count))
        queries: list[tuple[str, bytes, Callable[[RciResponse], object]]] = []
        for signal_number in range(signal_count):
            queries.append(
                (
                    f"signal_{signal_number}",
                    self.encoder.dgmos(signal_number),
                    self.encoder.decode_dgmos,
                )
            )
        if signal_count:
            queries.append(
                (
                    "values",
                    self.encoder.dgmsv(0, signal_count - 1),
                    self.encoder.decode_dgmsv,
                )
            )
        snapshot = self._query_snapshot(queries)
        return {
            "signals": [snapshot[f"signal_{index}"] for index in range(signal_count)],
            "values": snapshot.get("values", []),
        }

    def get_logging_workspace_snapshot(self, monitor_count: int) -> dict[str, object]:
        """Read monitor definitions/live data and trace metadata in one batch."""

        count = max(1, min(40, int(monitor_count)))
        queries: list[tuple[str, bytes, Callable[[RciResponse], object]]] = []
        for signal_number in range(40):
            queries.append(
                (
                    f"signal_{signal_number}",
                    self.encoder.dgmos(signal_number),
                    self.encoder.decode_dgmos,
                )
            )
        queries.extend(
            [
                (
                    "values",
                    self.encoder.dgmsv(0, count - 1),
                    self.encoder.decode_dgmsv,
                ),
                ("params", self.encoder.dgetp(), self.encoder.decode_dgetp),
                ("info", self.encoder.dgeti(), self.encoder.decode_dgeti),
                ("event", self.encoder.dgets(), self.encoder.decode_dgets),
                (
                    "sample_frequency",
                    self.encoder.ngsfr(),
                    self.encoder.decode_ngsfr,
                ),
            ]
        )
        snapshot = self._query_snapshot(queries)
        snapshot["signals"] = [
            snapshot.pop(f"signal_{index}") for index in range(40)
        ]
        return snapshot

    def get_internal_logging_snapshot(self) -> dict[str, object]:
        """Read trace state/configuration without four WAN round trips."""

        return self._query_snapshot(
            [
                ("params", self.encoder.dgetp(), self.encoder.decode_dgetp),
                ("info", self.encoder.dgeti(), self.encoder.decode_dgeti),
                ("event", self.encoder.dgets(), self.encoder.decode_dgets),
                (
                    "sample_frequency",
                    self.encoder.ngsfr(),
                    self.encoder.decode_ngsfr,
                ),
            ]
        )

    def get_adc_dac_snapshot(self) -> dict[str, object]:
        """Read all AD/DA mapping values in one transport batch."""
        return self._query_snapshot(
            [
                ("adc", self.encoder.bgads(), self.encoder.decode_bgads),
                ("adc_set", self.encoder.ngasn(), self.encoder.decode_ngasn),
                (
                    "temperature",
                    self.encoder.raw("BGTSA"),
                    lambda response: self._decode_raw_ints(response, "BGTSA"),
                ),
                ("dac", self.encoder.bgdas(), self.encoder.decode_bgdas),
            ]
        )

    def get_motor_protection_snapshot(
        self, *, linear_offsets: bool, include_power_supply: bool
    ) -> dict[str, object]:
        """Read Motor Protection configuration/live values in one batch."""
        offset_query: tuple[str, bytes, Callable[[RciResponse], object]]
        if linear_offsets:
            offset_query = (
                "offsets",
                self.encoder.raw("LGLMO"),
                lambda response: self._decode_raw_floats(response, "LGLMO"),
            )
        else:
            offset_query = (
                "offsets",
                self.encoder.cgmov(),
                self.encoder.decode_cgmov,
            )
        queries: list[tuple[str, bytes, Callable[[RciResponse], object]]] = [
            ("config", self.encoder.bgocv(), self.encoder.decode_bgocv),
            ("cooling", self.encoder.bgmcc(), self.encoder.decode_bgmcc),
            ("loop", self.encoder.bgsts(), self.encoder.decode_bgsts),
            offset_query,
            ("power", self.encoder.bgmpv(), self.encoder.decode_bgmpv),
            ("failsafe", self.encoder.bgmps(), self.encoder.decode_bgmps),
            ("output_limit", self.encoder.bgopl(), self.encoder.decode_bgopl),
        ]
        if include_power_supply:
            queries.append(
                ("power_supply", self.encoder.lgpsl(), self.encoder.decode_lgpsl)
            )
        return self._query_snapshot(queries)

    def get_velocity_tuning_snapshot(
        self, keys: Sequence[tuple[int, int]]
    ) -> dict[str, object]:
        """Read the limiter and all requested velocity filters in one batch."""
        addresses = [(int(axis), int(stage)) for axis, stage in keys]
        queries: list[tuple[str, bytes, Callable[[RciResponse], object]]] = [
            ("limiters", self.encoder.bgfbl(), self.encoder.decode_bgfbl)
        ]
        for index, (axis, stage) in enumerate(addresses):
            queries.append(
                (
                    f"filter_{index}",
                    self.encoder.vgvfs(axis, stage),
                    lambda response, axis=axis, stage=stage: self.encoder.decode_vgvfs(
                        response, axis, stage
                    ),
                )
            )
        snapshot = self._query_snapshot(queries)
        return {
            "limiters": snapshot["limiters"],
            "filters": [snapshot[f"filter_{index}"] for index in range(len(addresses))],
        }

    def get_diagnostics_snapshot(self) -> dict[str, object]:
        """Read the complete excitation/diagnostics group in one batch."""
        queries: list[tuple[str, bytes, Callable[[RciResponse], object]]] = [
            ("noise_type", self.encoder.dgnty(), self.encoder.decode_dgnty),
            ("noise_gain", self.encoder.dgnsg(), self.encoder.decode_dgnsg),
            ("noise_frequency", self.encoder.dgnsf(), self.encoder.decode_dgnsf),
            ("inject", self.encoder.dgnip(), self.encoder.decode_dgnip),
            ("outputs", self.encoder.dgdos(), self.encoder.decode_dgdos),
            ("filter_usage", self.encoder.dgnfu(), self.encoder.decode_dgnfu),
        ]
        for stage in range(4):
            queries.append(
                (
                    f"filter_{stage}",
                    self.encoder.dgnfs(stage),
                    lambda response, stage=stage: self.encoder.decode_dgnfs(
                        response, stage
                    ),
                )
            )
        snapshot = self._query_snapshot(queries)
        snapshot["filters"] = [snapshot.pop(f"filter_{stage}") for stage in range(4)]
        return snapshot

    def get_position_tuning_snapshot(
        self,
        keys: Sequence[tuple[int, int]],
        *,
        proximity_count: int,
        include_cascaded: bool,
    ) -> dict[str, object]:
        """Read the complete Position Tuning page in one transport batch."""
        addresses = [(int(axis), int(stage)) for axis, stage in keys]
        queries: list[tuple[str, bytes, Callable[[RciResponse], object]]] = []
        for index, (axis, stage) in enumerate(addresses):
            queries.append(
                (
                    f"filter_{index}",
                    self.encoder.cgpfs(axis, stage),
                    lambda response, axis=axis, stage=stage: self.encoder.decode_cgpfs(
                        response, axis, stage
                    ),
                )
            )
        if int(proximity_count) == 8:
            queries.append(("offsets", self.encoder.cgpox(), self.encoder.decode_cgpox))
        else:
            queries.append(("offsets", self.encoder.cgpov(), self.encoder.decode_cgpov))
        if include_cascaded:
            for stage in range(3):
                queries.append(
                    (
                        f"cascaded_filter_{stage}",
                        self.encoder.cgpcf(stage),
                        lambda response, stage=stage: self.encoder.decode_cgpcf(
                            response, stage
                        ),
                    )
                )
            queries.append(
                (
                    "cascaded_parameter",
                    self.encoder.cgpcm(),
                    self.encoder.decode_cgpcm,
                )
            )
        queries.append(
            ("nonlinear", self.encoder.cgpnp(), self.encoder.decode_cgpnp)
        )
        snapshot = self._query_snapshot(queries)
        snapshot["filters"] = [
            snapshot.pop(f"filter_{index}") for index in range(len(addresses))
        ]
        snapshot["cascaded_filters"] = (
            [snapshot.pop(f"cascaded_filter_{stage}") for stage in range(3)]
            if include_cascaded
            else []
        )
        return snapshot

    def get_ff_runtime_snapshot(self, source_count: int = 7) -> dict[str, object]:
        """Read FF status/configuration matrices in one transport batch."""
        count = max(0, int(source_count))
        queries: list[tuple[str, bytes, Callable[[RciResponse], object]]] = [
            ("status", self.encoder.fgffs(), self.encoder.decode_fgffs),
            ("inputs", self.encoder.fgffi(), self.encoder.decode_fgffi),
            ("config", self.encoder.fgffc(), self.encoder.decode_fgffc),
        ]
        for source in range(count):
            queries.append(
                (
                    f"parameter_{source}",
                    self.encoder.fgffp(source),
                    self.encoder.decode_fgffp,
                )
            )
        for axis in range(6):
            queries.append(
                (
                    f"gain_{axis}",
                    self.encoder.fgffg(axis, 0),
                    self.encoder.decode_fgffg,
                )
            )
        queries.extend(
            [
                ("multipliers", self.encoder.fgsfm(), self.encoder.decode_fgsfm),
                ("output_limit", self.encoder.bgffl(), self.encoder.decode_bgffl),
                ("zrot", self.encoder.fgzrp(), self.encoder.decode_fgzrp),
                ("loop", self.encoder.bgsts(), self.encoder.decode_bgsts),
            ]
        )
        snapshot = self._query_snapshot(queries)
        snapshot["parameters"] = [
            snapshot.pop(f"parameter_{source}") for source in range(count)
        ]
        gains: list[float] = []
        for axis in range(6):
            gains.extend(snapshot.pop(f"gain_{axis}"))  # type: ignore[arg-type]
        snapshot["gains"] = gains
        return snapshot

    def get_pff_tuning_snapshot(
        self,
        keys: Sequence[tuple[int, int, int]],
        *,
        source_count: int = 4,
    ) -> dict[str, object]:
        """Read PFF filters and configuration in one transport batch."""
        addresses = [
            (int(axis), int(source), int(stage)) for axis, source, stage in keys
        ]
        count = max(0, int(source_count))
        queries: list[tuple[str, bytes, Callable[[RciResponse], object]]] = []
        for index, (axis, source, stage) in enumerate(addresses):
            queries.append(
                (
                    f"filter_{index}",
                    self.encoder.fgfsp(axis, source, stage),
                    lambda response, axis=axis, source=source, stage=stage: self.encoder.decode_fgfsp(
                        response, axis, source, stage
                    ),
                )
            )
        queries.append(("inputs", self.encoder.fgipf(), self.encoder.decode_fgipf))
        for source in range(count):
            queries.append(
                (
                    f"parameter_{source}",
                    self.encoder.fgppf(source),
                    self.encoder.decode_fgppf,
                )
            )
        queries.append(("config", self.encoder.fgcpf(), self.encoder.decode_fgcpf))
        queries.extend(
            [
                ("loop", self.encoder.bgsts(), self.encoder.decode_bgsts),
                ("axis_loop_status", self.encoder.bgsst(), self.encoder.decode_bgsst),
            ]
        )
        snapshot = self._query_snapshot(queries)
        snapshot["filters"] = [
            snapshot.pop(f"filter_{index}") for index in range(len(addresses))
        ]
        snapshot["parameters"] = [
            snapshot.pop(f"parameter_{source}") for source in range(count)
        ]
        return snapshot

    def get_pneumatic_page_snapshot(
        self,
        keys: Sequence[tuple[int, int]],
        *,
        include_ramp: bool,
    ) -> dict[str, object]:
        """Read the complete Pneumatic page in one transport batch."""
        addresses = [(int(axis), int(stage)) for axis, stage in keys]
        queries: list[tuple[str, bytes, Callable[[RciResponse], object]]] = []
        for index, (axis, stage) in enumerate(addresses):
            queries.append(
                (
                    f"filter_{index}",
                    self.encoder.pgpaf(axis, stage),
                    lambda response, axis=axis, stage=stage: self.encoder.decode_pgpaf(
                        response, axis, stage
                    ),
                )
            )
        for axis in range(3):
            queries.append(
                (
                    f"steering_{axis}",
                    self.encoder.pgpsm(axis),
                    self.encoder.decode_pgpsm,
                )
            )
        queries.extend(
            [
                ("valve_offsets", self.encoder.pgpvo(), self.encoder.decode_pgpvo),
                ("motor_offsets", self.encoder.cgmov(), self.encoder.decode_cgmov),
                ("dither_value", self.encoder.pgdit(), self.encoder.decode_pgdit),
                ("dither_frequency", self.encoder.pgdfr(), self.encoder.decode_pgdfr),
                ("dither_alpha", self.encoder.pgdca(), self.encoder.decode_pgdca),
                ("config", self.encoder.pgpcp(), self.encoder.decode_pgpcp),
            ]
        )
        if include_ramp:
            queries.append(("ramp", self.encoder.pgprp(), self.encoder.decode_pgprp))
        queries.extend(
            [
                ("axes_status", self.encoder.pgpas(), self.encoder.decode_pgpas),
                (
                    "heights_valves",
                    self.encoder.pgphv(),
                    self.encoder.decode_pgphv,
                ),
                ("status_timer", self.encoder.pgpst(), self.encoder.decode_pgpst),
                ("loop", self.encoder.bgsts(), self.encoder.decode_bgsts),
                ("setpoint_status", self.encoder.pgpss(), self.encoder.decode_pgpss),
                ("axis_loop_status", self.encoder.bgsst(), self.encoder.decode_bgsst),
            ]
        )
        snapshot = self._query_snapshot(queries)
        snapshot["filters"] = [
            snapshot.pop(f"filter_{index}") for index in range(len(addresses))
        ]
        snapshot["steering"] = [
            snapshot.pop(f"steering_{axis}") for axis in range(3)
        ]
        return snapshot

    # --- Raw escape ---

    def raw_command(self, mnemonic: str, *params: str | int | float) -> RciResponse:
        return self.transact(self.encoder.raw(mnemonic, *params))

    # --- VelAxes output limiter ---

    def get_vel_axes_output_limiter(self) -> list[float]:
        return self.encoder.decode_bgfbl(self.transact(self.encoder.bgfbl()))

    def set_vel_axes_output_limiter(self, values: list[float]) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.bsfbl(values)), "BSFBL")

    # --- FF Zrot parameters ---

    def get_ff_zrot_parameters(self) -> list[str]:
        return self.encoder.decode_fgzrp(self.transact(self.encoder.fgzrp()))

    def set_ff_zrot_parameters(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.fszrp(*params)), "FSZRP")

    # --- Excitation parameters ---

    def get_excitation_params(self) -> list[str]:
        return self.encoder.decode_dgesp(self.transact(self.encoder.dgesp()))

    def set_excitation_params(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dsesp(*params)), "DSESP")

    def get_excitation_offset(self) -> float:
        """Read the extended-excitation DC offset (DGEOV).

        Older firmware may not implement this optional command.  Callers in
        measurement-only applications should treat a command error as
        "offset unsupported" while keeping the rest of excitation usable.
        """
        response = self.raw_command("DGEOV")
        self.encoder.ensure_ok(response, "DGEOV")
        if not response.data_tokens:
            raise ValueError("empty DGEOV response")
        return float(response.data_tokens[0])

    def set_excitation_offset(self, value: float) -> None:
        """Write the extended-excitation DC offset (DSEOV)."""
        self._ensure_writable()
        self.encoder.ensure_ok(
            self.raw_command("DSEOV", float(value)), "DSEOV"
        )

    # --- Event signal ---

    def get_event_signal(self) -> list[str]:
        return self.encoder.decode_dgets(self.transact(self.encoder.dgets()))

    def set_event_signal(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dsets(*params)), "DSETS")

    # --- Monitor signal ---

    def get_monitor_signal(self, sig_num: int) -> list[str]:
        return self.encoder.decode_dgmos(self.transact(self.encoder.dgmos(sig_num)))

    def set_monitor_signal(self, sig_num: int, *monsig: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dsmos(sig_num, *monsig)), "DSMOS")

    # --- Actual time ---

    def get_actual_time(self) -> list[str]:
        return self.encoder.decode_dgati(self.transact(self.encoder.dgati()))

    def set_actual_time(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.dsati(*params)), "DSATI")

    # --- Floor FF adaptive algorithm ---

    def get_floor_ff_adaptive_algo(self) -> int:
        return self.encoder.decode_fgfat(self.transact(self.encoder.fgfat()))

    def set_floor_ff_adaptive_algo(self, algo: int) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.fsfat(algo)), "FSFAT")

    # --- Pneumatic ramp parameters ---

    def get_pneumatic_ramp_parameters(self) -> list[str]:
        return self.encoder.decode_pgprp(self.transact(self.encoder.pgprp()))

    def set_pneumatic_ramp_parameters(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.psprp(*params)), "PSPRP")

    # --- Use pneumatic axis setpoint for all ---

    def get_use_pneum_axis_setpoint_for_all(self) -> int:
        return self.encoder.decode_pgpss(self.transact(self.encoder.pgpss()))

    def set_use_pneum_axis_setpoint_for_all(self, use_all: int) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.pspss(use_all)), "PSPSS")

    # --- Cascaded position filter ---

    def get_cascaded_position_filter(self, stage: int) -> FilterStage:
        return self.encoder.decode_cgpcf(
            self.transact(self.encoder.cgpcf(stage)), stage
        )

    def set_cascaded_position_filter(self, stage: int, filter_type: int, *params: float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(
            self.transact(self.encoder.cspcf(stage, filter_type, *params)), "CSCPF"
        )

    # --- Cascaded position parameter ---

    def get_cascaded_position_parameter(self) -> list[str]:
        return self.encoder.decode_cgpcm(self.transact(self.encoder.cgpcm()))

    def set_cascaded_position_parameter(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.cspcm(*params)), "CSCPP")

    # --- Non-linear position parameter ---

    def get_non_linear_position_parameter(self) -> list[str]:
        return self.encoder.decode_cgpnp(self.transact(self.encoder.cgpnp()))

    def set_non_linear_position_parameter(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.cspnp(*params)), "CSSFP")

    # --- Firmware config info ---

    def get_firmware_config_info(self) -> list[str]:
        return self.encoder.decode_bggsc(self.transact(self.encoder.bggsc()))

    # --- Power supply parameters ---

    def get_power_supply_limit(self) -> list[str]:
        return self.encoder.decode_lgpsl(self.transact(self.encoder.lgpsl()))

    def set_power_supply_limit(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.lspsl(*params)), "LSPSL")

    def get_power_supply_parameters(self) -> list[str]:
        return self.encoder.decode_lgpsl(self.transact(self.encoder.lgpsl()))

    def set_power_supply_parameters(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.lspsl(*params)), "LSPSL")

    # --- ZMS stability ---

    def get_zms_stability_thresholds(self) -> list[float]:
        return self.encoder.decode_bgsvt(self.transact(self.encoder.bgsvt()))

    def set_zms_stability_thresholds(self, *params: str | int | float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.bssvt(*params)), "BSSVT")

    def get_zms_last_failed_event(self) -> tuple[int, float]:
        return self.encoder.decode_bglse(self.transact(self.encoder.bglse()))

    def get_zms_rms_values(self) -> list[float]:
        return self.encoder.decode_bgsrv(self.transact(self.encoder.bgsrv()))

    # --- Safety / earthquake -----------------------------------------

    def get_safety_and_earthquake_config(self) -> list[float]:
        """Read the eight values used by ``SafetyPage.UpdatePage``.

        The original SAMBA19xUI sends ``LGSEP`` here.  Keeping this at the
        session layer avoids UI patches having to parse raw RCI responses.
        """
        response = self.raw_command("LGSEP")
        self.encoder.ensure_ok(response, "LGSEP")
        return [float(value) for value in response.data_tokens]

    def set_safety_and_earthquake_config(self, values: list[float]) -> None:
        self._ensure_writable()
        if len(values) != 8:
            raise ValueError("safety/earthquake config requires 8 values")
        response = self.raw_command("LSSEP", *values)
        self.encoder.ensure_ok(response, "LSSEP")

    @staticmethod
    def _decode_fault_word(value: str) -> int:
        try:
            return int(value, 0)
        except ValueError:
            return int(value, 16)

    def _get_sensor_rms_status(self, mnemonic: str) -> tuple[list[float], int, int]:
        response = self.raw_command(mnemonic)
        self.encoder.ensure_ok(response, mnemonic)
        tokens = response.data_tokens
        if len(tokens) < 14:
            raise ProtocolError(f"{mnemonic} expected 12 RMS values and 2 fault words")
        values = [float(value) for value in tokens[:12]]
        return (
            values,
            self._decode_fault_word(tokens[12]),
            self._decode_fault_word(tokens[13]),
        )

    def get_sensor_safety_rms_values(self) -> list[float]:
        values, geo_fault, prox_fault = self._get_sensor_rms_status("LGSRV")
        self._last_safety_faults = (geo_fault, prox_fault)
        return values

    def get_sensor_earthquake_rms_values(self) -> list[float]:
        values, geo_fault, prox_fault = self._get_sensor_rms_status("LGERV")
        self._last_earthquake_faults = (geo_fault, prox_fault)
        return values

    def get_safety_geo_fault(self) -> int:
        if not hasattr(self, "_last_safety_faults"):
            self.get_sensor_safety_rms_values()
        return int(self._last_safety_faults[0])

    def get_safety_prox_fault(self) -> int:
        if not hasattr(self, "_last_safety_faults"):
            self.get_sensor_safety_rms_values()
        return int(self._last_safety_faults[1])

    def get_earthquake_geo_fault(self) -> int:
        if not hasattr(self, "_last_earthquake_faults"):
            self.get_sensor_earthquake_rms_values()
        return int(self._last_earthquake_faults[0])

    def get_zms_stability_status_and_rms_values(
        self,
    ) -> tuple[tuple[int, int], list[float]]:
        """Return ZMS status words and the available RMS values.

        Merit Safety 2 returns two status words followed by twelve values.
        Older firmware returns RMS values only, in which case both status
        words default to OK just as the original UI did before the first
        status event arrived.
        """
        raw = self.get_zms_rms_values()
        if len(raw) >= 14:
            return (int(raw[0]), int(raw[1])), raw[2:14]
        return (0, 0), raw

    def get_zms_stability_status(self) -> tuple[int, int]:
        status, _values = self.get_zms_stability_status_and_rms_values()
        return status

    # --- ZMS / Safety (reuse existing commands) ---

    def get_amplifier_disable_events(self) -> list[int]:
        return self.encoder.decode_dgade(self.transact(self.encoder.dgade()))

    def get_motor_overcurrent_cooling_constant(self) -> float:
        return self.encoder.decode_bgmcc(self.transact(self.encoder.bgmcc()))

    def set_motor_overcurrent_cooling_constant(self, value: float) -> None:
        self._ensure_writable()
        self.encoder.ensure_ok(self.transact(self.encoder.bsmcc(value)), "BSMCC")

    def _ensure_writable(self) -> None:
        if self.readonly:
            raise PermissionError(
                "session is readonly; create ControllerSession(readonly=False) or unlock writes"
            )


def _decode_opticon_float(field: bytes) -> float:
    """Decode one six-byte Opticon float field used by DGTBB."""
    if len(field) != 6:
        raise ProtocolError(f"DGTBB float field must be 6 bytes, got {len(field)}")
    chunks = [byte & 0x3F for byte in field[:5]]
    chunks.append(field[5] & 0x03)
    bits = 0
    for index, value in enumerate(chunks):
        bits |= int(value) << (6 * index)
    return float(struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0])


def open_mock(readonly: bool = True) -> ControllerSession:
    from python_samba.transport.mock import MockTransport

    return ControllerSession(MockTransport(), backend_name="mock", readonly=readonly)


def open_serial(
    port: str,
    baudrate: int = 57600,
    *,
    readonly: bool = True,
    timeout: float = 5.0,
) -> ControllerSession:
    from python_samba.transport.serial_port import SerialConfig, SerialTransport

    transport = SerialTransport(SerialConfig(port=port, baudrate=baudrate, timeout=timeout))
    return ControllerSession(
        transport,
        backend_name="serial",
        port=port,
        baudrate=baudrate,
        timeout=timeout,
        readonly=readonly,
    )


def open_comm_server(
    port: str,
    baudrate: int = 57600,
    *,
    server: str = "127.0.0.1:47619",
    token_file: str | None = None,
    token: str | None = None,
    auto_start: bool = True,
    client_name: str = "python_samba",
    readonly: bool = True,
    timeout: float = 5.0,
) -> ControllerSession:
    from python_samba.transport.comm_server import (
        CommServerConfig,
        CommServerTransport,
    )

    transport = CommServerTransport(
        CommServerConfig(
            port=port,
            baudrate=baudrate,
            endpoint=server,
            timeout=timeout,
            token_file=token_file,
            token=token,
            auto_start=auto_start,
            client_name=client_name,
        )
    )
    return ControllerSession(
        transport,
        backend_name="server",
        port=port,
        baudrate=baudrate,
        server_endpoint=server,
        timeout=timeout,
        readonly=readonly,
    )


# =====================================================================
# Pneumatic aliases (for pneumatic_page_patch compatibility)
# =====================================================================

def get_pneumatic_individual_loop_status(self) -> list[int]:
    """Expand the BGSST pneumatic word into its three axis states."""
    _position, pneumatic, _digital_in, _digital_out = (
        self.get_pos_pneum_digital_status()
    )
    return [int(bool(pneumatic & (1 << bit))) for bit in range(3)]


def toggle_pneumatic_individual_loop_status(self, axis: int, state: bool) -> None:
    """Set one BGSST/BSSST pneumatic individual-loop axis state."""
    if not 0 <= axis < 3:
        raise ValueError(f"pneumatic individual-loop axis out of range: {axis}")
    position, pneumatic, _digital_in, _digital_out = (
        self.get_pos_pneum_digital_status()
    )
    if state:
        pneumatic |= 1 << axis
    else:
        pneumatic &= ~(1 << axis)
    self.set_pos_pneum_individual_loop_status(position, pneumatic)


def get_pneumatic_dither_value(self) -> float:
    return self.get_dither_value()


def set_pneumatic_dither_value(self, value: float) -> None:
    self.set_dither_value(value)


def get_pneumatic_dither_compensation(self) -> float:
    return self.get_dither_alpha()


def set_pneumatic_dither_compensation_alpha(self, alpha: float) -> None:
    self.set_dither_alpha(alpha)


def get_pneumatic_config_parameters(self) -> list[str]:
    return self.get_pneumatic_config()


def _set_pneumatic_config_value(
    self, index: int, value: str | int | float
) -> None:
    # Preserve the controller's integer tokens.  CommandEncoder.pspcp performs
    # the final integral validation and guarantees a non-scientific wire form.
    values: list[str | int | float] = list(self.get_pneumatic_config())
    if len(values) != 3:
        raise ProtocolError(
            f"PGPCP expected exactly 3 values before partial write, got {values}"
        )
    values[index] = value
    # Protocol order from the original IIDETCMFD2 interface is:
    # SoftupHeight, Setpoint, ModeTolerance.
    self.set_pneumatic_config(*values[:3])


def set_pneumatic_config_setpoint(self, value: str | int | float) -> None:
    _set_pneumatic_config_value(self, 1, value)


def set_pneumatic_config_softup_height(self, value: str | int | float) -> None:
    _set_pneumatic_config_value(self, 0, value)


def set_pneumatic_position_tolerance(self, value: str | int | float) -> None:
    _set_pneumatic_config_value(self, 2, value)


def get_pneumatic_valve_up_offsets(self) -> list[float]:
    off = self.get_pneumatic_valve_offsets()
    half = len(off) // 2
    return off[:half]


def get_pneumatic_valve_down_offsets(self) -> list[float]:
    off = self.get_pneumatic_valve_offsets()
    half = len(off) // 2
    return off[half:]


def set_pneumatic_valve_up_offsets(self, values: list[float]) -> None:
    down = self.get_pneumatic_valve_down_offsets()
    self.set_pneumatic_valve_offsets(list(values) + list(down))


def set_pneumatic_valve_down_offsets(self, values: list[float]) -> None:
    up = self.get_pneumatic_valve_up_offsets()
    self.set_pneumatic_valve_offsets(list(up) + list(values))


def get_motor_and_iso_offset_values(self) -> list[float]:
    return self.get_motor_offsets()


def set_motor_and_iso_offset_values(self, values: list[float]) -> None:
    if len(values) == 3:
        current = self.get_motor_offsets()
        if len(current) < 3:
            raise ProtocolError("motor/isolator offset response is too short")
        values = current[:-3] + list(values)
    self.set_motor_offsets(values)


def get_pneumatic_input_steering_matrix(self, axis: int) -> list[float]:
    values = self.get_pneumatic_steering_matrix(axis)
    split = len(values) // 2
    return values[:split]


def set_pneumatic_input_steering_matrix(self, axis: int, values: list[float]) -> None:
    current = self.get_pneumatic_steering_matrix(axis)
    split = len(current) // 2
    inputs = current[:split]
    inputs[: min(split, len(values))] = list(values)[:split]
    output = current[split:]
    self.set_pneumatic_steering_matrix(axis, inputs + output)


def get_pneumatic_output_steering_matrix(self, axis: int) -> list[float]:
    values = self.get_pneumatic_steering_matrix(axis)
    split = len(values) // 2
    return values[split:]


def set_pneumatic_output_steering_matrix(self, axis: int, values: list[float]) -> None:
    current = self.get_pneumatic_steering_matrix(axis)
    split = len(current) // 2
    inputs = current[:split]
    outputs = current[split:]
    outputs[: min(len(outputs), len(values))] = list(values)[:len(outputs)]
    self.set_pneumatic_steering_matrix(axis, inputs + outputs)


def get_pneumatic_ramp_parameters(self) -> list[str]:
    return self.encoder.decode_pgprp(self.transact(self.encoder.pgprp()))


def set_pneumatic_ramp_parameters(self, *params: str | int | float) -> None:
    self._ensure_writable()
    self.encoder.ensure_ok(self.transact(self.encoder.psprp(*params)), "PSPRP")


def set_pneumatic_ramp_parameter(
    self, name: str, value: str | int | float
) -> None:
    order = {
        "rms_hysteresis_factor": 0,
        "setpoint_gradient": 1,
        "move_up_gradient": 2,
        "move_down_gradient": 3,
        "valve_offset_gradient": 4,
    }
    if name not in order:
        raise ValueError(f"unknown pneumatic ramp parameter: {name}")
    values: list[str | int | float] = list(self.get_pneumatic_ramp_parameters())
    if len(values) != 5:
        raise ProtocolError(
            f"PGPRP expected exactly 5 values before partial write, got {values}"
        )
    values[order[name]] = value
    self.set_pneumatic_ramp_parameters(*values)


def get_system_loop_status(self) -> dict[str, bool]:
    loop = self.get_loop_status()
    return {
        "overall": bool(loop.system & 0x00001),
        "velocity": bool(loop.individual & 0x01),
        "position": bool(loop.individual & 0x02),
        "pneumatic": bool(loop.system & 0x00040),
        "ff": bool(loop.system & 0x00004),
        "pff": bool(loop.system & 0x04000),
        "dither_compensation": bool(loop.system & 0x02000),
        "reference_metrology": bool(loop.system & 0x20000),
        "move_up_at_startup": bool(loop.system & 0x00008),
    }


def set_use_pneum_axis_setpoint_for_all_axes(self, use_all: int) -> None:
    self.set_pneumatic_setpoint_status(use_all)


def set_dither(self, value: float, freq: float, alpha: float) -> None:
    self.set_dither_value(value)
    self.set_dither_frequency(freq)
    self.set_dither_alpha(alpha)


def move_pneumatic_system_up(self) -> None:
    self.move_pneumatic(1)


def move_pneumatic_system_down(self) -> None:
    self.move_pneumatic(2)


def use_current_pressure_setpoints_as_up_offset(self) -> None:
    self.use_current_pressure_offsets(1)


def use_current_pressure_setpoints_as_down_offset(self) -> None:
    self.use_current_pressure_offsets(2)


# Bind aliases to ControllerSession
ControllerSession.get_pneumatic_individual_loop_status = get_pneumatic_individual_loop_status
ControllerSession.toggle_pneumatic_individual_loop_status = toggle_pneumatic_individual_loop_status
ControllerSession.get_pneumatic_dither_value = get_pneumatic_dither_value
ControllerSession.set_pneumatic_dither_value = set_pneumatic_dither_value
ControllerSession.get_pneumatic_dither_compensation = get_pneumatic_dither_compensation
ControllerSession.set_pneumatic_dither_compensation_alpha = set_pneumatic_dither_compensation_alpha
ControllerSession.get_pneumatic_config_parameters = get_pneumatic_config_parameters
ControllerSession.set_pneumatic_config_setpoint = set_pneumatic_config_setpoint
ControllerSession.set_pneumatic_config_softup_height = set_pneumatic_config_softup_height
ControllerSession.set_pneumatic_position_tolerance = set_pneumatic_position_tolerance
ControllerSession.get_pneumatic_valve_up_offsets = get_pneumatic_valve_up_offsets
ControllerSession.get_pneumatic_valve_down_offsets = get_pneumatic_valve_down_offsets
ControllerSession.set_pneumatic_valve_up_offsets = set_pneumatic_valve_up_offsets
ControllerSession.set_pneumatic_valve_down_offsets = set_pneumatic_valve_down_offsets
ControllerSession.get_motor_and_iso_offset_values = get_motor_and_iso_offset_values
ControllerSession.set_motor_and_iso_offset_values = set_motor_and_iso_offset_values
ControllerSession.get_pneumatic_input_steering_matrix = get_pneumatic_input_steering_matrix
ControllerSession.set_pneumatic_input_steering_matrix = set_pneumatic_input_steering_matrix
ControllerSession.get_pneumatic_output_steering_matrix = get_pneumatic_output_steering_matrix
ControllerSession.set_pneumatic_output_steering_matrix = set_pneumatic_output_steering_matrix
ControllerSession.get_pneumatic_ramp_parameters = get_pneumatic_ramp_parameters
ControllerSession.set_pneumatic_ramp_parameters = set_pneumatic_ramp_parameters
ControllerSession.set_pneumatic_ramp_parameter = set_pneumatic_ramp_parameter
ControllerSession.get_system_loop_status = get_system_loop_status
ControllerSession.set_use_pneum_axis_setpoint_for_all_axes = set_use_pneum_axis_setpoint_for_all_axes
ControllerSession.set_dither = set_dither
ControllerSession.move_pneumatic_system_up = move_pneumatic_system_up
ControllerSession.move_pneumatic_system_down = move_pneumatic_system_down
ControllerSession.use_current_pressure_setpoints_as_up_offset = use_current_pressure_setpoints_as_up_offset
ControllerSession.use_current_pressure_setpoints_as_down_offset = use_current_pressure_setpoints_as_down_offset
