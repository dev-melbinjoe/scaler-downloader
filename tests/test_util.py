"""Tests for scaler_dl.util helpers."""

import tempfile
import unittest
from pathlib import Path

from scaler_dl.util import (
    free_bytes,
    gib,
    hms,
    human_duration,
    read_json,
    safe_filename,
    subtitle_ts,
    write_json,
)


class TestUtil(unittest.TestCase):
    def test_hms(self):
        self.assertEqual(hms(0), "00:00:00")
        self.assertEqual(hms(59), "00:00:59")
        self.assertEqual(hms(60), "00:01:00")
        self.assertEqual(hms(3665), "01:01:05")
        self.assertEqual(hms(-10), "00:00:00")

    def test_subtitle_ts(self):
        self.assertEqual(subtitle_ts(0, ","), "00:00:00,000")
        self.assertEqual(subtitle_ts(1.5, "."), "00:00:01.500")
        self.assertEqual(subtitle_ts(3665.123, ","), "01:01:05,123")

    def test_human_duration(self):
        self.assertEqual(human_duration(45), "1m")
        self.assertEqual(human_duration(60), "1m")
        self.assertEqual(human_duration(3600), "1h 00m")
        self.assertEqual(human_duration(5400), "1h 30m")
        self.assertEqual(human_duration(9000), "2h 30m")

    def test_safe_filename(self):
        self.assertEqual(safe_filename("Normal Title"), "Normal Title")
        self.assertEqual(safe_filename('Invalid: <File> *Name*? /Test\\'), "Invalid File Name Test")
        self.assertEqual(safe_filename("...Leading and trailing dots..."), "Leading and trailing dots")
        self.assertEqual(safe_filename(""), "untitled")
        self.assertEqual(safe_filename("A" * 100, limit=20), "A" * 20)

    def test_gib(self):
        self.assertEqual(gib(1024**3), "1.0 GB")
        self.assertEqual(gib(2.5 * 1024**3), "2.5 GB")

    def test_free_bytes(self):
        bytes_free = free_bytes(Path("."))
        self.assertIsInstance(bytes_free, int)
        self.assertGreater(bytes_free, 0)

    def test_json_atomic_read_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "sub" / "data.json"
            data = {"key": "value", "list": [1, 2, 3], "unicode": "scaler 🚀"}
            write_json(file_path, data)
            self.assertTrue(file_path.exists())

            loaded = read_json(file_path)
            self.assertEqual(loaded, data)

            missing = read_json(Path(tmpdir) / "missing.json", default={"default": True})
            self.assertEqual(missing, {"default": True})


if __name__ == "__main__":
    unittest.main()
