from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.io import loadmat

from python_vna.models import MeasurementSet, SavedSession
from python_vna.storage import (
    default_session_config,
    load_legacy_vna,
    load_session_json,
    save_measurement_csv,
    save_measurement_npz,
    save_legacy_vna,
    save_session_json,
)


class StorageTests(unittest.TestCase):
    def _sample_session(self) -> SavedSession:
        measurement = MeasurementSet(
            sample_rate=1024.0,
            time_data={"t": np.array([0.0, 0.1]), "channels": {"ai0": np.array([1.0, 2.0])}},
            spectra={
                "f": np.array([0.0, 1.0]),
                "fft": {"ai0": np.array([1.0 + 0.0j, 0.5 + 0.1j])},
                "autospectrum": {"ai0": np.array([1.0, 0.26])},
            },
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
        )
        return SavedSession(config=default_session_config(), measurement=measurement)

    def test_save_and_load_json(self):
        session = self._sample_session()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "session.json"
            save_session_json(session, path)
            loaded = load_session_json(path)
            self.assertEqual(loaded["config"]["title"], "USB-4431 Default Session")
            self.assertEqual(loaded["measurement"]["sample_rate"], 1024.0)

    def test_save_npz_and_csv(self):
        session = self._sample_session()
        with tempfile.TemporaryDirectory() as tmpdir:
            npz_path = Path(tmpdir) / "session.npz"
            csv_path = Path(tmpdir) / "session.csv"
            save_measurement_npz(session, npz_path)
            save_measurement_csv(session, csv_path)
            with np.load(npz_path) as data:
                self.assertIn("time_ai0", data.files)
            csv_text = csv_path.read_text(encoding="utf-8")
            self.assertIn("time_seconds,ai0", csv_text)

    def test_save_legacy_vna_round_trips_core_fields(self):
        session = self._sample_session()
        session.config.ai_channels[0].label = "Ref"
        session.config.ai_channels[0].engineering_unit = "m/s^2"
        session.config.ai_channels[0].full_scale = 5.0
        session.config.ai_channels[0].coupling = "bias"
        session.config.ai_channels[0].sensitivity = 2.0
        session.config.acquisition.reference_channel = "ai0"
        session.config.acquisition.response_channels = ["ai1"]
        session.measurement.frf = {"ai0->ai1": np.array([1.0 + 0.0j, 2.0 + 0.5j])}
        session.measurement.coherence = {"ai0->ai1": np.array([1.0, 0.9])}
        session.measurement.cross_spectra = {"ai0->ai1": np.array([0.5 + 0.0j, 0.25 + 0.1j])}

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "roundtrip.vna"
            save_legacy_vna(session, path)
            compressed_tag_count = path.read_bytes().count(b"\x0f\x00\x00\x00")
            mat = loadmat(path, squeeze_me=False, struct_as_record=False)
            loaded = load_legacy_vna(path)

        self.assertIn("key", mat)
        self.assertEqual(str(np.squeeze(mat["key"])), "DSPt vna_2 file")
        for field_name in (
            "num_io",
            "vdlg1_s1",
            "vdlg1_s2",
            "ChanStat",
            "ChanLabel",
            "EULabel",
            "SLm",
            "xplot_s1",
            "xplot_s2",
            "xplot_axes",
            "grids",
        ):
            self.assertIn(field_name, mat)
        slm = mat["SLm"][0, 0]
        self.assertEqual(slm.scmeas.shape, (1, 16))
        self.assertEqual(slm.xcmeas.shape, (4, 16))
        self.assertEqual(slm.scmeas[0, 0]._fieldnames[:4], ["tdmeas", "aspec", "fft", "acor"])
        self.assertIn("xfer", slm.xcmeas[0, 1]._fieldnames)
        self.assertEqual(slm.xcstate[0, 0].resp[0, 0].r.tolist(), [[2]])
        self.assertEqual(slm.filestor[0, 0].state.dtype, object)
        self.assertEqual(int(np.squeeze(slm.filestor[0, 0].state[2, 0])), 1)
        self.assertIn("xchanv", mat["xplot_s1"][0, 0]._fieldnames)
        self.assertEqual(
            mat["xplot_s1"][0, 0].xchanv[0, 0].xc_ckstate.shape,
            (4, 16),
        )
        self.assertEqual(mat["vdlg1_s2"].dtype.kind, "U")
        self.assertEqual(mat["vdlg1_s2"].shape, (16,))
        self.assertEqual(mat["ChanLabel"].dtype.kind, "U")
        self.assertEqual(mat["EULabel"].dtype.kind, "U")
        xplot_axes = np.asarray(mat["xplot_axes"], dtype=float)
        self.assertEqual(xplot_axes.shape, (10, 5))
        active_rows = xplot_axes[xplot_axes[:, 4] >= 0]
        self.assertGreaterEqual(active_rows.shape[0], 2)
        self.assertTrue(np.all(active_rows[:, 1] > active_rows[:, 0]))
        self.assertTrue(np.all(active_rows[:, 3] > active_rows[:, 2]))
        self.assertTrue(np.all(np.isfinite(active_rows[:, :4])))
        self.assertEqual(np.squeeze(mat["SystemClk"]).item(), 51200)
        self.assertEqual(np.squeeze(mat["SampleRate"]).item(), 1280)
        self.assertEqual(np.squeeze(mat["hdlg1_s1"])[0], 6)
        self.assertEqual(np.squeeze(mat["hdlg2_s1"]).tolist()[:7], [1, 0, 0, 9, 0, 0, 1])
        self.assertEqual(str(np.squeeze(mat["hdlg2_vis"])), "on")
        self.assertEqual(str(np.squeeze(mat["exdlg2_vis"])), "off")
        self.assertEqual(np.squeeze(mat["vdlg2_s1"])[1], 20)
        self.assertEqual(np.squeeze(mat["vdlg2_s1"])[4], 1)
        self.assertEqual(np.squeeze(slm.winsel).item(), 1)
        self.assertEqual(np.squeeze(slm.wincor).item(), 1)
        self.assertEqual(mat["vdlg1_s1"].dtype.kind, "f")
        self.assertEqual(mat["hdlg1_s1"].dtype, np.dtype("uint16"))
        self.assertEqual(mat["hdlg2_s1"].dtype, np.dtype("int16"))
        self.assertEqual(mat["num_io"].dtype, np.dtype("uint8"))
        self.assertEqual(mat["SampleRate"].dtype, np.dtype("uint16"))
        self.assertEqual(mat["SystemClk"].dtype, np.dtype("uint16"))
        self.assertEqual(np.asarray(slm.navg).dtype.kind, "f")
        self.assertEqual(np.asarray(slm.clist).dtype.kind, "f")
        self.assertEqual(np.asarray(slm.xcstate[0, 0].refc).dtype.kind, "f")
        self.assertEqual(np.asarray(slm.xcstate[0, 0].resp[0, 0].r).dtype.kind, "f")
        self.assertLessEqual(compressed_tag_count, 2)
        self.assertEqual(loaded.config.ai_channels[0].label, "Ref")
        self.assertEqual(loaded.config.ai_channels[0].engineering_unit, "m/s^2")
        self.assertEqual(loaded.config.ai_channels[0].full_scale, 5.0)
        self.assertEqual(loaded.config.ai_channels[0].coupling, "bias")
        self.assertEqual(loaded.config.ai_channels[0].sensitivity, 2.0)
        self.assertIn("ai0->ai1", loaded.measurement.frf)
        np.testing.assert_allclose(loaded.measurement.frf["ai0->ai1"], session.measurement.frf["ai0->ai1"])
        np.testing.assert_allclose(loaded.measurement.coherence["ai0->ai1"], session.measurement.coherence["ai0->ai1"])

    def test_save_legacy_vna_preserves_large_sensor_sensitivity(self):
        session = self._sample_session()
        session.config.ai_channels[0].label = "Hammer"
        session.config.ai_channels[0].engineering_unit = "N"
        session.config.ai_channels[0].sensitivity = 800.0
        session.config.ai_channels[0].per_eu_mode = "/Volt"

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "hammer.vna"
            save_legacy_vna(session, path)
            mat = loadmat(path, squeeze_me=False, struct_as_record=False)
            loaded = load_legacy_vna(path)

        self.assertEqual(float(mat["vdlg1_s1"][0, 6]), 800.0)
        self.assertEqual(float(mat["ChanStat"][0, 2]), 800.0)
        self.assertEqual(loaded.config.ai_channels[0].sensitivity, 800.0)

    def test_save_legacy_vna_writes_chinese_notes_as_utf16_char_data(self):
        session = self._sample_session()
        session.config.notes = "垂向"

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "chinese_notes.vna"
            save_legacy_vna(session, path)
            mat = loadmat(path, squeeze_me=True, struct_as_record=False)
            raw = path.read_bytes()

        self.assertEqual(str(mat["Cmprssd_Notes"]), "垂向")
        name_offset = raw.index(b"Cmprssd_Notes")
        data_type, byte_count = struct.unpack_from("<II", raw, name_offset + 16)
        self.assertEqual(data_type, 17)
        self.assertEqual(byte_count, 4)
        self.assertEqual(raw[name_offset + 24 : name_offset + 28], "垂向".encode("utf-16le"))

    def test_save_legacy_vna_hides_trigger_when_linked_excitation_is_enabled(self):
        session = self._sample_session()
        session.config.acquisition.excitation.enabled = True

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "linked_excitation.vna"
            save_legacy_vna(session, path)
            mat = loadmat(path, squeeze_me=False, struct_as_record=False)

        self.assertEqual(str(np.squeeze(mat["hdlg2_vis"])), "off")
        self.assertEqual(str(np.squeeze(mat["exdlg2_vis"])), "on")

    def test_default_channels_use_requested_engineering_units(self):
        config = default_session_config()
        self.assertEqual(config.ai_channels[0].coupling, "ac")
        for index, channel in enumerate(config.ai_channels):
            if index > 0:
                self.assertEqual(channel.coupling, "bias")
                self.assertTrue(channel.iepe_enabled)
            self.assertEqual(channel.iepe_current_ma, 2.1)
            self.assertEqual(channel.sensitivity, 1.0)
            self.assertEqual(channel.engineering_unit, "m/s^2")

    def test_load_legacy_vna(self):
        session = load_legacy_vna(r"D:\SynologyDrive\codex\vna\dsa\vna\sample.vna")
        self.assertEqual(session.config.title, "sample")
        self.assertTrue(len(session.config.ai_channels) >= 4)
        self.assertTrue(session.measurement is not None)
        self.assertTrue(len(session.measurement.frf) > 0)
        self.assertTrue(len(session.measurement.coherence) > 0)
        self.assertEqual(session.config.acquisition.frame_size, len(session.measurement.time_data["t"]))
        self.assertGreaterEqual(session.config.acquisition.averaging.count, 1)
        self.assertEqual(session.config.ai_channels[0].full_scale, 0.625)
        self.assertEqual(session.config.ai_channels[1].full_scale, 2.5)
        self.assertEqual(session.config.ai_channels[0].label, "Channel 1")
        self.assertEqual(session.config.ai_channels[0].engineering_unit, "Gs")

    def test_load_legacy_vna_restores_ui_count_and_record_length_not_measured_count(self):
        path = r"D:\SynologyDrive\ai_test\vna-test\test_matlab.vna"
        mat = loadmat(path, squeeze_me=False, struct_as_record=False)
        session = load_legacy_vna(path)
        hdlg1_s1 = np.squeeze(mat["hdlg1_s1"])
        vdlg2_s1 = np.squeeze(mat["vdlg2_s1"])
        measured_count = int(np.squeeze(mat["SLm"][0, 0].navg))

        self.assertEqual(session.config.acquisition.sample_rate, float(np.squeeze(mat["SampleRate"])))
        self.assertEqual(session.config.acquisition.frame_size, int(hdlg1_s1[2]))
        self.assertEqual(session.config.acquisition.bandwidth_hz, session.config.acquisition.sample_rate / 2.56)
        self.assertEqual(session.config.acquisition.averaging.count, int(vdlg2_s1[1]))
        self.assertEqual(session.config.acquisition.processing_window, "hanning")
        self.assertEqual(session.measurement.metadata["legacy_measured_average_count"], measured_count)

    def test_resaved_legacy_vna_keeps_valid_cursor_axis_history(self):
        session = load_legacy_vna(r"D:\SynologyDrive\ai_test\vna-test\test_matlab.vna")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "resaved.vna"
            save_legacy_vna(session, path)
            mat = loadmat(path, squeeze_me=False, struct_as_record=False)

        xplot_axes = np.asarray(mat["xplot_axes"], dtype=float)
        self.assertEqual(xplot_axes.shape, (10, 5))
        active_rows = xplot_axes[xplot_axes[:, 4] >= 0]
        self.assertGreaterEqual(active_rows.shape[0], 2)
        self.assertTrue(np.all(active_rows[:, 1] > active_rows[:, 0]))
        self.assertTrue(np.all(active_rows[:, 3] > active_rows[:, 2]))
        self.assertTrue(np.all(np.isfinite(active_rows[:, :4])))

    def test_save_legacy_vna_persists_current_upper_lower_display_state(self):
        session = self._sample_session()
        session.config.ai_channels = session.config.ai_channels[:2]
        session.config.ai_channels[0].label = "Ref"
        session.config.ai_channels[1].label = "Resp"
        session.config.acquisition.reference_channel = "ai0"
        session.config.acquisition.response_channels = ["ai1"]
        session.measurement.time_data["channels"]["ai1"] = np.array([0.5, 0.25])
        session.measurement.spectra["fft"]["ai1"] = np.array([0.5 + 0.0j, 0.25 + 0.1j])
        session.measurement.spectra["autospectrum"]["ai1"] = np.array([0.25, 0.0625])
        session.measurement.frf = {"ai0->ai1": np.array([1.0 + 0.0j, 2.0 + 0.5j])}
        session.measurement.coherence = {"ai0->ai1": np.array([1.0, 0.9])}
        session.measurement.metadata["legacy_display_state"] = {
            "layout": "dual",
            "top": {
                "mode": "autospectrum",
                "value_mode": "log_power_per_hz",
                "xscale": "log",
                "trace_names": ["Resp"],
                "reference_channel": "ai0",
            },
            "bottom": {
                "mode": "frf",
                "value_mode": "dB",
                "xscale": "log",
                "trace_names": ["ai0->ai1"],
                "reference_channel": "ai0",
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "display_state.vna"
            save_legacy_vna(session, path)
            mat = loadmat(path, squeeze_me=False, struct_as_record=False)
            loaded = load_legacy_vna(path)

        top = mat["xplot_s1"][0, 0]
        bottom = mat["xplot_s1"][0, 1]
        self.assertEqual(int(np.squeeze(top.ypu1sel)), 2)
        self.assertEqual(int(np.squeeze(top.ypu2sel)), 11)
        self.assertEqual(int(np.squeeze(top.xpu1sel)), 2)
        self.assertEqual(np.asarray(top.ylcb, dtype=float).tolist(), [[0.0, 1.0]])
        self.assertEqual(int(np.squeeze(bottom.ypu1sel)), 3)
        self.assertEqual(int(np.squeeze(bottom.ypu2sel)), 4)
        self.assertEqual(int(np.squeeze(bottom.xpu1sel)), 2)
        self.assertEqual(np.asarray(bottom.ylcb, dtype=float).tolist(), [[0.0, 1.0]])
        self.assertEqual(loaded.measurement.metadata["legacy_display_state"]["top"]["mode"], "autospectrum")
        self.assertEqual(loaded.measurement.metadata["legacy_display_state"]["top"]["trace_names"], ["Resp"])
        self.assertEqual(loaded.measurement.metadata["legacy_display_state"]["bottom"]["mode"], "frf")
        self.assertEqual(loaded.measurement.metadata["legacy_display_state"]["bottom"]["value_mode"], "dB")

    def test_save_legacy_vna_keeps_persisted_wincor_compatible_and_loads_runtime_power_correction(self):
        session = self._sample_session()
        session.config.acquisition.processing_window = "hanning"

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "hanning.vna"
            save_legacy_vna(session, path)
            mat = loadmat(path, squeeze_me=False, struct_as_record=False)
            loaded = load_legacy_vna(path)

        slm = mat["SLm"][0, 0]
        self.assertEqual(np.squeeze(slm.winsel).item(), 2)
        self.assertEqual(np.squeeze(slm.wincor).item(), 1)
        self.assertEqual(loaded.measurement.metadata["legacy_wincor"], 1.0)
        self.assertAlmostEqual(
            loaded.measurement.metadata["legacy_runtime_wincor"],
            2.0 / 3.0,
            places=9,
        )
        self.assertAlmostEqual(
            np.sqrt(loaded.measurement.metadata["legacy_runtime_wincor"]),
            np.sqrt(2.0 / 3.0),
            places=9,
        )

    def test_load_legacy_vna_preserves_mcsetup_fields_from_vdlg(self):
        candidates = [
            path
            for path in Path("D:/SynologyDrive").rglob("003.vna")
            if "651D-R" in str(path) and str(path).endswith(r"651D-R\003.vna")
        ]
        if not candidates:
            self.skipTest("651D-R 003.vna fixture is not available")

        session = load_legacy_vna(candidates[0])
        channels = session.config.ai_channels

        self.assertEqual(len(channels), 4)
        self.assertEqual([channel.enabled for channel in channels], [True, True, False, False])
        self.assertEqual([channel.full_scale for channel in channels], [5.0, 5.0, 5.0, 5.0])
        self.assertEqual([channel.coupling for channel in channels], ["bias", "bias", "bias", "bias"])
        self.assertEqual([channel.sensitivity for channel in channels], [1.0, 1.0, 20.0, 20.0])
        self.assertEqual([channel.engineering_unit for channel in channels], ["m/s^2"] * 4)
        self.assertEqual([channel.per_eu_mode for channel in channels], ["/Volt"] * 4)
        self.assertEqual([channel.db_reference for channel in channels], [1.0] * 4)
        self.assertEqual(session.config.acquisition.sample_rate, 2560.0)
        self.assertEqual(session.config.acquisition.frame_size, 4096)
        self.assertEqual(session.config.acquisition.bandwidth_hz, 1000.0)
        self.assertTrue(session.config.acquisition.anti_alias_filters_enabled)
        self.assertEqual(session.config.acquisition.processing_window, "hanning")
        self.assertEqual(session.config.acquisition.overlap_percent, 0)
        self.assertEqual(session.config.acquisition.averaging.mode, "linear")
        self.assertEqual(session.config.acquisition.averaging.count, 20)
        self.assertEqual(session.config.acquisition.trigger.mode, "Off (Free Run)")
        self.assertFalse(session.config.acquisition.trigger.enabled)
        self.assertEqual(session.config.acquisition.trigger.source, "immediate")
        self.assertAlmostEqual(session.config.acquisition.trigger.level_percent, 0.0)
        self.assertEqual(session.config.acquisition.trigger.pretrigger_samples, -10)
        self.assertEqual(session.config.acquisition.trigger.slope, "rising")
        self.assertAlmostEqual(session.config.acquisition.modal.force_window_fraction, 0.2)
        self.assertAlmostEqual(session.config.acquisition.modal.exponential_decay_fraction, 0.1)
        self.assertAlmostEqual(session.config.acquisition.modal.double_hit_threshold, 0.5)
        self.assertAlmostEqual(session.config.acquisition.modal.double_hit_delay_fraction, 0.2)
        display_state = session.measurement.metadata["legacy_display_state"]
        legacy_config = session.measurement.metadata["legacy_config_state"]
        self.assertEqual(legacy_config["hdlg1_s1"][:3], [5.0, 0.0, 4096.0])
        self.assertEqual(legacy_config["hdlg2_s1"][:4], [1.0, 0.0, -10.0, 9.0])
        self.assertEqual(legacy_config["vdlg2_s1"][:5], [1.0, 20.0, 0.637, 1.0, 2.0])
        self.assertEqual(display_state["top"]["mode"], "time")
        self.assertEqual(display_state["top"]["trace_names"], ["Channel 1", "Channel 2"])
        self.assertEqual(display_state["bottom"]["mode"], "frf")
        self.assertEqual(display_state["bottom"]["value_mode"], "dB")
        self.assertEqual(display_state["bottom"]["xscale"], "log")
        self.assertEqual(display_state["bottom"]["trace_names"], ["ai0->ai1"])
        self.assertAlmostEqual(display_state["bottom"]["axis_range"]["xmin"], 0.6250000037510972)
        self.assertAlmostEqual(display_state["bottom"]["axis_range"]["xmax"], 1280.000007682247)


if __name__ == "__main__":
    unittest.main()
