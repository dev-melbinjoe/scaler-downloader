"""Download one HLS recording to MP4.

Segments are fetched in parallel straight from Scaler's CDN (the playlist already carries
time-limited signed links), kept in a .parts folder so an interrupted download resumes, then
remuxed into a single MP4 by ffmpeg without re-encoding.
"""

from __future__ import annotations

import concurrent.futures as cf
import math
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

from .config import ROOT
from .control import STOP, check
from .util import free_bytes, gib, hms

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/155.0.0.0 Safari/537.36"
)
RESERVE_BYTES = 500 * 1024**2  # never fill the disk completely


class DownloadError(RuntimeError):
    pass


class NotEnoughSpace(DownloadError):
    pass


class LinkExpired(Exception):
    pass


def ffmpeg_exe() -> str:
    for candidate in (ROOT / "ffmpeg.exe", shutil.which("ffmpeg")):
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise DownloadError("ffmpeg not found: put ffmpeg.exe in the project folder or on PATH.")


def parse_master(text: str, base: str) -> list[dict]:
    lines = [line.strip() for line in text.splitlines()]
    variants = []
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        attrs = dict(
            part.split("=", 1) for part in line.split(":", 1)[1].replace('"', "").split(",") if "=" in part
        )
        uri = next((u for u in lines[i + 1:] if u and not u.startswith("#")), None)
        if uri:
            height = int(attrs.get("RESOLUTION", "0x0").split("x")[-1] or 0)
            variants.append({"height": height, "bandwidth": int(attrs.get("BANDWIDTH", 0)), "url": urljoin(base, uri)})
    return variants


def parse_media(text: str, base: str) -> list[tuple[float, str]]:
    segments, duration = [], 0.0
    for line in (line.strip() for line in text.splitlines()):
        if line.startswith("#EXTINF:"):
            duration = float(line[8:].split(",")[0])
        elif line.startswith("#EXT-X-KEY") and "METHOD=NONE" not in line:
            raise DownloadError("this recording is encrypted; only plain HLS recordings are supported")
        elif line and not line.startswith("#"):
            segments.append((duration, urljoin(base, line)))
    if not segments:
        raise DownloadError("the recording playlist is empty")
    return segments


def _fetch(url: str, dest: Path) -> int:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Referer": "https://www.scaler.com/", "Origin": "https://www.scaler.com"}
    )
    tmp = dest.with_suffix(".tmp")
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=60) as resp, open(tmp, "wb") as f:
                expected = int(resp.headers.get("Content-Length") or 0)
                shutil.copyfileobj(resp, f, 1 << 20)
            if expected and tmp.stat().st_size != expected:
                raise ConnectionError("incomplete segment")
            os.replace(tmp, dest)
            return dest.stat().st_size
        except urllib.error.HTTPError as e:
            # Signed links only become valid the second after the playlist is issued, so an early
            # 403 is retried; one that persists means the link has expired.
            if e.code in (401, 403) and attempt >= 2:
                raise LinkExpired(f"segment link rejected (HTTP {e.code})") from e
            if e.code not in (401, 403) and attempt == 4:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if attempt == 4:
                raise
        time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def download_recording(
    get_text: Callable[[str], str],
    master_url: str,
    out_file: Path,
    max_height: int = 720,
    workers: int = 6,
) -> dict:
    """`get_text` fetches a playlist with your Scaler session (only called from this thread)."""
    variants = parse_master(get_text(master_url), master_url)
    if variants:
        fitting = [v for v in variants if v["height"] <= max_height] or [min(variants, key=lambda v: v["height"])]
        chosen = max(fitting, key=lambda v: (v["height"], v["bandwidth"]))
    else:  # already a media playlist
        chosen = {"height": 0, "bandwidth": 0, "url": master_url}
    segments = parse_media(get_text(chosen["url"]), chosen["url"])
    time.sleep(2)  # the playlist's signed links are valid from the next second on
    total_seconds = sum(d for d, _ in segments)
    # Some playlists don't state a resolution; then the highest bitrate was picked.
    quality = f"{chosen['height']}p" if chosen["height"] else "best available quality"
    print(f"  {quality}, {len(segments)} segments, {hms(total_seconds)} of video", flush=True)

    parts = out_file.parent / ".parts"
    parts.mkdir(parents=True, exist_ok=True)
    names = [f"{i:05d}.ts" for i in range(len(segments))]

    def missing() -> list[int]:
        return [i for i, n in enumerate(names) if not (parts / n).exists()]

    # Measure a few segments first so we can refuse early instead of filling the disk.
    for i in missing()[:3]:
        _fetch(segments[i][1], parts / names[i])
    have = [(parts / n).stat().st_size for n in names if (parts / n).exists()]
    estimate = sum(have) / len(have) * len(names)
    still_needed = estimate - sum(have) + estimate  # remaining parts + the final MP4 (parts are deleted after)
    if free_bytes(out_file.parent) - still_needed < RESERVE_BYTES:
        raise NotEnoughSpace(
            f"needs about {gib(still_needed + RESERVE_BYTES)} free on {out_file.anchor or 'disk'}, "
            f"only {gib(free_bytes(out_file.parent))} available (video ~{gib(estimate)})"
        )

    for refresh in range(4):
        todo = missing()
        if not todo:
            break
        if refresh:  # signed links expire after a few hours; get a fresh playlist and continue
            segments = parse_media(get_text(chosen["url"]), chosen["url"])
            time.sleep(2)
        expired = 0
        done_bytes, started, last = 0, time.time(), 0.0
        with cf.ThreadPoolExecutor(workers) as pool:
            futures = {pool.submit(_fetch, segments[i][1], parts / names[i]): i for i in todo}
            for n, fut in enumerate(cf.as_completed(futures), 1):
                if STOP.is_set():  # Stop button: finished segments stay in .parts for next time
                    pool.shutdown(wait=True, cancel_futures=True)
                    check()
                try:
                    done_bytes += fut.result()
                except LinkExpired:
                    expired += 1
                    continue
                now = time.time()
                if now - last > 5 or n == len(todo):
                    last = now
                    rate = done_bytes / max(now - started, 0.1)
                    left = len(todo) - n
                    eta = left * (done_bytes / n) / max(rate, 1)
                    print(
                        f"  {len(names) - left}/{len(names)} segments  {done_bytes / 1e6:.0f} MB"
                        f"  {rate / 1e6:.1f} MB/s  ETA {hms(eta)}",
                        flush=True,
                    )
        if not expired:
            break
        print(f"  {expired} segment links expired; refreshing the playlist", flush=True)
    if missing():
        raise DownloadError(f"{len(missing())} segments could not be downloaded; run the command again to resume")

    target = math.ceil(max(d for d, _ in segments))
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{target}", "#EXT-X-MEDIA-SEQUENCE:0"]
    for (d, _), name in zip(segments, names):
        lines += [f"#EXTINF:{d:.6f},", name]
    lines.append("#EXT-X-ENDLIST")
    (parts / "index.m3u8").write_text("\n".join(lines) + "\n", encoding="utf-8")

    tmp_mp4 = out_file.with_suffix(".tmp.mp4")
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(parts / "index.m3u8"),
         "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", str(tmp_mp4)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not tmp_mp4.exists():
        raise DownloadError(f"ffmpeg could not join the segments: {proc.stderr.strip()[-500:]}")
    os.replace(tmp_mp4, out_file)
    shutil.rmtree(parts, ignore_errors=True)
    return {
        "height": chosen["height"],
        "segments": len(segments),
        "bytes": out_file.stat().st_size,
        "duration": total_seconds,
    }
