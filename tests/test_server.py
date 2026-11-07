"""Tests for scaler_dl.server security, range headers, and job queue."""

import io
import tempfile
import unittest
from pathlib import Path

from scaler_dl.server import Jobs, make_handler


class TestServer(unittest.TestCase):
    def test_jobs_queue_and_state(self):
        jobs = Jobs()
        added1 = jobs.add("sync")
        self.assertTrue(added1)

        # Duplicate addition of same job kind should be rejected
        added2 = jobs.add("sync")
        self.assertFalse(added2)

        state = jobs.state()
        self.assertIn("queue", state)
        self.assertIn("version", state)

        jobs.stop()
        state_after_stop = jobs.state()
        self.assertEqual(len(state_after_stop["queue"]), 0)

    def test_handler_path_security(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lib_dir = Path(tmpdir)
            (lib_dir / "safe.txt").write_text("safe content", encoding="utf-8")
            (lib_dir.parent / "secret.txt").write_text("secret", encoding="utf-8")

            # Monkeypatch library.LIBRARY_DIR
            from scaler_dl import library

            old_lib = library.LIBRARY_DIR
            library.LIBRARY_DIR = lib_dir
            try:
                jobs = Jobs()
                handler_cls = make_handler(jobs, 8765)

                class DummyHandler(handler_cls):
                    def __init__(self):
                        self.headers = {"Host": "127.0.0.1:8765"}
                        self.command = "GET"
                        self.sent_code = None
                        self.sent_body = None
                        self.wfile = io.BytesIO()

                    def _send(self, code, body, ctype):
                        self.sent_code = code
                        self.sent_body = body

                    def send_response(self, code):
                        self.sent_code = code

                    def send_header(self, *args):
                        pass

                    def end_headers(self):
                        pass

                h = DummyHandler()

                # Normal file
                h._file("safe.txt")
                # Attempt path traversal
                h._file("../secret.txt")
                self.assertEqual(h.sent_code, 404)

                # Windows root escape
                h._file(r"\Windows\System32")
                self.assertEqual(h.sent_code, 404)
            finally:
                library.LIBRARY_DIR = old_lib


if __name__ == "__main__":
    unittest.main()
