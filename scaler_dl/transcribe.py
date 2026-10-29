"""Speech-to-text with faster-whisper, running locally on the CPU.

The audio is transcribed in ~10 minute chunks cut at quiet moments: faster-whisper computes the
spectrogram of whatever it is given in one go, and for a 3-hour class that alone needs over 2 GB
of RAM. Finished segments are appended to transcript.partial.jsonl as they arrive, so an
interrupted run resumes where it stopped.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import numpy as np

from .control import check
from .downloader import ffmpeg_exe
from .util import hms, subtitle_ts, write_json

SAMPLE_RATE = 16000
CHUNK_SECONDS = 600


def load_audio(media: Path) -> np.ndarray:
    """16 kHz mono int16 samples via ffmpeg (faster-whisper's own loader breaks on PyAV 19)."""
    proc = subprocess.run(
        [ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(media),
         "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not read the audio: {proc.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(proc.stdout, dtype=np.int16)


def chunk_bounds(audio: np.ndarray, chunk: int = CHUNK_SECONDS, search: int = 20) -> list[int]:
    """Sample offsets every ~`chunk` seconds, each moved to the quietest 100 ms nearby."""
    sr, hop = SAMPLE_RATE, SAMPLE_RATE // 10
    bounds = [0]
    target = chunk * sr
    while target < len(audio) - 60 * sr:
        lo, hi = max(bounds[-1] + 60 * sr, target - search * sr), min(len(audio), target + search * sr)
        window = audio[lo:hi].astype(np.float32)
        energy = (window[: len(window) // hop * hop].reshape(-1, hop) ** 2).mean(axis=1)
        cut = lo + int(energy.argmin()) * hop + hop // 2
        bounds.append(cut)
        target = cut + chunk * sr
    bounds.append(len(audio))
    return bounds


def vocabulary_hint(title: str) -> str:
    """Class title as a plain sentence. A Title Cased hint makes Whisper Title Case Its Output."""
    words = [w if (w.isupper() or not w.isalpha()) else w.lower() for w in title.replace(":", " ").split()]
    return f"Lecture on {' '.join(words)}."


def transcribe(
    media: Path,
    out_dir: Path,
    model_size: str = "small",
    language: str | None = "en",
    hotwords: str | None = None,
    beam_size: int = 1,  # 5 is ~1.6x slower on a laptop CPU for a barely better transcript
) -> list[dict]:
    from faster_whisper import WhisperModel

    out_dir.mkdir(parents=True, exist_ok=True)
    partial = out_dir / "transcript.partial.jsonl"
    segments: list[dict] = []
    if partial.exists():
        segments = [json.loads(line) for line in partial.read_text(encoding="utf-8").splitlines() if line.strip()]
    resume_at = segments[-1]["end"] if segments else 0.0

    print(f"  reading audio of {media.name} ...", flush=True)
    audio = load_audio(media)
    duration = len(audio) / SAMPLE_RATE
    if resume_at:
        print(f"  resuming at {hms(resume_at)} of {hms(duration)}", flush=True)

    model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
    started = last_report = time.time()
    bounds = chunk_bounds(audio)
    with open(partial, "a", encoding="utf-8") as f:
        for a, b in zip(bounds, bounds[1:]):
            a = max(a, int(resume_at * SAMPLE_RATE))
            if b - a < SAMPLE_RATE:  # this chunk was finished in an earlier run
                continue
            offset = a / SAMPLE_RATE
            pieces, _info = model.transcribe(
                audio[a:b].astype(np.float32) / 32768.0,
                language=language,
                beam_size=beam_size,
                vad_filter=True,  # skips silence/breaks: faster, and avoids made-up text in quiet parts
                vad_parameters={"min_silence_duration_ms": 700},
                condition_on_previous_text=False,  # stops the "same sentence repeated forever" failure
                hotwords=hotwords,  # e.g. the class title, nudges spelling of technical terms
            )
            for piece in pieces:
                check()  # Stop button: what's done so far is kept in transcript.partial.jsonl
                text = piece.text.strip()
                if not text or (piece.no_speech_prob > 0.6 and piece.avg_logprob < -1.0):
                    continue
                seg = {"start": round(piece.start + offset, 2), "end": round(piece.end + offset, 2), "text": text}
                f.write(json.dumps(seg, ensure_ascii=False) + "\n")
                f.flush()
                segments.append(seg)
                now = time.time()
                if now - last_report > 30:
                    last_report = now
                    speed = (seg["end"] - resume_at) / max(now - started, 1)  # audio seconds per wall second
                    print(
                        f"  {hms(seg['end'])} / {hms(duration)} ({seg['end'] / duration:.0%})"
                        f"  speed {speed:.1f}x  ETA {hms((duration - seg['end']) / max(speed, 1e-6))}",
                        flush=True,
                    )

    write_transcript(out_dir, segments, duration, model_size)
    partial.unlink(missing_ok=True)
    print(f"  transcribed {hms(duration - resume_at)} of audio in {hms(time.time() - started)}", flush=True)
    return segments


def write_transcript(out_dir: Path, segments: list[dict], duration: float, model: str) -> None:
    write_json(out_dir / "transcript.json", {"duration": duration, "model": model, "segments": segments})

    srt, vtt, txt = [], ["WEBVTT", ""], []
    for i, s in enumerate(segments, 1):
        srt += [str(i), f"{subtitle_ts(s['start'], ',')} --> {subtitle_ts(s['end'], ',')}", s["text"], ""]
        vtt += [f"{subtitle_ts(s['start'], '.')} --> {subtitle_ts(s['end'], '.')}", s["text"], ""]
        txt.append(f"[{hms(s['start'])}] {s['text']}")
    # Named after the video so VLC / MPC / Windows players pick the subtitles up automatically.
    (out_dir / "video.srt").write_text("\n".join(srt), encoding="utf-8")
    (out_dir / "video.vtt").write_text("\n".join(vtt), encoding="utf-8")
    (out_dir / "transcript.txt").write_text("\n".join(txt) + "\n", encoding="utf-8")
