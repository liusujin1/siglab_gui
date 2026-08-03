"""Read and write legacy ``.SAMBA19xLabel`` files.

The field names and default ordering mirror ``SAMBA19xUILabels`` and
``SAMBA19xLabels`` from the original application.  Keeping the XML handling in
this Qt-free module makes it usable during startup and in headless tests.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import xml.etree.ElementTree as ET


LABEL_FILE_DEFAULTS: dict[str, tuple[str, ...]] = {
    "InputName": (
        "X1FB", "Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB",
        "XFF", "YFF", "ZFF", "Prox1", "Prox2", "Prox3", "ProxH1",
        "ProxH2", "ProxH3", "XPOS", "XACC", "YPOS", "YACC", "Y2FB",
        "X3FB", "X4FB", "Y4FB", "Z4FB", "Prox1-Off", "Prox2-Off",
        "Prox3-Off", "ProxH1-Off", "ProxH2-Off", "ProxH3-Off",
        "Zr_XACC", "Zr_YACC", "C_XACC", "C_YACC", "XPosRaw", "YPosRaw",
        "Prox4", "ProxH4", "Auxiliary1", "Auxiliary2", "Auxiliary3",
        "Auxiliary4", "Auxiliary5", "Prox4-Off", "ProxH4-Off",
    ),
    "NGIPosLoopModeName": ("PAtEndStop", "PGoingUp", "PAtTarget"),
    "VelAxesName": ("Xtrans", "Zrot", "Ytrans", "Ztrans", "Yrot", "Xrot"),
    "PosAxesName": (
        "Xrot", "Yrot", "Xtrans", "Ytrans", "Zrot", "Ztrans",
        "Xrot2", "Yrot2", "Xtrans2", "Ytrans2", "Zrot2", "Ztrans2",
    ),
    "PneuAxesName": ("Ztpneu", "Yrpneu", "Xrpneu"),
    "Vel7InputName": (
        "X1FB", "Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB", "Z4FB",
    ),
    "Vel8InputName": (
        "Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB", "X4FB", "Z4FB",
    ),
    "VelOutputName": (
        "OutX1", "OutY1", "OutZ1", "OutX2", "OutY2", "OutZ2",
        "OutX3", "OutY3", "OutZ3", "OutX4", "OutY4", "OutZ4",
    ),
    "MotorsName": (
        "OutX1", "OutY1", "OutZ1", "OutX2", "OutY2", "OutZ2",
        "OutX3", "OutY3", "OutZ3", "OutX4", "OutY4", "OutZ4",
    ),
    "MotorOffsetName": (
        "OutY1", "OutX2", "OutY3", "OutX4", "OutY2", "OutX1",
        "OutY4", "OutX3", "Iso1", "Iso2", "Iso3",
    ),
    "DACOutputName": (
        "OutX1", "OutY1", "OutZ1", "OutX2", "OutY2", "OutZ2",
        "OutX3", "OutY3", "OutZ3", "OutX4", "OutY4", "OutZ4",
        "Valve1", "Valve2", "Valve3", "Valve4", "Valve5", "Valve6",
        "Diag0", "Diag1",
    ),
    "MotorTemperaturSensorName": (
        "OutX1Temp", "OutY1Temp", "OutZ1Temp", "OutX2Temp", "OutY2Temp",
        "OutZ2Temp", "OutX3Temp", "OutY3Temp", "OutZ3Temp", "OutX4Temp",
        "OutY4Temp", "OutZ4Temp",
    ),
    "ADCInputName": (
        "X1FB", "Y1FB", "Z1FB", "X2FB", "Z2FB", "Y3FB", "Z3FB",
        "Xff", "Yff", "Zff", "Prox1", "Prox2", "Prox3", "ProxH1",
        "ProxH2", "ProxH3", "Xpos", "Xacc", "Ypos", "Yacc", "Y2FB",
        "X3FB", "X4FB", "Y4FB", "Z4FB", "Prox4", "ProxH4",
        "Auxiliary1", "Auxiliary2", "Auxiliary3", "Auxiliary4", "Auxiliary5",
    ),
    "FilterTypeName": (
        "NOFIL", "LPF1O", "LPF2O", "HPF1O", "HPF2O", "BPF", "NOTCH",
        "PID", "HOPT", "INOTCH", "VLOOP", "PLOOP", "PPID", "LL1O",
        "LL2O", "ANOTCH", "HPFXX", "LPFXX", "STRETCH", "BPF2E",
        "LINTEG", "VAR_FIL", "ANOTCH5", "LOPID",
    ),
    "PneuStatusVertLoopStatusName": (
        "Down", "Going2SoftStop", "Up Soft", "Going Up", "UP",
        "Going Down", "Initialisation", "OK",
    ),
    "ExcitTypeName": (
        "NoNoise", "WhiteNoise", "SineWave", "External_NotUsed",
        "DutyCycle", "ChirpSine", "Triangular", "Sawtooth", "Step",
    ),
    "PolynomName": (),
    "ProximityCorrectionSignalName": (),
}


# The original loader applies these ten arrays and leaves the remaining XML
# fields untouched.  A short/missing array is ignored independently.
RUNTIME_MIN_COUNTS: dict[str, int] = {
    "PosAxesName": 12,
    "VelAxesName": 6,
    "PneuAxesName": 3,
    "InputName": 46,
    "Vel7InputName": 8,
    "Vel8InputName": 8,
    "ADCInputName": 32,
    "DACOutputName": 20,
    "VelOutputName": 12,
    "MotorTemperaturSensorName": 12,
}


def _local_name(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1]


def parse_label_file(path: str | Path) -> dict[str, list[str]]:
    """Parse a legacy label file and return all known arrays."""
    root = ET.parse(path).getroot()
    if _local_name(root.tag) != "SAMBA19xUILabels":
        raise ValueError("Not a valid SAMBA19xUILabels file")

    values: dict[str, list[str]] = {}
    for element in list(root):
        name = _local_name(element.tag)
        if name not in LABEL_FILE_DEFAULTS:
            continue
        values[name] = [
            child.text or ""
            for child in list(element)
            if _local_name(child.tag) == "string"
        ]
    return values


def runtime_label_warnings(values: Mapping[str, Sequence[str]]) -> list[str]:
    """Return the per-array warnings produced by the old partial loader."""
    return [
        f"{name} requires at least {minimum} entries"
        for name, minimum in RUNTIME_MIN_COUNTS.items()
        if len(values.get(name, ())) < minimum
    ]


def write_label_file(
    path: str | Path,
    overrides: Mapping[str, Sequence[str]] | None = None,
) -> None:
    """Write an XML document compatible with the old XmlSerializer output."""
    arrays = {name: list(items) for name, items in LABEL_FILE_DEFAULTS.items()}
    if overrides:
        for name, items in overrides.items():
            if name in arrays:
                arrays[name] = [str(item) for item in items]

    root = ET.Element(
        "SAMBA19xUILabels",
        {
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
        },
    )
    ET.SubElement(root, "FileVersion").text = "1"
    for name, items in arrays.items():
        element = ET.SubElement(root, name)
        for item in items:
            ET.SubElement(element, "string").text = item

    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
