"""In-memory mock controller for offline development (no hardware, no vendor DLL)."""

from __future__ import annotations

from dataclasses import dataclass, field

from python_samba.protocol.frame import format_crc, xor_checksum
from python_samba.transport.serial_port import Transport, TransportError


def _is_float(x: str) -> bool:
    try:
        float(x)
        return True
    except ValueError:
        return False


def _fmt(x: str | int | float) -> str:
    if isinstance(x, float):
        return f"{x:.6e}"
    return str(x)


def _accept(
    msg_id: str,
    crl: str,
    mnemonic: str,
    *data: str | int | float,
    status: int = 0,
) -> bytes:
    """Build a minimal accept response with real CRC."""
    payload_parts = [crl, f"{status:02X}", mnemonic, *[_fmt(x) for x in data]]
    data_field = " ".join(payload_parts)
    proto = "0"
    body_core = f"{msg_id}{proto}{data_field}"
    length = 2 + len(body_core)
    len_field = f"{length:02X}" if length < 0xF8 else "##"
    mid = f"{len_field}{body_core}"
    crc = format_crc(xor_checksum(mid))
    return f":{mid}{crc}\r".encode("ascii")


@dataclass
class MockState:
    version: tuple[int, int, int, int] = (3, 3, 9, 0)
    individual_loop: int = 0x7F
    system_status: int = 0x1800
    position_individual_loop: int = 0x3F
    pneumatic_individual_loop: int = 0x07
    digital_input_word: int = 0x0900
    digital_output_word: int = 0x0000
    sample_hz: float = 2000.0
    velocity_filters: dict[tuple[int, int], tuple[int, tuple[float, ...]]] = field(
        default_factory=dict
    )
    proximity_filters: dict[tuple[int, int], tuple[int, tuple[float, ...]]] = field(
        default_factory=dict
    )
    velocity_sensor_matrix: dict[int, list[float]] = field(default_factory=dict)
    velocity_motor_matrix: dict[int, list[float]] = field(default_factory=dict)
    position_sensor_matrix: dict[int, list[float]] = field(default_factory=dict)
    position_motor_matrix: dict[int, list[float]] = field(default_factory=dict)
    geophone: list[int] = field(default_factory=lambda: [10, -3, 5, 2, 0, -1, 4])
    proximity_offsets: list[float] = field(
        default_factory=lambda: [100.0, 101.0, 102.0, 10.0, 11.0, 12.0]
    )
    proximity_live: list[float] = field(
        default_factory=lambda: [110.0, 111.0, 112.0, 20.0, 21.0, 22.0]
    )
    cascaded_position_filters: dict[int, tuple[int, tuple[float, ...]]] = field(
        default_factory=dict
    )
    cascaded_position_params: list[str] = field(
        default_factory=lambda: ["0", "0.0", "0.0", "0.0", "0.0", "0.0"]
    )
    non_linear_position_params: list[str] = field(
        default_factory=lambda: ["0", "0", "0.0", "0.0"]
    )
    # Feedforward / diagnostics / NVRAM
    ff_status: list[str] = field(default_factory=lambda: ["1", "0", "0"])
    ff_filters: dict[tuple[int, int], tuple[int, tuple[float, ...]]] = field(default_factory=dict)
    ff_inputs: list[str] = field(default_factory=lambda: ["0", "1", "2", "3", "4", "5", "6"])
    noise_type: int = 0
    noise_gain: float = 0.0
    noise_inject: list[str] = field(default_factory=lambda: ["0", "0", "0"])
    switch_status: list[str] = field(default_factory=lambda: ["0", "0.0"])
    output_limit_pct: int = 100
    nvram_user: dict[str, object] = field(default_factory=dict)

    # System / pneumatic / logging extensions
    switch_signal: list[str] = field(default_factory=lambda: ["0", "0", "0"])
    switch_conditions: list[str] = field(
        default_factory=lambda: ["50", "0.5", "15.0", "0", "0"]
    )
    motor_oc_config: list[str] = field(
        default_factory=lambda: ["N", "5.0"] + [str(1000 + i) for i in range(12)]
    )
    motor_power: list[float] = field(default_factory=lambda: [float(i) for i in range(12)])
    motor_failsafe: list[str] = field(default_factory=lambda: ["0"] * 12)
    motor_cooling_constant: float = 0.001
    amplifier_disable_events: list[int] = field(default_factory=lambda: [0] * 10)
    nvram_checksum_saved: list[int] = field(
        default_factory=lambda: [0x1001, 0x2002, 0x3003]
    )
    nvram_checksum_actual: list[int] = field(
        default_factory=lambda: [0x1001, 0x2002, 0x3003]
    )
    perf_monitor: list[str] = field(
        default_factory=lambda: ["1", "0", "0", "1000", "0.1", "1.0"]
    )
    perf_status: list[str] = field(default_factory=lambda: ["0", "0.0"])
    system_load: float = 12.5
    startup_ramp: list[str] = field(default_factory=lambda: ["0", "2.0"])
    adc_seq: list[int] = field(default_factory=lambda: list(range(25)))
    dac_seq: list[int] = field(default_factory=lambda: list(range(20)))
    pneum_filters: dict[tuple[int, int], tuple[int, tuple[float, ...]]] = field(default_factory=dict)
    pneum_steering: dict[int, list[float]] = field(default_factory=dict)
    pneum_config: list[str] = field(default_factory=lambda: ["100", "500", "1"])
    pneum_valve_off: list[float] = field(default_factory=lambda: [0.0] * 16)
    pneum_axes_status: list[str] = field(
        default_factory=lambda: ["7", "7", "1", "2", "3", "4", "5", "6"]
    )
    pneum_heights: list[str] = field(
        default_factory=lambda: ["1.0", "1.1", "1.2", "1.3", "0.1", "0.2", "0.3", "0.4"]
    )
    pneum_status_timer: tuple[float, float] = (12.5, 0.25)
    dither_value: float = 10.0
    dither_freq: float = 35.0
    dither_alpha: float = 1e-3
    pneum_setpoint_all: int = 0
    event_trace_params: list[str] = field(default_factory=lambda: ["2", "1024", "4", "1", "0", "0"])
    event_trace_info: list[str] = field(default_factory=lambda: ["0", "1", "0", "0"])
    event_signal: list[str] = field(default_factory=lambda: ["0", "0", "0", "100.0", "10"])
    pff_config: list[str] = field(default_factory=lambda: ["5", "10"])
    pff_gains: list[float] = field(default_factory=lambda: [0.1, 0.2, 0.3, 0.4, 0.5])


    monitor_signals: dict[int, list[str]] = field(default_factory=dict)
    # logged_traces[trace_num] = list of samples; each sample is list[float] channels
    logged_traces: dict[int, list[list[float]]] = field(default_factory=dict)
    monitor_live: list[float] = field(default_factory=lambda: [0.1 * i for i in range(8)])
    event_times: dict[int, list[str]] = field(default_factory=dict)
    pff_filters: dict[tuple[int, int, int], tuple[int, tuple[float, ...]]] = field(
        default_factory=dict
    )
    pff_params: dict[int, list[str]] = field(default_factory=dict)
    pff_gains_map: dict[tuple[int, int], list[float]] = field(default_factory=dict)
    pff_inputs: list[int] = field(default_factory=lambda: [0, 1, 2, 3])


    ff_output_limit: int = 100
    fb_limiter: list[float] = field(default_factory=lambda: [1000.0] * 6)
    global_constants: list[str] = field(
        default_factory=lambda: [
            "6", "6", "3", "6", "7", "6", "7", "4", "9", "3", "2000",
            "PneumIO#8#8", "PRamp", "SALMO", "Cascaded", "ZMS", "PSUCL",
            "TmpSens", "PPILS", "NLP",
        ]
    )
    controller_type: list[str] = field(default_factory=lambda: ["2"])
    ff_gains_map: dict[str, list[float]] = field(default_factory=dict)
    ff_config: list[str] = field(default_factory=lambda: ["5", "N"])
    ff_parameters: dict[str, list[str]] = field(default_factory=dict)
    stage_ff_mult: list[float] = field(default_factory=lambda: [1.0, 1.0, 1.0, 1.0])
    ff_algo: int = 0
    ff_zrot: list[str] = field(default_factory=lambda: ["10000", "32000", "0", "0"])
    excitation: list[str] = field(default_factory=lambda: ["0", "0.1", "10", "0", "0"])
    noise_freq: float = 10.0
    noise_filt_usage: str = "F"
    noise_filters: dict[int, tuple[int, tuple[float, ...]]] = field(default_factory=dict)
    diag_outputs: list[str] = field(
        default_factory=lambda: ["0", "0", "0", "0", "0", "0"]
    )
    test_mode: list[str] = field(default_factory=lambda: ["0"])
    dig_trace_info: list[str] = field(
        default_factory=lambda: ["0", "0", "0", "0", "0", "0", "1", "64", "1"]
    )
    dig_trace_status: list[str] = field(default_factory=lambda: ["0"])
    dig_trace_buf: list[float] = field(default_factory=lambda: [float(i) for i in range(16)])
    pos_sensor_dev: list[str] = field(default_factory=lambda: ["0"] * 18)
    pos_motor_dev: list[str] = field(default_factory=lambda: ["0"] * 24)
    pos_sensor_dev_axes: dict[int, list[str]] = field(default_factory=dict)
    pos_motor_dev_axes: dict[int, list[str]] = field(default_factory=dict)
    motor_offsets: list[float] = field(default_factory=lambda: [0.0] * 11)
    linear_motor_offsets: list[float] = field(default_factory=lambda: [0.0] * 12)
    controller_cfg: list[str] = field(default_factory=lambda: ["247"])
    adc_set_num: int = 3
    temp_sensor_adc: list[int] = field(default_factory=lambda: list(range(12)))
    analysis_params: list[str] = field(default_factory=lambda: ["0", "0", "0"])
    analysis_input: list[str] = field(
        default_factory=lambda: ["0", "0", "0", "0"]
    )
    analysis_filt: dict[str, list[str]] = field(default_factory=dict)
    analysis_out: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    analysis_enum: list[str] = field(default_factory=lambda: ["0"])
    analysis_events: list[str] = field(default_factory=lambda: ["0"])
    analysis_spec: list[str] = field(default_factory=lambda: ["0", "100.0"])
    pneum_ramp: list[float] = field(
        default_factory=lambda: [0.0, 1.0, 1.0, 1.0, 1.0]
    )
    safety_config: list[float] = field(
        default_factory=lambda: [1000.0, 0.0, 50.0, 0.0, 1.0, 6000.0, 0.0, 2.0]
    )
    safety_rms: list[float] = field(default_factory=lambda: [0.0] * 12)
    earthquake_rms: list[float] = field(default_factory=lambda: [0.0] * 12)
    safety_faults: tuple[int, int] = (0, 0)
    earthquake_faults: tuple[int, int] = (0, 0)
    actual_time: list[int] = field(default_factory=lambda: [1, 12, 30, 0])
    power_supply_limit: list[str] = field(
        default_factory=lambda: ["1000", "1", "0", "0", "0", "0", "0", "0"]
    )
    zms_thresholds: list[float] = field(default_factory=lambda: [1.0] * 12)
    zms_last_event: tuple[int, float] = (0, 0.0)
    zms_status: tuple[int, int] = (0, 0)
    zms_rms: list[float] = field(default_factory=lambda: [0.0] * 12)
    polynom_status: list[int] = field(default_factory=lambda: [0, 0])
    polynom_configs: dict[int, list[str]] = field(default_factory=dict)
    polynom_inputs: list[float] = field(default_factory=lambda: [0.0] * 19)
    polynom_outputs: list[float] = field(default_factory=lambda: [0.0] * 19)
    polynom_ramp: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])

    def __post_init__(self) -> None:
        if not self.velocity_filters:
            self.velocity_filters[(0, 0)] = (3, (0.15, 0.0, 1.0, 0.0, 0.0))
        if not self.proximity_filters:
            self.proximity_filters[(0, 0)] = (1, (0.05, 0.0, 1.0, 0.0, 0.0))
        if not self.ff_filters:
            self.ff_filters[(0, 0)] = (3, (0.1, 0.0, 1.0, 0.0, 0.0))
        for axis in range(6):
            self.velocity_sensor_matrix.setdefault(axis, [0.0] * 7)
            self.velocity_motor_matrix.setdefault(axis, [0.0] * 12)
            self.velocity_sensor_matrix[axis][0] = 1.0
            self.velocity_motor_matrix[axis][0] = 1.0
            self.position_sensor_matrix.setdefault(axis, [0.0] * 6)
            self.position_motor_matrix.setdefault(axis, [0.0] * 8)
            self.position_sensor_matrix[axis][0] = 1.0
            self.position_motor_matrix[axis][0] = 1.0
            self.pos_sensor_dev_axes.setdefault(axis, list(self.pos_sensor_dev))
            self.pos_motor_dev_axes.setdefault(axis, list(self.pos_motor_dev))

        if not self.pneum_filters:
            self.pneum_filters[(0, 0)] = (1, (0.08, 0.0, 1.0, 0.0, 0.0))
        for axis in range(3):
            self.pneum_steering.setdefault(axis, [0.0] * 16)
            self.pneum_steering[axis][0] = 1.0

        if not self.monitor_signals:
            for i in range(4):
                self.monitor_signals[i] = ["0", str(i), "0"]
        if not self.logged_traces:
            # 8 samples x 2 channels synthetic sine-ish
            self.logged_traces[0] = [
                [float(s), float(s) * 0.5 + 0.1] for s in range(8)
            ]
            self.event_times[0] = ["1", "12", "30", "0"]
        if not self.pff_filters:
            self.pff_filters[(0, 0, 0)] = (3, (0.12, 0.0, 1.0, 0.0, 0.0))
        if not self.pff_params:
            self.pff_params[0] = ["7", "0.01"]
        if not self.pff_gains_map:
            self.pff_gains_map[(0, 0)] = [0.1, 0.2, 0.3, 0.4, 0.5]
        # keep event_trace_params consistent with mock buffer: UsedEvent MaxBuff MonSig ...
        self.event_trace_params = ["2", "8", "2", "1", "0", "0"]
        self.event_trace_info = ["0", "1", "1", "0"]  # stopped, max1, saved1, err0

        if not self.ff_gains_map:
            self.ff_gains_map["0"] = [0.1, 0.2, 0.3, 0.4, 0.5]
        if not self.ff_parameters:
            self.ff_parameters["0"] = ["1", "F", "0.01"]
        if not self.noise_filters:
            self.noise_filters[0] = (1, (0.1, 0.0, 1.0, 0.0, 0.0))
        if not self.analysis_filt:
            self.analysis_filt["0"] = ["1", "0.1", "0", "1", "0", "0"]
        if not self.polynom_configs:
            # active + input(type/main/sub) + output(type/main/sub) +
            # five coefficients + limiter
            for index in range(19):
                self.polynom_configs[index] = [
                    "0", "1", str(index), "0", "2", str(index), "0",
                    "0", "0", "0", "0", "0", "0",
                ]


