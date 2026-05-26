from __future__ import annotations

from typing import Any

from python_vna.models import MeasurementSet, SessionConfig


def filter_measurement_to_enabled_channels(
    measurement: MeasurementSet | None,
    session_config: SessionConfig | None,
) -> MeasurementSet | None:
    """Return a measurement view that only exposes enabled AI channels.

    The acquisition pipeline can temporarily hold arrays for every hardware
    input.  Saving and offline cache sync should follow the channel setup,
    otherwise disabled channels reappear in VNA files and Analysis Viewer.
    """

    if measurement is None or session_config is None:
        return measurement
    alias_map = _channel_aliases_by_enabled_state(session_config)
    all_aliases = alias_map["all"]
    enabled_aliases = alias_map["enabled"]
    if not all_aliases or not enabled_aliases:
        return measurement

    def keep_channel(key: object) -> bool:
        text = str(key)
        if text in enabled_aliases:
            return True
        if text in all_aliases:
            return False
        return True

    def keep_pair(key: object) -> bool:
        text = str(key)
        if "->" not in text:
            return keep_channel(text)
        left, right = text.split("->", 1)
        return keep_channel(left) and keep_channel(right)

    time_data = dict(measurement.time_data)
    time_channels = measurement.time_data.get("channels", {})
    if isinstance(time_channels, dict):
        time_data["channels"] = {
            key: value for key, value in time_channels.items() if keep_channel(key)
        }

    spectra: dict[str, Any] = {}
    for name, value in measurement.spectra.items():
        if isinstance(value, dict):
            if name in {"autospectrum", "fft"}:
                spectra[name] = {
                    key: data for key, data in value.items() if keep_channel(key)
                }
            else:
                spectra[name] = {
                    key: data for key, data in value.items() if keep_pair(key)
                }
        else:
            spectra[name] = value

    metadata = dict(measurement.metadata)
    legacy_channels = metadata.get("legacy_channels")
    if isinstance(legacy_channels, dict):
        metadata["legacy_channels"] = {
            key: value for key, value in legacy_channels.items() if keep_channel(key)
        }
    legacy_display_state = metadata.get("legacy_display_state")
    if isinstance(legacy_display_state, dict):
        metadata["legacy_display_state"] = _filter_legacy_display_state(
            legacy_display_state,
            keep_pair=keep_pair,
        )

    return MeasurementSet(
        sample_rate=measurement.sample_rate,
        time_data=time_data,
        spectra=spectra,
        frf={key: value for key, value in measurement.frf.items() if keep_pair(key)},
        coherence={
            key: value for key, value in measurement.coherence.items() if keep_pair(key)
        },
        cross_spectra={
            key: value for key, value in measurement.cross_spectra.items() if keep_pair(key)
        },
        correlations={
            key: value
            for key, value in measurement.correlations.items()
            if _keep_correlation_key(key, keep_channel=keep_channel, keep_pair=keep_pair)
        },
        impulse_responses={
            key: value
            for key, value in measurement.impulse_responses.items()
            if keep_pair(key)
        },
        metadata=metadata,
    )


def _channel_aliases_by_enabled_state(session_config: SessionConfig) -> dict[str, set[str]]:
    all_aliases: set[str] = set()
    enabled_aliases: set[str] = set()
    for index, channel in enumerate(getattr(session_config, "ai_channels", []) or []):
        aliases = _channel_aliases(channel, index)
        all_aliases.update(aliases)
        if bool(getattr(channel, "enabled", True)):
            enabled_aliases.update(aliases)
    return {"all": all_aliases, "enabled": enabled_aliases}


def _channel_aliases(channel: object, index: int) -> set[str]:
    fallback_name = f"ai{index}"
    aliases = {
        fallback_name,
        f"Ch {index + 1}",
        f"Channel {index + 1}",
        str(index + 1),
    }
    for attr in ("name", "label", "physical_name"):
        value = str(getattr(channel, attr, "") or "").strip()
        if value:
            aliases.add(value)
            if "/" in value:
                aliases.add(value.rsplit("/", 1)[-1])
    return {alias for alias in aliases if alias}


def _keep_correlation_key(key: object, *, keep_channel, keep_pair) -> bool:
    text = str(key)
    if "->" in text:
        return keep_pair(text)
    if ":" in text:
        channel, _kind = text.split(":", 1)
        return keep_channel(channel)
    return keep_channel(text)


def _filter_legacy_display_state(
    state: dict[str, Any],
    *,
    keep_pair,
) -> dict[str, Any]:
    filtered = dict(state)
    for panel_key in ("top", "bottom"):
        panel = state.get(panel_key)
        if not isinstance(panel, dict):
            continue
        panel_copy = dict(panel)
        trace_names = panel_copy.get("trace_names")
        if isinstance(trace_names, (list, tuple)):
            panel_copy["trace_names"] = [
                trace for trace in trace_names if keep_pair(trace)
            ]
        filtered[panel_key] = panel_copy
    return filtered
