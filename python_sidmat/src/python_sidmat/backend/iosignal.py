"""IO signal model and naming — port of ``SAMBA19xLib.IOType`` and
``SAMBA19xLabels.GetIOName``.

An IO signal is the ``(Type, MainIndex, SubIndex)`` triple that every
SiDiMaT measurement channel (trace Ch0/Ch1, noise injection point,
diagnostic outputs) is addressed by.  Names mirror the original software so
the UI shows identical channel labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from python_samba.ui.label_files import LABEL_FILE_DEFAULTS as _DEFAULTS

__all__ = [
    "IOType",
    "IO_TYPE_NAMES",
    "io_type_name",
    "io_signal_list",
    "configure_filter_counts",
    "DEFAULT_VELOCITY_FILTER_COUNT",
    "DEFAULT_POSITION_FILTER_COUNT",
    "SUPPORTED_IO_TYPES",
]


# The original SystemConfigurationParameters defaults.  Real controllers can
# report different values through NGEXL; MainWindow updates these counts after
# connecting so position/velocity stage labels and picker entries stay valid.
DEFAULT_VELOCITY_FILTER_COUNT = 7
DEFAULT_POSITION_FILTER_COUNT = 4
_velocity_filter_count = DEFAULT_VELOCITY_FILTER_COUNT
_position_filter_count = DEFAULT_POSITION_FILTER_COUNT

# Type ids used by the controller (from SAMBA19xLabels.IOTypeName).
IO_TYPE_NAMES: dict[int, str] = {
    0: "Sensor",
    1: "ACTUATOR",
    2: "Velocity",
    3: "Noise",
    4: "VelAxesOutput",
    5: "Position",
    6: "NoUsed1",
    7: "NotUsed2",
    8: "Pneumatic",
    9: "NotUsed3",
    10: "FF",
    11: "PFF",
    12: "TempSensor",
    13: "Polynom",
    14: "ProxCorrection",
}

SUPPORTED_IO_TYPES: tuple[int, ...] = (
    0, 1, 2, 3, 4, 5, 8, 10, 11, 12, 13, 14,
)

# These two arrays are intentionally kept as local fallbacks because older
# python_samba label tables left them empty, while the original SAMBA labels
# contain the names below.  A loaded custom label file still takes precedence.
_FALLBACK_LABELS: dict[str, tuple[str, ...]] = {
    "PolynomName": (
        "Prox1 Polynom", "Prox2 Polynom", "Prox3 Polynom", "Prox4 Polynom",
        "Prox5 Polynom", "Prox6 Polynom", "Prox7 Polynom", "Prox8 Polynom",
        "Valve1 Polynom", "Valve2 Polynom", "Valve3 Polynom", "Valve4 Polynom",
        "Valve5 Polynom", "Valve6 Polynom", "Valve7 Polynom", "Valve8 Polynom",
        "Polynom17", "Polynom18", "Polynom19",
    ),
    "ProximityCorrectionSignalName": (
        "Prox1 Corr", "Prox2 Corr", "Prox3 Corr", "Prox4 Corr",
        "Prox5 Corr", "Prox6 Corr", "Prox7 Corr", "Prox8 Corr",
    ),
}


@dataclass(slots=True)
class IOType:
    type: int = 0
    main_index: int = 0
    sub_index: int = 0
    name: str = ""

    def __post_init__(self) -> None:
        self.type = int(self.type)
        self.main_index = int(self.main_index)
        self.sub_index = int(self.sub_index)
        if not self.name:
            self.name = io_type_name(self)

    def __iter__(self):
        # Allow tuple unpacking: io.type, io.main_index, io.sub_index
        return iter((self.type, self.main_index, self.sub_index))

    def encode(self) -> tuple[int, int, int]:
        return (self.type, self.main_index, self.sub_index)


def _n(names_key: str, index: int, fallback: str) -> str:
    table = _DEFAULTS.get(names_key, ()) or _FALLBACK_LABELS.get(names_key, ())
    if 0 <= index < len(table):
        return table[index]
    return fallback


def configure_filter_counts(
    *, velocity: int | None = None, position: int | None = None
) -> None:
    """Update controller-dependent filter counts used by IO labels/menus.

    Velocity controllers support at most seven stages and position controllers
    at most twelve in the legacy IO protocol.  Values outside those ranges are
    rejected instead of generating selectable IO triples the controller cannot
    address.
    """

    global _velocity_filter_count, _position_filter_count
    new_velocity = _velocity_filter_count
    new_position = _position_filter_count
    if velocity is not None:
        value = int(velocity)
        if not 0 <= value <= 7:
            raise ValueError(f"velocity filter count must be in 0..7, got {velocity}")
        new_velocity = value
    if position is not None:
        value = int(position)
        if not 0 <= value <= 12:
            raise ValueError(f"position filter count must be in 0..12, got {position}")
        new_position = value
    # Commit only after both values validate, so one bad NGEXL field cannot
    # leave the module with a half-updated signal map.
    _velocity_filter_count = new_velocity
    _position_filter_count = new_position


def io_type_name(io: IOType) -> str:
    """Return the display name for an IO signal (``GetIOName`` port)."""
    t, mi, si = io.type, io.main_index, io.sub_index

    if t == 0:  # Sensor
        return _n("InputName", mi, "Unknown Sens")
    if t == 12:  # TempSensor
        return _n("MotorTemperaturSensorName", mi, "Unknown Temp")
    if t == 14:  # ProxCorrection
        return _n("ProximityCorrectionSignalName", mi, "Unknown ProxCorr")
    if t == 13:  # Polynom
        if si > 1:
            return "Unknown Poly"
        base = _n("PolynomName", mi, "NaN")
        if si == 0:
            return base + " Input"
        if si == 1:
            return base + " Output"
        return base
    if t == 1:  # ACTUATOR
        return _n("DACOutputName", mi, "Unknown Actua")
    if t == 2:  # Velocity
        if not 0 <= mi < 6 or not (-1 <= si <= _velocity_filter_count):
            return "Unknown Vel"
        axes = _DEFAULTS["VelAxesName"]
        if si == -1:
            return f"Vel {axes[mi]} Raw"
        if si == _velocity_filter_count:
            return f"Vel {axes[mi]} Output"
        return f"Vel {axes[mi]} Stage{si + 1}"
    if t == 4:  # VelAxesOutput
        if not 0 <= mi < 6:
            return "Unknown Out"
        return f"Vel {_DEFAULTS['VelAxesName'][mi]} Output"
    if t == 5:  # Position
        if not 0 <= mi < 12 or not (-1 <= si <= _position_filter_count):
            return "Unknown Pos"
        axes = _DEFAULTS["PosAxesName"]
        if si == -1:
            return f"Pos {axes[mi]} Raw"
        if si == _position_filter_count:
            return f"Pos {axes[mi]} Output"
        return f"Pos {axes[mi]} Stage{si + 1}"
    if t == 8:  # Pneumatic
        if not 0 <= mi < 3 or not (-1 <= si <= 4):
            return "Unknown Pneu"
        axes = _DEFAULTS["PneuAxesName"]
        if si == -1:
            return f"Pneu {axes[mi]} Raw"
        if si == 4:
            return f"Pneu {axes[mi]} Output"
        return f"Pneu {axes[mi]} Stage{si + 1}"
    if t == 10:  # FF
        if not 0 <= mi < 7 or si < 0:
            return "Unknown FF"
        if si < 3:
            return f"FF Ch{mi + 1} RefFil{si + 1}"
        if si < 6:
            return f"FF Ch{mi + 1} SecFil{si + 1 - 3}"
        if si < 12:
            return f"FF Ch{mi + 1} {_DEFAULTS['VelAxesName'][si - 6]} Out"
        return "Unknown FF"
    if t == 11:  # PFF
        if not 0 <= mi < 4 or si < 0:
            return "Unknown PFF"
        if si < 3:
            return f"PFF Ch{mi + 1} RefFil{si + 1}"
        if si < 6:
            return f"PFF Ch{mi + 1} SecFil{si + 1 - 3}"
        if si < 9:
            return f"PFF Ch{mi + 1} {_DEFAULTS['PneuAxesName'][si - 6]} Out"
        return "Unknown PFF"
    if t == 3:  # Noise
        return "Excitation"
    return "Unknown Type"


def _signal_range(io_type: int, main_max: int, sub_max: int) -> list[IOType]:
    out: list[IOType] = []
    for mi in range(main_max):
        for si in range(sub_max):
            out.append(IOType(io_type, mi, si))
    return out


def _filtered_signal_range(
    io_type: int, main_max: int, filter_count: int
) -> list[IOType]:
    """Raw (-1), configured stages, then output for each axis."""

    out: list[IOType] = []
    for mi in range(main_max):
        out.append(IOType(io_type, mi, -1))
        for si in range(filter_count):
            out.append(IOType(io_type, mi, si))
        out.append(IOType(io_type, mi, filter_count))
    return out


def io_signal_list(io_type: int) -> list[IOType]:
    """All selectable IO signals of one type, for a channel picker menu."""
    if io_type == 0:
        return [IOType(0, mi, 0) for mi in range(min(46, len(_DEFAULTS["InputName"])))]
    if io_type == 1:
        return [IOType(1, mi, 0) for mi in range(len(_DEFAULTS["DACOutputName"]))]
    if io_type == 2:
        return _filtered_signal_range(2, 6, _velocity_filter_count)
    if io_type == 3:
        return [IOType(3, 0, 0)]
    if io_type == 4:
        return [IOType(4, mi, 0) for mi in range(6)]
    if io_type == 5:
        return _filtered_signal_range(5, 12, _position_filter_count)
    if io_type == 8:
        return _filtered_signal_range(8, 3, 4)
    if io_type == 10:
        return _signal_range(10, 7, 12)
    if io_type == 11:
        return _signal_range(11, 4, 9)
    if io_type == 12:
        return [IOType(12, mi, 0) for mi in range(len(_DEFAULTS["MotorTemperaturSensorName"]))]
    if io_type == 13:
        return _signal_range(13, 19, 2)
    if io_type == 14:
        return [IOType(14, mi, 0) for mi in range(8)]
    return []
