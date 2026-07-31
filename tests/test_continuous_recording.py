from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import zipfile

import numpy as np

from python_vna.continuous_recording import (
    ContinuousDatWriter,
    iter_dat_frames,
    read_dat_header,
)
from python_vna.analysis_data import load_analysis_path, load_continuous_channels
from python_vna.daq.base import BackendFrame
from python_vna.storage import default_session_config


class _Clock:
    def __init__(self) -> None:
        self.unix_ns = 1_700_000_000_000_000_000
        self.monotonic = 0.0

    def time_ns(self) -> int:
        self.unix_ns += 1_000_000
        return self.unix_ns

    def monotonic_seconds(self) -> float:
        return self.monotonic

    def advance(self, seconds: float) -> None:
        self.monotonic += seconds
        self.unix_ns += int(seconds * 1_000_000_000)


class ContinuousRecordingTests(unittest.TestCase):
    def _frame(self, index: int, value: float = 1.0) -> BackendFrame:
        return BackendFrame(
            sample_rate=2560.0,
            channel_names=["ai0", "ai1"],
            data=np.array(
                [[value, value + 1.0, value + 2.0], [value + 3.0, value + 4.0, value + 5.0]],
                dtype=float,
            ),
            timestamps=np.array([0.0, 1.0 / 2560.0, 2.0 / 2560.0], dtype=float),
            frame_index=index,
            metadata={},
        )

    def test_dat_writer_persists_header_frame_data_and_manifest(self):
        clock = _Clock()
        session = default_session_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            writer = ContinuousDatWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0", "ai1"],
                software_version="test",
                segment_seconds=600.0,
                compress_closed_segments=False,
                time_fn=clock.time_ns,
                monotonic_fn=clock.monotonic_seconds,
            )
            writer.start()
            status = writer.write_frame(self._frame(1, 10.0))
            writer.close()

            segment_path = output_dir / "segment_0001.dat"
            text = segment_path.read_text(encoding="utf-8")
            header = read_dat_header(segment_path)
            frames = list(iter_dat_frames(segment_path))
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))

            self.assertTrue(text.startswith("# Python VNA continuous DAT"))
            self.assertIn("# format=python_vna_continuous_text_dat", text)
            self.assertIn("# start_time_local=", text)
            self.assertIn("# sample_rate=2560", text)
            self.assertIn(
                "time_s\tlocal_time\tunix_ns\tframe_index\tsample_index\tai0\tai1",
                text,
            )
            self.assertIn("\t1\t0\t10\t13", text)
            self.assertEqual(header["sample_rate"], 2560.0)
            self.assertEqual(header["channel_names"], ["ai0", "ai1"])
            self.assertIn("start_time_local", header)
            self.assertEqual(header["format_version"], 3)
            self.assertEqual(frames[0][1]["frame_index"], 1)
            np.testing.assert_allclose(frames[0][1]["data"], self._frame(1, 10.0).data)
            self.assertEqual(status.total_samples, 3)
            self.assertTrue(manifest["completed"])
            self.assertIn("start_time_local", manifest)
            self.assertIn("end_local", manifest)
            self.assertIn("start_time_local", manifest["segments"][0])
            self.assertEqual(manifest["total_frames"], 1)
            self.assertEqual(manifest["segments"][0]["samples"], 3)

    def test_dat_writer_compresses_closed_segments_and_readers_open_zip(self):
        clock = _Clock()
        session = default_session_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            writer = ContinuousDatWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0", "ai1"],
                software_version="test",
                time_fn=clock.time_ns,
                monotonic_fn=clock.monotonic_seconds,
            )
            writer.start()
            writer.write_frame(self._frame(1, 10.0))
            writer.close()

            segment_path = output_dir / "segment_0001.dat"
            archive_path = output_dir / "segment_0001.zip"
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))

            self.assertFalse(segment_path.exists())
            self.assertTrue(archive_path.exists())
            self.assertEqual(manifest["segments"][0]["path"], "segment_0001.zip")
            self.assertEqual(manifest["segments"][0]["raw_path"], "segment_0001.dat")
            self.assertTrue(manifest["segments"][0]["compressed"])
            self.assertEqual(manifest["segment_compression"], "zip")
            with zipfile.ZipFile(archive_path) as archive:
                self.assertIn("segment_0001.dat", archive.namelist())
            header = read_dat_header(archive_path)
            frames = list(iter_dat_frames(archive_path))
            self.assertEqual(header["format"], "python_vna_continuous_text_dat")
            np.testing.assert_allclose(frames[0][1]["data"], self._frame(1, 10.0).data)

    def test_dat_writer_uses_frame_start_time_for_sample_rows(self):
        clock = _Clock()
        session = default_session_config()
        frame_start_unix_ns = 1_700_000_001_000_000_000
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            writer = ContinuousDatWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0", "ai1"],
                software_version="test",
                compress_closed_segments=False,
                time_fn=clock.time_ns,
                monotonic_fn=clock.monotonic_seconds,
            )
            frame = self._frame(1, 10.0)
            frame.metadata["frame_start_unix_ns"] = frame_start_unix_ns
            writer.start()
            writer.write_frame(frame)
            writer.close()

            rows = [
                line.split("\t")
                for line in (output_dir / "segment_0001.dat").read_text(
                    encoding="utf-8"
                ).splitlines()
                if line and not line.startswith("#") and not line.startswith("time_s")
            ]
            self.assertEqual(int(rows[0][2]), frame_start_unix_ns)
            self.assertEqual(
                int(rows[1][2]),
                frame_start_unix_ns + int(round(1_000_000_000 / 2560.0)),
            )

    def test_dat_writer_rotates_segments_by_elapsed_time(self):
        clock = _Clock()
        session = default_session_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            writer = ContinuousDatWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0", "ai1"],
                software_version="test",
                segment_seconds=10.0,
                compress_closed_segments=False,
                time_fn=clock.time_ns,
                monotonic_fn=clock.monotonic_seconds,
            )
            writer.start()
            writer.write_frame(self._frame(1, 1.0))
            clock.advance(11.0)
            writer.write_frame(self._frame(2, 2.0))
            writer.close(completed=False, error="manual stop")

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue((output_dir / "segment_0001.dat").exists())
            self.assertTrue((output_dir / "segment_0002.dat").exists())
            self.assertFalse(manifest["completed"])
            self.assertEqual(manifest["error"], "manual stop")
            self.assertEqual(len(manifest["segments"]), 2)

    def test_dat_writer_queues_segment_compression_without_blocking_rotation(self):
        class _SlowCompressionWriter(ContinuousDatWriter):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.compression_started = threading.Event()
                self.release_compression = threading.Event()

            def _compress_segment(self, segment_path):
                self.compression_started.set()
                self.release_compression.wait(timeout=2.0)
                return super()._compress_segment(segment_path)

        clock = _Clock()
        session = default_session_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            writer = _SlowCompressionWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0", "ai1"],
                software_version="test",
                segment_seconds=1.0,
                time_fn=clock.time_ns,
                monotonic_fn=clock.monotonic_seconds,
            )
            writer.start()
            writer.write_frame(self._frame(1, 1.0))
            clock.advance(2.0)
            start = time.perf_counter()
            writer.write_frame(self._frame(2, 2.0))
            elapsed = time.perf_counter() - start

            self.assertLess(elapsed, 0.5)
            self.assertTrue(writer.compression_started.wait(timeout=1.0))
            self.assertTrue((output_dir / "segment_0001.dat").exists())
            self.assertTrue((output_dir / "segment_0002.dat").exists())

            writer.release_compression.set()
            writer.close()
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["segments"][0]["path"], "segment_0001.zip")
            self.assertTrue(manifest["segments"][0]["compressed"])

    def test_dat_writer_rotates_segments_by_size_limit(self):
        clock = _Clock()
        session = default_session_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            writer = ContinuousDatWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0", "ai1"],
                software_version="test",
                segment_seconds=600.0,
                max_segment_bytes=1,
                compress_closed_segments=False,
                time_fn=clock.time_ns,
                monotonic_fn=clock.monotonic_seconds,
            )
            writer.start()
            writer.write_frame(self._frame(1, 1.0))
            writer.write_frame(self._frame(2, 2.0))
            writer.close()

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue((output_dir / "segment_0001.dat").exists())
            self.assertTrue((output_dir / "segment_0002.dat").exists())
            self.assertEqual(len(manifest["segments"]), 2)
            self.assertEqual(manifest["max_segment_bytes"], 1)

    def test_dat_writer_final_manifest_includes_latest_frame_even_with_interval(self):
        clock = _Clock()
        session = default_session_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            writer = ContinuousDatWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0", "ai1"],
                software_version="test",
                manifest_interval_seconds=60.0,
                compress_closed_segments=False,
                time_fn=clock.time_ns,
                monotonic_fn=clock.monotonic_seconds,
            )
            writer.start()
            writer.write_frame(self._frame(1, 1.0))
            writer.write_frame(self._frame(2, 2.0))
            writer.close()

            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["total_frames"], 2)
            self.assertEqual(manifest["total_samples"], 6)

    def test_binary_writer_persists_fast_segments_and_analysis_reads_channels(self):
        clock = _Clock()
        session = default_session_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            writer = ContinuousDatWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0", "ai1"],
                software_version="test",
                storage_format="binary",
                compress_closed_segments=False,
                time_fn=clock.time_ns,
                monotonic_fn=clock.monotonic_seconds,
            )
            writer.start()
            writer.write_frame(self._frame(1, 10.0))
            writer.close()

            segment_path = output_dir / "segment_0001.bin"
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            dataset = load_analysis_path(output_dir / "manifest.json")
            time_s, channels = load_continuous_channels(dataset, ["ai1"])

            self.assertTrue(segment_path.exists())
            self.assertEqual(segment_path.stat().st_size, 2 * 3 * 8)
            self.assertEqual(manifest["storage_format"], "binary")
            self.assertEqual(manifest["binary_layout"], "sample_major")
            self.assertFalse(manifest["segments"][0]["compressed"])
            self.assertFalse((output_dir / ".analysis_cache").exists())
            np.testing.assert_allclose(time_s, np.array([0.0, 1.0, 2.0]) / 2560.0)
            np.testing.assert_allclose(channels["ai1"], [13.0, 14.0, 15.0])


if __name__ == "__main__":
    unittest.main()
