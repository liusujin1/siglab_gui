"""Filter type definitions — port of ``SAMBA19xLib.Controller.filterDefinitions``.

Each entry mirrors one ``TCMFDFilterDefintion`` row:
``(smallName, description, type_id, param_count, param_labels[5])``.
``GetFilterName`` semantics: type 0 → ``"----"``; 1..23 → smallName; else
``"UKNOWN"``.
"""

from __future__ import annotations

__all__ = ["FILTER_TYPES", "filter_name", "filter_description"]

# (smallName, description, param_names)
FILTER_TYPES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("NOFIL", "Pass through", ("unused",) * 5),
    ("LPF1O", "Low pass (1st order)", ("Cut-off frequency (Hz)", "unused", "Gain", "unused", "unused")),
    ("LPF2O", "Low pass (2nd order)", ("Cut-off frequency (Hz)", "unused", "Gain", "unused", "unused")),
    ("HPF1O", "High pass (1st order)", ("Cut-off frequency (Hz)", "unused", "Gain", "unused", "unused")),
    ("HPF2O", "High pass (2nd order)", ("Cut-off frequency (Hz)", "unused", "Gain", "unused", "unused")),
    ("BPF", "Band pass", ("Frequency (Hz)", "Q-Factor", "Gain", "unused", "unused")),
    ("NOTCH", "Notch filter", ("Frequency (Hz)", "Q-Factor", "Gain", "unused", "unused")),
    ("PID", "PID Controller", ("P-Gain", "I-Gain", "D-Gain", "unused", "unused")),
    ("HOPT", "High Optional Filter", ("Frequency (Hz)", "Frequency (Hz)", "Parameter 3", "unused", "unused")),
    ("INOTCH", "Inverted Notch Filter", ("Frequency (Hz)", "Numinator", "Denominator", "unused", "unused")),
    ("VLOOP", "Velocity Closed Loop Filter", ("Frequency (Hz)", "Frequency (Hz)", "unused", "unused", "unused")),
    ("PLOOP", "Position Loop Filter", ("Frequency (Hz)", "Frequency (Hz)", "unused", "unused", "unused")),
    ("PPID", "Pneum PID", ("P-Gain", "I-Gain", "D-Gain", "unused", "unused")),
    ("LL1O", "LeadLag (1st order)", ("Frequency1 (Hz)", "Frequency2 (Hz)", "Gain", "unused", "unused")),
    ("LL2O", "LeadLag (2nd order)", ("Frequency1 (Hz)", "Frequency2 (Hz)", "Gain", "unused", "unused")),
    ("ANOTCH", "Async Notch", ("Parameter 1", "Parameter 2", "Parameter 3", "unused", "unused")),
    ("HPFQF", "High pass with q-factor", ("Cut-off frequency (Hz)", "Q-Factor", "unused", "unused", "unused")),
    ("LPFQF", "Low pass with q-factor", ("Cut-off frequency (Hz)", "Q-Factor", "unused", "unused", "unused")),
    ("STRETCH", "Stretcher Filter", ("unused",) * 5),
    ("BPF2E", "Band pass Filter (second edition)", ("Frequency 1", "Frequency 2", "Gain", "unused", "unused")),
    ("LINTEG", "Limited Integrator", ("Lim. Frequency", "0 dB Frequency", "Gain", "unused", "unused")),
    ("VARFIL", "Variable Filter", ("b0", "b1", "b2", "a1", "a2")),
    ("ANOTCH5", "ANOTCH 5 parameters", ("num. freq.", "num. damp.", "denom. freq.", "denom. damp.", "gain")),
    ("LOPID", "Limited Output PID", ("P-Gain.", "I-Gain", "D-Gain.", "Output Lower Limit", "Output Upper Limit")),
)


def filter_name(type_id: int) -> str:
    """Display name for a filter type (port of ``GetFilterName``)."""
    type_id = int(type_id)
    if type_id == 0:
        return "----"
    if 0 < type_id < len(FILTER_TYPES):
        return FILTER_TYPES[type_id][0]
    return "UKNOWN"


def filter_description(type_id: int) -> str:
    type_id = int(type_id)
    if 0 < type_id < len(FILTER_TYPES):
        return FILTER_TYPES[type_id][1]
    return ""
