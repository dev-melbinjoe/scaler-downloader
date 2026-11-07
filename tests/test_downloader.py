"""Tests for scaler_dl.downloader HLS parser and error checks."""

import unittest

from scaler_dl.downloader import DownloadError, parse_master, parse_media

SAMPLE_MASTER_M3U8 = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
360p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=1280x720
720p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080
1080p.m3u8
"""

SAMPLE_MEDIA_M3U8 = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXTINF:9.008,
seg_001.ts
#EXTINF:8.500,
seg_002.ts
#EXT-X-ENDLIST
"""

ENCRYPTED_MEDIA_M3U8 = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="https://example.com/key"
#EXTINF:10.0,
seg_001.ts
#EXT-X-ENDLIST
"""


class TestDownloader(unittest.TestCase):
    def test_parse_master(self):
        base = "https://cdn.scaler.com/video/"
        variants = parse_master(SAMPLE_MASTER_M3U8, base)
        self.assertEqual(len(variants), 3)

        v720 = [v for v in variants if v["height"] == 720][0]
        self.assertEqual(v720["height"], 720)
        self.assertEqual(v720["bandwidth"], 1400000)
        self.assertEqual(v720["url"], "https://cdn.scaler.com/video/720p.m3u8")

    def test_parse_media(self):
        base = "https://cdn.scaler.com/video/720p/"
        segments = parse_media(SAMPLE_MEDIA_M3U8, base)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0][0], 9.008)
        self.assertEqual(segments[0][1], "https://cdn.scaler.com/video/720p/seg_001.ts")
        self.assertEqual(segments[1][0], 8.5)
        self.assertEqual(segments[1][1], "https://cdn.scaler.com/video/720p/seg_002.ts")

    def test_parse_media_empty(self):
        with self.assertRaises(DownloadError):
            parse_media("#EXTM3U\n#EXT-X-ENDLIST", "https://example.com/")

    def test_parse_media_encrypted(self):
        with self.assertRaises(DownloadError):
            parse_media(ENCRYPTED_MEDIA_M3U8, "https://example.com/")


if __name__ == "__main__":
    unittest.main()
