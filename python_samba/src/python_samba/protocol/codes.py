"""Status codes and domain enums from the RCI specification."""

from __future__ import annotations

from enum import IntEnum, IntFlag


class StatusCode(IntEnum):
    SUCCESS = 0x00
    BAD_CRL = 0x01
    INCOMPLETE_COMMAND = 0x02
    UNKNOWN_COMMAND = 0x03
    INVALID_CHAR = 0x04
    INVALID_ONOFF_VALUE = 0x05
    INVALID_FLOAT = 0x06
    INVALID_INTEGER = 0x07
    INVALID_HEX = 0x08
    INVALID_FILTER_STAGE = 0x09
    INVALID_AXIS_CODE = 0x0A
    INVALID_FILTER_PARAM = 0x0B
    INVALID_FILTER_TYPE = 0x0C
    INVALID_FF_SOURCE = 0x0D
    OUT_OF_RANGE = 0x0E
    PASSTHRU_OFF = 0x0F
    NVRAM_PROTECTED = 0x10
    NOT_ALLOWED_NOW = 0x11
    KASSANDRA_NOT_RESPONDING = 0x12
    KASSANDRA_NOT_PRESENT = 0x13
    EVENT_LOGGING_IS_STARTED = 0x14
    NO_EVENT_TRACES_IS_SAVED = 0x15
    COMM_MENU_NOT_ALLOWED = 0xFB
    TIMEOUT = 0xFC
    SLAVE_COMM_FAIL = 0xFD
    ABORT_STATUS = 0xFE
    COMMAND_NOT_IMPLEMENTED = 0xFF


STATUS_NAMES = {int(s): s.name for s in StatusCode}


def status_name(code: int) -> str:
    return STATUS_NAMES.get(code, f"UNKNOWN_0x{code:02X}")


class FilterType(IntEnum):
    NOFIL = 0
    LPF1O = 1
    LPF2O = 2
    HPF1O = 3
    HPF2O = 4
    BPF = 5
    NOTCH = 6
    PID = 7
    HOPT = 8
    INOTCH = 9
    VLOOP = 10
    PLOOP = 11
    PPID = 12
    LEADLAG = 13
    LEADLAG2 = 14
    ANOTCH = 15
    HPFXX = 16
    LPFXX = 17
    STRETCH = 18
    BPF2E = 19
    LIMINTEG = 20
    VAR_FILT = 21
    ANOTCH5P = 22
    LOPID = 23


# Filter definitions matching SAMBA19xLib.Controller.cs
# (smallName, longName, index, paramCount, desc0..desc4)
FILTER_DEFINITIONS: list[tuple[str, str, int, int, str, str, str, str, str]] = [
    ("NOFIL",  "Pass through",                    0, 0, "unused", "unused", "unused", "unused", "unused"),
    ("LPF1O",  "Low pass (1st order)",            1, 2, "Cut-off frequency (Hz)", "unused", "Gain", "unused", "unused"),
    ("LPF2O",  "Low pass (2nd order)",            2, 2, "Cut-off frequency (Hz)", "unused", "Gain", "unused", "unused"),
    ("HPF1O",  "High pass (1st order)",           3, 2, "Cut-off frequency (Hz)", "unused", "Gain", "unused", "unused"),
    ("HPF2O",  "High pass (2nd order)",           4, 2, "Cut-off frequency (Hz)", "unused", "Gain", "unused", "unused"),
    ("BPF",    "Band pass",                       5, 3, "Frequency (Hz)", "Q-Factor", "Gain", "unused", "unused"),
    ("NOTCH",  "Notch filter",                    6, 3, "Frequency (Hz)", "Q-Factor", "Gain", "unused", "unused"),
    ("PID",    "PID Controller",                  7, 3, "P-Gain", "I-Gain", "D-Gain", "unused", "unused"),
    ("HOPT",   "High Optional Filter",            8, 3, "Frequency (Hz)", "Frequency (Hz)", "Parameter 3", "unused", "unused"),
    ("INOTCH", "Inverted Notch Filter",           9, 3, "Frequency (Hz)", "Numinator", "Denominator", "unused", "unused"),
    ("VLOOP",  "Velocity Closed Loop Filter",    10, 2, "Frequency (Hz)", "Frequency (Hz)", "unused", "unused", "unused"),
    ("PLOOP",  "Position Loop Filter",           11, 2, "Frequency (Hz)", "Frequency (Hz)", "unused", "unused", "unused"),
    ("PPID",   "Pneum PID",                      12, 3, "P-Gain", "I-Gain", "D-Gain", "unused", "unused"),
    ("LL1O",   "LeadLag (1st order)",            13, 3, "Frequency1 (Hz)", "Frequency2 (Hz)", "Gain", "unused", "unused"),
    ("LL2O",   "LeadLag (2nd order)",            14, 3, "Frequency1 (Hz)", "Frequency2 (Hz)", "Gain", "unused", "unused"),
    ("ANOTCH", "Async Notch",                    15, 3, "Parameter 1", "Parameter 2", "Parameter 3", "unused", "unused"),
    ("HPFQF",  "High pass with q-factor",        16, 2, "Cut-off frequency (Hz)", "Q-Factor", "unused", "unused", "unused"),
    ("LPFQF",  "Low pass with q-factor",         17, 2, "Cut-off frequency (Hz)", "Q-Factor", "unused", "unused", "unused"),
    ("STRETCH","Stretcher Filter",               18, 0, "unused", "unused", "unused", "unused", "unused"),
    ("BPF2E",  "Band pass Filter (2nd edition)", 19, 3, "Frequency 1", "Frequency 2", "Gain", "unused", "unused"),
    ("LINTEG", "Limited Integrator",             20, 3, "Lim. Frequency", "0 dB Frequency", "Gain", "unused", "unused"),
    ("VARFIL", "Variable Filter",                21, 5, "b0", "b1", "b2", "a1", "a2"),
    ("ANOTCH5","ANOTCH 5 parameters",            22, 5, "num. freq.", "num. damp.", "denom. freq.", "denom. damp.", "gain"),
    ("LOPID",  "Limited Output PID",             23, 5, "P-Gain.", "I-Gain", "D-Gain.", "Output Lower Limit", "Output Upper Limit"),
]


