"""Page registry mirroring SAMBA19xUI navigation (manual rev 08).

status: ready | partial | stub
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PageSpec:
    page_id: str
    title: str
    group: str
    status: str  # ready | partial | stub
    notes: str = ""


# Order follows SAMBA19xUI User Interface Manual TOC.
PAGE_SPECS: list[PageSpec] = [
    PageSpec("connect", "Connect", "Connection", "ready", "backend/port/baud"),
    PageSpec("status", "Status", "Monitor", "ready", "FW, loop, sample, geophone"),
    PageSpec("loops_config", "Loops Configuration", "System", "ready", "BGSTS/BSSTS + output limit"),
    PageSpec("performance", "Performance Monitor", "System", "ready", "DGPMV/DGPMS/DGSLO"),
    PageSpec("switch", "Switch Criterion", "System", "ready", "BGSWS/BGOCD/DGCSS"),
    PageSpec(
        "motor_protection",
        "Motor Protection",
        "System",
        "ready",
        "BGOCV/BGMPV/BGMPS + BGMCC/BSMCC",
    ),
    PageSpec("velocity_tuning", "Velocity Tuning", "Velocity", "ready", "filters"),
    PageSpec("velocity_matrix", "Velocity Sensor/Motor Matrix", "Velocity", "ready"),
    PageSpec("position_tuning", "Position Tuning", "Position", "ready", "prox filter + offsets"),
    PageSpec("position_sensor_matrix", "Position Sensor Matrix", "Position", "ready"),
    PageSpec("position_motor_matrix", "Position Motor Matrix", "Position", "ready"),
    PageSpec("pneumatic_tuning", "Pneumatic Tuning", "Pneumatic", "ready", "filter/status/heights"),
    PageSpec("floatation", "Floatation Config", "Pneumatic", "ready", "PSPCP/PGPCP/valve/setpoint"),
    PageSpec("dither", "Dithering Config", "Pneumatic", "ready", "PSDIT/PSDFR/PSDCA"),
    PageSpec("pneumatic_ramp", "Pneumatic Ramp", "Pneumatic", "ready", "BGSUT/BSSUT startup ramp"),
    PageSpec("ff_tuning", "Feed Forward Tuning", "Feedforward", "ready", "status + filter stage"),
    PageSpec("pff_tuning", "Pneumatic FF Tuning", "Feedforward", "ready", "filter/gains/reset/inputs"),
    PageSpec("diagnostics", "Diagnostics / Noise", "Diagnostics", "ready", "noise type/gain/inject"),
    PageSpec(
        "nvram",
        "Save / Load / Clear NVRAM",
        "Setup",
        "ready",
        "NASUP/NARUP/NACLR + BCNCS/BBNCS",
    ),
    PageSpec("dac_adc", "DAC / ADC Sequence", "Setup", "ready", "BGADS/BGDAS"),
    PageSpec("logging", "Event Logging", "Advanced", "ready", "monitor/download DGLDV"),
    PageSpec("raw", "Raw RCI", "Advanced", "ready", "escape hatch"),
]


GROUPS: list[str] = []
for spec in PAGE_SPECS:
    if spec.group not in GROUPS:
        GROUPS.append(spec.group)