class MockTransport(Transport):
    """Parses host frames and replies like a cooperative OPTICON."""

    def __init__(self, state: MockState | None = None) -> None:
        self.state = state or MockState()
        self._open = False
        self._rx = bytearray()
        self._tx_queue: list[bytes] = []

    def open(self) -> None:
        self._open = True
        self._rx.clear()
        self._tx_queue.clear()

    def close(self) -> None:
        self._open = False
        self._rx.clear()
        self._tx_queue.clear()

    @property
    def is_open(self) -> bool:
        return self._open

    def write(self, data: bytes) -> None:
        if not self._open:
            raise TransportError("mock not open")
        self._rx.extend(data)
        while b"\r" in self._rx:
            idx = self._rx.index(b"\r")
            frame = bytes(self._rx[: idx + 1])
            del self._rx[: idx + 1]
            self._tx_queue.append(self._handle(frame))

    def read_until(self, terminator: bytes = b"\r", timeout: float = 2.0) -> bytes:
        if not self._open:
            raise TransportError("mock not open")
        if not self._tx_queue:
            raise TransportError("mock has no response queued (send a command first)")
        return self._tx_queue.pop(0)

    def _handle(self, frame: bytes) -> bytes:
        text = frame.decode("ascii", errors="replace").strip("\r\n")
        if not text.startswith(":"):
            return _accept("?", "00", "?????", status=3)
        body = text[1:]
        if len(body) < 5:
            return _accept("?", "00", "?????", status=2)
        mid, crc = body[:-2], body[-2:]
        if crc != "##":
            try:
                if xor_checksum(mid) != int(crc, 16):
                    pass
            except ValueError:
                pass
        msg_id = mid[2]
        data = mid[3:].strip()
        parts = data.split()
        if len(parts) < 2:
            return _accept(msg_id, "00", "?????", status=2)
        crl, mnemonic, *params = parts
        return self._dispatch(msg_id, crl, mnemonic, params)

    def _dispatch(self, msg_id: str, crl: str, mnemonic: str, params: list[str]) -> bytes:
        st = self.state
        m = mnemonic.upper()
        if m == "BGVIS":
            maj, minor, patch, lib = st.version
            return _accept(msg_id, crl, m, maj, minor, patch, lib)
        if m == "BGSTS":
            return _accept(msg_id, crl, m, f"{st.individual_loop:X}", f"{st.system_status:X}")
        if m == "BSSTS" and len(params) >= 2:
            st.individual_loop = int(params[0], 16)
            st.system_status = int(params[1], 16)
            return _accept(msg_id, crl, m)
        if m == "BGSST":
            return _accept(
                msg_id,
                crl,
                m,
                f"{st.position_individual_loop:X}",
                f"{st.pneumatic_individual_loop:X}",
                f"{st.digital_input_word:X}",
                f"{st.digital_output_word:X}",
            )
        if m == "BSSST" and len(params) >= 2:
            st.position_individual_loop = int(params[0], 16)
            st.pneumatic_individual_loop = int(params[1], 16)
            return _accept(msg_id, crl, m)
        if m == "NGSFR":
            return _accept(msg_id, crl, m, st.sample_hz)
        if m == "VGVFS" and len(params) >= 2:
            axis, stage = int(params[0]), int(params[1])
            ftype, fparams = st.velocity_filters.get((axis, stage), (0, (0.0,) * 5))
            return _accept(msg_id, crl, m, ftype, *fparams)
        if m == "VSVFS" and len(params) >= 8:
            axis, stage, ftype = int(params[0]), int(params[1]), int(params[2])
            fparams = tuple(float(x) for x in params[3:8])
            st.velocity_filters[(axis, stage)] = (ftype, fparams)
            return _accept(msg_id, crl, m)
        if m == "VGSMV" and params:
            axis = int(params[0])
            return _accept(msg_id, crl, m, *st.velocity_sensor_matrix.get(axis, [0.0] * 7))
        if m == "VSSMV" and len(params) >= 8:
            axis = int(params[0])
            st.velocity_sensor_matrix[axis] = [float(x) for x in params[1:8]]
            return _accept(msg_id, crl, m)
        if m == "VGMMV" and params:
            axis = int(params[0])
            return _accept(msg_id, crl, m, *st.velocity_motor_matrix.get(axis, [0.0] * 12))
        if m == "VSMMV" and len(params) >= 13:
            axis = int(params[0])
            st.velocity_motor_matrix[axis] = [float(x) for x in params[1:13]]
            return _accept(msg_id, crl, m)
        if m == "VGGIV":
            return _accept(msg_id, crl, m, *st.geophone)
        if m == "CGSMV" and params:
            axis = int(params[0])
            return _accept(msg_id, crl, m, *st.position_sensor_matrix.get(axis, [0.0] * 6))
        if m == "CSSMV" and len(params) >= 7:
            axis = int(params[0])
            st.position_sensor_matrix[axis] = [float(x) for x in params[1:7]]
            return _accept(msg_id, crl, m)
        if m == "CGMMV" and params:
            axis = int(params[0])
            return _accept(msg_id, crl, m, *st.position_motor_matrix.get(axis, [0.0] * 8))
        if m == "CSMMV" and len(params) >= 9:
            axis = int(params[0])
            st.position_motor_matrix[axis] = [float(x) for x in params[1:9]]
            return _accept(msg_id, crl, m)
        if m == "CGPOV":
            return _accept(msg_id, crl, m, *st.proximity_offsets)
        if m == "CGPOX":
            values = list(st.proximity_offsets[:8])
            values.extend([0.0] * (8 - len(values)))
            return _accept(msg_id, crl, m, *values)
        if m == "CSPOV" and len(params) >= 6:
            st.proximity_offsets = [float(x) for x in params[:6]]
            return _accept(msg_id, crl, m)
        if m == "CSPOX" and len(params) >= 8:
            st.proximity_offsets = [float(x) for x in params[:8]]
            return _accept(msg_id, crl, m)
        if m == "CAUCO":
            st.proximity_offsets = list(st.proximity_live)
            return _accept(msg_id, crl, m)
        if m == "CAUCX":
            values = list(st.proximity_live[:8])
            values.extend([0.0] * (8 - len(values)))
            st.proximity_offsets = values
            return _accept(msg_id, crl, m)
        if m == "PGGIX":
            values = list(st.proximity_live[:8])
            values.extend([0.0] * (8 - len(values)))
            return _accept(msg_id, crl, m, *values)
        if m == "CGPFS" and len(params) >= 2:
            axis, stage = int(params[0]), int(params[1])
            ftype, fparams = st.proximity_filters.get((axis, stage), (0, (0.0,) * 5))
            return _accept(msg_id, crl, m, ftype, *fparams)
        if m == "CSPFS" and len(params) >= 8:
            axis, stage, ftype = int(params[0]), int(params[1]), int(params[2])
            fparams = tuple(float(x) for x in params[3:8])
            st.proximity_filters[(axis, stage)] = (ftype, fparams)
            return _accept(msg_id, crl, m)
        # Feedforward
        if m == "FGFFS":
            return _accept(msg_id, crl, m, *st.ff_status)
        if m == "FSFFS" and params:
            st.ff_status = list(params)
            return _accept(msg_id, crl, m)
        if m == "FGPFS" and len(params) >= 3:
            axis, source, stage = map(int, params[:3])
            logical_index = axis if stage >= 6 else source
            ftype, fparams = st.ff_filters.get(
                (logical_index, stage), (0, (0.0,) * 5)
            )
            return _accept(msg_id, crl, m, ftype, *fparams)
        if m == "FSPFS" and len(params) >= 9:
            axis, source, stage, ftype = map(int, params[:4])
            logical_index = axis if stage >= 6 else source
            fparams = tuple(float(x) for x in params[4:9])
            st.ff_filters[(logical_index, stage)] = (ftype, fparams)
            return _accept(msg_id, crl, m)
        if m == "FGFFI":
            return _accept(msg_id, crl, m, *st.ff_inputs)
        # Diagnostics
        if m == "DGNTY":
            return _accept(msg_id, crl, m, st.noise_type)
        if m == "DSNTY" and params:
            st.noise_type = int(params[0])
            return _accept(msg_id, crl, m)
        if m == "DGNSG":
            return _accept(msg_id, crl, m, st.noise_gain)
        if m == "DSNSG" and params:
            st.noise_gain = float(params[0])
            return _accept(msg_id, crl, m)
        if m == "DGNIP":
            return _accept(msg_id, crl, m, *st.noise_inject)
        if m == "DSNIP" and params:
            st.noise_inject = list(params)
            return _accept(msg_id, crl, m)
        if m == "DGCSS":
            return _accept(msg_id, crl, m, *st.switch_status)
        # NVRAM
        if m == "NASUP":
            st.nvram_user = {
                "version": st.version,
                "individual_loop": st.individual_loop,
                "system_status": st.system_status,
                "output_limit_pct": st.output_limit_pct,
                "noise_type": st.noise_type,
                "noise_gain": st.noise_gain,
                "dig_trace_info": list(st.dig_trace_info),
                "dither_value": st.dither_value,
                "dither_frequency": st.dither_freq,
                "dither_alpha": st.dither_alpha,
            }
            return _accept(msg_id, crl, m)
        if m == "NARUP":
            if st.nvram_user:
                st.individual_loop = int(st.nvram_user.get("individual_loop", st.individual_loop))
                st.system_status = int(st.nvram_user.get("system_status", st.system_status))
                st.output_limit_pct = int(st.nvram_user.get("output_limit_pct", st.output_limit_pct))
                st.noise_type = int(st.nvram_user.get("noise_type", st.noise_type))
                st.noise_gain = float(st.nvram_user.get("noise_gain", st.noise_gain))
                st.dig_trace_info = list(
                    st.nvram_user.get("dig_trace_info", st.dig_trace_info)
                )
                st.dither_value = float(
                    st.nvram_user.get("dither_value", st.dither_value)
                )
                st.dither_freq = float(
                    st.nvram_user.get("dither_frequency", st.dither_freq)
                )
                st.dither_alpha = float(
                    st.nvram_user.get("dither_alpha", st.dither_alpha)
                )
            return _accept(msg_id, crl, m)
        if m == "NACLR":
            st.nvram_user = {}
            return _accept(msg_id, crl, m)
        if m == "BCNCS":
            status = 0
            for index, (saved, actual) in enumerate(
                zip(st.nvram_checksum_saved, st.nvram_checksum_actual)
            ):
                if saved != actual:
                    status |= 1 << index
            return _accept(
                msg_id,
                crl,
                m,
                status,
                st.nvram_checksum_saved[0], st.nvram_checksum_actual[0],
                st.nvram_checksum_saved[1], st.nvram_checksum_actual[1],
                st.nvram_checksum_saved[2], st.nvram_checksum_actual[2],
            )
        if m == "BBNCS":
            return _accept(msg_id, crl, m, *st.nvram_checksum_actual)
        # Basic extras
        if m == "BGOPL":
            return _accept(msg_id, crl, m, st.output_limit_pct)
        if m == "BSOPL" and params:
            st.output_limit_pct = int(float(params[0]))
            return _accept(msg_id, crl, m)

        # Switch
        if m == "BGSWS":
            return _accept(msg_id, crl, m, *st.switch_signal)
        if m == "BSSWS" and params:
            st.switch_signal = list(params)
            return _accept(msg_id, crl, m)
        if m == "BGOCD":
            return _accept(msg_id, crl, m, *st.switch_conditions)
        if m == "BSOCD" and params:
            running = st.switch_conditions[4] if len(st.switch_conditions) > 4 else "0"
            st.switch_conditions = list(params[:4]) + [running]
            return _accept(msg_id, crl, m)
        # Motor protection
        if m == "BGOCV":
            return _accept(msg_id, crl, m, *st.motor_oc_config)
        if m == "BSOCV" and params:
            st.motor_oc_config = list(params)
            return _accept(msg_id, crl, m)
        if m == "BGMPV":
            return _accept(msg_id, crl, m, *st.motor_power)
        if m == "BGMPS":
            return _accept(msg_id, crl, m, *st.motor_failsafe)
        if m == "BGMCC":
            return _accept(msg_id, crl, m, st.motor_cooling_constant)
        if m == "BSMCC" and params:
            st.motor_cooling_constant = float(params[0])
            return _accept(msg_id, crl, m)
        if m == "DGADE":
            return _accept(
                msg_id,
                crl,
                m,
                *[f"{value:X}" for value in st.amplifier_disable_events],
            )
        # Performance
        if m == "DGPMV":
            return _accept(msg_id, crl, m, *st.perf_monitor)
        if m == "DSPMV" and params:
            st.perf_monitor = list(params)
            return _accept(msg_id, crl, m)
        if m == "DGPMS":
            return _accept(msg_id, crl, m, *st.perf_status)
        if m == "DGSLO":
            return _accept(msg_id, crl, m, st.system_load)
        # Ramp
        if m == "BGSUT":
            return _accept(msg_id, crl, m, *st.startup_ramp)
        if m == "BSSUT" and params:
            st.startup_ramp = list(params)
            return _accept(msg_id, crl, m)
        # DAC/ADC
        if m == "BGADS":
            return _accept(msg_id, crl, m, *st.adc_seq)
        if m == "BSADS" and params:
            st.adc_seq = [int(x) for x in params]
            return _accept(msg_id, crl, m)
        if m == "BGDAS":
            return _accept(msg_id, crl, m, *st.dac_seq)
        if m == "BSDAS" and params:
            st.dac_seq = [int(x) for x in params]
            return _accept(msg_id, crl, m)
        # Pneumatic
        if m == "PGPAF" and len(params) >= 2:
            axis, stage = int(params[0]), int(params[1])
            ftype, fparams = st.pneum_filters.get((axis, stage), (0, (0.0,) * 5))
            return _accept(msg_id, crl, m, ftype, *fparams)
        if m == "PSPAF" and len(params) >= 8:
            axis, stage, ftype = int(params[0]), int(params[1]), int(params[2])
            fparams = tuple(float(x) for x in params[3:8])
            st.pneum_filters[(axis, stage)] = (ftype, fparams)
            return _accept(msg_id, crl, m)
        if m == "PGPSM" and params:
            axis = int(params[0])
            values = st.pneum_steering.get(axis, [0.0] * 16)
            return _accept(msg_id, crl, m, *values)
        if m == "PSPSM" and len(params) >= 2:
            axis = int(params[0])
            st.pneum_steering[axis] = [float(x) for x in params[1:]]
            return _accept(msg_id, crl, m)
        if m == "PGPCP":
            return _accept(msg_id, crl, m, *st.pneum_config)
        if m == "PSPCP" and len(params) == 3:
            st.pneum_config = [str(int(value)) for value in params]
            return _accept(msg_id, crl, m)
        if m == "PGPVO":
            return _accept(msg_id, crl, m, *st.pneum_valve_off)
        if m == "PSPVO" and params:
            st.pneum_valve_off = [float(x) for x in params]
            return _accept(msg_id, crl, m)
        if m == "PGPAS":
            return _accept(msg_id, crl, m, *st.pneum_axes_status)
        if m == "PGPHV":
            return _accept(msg_id, crl, m, *st.pneum_heights)
        if m == "PGPST":
            return _accept(msg_id, crl, m, *st.pneum_status_timer)
        if m == "PGGIV":
            return _accept(msg_id, crl, m, *st.proximity_live[:6])
        if m == "PGDIT":
            return _accept(msg_id, crl, m, st.dither_value)
        if m == "PSDIT" and len(params) >= 2:
            st.dither_value = float(params[1])
            return _accept(msg_id, crl, m)
        if m == "PGDFR":
            return _accept(msg_id, crl, m, st.dither_freq)
        if m == "PSDFR" and len(params) >= 2:
            # Match the real protocol's integer-valued dither frequency so a
            # regression back to scientific-notation floats is caught by CI.
            st.dither_freq = float(int(params[1]))
            return _accept(msg_id, crl, m)
        if m == "PGDCA":
            return _accept(msg_id, crl, m, st.dither_alpha)
        if m == "PSDCA" and params:
            st.dither_alpha = float(params[0])
            return _accept(msg_id, crl, m)
        if m == "PGPSS":
            return _accept(msg_id, crl, m, st.pneum_setpoint_all)
        if m == "PSPSS" and params:
            st.pneum_setpoint_all = int(params[0])
            return _accept(msg_id, crl, m)
        if m == "PAUCO" and params:
            condition = int(params[0])
            split = len(st.pneum_valve_off) // 2
            valve_outputs = [float(value) for value in st.pneum_heights[-4:]]
            if condition in (0, 1):
                count = split
                st.pneum_valve_off[:split] = [
                    valve_outputs[index % len(valve_outputs)]
                    for index in range(count)
                ]
            elif condition == 2:
                count = len(st.pneum_valve_off) - split
                st.pneum_valve_off[split:] = [
                    valve_outputs[index % len(valve_outputs)]
                    for index in range(count)
                ]
            else:
                return _accept(msg_id, crl, m, status=0x08)
            return _accept(msg_id, crl, m)
        if m == "PAMOV":
            return _accept(msg_id, crl, m)
        # Logging
        if m == "DGETP":
            return _accept(msg_id, crl, m, *st.event_trace_params)
        if m == "DSETP" and params:
            st.event_trace_params = list(params)
            return _accept(msg_id, crl, m)
        if m == "DGETI":
            return _accept(msg_id, crl, m, *st.event_trace_info)
        if m == "DGETS":
            return _accept(msg_id, crl, m, *st.event_signal)
        if m == "DSETS" and params:
            st.event_signal = list(params)
            return _accept(msg_id, crl, m)
        # PFF
        if m == "FGCPF":
            return _accept(msg_id, crl, m, *st.pff_config)
        if m == "FSCPF" and params:
            st.pff_config = list(params)
            return _accept(msg_id, crl, m)
        # FGGPF/FSGPF handled below (axis+source form and legacy gains-only form)

        # Monitor signals
        if m == "DGMOS" and params:
            sn = int(params[0])
            return _accept(msg_id, crl, m, *st.monitor_signals.get(sn, ["0", "0", "0"]))
        if m == "DSMOS" and len(params) >= 2:
            sn = int(params[0])
            st.monitor_signals[sn] = list(params[1:])
            return _accept(msg_id, crl, m)
        if m == "DGLDV" and len(params) >= 2:
            tn, sn = int(params[0]), int(params[1])
            trace = st.logged_traces.get(tn, [])
            if sn < 0 or sn >= len(trace):
                return _accept(msg_id, crl, m, status=0x15)
            return _accept(msg_id, crl, m, *trace[sn])
        if m == "DGLDA" and params:
            tn = int(params[0])
            trace = st.logged_traces.get(tn, [])
            flat = [v for row in trace for v in row]
            if not flat:
                return _accept(msg_id, crl, m, status=0x15)
            return _accept(msg_id, crl, m, *flat)
        if m == "DGMSV" and len(params) >= 2:
            i1, i2 = int(params[0]), int(params[1])
            vals = st.monitor_live[i1 : i2 + 1]
            return _accept(msg_id, crl, m, *vals)
        if m == "DGEVT" and params:
            tn = int(params[0])
            return _accept(msg_id, crl, m, *st.event_times.get(tn, ["0", "0", "0", "0"]))
        # When starting logging, synthesize a fresh mini-trace if empty
        if m == "DSSET" and params:
            st.event_trace_info[0] = str(int(params[0]))
            if int(params[0]) == 1:
                # starting invalidates previous (doc); keep a demo buffer after stop cycle
                st.logged_traces = {}
                st.event_trace_info[2] = "0"
            else:
                # stop -> save one demo trace
                n = 8
                try:
                    n = int(st.event_trace_params[1])
                except Exception:
                    pass
                ch = 2
                try:
                    ch = int(st.event_trace_params[2])
                except Exception:
                    pass
                st.logged_traces[0] = [
                    [float(s + c * 0.1) for c in range(ch)] for s in range(max(1, n))
                ]
                st.event_times[0] = ["2", "8", "15", "30"]
                st.event_trace_info[2] = "1"
            return _accept(msg_id, crl, m)
        # PFF deep
        if m == "FGFSP" and len(params) >= 3:
            axis, source, stage = int(params[0]), int(params[1]), int(params[2])
            ftype, fparams = st.pff_filters.get((axis, source, stage), (0, (0.0,) * 5))
            return _accept(msg_id, crl, m, ftype, *fparams)
        if m == "FSFSP" and len(params) >= 9:
            axis, source, stage = int(params[0]), int(params[1]), int(params[2])
            ftype = int(params[3])
            fparams = tuple(float(x) for x in params[4:9])
            st.pff_filters[(axis, source, stage)] = (ftype, fparams)
            return _accept(msg_id, crl, m)
        if m == "FGPPF" and params:
            source = int(params[0])
            return _accept(msg_id, crl, m, *st.pff_params.get(source, ["0", "0.0"]))
        if m == "FSPPF" and len(params) >= 3:
            source = int(params[0])
            st.pff_params[source] = [str(params[1]), str(params[2])]
            return _accept(msg_id, crl, m)
        if m == "FARPF" and len(params) >= 2:
            axis, source = int(params[0]), int(params[1])
            n = 5
            try:
                n = int(float(st.pff_config[0]))
            except Exception:
                pass
            st.pff_gains_map[(axis, source)] = [0.0] * max(1, n)
            return _accept(msg_id, crl, m)
        if m == "FGGPF" and len(params) >= 2:
            axis, source = int(params[0]), int(params[1])
            gains = st.pff_gains_map.get((axis, source), list(st.pff_gains))
            return _accept(msg_id, crl, m, *gains)
        if m == "FGGPF" and len(params) < 2:
            return _accept(msg_id, crl, m, *st.pff_gains)
        if m == "FSGPF" and len(params) >= 3:
            try:
                axis, source = int(params[0]), int(params[1])
                gains = [float(x) for x in params[2:]]
                st.pff_gains_map[(axis, source)] = gains
                st.pff_gains = gains
                return _accept(msg_id, crl, m)
            except ValueError:
                st.pff_gains = [float(x) for x in params if _is_float(x)] or st.pff_gains
                return _accept(msg_id, crl, m)
        if m == "FGIPF":
            return _accept(msg_id, crl, m, *st.pff_inputs)
        if m == "FSIPF" and params:
            st.pff_inputs = [int(x) for x in params]
            return _accept(msg_id, crl, m)


        if m == "BGFFL":
            return _accept(msg_id, crl, m, st.ff_output_limit)
        if m == "BSFFL" and params:
            st.ff_output_limit = int(float(params[0]))
            return _accept(msg_id, crl, m)
        if m == "BGFBL":
            return _accept(msg_id, crl, m, *st.fb_limiter)
        if m == "BSFBL" and params:
            st.fb_limiter = [float(x) for x in params]
            return _accept(msg_id, crl, m)
        if m == "BGGSC":
            return _accept(msg_id, crl, m, *st.global_constants)
        if m == "BGCOT":
            return _accept(msg_id, crl, m, *st.controller_type)
        if m == "BSCOT" and params:
            st.controller_type = list(params)
            return _accept(msg_id, crl, m)
        if m == "FGFFG" and len(params) >= 2:
            axis, source = int(params[0]), int(params[1])
            key = f"{axis}:{source}"
            fallback = st.ff_gains_map.get(str(source), [0.0] * 5)
            return _accept(msg_id, crl, m, *st.ff_gains_map.get(key, fallback))
        if m == "FSFFG" and len(params) >= 3:
            axis, source = int(params[0]), int(params[1])
            values = [float(x) for x in params[2:7]]
            values.extend([0.0] * (5 - len(values)))
            st.ff_gains_map[f"{axis}:{source}"] = values
            return _accept(msg_id, crl, m)
        if m == "FARFF" and len(params) >= 2:
            axis, source = int(params[0]), int(params[1])
            key = f"{axis}:{source}"
            st.ff_gains_map[key] = [0.0] * 5
            return _accept(msg_id, crl, m)
        if m == "FGFFC":
            return _accept(msg_id, crl, m, *st.ff_config)
        if m == "FSFFC" and params:
            st.ff_config = list(params)
            return _accept(msg_id, crl, m)
        if m == "FGFFP":
            key = str(params[0]) if params else "0"
            return _accept(
                msg_id,
                crl,
                m,
                *st.ff_parameters.get(key, ["0", "F", "0"]),
            )
        if m == "FSFFP" and params:
            key = str(params[0])
            st.ff_parameters[key] = list(params[1:]) if len(params) > 1 else list(params)
            return _accept(msg_id, crl, m)
        if m == "FGSFM":
            return _accept(msg_id, crl, m, *st.stage_ff_mult)
        if m == "FSSFM" and params:
            st.stage_ff_mult = [float(x) for x in params]
            return _accept(msg_id, crl, m)
        if m == "FGFAT":
            return _accept(msg_id, crl, m, st.ff_algo)
        if m == "FSFAT" and params:
            st.ff_algo = int(params[0])
            return _accept(msg_id, crl, m)
        if m == "FGZRP":
            return _accept(msg_id, crl, m, *st.ff_zrot)
        if m == "FSZRP" and params:
            st.ff_zrot = list(params)
            return _accept(msg_id, crl, m)
        if m == "FSFFI" and params:
            st.ff_inputs = list(params)
            return _accept(msg_id, crl, m)
        if m == "DGESP":
            return _accept(msg_id, crl, m, *st.excitation)
        if m == "DSESP" and params:
            st.excitation = list(params)
            # mirror type/gain into legacy fields when present
            try:
                st.noise_type = int(float(params[0]))
            except Exception:
                pass
            if len(params) > 1:
                try:
                    st.noise_gain = float(params[1])
                except Exception:
                    pass
            return _accept(msg_id, crl, m)
        if m == "DGNSF":
            return _accept(msg_id, crl, m, st.noise_freq)
        if m == "DSNSF" and params:
            st.noise_freq = float(params[0])
            return _accept(msg_id, crl, m)
        if m == "DGNFU":
            return _accept(msg_id, crl, m, st.noise_filt_usage)
        if m == "DSNFU" and params:
            st.noise_filt_usage = str(params[0])
            return _accept(msg_id, crl, m)
        if m == "DGNFS" and params:
            stage = int(params[0])
            ftype, fparams = st.noise_filters.get(stage, (0, (0.0,) * 5))
            return _accept(msg_id, crl, m, ftype, *fparams)
        if m == "DSNFS" and len(params) >= 7:
            stage, ftype = int(params[0]), int(params[1])
            fparams = tuple(float(x) for x in params[2:7])
            st.noise_filters[stage] = (ftype, fparams)
            return _accept(msg_id, crl, m)
        if m == "DGDOS":
            return _accept(msg_id, crl, m, *st.diag_outputs)
        if m == "DSDOS" and params:
            st.diag_outputs = list(params)
            return _accept(msg_id, crl, m)
        if m == "DGTMO":
            return _accept(msg_id, crl, m, *st.test_mode)
        if m == "DSTMO" and params:
            st.test_mode = list(params)
            return _accept(msg_id, crl, m)
        if m == "DGTIV":
            return _accept(msg_id, crl, m, *st.dig_trace_info)
        if m == "DSTIV" and params:
            st.dig_trace_info = list(params)
            return _accept(msg_id, crl, m)
        if m == "DASTA":
            # The in-memory trace completes immediately.  Real hardware is
            # polled until DGTAS transitions to zero.
            st.dig_trace_status = ["0"]
            return _accept(msg_id, crl, m, "0")
        if m == "DGTAS":
            return _accept(msg_id, crl, m, *st.dig_trace_status)
        if m == "DGTBV" and params:
            st.dig_trace_status = ["0"]
            sample_count = min(8, len(st.dig_trace_buf) // 2)
            return _accept(
                msg_id, crl, m, sample_count,
                *st.dig_trace_buf[:2 * sample_count],
            )
        if m == "CGPSD":
            axis = int(params[0]) if params else 0
            return _accept(msg_id, crl, m, *st.pos_sensor_dev_axes.get(axis, st.pos_sensor_dev))
        if m == "CSPSD" and params:
            if len(params) >= 7:
                axis, values = int(params[0]), list(params[1:])
            else:
                axis, values = 0, list(params)
            st.pos_sensor_dev_axes[axis] = values
            if axis == 0:
                st.pos_sensor_dev = values
            return _accept(msg_id, crl, m)
        if m == "CGPMD":
            axis = int(params[0]) if params else 0
            return _accept(msg_id, crl, m, *st.pos_motor_dev_axes.get(axis, st.pos_motor_dev))
        if m == "CSPMD" and params:
            if len(params) >= 9:
                axis, values = int(params[0]), list(params[1:])
            else:
                axis, values = 0, list(params)
            st.pos_motor_dev_axes[axis] = values
            if axis == 0:
                st.pos_motor_dev = values
            return _accept(msg_id, crl, m)
        if m == "CGMOV":
            return _accept(msg_id, crl, m, *st.motor_offsets)
        if m == "CSMOV" and params:
            if len(params) != 11:
                return _accept(msg_id, crl, m, status=2)
            st.motor_offsets = [float(x) for x in params]
            return _accept(msg_id, crl, m)
        if m == "LGLMO":
            return _accept(msg_id, crl, m, *st.linear_motor_offsets)
        if m == "LSLMO" and params:
            st.linear_motor_offsets = [float(x) for x in params[:12]]
            return _accept(msg_id, crl, m)
        if m == "NSSFR" and len(params) >= 2:
            st.sample_hz = float(params[0])
            return _accept(msg_id, crl, m)
        if m == "NGEXL":
            return _accept(msg_id, crl, m, *st.controller_cfg)
        if m == "NSEXL" and params:
            st.controller_cfg = list(params)
            return _accept(msg_id, crl, m)
        if m == "NGASN":
            return _accept(msg_id, crl, m, st.adc_set_num)
        if m == "NSASN" and params:
            st.adc_set_num = int(params[0])
            return _accept(msg_id, crl, m)
        if m == "LGANP":
            return _accept(msg_id, crl, m, *st.analysis_params)
        if m == "LSANP" and params:
            st.analysis_params = list(params)
            return _accept(msg_id, crl, m)
        if m == "LGAIS":
            return _accept(msg_id, crl, m, *st.analysis_input)
        if m == "LSAIS" and params:
            st.analysis_input = list(params)
            return _accept(msg_id, crl, m)
        if m == "LGAFC":
            key = str(params[0]) if params else "0"
            return _accept(msg_id, crl, m, *st.analysis_filt.get(key, ["0", "0", "0", "0", "0", "0"]))
        if m == "LSAFC" and params:
            key = str(params[0])
            st.analysis_filt[key] = list(params[1:]) if len(params) > 1 else list(params)
            return _accept(msg_id, crl, m)
        if m == "LGAFO":
            return _accept(msg_id, crl, m, *st.analysis_out)
        if m == "LGAEN":
            return _accept(msg_id, crl, m, *st.analysis_enum)
        if m == "LGAEV":
            return _accept(msg_id, crl, m, *st.analysis_events)
        if m == "LGAFS":
            return _accept(msg_id, crl, m, *st.analysis_spec)
        if m == "LSAFS" and params:
            st.analysis_spec = list(params)
            return _accept(msg_id, crl, m)

        # === Newly added mock handlers ===

        # VelAxes output limiter (BGFBL/BSFBL)
        if m == "BGFBL":
            return _accept(msg_id, crl, m, *st.fb_limiter)
        if m == "BSFBL" and params:
            st.fb_limiter = [float(x) for x in params]
            return _accept(msg_id, crl, m)

        # Actual time (DGATI/DSATI)
        if m == "DGATI":
            return _accept(msg_id, crl, m, *st.actual_time)
        if m == "DSATI" and len(params) == 4:
            st.actual_time = [int(value) for value in params]
            return _accept(msg_id, crl, m)

        # Floor FF adaptive algorithm (FGFAT/FSFAT)
        if m == "FGFAT":
            return _accept(msg_id, crl, m, st.ff_algo)
        if m == "FSFAT" and params:
            st.ff_algo = int(params[0])
            return _accept(msg_id, crl, m)

        # Pneumatic ramp parameters (PGPRP/PSPRP)
        if m == "PGPRP":
            return _accept(msg_id, crl, m, *st.pneum_ramp)
        if m == "PSPRP" and len(params) == 5:
            st.pneum_ramp = [
                int(params[0]),
                *(float(value) for value in params[1:]),
            ]
            return _accept(msg_id, crl, m)

        if m == "BGTSA":
            return _accept(msg_id, crl, m, *st.temp_sensor_adc)
        if m == "BSTSA" and params:
            st.temp_sensor_adc = [int(value) for value in params[:12]]
            return _accept(msg_id, crl, m)

        # Cascaded position filter (CGCPF/CSCPF)
        if m == "CGCPF" and params:
            stage = int(params[0])
            ftype, fparams = st.cascaded_position_filters.get(
                stage, (0, (0.0,) * 5)
            )
            return _accept(msg_id, crl, m, ftype, *fparams)
        if m == "CSCPF" and params:
            stage, ftype = int(params[0]), int(params[1])
            st.cascaded_position_filters[stage] = (
                ftype, tuple(float(value) for value in params[2:7])
            )
            return _accept(msg_id, crl, m)

        # Cascaded position parameter (CGCPP/CSCPP)
        if m == "CGCPP":
            return _accept(msg_id, crl, m, *st.cascaded_position_params)
        if m == "CSCPP" and params:
            st.cascaded_position_params[1] = params[0]
            return _accept(msg_id, crl, m)

        # Non-linear position (CGSFP/CSSFP)
        if m == "CGSFP":
            return _accept(msg_id, crl, m, *st.non_linear_position_params)
        if m == "CSSFP" and params:
            st.non_linear_position_params = list(params[:4])
            return _accept(msg_id, crl, m)

        # Power-supply current limitation (LGPSL/LSPSL)
        if m == "LGPSL":
            return _accept(msg_id, crl, m, *st.power_supply_limit)
        if m == "LSPSL" and len(params) >= 4:
            st.power_supply_limit[0:2] = list(params[:2])
            return _accept(msg_id, crl, m)

        # ZMS stability (BGSVT/BSSVT, BGLSE, BGSRV)
        if m == "BGSVT":
            return _accept(msg_id, crl, m, *st.zms_thresholds)
        if m == "BSSVT" and len(params) >= 12:
            st.zms_thresholds = [float(value) for value in params[:12]]
            return _accept(msg_id, crl, m)
        if m == "BGLSE":
            return _accept(msg_id, crl, m, *st.zms_last_event)
        if m == "BGSRV":
            return _accept(msg_id, crl, m, *st.zms_status, *st.zms_rms)

        # Safety / earthquake (commands used by the original SafetyPage)
        if m == "LGSEP":
            return _accept(msg_id, crl, m, *st.safety_config)
        if m == "LSSEP" and len(params) >= 8:
            st.safety_config = [float(value) for value in params[:8]]
            return _accept(msg_id, crl, m)
        if m == "LGSRV":
            return _accept(
                msg_id, crl, m, *st.safety_rms,
                f"{st.safety_faults[0]:X}", f"{st.safety_faults[1]:X}",
            )
        if m == "LGERV":
            return _accept(
                msg_id, crl, m, *st.earthquake_rms,
                f"{st.earthquake_faults[0]:X}", f"{st.earthquake_faults[1]:X}",
            )

        # Polynomial compensation (source SAMBA19xUI PolynomPage)
        if m == "LGPSP":
            return _accept(msg_id, crl, m, *st.polynom_status)
        if m == "LSPSP" and len(params) >= 2:
            st.polynom_status = [int(params[0]), int(params[1])]
            return _accept(msg_id, crl, m)
        if m == "LGPCP" and params:
            index = int(params[0])
            return _accept(msg_id, crl, m, *st.polynom_configs.get(index, ["0"] * 13))
        if m == "LSPCP" and len(params) >= 14:
            index = int(params[0])
            st.polynom_configs[index] = list(params[1:14])
            return _accept(msg_id, crl, m)
        if m == "LGPIV":
            return _accept(msg_id, crl, m, *st.polynom_inputs)
        if m == "LGPOV":
            return _accept(msg_id, crl, m, *st.polynom_outputs)
        if m == "LGPRP":
            return _accept(msg_id, crl, m, *st.polynom_ramp)
        if m == "LSPRP" and len(params) >= 4:
            st.polynom_ramp = [float(value) for value in params[:4]]
            return _accept(msg_id, crl, m)

        return _accept(msg_id, crl, m, status=3)