def filter_small_name(ftype: int) -> str:
    """Get the short display name for a filter type (e.g., 'LPF1O', '---' for NOFIL)."""
    if ftype == 0:
        return "---"
    if 0 <= ftype < len(FILTER_DEFINITIONS):
        return FILTER_DEFINITIONS[ftype][0]
    return f"F{ftype}"


def filter_long_name(ftype: int) -> str:
    """Get the long descriptive name."""
    if 0 <= ftype < len(FILTER_DEFINITIONS):
        return FILTER_DEFINITIONS[ftype][1]
    return f"Unknown ({ftype})"


def filter_param_descriptions(ftype: int) -> tuple[str, str, str, str, str]:
    """Get parameter descriptions for a filter type."""
    if 0 <= ftype < len(FILTER_DEFINITIONS):
        return FILTER_DEFINITIONS[ftype][4:9]
    return ("", "", "", "", "")


def filter_param_count(ftype: int) -> int:
    """Get number of active parameters for a filter type."""
    if 0 <= ftype < len(FILTER_DEFINITIONS):
        return FILTER_DEFINITIONS[ftype][3]
    return 0


def filter_tooltip(ftype: int, params: tuple[float, ...]) -> str:
    """Build a tooltip like 'Low pass (1st order); Cut-off frequency (Hz): 10.000'."""
    if ftype == 0:
        return "Pass through"
    if 0 <= ftype < len(FILTER_DEFINITIONS):
        name = FILTER_DEFINITIONS[ftype][1]
        descs = FILTER_DEFINITIONS[ftype][4:9]
        parts = [name]
        for i in range(5):
            if descs[i] != "unused" and i < len(params):
                parts.append(f"{descs[i]}: {params[i]:.3e}")
        return "; ".join(parts)
    return f"Filter type {ftype}"


class VelAxis(IntEnum):
    X_TRANS = 0
    Z_ROT = 1
    Y_TRANS = 2
    Z_TRANS = 3
    Y_ROT = 4
    X_ROT = 5


class SystemStatus(IntFlag):
    """Bit field returned as System Status Word (BGSTS)."""

    LOOP = 1
    ADAPTIVE = 2
    FF_LOOP = 4
    NOISE = 8
    FLOOR_FF_LOOP = 16
    FLOOR_FF_ADAPTIVE = 32
    PNEU_LOOP = 64
    NOISE_FILTER = 128
    BOXER_CROSS = 256
    ANALOG_FF = 2048
    USE_FB_FOR_FF = 4096
    DITHER_COMP = 8192
    PFF_LOOP = 16384
    # Doc lists 327768 which looks like a typo for 32768
    PFF_ADAPTIVE = 32768
    USE_TEMP_SENSORS = 65536


class IndividualLoopStatus(IntFlag):
    """Per-axis bits in Individual Loop Status (BGSTS first word)."""

    X_TRANS = 1
    Z_ROT = 2
    Y_TRANS = 4  # doc table numbering is 1..6 for axes; bit values follow powers of two in practice via RCI demo hex
    Z_TRANS = 8
    Y_ROT = 16
    X_ROT = 32


# Demo sample showed Loop-Status 1800h and Motor-Status 7fh — treat raw ints in domain models.
